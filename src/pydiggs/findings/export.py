"""Whole ``diggs-findings`` documents: several reports in one, as a dict or as JSON.

Pure apart from reading the packaged JSON Schema: no lxml, no instance I/O. The
documents are byte-for-byte what ``pydiggs validate --format json`` writes for the
same reports (minus its trailing newline).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from functools import cache
from importlib import resources
from typing import Any, Final

from pydiggs.errors import ResourceError
from pydiggs.findings import _json
from pydiggs.findings.model import Report

__all__ = [
    "FINDINGS_SCHEMA_URL",
    "FINDINGS_SCHEMA_VERSION",
    "findings_document",
    "findings_json",
    "findings_schema",
]

#: The ``schema_version`` every document pydiggs writes declares.
FINDINGS_SCHEMA_VERSION: Final = _json.SCHEMA_VERSION
#: The published JSON Schema (also its ``$id`` and every document's ``$schema``).
FINDINGS_SCHEMA_URL: Final = _json.SCHEMA_URL


def _document(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The document around ``files[]`` entries, sorted by path (stable), for this pydiggs.

    ``tool`` is the running pydiggs and its bundled reference data. The CLI passes its
    unreadable entries here too, so the batch document is built in one place.
    """
    import pydiggs  # noqa: PLC0415  # the package imports this module
    from pydiggs.findings.run import reference_data  # noqa: PLC0415  # keeps lxml out

    files = sorted(entries, key=lambda e: str(e["path"]))
    return _json.document(files, tool_version=pydiggs.__version__, reference_data=reference_data())


def findings_document(reports: Iterable[Report], *, absolute_paths: bool = False) -> dict[str, Any]:
    """Build one ``diggs-findings`` 1.0 document from several reports, sorted by path.

    ``tool.version`` and ``tool.reference_data`` describe the running pydiggs. No
    reports gives a valid document with an empty ``files`` list.

    Args:
        reports: Reports from :func:`pydiggs.validate` or :func:`pydiggs.validate_bytes`.
        absolute_paths: Write each ``absolute_path`` (when known) instead of ``path``.

    Returns:
        A JSON-serialisable dict that validates against :func:`findings_schema`. For one
        report made by this pydiggs it equals ``report.to_document()``.

    Examples:
        >>> from pydiggs import findings_document, validate_bytes
        >>> b = validate_bytes(b"<Diggs/>", name="b.xml")
        >>> a = validate_bytes(b"<x/>", name="a.xml")
        >>> [f["path"] for f in findings_document([b, a])["files"]]
        ['a.xml', 'b.xml']
        >>> findings_document([])["files"]
        []
    """
    ordered = sorted(reports, key=lambda r: (r.path, r.absolute_path or ""))
    return _document(r.to_dict(absolute_paths=absolute_paths) for r in ordered)


def findings_json(
    reports: Iterable[Report], *, indent: int | None = 2, absolute_paths: bool = False
) -> str:
    r"""Serialise :func:`findings_document` deterministically.

    Keys keep their documented order, non-ASCII text is written as is
    (``ensure_ascii=False``) and NaN is refused, so the same reports always give the
    same text. ``indent=None`` gives one line with no newline in it, safe for NDJSON.

    Args:
        reports: As for :func:`findings_document`.
        indent: Spaces per level (default 2, as the CLI writes); None for one line.
        absolute_paths: As for :func:`findings_document`.

    Returns:
        The JSON text, without a trailing newline.

    Examples:
        >>> from pydiggs import findings_json, validate_bytes
        >>> text = findings_json([validate_bytes(b"<Diggs/>")], indent=None)
        >>> "\n" in text, text.startswith('{"$schema": "https://')
        (False, True)
    """
    return _json.dumps(findings_document(reports, absolute_paths=absolute_paths), indent=indent)


@cache
def _schema_text() -> str:
    try:
        return (
            resources.files("pydiggs.findings").joinpath(_json.SCHEMA_RESOURCE).read_text("utf-8")
        )
    except OSError as exc:
        raise ResourceError(f"bundled findings schema is missing: {exc}") from exc


def findings_schema() -> dict[str, Any]:
    """Return the packaged ``diggs-findings`` JSON Schema (draft 2020-12), parsed.

    The same file is published at :data:`FINDINGS_SCHEMA_URL`; reading it here needs
    no network. Each call returns a fresh copy, so changing it changes nothing else.

    Returns:
        The schema as a dict.

    Raises:
        ResourceError: The packaged schema is missing or broken.

    Examples:
        >>> from pydiggs import FINDINGS_SCHEMA_URL, findings_schema
        >>> schema = findings_schema()
        >>> schema["$id"] == FINDINGS_SCHEMA_URL
        True
    """
    try:
        schema: dict[str, Any] = json.loads(_schema_text())
    except ValueError as exc:
        raise ResourceError(f"bundled findings schema is broken: {exc}") from exc
    return schema
