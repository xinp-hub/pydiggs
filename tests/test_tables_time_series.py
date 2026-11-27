"""Time series, several result tables, empty result tables, and long tables written in parts.

The writer writes a ``Monitor`` and a ``MeasurementWhileDrilling`` as well as a ``Test``;
a result placed by time (``time`` or ``elapsed_time``); the result tables a procedure holds
(a dissipation time series); and a result table with no rows. A result table too long for
one element is written as consecutive measurements, which the tables read back as one.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
import datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import ModelWriteError, tables
from pydiggs._writer import _emit
from pydiggs.model import Column, Domain, Model, ResultTable

OFFICIAL = Path(__file__).resolve().parent / "fixtures" / "official" / "3.0"
CRS = "http://www.opengis.net/def/crs/EPSG/0/27700"
MWD = "https://diggsml.org/def/codes/DIGGS/0.1/mwd_properties.xml"
Tables = dict[str, list[dict[str, Any]]]


def _base() -> Tables:
    return {
        "document": [{"creation_date": "2026-11-22"}],
        "projects": [{"key": "P1", "name": "Riverside"}],
        "features": [
            {"key": "BH-1", "project": "P1", "x": 1, "y": 2, "crs": CRS, "depth_uom": "m"}
        ],
    }


def _times(count: int) -> list[str]:
    return [f"2024-11-0{1 + i // 1440}T{i // 60 % 24:02d}:{i % 60:02d}:00Z" for i in range(count)]


def monitor(count: int = 5, *, times: list[str] | None = None) -> Tables:
    """A water level series of a piezometer: one row per reading, placed by time."""
    stamps = times if times is not None else _times(count)
    return {
        **_base(),
        "measurements": [
            {
                "key": "WL-1",
                "feature": "BH-1",
                "element": "Monitor",
                "procedure": "WaterLevelMonitoring",
                "name": "Water levels",
            }
        ],
        "components": [
            {
                "key": "RP-1",
                "holder": "WL-1",
                "role": "referencePoints",
                "element": "ReferencePoint",
                "start": "2024-11-01",
            }
        ],
        "links": [
            {"source": "WL-1", "relation": "monitorLocation", "target": "BH-1"},
            {"source": "RP-1", "relation": "rpLocation", "target": "BH-1"},
        ],
        "result_columns": [
            {"procedure": "WaterLevelMonitoring", "column": "water_depth", "uom": "m"}
        ],
        "results_WaterLevelMonitoring": [
            {"measurement": "WL-1", "row": i, "time": stamp, "water_depth": 3 + i / 100}
            for i, stamp in enumerate(stamps)
        ],
    }


def mwd(count: int = 4) -> Tables:
    """A drilling log placed by the seconds elapsed since the start."""
    return {
        **_base(),
        "measurements": [
            {
                "key": "MWD-1",
                "feature": "BH-1",
                "element": "MeasurementWhileDrilling",
                "procedure": "MWDProcedure",
            }
        ],
        "result_columns": [
            {"procedure": "MWDProcedure", "column": "elapsed_time", "uom": "s"},
            {
                "procedure": "MWDProcedure",
                "column": "penetration_rate",
                "definition": f"{MWD}#penetration_rate",
                "uom": "m/s",
            },
            {
                "procedure": "MWDProcedure",
                "column": "event_new_rod",
                "definition": f"{MWD}#event_new_rod",
                "type": "boolean",
            },
        ],
        "results_MWDProcedure": [
            {
                "measurement": "MWD-1",
                "row": i,
                "elapsed_time": 2.5 * i,
                "penetration_rate": 0.01 * (i + 1),
                "event_new_rod": i == 2,
            }
            for i in range(count)
        ],
    }


def dissipation(count: int = 4) -> Tables:
    """A pore pressure dissipation test: a summary row, and its series in the procedure."""
    procedure = "PorePressureDissipationTest"
    return {
        **_base(),
        "measurements": [
            {"key": "PPD-1", "feature": "BH-1", "procedure": procedure, "depth": 12.5}
        ],
        "components": [
            {
                "key": "PPD-1-series",
                "holder": "PPD-1",
                "role": "dissipationTimeSeries",
                "element": "TemporalResult",
            }
        ],
        "result_columns": [
            {"procedure": procedure, "column": "u50", "uom": "kPa"},
            {"procedure": procedure, "column": "t50", "uom": "s"},
            {"procedure": procedure, "column": "elapsed_time", "uom": "s"},
            {"procedure": procedure, "column": "pore_pressure_u2", "uom": "kPa"},
        ],
        f"results_{procedure}": [
            {"measurement": "PPD-1", "result": 0, "row": 0, "u50": 471.8, "t50": 40.1},
            *(
                {
                    "measurement": "PPD-1",
                    "result": 1,
                    "row": i,
                    "elapsed_time": 5 * i,
                    "pore_pressure_u2": 850 - 10 * i,
                }
                for i in range(count)
            ),
        ],
    }


def triaxial() -> Tables:
    """A test with no result rows: its columns are named for it in ``result_columns``."""
    return {
        **_base(),
        "measurements": [
            {"key": "TX-1", "feature": "BH-1", "procedure": "TriaxialTest", "depth": 3}
        ],
        "result_columns": [
            {
                "procedure": "TriaxialTest",
                "measurement": "TX-1",
                "column": "undrained_shear_strength",
                "property": "shear_strength_undrained",
                "uom": "kPa",
            }
        ],
    }


def errors(xml: bytes) -> list[str]:
    return [f.message for f in pydiggs.validate_bytes(xml).findings if f.severity.rank > 1]


def written(data: Tables) -> bytes:
    made = tables.to_model(data)
    assert made.lossless, [f.message for f in made.findings]
    return pydiggs.write_bytes(made.model)


def same_results(data: Tables, back: tables.TableSet) -> None:
    for name, rows in data.items():
        if name.startswith("results_"):
            got = back.rows(name)
            assert len(got) == len(rows)
            for sent, row in zip(rows, got, strict=True):
                for column, value in sent.items():
                    if column != "measurement":
                        assert row[column] == value, (column, row)


# --- the writer's new shapes ----------------------------------------------------------------


def test_a_monitoring_series_is_written_by_time_and_reads_back() -> None:
    data = monitor()
    xml = written(data)
    assert errors(xml) == []
    assert xml == written(data)  # the same bytes every time
    model = pydiggs.read_bytes(xml)
    (series,) = model.measurements
    assert series.element == "Monitor"
    (table,) = series.results
    assert [c.name for c in table.domain.index] == ["time"]
    assert list(table.domain.index[0].values) == _times(5)
    back = tables.from_model(model)
    assert back.rows("measurements")[0]["element"] == "Monitor"
    same_results(data, back)
    assert not [r for r in back.rows("extra") if "results" in str(r["path"])]
    checked = tables.round_trip(data)
    assert checked.ok, (checked.differences, checked.findings)


def test_a_drilling_log_is_written_by_elapsed_time_in_its_unit() -> None:
    data = mwd()
    xml = written(data)
    assert errors(xml) == []
    assert b'<TimeIntervalList unit="second">' in xml
    (log,) = pydiggs.read_bytes(xml).measurements
    assert log.element == "MeasurementWhileDrilling"
    (index,) = log.results[0].domain.index
    assert (index.name, index.uom, index.values) == ("elapsed_time", "s", (0.0, 2.5, 5.0, 7.5))
    back = tables.from_model(pydiggs.read_bytes(xml))
    same_results(data, back)
    units = {r["column"]: r["uom"] for r in back.rows("result_columns")}
    assert units["elapsed_time"] == "s"
    assert tables.round_trip(data).ok


def test_a_test_with_a_series_in_its_procedure_has_two_result_tables() -> None:
    data = dissipation()
    xml = written(data)
    assert errors(xml) == []
    (test,) = pydiggs.read_bytes(xml).measurements
    assert [t.row_count for t in test.results] == [1, 4]
    assert test.results[0].location is not None  # the summary, at its depth
    assert test.results[1].domain.index[0].name == "elapsed_time"
    back = tables.from_model(pydiggs.read_bytes(xml))
    same_results(data, back)
    assert back.rows("measurements")[0]["depth"] == 12.5
    checked = tables.round_trip(data)
    assert checked.ok, (checked.differences, checked.findings)


def test_a_test_with_no_result_rows_is_written_with_an_empty_result_table() -> None:
    data = triaxial()
    xml = written(data)
    assert errors(xml) == []
    root = etree.fromstring(xml)
    (values,) = root.iter("{*}dataValues")
    assert values.text is None
    (test,) = pydiggs.read_bytes(xml).measurements
    (table,) = test.results
    assert table.row_count == 0
    assert [(c.key, c.uom) for c in table.columns] == [("shear_strength_undrained", "kPa")]
    (described,) = tables.from_model(pydiggs.read_bytes(xml)).rows("result_columns")
    assert (described["measurement"], described["uom"]) == ("TX-1", "kPa")
    assert tables.round_trip(data).ok


def test_a_monitor_with_no_readings_has_an_empty_list_of_times() -> None:
    data = monitor(0)
    data["result_columns"][0]["measurement"] = "WL-1"
    xml = written(data)
    assert errors(xml) == []
    assert b"<timePositionList/>" in xml
    (series,) = pydiggs.read_bytes(xml).measurements
    assert series.results[0].row_count == 0
    assert tables.round_trip(data).ok


def test_a_second_result_table_needs_a_result_object_in_the_procedure() -> None:
    data = dissipation()
    del data["components"]
    made = tables.to_model(data)
    (lost,) = [f for f in made.findings if f.rule_id == "tables.incomplete"]
    assert "result object" in lost.message
    assert made.model.measurements == ()


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"time": "2024-11-01 10:00"}, "hold a space"),
        ({"result": 2}, "without a gap"),
        ({"result": "x"}, "not numbers"),
        ({"position": 1.0}, "placed one way"),
    ],
)
def test_result_rows_that_cannot_be_placed_are_named(change: dict[str, Any], says: str) -> None:
    data = monitor(3)
    data["results_WaterLevelMonitoring"][1].update(change)
    made = tables.to_model(data)
    assert any(says in f.message for f in made.findings), [f.message for f in made.findings]


def test_an_elapsed_time_unit_diggs_cannot_list_is_named_by_check_and_not_written() -> None:
    data = mwd()
    data["result_columns"][0]["uom"] = "ms"
    found = tables.check(data)
    assert any("elapsed_time" in f.message for f in found if f.rule_id == "tables.unknown-unit")
    made = tables.to_model(data)
    assert made.model.measurements == ()
    assert any("elapsed_time in 'ms'" in f.message for f in made.findings)


def test_check_takes_the_new_columns_for_what_they_are() -> None:
    for data in (monitor(), dissipation()):
        assert [f.rule_id for f in tables.check(data)] == []
    assert not [f for f in tables.check(mwd()) if "elapsed_time" in f.message]


def test_the_profile_describes_the_new_columns() -> None:
    measurements = next(r for r in tables.profile()["resources"] if r["name"] == "measurements")
    assert "element" in [f["name"] for f in measurements["schema"]["fields"]]
    described = tables.from_model(pydiggs.read_bytes(written(monitor()))).descriptor()
    series = next(r for r in described["resources"] if r["name"].startswith("results_"))
    assert series["schema"]["primaryKey"] == ["measurement", "result", "row"]
    assert tables.PROFILE_VERSION == "1.0-beta.1"


# --- a table too long for one element is written in parts ---------------------------------


def _texts(xml: bytes) -> list[int]:
    root = etree.fromstring(xml)
    names = ("dataValues", "posList", "timePositionList", "timeIntervalList")
    return [
        len((el.text or "").encode()) for el in root.iter() if etree.QName(el).localname in names
    ]


@pytest.mark.parametrize("limit", [120, 300, 1000])
def test_a_long_series_is_written_in_parts_under_the_limit(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    data = monitor(60)
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", limit)
    xml = written(data)
    assert max(_texts(xml)) <= limit
    assert errors(xml) == []
    model = pydiggs.read_bytes(xml)
    keys = [m.key for m in model.measurements]
    assert keys[0] == "WL-1" and len(keys) > 1
    assert keys[1:] == [f"WL-1.part{n}" for n in range(2, len(keys) + 1)]
    rows = [r for m in model.measurements for r in m.results[0].domain.index[0].values]
    assert rows == _times(60)  # in order, none lost
    back = tables.from_model(model)
    assert len(back.rows("measurements")) == 1
    assert not [r for r in back.rows("links") if r["source"].startswith("WL-1.part")]
    same_results(data, back)
    checked = tables.round_trip(data)
    assert checked.ok, (checked.differences, checked.findings)


def test_a_long_sounding_is_split_with_its_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    data: Tables = {
        **_base(),
        "measurements": [
            {"key": "CPT-1", "feature": "BH-1", "procedure": "StaticConePenetrationTest"}
        ],
        "result_columns": [
            {"procedure": "StaticConePenetrationTest", "column": "tip_resistance", "uom": "MPa"}
        ],
        "results_StaticConePenetrationTest": [
            {"measurement": "CPT-1", "row": i, "position": i / 50, "tip_resistance": 1 + i / 7}
            for i in range(80)
        ],
    }
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", 400)
    xml = written(data)
    assert max(_texts(xml)) <= 400
    assert errors(xml) == []
    assert len(pydiggs.read_bytes(xml).measurements) > 1
    assert tables.round_trip(data).ok


def test_a_series_by_top_and_base_never_leaves_a_part_of_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data: Tables = {
        **_base(),
        "measurements": [{"key": "D-1", "feature": "BH-1", "procedure": "DrivenPenetrationTest"}],
        "params": [
            {"node": "D-1", "scope": "procedure", "name": "penetrationTestType", "value": "SPT"}
        ],
        "results_DrivenPenetrationTest": [
            {"measurement": "D-1", "row": i, "top": i, "base": i + 0.5, "n_value": 10 + i}
            for i in range(7)
        ],
    }
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", 18)  # three rows of top and base per part
    xml = written(data)
    parts = pydiggs.read_bytes(xml).measurements
    assert [t.row_count for m in parts for t in m.results] == [3, 2, 2]
    assert all(m.results[0].extent is None for m in parts)
    assert tables.round_trip(data).ok


def test_one_row_longer_than_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    model = tables.to_model(mwd()).model
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", 5)  # each row is 10 bytes: "0.01,false"
    with pytest.raises(ModelWriteError, match="row 1 alone makes 10 bytes"):
        pydiggs.write_bytes(model)
    made = tables.to_model(mwd())  # the test is named, and left out
    (lost,) = [f for f in made.findings if f.rule_id == "tables.incomplete"]
    assert "row 1 alone makes" in lost.message
    assert made.model.measurements == ()


def test_a_table_held_by_the_procedure_is_not_split(monkeypatch: pytest.MonkeyPatch) -> None:
    model = tables.to_model(dissipation(40)).model
    monkeypatch.setattr(_emit, "MAX_TEXT_BYTES", 100)
    with pytest.raises(ModelWriteError, match="over the 100 a DIGGS file can hold"):
        pydiggs.write_bytes(model)


def test_measurements_that_only_look_like_parts_stay_apart() -> None:
    data = monitor(4)
    other = copy.deepcopy(data["measurements"][0])
    other.update(key="WL-1.part2", name="Another series")  # a name: not a part
    data["measurements"].append(other)
    data["links"].append({"source": "WL-1.part2", "relation": "monitorLocation", "target": "BH-1"})
    data["components"].append({**data["components"][0], "key": "RP-2", "holder": "WL-1.part2"})
    data["links"].append({"source": "RP-2", "relation": "rpLocation", "target": "BH-1"})
    data["results_WaterLevelMonitoring"] += [
        {**row, "measurement": "WL-1.part2"} for row in monitor(4)["results_WaterLevelMonitoring"]
    ]
    back = tables.from_model(pydiggs.read_bytes(written(data)))
    assert [r["key"] for r in back.rows("measurements")] == ["WL-1", "WL-1.part2"]


# --- property: tables to DIGGS to tables, row for row --------------------------------------

_STAMPS = st.one_of(
    st.datetimes(
        min_value=datetime.datetime(1990, 1, 1),
        max_value=datetime.datetime(2040, 1, 1),
    ).map(lambda d: d.isoformat()),
    st.dates().map(lambda d: d.isoformat()).filter(lambda t: t[:1] != "0"),
    st.datetimes()
    .map(lambda d: d.strftime("%Y-%m-%dT%H:%M:%S") + "-08:00")
    .filter(lambda t: t[:1] not in "0" and len(t.split("-")[0]) == 4),
)
_VALUES = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_subnormal=False)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    kind=st.sampled_from(["monitor", "mwd", "dissipation"]),
    stamps=st.lists(_STAMPS, min_size=1, max_size=30),
    values=st.lists(_VALUES, min_size=30, max_size=30),
    limit=st.sampled_from([None, 150, 400]),
)
def test_time_series_come_back_row_for_row(
    kind: str, stamps: list[str], values: list[float], limit: int | None
) -> None:
    if kind == "monitor":
        data = monitor(times=stamps)
        for row, value in zip(data["results_WaterLevelMonitoring"], values, strict=False):
            row["water_depth"] = value
    elif kind == "mwd":
        data = mwd(len(stamps))
        for row, value in zip(data["results_MWDProcedure"], values, strict=False):
            row["penetration_rate"] = value
    else:
        data = dissipation(len(stamps))
        series = data["results_PorePressureDissipationTest"][1:]
        for row, value in zip(series, values, strict=False):
            row["pore_pressure_u2"] = value
    with pytest.MonkeyPatch.context() as patch:
        if limit is not None and kind != "dissipation":
            patch.setattr(_emit, "MAX_TEXT_BYTES", limit)
        made = tables.to_model(data)
        assert made.lossless, [f.message for f in made.findings]
        xml = pydiggs.write_bytes(made.model)
    back = tables.from_model(pydiggs.read_bytes(xml))
    same_results(data, back)
    if kind == "monitor":
        assert [r["time"] for r in back.rows("results_WaterLevelMonitoring")] == stamps


# --- the official examples: what still does not come back -------------------------------------

#: Per official file: the nodes, by kind, that do not come back after
#: ``tables.from_model(read(f))`` -> ``to_model`` -> ``write_bytes`` -> ``read_bytes``.
#: Features with no centerline (stations, trench walls, geophysics) are still to come.
LOST: dict[str, dict[str, int]] = {
    "Aeromag.xml": {
        "Component:BusinessAssociate": 1,
        "Component:Role": 1,
        "Feature:GP_ArealSurvey": 1,
        "Measurement:Test": 1,
        "Other:description": 1,
    },
    "Borehole-CPT_Example_Annotated.xml": {},
    "GeophysicalFieldSurveyExamples.xml": {
        "Component:Configuration": 1,
        "Component:GP_DataAcquisitionSystem": 1,
        "Component:GP_Receiver": 3,
        "Component:GP_Source": 3,
        "Component:Parameter": 7,
        "Component:Platform": 1,
        "Component:PlatformMovement": 1,
        "Component:SensorSpacing": 1,
        "Feature:GP_MultiTrack": 1,
        "Feature:Station": 1,
        "Measurement:GeophysicalFieldSurvey": 6,
    },
    "MWDExample.xml": {},
    "Piezometer_Example.xml": {},
    "PorePressureDissipation_Example.xml": {},
    "TrenchWallExample.xml": {
        "Component:Lithology": 3,
        "Component:TrenchConstructionMethod": 1,
        "Feature:TrenchWall": 1,
        "LithologyLayer:LithologyObservation": 3,
        "Observation:DiscontinuitySystem": 1,
        "Observation:LithologySystem": 1,
    },
}


def _filled(row: dict[str, Any]) -> dict[str, Any]:
    return {column: value for column, value in row.items() if value is not None}


def _kinds(model: Model) -> collections.Counter[str]:
    return collections.Counter(f"{type(n).__name__}:{n.element}" for n in model.nodes())


def _there_and_back(data: Any) -> tuple[Model, tables.TableSet]:
    xml = pydiggs.write_bytes(tables.to_model(data).model)
    model = pydiggs.read_bytes(xml)
    return model, tables.from_model(model)


@pytest.mark.parametrize("name", sorted(LOST))
def test_what_each_official_example_still_loses_is_pinned(name: str) -> None:
    source = pydiggs.read(OFFICIAL / name)
    sent = tables.from_model(source)
    model, back = _there_and_back(sent.tables)
    before, after = _kinds(source), _kinds(model)
    lost = {kind: before[kind] - after[kind] for kind in before if before[kind] > after[kind]}
    assert lost == LOST[name]
    # What comes back comes back with its values: every result row of every test kept.
    kept = {m["key"] for m in back.rows("measurements")}
    for table, rows in sent.tables.items():
        if table.startswith("results_"):
            want = [_filled(r) for r in rows if r["measurement"] in kept]
            assert want == [_filled(r) for r in back.rows(table)]


def test_the_official_dissipation_test_keeps_its_summary_and_its_series() -> None:
    sent = tables.from_model(pydiggs.read(OFFICIAL / "PorePressureDissipation_Example.xml"))
    rows = sent.rows("results_PorePressureDissipationTest")
    assert collections.Counter(r["result"] for r in rows) == {0: 1, 1: 361}
    assert {r["elapsed_time"] for r in rows if r["result"] == 1} >= {0.0, 5.0}
    assert not [r for r in sent.rows("extra") if str(r["path"]).startswith("results")]


def test_the_official_monitoring_series_comes_back_whole_in_its_well() -> None:
    sent = tables.from_model(pydiggs.read(OFFICIAL / "Piezometer_Example.xml"))
    model, back = _there_and_back(sent.tables)
    (series,) = [m for m in model.measurements if m.element == "Monitor"]
    (well,) = [f for f in model.features if f.element == "Well"]
    assert series.feature == well.key
    rows = back.rows("results_WaterLevelMonitoring")
    assert len(rows) == 22_804
    assert rows == sent.rows("results_WaterLevelMonitoring")


# --- scaling: a series over 10 MB of text is written in parts and reads back ----------------


def _series_model(count: int) -> Model:
    made = tables.to_model(monitor(1)).model
    (series,) = made.measurements
    stamps = tuple(f"2024-01-01T00:00:00.{i:07d}Z" for i in range(count))
    table = ResultTable(
        domain=Domain(
            shape="temporal",
            index=(Column(name="time", key="time", type="datetime", values=stamps),),
        ),
        columns=(dataclasses.replace(series.results[0].columns[0], values=(1.5,) * count),),
        row_count=count,
    )
    return dataclasses.replace(made, measurements=(dataclasses.replace(series, results=(table,)),))


@pytest.mark.scaling
def test_a_series_over_ten_megabytes_is_written_in_parts_and_reads_back() -> None:
    count = 400_000  # 400,000 times of 28 bytes: over 11 MB in one list
    xml = pydiggs.write_bytes(_series_model(count))
    assert max(_texts(xml)) <= _emit.MAX_TEXT_BYTES
    model = pydiggs.read_bytes(xml)
    assert len(model.measurements) == 2
    back = tables.from_model(model).rows("results_WaterLevelMonitoring")
    assert len(back) == count
    assert back[-1]["time"] == f"2024-01-01T00:00:00.{count - 1:07d}Z"
