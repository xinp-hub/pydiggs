"""diggs-findings 1.2 is 1.1 plus one step, ``diff`` (the model diff of pyDIGGS 1.12.0).

The 1.0 and 1.1 schema files are frozen: they stay in the package byte for byte, and every
document that is valid against 1.1 is valid against 1.2.
"""

from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources
from pathlib import Path

import jsonschema
import pytest

import pydiggs
from pydiggs import Finding, Report, Step, explain_rule, rule_catalogue, validate_bytes
from pydiggs.findings import _json, rules, run

ROOT = Path(__file__).resolve().parent.parent
URL = "https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-{}.schema.json"
#: sha256 of diggs-findings-1.1.schema.json as released in pyDIGGS 1.9.0 to 1.11.0.
SHA256_1_1 = "aab8cf21e132dfb6ccd90ea4253387113707a32eb4eb3164fe3867ec605468b8"
DIFF_RULES = ("diff.ambiguous-match", "diff.limit", "diff.timeout")


def _packaged(version: str) -> bytes:
    name = f"diggs-findings-{version}.schema.json"
    return resources.files("pydiggs.findings").joinpath(name).read_bytes()


SCHEMA_1_1 = json.loads(_packaged("1.1"))
SCHEMA_1_2 = json.loads(_packaged("1.2"))
V11 = jsonschema.Draft202012Validator(SCHEMA_1_1)
V12 = jsonschema.Draft202012Validator(SCHEMA_1_2)


def _without_text(node):
    if isinstance(node, dict):
        return {k: _without_text(v) for k, v in node.items() if k not in ("description", "title")}
    if isinstance(node, list):
        return [_without_text(v) for v in node]
    return node


def test_the_1_1_schema_file_is_byte_identical_and_still_packaged_and_published():
    assert hashlib.sha256(_packaged("1.1")).hexdigest() == SHA256_1_1
    assert SCHEMA_1_1["$id"] == URL.format("1.1")
    hooks = (ROOT / "scripts" / "mkdocs_hooks.py").read_text("utf-8")
    provenance = (ROOT / "src" / "pydiggs" / "PROVENANCE.toml").read_text("utf-8")
    for version in ("1.0", "1.1", "1.2"):
        name = f"diggs-findings-{version}.schema.json"
        assert name in hooks
        assert f'[files."findings/{name}"]' in provenance
        assert (ROOT / "js" / "schemas" / name).read_bytes() == _packaged(version)


def test_1_2_is_a_valid_schema_with_the_published_id_and_is_what_pydiggs_writes():
    jsonschema.Draft202012Validator.check_schema(SCHEMA_1_2)
    assert SCHEMA_1_2["$id"] == URL.format("1.2") == pydiggs.FINDINGS_SCHEMA_URL
    assert SCHEMA_1_2["title"] == "diggs-findings 1.2"
    assert pydiggs.FINDINGS_SCHEMA_VERSION == "1.2"
    assert pydiggs.findings_schema() == SCHEMA_1_2
    assert _json.SCHEMA_RESOURCE == "diggs-findings-1.2.schema.json"
    assert pydiggs.version_info().findings_schema_version == "1.2"
    assert pydiggs.version_info().findings_schema_url == URL.format("1.2")


def test_1_2_differs_from_1_1_by_exactly_the_diff_step_and_the_accepted_urls():
    older = _without_text(copy.deepcopy(SCHEMA_1_1))
    newer = _without_text(copy.deepcopy(SCHEMA_1_2))
    steps = newer["$defs"]["step"]["enum"]
    assert steps[-1] == "diff"
    del steps[-1]
    finding = newer["$defs"]["finding"]["properties"]
    assert "|ags4|convert|diff)" in finding["rule_id"]["pattern"]
    finding["rule_id"]["pattern"] = finding["rule_id"]["pattern"].replace("|diff", "")
    assert newer["properties"]["$schema"] == {
        "enum": [URL.format("1.2"), URL.format("1.1"), URL.format("1.0")]
    }
    newer["properties"]["$schema"] = older["properties"]["$schema"]
    newer["$id"] = older["$id"]
    assert newer == older


def test_the_step_enum_is_the_python_enum_and_diff_is_never_validated():
    assert SCHEMA_1_2["$defs"]["step"]["enum"] == [s.value for s in Step]
    assert Step.DIFF.value == "diff"
    assert [s.value for s in Step][-3:] == ["ags4", "convert", "diff"]
    assert Step.DIFF.order > Step.CONVERT.order
    assert Step.DIFF not in run.NATIVE_STEPS
    with pytest.raises(ValueError, match="diff"):
        validate_bytes(b"<Diggs/>", checks=["diff"])


def test_a_1_1_document_is_a_1_2_document():
    report = validate_bytes(b"<Diggs/>", name="a.xml")
    document = json.loads(report.to_json())
    V12.validate(document)
    as_1_1 = {**document, "$schema": URL.format("1.1"), "schema_version": "1.1"}
    V11.validate(as_1_1)
    V12.validate(as_1_1)
    assert not V11.is_valid(document)  # 1.1 pins its own URL


def test_a_report_with_a_diff_finding_validates_against_1_2_only():
    finding = Finding(
        step="diff", rule_id="diff.limit", severity="warning", message="Stopped at max_changes."
    )
    report = Report(
        path="a.xml",
        sha256="a" * 64,
        diggs_version="3.0.0",
        steps={},
        findings=(finding,),
        tool_version="1.12.0",
    )
    document = json.loads(report.to_json())
    V12.validate(document)
    assert not V11.is_valid({**document, "$schema": URL.format("1.1"), "schema_version": "1.1"})
    with pytest.raises(ValueError, match="not a valid rule id"):
        Finding(step="diff", rule_id="convert.x", severity="info", message="")


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["files"][0]["findings"][0].update(step="diffs"),
        lambda d: d["files"][0]["findings"][0].update(rule_id="diffs.limit"),
        lambda d: d.update({"$schema": URL.format("1.3")}),
    ],
    ids=["step", "rule-id", "future-url"],
)
def test_1_2_still_refuses_what_it_does_not_define(change):
    report = Report(
        path="a.xml",
        sha256="a" * 64,
        diggs_version="3.0.0",
        steps={},
        findings=(Finding(step="diff", rule_id="diff.limit", severity="warning", message="m"),),
        tool_version="1",
    )
    document = json.loads(report.to_json())
    change(document)
    assert not V12.is_valid(document)


# --- the rule ids --------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", DIFF_RULES)
def test_each_diff_rule_is_catalogued_with_a_severity_and_plain_guidance(rule_id):
    info = explain_rule(rule_id)
    assert info.known and info.step == "diff"
    assert info.severities
    assert info.guidance is not None and info.guidance.title and info.guidance.fix
    assert rule_id in {r.id for r in rule_catalogue()}


def test_the_severities_say_a_partial_diff_is_a_warning_and_ambiguity_is_information():
    assert explain_rule("diff.ambiguous-match").severities == ("info",)
    assert explain_rule("diff.limit").severities == ("warning",)
    assert explain_rule("diff.timeout").severities == ("warning",)
    assert rules.SARIF_LEVEL["warning"] == "warning"
