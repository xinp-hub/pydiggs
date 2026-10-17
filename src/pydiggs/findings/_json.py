"""The ``diggs-findings`` 1.0 JSON document.

Pure: no lxml, no I/O. The JSON Schema ships next to this module as
``diggs-findings-1.0.schema.json`` and is published at :data:`SCHEMA_URL`.

A new optional field is a 1.1; renaming or removing one is a 2.0. There is no
timestamp, so the same input always gives the same bytes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Final

from pydiggs.findings.model import Finding, Report, Severity, UnreadableFile

__all__ = [
    "FORMAT",
    "SCHEMA_RESOURCE",
    "SCHEMA_URL",
    "SCHEMA_VERSION",
    "document",
    "dumps",
    "file_entry",
    "finding_entry",
    "unreadable_entry",
]

FORMAT: Final = "diggs-findings"
SCHEMA_VERSION: Final = "1.0"
#: File name of the JSON Schema inside the ``pydiggs.findings`` package.
SCHEMA_RESOURCE: Final = "diggs-findings-1.0.schema.json"
#: Where the schema is published; also the schema's ``$id``.
SCHEMA_URL: Final = "https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-1.0.schema.json"


def finding_entry(finding: Finding) -> dict[str, Any]:
    """One ``findings[]`` item."""
    entry: dict[str, Any] = {
        "layer": finding.layer,
        "step": finding.step.value,
        "rule_id": finding.rule_id,
        "severity": finding.severity.value,
        "message": finding.message,
        "location": {
            "line": finding.line,
            "column": finding.column,
            "xpath": finding.xpath,
            "gml_id": finding.gml_id,
        },
    }
    if finding.source_snippet is not None:
        entry["source_snippet"] = finding.source_snippet
    return entry


def _path(path: str, absolute_path: str | None, *, absolute_paths: bool) -> str:
    return absolute_path if absolute_paths and absolute_path else path


def file_entry(report: Report, *, absolute_paths: bool = False) -> dict[str, Any]:
    """One ``files[]`` item for a file that was checked."""
    return {
        "path": _path(report.path, report.absolute_path, absolute_paths=absolute_paths),
        "sha256": report.sha256,
        "diggs_version": report.diggs_version,
        "status": "checked",
        "steps": [{"step": step.value, "status": status} for step, status in report.steps.items()],
        "counts": report.counts,
        "findings": [finding_entry(f) for f in report.findings],
    }


def unreadable_entry(item: UnreadableFile, *, absolute_paths: bool = False) -> dict[str, Any]:
    """One ``files[]`` item for a file that could not be read."""
    return {
        "path": _path(item.path, item.absolute_path, absolute_paths=absolute_paths),
        "sha256": None,
        "diggs_version": None,
        "status": "unreadable",
        "error": item.error,
        "steps": [],
        "counts": {s.value: 0 for s in Severity},
        "findings": [],
    }


def document(
    files: Iterable[Mapping[str, Any]],
    *,
    tool_version: str,
    reference_data: Mapping[str, str],
) -> dict[str, Any]:
    """The whole document around already-built ``files[]`` items."""
    return {
        "$schema": SCHEMA_URL,
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": "pydiggs",
            "version": tool_version,
            "reference_data": dict(reference_data),
        },
        "files": [dict(f) for f in files],
    }


def dumps(doc: Mapping[str, Any], *, indent: int | None = 2) -> str:
    """Serialise deterministically (insertion order is fixed by the builders above)."""
    return json.dumps(doc, indent=indent, ensure_ascii=False, allow_nan=False)
