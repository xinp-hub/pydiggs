"""Inspector-derived checks (work card DX-1.3): depth, axis order, CRS, remote links."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

import pydiggs
from pydiggs import _geo_checks
from pydiggs.findings.model import Severity, Step
from pydiggs.semantic import validate_context_tree

ROOT = Path(__file__).resolve().parent.parent
DEPTH = "geometry.reported-depth-mismatch"
AXIS = "geometry.crs-axis-order-suspect"
UNRESOLVABLE = "geometry.crs-unresolvable"
REMOTE = "context.xlink-remote-unresolved"
NEW = {DEPTH, AXIS, UNRESOLVABLE, REMOTE}

_HEAD = (
    '<Diggs xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink" gml:id="d1">'
)


def _doc(body: str) -> bytes:
    return f"{_HEAD}{body}</Diggs>".encode()


def _borehole(
    depth: str,
    uom: str,
    pos_list: str,
    *,
    uom_labels: str | None = 'uomLabels="m m m"',
    srs: str = "urn:ogc:def:crs,crs:EPSG::26911,crs:EPSG::5703",
) -> str:
    labels = uom_labels or ""
    return (
        '<samplingFeature><Borehole gml:id="bh1">'
        f'<centerLine><LinearExtent gml:id="cl1" srsName="{srs}" srsDimension="3">'
        f"<gml:posList {labels}>{pos_list}</gml:posList></LinearExtent></centerLine>"
        f'<totalMeasuredDepth uom="{uom}">{depth}</totalMeasuredDepth>'
        "</Borehole></samplingFeature>"
    )


def _found(data: bytes, rule: str | None = None) -> list:
    report = pydiggs.validate_bytes(data, checks=["geometry", "context"])
    return [f for f in report.findings if f.rule_id in NEW and (rule is None or f.rule_id == rule)]


# --- reported depth vs centerLine -------------------------------------------------------


def test_depth_mismatch_is_flagged_on_the_feature():
    found = _found(_doc(_borehole("30", "m", "0 0 100 0 0 80")), DEPTH)
    assert len(found) == 1
    assert found[0].step == Step.GEOMETRY
    assert found[0].severity == Severity.WARNING
    assert found[0].gml_id == "bh1"
    assert "30 m" in found[0].message
    assert "20 m" in found[0].message


def test_inclined_centerline_within_tolerance_passes():
    # 3-4-5 triangle: 50 m long; reported 50.5 m is within 2 %.
    assert _found(_doc(_borehole("50.5", "m", "0 0 0 30 0 -40")), DEPTH) == []


def test_units_are_converted_per_axis():
    # Axes in ftUS, depth in ft: 100 ftUS is 100.0002 ft.
    body = _borehole("100", "ft", "0 0 500 0 0 400", uom_labels='uomLabels="ftUS ftUS ftUS"')
    assert _found(_doc(body), DEPTH) == []
    body = _borehole("120", "ft", "0 0 500 0 0 400", uom_labels='uomLabels="ftUS ftUS ftUS"')
    assert len(_found(_doc(body), DEPTH)) == 1


def test_local_cartesian_crs_axes_give_the_units():
    crs = (
        '<documentInformation><DocumentInformation gml:id="di"><crs><LocalCartesianCRS'
        ' gml:id="grid"><gml:cartesianCS><gml:CartesianCS gml:id="cs">'
        + "".join(
            f'<gml:axis><gml:CoordinateSystemAxis gml:id="a{i}" uom="ft"/></gml:axis>'
            for i in range(3)
        )
        + "</gml:CartesianCS></gml:cartesianCS></LocalCartesianCRS></crs>"
        "</DocumentInformation></documentInformation>"
    )
    good = _borehole("10", "ft", "0 0 0 0 0 -10", uom_labels=None, srs="#grid")
    bad = _borehole("3", "m", "0 0 0 0 0 -30", uom_labels=None, srs="#grid")
    assert _found(_doc(crs + good), DEPTH) == []
    assert len(_found(_doc(crs + bad), DEPTH)) == 1  # 30 ft is 9.14 m, not 3 m


@pytest.mark.parametrize(
    "body",
    [
        _borehole("30", "m", "0 0 100 0 0 80", uom_labels=None),  # units not stated
        _borehole("30", "m", "0 0 100 0 0 80", uom_labels='uomLabels="deg deg m"'),
        _borehole("30", "m", "0 0 100 0 0 80", uom_labels='uomLabels="m m"'),  # wrong count
        _borehole("30", "furlong", "0 0 100 0 0 80"),  # unknown depth unit
        _borehole("thirty", "m", "0 0 100 0 0 80"),
        _borehole("30", "m", "0 0 100 0 0"),  # not a whole number of positions
        _borehole("30", "m", "0 0 100"),  # one point
    ],
)
def test_depth_check_is_skipped_rather_than_guessed(body):
    assert _found(_doc(body), DEPTH) == []


@given(
    reported=st.floats(min_value=0.1, max_value=2000, allow_nan=False),
    ratio=st.floats(min_value=0.9, max_value=1.1, allow_nan=False),
)
def test_tolerance_boundary_matches_the_inspector(reported, ratio):
    computed = reported * ratio
    diff = abs(reported - computed)
    tolerance = max(0.02 * max(reported, computed), 0.05)
    assume(abs(diff - tolerance) > 1e-6 * max(1.0, tolerance))
    body = _borehole(repr(reported), "m", f"0 0 0 0 0 {-computed!r}")
    assert bool(_found(_doc(body), DEPTH)) == (diff > tolerance)


# --- axis order -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("srs", "pos", "flagged"),
    [
        ("urn:ogc:def:crs:EPSG::4326", "-91.2129 30.4291", True),  # lon, lat: swapped
        ("urn:ogc:def:crs:EPSG::4326", "30.4291 -91.2129", False),
        ("urn:ogc:def:crs:EPSG::4326", "-33.86 151.21", False),  # Sydney, correct order
        ("urn:ogc:def:crs:EPSG::4326", "-33.86 -70.65", False),  # Santiago: S and W is fine
        ("http://www.opengis.net/def/crs/EPSG/0/32611", "500000 4000000", False),  # projected
        (
            "https://www.opengis.net/def/crs-compound?1=http://www.opengis.net/def/crs/EPSG/0/"
            "4326%262=http://www.opengis.net/def/crs/EPSG/0/5703",
            "-91.2 30.4 10",
            True,
        ),
    ],
)
def test_axis_order_only_flags_impossible_latitudes(srs, pos, flagged):
    body = f'<gml:Point gml:id="p1" srsName="{srs}"><gml:pos>{pos}</gml:pos></gml:Point>'
    assert bool(_found(_doc(body), AXIS)) is flagged


def test_axis_order_reports_a_poslist_once():
    body = (
        '<gml:LineString gml:id="l1" srsName="EPSG:4326" srsDimension="2">'
        "<gml:posList>-91 30 -91.1 30.1 -91.2 30.2</gml:posList></gml:LineString>"
    )
    assert len(_found(_doc(body), AXIS)) == 1


# --- srsName resolution -----------------------------------------------------------------


@pytest.mark.parametrize(
    "srs",
    [
        "urn:ogc:def:crs:EPSG::4326",
        "urn:ogc:def:crs:EPSG:6.6:4326",
        "urn:ogc:def:crs,crs:EPSG::9357,crs:EPSG::5773",
        "http://www.opengis.net/def/crs/EPSG/0/26911",
        "https://www.opengis.net/def/crs-compound?1=http://www.opengis.net/def/crs/EPSG/0/4326"
        "&2=http://www.opengis.net/def/crs/EPSG/0/6360",
        "http://www.opengis.net/gml/srs/epsg.xml#4326",
        "#4326",
        "EPSG:2229",
        "urn:diggs:def:crs:DIGGS:0.1:4326_5714",
        "urn:def:crs:DIGGS:0.1:3734_6360",
        "https://diggsml.org/def/crs/DIGGS/0.1/lrs.xml#ft",
        "#d1",  # an element in the file
        "d1",  # a bare id, as 2.x files write it
    ],
)
def test_recognised_srs_names_resolve(srs):
    body = f'<gml:Point gml:id="p1" srsName="{srs.replace("&", "&amp;")}"/>'
    assert _found(_doc(body), UNRESOLVABLE) == []


@pytest.mark.parametrize(
    ("srs", "phrase"),
    [
        ("#missing-lsr", 'gml:id "missing-lsr"'),
        ("missing-lsr", 'gml:id "missing-lsr"'),
        ("http://example.org/crs.xml#a", "not a recognised"),
        ("urn:ogc:def:crs:FOO::1", "not a recognised"),
    ],
)
def test_unresolvable_srs_names_are_reported_once(srs, phrase):
    body = "".join(f'<gml:Point gml:id="p{i}" srsName="{srs}"/>' for i in range(3))
    found = _found(_doc(body), UNRESOLVABLE)
    assert len(found) == 1
    assert phrase in found[0].message
    assert "(3 uses)" in found[0].message
    assert found[0].gml_id == "p0"


# --- remote links -----------------------------------------------------------------------


def test_remote_links_are_info_once_per_document():
    body = (
        '<a xlink:href="https://diggsml.org/def/crs/DIGGS/0.1/lrm.xml#ft"/>'
        '<b xlink:href="https://diggsml.org/def/crs/DIGGS/0.1/lrm.xml#m"/>'
        '<c xlink:href="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#x"/>'
        '<d xlink:href="#d1"/>'
        '<e xlink:href="http://example.org/other.xml"/>'
    )
    found = _found(_doc(body), REMOTE)
    assert [f.severity for f in found] == [Severity.INFO, Severity.INFO]
    assert "lrm.xml" in found[0].message
    assert "(2 links)" in found[0].message
    assert "other.xml" in found[1].message


# --- integration ------------------------------------------------------------------------


def test_steps_select_their_own_rules():
    body = _borehole("30", "m", "0 0 100 0 0 80") + '<a xlink:href="http://example.org/x.xml"/>'
    geometry = pydiggs.validate_bytes(_doc(body), checks=["geometry"])
    assert {f.rule_id for f in geometry.findings} & NEW == {DEPTH}
    context = pydiggs.validate_bytes(_doc(body), checks=["context"])
    assert {f.rule_id for f in context.findings} & NEW == {REMOTE}


def test_legacy_context_check_output_is_unchanged():
    from lxml import etree

    root = etree.fromstring(_doc(_borehole("30", "m", "0 0 100 0 0 80")))
    legacy = validate_context_tree(root)
    assert {m.check for m in legacy.messages}.isdisjoint({104, 105, 106, 107})


def test_new_rules_are_catalogued():
    for rule_id, severity in [
        (DEPTH, Severity.WARNING),
        (AXIS, Severity.WARNING),
        (UNRESOLVABLE, Severity.WARNING),
        (REMOTE, Severity.INFO),
    ]:
        info = pydiggs.explain_rule(rule_id)
        assert info.known
        assert info.severities == (severity,)


def test_to_metres_rejects_non_lengths():
    assert _geo_checks.to_metres("m") == 1.0
    assert _geo_checks.to_metres("dega") is None
    assert _geo_checks.to_metres("kPa") is None
    assert _geo_checks.to_metres("nope") is None
