"""SARIF 2.1.0 output, for code-scanning and CI tools.

Pure: no lxml, no I/O. One run per layer (``native``, later ``official``). Paths are
relative to the ``SRCROOT`` base id unless absolute paths are asked for, so a log
carries no home directory by default.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Final
from urllib.parse import quote

from pydiggs.findings import rules
from pydiggs.findings.model import Finding, Report, Severity, UnreadableFile

__all__ = ["FINGERPRINT", "SARIF_SCHEMA", "SARIF_VERSION", "file_uri", "sarif_log"]

SARIF_VERSION: Final = "2.1.0"
SARIF_SCHEMA: Final = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
#: ``partialFingerprints`` key carrying the conformance key.
FINGERPRINT: Final = "diggsConformanceKey/v1"
_INFO_URI: Final = "https://xinp-hub.github.io/pydiggs/"
_BASE_ID: Final = "SRCROOT"


def _artifact_location(
    path: str, absolute_path: str | None, *, absolute_paths: bool
) -> dict[str, Any]:
    if absolute_paths and absolute_path:
        return {"uri": file_uri(absolute_path)}
    return {"uri": quote(path, safe="/"), "uriBaseId": _BASE_ID}


_WINDOWS_ABSOLUTE: Final = re.compile(r"^(?:[A-Za-z]:/|//[^/]+/[^/]+)")


def file_uri(absolute_path: str) -> str:
    """A ``file:`` URI for a forward-slash absolute path, POSIX or Windows.

    Example:
        >>> file_uri("/home/me/a b.xml")
        'file:///home/me/a%20b.xml'
        >>> file_uri("C:/Users/me/a b.xml")
        'file:///C:/Users/me/a%20b.xml'
        >>> file_uri("//server/share/a.xml")
        'file://server/share/a.xml'
    """
    if _WINDOWS_ABSOLUTE.match(absolute_path):
        return PureWindowsPath(absolute_path).as_uri()
    return PurePosixPath(absolute_path).as_uri()


def _logical_location(xpath: str) -> dict[str, str]:
    last = xpath.rstrip("/").rsplit("/", 1)[-1]
    return {"fullyQualifiedName": xpath, "kind": "attribute" if last.startswith("@") else "element"}


def _result(finding: Finding, rule_index: int, artifact: dict[str, Any]) -> dict[str, Any]:
    location: dict[str, Any] = {"physicalLocation": {"artifactLocation": dict(artifact)}}
    if finding.line is not None:
        region: dict[str, int] = {"startLine": finding.line}
        if finding.column is not None:
            region["startColumn"] = finding.column
        location["physicalLocation"]["region"] = region
    if finding.xpath:
        location["logicalLocations"] = [_logical_location(finding.xpath)]
    properties: dict[str, str] = {"step": finding.step.value}
    if finding.gml_id:
        properties["gmlId"] = finding.gml_id
    return {
        "ruleId": finding.rule_id,
        "ruleIndex": rule_index,
        "level": rules.SARIF_LEVEL[finding.severity],
        "message": {"text": finding.message or rules.describe(finding.rule_id)},
        "locations": [location],
        "partialFingerprints": {FINGERPRINT: "|".join(finding.conformance_key())},
        "properties": properties,
    }


def _run(
    layer: str,
    reports: Sequence[Report],
    unreadable: Sequence[UnreadableFile],
    *,
    tool_version: str,
    absolute_paths: bool,
) -> dict[str, Any]:
    findings = [(i, f) for i, r in enumerate(reports) for f in r.findings if f.layer == layer]
    worst: dict[str, Severity] = {}
    for _, f in findings:
        if f.rule_id not in worst or f.severity.rank > worst[f.rule_id].rank:
            worst[f.rule_id] = f.severity
    rule_ids = sorted(worst)
    index = {rule_id: i for i, rule_id in enumerate(rule_ids)}

    artifacts: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    def artifact_index(artifact: dict[str, Any]) -> int:
        # SARIF requires unique artifacts; two inputs can share a bare file name.
        key = json.dumps(artifact, sort_keys=True)
        if key not in seen:
            seen[key] = len(artifacts)
            artifacts.append(artifact)
        return seen[key]

    locations: list[dict[str, Any]] = []
    for report in reports:
        loc = _artifact_location(report.path, report.absolute_path, absolute_paths=absolute_paths)
        index_ = artifact_index({"location": loc, "hashes": {"sha-256": report.sha256}})
        locations.append({**loc, "index": index_})
    notifications = []
    for item in unreadable:
        loc = _artifact_location(item.path, item.absolute_path, absolute_paths=absolute_paths)
        index_ = artifact_index({"location": loc})
        notifications.append(
            {
                "level": "error",
                "message": {"text": f"could not read {item.path}: {item.error}"},
                "locations": [{"physicalLocation": {"artifactLocation": {**loc, "index": index_}}}],
            }
        )

    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "pydiggs",
                "version": tool_version,
                "informationUri": _INFO_URI,
                "rules": [
                    {
                        "id": rule_id,
                        "shortDescription": {"text": rules.describe(rule_id)},
                        "helpUri": rules.HELP_URI,
                        "defaultConfiguration": {"level": rules.SARIF_LEVEL[worst[rule_id]]},
                    }
                    for rule_id in rule_ids
                ],
            }
        },
        "invocations": [
            {"executionSuccessful": not notifications, "toolExecutionNotifications": notifications}
        ],
        "artifacts": artifacts,
        "results": [_result(f, index[f.rule_id], locations[i]) for i, f in findings],
        "properties": {"layer": layer},
    }
    if not absolute_paths:
        run["originalUriBaseIds"] = {
            _BASE_ID: {"description": {"text": "The directory pydiggs was run from."}}
        }
    return run


def sarif_log(
    reports: Iterable[Report],
    unreadable: Iterable[UnreadableFile],
    *,
    tool_version: str,
    absolute_paths: bool = False,
) -> dict[str, Any]:
    """A SARIF 2.1.0 log with one run per layer present (always at least ``native``)."""
    report_list = list(reports)
    unreadable_list = list(unreadable)
    layers = sorted({f.layer for r in report_list for f in r.findings} | {"native"})
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            _run(
                layer,
                report_list,
                unreadable_list,
                tool_version=tool_version,
                absolute_paths=absolute_paths,
            )
            for layer in layers
        ],
    }
