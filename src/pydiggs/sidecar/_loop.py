"""The sidecar's process side: stdio setup, the reader, the executor and the writer.

Three threads share one :class:`Server`:

* the **reader** reads bounded lines from stdin, answers cheap methods (``ping``,
  ``versions``, ``rules.*``) at once, and queues ``initialize``, the document methods
  (``validate``, ``read``, ``convert``, ``ags4.check``) and ``shutdown`` for the
  executor;
* the **executor** runs one queued job at a time (validators are compiled per
  thread, so warm-up and validation share this thread);
* the **main** thread waits for the end (``exit``, EOF, a broken pipe or a signal)
  and returns the exit status. A signal handler only sets a flag, which this thread
  sees within :data:`_WAIT_SECONDS`.

Every frame goes through :meth:`Server.send`: one lock, one write and one flush per
frame, so frames never interleave.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Final, Literal

from pydiggs import _service
from pydiggs.sidecar import _paths, _protocol
from pydiggs.sidecar._protocol import ErrorCode, ProtocolError, RequestId

if TYPE_CHECKING:
    from types import FrameType

    from pydiggs._serve_cli import ServeOptions

__all__ = ["Server", "serve"]

_log = logging.getLogger("pydiggs.sidecar")

#: Bytes the reader asks for at a time while discarding an over-long line.
_DISCARD_CHUNK: Final = 1 << 20
#: How often the main thread wakes while waiting (to act on a signal; lets Windows see
#: Ctrl+Break).
_WAIT_SECONDS: Final = 0.5
#: Requests answered without the executor.
_CHEAP: Final = frozenset({"ping", "versions", "rules.list", "rules.explain"})
_KNOWN: Final = _CHEAP | {"initialize", "validate", "read", "convert", "ags4.check", "shutdown"}
#: The requests that take a document: one runs, one waits, both can be cancelled.
_DOCUMENT: Final = ("validate", "read", "convert", "ags4.check")
#: The protocol minor that added each document method (1.0 has validate).
_SINCE_MINOR: Final = {"validate": 0, "read": 1, "convert": 2, "ags4.check": 2}

_DocumentKind = Literal["validate", "read", "convert", "ags4.check"]


class _TooLong:
    """Marker: the line was longer than ``max_line_bytes`` and has been discarded."""


_TOO_LONG: Final = _TooLong()

Phase = Literal["new", "initializing", "ready", "shutdown"]


@dataclass(eq=False)
class _Job:
    kind: Literal["initialize", "validate", "read", "convert", "ags4.check", "shutdown"]
    id: RequestId
    params: Any = None
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def key(self) -> tuple[bool, RequestId]:
        return (isinstance(self.id, str), self.id)


def _key(request_id: RequestId) -> tuple[bool, RequestId]:
    return (isinstance(request_id, str), request_id)


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


class Server:
    """One sidecar session over a binary reader and writer.

    ``reader`` needs ``readline(limit)`` (a buffered binary stream); ``writer``
    needs ``write`` and ``flush``. :func:`serve` passes stdin and the saved stdout
    descriptor; tests pass pipes.

    Examples:
        >>> import io, json
        >>> from pydiggs._serve_cli import ServeOptions
        >>> line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + chr(10)
        >>> out = io.BytesIO()
        >>> Server(ServeOptions(), (), io.BytesIO(line.encode()), out).run()
        0
        >>> json.loads(out.getvalue())["error"]["data"]
        {'code': 'not-initialized'}
    """

    def __init__(
        self,
        options: ServeOptions,
        roots: tuple[Path, ...],
        reader: BinaryIO,
        writer: BinaryIO,
    ) -> None:
        self.options = options
        self.roots = roots
        self._spellings = _paths.root_spellings(options.allow_paths, roots)
        self.max_line_bytes = _protocol.max_line_bytes(options.max_bytes)
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()
        self._minor = 0  # the negotiated protocol's minor version, set by initialize
        self._cv = threading.Condition()
        self._phase: Phase = "new"
        self._progress = False
        self._running: _Job | None = None
        self._queue: deque[_Job] = deque()
        self._done = threading.Event()
        self._exit_code = 0
        self._broken = False
        self._signalled = False

    # --- lifecycle --------------------------------------------------------------------

    def run(self) -> int:
        """Serve until ``exit``, EOF, a broken pipe or :meth:`finish`; return the status."""
        threads = [
            threading.Thread(
                target=self._guard(self._read_loop), name="sidecar-reader", daemon=True
            ),
            threading.Thread(
                target=self._guard(self._exec_loop), name="sidecar-executor", daemon=True
            ),
        ]
        for thread in threads:
            thread.start()
        while not self._done.wait(_WAIT_SECONDS):
            if self._signalled:
                self.finish(0)
        return self._exit_code

    def signal_stop(self) -> None:
        """Ask :meth:`run` to end the session with status 0; safe in a signal handler.

        It only sets a flag: taking a lock here could deadlock against the main thread,
        which the handler interrupts, while it holds that lock in ``Event.wait``.
        """
        self._signalled = True

    def finish(self, code: int) -> None:
        """End the session with ``code`` (the first call wins); running work is abandoned."""
        with self._cv:
            if self._done.is_set():
                return
            self._exit_code = code
            self._done.set()
            self._cv.notify_all()

    def _guard(self, target: Callable[[], None]) -> Callable[[], None]:
        """A thread body that ends the session instead of dying silently."""

        def body() -> None:
            try:
                target()
            except Exception as exc:  # the session cannot go on without this thread
                # No traceback: it would carry install paths into the log.
                _log.error("sidecar thread failed: %s", type(exc).__name__)
                self.finish(1)

        return body

    # --- writing ----------------------------------------------------------------------

    def send(self, frame: bytes) -> None:
        """Write one frame: one lock, one write, one flush. A broken pipe ends quietly."""
        with self._write_lock:
            if self._broken:
                return
            try:
                self._writer.write(frame)
                self._writer.flush()
            except (OSError, ValueError):  # EPIPE, closed stream: the host has gone
                self._broken = True
                self.finish(0)

    def _reply(
        self, request_id: RequestId, result: object = None, *, raw: bytes | None = None
    ) -> None:
        self.send(_protocol.result_frame(request_id, result, raw=raw))

    def _error(self, request_id: RequestId | None, error: ProtocolError) -> None:
        self.send(_protocol.error_frame(request_id, error))

    def _progress_note(self, job: _Job, step: str, status: str) -> None:
        if self._progress and not self._done.is_set():
            note = {"request_id": job.id, "step": step, "status": status}
            self.send(_protocol.notification_frame("$/progress", note))

    # --- reading ----------------------------------------------------------------------

    def _read_line(self) -> bytes | _TooLong | None:
        """One line without its terminator, :data:`_TOO_LONG`, or None at EOF."""
        limit = self.max_line_bytes + 2  # room for \r\n
        line = self._reader.readline(limit)
        if not line:
            return None
        if line.endswith(b"\n"):
            body = line[:-1].removesuffix(b"\r")
            return _TOO_LONG if len(body) > self.max_line_bytes else body
        if len(line) < limit:  # the last line, without a newline
            return line
        while True:  # discard the rest of the over-long line
            chunk = self._reader.readline(_DISCARD_CHUNK)
            if not chunk or chunk.endswith(b"\n"):
                return _TOO_LONG

    def _read_loop(self) -> None:
        while not self._done.is_set():
            try:
                line = self._read_line()
            except (OSError, ValueError):
                line = None
            if line is None:
                _log.debug("stdin closed")
                self.finish(0)
                return
            if isinstance(line, _TooLong):
                error = ProtocolError(
                    ErrorCode.INVALID_REQUEST,
                    "Line too long",
                    extra={"max_line_bytes": self.max_line_bytes},
                )
                self._error(None, error)
                continue
            self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        parsed = _protocol.parse_line(line)
        if parsed is None:
            return
        if isinstance(parsed, _protocol.Invalid):
            if not parsed.silent:
                self._error(parsed.id, parsed.error)
            return
        if parsed.is_notification:
            self._notification(parsed)
            return
        assert parsed.id is not None  # noqa: S101  # requests always carry an id
        try:
            self._request(parsed.id, parsed.method, parsed.params)
        except ProtocolError as error:
            self._error(parsed.id, error)
        except Exception as exc:  # fail closed: generic text out, real text to stderr
            self._internal(parsed.id, exc)

    def _internal(self, request_id: RequestId | None, exc: BaseException) -> None:
        correlation_id = os.urandom(8).hex()
        _log.error(
            "internal error [correlation_id=%s]: %s: %s",
            correlation_id,
            type(exc).__name__,
            exc,
            extra={"request_id": request_id},
        )
        error = ProtocolError(ErrorCode.INTERNAL_ERROR, extra={"correlation_id": correlation_id})
        self._error(request_id, error)

    # --- dispatch ---------------------------------------------------------------------

    def _notification(self, message: _protocol.Message) -> None:
        if message.method == "exit":
            with self._cv:
                code = 0 if self._phase == "shutdown" else 1
            self.finish(code)
        elif message.method == "$/cancelRequest":
            target = _protocol.check_cancel(message.params)
            if target is not None:
                self._cancel(target)
        # Every other notification (``$/`` or not, before initialize or not) is ignored.

    def _request(self, request_id: RequestId, method: str, params: dict[str, Any]) -> None:
        _log.debug("request %s", method[:64], extra={"request_id": request_id})
        if method not in _KNOWN:
            raise ProtocolError(ErrorCode.METHOD_NOT_FOUND)
        with self._cv:
            phase = self._phase
        if phase == "shutdown":
            raise ProtocolError(ErrorCode.SHUTTING_DOWN)
        if method == "initialize":
            self._initialize(request_id, params, phase)
        elif phase != "ready":
            raise ProtocolError(ErrorCode.NOT_INITIALIZED)
        elif method in _SINCE_MINOR:
            # read is protocol 1.1, convert and ags4.check 1.2: an older session lacks them
            if self._minor < _SINCE_MINOR[method]:
                raise ProtocolError(ErrorCode.METHOD_NOT_FOUND)
            kind: _DocumentKind = method  # type: ignore[assignment]
            self._submit_document(kind, request_id, params)
        elif method == "shutdown":
            self._shutdown(request_id, params)
        else:
            self._cheap(request_id, method, params)

    def _cheap(self, request_id: RequestId, method: str, params: dict[str, Any]) -> None:
        result: object
        if method == "ping":
            _protocol.check_empty(params)
            result = {}
        elif method == "versions":
            _protocol.check_empty(params)
            result = _service.versions()
        elif method == "rules.list":
            result = _service.rules(_protocol.check_rules_list(params))
        else:
            result = _service.explain(_protocol.check_explain(params))
        if isinstance(result, _service.Outcome):
            raise ProtocolError(ErrorCode.INVALID_PARAMS, errors=result.errors)
        self._reply(request_id, result)

    def _initialize(self, request_id: RequestId, params: dict[str, Any], phase: Phase) -> None:
        if phase != "new":
            raise ProtocolError(ErrorCode.INVALID_REQUEST, "Already initialized")
        known = tuple(_service.versions()["diggs_versions"])
        checked = _protocol.check_initialize(params, known)
        version = _protocol.negotiate(checked.protocol_version)
        if checked.client_name is not None:
            _log.info("client %s", checked.client_name, extra={"request_id": request_id})
        with self._cv:
            self._phase = "initializing"
            self._progress = checked.progress
            self._queue.append(_Job("initialize", request_id, (version, checked)))
            self._cv.notify_all()
            # Read nothing more until warm-up ends, so hosts may pipeline requests.
            while self._phase == "initializing" and not self._done.is_set():
                self._cv.wait()

    def _submit_document(
        self, kind: _DocumentKind, request_id: RequestId, params: dict[str, Any]
    ) -> None:
        """Queue a validation or a read: at most one running (or about to run) and one waiting.

        The duplicate-id and busy checks come first, so a refused request never has
        its content decoded. Only this (reader) thread adds jobs, so the queue can
        only shrink between the checks and the append.
        """
        key = _key(request_id)
        with self._cv:
            jobs = [j for j in (self._running, *self._queue) if j is not None]
            if any(j.key == key for j in jobs):
                raise ProtocolError(ErrorCode.INVALID_REQUEST, "Duplicate id")
            if len(jobs) >= 2:  # noqa: PLR2004  # one running plus one waiting
                raise ProtocolError(ErrorCode.BUSY)
        check = getattr(_protocol, _CHECKS[kind])
        checked = check(params, max_bytes=self.options.max_bytes, time_limit=self.options.timeout)
        job = _Job(kind, request_id, checked)
        with self._cv:
            waiting = any(j is not None for j in (self._running, *self._queue))
            self._queue.append(job)
            self._cv.notify_all()
        if waiting:
            self._progress_note(job, "queue", "waiting")

    def _shutdown(self, request_id: RequestId, params: dict[str, Any]) -> None:
        """Refuse new work; drop the waiting validation; answer after the running one.

        A validation queued while nothing was running counts as running (the
        executor may not have picked it up yet), so the outcome does not depend on
        thread timing.
        """
        _protocol.check_empty(params)
        with self._cv:
            self._phase = "shutdown"
            keep = 0 if self._running is not None else 1
            dropped = list(self._queue)[keep:]
            for job in dropped:
                self._queue.remove(job)
            self._queue.append(_Job("shutdown", request_id))
            self._cv.notify_all()
        for job in dropped:
            self._error(job.id, ProtocolError(ErrorCode.SHUTTING_DOWN))

    def _cancel(self, target: RequestId) -> None:
        """Drop a queued validation (answered at once) or flag the running one."""
        key = _key(target)
        dropped = None
        with self._cv:
            for job in self._queue:
                if job.kind in _DOCUMENT and job.key == key:
                    dropped = job
                    self._queue.remove(job)
                    break
            running = self._running
            if (
                dropped is None
                and running is not None
                and running.kind in _DOCUMENT
                and running.key == key
            ):
                running.cancel.set()
        if dropped is not None:
            self._error(dropped.id, ProtocolError(ErrorCode.CANCELLED))

    # --- executor ---------------------------------------------------------------------

    def _exec_loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._done.is_set():
                    self._cv.wait()
                if self._done.is_set():
                    return
                job = self._queue.popleft()
                self._running = job
            try:
                self._execute(job)
            finally:
                with self._cv:
                    self._running = None
                    self._cv.notify_all()

    def _execute(self, job: _Job) -> None:
        if job.kind == "shutdown":
            self._reply(job.id, None)
        elif job.kind == "initialize":
            self._warm(job)
        else:
            try:
                if job.kind == "read":
                    self._read(job)
                elif job.kind == "convert":
                    self._convert(job)
                elif job.kind == "ags4.check":
                    self._ags4_check(job)
                else:
                    self._validate(job)
            except ProtocolError as error:
                if not self._done.is_set():
                    self._error(job.id, error)
            except Exception as exc:  # fail closed
                self._internal(job.id, exc)

    def _warm(self, job: _Job) -> None:
        version, checked = job.params
        try:
            _service.warm_worker(checked.diggs_versions)
        except Exception as exc:  # ResourceError, or anything else: the data is unusable
            correlation_id = os.urandom(8).hex()
            _log.error(  # the traceback could carry install paths
                "warm-up failed [correlation_id=%s]: %s: %s",
                correlation_id,
                type(exc).__name__,
                exc,
                extra={"request_id": job.id},
            )
            error = ProtocolError(
                ErrorCode.RESOURCE_ERROR, extra={"correlation_id": correlation_id}
            )
            self._error(job.id, error)
            self.finish(3)
            return
        info = _service.versions()
        options = self.options
        result = {
            "protocol_version": version,
            "pydiggs_version": info["pydiggs_version"],
            "findings_schema_version": info["findings_schema_version"],
            "findings_schema_url": info["findings_schema_url"],
            "diggs_versions": info["diggs_versions"],
            "reference_data": info["reference_data"],
            "capabilities": {
                "validate": {"content": True, "path": bool(self.roots)},
                "cancel": True,
                "progress": checked.progress,
                "rules": True,
                "ping": True,
            },
            "limits": {
                "max_bytes": options.max_bytes,
                "max_path_bytes": options.max_path_bytes,
                "max_line_bytes": self.max_line_bytes,
                "max_findings": options.max_findings,
                "max_response_bytes_estimate": options.max_findings * 2048 + 65536,
                "time_budget_s": options.timeout,
            },
        }
        minor = int(version.split(".")[1])
        if minor >= 1:  # protocol 1.1: the read method
            result.update(_service.model_info())
            result["capabilities"]["read"] = {
                "content": True,
                "path": bool(self.roots),
                "select": True,
            }
            result["limits"]["max_result_bytes"] = _protocol.MAX_RESULT_BYTES
        if minor >= 2:  # noqa: PLR2004  # protocol 1.2: convert and ags4.check
            result["capabilities"]["convert"] = {
                "content": True,
                "path": bool(self.roots),
                "to": list(_service.CONVERT_TARGETS),
            }
            result["capabilities"]["ags4_check"] = {
                "content": True,
                "path": bool(self.roots),
                "dictionary": True,
            }
        with self._cv:
            if self._phase == "initializing":
                self._minor = minor
                self._phase = "ready"
        self._reply(job.id, result)

    def _read_path(self, path: str) -> bytes:
        """Vet, open, verify and read a by-path document."""
        try:
            _paths.vet_request_path(path)
            fd = _paths.open_verified(path, self.roots, spellings=self._spellings)
        except _paths.NotPermittedError:
            raise ProtocolError(ErrorCode.PATH_NOT_PERMITTED) from None
        try:
            data = _service.read_fd(fd, max_bytes=self.options.max_path_bytes, name=_basename(path))
        finally:
            os.close(fd)
        if isinstance(data, _service.Outcome):
            assert data.code is not None  # noqa: S101  # an Outcome from read_fd is an error
            raise _protocol.service_error(data.code, data.detail)
        return data

    def _document(self, job: _Job, step: str) -> tuple[bytes, str | None, int, Callable[[], bool]]:
        """A document job's content, name, size limit and cancellation hook."""
        params: (
            _protocol.ValidateParams
            | _protocol.ReadParams
            | _protocol.ConvertParams
            | _protocol.Ags4CheckParams
        ) = job.params
        self._progress_note(job, step, "started")
        name = params.name
        if params.path is not None:
            content = self._read_path(params.path)
            limit = self.options.max_path_bytes
            if name is None:
                name = _basename(params.path)
        else:
            assert params.content is not None  # noqa: S101  # exactly one is set
            content, limit = params.content, self.options.max_bytes

        def should_stop() -> bool:
            stop = job.cancel.is_set() or self._done.is_set()
            if not stop:
                self._progress_note(job, step, "running")
            return stop

        return content, name, limit, should_stop

    def _read(self, job: _Job) -> None:
        params: _protocol.ReadParams = job.params
        content, name, limit, should_stop = self._document(job, "read")
        request = _service.ReadRequest(
            content=content,
            name=name,
            diggs_version=params.diggs_version,
            select=params.select,
            max_bytes=limit,
            time_budget=params.time_budget,
            max_result_bytes=_protocol.MAX_RESULT_BYTES,
        )
        self._answer(job, _service.read(request, should_stop=should_stop))

    def _convert(self, job: _Job) -> None:
        params: _protocol.ConvertParams = job.params
        content, name, limit, should_stop = self._document(job, "convert")
        request = _service.ConvertRequest(
            content=content,
            name=name,
            to=params.to,
            diggs_version=params.diggs_version,
            max_bytes=limit,
            time_budget=params.time_budget,
            max_result_bytes=_protocol.MAX_RESULT_BYTES,
        )
        self._answer(job, _service.convert(request, should_stop=should_stop))

    def _ags4_check(self, job: _Job) -> None:
        params: _protocol.Ags4CheckParams = job.params
        content, name, limit, should_stop = self._document(job, "ags4.check")
        # Each input keeps its own transport's limit: inline bytes max_bytes, a path
        # max_path_bytes, whichever way the file itself came.
        dictionary, dictionary_limit = params.dictionary, self.options.max_bytes
        if params.dictionary_path is not None:
            dictionary = self._read_path(params.dictionary_path)
            dictionary_limit = self.options.max_path_bytes
        request = _service.Ags4CheckRequest(
            content=content,
            name=name,
            dictionary=dictionary,
            dictionary_max_bytes=dictionary_limit,
            max_bytes=limit,
            max_findings=self.options.max_findings,
            time_budget=params.time_budget,
        )
        self._answer(job, _service.ags4_check(request, should_stop=should_stop))

    def _validate(self, job: _Job) -> None:
        params: _protocol.ValidateParams = job.params
        content, name, limit, should_stop = self._document(job, "validate")

        request = _service.Request(
            content=content,
            name=name,
            checks=params.checks,
            diggs_version=params.diggs_version,
            max_bytes=limit,
            max_findings=self.options.max_findings,
            time_budget=params.time_budget,
        )
        self._answer(job, _service.validate(request, should_stop=should_stop))

    def _answer(self, job: _Job, outcome: _service.Outcome) -> None:
        """Send a document job's outcome: the document, or its protocol error."""
        if self._done.is_set():
            return  # abandoned (EOF, exit, signal): nobody is listening
        if outcome.ok:
            assert outcome.document is not None  # noqa: S101  # ok means a document
            self._reply(job.id, raw=outcome.document)
            return
        assert outcome.code is not None  # noqa: S101  # not ok means a code
        extra: dict[str, Any] = {}
        if outcome.correlation_id is not None:
            extra["correlation_id"] = outcome.correlation_id
        if outcome.kind is not None:
            extra["kind"] = outcome.kind
        if outcome.code is _service.Code.RESULT_TOO_LARGE:
            extra["max_result_bytes"] = _protocol.MAX_RESULT_BYTES
        error = _protocol.service_error(outcome.code, outcome.detail, **extra)
        if outcome.errors:
            error = ProtocolError(error.error, None, outcome.errors, error.extra)
        self._error(job.id, error)


#: The parameter check of each document method (a name in ``_protocol``, looked up per call).
_CHECKS: Final = {
    "validate": "check_validate",
    "read": "check_read",
    "convert": "check_convert",
    "ags4.check": "check_ags4_check",
}


# --- process setup ----------------------------------------------------------------------


@contextlib.contextmanager
def _claimed_stdout() -> Iterator[int]:
    """Move frames to a private copy of fd 1 and point fd 1 (and ``sys.stdout``) at stderr.

    Anything else that writes to stdout (a stray ``print``, C code, a child process)
    lands on stderr, so it can never corrupt a frame. Restored on exit.
    """
    sys.stdout.flush()
    frames_fd = os.dup(1)  # non-inheritable (PEP 446)
    try:
        os.dup2(2, 1)
    except OSError:
        os.close(frames_fd)
        raise
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import msvcrt  # noqa: PLC0415  # Windows only

        msvcrt.setmode(0, os.O_BINARY)  # type: ignore[attr-defined,unused-ignore]
        msvcrt.setmode(frames_fd, os.O_BINARY)  # type: ignore[attr-defined,unused-ignore]
    try:
        yield frames_fd
    finally:
        sys.stdout = old_stdout
        with contextlib.suppress(OSError):
            os.dup2(frames_fd, 1)
        os.close(frames_fd)


@contextlib.contextmanager
def _signals(server: Server) -> Iterator[None]:
    """SIGTERM, SIGINT (and SIGBREAK on Windows) end the session with status 0.

    The handler takes no lock; :meth:`Server.run` notices the flag and finishes.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    names = ("SIGTERM", "SIGINT", "SIGBREAK")
    numbers = [getattr(signal, n) for n in names if hasattr(signal, n)]

    def handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        server.signal_stop()  # a flag only: run() calls finish() outside the handler

    previous = {n: signal.signal(n, handler) for n in numbers}
    try:
        yield
    finally:
        for number, old in previous.items():
            signal.signal(number, old)


def serve(options: ServeOptions) -> int:
    """Run the sidecar on this process's stdin and stdout (``serve_stdio``)."""
    try:
        roots = _paths.canonical_roots(options.allow_paths)
    except ValueError as exc:
        print(f"pydiggs serve: {exc}", file=sys.stderr)
        return 2
    with contextlib.ExitStack() as stack:
        try:
            frames_fd = stack.enter_context(_claimed_stdout())
        except (OSError, AttributeError, ValueError):
            print("pydiggs serve: stdin and stdout must be open (pipes)", file=sys.stderr)
            return 2
        # Never closed here: a reader thread may still be blocked on stdin, and closing a
        # buffered stream waits for its lock. closefd=False leaves the descriptors alone.
        reader = open(0, "rb", buffering=1 << 16, closefd=False)  # noqa: SIM115
        writer = open(frames_fd, "wb", buffering=1 << 16, closefd=False)  # noqa: SIM115
        server = Server(options, roots, reader, writer)
        _log.info("sidecar ready for initialize")
        stack.enter_context(_signals(server))
        return server.run()
