"""The one entry point every adapter uses for untrusted input.

``pydiggs serve --stdio``, ``pydiggs.server`` and ``pydiggs.mcp_server`` validate
documents through :func:`validate` (in the calling thread) or :func:`validate_in_pool`
(in a worker process with a hard timeout), and answer rule and version questions
through :func:`rules`, :func:`explain` and :func:`versions`. They never call
:func:`pydiggs.validate`, :func:`pydiggs.validate_bytes` or the legacy validator
themselves (a test enforces this), so every remote caller gets the same pinned
settings:

* only the bundled schema, dictionaries and Schematron rules;
* ``allow_network=False``, ``include_snippets=False``, local dictionaries off;
* string parameters checked and capped before use, with fixed error texts that
  never echo the caller's value;
* install, virtual-environment, temp and home folders scrubbed from finding text;
* a typed :class:`Outcome` instead of exceptions: the document as one line of JSON
  bytes, or an error :class:`Code`. Internal errors carry a fixed text and a
  correlation id; the real text goes to the ``pydiggs.service`` logger only.

The protocol mappings (RFC 9457, JSON-RPC, MCP ``isError``) live in each adapter and
start from :class:`Code`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import json
import logging
import math
import secrets
import site
import sys
import tempfile
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import pydiggs
from pydiggs import _xml
from pydiggs._versions import version_info
from pydiggs.detect import SCHEMA_PATHS
from pydiggs.errors import InstanceReadError, ResourceError
from pydiggs.findings import _json, run
from pydiggs.findings.model import Report, Step
from pydiggs.findings.rules import MAX_RULE_ID_CHARS, explain_rule, rule_catalogue

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydiggs._pool import WorkerPool

__all__ = [
    "MAX_NAME_INPUT_CHARS",
    "Code",
    "Outcome",
    "Request",
    "explain",
    "make_pool",
    "read_fd",
    "rules",
    "validate",
    "validate_in_pool",
    "versions",
    "warm_worker",
]

#: Longest ``name`` accepted before reduction to a basename (``Report.path`` keeps 255).
MAX_NAME_INPUT_CHARS: Final = 4096
#: Longest ``diggs_version``, ``checks`` item or ``step`` accepted.
_MAX_TOKEN_CHARS: Final = 64
#: Most ``checks`` items accepted (each step at most once is plenty).
_MAX_CHECKS: Final = 32
#: Largest per-request time budget or timeout: one day (as ``validate --timeout``).
MAX_SECONDS: Final = 86_400
#: The extra CPU seconds a pooled job may use past its timeout before the kernel stops it.
_CPU_SLACK: Final = 5.0

_STEP_VALUES: Final = frozenset(s.value for s in run.NATIVE_STEPS)
_ALL_STEPS: Final = frozenset(s.value for s in Step)

_log = logging.getLogger("pydiggs.service")


class Code(StrEnum):
    """Why a request produced no document. Adapters map each to their own protocol.

    Examples:
        >>> Code.INPUT_TOO_LARGE.value
        'input-too-large'
    """

    INPUT_TOO_LARGE = "input-too-large"
    COMPRESSED_INPUT = "compressed-input"
    INPUT_UNREADABLE = "input-unreadable"
    INVALID_PARAMETER = "invalid-parameter"
    RESOURCE_ERROR = "resource-error"
    INTERNAL = "internal"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


_GENERIC: Final = {
    Code.RESOURCE_ERROR: "pydiggs's bundled reference data could not be loaded.",
    Code.INTERNAL: "An internal error occurred.",
    Code.CANCELLED: "The request was cancelled.",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    """One validation request. Limits come from the adapter's settings, not the caller.

    ``name``, ``checks`` and ``diggs_version`` may come from the caller; they are
    checked by :func:`validate`, which answers ``invalid-parameter`` rather than
    raising. The limits are the adapter's own and are checked here.

    Examples:
        >>> Request(content=b"<Diggs/>", name="a.xml", max_bytes=1024).format
        'findings'
        >>> Request(content=b"", max_bytes=0)
        Traceback (most recent call last):
        ...
        ValueError: max_bytes must be a positive int, got 0
    """

    content: bytes
    name: str | None = None
    checks: Sequence[str] | None = None
    diggs_version: str | None = None
    format: Literal["findings", "sarif"] = "findings"
    max_bytes: int = 25 * 2**20
    max_findings: int | None = run.DEFAULT_MAX_FINDINGS
    time_budget: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes):
            raise TypeError(f"content must be bytes, not {type(self.content).__name__}")
        if self.format not in ("findings", "sarif"):
            raise ValueError(f"format must be 'findings' or 'sarif', got {self.format!r}")
        if type(self.max_bytes) is not int or self.max_bytes < 1:
            raise ValueError(f"max_bytes must be a positive int, got {self.max_bytes!r}")
        if self.max_findings is not None and (
            type(self.max_findings) is not int or self.max_findings < 1
        ):
            raise ValueError(
                f"max_findings must be a positive int or None, got {self.max_findings!r}"
            )
        if self.time_budget is not None and not _valid_seconds(self.time_budget):
            raise ValueError(
                f"time_budget must be a positive number of seconds, at most {MAX_SECONDS}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """What a request produced: a document, or an error code with a safe text.

    ``document`` is UTF-8 JSON on one line (a diggs-findings 1.0 document, or a SARIF
    2.1.0 log when asked for). ``detail`` never contains an absolute path or a value
    the caller sent, only the reported basename. ``errors`` lists ``(parameter,
    problem)`` pairs for ``invalid-parameter``. ``correlation_id`` ties a
    ``resource-error`` or ``internal`` answer to the log line with the real text.

    Examples:
        >>> Outcome(document=b"{}").ok
        True
        >>> Outcome(code=Code.TIMEOUT, detail="slow").ok
        False
    """

    code: Code | None = None
    document: bytes | None = None
    detail: str = ""
    errors: tuple[tuple[str, str], ...] = ()
    correlation_id: str | None = None

    @property
    def ok(self) -> bool:
        """True when there is a document (even one full of findings)."""
        return self.code is None


def _valid_seconds(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and 0 < value <= MAX_SECONDS
        and math.isfinite(value)
    )


# --- scrubbing --------------------------------------------------------------------------


def _spellings(location: Path) -> set[str]:
    """Ways a message may spell a folder: as is, resolved, POSIX, ``file:`` URI, %-encoded."""
    forms: set[str] = set()
    for path in {location, location.resolve()}:
        posix = path.as_posix()
        forms |= {str(path), posix, urllib.parse.quote(posix)}
        try:
            uri = path.as_uri()
        except ValueError:
            continue
        forms |= {uri, urllib.parse.unquote(uri)}
    return {f for f in forms if len(f) > 1 and Path(f).anchor != f}


@cache
def _replacements() -> tuple[tuple[str, str], ...]:
    """``(spelling, placeholder)`` for every folder to hide, longest spelling first."""
    folders: list[tuple[str, str]] = [(str(Path(pydiggs.__file__).parent), "<pydiggs>")]
    site_dirs = list(getattr(site, "getsitepackages", list)())
    user_site = getattr(site, "getusersitepackages", lambda: None)()
    if isinstance(user_site, str):
        site_dirs.append(user_site)
    folders += [(d, "<site-packages>") for d in site_dirs]
    prefixes = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
    folders += [(p, "<python>") for p in prefixes]
    folders.append((tempfile.gettempdir(), "<tmp>"))
    with contextlib.suppress(KeyError, RuntimeError, OSError):
        folders.append((str(Path.home()), "~"))
    pairs = {
        (spelling, label) for folder, label in folders for spelling in _spellings(Path(folder))
    }
    return tuple(sorted(pairs, key=lambda p: (-len(p[0]), p[0], p[1])))


def _scrub(text: str) -> str:
    """Replace install, environment, temp and home folders in ``text`` with placeholders.

    Examples:
        >>> import tempfile
        >>> _scrub(tempfile.gettempdir() + "/x.xsd")
        '<tmp>/x.xsd'
    """
    for spelling, label in _replacements():
        if spelling in text:
            text = text.replace(spelling, label)
    return text


def _scrubbed(report: Report) -> Report:
    findings = tuple(dataclasses.replace(f, message=_scrub(f.message)) for f in report.findings)
    if findings == report.findings:
        return report
    return dataclasses.replace(report, findings=findings)


# --- validation -------------------------------------------------------------------------


def _parameter_errors(request: Request) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    errors: list[tuple[str, str]] = []
    if request.name is not None and (
        not isinstance(request.name, str) or len(request.name) > MAX_NAME_INPUT_CHARS
    ):
        errors.append(("name", f"must be a string of at most {MAX_NAME_INPUT_CHARS} characters"))
    if request.checks is not None:
        checks = request.checks
        if (
            isinstance(checks, str)
            or not isinstance(checks, Sequence)
            or not 0 < len(checks) <= _MAX_CHECKS
            or not all(isinstance(c, str) and c in _STEP_VALUES for c in checks)
        ):
            errors.append(("checks", f"expected some of {','.join(sorted(_STEP_VALUES))}"))
    if request.diggs_version is not None and (
        not isinstance(request.diggs_version, str) or request.diggs_version not in SCHEMA_PATHS
    ):
        errors.append(("diggs_version", f"expected one of {', '.join(sorted(SCHEMA_PATHS))}"))
    return tuple(errors)


def _failure(code: Code, exc: BaseException | str) -> Outcome:
    """An internal or resource error: generic text out, real text to the log only."""
    correlation_id = secrets.token_hex(8)
    _log.error("%s [correlation_id=%s]: %s", code.value, correlation_id, exc)
    return Outcome(code=code, detail=_GENERIC[code], correlation_id=correlation_id)


def _read_error(exc: OSError, shown: str) -> Outcome:
    if exc.errno == errno.EFBIG:
        return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
    if exc.errno == errno.EINVAL and "compressed" in (exc.strerror or ""):
        return Outcome(code=Code.COMPRESSED_INPUT, detail=f"{shown} is compressed")
    return Outcome(code=Code.INPUT_UNREADABLE, detail=f"{shown} could not be read")


def validate(request: Request, *, should_stop: Callable[[], bool] | None = None) -> Outcome:
    """Validate ``request`` in the calling thread with the pinned, safe settings.

    ``time_budget`` is cooperative (checked between steps); running out still gives
    a document, with ``structure.timeout``. ``should_stop`` is checked at the same
    boundaries; when it returns True the answer is ``cancelled``.

    Args:
        request: The document and its parameters.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        The document, or an error :class:`Code`. Never raises for bad input.

    Examples:
        >>> from pydiggs._service import Request, validate
        >>> outcome = validate(Request(content=b"<Diggs", name="x/a.xml", checks=["structure"]))
        >>> outcome.ok, b'"path": "a.xml"' in outcome.document
        (True, True)
        >>> validate(Request(content=b"<Diggs/>", checks=["bogus"])).code
        <Code.INVALID_PARAMETER: 'invalid-parameter'>
    """
    errors = _parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = run._input_name(request.name)
    try:
        report = run._validate_bytes(
            request.content,
            name=request.name,
            diggs_version=request.diggs_version,
            checks=request.checks,
            allow_network=False,
            include_snippets=False,
            max_findings=request.max_findings,
            max_bytes=request.max_bytes,
            time_budget=request.time_budget,
            should_stop=should_stop,
        )
        report = _scrubbed(report)
        body = report.to_sarif() if request.format == "sarif" else report.to_document()
        text = _json.dumps(body, indent=None)
    except run._Cancelled:
        return Outcome(code=Code.CANCELLED, detail=_GENERIC[Code.CANCELLED])
    except InstanceReadError as exc:
        return _read_error(exc, shown)
    except ResourceError as exc:
        return _failure(Code.RESOURCE_ERROR, exc)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _failure(Code.INTERNAL, f"{type(exc).__name__}: {exc}")
    # A lone surrogate becomes a \\udcxx JSON escape rather than invalid UTF-8.
    return Outcome(document=text.encode("utf-8", "backslashreplace"))


def read_fd(fd: int, *, max_bytes: int, name: str | None) -> bytes | Outcome:
    """Read an opened, already verified descriptor for :class:`Request` ``content``.

    For the sidecar's path mode: the adapter opens the file and checks *that*
    descriptor; this reads it with the instance limits and never decodes a URI. The
    descriptor is not closed.

    Args:
        fd: A descriptor of a regular file.
        max_bytes: The adapter's path-mode limit.
        name: The basename to report in an error text.

    Returns:
        The bytes, or an ``input-too-large``, ``compressed-input`` or
        ``input-unreadable`` :class:`Outcome`.

    Examples:
        >>> import os, tempfile
        >>> with tempfile.TemporaryFile() as fh:
        ...     _ = fh.write(b"<Diggs/>")
        ...     _ = fh.seek(0)
        ...     read_fd(fh.fileno(), max_bytes=4, name="a.xml").code
        <Code.INPUT_TOO_LARGE: 'input-too-large'>
    """
    shown = run._input_name(name if isinstance(name, str) else None)
    try:
        return _xml.read_fd(fd, max_bytes)
    except OSError as exc:
        return _read_error(exc, shown)


# --- process isolation ------------------------------------------------------------------


def warm_worker(diggs_versions: Sequence[str] | None = None) -> None:
    """Compile the bundled validators for the calling thread (a pool initialiser).

    Args:
        diggs_versions: Warm only these DIGGS versions' schemas; None warms every
            bundled version. The dictionaries and Schematron rules are always warmed.

    Raises:
        ValueError: A version is not bundled.
        ResourceError: pydiggs's bundled data is missing or broken.
    """
    from pydiggs.findings.run import warm_up  # noqa: PLC0415  # the worker's own import

    if diggs_versions is None:
        warm_up()
        return
    for version in diggs_versions:
        warm_up(version)


def make_pool(workers: int, *, timeout: float, memory_mb: int | None = 2048) -> WorkerPool:
    """A :class:`~pydiggs._pool.WorkerPool` for :func:`validate_in_pool`.

    Each worker warms every bundled DIGGS version first, then (on Linux) runs under
    an address-space cap of ``memory_mb``, and on Linux and macOS under a CPU-time
    backstop of ``timeout`` plus 5 seconds per job.

    Args:
        workers: Worker processes.
        timeout: The hard timeout the caller will use, in seconds.
        memory_mb: Per-worker memory cap (Linux only); None for none.

    Returns:
        A pool whose workers start on first use (call ``start()`` to warm them now).
    """
    from pydiggs._pool import WorkerPool  # noqa: PLC0415  # keeps multiprocessing lazy

    if not _valid_seconds(timeout):
        raise ValueError(f"timeout must be a positive number of seconds, at most {MAX_SECONDS}")
    return WorkerPool(
        workers,
        initializer=warm_worker,
        memory_mb=memory_mb,
        cpu_seconds=float(timeout) + _CPU_SLACK,
    )


def _pooled(request: Request) -> bytes:
    """The worker's side: one header line of JSON, a newline, then the document bytes."""
    outcome = validate(request)
    header = {
        "code": outcome.code.value if outcome.code is not None else None,
        "detail": outcome.detail,
        "errors": [list(e) for e in outcome.errors],
        "correlation_id": outcome.correlation_id,
    }
    head = json.dumps(header, ensure_ascii=True).encode("ascii")
    return head + b"\n" + (outcome.document or b"")


def _decode(frame: bytes) -> Outcome:
    """The parent's side of :func:`_pooled`; the document bytes are never parsed here."""
    head, sep, document = frame.partition(b"\n")
    header = json.loads(head) if sep else None
    if not isinstance(header, dict):
        raise ValueError("malformed reply")
    code, detail, errors, correlation_id = (
        header.get(k) for k in ("code", "detail", "errors", "correlation_id")
    )
    if not isinstance(detail, str) or not isinstance(errors, list):
        raise ValueError("malformed reply")
    pairs = tuple((str(e[0]), str(e[1])) for e in errors if isinstance(e, list) and len(e) == 2)  # noqa: PLR2004  # (parameter, problem)
    if code is None:
        return Outcome(document=document)
    return Outcome(
        code=Code(code),
        detail=detail,
        errors=pairs,
        correlation_id=correlation_id if isinstance(correlation_id, str) else None,
    )


def validate_in_pool(pool: WorkerPool, request: Request, *, timeout: float) -> Outcome:
    """Validate ``request`` in a worker process, killing it after ``timeout`` seconds.

    Blocking (offload it from an event loop). The worker runs :func:`validate`; what
    comes back is JSON bytes, never a pickle. A timeout kills and replaces the worker
    and answers ``timeout``; a crash answers ``internal`` (``resource-error`` when the
    worker could not load the bundled data).

    Args:
        pool: From :func:`make_pool`.
        request: The document and its parameters.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`validate`, plus ``timeout``.
    """
    from pydiggs._pool import JobFailedError, JobTimeoutError, PoolError  # noqa: PLC0415

    try:
        frame = pool.run(_pooled, request, timeout=timeout)
    except JobTimeoutError:
        return Outcome(code=Code.TIMEOUT, detail=f"Validation did not finish within {timeout:g} s.")
    except JobFailedError as exc:
        code = Code.RESOURCE_ERROR if exc.type_name == "ResourceError" else Code.INTERNAL
        return _failure(code, f"worker {exc.phase} failed: {exc}")
    except PoolError as exc:
        return _failure(Code.INTERNAL, f"worker failed: {type(exc).__name__}: {exc}")
    try:
        return _decode(frame)
    except (ValueError, TypeError) as exc:
        return _failure(Code.INTERNAL, f"malformed worker reply: {exc}")


# --- rules and versions -----------------------------------------------------------------


def versions() -> dict[str, Any]:
    """:func:`pydiggs.version_info` as a dict, for ``versions`` endpoints.

    Examples:
        >>> versions()["findings_schema_version"]
        '1.0'
    """
    return version_info().to_dict()


def rules(step: str | None = None) -> list[dict[str, Any]] | Outcome:
    """The rule catalogue as dicts, optionally for one step.

    Args:
        step: A step value, or None for all.

    Returns:
        The entries, or an ``invalid-parameter`` :class:`Outcome` for an unknown step.

    Examples:
        >>> {r["step"] for r in rules("schema")}
        {'schema'}
        >>> rules("nope").code
        <Code.INVALID_PARAMETER: 'invalid-parameter'>
    """
    if step is not None and (not isinstance(step, str) or step not in _ALL_STEPS):
        return Outcome(
            code=Code.INVALID_PARAMETER,
            detail="invalid parameters",
            errors=(("step", f"expected one of {', '.join(s.value for s in Step)}"),),
        )
    return [r.to_dict() for r in rule_catalogue() if step is None or r.step.value == step]


def explain(rule_id: str) -> dict[str, Any] | Outcome:
    """:func:`pydiggs.explain_rule` as a dict; bad grammar is ``invalid-parameter``.

    Examples:
        >>> explain("dictionary.5")["known"], explain("crs.unknown")["known"]
        (True, False)
        >>> explain("no dots").code
        <Code.INVALID_PARAMETER: 'invalid-parameter'>
    """
    problem = (("rule_id", f"expected <step>.<name>, at most {MAX_RULE_ID_CHARS} characters"),)
    if not isinstance(rule_id, str) or len(rule_id) > MAX_RULE_ID_CHARS:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=problem)
    try:
        return explain_rule(rule_id).to_dict()
    except ValueError:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=problem)
