"""Wells with screens, samples taken at a point, and the specimens tested from them.

A well's opening has its screened interval in ``components.top`` and ``base``; a sample
taken at one depth has it in ``depth``; a specimen names its sample with a ``links`` row.
All three go from tables to DIGGS and back, and the official piezometer example, which
has a well, its screen, a sample and a specimen, comes back whole.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import ModelWriteError, tables
from pydiggs.model import Model

OFFICIAL = Path(__file__).resolve().parent / "fixtures" / "official" / "3.0"
PIEZOMETER = OFFICIAL / "Piezometer_Example.xml"
CRS = "http://www.opengis.net/def/crs/EPSG/0/27700"
NS = {"d": "http://diggsml.org/schemas/3", "gml": "http://www.opengis.net/gml/3.2"}
HREF = "{http://www.w3.org/1999/xlink}href"
Tables = dict[str, list[dict[str, Any]]]


def well(*, top: float = 12.0, base: float = 15.0, depth: float | None = 4.5) -> Tables:
    """A boring, a well in it with one screen, and a sample with a specimen tested from it.

    ``depth`` is where the sample was taken, a point; None gives an interval instead.
    """
    place = {"top": 4.0, "base": 4.5} if depth is None else {"depth": depth}
    return {
        "document": [{"creation_date": "2026-11-26"}],
        "projects": [{"key": "P1", "name": "Riverside"}],
        "features": [
            {"key": "BH-1", "project": "P1", "x": 1, "y": 2, "crs": CRS, "depth_uom": "m"},
            {
                "key": "PZ-1",
                "project": "P1",
                "element": "Well",
                "parent": "BH-1",
                "x": 1,
                "y": 2,
                "crs": CRS,
                "depth_uom": "m",
            },
        ],
        "sampling_activities": [{"key": "SA-1", "feature": "BH-1", **place}],
        "samples": [{"key": "S-1", "sampling_activity": "SA-1", "name": "S-1"}],
        "measurements": [
            {"key": "WC-1", "feature": "BH-1", "procedure": "WaterContentTest", "depth": 4.5}
        ],
        "result_columns": [
            {"procedure": "WaterContentTest", "column": "water_content_natural", "uom": "%"}
        ],
        "results_WaterContentTest": [
            {"measurement": "WC-1", "row": 0, "water_content_natural": 21.9}
        ],
        "components": [
            {
                "key": "SCREEN-1",
                "holder": "PZ-1",
                "role": "opening",
                "element": "WellOpening",
                "top": top,
                "base": base,
            },
            {
                "key": "SPEC-1",
                "holder": "WC-1",
                "role": "specimen",
                "element": "SoilSpecimen",
                "name": "S-1 A",
            },
        ],
        "links": [{"source": "SPEC-1", "relation": "sample", "target": "S-1"}],
    }


def written(data: Tables) -> bytes:
    made = tables.to_model(data)
    assert made.lossless, [f.message for f in made.findings]
    return pydiggs.write_bytes(made.model)


def _filled(row: dict[str, Any]) -> dict[str, Any]:
    return {column: value for column, value in row.items() if value is not None}


def _kinds(model: Model) -> collections.Counter[str]:
    return collections.Counter(f"{type(n).__name__}:{n.element}" for n in model.nodes())


# --- a well and its screen -------------------------------------------------------------------


def test_a_well_with_a_screen_a_point_sample_and_a_specimen_round_trips() -> None:
    checked = tables.round_trip(well())
    assert checked.ok, (checked.findings, checked.differences)
    assert checked.validation.verdict.startswith("valid")


def test_a_screen_is_written_as_the_open_interval_of_a_well_opening() -> None:
    root = etree.fromstring(written(well()))
    (extent,) = root.findall(".//d:Well/d:opening/d:WellOpening/d:openInterval/d:LinearExtent", NS)
    assert extent.get("srsName") == "#PZ-1-lrs"
    assert extent.findtext("gml:posList", namespaces=NS) == "12 15"


def test_a_screen_comes_back_in_top_and_base() -> None:
    back = tables.from_model(pydiggs.read_bytes(written(well(top=45, base=50))))
    (screen,) = [c for c in back.rows("components") if c["element"] == "WellOpening"]
    assert _filled(screen) == {
        "key": "SCREEN-1",
        "holder": "PZ-1",
        "role": "opening",
        "element": "WellOpening",
        "top": 45.0,
        "base": 50.0,
        "depth_uom": "m",
    }
    assert not [r for r in back.rows("extra") if r["node"] == "SCREEN-1"]


def test_a_well_opening_with_no_interval_is_named_and_left_out_with_its_well() -> None:
    data = well()
    del data["components"][0]["top"], data["components"][0]["base"]
    made = tables.to_model(data)
    (lost,) = [f for f in made.findings if f.rule_id == "tables.incomplete"]
    assert "openInterval" in lost.message
    assert [f.element for f in made.model.features] == ["Borehole"]


# --- a sample taken at a point ---------------------------------------------------------------


def test_a_point_sample_is_written_as_a_point_location() -> None:
    root = etree.fromstring(written(well(depth=4.5)))
    (point,) = root.findall(".//d:SamplingActivity/d:samplingLocation/d:PointLocation", NS)
    assert (point.get("srsName"), point.get("srsDimension")) == ("#BH-1-lrs", "1")
    assert point.findtext("gml:pos", namespaces=NS) == "4.5"
    (produced,) = root.findall(".//d:SampleProduced/d:location/d:PointLocation", NS)
    assert produced.findtext("gml:pos", namespaces=NS) == "4.5"
    assert not root.findall(".//d:SamplingActivity//d:LinearExtent", NS)


def test_a_point_sample_comes_back_in_depth_of_the_activity_and_of_the_sample() -> None:
    back = tables.from_model(pydiggs.read_bytes(written(well(depth=4.5))))
    for table in ("sampling_activities", "samples"):
        (row,) = back.rows(table)
        assert (row["depth"], row["top"], row["base"], row["depth_uom"]) == (4.5, None, None, "m")


def test_a_sample_taken_over_an_interval_keeps_top_and_base_and_has_no_depth() -> None:
    back = tables.from_model(pydiggs.read_bytes(written(well(depth=None))))
    for table in ("sampling_activities", "samples"):
        (row,) = back.rows(table)
        assert (row["depth"], row["top"], row["base"]) == (None, 4.0, 4.5)


def test_a_sample_with_no_activity_row_takes_its_depth_from_its_own_row() -> None:
    data = well()
    del data["sampling_activities"]
    data["samples"] = [{"key": "S-1", "feature": "BH-1", "depth": "4.50"}]
    xml = written(data)
    root = etree.fromstring(xml)
    assert root.findtext(".//d:samplingLocation/d:PointLocation/gml:pos", namespaces=NS) == "4.50"
    (sample,) = tables.from_model(pydiggs.read_bytes(xml)).rows("samples")
    assert (sample["depth"], sample["feature"]) == (4.5, "BH-1")
    assert tables.round_trip(data).ok


def test_the_reader_gives_a_point_sample_an_interval_with_a_top_and_no_base() -> None:
    model = pydiggs.read_bytes(written(well(depth=4.5)))
    (activity,) = model.sampling_activities
    (sample,) = model.samples
    for node in (activity, sample):
        assert node.interval is not None and node.interval.base is None
        assert node.interval.top is not None
        assert (node.interval.top.text, node.interval.top.uom) == ("4.5", "m")
        assert node.interval.lrs == "#BH-1-lrs"


def test_a_depth_with_a_top_or_a_base_is_reported_and_the_interval_is_kept() -> None:
    data = well()
    data["sampling_activities"] = [
        {"key": "SA-1", "feature": "BH-1", "top": 4.0, "base": 4.5, "depth": 4.2}
    ]
    made = tables.to_model(data)
    (noted,) = [f for f in made.findings if f.rule_id == "tables.not-carried"]
    assert noted.path == "sampling_activities/1/depth"
    (activity,) = made.model.sampling_activities
    assert activity.interval is not None and activity.interval.base is not None


def test_a_point_depth_in_another_unit_is_reported_never_converted() -> None:
    data = well()
    data["sampling_activities"][0]["depth_uom"] = "ft"
    made = tables.to_model(data)
    assert "tables.unknown-unit" in {f.rule_id for f in made.findings}
    assert made.model.samples == ()  # no depth and no date: DIGGS cannot place the sample


def test_a_point_depth_that_is_not_a_number_is_reported() -> None:
    data = well()
    data["sampling_activities"][0]["depth"] = "about 4"
    made = tables.to_model(data)
    (noted,) = [f for f in made.findings if f.rule_id == "tables.value-unparsed"]
    assert noted.path == "sampling_activities/1/depth"


def test_the_writer_refuses_a_point_depth_in_a_unit_reading_would_not_give() -> None:
    model = tables.to_model(well()).model
    (activity,) = model.sampling_activities
    assert activity.interval is not None and activity.interval.top is not None
    top = dataclasses.replace(activity.interval.top, uom="ft")
    moved = dataclasses.replace(activity, interval=dataclasses.replace(activity.interval, top=top))
    with pytest.raises(ModelWriteError, match="a depth in 'ft'"):
        pydiggs.write_bytes(dataclasses.replace(model, sampling_activities=(moved,)))


# --- a specimen and its sample ---------------------------------------------------------------


def test_a_specimen_names_its_sample() -> None:
    xml = written(well())
    root = etree.fromstring(xml)
    (ref,) = root.findall(".//d:WaterContentTest/d:specimen/d:SoilSpecimen/d:sampleRef", NS)
    assert ref.get(HREF) == "#S-1"
    back = tables.from_model(pydiggs.read_bytes(xml))
    assert [_filled(r) for r in back.rows("links")] == [
        {"source": "SPEC-1", "relation": "sample", "target": "S-1"}
    ]


def test_a_specimen_with_no_sample_is_named_and_left_out_with_its_test() -> None:
    data = well()
    del data["links"]
    made = tables.to_model(data)
    (lost,) = [f for f in made.findings if f.rule_id == "tables.incomplete"]
    assert "sampleRef" in lost.message
    assert made.model.measurements == ()


def test_a_specimen_whose_sample_is_not_in_the_tables_is_an_orphan() -> None:
    data = well()
    data["links"][0]["target"] = "S-9"
    made = tables.to_model(data)
    assert "tables.orphan-row" in {f.rule_id for f in made.findings}
    assert made.model.measurements == ()


# --- the official piezometer example ---------------------------------------------------------


def test_the_official_piezometer_example_comes_back_whole() -> None:
    source = pydiggs.read(PIEZOMETER)
    sent = tables.from_model(source)
    made = tables.to_model(sent.tables)
    xml = pydiggs.write_bytes(made.model)
    model = pydiggs.read_bytes(xml)
    assert _kinds(model) == _kinds(source)
    assert {f.rule_id for f in made.findings} == {
        "tables.key-rewritten",
        "tables.extra-not-written",
    }
    assert pydiggs.validate_bytes(xml).verdict.startswith("valid")
    back = tables.from_model(model)
    (screen,) = [c for c in back.rows("components") if c["element"] == "WellOpening"]
    assert (screen["top"], screen["base"], screen["depth_uom"]) == (45.0, 50.0, "ft")
    (sample,) = back.rows("samples")
    assert (sample["top"], sample["base"], sample["depth_uom"]) == (0.0, 2.0, "ft")
    (specimen,) = [c for c in back.rows("components") if c["element"] == "SoilSpecimen"]
    assert {"source": specimen["key"], "relation": "sample", "target": sample["key"]} in [
        _filled(r) for r in back.rows("links")
    ]
    assert len(back.rows("results_WaterLevelMonitoring")) == 22_804
    # Written once, the file names its own depth systems: nothing is assumed a second time.
    assert "tables.assumed" not in {f.rule_id for f in back.findings}


def test_depths_along_a_system_the_file_does_not_define_are_read_and_reported() -> None:
    """The official example names two depth systems it does not define (its own findings say so)."""
    sent = tables.from_model(pydiggs.read(PIEZOMETER))
    assumed = [f for f in sent.findings if f.rule_id == "tables.assumed"]
    assert sorted(f.path for f in assumed) == [
        "components/1",
        "samples/1",
        "sampling_activities/1",
    ]
    assert all("does not define" in f.message for f in assumed)
    (screen,) = [c for c in sent.rows("components") if c["element"] == "WellOpening"]
    assert (screen["top"], screen["base"]) == (45.0, 50.0)
    paths = {r["path"] for r in sent.rows("extra")}
    assert not paths & {"interval", "samplingLocation"}


def test_a_well_with_no_depths_still_gives_the_unit_its_registry_method_states() -> None:
    sent = tables.from_model(pydiggs.read(PIEZOMETER))
    units = {f["element"]: f["depth_uom"] for f in sent.rows("features")}
    assert units == {"Borehole": "ft", "Well": "ft"}


def test_depths_along_another_feature_the_file_defines_stay_in_extra() -> None:
    xml = written(well()).replace(
        b'srsName="#PZ-1-lrs" srsDimension="1"', b'srsName="#BH-1-lrs" srsDimension="1"'
    )
    back = tables.from_model(pydiggs.read_bytes(xml))
    (screen,) = [c for c in back.rows("components") if c["element"] == "WellOpening"]
    assert (screen["top"], screen["base"]) == (None, None)
    assert [r["path"] for r in back.rows("extra") if r["node"] == "SCREEN-1"] == ["interval"]
    assert "tables.assumed" not in {f.rule_id for f in back.findings}


# --- property: tables to DIGGS to tables -----------------------------------------------------

_DEPTHS = st.decimals(min_value=0, max_value=500, places=2).map(float)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    screens=st.lists(st.tuples(_DEPTHS, _DEPTHS), min_size=1, max_size=4),
    places=st.lists(st.one_of(_DEPTHS, st.tuples(_DEPTHS, _DEPTHS)), min_size=1, max_size=5),
    unit=st.sampled_from(["m", "ft"]),
)
def test_wells_samples_and_specimens_come_back_row_for_row(
    screens: list[tuple[float, float]], places: list[float | tuple[float, float]], unit: str
) -> None:
    data = well()
    for feature in data["features"]:
        feature["depth_uom"] = unit
    first = copy.deepcopy(data["components"][0])
    data["components"] = [
        {**first, "key": f"SCREEN-{n}", "top": min(pair), "base": max(pair)}
        for n, pair in enumerate(screens)
    ]
    data["sampling_activities"], data["samples"], data["links"] = [], [], []
    for n, place in enumerate(places):
        where = (
            {"top": min(place), "base": max(place)}
            if isinstance(place, tuple)
            else {"depth": place}
        )
        data["sampling_activities"].append({"key": f"SA-{n}", "feature": "BH-1", **where})
        data["samples"].append({"key": f"S-{n}", "sampling_activity": f"SA-{n}"})
        data["components"].append(
            {"key": f"SPEC-{n}", "holder": "WC-1", "role": "specimen", "element": "SoilSpecimen"}
        )
        data["links"].append({"source": f"SPEC-{n}", "relation": "sample", "target": f"S-{n}"})
    checked = tables.round_trip(data)
    assert checked.ok, (checked.findings, checked.differences)
    back = tables.from_model(pydiggs.read_bytes(checked.diggs))
    sent = {a["key"]: a for a in data["sampling_activities"]}
    for row in back.rows("sampling_activities"):
        want = sent[row["key"]]
        assert (row["depth"], row["top"], row["base"]) == (
            want.get("depth"),
            want.get("top"),
            want.get("base"),
        )
        assert row["depth_uom"] == unit
    got = {c["key"]: (c["top"], c["base"]) for c in back.rows("components") if c["top"] is not None}
    assert got == {c["key"]: (c["top"], c["base"]) for c in data["components"] if "top" in c}
    assert len(back.rows("links")) == len(places)
