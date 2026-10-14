"""Hardened XML parsing shared by every pydiggs check.

DIGGS files routinely come from third parties, and libxml2 (the C library under
lxml) has been without a maintainer since late 2025. pydiggs therefore never relies
on parser defaults: every document goes through :func:`safe_parser`, which refuses
external entities, DTD loading and network access, and does not lift libxml2's size
limits. Remote dictionaries (``allow_network=True`` only) are fetched by
:func:`fetch_and_parse`, which re-checks the whitelist on every redirect.
"""

from __future__ import annotations

import http.client
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from typing import IO
from urllib.parse import urlsplit

from lxml import etree

#: Upper bound on a remote dictionary download (``allow_network=True`` only).
MAX_REMOTE_BYTES = 20 * 1024 * 1024
#: Seconds allowed for any single socket operation of a download.
REMOTE_TIMEOUT = 15.0
#: Seconds allowed for a whole download, however slowly the server sends.
REMOTE_DEADLINE = 60.0

_CHUNK = 64 * 1024
_DEFAULT_PORTS = {"http": 80, "https": 443}

XMLSource = str | bytes | os.PathLike[str] | IO[bytes]


def safe_parser() -> etree.XMLParser:
    """Return a new parser that refuses external entities, DTDs and the network.

    ``resolve_entities="internal"`` expands only entities declared inside the
    document, so legitimate files parse as before; external entities stay
    unresolved and libxml2 reports them as undefined. (``False`` would leave entity
    nodes in the tree, which ``XMLSchema.validate`` cannot handle.) A fresh parser
    per call keeps this safe across threads; lxml parsers are not shareable.
    """
    return etree.XMLParser(
        resolve_entities="internal",
        load_dtd=False,
        dtd_validation=False,
        no_network=True,
        huge_tree=False,
    )


def parse(source: XMLSource) -> etree._ElementTree:
    """Parse a path, URI string or binary file object with :func:`safe_parser`."""
    if isinstance(source, os.PathLike):
        source = os.fspath(source)
    return etree.parse(source, parser=safe_parser())


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
    opener = urllib.request.build_opener(_WhitelistRedirectHandler(allowed))
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
