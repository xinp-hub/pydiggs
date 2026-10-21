"""The diggs-findings 1.0 JSON document and its JSON Schema."""

from __future__ import annotations

import json
from importlib import resources

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pydiggs.findings import _json
from pydiggs.findings.model import Finding, Report, Step, UnreadableFile

from .strategies import findings, reports, unreadable_files

SCHEMA = json.loads(
    resources.files("pydiggs.findings").joinpath(_json.SCHEMA_RESOURCE).read_text("utf-8")
)
VALIDATOR = jsonschema.Draft202012Validator(
    SCHEMA, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
)


def _doc(files):
    return _json.document(files, tool_version="1.2.0", reference_data={"schemas": "15ce278"})


def test_schema_is_a_valid_2020_12_schema_with_the_published_id():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["$id"] == _json.SCHEMA_URL
    assert SCHEMA["properties"]["$schema"]["const"] == _json.SCHEMA_URL


def test_schema_enums_match_the_model():
    assert SCHEMA["$defs"]["step"]["enum"] == [s.value for s in Step]
    statuses = SCHEMA["$defs"]["file"]["properties"]["steps"]["items"]["properties"]["status"]
    assert statuses["enum"] == ["passed", "partial", "failed", "skipped", "incomplete"]
    from typing import get_args

    from pydiggs.findings.model import FileStatus, StepStatus, Verdict

    assert statuses["enum"] == list(get_args(StepStatus))
    file_props = SCHEMA["$defs"]["file"]["properties"]
    assert file_props["status"]["enum"] == [*get_args(FileStatus), "unreadable"]
    assert file_props["verdict"]["enum"] == list(get_args(Verdict))


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(reports(), st.booleans())
def test_every_report_validates_against_the_schema(report, absolute):
    doc = json.loads(report.to_json(absolute_paths=absolute))
    VALIDATOR.validate(doc)
    assert doc["files"][0] == report.to_dict(absolute_paths=absolute)


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(st.lists(reports(), max_size=3), st.lists(unreadable_files(), max_size=2))
def test_batch_documents_validate(checked, unreadable):
    files = [r.to_dict() for r in checked] + [_json.unreadable_entry(u) for u in unreadable]
    VALIDATOR.validate(json.loads(_json.dumps(_doc(files))))


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(findings())
def test_every_finding_entry_validates(finding):
    VALIDATOR.validate(_doc([_file_with(finding).to_dict()]))


def _file_with(finding: Finding) -> Report:
    return Report(
        path="a.xml",
        sha256="a" * 64,
        diggs_version="3.0.0",
        steps={finding.step: "failed"},
        findings=(finding,),
        tool_version="1.2.0",
    )


def test_document_shape_and_field_order():
    finding = Finding(
        step="dictionary",
        rule_id="dictionary.5",
        severity="error",
        message='Code "x" is not found.',
        line=3,
        xpath="/diggs:Diggs[1]",
        gml_id="d1",
    )
    doc = json.loads(_file_with(finding).to_json())
    assert list(doc) == ["$schema", "format", "schema_version", "tool", "files"]
    assert doc["format"] == "diggs-findings"
    assert doc["schema_version"] == "1.0"
    entry = doc["files"][0]
    assert list(entry) == [
        "path",
        "sha256",
        "diggs_version",
        "status",
        "verdict",
        "steps",
        "counts",
        "findings",
    ]
    assert entry["findings"] == [
        {
            "layer": "native",
            "step": "dictionary",
            "rule_id": "dictionary.5",
            "severity": "error",
            "message": 'Code "x" is not found.',
            "location": {"line": 3, "column": None, "xpath": "/diggs:Diggs[1]", "gml_id": "d1"},
        }
    ]


def test_snippet_key_present_only_when_set():
    with_snippet = Finding(
        step="schema", rule_id="schema.x", severity="info", message="", source_snippet="<a/>"
    )
    assert _json.finding_entry(with_snippet)["source_snippet"] == "<a/>"
    without = Finding(step="schema", rule_id="schema.x", severity="info", message="")
    assert "source_snippet" not in _json.finding_entry(without)


def test_absolute_path_written_only_on_request():
    report = Report(
        path="a.xml",
        absolute_path="/home/me/a.xml",
        sha256="a" * 64,
        diggs_version=None,
        steps={},
        tool_version="1",
    )
    assert report.to_dict()["path"] == "a.xml"
    assert "/home/me" not in report.to_json()
    assert report.to_dict(absolute_paths=True)["path"] == "/home/me/a.xml"
    item = UnreadableFile(path="b.xml", error="gone", absolute_path="/home/me/b.xml")
    assert _json.unreadable_entry(item)["path"] == "b.xml"
    assert _json.unreadable_entry(item, absolute_paths=True)["path"] == "/home/me/b.xml"


def test_unreadable_entry_rules_are_enforced_by_the_schema():
    bad = _json.unreadable_entry(UnreadableFile(path="b.xml", error="gone"))
    del bad["error"]
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(_doc([bad]))
    checked = _file_with(Finding(step="schema", rule_id="schema.x", severity="info", message=""))
    entry = checked.to_dict()
    entry["error"] = "should not be here"
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(_doc([entry]))


def test_dumps_is_deterministic_and_indent_configurable():
    report = _file_with(Finding(step="schema", rule_id="schema.x", severity="info", message="é"))
    assert report.to_json() == report.to_json()
    assert "é" in report.to_json()
    assert "\n" not in report.to_json(indent=None)
