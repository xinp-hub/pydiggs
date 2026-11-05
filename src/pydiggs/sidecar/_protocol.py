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

from pydiggs._service import MAX_NAME_INPUT_CHARS, Code

__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_VERSIONS",
    "ErrorCode",
    "Invalid",
    "Message",
    "ProtocolError",
    "check_cancel",
    "check_empty",
    "check_explain",
    "check_initialize",
    "check_rules_list",
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
PROTOCOL_VERSION: Final = "1.0"
#: Every protocol version this build can answer, oldest first.
SUPPORTED_VERSIONS: Final = ("1.0",)

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
        >>> negotiate("1.0"), negotiate("1.7")
        ('1.0', '1.0')
        >>> try:
        ...     negotiate("2.0")
        ... except ProtocolError as error:
        ...     print(error.data())
        {'code': 'unsupported-protocol-version', 'supported': ['1.0']}
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
    if ("content_base64" in params) == ("path" in params):
        raise _bad("content_base64", "give exactly one of content_base64 and path")
    name = _optional_str(params, "name", MAX_NAME_INPUT_CHARS)
    checks = params.get("checks")
    if "checks" in params and (
        not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)
    ):
        raise _bad("checks", "must be an array of strings")
    version = _optional_str(params, "diggs_version", 64)
    budget: float = time_limit
    if "time_budget_s" in params:
        raw = params["time_budget_s"]
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int | float)
            or not 0 < raw <= time_limit  # before isfinite: a huge int would overflow
            or not math.isfinite(raw)
        ):
            raise _bad("time_budget_s", f"must be a number of seconds in (0, {time_limit:g}]")
        budget = raw
    content = path = None
    if "path" in params:
        path = params["path"]
        if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
            raise _bad("path", f"must be a string of at most {MAX_PATH_CHARS} characters")
    else:
        encoded = params["content_base64"]
        if not isinstance(encoded, str):
            raise _bad("content_base64", "must be a base64 string")
        if len(encoded) > _encoded_limit(max_bytes):
            raise ProtocolError(ErrorCode.INPUT_TOO_LARGE, extra={"max_bytes": max_bytes})
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error):
            raise _bad("content_base64", "must be standard, padded base64") from None
        if len(content) > max_bytes:
            raise ProtocolError(ErrorCode.INPUT_TOO_LARGE, extra={"max_bytes": max_bytes})
    return ValidateParams(content, path, name, checks, version, budget)


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


def openrpc_document(findings_schema_url: str) -> dict[str, Any]:
    """The OpenRPC 1.3 description of protocol 1.0, committed as ``openrpc.json``.

    Notifications (``exit``, ``$/cancelRequest``, ``$/progress``) are described in
    ``x-notifications``, since OpenRPC has no notification object.

    Examples:
        >>> doc = openrpc_document("https://example.invalid/s.json")
        >>> doc["info"]["version"], [m["name"] for m in doc["methods"]][:2]
        ('1.0', ['initialize', 'validate'])
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
                "time_budget_s",
            )
        },
    }
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
            [
                *common,
                ErrorCode.INVALID_PARAMS,
                ErrorCode.BUSY,
                ErrorCode.INPUT_TOO_LARGE,
                ErrorCode.COMPRESSED_INPUT,
                ErrorCode.PATH_NOT_PERMITTED,
                ErrorCode.INPUT_UNREADABLE,
                ErrorCode.RESOURCE_ERROR,
                ErrorCode.CANCELLED,
                ErrorCode.INTERNAL_ERROR,
            ],
        ),
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
                    },
                },
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
