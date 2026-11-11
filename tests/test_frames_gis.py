"""The read API's exports (work package R3c): pydiggs.frames and pydiggs.gis."""

from __future__ import annotations

import doctest
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _views, frames

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official"
CPT = OFFICIAL / "2.6" / "CPT_and_PorePressureDissipation.xml"  # one sounding, UTM zone 11N
ODOT = OFFICIAL / "2.5.a" / "ODOT_600304_20180702.xml"  # six boreholes in EPSG:4326
LITHO = OFFICIAL / "2.5.a" / "LithoGrading.xml"  # eight features, layers, a state plane CRS
SCPT = "StaticConePenetrationTest"


@pytest.fixture(scope="module")
def cpt():
    return pydiggs.read(CPT)


@pytest.fixture(scope="module")
def odot():
    return pydiggs.read(ODOT)


@pytest.fixture(scope="module")
def litho():
    return pydiggs.read(LITHO)


# --- views (stdlib) --------------------------------------------------------------------------


def test_every_view_has_columns_of_one_length(cpt, litho):
    for model in (cpt, litho):
        for view in _views.VIEWS:
            data = _views.columns(model, view)
            assert data, view
            assert len({len(cells) for cells in data.values()}) == 1, view
    assert frames.VIEWS == ("features", "samples", "layers", "measurements", "results")


def test_features_view(cpt):
    data = _views.columns(cpt, "features")
    assert data["key"] == [f.key for f in cpt.features]
    assert data["kind"] == ["sounding"]
    location = cpt.features[0].location
    assert (data["x"][0], data["y"][0], data["z"][0]) == location.coordinates
    assert data["crs"] == [location.crs]
    assert data["lon"] == [None]  # a projected CRS: the core does not transform
    assert _views.columns(cpt, "features", kind="borehole")["key"] == []


def test_geographic_features_have_lon_and_lat(odot):
    data = _views.columns(odot, "features")
    assert len(data["key"]) == 6
    for lon, lat, feature in zip(data["lon"], data["lat"], odot.features, strict=True):
        assert (lon, lat) == (feature.location.geographic.lon, feature.location.geographic.lat)


def test_layers_and_samples_views(litho):
    layers = _views.columns(litho, "layers")
    count = sum(len(o.layers) for o in litho.observations)
    assert count > 0 and len(layers["key"]) == count
    assert all(
        top is None or base is None or top <= base
        for top, base in zip(layers["top"], layers["base"], strict=True)
    )
    one = litho.observations[0]
    only = _views.columns(litho, "layers", feature=one.feature)
    assert set(only["feature"]) == {one.feature}
    samples = _views.columns(litho, "samples")
    assert samples["key"] == [s.key for s in litho.samples]


def test_measurements_and_results_views(cpt):
    measurements = _views.columns(cpt, "measurements")
    assert measurements["key"] == [m.key for m in cpt.measurements]
    assert measurements["rows"] == [sum(t.row_count for t in m.results) for m in cpt.measurements]
    test = next(m for m in cpt.measurements if m.procedure and m.procedure.type == SCPT)
    table = test.results[0]
    results = _views.columns(cpt, "results", procedure=SCPT)
    assert list(results)[:5] == ["measurement", "feature", "procedure", "table", "row"]
    assert len(results["row"]) == sum(t.row_count for t in test.results)
    assert set(results["measurement"]) == {test.key}
    tip = table.column("tip_resistance")
    assert tuple(results["tip_resistance"][: table.row_count]) == tip.values
    assert tuple(results["position"][: table.row_count]) == table.domain.index[0].values
    assert _views.columns(cpt, "results", measurement=test.key) == results


def test_results_in_si(cpt):
    plain = _views.columns(cpt, "results", procedure=SCPT)
    si = _views.columns(cpt, "results", procedure=SCPT, si=True)
    test = next(m for m in cpt.measurements if m.procedure and m.procedure.type == SCPT)
    tip = test.results[0].column("tip_resistance")
    assert tip.si_factor is not None and tip.si_factor != 1
    assert si["tip_resistance"][0] == pytest.approx(plain["tip_resistance"][0] * tip.si_factor)


def test_an_inventory_model_has_no_result_rows():
    model = pydiggs.read(CPT, results=False)
    results = _views.columns(model, "results")
    assert list(results) == ["measurement", "feature", "procedure", "table", "row"]
    assert results["row"] == []
    assert _views.columns(model, "measurements")["rows"][0] > 0


def test_unknown_views_and_filters_are_refused(cpt):
    with pytest.raises(ValueError, match="view must be one of"):
        _views.columns(cpt, "tables")
    with pytest.raises(ValueError, match="takes no 'procedure' filter"):
        _views.columns(cpt, "samples", procedure=SCPT)


# --- frames ----------------------------------------------------------------------------------


def test_to_pandas(cpt):
    pandas = pytest.importorskip("pandas")
    frame = frames.to_pandas(cpt, "results", procedure=SCPT)
    assert isinstance(frame, pandas.DataFrame)
    data = _views.columns(cpt, "results", procedure=SCPT)
    assert list(frame.columns) == list(data)
    assert len(frame) == len(data["row"])
    assert str(frame["tip_resistance"].dtype) == "float64"
    assert frame["tip_resistance"].iloc[0] == data["tip_resistance"][0]
    features = frames.to_pandas(cpt, "features")
    assert features.shape == (1, 14)
    assert frames.to_pandas(cpt, "samples").shape == (0, 11)  # empty, with its columns


def test_to_polars(cpt):
    polars = pytest.importorskip("polars")
    frame = frames.to_polars(cpt, "results", procedure=SCPT, si=True)
    assert isinstance(frame, polars.DataFrame)
    data = _views.columns(cpt, "results", procedure=SCPT, si=True)
    assert frame.columns == list(data)
    assert frame.height == len(data["row"])
    assert frame["tip_resistance"][0] == pytest.approx(data["tip_resistance"][0])
    assert frames.to_polars(cpt, "features").height == 1


def test_a_missing_library_names_its_extra(cpt, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(ImportError, match=r"pydiggs\.frames needs pandas.*pydiggs\[frames\]"):
        frames.to_pandas(cpt)
    with pytest.raises(ImportError, match=r"pydiggs\.frames needs polars.*pydiggs\[polars\]"):
        frames.to_polars(cpt)
    gis = sys.modules.get("pydiggs.gis") or importlib.__import__("pydiggs.gis").gis
    with pytest.raises(ImportError, match=r"pydiggs\.gis needs pyproj.*pydiggs\[gis\]"):
        gis.transform(cpt)


def test_another_missing_module_is_not_blamed_on_the_extra(monkeypatch):
    def broken(name):
        raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="numpy"):
        _views.require("pandas", "frames", "pydiggs.frames")


def test_importing_the_export_modules_loads_no_library():
    code = (
        "import sys, pydiggs, pydiggs.frames, pydiggs.gis;"
        "print(sorted({'pandas','polars','pyproj','geopandas','shapely','pyogrio','pyarrow',"
        "'numpy'} & set(sys.modules)))"
    )
    out = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


# --- gis -------------------------------------------------------------------------------------


def test_transform_gives_projected_positions_a_longitude_and_latitude(cpt):
    pytest.importorskip("pyproj")
    from pydiggs import gis

    moved = gis.transform(cpt)
    before, after = cpt.features[0].location, moved.features[0].location
    assert after.coordinates == before.coordinates and after.crs == before.crs
    assert before.geographic is None  # the original model is not changed
    # UTM zone 11N, 387,417 m E 3,742,645 m N: Long Beach, California.
    assert after.geographic.lon == pytest.approx(-118.2165, abs=1e-4)
    assert after.geographic.lat == pytest.approx(33.8180, abs=1e-4)
    assert after.geographic.datum.startswith("World Geodetic System 1984")
    assert after.transformed_by.startswith("pydiggs[gis] pyproj ")
    assert pydiggs.Model.from_json(moved.to_json()) == moved


def test_transform_leaves_positions_already_in_the_target_alone(odot):
    pytest.importorskip("pyproj")
    from pydiggs import gis

    assert gis.transform(odot) == odot
    nad83 = gis.transform(odot, to="EPSG:4269")
    first, moved = odot.features[0].location, nad83.features[0].location
    assert moved.transformed_by is not None
    assert moved.geographic.datum == "North American Datum 1983"
    assert moved.geographic.lon == pytest.approx(first.geographic.lon, abs=1e-4)
    assert moved.geographic.lat == pytest.approx(first.geographic.lat, abs=1e-4)


def test_transform_skips_what_it_cannot_resolve():
    pytest.importorskip("pyproj")
    from pydiggs import gis

    ns = 'xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2"'
    body = (
        f"<Diggs {ns}>"
        + "".join(
            f'<samplingFeature><Borehole gml:id="{gml_id}"><referencePoint><PointLocation>'
            f'<gml:pos srsName="{srs}" srsDimension="2">500000 4000000</gml:pos>'
            "</PointLocation></referencePoint></Borehole></samplingFeature>"
            for gml_id, srs in (
                ("local", "#site-grid"),
                ("unknown", "urn:ogc:def:crs:EPSG::999999"),
                ("utm", "urn:ogc:def:crs:EPSG::32633"),
            )
        )
        + "</Diggs>"
    )
    moved = gis.transform(pydiggs.read_bytes(body.encode()))
    assert moved.get("local").location.geographic is None
    assert moved.get("unknown").location.geographic is None
    utm = moved.get("utm").location.geographic
    assert (utm.lon, utm.lat) == (pytest.approx(15.0), pytest.approx(36.1447, abs=1e-3))


def test_transform_needs_a_geographic_target(cpt):
    pytest.importorskip("pyproj")
    from pydiggs import gis

    for bad in ("EPSG:26911", "not a crs"):
        with pytest.raises(ValueError, match="geographic CRS"):
            gis.transform(cpt, to=bad)


def test_to_geodataframe(litho, odot):
    geopandas = pytest.importorskip("geopandas")
    from pydiggs import gis

    frame = gis.to_geodataframe(odot)
    assert isinstance(frame, geopandas.GeoDataFrame)
    assert frame.crs.to_epsg() == 4326
    assert list(frame["key"]) == [f.key for f in odot.features]
    first = odot.features[0].location.geographic
    assert (frame.geometry.iloc[0].x, frame.geometry.iloc[0].y) == (first.lon, first.lat)
    state_plane = gis.to_geodataframe(litho)
    assert len(state_plane) == len(litho.features)
    assert state_plane.geometry.notna().all()
    assert state_plane.geometry.x.between(-85, -80).all()  # Ohio
    assert state_plane.geometry.y.between(38, 42).all()
    assert len(gis.to_geodataframe(litho, kind="no-such-kind")) == 0


@pytest.mark.parametrize("suffix", [".gpkg", ".parquet", ".geojson"])
def test_write_round_trips(odot, tmp_path, suffix):
    geopandas = pytest.importorskip("geopandas")
    pytest.importorskip("pyogrio")
    pytest.importorskip("pyarrow")
    from pydiggs import gis

    path = gis.write(odot, tmp_path / f"boreholes{suffix}")
    assert path.is_file()
    back = geopandas.read_parquet(path) if suffix == ".parquet" else geopandas.read_file(path)
    assert sorted(back["key"]) == sorted(f.key for f in odot.features)
    assert back.crs.to_epsg() == 4326
    expected = {f.key: f.location.geographic for f in odot.features}
    for key, point in zip(back["key"], back.geometry, strict=True):
        assert (point.x, point.y) == pytest.approx((expected[key].lon, expected[key].lat))
    if suffix == ".geojson":
        assert json.loads(path.read_text("utf-8"))["type"] == "FeatureCollection"
    gis.write(odot, path)  # an existing file is replaced


def test_write_refuses_other_formats(odot, tmp_path):
    pytest.importorskip("geopandas")
    from pydiggs import gis

    with pytest.raises(ValueError, match="GPKG, Parquet, GeoJSON"):
        gis.write(odot, tmp_path / "boreholes.shp")
    with pytest.raises(ValueError, match="GPKG, Parquet, GeoJSON"):
        gis.write(odot, tmp_path / "boreholes.gpkg", driver="ESRI Shapefile")
    # the driver decides when the suffix does not name a format
    assert gis.write(odot, tmp_path / "boreholes.json", driver="GeoJSON").is_file()


def test_doctests():
    pytest.importorskip("pandas")
    pytest.importorskip("pyproj")
    from pydiggs import gis

    for module in (_views, frames, gis):
        failed, attempted = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
        assert attempted > 0 and failed == 0, module.__name__


_GML = 'xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2"'


def _one_position(srs: str, coordinates: str, dim: int = 2):
    return pydiggs.read_bytes(
        (
            f'<Diggs {_GML}><samplingFeature><Borehole gml:id="b"><referencePoint>'
            f'<PointLocation><gml:pos srsName="urn:ogc:def:crs:EPSG::{srs}"'
            f' srsDimension="{dim}">{coordinates}</gml:pos></PointLocation></referencePoint>'
            "</Borehole></samplingFeature></Diggs>"
        ).encode()
    )


@pytest.mark.parametrize(
    ("srs", "coordinates", "dim", "lon", "lat"),
    [
        ("26915", "480000 4980000", 2, -93.2536, 44.9732),  # UTM 15N: east, north
        ("27700", "530000 180000", 2, -0.1284, 51.5040),  # British National Grid
        ("2193", "5920000 1757000", 2, 174.76, -36.85),  # NZTM: north first
        ("4267", "44.97 -93.25", 2, -93.2502, 44.9700),  # NAD27: another datum
        ("4269", "-93.25 44.97", 2, -93.25, 44.97),  # written longitude first
        ("4978", "-242000 -4513000 4485000", 3, -93.07, 44.97),  # geocentric
    ],
)
def test_transforming_twice_is_transforming_once(srs, coordinates, dim, lon, lat):
    pytest.importorskip("geopandas")
    from pydiggs import gis

    model = _one_position(srs, coordinates, dim)
    once = gis.transform(model)
    geographic = once.get("b").location.geographic
    assert (geographic.lon, geographic.lat) == (
        pytest.approx(lon, abs=2e-2),
        pytest.approx(lat, abs=2e-2),
    )
    assert gis.transform(once) == once
    assert gis.transform(gis.transform(model, "EPSG:4269"), "EPSG:4326") == once
    first, again = gis.to_geodataframe(model), gis.to_geodataframe(once)
    assert first.geometry.iloc[0].equals(again.geometry.iloc[0])
    assert pydiggs.Model.from_json(once.to_json()) == once


def test_a_later_target_replaces_an_earlier_one():
    pytest.importorskip("pyproj")
    from pydiggs import gis

    model = _one_position("4326", "44.97 -93.25")
    nad83 = gis.transform(model, "EPSG:4269")
    assert nad83.get("b").location.transformed_by is not None
    assert gis.transform(nad83, "EPSG:4326") == model  # back to what the reader gave


def test_a_result_named_like_another_tables_index_gets_a_suffix():
    from pydiggs.model import (
        Column,
        Domain,
        Measurement,
        Model,
        Provenance,
        ResultTable,
        SourceInfo,
    )

    def measurement(key, table):
        return Measurement(
            key=key,
            gml_id=None,
            element="Test",
            kind="test",
            name=None,
            names=(),
            description=None,
            remarks=(),
            provenance=Provenance(xpath=f"/{key}"),
            extra=(),
            results=(table,),
        )

    stepped = ResultTable(
        domain=Domain(shape="interval", index=(Column(name="top", type="number", values=(1.0,)),)),
        columns=(Column(name="n", type="number", values=(10.0,)),),
        row_count=1,
    )
    flat = ResultTable(columns=(Column(name="top", type="number", values=(99.0,)),), row_count=1)
    model = Model(
        schema_version="1.0",
        tool={"name": "pydiggs", "version": "x"},
        source=SourceInfo(
            name=None, sha256="0" * 64, size=0, diggs_version="3.0.0", namespace=None, detected=True
        ),
        complete=True,
        measurements=(measurement("a", stepped), measurement("b", flat)),
    )
    data = _views.columns(model, "results")
    assert data["top"] == [1.0, None]  # the place of each row
    assert data["top_value"] == [None, 99.0]  # the measured value named "top"
    # the name does not depend on what else is in the frame
    alone = _views.columns(model, "results", measurement="b")
    assert alone["top_value"] == [99.0] and "top" not in alone


def test_a_measured_elapsed_time_is_always_elapsed_time_value():
    lugeon = pydiggs.read(OFFICIAL / "2.6" / "LugeonTestExample.xml")
    assert any(
        (c.key or c.name) == "elapsed_time"
        for m in lugeon.measurements
        for t in m.results
        for c in t.columns
    )
    data = _views.columns(lugeon, "results")
    assert "elapsed_time_value" in data and "elapsed_time" not in data
