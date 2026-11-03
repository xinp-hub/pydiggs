"""A supervised pool of spawned worker processes with a hard limit per job.

Stdlib only. Each worker is a fresh interpreter (``spawn`` on every OS) that runs one
job at a time. A job that runs past its timeout is killed with its worker; a worker
that dies, answers with something unexpected, or lets an exception escape is
replaced too. A worker is reused only after it returned a result normally, so no
state of a failed job can reach the next one.

The two directions are deliberately not alike. Parent to worker carries the job
(a module-level function and its arguments) through :mod:`multiprocessing`'s
pickling: the parent trusts itself. Worker to parent never unpickles anything: a
worker sends raw frames (``Connection.send_bytes``) that the parent reads with
``recv_bytes``. A result is the job's own ``bytes``, returned untouched; an error is
a small JSON object of strings and ints. A worker that has processed hostile input
therefore cannot make the parent run code.

Per-worker limits are set before the initialiser runs: on Linux an address-space cap
(``RLIMIT_AS``, ``memory_mb``); on Linux and macOS a CPU-seconds backstop per job
(``RLIMIT_CPU``, raised before each job), in case the parent's own kill is late. macOS
does not enforce ``RLIMIT_AS`` and Windows has neither, so there the timeout is the
only limit (use a container or a Job Object at the host).
"""

from __future__ import annotations

import contextlib
import json
import math
import multiprocessing
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from multiprocessing.context import SpawnContext, SpawnProcess

__all__ = [
    "DEFAULT_MAX_REPLY_BYTES",
    "JobFailedError",
    "JobTimeoutError",
    "PoolClosedError",
    "PoolError",
    "WorkerCrashedError",
    "WorkerPool",
]

#: Largest result frame accepted from a worker (a findings document is capped at 16 MiB).
DEFAULT_MAX_REPLY_BYTES: Final = 64 * 2**20
#: Seconds a worker is given to exit after SIGTERM before it is killed.
_GRACE: Final = 5.0
#: Most characters of an exception's text sent back in an error frame.
_MAX_ERROR_TEXT: Final = 2000

#: Only Linux enforces ``RLIMIT_AS`` (a plain bool, so type checkers on other OSes
#: still check the Linux branch).
_ENFORCES_RLIMIT_AS: Final[bool] = sys.platform.startswith("linux")
_HAS_RLIMIT_CPU: Final[bool] = sys.platform != "win32"

_READY: Final = b"I"
_RESULT: Final = b"R"
_ERROR: Final = b"E"


class PoolError(Exception):
    """Base class: a job did not produce a result."""


class JobTimeoutError(PoolError):
    """The job ran past its timeout; its worker was killed and replaced."""


class WorkerCrashedError(PoolError):
    """The worker died, or answered with something that is not a valid frame."""


class PoolClosedError(PoolError):
    """The pool was closed before or while the job waited for a worker."""


class JobFailedError(PoolError):
    """The job (or the worker's initialiser) raised; the worker was replaced.

    Only plain data crosses back: the exception's type name, its text (cut to 2,000
    characters) and its ``errno`` when it had an integer one. ``phase`` is ``"init"``
    when the initialiser failed, else ``"job"``.

    Example:
        >>> exc = JobFailedError("job", "ValueError", "bad", None)
        >>> exc.type_name, str(exc)
        ('ValueError', 'ValueError: bad')
    """

    def __init__(self, phase: str, type_name: str, message: str, errno: int | None) -> None:
        super().__init__(f"{type_name}: {message}" if message else type_name)
        self.phase = phase
        self.type_name = type_name
        self.message = message
        self.errno = errno


def _error_frame(phase: str, exc: BaseException) -> bytes:
    errno = getattr(exc, "errno", None)
    body = {
        "phase": phase,
        "type": type(exc).__name__,
        "message": str(exc)[:_MAX_ERROR_TEXT],
        "errno": errno if type(errno) is int else None,
    }
    return _ERROR + json.dumps(body, ensure_ascii=True).encode("ascii")


def _parse_error(payload: bytes) -> JobFailedError:
    """Rebuild a :class:`JobFailedError` from an error frame; anything malformed is a crash."""
    try:
        body = json.loads(payload)
    except ValueError as exc:
        raise WorkerCrashedError("malformed error frame") from exc
    if not isinstance(body, dict):
        raise WorkerCrashedError("malformed error frame")
    phase, type_name, message, errno = (body.get(k) for k in ("phase", "type", "message", "errno"))
    if not (
        isinstance(phase, str)
        and isinstance(type_name, str)
        and isinstance(message, str)
        and (errno is None or type(errno) is int)
    ):
        raise WorkerCrashedError("malformed error frame")
    return JobFailedError(phase, type_name[:200], message[:_MAX_ERROR_TEXT], errno)


def _limit_memory(memory_mb: int | None) -> None:
    """Cap the address space on Linux (the only OS that enforces ``RLIMIT_AS``)."""
    if memory_mb is None or not _ENFORCES_RLIMIT_AS:
        return
    import resource  # noqa: PLC0415  # POSIX only

    cap = memory_mb * 2**20
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def _limit_cpu(cpu_seconds: float | None) -> None:
    """Allow ``cpu_seconds`` more CPU time from now (soft limit; SIGXCPU kills)."""
    if cpu_seconds is None or not _HAS_RLIMIT_CPU:
        return
    import resource  # noqa: PLC0415  # POSIX only

    usage = resource.getrusage(resource.RUSAGE_SELF)
    used = usage.ru_utime + usage.ru_stime
    _, hard = resource.getrlimit(resource.RLIMIT_CPU)
    soft = math.ceil(used + cpu_seconds)
    if hard != resource.RLIM_INFINITY:
        soft = min(soft, hard)
    resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))


def _worker_main(
    requests: Connection,
    replies: Connection,
    initializer: Callable[[], object] | None,
    memory_mb: int | None,
    cpu_seconds: float | None,
) -> None:
    """The worker's loop: limits, initialiser, then one job at a time until told to stop."""
    try:
        _limit_memory(memory_mb)
        if initializer is not None:
            initializer()
    except BaseException as exc:  # reported to the parent as data
        replies.send_bytes(_error_frame("init", exc))
        return
    replies.send_bytes(_READY)
    while True:
        try:
            job = requests.recv()  # from the parent, which trusts itself
        except (EOFError, OSError):
            return
        if job is None:
            return
        function, args = job
        _limit_cpu(cpu_seconds)
        try:
            result = function(*args)
            if not isinstance(result, bytes):
                raise TypeError(f"a pool job must return bytes, not {type(result).__name__}")
        except BaseException as exc:  # reported to the parent as data
            replies.send_bytes(_error_frame("job", exc))
            return  # the parent replaces this worker
        replies.send_bytes(_RESULT + result)


@dataclass(eq=False)
class _Worker:
    process: SpawnProcess
    requests: Connection
    replies: Connection
    ready: bool = False

    def take_ready(self, timeout: float | None) -> bool:
        """Consume the ready frame; False when it has not come within ``timeout``.

        Raises:
            JobFailedError: The initialiser failed.
            WorkerCrashedError: The worker died or sent something else.
        """
        if self.ready:
            return True
        try:
            if not self.replies.poll(timeout):
                return False
            frame = self.replies.recv_bytes(DEFAULT_MAX_REPLY_BYTES)
        except (EOFError, OSError) as exc:
            raise WorkerCrashedError("worker died while starting") from exc
        if frame == _READY:
            self.ready = True
            return True
        if frame[:1] == _ERROR:
            raise _parse_error(frame[1:])
        raise WorkerCrashedError("unexpected frame while starting")

    def stop(self, *, graceful: bool = False) -> None:
        """Stop the process (politely when ``graceful`` and idle) and close both pipes."""
        if graceful and self.process.is_alive():
            with contextlib.suppress(OSError, ValueError):
                self.requests.send(None)
            self.process.join(1.0)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(_GRACE)
            if self.process.is_alive():
                self.process.kill()
        self.process.join()
        self.requests.close()
        self.replies.close()


class WorkerPool:
    """A fixed number of spawned workers, each running one job at a time.

    :meth:`run` is blocking and thread-safe: call it from as many threads as you like
    (for example from ``anyio.to_thread``); a caller waits for a free worker, then the
    timeout starts. Workers start lazily on first use, or all at once with
    :meth:`start`. :meth:`ready` reports whether every worker finished its initialiser.

    Args:
        workers: Number of worker processes (at least 1).
        initializer: Called once in each new worker, after the limits are set (for
            example :func:`pydiggs.warm_up`). Must be a module-level function.
        memory_mb: Address-space cap per worker in MiB; enforced on Linux only.
        cpu_seconds: CPU seconds a job may use before the kernel stops its worker
            (Linux and macOS): a backstop behind the timeout, not a replacement.
        max_reply_bytes: Largest result frame accepted; larger is a crash.

    Example:
        >>> from pydiggs._pool import WorkerPool
        >>> with WorkerPool(1) as pool:
        ...     pool.run(bytes.upper, b"ok", timeout=60)
        b'OK'
    """

    def __init__(
        self,
        workers: int = 1,
        *,
        initializer: Callable[[], object] | None = None,
        memory_mb: int | None = None,
        cpu_seconds: float | None = None,
        max_reply_bytes: int = DEFAULT_MAX_REPLY_BYTES,
    ) -> None:
        if type(workers) is not int or workers < 1:
            raise ValueError(f"workers must be a positive int, got {workers!r}")
        if memory_mb is not None and (type(memory_mb) is not int or memory_mb < 1):
            raise ValueError(f"memory_mb must be a positive int or None, got {memory_mb!r}")
        if cpu_seconds is not None and not cpu_seconds > 0:
            raise ValueError(f"cpu_seconds must be positive or None, got {cpu_seconds!r}")
        self.workers = workers
        self._initializer = initializer
        self._memory_mb = memory_mb
        self._cpu_seconds = cpu_seconds
        self._max_reply = max_reply_bytes
        self._context: SpawnContext = multiprocessing.get_context("spawn")
        self._cond = threading.Condition()
        #: Free slots: a started worker, or None for one to start when needed.
        self._idle: list[_Worker | None] = [None] * workers
        self._busy: set[_Worker] = set()
        self._closed = False

    def __enter__(self) -> WorkerPool:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _spawn(self) -> _Worker:
        requests_in, requests_out = self._context.Pipe(duplex=False)
        replies_in, replies_out = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_worker_main,
            args=(requests_in, replies_out, self._initializer, self._memory_mb, self._cpu_seconds),
            daemon=True,
        )
        try:
            process.start()
        finally:
            requests_in.close()
            replies_out.close()
        return _Worker(process, requests_out, replies_in)

    def start(self) -> None:
        """Start every worker that is not running yet (they warm up in the background)."""
        with self._cond:
            if self._closed:
                raise PoolClosedError("the pool is closed")
            self._idle = [w if w is not None else self._spawn() for w in self._idle]

    def ready(self) -> bool:
        """True when every worker is running and its initialiser has finished.

        Never blocks. A worker whose initialiser failed is dropped (its slot starts a
        new worker on the next job), so this stays False.
        """
        with self._cond:
            if self._closed:
                return False
            ok = all(w.ready for w in self._busy)
            for index, worker in enumerate(self._idle):
                if worker is None:
                    ok = False
                    continue
                try:
                    ok = worker.take_ready(0) and ok
                except PoolError:
                    worker.stop()
                    self._idle[index] = None
                    ok = False
            return ok

    def _acquire(self) -> _Worker:
        with self._cond:
            while not self._idle and not self._closed:
                self._cond.wait()
            if self._closed:
                raise PoolClosedError("the pool is closed")
            worker = self._idle.pop()
            if worker is None:
                try:
                    worker = self._spawn()
                except BaseException:
                    self._idle.append(None)
                    self._cond.notify()
                    raise
            self._busy.add(worker)
            return worker

    def _release(self, worker: _Worker, *, healthy: bool) -> None:
        if not healthy:
            worker.stop()
        with self._cond:
            self._busy.discard(worker)
            if self._closed:
                if healthy:
                    worker.stop(graceful=True)
                return
            replacement: _Worker | None = worker
            if not healthy:
                try:  # respawn now, so the replacement warms up before it is needed
                    replacement = self._spawn()
                except OSError:
                    replacement = None
            self._idle.append(replacement)
            self._cond.notify()

    def run(self, function: Callable[..., bytes], *args: Any, timeout: float | None) -> bytes:
        """Run ``function(*args)`` in a worker and return the bytes it returned.

        The timeout covers the worker's start-up (when it is new) and the job, not
        the wait for a free worker.

        Args:
            function: A module-level function returning ``bytes``.
            *args: Its arguments (pickled to the worker).
            timeout: Seconds before the worker is killed; None waits forever.

        Returns:
            The job's bytes, exactly as the worker sent them.

        Raises:
            JobTimeoutError: The timeout passed; the worker was killed and replaced.
            JobFailedError: The job or the initialiser raised; the worker was replaced.
            WorkerCrashedError: The worker died or sent an invalid frame; it was replaced.
            PoolClosedError: The pool is closed.
        """
        worker = self._acquire()
        deadline = None if timeout is None else time.monotonic() + timeout
        healthy = False
        try:
            if not worker.take_ready(_remaining(deadline)):
                raise JobTimeoutError(f"no result within {timeout:g} s")
            try:
                worker.requests.send((function, args))
                if not worker.replies.poll(_remaining(deadline)):
                    raise JobTimeoutError(f"no result within {timeout:g} s")
                frame = worker.replies.recv_bytes(self._max_reply)
            except (EOFError, OSError) as exc:
                raise WorkerCrashedError("worker died during the job") from exc
            kind, payload = frame[:1], frame[1:]
            if kind == _RESULT:
                healthy = True
                return payload
            if kind == _ERROR:
                raise _parse_error(payload)
            raise WorkerCrashedError("unexpected frame from worker")
        finally:
            self._release(worker, healthy=healthy)

    def close(self) -> None:
        """Stop every worker; running jobs end with :class:`WorkerCrashedError`. Idempotent."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            idle, self._idle = self._idle, []
            busy = list(self._busy)
            self._cond.notify_all()
        for worker in idle:
            if worker is not None:
                worker.stop(graceful=True)
        for worker in busy:
            if worker.process.is_alive():
                worker.process.terminate()


def _remaining(deadline: float | None) -> float | None:
    return None if deadline is None else max(0.0, deadline - time.monotonic())
