"""Hardened XML parsing shared by every pydiggs check.

DIGGS files routinely come from third parties, and libxml2 (the C library under
lxml) has been without a maintainer since late 2025. pydiggs therefore never relies
on parser defaults: every document goes through :func:`safe_parser`, which turns off
entity expansion, DTD loading and network access, and keeps libxml2's size limits.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from lxml import etree

#: Upper bound on a remote dictionary download (``allow_network=True`` only).
MAX_REMOTE_BYTES = 20 * 1024 * 1024
#: Seconds before a remote dictionary download gives up.
REMOTE_TIMEOUT = 15.0


def safe_parser() -> etree.XMLParser:
    """Return a new parser with entity expansion, DTDs and network access off.

    A fresh parser per call keeps this safe across threads; lxml parsers are not
    shareable between them.
    """
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        dtd_validation=False,
        no_network=True,
        huge_tree=False,
    )


def parse(source: str | os.PathLike[str]) -> etree._ElementTree:
    """Parse a local XML file with :func:`safe_parser`."""
    return etree.parse(str(Path(source)), parser=safe_parser())


def fetch_and_parse(url: str) -> etree._ElementTree:
    """Download ``url`` (http/https only, size-capped) and parse it safely.

    libxml2 2.14 dropped its own HTTP client, so the download happens in Python
    where the timeout and size limit can be enforced.

    Raises:
        OSError: on a non-HTTP scheme, a network failure, or an oversized body.
        etree.XMLSyntaxError: if the body is not well-formed XML.
    """
    if not url.startswith(("http://", "https://")):
        raise OSError(f"refusing to fetch non-HTTP URL: {url}")
    with urllib.request.urlopen(url, timeout=REMOTE_TIMEOUT) as response:  # noqa: S310
        body = response.read(MAX_REMOTE_BYTES + 1)
    if len(body) > MAX_REMOTE_BYTES:
        raise OSError(f"remote dictionary exceeds {MAX_REMOTE_BYTES} bytes: {url}")
    root = etree.fromstring(body, parser=safe_parser(), base_url=url)
    return etree.ElementTree(root)
