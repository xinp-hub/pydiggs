"""``pydiggs validate``: formats, batch inputs and exit codes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import jsonschema
import pytest

from pydiggs.cli import main
from pydiggs.findings import _json
from pydiggs.findings._cli import expand_inputs

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"
SYNTAX_ERROR = ROOT / "tests" / "test_files" / "Syntax_Error_1.xml"
FINDINGS_SCHEMA = jsonschema.Draft202012Validator(
    json.loads(
        resources.files("pydiggs.findings").joinpath(_json.SCHEMA_RESOURCE).read_text("utf-8")
    )
)
SARIF = jsonschema.Draft4Validator(
    json.loads((ROOT / "tests/fixtures/sarif/sarif-schema-2.1.0.json").read_text("utf-8"))
)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for src, name in ((NO_ERROR, "ok.xml"), (SCHEMA_ERROR, "bad.xml")):
        shutil.copy(src, tmp_path / name)
    return tmp_path


def _run(capsys, *argv):
    code = main(["validate", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_text_output_and_exit_codes(work, capsys):
    code, out, err = _run(capsys, "ok.xml")
    assert (code, err) == (0, "")
    assert out.startswith("ok.xml: 0 error(s), 31 warning(s), 20 info\n")
    code, out, _ = _run(capsys, "bad.xml", "--checks", "schema")
    assert code == 1
    assert "bad.xml:12: error schema.cvc-complex-type.2.4: Element" in out
    assert _run(capsys, "ok.xml", "--fail-on", "warning")[0] == 1
    assert _run(capsys, "ok.xml", "--checks", "schema", "--fail-on", "warning")[0] == 0


def test_json_batch_is_schema_valid_sorted_and_relative(work, capsys):
    (work / "sub").mkdir()
    shutil.copy(SYNTAX_ERROR, work / "sub" / "syntax.xml")
    code, out, _ = _run(capsys, "--format", "json", ".", "missing.xml")
    assert code == 2  # an unreadable input outranks findings
    doc = json.loads(out)
    FINDINGS_SCHEMA.validate(doc)
    assert [f["path"] for f in doc["files"]] == [
        "bad.xml",
        "missing.xml",
        "ok.xml",
        "sub/syntax.xml",
    ]
    statuses = {f["path"]: f["status"] for f in doc["files"]}
    assert statuses["missing.xml"] == "unreadable"
    assert str(work) not in out
    syntax = doc["files"][3]["findings"][0]
    assert syntax["rule_id"] == "structure.well-formed"
    assert syntax["location"]["column"] == 52


def test_syntax_error_alone_exits_1(work, capsys):
    shutil.copy(SYNTAX_ERROR, work / "s.xml")
    assert _run(capsys, "s.xml", "--format", "json")[0] == 1


def test_sarif_output_validates_and_writes_to_file(work, capsys):
    code, out, _ = _run(capsys, "--format", "sarif", "--output", "out.sarif", "bad.xml")
    assert code == 1
    assert out == ""
    log = json.loads((work / "out.sarif").read_text("utf-8"))
    SARIF.validate(log)
    assert log["runs"][0]["artifacts"][0]["location"] == {
        "uri": "bad.xml",
        "uriBaseId": "SRCROOT",
    }


def test_absolute_paths_and_snippets_on_request(work, capsys):
    _, out, _ = _run(capsys, "--format", "json", "--absolute-paths", "--snippets", "bad.xml")
    entry = json.loads(out)["files"][0]
    assert entry["path"] == (work / "bad.xml").resolve().as_posix()
    assert entry["findings"][0]["source_snippet"].startswith("<")
    _, out, _ = _run(capsys, "--format", "json", "bad.xml")
    assert "source_snippet" not in out


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--format", "xml", "ok.xml"],
        ["--checks", "crs", "ok.xml"],
        ["--checks", ",", "ok.xml"],
        ["--diggs-version", "9", "ok.xml"],
    ],
    ids=["no-path", "format", "unknown-check", "empty-checks", "version"],
)
def test_usage_errors_exit_2(work, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        main(["validate", *argv])
    assert exc.value.code == 2


def test_no_inputs_and_broken_resources_exit_2(work, capsys):
    (work / "empty").mkdir()
    code, _, err = _run(capsys, "empty")
    assert code == 2
    assert "no XML files found" in err
    code, _, err = _run(capsys, "ok.xml", "--schema-path", "missing.xsd")
    assert code == 2
    assert "cannot read schema" in err


def test_output_refuses_symlinks(work, capsys):
    (work / "target").write_text("keep")
    (work / "link.json").symlink_to(work / "target")
    code, _, err = _run(capsys, "ok.xml", "--format", "json", "--output", "link.json")
    assert code == 2
    assert "symlink" in err
    assert (work / "target").read_text() == "keep"


def test_expand_inputs_recurses_sorted_without_symlinks_or_duplicates(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    for p in ("b/2.xml", "a/1.XML", "a/skip.txt", "top.xml"):
        (tmp_path / p).write_text("<x/>")
    (tmp_path / "a" / "link.xml").symlink_to(tmp_path / "top.xml")
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    found = expand_inputs([str(tmp_path), str(tmp_path / "top.xml")])
    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["top.xml", "a/1.XML", "b/2.xml"]


def test_help_mentions_validate_and_legacy_commands_still_work(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "pydiggs validate --help" in " ".join(capsys.readouterr().out.split())
    with pytest.raises(SystemExit):
        main(["validate", "--help"])
    help_text = capsys.readouterr().out
    for option in ("--format", "--fail-on", "--snippets", "--absolute-paths", "--allow-network"):
        assert option in help_text


def test_console_script_entry_point(tmp_path):
    out = subprocess.run(  # noqa: S603  # fixed argv
        [sys.executable, "-m", "pydiggs.cli", "validate", "--format", "json", str(NO_ERROR)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert out.returncode == 0
    assert json.loads(out.stdout)["files"][0]["path"] == "No_Error_2_6.xml"


@pytest.mark.slow  # each worker loads the bundled validators afresh
def test_timeout_runs_each_file_in_its_own_process(work, capsys):
    plain = _run(capsys, "--format", "json", "ok.xml", "bad.xml")
    isolated = _run(capsys, "--format", "json", "--timeout", "300", "ok.xml", "bad.xml")
    assert isolated == plain  # same report, exit code and output


def test_timeout_fails_closed_and_the_batch_goes_on(work, capsys):
    code, out, _ = _run(capsys, "--format", "json", "--timeout", "0.001", "ok.xml", "bad.xml")
    assert code == 1
    files = json.loads(out)["files"]
    assert [f["path"] for f in files] == ["bad.xml", "ok.xml"]
    for entry in files:
        (finding,) = entry["findings"]
        assert (finding["rule_id"], finding["severity"]) == ("structure.timeout", "error")
        assert {s["status"] for s in entry["steps"]} == {"incomplete"}
        assert entry["sha256"]


def test_timeout_reports_unreadable_files_and_rejects_bad_values(work, capsys):
    code, out, _ = _run(capsys, "--format", "json", "--timeout", "300", "missing.xml")
    assert code == 2
    assert json.loads(out)["files"][0]["status"] == "unreadable"
    with pytest.raises(SystemExit):
        main(["validate", "--timeout", "0", "ok.xml"])


def test_output_file_bytes_are_the_same_on_every_os(work, capsys):
    for fmt in ("json", "sarif", "text"):
        code, out, _ = _run(capsys, "--format", fmt, "bad.xml")
        assert _run(capsys, "--format", fmt, "--output", f"out.{fmt}", "bad.xml")[0] == code
        data = (work / f"out.{fmt}").read_bytes()
        assert b"\r\n" not in data
        assert data == out.replace("\r\n", "\n").encode("utf-8")
