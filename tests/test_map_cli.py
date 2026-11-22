"""``pydiggs map``: test, check, coverage and init, with their exit codes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _map_cli, cli, mapping

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "mapping"


@pytest.fixture
def export(tmp_path: Path) -> Path:
    """A copy of the worked example: the vendor's CSV export and its mapping."""
    folder = tmp_path / "export"
    folder.mkdir()
    for path in EXAMPLE.glob("*.csv"):
        shutil.copyfile(path, folder / path.name)
    shutil.copyfile(EXAMPLE / "demo.toml", tmp_path / "demo.toml")
    return folder


def run(*arguments: str | Path) -> int:
    return _map_cli.main([str(argument) for argument in arguments])


def test_map_is_a_pydiggs_command(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mapping_file = export.parent / "demo.toml"
    assert cli.main(["map", "check", str(export), "--mapping", str(mapping_file)]) == 0
    assert "tables.mapping-code-not-exact" in capsys.readouterr().out


def test_test_passes_on_the_worked_example_and_keeps_the_file(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = export.parent / "site.xml"
    code = run(
        "test", export, "--mapping", export.parent / "demo.toml", "-o", target, "--format", "json"
    )
    summary = json.loads(capsys.readouterr().out)
    assert code == 0, summary
    assert summary["ok"] is True
    assert summary.get("differences", []) == []
    assert pydiggs.validate(target).verdict == "valid"


def test_test_fails_when_a_value_cannot_be_carried(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spt = export / "spt.csv"
    spt.write_text(spt.read_text(encoding="utf-8").replace("BH01,1,", "BH99,1,", 1), "utf-8")
    assert run("test", export, "--mapping", export.parent / "demo.toml") == 1
    out = capsys.readouterr().out
    assert "ok: False" in out
    assert "tables.orphan-row" in out


def test_check_needs_no_writing_and_finds_a_bad_target(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = export.parent / "broken.toml"
    text = (export.parent / "demo.toml").read_text(encoding="utf-8")
    broken.write_text(text.replace('top = "top_m"', 'depth_top = "top_m"', 1), encoding="utf-8")
    assert run("check", export, "--mapping", broken) == 1
    assert "tables.mapping-unknown-target" in capsys.readouterr().out


def test_check_of_profile_tables_without_a_mapping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "profile"
    folder.mkdir()
    (folder / "features.csv").write_text(
        "key,name,x,y,crs,depth_uom\nBH-1,BH-1,1,2,http://www.opengis.net/def/crs/EPSG/0/27700,m\n",
        encoding="utf-8",
    )
    assert run("check", folder, "--fail-on", "error") == 0
    capsys.readouterr()
    # A table name that is not the profile's, without a mapping, is a usage error.
    (folder / "holes.csv").write_text("hole_id\nBH-1\n", encoding="utf-8")
    assert run("check", folder) == 2
    assert "not a table of the profile" in capsys.readouterr().err


def test_coverage_reports_what_is_read(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run("coverage", export, "--mapping", export.parent / "demo.toml", "--format", "json")
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    text = json.dumps(summary)
    assert "100.0" in text  # every source column of the example is read


def test_init_writes_a_mapping_that_loads_and_does_not_replace_one(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = export.parent / "start.toml"
    assert run("init", export, "-o", target) == 0
    loaded = mapping.load(target.read_text(encoding="utf-8"))
    assert loaded.method == "generated"
    assert {table.source for table in loaded.tables} <= {p.stem for p in export.glob("*.csv")}
    capsys.readouterr()
    assert run("init", export, "-o", target) == 2
    assert "--force" in capsys.readouterr().err
    assert run("init", export, "-o", target, "--force") == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["test", "no-such-folder"],
        ["check", "no-such-folder"],
        ["coverage", "export", "--mapping", "no-such-file.toml"],
    ],
)
def test_an_input_that_cannot_be_read_is_exit_2(
    arguments: list[str],
    export: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(export.parent)
    assert run(*arguments) == 2
    assert capsys.readouterr().err.startswith("pydiggs map: ")


def test_a_mapping_that_is_not_one_is_exit_2(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = export.parent / "bad.toml"
    bad.write_text('mapping_format = "9.0"\n', encoding="utf-8")
    assert run("check", export, "--mapping", bad) == 2
    assert "pydiggs map: " in capsys.readouterr().err
