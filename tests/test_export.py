"""Whole documents: Report.to_document, findings_document/json/schema, version_info."""

from __future__ import annotations

import json
from importlib import metadata, resources
from pathlib import Path

import jsonschema
import pytest

import pydiggs
from pydiggs import (
    FINDINGS_SCHEMA_URL,
    FINDINGS_SCHEMA_VERSION,
    ResourceError,
    VersionInfo,
    findings_document,
    findings_json,
    findings_schema,
    validate,
    validate_bytes,
    version_info,
)
from pydiggs.cli import main
from pydiggs.findings import _json, export
from pydiggs.findings.run import reference_data

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "tests" / "test_files"
SCHEMA = jsonschema.Draft202012Validator(findings_schema())
LINE_SEPARATOR = chr(0x2028)


@pytest.fixture
def reports():
    return [
        validate_bytes((FILES / "2.6" / "Schema_Error_1-2_6.xml").read_bytes(), name="z.xml"),
        validate_bytes((FILES / "Syntax_Error_1.xml").read_bytes(), name="a.xml"),
        validate_bytes(b"<x/>", name="m.xml"),
    ]


def test_constants_are_the_document_constants():
    assert FINDINGS_SCHEMA_VERSION == _json.SCHEMA_VERSION == "1.1"
    assert FINDINGS_SCHEMA_URL == _json.SCHEMA_URL


def test_findings_schema_is_the_packaged_file_and_a_fresh_copy():
    packaged = resources.files("pydiggs.findings").joinpath(_json.SCHEMA_RESOURCE)
    assert findings_schema() == json.loads(packaged.read_text("utf-8"))
    schema = findings_schema()
    schema["$id"] = "changed"
    assert findings_schema()["$id"] == FINDINGS_SCHEMA_URL
    jsonschema.Draft202012Validator.check_schema(schema)


def test_a_broken_packaged_schema_is_a_resource_error(monkeypatch):
    monkeypatch.setattr(export, "_schema_text", lambda: "{not json")
    with pytest.raises(ResourceError, match="broken"):
        findings_schema()


def test_a_missing_packaged_schema_is_a_resource_error(monkeypatch):
    export._schema_text.cache_clear()
    monkeypatch.setattr(export._json, "SCHEMA_RESOURCE", "missing.json")
    try:
        with pytest.raises(ResourceError, match="missing"):
            findings_schema()
    finally:
        export._schema_text.cache_clear()


def test_to_document_dumps_to_exactly_to_json(reports):
    for report in reports:
        for absolute in (False, True):
            doc = report.to_document(absolute_paths=absolute)
            assert _json.dumps(doc) == report.to_json(absolute_paths=absolute)
            assert _json.dumps(doc, indent=None) == report.to_json(None, absolute_paths=absolute)
            assert doc["files"] == [report.to_dict(absolute_paths=absolute)]
            SCHEMA.validate(doc)


def test_to_document_names_the_version_that_made_the_report():
    report = validate_bytes(b"<x/>")
    old = type(report)(**{**_fields(report), "tool_version": "0.9"})
    assert old.to_document()["tool"]["version"] == "0.9"


def _fields(report):
    return {
        "path": report.path,
        "sha256": report.sha256,
        "diggs_version": report.diggs_version,
        "steps": dict(report.steps),
        "findings": report.findings,
        "tool_version": report.tool_version,
        "reference_data": dict(report.reference_data),
        "absolute_path": report.absolute_path,
        "status": report.status,
    }


def test_findings_document_sorts_by_path_and_is_schema_valid(reports):
    doc = findings_document(reports)
    assert [f["path"] for f in doc["files"]] == ["a.xml", "m.xml", "z.xml"]
    assert doc["tool"] == {
        "name": "pydiggs",
        "version": metadata.version("pydiggs"),
        "reference_data": reference_data(),
    }
    SCHEMA.validate(doc)
    assert findings_document(iter(reports)) == doc  # any iterable, consumed once


def test_findings_document_of_one_report_is_its_to_document(reports):
    for report in reports:
        assert findings_document([report]) == report.to_document()


def test_an_empty_document_is_valid():
    doc = findings_document([])
    assert doc["files"] == []
    SCHEMA.validate(doc)


def test_findings_json_is_deterministic_and_one_line_without_indent(reports):
    text = findings_json(reports, indent=None)
    assert "\n" not in text
    assert json.loads(text) == findings_document(reports)
    assert findings_json(reversed(reports)) == findings_json(reports)
    assert findings_json(reports) == _json.dumps(findings_document(reports))


def test_findings_json_keeps_non_ascii_and_escapes_line_separators():
    report = validate_bytes(b"<x/>", name="caf\u00e9" + LINE_SEPARATOR + ".xml")
    text = findings_json([report], indent=None)
    assert "caf\u00e9" in text
    assert LINE_SEPARATOR not in text  # escaped by validate_bytes, never raw in NDJSON


def test_absolute_paths_fall_back_to_path_for_bytes(reports, tmp_path):
    path = tmp_path / "f.xml"
    path.write_bytes(b"<x/>")
    by_path = validate(path)
    doc = findings_document([*reports, by_path], absolute_paths=True)
    shown = {f["path"] for f in doc["files"]}
    assert by_path.absolute_path in shown
    assert {"a.xml", "m.xml", "z.xml"} <= shown


# --- the CLI's batch JSON is unchanged ---------------------------------------------


def _legacy_render(reports, unreadable, *, absolute_paths):
    """``_cli._render`` for ``--format json`` exactly as pydiggs 1.2.0 wrote it."""
    entries = [r.to_dict(absolute_paths=absolute_paths) for r in reports]
    entries += [_json.unreadable_entry(u, absolute_paths=absolute_paths) for u in unreadable]
    entries.sort(key=lambda e: str(e["path"]))
    doc = _json.document(entries, tool_version=pydiggs.__version__, reference_data=reference_data())
    return _json.dumps(doc) + "\n"


@pytest.mark.parametrize("absolute", [False, True])
def test_cli_json_for_a_mixed_set_is_byte_identical_to_1_2_0(
    tmp_path, monkeypatch, capsys, absolute
):
    from pydiggs.findings import _cli

    monkeypatch.chdir(tmp_path)
    for name, source in [
        ("b.xml", FILES / "2.6" / "Schema_Error_1-2_6.xml"),
        ("a.xml", FILES / "2.6" / "No_Error_2_6.xml"),
        ("sub/c.xml", FILES / "Syntax_Error_1.xml"),
    ]:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(source.read_bytes())
    argv = ["--format", "json", ".", "missing.xml", "aa-missing.xml"]
    argv += ["--absolute-paths"] if absolute else []
    assert main(["validate", *argv]) == 2  # unreadable inputs
    out = capsys.readouterr().out
    args = _cli._options(_cli.build_parser(), argv)
    reports, unreadable = _cli._run(args, _cli.expand_inputs(args.paths))
    assert len(unreadable) == 2
    assert out == _legacy_render(reports, unreadable, absolute_paths=absolute)
    if not absolute:  # the readable part is what findings_json() gives for the reports
        readable = json.loads(out)
        readable["files"] = [f for f in readable["files"] if f["status"] != "unreadable"]
        assert _json.dumps(readable) == findings_json(reports)


# --- version_info ------------------------------------------------------------------


def test_version_info_describes_the_running_package():
    info = version_info()
    assert isinstance(info, VersionInfo)
    assert info.pydiggs_version == pydiggs.__version__ == metadata.version("pydiggs")
    assert info.findings_schema_version == FINDINGS_SCHEMA_VERSION
    assert info.findings_schema_url == FINDINGS_SCHEMA_URL
    assert info.diggs_versions == ("2.5.a", "2.6", "3.0.0", "3.1-dev")
    assert dict(info.reference_data) == reference_data()
    data = info.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert list(data) == [
        "pydiggs_version",
        "findings_schema_version",
        "findings_schema_url",
        "diggs_versions",
        "reference_data",
    ]


def test_version_info_is_immutable_and_comparable():
    info = version_info()
    assert info == version_info()
    with pytest.raises(AttributeError):
        info.pydiggs_version = "0"  # type: ignore[misc]
    with pytest.raises(TypeError):
        info.reference_data["x"] = "y"  # type: ignore[index]
    copy = VersionInfo(**{**info.to_dict(), "diggs_versions": ["2.6"]})
    assert copy.diggs_versions == ("2.6",)


def test_version_info_pickles_and_deep_copies():
    import copy
    import pickle

    info = pydiggs.version_info()
    assert pickle.loads(pickle.dumps(info)) == info  # noqa: S301  # our own bytes
    assert copy.deepcopy(info) == info
