"""diggs-findings 1.3 is 1.2 plus one step, ``tables`` (the tables conversion, ``pydiggs.tables``).

The 1.0, 1.1 and 1.2 schema files are frozen: they stay in the package byte for byte, and
every document that is valid against 1.2 is valid against 1.3.
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
#: sha256 of diggs-findings-1.2.schema.json as released in pyDIGGS 1.12.0 to 1.14.0.
SHA256_1_2 = "70fced759aef2a2dd1269d9b5f831b709eb9d6e908ea17ae5523ebb882924007"
#: The rules 1.3 was published for; later ``tables.*`` rules need no new schema version.
TABLES_RULES = (
    "tables.unmapped-column",
    "tables.unknown-unit",
    "tables.orphan-row",
    "tables.duplicate-key",
    "tables.key-rewritten",
    "tables.value-unparsed",
    "tables.assumed",
    "tables.value-reformatted",
    "tables.incomplete",
    "tables.no-crs",
    "tables.extra-not-written",
    "tables.not-carried",
)


def _packaged(version: str) -> bytes:
    name = f"diggs-findings-{version}.schema.json"
    return resources.files("pydiggs.findings").joinpath(name).read_bytes()


SCHEMA_1_2 = json.loads(_packaged("1.2"))
SCHEMA_1_3 = json.loads(_packaged("1.3"))
V12 = jsonschema.Draft202012Validator(SCHEMA_1_2)
V13 = jsonschema.Draft202012Validator(SCHEMA_1_3)
#: Every earlier version, with its validator.
EARLIER = {
    version: jsonschema.Draft202012Validator(json.loads(_packaged(version)))
    for version in ("1.0", "1.1", "1.2")
}


def _as(version: str, document: dict) -> dict:
    """``document`` with the two fields that name the schema version set to ``version``."""
    return {**document, "$schema": URL.format(version), "schema_version": version}


def _without_text(node):
    if isinstance(node, dict):
        return {k: _without_text(v) for k, v in node.items() if k not in ("description", "title")}
    if isinstance(node, list):
        return [_without_text(v) for v in node]
    return node


def _tables_report(**changes) -> Report:
    fields = {
        "path": "a.xml",
        "sha256": "a" * 64,
        "diggs_version": "3.0.0",
        "steps": {},
        "findings": (
            Finding(
                step="tables",
                rule_id="tables.unmapped-column",
                severity="warning",
                message="A column is not a column of its profile table.",
            ),
        ),
        "tool_version": "1.15.0",
    }
    return Report(**{**fields, **changes})


def test_the_1_2_schema_file_is_byte_identical_and_still_packaged_and_published():
    assert hashlib.sha256(_packaged("1.2")).hexdigest() == SHA256_1_2
    assert SCHEMA_1_2["$id"] == URL.format("1.2")
    hooks = (ROOT / "scripts" / "mkdocs_hooks.py").read_text("utf-8")
    provenance = (ROOT / "src" / "pydiggs" / "PROVENANCE.toml").read_text("utf-8")
    for version in ("1.0", "1.1", "1.2", "1.3"):
        name = f"diggs-findings-{version}.schema.json"
        assert name in hooks
        assert f'[files."findings/{name}"]' in provenance
        assert (ROOT / "js" / "schemas" / name).read_bytes() == _packaged(version)


def test_1_3_is_a_valid_schema_with_the_published_id_and_is_what_pydiggs_writes():
    jsonschema.Draft202012Validator.check_schema(SCHEMA_1_3)
    assert SCHEMA_1_3["$id"] == URL.format("1.3") == pydiggs.FINDINGS_SCHEMA_URL
    assert SCHEMA_1_3["title"] == "diggs-findings 1.3"
    assert pydiggs.FINDINGS_SCHEMA_VERSION == "1.3"
    assert pydiggs.findings_schema() == SCHEMA_1_3
    assert _json.SCHEMA_RESOURCE == "diggs-findings-1.3.schema.json"
    assert pydiggs.version_info().findings_schema_version == "1.3"
    assert pydiggs.version_info().findings_schema_url == URL.format("1.3")


def test_1_3_differs_from_1_2_by_exactly_the_tables_step_and_the_accepted_urls():
    older = _without_text(copy.deepcopy(SCHEMA_1_2))
    newer = _without_text(copy.deepcopy(SCHEMA_1_3))
    steps = newer["$defs"]["step"]["enum"]
    assert steps[-1] == "tables"
    del steps[-1]
    finding = newer["$defs"]["finding"]["properties"]
    assert "|ags4|convert|diff|tables)" in finding["rule_id"]["pattern"]
    finding["rule_id"]["pattern"] = finding["rule_id"]["pattern"].replace("|tables", "")
    assert newer["properties"]["$schema"] == {
        "enum": [URL.format("1.3"), URL.format("1.2"), URL.format("1.1"), URL.format("1.0")]
    }
    newer["properties"]["$schema"] = older["properties"]["$schema"]
    newer["$id"] = older["$id"]
    assert newer == older


def test_the_step_enum_is_the_python_enum_and_tables_is_never_validated():
    assert SCHEMA_1_3["$defs"]["step"]["enum"] == [s.value for s in Step]
    assert Step.TABLES.value == "tables"
    assert [s.value for s in Step][-4:] == ["ags4", "convert", "diff", "tables"]
    assert Step.TABLES.order > Step.DIFF.order
    assert Step.TABLES not in run.NATIVE_STEPS
    with pytest.raises(ValueError, match="tables"):
        validate_bytes(b"<Diggs/>", checks=["tables"])


def test_a_1_2_document_is_a_1_3_document():
    report = validate_bytes(b"<Diggs/>", name="a.xml")
    document = json.loads(report.to_json())
    V13.validate(document)
    as_1_2 = _as("1.2", document)
    V12.validate(as_1_2)
    V13.validate(as_1_2)
    assert not V12.is_valid(document)  # 1.2 pins its own URL and the earlier ones


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2"])
def test_every_earlier_document_is_a_1_3_document(version):
    document = _as(version, json.loads(validate_bytes(b"<Diggs/>", name="a.xml").to_json()))
    EARLIER[version].validate(document)
    V13.validate(document)


@pytest.mark.parametrize(
    ("version", "finding"),
    [
        (
            "1.1",
            Finding(
                step="ags4", rule_id="ags4.rule-4", severity="error", message="m", path="GEOL/1"
            ),
        ),
        (
            "1.1",
            Finding(step="convert", rule_id="convert.assumed", severity="warning", message="m"),
        ),
        ("1.2", Finding(step="diff", rule_id="diff.limit", severity="warning", message="m")),
    ],
    ids=["ags4", "convert", "diff"],
)
def test_what_each_earlier_minor_added_is_still_valid_in_1_3(version, finding):
    document = _as(version, json.loads(_tables_report(findings=(finding,)).to_json()))
    EARLIER[version].validate(document)
    V13.validate(document)


def test_a_report_with_a_tables_finding_validates_against_1_3_only():
    document = json.loads(_tables_report().to_json())
    V13.validate(document)
    for version, validator in EARLIER.items():
        assert not validator.is_valid(_as(version, document)), version
    with pytest.raises(ValueError, match="not a valid rule id"):
        Finding(step="tables", rule_id="convert.x", severity="info", message="")


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["files"][0]["findings"][0].update(step="table"),
        lambda d: d["files"][0]["findings"][0].update(rule_id="table.assumed"),
        lambda d: d.update({"$schema": URL.format("1.4")}),
    ],
    ids=["step", "rule-id", "future-url"],
)
def test_1_3_still_refuses_what_it_does_not_define(change):
    document = json.loads(_tables_report().to_json())
    change(document)
    assert not V13.is_valid(document)


# --- the rule ids --------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", TABLES_RULES)
def test_each_tables_rule_is_catalogued_with_a_severity_and_plain_guidance(rule_id):
    info = explain_rule(rule_id)
    assert info.known and info.step == "tables"
    assert info.severities
    assert info.guidance is not None and info.guidance.title and info.guidance.fix
    assert rule_id in {r.id for r in rule_catalogue()}


def test_every_catalogued_tables_rule_fits_the_1_3_rule_id_pattern():
    pattern = SCHEMA_1_3["$defs"]["finding"]["properties"]["rule_id"]["pattern"]
    ids = [r.id for r in rule_catalogue() if r.step == "tables"]
    assert set(TABLES_RULES) <= set(ids)
    for rule_id in ids:
        assert jsonschema.Draft202012Validator({"type": "string", "pattern": pattern}).is_valid(
            rule_id
        ), rule_id


def test_the_severities_say_a_row_left_out_is_an_error_and_a_rewrite_is_information():
    assert explain_rule("tables.orphan-row").severities == ("error",)
    assert explain_rule("tables.duplicate-key").severities == ("error",)
    assert explain_rule("tables.incomplete").severities == ("error",)
    assert explain_rule("tables.key-rewritten").severities == ("info",)
    assert explain_rule("tables.value-reformatted").severities == ("info",)
    for rule_id in (
        "tables.unmapped-column",
        "tables.unknown-unit",
        "tables.value-unparsed",
        "tables.assumed",
        "tables.no-crs",
        "tables.extra-not-written",
        "tables.not-carried",
    ):
        assert explain_rule(rule_id).severities == ("warning",), rule_id
    assert rules.SARIF_LEVEL["warning"] == "warning"
