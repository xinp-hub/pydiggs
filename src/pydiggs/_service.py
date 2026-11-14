"""The one entry point every adapter uses for untrusted input.

``pydiggs serve --stdio``, ``pydiggs.server`` and ``pydiggs.mcp_server`` validate
documents through :func:`validate` (in the calling thread) or :func:`validate_in_pool`
(in a worker process with a hard timeout), read them into diggs-model documents
through :func:`read` or :func:`read_in_pool`, check AGS4 files through
:func:`ags4_check` or :func:`ags4_check_in_pool`, convert AGS4 to DIGGS through
:func:`convert` or :func:`convert_in_pool`, and answer rule and version questions
through :func:`rules`, :func:`explain` and :func:`versions`. They never call
:func:`pydiggs.validate`, :func:`pydiggs.validate_bytes`, :func:`pydiggs.read`,
:func:`pydiggs.read_bytes`, :func:`pydiggs.ags4.check`, the converter or the legacy
validator themselves (a test enforces this), so every remote caller gets the same
pinned settings:

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

import base64
import contextlib
import dataclasses
import errno
import hashlib
import json
import logging
import math
import os
import secrets
import site
import sys
import tempfile
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path, PurePath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Final, Literal

import pydiggs
from pydiggs import _reader, _xml
from pydiggs._versions import version_info
from pydiggs.detect import SCHEMA_PATHS
from pydiggs.errors import DocumentError, InstanceReadError, ResourceError
from pydiggs.findings import _json, run
from pydiggs.findings.model import Report, Step
from pydiggs.findings.rules import MAX_RULE_ID_CHARS, explain_rule, rule_catalogue

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydiggs._pool import WorkerPool
    from pydiggs.model import Model

__all__ = [
    "CONVERT_DIGGS_VERSIONS",
    "CONVERT_TARGETS",
    "DIGGS_MEDIA_TYPE",
    "DOCUMENT_KINDS",
    "MAX_NAME_INPUT_CHARS",
    "MAX_SELECT_KEYS",
    "Ags4CheckRequest",
    "Code",
    "ConvertRequest",
    "Outcome",
    "ReadRequest",
    "Request",
    "ags4_check",
    "ags4_check_in_pool",
    "capabilities",
    "capabilities_schema",
    "convert",
    "convert_in_pool",
    "explain",
    "make_pool",
    "model_info",
    "model_schema",
    "parameter_errors",
    "read",
    "read_fd",
    "read_in_pool",
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
#: Why a read or a conversion may answer ``invalid-document`` (``Outcome.kind``; an
#: open set). The last two are a conversion's: the source is not AGS4, or is AGS 3.
DOCUMENT_KINDS: Final = (
    "malformed",
    "not-diggs",
    "unsafe-entity",
    "namespace-too-long",
    "not-ags4",
    "unsupported-version",
)
#: Most node keys a read request's ``select.results`` list may name.
MAX_SELECT_KEYS: Final = 1000
#: Longest node key accepted in ``select.results`` (a key is a gml:id or "@" + an xpath).
_MAX_KEY_CHARS: Final = 4096
#: The read cache: models read lately, by content hash (a cache, never state).
_CACHE_MODELS: Final = 4
#: Largest total weight the read cache keeps. A model's weight is the size of its
#: source or of its JSON document, whichever is larger: a file of many small elements
#: makes a document far larger than itself, and a model in memory is several times
#: its document.
_CACHE_WEIGHT: Final = 64 * 2**20

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
    #: Reading only: the bytes are not a DIGGS document (``Outcome.kind`` says why).
    INVALID_DOCUMENT = "invalid-document"
    #: Reading only: the diggs-model document is larger than the adapter allows.
    RESULT_TOO_LARGE = "result-too-large"


#: Fixed texts for ``invalid-document``: the finding's own message can quote the file.
_DOCUMENT_TEXT: Final = {
    "malformed": "The bytes are not well-formed XML.",
    "not-diggs": "The root element is not a DIGGS Diggs element.",
    "unsafe-entity": "The document declares an entity whose value contains markup.",
    "namespace-too-long": "The document declares a namespace URI longer than accepted.",
    "not-ags4": "The bytes are not an AGS4 file.",
    "unsupported-version": "The file is AGS 3 or older; convert it to AGS4 first.",
}

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

    ``document`` is UTF-8 JSON on one line (a diggs-findings 1.1 document, a SARIF
    2.1.0 log when asked for, a diggs-model document from :func:`read`, or a
    conversion result from :func:`convert`). ``detail``
    never contains an absolute path or a value the caller sent, only the reported
    basename.
    ``errors`` lists ``(parameter, problem)`` pairs for ``invalid-parameter``.
    ``correlation_id`` ties a ``resource-error`` or ``internal`` answer to the log line
    with the real text.

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
    #: For ``invalid-document``: one of :data:`DOCUMENT_KINDS`.
    kind: str | None = None

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


def _file_uri(path: PurePath) -> str | None:
    """``path`` as a ``file:`` URI, as :meth:`Path.as_uri` spelled it before Python 3.14.

    Built here because from Python 3.14 ``as_uri`` imports ``urllib.request``, and with
    it ``http.client``, ``socket`` and ``ssl``: networking that the core path never
    imports (the Pyodide smoke checks this). None for a relative path.

    Examples:
        >>> from pathlib import PurePosixPath
        >>> _file_uri(PurePosixPath("/srv/a b/x"))
        'file:///srv/a%20b/x'
        >>> _file_uri(PureWindowsPath("C:/Users/x")), _file_uri(PureWindowsPath("//host/share/x"))
        ('file:///C:/Users/x', 'file://host/share/x')
        >>> _file_uri(PurePosixPath("relative")) is None
        True
    """
    if not path.is_absolute():
        return None
    if isinstance(path, PureWindowsPath):
        drive, posix = path.drive, path.as_posix()
        if len(drive) == 2 and drive[1] == ":":  # noqa: PLR2004  # "C:"
            return f"file:///{drive}" + urllib.parse.quote_from_bytes(posix[2:].encode("utf-8"))
        return "file:" + urllib.parse.quote_from_bytes(posix.encode("utf-8"))
    return "file://" + urllib.parse.quote_from_bytes(os.fsencode(path.as_posix()))


def _spellings(location: Path) -> set[str]:
    """Ways a message may spell a folder: as is, resolved, POSIX, ``file:`` URI, %-encoded."""
    forms: set[str] = set()
    for path in {location, location.resolve()}:
        posix = path.as_posix()
        forms |= {str(path), posix, urllib.parse.quote(posix)}
        uri = _file_uri(path)
        if uri is not None:
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


# --- reading ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadRequest:
    """One read request. Limits come from the adapter's settings, not the caller.

    ``name``, ``diggs_version`` and ``select`` may come from the caller; they are
    checked by :func:`read`, which answers ``invalid-parameter`` rather than raising.
    ``select`` is None or ``{"results": "all" | "none" | [node keys]}``: which result
    tables keep their values (the others keep their columns and ``row_count``).

    Examples:
        >>> ReadRequest(content=b"<Diggs/>", select={"results": "none"}).max_bytes
        26214400
        >>> ReadRequest(content=b"", max_result_bytes=0)
        Traceback (most recent call last):
        ...
        ValueError: max_result_bytes must be a positive int or None, got 0
    """

    content: bytes
    name: str | None = None
    diggs_version: str | None = None
    select: object = None
    max_bytes: int = 25 * 2**20
    time_budget: float | None = None
    max_result_bytes: int | None = 256 * 2**20

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes):
            raise TypeError(f"content must be bytes, not {type(self.content).__name__}")
        if type(self.max_bytes) is not int or self.max_bytes < 1:
            raise ValueError(f"max_bytes must be a positive int, got {self.max_bytes!r}")
        if self.max_result_bytes is not None and (
            type(self.max_result_bytes) is not int or self.max_result_bytes < 1
        ):
            raise ValueError(
                f"max_result_bytes must be a positive int or None, got {self.max_result_bytes!r}"
            )
        if self.time_budget is not None and not _valid_seconds(self.time_budget):
            raise ValueError(
                f"time_budget must be a positive number of seconds, at most {MAX_SECONDS}"
            )


_SELECT_PROBLEM: Final = (
    f'expected {{"results": "all" | "none" | [at most {MAX_SELECT_KEYS} node keys]}}'
)


def _selected(select: object) -> Literal["all", "none"] | frozenset[str] | None:
    """What ``select`` asks for, or None when it is not a valid selection."""
    if select is None:
        return "all"
    if not isinstance(select, Mapping) or set(select) - {"results"}:
        return None
    results = select.get("results", "all")
    if results == "all":
        return "all"
    if results == "none":
        return "none"
    if (
        isinstance(results, str)
        or not isinstance(results, Sequence)
        or len(results) > MAX_SELECT_KEYS
        or not all(isinstance(k, str) and 0 < len(k) <= _MAX_KEY_CHARS for k in results)
    ):
        return None
    return frozenset(results)


def _read_parameter_errors(request: ReadRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    errors: list[tuple[str, str]] = []
    if request.name is not None and (
        not isinstance(request.name, str) or len(request.name) > MAX_NAME_INPUT_CHARS
    ):
        errors.append(("name", f"must be a string of at most {MAX_NAME_INPUT_CHARS} characters"))
    if request.diggs_version is not None and (
        not isinstance(request.diggs_version, str) or request.diggs_version not in SCHEMA_PATHS
    ):
        errors.append(("diggs_version", f"expected one of {', '.join(sorted(SCHEMA_PATHS))}"))
    if _selected(request.select) is None:
        errors.append(("select", _SELECT_PROBLEM))
    return tuple(errors)


class _ModelCache:
    """Models read lately, by (content sha256, DIGGS version, with values): an LRU.

    A host lists a file with ``select.results = "none"`` and then asks for specific
    tables; the second call finds the model here instead of parsing again. A model
    cut short by the time budget is not kept (another call may get further); one
    cut short by a work limit is, since reading the same bytes stops at the same
    place. Bounded by count and by total weight (see
    :data:`_CACHE_WEIGHT`); a model heavier than the whole budget is never kept.
    """

    def __init__(self, models: int, weight: int) -> None:
        self._models = models
        self._source_bytes = weight
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str | None, bool], tuple[Model, int]] = OrderedDict()

    def get(self, key: tuple[str, str | None, bool]) -> Model | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, key: tuple[str, str | None, bool], model: Model, size: int) -> None:
        if size > self._source_bytes:
            return
        with self._lock:
            self._entries[key] = (model, size)
            self._entries.move_to_end(key)
            while (
                len(self._entries) > self._models
                or sum(s for _, s in self._entries.values()) > self._source_bytes
            ):
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_read_cache: Final = _ModelCache(_CACHE_MODELS, _CACHE_WEIGHT)


def _without_values(document: dict[str, Any], keep: frozenset[str]) -> None:
    """Empty the result tables of every node not in ``keep`` (as ``results=False`` reads)."""
    for collection in ("measurements", "others"):
        for node in document.get(collection, ()):
            if node["key"] in keep:
                continue
            for table in node.get("results", ()):
                for column in (*table["columns"], *table["domain"]["index"]):
                    column["values"] = []


_CacheKey = tuple[str, str | None, bool]


def _model(
    request: ReadRequest, *, with_values: bool, should_stop: Callable[[], bool] | None
) -> tuple[Model, _CacheKey | None]:
    """The request's model, and the key to cache it under when it was read just now."""
    key = (hashlib.sha256(request.content).hexdigest(), request.diggs_version, with_values)
    cached = _read_cache.get(key)
    if cached is not None:
        return cached, None
    model = _reader.read_stoppable(
        request.content,
        name=request.name,
        diggs_version=request.diggs_version,
        results=with_values,
        max_bytes=request.max_bytes,
        time_budget=request.time_budget,
        should_stop=should_stop,
    )
    return model, None if "time_budget" in model.limits_hit else key


def read(request: ReadRequest, *, should_stop: Callable[[], bool] | None = None) -> Outcome:
    """Read ``request`` into a diggs-model document, in the calling thread.

    The document is what :meth:`pydiggs.Model.to_json` gives for the same bytes, name
    and version, so every surface answers with the same bytes; ``select`` only empties
    the values of the tables it leaves out. ``time_budget`` is cooperative (checked
    between top-level members and every so often inside one); running out, or
    reaching one of the reader's work limits, still gives a document, with
    ``complete: false``. ``should_stop`` is checked at the same points.

    Args:
        request: The document and its parameters.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        The document, or an error :class:`Code`: the ones :func:`validate` answers,
        ``invalid-document`` (with ``kind``) and ``result-too-large``. Never raises
        for bad input.

    Examples:
        >>> from pydiggs._service import ReadRequest, read
        >>> outcome = read(ReadRequest(content=b"<Diggs xmlns='http://diggsml.org/schemas/3'/>"))
        >>> outcome.ok, b'"schema_version": "1.0"' in outcome.document
        (True, True)
        >>> bad = read(ReadRequest(content=b"<html/>"))
        >>> bad.code, bad.kind
        (<Code.INVALID_DOCUMENT: 'invalid-document'>, 'not-diggs')
    """
    errors = _read_parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = run._input_name(request.name)
    try:
        return _read_outcome(request, shown, should_stop)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _read_failure(exc, shown)


def _read_failure(exc: Exception, shown: str) -> Outcome:
    """The outcome for what a read raised: cancelled, not a document, unreadable, or internal."""
    if isinstance(exc, run._Cancelled):
        return Outcome(code=Code.CANCELLED, detail=_GENERIC[Code.CANCELLED])
    if isinstance(exc, DocumentError):
        detail = _DOCUMENT_TEXT.get(exc.kind, "The bytes are not a DIGGS document.")
        return Outcome(code=Code.INVALID_DOCUMENT, detail=detail, kind=exc.kind)
    if isinstance(exc, InstanceReadError):
        return _read_error(exc, shown)
    if isinstance(exc, ResourceError):
        return _failure(Code.RESOURCE_ERROR, exc)
    return _failure(Code.INTERNAL, f"{type(exc).__name__}: {exc}")


def _read_outcome(
    request: ReadRequest, shown: str, should_stop: Callable[[], bool] | None
) -> Outcome:
    """:func:`read` after its parameter checks; reader errors propagate to it."""
    cancelled = Outcome(code=Code.CANCELLED, detail=_GENERIC[Code.CANCELLED])
    if should_stop is not None and should_stop():
        return cancelled
    if len(request.content) > request.max_bytes:  # before the cache: the limit is the request's
        return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
    selected = _selected(request.select)
    model, fresh = _model(request, with_values=selected != "none", should_stop=should_stop)
    document = model.to_dict()
    document["source"]["name"] = shown  # a cached model may carry another name
    if isinstance(selected, frozenset):
        _without_values(document, selected)
    for note in document["notes"]:
        note["message"] = _scrub(note["message"])
    body = json.dumps(document, ensure_ascii=False, allow_nan=False).encode(
        "utf-8", "backslashreplace"
    )
    if fresh is not None:
        _read_cache.put(fresh, model, max(len(request.content), len(body)))
    if should_stop is not None and should_stop():
        return cancelled
    if request.max_result_bytes is not None and len(body) > request.max_result_bytes:
        return Outcome(
            code=Code.RESULT_TOO_LARGE,
            detail=(
                f"The document for {shown} is larger than allowed; ask for fewer result"
                " tables with select."
            ),
        )
    return Outcome(document=body)


# --- AGS4: checking and conversion ------------------------------------------------------

#: The formats :func:`convert` converts to (``to``). AGS4 to DIGGS only so far; DIGGS to
#: AGS4 will be ``"ags4"``, with DIGGS XML as the content.
CONVERT_TARGETS: Final = ("diggs",)
#: The DIGGS versions the converter writes (the converter's own list; a test holds them equal).
CONVERT_DIGGS_VERSIONS: Final = ("3.0.0",)
#: The name reported for AGS4 content sent without one.
_AGS4_INPUT_NAME: Final = "input.ags"
#: The media type of the DIGGS output, as :func:`convert` reports it.
DIGGS_MEDIA_TYPE: Final = "application/xml"
_DICTIONARY_PROBLEM: Final = (
    "dictionary",
    "is not a usable AGS4 data dictionary (an AGS4 file whose DICT group defines groups)",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertRequest:
    """One conversion request. Limits come from the adapter's settings, not the caller.

    ``name``, ``to`` and ``diggs_version`` may come from the caller; they are checked
    by :func:`convert`, which answers ``invalid-parameter`` rather than raising.
    ``to`` is the target format: ``"diggs"`` (AGS4 content in, DIGGS XML out) is the
    only one so far. ``diggs_version`` None means the converter's default, 3.0.0.

    Examples:
        >>> ConvertRequest(content=b'"GROUP","PROJ"').to
        'diggs'
        >>> ConvertRequest(content=b"", time_budget=0)
        Traceback (most recent call last):
        ...
        ValueError: time_budget must be a positive number of seconds, at most 86400
    """

    content: bytes
    name: str | None = None
    to: str = "diggs"
    diggs_version: str | None = None
    max_bytes: int = 25 * 2**20
    time_budget: float | None = None
    max_result_bytes: int | None = 256 * 2**20

    def __post_init__(self) -> None:
        _check_limits(self.content, self.max_bytes, self.time_budget)
        if self.max_result_bytes is not None and (
            type(self.max_result_bytes) is not int or self.max_result_bytes < 1
        ):
            raise ValueError(
                f"max_result_bytes must be a positive int or None, got {self.max_result_bytes!r}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class Ags4CheckRequest:
    """One AGS4 check request. Limits come from the adapter's settings, not the caller.

    ``dictionary`` is the bytes of an AGS4 data dictionary the caller supplies (pyDIGGS
    bundles none); it is held to ``dictionary_max_bytes``, or to ``max_bytes`` when that
    is None (an adapter whose dictionary can come by another transport than the file,
    such as the sidecar's ``dictionary_path``, gives that transport's limit). ``name`` is
    checked by :func:`ags4_check`, which answers ``invalid-parameter`` rather than raising.

    Examples:
        >>> Ags4CheckRequest(content=b'"GROUP","PROJ"', name="site.ags").format
        'findings'
    """

    content: bytes
    name: str | None = None
    dictionary: bytes | None = None
    format: Literal["findings", "sarif"] = "findings"
    max_bytes: int = 25 * 2**20
    max_findings: int | None = run.DEFAULT_MAX_FINDINGS
    time_budget: float | None = None
    dictionary_max_bytes: int | None = None

    def __post_init__(self) -> None:
        _check_limits(self.content, self.max_bytes, self.time_budget)
        if self.dictionary is not None and not isinstance(self.dictionary, bytes):
            raise TypeError(f"dictionary must be bytes, not {type(self.dictionary).__name__}")
        limit = self.dictionary_max_bytes
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError(f"dictionary_max_bytes must be a positive int or None, got {limit!r}")
        if self.format not in ("findings", "sarif"):
            raise ValueError(f"format must be 'findings' or 'sarif', got {self.format!r}")
        if self.max_findings is not None and (
            type(self.max_findings) is not int or self.max_findings < 1
        ):
            raise ValueError(
                f"max_findings must be a positive int or None, got {self.max_findings!r}"
            )


def _check_limits(content: object, max_bytes: object, time_budget: object) -> None:
    """The checks every AGS4 request makes of its content and the adapter's limits."""
    if not isinstance(content, bytes):
        raise TypeError(f"content must be bytes, not {type(content).__name__}")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError(f"max_bytes must be a positive int, got {max_bytes!r}")
    if time_budget is not None and not _valid_seconds(time_budget):
        raise ValueError(f"time_budget must be a positive number of seconds, at most {MAX_SECONDS}")


def _ags4_name(name: str | None) -> str:
    """The reported name of AGS4 content: its basename, or ``input.ags``."""
    if name is None or run._NAME_SEPARATORS.split(name)[-1] in ("", ".", ".."):
        return _AGS4_INPUT_NAME
    return run._input_name(name)


def _name_errors(name: object) -> list[tuple[str, str]]:
    if name is not None and (not isinstance(name, str) or len(name) > MAX_NAME_INPUT_CHARS):
        return [("name", f"must be a string of at most {MAX_NAME_INPUT_CHARS} characters")]
    return []


def _convert_parameter_errors(request: ConvertRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    errors = _name_errors(request.name)
    if not isinstance(request.to, str) or request.to not in CONVERT_TARGETS:
        errors.append(("to", f"expected one of {', '.join(CONVERT_TARGETS)}"))
    if request.diggs_version is not None and (
        not isinstance(request.diggs_version, str)
        or request.diggs_version not in CONVERT_DIGGS_VERSIONS
    ):
        errors.append(("diggs_version", f"expected one of {', '.join(CONVERT_DIGGS_VERSIONS)}"))
    return tuple(errors)


def _ags4_check_parameter_errors(request: Ags4CheckRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    return tuple(_name_errors(request.name))


def parameter_errors(request: object) -> tuple[tuple[str, str], ...]:
    """The caller-supplied parameter problems of any request type (for adapters' pre-checks).

    Examples:
        >>> parameter_errors(ConvertRequest(content=b"", to="ags4"))
        (('to', 'expected one of diggs'),)
    """
    if isinstance(request, Request):
        return _parameter_errors(request)
    if isinstance(request, ReadRequest):
        return _read_parameter_errors(request)
    if isinstance(request, ConvertRequest):
        return _convert_parameter_errors(request)
    if isinstance(request, Ags4CheckRequest):
        return _ags4_check_parameter_errors(request)
    raise TypeError(f"not a service request: {type(request).__name__}")


_CANCELLED: Final = Outcome(code=Code.CANCELLED, detail=_GENERIC[Code.CANCELLED])


def _unreadable_text(shown: str) -> Outcome:
    return Outcome(code=Code.INPUT_UNREADABLE, detail=f"{shown} could not be read as AGS4 text")


def _read_ags4(content: bytes, shown: str, max_bytes: int, time_budget: float | None) -> Any:
    """``content`` as an :class:`pydiggs.ags4.File`, or the Outcome that says why not.

    A ``DocumentError`` (not AGS4, or AGS 3) propagates: the caller decides whether it
    is an error (a conversion) or a finding (a check).
    """
    from pydiggs import ags4  # noqa: PLC0415  # AGS4 loads on first use

    if len(content) > max_bytes:
        return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
    try:
        return ags4.read_bytes(content, name=shown, max_bytes=max_bytes, time_budget=time_budget)
    except DocumentError:
        raise
    except ValueError:  # not text in any encoding the reader takes
        return _unreadable_text(shown)


def convert(request: ConvertRequest, *, should_stop: Callable[[], bool] | None = None) -> Outcome:
    """Convert AGS4 content to DIGGS, in the calling thread, with the pinned settings.

    The document is one line of JSON: ``to``, ``media_type`` and ``output_name`` (the
    output's suggested name), ``diggs_version``, ``mapping_version``, ``complete``,
    ``lossless``, ``report`` (the diggs-findings 1.1 document about the source:
    the AGS4 reader's notes and every ``convert`` finding), ``validation`` (the
    diggs-findings document of :func:`pydiggs.validate_bytes` on the output),
    ``output_sha256`` and ``output_base64`` (the DIGGS XML, standard base64). Every
    surface sends these same bytes, and the XML is what
    ``pydiggs.preview.convert.ags4_to_diggs`` writes for the same bytes and name.

    ``time_budget`` covers reading, converting and validating; running out still gives
    a document, with ``complete: false`` (see ``ags4_to_diggs``). ``should_stop`` is
    checked before the conversion starts and after it ends: the converter itself
    cannot be interrupted.

    Args:
        request: The AGS4 content and its parameters.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        The document, or an error :class:`Code`: the ones :func:`validate` answers,
        ``invalid-document`` (``kind`` ``not-ags4`` or ``unsupported-version``) and
        ``result-too-large`` (the document is larger than ``max_result_bytes``).
        Never raises for bad input.

    Examples:
        >>> from pathlib import Path
        >>> data = Path("tests/fixtures/ags4/convert_tier_a.ags").read_bytes()
        >>> outcome = convert(ConvertRequest(content=data, name="up/site.ags"))
        >>> result = json.loads(outcome.document)
        >>> result["output_name"], result["complete"], result["report"]["files"][0]["path"]
        ('site.xml', True, 'site.ags')
        >>> convert(ConvertRequest(content=b"<Diggs/>")).kind
        'not-ags4'
    """
    errors = _convert_parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = _ags4_name(request.name)
    if should_stop is not None and should_stop():
        return _CANCELLED
    try:
        return _convert_outcome(request, shown, should_stop)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _read_failure(exc, shown)


def _too_large_result(shown: str) -> Outcome:
    return Outcome(
        code=Code.RESULT_TOO_LARGE,
        detail=f"The conversion of {shown} is larger than allowed.",
    )


def _convert_outcome(
    request: ConvertRequest, shown: str, should_stop: Callable[[], bool] | None
) -> Outcome:
    """:func:`convert` after its parameter checks; converter errors propagate to it."""
    from pydiggs.preview import convert as preview  # noqa: PLC0415  # loads on first use

    started = time.monotonic()
    file = _read_ags4(request.content, shown, request.max_bytes, request.time_budget)
    if isinstance(file, Outcome):
        return file
    budget = request.time_budget
    if budget is not None:
        budget = max(budget - (time.monotonic() - started), 1e-9)
    conversion = preview.ags4_to_diggs(
        file,
        diggs_version=request.diggs_version or CONVERT_DIGGS_VERSIONS[0],
        validate=True,
        name=shown,
        time_budget=budget,
    )
    if should_stop is not None and should_stop():
        return _CANCELLED
    xml: bytes = conversion.xml
    limit = request.max_result_bytes
    if limit is not None and 4 * math.ceil(len(xml) / 3) > limit:  # before encoding it
        return _too_large_result(shown)
    validation = conversion.validation
    stem = shown.rsplit(".", 1)[0] or "output"
    result = {
        "to": "diggs",
        "media_type": DIGGS_MEDIA_TYPE,
        "output_name": f"{stem}.xml",
        "diggs_version": conversion.model.source.diggs_version,
        "mapping_version": conversion.mapping_version,
        "complete": conversion.complete,
        "lossless": conversion.lossless,
        "report": _scrubbed(conversion.report).to_document(),
        "validation": None if validation is None else _scrubbed(validation).to_document(),
        "output_sha256": hashlib.sha256(xml).hexdigest(),
        "output_base64": base64.b64encode(xml).decode("ascii"),
    }
    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode(
        "utf-8", "backslashreplace"
    )
    if limit is not None and len(body) > limit:
        return _too_large_result(shown)
    return Outcome(document=body)


def ags4_check(
    request: Ags4CheckRequest, *, should_stop: Callable[[], bool] | None = None
) -> Outcome:
    """Check AGS4 content against the AGS4 rules, in the calling thread.

    The report is what :func:`pydiggs.ags4.check` gives for the same bytes saved as a
    file (line numbers included), reported under ``name``. Content that is not AGS4 at
    all, or is AGS 3, is a report with that one finding, as ``pydiggs ags4 check``
    gives. A ``dictionary`` that cannot be used is ``invalid-parameter``.
    ``time_budget`` is one deadline for reading the dictionary and checking: the check
    gets what the dictionary read leaves, and a dictionary read the budget stops is
    ``invalid-parameter`` (``dictionary``: could not be read within the time budget).
    ``should_stop`` is checked before and after the check.

    Args:
        request: The AGS4 content, an optional dictionary and the parameters.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        A diggs-findings 1.1 document (or a SARIF log), or an error :class:`Code`.
        Never raises for bad input.

    Examples:
        >>> from pathlib import Path
        >>> data = Path("tests/fixtures/ags4/c2_rule_10a.ags").read_bytes()
        >>> report = json.loads(ags4_check(Ags4CheckRequest(content=data)).document)
        >>> entry = report["files"][0]
        >>> errors = [f for f in entry["findings"] if f["severity"] == "error"]
        >>> entry["path"], [(f["rule_id"], f["location"]["line"]) for f in errors]
        ('input.ags', [('ags4.rule-10a', 77)])
        >>> bad = json.loads(ags4_check(Ags4CheckRequest(content=b"<Diggs/>")).document)
        >>> [f["rule_id"] for f in bad["files"][0]["findings"]]
        ['ags4.rule-2']
    """
    errors = _ags4_check_parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = _ags4_name(request.name)
    if should_stop is not None and should_stop():
        return _CANCELLED
    try:
        outcome = _ags4_check_outcome(request, shown)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _read_failure(exc, shown)
    if should_stop is not None and should_stop():
        return _CANCELLED
    return outcome


def _not_ags4(content: bytes, shown: str, exc: DocumentError) -> Report:
    """A report for content with no AGS4 table at all: its one finding is the reason."""
    return Report(
        path=shown,
        sha256=hashlib.sha256(content).hexdigest(),
        diggs_version=None,
        steps={Step.AGS4: "failed"},
        findings=() if exc.finding is None else (exc.finding,),
        tool_version=pydiggs.__version__,
        format="ags4",
    )


def _bad_dictionary() -> Outcome:
    return Outcome(
        code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=(_DICTIONARY_PROBLEM,)
    )


#: The time budget ran out while the caller's dictionary was being read: a parameter
#: problem (every surface already declares it), not ``timeout``, which the adapters
#: keep for their own hard limit.
_DICTIONARY_TIMEOUT: Final = Outcome(
    code=Code.INVALID_PARAMETER,
    detail="invalid parameters",
    errors=(("dictionary", "could not be read within the time budget"),),
)


def _remaining(deadline: float | None) -> float | None:
    """Seconds left until ``deadline`` (a ``time.monotonic()`` value); None for no limit."""
    if deadline is None:
        return None
    return max(deadline - time.monotonic(), 1e-9)


def _ags4_check_outcome(request: Ags4CheckRequest, shown: str) -> Outcome:
    """:func:`ags4_check` after its parameter checks.

    The checker finds each row's line in the file's text, which it reads from a path:
    so the content is written to a private temporary folder (mode 0700, removed at
    once) and checked there. Nothing else touches the filesystem.
    """
    from pydiggs import ags4  # noqa: PLC0415  # AGS4 loads on first use

    if len(request.content) > request.max_bytes:
        return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
    # One deadline for the whole request: the dictionary read takes what it needs of the
    # budget and the check gets what is left.
    budget = request.time_budget
    deadline = None if budget is None else time.monotonic() + budget
    dictionary = None
    if request.dictionary is not None:
        limit = request.dictionary_max_bytes or request.max_bytes
        try:
            dictionary = ags4.read_bytes(
                request.dictionary,
                name="dictionary.ags",
                max_bytes=limit,
                time_budget=_remaining(deadline),
            )
        except (InstanceReadError, ValueError):  # DocumentError is a ValueError
            return _bad_dictionary()
        if "time_budget" in dictionary.limits_hit:
            return _DICTIONARY_TIMEOUT
    with tempfile.TemporaryDirectory(prefix="pydiggs-ags4-") as folder:
        path = Path(folder, _AGS4_INPUT_NAME)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(request.content)
        try:
            report = ags4.check(
                path,
                dictionary=dictionary,
                max_bytes=request.max_bytes,
                max_findings=request.max_findings,
                time_budget=_remaining(deadline),
            )
        except DocumentError as exc:
            report = _not_ags4(request.content, shown, exc)
        except ValueError:  # not text in any encoding the reader takes
            return _unreadable_text(shown)
        except ResourceError as exc:
            if dictionary is None:  # pragma: no cover - only a dictionary raises it
                raise
            _log.info("AGS4 dictionary refused: %s", exc)
            return _bad_dictionary()
    report = _scrubbed(dataclasses.replace(report, path=shown, absolute_path=None))
    body = report.to_sarif() if request.format == "sarif" else report.to_document()
    return Outcome(document=_json.dumps(body, indent=None).encode("utf-8", "backslashreplace"))


# --- process isolation ------------------------------------------------------------------


def warm_worker(diggs_versions: Sequence[str] | None = None) -> None:
    """Compile the bundled validators for the calling thread (a pool initialiser).

    Args:
        diggs_versions: Warm only these DIGGS versions' schemas; None warms every
            released bundled version (not the experimental ``3.1-dev``). The
            dictionaries and Schematron rules are always warmed.

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


_AnyRequest = Request | ReadRequest | ConvertRequest | Ags4CheckRequest


def _run(request: _AnyRequest) -> Outcome:
    """Answer any request in the calling thread."""
    if isinstance(request, ReadRequest):
        return read(request)
    if isinstance(request, ConvertRequest):
        return convert(request)
    if isinstance(request, Ags4CheckRequest):
        return ags4_check(request)
    return validate(request)


def _pooled(request: _AnyRequest) -> bytes:
    """The worker's side: one header line of JSON, a newline, then the document bytes."""
    outcome = _run(request)
    header = {
        "code": outcome.code.value if outcome.code is not None else None,
        "detail": outcome.detail,
        "errors": [list(e) for e in outcome.errors],
        "correlation_id": outcome.correlation_id,
        "kind": outcome.kind,
    }
    head = json.dumps(header, ensure_ascii=True).encode("ascii")
    return head + b"\n" + (outcome.document or b"")


def _decode(frame: bytes) -> Outcome:
    """The parent's side of :func:`_pooled`; the document bytes are never parsed here."""
    head, sep, document = frame.partition(b"\n")
    header = json.loads(head) if sep else None
    if not isinstance(header, dict):
        raise ValueError("malformed reply")
    code, detail, errors, correlation_id, kind = (
        header.get(k) for k in ("code", "detail", "errors", "correlation_id", "kind")
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
        kind=kind if isinstance(kind, str) else None,
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
    return _in_pool(pool, request, timeout=timeout, what="Validation")


def read_in_pool(pool: WorkerPool, request: ReadRequest, *, timeout: float) -> Outcome:
    """Read ``request`` in a worker process, killing it after ``timeout`` seconds.

    As :func:`validate_in_pool`, for :func:`read`. Each worker keeps its own read
    cache.

    Args:
        pool: From :func:`make_pool`.
        request: The document and its parameters.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`read`, plus ``timeout``.
    """
    return _in_pool(pool, request, timeout=timeout, what="Reading")


def convert_in_pool(pool: WorkerPool, request: ConvertRequest, *, timeout: float) -> Outcome:
    """Convert ``request`` in a worker process, killing it after ``timeout`` seconds.

    As :func:`validate_in_pool`, for :func:`convert`.

    Args:
        pool: From :func:`make_pool`.
        request: The AGS4 content and its parameters.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`convert`, plus ``timeout``.
    """
    return _in_pool(pool, request, timeout=timeout, what="Conversion")


def ags4_check_in_pool(pool: WorkerPool, request: Ags4CheckRequest, *, timeout: float) -> Outcome:
    """Check ``request`` in a worker process, killing it after ``timeout`` seconds.

    As :func:`validate_in_pool`, for :func:`ags4_check`.

    Args:
        pool: From :func:`make_pool`.
        request: The AGS4 content, an optional dictionary and the parameters.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`ags4_check`, plus ``timeout``.
    """
    return _in_pool(pool, request, timeout=timeout, what="Checking")


def _in_pool(pool: WorkerPool, request: _AnyRequest, *, timeout: float, what: str) -> Outcome:
    from pydiggs._pool import JobFailedError, JobTimeoutError, PoolError  # noqa: PLC0415

    try:
        frame = pool.run(_pooled, request, timeout=timeout)
    except JobTimeoutError:
        return Outcome(code=Code.TIMEOUT, detail=f"{what} did not finish within {timeout:g} s.")
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


def model_info() -> dict[str, str]:
    """The diggs-model version and schema URL that :func:`read` answers with.

    Examples:
        >>> model_info()["model_schema_version"]
        '1.0'
    """
    from pydiggs.model import MODEL_SCHEMA_URL, MODEL_SCHEMA_VERSION  # noqa: PLC0415

    return {"model_schema_version": MODEL_SCHEMA_VERSION, "model_schema_url": MODEL_SCHEMA_URL}


def model_schema() -> dict[str, Any]:
    """The packaged diggs-model JSON Schema, a fresh dict (for adapters' output schemas).

    Examples:
        >>> model_schema()["title"]
        'diggs-model 1.0'
    """
    from pydiggs.model import model_schema as packaged  # noqa: PLC0415

    return packaged()


def versions() -> dict[str, Any]:
    """:func:`pydiggs.version_info` as a dict, for ``versions`` endpoints.

    Examples:
        >>> versions()["findings_schema_version"]
        '1.1'
    """
    return version_info().to_dict()


def capabilities() -> dict[str, Any]:
    """:func:`pydiggs.capabilities` as a diggs-capabilities document, for adapters.

    Examples:
        >>> capabilities()["schema_version"]
        '1.0'
    """
    from pydiggs._capabilities import capabilities as describe  # noqa: PLC0415

    return describe().to_dict()


def capabilities_schema() -> dict[str, Any]:
    """The packaged diggs-capabilities JSON Schema, a fresh dict (for adapters' schemas).

    Examples:
        >>> capabilities_schema()["title"]
        'diggs-capabilities 1.0'
    """
    from pydiggs._capabilities import capabilities_schema as packaged  # noqa: PLC0415

    return packaged()


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
