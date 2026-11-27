"""The one entry point every adapter uses for untrusted input.

``pydiggs serve --stdio``, ``pydiggs.server`` and ``pydiggs.mcp_server`` validate
documents through :func:`validate` (in the calling thread) or :func:`validate_in_pool`
(in a worker process with a hard timeout), read them into diggs-model documents
through :func:`read` or :func:`read_in_pool`, check AGS4 files through
:func:`ags4_check` or :func:`ags4_check_in_pool`, convert AGS4 to DIGGS and DIGGS to
AGS4 through :func:`convert` or :func:`convert_in_pool`, compare two DIGGS files
through :func:`diff` or :func:`diff_in_pool`, write, check and round-trip packages of
the DIGGS tables profile through :func:`tables` or :func:`tables_in_pool` and turn DIGGS
into one through :func:`tables_from_diggs` or :func:`tables_from_diggs_in_pool`, and
answer rule and version questions through :func:`rules`, :func:`explain` and
:func:`versions`. They never call
:func:`pydiggs.validate`, :func:`pydiggs.validate_bytes`, :func:`pydiggs.read`,
:func:`pydiggs.read_bytes`, :func:`pydiggs.ags4.check`, :mod:`pydiggs.convert`,
:mod:`pydiggs.diff` or the legacy validator themselves (a test enforces this), so every
remote caller gets the same pinned settings:

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

The conformance suite (:mod:`pydiggs.conformance`, a preview) is here too, for an adapter
that runs on the caller's own machine: :func:`conformance_classes`,
:func:`conformance_new`, :func:`conformance_reference`, :func:`conformance_grade` and
:func:`conformance_verify` take files as bytes, keep them within fixed limits in a
private temporary folder, and answer one JSON document, with a file inside it as base64
beside its sha256. No HTTP route, sidecar method or MCP tool offers them.
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
import re
import secrets
import site
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from io import BytesIO
from pathlib import Path, PurePath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Final, Literal

import pydiggs
from pydiggs import _reader, _xml
from pydiggs._versions import version_info
from pydiggs.detect import SCHEMA_PATHS
from pydiggs.errors import DocumentError, InstanceReadError, ResourceError
from pydiggs.findings import _json, run
from pydiggs.findings.model import Report, Step, StepStatus
from pydiggs.findings.rules import MAX_RULE_ID_CHARS, explain_rule, rule_catalogue

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydiggs._pool import WorkerPool
    from pydiggs.convert import Conversion
    from pydiggs.model import Model

__all__ = [
    "AGS4_MEDIA_TYPE",
    "CONFORMANCE_KINDS",
    "CONFORMANCE_MAX_EVIDENCE_BYTES",
    "CONFORMANCE_MAX_MANIFEST_BYTES",
    "CONFORMANCE_MAX_ZIP_MEMBERS",
    "CONFORMANCE_TIME_BUDGET",
    "CONVERT_AGS_VERSIONS",
    "CONVERT_DIGGS_VERSIONS",
    "CONVERT_TARGETS",
    "DIFF_DEFAULTS",
    "DIFF_MATCH",
    "DIFF_SIDES",
    "DIGGS_MEDIA_TYPE",
    "DOCUMENT_KINDS",
    "MAX_NAME_INPUT_CHARS",
    "MAX_SELECT_KEYS",
    "PACKAGE_MEDIA_TYPE",
    "TABLES_MAX_FILES",
    "TABLES_OPERATIONS",
    "TABLES_UNPACK_FACTOR",
    "Ags4CheckRequest",
    "Code",
    "ConformanceNewRequest",
    "ConformanceRequest",
    "ConvertRequest",
    "DiffRequest",
    "Outcome",
    "ReadRequest",
    "Request",
    "TablesFromDiggsRequest",
    "TablesRequest",
    "ags4_check",
    "ags4_check_in_pool",
    "capabilities",
    "capabilities_schema",
    "conformance_classes",
    "conformance_grade",
    "conformance_new",
    "conformance_reference",
    "conformance_verify",
    "convert",
    "convert_in_pool",
    "diff",
    "diff_in_pool",
    "diff_info",
    "explain",
    "make_pool",
    "model_info",
    "model_schema",
    "package_file",
    "parameter_errors",
    "read",
    "read_fd",
    "read_in_pool",
    "rules",
    "tables",
    "tables_from_diggs",
    "tables_from_diggs_in_pool",
    "tables_in_pool",
    "tables_info",
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
#: open set). ``not-ags4`` and ``unsupported-version`` are an AGS4 to DIGGS
#: conversion's: the source is not AGS4, or is AGS 3; the last three are a tables
#: package's: not a ZIP, an entry refused, not tables of the profile; the others are
#: those of DIGGS input (a read, or DIGGS to AGS4).
DOCUMENT_KINDS: Final = (
    "malformed",
    "not-diggs",
    "unsafe-entity",
    "namespace-too-long",
    "not-ags4",
    "unsupported-version",
    "not-zip",
    "unsafe-package",
    "not-tables",
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
    "not-zip": "The package is not a ZIP file.",
    "unsafe-package": (
        "The package holds an entry that is not a plain CSV or JSON file at its top level"
        " (or one that is linked, encrypted or listed twice)."
    ),
    "not-tables": (
        "The package is not tables of the DIGGS tables profile: a table the profile does"
        " not have, a table without a column it requires, or a datapackage.json that cannot"
        " be used. Run pydiggs map check on it for the details."
    ),
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

    ``document`` is UTF-8 JSON on one line (a diggs-findings 1.3 document, a SARIF
    2.1.0 log when asked for, a diggs-model document from :func:`read`, or a
    conversion result from :func:`convert`). ``detail``
    never contains an absolute path or a value the caller sent, only the reported
    basename. One exception: a conformance request's ``invalid-document`` carries the
    suite's own message, which can quote a value of the files given (a suite version, a
    digest), at a bounded length and never with the private folder's path.
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
    #: For ``invalid-document``: one of :data:`DOCUMENT_KINDS`, or of
    #: :data:`CONFORMANCE_KINDS` from a conformance request.
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

#: The formats :func:`convert` converts to (``to``): ``"diggs"`` (the default; the
#: content is AGS4) and ``"ags4"`` (the content is DIGGS XML).
CONVERT_TARGETS: Final = ("diggs", "ags4")
#: The DIGGS versions the converter writes (the converter's own list; a test holds them equal).
CONVERT_DIGGS_VERSIONS: Final = ("3.0.0",)
#: The AGS4 versions DIGGS to AGS4 writes, the default first (a test holds them equal to
#: the converter's own list).
CONVERT_AGS_VERSIONS: Final = ("4.2", "4.1.1")
#: The name reported for AGS4 content sent without one.
_AGS4_INPUT_NAME: Final = "input.ags"
#: The name reported for DIGGS content (``to="ags4"``) sent without one.
_DIGGS_INPUT_NAME: Final = "input.xml"
#: The media type of the DIGGS output, as :func:`convert` reports it.
DIGGS_MEDIA_TYPE: Final = "application/xml"
#: The media type of the AGS4 output (and of the residual file): AGS4 has no registered
#: media type, and the converter writes UTF-8 text (ASCII for a file that follows Rule 1).
AGS4_MEDIA_TYPE: Final = "text/plain; charset=utf-8"
_DICTIONARY_PROBLEM: Final = (
    "dictionary",
    "is not a usable AGS4 data dictionary (an AGS4 file whose DICT group defines groups)",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertRequest:
    """One conversion request. Limits come from the adapter's settings, not the caller.

    ``name``, ``to``, ``diggs_version`` and ``ags_version`` may come from the caller;
    they are checked by :func:`convert`, which answers ``invalid-parameter`` rather
    than raising. ``to`` is the target format: ``"diggs"`` (AGS4 content in, DIGGS XML
    out; the default) or ``"ags4"`` (DIGGS XML in, an AGS4 file out).
    ``diggs_version`` is for ``"diggs"`` only (None: the converter's default, 3.0.0)
    and ``ags_version`` for ``"ags4"`` only (None: the default, 4.2; or 4.1.1).

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
    ags_version: str | None = None
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


def _ags4_name(name: str | None, default: str = _AGS4_INPUT_NAME) -> str:
    """The reported name of AGS4 (or DIGGS) content: its basename, or ``default``."""
    if name is None or run._NAME_SEPARATORS.split(name)[-1] in ("", ".", ".."):
        return default
    return run._input_name(name)


def _name_errors(name: object) -> list[tuple[str, str]]:
    if name is not None and (not isinstance(name, str) or len(name) > MAX_NAME_INPUT_CHARS):
        return [("name", f"must be a string of at most {MAX_NAME_INPUT_CHARS} characters")]
    return []


def _convert_parameter_errors(request: ConvertRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    errors = _name_errors(request.name)
    to = request.to if isinstance(request.to, str) and request.to in CONVERT_TARGETS else None
    if to is None:
        errors.append(("to", f"expected one of {', '.join(CONVERT_TARGETS)}"))
    if request.diggs_version is not None:
        if to == "ags4":
            errors.append(("diggs_version", "applies only to to=diggs"))
        elif (
            not isinstance(request.diggs_version, str)
            or request.diggs_version not in CONVERT_DIGGS_VERSIONS
        ):
            errors.append(("diggs_version", f"expected one of {', '.join(CONVERT_DIGGS_VERSIONS)}"))
    if request.ags_version is not None:
        if to == "diggs":
            errors.append(("ags_version", "applies only to to=ags4"))
        elif (
            not isinstance(request.ags_version, str)
            or request.ags_version not in CONVERT_AGS_VERSIONS
        ):
            errors.append(("ags_version", f"expected one of {', '.join(CONVERT_AGS_VERSIONS)}"))
    return tuple(errors)


def _ags4_check_parameter_errors(request: Ags4CheckRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    return tuple(_name_errors(request.name))


def parameter_errors(request: object) -> tuple[tuple[str, str], ...]:
    """The caller-supplied parameter problems of any request type (for adapters' pre-checks).

    Examples:
        >>> parameter_errors(ConvertRequest(content=b"", to="csv"))
        (('to', 'expected one of diggs, ags4'),)
        >>> parameter_errors(ConvertRequest(content=b"", to="ags4", ags_version="4.0"))
        (('ags_version', 'expected one of 4.2, 4.1.1'),)
    """
    checks: tuple[tuple[type, Callable[[Any], tuple[tuple[str, str], ...]]], ...] = (
        (Request, _parameter_errors),
        (ReadRequest, _read_parameter_errors),
        (ConvertRequest, _convert_parameter_errors),
        (Ags4CheckRequest, _ags4_check_parameter_errors),
        (DiffRequest, _diff_parameter_errors),
        (TablesRequest, _tables_parameter_errors),
        (TablesFromDiggsRequest, lambda r: tuple(_name_errors(r.name))),
    )
    for kind, check in checks:
        if isinstance(request, kind):
            return check(request)
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
    """Convert AGS4 content to DIGGS, or DIGGS to AGS4, in the calling thread.

    ``request.to`` chooses the direction: ``"diggs"`` (the default) converts AGS4
    content with :func:`pydiggs.convert.ags4_to_diggs`, ``"ags4"`` converts DIGGS XML
    with :func:`pydiggs.convert.diggs_to_ags4`. The document is one line of JSON: ``to``,
    ``media_type`` (``application/xml`` for DIGGS, ``text/plain; charset=utf-8`` for
    AGS4) and ``output_name`` (the output's suggested name), ``diggs_version`` (of the
    DIGGS output, or of the DIGGS input), ``ags_version`` (of the AGS4 output; None for
    DIGGS), ``mapping_version``, ``complete``, ``lossless``, ``report`` (the
    diggs-findings 1.3 document about the source: the reader's notes and every
    ``convert`` finding), ``validation`` (the diggs-findings document of
    :func:`pydiggs.validate_bytes` on DIGGS output, or of :func:`pydiggs.ags4.check` on
    AGS4 output), ``output_sha256`` and ``output_base64`` (the output, standard
    base64), and ``residual_base64``: for AGS4 to DIGGS, the AGS4 file of what was not
    converted (``Conversion.residual``), or None when everything was; always None for
    DIGGS to AGS4. Every surface sends these same bytes, and the output is what
    :mod:`pydiggs.convert` writes for the same bytes and name.

    ``time_budget`` covers reading, converting and validating (AGS4 to DIGGS) or
    reading (DIGGS to AGS4); running out still gives a document, with
    ``complete: false``. ``should_stop`` is checked before the conversion starts and
    after it ends: the converter itself cannot be interrupted.

    Args:
        request: The content and its parameters.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        The document, or an error :class:`Code`: the ones :func:`validate` answers,
        ``invalid-document`` (``kind`` ``not-ags4`` or ``unsupported-version`` for AGS4
        input, a :func:`read` kind for DIGGS input) and ``result-too-large`` (the
        document is larger than ``max_result_bytes``). Never raises for bad input.

    Examples:
        >>> from pathlib import Path
        >>> data = Path("tests/fixtures/ags4/convert_tier_a.ags").read_bytes()
        >>> outcome = convert(ConvertRequest(content=data, name="up/site.ags"))
        >>> result = json.loads(outcome.document)
        >>> result["output_name"], result["complete"], result["report"]["files"][0]["path"]
        ('site.xml', True, 'site.ags')
        >>> convert(ConvertRequest(content=b"<Diggs/>")).kind
        'not-ags4'
        >>> xml = base64.b64decode(result["output_base64"])
        >>> back = json.loads(convert(ConvertRequest(content=xml, to="ags4")).document)
        >>> back["output_name"], back["ags_version"], back["media_type"]
        ('input.ags', '4.2', 'text/plain; charset=utf-8')
    """
    errors = _convert_parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    to_ags4 = request.to == "ags4"
    shown = _ags4_name(request.name, _DIGGS_INPUT_NAME if to_ags4 else _AGS4_INPUT_NAME)
    if should_stop is not None and should_stop():
        return _CANCELLED
    try:
        if to_ags4:
            return _to_ags4_outcome(request, shown, should_stop)
        return _convert_outcome(request, shown, should_stop)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _read_failure(exc, shown)


def _too_large_result(shown: str) -> Outcome:
    return Outcome(
        code=Code.RESULT_TOO_LARGE,
        detail=f"The conversion of {shown} is larger than allowed.",
    )


def _encoded_size(*outputs: bytes | None) -> int:
    """The base64 length of ``outputs`` together (before encoding them)."""
    return sum(4 * math.ceil(len(o) / 3) for o in outputs if o is not None)


def _conversion_body(
    conversion: Conversion,
    *,
    to: str,
    shown: str,
    output: bytes,
    residual: bytes | None,
    limit: int | None,
) -> Outcome:
    """The result document of :func:`convert`, or ``result-too-large``."""
    validation = conversion.validation
    stem = shown.rsplit(".", 1)[0] or "output"
    to_ags4 = to == "ags4"
    result = {
        "to": to,
        "media_type": AGS4_MEDIA_TYPE if to_ags4 else DIGGS_MEDIA_TYPE,
        "output_name": f"{stem}.ags" if to_ags4 else f"{stem}.xml",
        "diggs_version": conversion.model.source.diggs_version,
        "ags_version": conversion.ags4.version if conversion.ags4 is not None else None,
        "mapping_version": conversion.mapping_version,
        "complete": conversion.complete,
        "lossless": conversion.lossless,
        "report": _scrubbed(conversion.report).to_document(),
        "validation": None if validation is None else _scrubbed(validation).to_document(),
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "output_base64": base64.b64encode(output).decode("ascii"),
        "residual_base64": None if residual is None else base64.b64encode(residual).decode("ascii"),
    }
    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode(
        "utf-8", "backslashreplace"
    )
    if limit is not None and len(body) > limit:
        return _too_large_result(shown)
    return Outcome(document=body)


def _budget_left(budget: float | None, started: float) -> float | None:
    """What is left of ``budget`` seconds since ``started`` (never zero: readers refuse it)."""
    if budget is None:
        return None
    return max(budget - (time.monotonic() - started), 1e-9)


def _convert_outcome(
    request: ConvertRequest, shown: str, should_stop: Callable[[], bool] | None
) -> Outcome:
    """:func:`convert` to DIGGS after its parameter checks; converter errors propagate."""
    from pydiggs import ags4  # noqa: PLC0415  # loads on first use
    from pydiggs import convert as converter  # noqa: PLC0415

    started = time.monotonic()
    file = _read_ags4(request.content, shown, request.max_bytes, request.time_budget)
    if isinstance(file, Outcome):
        return file
    conversion = converter.ags4_to_diggs(
        file,
        diggs_version=request.diggs_version or CONVERT_DIGGS_VERSIONS[0],
        validate=True,
        name=shown,
        time_budget=_budget_left(request.time_budget, started),
    )
    if should_stop is not None and should_stop():
        return _CANCELLED
    xml = conversion.xml
    if xml is None:  # pragma: no cover - ags4_to_diggs always sets xml
        raise RuntimeError("the conversion to DIGGS gave no XML")
    residual = conversion.residual
    residual_bytes = ags4.dumps(residual) if residual is not None and residual.groups else None
    limit = request.max_result_bytes
    if limit is not None and _encoded_size(xml, residual_bytes) > limit:  # before encoding
        return _too_large_result(shown)
    return _conversion_body(
        conversion, to="diggs", shown=shown, output=xml, residual=residual_bytes, limit=limit
    )


def _to_ags4_outcome(
    request: ConvertRequest, shown: str, should_stop: Callable[[], bool] | None
) -> Outcome:
    """:func:`convert` to AGS4 after its parameter checks; reader errors propagate."""
    from pydiggs import ags4  # noqa: PLC0415  # loads on first use
    from pydiggs import convert as converter  # noqa: PLC0415

    if len(request.content) > request.max_bytes:
        return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
    conversion = converter.diggs_to_ags4(
        request.content,
        ags_version=request.ags_version or CONVERT_AGS_VERSIONS[0],
        check=True,
        name=shown,
        max_bytes=request.max_bytes,
        time_budget=request.time_budget,
    )
    if should_stop is not None and should_stop():
        return _CANCELLED
    if conversion.ags4 is None:  # pragma: no cover - diggs_to_ags4 always sets ags4
        raise RuntimeError("the conversion to AGS4 gave no AGS4 file")
    output = ags4.dumps(conversion.ags4)
    limit = request.max_result_bytes
    if limit is not None and _encoded_size(output) > limit:  # before encoding it
        return _too_large_result(shown)
    return _conversion_body(
        conversion, to="ags4", shown=shown, output=output, residual=None, limit=limit
    )


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
        A diggs-findings 1.3 document (or a SARIF log), or an error :class:`Code`.
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


# --- comparing two files ----------------------------------------------------------------

#: The ways :func:`diff` pairs records (``match``): :func:`pydiggs.diff.compare`'s own.
DIFF_MATCH: Final = ("auto", "gml-id", "natural", "gml_id")
#: :func:`pydiggs.diff.compare`'s defaults, which every surface uses when not given.
DIFF_DEFAULTS: Final[Mapping[str, object]] = {
    "match": "auto",
    "rel_tol": 1e-9,
    "abs_tol": 0.0,
    "max_changes": 10_000,
    "show_reformatted": False,
    "show_renamed": False,
}
#: The two inputs of a comparison, as parameter names and in the order they are read.
DIFF_SIDES: Final = ("old", "new")


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffRequest:
    """One comparison of two DIGGS files. Limits come from the adapter, not the caller.

    ``old`` and ``new`` are the two files' bytes, each held to ``max_bytes`` (the
    adapter's per-file limit). The names and the comparison options may come from the
    caller; they are checked by :func:`diff`, which answers ``invalid-parameter``
    rather than raising. ``time_budget`` covers reading both files and comparing them.

    Examples:
        >>> DiffRequest(old=b"<a/>", new=b"<b/>").match
        'auto'
        >>> DiffRequest(old=b"", new="text")
        Traceback (most recent call last):
        ...
        TypeError: new must be bytes, not str
    """

    old: bytes
    new: bytes
    old_name: str | None = None
    new_name: str | None = None
    match: object = "auto"
    rel_tol: object = 1e-9
    abs_tol: object = 0.0
    max_changes: object = 10_000
    show_reformatted: object = False
    show_renamed: object = False
    max_bytes: int = 25 * 2**20
    time_budget: float | None = None
    max_result_bytes: int | None = 256 * 2**20

    def __post_init__(self) -> None:
        for side in DIFF_SIDES:
            content = getattr(self, side)
            if not isinstance(content, bytes):
                raise TypeError(f"{side} must be bytes, not {type(content).__name__}")
        _check_limits(self.old, self.max_bytes, self.time_budget)
        if self.max_result_bytes is not None and (
            type(self.max_result_bytes) is not int or self.max_result_bytes < 1
        ):
            raise ValueError(
                f"max_result_bytes must be a positive int or None, got {self.max_result_bytes!r}"
            )


def _finite(value: object, *, below_one: bool = False) -> bool:
    """A finite number, 0 or more (and less than 1 when ``below_one``); never a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        number = float(value)  # a huge int (a 401-digit JSON number) overflows here
    except OverflowError:
        return False
    return math.isfinite(number) and number >= 0 and (not below_one or number < 1)


def _diff_parameter_errors(request: DiffRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    errors = [
        (f"{side}_name", problem)
        for side in DIFF_SIDES
        for _, problem in _name_errors(getattr(request, f"{side}_name"))
    ]
    if not isinstance(request.match, str) or request.match not in DIFF_MATCH:
        errors.append(("match", f"expected one of {', '.join(DIFF_MATCH[:3])}"))
    if not _finite(request.rel_tol, below_one=True):
        errors.append(("rel_tol", "must be a finite number, 0 or more and less than 1"))
    if not _finite(request.abs_tol):
        errors.append(("abs_tol", "must be a finite number, 0 or more"))
    changes = request.max_changes
    if isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
        errors.append(("max_changes", "must be an integer, 0 or more"))
    errors.extend(
        (flag, "must be true or false")
        for flag in ("show_reformatted", "show_renamed")
        if not isinstance(getattr(request, flag), bool)
    )
    return tuple(errors)


def diff(request: DiffRequest, *, should_stop: Callable[[], bool] | None = None) -> Outcome:
    """Compare two DIGGS files with :func:`pydiggs.diff.compare`, in the calling thread.

    The document is the diggs-diff 1.0 document :meth:`pydiggs.diff.Diff.to_json` gives
    for the same bytes and options, with each file reported under its basename
    (``old_name``, ``new_name``; ``input.xml`` when not given), so every surface answers
    with the same bytes. ``time_budget`` covers reading both files and comparing them;
    running out still gives a document, with ``complete: false`` and a ``diff.timeout``
    note. ``should_stop`` is checked before the comparison starts and after it ends: the
    comparison itself cannot be interrupted.

    A problem with one of the files names it: ``errors`` holds ``("old", ...)`` or
    ``("new", ...)`` with a fixed text, for ``input-too-large``, ``compressed-input``,
    ``input-unreadable`` and ``invalid-document`` (``kind`` as for :func:`read`).

    Args:
        request: The two files and the comparison options.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        The document, or an error :class:`Code`: the ones :func:`read` answers. Never
        raises for bad input.

    Examples:
        >>> from pathlib import Path
        >>> data = Path("tests/fixtures/official/3.0/Aeromag.xml").read_bytes()
        >>> outcome = diff(DiffRequest(old=data, new=data, old_name="up/a.xml"))
        >>> result = json.loads(outcome.document)
        >>> result["identical"], result["old"]["name"], result["new"]["name"]
        (True, 'a.xml', 'input.xml')
        >>> bad = diff(DiffRequest(old=data, new=b"<html/>"))
        >>> bad.code, bad.kind
        (<Code.INVALID_DOCUMENT: 'invalid-document'>, 'not-diggs')
        >>> bad.errors
        (('new', 'is not a DIGGS document'),)
    """
    errors = _diff_parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = {side: run._input_name(getattr(request, f"{side}_name")) for side in DIFF_SIDES}
    if should_stop is not None and should_stop():
        return _CANCELLED
    reading = [DIFF_SIDES[0]]  # the file being read, so that a failure can name it
    try:
        outcome = _diff_outcome(request, shown, reading)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _diff_failure(exc, reading[-1], shown[reading[-1]])
    if should_stop is not None and should_stop():
        return _CANCELLED
    return outcome


def _diff_outcome(request: DiffRequest, shown: Mapping[str, str], reading: list[str]) -> Outcome:
    """:func:`diff` after its parameter checks: :func:`pydiggs.diff.compare`'s own steps.

    The same options, one deadline for the whole call, the old file read first. Each
    file's name is appended to ``reading`` before it is read; reader errors propagate.
    """
    from pydiggs.diff import _engine, _load, _options  # noqa: PLC0415  # loads on first use

    for side in DIFF_SIDES:  # before reading either: the limit is per file
        if len(getattr(request, side)) > request.max_bytes:
            return Outcome(
                code=Code.INPUT_TOO_LARGE,
                detail=f"The {side} file, {shown[side]}, is larger than allowed",
                errors=((side, "is larger than allowed"),),
            )
    options = _options(
        match=request.match,  # type: ignore[arg-type]  # checked by diff()
        rel_tol=request.rel_tol,  # type: ignore[arg-type]
        abs_tol=request.abs_tol,  # type: ignore[arg-type]
        max_changes=request.max_changes,  # type: ignore[arg-type]
        time_budget=request.time_budget,
        max_bytes=request.max_bytes,
        show_reformatted=bool(request.show_reformatted),
        show_renamed=bool(request.show_renamed),
    )
    budget = request.time_budget
    deadline = None if budget is None else _engine._now() + float(budget)
    old_model, old_seen = _load(request.old, "old", request.max_bytes, deadline)
    reading.append("new")
    new_model, new_seen = _load(request.new, "new", request.max_bytes, deadline)
    seen = None if old_seen is None or new_seen is None else (old_seen, new_seen)
    compared = _engine.compare_models(old_model, new_model, options, deadline=deadline, seen=seen)
    document = compared.diff.to_dict()
    for side in DIFF_SIDES:
        document[side]["name"] = shown[side]
    for note in document["notes"]:
        note["message"] = _scrub(note["message"])
    body = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True).encode(
        "utf-8", "backslashreplace"
    )
    if request.max_result_bytes is not None and len(body) > request.max_result_bytes:
        return Outcome(
            code=Code.RESULT_TOO_LARGE,
            detail=(
                f"The comparison of {shown['old']} and {shown['new']} is larger than"
                " allowed; list fewer changes with max_changes."
            ),
        )
    return Outcome(document=body)


def _diff_failure(exc: Exception, side: str, shown: str) -> Outcome:
    """The outcome of a comparison that raised, naming the file when it was one of them."""
    outcome = _read_failure(exc, shown)
    if outcome.code is Code.INVALID_DOCUMENT:
        return dataclasses.replace(
            outcome,
            detail=f"The {side} file: {outcome.detail}",
            errors=((side, "is not a DIGGS document"),),
        )
    if outcome.code in (Code.INPUT_TOO_LARGE, Code.COMPRESSED_INPUT, Code.INPUT_UNREADABLE):
        problem = outcome.detail.removeprefix(f"{shown} ")
        return dataclasses.replace(
            outcome, detail=f"The {side} file: {outcome.detail}", errors=((side, problem),)
        )
    return outcome


# --- tables: the DIGGS tables profile (preview) -----------------------------------------

#: What :func:`tables` does with a package (``operation``): write it as a DIGGS file,
#: check it against the DIGGS dictionaries, or write it and read it back.
TABLES_OPERATIONS: Final = ("to-diggs", "check", "round-trip")
#: Most files a package may hold (``pydiggs.tables._package.MAX_FILES``; a test holds
#: them equal). Kept here so that importing the service does not load the profile.
TABLES_MAX_FILES: Final = 256
#: A package ZIP may unpack to at most this many times its size limit (all files together).
TABLES_UNPACK_FACTOR: Final = 4
#: The media type of a package (a ZIP of its CSV files and ``datapackage.json``).
PACKAGE_MEDIA_TYPE: Final = "application/zip"
#: The name reported for a package sent without one, and for its output.
_TABLES_INPUT_NAME: Final = "tables.zip"
#: Fixed texts for a package that cannot be used, by ``Outcome.kind``.
_PACKAGE_TEXT: Final = _DOCUMENT_TEXT


@dataclass(frozen=True, slots=True, kw_only=True)
class TablesRequest:
    """Tables of the DIGGS tables profile to write, check or round-trip; one package.

    The package is ``content``, a ZIP of its CSV files and ``datapackage.json`` (held to
    ``max_bytes``, unpacked in memory to at most ``max_unpacked_bytes``, by default
    :data:`TABLES_UNPACK_FACTOR` times ``max_bytes``), or ``files``, the files already
    read by the adapter (the sidecar's folder path), file name to bytes. Exactly one is
    given. ``operation`` (one of :data:`TABLES_OPERATIONS`) and ``name`` may come from
    the caller; :func:`tables` checks them. There is no time budget: building a model
    from tables cannot be interrupted, and is bounded by the size limits (the HTTP
    server's worker timeout still applies).

    Examples:
        >>> TablesRequest(content=b"PK").operation
        'to-diggs'
        >>> TablesRequest(content=b"PK", files={})
        Traceback (most recent call last):
        ...
        ValueError: give exactly one of content and files
    """

    content: bytes | None = None
    files: Mapping[str, bytes] | None = None
    operation: object = "to-diggs"
    name: str | None = None
    max_bytes: int = 25 * 2**20
    max_unpacked_bytes: int | None = None
    max_result_bytes: int | None = 256 * 2**20

    def __post_init__(self) -> None:
        if (self.content is None) == (self.files is None):
            raise ValueError("give exactly one of content and files")
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError(f"content must be bytes, not {type(self.content).__name__}")
        if self.files is not None and not all(
            isinstance(k, str) and isinstance(v, bytes) for k, v in self.files.items()
        ):
            raise TypeError("files must map str to bytes")
        _check_limits(b"", self.max_bytes, None)
        for limit in ("max_unpacked_bytes", "max_result_bytes"):
            value = getattr(self, limit)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{limit} must be a positive int or None, got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class TablesFromDiggsRequest:
    """One DIGGS file to turn into tables of the profile, answered as a package ZIP.

    ``name`` may come from the caller; :func:`tables_from_diggs` checks it.
    ``time_budget`` covers reading the file, as for :func:`read`.

    Examples:
        >>> TablesFromDiggsRequest(content=b"<Diggs/>").max_bytes
        26214400
    """

    content: bytes
    name: str | None = None
    max_bytes: int = 25 * 2**20
    time_budget: float | None = None
    max_result_bytes: int | None = 256 * 2**20

    def __post_init__(self) -> None:
        _check_limits(self.content, self.max_bytes, self.time_budget)
        limit = self.max_result_bytes
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError(f"max_result_bytes must be a positive int or None, got {limit!r}")


def _tables_parameter_errors(request: TablesRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    errors = _name_errors(request.name)
    operation = request.operation
    if not isinstance(operation, str) or operation not in TABLES_OPERATIONS:
        errors.append(("operation", f"expected one of {', '.join(TABLES_OPERATIONS)}"))
    return tuple(errors)


def _tables_report(
    findings: Sequence[Any],
    *,
    shown: str,
    sha256: str,
    diggs_version: str | None = None,
    complete: bool = True,
) -> dict[str, Any]:
    """A diggs-findings document of step ``tables`` about ``shown``."""
    from pydiggs.findings.model import Severity  # noqa: PLC0415

    failed = any(f.severity is Severity.ERROR for f in findings)
    status: StepStatus = "incomplete" if not complete else ("failed" if failed else "passed")
    report = Report(
        path=shown,
        sha256=sha256,
        diggs_version=diggs_version,
        steps={Step.TABLES: status},
        findings=tuple(findings),
        tool_version=pydiggs.__version__,
    )
    return _scrubbed(report).to_document()


def _json_value(value: object) -> object:
    """A cell for a JSON result: as it is, a non-finite number or another type as text."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    return str(value)


def _body(result: Mapping[str, Any]) -> bytes:
    return json.dumps(result, ensure_ascii=False, allow_nan=False).encode(
        "utf-8", "backslashreplace"
    )


def _tables_too_large(shown: str) -> Outcome:
    return Outcome(
        code=Code.RESULT_TOO_LARGE, detail=f"The result for {shown} is larger than allowed."
    )


def tables(request: TablesRequest, *, should_stop: Callable[[], bool] | None = None) -> Outcome:
    r"""Write, check or round-trip a package of profile tables, in the calling thread.

    The package (a ZIP, or files the adapter read) is read as
    :func:`pydiggs.tables.read_package` reads a folder: the resources of its
    ``datapackage.json``, or every ``<table>.csv``. Then, by ``request.operation``:

    - ``"to-diggs"``: :func:`pydiggs.tables.to_model` and :func:`pydiggs.write_bytes`.
      The document is one line of JSON: ``profile_version``, ``diggs_version``
      (``3.0.0``), ``media_type`` (``application/xml``), ``output_name``, ``lossless``,
      ``counts`` (features, samples, layers, measurements), ``report`` (a diggs-findings
      1.3 document of step ``tables``), ``output_sha256`` and ``output_base64``: the
      same bytes as ``pydiggs.write_bytes(tables.to_model(...).model)``.
    - ``"check"``: the diggs-findings 1.3 document of :func:`pydiggs.tables.check`.
    - ``"round-trip"``: :func:`pydiggs.tables.round_trip`: ``profile_version``, ``ok``,
      ``report`` (what writing left out or assumed), ``validation`` (of the DIGGS
      written), ``differences`` (``table``, ``row``, ``column``, ``sent``, ``got``),
      ``output_sha256`` and ``output_base64``.

    Each report is about the package, named ``name`` (default ``tables.zip``), with the
    sha256 of its file list (each file's name and sha256, in name order), so the same
    files give the same report whether they came as a ZIP or as a folder.
    ``should_stop`` is checked before and after: building cannot be interrupted.

    Args:
        request: The package and its parameters.
        should_stop: Cancellation hook for adapters that can cancel (the sidecar).

    Returns:
        The document, or an error :class:`Code`: ``invalid-parameter``,
        ``input-too-large`` (the ZIP, its files or what they unpack to),
        ``input-unreadable`` (a file is not UTF-8 CSV), ``invalid-document`` (``kind``
        ``not-zip``, ``unsafe-package`` or ``not-tables``), ``result-too-large``,
        ``resource-error``, ``internal`` or ``cancelled``. Never raises for bad input.

    Examples:
        >>> from pydiggs.tables import _package
        >>> package = _package.pack([("features.csv", b"key,depth_uom\nBH-1,m\n")])
        >>> result = json.loads(tables(TablesRequest(content=package)).document)
        >>> (
        ...     result["output_name"],
        ...     result["counts"]["features"],
        ...     result["report"]["files"][0]["path"],
        ... )
        ('tables.xml', 1, 'tables.zip')
        >>> bad = tables(TablesRequest(content=b"not a zip"))
        >>> bad.code, bad.kind
        (<Code.INVALID_DOCUMENT: 'invalid-document'>, 'not-zip')
    """
    errors = _tables_parameter_errors(request)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = _ags4_name(request.name, _TABLES_INPUT_NAME)
    if should_stop is not None and should_stop():
        return _CANCELLED
    try:
        outcome = _tables_outcome(request, shown)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _tables_failure(exc, shown)
    if should_stop is not None and should_stop():
        return _CANCELLED
    return outcome


def _package_files(request: TablesRequest, shown: str) -> Mapping[str, bytes] | Outcome:
    """The package's files: unpacked from the ZIP within the limits, or as the adapter read them."""
    from pydiggs.tables import _package  # noqa: PLC0415  # loads on first use

    if request.files is not None:
        files = request.files
    else:
        content = request.content
        assert content is not None  # noqa: S101  # exactly one is set
        if len(content) > request.max_bytes:
            return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
        limit = request.max_unpacked_bytes or TABLES_UNPACK_FACTOR * request.max_bytes
        files = _package.unpack(content, max_unpacked_bytes=limit)
    if len(files) > TABLES_MAX_FILES:
        return Outcome(
            code=Code.INPUT_TOO_LARGE,
            detail=f"{shown} has more than {TABLES_MAX_FILES} files.",
        )
    if not all(_package.valid_name(n) for n in files):
        kind = "unsafe-package"
        return Outcome(code=Code.INVALID_DOCUMENT, detail=_PACKAGE_TEXT[kind], kind=kind)
    return files


def _tables_outcome(request: TablesRequest, shown: str) -> Outcome:
    """:func:`tables` after its parameter checks; package and tables errors propagate."""
    from pydiggs import tables as profile  # noqa: PLC0415  # loads on first use
    from pydiggs.tables import _io, _package  # noqa: PLC0415

    files = _package_files(request, shown)
    if isinstance(files, Outcome):
        return files
    sha256 = _package.digest(files)
    found = _io.tables_from_files(files)
    stem = shown.rsplit(".", 1)[0] or "tables"
    limit = request.max_result_bytes
    if request.operation == "check":
        report = _tables_report(profile.check(found), shown=shown, sha256=sha256)
        return Outcome(
            document=_json.dumps(report, indent=None).encode("utf-8", "backslashreplace")
        )
    if request.operation == "round-trip":
        checked = profile.round_trip(found)
        output = checked.diggs
        if limit is not None and _encoded_size(output) > limit:
            return _tables_too_large(shown)
        result: dict[str, Any] = {
            "profile_version": profile.PROFILE_VERSION,
            "ok": checked.ok,
            "report": _tables_report(checked.findings, shown=shown, sha256=sha256),
            "validation": _scrubbed(
                dataclasses.replace(checked.validation, path=f"{stem}.xml")
            ).to_document(),
            "differences": [
                {
                    "table": d.table,
                    "row": d.row,
                    "column": d.column,
                    "sent": _json_value(d.sent),
                    "got": _json_value(d.got),
                }
                for d in checked.differences
            ],
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_base64": base64.b64encode(output).decode("ascii"),
        }
    else:
        built = profile.to_model(found)
        output = pydiggs.write_bytes(built.model)
        if limit is not None and _encoded_size(output) > limit:
            return _tables_too_large(shown)
        model = built.model
        result = {
            "profile_version": profile.PROFILE_VERSION,
            "diggs_version": CONVERT_DIGGS_VERSIONS[0],
            "media_type": DIGGS_MEDIA_TYPE,
            "output_name": f"{stem}.xml",
            "lossless": built.lossless,
            "counts": {
                "features": len(model.features),
                "samples": len(model.samples),
                "layers": sum(len(observation.layers) for observation in model.observations),
                "measurements": len(model.measurements),
            },
            "report": _tables_report(built.findings, shown=shown, sha256=sha256),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_base64": base64.b64encode(output).decode("ascii"),
        }
    body = _body(result)
    if limit is not None and len(body) > limit:
        return _tables_too_large(shown)
    return Outcome(document=body)


def _tables_failure(exc: Exception, shown: str) -> Outcome:
    """The outcome for what a tables request raised: the package, its tables, or internal."""
    import csv  # noqa: PLC0415  # only to recognise its error

    from pydiggs.errors import TablesError  # noqa: PLC0415
    from pydiggs.tables._package import PackageError  # noqa: PLC0415

    if isinstance(exc, PackageError):
        if exc.kind == "too-large":
            return Outcome(code=Code.INPUT_TOO_LARGE, detail=str(exc))
        return Outcome(code=Code.INVALID_DOCUMENT, detail=_PACKAGE_TEXT[exc.kind], kind=exc.kind)
    if isinstance(exc, TablesError) and isinstance(exc.__cause__, UnicodeDecodeError | csv.Error):
        exc = exc.__cause__  # a file that is not UTF-8 CSV: unreadable, not "not tables"
    elif isinstance(exc, TablesError):
        return Outcome(
            code=Code.INVALID_DOCUMENT, detail=_PACKAGE_TEXT["not-tables"], kind="not-tables"
        )
    if isinstance(exc, UnicodeDecodeError | csv.Error):
        return Outcome(
            code=Code.INPUT_UNREADABLE,
            detail=f"A file of {shown} could not be read as UTF-8 CSV or JSON text.",
        )
    return _read_failure(exc, shown)


def tables_from_diggs(
    request: TablesFromDiggsRequest, *, should_stop: Callable[[], bool] | None = None
) -> Outcome:
    """Turn one DIGGS file into tables of the profile, answered as a package ZIP.

    The file is read as :func:`read` reads it, then :func:`pydiggs.tables.from_model`
    makes the tables. The document is one line of JSON: ``profile_version``,
    ``diggs_version`` (of the source), ``complete``, ``lossless``, ``plan`` (what
    ``plan.json`` holds: the tables in load order, with their identity columns),
    ``report`` (a diggs-findings 1.3 document about the source: the reader's notes and
    every ``tables`` finding), ``media_type`` (``application/zip``), ``output_name``,
    ``output_sha256`` and ``output_base64``: a ZIP of the files
    :func:`pydiggs.tables.write_package` writes (a CSV per table, ``datapackage.json``,
    ``plan.json``), byte for byte, with fixed timestamps.

    Args:
        request: The DIGGS file and its parameters.
        should_stop: Cancellation hook, checked while reading and after.

    Returns:
        The document, or an error :class:`Code`: the ones :func:`read` answers. Never
        raises for bad input.

    Examples:
        >>> from pathlib import Path
        >>> data = Path("tests/fixtures/official/3.0/Aeromag.xml").read_bytes()
        >>> result = json.loads(tables_from_diggs(TablesFromDiggsRequest(content=data)).document)
        >>> result["output_name"], result["media_type"], result["plan"]["steps"][0]["table"]
        ('input.zip', 'application/zip', 'document')
    """
    errors = tuple(_name_errors(request.name))
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    shown = _ags4_name(request.name, _DIGGS_INPUT_NAME)
    if should_stop is not None and should_stop():
        return _CANCELLED
    try:
        outcome = _from_diggs_outcome(request, shown, should_stop)
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _read_failure(exc, shown)
    if should_stop is not None and should_stop():
        return _CANCELLED
    return outcome


def _from_diggs_outcome(
    request: TablesFromDiggsRequest, shown: str, should_stop: Callable[[], bool] | None
) -> Outcome:
    """:func:`tables_from_diggs` after its parameter checks; reader errors propagate."""
    from pydiggs import tables as profile  # noqa: PLC0415  # loads on first use
    from pydiggs.tables import _io, _package  # noqa: PLC0415

    if len(request.content) > request.max_bytes:
        return Outcome(code=Code.INPUT_TOO_LARGE, detail=f"{shown} is larger than allowed")
    model = _reader.read_stoppable(
        request.content,
        name=shown,
        diggs_version=None,
        results=True,
        max_bytes=request.max_bytes,
        time_budget=request.time_budget,
        should_stop=should_stop,
    )
    found = profile.from_model(model)
    output = _package.pack(_io.package_files(found))
    limit = request.max_result_bytes
    if limit is not None and _encoded_size(output) > limit:
        return _tables_too_large(shown)
    findings = (*model.notes, *found.findings)
    report = _tables_report(
        findings,
        shown=shown,
        sha256=hashlib.sha256(request.content).hexdigest(),
        diggs_version=model.source.diggs_version,
        complete=model.complete,
    )
    result = {
        "profile_version": profile.PROFILE_VERSION,
        "diggs_version": model.source.diggs_version,
        "complete": model.complete,
        "lossless": all(f.severity.rank == 0 for f in found.findings),
        "plan": found.plan.to_dict(),
        "report": report,
        "media_type": PACKAGE_MEDIA_TYPE,
        "output_name": f"{shown.rsplit('.', 1)[0] or 'input'}.zip",
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "output_base64": base64.b64encode(output).decode("ascii"),
    }
    body = _body(result)
    if limit is not None and len(body) > limit:
        return _tables_too_large(shown)
    return Outcome(document=body)


# --- conformance ------------------------------------------------------------------------

#: Seconds for reading, and again for validating, a file the suite grades (a pairwise
#: challenge spends them twice: its ``challenge.xml`` is graded when it is loaded).
CONFORMANCE_TIME_BUDGET: Final = 120.0
#: Largest ``challenge.json`` accepted, alone or in a challenge ZIP.
CONFORMANCE_MAX_MANIFEST_BYTES: Final = 64 * 1024
#: Largest evidence document accepted.
CONFORMANCE_MAX_EVIDENCE_BYTES: Final = 8 * 2**20
#: Most members a challenge ZIP may hold.
CONFORMANCE_MAX_ZIP_MEMBERS: Final = 200
#: Why a conformance request may answer ``invalid-document`` (``Outcome.kind``): the
#: ``kind`` of the suite's own :class:`~pydiggs.errors.DocumentError`.
CONFORMANCE_KINDS: Final = ("not-a-challenge", "suite-version", "source-export", "not-evidence")
#: Longest ``detail`` of a conformance refusal: the suite's message can quote a file.
_CONFORMANCE_MAX_DETAIL: Final = 1000
_CONFORMANCE_MANIFEST: Final = "challenge.json"
_CONFORMANCE_IMPORT_FILE: Final = "challenge.xml"
_CONFORMANCE_ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)
_CONFORMANCE_NAME: Final = re.compile(r"[^A-Za-z0-9._ -]")
_CONFORMANCE_NAME_CHARS: Final = 120


@dataclass(frozen=True, slots=True, kw_only=True)
class ConformanceNewRequest:
    """One request for a conformance challenge (:func:`conformance_new`).

    ``class_id``, ``product``, ``version`` and ``seed`` may come from the caller and are
    checked by :func:`conformance_new`. ``evidence`` and ``export`` make a pairwise
    import challenge, from another product's graded export: the bytes of its
    ``evidence.json`` and of the DIGGS file that evidence graded, both or neither. The
    limits are the adapter's own and are checked here.

    Examples:
        >>> ConformanceNewRequest(class_id="spt-export", product="P", version="1").time_budget
        120.0
        >>> ConformanceNewRequest(class_id="spt-import", product="P", version="1", export=b"<x/>")
        Traceback (most recent call last):
        ...
        ValueError: evidence and export are given together, or neither
    """

    class_id: str
    product: str
    version: str
    seed: str | None = None
    evidence: bytes | None = None
    export: bytes | None = None
    max_bytes: int = 25 * 2**20
    time_budget: float = CONFORMANCE_TIME_BUDGET

    def __post_init__(self) -> None:
        for name in ("evidence", "export"):
            _optional_bytes(getattr(self, name), name)
        if (self.evidence is None) != (self.export is None):
            raise ValueError("evidence and export are given together, or neither")
        _conformance_limits(self.max_bytes, self.time_budget)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConformanceRequest:
    """One request about a challenge: its reference file, a grading or a verification.

    ``challenge`` is the bytes of ``challenge.json``, or of the challenge's folder as a
    ZIP (what :func:`conformance_new` gives). A pairwise challenge is taken as the ZIP
    only: its ``challenge.xml``, the other product's export, is part of it.
    ``submission`` is the DIGGS file a product exported, or the filled ``answers.csv``
    of an import class, and ``name`` its file name, which the evidence records
    (:func:`conformance_grade`, :func:`conformance_verify`). ``evidence`` is the
    ``evidence.json`` to compare with (:func:`conformance_verify`). The limits are the
    adapter's own and are checked here.

    Examples:
        >>> ConformanceRequest(challenge=b"{}").max_bytes
        26214400
        >>> ConformanceRequest(challenge=b"{}", time_budget=0)
        Traceback (most recent call last):
        ...
        ValueError: time_budget must be a positive number of seconds, at most 86400
    """

    challenge: bytes
    submission: bytes | None = None
    evidence: bytes | None = None
    name: str | None = None
    max_bytes: int = 25 * 2**20
    time_budget: float = CONFORMANCE_TIME_BUDGET

    def __post_init__(self) -> None:
        if not isinstance(self.challenge, bytes):
            raise TypeError(f"challenge must be bytes, not {type(self.challenge).__name__}")
        for name in ("submission", "evidence"):
            _optional_bytes(getattr(self, name), name)
        _conformance_limits(self.max_bytes, self.time_budget)


def _optional_bytes(value: object, name: str) -> None:
    if value is not None and not isinstance(value, bytes):
        raise TypeError(f"{name} must be bytes or None, not {type(value).__name__}")


def _conformance_limits(max_bytes: object, time_budget: object) -> None:
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError(f"max_bytes must be a positive int, got {max_bytes!r}")
    if not _valid_seconds(time_budget):
        raise ValueError(f"time_budget must be a positive number of seconds, at most {MAX_SECONDS}")


class _ConformanceRefusedError(Exception):
    """A file a conformance request cannot use; ``outcome`` is the answer."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.detail)
        self.outcome = outcome


def _conformance_refused(kind: str, detail: str) -> _ConformanceRefusedError:
    return _ConformanceRefusedError(Outcome(code=Code.INVALID_DOCUMENT, detail=detail, kind=kind))


def _conformance_too_large(detail: str) -> _ConformanceRefusedError:
    return _ConformanceRefusedError(Outcome(code=Code.INPUT_TOO_LARGE, detail=detail))


def _conformance_detail(exc: BaseException, scratch: Path | None = None) -> str:
    """The suite's message, with the private folder taken out of it, at a bounded length.

    The caller never named the folder its files were written to. The message can quote
    a value of a file it was given (a suite version, a digest, a seed): it is text to
    show, never markup.
    """
    text = str(exc)
    if scratch is not None:
        for folder in ("challenge", "submission"):
            text = text.replace(str(scratch / folder) + os.sep, "")
        text = text.replace(str(scratch), ".")
    text = _scrub(text)
    if len(text) > _CONFORMANCE_MAX_DETAIL:
        text = text[: _CONFORMANCE_MAX_DETAIL - 1] + "…"
    return text


def _conformance_failure(exc: Exception, scratch: Path | None = None) -> Outcome:
    """The :class:`Outcome` of an error a conformance request met."""
    if isinstance(exc, _ConformanceRefusedError):
        return exc.outcome
    if isinstance(exc, DocumentError):
        return Outcome(
            code=Code.INVALID_DOCUMENT, detail=_conformance_detail(exc, scratch), kind=exc.kind
        )
    if isinstance(exc, InstanceReadError):
        return Outcome(code=Code.INPUT_UNREADABLE, detail=_conformance_detail(exc, scratch))
    if isinstance(exc, ResourceError):
        return _failure(Code.RESOURCE_ERROR, exc)
    return _failure(Code.INTERNAL, exc)


def _conformance_name(name: str | None) -> str:
    """The last part of ``name``, with characters a file system may refuse replaced.

    Examples:
        >>> _conformance_name("up/../my export.xml"), _conformance_name("../..")
        ('my export.xml', 'submission')
    """
    last = run._NAME_SEPARATORS.split(name or "")[-1].strip()
    cleaned = _CONFORMANCE_NAME.sub("_", last)[:_CONFORMANCE_NAME_CHARS].strip(" .")
    return cleaned or "submission"


def _conformance_zip(folder: str, files: Mapping[str, bytes]) -> bytes:
    """The files in one folder of a ZIP, written the same way every time."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(f"{folder}/{name}", date_time=_CONFORMANCE_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()


def _conformance_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """The one member of ``archive`` whose file name is ``name``, read within ``limit``.

    None when no member has that name. Only the last part of a member's path is looked
    at, and it is never used as a path.
    """
    found = [
        member
        for member in archive.infolist()
        if not member.is_dir() and member.filename.replace("\\", "/").split("/")[-1] == name
    ]
    if not found:
        return None
    if len(found) > 1:
        raise _conformance_refused(
            "not-a-challenge", f"The challenge ZIP must hold exactly one {name}."
        )
    if found[0].file_size > limit:
        raise _conformance_too_large(f"The {name} in the challenge ZIP is larger than allowed.")
    with archive.open(found[0]) as member:
        data = member.read(limit + 1)  # a member can inflate to more than it states
    if len(data) > limit:
        raise _conformance_too_large(f"The {name} in the challenge ZIP is larger than allowed.")
    return data


def _pairwise(manifest: bytes) -> bool:
    """Whether a manifest names another product's export (``source_export``)."""
    try:
        described = json.loads(manifest)
    except (ValueError, RecursionError):
        return False  # conformance.load says what is wrong with it
    return isinstance(described, dict) and "source_export" in described


def _conformance_files(challenge: bytes, max_bytes: int) -> tuple[bytes, bytes | None]:
    """``challenge.json``, and the ``challenge.xml`` of a pairwise challenge, from what was given.

    Raises:
        _ConformanceRefusedError: The bytes are neither a manifest nor a challenge ZIP
            within the limits, or a pairwise challenge came without its ``challenge.xml``.
    """
    if not challenge.startswith(b"PK"):
        if len(challenge) > CONFORMANCE_MAX_MANIFEST_BYTES:
            raise _conformance_too_large(
                "The challenge is neither a ZIP nor a challenge.json, which is at most"
                f" {CONFORMANCE_MAX_MANIFEST_BYTES} bytes."
            )
        if _pairwise(challenge):
            raise _conformance_refused(
                "source-export",
                "This challenge was made from another product's export: give the challenge"
                f" ZIP, which holds that export as {_CONFORMANCE_IMPORT_FILE}.",
            )
        return challenge, None
    if len(challenge) > max_bytes:
        raise _conformance_too_large("The challenge ZIP is larger than allowed.")
    try:
        with zipfile.ZipFile(BytesIO(challenge)) as archive:
            if len(archive.infolist()) > CONFORMANCE_MAX_ZIP_MEMBERS:
                raise _conformance_too_large(
                    f"The challenge ZIP holds more than {CONFORMANCE_MAX_ZIP_MEMBERS} files."
                )
            manifest = _conformance_member(
                archive, _CONFORMANCE_MANIFEST, CONFORMANCE_MAX_MANIFEST_BYTES
            )
            if manifest is None:
                raise _conformance_refused(
                    "not-a-challenge",
                    f"The challenge ZIP must hold exactly one {_CONFORMANCE_MANIFEST}.",
                )
            if not _pairwise(manifest):
                return manifest, None  # every other file is rebuilt from the seed
            exported = _conformance_member(archive, _CONFORMANCE_IMPORT_FILE, max_bytes)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError) as exc:
        # RuntimeError: an encrypted member. The reason can quote the archive, so it is
        # not passed on.
        raise _conformance_refused("not-a-challenge", "The challenge ZIP cannot be read.") from exc
    if exported is None:
        raise _conformance_refused(
            "source-export",
            "This challenge was made from another product's export, and its ZIP does not"
            f" hold that export as {_CONFORMANCE_IMPORT_FILE}.",
        )
    return manifest, exported


def _conformance_load(request: ConformanceRequest, scratch: Path) -> Any:
    """The :class:`pydiggs.conformance.Challenge` of a request, by ``conformance.load``.

    The manifest, and the ``challenge.xml`` of a pairwise challenge, are written under
    fixed names in the private folder; ``conformance.load`` rebuilds every other file
    from the seed, and checks and grades a pairwise ``challenge.xml`` again.
    """
    from pydiggs import conformance as suite  # noqa: PLC0415  # loads on first use

    manifest, exported = _conformance_files(request.challenge, request.max_bytes)
    folder = scratch / "challenge"
    folder.mkdir()
    (folder / _CONFORMANCE_MANIFEST).write_bytes(manifest)
    if exported is not None:
        (folder / _CONFORMANCE_IMPORT_FILE).write_bytes(exported)
    return suite.load(folder, max_bytes=request.max_bytes, time_budget=request.time_budget)


def _conformance_submission(request: ConformanceRequest, scratch: Path) -> Path:
    """The submission as a file of its own (reduced) name, which the evidence records."""
    data = request.submission
    assert data is not None  # noqa: S101  # the caller checked
    if len(data) > request.max_bytes:
        raise _conformance_too_large("The submission is larger than allowed.")
    folder = scratch / "submission"
    folder.mkdir()
    path = folder / _conformance_name(request.name)
    path.write_bytes(data)
    return path


def _conformance_request_errors(
    request: ConformanceRequest, needs: Sequence[str]
) -> tuple[tuple[str, str], ...]:
    errors = _name_errors(request.name)
    errors += [(name, "is required") for name in needs if getattr(request, name) is None]
    return tuple(errors)


def _conformance_run(
    request: ConformanceRequest,
    needs: Sequence[str],
    task: Callable[[ConformanceRequest, Path], Mapping[str, Any]],
) -> Outcome:
    """``task`` in a private temporary folder; whatever it meets becomes an :class:`Outcome`."""
    errors = _conformance_request_errors(request, needs)
    if errors:
        return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
    try:
        with tempfile.TemporaryDirectory() as scratch:
            try:
                return Outcome(document=_body(task(request, Path(scratch))))
            except Exception as exc:  # fail closed: no traceback or path reaches the caller
                return _conformance_failure(exc, Path(scratch))
    except OSError as exc:  # the private folder could not be made or removed
        return _failure(Code.INTERNAL, exc)


def conformance_classes() -> dict[str, Any]:
    """The classes a conformance challenge can be made for, and what is not testable yet.

    ``suite_version``, ``pydiggs`` (this release), ``statement`` (what a result does and
    does not show, as the evidence states it), ``classes`` (``id``, ``title``,
    ``direction``, ``family``, as :func:`pydiggs.conformance.classes` lists them) and
    ``untestable`` (the titles of the parts of DIGGS the suite has no class for yet).

    Examples:
        >>> listing = conformance_classes()
        >>> listing["classes"][0]["id"], listing["classes"][0]["direction"]
        ('spt-export', 'export')
    """
    from pydiggs import conformance as suite  # noqa: PLC0415  # loads on first use

    return {
        "suite_version": suite.SUITE_VERSION,
        "pydiggs": pydiggs.__version__,
        "statement": suite.STATEMENT,
        "classes": [
            {"id": c.id, "title": c.title, "direction": c.direction, "family": c.family}
            for c in suite.classes()
        ],
        "untestable": [f.title for f in suite.families() if not f.testable and not f.embedded],
    }


def _conformance_new_errors(request: ConformanceNewRequest) -> tuple[tuple[str, str], ...]:
    """Check the caller-supplied parameters; fixed texts that never echo the value."""
    from pydiggs import conformance as suite  # noqa: PLC0415  # loads on first use
    from pydiggs.conformance import _challenge  # noqa: PLC0415

    errors: list[tuple[str, str]] = []
    known = [c.id for c in suite.classes()]
    if not isinstance(request.class_id, str) or request.class_id not in known:
        errors.append(("class_id", f"expected one of {', '.join(known)}"))
    for name in ("product", "version"):
        try:
            _challenge._clean(getattr(request, name), name)
        except ValueError:
            errors.append((name, f"must be 1 to {_challenge._MAX_TEXT} characters of text"))
    seed = request.seed
    if seed is not None and (not isinstance(seed, str) or not _challenge._SEED.fullmatch(seed)):
        errors.append(("seed", "must be 1 to 64 letters, digits, '.', '_' or '-'"))
    return tuple(errors)


def conformance_new(request: ConformanceNewRequest) -> Outcome:
    """Make a conformance challenge: :func:`pydiggs.conformance.new`, as a ZIP of its folder.

    The document is one line of JSON: ``class_id``, ``title``, ``direction``, ``seed``,
    ``token``, ``id``, ``folder`` (the folder's name, ``<class>-<seed>``), ``files`` (the
    names in it), ``manifest`` (the text of ``challenge.json``), ``readme`` (the text of
    ``README.md``), ``output_sha256`` and ``output_base64``: a ZIP of the folder
    ``pydiggs conformance new`` writes, with fixed timestamps, so the same seed gives the
    same bytes. With ``evidence`` and ``export`` the challenge is a pairwise one, and the
    ZIP's ``challenge.xml`` is that export.

    Args:
        request: The class, the product and, for a pairwise challenge, the other
            product's evidence and export.

    Returns:
        The document, or an error :class:`Code`: ``invalid-parameter`` (an unknown
        class, a product, version or seed that cannot be used, or a pairwise challenge
        asked of an export class or with another seed), ``input-too-large`` (the
        evidence or the export), ``invalid-document`` (``kind`` ``not-evidence`` or
        ``source-export``: the evidence is not passing evidence of this suite for the
        export, or does not verify), ``resource-error`` or ``internal``. Never raises
        for bad input.

    Examples:
        >>> request = ConformanceNewRequest(
        ...     class_id="spt-export", product="MyLog", version="4.2.1", seed="demo"
        ... )
        >>> made = json.loads(conformance_new(request).document)
        >>> made["folder"], made["direction"], len(made["output_sha256"])
        ('spt-export-demo', 'export', 64)
        >>> bad = ConformanceNewRequest(class_id="nope", product="P", version="1")
        >>> conformance_new(bad).errors[0][0]
        'class_id'
    """
    from pydiggs import conformance as suite  # noqa: PLC0415  # loads on first use

    try:
        errors = _conformance_new_errors(request)
        if errors:
            return Outcome(code=Code.INVALID_PARAMETER, detail="invalid parameters", errors=errors)
        if request.evidence is not None and len(request.evidence) > CONFORMANCE_MAX_EVIDENCE_BYTES:
            raise _conformance_too_large("The evidence is larger than allowed.")
        if request.export is not None and len(request.export) > request.max_bytes:
            raise _conformance_too_large("The export is larger than allowed.")
        try:
            challenge = suite.new(
                request.class_id,
                product=request.product,
                version=request.version,
                seed=request.seed,
                from_export=request.evidence,
                export=request.export,
                max_bytes=request.max_bytes,
                time_budget=request.time_budget,
            )
        except DocumentError:
            raise
        except ValueError as exc:  # a pairwise challenge of an export class, or another seed
            return Outcome(code=Code.INVALID_PARAMETER, detail=_conformance_detail(exc))
        folder = f"{challenge.info.id}-{challenge.seed}"
        output = _conformance_zip(folder, challenge.files)
        result = {
            "class_id": challenge.info.id,
            "title": challenge.info.title,
            "direction": challenge.info.direction,
            "seed": challenge.seed,
            "token": challenge.token,
            "id": challenge.id,
            "folder": folder,
            "files": sorted(challenge.files),
            "manifest": challenge.files[_CONFORMANCE_MANIFEST].decode("utf-8"),
            "readme": challenge.files["README.md"].decode("utf-8"),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_base64": base64.b64encode(output).decode("ascii"),
        }
        return Outcome(document=_body(result))
    except Exception as exc:  # fail closed: no traceback or path reaches the caller
        return _conformance_failure(exc)


def _reference_task(request: ConformanceRequest, scratch: Path) -> Mapping[str, Any]:
    loaded = _conformance_load(request, scratch)
    output: bytes = loaded.reference()
    return {
        "output_name": f"{loaded.info.id}-{loaded.seed}-pydiggs.xml",
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "output_base64": base64.b64encode(output).decode("ascii"),
    }


def conformance_reference(request: ConformanceRequest) -> Outcome:
    """The known-good DIGGS file of a challenge: ``Challenge.reference()``.

    It is pyDIGGS's own export of the challenge's data, a way to try the flow. The
    document is one line of JSON: ``output_name``, ``output_sha256`` and
    ``output_base64``. Only ``request.challenge`` is used.

    Returns:
        The document, or an error :class:`Code` as :func:`conformance_grade` answers
        for its challenge.

    Examples:
        >>> manifest = b'{"format": "something else"}'
        >>> refused = conformance_reference(ConformanceRequest(challenge=manifest))
        >>> refused.code.value, refused.kind, refused.detail
        ('invalid-document', 'not-a-challenge', 'challenge.json is not a challenge manifest')
    """
    return _conformance_run(request, (), _reference_task)


def _grade_task(request: ConformanceRequest, scratch: Path) -> Mapping[str, Any]:
    from pydiggs import conformance as suite  # noqa: PLC0415  # loads on first use

    result = suite.grade(
        _conformance_load(request, scratch),
        _conformance_submission(request, scratch),
        max_bytes=request.max_bytes,
        time_budget=request.time_budget,
    )
    return {
        "passed": result.passed,
        "evidence": result.to_dict(),
        "evidence_json": result.to_json() + "\n",
        "report_html": result.to_html(),
    }


def conformance_grade(request: ConformanceRequest) -> Outcome:
    """Grade a submission against a challenge: :func:`pydiggs.conformance.grade`.

    The class of the challenge decides what the submission is: the DIGGS file a product
    exported, or the filled ``answers.csv`` of an import class. The document is one line
    of JSON: ``passed``, ``evidence`` (the diggs-conformance document), ``evidence_json``
    (the exact text ``pydiggs conformance grade`` writes as ``evidence.json``) and
    ``report_html`` (its ``report.html``). A file that fails, or is not DIGGS at all, is
    a failing result, not an error.

    Args:
        request: The challenge, the submission and its file name.

    Returns:
        The document, or an error :class:`Code`: ``invalid-parameter`` (no submission,
        or a name that cannot be used), ``input-too-large`` (the challenge, a member of
        its ZIP, the number of its members, or the submission), ``invalid-document``
        (``kind`` one of :data:`CONFORMANCE_KINDS`: the challenge is not one, is of a
        suite version this release does not grade, or is a pairwise one without its
        own ``challenge.xml``; or the answers cannot be read), ``input-unreadable``,
        ``resource-error`` or ``internal``. ``detail`` is then the suite's own message,
        which can quote a value of the files given. Never raises for bad input.

    Examples:
        >>> new = ConformanceNewRequest(
        ...     class_id="spt-export", product="P", version="1", seed="demo"
        ... )
        >>> challenge = base64.b64decode(json.loads(conformance_new(new).document)["output_base64"])
        >>> request = ConformanceRequest(challenge=challenge, submission=b"<x/>", name="a.xml")
        >>> graded = json.loads(conformance_grade(request).document)
        >>> graded["passed"], graded["evidence"]["submission"]["name"]
        (False, 'a.xml')
    """
    return _conformance_run(request, ("submission",), _grade_task)


def _verify_task(request: ConformanceRequest, scratch: Path) -> Mapping[str, Any]:
    from pydiggs import conformance as suite  # noqa: PLC0415  # loads on first use

    raw = request.evidence
    assert raw is not None  # noqa: S101  # the caller checked
    if len(raw) > CONFORMANCE_MAX_EVIDENCE_BYTES:
        raise _conformance_too_large("The evidence is larger than allowed.")
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as exc:
        raise _conformance_refused("not-evidence", "The evidence is not JSON.") from exc
    if not isinstance(document, dict):
        raise _conformance_refused(
            "not-evidence", "The evidence is not a diggs-conformance document."
        )
    outcome = suite.verify(
        document,
        _conformance_load(request, scratch),
        _conformance_submission(request, scratch),
        max_bytes=request.max_bytes,
        time_budget=request.time_budget,
    )
    return {
        "reproduced": outcome.ok,
        "differences": list(outcome.differences),
        "notes": list(outcome.notes),
        "verdict": "pass" if outcome.result.passed else "fail",
        "class_id": outcome.result.info.id,
    }


def conformance_verify(request: ConformanceRequest) -> Outcome:
    """Grade a submission again and compare with evidence: :func:`pydiggs.conformance.verify`.

    The document is one line of JSON: ``reproduced`` (grading again gives the evidence),
    ``differences`` (the parts of the evidence that differ), ``notes``, ``verdict``
    (``pass`` or ``fail``, of the grading done now) and ``class_id``.

    Args:
        request: The evidence, the challenge, the submission and its file name.

    Returns:
        The document, or an error :class:`Code`: as :func:`conformance_grade`, and
        ``invalid-parameter`` without evidence, ``input-too-large`` for evidence over
        :data:`CONFORMANCE_MAX_EVIDENCE_BYTES`, ``invalid-document`` of ``kind``
        ``not-evidence`` for evidence that is not a diggs-conformance document this
        release reads. Never raises for bad input.

    Examples:
        >>> request = ConformanceRequest(challenge=b"{}", submission=b"<x/>")
        >>> conformance_verify(request).errors
        (('evidence', 'is required'),)
    """
    return _conformance_run(request, ("submission", "evidence"), _verify_task)


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


_AnyRequest = (
    Request
    | ReadRequest
    | ConvertRequest
    | Ags4CheckRequest
    | DiffRequest
    | TablesRequest
    | TablesFromDiggsRequest
)


def _run(request: _AnyRequest) -> Outcome:
    """Answer any request in the calling thread."""
    answers: dict[type, Callable[..., Outcome]] = {
        ReadRequest: read,
        ConvertRequest: convert,
        Ags4CheckRequest: ags4_check,
        DiffRequest: diff,
        TablesRequest: tables,
        TablesFromDiggsRequest: tables_from_diggs,
    }
    return answers.get(type(request), validate)(request)


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
        request: The content and its parameters.
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


def diff_in_pool(pool: WorkerPool, request: DiffRequest, *, timeout: float) -> Outcome:
    """Compare two files in a worker process, killing it after ``timeout`` seconds.

    As :func:`validate_in_pool`, for :func:`diff`: one job reads both files and
    compares them, so ``timeout`` covers the pair.

    Args:
        pool: From :func:`make_pool`.
        request: The two files and the comparison options.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`diff`, plus ``timeout``.
    """
    return _in_pool(pool, request, timeout=timeout, what="Comparison")


def tables_in_pool(pool: WorkerPool, request: TablesRequest, *, timeout: float) -> Outcome:
    """Write, check or round-trip a package in a worker process, killed after ``timeout`` s.

    As :func:`validate_in_pool`, for :func:`tables`: the hard timeout is the only time
    limit a package has.

    Args:
        pool: From :func:`make_pool`.
        request: The package and its parameters.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`tables`, plus ``timeout``.
    """
    return _in_pool(pool, request, timeout=timeout, what="The tables request")


def tables_from_diggs_in_pool(
    pool: WorkerPool, request: TablesFromDiggsRequest, *, timeout: float
) -> Outcome:
    """Turn a DIGGS file into tables in a worker process, killed after ``timeout`` seconds.

    As :func:`validate_in_pool`, for :func:`tables_from_diggs`.

    Args:
        pool: From :func:`make_pool`.
        request: The DIGGS file and its parameters.
        timeout: Hard limit in seconds.

    Returns:
        As for :func:`tables_from_diggs`, plus ``timeout``.
    """
    return _in_pool(pool, request, timeout=timeout, what="Reading into tables")


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


def diff_info() -> dict[str, str]:
    """The diggs-diff version and schema URL that :func:`diff` answers with.

    Examples:
        >>> diff_info()["diff_schema_version"]
        '1.0'
    """
    from pydiggs.diff import DIFF_SCHEMA_URL, DIFF_SCHEMA_VERSION  # noqa: PLC0415

    return {"diff_schema_version": DIFF_SCHEMA_VERSION, "diff_schema_url": DIFF_SCHEMA_URL}


def versions() -> dict[str, Any]:
    """:func:`pydiggs.version_info` as a dict, for ``versions`` endpoints.

    Examples:
        >>> versions()["findings_schema_version"]
        '1.3'
    """
    return version_info().to_dict()


def tables_info() -> dict[str, str]:
    """The version of the DIGGS tables profile the ``tables`` operations read and write.

    Examples:
        >>> tables_info()["tables_profile_version"]
        '1.0-beta.1'
    """
    from pydiggs.tables import PROFILE_VERSION  # noqa: PLC0415  # loads the profile

    return {"tables_profile_version": PROFILE_VERSION}


def package_file(name: str) -> bool:
    """Whether a file named ``name`` belongs in a tables package (a plain CSV or JSON name).

    The sidecar reads only such files from a package folder; the same rule refuses any
    other entry of a package ZIP.

    Examples:
        >>> package_file("features.csv"), package_file(".DS_Store"), package_file("a.txt")
        (True, False, False)
    """
    from pydiggs.tables._package import valid_name  # noqa: PLC0415

    return valid_name(name)


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
