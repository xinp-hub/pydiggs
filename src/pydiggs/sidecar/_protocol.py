r"""The sidecar wire protocol: NDJSON framing, JSON-RPC 2.0 messages, errors, schemas.

Pure: no I/O, no threads, no pydiggs imports beyond the service's error codes. The
adapter (``_loop``) reads lines, hands each to :func:`parse_line`, checks parameters
with the ``check_*`` functions and writes the frames built here.

Framing is one UTF-8 JSON value per line, terminated by ``\n`` (``\r\n`` is
accepted on input). Every frame this module builds is a single line: JSON escapes
``\n`` and ``\r`` inside strings, and :func:`_one_line` also escapes U+0085, U+2028
and U+2029, which some line splitters treat as line ends.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from pydiggs._service import (
    CONVERT_AGS_VERSIONS,
    CONVERT_DIGGS_VERSIONS,
    CONVERT_TARGETS,
    DIFF_DEFAULTS,
    DIFF_MATCH,
    DIFF_SIDES,
    MAX_NAME_INPUT_CHARS,
    TABLES_OPERATIONS,
    Code,
)

__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_VERSIONS",
    "ErrorCode",
    "Invalid",
    "Message",
    "ProtocolError",
    "check_ags4_check",
    "check_cancel",
    "check_convert",
    "check_diff",
    "check_empty",
    "check_explain",
    "check_initialize",
    "check_read",
    "check_rules_list",
    "check_tables",
    "check_tables_from_diggs",
    "check_validate",
    "error_frame",
    "max_line_bytes",
    "negotiate",
    "notification_frame",
    "openrpc_document",
    "parse_line",
    "result_frame",
    "service_error",
]

#: The protocol version this build speaks best.
PROTOCOL_VERSION: Final = "1.4"
#: Every protocol version this build can answer, oldest first. 1.1 adds ``read``; 1.2
#: adds ``convert`` and ``ags4.check``; 1.3 adds ``diff``; 1.4 adds the ``tables.*``
#: methods (preview).
SUPPORTED_VERSIONS: Final = ("1.0", "1.1", "1.2", "1.3", "1.4")
#: The ``tables.*`` method that runs each :data:`~pydiggs._service.TABLES_OPERATIONS` entry.
TABLES_METHODS: Final = {f"tables.{op.replace('-', '_')}": op for op in TABLES_OPERATIONS}
#: Largest ``read``, ``convert``, ``diff`` or ``tables.*`` result sent; a larger one is
#: ``result-too-large``.
MAX_RESULT_BYTES: Final = 256 * 2**20

#: Longest string id accepted on a request.
MAX_ID_CHARS: Final = 256
#: Longest ``client_info.name`` and ``client_info.version``.
MAX_CLIENT_NAME: Final = 128
MAX_CLIENT_VERSION: Final = 64
#: Longest ``path`` accepted (before any checks).
MAX_PATH_CHARS: Final = 4096
#: Deepest JSON nesting accepted in a line (deeper is a parse error on every Python).
MAX_JSON_DEPTH: Final = 64
#: Longest ``rule_id``; the service applies the same cap.
MAX_RULE_ID_CHARS: Final = 256
#: Room for the JSON-RPC envelope and parameters around the base64 content.
LINE_SLACK: Final = 64 * 1024
#: Rough size of one finding in a result line, for ``limits.max_response_bytes_estimate``.
_FINDING_BYTES: Final = 2048

_VERSION_RE: Final = re.compile(r"(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")


class ErrorCode(Enum):
    """Every JSON-RPC error the sidecar sends: ``(code, data.code slug)``.

    The ``-32000..-32099`` band is pydiggs-defined: never read it with LSP or MCP
    meanings. Hosts switch on the slug (``error.data.code``), which is stable.

    Examples:
        >>> ErrorCode.BUSY.code, ErrorCode.BUSY.slug
        (-32000, 'busy')
    """

    PARSE_ERROR = (-32700, "parse-error")
    INVALID_REQUEST = (-32600, "invalid-request")
    METHOD_NOT_FOUND = (-32601, "method-not-found")
    INVALID_PARAMS = (-32602, "invalid-params")
    INTERNAL_ERROR = (-32603, "internal")
    BUSY = (-32000, "busy")
    NOT_INITIALIZED = (-32001, "not-initialized")
    SHUTTING_DOWN = (-32002, "shutting-down")
    INPUT_TOO_LARGE = (-32003, "input-too-large")
    COMPRESSED_INPUT = (-32004, "compressed-input")
    PATH_NOT_PERMITTED = (-32005, "path-not-permitted")
    INPUT_UNREADABLE = (-32006, "input-unreadable")
    RESOURCE_ERROR = (-32007, "resource-error")
    CANCELLED = (-32008, "cancelled")
    UNSUPPORTED_PROTOCOL_VERSION = (-32009, "unsupported-protocol-version")
    INVALID_DOCUMENT = (-32010, "invalid-document")
    RESULT_TOO_LARGE = (-32011, "result-too-large")

    @property
    def code(self) -> int:
        """The JSON-RPC ``error.code``."""
        return int(self.value[0])

    @property
    def slug(self) -> str:
        """The stable ``error.data.code``."""
        return str(self.value[1])


#: Fixed messages, so no caller value is ever echoed.
_MESSAGES: Final = {
    ErrorCode.PARSE_ERROR: "Parse error",
    ErrorCode.INVALID_REQUEST: "Invalid request",
    ErrorCode.METHOD_NOT_FOUND: "Method not found",
    ErrorCode.INVALID_PARAMS: "Invalid params",
    ErrorCode.INTERNAL_ERROR: "An internal error occurred.",
    ErrorCode.BUSY: "A validation is already running and another is queued.",
    ErrorCode.NOT_INITIALIZED: "The first request must be initialize.",
    ErrorCode.SHUTTING_DOWN: "The server is shutting down.",
    ErrorCode.INPUT_TOO_LARGE: "The document is larger than allowed.",
    ErrorCode.COMPRESSED_INPUT: "Compressed documents are not accepted.",
    ErrorCode.PATH_NOT_PERMITTED: "The path is not permitted.",
    ErrorCode.INPUT_UNREADABLE: "The document could not be read.",
    ErrorCode.RESOURCE_ERROR: "pydiggs's bundled reference data could not be loaded.",
    ErrorCode.CANCELLED: "The request was cancelled.",
    ErrorCode.UNSUPPORTED_PROTOCOL_VERSION: "Unsupported protocol version.",
    ErrorCode.INVALID_DOCUMENT: "The bytes are not a document of the expected format.",
    ErrorCode.RESULT_TOO_LARGE: "The result is larger than allowed.",
}

_FROM_SERVICE: Final = {
    Code.INPUT_TOO_LARGE: ErrorCode.INPUT_TOO_LARGE,
    Code.COMPRESSED_INPUT: ErrorCode.COMPRESSED_INPUT,
    Code.INPUT_UNREADABLE: ErrorCode.INPUT_UNREADABLE,
    Code.INVALID_PARAMETER: ErrorCode.INVALID_PARAMS,
    Code.RESOURCE_ERROR: ErrorCode.RESOURCE_ERROR,
    Code.INTERNAL: ErrorCode.INTERNAL_ERROR,
    Code.CANCELLED: ErrorCode.CANCELLED,
    Code.TIMEOUT: ErrorCode.INTERNAL_ERROR,  # in-thread validation never times out
    Code.INVALID_DOCUMENT: ErrorCode.INVALID_DOCUMENT,
    Code.RESULT_TOO_LARGE: ErrorCode.RESULT_TOO_LARGE,
}

RequestId = str | int


@dataclass(eq=False)
class ProtocolError(Exception):
    """A JSON-RPC error to send back.

    Examples:
        >>> err = ProtocolError(ErrorCode.INVALID_PARAMS, errors=(("x", "unknown"),))
        >>> err.data()
        {'code': 'invalid-params', 'errors': [{'param': 'x', 'problem': 'unknown'}]}
    """

    error: ErrorCode
    message: str | None = None
    errors: tuple[tuple[str, str], ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def data(self) -> dict[str, Any]:
        """The ``error.data`` object: the slug, then any parameter problems and extras."""
        data: dict[str, Any] = {"code": self.error.slug}
        if self.errors:
            data["errors"] = [{"param": p, "problem": q} for p, q in self.errors]
        data.update(self.extra)
        return data

    def text(self) -> str:
        """The ``error.message``: the given text, else the fixed one."""
        return self.message or _MESSAGES[self.error]


def service_error(code: Code, detail: str = "", **extra: Any) -> ProtocolError:
    """Map a service :class:`~pydiggs._service.Code` to its protocol error.

    Examples:
        >>> service_error(Code.CANCELLED).error
        <ErrorCode.CANCELLED: (-32008, 'cancelled')>
    """
    return ProtocolError(_FROM_SERVICE[code], detail or None, extra={**extra})


@dataclass(frozen=True, slots=True)
class Message:
    """One parsed request (``id`` set) or notification (``is_notification``).

    Examples:
        >>> msg = parse_line(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
        >>> (msg.id, msg.method, msg.params, msg.is_notification)
        (1, 'ping', {}, False)
    """

    method: str
    params: dict[str, Any]
    id: RequestId | None = None
    is_notification: bool = False


@dataclass(frozen=True, slots=True)
class Invalid:
    """A line that cannot be served: the error, and the id to answer (or None).

    ``id`` is the request's id when the line was a well-formed request object with a
    usable id; ``None`` otherwise (sent as ``"id": null``). ``silent`` marks a
    notification with unusable params, which by JSON-RPC rules gets no answer.
    """

    error: ProtocolError
    id: RequestId | None = None
    silent: bool = False


def max_line_bytes(max_bytes: int) -> int:
    """The longest line accepted: base64 of ``max_bytes`` plus 64 KiB of envelope.

    Examples:
        >>> max_line_bytes(3)
        65540
    """
    return math.ceil(4 * max_bytes / 3) + LINE_SLACK


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _valid_id(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and len(value) <= MAX_ID_CHARS


def parse_line(line: bytes) -> Message | Invalid | None:
    r"""Parse one line (without its terminator) into a message, an error, or nothing.

    Blank lines give None. Invalid UTF-8, a byte-order mark, invalid JSON (NaN and
    Infinity included), nesting deeper than :data:`MAX_JSON_DEPTH` and integers
    longer than the interpreter converts are parse errors with ``id: null``. A batch (array), a
    null or malformed id and a non-object are invalid requests.

    Args:
        line: The raw bytes of one line, with ``\n`` / ``\r\n`` removed.

    Returns:
        A :class:`Message`, an :class:`Invalid`, or None for a blank line.

    Examples:
        >>> parse_line(b"   ") is None
        True
        >>> parse_line(b"[1]").error.error.slug
        'invalid-request'
        >>> parse_line(b"\xff").error.error.slug
        'parse-error'
    """
    if not line.strip(b" \t"):
        return None
    if line.startswith(b"\xef\xbb\xbf"):
        return Invalid(ProtocolError(ErrorCode.PARSE_ERROR, "Parse error: byte-order mark"))
    try:
        text = line.decode("utf-8", "strict")
        value = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return Invalid(ProtocolError(ErrorCode.PARSE_ERROR))
    if _too_deep(value):
        return Invalid(ProtocolError(ErrorCode.PARSE_ERROR))
    return _message(value)


def _too_deep(value: object) -> bool:
    """Nesting deeper than :data:`MAX_JSON_DEPTH`, checked without recursion.

    Before Python 3.14 ``json.loads`` raises ``RecursionError`` near the recursion
    limit; 3.14 parses far deeper. One explicit limit gives the same answer everywhere.
    """
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            node = list(node.values())
        if isinstance(node, list):
            if depth > MAX_JSON_DEPTH:
                return True
            stack.extend((child, depth + 1) for child in node)
    return False


def _message(value: object) -> Message | Invalid:
    if not isinstance(value, dict):
        text = "Batches are not supported" if isinstance(value, list) else None
        return Invalid(ProtocolError(ErrorCode.INVALID_REQUEST, text))
    has_id = "id" in value
    raw_id = value.get("id")
    if has_id and not _valid_id(raw_id):
        return Invalid(ProtocolError(ErrorCode.INVALID_REQUEST, "Invalid id"))
    request_id: RequestId | None = raw_id if has_id else None
    method = value.get("method")
    params = value.get("params", {})
    if (
        value.get("jsonrpc") != "2.0"
        or not isinstance(method, str)
        or not method
        or not isinstance(params, dict | list)
    ):
        return Invalid(ProtocolError(ErrorCode.INVALID_REQUEST), request_id)
    if isinstance(params, list):
        error = ProtocolError(ErrorCode.INVALID_PARAMS, errors=(("params", "must be an object"),))
        return Invalid(error, request_id, silent=not has_id)
    return Message(method, params, request_id, is_notification=not has_id)


# --- frames ---------------------------------------------------------------------------------

_LINE_BREAKS: Final = (
    (b"\xc2\x85", b"\\u0085"),
    (b"\xe2\x80\xa8", b"\\u2028"),
    (b"\xe2\x80\xa9", b"\\u2029"),
)


def _one_line(data: bytes) -> bytes:
    """Escape U+0085, U+2028 and U+2029 in UTF-8 JSON (they occur only inside strings)."""
    for raw, escaped in _LINE_BREAKS:
        if raw in data:
            data = data.replace(raw, escaped)
    return data


def _dumps(value: object) -> bytes:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return _one_line(text.encode("utf-8", "backslashreplace"))


def result_frame(
    request_id: RequestId, result: object = None, *, raw: bytes | None = None
) -> bytes:
    """A success response line. ``raw`` is a result already encoded as JSON bytes.

    Examples:
        >>> print(result_frame(1, {}).decode().strip())
        {"jsonrpc":"2.0","id":1,"result":{}}
        >>> print(result_frame("a", raw=b'{"x":1}').decode().strip())
        {"jsonrpc":"2.0","id":"a","result":{"x":1}}
    """
    body = _one_line(raw) if raw is not None else _dumps(result)
    return b'{"jsonrpc":"2.0","id":' + _dumps(request_id) + b',"result":' + body + b"}\n"


def error_frame(request_id: RequestId | None, error: ProtocolError) -> bytes:
    """An error response line (``id: null`` when the request's id is unknown).

    Examples:
        >>> import json
        >>> json.loads(error_frame(None, ProtocolError(ErrorCode.PARSE_ERROR)))["error"]
        {'code': -32700, 'message': 'Parse error', 'data': {'code': 'parse-error'}}
    """
    body = {"code": error.error.code, "message": error.text(), "data": error.data()}
    return _dumps({"jsonrpc": "2.0", "id": request_id, "error": body}) + b"\n"


def notification_frame(method: str, params: dict[str, Any]) -> bytes:
    """A server-to-host notification line (``$/progress``).

    Examples:
        >>> print(notification_frame("$/progress", {"request_id": 1}).decode().strip())
        {"jsonrpc":"2.0","method":"$/progress","params":{"request_id":1}}
    """
    return _dumps({"jsonrpc": "2.0", "method": method, "params": params}) + b"\n"


# --- parameters -----------------------------------------------------------------------------


def _bad(param: str, problem: str) -> ProtocolError:
    return ProtocolError(ErrorCode.INVALID_PARAMS, errors=((param, problem),))


def _only(params: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        # The names are the caller's; report each as a fixed problem, capped.
        problems = tuple((_shown(name), "unknown parameter") for name in unknown[:20])
        raise ProtocolError(ErrorCode.INVALID_PARAMS, errors=problems)


def _shown(name: str) -> str:
    """A caller-supplied parameter name, made safe to echo (short, printable ASCII)."""
    clean = "".join(c if c.isascii() and c.isprintable() else "?" for c in name[:64])
    return clean + ("..." if len(name) > 64 else "")  # noqa: PLR2004  # the cap above


def _optional_str(params: dict[str, Any], name: str, limit: int) -> str | None:
    value = params.get(name)
    if value is None and name not in params:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise _bad(name, f"must be a string of at most {limit} characters")
    return value


def check_empty(params: dict[str, Any]) -> None:
    """``ping``, ``versions``, ``shutdown``: no parameters at all.

    Examples:
        >>> check_empty({"x": 1})
        Traceback (most recent call last):
        ...
        pydiggs.sidecar._protocol.ProtocolError: ...
    """
    _only(params, frozenset())


@dataclass(frozen=True, slots=True)
class InitializeParams:
    """``initialize`` parameters, checked."""

    protocol_version: str
    progress: bool
    diggs_versions: tuple[str, ...] | None
    client_name: str | None


def check_initialize(params: dict[str, Any], known_versions: tuple[str, ...]) -> InitializeParams:
    """Check ``initialize`` parameters (version syntax only; see :func:`negotiate`).

    Unknown keys inside ``capabilities`` are ignored (hosts may advertise features
    of newer minors); unknown top-level parameters are refused.

    Examples:
        >>> check_initialize({"protocol_version": "1.0"}, ("2.6",)).progress
        False
    """
    _only(params, frozenset({"protocol_version", "client_info", "capabilities", "diggs_versions"}))
    version = params.get("protocol_version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise _bad("protocol_version", "must be a string like '1.0'")
    client_name = None
    info = params.get("client_info")
    if info is not None or "client_info" in params:
        if not isinstance(info, dict):
            raise _bad("client_info", "must be an object")
        _only(
            {f"client_info.{k}": v for k, v in info.items()},
            frozenset({"client_info.name", "client_info.version"}),
        )
        client_name = _optional_str(info, "name", MAX_CLIENT_NAME)
        _optional_str(info, "version", MAX_CLIENT_VERSION)
    progress = False
    caps = params.get("capabilities")
    if caps is not None or "capabilities" in params:
        if not isinstance(caps, dict):
            raise _bad("capabilities", "must be an object")
        progress = caps.get("progress", False)
        if not isinstance(progress, bool):
            raise _bad("capabilities.progress", "must be a boolean")
    versions: tuple[str, ...] | None = None
    if "diggs_versions" in params:
        raw = params["diggs_versions"]
        if (
            not isinstance(raw, list)
            or not 0 < len(raw) <= len(known_versions)
            or not all(isinstance(v, str) and v in known_versions for v in raw)
        ):
            raise _bad("diggs_versions", f"expected some of {', '.join(known_versions)}")
        versions = tuple(sorted(set(raw), key=known_versions.index))
    return InitializeParams(version, progress, versions, client_name)


def negotiate(requested: str) -> str:
    """The protocol version to answer for ``requested`` (already syntax-checked).

    The major must match; the answer is the highest supported minor not above the
    requested one.

    Raises:
        ProtocolError: ``unsupported-protocol-version`` with ``data.supported``.

    Examples:
        >>> negotiate("1.0"), negotiate("1.2"), negotiate("1.7")
        ('1.0', '1.2', '1.4')
        >>> try:
        ...     negotiate("2.0")
        ... except ProtocolError as error:
        ...     print(error.data())
        {'code': 'unsupported-protocol-version', 'supported': ['1.0', '1.1', '1.2', '1.3', '1.4']}
    """
    major, minor = (int(p) for p in requested.split("."))
    candidates = [
        v
        for v in SUPPORTED_VERSIONS
        if int(v.split(".")[0]) == major and int(v.split(".")[1]) <= minor
    ]
    if not candidates:
        raise ProtocolError(
            ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
            extra={"supported": list(SUPPORTED_VERSIONS)},
        )
    return max(candidates, key=lambda v: int(v.split(".")[1]))


@dataclass(frozen=True, slots=True)
class ValidateParams:
    """``validate`` parameters, checked: decoded content or a path to vet."""

    content: bytes | None
    path: str | None
    name: str | None
    checks: list[str] | None
    diggs_version: str | None
    time_budget: float


def _encoded_limit(max_bytes: int) -> int:
    return 4 * math.ceil(max_bytes / 3)


def check_validate(params: dict[str, Any], *, max_bytes: int, time_limit: float) -> ValidateParams:
    """Check ``validate`` parameters and decode ``content_base64``.

    The encoded length is checked against ``max_bytes`` before decoding; decoding is
    strict (standard alphabet, padding, no whitespace).

    Args:
        params: The request's parameters.
        max_bytes: The content-mode limit.
        time_limit: The launch ``--timeout``: the default and largest ``time_budget_s``.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_validate({"content_base64": "PERpZ2dzLz4="}, max_bytes=100, time_limit=60)
        >>> p.content, p.time_budget
        (b'<Diggs/>', 60)
        >>> check_validate({"content_base64": "PERpZ2dzLz4="}, max_bytes=3, time_limit=60)
        Traceback (most recent call last):
        ...
        pydiggs.sidecar._protocol.ProtocolError: ...
    """
    _only(
        params,
        frozenset({"content_base64", "path", "name", "checks", "diggs_version", "time_budget_s"}),
    )
    checks = params.get("checks")
    if "checks" in params and (
        not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)
    ):
        raise _bad("checks", "must be an array of strings")
    content, path, name, version, budget = _check_document(
        params, max_bytes=max_bytes, time_limit=time_limit
    )
    return ValidateParams(content, path, name, checks, version, budget)


@dataclass(frozen=True, slots=True)
class ReadParams:
    """``read`` parameters, checked: decoded content or a path to vet, and ``select``."""

    content: bytes | None
    path: str | None
    name: str | None
    diggs_version: str | None
    time_budget: float
    select: object


def check_read(params: dict[str, Any], *, max_bytes: int, time_limit: float) -> ReadParams:
    """Check ``read`` parameters and decode ``content_base64`` (as :func:`check_validate`).

    ``select`` must be an object when given; the service checks what is in it.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_read(
        ...     {"content_base64": "PERpZ2dzLz4=", "select": {"results": "none"}},
        ...     max_bytes=100,
        ...     time_limit=60,
        ... )
        >>> p.content, p.select
        (b'<Diggs/>', {'results': 'none'})
    """
    _only(
        params,
        frozenset({"content_base64", "path", "name", "diggs_version", "time_budget_s", "select"}),
    )
    select = params.get("select")
    if "select" in params and not isinstance(select, dict):
        raise _bad("select", 'must be an object: {"results": "all" | "none" | [node keys]}')
    content, path, name, version, budget = _check_document(
        params, max_bytes=max_bytes, time_limit=time_limit
    )
    return ReadParams(content, path, name, version, budget, select)


def _check_document(
    params: dict[str, Any], *, max_bytes: int, time_limit: float
) -> tuple[bytes | None, str | None, str | None, str | None, float]:
    """The parameters ``validate`` and ``read`` share: the document, its name and limits."""
    if ("content_base64" in params) == ("path" in params):
        raise _bad("content_base64", "give exactly one of content_base64 and path")
    name = _optional_str(params, "name", MAX_NAME_INPUT_CHARS)
    version = _optional_str(params, "diggs_version", 64)
    budget = _time_budget(params, time_limit)
    content = path = None
    if "path" in params:
        path = params["path"]
        if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
            raise _bad("path", f"must be a string of at most {MAX_PATH_CHARS} characters")
    else:
        content = _decode_base64(params["content_base64"], "content_base64", max_bytes)
    return content, path, name, version, budget


@dataclass(frozen=True, slots=True)
class ConvertParams:
    """``convert`` parameters, checked: decoded content or a path to vet, and the target."""

    content: bytes | None
    path: str | None
    name: str | None
    diggs_version: str | None
    time_budget: float
    to: str
    ags_version: str | None = None


def check_convert(params: dict[str, Any], *, max_bytes: int, time_limit: float) -> ConvertParams:
    """Check ``convert`` parameters (protocol 1.2) and decode ``content_base64``.

    ``to`` is the target format: ``"diggs"`` (the default; the content is AGS4) or
    ``"ags4"`` (the content is DIGGS XML; ``ags_version`` 4.2, the default, or 4.1.1).
    The service checks ``to``, ``diggs_version`` and ``ags_version`` against what it
    converts.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_convert({"content_base64": "IkdST1VQIg=="}, max_bytes=100, time_limit=60)
        >>> p.content, p.to
        (b'"GROUP"', 'diggs')
    """
    _only(
        params,
        frozenset(
            {
                "content_base64",
                "path",
                "name",
                "to",
                "diggs_version",
                "ags_version",
                "time_budget_s",
            }
        ),
    )
    to = _optional_str(params, "to", 64)
    ags_version = _optional_str(params, "ags_version", 64)
    content, path, name, version, budget = _check_document(
        params, max_bytes=max_bytes, time_limit=time_limit
    )
    return ConvertParams(
        content, path, name, version, budget, to or CONVERT_TARGETS[0], ags_version
    )


@dataclass(frozen=True, slots=True)
class Ags4CheckParams:
    """``ags4.check`` parameters, checked: the file and an optional dictionary."""

    content: bytes | None
    path: str | None
    name: str | None
    time_budget: float
    dictionary: bytes | None
    dictionary_path: str | None


def check_ags4_check(
    params: dict[str, Any], *, max_bytes: int, time_limit: float
) -> Ags4CheckParams:
    """Check ``ags4.check`` parameters (protocol 1.2) and decode the base64 content.

    The file is ``content_base64`` or ``path``, as for ``validate``; the optional data
    dictionary is ``dictionary_base64`` or ``dictionary_path`` (pyDIGGS bundles none).
    Each is held to ``max_bytes``; both travel in the one request line.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_ags4_check(
        ...     {"content_base64": "IkdST1VQIg==", "dictionary_base64": "IkdST1VQIg=="},
        ...     max_bytes=100,
        ...     time_limit=60,
        ... )
        >>> p.content, p.dictionary
        (b'"GROUP"', b'"GROUP"')
    """
    _only(
        params,
        frozenset(
            {
                "content_base64",
                "path",
                "name",
                "time_budget_s",
                "dictionary_base64",
                "dictionary_path",
            }
        ),
    )
    content, path, name, _version, budget = _check_document(
        params, max_bytes=max_bytes, time_limit=time_limit
    )
    dictionary = dictionary_path = None
    if "dictionary_base64" in params and "dictionary_path" in params:
        raise _bad("dictionary_base64", "give at most one of dictionary_base64 and dictionary_path")
    if "dictionary_path" in params:
        dictionary_path = params["dictionary_path"]
        if (
            not isinstance(dictionary_path, str)
            or not dictionary_path
            or len(dictionary_path) > MAX_PATH_CHARS
        ):
            raise _bad(
                "dictionary_path", f"must be a string of at most {MAX_PATH_CHARS} characters"
            )
    elif "dictionary_base64" in params:
        dictionary = _decode_base64(params["dictionary_base64"], "dictionary_base64", max_bytes)
    return Ags4CheckParams(content, path, name, budget, dictionary, dictionary_path)


@dataclass(frozen=True, slots=True)
class DiffParams:
    """``diff`` parameters, checked: each file as decoded content or a path to vet.

    ``contents``, ``paths`` and ``names`` are keyed by ``"old"`` and ``"new"``; for each
    side exactly one of its content and its path is set. ``options`` holds the
    comparison options given (the service checks their values).
    """

    contents: dict[str, bytes | None]
    paths: dict[str, str | None]
    names: dict[str, str | None]
    options: dict[str, Any]
    time_budget: float


def check_diff(params: dict[str, Any], *, max_bytes: int, time_limit: float) -> DiffParams:
    """Check ``diff`` parameters (protocol 1.3) and decode the base64 contents.

    Each file is ``<side>_base64`` or ``<side>_path`` (exactly one of the two, for
    ``old`` and for ``new``), named by ``<side>_name``. Each is held to ``max_bytes``;
    both travel in the one request line, so two inline files together must fit
    ``max_line_bytes`` (give paths for larger ones). The options are those of
    :func:`pydiggs.diff.compare`; their values are checked by the service.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_diff(
        ...     {"old_base64": "PERpZ2dzLz4=", "new_path": "/data/b.xml", "max_changes": 5},
        ...     max_bytes=100,
        ...     time_limit=60,
        ... )
        >>> p.contents["old"], p.paths["new"], p.options, p.time_budget
        (b'<Diggs/>', '/data/b.xml', {'max_changes': 5}, 60)
    """
    sides = {f"{side}_{part}" for side in DIFF_SIDES for part in ("base64", "path", "name")}
    _only(params, frozenset({*sides, *DIFF_DEFAULTS, "time_budget_s"}))
    budget = _time_budget(params, time_limit)
    contents: dict[str, bytes | None] = {}
    paths: dict[str, str | None] = {}
    names: dict[str, str | None] = {}
    for side in DIFF_SIDES:
        inline, by_path = f"{side}_base64", f"{side}_path"
        if (inline in params) == (by_path in params):
            raise _bad(inline, f"give exactly one of {inline} and {by_path}")
        names[side] = _optional_str(params, f"{side}_name", MAX_NAME_INPUT_CHARS)
        contents[side] = paths[side] = None
        if by_path in params:
            path = params[by_path]
            if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
                raise _bad(by_path, f"must be a string of at most {MAX_PATH_CHARS} characters")
            paths[side] = path
        else:
            contents[side] = _decode_base64(params[inline], inline, max_bytes)
    options = {key: params[key] for key in DIFF_DEFAULTS if key in params}
    return DiffParams(contents, paths, names, options, budget)


@dataclass(frozen=True, slots=True)
class TablesParams:
    """``tables.to_diggs``, ``tables.check`` and ``tables.round_trip`` parameters, checked.

    ``content`` is the package ZIP, decoded; ``path`` a folder to vet; exactly one is set.
    """

    content: bytes | None
    path: str | None
    name: str | None


def check_tables(params: dict[str, Any], *, max_bytes: int, time_limit: float) -> TablesParams:
    """Check a ``tables.*`` package method's parameters (protocol 1.4) and decode the ZIP.

    The package is ``package_base64`` (a ZIP of the CSV files and ``datapackage.json``,
    held to ``max_bytes``) or ``path`` (an absolute folder, under an ``--allow-path``
    root). There is no ``time_budget_s``: building from tables cannot be interrupted, so
    ``time_limit`` is not used.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_tables({"path": "/data/site", "name": "site"}, max_bytes=100, time_limit=60)
        >>> p.content, p.path, p.name
        (None, '/data/site', 'site')
    """
    del time_limit
    _only(params, frozenset({"package_base64", "path", "name"}))
    if ("package_base64" in params) == ("path" in params):
        raise _bad("package_base64", "give exactly one of package_base64 and path")
    name = _optional_str(params, "name", MAX_NAME_INPUT_CHARS)
    if "path" in params:
        path = params["path"]
        if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
            raise _bad("path", f"must be a string of at most {MAX_PATH_CHARS} characters")
        return TablesParams(None, path, name)
    content = _decode_base64(params["package_base64"], "package_base64", max_bytes)
    return TablesParams(content, None, name)


@dataclass(frozen=True, slots=True)
class TablesFromDiggsParams:
    """``tables.from_diggs`` parameters, checked: decoded content or a path to vet."""

    content: bytes | None
    path: str | None
    name: str | None
    time_budget: float


def check_tables_from_diggs(
    params: dict[str, Any], *, max_bytes: int, time_limit: float
) -> TablesFromDiggsParams:
    """Check ``tables.from_diggs`` parameters (protocol 1.4), as :func:`check_read` does.

    Raises:
        ProtocolError: ``invalid-params`` or ``input-too-large``.

    Examples:
        >>> p = check_tables_from_diggs(
        ...     {"content_base64": "PERpZ2dzLz4="}, max_bytes=100, time_limit=60
        ... )
        >>> p.content, p.time_budget
        (b'<Diggs/>', 60)
    """
    _only(params, frozenset({"content_base64", "path", "name", "time_budget_s"}))
    content, path, name, _version, budget = _check_document(
        params, max_bytes=max_bytes, time_limit=time_limit
    )
    return TablesFromDiggsParams(content, path, name, budget)


def _time_budget(params: dict[str, Any], time_limit: float) -> float:
    """``time_budget_s``: at most the launch ``--timeout``, which is also the default."""
    if "time_budget_s" not in params:
        return time_limit
    raw = params["time_budget_s"]
    if (
        isinstance(raw, bool)
        or not isinstance(raw, int | float)
        or not 0 < raw <= time_limit  # before isfinite: a huge int would overflow
        or not math.isfinite(raw)
    ):
        raise _bad("time_budget_s", f"must be a number of seconds in (0, {time_limit:g}]")
    return raw


def _decode_base64(encoded: object, param: str, max_bytes: int) -> bytes:
    """Strictly decode one base64 parameter, refusing more than ``max_bytes`` bytes."""
    if not isinstance(encoded, str):
        raise _bad(param, "must be a base64 string")
    if len(encoded) > _encoded_limit(max_bytes):
        raise ProtocolError(ErrorCode.INPUT_TOO_LARGE, extra={"max_bytes": max_bytes})
    try:
        content = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        raise _bad(param, "must be standard, padded base64") from None
    if len(content) > max_bytes:
        raise ProtocolError(ErrorCode.INPUT_TOO_LARGE, extra={"max_bytes": max_bytes})
    return content


def check_rules_list(params: dict[str, Any]) -> str | None:
    """``rules.list``: the optional ``step`` (its value is checked by the service).

    Examples:
        >>> check_rules_list({"step": "schema"})
        'schema'
    """
    _only(params, frozenset({"step"}))
    return _optional_str(params, "step", 64)


def check_explain(params: dict[str, Any]) -> str:
    """``rules.explain``: the required ``rule_id`` (grammar checked by the service).

    Examples:
        >>> check_explain({"rule_id": "dictionary.5"})
        'dictionary.5'
    """
    _only(params, frozenset({"rule_id"}))
    rule_id = params.get("rule_id")
    if not isinstance(rule_id, str) or len(rule_id) > MAX_RULE_ID_CHARS:
        raise _bad("rule_id", f"must be a string of at most {MAX_RULE_ID_CHARS} characters")
    return rule_id


def check_cancel(params: dict[str, Any]) -> RequestId | None:
    """``$/cancelRequest``: the id to cancel, or None when the params are unusable.

    Examples:
        >>> check_cancel({"id": 3}), check_cancel({"id": True})
        (3, None)
    """
    value = params.get("id")
    return value if _valid_id(value) else None


# --- OpenRPC --------------------------------------------------------------------------------

_STR = {"type": "string"}
_EMPTY = {"type": "object", "additionalProperties": False}


def _param(
    name: str, schema: dict[str, Any], *, required: bool = False, summary: str = ""
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "schema": schema, "required": required}
    if summary:
        entry["summary"] = summary
    return entry


def _method(
    name: str,
    summary: str,
    params: list[dict[str, Any]],
    result: dict[str, Any],
    errors: list[ErrorCode],
) -> dict[str, Any]:
    return {
        "name": name,
        "summary": summary,
        "paramStructure": "by-name",
        "params": params,
        "result": {"name": "result", "schema": result},
        "errors": [{"$ref": f"#/components/errors/{e.slug}"} for e in errors],
    }


def openrpc_document(
    findings_schema_url: str, model_schema_url: str, diff_schema_url: str
) -> dict[str, Any]:
    """The OpenRPC 1.3 description of protocol 1.4, committed as ``openrpc.json``.

    Notifications (``exit``, ``$/cancelRequest``, ``$/progress``) are described in
    ``x-notifications``, since OpenRPC has no notification object.

    Examples:
        >>> doc = openrpc_document(
        ...     "https://example.invalid/f.json",
        ...     "https://example.invalid/m.json",
        ...     "https://example.invalid/d.json",
        ... )
        >>> doc["info"]["version"], [m["name"] for m in doc["methods"]][:6]
        ('1.4', ['initialize', 'validate', 'read', 'convert', 'ags4.check', 'diff'])
    """
    rule = {"$ref": "#/components/schemas/RuleInfo"}
    common = [ErrorCode.INVALID_REQUEST, ErrorCode.NOT_INITIALIZED, ErrorCode.SHUTTING_DOWN]
    limits = {
        "type": "object",
        "properties": {
            k: {"type": "integer" if k != "time_budget_s" else "number"}
            for k in (
                "max_bytes",
                "max_path_bytes",
                "max_line_bytes",
                "max_findings",
                "max_response_bytes_estimate",
                "max_result_bytes",
                "time_budget_s",
            )
        },
    }
    document_errors = [
        ErrorCode.INVALID_PARAMS,
        ErrorCode.BUSY,
        ErrorCode.INPUT_TOO_LARGE,
        ErrorCode.COMPRESSED_INPUT,
        ErrorCode.PATH_NOT_PERMITTED,
        ErrorCode.INPUT_UNREADABLE,
        ErrorCode.RESOURCE_ERROR,
        ErrorCode.CANCELLED,
        ErrorCode.INTERNAL_ERROR,
    ]
    init_result = {
        "type": "object",
        "required": [
            "protocol_version",
            "pydiggs_version",
            "findings_schema_version",
            "findings_schema_url",
            "diggs_versions",
            "reference_data",
            "capabilities",
            "limits",
        ],
        "properties": {
            "protocol_version": _STR,
            "pydiggs_version": _STR,
            "findings_schema_version": _STR,
            "findings_schema_url": _STR,
            "model_schema_version": _STR,
            "model_schema_url": _STR,
            "diff_schema_version": _STR,
            "diff_schema_url": _STR,
            "tables_profile_version": _STR,
            "diggs_versions": {"type": "array", "items": _STR},
            "reference_data": {"type": "object", "additionalProperties": _STR},
            "capabilities": {"$ref": "#/components/schemas/ServerCapabilities"},
            "limits": limits,
        },
    }
    methods = [
        _method(
            "initialize",
            "Negotiate the protocol version, warm the validators and report limits.",
            [
                _param(
                    "protocol_version", {"type": "string", "pattern": r"^\d+\.\d+$"}, required=True
                ),
                _param(
                    "client_info",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string", "maxLength": MAX_CLIENT_NAME},
                            "version": {"type": "string", "maxLength": MAX_CLIENT_VERSION},
                        },
                    },
                ),
                _param(
                    "capabilities",
                    {"type": "object", "properties": {"progress": {"type": "boolean"}}},
                ),
                _param("diggs_versions", {"type": "array", "items": _STR, "minItems": 1}),
            ],
            init_result,
            [
                ErrorCode.INVALID_REQUEST,
                ErrorCode.INVALID_PARAMS,
                ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
                ErrorCode.RESOURCE_ERROR,
                ErrorCode.SHUTTING_DOWN,
            ],
        ),
        _method(
            "validate",
            "Validate one document, given as base64 content or (if enabled) an absolute path.",
            [
                _param("content_base64", {"type": "string", "contentEncoding": "base64"}),
                _param("path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                _param("name", {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS}),
                _param("checks", {"type": "array", "items": _STR, "minItems": 1}),
                _param("diggs_version", _STR),
                _param("time_budget_s", {"type": "number", "exclusiveMinimum": 0}),
            ],
            {"$ref": findings_schema_url},
            [*common, *document_errors],
        ),
        _method(
            "read",
            "Read one document into a diggs-model document (protocol 1.1). A schema-invalid"
            " file still reads; run validate for a verdict.",
            [
                _param("content_base64", {"type": "string", "contentEncoding": "base64"}),
                _param("path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                _param("name", {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS}),
                _param("diggs_version", _STR),
                _param("time_budget_s", {"type": "number", "exclusiveMinimum": 0}),
                _param(
                    "select",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "results": {
                                "oneOf": [
                                    {"enum": ["all", "none"]},
                                    {"type": "array", "items": _STR, "maxItems": 1000},
                                ]
                            }
                        },
                    },
                    summary=(
                        "Which result tables keep their values (default: all); the others"
                        " keep their columns and row_count."
                    ),
                ),
            ],
            {"$ref": model_schema_url},
            [
                *common,
                *document_errors,
                ErrorCode.INVALID_DOCUMENT,
                ErrorCode.RESULT_TOO_LARGE,
            ],
        ),
        _method(
            "convert",
            "Convert one AGS4 file to DIGGS 3.0.0 (to: diggs, the default), or one DIGGS"
            " document to an AGS4 file (to: ags4), with every loss reported (protocol 1.2)."
            " The result carries the output as base64, and for AGS4 to DIGGS the residual"
            " AGS4 file of what was not converted.",
            [
                _param("content_base64", {"type": "string", "contentEncoding": "base64"}),
                _param("path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                _param("name", {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS}),
                _param(
                    "to",
                    {"enum": list(CONVERT_TARGETS)},
                    summary=(
                        "The format to convert to: diggs (the default; the content is AGS4)"
                        " or ags4 (the content is DIGGS XML)."
                    ),
                ),
                _param(
                    "diggs_version",
                    {"enum": list(CONVERT_DIGGS_VERSIONS)},
                    summary="to: diggs only. The DIGGS version to write (default 3.0.0).",
                ),
                _param(
                    "ags_version",
                    {"enum": list(CONVERT_AGS_VERSIONS)},
                    summary=(
                        "to: ags4 only. The AGS4 version to write (default 4.2). The BGS AGS"
                        " validator and python-ags4 1.2.0 accept AGS4 only up to 4.1.1"
                        " (November 2026)."
                    ),
                ),
                _param("time_budget_s", {"type": "number", "exclusiveMinimum": 0}),
            ],
            {"$ref": "#/components/schemas/ConversionResult"},
            [
                *common,
                *document_errors,
                ErrorCode.INVALID_DOCUMENT,
                ErrorCode.RESULT_TOO_LARGE,
            ],
        ),
        _method(
            "ags4.check",
            "Check one AGS4 file against the AGS4 rules (Rules 1 to 20; protocol 1.2), with an"
            " optional data dictionary the host supplies.",
            [
                _param("content_base64", {"type": "string", "contentEncoding": "base64"}),
                _param("path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                _param("name", {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS}),
                _param("dictionary_base64", {"type": "string", "contentEncoding": "base64"}),
                _param("dictionary_path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                _param("time_budget_s", {"type": "number", "exclusiveMinimum": 0}),
            ],
            {"$ref": findings_schema_url},
            [*common, *document_errors],
        ),
        _method(
            "diff",
            "Compare two DIGGS files (protocol 1.3): what was added, removed or changed, as"
            " the diggs-diff 1.0 document pydiggs.diff.compare gives. Each file is given as"
            " base64 content or (if enabled) an absolute path.",
            [
                *(
                    param
                    for side in DIFF_SIDES
                    for param in (
                        _param(f"{side}_base64", {"type": "string", "contentEncoding": "base64"}),
                        _param(f"{side}_path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                        _param(
                            f"{side}_name", {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS}
                        ),
                    )
                ),
                _param(
                    "match",
                    {"enum": list(DIFF_MATCH), "default": DIFF_DEFAULTS["match"]},
                    summary=(
                        "How records are paired: auto (by gml:id, then by natural key), gml-id"
                        " (ids only; also written gml_id) or natural (natural keys only)."
                    ),
                ),
                _param(
                    "rel_tol",
                    {
                        "type": "number",
                        "minimum": 0,
                        "exclusiveMaximum": 1,
                        "default": DIFF_DEFAULTS["rel_tol"],
                    },
                ),
                _param(
                    "abs_tol",
                    {"type": "number", "minimum": 0, "default": DIFF_DEFAULTS["abs_tol"]},
                ),
                _param(
                    "max_changes",
                    {"type": "integer", "minimum": 0, "default": DIFF_DEFAULTS["max_changes"]},
                    summary="Most changes listed; the counts stay exact.",
                ),
                _param(
                    "show_reformatted",
                    {"type": "boolean", "default": DIFF_DEFAULTS["show_reformatted"]},
                ),
                _param(
                    "show_renamed", {"type": "boolean", "default": DIFF_DEFAULTS["show_renamed"]}
                ),
                _param("time_budget_s", {"type": "number", "exclusiveMinimum": 0}),
            ],
            {"$ref": diff_schema_url},
            [
                *common,
                *document_errors,
                ErrorCode.INVALID_DOCUMENT,
                ErrorCode.RESULT_TOO_LARGE,
            ],
        ),
        *_tables_methods(findings_schema_url, common, document_errors),
        _method("ping", "Liveness check.", [], _EMPTY, common),
        _method(
            "versions",
            "Package, schema and reference-data versions.",
            [],
            {"type": "object"},
            common,
        ),
        _method(
            "rules.list",
            "The rule catalogue, optionally for one step.",
            [_param("step", _STR)],
            {"type": "array", "items": rule},
            [*common, ErrorCode.INVALID_PARAMS],
        ),
        _method(
            "rules.explain",
            "Describe one rule id.",
            [_param("rule_id", {"type": "string", "maxLength": MAX_RULE_ID_CHARS}, required=True)],
            rule,
            [*common, ErrorCode.INVALID_PARAMS],
        ),
        _method(
            "shutdown",
            "Finish the running validation, refuse new work, then answer null.",
            [],
            {"type": "null"},
            common,
        ),
    ]
    return {
        "openrpc": "1.3.2",
        "info": {
            "title": "pydiggs stdio sidecar",
            "version": PROTOCOL_VERSION,
            "description": (
                "JSON-RPC 2.0 over NDJSON on stdin/stdout (pydiggs serve --stdio)."
                " The -32000..-32099 error band is pydiggs-defined; switch on error.data.code."
            ),
        },
        "methods": methods,
        "components": {
            "errors": {
                e.slug: {"code": e.code, "message": _MESSAGES[e], "data": {"code": e.slug}}
                for e in ErrorCode
            },
            "schemas": {
                "RuleInfo": {
                    "type": "object",
                    "required": ["id", "step", "description", "known"],
                    "properties": {
                        "id": _STR,
                        "pattern": {"type": ["string", "null"]},
                        "step": _STR,
                        "description": _STR,
                        "severities": {"type": "array", "items": _STR},
                        "help_uri": {"type": ["string", "null"]},
                        "known": {"type": "boolean"},
                        "guidance": {
                            "type": ["object", "null"],
                            "required": ["title", "meaning", "fix", "locale"],
                            "properties": {
                                "title": _STR,
                                "meaning": _STR,
                                "fix": _STR,
                                "locale": _STR,
                            },
                        },
                    },
                },
                "ConversionResult": {
                    "type": "object",
                    "description": (
                        "As POST /v1/convert answers: the output (base64) with its media type"
                        " and sha256, the findings about the source and the output, and for"
                        " AGS4 to DIGGS the residual AGS4 file (null when nothing was left"
                        " over, and for DIGGS to AGS4)."
                    ),
                    "required": [
                        "to",
                        "media_type",
                        "output_name",
                        "diggs_version",
                        "ags_version",
                        "mapping_version",
                        "complete",
                        "lossless",
                        "report",
                        "validation",
                        "output_sha256",
                        "output_base64",
                        "residual_base64",
                    ],
                    "properties": {
                        "to": {"enum": list(CONVERT_TARGETS)},
                        "media_type": _STR,
                        "output_name": _STR,
                        "diggs_version": {"type": ["string", "null"]},
                        "ags_version": {
                            "anyOf": [{"enum": list(CONVERT_AGS_VERSIONS)}, {"type": "null"}]
                        },
                        "mapping_version": _STR,
                        "complete": {"type": "boolean"},
                        "lossless": {"type": "boolean"},
                        "report": {"$ref": findings_schema_url},
                        "validation": {"anyOf": [{"$ref": findings_schema_url}, {"type": "null"}]},
                        "output_sha256": _STR,
                        "output_base64": {"type": "string", "contentEncoding": "base64"},
                        "residual_base64": {
                            "type": ["string", "null"],
                            "contentEncoding": "base64",
                        },
                    },
                },
                **_tables_schemas(findings_schema_url),
                "ServerCapabilities": {
                    "type": "object",
                    "properties": {
                        "validate": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "boolean"},
                                "path": {"type": "boolean"},
                            },
                        },
                        "read": {
                            "type": "object",
                            "description": "Protocol 1.1 and later.",
                            "properties": {
                                "content": {"type": "boolean"},
                                "path": {"type": "boolean"},
                                "select": {"type": "boolean"},
                            },
                        },
                        "convert": {
                            "type": "object",
                            "description": "Protocol 1.2 and later.",
                            "properties": {
                                "content": {"type": "boolean"},
                                "path": {"type": "boolean"},
                                "to": {"type": "array", "items": _STR},
                                "ags_versions": {"type": "array", "items": _STR},
                                "residual": {"type": "boolean"},
                            },
                        },
                        "ags4_check": {
                            "type": "object",
                            "description": "Protocol 1.2 and later (the ags4.check method).",
                            "properties": {
                                "content": {"type": "boolean"},
                                "path": {"type": "boolean"},
                                "dictionary": {"type": "boolean"},
                            },
                        },
                        "diff": {
                            "type": "object",
                            "description": "Protocol 1.3 and later.",
                            "properties": {
                                "content": {"type": "boolean"},
                                "path": {"type": "boolean"},
                                "match": {"type": "array", "items": _STR},
                            },
                        },
                        "tables": {
                            "type": "object",
                            "description": "Protocol 1.4+ (the tables.* methods, a preview).",
                            "properties": {
                                "content": {"type": "boolean"},
                                "path": {"type": "boolean"},
                                "methods": {"type": "array", "items": _STR},
                                "profile_version": _STR,
                            },
                        },
                        "cancel": {"type": "boolean"},
                        "progress": {"type": "boolean"},
                        "rules": {"type": "boolean"},
                        "ping": {"type": "boolean"},
                    },
                },
            },
        },
        "x-notifications": [
            {"name": "exit", "direction": "host-to-server", "params": []},
            {
                "name": "$/cancelRequest",
                "direction": "host-to-server",
                "params": [_param("id", {"type": ["string", "integer"]}, required=True)],
            },
            {
                "name": "$/progress",
                "direction": "server-to-host",
                "params": [
                    _param("request_id", {"type": ["string", "integer"]}, required=True),
                    _param("step", _STR, required=True),
                    _param("status", _STR, required=True),
                ],
            },
        ],
    }


def _tables_methods(
    findings_schema_url: str, common: list[ErrorCode], document_errors: list[ErrorCode]
) -> list[dict[str, Any]]:
    """The protocol 1.4 ``tables.*`` methods, for :func:`openrpc_document`."""
    package = [
        _param(
            "package_base64",
            {"type": "string", "contentEncoding": "base64", "contentMediaType": "application/zip"},
            summary=(
                "The package: a ZIP of the CSV files and datapackage.json, standard base64."
                " Flat: the files at the top (one wrapping folder is allowed)."
            ),
        ),
        _param(
            "path",
            {"type": "string", "maxLength": MAX_PATH_CHARS},
            summary=(
                "Instead: an absolute folder under an --allow-path root; its CSV and JSON"
                " files are read, each opened and verified as path is for validate."
            ),
        ),
        _param(
            "name",
            {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS},
            summary="The name to report (default tables.zip, or the folder's name).",
        ),
    ]
    errors = [*common, *document_errors, ErrorCode.INVALID_DOCUMENT, ErrorCode.RESULT_TOO_LARGE]
    return [
        _method(
            "tables.to_diggs",
            "Write a package of DIGGS tables profile tables as a DIGGS 3.0.0 file (protocol"
            " 1.4, preview), with every row or value that could not be carried reported.",
            package,
            {"$ref": "#/components/schemas/TablesToDiggsResult"},
            errors,
        ),
        _method(
            "tables.check",
            "Check a package of profile tables against the DIGGS dictionaries without"
            " writing a file (protocol 1.4, preview): pydiggs.tables.check.",
            package,
            {"$ref": findings_schema_url},
            errors,
        ),
        _method(
            "tables.round_trip",
            "Write a package of profile tables as DIGGS, validate it, read it back and"
            " compare (protocol 1.4, preview): pydiggs.tables.round_trip.",
            package,
            {"$ref": "#/components/schemas/TablesRoundTripResult"},
            errors,
        ),
        _method(
            "tables.from_diggs",
            "Turn one DIGGS document into tables of the profile (protocol 1.4, preview):"
            " a ZIP of a CSV file per table, datapackage.json and plan.json, as base64.",
            [
                _param("content_base64", {"type": "string", "contentEncoding": "base64"}),
                _param("path", {"type": "string", "maxLength": MAX_PATH_CHARS}),
                _param("name", {"type": "string", "maxLength": MAX_NAME_INPUT_CHARS}),
                _param("time_budget_s", {"type": "number", "exclusiveMinimum": 0}),
            ],
            {"$ref": "#/components/schemas/TablesFromDiggsResult"},
            errors,
        ),
    ]


def _tables_schemas(findings_schema_url: str) -> dict[str, Any]:
    """The result schemas of the ``tables.*`` methods, for :func:`openrpc_document`."""
    findings = {"$ref": findings_schema_url}
    output = {
        "output_sha256": _STR,
        "output_base64": {"type": "string", "contentEncoding": "base64"},
    }
    return {
        "TablesToDiggsResult": {
            "type": "object",
            "description": "As POST /v1/tables/to-diggs answers: the DIGGS file as base64.",
            "required": [
                "profile_version",
                "diggs_version",
                "media_type",
                "output_name",
                "lossless",
                "counts",
                "report",
                "output_sha256",
                "output_base64",
            ],
            "properties": {
                "profile_version": _STR,
                "diggs_version": _STR,
                "media_type": _STR,
                "output_name": _STR,
                "lossless": {"type": "boolean"},
                "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
                "report": findings,
                **output,
            },
        },
        "TablesRoundTripResult": {
            "type": "object",
            "description": "As POST /v1/tables/round-trip answers.",
            "required": [
                "profile_version",
                "ok",
                "report",
                "validation",
                "differences",
                "output_sha256",
                "output_base64",
            ],
            "properties": {
                "profile_version": _STR,
                "ok": {"type": "boolean"},
                "report": findings,
                "validation": findings,
                "differences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["table", "row", "column", "sent", "got"],
                        "properties": {"table": _STR, "row": _STR, "column": _STR},
                    },
                },
                **output,
            },
        },
        "TablesFromDiggsResult": {
            "type": "object",
            "description": (
                "As POST /v1/tables/from-diggs answers: the package as a ZIP, in base64."
            ),
            "required": [
                "profile_version",
                "diggs_version",
                "complete",
                "lossless",
                "plan",
                "report",
                "media_type",
                "output_name",
                "output_sha256",
                "output_base64",
            ],
            "properties": {
                "profile_version": _STR,
                "diggs_version": {"type": ["string", "null"]},
                "complete": {"type": "boolean"},
                "lossless": {"type": "boolean"},
                "plan": {"type": "object"},
                "report": findings,
                "media_type": {"const": "application/zip"},
                "output_name": _STR,
                **output,
            },
        },
    }
