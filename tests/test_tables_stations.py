"""Stations and trench walls: the features DIGGS 3.0.0 gives no centerline.

A station and a trench wall are written with no centerline and no depth axis. A station's
``start`` and ``end`` are when it was occupied; a trench wall has the outline of its face
in ``extent``, which DIGGS requires. What needs a depth along such a feature (a layer, a
sample, a test) is reported, never guessed.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
import doctest
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import ModelWriteError, tables
from pydiggs.findings.model import Finding
from pydiggs.model import ExtraNode
from pydiggs.tables import _flat

OFFICIAL = Path(__file__).resolve().parent / "fixtures" / "official" / "3.0"
CRS = "http://www.opengis.net/def/crs/EPSG/0/27700"
CRS_3D = "http://www.opengis.net/def/crs/EPSG/0/7405"
NS = {"d": "http://diggsml.org/schemas/3", "gml": "http://www.opengis.net/gml/3.2"}
OUTLINE = "POLYGON ((0 0, 18.10 0.6, 14.3 5.2, 0.7 2.98, 0 0))"
Tables = dict[str, list[dict[str, Any]]]


def site() -> Tables:
    """A station and a trench wall, each with what the profile writes of it."""
    return {
        "document": [{"creation_date": "2026-11-27"}],
        "projects": [{"key": "P1", "name": "Riverside"}],
        "features": [
            {
                "key": "ST-1",
                "project": "P1",
                "element": "Station",
                "name": "Gauge 1",
                "x": 451200,
                "y": 206300,
                "crs": CRS,
                "start": "2026-05-01",
                "end": "2026-05-02",
            },
            {
                "key": "TW-1",
                "project": "P1",
                "element": "TrenchWall",
                "name": "Trench 1, north wall",
                "x": 451210,
                "y": 206310,
                "z": 5.0,
                "crs": CRS_3D,
                "extent": OUTLINE,
                "extent_crs": CRS,
                "start": "2026-05-03",
            },
        ],
        "params": [
            {"node": "ST-1", "name": "type", "value": "Sampling station"},
            {"node": "TW-1", "name": "trenchDepth", "value": "5.50", "uom": "m"},
        ],
        "components": [
            {
                "key": "CM-1",
                "holder": "TW-1",
                "role": "constructionMethod",
                "element": "TrenchConstructionMethod",
                "name": "backhoe",
            }
        ],
    }


def only(data: Tables, key: str) -> Tables:
    """``data`` with the one feature ``key`` and what belongs to it."""
    kept = copy.deepcopy(data)
    kept["features"] = [row for row in kept["features"] if row["key"] == key]
    kept["params"] = [row for row in kept["params"] if row["node"] == key]
    kept["components"] = [row for row in kept["components"] if row["holder"] == key]
    return kept


def written(data: Tables) -> bytes:
    made = tables.to_model(data)
    assert made.lossless, [f.message for f in made.findings]
    return pydiggs.write_bytes(made.model)


def _filled(row: dict[str, Any]) -> dict[str, Any]:
    return {column: value for column, value in row.items() if value is not None}


def _found(findings: tuple[Finding, ...], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule_id == rule]


def _features(data: Tables) -> dict[str, dict[str, Any]]:
    back = tables.from_model(pydiggs.read_bytes(written(data)))
    return {row["key"]: _filled(row) for row in back.rows("features")}


# --- there and back --------------------------------------------------------------------------


def test_a_station_and_a_trench_wall_round_trip() -> None:
    checked = tables.round_trip(site())
    assert checked.ok, (checked.findings, checked.differences)
    assert checked.validation.verdict.startswith("valid")
    assert not checked.validation.findings


def test_the_same_tables_give_the_same_bytes() -> None:
    assert written(site()) == written(site())


def test_neither_is_written_with_a_centerline_or_a_depth_axis() -> None:
    root = etree.fromstring(written(site()))
    for element in ("Station", "TrenchWall"):
        (feature,) = root.findall(f".//d:{element}", NS)
        assert feature.find("d:referencePoint/d:PointLocation/gml:pos", NS) is not None
        assert feature.find("d:centerLine", NS) is None
        assert feature.find("d:linearReferencing", NS) is None
    assert not root.findall(".//d:LinearSpatialReferenceSystem", NS)


def test_a_station_comes_back_as_its_row() -> None:
    assert _features(site())["ST-1"] == {
        "key": "ST-1",
        "project": "P1",
        "element": "Station",
        "name": "Gauge 1",
        "x": 451200.0,
        "y": 206300.0,
        "crs": CRS,
        "start": "2026-05-01",
        "end": "2026-05-02",
    }


def test_a_trench_wall_comes_back_as_its_row() -> None:
    assert _features(site())["TW-1"] == {
        "key": "TW-1",
        "project": "P1",
        "element": "TrenchWall",
        "name": "Trench 1, north wall",
        "x": 451210.0,
        "y": 206310.0,
        "z": 5.0,
        "crs": CRS_3D,
        "extent": OUTLINE,
        "extent_crs": CRS,
        "start": "2026-05-03",
    }


def test_what_belongs_to_them_comes_back() -> None:
    back = tables.from_model(pydiggs.read_bytes(written(site())))
    params = {(r["node"], r["name"]): (r["value"], r["uom"]) for r in back.rows("params")}
    assert params[("ST-1", "type")] == ("Sampling station", None)
    assert params[("TW-1", "trenchDepth")] == ("5.50", "m")
    (method,) = back.rows("components")
    assert (method["holder"], method["element"], method["name"]) == (
        "TW-1",
        "TrenchConstructionMethod",
        "backhoe",
    )
    assert not back.rows("extra")


# --- a station's time ------------------------------------------------------------------------


def test_a_station_s_start_and_end_are_when_it_was_occupied() -> None:
    root = etree.fromstring(written(site()))
    (interval,) = root.findall(".//d:Station/d:whenOccupied/d:TimeInterval", NS)
    assert interval.findtext("d:start", namespaces=NS) == "2026-05-01"
    assert interval.findtext("d:end", namespaces=NS) == "2026-05-02"
    assert root.find(".//d:Station/d:whenConstructed", NS) is None
    (built,) = root.findall(".//d:TrenchWall/d:whenConstructed/d:TimeInterval", NS)
    assert built.findtext("d:start", namespaces=NS) == "2026-05-03"


@pytest.mark.parametrize("given", [{"start": "2026-05-01"}, {"end": "2026-05-02T16:00:00Z"}])
def test_a_station_with_only_a_start_or_only_an_end_comes_back_so(given: dict[str, str]) -> None:
    data = only(site(), "ST-1")
    del data["features"][0]["start"], data["features"][0]["end"]
    data["features"][0].update(given)
    row = _features(data)["ST-1"]
    assert {c: row.get(c) for c in ("start", "end")} == {"start": None, "end": None} | given


def test_a_station_with_no_time_has_no_when_occupied() -> None:
    data = only(site(), "ST-1")
    del data["features"][0]["start"], data["features"][0]["end"]
    assert etree.fromstring(written(data)).find(".//d:whenOccupied", NS) is None


# --- a trench wall's outline -----------------------------------------------------------------


def test_the_outline_is_written_as_the_feature_extent_coordinate_for_coordinate() -> None:
    root = etree.fromstring(written(site()))
    (surface,) = root.findall(".//d:TrenchWall/d:featureExtent/d:PlanarSurface", NS)
    assert (surface.get("srsName"), surface.get("srsDimension")) == (CRS, "2")
    (ring,) = surface.findall("gml:exterior/gml:LinearRing/gml:posList", NS)
    assert ring.text == "0 0 18.10 0.6 14.3 5.2 0.7 2.98 0 0"  # 18.10 as given, not 18.1


def test_an_outline_with_three_coordinates_a_position() -> None:
    data = only(site(), "TW-1")
    data["features"][0]["extent"] = "polygon z((1 2 5.0,9.50 2 5.0,9.50 2 2,1 2 2,1 2 5.0))"
    data["features"][0]["extent_crs"] = CRS_3D
    checked = tables.round_trip(data)  # spaced another way: the same positions
    assert checked.ok, (checked.findings, checked.differences)
    surface = etree.fromstring(checked.diggs).find(".//d:PlanarSurface", NS)
    assert surface is not None
    assert surface.get("srsDimension") == "3"
    row = _features(data)["TW-1"]
    assert row["extent"] == "POLYGON Z ((1 2 5.0, 9.50 2 5.0, 9.50 2 2, 1 2 2, 1 2 5.0))"


def test_the_round_trip_check_names_an_outline_that_changed() -> None:
    assert _flat.same_ring("POLYGON((0 0,4 0,4 2,0 0))", "POLYGON ((0.0 0, 4 0, 4 2, 0 0))")
    assert not _flat.same_ring("POLYGON((0 0,4 0,4 2,0 0))", "POLYGON ((0 0, 4 0, 4 3, 0 0))")
    assert not _flat.same_ring("POLYGON((0 0,4 0,4 2,0 0))", "not a polygon")


def test_a_trench_wall_with_no_outline_is_named_and_left_out() -> None:
    data = only(site(), "TW-1")
    del data["features"][0]["extent"]
    made = tables.to_model(data)
    assert not made.model.features
    lost = _found(made.findings, "tables.incomplete")
    assert "an extent (the outline of the wall face, as a POLYGON)" in lost[0].message
    assert lost[0].path == "features/1"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("0 0 4 0 4 2 0 0", "not a POLYGON with one ring"),
        ("POLYGON ((0 0, 4 0, 4 2, 0 0), (1 1, 2 1, 2 2, 1 1))", "not a POLYGON with one ring"),
        ("MULTIPOLYGON (((0 0, 4 0, 4 2, 0 0)))", "not a POLYGON with one ring"),
        ("POLYGON EMPTY", "not a POLYGON with one ring"),
        ("POLYGON ((0 0, 4 0, 4 2))", "at least four positions"),
        ("POLYGON ((0 0, 4 0, 4 2, 0 2))", "not closed"),
        ("POLYGON ((0 0, 4 0 1, 4 2, 0 0))", "same number of coordinates"),
        ("POLYGON Z ((0 0, 4 0, 4 2, 0 0))", "two coordinates (x y), or three (x y z)"),
        ("POLYGON ((0 0 0 0, 4 0 0 0, 4 2 0 0, 0 0 0 0))", "two coordinates (x y), or three"),
        ("POLYGON ((0 0, 4 0, 4 two, 0 0))", "not a number"),
        ("POLYGON ((0 0, 4 0, 4 nan, 0 0))", "not a number"),
    ],
)
def test_an_outline_the_profile_cannot_read_is_named_never_repaired(text: str, why: str) -> None:
    data = only(site(), "TW-1")
    data["features"][0]["extent"] = text
    made = tables.to_model(data)
    assert not made.model.features
    (lost, *_rest) = _found(made.findings, "tables.incomplete")
    assert "an extent that is a polygon the profile reads" in lost.message
    assert why in lost.message


def test_an_outline_with_no_reference_system_is_written_and_reported() -> None:
    data = only(site(), "TW-1")
    del data["features"][0]["extent_crs"]
    made = tables.to_model(data)
    (noted,) = _found(made.findings, "tables.no-crs")
    assert noted.path == "features/1/extent_crs"
    surface = etree.fromstring(pydiggs.write_bytes(made.model)).find(".//d:PlanarSurface", NS)
    assert surface is not None
    assert surface.get("srsName") is None


def test_an_outline_in_a_system_only_a_diggs_file_defines_is_reported() -> None:
    data = only(site(), "TW-1")
    data["features"][0]["extent_crs"] = "#wall-face"
    made = tables.to_model(data)
    (noted,) = _found(made.findings, "tables.no-crs")
    assert "the tables do not carry its definition" in noted.message
    assert _features_of(made.model)["TW-1"]["extent_crs"] == "#wall-face"


def _features_of(model: pydiggs.model.Model) -> dict[str, dict[str, Any]]:
    back = tables.from_model(pydiggs.read_bytes(pydiggs.write_bytes(model)))
    return {row["key"]: _filled(row) for row in back.rows("features")}


@pytest.mark.parametrize("element", ["Station", "Borehole"])
def test_an_outline_on_another_kind_of_feature_is_reported_not_written(element: str) -> None:
    data = only(site(), "ST-1")
    data["features"][0] |= {"element": element, "extent": OUTLINE, "extent_crs": CRS}
    data["features"][0] |= {"depth_uom": "m"} if element == "Borehole" else {}
    data["params"] = []
    made = tables.to_model(data)
    (noted,) = _found(made.findings, "tables.not-carried")
    assert "for a TrenchWall only" in noted.message
    assert noted.path == "features/1/extent"
    assert [f.element for f in made.model.features] == [element]
    assert b"featureExtent" not in pydiggs.write_bytes(made.model)


# --- what such a feature cannot hold ---------------------------------------------------------


@pytest.mark.parametrize("key", ["ST-1", "TW-1"])
def test_it_needs_its_reference_point(key: str) -> None:
    data = only(site(), key)
    del data["features"][0]["x"]
    made = tables.to_model(data)
    assert not made.model.features
    (lost, *_rest) = _found(made.findings, "tables.incomplete")
    assert "an x and a y" in lost.message
    assert "no depth axis" in lost.message
    assert not _found(made.findings, "tables.assumed")  # no place is made up for it


@pytest.mark.parametrize("column", ["depth_uom", "total_depth", "total_depth_uom"])
def test_a_depth_unit_or_a_total_depth_is_reported_not_written(column: str) -> None:
    data = only(site(), "ST-1")
    data["features"][0][column] = 3.5 if column == "total_depth" else "m"
    made = tables.to_model(data)
    (noted,) = _found(made.findings, "tables.not-carried")
    assert noted.path == f"features/1/{column}"
    assert [f.element for f in made.model.features] == ["Station"]
    assert made.model.features[0].total_depth is None


def test_no_depth_unit_is_assumed_for_them() -> None:
    made = tables.to_model(site())
    assert not made.findings
    assert [(f.lrs, f.centerline) for f in made.model.features] == [(None, None), (None, None)]


def test_a_layer_on_a_trench_wall_is_reported_not_written() -> None:
    data = only(site(), "TW-1")
    data["layers"] = [
        {"key": "L-1", "feature": "TW-1", "top": 0, "base": 1.5, "description": "SAND"},
        {"key": "L-2", "feature": "TW-1", "description": "CLAY"},
    ]
    made = tables.to_model(data)
    (lost, *_rest) = _found(made.findings, "tables.incomplete")
    assert "no depth axis" in lost.message
    assert "(2 rows)" in lost.message
    assert [f.element for f in made.model.features] == ["TrenchWall"]
    assert not made.model.observations


@pytest.mark.parametrize(
    "place", [{"start": "2026-05-01T10:00:00Z"}, {"depth": 0.5}, {"top": 0, "base": 0.5}]
)
def test_a_sample_on_a_station_is_reported_not_written(place: dict[str, Any]) -> None:
    data = only(site(), "ST-1")
    data["sampling_activities"] = [{"key": "SA-1", "feature": "ST-1", **place}]
    data["samples"] = [
        {"key": "S-1", "sampling_activity": "SA-1"},
        {"key": "S-2", "feature": "ST-1", **place},
    ]
    made = tables.to_model(data)
    by_table = {f.path.partition("/")[0]: f for f in _found(made.findings, "tables.incomplete")}
    for table in ("sampling_activities", "samples"):
        assert "no depth axis" in by_table[table].message
        assert "a place as a depth along the feature only" in by_table[table].message
    assert not made.model.samples
    assert not made.model.sampling_activities
    assert [f.element for f in made.model.features] == ["Station"]


@pytest.mark.parametrize(
    ("element", "procedure", "placed"),
    [
        ("Test", "WaterContentTest", {}),
        ("Test", "WaterContentTest", {"position": 1.0}),
        ("Monitor", "WaterLevelMonitoring", {"time": "2026-05-01T00:00:00Z"}),
        ("MeasurementWhileDrilling", "MWDProcedure", {"elapsed_time": 0}),
    ],
)
def test_a_test_or_a_series_on_a_station_is_reported_not_written(
    element: str, procedure: str, placed: dict[str, Any]
) -> None:
    data = only(site(), "ST-1")
    data["measurements"] = [
        {"key": "M-1", "feature": "ST-1", "element": element, "procedure": procedure, "depth": 1}
    ]
    data["result_columns"] = [{"procedure": procedure, "column": "value", "uom": "m"}]
    data[f"results_{procedure}"] = [{"measurement": "M-1", "value": 1.2, **placed}]
    made = tables.to_model(data)
    (lost,) = [f for f in made.findings if f.path.startswith("measurements")]
    assert lost.rule_id == "tables.incomplete"
    assert "no depth axis" in lost.message
    assert not made.model.measurements
    assert [f.element for f in made.model.features] == ["Station"]


def test_a_depth_on_a_record_of_a_trench_wall_is_reported_and_the_record_kept() -> None:
    data = only(site(), "TW-1")
    data["components"][0] |= {"top": 0, "base": 2.5}
    made = tables.to_model(data)
    (noted,) = _found(made.findings, "tables.not-carried")
    assert noted.path == "components/1/top"
    assert "no depth axis" in noted.message
    (method,) = made.model.features[0].construction
    assert (method.element, method.interval, method.location) == (
        "TrenchConstructionMethod",
        None,
        None,
    )


def test_a_borehole_beside_them_keeps_its_depths() -> None:
    data = site()
    data["features"].append(
        {"key": "BH-1", "project": "P1", "x": 1, "y": 2, "crs": CRS, "depth_uom": "ft"}
    )
    data["layers"] = [{"key": "L-1", "feature": "BH-1", "top": 0, "base": 4, "description": "FILL"}]
    checked = tables.round_trip(data)
    assert not checked.differences
    # Only the layer's classification type is assumed, as for any borehole log.
    assert [f.path for f in checked.findings] == ["observations"]
    root = etree.fromstring(checked.diggs)
    assert len(root.findall(".//d:centerLine", NS)) == 1
    assert root.find(".//d:Borehole/d:centerLine", NS) is not None


# --- the writer ------------------------------------------------------------------------------


def test_the_writer_writes_a_station_as_it_was_read() -> None:
    model = pydiggs.read_bytes(written(only(site(), "ST-1")))
    (station,) = model.features
    assert [extra.qname for extra in station.extra] == ["whenOccupied"]
    assert pydiggs.write_bytes(model) == written(only(site(), "ST-1"))


def test_the_writer_refuses_any_other_extra_of_a_feature() -> None:
    model = tables.to_model(only(site(), "ST-1")).model
    (station,) = model.features
    other = ExtraNode(qname="status", ns=NS["d"], text="open")
    changed = dataclasses.replace(model, features=(dataclasses.replace(station, extra=(other,)),))
    with pytest.raises(ModelWriteError, match="`extra` is not empty"):
        pydiggs.write_bytes(changed)


def test_the_writer_refuses_two_extents() -> None:
    model = tables.to_model(only(site(), "TW-1")).model
    (wall,) = model.features
    twice = dataclasses.replace(wall, extra=(*wall.extra, *wall.extra))
    with pytest.raises(ModelWriteError, match="`extra` is not empty"):
        pydiggs.write_bytes(dataclasses.replace(model, features=(twice,)))


def test_the_writer_refuses_an_extent_on_a_feature_that_has_none() -> None:
    wall = tables.to_model(only(site(), "TW-1")).model.features[0]
    model = tables.to_model(only(site(), "ST-1")).model
    (station,) = model.features
    changed = dataclasses.replace(model, features=(dataclasses.replace(station, extra=wall.extra),))
    with pytest.raises(ModelWriteError, match="Station has no child element 'featureExtent'"):
        pydiggs.write_bytes(changed)


# --- reading a DIGGS file --------------------------------------------------------------------


def test_an_outline_with_a_hole_stays_in_extra_and_is_not_written_back() -> None:
    root = etree.fromstring(written(only(site(), "TW-1")))
    surface = root.find(".//d:PlanarSurface", NS)
    assert surface is not None
    hole = etree.SubElement(surface, f"{{{NS['gml']}}}interior")
    ring = etree.SubElement(hole, f"{{{NS['gml']}}}LinearRing")
    etree.SubElement(ring, f"{{{NS['gml']}}}posList").text = "1 1 2 1 2 2 1 1"
    sent = tables.from_model(pydiggs.read_bytes(etree.tostring(root)))
    (row,) = sent.rows("features")
    assert row["extent"] is None
    assert [r["path"] for r in sent.rows("extra") if r["node"] == "TW-1"] == ["featureExtent"]
    made = tables.to_model(sent.tables)
    assert not made.model.features
    assert _found(made.findings, "tables.extra-not-written")
    assert "an extent" in _found(made.findings, "tables.incomplete")[0].message


def test_the_official_station_comes_back_with_its_time() -> None:
    source = pydiggs.read(OFFICIAL / "GeophysicalFieldSurveyExamples.xml")
    sent = tables.from_model(source)
    (station,) = [r for r in sent.rows("features") if r["element"] == "Station"]
    assert (station["start"], station["end"], station["depth_uom"]) == ("2021-05-23", None, None)
    assert not [r for r in sent.rows("extra") if r["node"] == station["key"]]
    back = tables.from_model(
        pydiggs.read_bytes(pydiggs.write_bytes(tables.to_model(sent.tables).model))
    )
    (again,) = [r for r in back.rows("features") if r["element"] == "Station"]
    # The file names no project for it, so one is made (and reported) on the way back.
    assert {**_filled(again), "project": None} == {**_filled(station), "project": None}
    kinds = {(r["node"], r["name"]): r["value"] for r in back.rows("params")}
    assert kinds[(station["key"], "type")] == "Sampling station"


def test_the_official_trench_wall_comes_back_without_what_is_drawn_on_its_face() -> None:
    source = pydiggs.read(OFFICIAL / "TrenchWallExample.xml")
    sent = tables.from_model(source)
    (wall,) = sent.rows("features")
    assert wall["extent"].startswith("POLYGON ((0.0 0.0, 2.1676 0.2391, ")
    assert wall["extent"].endswith(", 0.3028 1.4344, 0.0 0.0))")
    assert wall["extent_crs"] == "#trench-wall-2d-crs-001"
    assert not [r for r in sent.rows("extra") if r["path"] == "featureExtent"]
    made = tables.to_model(sent.tables)
    # The outline is in the wall's own system, which the file defines and the tables do not.
    (noted,) = _found(made.findings, "tables.no-crs")
    assert "#" in noted.message
    layers = [f for f in _found(made.findings, "tables.incomplete") if f.path.startswith("layers")]
    assert "(3 rows)" in layers[0].message
    model = pydiggs.read_bytes(pydiggs.write_bytes(made.model))
    kinds = collections.Counter(f"{type(n).__name__}:{n.element}" for n in model.nodes())
    assert kinds["Feature:TrenchWall"] == 1
    assert kinds["Component:TrenchConstructionMethod"] == 1
    assert kinds["LithologyLayer:LithologyObservation"] == 0
    (again,) = tables.from_model(model).rows("features")
    assert _filled(again) == _filled(wall)


def test_geophysics_features_are_still_reported() -> None:
    sent = tables.from_model(pydiggs.read(OFFICIAL / "GeophysicalFieldSurveyExamples.xml"))
    made = tables.to_model(sent.tables)
    assert "GP_MultiTrack" not in [f.element for f in made.model.features]
    assert [f for f in _found(made.findings, "tables.incomplete") if "GP_MultiTrack" in f.message]


# --- generated tables ------------------------------------------------------------------------

_COORDINATE = st.decimals(
    min_value=-1000, max_value=1000, places=3, allow_nan=False, allow_infinity=False
).map(str)


@st.composite
def _outlines(draw: st.DrawFn) -> tuple[str, int]:
    dim = draw(st.sampled_from([2, 3]))
    count = draw(st.integers(min_value=3, max_value=7))
    positions = [" ".join(draw(_COORDINATE) for _ in range(dim)) for _ in range(count)]
    positions.append(positions[0])
    return f"POLYGON {'Z ' if dim == 3 else ''}(({', '.join(positions)}))", dim


_TIME = st.sampled_from([None, "2026-05-01", "2026-05-01T08:30:00Z", "2026-05-02T17:00:00+01:00"])


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    outlines=st.lists(_outlines(), max_size=3),
    stations=st.lists(st.tuples(_TIME, _TIME), max_size=3),
    with_crs=st.booleans(),
)
def test_stations_and_trench_walls_come_back_row_for_row(
    outlines: list[tuple[str, int]], stations: list[tuple[str | None, str | None]], with_crs: bool
) -> None:
    features: list[dict[str, Any]] = []
    for number, (outline, _dim) in enumerate(outlines):
        features.append(
            {
                "key": f"TW-{number}",
                "project": "P1",
                "element": "TrenchWall",
                "name": f"Wall {number}",
                "x": 10.5 + number,
                "y": 20.25,
                "crs": CRS,
                "extent": outline,
                "extent_crs": CRS if with_crs else None,
            }
        )
    for number, (start, end) in enumerate(stations):
        features.append(
            {
                "key": f"ST-{number}",
                "project": "P1",
                "element": "Station",
                "name": f"Station {number}",
                "x": 30.0 + number,
                "y": 40.0,
                "z": 1.5,
                "crs": CRS_3D,
                "start": start,
                "end": end,
            }
        )
    data: Tables = {"projects": [{"key": "P1", "name": "Riverside"}], "features": features}
    made = tables.to_model(data)
    assert {f.rule_id for f in made.findings} <= {"tables.assumed", "tables.no-crs"}
    xml = pydiggs.write_bytes(made.model)
    assert not [f for f in pydiggs.validate_bytes(xml).findings if f.severity.rank > 1]
    back = tables.from_model(pydiggs.read_bytes(xml))
    assert [_filled(row) for row in back.rows("features")] == [_filled(row) for row in features]
    for (outline, dim), row in zip(outlines, back.rows("features"), strict=False):
        ring = _flat.ring_of(row["extent"])
        assert ring == _flat.ring_of(outline)  # every coordinate as the text it was given
        assert not isinstance(ring, str)
        assert ring[0] == dim


def test_docstring_examples_run() -> None:
    failed, attempted = doctest.testmod(_flat)
    assert attempted > 0
    assert failed == 0
