"""The read API's typed coverage (work package R2) and pydiggs.crs.

R1's tests (test_read_preview.py) hold the corpus, census, schema and streaming
properties; these pin what R2 types: simple values in ``params``, nested records as
components, construction, water strikes, a well's parent, samples' depths, lithology,
the five typed procedures, result-table domains and the CRS facts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydiggs import crs, model, preview
from pydiggs.errors import ResourceError

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official"


@pytest.fixture(scope="module")
def annotated():
    return preview.read(OFFICIAL / "3.0" / "Borehole-CPT_Example_Annotated.xml")


@pytest.fixture(scope="module")
def documentation():
    return preview.read(OFFICIAL / "2.6" / "DocumentationExample.xml")


@pytest.fixture(scope="module")
def cpt():
    return preview.read(OFFICIAL / "2.6" / "CPT_and_PorePressureDissipation.xml")


_MINIMAL = """<Diggs xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:xlink="http://www.w3.org/1999/xlink" gml:id="d">
 <samplingFeature>
  <Borehole gml:id="bh">
   <gml:name>BH-1</gml:name>
   <projectRef xlink:href="#p"/>
   <status>final</status>
   <totalMeasuredDepth uom="ft">20</totalMeasuredDepth>
   <boreholeType codeSpace="https://example.org/types">rotary</boreholeType>
   <boreholeType>auger</boreholeType>
   <bearing uom="dega" refAzimuth="true north">90</bearing>
   <casing><Casing gml:id="c1"><casingMaterial>steel</casingMaterial>
     <casingLocation><LinearExtent gml:id="le" srsName="#lsr" srsDimension="1">
      <gml:posList>0 5</gml:posList></LinearExtent></casingLocation></Casing></casing>
   <waterStrike><WaterStrike gml:id="ws"><status>after drilling</status>
     <waterStrikeReadings><WaterStrikeReading gml:id="wr">
      <dateTime>2024-01-02</dateTime>
      <waterLocation><PointLocation gml:id="wl" srsName="#lsr" srsDimension="1">
       <gml:pos>12</gml:pos></PointLocation></waterLocation>
     </WaterStrikeReading></waterStrikeReadings></WaterStrike></waterStrike>
   <linearReferencing><LinearSpatialReferenceSystem gml:id="lsr">
    <lrm><LinearReferencingMethod gml:id="lrm"><name>depth</name><units>ft</units>
    </LinearReferencingMethod></lrm></LinearSpatialReferenceSystem></linearReferencing>
  </Borehole>
 </samplingFeature>
 <measurement>
  <Test gml:id="t">
   <samplingFeatureRef xlink:href="#ws"/>
   <outcome><TestResult gml:id="r">
    <location><MultiPointLocation gml:id="m"><gml:posList srsName="#lsr" srsDimension="1">
     1 2 3</gml:posList></MultiPointLocation></location>
    <results><ResultSet><parameters><PropertyParameters gml:id="pp"><properties>
     <Property index="1"><propertyName>qc</propertyName><typeData>double</typeData>
      <propertyClass codeSpace="https://example.org/p#tip_resistance">qc</propertyClass>
      <uom>MPa</uom></Property></properties></PropertyParameters></parameters>
     <dataValues cs="," ts=" ">1.5 2.5 3.5</dataValues></ResultSet></results>
   </TestResult></outcome>
  </Test>
 </measurement>
</Diggs>
"""


@pytest.fixture(scope="module")
def minimal():
    return preview.read_bytes(_MINIMAL.encode(), name="minimal.xml")


# --- params: simple values -----------------------------------------------------------


def test_simple_values_become_typed_params(minimal):
    bh = minimal.get("bh")
    assert isinstance(bh, model.Feature)
    assert bh.params["status"] == "final"
    # A value with a uom that a typed field consumed is not repeated in params.
    assert "totalMeasuredDepth" not in bh.params
    assert bh.total_depth is not None
    assert bh.total_depth.value == 20.0


def test_repeated_attributed_and_reference_children_stay_out_of_params(minimal):
    bh = minimal.get("bh")
    assert isinstance(bh, model.Feature)
    extra = [e.qname.rpartition(":")[2] for e in bh.extra]
    # repeated -> extra (both copies, in order); other attributes -> extra
    assert extra.count("boreholeType") == 2
    assert "bearing" in extra
    assert "boreholeType" not in bh.params
    assert "bearing" not in bh.params
    # a plain reference is a link, not extra
    assert "projectRef" not in extra
    assert any(link.relation == "project" and link.source == "bh" for link in minimal.links)


def test_param_values_are_quantities_codes_or_text(annotated):
    spt = next(
        m.procedure
        for m in annotated.measurements
        if m.procedure is not None and m.procedure.type == "DrivenPenetrationTest"
    )
    efficiency = spt.params["hammerEfficiency"]
    assert isinstance(efficiency, model.Quantity)
    assert efficiency.uom == "%"
    assert spt.params["penetrationTestType"] == "SPT"
    feature = annotated.features[0]
    assert isinstance(feature.params["internalIdentifier"], model.Code)


# --- components ------------------------------------------------------------------------


def test_construction_and_water_strikes_are_components(minimal):
    bh = minimal.get("bh")
    assert isinstance(bh, model.Feature)
    (casing,) = bh.construction
    assert isinstance(casing, model.Component)
    assert (casing.key, casing.role, casing.element) == ("c1", "casing", "Casing")
    assert casing.params["casingMaterial"] == "steel"
    assert casing.interval is not None
    assert (casing.interval.top.value, casing.interval.base.value) == (0.0, 5.0)
    (strike,) = bh.water_strikes
    (reading,) = strike.components
    assert reading.role == "waterStrikeReadings"
    assert reading.params["dateTime"] == "2024-01-02"
    assert reading.location is not None
    assert reading.location.coordinates == (12.0,)


def test_components_are_nodes_that_links_resolve_to(minimal):
    assert minimal.get("wr") is not None
    keys = [n.key for n in minimal.nodes()]
    assert len(keys) == len(set(keys))
    (link,) = [link for link in minimal.links if link.relation == "samplingFeature"]
    assert link.target == "ws", "a link into a component targets the component"


def test_owner_of_finds_a_component(minimal):
    from pydiggs.findings.model import Finding, Severity, Step

    finding = Finding(
        step=Step.STRUCTURE,
        rule_id="structure.value-unparsed",
        severity=Severity.INFO,
        message="m",
        xpath="/Diggs[1]/samplingFeature[1]/Borehole[1]/casing[1]/Casing[1]/casingMaterial[1]",
    )
    owner = minimal.owner_of(finding)
    assert owner is not None
    assert owner.key == "c1"


def test_a_well_points_at_its_parent_hole(documentation):
    well = next(f for f in documentation.features if f.element == "Well")
    assert well.parent == "Location_B-001-0-20"
    roles = {c.role for c in well.construction}
    assert {"opening", "wellCasing"} <= roles
    opening = next(c for c in well.construction if c.role == "opening")
    assert opening.interval is not None
    assert (opening.interval.top.text, opening.interval.base.text) == ("19", "20")


def test_borehole_construction_methods_have_intervals(documentation):
    bh = documentation.get("Location_B-001-0-20")
    assert isinstance(bh, model.Feature)
    methods = [c for c in bh.construction if c.role == "constructionMethod"]
    assert [(m.interval.top.text, m.interval.base.text) for m in methods if m.interval] == [
        ("0.00", "21.00"),
        ("21.00", "41.00"),
    ]


# --- samples and lithology -------------------------------------------------------------


def test_samples_carry_their_activitys_depth_method_and_recovery():
    m = preview.read(OFFICIAL / "2.6" / "LithoGrading-from25.xml")
    sample = m.samples[0]
    activity = m.get(sample.sampling_activity or "")
    assert isinstance(activity, model.SamplingActivity)
    assert sample.interval == activity.interval
    assert sample.interval is not None
    assert sample.method == activity.method
    assert activity.method is not None
    assert activity.method.value == "SPT"
    assert sample.type == model.Code(value="Soil", code_space=None)
    assert sample.feature == activity.feature
    with_recovery = next(a for a in m.sampling_activities if a.recovery is not None)
    assert with_recovery.recovery is not None
    assert with_recovery.recovery.uom == "ft"


def test_lithology_layers_type_their_primary_lithology():
    m = preview.read(OFFICIAL / "2.6" / "LithoGrading-from25.xml")
    layers = [layer for o in m.observations for layer in o.layers]
    layer = next(layer for layer in layers if layer.colour is not None)
    assert layer.colour is not None
    assert layer.colour.code_space == "Munsell"
    assert layer.components[0].role == "primaryLithology"
    classified = next(layer for layer in layers if layer.classification)
    assert classified.classification[0].value
    assert classified.description


# --- the five typed procedures ---------------------------------------------------------


def test_spt_drive_sets(annotated):
    spt = next(
        m
        for m in annotated.measurements
        if m.procedure and m.procedure.type == "DrivenPenetrationTest"
    )
    assert spt.procedure is not None
    sets = [c for c in spt.procedure.components if c.role == "driveSet"]
    assert len(sets) == 3
    assert [s.params["index"] for s in sets] == ["1", "2", "3"]
    penetration = sets[0].params["penetration"]
    assert isinstance(penetration, model.Quantity)
    assert penetration.uom is not None
    table = spt.results[0]
    assert table.domain.shape == "interval"
    assert table.extent is not None, "an SPT result is measured over one interval"


def test_cpt_parameters_cone_and_depth_index(cpt):
    test = cpt.measurements[0]
    assert test.procedure is not None
    assert test.procedure.type == "StaticConePenetrationTest"
    tip = test.procedure.params["tipArea"]
    assert isinstance(tip, model.Quantity)
    assert (tip.value, tip.uom) == (15.0, "cm2")
    assert test.procedure.components[0].role == "testProcedureEquipment"
    table = test.results[0]
    (depth,) = table.domain.index
    assert (depth.name, depth.type, depth.uom) == ("position", "number", "m")
    assert len(depth.values) == table.row_count == 1400
    assert depth.values[:2] == (0.025, 0.05)
    assert table.domain.crs == "#cptsr1"
    assert table.column("tip_resistance") is not None


def test_atterberg_and_particle_size_tables_by_key():
    m = preview.read(OFFICIAL / "2.6" / "AtterbergExample-from25.xml")
    atterberg = [
        t for t in m.measurements if t.procedure and t.procedure.type == "AtterbergLimitsTest"
    ]
    assert atterberg
    keys = {c.key for t in atterberg for r in t.results for c in r.columns}
    assert {"liquid_limit", "plastic_limit"} <= keys
    m = preview.read(OFFICIAL / "2.6" / "LithoGrading-from25.xml")
    psd = next(t for t in m.measurements if t.procedure and t.procedure.type == "ParticleSizeTest")
    assert psd.procedure is not None
    assert any(c.role == "sieveAnalysis" for c in psd.procedure.components)


def test_water_level_monitoring_is_a_time_series():
    m = preview.read(OFFICIAL / "3.0" / "Piezometer_Example.xml")
    monitor = next(
        t for t in m.measurements if t.procedure and t.procedure.type == "WaterLevelMonitoring"
    )
    table = monitor.results[0]
    assert table.domain.shape == "temporal"
    (time,) = table.domain.index
    assert (time.name, time.type) == ("time", "datetime")
    assert len(time.values) == table.row_count
    assert table.column("water_depth") is not None


def test_elapsed_time_index_has_a_unit():
    m = preview.read(OFFICIAL / "2.6" / "PorePressureDissipationOnly.xml")
    tables = [t for meas in m.measurements for t in meas.results if t.domain.shape == "temporal"]
    (elapsed,) = tables[0].domain.index
    assert (elapsed.name, elapsed.uom, elapsed.values[:2]) == ("elapsed_time", "s", (0.0, 5.0))


def test_result_sets_are_not_kept_twice():
    m = preview.read(OFFICIAL / "2.6" / "PorePressureDissipationOnly.xml")
    text = m.to_json()
    data = json.loads(text)

    def walk(value):
        if isinstance(value, dict):
            if "qname" in value:
                assert not value["qname"].endswith("ResultSet"), value["qname"]
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for collection in ("features", "measurements", "others"):
        walk(data[collection])


def test_inventory_mode_empties_index_values_too(cpt):
    m = preview.read(OFFICIAL / "2.6" / "CPT_and_PorePressureDissipation.xml", results=False)
    table = m.measurements[0].results[0]
    assert table.row_count == 1400
    assert table.domain.index[0].values == ()
    assert cpt.measurements[0].results[0].domain.index[0].values != ()


def test_result_time_is_a_time_span():
    m = preview.read(OFFICIAL / "2.6" / "LabCompaction.xml")
    times = [t.result_time for t in m.measurements if t.result_time is not None]
    for time in times:
        assert isinstance(time, model.TimeSpan)


# --- depths along a linear referencing system ------------------------------------------


def test_depths_take_the_unit_of_their_linear_referencing_system(minimal):
    bh = minimal.get("bh")
    assert isinstance(bh, model.Feature)
    interval = bh.construction[0].interval
    assert interval is not None
    assert interval.top.uom == "ft"
    assert interval.top.si is not None
    assert interval.top.si.uom == "m"
    assert minimal.crs["#lsr"].unit == "ft"
    assert minimal.crs["#lsr"].name == "depth"
    (index,) = minimal.measurements[0].results[0].domain.index
    assert (index.uom, index.values) == ("ft", (1.0, 2.0, 3.0))


# --- pydiggs.crs -----------------------------------------------------------------------


def test_epsg_facts():
    pa = crs.epsg(2272)
    assert pa is not None
    assert (pa.type, pa.unit, pa.axis_order) == ("projected", "US survey foot", "east,north")
    wgs84 = crs.epsg(4326)
    assert wgs84 is not None
    assert (wgs84.type, wgs84.axis_order) == ("geographic_2d", "lat,lon")
    assert crs.epsg(999_999) is None


def test_epsg_source_names_the_dataset_and_its_terms():
    source = crs.epsg_source()
    assert source["version"].startswith("v")
    assert "IOGP" in source["owner"] or "Oil & Gas" in source["owner"]
    assert source["terms"] == "https://epsg.org/terms-of-use.html"


@pytest.mark.parametrize(
    ("srs_name", "form", "known", "axis_order"),
    [
        ("urn:ogc:def:crs:EPSG::4326", "epsg", True, "lat,lon"),
        ("http://www.opengis.net/def/crs/EPSG/0/2272", "epsg", True, "east,north"),
        ("urn:ogc:def:crs:OGC:1.3:CRS84", "ogc", True, "lon,lat"),
        ("urn:ogc:def:crs:EPSG::999999", "epsg", False, None),
        ("http://example.org/crs.xml#a", "unknown", False, None),
    ],
)
def test_parse(srs_name, form, known, axis_order):
    ref = crs.parse(srs_name)
    assert (ref.form, ref.known, ref.axis_order) == (form, known, axis_order)


def test_parse_splits_a_compound_code_and_compound_names():
    compound = crs.parse("urn:ogc:def:crs:EPSG::5498")
    assert compound.horizontal is not None
    assert compound.vertical is not None
    assert (compound.horizontal.epsg, compound.vertical.epsg) == (4269, 5703)
    assert compound.name == "NAD83 + NAVD88 height"
    assert compound.axis_order == "lat,lon,up"
    pair = crs.parse("urn:diggs:def:crs:DIGGS:0.1:4326_5714")
    assert pair.vertical is not None
    assert pair.vertical.epsg == 5714


def test_parse_local_ids():
    assert crs.parse("#lsr", local_ids=frozenset({"lsr"})).known
    assert not crs.parse("#lsr", local_ids=frozenset()).known
    assert crs.parse("#lsr").local == "lsr"


def test_every_corpus_epsg_code_is_in_the_subset():
    """The subset was built to cover the corpus; a new corpus file must not regress it."""
    from lxml import etree

    from pydiggs._geo_checks import parse_srs_name

    files = [*OFFICIAL.rglob("*.xml"), *(ROOT / ".cache" / "conformance").rglob("*.xml")]
    missing = set()
    for path in files:
        for el in etree.parse(str(path)).iter(etree.Element):
            srs = el.get("srsName")
            ref = parse_srs_name(srs) if srs else None
            for code in () if ref is None else (ref.horizontal, ref.vertical):
                if code is not None and crs.epsg(code) is None:
                    missing.add(code)
    assert not missing, f"rebuild with scripts/build_epsg_subset.py: {sorted(missing)}"


def test_a_broken_subset_raises_resource_error(monkeypatch):
    monkeypatch.setattr(crs, "_RESOURCE", "missing.json")
    crs._subset.cache_clear()
    try:
        with pytest.raises(ResourceError):
            crs.epsg(4326)
    finally:
        monkeypatch.undo()
        crs._subset.cache_clear()
