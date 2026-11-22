"""Regressions from the 1.16.0 pre-release review of the tables profile and mapping files."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

import pydiggs
from pydiggs import ModelWriteError, TablesError, _tables_cli, mapping, tables
from pydiggs._writer import _emit

OFFICIAL = Path(__file__).resolve().parent / "fixtures" / "official" / "3.0"
CRS = "http://www.opengis.net/def/crs/EPSG/0/27700"


def borehole(**extra: Any) -> dict[str, Any]:
    return {"key": "BH-1", "name": "BH-1", "x": 1, "y": 2, "crs": CRS, "depth_uom": "m", **extra}


def spt(*values: Any) -> dict[str, list[dict[str, Any]]]:
    """One SPT test whose rows sit at depths 1, 2, 3 … with these blow counts."""
    return {
        "features": [borehole()],
        "measurements": [{"key": "T", "feature": "BH-1", "procedure": "DrivenPenetrationTest"}],
        "params": [
            {"node": "T", "scope": "procedure", "name": "penetrationTestType", "value": "SPT"}
        ],
        "results_DrivenPenetrationTest": [
            {"measurement": "T", "row": i, "position": i + 1, "n_value": value}
            for i, value in enumerate(values)
        ],
    }


def rules(findings: Any) -> list[tuple[str, str | None]]:
    return [(f.rule_id, f.path) for f in findings]


# --- 1. a folder pyDIGGS wrote can always be read back; bad input is exit 2 ---------------


def test_an_official_example_goes_to_csv_and_back_through_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "out"
    source = OFFICIAL / "Piezometer_Example.xml"
    assert _tables_cli.main(["from-diggs", str(source), "-o", str(folder)]) == 0
    longest = max(len(line) for line in (folder / "extra.csv").read_text("utf-8").splitlines())
    assert longest > 131_072  # over the csv module's default field limit
    code = _tables_cli.main(["to-diggs", str(folder), "-o", str(tmp_path / "x.xml")])
    assert code in (0, 1), capsys.readouterr().err  # findings, never a crash
    assert pydiggs.validate(tmp_path / "x.xml").verdict.startswith("valid")


def test_reading_a_long_cell_leaves_the_csv_limit_as_it_was(tmp_path: Path) -> None:
    import csv

    before = csv.field_size_limit()
    (tmp_path / "extra.csv").write_text(
        "node,seq,path,content\nBH-1,0,x," + "9" * 200_000 + "\n", encoding="utf-8"
    )
    (row,) = tables.read_package(tmp_path)["extra"]
    assert len(row["content"]) == 200_000
    assert csv.field_size_limit() == before


@pytest.mark.parametrize(
    ("name", "content", "says"),
    [
        ("features.csv", "key,name\nCaf\xe9,x\n".encode("cp1252"), "not UTF-8"),
        ("features.csv", b'key,name\n"BH-1,x\n', "not a readable CSV"),
        ("datapackage.json", b"[" * 200_000, "nested too deeply"),
    ],
)
def test_input_that_cannot_be_read_is_a_tables_error_and_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, content: bytes, says: str
) -> None:
    (tmp_path / name).write_bytes(content)
    with pytest.raises(TablesError, match=says):
        tables.to_model(tables.read_package(tmp_path))
    assert _tables_cli.main(["to-diggs", str(tmp_path), "-o", str(tmp_path / "x.xml")]) == 2
    assert capsys.readouterr().err.startswith("pydiggs tables: ")


# --- 2. a finding names the row as the input numbers it ----------------------------------


def test_a_blank_row_does_not_shift_the_rows_findings_name() -> None:
    data = {
        "features": [
            borehole(),
            {"key": "", "name": None, "x": float("nan")},
            borehole(key="BH-2", name="BH-2", total_depth="abc"),
        ]
    }
    expected = ("tables.value-unparsed", "features/3/total_depth")
    assert expected in rules(tables.to_model(data).findings)
    assert expected in rules(tables.check(data))


# --- 3. a mapping never lets a depth fall back to metres ----------------------------------


def test_an_unknown_depth_unit_through_a_mapping_refuses_the_feature() -> None:
    loaded = mapping.load(
        """
mapping_format = "1.0"
name = "t"
version = "1"
method = "manual"
profile_version = "1.0-beta.1"
[[tables]]
source = "hole"
target = "features"
columns = { key = "id", depth_uom = { from = "unit", units = true } }
"""
    )
    applied = loaded.apply({"hole": [{"id": "B1", "unit": "fathoms"}]})
    made = tables.to_model(applied.tables)
    assert ("tables.unknown-unit", "features/1/depth_uom") in rules(made.findings)
    assert made.model.features == ()


# --- 4. no number is rounded silently --------------------------------------------------------


@pytest.mark.parametrize("value", ["12345678901234567", "0.1234567890123456789", 10**400])
def test_a_number_a_float_cannot_hold_is_reported_not_rounded(value: Any) -> None:
    made = tables.to_model(spt(5, value))
    found = [f for f in made.findings if f.rule_id == "tables.value-unparsed"]
    assert found, rules(made.findings)
    assert "12345678901234568" not in pydiggs.write_bytes(made.model).decode()


def test_trailing_zeros_are_the_same_number() -> None:
    checked = tables.round_trip({**spt("5.0", "7"), "document": [{"creation_date": "2026-11-22"}]})
    assert checked.differences == ()


# --- 5. a dropped result row is named, and the round trip lines rows up by depth ----------


def test_an_empty_positioned_result_row_is_named_and_does_not_shift_the_rest() -> None:
    checked = tables.round_trip(spt(5, "", 7))
    assert ("tables.not-carried", "results_DrivenPenetrationTest/2") in rules(checked.findings)
    assert checked.differences == ()


# --- 6. a character XML cannot hold is a finding, not a failed export ----------------------


@pytest.mark.parametrize("bad", ["a\x01b", "a￾b", "a\ud800b"])
def test_a_character_xml_cannot_hold_is_named_and_the_rest_is_written(bad: str) -> None:
    made = tables.to_model({"features": [borehole(description=bad)]})
    assert ("tables.value-unparsed", "features/1/description") in rules(made.findings)
    assert pydiggs.validate_bytes(pydiggs.write_bytes(made.model)).verdict == "valid"


def test_the_writer_names_the_record_whose_text_xml_cannot_hold() -> None:
    model = tables.to_model({"features": [borehole()]}).model
    broken = dataclasses.replace(model.features[0], description="a\x01b")
    with pytest.raises(ModelWriteError, match="Borehole 'BH-1'"):
        pydiggs.write_bytes(dataclasses.replace(model, features=(broken,)))


# --- 7. a link cannot read outside the folder --------------------------------------------


def test_a_csv_link_that_leaves_the_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("key,name\nBH-1,x\n", encoding="utf-8")
    folder = tmp_path / "package"
    folder.mkdir()
    (folder / "features.csv").symlink_to(outside)
    with pytest.raises(TablesError, match="leaves the folder"):
        tables.read_package(folder)


# --- 8. params on a mapping entry with no key are named ------------------------------------


def test_params_on_an_entry_with_no_key_are_named() -> None:
    loaded = mapping.load(
        """
mapping_format = "1.0"
name = "t"
version = "1"
method = "manual"
profile_version = "1.0-beta.1"
[[tables]]
source = "s"
target = "results_DrivenPenetrationTest"
columns = { measurement = "m", n_value = "n" }
[[tables.params]]
name = "penetrationTestType"
value = "SPT"
"""
    )
    applied = loaded.apply({"s": [{"m": "T", "n": "5"}]})
    assert [f.rule_id for f in applied.findings] == ["tables.mapping-unknown-target"]


# --- 9. descriptor details ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("resources", "says"),
    [
        ([{"name": "features", "path": ["features.csv"]}], "must be one file name"),
        (
            [{"name": "features", "path": "a.csv"}, {"name": "features", "path": "b.csv"}],
            "listed twice",
        ),
    ],
)
def test_a_descriptor_that_would_lose_rows_is_refused(
    tmp_path: Path, resources: list[dict[str, Any]], says: str
) -> None:
    for name in ("features.csv", "a.csv", "b.csv"):
        (tmp_path / name).write_text("key\nBH-1\n", encoding="utf-8")
    (tmp_path / "datapackage.json").write_text(json.dumps({"resources": resources}), "utf-8")
    with pytest.raises(TablesError, match=says):
        tables.read_package(tmp_path)


# --- 10. a value is never taken for the count ----------------------------------------------


def test_a_value_spelled_like_the_count_marker_is_shown_as_given() -> None:
    made = tables.to_model({"features": [borehole(depth_uom="{times}")]})
    (finding,) = [f for f in made.findings if f.rule_id == "tables.unknown-unit"]
    assert "1 row" not in finding.message.split("measures", 1)[0]


# --- the size guard: nothing is written that cannot be read back ---------------------------


def test_the_writer_refuses_a_text_too_long_to_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tables.to_model(spt(*range(1, 30))).model  # written under the real limit
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", 40)
    with pytest.raises(ModelWriteError, match="over the 40 a DIGGS file can hold"):
        pydiggs.write_bytes(model)


def test_to_model_names_the_test_too_long_and_writes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", 40)
    data = spt(*range(1, 30))
    data["features"].append(borehole(key="BH-2", name="BH-2"))
    data["measurements"].append(
        {"key": "U", "feature": "BH-2", "procedure": "DrivenPenetrationTest", "depth": 3}
    )
    data["params"].append(
        {"node": "U", "scope": "procedure", "name": "penetrationTestType", "value": "SPT"}
    )
    data["results_DrivenPenetrationTest"].append({"measurement": "U", "n_value": 12})
    made = tables.to_model(data)
    (refused,) = [f for f in made.findings if f.rule_id == "tables.incomplete"]
    assert "split the data, by hole or by period" in refused.message
    assert [m.key for m in made.model.measurements] == ["U"]  # the rest is written
    pydiggs.write_bytes(made.model)


def test_a_text_just_under_the_limit_is_written_and_reads_back() -> None:
    text = "x" * 9
    assert _emit._bounded(text, 1, "w") == text
    assert _emit.MAX_TEXT_BYTES == 10_000_000
