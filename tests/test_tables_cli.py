"""``pydiggs tables``: schema, to-diggs and from-diggs, with their exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _tables_cli, tables

FEATURES = (
    "key,name,x,y,crs,depth_uom\nBH-1,BH-1,1,2,http://www.opengis.net/def/crs/EPSG/0/27700,m\n"
)
LAYERS = "key,feature,top,base,description\nL1,BH-1,0,1.5,CLAY\n"
DOCUMENT = "creation_date\n2026-11-21\n"
PROJECTS = "key,name\nP1,Riverside\n"
OBSERVATIONS = "key,feature\nLOG,BH-1\n"
PARAMS = "node,name,value\nLOG,lithologyClassificationType,USCS\n"


def package(folder: Path, **files: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / f"{name}.csv").write_text(text, encoding="utf-8")
    return folder


def complete(folder: Path) -> Path:
    layers = LAYERS.replace("key,feature", "key,observation,feature").replace(
        "L1,BH-1", "L1,LOG,BH-1"
    )
    return package(
        folder,
        document=DOCUMENT,
        projects=PROJECTS,
        features=FEATURES.replace("key,name", "key,project,name").replace(
            "BH-1,BH-1", "BH-1,P1,BH-1"
        ),
        observations=OBSERVATIONS,
        layers=layers,
        params=PARAMS,
    )


def test_schema_prints_the_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert _tables_cli.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out) == tables.profile()


def test_schema_writes_a_file_and_does_not_replace_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "profile.json"
    assert _tables_cli.main(["schema", "-o", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["name"] == "diggs-tables"
    assert _tables_cli.main(["schema", "-o", str(target)]) == 2
    assert "--force" in capsys.readouterr().err
    assert _tables_cli.main(["schema", "-o", str(target), "--force"]) == 0


def test_to_diggs_writes_a_valid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = complete(tmp_path / "export")
    target = tmp_path / "out.xml"
    code = _tables_cli.main(["to-diggs", str(source), "-o", str(target), "--format", "json"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0, summary
    assert summary["lossless"] is True
    assert (summary["features"], summary["layers"]) == (1, 1)
    assert summary["findings"] == []
    assert pydiggs.validate(target).verdict == "valid"


def test_to_diggs_names_what_was_left_out_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = package(tmp_path / "export", features=FEATURES, layers=LAYERS.replace("BH-1", "BH-9"))
    target = tmp_path / "out.xml"
    assert _tables_cli.main(["to-diggs", str(source), "-o", str(target)]) == 1
    out = capsys.readouterr().out
    assert "tables.orphan-row" in out
    assert "layers/1/feature" in out
    assert pydiggs.validate(target).verdict == "valid"  # the rest is still written


def test_fail_on_loss_counts_assumed_values(tmp_path: Path) -> None:
    source = package(tmp_path / "export", features=FEATURES)
    assert _tables_cli.main(["to-diggs", str(source), "-o", str(tmp_path / "a.xml")]) == 0
    arguments = ["to-diggs", str(source), "-o", str(tmp_path / "b.xml"), "--fail-on", "loss"]
    assert _tables_cli.main(arguments) == 1


def test_from_diggs_writes_tables_that_go_back_to_the_same_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.xml"
    assert _tables_cli.main(["to-diggs", str(complete(tmp_path / "a")), "-o", str(first)]) == 0
    capsys.readouterr()  # the summary of the first command
    folder = tmp_path / "load"
    assert _tables_cli.main(["from-diggs", str(first), "-o", str(folder), "--format", "json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tables"]["features"] == 1
    assert (folder / "plan.json").is_file()
    assert (folder / "datapackage.json").is_file()
    second = tmp_path / "second.xml"
    assert _tables_cli.main(["to-diggs", str(folder), "-o", str(second)]) == 0
    assert second.read_bytes() == first.read_bytes()
    # A folder that already holds files is not written into without --force.
    assert _tables_cli.main(["from-diggs", str(first), "-o", str(folder)]) == 2
    assert "--force" in capsys.readouterr().err
    assert _tables_cli.main(["from-diggs", str(first), "-o", str(folder), "--force"]) == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["to-diggs", "no-such-folder", "-o", "out.xml"],
        ["from-diggs", "no-such-file.xml", "-o", "load"],
    ],
)
def test_an_input_that_cannot_be_read_is_exit_2(
    arguments: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _tables_cli.main(arguments) == 2
    assert capsys.readouterr().err.startswith("pydiggs tables: ")


def test_a_file_that_is_not_diggs_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "not.xml"
    source.write_text("<html/>", encoding="utf-8")
    assert _tables_cli.main(["from-diggs", str(source), "-o", str(tmp_path / "load")]) == 2
    assert not (tmp_path / "load").exists()
    assert "pydiggs tables: " in capsys.readouterr().err
