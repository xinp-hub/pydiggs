"""The router, its admission and body limits, and the app factory.

Every limit that protects the server is enforced *inside the routes*, so it holds
when a host app mounts only :func:`create_router`: cross-site refusal, content
negotiation, the media-type and ``Content-Encoding`` checks, ``Content-Length``,
a non-blocking admission slot taken before the body is read, a streamed body read
with a running byte count and a deadline (``body_timeout_s``), and bounded
multipart parsing. :func:`create_app` adds the app-level layers on top: a ``Host``
check, CORS for listed origins only, problem responses for unknown paths, and
``/livez`` and ``/readyz``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import json
import logging
import math
import re
import threading
import warnings
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias
from urllib.parse import urlsplit

import anyio
import anyio.to_thread
from anyio.lowlevel import RunVar
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from pydiggs import _service
from pydiggs._service import (
    Ags4CheckRequest,
    ConvertRequest,
    DiffRequest,
    Outcome,
    ReadRequest,
    TablesFromDiggsRequest,
    TablesRequest,
)
from pydiggs._service import Request as ValidationRequest
from pydiggs.server import _schemas
from pydiggs.server._problems import (
    SECURITY_HEADERS,
    from_outcome,
    http_exception_handler,
    internal_error,
    problem,
    unhandled_exception_handler,
)
from pydiggs.server._settings import LOOPBACK_HOSTS, ServerSettings

if TYPE_CHECKING:
    from pydiggs._pool import WorkerPool

__all__ = ["create_app", "create_router", "openapi_text"]

#: Multipart limits: one file (two for an AGS4 check, the file and a dictionary, and for
#: a comparison, the old and the new file), a few short fields.
MAX_FILES: Final = 1
MAX_FIELDS: Final = 8
MAX_PART_SIZE: Final = 64 * 2**10
#: Multipart framing and fields allowed on top of ``max_body_bytes``.
MULTIPART_SLACK: Final = MAX_FIELDS * MAX_PART_SIZE + 64 * 2**10
#: ``/v1/read`` sends a document of at most this many times ``max_body_bytes`` (a real
#: model is 0.6 to 2 times its XML); a larger one is 422 ``result-too-large``.
RESULT_FACTOR: Final = 4
#: Most ``Accept`` items considered (the rest are ignored).
_MAX_ACCEPT_ITEMS: Final = 32
_RETRY_AFTER: Final = {"Retry-After": "1"}
_FORM_FIELDS: Final = frozenset({"name", "checks", "diggs_version"})
_READ_FIELDS: Final = frozenset({"name", "diggs_version", "select"})
_CONVERT_FIELDS: Final = frozenset({"name", "to", "diggs_version", "ags_version"})
_CHECK_FIELDS: Final = frozenset({"name"})
_RULES_FIELDS: Final = frozenset({"step"})
_TABLES_FIELDS: Final = frozenset({"name"})
_DIFF_FIELDS: Final = frozenset(
    {
        "old_name",
        "new_name",
        "match",
        "rel_tol",
        "abs_tol",
        "max_changes",
        "show_reformatted",
        "show_renamed",
    }
)
#: The file parts a comparison takes, both required, each within ``max_body_bytes``.
_DIFF_FILES: Final = _service.DIFF_SIDES
#: A number as JSON writes it (``float()`` alone would take ``nan``, ``inf`` and ``1_0``).
_NUMBER: Final = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
#: An integer of at most 18 digits (``int()`` alone would take ``1_0`` and spaces).
_INTEGER: Final = re.compile(r"[0-9]{1,18}")
_MAX_NUMBER_CHARS: Final = 64
#: The file parts an AGS4 check takes: the file and, optionally, a data dictionary.
_CHECK_FILES: Final = ("file", "dictionary")

Format = Literal["findings", "sarif"]
_MEDIA: Final[dict[Format, str]] = {
    "findings": _schemas.FINDINGS_MEDIA_TYPE,
    "sarif": _schemas.SARIF_MEDIA_TYPE,
}

_log = logging.getLogger("pydiggs.server")


class _TooLargeError(Exception):
    """The body passed its limit while it was being read."""


# --- request inspection ------------------------------------------------------------------


_CANDIDATES: Final[tuple[tuple[Format, str], ...]] = (
    ("findings", _schemas.FINDINGS_MEDIA_TYPE),
    ("sarif", _schemas.SARIF_MEDIA_TYPE),
)
_SPECIFICITY: Final = {" exact": 2, "application/*": 1, "*/*": 0}


def _quality(params: str) -> float:
    """The ``q`` of one ``Accept`` item: 1 when absent, 0 when malformed, clamped to 0..1."""
    for param in params.split(";"):
        key, _, value = param.partition("=")
        if key.strip().lower() == "q":
            try:
                quality = float(value.strip())
            except ValueError:
                return 0.0
            return min(1.0, max(0.0, quality)) if math.isfinite(quality) else 0.0
    return 1.0


def negotiate(accept: str | None) -> Format | None:
    """Choose ``findings`` (``application/json``) or ``sarif`` from an ``Accept`` header.

    The most specific matching range sets each candidate's quality; the higher quality
    wins, then the more specific match, then ``findings``. None means neither is
    acceptable (406).

    Examples:
        >>> negotiate(None), negotiate("application/sarif+json"), negotiate("*/*")
        ('findings', 'sarif', 'findings')
        >>> negotiate("application/xml") is None
        True
    """
    if accept is None or not accept.strip():
        return "findings"
    (spec_f, q_f), (spec_s, q_s) = _best(accept)
    if q_f <= 0 and q_s <= 0:
        return None
    if q_s > q_f or (q_s == q_f and spec_s > spec_f):
        return "sarif"
    return "findings"


def accepts_json(accept: str | None) -> bool:
    """Whether an ``Accept`` header allows ``application/json`` (all ``/v1/read`` sends).

    Examples:
        >>> accepts_json(None), accepts_json("*/*"), accepts_json("application/sarif+json")
        (True, True, False)
    """
    if accept is None or not accept.strip():
        return True
    return _best(accept)[0][1] > 0


def _best(accept: str) -> tuple[tuple[int, float], tuple[int, float]]:
    """``(specificity, quality)`` of the best range for JSON, and for SARIF."""
    best: dict[Format, tuple[int, float]] = {"findings": (-1, 0.0), "sarif": (-1, 0.0)}
    for item in accept.split(",")[:_MAX_ACCEPT_ITEMS]:
        media, _, params = item.partition(";")
        media = media.strip().lower()
        quality = _quality(params)
        for fmt, exact in _CANDIDATES:
            specificity = _SPECIFICITY.get(media if media != exact else " exact", -1)
            if specificity > best[fmt][0]:
                best[fmt] = (specificity, quality)
    return best["findings"], best["sarif"]


def _media_type(request: Request) -> str:
    return request.headers.get("content-type", "").partition(";")[0].strip().lower()


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def is_cross_site(headers: Headers, settings: ServerSettings) -> bool:
    """True when the request must be refused as cross-site.

    A listed ``cors_origins`` origin is always allowed. Otherwise a request is
    cross-site when ``Sec-Fetch-Site`` says so, or when it carries an ``Origin`` that
    is not the server's own (its host differs from ``Host``; ``null`` never matches).

    Examples:
        >>> from starlette.datastructures import Headers
        >>> s = ServerSettings()
        >>> is_cross_site(Headers({"origin": "https://evil.example", "host": "a"}), s)
        True
        >>> is_cross_site(Headers({"origin": "http://a:8000", "host": "a:8000"}), s)
        False
    """
    if not settings.reject_cross_site:
        return False
    origin = headers.get("origin")
    if origin is not None and origin in settings.cors_origins:
        return False
    if headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return True
    if origin is None:
        return False
    host = headers.get("host", "").strip().lower()
    try:
        netloc = urlsplit(origin.strip()).netloc.lower()
    except ValueError:
        return True
    return not host or netloc != host


# --- body reading ------------------------------------------------------------------------


async def _counted(request: Request, limit: int) -> AsyncGenerator[bytes, None]:
    """``request.stream()`` with a running byte count; past ``limit`` it stops reading."""
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _TooLargeError
        yield chunk


async def _read_raw(request: Request, limit: int) -> bytes:
    chunks = [chunk async for chunk in _counted(request, limit)]
    return b"".join(chunks)


@dataclasses.dataclass(slots=True)
class _Upload:
    content: bytes
    filename: str | None
    fields: dict[str, list[str]]
    #: Other file parts (an AGS4 check's ``dictionary``), by part name.
    attachments: dict[str, bytes] = dataclasses.field(default_factory=dict)


async def _read_multipart(
    request: Request,
    limit: int,
    file_limit: int,
    allowed: frozenset[str],
    files: Sequence[str] = ("file",),
) -> _Upload | Response:
    """Parse the ``file`` part, other ``files`` parts and a few short fields (``allowed``).

    At most :data:`MAX_FIELDS` fields; exactly one ``file`` part, and each other part
    in ``files`` at most once (optional). Anything else is ``invalid-multipart``.
    The whole body counts against ``limit`` while it streams; each file part against
    ``file_limit``. Spooled parts are closed (and so deleted) before returning.
    """
    parser = MultiPartParser(
        request.headers,
        _counted(request, limit),
        max_files=len(files),
        max_fields=MAX_FIELDS,
        max_part_size=MAX_PART_SIZE,
    )
    try:
        form = await parser.parse()
    except MultiPartException:
        return problem("invalid-multipart", "The multipart body could not be parsed.")
    try:
        upload = _file_part(form)
        if isinstance(upload, Response):
            return upload
        content = await upload.read(file_limit + 1)
        if len(content) > file_limit:
            return _too_large()
        attachments: dict[str, bytes] = {}
        fields: dict[str, list[str]] = {}
        for key, value in form.multi_items():
            if key == "file":
                continue
            if key in files and isinstance(value, UploadFile) and key not in attachments:
                attachments[key] = await value.read(file_limit + 1)
                if len(attachments[key]) > file_limit:
                    return _too_large()
                continue
            if key not in allowed or not isinstance(value, str):
                names = ", ".join(sorted({*files, *allowed}))
                return problem("invalid-multipart", f"Only the fields {names} are accepted.")
            fields.setdefault(key, []).append(value)
        return _Upload(
            content=content, filename=upload.filename, fields=fields, attachments=attachments
        )
    finally:
        await form.close()


def _file_part(form: FormData) -> UploadFile | Response:
    """The one ``file`` part: a second one is refused, not "the last one wins"."""
    parts = form.getlist("file")
    if len(parts) > 1:
        return problem("invalid-multipart", "Send exactly one part named 'file'.")
    if not parts or not isinstance(parts[0], UploadFile):
        return problem("missing-input", "Send the document in a multipart part named 'file'.")
    return parts[0]


def _too_large() -> Response:
    return problem(
        "input-too-large",
        "The body is larger than this server accepts.",
        headers={"Connection": "close"},
    )


def _checks(values: Sequence[str]) -> list[str] | None:
    """``checks`` given as repeated and/or comma-separated values, each step once.

    Each value must match the documented ``^step(,step)*$`` of at most
    :data:`~pydiggs.server._schemas.MAX_CHECKS_CHARS` characters: no blanks, no empty
    items. A repeated step runs once. None when absent; an empty list (refused as
    ``invalid-parameter``) when a value is too long; an unknown, blank or empty item is
    kept, so it is refused as ``invalid-parameter`` too.
    """
    if not values:
        return None
    if any(len(value) > _schemas.MAX_CHECKS_CHARS for value in values):
        return []
    return list(dict.fromkeys(part for value in values for part in value.split(",")))


def _precheck(
    request: Request,
    settings: ServerSettings,
    *,
    sarif: bool = True,
    json_text: str = "Accept application/json (diggs-model).",
    raw_media: Sequence[str] = _schemas.XML_MEDIA_TYPES,
    files: int = 1,
    allowed: frozenset[str] = frozenset(),
) -> tuple[Format, bool] | Response:
    """Checks that need only the headers: Accept, media type, encoding, Content-Length.

    Returns the chosen format and whether the body is multipart, or the refusal.
    ``sarif=False`` is for an endpoint that only sends ``application/json``
    (``json_text`` is its refusal); ``raw_media`` are the media types of a raw body
    (none: multipart only). ``files`` is how many files of up to ``max_body_bytes``
    each the body may carry. ``allowed`` are the query parameters the endpoint takes;
    any other is refused (see :func:`_unknown_query`), after ``Accept`` and before the
    media type, as ``@pydiggs/client``'s handler does.
    """
    fmt = _negotiated(request, sarif=sarif, json_text=json_text)
    if isinstance(fmt, Response):
        return fmt
    unknown = _unknown_query(request, settings, allowed)
    if unknown is not None:
        return unknown
    media = _media_type(request)
    multipart = media == "multipart/form-data"
    if not multipart and media not in raw_media:
        accepted = [*raw_media, "multipart/form-data"]
        text = " or ".join(filter(None, (", ".join(accepted[:-1]), accepted[-1])))
        return problem("unsupported-media-type", f"Send {text}.")
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        return problem("compressed-input", "Compressed request bodies are not accepted.")
    length = _content_length(request)
    if length is not None and length > _body_limit(settings, multipart=multipart, files=files):
        return _too_large()
    return fmt, multipart


def _negotiated(request: Request, *, sarif: bool, json_text: str) -> Format | Response:
    """The format ``Accept`` asks for, or ``406 not-acceptable``."""
    accept = request.headers.get("accept")
    if not sarif:
        return "findings" if accepts_json(accept) else problem("not-acceptable", json_text)
    fmt = negotiate(accept)
    if fmt is None:
        return problem(
            "not-acceptable",
            "Accept application/json (diggs-findings) or application/sarif+json.",
        )
    return fmt


def _unknown_query(
    request: Request, settings: ServerSettings, allowed: frozenset[str]
) -> Response | None:
    """``422 invalid-parameter`` for query parameters the endpoint does not take.

    Each name once, in the order of the URL, at most :data:`~._problems.MAX_ERRORS`;
    a name given with no value (``?foo``) or an empty name (``?=x``) counts. Values
    are never echoed. With ``unknown_query="ignore"`` (deprecated) they are logged
    and ignored, as in 1.13 and earlier. Multipart fields are not query parameters:
    an unknown field stays ``400 invalid-multipart``.
    """
    unknown = [name for name in dict.fromkeys(request.query_params.keys()) if name not in allowed]
    if not unknown:
        return None
    if settings.unknown_query == "ignore":
        _log.warning(
            "ignored %d unknown query parameter(s) on %s (unknown_query='ignore' is deprecated)",
            len(unknown),
            # The route's template, never the URL: a mount prefix may carry client text.
            getattr(request.scope.get("route"), "path", "?"),
        )
        return None
    return problem(
        "invalid-parameter",
        "invalid parameters",
        errors=tuple((name, "unknown parameter") for name in unknown),
    )


def _body_limit(settings: ServerSettings, *, multipart: bool, files: int = 1) -> int:
    return files * settings.max_body_bytes + (MULTIPART_SLACK if multipart else 0)


def _parameters(
    content: bytes, params: dict[str, list[str]], filename: str | None, fmt: Format
) -> ValidationRequest:
    names, versions = params.get("name", []), params.get("diggs_version", [])
    return ValidationRequest(
        content=content,
        name=names[-1] if names else filename,
        checks=_checks(params.get("checks", [])),
        diggs_version=versions[-1] if versions else None,
        format=fmt,
    )


def _select(values: Sequence[str]) -> object:
    """The ``select`` parameter: JSON text, parsed. An unusable value stays text.

    Text is not a selection, so the service refuses it as ``invalid-parameter``.
    """
    if not values:
        return None
    text = values[-1]
    if len(text) > _schemas.MAX_SELECT_CHARS:
        return text[:1]
    try:
        parsed: object = json.loads(text)
    except (ValueError, RecursionError):
        return text[:1]
    return text[:1] if parsed is None else parsed


def _read_parameters(
    content: bytes, params: dict[str, list[str]], filename: str | None
) -> ReadRequest:
    names, versions = params.get("name", []), params.get("diggs_version", [])
    return ReadRequest(
        content=content,
        name=names[-1] if names else filename,
        diggs_version=versions[-1] if versions else None,
        select=_select(params.get("select", [])),
    )


def _convert_parameters(
    content: bytes, params: dict[str, list[str]], filename: str | None
) -> ConvertRequest:
    names, targets, versions, ags_versions = (
        params.get(k, []) for k in ("name", "to", "diggs_version", "ags_version")
    )
    return ConvertRequest(
        content=content,
        name=names[-1] if names else filename,
        to=targets[-1] if targets else _service.CONVERT_TARGETS[0],
        diggs_version=versions[-1] if versions else None,
        ags_version=ags_versions[-1] if ags_versions else None,
    )


def _check_parameters(
    content: bytes, params: dict[str, list[str]], filename: str | None, fmt: Format
) -> Ags4CheckRequest:
    names = params.get("name", [])
    return Ags4CheckRequest(content=content, name=names[-1] if names else filename, format=fmt)


def _tables_parameters(operation: str) -> _Build:
    """The service request of ``POST /v1/tables/<operation>``: a package ZIP and its name."""

    def build(content: bytes, params: dict[str, list[str]], filename: str | None) -> TablesRequest:
        names = params.get("name", [])
        return TablesRequest(
            content=content, name=names[-1] if names else filename, operation=operation
        )

    return build


def _from_diggs_parameters(
    content: bytes, params: dict[str, list[str]], filename: str | None
) -> TablesFromDiggsRequest:
    names = params.get("name", [])
    return TablesFromDiggsRequest(content=content, name=names[-1] if names else filename)


_Job: TypeAlias = (
    ValidationRequest
    | ReadRequest
    | ConvertRequest
    | Ags4CheckRequest
    | DiffRequest
    | TablesRequest
    | TablesFromDiggsRequest
)
_Build: TypeAlias = Callable[[bytes, dict[str, list[str]], str | None], _Job]
_Reader: TypeAlias = Callable[[], Awaitable["_Job | Response"]]


def _overridden_errors(
    query: dict[str, list[str]],
    form: dict[str, list[str]],
    check: Callable[[dict[str, list[str]]], tuple[tuple[str, str], ...]],
) -> tuple[tuple[str, str], ...]:
    """The problems of the query values the form overrides, each judged on its own.

    The form wins, but a query value it overrides must still be valid. Each such value
    is put in place of the form's in the effective request (the query, then the form),
    and only the problems that request reports about that same parameter count, less
    any the effective request has itself (the service reports those). A problem it
    causes in another parameter does not count: that parameter's effective value comes
    from elsewhere. So ``?to=ags4&ags_version=4.2`` with the field ``ags_version=4.1.1``
    and ``?to=diggs`` with the fields ``to=ags4&ags_version=4.2`` are both accepted,
    and ``?to=bogus`` with the field ``to=diggs`` is not. ``check`` gives a parameter
    set's problems as ``(parameter, problem)`` pairs, with fixed texts.
    """
    effective = query | form
    own: set[tuple[str, str]] | None = None
    errors: list[tuple[str, str]] = []
    for key in form:
        if not query.get(key):
            continue
        if own is None:
            own = set(check(effective))
        for error in check(effective | {key: query[key]}):
            if error[0] == key and error not in own and error not in errors:
                errors.append(error)
    return tuple(errors)


async def _read_input(
    request: Request,
    settings: ServerSettings,
    *,
    multipart: bool,
    fields: frozenset[str],
    build: _Build,
    files: Sequence[str] = ("file",),
) -> _Job | Response:
    """Read the body (streamed, counted) and the parameters into a service request.

    A multipart request may give each parameter in the query or in the form; the
    form wins, but both must be valid (``invalid-parameter``; an overridden query value
    is judged in context, see :func:`_overridden_errors`). The ``name`` parameter
    wins over the part's file name. Repeated values: the last ``name``, ``to``,
    ``diggs_version``, ``ags_version`` and ``select`` count; ``checks`` values are joined. An AGS4
    check's ``dictionary`` part (``files``) goes into the request as bytes.
    """
    params = {key: request.query_params.getlist(key) for key in fields}
    filename: str | None = None
    attachments: dict[str, bytes] = {}
    limit = _body_limit(settings, multipart=multipart)
    try:
        if multipart:
            upload = await _read_multipart(request, limit, settings.max_body_bytes, fields, files)
            if isinstance(upload, Response):
                return upload
            content, filename, attachments = upload.content, upload.filename, upload.attachments
            errors = _overridden_errors(
                params,
                upload.fields,
                lambda given: _service.parameter_errors(build(b"", given, filename)),
            )
            if errors:
                return problem("invalid-parameter", "invalid parameters", errors=errors)
            params |= upload.fields
        else:
            content = await _read_raw(request, limit)
    except _TooLargeError:
        return _too_large()
    job = dataclasses.replace(build(content, params, filename), max_bytes=settings.max_body_bytes)
    if isinstance(job, ReadRequest | ConvertRequest | TablesRequest | TablesFromDiggsRequest):
        limit = RESULT_FACTOR * settings.max_body_bytes
        job = dataclasses.replace(job, max_result_bytes=limit)
    if isinstance(job, Ags4CheckRequest) and "dictionary" in attachments:
        job = dataclasses.replace(job, dictionary=attachments["dictionary"])
    return job


# --- comparing two files -------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class _Pair:
    """The two file parts of a comparison and its short fields."""

    contents: dict[str, bytes]
    filenames: dict[str, str | None]
    fields: dict[str, list[str]]


def _diff_option(key: str, text: str) -> object:
    """One comparison option from its text; text that does not parse is kept as text.

    The service then refuses it as ``invalid-parameter`` with a fixed problem, so the
    caller's value is never echoed.
    """
    if key == "match":
        return text
    if key in ("show_reformatted", "show_renamed"):
        return {"true": True, "false": False}.get(text, text)
    if key == "max_changes":
        return int(text) if _INTEGER.fullmatch(text) else text
    if len(text) <= _MAX_NUMBER_CHARS and _NUMBER.fullmatch(text):
        return float(text)
    return text


def _diff_request(
    old: bytes, new: bytes, params: dict[str, list[str]], filenames: dict[str, str | None]
) -> DiffRequest:
    """The service request: the last value of each parameter; a name wins over the part's."""
    names: dict[str, str | None] = {}
    for side in _service.DIFF_SIDES:
        given = params.get(f"{side}_name")
        names[side] = given[-1] if given else filenames.get(side)
    options = {
        key: _diff_option(key, values[-1])
        for key, values in params.items()
        if key in _service.DIFF_DEFAULTS and values
    }
    return DiffRequest(
        old=old,
        new=new,
        old_name=names["old"],
        new_name=names["new"],
        **options,  # type: ignore[arg-type]  # the service checks every value
    )


async def _read_pair(request: Request, limit: int, file_limit: int) -> _Pair | Response:
    """Parse the ``old`` and ``new`` parts (both required, once each) and the short fields.

    The whole body counts against ``limit`` while it streams; each file part against
    ``file_limit``. Spooled parts are closed (and so deleted) before returning.
    """
    parser = MultiPartParser(
        request.headers,
        _counted(request, limit),
        max_files=len(_DIFF_FILES),
        max_fields=MAX_FIELDS,
        max_part_size=MAX_PART_SIZE,
    )
    try:
        form = await parser.parse()
    except MultiPartException:
        return problem("invalid-multipart", "The multipart body could not be parsed.")
    try:
        contents: dict[str, bytes] = {}
        filenames: dict[str, str | None] = {}
        for side in _DIFF_FILES:
            parts = form.getlist(side)
            if len(parts) > 1:
                return problem("invalid-multipart", f"Send exactly one part named '{side}'.")
            if not parts or not isinstance(parts[0], UploadFile):
                return problem(
                    "missing-input",
                    "Send the two documents in multipart parts named 'old' and 'new'.",
                )
            contents[side] = await parts[0].read(file_limit + 1)
            if len(contents[side]) > file_limit:
                return _too_large()
            filenames[side] = parts[0].filename
        fields: dict[str, list[str]] = {}
        for key, value in form.multi_items():
            if key in _DIFF_FILES:
                continue
            if key not in _DIFF_FIELDS or not isinstance(value, str):
                names = ", ".join(sorted({*_DIFF_FILES, *_DIFF_FIELDS}))
                return problem("invalid-multipart", f"Only the fields {names} are accepted.")
            fields.setdefault(key, []).append(value)
        return _Pair(contents=contents, filenames=filenames, fields=fields)
    finally:
        await form.close()


async def _read_diff_input(request: Request, settings: ServerSettings) -> _Job | Response:
    """Read a comparison's two files and parameters into a service request.

    As :func:`_read_input` does for one file: each parameter in the query or in the
    form (the form wins, but both must be valid), the body streamed and counted. The
    body may carry two files of up to ``max_body_bytes`` each; the result is held to
    the same :data:`RESULT_FACTOR` times ``max_body_bytes`` as a read's.
    """
    params = {key: request.query_params.getlist(key) for key in _DIFF_FIELDS}
    limit = _body_limit(settings, multipart=True, files=len(_DIFF_FILES))
    try:
        pair = await _read_pair(request, limit, settings.max_body_bytes)
    except _TooLargeError:
        return _too_large()
    if isinstance(pair, Response):
        return pair
    errors = _overridden_errors(
        params,
        pair.fields,
        lambda given: _service.parameter_errors(_diff_request(b"", b"", given, pair.filenames)),
    )
    if errors:
        return problem("invalid-parameter", "invalid parameters", errors=errors)
    params |= pair.fields
    job = _diff_request(pair.contents["old"], pair.contents["new"], params, pair.filenames)
    return dataclasses.replace(
        job,
        max_bytes=settings.max_body_bytes,
        max_result_bytes=RESULT_FACTOR * settings.max_body_bytes,
    )


# --- per-router state --------------------------------------------------------------------


class _Admission:
    """A non-blocking counter of requests in flight (running plus queued)."""

    def __init__(self, slots: int) -> None:
        self.slots = slots
        self._used = 0
        self._lock = threading.Lock()

    def try_enter(self) -> bool:
        with self._lock:
            if self._used >= self.slots:
                return False
            self._used += 1
            return True

    def leave(self) -> None:
        with self._lock:
            self._used -= 1


class _State:
    """What one router owns: settings, admission, the run limiter and the pool."""

    def __init__(self, settings: ServerSettings, isolation: Literal["process", "thread"]) -> None:
        if settings.unknown_query == "ignore":
            warnings.warn(
                "unknown_query='ignore' is deprecated and will be removed in pyDIGGS 2.0:"
                " send only the query parameters an endpoint takes",
                DeprecationWarning,
                stacklevel=3,  # the caller of create_app or create_router
            )
        self.settings = settings
        self.isolation = isolation
        self.admission = _Admission(settings.admission_slots)
        self._limiter: RunVar[anyio.CapacityLimiter] = RunVar("pydiggs_server_limiter")
        self._pool: WorkerPool | None = None
        self._pool_lock = threading.Lock()
        self._closed = False

    def limiter(self) -> anyio.CapacityLimiter:
        """The per-event-loop limiter: at most ``workers`` validations run at once."""
        try:
            return self._limiter.get()
        except LookupError:
            limiter = anyio.CapacityLimiter(self.settings.workers)
            self._limiter.set(limiter)
            return limiter

    def pool(self, *, probe: bool = False) -> WorkerPool | None:
        """The worker pool, created on first use (its workers start lazily).

        None only for a ``probe`` after :meth:`close`: a readiness probe during
        shutdown must not start workers that nothing would stop.
        """
        with self._pool_lock:
            if probe and self._closed:
                return None
            if self._pool is None:
                self._pool = _service.make_pool(
                    self.settings.workers,
                    timeout=self.settings.timeout_s,
                    memory_mb=self.settings.worker_memory_mb,
                )
            return self._pool

    def _job_pool(self) -> WorkerPool:
        pool = self.pool()
        assert pool is not None  # noqa: S101  # only a probe gets None
        return pool

    async def start(self) -> None:
        """Start (and so begin warming) every worker; a no-op for thread isolation."""
        if self.isolation == "process":
            await anyio.to_thread.run_sync(self._job_pool().start)

    async def open(self) -> None:
        """The lifespan's start: probes may start a pool again, then start it."""
        self._closed = False
        await self.start()

    async def close(self) -> None:
        """Stop the workers; a later request starts a new pool, a readiness probe not."""
        with self._pool_lock:
            self._closed = True
            pool, self._pool = self._pool, None
        if pool is not None:
            await anyio.to_thread.run_sync(pool.close)

    async def ready(self) -> bool:
        if self.isolation == "thread":
            return True
        pool = self.pool(probe=True)
        if pool is None:  # shut down: never create a pool just to report on it
            return False
        await anyio.to_thread.run_sync(pool.start)  # idempotent; a new pool begins warming
        return await anyio.to_thread.run_sync(pool.ready)  # ready() may block briefly

    async def run(self, request: _Job) -> Outcome:
        timeout = self.settings.timeout_s
        job: Callable[[], Outcome]
        in_thread, in_pool = _RUNNERS[type(request)]
        if self.isolation == "process":
            pooled = getattr(_service, in_pool)
            job = functools.partial(pooled, self._job_pool(), request, timeout=timeout)
        else:
            job = functools.partial(getattr(_service, in_thread), request)
        return await anyio.to_thread.run_sync(job, limiter=self.limiter())


#: For each request type: the service call in this thread, and in the worker pool (names
#: in ``_service``, looked up per request).
_RUNNERS: Final[dict[type, tuple[str, str]]] = {
    ValidationRequest: ("validate", "validate_in_pool"),
    ReadRequest: ("read", "read_in_pool"),
    ConvertRequest: ("convert", "convert_in_pool"),
    Ags4CheckRequest: ("ags4_check", "ags4_check_in_pool"),
    DiffRequest: ("diff", "diff_in_pool"),
    TablesRequest: ("tables", "tables_in_pool"),
    TablesFromDiggsRequest: ("tables_from_diggs", "tables_from_diggs_in_pool"),
}


# --- routes ------------------------------------------------------------------------------

_Endpoint = Callable[[Request], Awaitable[Response]]


def _guarded(state: _State, endpoint: _Endpoint) -> _Endpoint:
    """Refuse cross-site requests and turn any escaping exception into a 500 problem."""

    @functools.wraps(endpoint)
    async def wrapper(request: Request) -> Response:
        if is_cross_site(request.headers, state.settings):
            return problem("cross-site", "Cross-site requests are not accepted.")
        try:
            response = await endpoint(request)
        except ClientDisconnect:
            return problem("missing-input", "The request body was not received completely.")
        except Exception as exc:  # fail closed: no traceback or path reaches the client
            return internal_error(exc)
        response.headers.update(SECURITY_HEADERS)
        return response

    return wrapper


async def _admitted(
    state: _State,
    request: Request,
    *,
    multipart: bool,
    fields: frozenset[str] = frozenset(),
    build: _Build | None = None,
    files: Sequence[str] = ("file",),
    reader: _Reader | None = None,
) -> Outcome | Response:
    """Read the request's input and run it, within the admission and body limits.

    The input is read by :func:`_read_input` with ``fields``, ``build`` and ``files``,
    or by ``reader`` when given (a comparison's two files).
    """
    settings = state.settings
    if not state.admission.try_enter():
        return problem(
            "busy",
            "Too many requests are in progress; try again shortly.",
            headers={**_RETRY_AFTER, "Connection": "close"},
        )
    try:
        try:
            with anyio.fail_after(settings.body_timeout_s):
                if reader is not None:
                    job = await reader()
                else:
                    assert build is not None  # noqa: S101  # one of the two is given
                    job = await _read_input(
                        request,
                        settings,
                        multipart=multipart,
                        fields=fields,
                        build=build,
                        files=files,
                    )
        except TimeoutError:
            return problem(
                "request-timeout",
                "The request body did not arrive in time.",
                headers={"Connection": "close"},
            )
        if isinstance(job, Response):
            return job
        if state.isolation == "thread" and not isinstance(job, TablesRequest):
            # A package has no time budget: building from tables cannot be interrupted.
            job = dataclasses.replace(job, time_budget=settings.timeout_s)
        return await state.run(job)
    finally:
        state.admission.leave()


def _json(body: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        status_code=status,
        media_type=_schemas.FINDINGS_MEDIA_TYPE,
    )


def _add_ags4_routes(router: APIRouter, state: _State) -> None:
    """``POST /v1/convert`` and ``POST /v1/ags4/check``."""
    settings = state.settings

    async def convert(request: Request) -> Response:
        # A raw body is DIGGS XML for to=ags4 (given in the query) and AGS4 text otherwise.
        targets = request.query_params.getlist("to")
        to_ags4 = bool(targets) and targets[-1] == "ags4"
        checked = _precheck(
            request,
            settings,
            sarif=False,
            json_text="Accept application/json (a conversion result).",
            raw_media=_schemas.XML_MEDIA_TYPES if to_ags4 else _schemas.AGS4_MEDIA_TYPES,
            allowed=_CONVERT_FIELDS,
        )
        if isinstance(checked, Response):
            return checked
        outcome = await _admitted(
            state,
            request,
            multipart=checked[1],
            fields=_CONVERT_FIELDS,
            build=_convert_parameters,
        )
        if isinstance(outcome, Response):
            return outcome
        if not outcome.ok or outcome.document is None:
            return from_outcome(outcome)
        return Response(
            outcome.document, media_type=_schemas.FINDINGS_MEDIA_TYPE, headers={"Vary": "Accept"}
        )

    async def ags4_check(request: Request) -> Response:
        checked = _precheck(
            request, settings, raw_media=_schemas.AGS4_MEDIA_TYPES, allowed=_CHECK_FIELDS
        )
        if isinstance(checked, Response):
            return checked
        fmt, multipart = checked

        def build(
            content: bytes, params: dict[str, list[str]], filename: str | None
        ) -> Ags4CheckRequest:
            job = _check_parameters(content, params, filename, fmt)
            return dataclasses.replace(job, max_findings=settings.max_findings)

        outcome = await _admitted(
            state,
            request,
            multipart=multipart,
            fields=_CHECK_FIELDS,
            build=build,
            files=_CHECK_FILES,
        )
        if isinstance(outcome, Response):
            return outcome
        if not outcome.ok or outcome.document is None:
            return from_outcome(outcome)
        return Response(outcome.document, media_type=_MEDIA[fmt], headers={"Vary": "Accept"})

    router.add_api_route(
        "/convert",
        _guarded(state, convert),
        methods=["POST"],
        summary="Convert AGS4 to DIGGS 3.0.0 or DIGGS to AGS4, with every loss reported",
        operation_id="convert",
        responses=_schemas.convert_responses(),
        openapi_extra={
            "parameters": _schemas.convert_parameters(),
            "requestBody": _schemas.convert_request_body(),
        },
    )
    router.add_api_route(
        "/ags4/check",
        _guarded(state, ags4_check),
        methods=["POST"],
        summary="Check one AGS4 file against the AGS4 rules (Rules 1 to 20)",
        operation_id="ags4Check",
        responses=_schemas.ags4_check_responses(),
        openapi_extra={
            "parameters": _schemas.ags4_check_parameters(),
            "requestBody": _schemas.ags4_check_request_body(),
        },
    )


def _add_diff_route(router: APIRouter, state: _State) -> None:
    """``POST /v1/diff``: two multipart files, as ``/v1/read`` takes one."""
    settings = state.settings

    async def diff(request: Request) -> Response:
        checked = _precheck(
            request,
            settings,
            sarif=False,
            json_text="Accept application/json (a diggs-diff document).",
            raw_media=(),
            files=len(_DIFF_FILES),
            allowed=_DIFF_FIELDS,
        )
        if isinstance(checked, Response):
            return checked
        outcome = await _admitted(
            state, request, multipart=True, reader=lambda: _read_diff_input(request, settings)
        )
        if isinstance(outcome, Response):
            return outcome
        if not outcome.ok or outcome.document is None:
            return from_outcome(outcome)
        return Response(
            outcome.document, media_type=_schemas.FINDINGS_MEDIA_TYPE, headers={"Vary": "Accept"}
        )

    router.add_api_route(
        "/diff",
        _guarded(state, diff),
        methods=["POST"],
        summary="Compare two DIGGS documents: what was added, removed or changed",
        operation_id="diff",
        responses=_schemas.diff_responses(),
        openapi_extra={
            "parameters": _schemas.diff_parameters(),
            "requestBody": _schemas.diff_request_body(),
        },
    )


#: ``POST /v1/tables/<operation>``: the summary and operation id of each package route.
_TABLES_ROUTES: Final = {
    "to-diggs": (
        "Write a package of DIGGS tables profile tables as DIGGS 3.0.0 (preview)",
        "tablesToDiggs",
    ),
    "check": (
        "Check a package of profile tables against the DIGGS dictionaries (preview)",
        "tablesCheck",
    ),
    "round-trip": (
        "Write a package of profile tables as DIGGS, read it back and compare (preview)",
        "tablesRoundTrip",
    ),
}


def _add_tables_routes(router: APIRouter, state: _State) -> None:
    """``POST /v1/tables/to-diggs``, ``/check``, ``/round-trip`` and ``/from-diggs``."""
    settings = state.settings

    async def answer(outcome: Outcome | Response) -> Response:
        if isinstance(outcome, Response):
            return outcome
        if not outcome.ok or outcome.document is None:
            return from_outcome(outcome)
        return Response(
            outcome.document, media_type=_schemas.FINDINGS_MEDIA_TYPE, headers={"Vary": "Accept"}
        )

    def package_route(operation: str) -> _Endpoint:
        build = _tables_parameters(operation)

        async def endpoint(request: Request) -> Response:
            checked = _precheck(
                request,
                settings,
                sarif=False,
                json_text="Accept application/json.",
                raw_media=_schemas.ZIP_MEDIA_TYPES,
                allowed=_TABLES_FIELDS,
            )
            if isinstance(checked, Response):
                return checked
            return await answer(
                await _admitted(
                    state, request, multipart=checked[1], fields=_TABLES_FIELDS, build=build
                )
            )

        return endpoint

    async def from_diggs(request: Request) -> Response:
        checked = _precheck(
            request,
            settings,
            sarif=False,
            json_text="Accept application/json (the tables as a package ZIP, in base64).",
            allowed=_TABLES_FIELDS,
        )
        if isinstance(checked, Response):
            return checked
        return await answer(
            await _admitted(
                state,
                request,
                multipart=checked[1],
                fields=_TABLES_FIELDS,
                build=_from_diggs_parameters,
            )
        )

    for operation, (summary, operation_id) in _TABLES_ROUTES.items():
        router.add_api_route(
            f"/tables/{operation}",
            _guarded(state, package_route(operation)),
            methods=["POST"],
            summary=summary,
            operation_id=operation_id,
            responses=_schemas.tables_responses(operation),
            openapi_extra={
                "parameters": _schemas.tables_parameters(),
                "requestBody": _schemas.tables_request_body(),
            },
        )
    router.add_api_route(
        "/tables/from-diggs",
        _guarded(state, from_diggs),
        methods=["POST"],
        summary="Turn one DIGGS document into tables of the profile, as a package (preview)",
        operation_id="tablesFromDiggs",
        responses=_schemas.tables_from_diggs_responses(),
        openapi_extra={
            "parameters": _schemas.tables_parameters(),
            "requestBody": _schemas.tables_from_diggs_request_body(),
        },
    )


def _build_router(state: _State) -> APIRouter:
    settings = state.settings

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        await state.open()
        try:
            yield
        finally:
            await state.close()

    router = APIRouter(prefix="/v1", lifespan=lifespan, tags=["pydiggs"])

    async def validate(request: Request) -> Response:
        checked = _precheck(request, settings, allowed=_FORM_FIELDS)
        if isinstance(checked, Response):
            return checked
        fmt, multipart = checked

        def build(
            content: bytes, params: dict[str, list[str]], filename: str | None
        ) -> ValidationRequest:
            job = _parameters(content, params, filename, fmt)
            return dataclasses.replace(job, max_findings=settings.max_findings)

        outcome = await _admitted(
            state, request, multipart=multipart, fields=_FORM_FIELDS, build=build
        )
        if isinstance(outcome, Response):
            return outcome
        if not outcome.ok or outcome.document is None:
            return from_outcome(outcome)
        return Response(outcome.document, media_type=_MEDIA[fmt], headers={"Vary": "Accept"})

    async def read(request: Request) -> Response:
        checked = _precheck(request, settings, sarif=False, allowed=_READ_FIELDS)
        if isinstance(checked, Response):
            return checked
        outcome = await _admitted(
            state, request, multipart=checked[1], fields=_READ_FIELDS, build=_read_parameters
        )
        if isinstance(outcome, Response):
            return outcome
        if not outcome.ok or outcome.document is None:
            return from_outcome(outcome)
        return Response(
            outcome.document, media_type=_schemas.FINDINGS_MEDIA_TYPE, headers={"Vary": "Accept"}
        )

    async def versions(request: Request) -> Response:
        del request
        return _json({"api_version": "1", **_service.versions()})

    async def capabilities(request: Request) -> Response:
        del request
        return _json(_service.capabilities())

    async def rules(request: Request) -> Response:
        unknown = _unknown_query(request, settings, _RULES_FIELDS)
        if unknown is not None:
            return unknown
        answer = _service.rules(request.query_params.get("step"))
        if isinstance(answer, Outcome):
            return from_outcome(answer)
        return _json(answer)

    async def rule(request: Request) -> Response:
        answer = _service.explain(str(request.path_params["rule_id"]))
        if isinstance(answer, Outcome):
            return from_outcome(answer)
        return _json(answer)

    async def readyz(request: Request) -> Response:
        del request
        return await _readiness(state)

    router.add_api_route(
        "/validate",
        _guarded(state, validate),
        methods=["POST"],
        summary="Validate one DIGGS document",
        operation_id="validate",
        responses=_schemas.validate_responses(),
        openapi_extra={
            "parameters": _schemas.validate_parameters(),
            "requestBody": _schemas.validate_request_body(),
        },
    )
    router.add_api_route(
        "/read",
        _guarded(state, read),
        methods=["POST"],
        summary="Read one DIGGS document into a diggs-model document",
        operation_id="read",
        responses=_schemas.read_responses(),
        openapi_extra={
            "parameters": _schemas.read_parameters(),
            "requestBody": _schemas.read_request_body(),
        },
    )
    _add_ags4_routes(router, state)
    _add_diff_route(router, state)
    _add_tables_routes(router, state)
    if settings.versions_enabled:
        router.add_api_route(
            "/versions",
            _guarded(state, versions),
            methods=["GET"],
            summary="Versions of pydiggs, the findings schema and the bundled data",
            operation_id="versions",
            responses=_schemas.versions_responses(),
        )
        router.add_api_route(
            "/capabilities",
            _guarded(state, capabilities),
            methods=["GET"],
            summary="What this pydiggs can do: operations, options, stability, extras",
            operation_id="capabilities",
            responses=_schemas.capabilities_responses(),
        )
    router.add_api_route(
        "/rules",
        _guarded(state, rules),
        methods=["GET"],
        summary="The rule catalogue",
        operation_id="listRules",
        responses=_schemas.rules_responses(),
        openapi_extra={"parameters": _schemas.rules_parameters()},
    )
    router.add_api_route(
        "/rules/{rule_id}",
        _guarded(state, rule),
        methods=["GET"],
        summary="Explain one rule id",
        operation_id="explainRule",
        responses=_schemas.rule_responses(),
        openapi_extra={"parameters": _schemas.rule_id_parameter()},
    )
    router.add_api_route(
        "/readyz",
        _guarded(state, readyz),
        methods=["GET"],
        summary="Readiness: 200 once the workers are warm",
        operation_id="readyzV1",
        responses=_schemas.readyz_responses(),
    )
    return router


async def _readiness(state: _State) -> Response:
    if await state.ready():
        return _json({"status": "ready"})
    return problem("not-ready", "The validation workers are still starting.", headers=_RETRY_AFTER)


def create_router(settings: ServerSettings | None = None) -> APIRouter:
    """An :class:`~fastapi.APIRouter` (prefix ``/v1``) to mount in your own FastAPI app.

    The routes enforce every limit themselves (body size, admission, multipart,
    cross-site refusal), so they hold without :func:`create_app`'s middleware; they do
    not check ``Host`` or add CORS headers: that is the host app's job. ``isolation``
    None means ``"thread"`` here: no hard timeout, unsafe for untrusted input (a
    warning is logged, as by :func:`create_app`); pass
    ``ServerSettings(isolation="process")`` for worker processes. The router's
    lifespan starts and stops the pool when the host app runs lifespans; otherwise
    the pool starts on the first request or ``/v1/readyz`` probe.

    Args:
        settings: Limits and switches; default ``ServerSettings()``.

    Returns:
        The router: ``POST /v1/validate``, ``POST /v1/read``, ``POST /v1/convert``,
        ``POST /v1/ags4/check``, ``POST /v1/diff``, ``POST /v1/tables/to-diggs``,
        ``/check``, ``/round-trip`` and ``/from-diggs`` (preview), ``GET /v1/versions`` and
        ``GET /v1/capabilities`` (if ``versions_enabled``),
        ``GET /v1/rules``, ``GET /v1/rules/{rule_id}`` and ``GET /v1/readyz``.

    Examples:
        >>> from fastapi import FastAPI
        >>> from pydiggs.server import ServerSettings, create_router
        >>> app = FastAPI()
        >>> app.include_router(create_router(ServerSettings(workers=1)))
        >>> sorted(app.openapi()["paths"])[:3]
        ['/v1/ags4/check', '/v1/capabilities', '/v1/convert']
    """
    settings = settings if settings is not None else ServerSettings()
    isolation = settings.isolation or "thread"
    if isolation == "thread":
        _warn_thread_isolation()
    return _build_router(_State(settings, isolation))


def _warn_thread_isolation() -> None:
    _log.warning(
        "isolation='thread' has no hard timeout and is unsafe for untrusted input;"
        " use isolation='process'"
    )


class _HostCheck:
    """Refuse a ``Host`` header that is not listed (DNS rebinding), as a problem."""

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self.allowed = frozenset(h.lower() for h in allowed_hosts)

    @staticmethod
    def host_name(header: str) -> str:
        """``Host`` without its port; IPv6 literals keep their brackets."""
        header = header.strip().lower()
        if header.startswith("["):
            end = header.find("]")
            return header[: end + 1] if end != -1 else ""
        return header.partition(":")[0]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = Headers(scope=scope).get("host")
            if host is None or self.host_name(host) not in self.allowed:
                response = problem("invalid-host", "The Host header is not one this server serves.")
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    """The complete HTTP API: :func:`create_router` plus app-level protection.

    ``isolation`` None means ``"process"`` (spawned workers, hard timeout). The app
    adds a ``Host`` check against ``allowed_hosts`` (the loopback names when unset),
    CORS for the listed ``cors_origins`` only, problem responses
    for unknown paths and methods, and the root probes ``GET /livez`` (200 while
    serving) and ``GET /readyz``. ``/docs``, ``/redoc`` and ``/openapi.json`` are
    served only when ``docs_enabled``.

    Args:
        settings: Limits and switches; default ``ServerSettings()``.

    Returns:
        A FastAPI application; run it with ``pydiggs serve --http`` or any ASGI server.

    Examples:
        >>> from pydiggs.server import ServerSettings, create_app
        >>> app = create_app(ServerSettings(workers=1))
        >>> app.docs_url is None, "/livez" in app.openapi()["paths"]
        (True, True)
    """
    settings = settings if settings is not None else ServerSettings()
    isolation = settings.isolation or "process"
    if isolation == "thread":
        _warn_thread_isolation()
    state = _State(dataclasses.replace(settings, isolation=isolation), isolation)
    docs = settings.docs_enabled
    app = FastAPI(
        title="pydiggs HTTP API",
        version="1",
        summary=(
            "Validate, read and compare DIGGS documents (XSD, dictionaries, Schematron);"
            " check AGS4 files, convert AGS4 to DIGGS and DIGGS to AGS4, and convert"
            " tables of the DIGGS tables profile to DIGGS and back."
        ),
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        exception_handlers={
            StarletteHTTPException: http_exception_handler,
            Exception: unhandled_exception_handler,
        },
    )
    app.include_router(_build_router(state))

    async def livez(request: Request) -> Response:
        del request
        return _json({"status": "ready"})

    async def readyz(request: Request) -> Response:
        del request
        return await _readiness(state)

    app.add_api_route(
        "/livez",
        livez,
        methods=["GET"],
        summary="Liveness: 200 while the server runs",
        operation_id="livez",
        responses={200: _schemas.readyz_responses()[200]},
    )
    app.add_api_route(
        "/readyz",
        readyz,
        methods=["GET"],
        summary="Readiness: 200 once the workers are warm",
        operation_id="readyz",
        responses={k: v for k, v in _schemas.readyz_responses().items() if k != 403},  # noqa: PLR2004
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Accept", "Content-Type"],
            max_age=600,
        )
    hosts = settings.allowed_hosts if settings.allowed_hosts is not None else LOOPBACK_HOSTS
    app.add_middleware(_HostCheck, allowed_hosts=hosts)
    app.openapi_schema = _schemas.lift_components(app.openapi())
    return app


def openapi_text() -> str:
    """The OpenAPI document of :func:`create_app` with default settings, as committed.

    ``src/pydiggs/server/openapi.json`` holds this text; a test fails when they drift.

    Examples:
        >>> import json
        >>> json.loads(openapi_text())["openapi"].startswith("3.1")
        True
    """
    document = create_app(ServerSettings(workers=1, isolation="process")).openapi()
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
