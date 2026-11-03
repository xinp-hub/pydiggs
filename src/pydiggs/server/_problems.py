"""RFC 9457 problem details: every HTTP error the server sends, and how codes map to them.

All mapping from :class:`pydiggs._service.Code` to an HTTP status lives here. The
bodies carry fixed texts only: never a value the client sent, never a path, never an
exception message (those go to the log, tied to the answer by ``correlation_id``).
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Mapping
from typing import Final

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from pydiggs._service import Code, Outcome

__all__ = [
    "MAX_ERRORS",
    "PROBLEMS",
    "PROBLEM_MEDIA_TYPE",
    "SECURITY_HEADERS",
    "STATUS_FOR_CODE",
    "from_outcome",
    "http_exception_handler",
    "internal_error",
    "problem",
    "type_uri",
    "unhandled_exception_handler",
]

#: The media type of every error body.
PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
#: Where each ``type`` URI points: one anchor per code in the HTTP API page.
_TYPE_BASE: Final = "https://xinp-hub.github.io/pydiggs/http-api/#problem-"
#: Most ``errors[]`` entries in one problem.
MAX_ERRORS: Final = 20
#: Sent with every response the router or app produces.
SECURITY_HEADERS: Final[Mapping[str, str]] = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

#: ``code`` -> ``(status, title)`` for every problem the server can send.
PROBLEMS: Final[Mapping[str, tuple[int, str]]] = {
    "missing-input": (400, "Missing input"),
    "invalid-multipart": (400, "Invalid multipart body"),
    "invalid-host": (400, "Invalid Host header"),
    "cross-site": (403, "Cross-site request refused"),
    "not-found": (404, "Not found"),
    "method-not-allowed": (405, "Method not allowed"),
    "not-acceptable": (406, "Not acceptable"),
    "input-too-large": (413, "Input too large"),
    "compressed-input": (415, "Compressed input refused"),
    "unsupported-media-type": (415, "Unsupported media type"),
    "input-unreadable": (422, "Input unreadable"),
    "invalid-parameter": (422, "Invalid parameter"),
    "resource-error": (500, "Bundled reference data unavailable"),
    "internal": (500, "Internal error"),
    "busy": (503, "Server busy"),
    "not-ready": (503, "Not ready"),
    "timeout": (504, "Validation timed out"),
}

#: HTTP status for each :class:`Code` a validation may answer with.
STATUS_FOR_CODE: Final[Mapping[Code, int]] = {
    Code.INPUT_TOO_LARGE: 413,
    Code.COMPRESSED_INPUT: 415,
    Code.INPUT_UNREADABLE: 422,
    Code.INVALID_PARAMETER: 422,
    Code.RESOURCE_ERROR: 500,
    Code.INTERNAL: 500,
    Code.CANCELLED: 500,  # the HTTP adapter never cancels; fail closed if it ever happens
    Code.TIMEOUT: 504,
}

_HTTP_CODES: Final = {404: "not-found", 405: "method-not-allowed"}
_GENERIC_INTERNAL: Final = "An internal error occurred."

_log = logging.getLogger("pydiggs.server")


def type_uri(code: str) -> str:
    """The ``type`` URI of the problem ``code``: its anchor in the HTTP API page.

    Example:
        >>> type_uri("busy")
        'https://xinp-hub.github.io/pydiggs/http-api/#problem-busy'
    """
    return _TYPE_BASE + code


def problem(
    code: str,
    detail: str,
    *,
    errors: tuple[tuple[str, str], ...] = (),
    correlation_id: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """An ``application/problem+json`` response for the problem ``code``.

    Args:
        code: A key of :data:`PROBLEMS`; it sets the status and title.
        detail: A fixed, safe text (never a client value or a path).
        errors: ``(parameter, problem)`` pairs; at most :data:`MAX_ERRORS` are sent.
        correlation_id: Ties a 500 answer to the log line with the real text.
        headers: Extra headers (``Retry-After``, ``Allow``).

    Returns:
        The response, with ``Cache-Control: no-store`` and ``nosniff``.

    Example:
        >>> response = problem("busy", "Try again shortly.", headers={"Retry-After": "1"})
        >>> response.status_code, response.media_type, response.headers["retry-after"]
        (503, 'application/problem+json', '1')
    """
    status, title = PROBLEMS[code]
    body: dict[str, object] = {
        "type": type_uri(code),
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    if errors:
        body["errors"] = [{"parameter": p, "problem": t} for p, t in errors[:MAX_ERRORS]]
    if correlation_id is not None:
        body["correlation_id"] = correlation_id
    return Response(
        json.dumps(body, ensure_ascii=True).encode("ascii"),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={**SECURITY_HEADERS, **(headers or {})},
    )


def from_outcome(outcome: Outcome) -> Response:
    """The problem response for a validation that produced no document.

    Example:
        >>> from pydiggs._service import Code, Outcome
        >>> from_outcome(Outcome(code=Code.TIMEOUT, detail="slow")).status_code
        504
    """
    code = outcome.code if outcome.code is not None else Code.INTERNAL
    status = STATUS_FOR_CODE[code]
    name = "internal" if code is Code.CANCELLED else code.value
    return problem(
        name,
        outcome.detail or _GENERIC_INTERNAL,
        errors=outcome.errors,
        correlation_id=outcome.correlation_id,
        headers={"Connection": "close"} if status == 413 else None,  # noqa: PLR2004
    )


def internal_error(exc: BaseException) -> Response:
    """A 500 ``internal`` problem; the real text goes to the ``pydiggs.server`` log only.

    Example:
        >>> internal_error(RuntimeError("/secret/path")).body.find(b"secret")
        -1
    """
    correlation_id = secrets.token_hex(8)
    _log.error("internal [correlation_id=%s]: %s: %s", correlation_id, type(exc).__name__, exc)
    return problem("internal", _GENERIC_INTERNAL, correlation_id=correlation_id)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """App-level handler: Starlette's 404 and 405 (and any other HTTP error) as problems."""
    del request
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered for it
        return internal_error(exc)
    code = _HTTP_CODES.get(exc.status_code)
    if code is None:
        return internal_error(exc)
    headers = {"Allow": exc.headers["Allow"]} if exc.headers and "Allow" in exc.headers else None
    return problem(code, PROBLEMS[code][1] + ".", headers=headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """App-level last resort: a fixed 500 problem with a correlation id."""
    del request
    return internal_error(exc)
