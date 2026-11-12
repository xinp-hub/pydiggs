"""diggs-findings 1.1 is 1.0 plus two steps, ``location.path`` and ``files[].format``.

The 1.0 schema file is frozen: it stays in the package, byte for byte, and every
document that is valid against it is valid against 1.1.
"""

from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources
from pathlib import Path

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import Finding, Report, Step, validate, validate_bytes
from pydiggs.findings import _json, _sarif, run

from .strategies import reports, unreadable_files

ROOT = Path(__file__).resolve().parent.parent
URL = "https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-{}.schema.json"
#: sha256 of diggs-findings-1.0.schema.json as released in pyDIGGS 1.3.0 to 1.8.0.
SHA256_1_0 = "8442ad9b40d927b9dcba6dccd70bff8da96b7c13c13cf7c9fc093f92fcb31246"
NEW_STEPS = ["ags4", "convert"]


def _packaged(version: str) -> bytes:
    name = f"diggs-findings-{version}.schema.json"
    return resources.files("pydiggs.findings").joinpath(name).read_bytes()


SCHEMA_1_0 = json.loads(_packaged("1.0"))
SCHEMA_1_1 = json.loads(_packaged("1.1"))
V10 = jsonschema.Draft202012Validator(SCHEMA_1_0)
V11 = jsonschema.Draft202012Validator(SCHEMA_1_1)


def _as_1_0(document: dict) -> dict:
    """``document`` with the two fields that name the schema version set back to 1.0."""
    return {**document, "$schema": URL.format("1.0"), "schema_version": "1.0"}


def _document(report: Report) -> dict:
    return json.loads(report.to_json())


def _ags4_report(**changes) -> Report:
    fields = {
        "path": "site.ags",
        "sha256": "b" * 64,
        "diggs_version": None,
        "steps": {Step.AGS4: "failed"},
        "findings": (
            Finding(
                step="ags4",
                rule_id="ags4.rule-4",
                severity="error",
                message="A DATA row has 1 values where the HEADING row has 2 headings.",
                line=6,
                path="GEOL/12/GEOL_LEG",
            ),
            Finding(
                step="convert",
                rule_id="convert.unmapped-group",
                severity="warning",
                message="Group XXXX has no mapping.",
                path="XXXX",
            ),
        ),
        "tool_version": "1.9.0",
        "format": "ags4",
    }
    return Report(**{**fields, **changes})


# --- the 1.0 file is frozen ---------------------------------------------------------


def test_the_1_0_schema_file_is_byte_identical_and_still_packaged():
    assert hashlib.sha256(_packaged("1.0")).hexdigest() == SHA256_1_0
    assert SCHEMA_1_0["$id"] == URL.format("1.0")
    assert (ROOT / "js" / "schemas" / "diggs-findings-1.0.schema.json").read_bytes() == _packaged(
        "1.0"
    )


def test_both_schemas_are_published_with_the_docs_and_listed_as_local_data():
    hooks = (ROOT / "scripts" / "mkdocs_hooks.py").read_text("utf-8")
    provenance = (ROOT / "src" / "pydiggs" / "PROVENANCE.toml").read_text("utf-8")
    for version in ("1.0", "1.1"):
        name = f"diggs-findings-{version}.schema.json"
        assert name in hooks
        assert f'[files."findings/{name}"]' in provenance
        assert (ROOT / "js" / "schemas" / name).read_bytes() == _packaged(version)


# --- what 1.1 is --------------------------------------------------------------------


def test_1_1_is_a_valid_schema_with_the_published_id_and_is_what_pydiggs_writes():
    jsonschema.Draft202012Validator.check_schema(SCHEMA_1_1)
    assert SCHEMA_1_1["$id"] == URL.format("1.1") == pydiggs.FINDINGS_SCHEMA_URL
    assert SCHEMA_1_1["title"] == "diggs-findings 1.1"
    assert pydiggs.FINDINGS_SCHEMA_VERSION == "1.1"
    assert pydiggs.findings_schema() == SCHEMA_1_1
    assert _json.SCHEMA_RESOURCE == "diggs-findings-1.1.schema.json"
    assert pydiggs.version_info().findings_schema_version == "1.1"
    assert pydiggs.version_info().findings_schema_url == URL.format("1.1")


def _without_text(node):
    """``node`` without its descriptions and titles (they are not the contract)."""
    if isinstance(node, dict):
        return {k: _without_text(v) for k, v in node.items() if k not in ("description", "title")}
    if isinstance(node, list):
        return [_without_text(v) for v in node]
    return node


def test_1_1_differs_from_1_0_by_exactly_the_three_additions():
    older = _without_text(copy.deepcopy(SCHEMA_1_0))
    newer = _without_text(copy.deepcopy(SCHEMA_1_1))

    # 1. the two steps, in the enum and in the rule-id pattern
    steps = newer["$defs"]["step"]["enum"]
    assert steps[-2:] == NEW_STEPS
    del steps[-2:]
    finding = newer["$defs"]["finding"]["properties"]
    assert "|context|ags4|convert)" in finding["rule_id"]["pattern"]
    finding["rule_id"]["pattern"] = finding["rule_id"]["pattern"].replace("|ags4|convert", "")

    # 2. the optional location.path
    location = finding["location"]
    assert location["properties"].pop("path") == {
        "type": "string",
        "minLength": 1,
        "maxLength": 1024,
    }
    assert "path" not in location["required"]

    # 3. the optional files[].format, null diggs_version for an AGS4 file
    file = newer["$defs"]["file"]
    assert file["properties"].pop("format") == {"enum": ["diggs", "ags4"]}
    assert "format" not in file["required"]
    assert file["allOf"].pop() == {
        "if": {"required": ["format"], "properties": {"format": {"const": "ags4"}}},
        "then": {"properties": {"diggs_version": {"type": "null"}}},
    }

    # and the identity: its own $id, and both $schema URLs accepted
    assert newer["properties"]["$schema"] == {"enum": [URL.format("1.1"), URL.format("1.0")]}
    newer["properties"]["$schema"] = {"const": URL.format("1.0")}
    newer["$id"] = URL.format("1.0")
    assert newer == older


def test_the_step_enum_is_the_python_enum_and_the_new_steps_are_never_validated():
    assert SCHEMA_1_1["$defs"]["step"]["enum"] == [s.value for s in Step]
    assert [Step.AGS4.value, Step.CONVERT.value] == NEW_STEPS
    assert Step.CONVERT.order > Step.AGS4.order > Step.CONTEXT.order
    assert not {Step.AGS4, Step.CONVERT} & set(run.NATIVE_STEPS)
    with pytest.raises(ValueError, match="ags4"):
        validate_bytes(b"<Diggs/>", checks=["ags4"])


# --- every 1.0 document is a 1.1 document -------------------------------------------


@settings(deadline=None)
@given(st.lists(reports(), max_size=3), st.lists(unreadable_files(), max_size=2))
def test_every_1_0_document_validates_against_1_1(checked, unreadable):
    files = [r.to_dict() for r in checked] + [_json.unreadable_entry(u) for u in unreadable]
    document = _json.document(files, tool_version="1.8.0", reference_data={"schemas": "15ce278"})
    as_written = json.loads(_json.dumps(document))
    V11.validate(as_written)
    old = _as_1_0(as_written)
    V11.validate(old)  # a document a 1.0 writer made, steps and fields permitting
    uses_1_1 = any(
        "format" in entry
        or any(f["step"] in NEW_STEPS or "path" in f["location"] for f in entry["findings"])
        or any(s["step"] in NEW_STEPS for s in entry["steps"])
        for entry in files
    )
    assert V10.is_valid(old) is not uses_1_1


@pytest.mark.parametrize(
    "fixture",
    [
        "tests/test_files/2.6/No_Error_2_6.xml",
        "tests/test_files/2.6/Schema_Error_1-2_6.xml",
        "tests/test_files/Syntax_Error_1.xml",
        "tests/fixtures/findings/two_failing_boreholes.xml",
    ],
)
def test_a_validate_report_has_its_1_0_content_and_only_the_version_moved(fixture):
    document = _document(validate(ROOT / fixture))
    V11.validate(document)
    assert document["$schema"] == URL.format("1.1")
    assert document["schema_version"] == "1.1"
    entry = document["files"][0]
    assert "format" not in entry
    assert all("path" not in f["location"] for f in entry["findings"])
    V10.validate(_as_1_0(document))
    # The 1.0 schema pins $schema to its own URL, so the document as written is not 1.0.
    assert not V10.is_valid(document)


def test_model_notes_still_fit_the_1_0_finding_definition():
    model = pydiggs.read(ROOT / "tests/fixtures/findings/two_failing_boreholes.xml")
    note = jsonschema.Draft202012Validator(
        {"$ref": URL.format("1.0") + "#/$defs/finding"},
        registry=_registry(),
    )
    for entry in model.to_dict()["notes"]:
        note.validate(entry)


def _registry():
    import referencing

    return referencing.Registry().with_resource(
        SCHEMA_1_0["$id"], referencing.Resource.from_contents(SCHEMA_1_0)
    )


# --- the additions ------------------------------------------------------------------


def test_an_ags4_report_validates_against_1_1_only():
    document = _document(_ags4_report())
    V11.validate(document)
    assert not V10.is_valid(_as_1_0(document))
    entry = document["files"][0]
    assert list(entry)[:4] == ["path", "sha256", "format", "diggs_version"]
    assert entry["format"] == "ags4"
    assert entry["diggs_version"] is None
    assert [s["step"] for s in entry["steps"]] == ["ags4"]
    first = entry["findings"][0]
    assert first["location"] == {
        "line": 6,
        "column": None,
        "xpath": None,
        "gml_id": None,
        "path": "GEOL/12/GEOL_LEG",
    }
    assert entry["findings"][1]["rule_id"] == "convert.unmapped-group"


def test_the_new_fields_are_written_only_when_set():
    plain = Finding(step="schema", rule_id="schema.x", severity="info", message="")
    assert "path" not in _json.finding_entry(plain)["location"]
    report = Report(path="a.xml", sha256="a" * 64, diggs_version="2.6", steps={}, tool_version="1")
    assert report.format == "diggs"
    assert "format" not in report.to_dict()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["files"][0].update(format="ags3"),
        lambda d: d["files"][0].update(diggs_version="2.6"),
        lambda d: d["files"][0]["findings"][0]["location"].update(path=""),
        lambda d: d["files"][0]["findings"][0]["location"].update(path=None),
        lambda d: d["files"][0]["findings"][0]["location"].update(path="x" * 1025),
        lambda d: d["files"][0]["findings"][0].update(step="ags5"),
        lambda d: d["files"][0]["findings"][0].update(rule_id="ags5.rule-1"),
        lambda d: d.update({"$schema": URL.format("1.2")}),
    ],
)
def test_1_1_still_refuses_what_it_does_not_define(change):
    document = _document(_ags4_report())
    change(document)
    assert not V11.is_valid(document)


def test_the_python_model_enforces_the_same_rules():
    with pytest.raises(ValueError, match="ags4 report has no diggs_version"):
        _ags4_report(diggs_version="2.6")
    with pytest.raises(ValueError, match="unknown file format"):
        _ags4_report(format="ags3")
    for bad in ("", "x" * 1025):
        with pytest.raises(ValueError, match="path"):
            Finding(step="ags4", rule_id="ags4.rule-1", severity="error", message="", path=bad)
    with pytest.raises(TypeError, match="path"):
        Finding(step="ags4", rule_id="ags4.rule-1", severity="error", message="", path=3)
    with pytest.raises(ValueError, match="not a valid rule id"):
        Finding(step="ags4", rule_id="convert.x", severity="error", message="")


def test_path_takes_part_in_the_conformance_key_of_a_finding_with_no_xpath():
    def at(**where) -> Finding:
        return Finding(step="ags4", rule_id="ags4.rule-2b", severity="error", message="m", **where)

    geol, loca = at(path="GEOL"), at(path="LOCA")
    assert geol.conformance_key() != loca.conformance_key()
    assert geol.conformance_key() == ("ags4", "error", "path:GEOL", "ags4.rule-2b")
    assert at(path="GEOL/3", line=6).conformance_key()[2] == "path:GEOL/3;line:6"
    assert at(line=6).conformance_key()[2] == "line:6"
    assert at().conformance_key()[2] == "file"
    # An XML finding's key is unchanged, also if it has a path: the xpath wins.
    xml = {
        "step": "schema",
        "rule_id": "schema.cvc-elt.1",
        "severity": "error",
        "message": "m",
        "line": 4,
        "xpath": "/diggs:Diggs[1]/diggs:Borehole[2]",
    }
    expected = ("schema", "error", "xpath:/Diggs[1]/Borehole[2]", "schema.cvc-elt.1")
    assert Finding(**xml).conformance_key() == expected
    assert Finding(**xml, path="GEOL").conformance_key() == expected
    no_xpath = {k: v for k, v in xml.items() if k != "xpath"}
    assert Finding(**no_xpath).conformance_key()[2] == "line:4"
    # The SARIF fingerprints of the two AGS4 findings no longer collide.
    report = _ags4_report(findings=(geol, loca))
    sarif = _sarif.sarif_log([report], [], tool_version="1")
    prints = [r["partialFingerprints"] for r in sarif["runs"][0]["results"]]
    assert len({json.dumps(p, sort_keys=True) for p in prints}) == 2


def test_path_takes_part_in_equality_order_and_pickling():
    import pickle

    a = Finding(step="ags4", rule_id="ags4.rule-4", severity="error", message="m", line=3)
    b = Finding(
        step="ags4", rule_id="ags4.rule-4", severity="error", message="m", line=3, path="GEOL/2"
    )
    assert a != b
    assert a.sort_key() < b.sort_key()
    report = _ags4_report(findings=(b, a))
    assert report.findings == (a, b)
    again = pickle.loads(pickle.dumps(report))  # noqa: S301  # our own bytes
    assert again == report
    assert again.format == "ags4"
