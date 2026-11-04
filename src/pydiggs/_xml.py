"""Hardened XML parsing shared by every pydiggs check.

DIGGS files routinely come from third parties, and libxml2 (the C library under
lxml) has been without a maintainer since late 2025. pydiggs therefore never relies
on parser defaults: every document goes through :func:`safe_parser`, which refuses
external entities, DTD loading and network access, and does not lift libxml2's size
limits. Remote dictionaries (``allow_network=True`` only) are fetched by
:func:`fetch_and_parse`, which re-checks the whitelist on every redirect.
"""

from __future__ import annotations

import errno
import functools
import os
import stat
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

from lxml import etree

if TYPE_CHECKING:
    import http.client
    import urllib.request

# urllib.request pulls in http.client, socket and ssl. Only an allow_network download
# needs them, so they are imported there: a plain ``import pydiggs`` (and the browser
# build) loads no networking modules.

#: Upper bound on a local XML file; far above any real DIGGS file.
MAX_LOCAL_BYTES = 1024 * 1024 * 1024
#: Upper bound on a remote dictionary download (``allow_network=True`` only).
MAX_REMOTE_BYTES = 20 * 1024 * 1024
#: Seconds allowed for any single socket operation of a download.
REMOTE_TIMEOUT = 15.0
#: Seconds allowed for a whole download, however slowly the server sends.
REMOTE_DEADLINE = 60.0

_CHUNK = 64 * 1024
_GZIP_MAGIC = b"\x1f\x8b"
_DEFAULT_PORTS = {"http": 80, "https": 443}

XMLSource = str | bytes | os.PathLike[str] | IO[bytes]


class _RefuseExternal(etree.Resolver):  # type: ignore[misc]  # lxml has no stubs here
    """Answer every external load (DTD subset, parameter entity) with nothing.

    ``attribute_defaults=True`` makes libxml2 read the external DTD subset (a
    ``SYSTEM`` id) even with ``load_dtd=False``: from local disk, a FIFO or a UNC
    path. This resolver runs before libxml2 opens anything and returns an empty
    document, so no file is opened and nothing outside the instance contributes.
    """

    def resolve(self, url: str, pubid: str, context: object) -> object:  # noqa: ARG002
        return self.resolve_string("", context)


def safe_parser(*, attribute_defaults: bool = False) -> etree.XMLParser:
    """Return a new parser that refuses external entities, DTDs and the network.

    ``resolve_entities="internal"`` expands only entities declared inside the
    document, so legitimate files parse as before; external entities stay
    unresolved and libxml2 reports them as undefined. (``False`` would leave entity
    nodes in the tree, which ``XMLSchema.validate`` cannot handle.) A fresh parser
    per call keeps this safe across threads; lxml parsers are not shareable.

    ``attribute_defaults=True`` fills in the internal DTD subset's ATTLIST defaults
    (validate() measures them). That option alone would make libxml2 load the
    external subset, so such a parser also refuses every external load: only the
    internal subset ever contributes.
    """
    parser = etree.XMLParser(
        resolve_entities="internal",
        load_dtd=False,
        dtd_validation=False,
        no_network=True,
        huge_tree=False,
        attribute_defaults=attribute_defaults,
    )
    if attribute_defaults:
        parser.resolvers.add(_RefuseExternal())
    return parser


def read_limited(
    source: XMLSource, limit: int | None = None, *, regular_only: bool = False
) -> tuple[bytes, str | bytes | None]:
    """Read a path, ``file:`` URI or binary file object, enforcing the local limits.

    ``limit`` is the largest accepted size in bytes (default :data:`MAX_LOCAL_BYTES`).
    ``regular_only`` (used by :func:`pydiggs.validate`) opens without blocking,
    refuses anything but a regular file (so a FIFO cannot hang a batch) and raises
    errno-carrying errors without the file name. The legacy callers leave it off: they
    can still read ``/dev/stdin`` or a pipe and get the 1.1 messages, name included.
    Returns the bytes and the name to report them under.

    Raises:
        OSError: if the file cannot be read, exceeds the limit, or is gzip-compressed.
    """
    limit = MAX_LOCAL_BYTES if limit is None else limit
    if hasattr(source, "read"):
        reader = cast("IO[bytes]", source)
        name = getattr(reader, "name", None)
        data = reader.read(limit + 1)
    else:
        path = os.fsdecode(os.fspath(source))
        if path.startswith("file:"):
            from urllib.request import url2pathname  # noqa: PLC0415  # see the note on imports

            path = url2pathname(urlsplit(path).path)
        name = path
        if regular_only:
            # O_NONBLOCK: opening a FIFO must not hang; then refuse anything irregular.
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as fh:
                info = os.fstat(fh.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise OSError(errno.EINVAL, "not a regular file")
                data = _read(fh, info, limit)
        else:
            with Path(path).open("rb") as fh:
                data = _read(fh, os.fstat(fh.fileno()), limit)
    if regular_only:  # validate(): no file name; it reports the path in its own form
        _check_bytes(data, limit)
    elif len(data) > limit:
        raise OSError(f"XML file exceeds {limit} bytes: {name}")  # 1.1 text, unchanged
    elif data[:2] == _GZIP_MAGIC:
        raise OSError(f"compressed XML is not accepted: {name}")
    return data, name


def _check_bytes(data: bytes | bytearray | memoryview, limit: int) -> None:
    """Refuse instance bytes over ``limit`` or gzip-compressed, with errno and no name.

    The one check behind :func:`pydiggs.validate` (``regular_only``),
    :func:`pydiggs.validate_bytes` and :func:`read_fd`, so they cannot drift apart.
    ``data`` may be up to ``limit + 1`` bytes long (the reader's one-byte overshoot).

    Raises:
        OSError: ``EFBIG`` past the limit, ``EINVAL`` for gzip magic.
    """
    if len(data) > limit:
        raise OSError(errno.EFBIG, f"XML file exceeds {limit} bytes")
    if data[:2] == _GZIP_MAGIC:
        raise OSError(errno.EINVAL, "compressed XML is not accepted")


#: Most bytes asked of a stream or descriptor in one read.
_STREAM_CHUNK: Final = 1024 * 1024


def read_chunks(read: Callable[[int], object], limit: int) -> bytes:
    """Read with ``read(n)`` until EOF or ``limit + 1`` bytes, then apply :func:`_check_bytes`.

    ``read`` is a binary stream's ``read`` (or a descriptor reader). Nothing is
    allocated past what arrives, so a short stream costs only its size.

    Raises:
        TypeError: ``read`` returned text (a text stream) or something not bytes-like.
        OSError: as :func:`_check_bytes`; ``EAGAIN`` when a non-blocking stream has no
            data (``read`` returned None); any error ``read`` raises.
    """
    buffer = bytearray()
    while len(buffer) <= limit:
        chunk = read(min(_STREAM_CHUNK, limit + 1 - len(buffer)))
        if chunk is None:
            raise OSError(errno.EAGAIN, "the stream has no data available (non-blocking)")
        if isinstance(chunk, str):
            raise TypeError("expected a binary stream, got a text stream (read() returned str)")
        if not isinstance(chunk, bytes | bytearray | memoryview):
            raise TypeError(f"read() returned {type(chunk).__name__}, not bytes")
        if not chunk:
            break
        buffer += chunk
    data = bytes(buffer)
    _check_bytes(data, limit)
    return data


def read_fd(fd: int, limit: int) -> bytes:
    """Read an already opened, already verified descriptor, enforcing the instance limits.

    For callers that open a file themselves and check *that* descriptor (open first,
    then verify). Unlike :func:`read_limited` no path string is involved, so nothing
    is URI-decoded. The descriptor must be a regular file; it is not closed.

    Raises:
        OSError: ``EINVAL`` when it is not a regular file, as :func:`_check_bytes`, or
            any read error.
    """
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise OSError(errno.EINVAL, "not a regular file")
    return read_chunks(lambda n: os.read(fd, n), limit)


def _read(fh: IO[bytes], info: os.stat_result, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes, asking first for only the file's size.

    read(n) allocates n bytes at once, so asking for the limit itself would reserve
    100 MiB (or 1 GiB) for every small file. A file that grew since fstat() is read on
    up to the limit.
    """
    size = info.st_size if stat.S_ISREG(info.st_mode) and info.st_size > 0 else limit
    data = fh.read(min(size, limit) + 1)
    if len(data) > size and len(data) <= limit:
        data += fh.read(limit + 1 - len(data))
    return data


#: Parser options that leave every entity unexpanded and read nothing external.
_UNEXPANDED: Final[dict[str, bool]] = {
    "resolve_entities": False,
    "load_dtd": False,
    "dtd_validation": False,
    "no_network": True,
    "huge_tree": False,
}


def unexpanded_pull_parser() -> etree.XMLPullParser:
    """A push parser reporting ``start`` events that expands no entity at all."""
    return etree.XMLPullParser(events=("start",), **_UNEXPANDED)


def parse_unexpanded(data: bytes) -> etree._ElementTree:
    """Parse ``data`` with every entity left unexpanded (to inspect its DTD).

    Raises:
        lxml.etree.XMLSyntaxError: if the data is not well-formed.
    """
    return etree.fromstring(data, etree.XMLParser(**_UNEXPANDED)).getroottree()


_PROLOG_CHUNK: Final = 64 * 1024


def dtd_markup_entity(doc: etree._ElementTree) -> str | None:
    """Name of the first entity in ``doc``'s internal DTD whose value has markup."""
    dtd = doc.docinfo.internalDTD
    if dtd is None:
        return None
    for entity in dtd.iterentities():
        if "<" in (entity.content or ""):
            return entity.name or ""
    return None


def markup_entity(data: bytes) -> str | None:
    """Name of the first internal-DTD entity whose value contains markup, else None.

    A cheap first line, before the full parse: the prolog is parsed up to the root
    start tag with entities left unexpanded. When that parser cannot decide (lxml's
    push parser rejects, for example, UTF-32 with a BOM), the whole file is parsed
    without expanding entities instead; "cannot tell" is never "safe". validate()
    checks the main parse's DTD again afterwards.
    """
    parser = unexpanded_pull_parser()
    try:
        for start in range(0, len(data), _PROLOG_CHUNK):
            parser.feed(data[start : start + _PROLOG_CHUNK])
            for _, element in parser.read_events():
                return dtd_markup_entity(element.getroottree())
    except etree.XMLSyntaxError:
        pass
    try:
        unexpanded = parse_unexpanded(data)
    except etree.XMLSyntaxError:
        return None  # not well-formed even unexpanded; the main parse reports it
    return dtd_markup_entity(unexpanded)


def parse_bytes(
    data: bytes, base_url: str | None, *, attribute_defaults: bool = False
) -> etree._ElementTree:
    """Parse bytes already read by :func:`read_limited` with :func:`safe_parser`.

    Raises:
        etree.XMLSyntaxError: if the content is not well-formed XML. Its
            ``parser_errors`` lists this parse's own errors (the exception's
            ``error_log`` can also hold earlier parses' errors).
    """
    parser = safe_parser(attribute_defaults=attribute_defaults)
    try:
        root = etree.fromstring(data, parser=parser, base_url=base_url)
    except etree.XMLSyntaxError as exc:
        exc.parser_errors = list(parser.error_log)
        raise
    return etree.ElementTree(root)


def parse(source: XMLSource) -> etree._ElementTree:
    """Parse a path, ``file:`` URI or binary file object with :func:`safe_parser`.

    Files are read here rather than by libxml2, which silently gunzips anything it
    opens by path: an 11 kB compressed file can expand to hundreds of megabytes.

    Raises:
        OSError: if the file cannot be read, exceeds :data:`MAX_LOCAL_BYTES`, or is
            gzip-compressed.
        etree.XMLSyntaxError: if the content is not well-formed XML.
    """
    data, name = read_limited(source)
    base_url = os.fsdecode(name) if isinstance(name, str | bytes) else None
    return parse_bytes(data, base_url)


def url_allowed(url: str, patterns: Iterable[str]) -> bool:
    """Return True if ``url`` falls under one of the http(s) whitelist ``patterns``.

    Scheme, host and port must match exactly and the path must sit under the
    pattern's path, so ``https://diggsml.org.evil.example`` does not pass for
    ``https://diggsml.org``. URLs carrying credentials never pass.
    """
    try:
        target = urlsplit(url)
        target_port = target.port or _DEFAULT_PORTS.get(target.scheme)
    except ValueError:
        return False
    if (
        target.scheme not in _DEFAULT_PORTS
        or not target.hostname
        or target.username is not None
        or target.password is not None
    ):
        return False
    for pattern in patterns:
        try:
            allowed = urlsplit(pattern)
            allowed_port = allowed.port or _DEFAULT_PORTS.get(allowed.scheme)
        except ValueError:
            continue
        if allowed.scheme != target.scheme or allowed.hostname != target.hostname:
            continue
        if allowed_port != target_port:
            continue
        prefix = allowed.path.rstrip("/")
        if not prefix or target.path == prefix or target.path.startswith(prefix + "/"):
            return True
    return False


@functools.cache
def _redirect_handler_class() -> Callable[[tuple[str, ...]], urllib.request.HTTPRedirectHandler]:
    """The whitelist-enforcing redirect handler, defined on first use (see imports)."""
    import urllib.error  # noqa: PLC0415  # see the note on imports
    import urllib.request  # noqa: PLC0415

    class _WhitelistRedirectHandler(urllib.request.HTTPRedirectHandler):
        def __init__(self, patterns: tuple[str, ...]) -> None:
            super().__init__()
            self._patterns = patterns

        def redirect_request(  # noqa: PLR0917  # signature fixed by the stdlib base class
            self,
            req: urllib.request.Request,
            fp: IO[bytes],
            code: int,
            msg: str,
            headers: http.client.HTTPMessage,
            newurl: str,
        ) -> urllib.request.Request | None:
            if not url_allowed(newurl, self._patterns):
                raise urllib.error.HTTPError(
                    newurl, code, f"redirect outside the whitelist: {newurl}", headers, fp
                )
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return _WhitelistRedirectHandler


def fetch_and_parse(url: str, patterns: Iterable[str]) -> etree._ElementTree:
    """Download a whitelisted dictionary and parse it with :func:`safe_parser`.

    libxml2 2.14 dropped its own HTTP client, so the download happens in Python,
    where the whitelist, redirects, size and time can all be enforced.

    Raises:
        OSError: if the URL or a redirect is outside ``patterns``, on any network or
            protocol failure, or if the body is too large or too slow.
        etree.XMLSyntaxError: if the body is not well-formed XML.
    """
    allowed = tuple(patterns)
    if not url_allowed(url, allowed):
        raise OSError(f"refusing to fetch a URL outside the whitelist: {url}")
    import http.client  # noqa: PLC0415  # see the note on imports
    import urllib.request  # noqa: PLC0415

    handler = _redirect_handler_class()(allowed)
    opener = urllib.request.build_opener(handler)
    deadline = time.monotonic() + REMOTE_DEADLINE
    try:
        with opener.open(url, timeout=REMOTE_TIMEOUT) as response:
            final_url = response.geturl()
            chunks: list[bytes] = []
            size = 0
            while chunk := response.read(_CHUNK):
                size += len(chunk)
                if size > MAX_REMOTE_BYTES:
                    raise OSError(f"remote dictionary exceeds {MAX_REMOTE_BYTES} bytes: {url}")
                if time.monotonic() > deadline:
                    raise OSError(f"remote dictionary took over {REMOTE_DEADLINE:.0f} s: {url}")
                chunks.append(chunk)
    except (ValueError, http.client.HTTPException) as exc:
        raise OSError(f"could not fetch {url}: {exc}") from exc
    root = etree.fromstring(b"".join(chunks), parser=safe_parser(), base_url=final_url)
    return etree.ElementTree(root)
