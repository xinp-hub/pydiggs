"""The internal DIGGS writer (``pydiggs._writer``): model in, DIGGS 3.0.0 XML out.

Property 5 of the conversion-core brief (writer fidelity): reading the written XML gives
a model equal to the built one on typed fields, ``params``, components, links and table
cells; and every output has zero validation errors. Models are built three ways: by hand
(a small site, as the AGS4 converter will build it), by hypothesis, and by reading the
official DIGGS 3.0 examples and reducing them to what the writer writes.
"""

from __future__ import annotations

import doctest
import importlib.util
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import _writer, _xml
from pydiggs._writer import _emit, _layout, write_model
from pydiggs._writer._layout import Ids, load_layout
from pydiggs.errors import ResourceError
from pydiggs.model import (
    Code,
    Column,
    Component,
    CrsRef,
    DocumentInfo,
    Domain,
    Feature,
    Interval,
    Link,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Observation,
    Other,
    Position,
    Procedure,
    Project,
    Provenance,
    Quantity,
    ResultTable,
    Sample,
    SamplingActivity,
    SourceInfo,
    TimeSpan,
)

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official" / "3.0"
SCRIPT = ROOT / "scripts" / "writer_layout.py"
LAYOUT = load_layout("3.0.0")
DIGGS = "http://diggsml.org/schemas/3"
PROPERTIES = "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"
OSGB = "http://www.opengis.net/def/crs/EPSG/0/27700"

# --- building models by hand ------------------------------------------------------


def _kind(element: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", element).lower()


def _node(cls: type, element: str, gml_id: str | None, **fields: Any) -> Any:
    """A node as the reader would shape it: key = gml:id, name = first of names."""
    names = tuple(fields.pop("names", ()))
    return cls(
        key=fields.pop("key", gml_id or f"@{element}"),
        gml_id=gml_id,
        element=element,
        kind=fields.pop("kind", _kind(element)),
        name=names[0] if names else None,
        names=names,
        description=fields.pop("description", None),
        remarks=tuple(fields.pop("remarks", ())),
        params=fields.pop("params", {}),
        provenance=Provenance(xpath=f"/{gml_id or element}"),
        extra=fields.pop("extra", ()),
        **fields,
    )


def _quantity(text: str, uom: str | None = None) -> Quantity:
    return Quantity(text=text, value=float(text), uom=uom)


def _depths(top: str, base: str, lrs: str, uom: str = "m") -> Interval:
    """A depth interval along ``lrs``; the reader copies the system's unit onto the ends."""
    return Interval(top=_quantity(top, uom), base=_quantity(base, uom), lrs=lrs)


def _link(source: str, relation: str, target: str, href: str | None = None) -> Link:
    return Link(
        source=source, relation=relation, target=target, href=href or f"#{target}", resolved=True
    )


def _column(key: str, values: tuple[Any, ...], **fields: Any) -> Column:
    return Column(
        name=fields.pop("name", key.replace("_", " ")),
        key=key,
        definition=fields.pop("definition", f"{PROPERTIES}#{key}"),
        type=fields.pop("type", "number"),
        values=values,
        **fields,
    )


def _document(**fields: Any) -> DocumentInfo:
    fields.setdefault("creation_date", "2026-01-31")
    return _node(DocumentInfo, "DocumentInformation", "doc", kind="document_info", **fields)


def _model(**fields: Any) -> Model:
    fields.setdefault("document", _document())
    return Model(
        schema_version="1.0",
        tool={"name": "test", "version": "0"},
        source=SourceInfo(
            name=None,
            sha256="0" * 64,
            size=0,
            diggs_version="3.0.0",
            namespace=DIGGS,
            detected=False,
        ),
        complete=True,
        **fields,
    )


def _local_crs(lrs: str, unit: str = "m") -> CrsRef:
    return CrsRef(srs_name=lrs, form="local", unit=unit, known=True, local=lrs[1:])


def _site() -> Model:
    """A small ground investigation: what the AGS4 converter builds for Tier A groups."""
    target = {"investigationTarget": "Natural Ground"}
    project = _node(Project, "Project", "P1", kind="project", names=("Harbour Road",))
    casing = _node(
        Component,
        "Casing",
        "BH01-casing",
        role="casing",
        params={"casingOutsideDiameter": _quantity("150", "mm")},
        interval=_depths("0", "6", "#BH01-lrs"),
    )
    borehole = _node(
        Feature,
        "Borehole",
        "BH01",
        names=("BH01", "Borehole 1"),
        description="Cable percussion borehole & rotary follow-on <north side>",
        remarks=("Terminated on refusal.", "Logged by A. N. Engineer"),
        params=target,
        location=Position(coordinates=(523456.5, 178901.25, 12.4), dim=3, crs=OSGB),
        centerline=(
            Position(coordinates=(523456.5, 178901.25, 12.4), dim=3, crs=OSGB),
            Position(coordinates=(523456.5, 178901.25, 1.9), dim=3, crs=OSGB),
        ),
        total_depth=_quantity("10.50", "m"),
        lrs="#BH01-lrs",
        when_constructed=TimeSpan(start="2025-03-04", end="2025-03-05"),
        construction=(casing,),
    )
    pit = _node(
        Feature,
        "TrialPit",
        "TP01",
        names=("TP01",),
        params=target,
        location=Position(coordinates=(523460.0, 178910.0), dim=2, crs=OSGB),
        centerline=(
            Position(coordinates=(523460.0, 178910.0), dim=2, crs=OSGB),
            Position(coordinates=(523463.0, 178910.0), dim=2, crs=OSGB),
        ),
    )
    activity = _node(
        SamplingActivity,
        "SamplingActivity",
        "SA1",
        kind="sampling_activity",
        params={**target, "activityType": "collect"},
        feature="BH01",
        interval=_depths("1.20", "1.65", "#BH01-lrs"),
        method=Code(value="SPT split spoon"),
        recovery=_quantity("0.40", "m"),
        date=TimeSpan(start="2025-03-04T10:15:00"),
    )
    sample = _node(
        Sample,
        "Sample",
        "S1",
        kind="sample",
        names=("BH01 1.20 D",),
        feature="BH01",
        sampling_activity="SA1",
        interval=activity.interval,
        method=activity.method,
        recovery=activity.recovery,
        type=Code(value="Soil"),
    )
    lithology = _node(
        Component,
        "Lithology",
        "L1-1-lith",
        role="primaryLithology",
        params={
            "classificationCode": Code(value="CL", code_space="urn:example:uscs"),
            "lithDescription": "Firm brown slightly sandy CLAY",
            "legendCode": Code(value="201", code_space="urn:example:legend"),
        },
    )
    layer = _node(
        LithologyLayer,
        "LithologyObservation",
        "L1-1",
        kind="lithology_layer",
        description="Firm brown slightly sandy CLAY",
        interval=_depths("0", "2.3", "#BH01-lrs"),
        classification=(
            Code(value="CL", code_space="urn:example:uscs"),
            Code(value="201", code_space="urn:example:legend"),
        ),
        components=(lithology,),
    )
    observation = _node(
        Observation,
        "LithologySystem",
        "L1",
        params={"lithologyClassificationType": "USCS"},
        system="LithologySystem",
        feature="BH01",
        layers=(layer,),
    )
    spt = _node(
        Measurement,
        "Test",
        "T-SPT",
        names=("SPT at 1.20 m",),
        params=target,
        feature="BH01",
        result_time=TimeSpan(start="2025-03-04T10:15:00"),
        procedure=Procedure(
            type="DrivenPenetrationTest",
            params={"penetrationTestType": "SPT", "hammerEfficiency": _quantity("72", "%")},
        ),
        results=(
            ResultTable(
                domain=Domain(shape="interval", crs="#BH01-lrs"),
                columns=(_column("n_value", (17.0,)),),
                row_count=1,
                extent=_depths("1.20", "1.65", "#BH01-lrs"),
            ),
        ),
    )
    limits = _node(
        Measurement,
        "Test",
        "T-LL",
        params=target,
        feature="BH01",
        samples=("S1",),
        procedure=Procedure(type="AtterbergLimitsTest"),
        results=(
            ResultTable(
                domain=Domain(shape="point"),
                columns=(
                    _column("liquid_limit", (41.0,)),
                    _column("plastic_limit", (None,)),
                    _column("checked", (True,), type="boolean", definition="urn:example:lab"),
                    _column("note", ("NP?",), type="string", definition="urn:example:lab"),
                    _column(
                        "tested",
                        ("2025-03-09T08:00:00",),
                        type="datetime",
                        definition="urn:example:lab",
                    ),
                ),
                row_count=1,
                location=Position(coordinates=(1.2,), dim=1, crs="#BH01-lrs"),
            ),
        ),
    )
    grading = _node(
        Measurement,
        "Test",
        "T-CPT",
        params=target,
        feature="BH01",
        procedure=Procedure(type="StaticConePenetrationTest"),
        results=(
            ResultTable(
                domain=Domain(
                    shape="point",
                    index=(
                        Column(
                            name="position",
                            key="position",
                            uom="m",
                            type="number",
                            values=(0.5, 1.0, 1.5),
                        ),
                    ),
                    crs="#BH01-lrs",
                ),
                columns=(
                    _column("tip_resistance", (1.25, None, 2e-3), uom="MPa"),
                    _column("sleeve_friction", (0.03, 0.04, 0.05), uom="MPa"),
                ),
                row_count=3,
            ),
        ),
    )
    links = (
        _link("BH01", "project", "P1"),
        _link("BH01", "linearElement", "BH01", "#BH01-cl"),
        _link("TP01", "project", "P1"),
        _link("SA1", "project", "P1"),
        _link("SA1", "samplingFeature", "BH01"),
        _link("S1", "project", "P1"),
        _link("S1", "samplingActivity", "SA1"),
        _link("S1", "sampleProduced", "SA1", "#SA1-S1"),
        _link("L1", "project", "P1"),
        _link("L1", "samplingFeature", "BH01"),
        _link("T-SPT", "project", "P1"),
        _link("T-SPT", "samplingFeature", "BH01"),
        _link("T-LL", "project", "P1"),
        _link("T-LL", "samplingFeature", "BH01"),
        _link("T-LL", "sample", "S1"),
        _link("T-CPT", "project", "P1"),
        _link("T-CPT", "samplingFeature", "BH01"),
    )
    return _model(
        projects=(project,),
        features=(borehole, pit),
        sampling_activities=(activity,),
        samples=(sample,),
        observations=(observation,),
        measurements=(spt, limits, grading),
        crs={"#BH01-lrs": _local_crs("#BH01-lrs")},
        links=links,
    )


# --- comparing a model with what reading its XML gives -----------------------------

#: Fields reading derives, which the writer neither needs nor writes: SI conversions,
#: geographic coordinates, where a node was in its file, and ``extra`` (the read-back
#: model keeps there the helper elements the writer makes: the linear referencing
#: system, ``SampleProduced``, the sampling method's ``Specification``).
_DERIVED = frozenset(
    {"si", "si_uom", "si_factor", "si_offset", "geographic", "transformed_by", "provenance"}
    | {"extra"}
)
_REFS = ("feature", "sampling_activity", "specimen", "parent")
_COLLECTIONS = ("projects", "features", "sampling_activities", "samples", "observations")


def _comparable(model: Model, made_up: frozenset[int] = frozenset()) -> dict[str, Any]:
    """Everything the writer supports, with node keys replaced by their position.

    Keys differ between a built model and its read-back only for nodes without a
    ``gml_id`` (the writer makes one up): ``made_up`` lists their positions, and their
    ids, and the hrefs that point at them, are left out of the comparison.
    """
    index = {node.key: number for number, node in enumerate(model.nodes())}

    def ref(key: str | None) -> int | None:
        return None if key is None else index[key]

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        out = {name: walk(item) for name, item in value.items() if name not in _DERIVED}
        if "key" in value and "element" in value:  # a node (a column has a key too)
            out["key"] = index[value["key"]]
            if out["key"] in made_up:
                out["gml_id"] = None
            for name in _REFS:
                if name in value:
                    out[name] = ref(value[name])
            if "samples" in value:
                out["samples"] = [ref(key) for key in value["samples"]]
        return out

    data = model.to_dict()
    body = {name: walk(data[name]) for name in ("document", *_COLLECTIONS, "measurements")}
    links = [
        {
            "source": ref(link.source),
            "relation": link.relation,
            "target": ref(link.target),
            "href": None if ref(link.target) in made_up else link.href,
        }
        for link in model.links
    ]
    # Reading lists links in document order; a built model may list them in any order.
    # What must survive is each node's links, and the order of those of one relation.
    body["links"] = sorted(links, key=lambda link: (link["source"], link["relation"]))
    return body


def _made_up(model: Model) -> frozenset[int]:
    return frozenset(n for n, node in enumerate(model.nodes()) if node.gml_id is None)


def _errors(report: pydiggs.Report) -> list[tuple[str, str]]:
    return [
        (finding.rule_id, finding.message)
        for finding in report.findings
        if finding.severity is pydiggs.Severity.ERROR
    ]


def _parse(xml: bytes) -> etree._Element:
    return _xml.parse_bytes(xml, None).getroot()


def _round_trip(model: Model) -> tuple[bytes, Model]:
    """Write ``model``, read it back and assert the two agree on what the writer writes."""
    xml = write_model(model)
    back = pydiggs.read_bytes(xml)
    made_up = _made_up(model)
    assert _comparable(back, made_up) == _comparable(model, made_up)
    assert back.complete
    assert back.source.diggs_version == "3.0.0"
    ids = [el.get(_emit._GML_ID) for el in _parse(xml).iter() if el.get(_emit._GML_ID)]
    assert len(ids) == len(set(ids))
    return xml, back


# --- the hand-built site ------------------------------------------------------------


def test_site_round_trips_and_validates() -> None:
    model = _site()
    xml, back = _round_trip(model)
    report = pydiggs.validate_bytes(xml)
    assert report.ok
    assert [f.rule_id for f in report.findings if f.severity is not pydiggs.Severity.INFO] == []
    assert back.notes == ()
    # Reading derives the rest: SI values, the CRS table, the units of depths.
    assert back.crs["#BH01-lrs"].unit == "m"
    assert back.features[0].total_depth == Quantity(
        text="10.50", value=10.5, uom="m", si=back.features[0].total_depth.si
    )
    assert back.samples[0].interval == back.sampling_activities[0].interval
    assert back.measurements[2].results[0].to_records()[1] == {
        "tip_resistance": None,
        "sleeve_friction": 0.04,
    }


def test_output_is_deterministic_and_plain() -> None:
    xml = write_model(_site())
    assert xml == write_model(_site())
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>\n<Diggs ")
    assert b"<!DOCTYPE" not in xml
    assert b"<!ENTITY" not in xml
    assert _xml.markup_entity(xml) is None
    root = _parse(xml)
    assert root.getroottree().docinfo.doctype == ""
    assert root.tag == f"{{{DIGGS}}}Diggs"
    assert root.get("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation") == (
        f"{DIGGS} https://diggsml.org/schemas/3.0.0/Diggs.xsd"
    )
    # No clock and no randomness: nothing in the output that is not from the model.
    assert not re.search(rb"20[2-9]\d-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+", xml)


def test_children_follow_the_schema_order_whatever_the_model_order() -> None:
    model = _site()
    borehole = model.features[0]
    shuffled = replace(borehole, params=dict(reversed(list(borehole.params.items()))))
    links = tuple(reversed(model.links))
    one = write_model(replace(model, features=(shuffled, model.features[1])))
    assert one == write_model(model)
    # Link order is kept within one element, so reversing every link changes nothing
    # here only because no element of the site has two links of one relation.
    assert write_model(replace(model, links=links)) == write_model(model)


@pytest.mark.parametrize(
    "text",
    [
        "</gml:name><evil xmlns='urn:x'/>",
        "]]> <![CDATA[ x ]]>",
        '<!DOCTYPE d [<!ENTITY e "boom">]> &e; &amp; &#x41;',
        "<?pi target?> <!-- comment -->",
        "tab\there, new\nline, quote \" and ' and \u00e9\u4e2d\U0001f600",
    ],
)
def test_text_is_data_never_markup(text: str) -> None:
    model = _site()
    borehole = replace(
        model.features[0],
        name=text,
        names=(text,),
        description=text,
        remarks=(text,),
        params={"investigationTarget": text},
    )
    model = replace(model, features=(borehole, model.features[1]))
    xml, back = _round_trip(model)
    assert back.features[0].description == text
    root = _parse(xml)
    assert not [el for el in root.iter() if not isinstance(el.tag, str)]  # no comment or PI
    assert not list(root.iter("{*}evil"))
    assert root.getroottree().docinfo.doctype == ""
    assert _xml.markup_entity(xml) is None


@pytest.mark.parametrize("bad", ["nul\x00", "vertical\x0btab", "\ud800", "\ufffe"])
def test_characters_xml_forbids_are_refused(bad: str) -> None:
    model = _site()
    borehole = replace(model.features[0], description=bad)
    with pytest.raises(ValueError):
        write_model(replace(model, features=(borehole, model.features[1])))


def test_number_columns_of_integer_properties_are_declared_integer() -> None:
    root = _parse(write_model(_site()))
    declared = {
        el.findtext(f"{{{DIGGS}}}propertyClass"): el.findtext(f"{{{DIGGS}}}typeData")
        for el in root.iter(f"{{{DIGGS}}}Property")
    }
    assert declared == {
        "n_value": "integer",
        "liquid_limit": "integer",
        "plastic_limit": "integer",
        "checked": "boolean",
        "note": "string",
        "tested": "dateTime",
        "tip_resistance": "double",
        "sleeve_friction": "double",
    }
    assert {"n_value", "blow_count", "liquid_limit"} <= _emit.integer_properties()
    assert "water_content" not in _emit.integer_properties()


# --- gml:id assignment ----------------------------------------------------------------


def _without_ids(model: Model) -> Model:
    """``model`` with no ``gml_id`` on its tests and no helper links (the writer's to make)."""
    tests = tuple(replace(m, key=f"@{n}", gml_id=None) for n, m in enumerate(model.measurements))
    rename = {old.key: new.key for old, new in zip(model.measurements, tests, strict=True)}
    links = tuple(
        replace(link, source=rename.get(link.source, link.source))
        for link in model.links
        if link.relation not in ("linearElement", "sampleProduced")
    )
    return replace(model, measurements=tests, links=links)


def test_missing_ids_are_made_up_the_same_way_every_time() -> None:
    model = _without_ids(_site())
    xml = write_model(model)
    assert xml == write_model(model)
    back = pydiggs.read_bytes(xml)
    assert [m.gml_id for m in back.measurements] == ["Test", "Test-2", "Test-3"]
    ids = {el.get(_emit._GML_ID) for el in _parse(xml).iter()} - {None}
    assert {
        "Diggs",
        "BH01.referencePoint",
        "BH01.centerLine",
        "BH01-lrs",
        "BH01-lrs.lrm",
        "BH01.whenConstructed",
        "BH01-casing.casingLocation",
        "SA1.samplingLocation",
        "SA1.samplingMethod",
        "SA1.samplingDate",
        "SA1.sampleProduced",
        "L1-1.location",
        "SA1.sampleProduced.location",
        "Test.resultTime",
        "Test.location",
        "Test.procedure",
        "Test-3.location",
    } <= ids
    # DIGGS 3.0.0 makes these ids optional, so none is made up.
    assert not {i for i in ids if i.endswith((".outcome", ".parameters", ".p1"))}
    # The helper links the model left out are in the document, so reading returns them.
    made = {(link.source, link.relation): link.href for link in back.links}
    assert made["BH01", "linearElement"] == "#BH01.centerLine"
    assert made["S1", "sampleProduced"] == "#SA1.sampleProduced"
    assert pydiggs.validate_bytes(xml).ok
    assert _comparable(replace(back, links=()), _made_up(model)) == _comparable(
        replace(model, links=()), _made_up(model)
    )


def test_a_made_up_id_never_takes_one_the_model_uses() -> None:
    model = _site()
    taken = replace(model.features[1], key="BH01.referencePoint", gml_id="BH01.referencePoint")
    links = tuple(
        replace(link, source=taken.key) if link.source == "TP01" else link for link in model.links
    )
    xml, _ = _round_trip(replace(model, features=(model.features[0], taken), links=links))
    ids = [el.get(_emit._GML_ID) for el in _parse(xml).iter() if el.get(_emit._GML_ID)]
    assert "BH01.referencePoint-2" in ids


def test_ids_are_linear_in_the_number_made() -> None:
    ids = Ids(["x"])
    made = [ids.new("x") for _ in range(2000)]
    assert made[:2] == ["x-2", "x-3"]
    assert made[-1] == "x-2001"
    assert ids._next["x"] == 2001  # each call starts where the last one stopped
    with pytest.raises(ValueError, match="more than once"):
        ids.claim("x-7", "here")
    with pytest.raises(ValueError, match="not a valid XML id"):
        ids.claim("7x", "here")


# --- what the writer refuses ------------------------------------------------------------


def _swap(model: Model, node: Node) -> Model:
    """``model`` with the top-level node of ``node``'s key replaced by ``node``."""
    for name in (*_COLLECTIONS, "measurements"):
        nodes = getattr(model, name)
        if any(old.key == node.key for old in nodes):
            changed = tuple(node if old.key == node.key else old for old in nodes)
            return replace(model, **{name: changed})
    raise AssertionError(node.key)


def _table(model: Model, **changes: Any) -> Model:
    """``model`` with the SPT test's result table changed."""
    test = model.measurements[0]
    return _swap(model, replace(test, results=(replace(test.results[0], **changes),)))


def _layer(model: Model, **changes: Any) -> Model:
    observation = model.observations[0]
    return _swap(model, replace(observation, layers=(replace(observation.layers[0], **changes),)))


def _drop_link(model: Model, source: str, relation: str) -> Model:
    kept = tuple(link for link in model.links if (link.source, link.relation) != (source, relation))
    return replace(model, links=kept)


_REFUSED: dict[str, tuple[Callable[[Model], Model], str]] = {
    "extra": (
        lambda m: _swap(m, replace(m.features[0], extra=(pydiggs.model.ExtraNode(qname="x"),))),
        "`extra` is not empty",
    ),
    "procedure extra": (
        lambda m: _swap(
            m,
            replace(
                m.measurements[0],
                procedure=replace(
                    m.measurements[0].procedure, extra=(pydiggs.model.ExtraNode(qname="x"),)
                ),
            ),
        ),
        "`extra` is not empty",
    ),
    "untyped member": (
        lambda m: replace(m, others=(_node(Other, "SamplingFeatureGroup", "g1"),)),
        "`associates` and `others` must be empty",
    ),
    "no document": (lambda m: replace(m, document=None), "requires document information"),
    "no creation date": (
        lambda m: replace(m, document=_document(creation_date=None)),
        "requires a creation_date",
    ),
    "unknown element": (
        lambda m: _swap(m, replace(m.features[1], element="Wormhole")),
        "DIGGS 3.0.0 has no object element 'Wormhole'",
    ),
    "unknown param": (
        lambda m: _swap(m, replace(m.features[1], params={"wormholeDepth": "1"})),
        "TrialPit has no child element 'wormholeDepth'",
    ),
    "typed field without an element": (
        lambda m: _swap(m, replace(m.projects[0], element="DocumentInformation")),
        "has no child element 'name'",
    ),
    "not a Test": (
        lambda m: _swap(m, replace(m.measurements[0], element="Monitor")),
        "a Test with exactly one result table",
    ),
    "two tables": (
        lambda m: _swap(m, replace(m.measurements[0], results=m.measurements[0].results * 2)),
        "a Test with exactly one result table",
    ),
    "table without a place": (lambda m: _table(m, extent=None), "needs a location"),
    "table with two places": (
        lambda m: _table(m, location=Position(coordinates=(1.0,), dim=1, crs="#BH01-lrs")),
        "not several",
    ),
    "extent on another system": (
        lambda m: _table(m, domain=Domain(shape="interval", crs="#other")),
        "domain.crs must be the extent's lrs",
    ),
    "no columns": (lambda m: _table(m, columns=()), "at least one column"),
    "ragged column": (lambda m: _table(m, row_count=2), "1 values for 2 rows"),
    "column type": (
        lambda m: _table(m, columns=(_column("n_value", (1.0,), type="complex"),)),
        "unknown column type 'complex'",
    ),
    "column without a key": (
        lambda m: _table(m, columns=(replace(_column("n_value", (1.0,)), key=None),)),
        "a column needs a key and a definition",
    ),
    "column without a definition": (
        lambda m: _table(m, columns=(_column("n_value", (1.0,), definition=None),)),
        "a column needs a key and a definition",
    ),
    "key is not the definition's fragment": (
        lambda m: _table(m, columns=(_column("n_value", (1.0,), definition="urn:x#other"),)),
        "key must be the fragment of its definition",
    ),
    "text cell with a space": (
        lambda m: _table(m, columns=(_column("note", ("two words",), type="string"),)),
        "not a single value without white space",
    ),
    "text cell with a comma": (
        lambda m: _table(m, columns=(_column("note", ("a,b",), type="string"),)),
        "not text without a comma",
    ),
    "number cell that is text": (
        lambda m: _table(m, columns=(_column("n_value", ("17",)),)),
        "is not a finite number",
    ),
    "boolean cell that is a number": (
        lambda m: _table(m, columns=(_column("checked", (1.0,), type="boolean"),)),
        "is not a boolean",
    ),
    "null row in a one-column table": (
        lambda m: _table(m, columns=(_column("n_value", (None,)),)),
        "row 1 of a one-column table is null",
    ),
    "row index of one row": (
        lambda m: _table(
            m,
            extent=None,
            domain=Domain(
                shape="interval",
                index=(_column("top", (1.0,)), _column("base", (2.0,))),
                crs="#BH01-lrs",
            ),
        ),
        "needs two rows or more",
    ),
    "row index of no rows": (
        lambda m: _table(
            m,
            extent=None,
            row_count=0,
            columns=(_column("n_value", ()),),
            domain=Domain(shape="point", index=(_column("position", ()),), crs="#BH01-lrs"),
        ),
        "a row index needs at least one row",
    ),
    "time index": (
        lambda m: _table(
            m,
            extent=None,
            domain=Domain(
                shape="temporal", index=(_column("elapsed_time", (1.0,), name="elapsed_time"),)
            ),
        ),
        "no location for a row index of \\['elapsed_time'\\]",
    ),
    "typed reference without a link": (
        lambda m: _drop_link(m, "SA1", "samplingFeature"),
        "feature 'BH01' has no 'samplingFeature' link",
    ),
    "samples entry without a link": (
        lambda m: _drop_link(m, "T-LL", "sample"),
        "samples entry 'S1' has no 'sample' link",
    ),
    "href that is not the target's id": (
        lambda m: replace(m, links=(*m.links, _link("TP01", "program", "P1", "#elsewhere"))),
        "not the gml:id of its target",
    ),
    "link with no element": (
        lambda m: replace(m, links=(*m.links, _link("TP01", "wormhole", "P1"))),
        "TrialPit has no child element for link 'wormhole'",
    ),
    "two sampleProduced links": (
        lambda m: replace(m, links=(*m.links, _link("S1", "sampleProduced", "SA1", "#other"))),
        "must be one link to 'SA1'",
    ),
    "surrounding white space": (
        lambda m: _swap(m, replace(m.features[1], description=" padded")),
        "surrounding white space",
    ),
    "empty param": (
        lambda m: _swap(m, replace(m.features[1], params={"investigationTarget": ""})),
        "is empty",
    ),
    "quantity param without a unit": (
        lambda m: _swap(m, replace(m.features[1], params={"bearing": _quantity("12")})),
        "needs a uom",
    ),
    "code param without a code space": (
        lambda m: _swap(m, replace(m.features[1], params={"status": Code(value="x")})),
        "needs a code_space",
    ),
    "name that is not the first of names": (
        lambda m: _swap(m, replace(m.features[1], name="other")),
        "name must be the first of names",
    ),
    "open interval": (
        lambda m: _swap(
            m,
            replace(
                m.sampling_activities[0], interval=Interval(top=_quantity("1"), lrs="#BH01-lrs")
            ),
        ),
        "needs both a top and a base",
    ),
    "time without a start or an end": (
        lambda m: _swap(m, replace(m.features[0], when_constructed=TimeSpan())),
        "needs a start or an end",
    ),
    "infinite coordinate": (
        lambda m: _swap(
            m,
            replace(m.features[0], location=Position(coordinates=(float("inf"), 0.0), dim=2)),
        ),
        "is not a finite number",
    ),
    "dim that is not the number of coordinates": (
        lambda m: _swap(m, replace(m.features[0], location=Position(coordinates=(1.0,), dim=2))),
        "dim must be its number of coordinates",
    ),
    "centerline in two systems": (
        lambda m: _swap(
            m,
            replace(
                m.features[1],
                centerline=(m.features[1].centerline[0], Position(coordinates=(1.0, 2.0), dim=2)),
            ),
        ),
        "must share one crs and dim",
    ),
    "id that is not an XML name": (
        lambda m: _swap(m, replace(m.features[1], gml_id="has space")),
        "'has space' is not a valid XML id",
    ),
    "id used twice": (
        lambda m: _swap(m, replace(m.features[1], gml_id="BH01")),
        "'BH01' is used more than once",
    ),
    "id that is the lrs": (
        lambda m: _swap(m, replace(m.features[1], gml_id="BH01-lrs")),
        "'BH01-lrs' is used more than once",
    ),
    "lrs that is not a fragment": (
        lambda m: _swap(m, replace(m.features[0], lrs="BH01-lrs")),
        "must be '#' and an id",
    ),
    "lrs without a centerline": (
        lambda m: _swap(m, replace(m.features[0], centerline=None)),
        "needs a centerline",
    ),
    "lrs without a unit": (lambda m: replace(m, crs={}), "must give the unit of its lrs"),
    "sample without a sampling activity": (
        lambda m: _swap(
            _drop_link(_drop_link(m, "S1", "samplingActivity"), "S1", "sampleProduced"),
            replace(
                m.samples[0],
                sampling_activity=None,
                feature=None,
                interval=None,
                method=None,
                recovery=None,
            ),
        ),
        "requires a sample to name its sampling activity",
    ),
    "sample depth that is not its activity's": (
        lambda m: _swap(m, replace(m.samples[0], interval=_depths("7", "8", "#BH01-lrs"))),
        "records a sample's interval on its sampling activity",
    ),
    "project client": (
        lambda m: _swap(m, replace(m.projects[0], client="ACME")),
        "client has no DIGGS element",
    ),
    "observation system": (
        lambda m: _swap(m, replace(m.observations[0], system="Other")),
        "system must be the element name",
    ),
    "layer description": (
        lambda m: _layer(m, description="something else"),
        "description is not its primary lithology's lithDescription",
    ),
    "layer classification": (
        lambda m: _layer(m, classification=()),
        "classification is not its primary lithology's classification codes",
    ),
    "layer colour": (
        lambda m: _layer(m, colour=Code(value="brown")),
        "colour is not a colorCode or colorName",
    ),
    "component place the element has no child for": (
        lambda m: _layer(
            m,
            components=(
                replace(
                    m.observations[0].layers[0].components[0],
                    interval=_depths("0", "1", "#BH01-lrs"),
                ),
            ),
        ),
        "Lithology has no child element for its interval",
    ),
    "component role": (
        lambda m: _swap(
            m,
            replace(
                m.features[0], construction=(replace(m.features[0].construction[0], role="x"),)
            ),
        ),
        "Borehole has no child element 'x'",
    ),
    "id on an element that takes none": (
        lambda m: _swap(
            m,
            replace(
                m.features[0],
                construction=(
                    replace(m.features[0].construction[0], element="Remark", role="remark"),
                ),
            ),
        ),
        "Remark takes no gml:id",
    ),
}


@pytest.mark.parametrize("case", sorted(_REFUSED))
def test_what_cannot_be_read_back_is_refused(case: str) -> None:
    change, message = _REFUSED[case]
    write_model(_site())  # the site itself is written
    with pytest.raises(ValueError, match=message):
        write_model(change(_site()))


def test_only_diggs_3_0_0_is_written() -> None:
    assert _writer.DIGGS_VERSIONS == ("3.0.0",)
    for version in ("2.6", "2.5.a", "3.1-dev", "3.0", "", "3.0.0 "):
        with pytest.raises(ValueError, match=r"the writer targets DIGGS 3\.0\.0, not"):
            write_model(_site(), version)


def test_external_links_keep_their_href() -> None:
    model = _site()
    href = "https://example.org/programs.xml#p9"
    outside = Link(source="TP01", relation="program", target=None, href=href, resolved=False)
    xml, back = _round_trip(replace(model, links=(*model.links[:3], outside, *model.links[3:])))
    assert back.links[3] == outside
    assert not _errors(pydiggs.validate_bytes(xml))


# --- the official DIGGS 3.0 examples, reduced to what the writer writes -----------------


def _walk(node: dict[str, Any]) -> list[dict[str, Any]]:
    """A node dict and every node nested in it (layers, components)."""
    found = [node]
    nested = [node.get(name) or [] for name in ("construction", "water_strikes", "components")]
    nested.append(node.get("layers") or [])
    nested.append((node.get("procedure") or {}).get("components") or [])
    for group in nested:
        for child in group:
            found.extend(_walk(child))
    return found


def _writable_test(test: dict[str, Any]) -> bool:
    """A ``Test`` with one result table that has columns and a place the writer writes."""
    if test["element"] != "Test" or len(test["results"]) != 1:
        return False
    table = test["results"][0]
    names = [column["name"] for column in table["domain"]["index"]]
    positions = (
        ["position"] if len(names) == 1 else [f"position_{i + 1}" for i in range(len(names))]
    )
    if not table["columns"] or (names and names not in (["top", "base"], positions)):
        return False
    if any(column["key"] is None or column["definition"] is None for column in table["columns"]):
        return False
    return bool(names or table["location"] or table["extent"])


def _codes(params: dict[str, Any], names: tuple[str, ...]) -> list[dict[str, Any]]:
    values = [params.get(name) for name in names]
    return [
        {"value": v, "code_space": None} if isinstance(v, str) else v
        for v in values
        if isinstance(v, str) or (isinstance(v, dict) and "code_space" in v)
    ]


def _colours(component: dict[str, Any]) -> list[dict[str, Any]]:
    found = _codes(component["params"], ("colorCode", "colorName"))
    for child in component["components"]:
        found.extend(_colours(child))
    return found


def _cut_members(data: dict[str, Any], cut: Counter[str]) -> dict[str, str]:
    """Drop the top-level members the writer has no element for; sample -> its activity."""
    cut["others"] = len(data["others"]) + len(data["associates"])
    data["others"], data["associates"] = [], []
    # Only a lithology system has typed content (its layers); the others are all `extra`.
    cut["observations"] = sum(o["element"] != "LithologySystem" for o in data["observations"])
    data["observations"] = [o for o in data["observations"] if o["element"] == "LithologySystem"]
    cut["measurements"] = sum(not _writable_test(m) for m in data["measurements"])
    data["measurements"] = [m for m in data["measurements"] if _writable_test(m)]
    activities = {a["key"] for a in data["sampling_activities"]}
    activity_of = {
        link["source"]: link["target"]
        for link in reversed(data["links"])
        if link["relation"] == "samplingActivity" and link["target"] in activities
    }
    kept = [s for s in data["samples"] if s["key"] in activity_of]
    cut["samples"] = len(data["samples"]) - len(kept)
    data["samples"] = kept
    return activity_of


def _trim(data: dict[str, Any], nodes: dict[str, dict[str, Any]], cut: Counter[str]) -> None:
    """Empty every ``extra`` and drop what the writer refuses on a node."""
    for node in nodes.values():
        cut["extra"] += len(node["extra"])
        node["extra"] = []
        if node.get("procedure"):
            cut["extra"] += len(node["procedure"]["extra"])
            node["procedure"]["extra"] = []
        if node["gml_id"] is not None and not _layout._NCNAME.fullmatch(node["gml_id"]):
            node["gml_id"] = None
        lay = LAYOUT.element(node["element"])
        if node.get("interval") and node.get("location") and lay.interval == lay.location:
            node["location"] = None  # one child element holds both: the model cannot say which
            cut["places"] += 1
    registry = {
        link["source"]
        for link in data["links"]
        if link["relation"] == "lrm" and link["target"] is None
    }
    for feature in data["features"]:
        unit = (data["crs"].get(feature["lrs"] or "") or {}).get("unit")
        known = unit in _emit._LRM_NAMES or feature["key"] in registry
        if feature["lrs"] and (feature["centerline"] is None or not known):
            feature["lrs"] = None
            cut["lrs"] += 1


def _kept_link(
    link: dict[str, Any], nodes: dict[str, dict[str, Any]], activity_of: dict[str, str]
) -> dict[str, Any] | None:
    """``link`` as the writer can write it, or None."""
    source, target = nodes.get(link["source"]), nodes.get(link["target"] or "")
    relation = link["relation"]
    if source is None:
        return None
    children = LAYOUT.element(source["element"]).by_local
    placed = f"{relation}Ref" in children or relation in children
    if relation == "linearElement" and source.get("lrs"):
        # A dangling one is pointed at the centerline, where the writer would put it.
        made = {"target": link["source"], "href": f"#{source['gml_id']}.centerLine"}
        return link if link["target"] == link["source"] else {**link, **made, "resolved": True}
    if relation == "lrm" and source.get("lrs"):
        return link if link["target"] is None else None
    if relation == "sampleProduced" and source["key"] in activity_of:
        return link if link["target"] == activity_of[source["key"]] else None
    if target is None:  # an external reference is kept; one that does not resolve is not
        external = link["target"] is None and not link["href"].startswith("#")
        return link if external and placed else None
    return link if placed and link["href"] == f"#{target['gml_id']}" else None


def _rederive(data: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> None:
    """Fill the derived fields as the reader derives them from the links and nodes left."""
    targets: dict[tuple[str, str], list[str]] = {}
    for link in data["links"]:
        if link["target"] is not None:
            targets.setdefault((link["source"], link["relation"]), []).append(link["target"])
    for node in nodes.values():
        for name, relation in _emit._TYPED_REFS:
            if name in node:
                node[name] = next(iter(targets.get((node["key"], relation), [])), None)
        if "samples" in node:
            node["samples"] = list(dict.fromkeys(targets.get((node["key"], "sample"), [])))
    by_key = {a["key"]: a for a in data["sampling_activities"]}
    for sample in data["samples"]:
        activity = by_key[sample["sampling_activity"]]
        for name in ("interval", "method", "recovery", "feature"):
            sample[name] = activity[name]
    for observation in data["observations"]:
        for layer in observation["layers"]:
            primary = next(
                (c for c in layer["components"] if c["role"] == "primaryLithology"), None
            )
            params = primary["params"] if primary else {}
            described = params.get("lithDescription")
            layer["description"] = described if isinstance(described, str) else None
            layer["classification"] = _codes(params, _emit._CLASSIFICATION)
            if primary is None or layer["colour"] not in _colours(primary):
                layer["colour"] = None


def _reduce(model: Model) -> tuple[Model, Counter[str]]:
    """``model`` cut down to what the writer writes; and how much of each thing was cut.

    The writer does not write ``extra``, untyped members, observation systems without
    typed layers, or measurements other than a ``Test`` with one located result table. A
    file's own defects go too: ids that are not XML names, references that do not
    resolve, samples without a sampling activity. Derived fields are then recomputed as
    the reader derives them.
    """
    data = model.to_dict()
    cut: Counter[str] = Counter()
    activity_of = _cut_members(data, cut)
    tops = [data["document"], *(n for name in (*_COLLECTIONS, "measurements") for n in data[name])]
    nodes = {node["key"]: node for top in tops for node in _walk(top)}
    _trim(data, nodes, cut)
    links = []
    seen = set()
    for link in data["links"]:
        kept = _kept_link(link, nodes, activity_of)
        key = (link["source"], link["relation"], link["href"])
        if kept is not None and key not in seen:
            seen.add(key)
            links.append(kept)
    cut["links"] = len(data["links"]) - len(links)
    data["links"] = links
    _rederive(data, nodes)
    return Model.from_dict(data), cut


#: What is left of each example once reduced: features, sampling activities, samples,
#: lithology systems and tests. Pinned so that a reducer that cuts everything shows up.
_KEPT = {
    "Aeromag.xml": (1, 0, 0, 0, 0),
    "Borehole-CPT_Example_Annotated.xml": (2, 35, 35, 1, 99),
    "GeophysicalFieldSurveyExamples.xml": (3, 0, 0, 0, 0),
    "MWDExample.xml": (1, 0, 0, 0, 0),
    "Piezometer_Example.xml": (2, 1, 1, 0, 1),
    "PorePressureDissipation_Example.xml": (1, 0, 0, 0, 1),
    "TrenchWallExample.xml": (1, 0, 0, 1, 0),
}
#: Examples that write and read back equal, but are not valid DIGGS once reduced, because
#: the reader keeps a child their schema requires in ``extra``, which the writer refuses.
_NOT_VALID_REDUCED = {
    "Aeromag.xml": "GP_ArealSurvey: its required geophysics members are in `extra`",
    "GeophysicalFieldSurveyExamples.xml": (
        "its features repeat investigationTarget (a repeated value is `extra`, not a param)"
    ),
    "TrenchWallExample.xml": "TrenchWall: its required referenceEdge is in `extra`",
}


@pytest.mark.parametrize("path", sorted(OFFICIAL.glob("*.xml")), ids=lambda p: p.name)
def test_official_examples_round_trip(path: Path) -> None:
    original = pydiggs.read(str(path))
    model, _cut = _reduce(original)
    kept = (model.features, model.sampling_activities, model.samples, model.observations)
    assert (*map(len, kept), len(model.measurements)) == _KEPT[path.name]
    xml, _back = _round_trip(model)
    assert xml == write_model(model)
    new = set(_errors(pydiggs.validate_bytes(xml))) - set(_errors(pydiggs.validate(str(path))))
    if path.name in _NOT_VALID_REDUCED:
        assert new, "this example is valid when reduced: take it off the list"
    else:
        assert not new


def test_the_official_examples_are_all_listed() -> None:
    assert sorted(_KEPT) == sorted(p.name for p in OFFICIAL.glob("*.xml"))
    assert set(_NOT_VALID_REDUCED) < set(_KEPT)


def test_a_model_read_from_diggs_2_6_is_written_as_3_0_0() -> None:
    """The input's own version does not matter: the output is always DIGGS 3.0.0."""
    path = ROOT / "tests" / "fixtures" / "official" / "2.6" / "cptExample-from25.xml"
    original = pydiggs.read(str(path))
    assert original.source.diggs_version == "2.6"
    model, _cut = _reduce(original)
    _xml_bytes, back = _round_trip(model)
    assert back.source.namespace == DIGGS
    assert len(back.measurements) == 1
    assert (
        back.measurements[0].results[0].row_count == original.measurements[0].results[0].row_count
    )


# --- the layout file ---------------------------------------------------------------------


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("writer_layout", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def test_layout_is_what_the_script_generates_from_the_xsd() -> None:
    script = _script()
    assert sorted(script.PROFILES) == list(_writer.DIGGS_VERSIONS) == ["3.0.0"]
    assert script.check(script.PROFILES["3.0.0"]) == []
    assert script.main(["--check"]) == 0


def test_layout_check_command_exits_0() -> None:
    done = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, check=False
    )
    assert (done.returncode, done.stdout.strip()) == (0, "writer layout OK")


@pytest.mark.parametrize("change", ["edited", "missing", "digest"])
def test_layout_check_fails_on_a_stale_file(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    profile = script.PROFILES["3.0.0"]
    pkg = tmp_path / "pydiggs"
    (pkg / "_writer").mkdir(parents=True)
    layout = profile.out.read_text(encoding="utf-8")
    provenance = script.PROVENANCE.read_text(encoding="utf-8")
    if change == "edited":
        layout = layout.replace('"creationDate",', '"creationDate",\n  "inventedDate",', 1)
    if change == "digest":
        provenance = provenance.replace(script._sha256(layout), "0" * 64)
    if change != "missing":
        (pkg / "_writer" / profile.out.name).write_text(layout, encoding="utf-8")
    (pkg / "PROVENANCE.toml").write_text(provenance, encoding="utf-8")
    stale = type(profile)(version=profile.version, namespaces=profile.namespaces)
    out = pkg / "_writer" / profile.out.name
    monkeypatch.setattr(type(profile), "out", property(lambda self: out))
    monkeypatch.setattr(type(profile), "out_rel", property(lambda self: f"_writer/{out.name}"))
    monkeypatch.setattr(script, "PROVENANCE", pkg / "PROVENANCE.toml")
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script, "PROFILES", {"3.0.0": stale})
    assert script.check(stale)
    assert script.main(["--check"]) == 1
    assert "problem(s)" in capsys.readouterr().out
    # Writing repairs it: the file and its digest are current again.
    assert script.main([]) == 0
    assert script.check(stale) == []


def test_layout_agrees_with_the_official_examples() -> None:
    """Every object in the valid official files has its children in the layout's order."""
    seen: Counter[str] = Counter()
    for path in sorted(OFFICIAL.glob("*.xml")):
        for el in _xml.parse(path).getroot().iter():
            if not isinstance(el.tag, str):
                continue
            name = etree.QName(el).localname
            lay = LAYOUT.elements.get(name)
            if lay is None or lay.tag != el.tag:
                continue
            seen[name] += 1
            order = {tag: number for number, tag in enumerate(lay.children)}
            places = [order.get(child.tag) for child in el if isinstance(child.tag, str)]
            assert None not in places, (path.name, name, [c.tag for c in el])
            assert places == sorted(places), (path.name, name)
            if el.get(_emit._GML_ID) is None:
                assert lay.id != "required", (path.name, name)
            else:
                assert lay.id is not None, (path.name, name)
    assert len(seen) > 60
    assert seen["Borehole"] and seen["Test"] and seen["Property"] > 100


def test_layout_ships_with_the_package_and_names_the_namespaces() -> None:
    resource = resources.files("pydiggs._writer").joinpath("layout-3.0.0.toml")
    assert resource.is_file()
    assert [p.name for p in Path(_writer.__file__).parent.glob("layout-*.toml")] == [
        "layout-3.0.0.toml"
    ]
    assert LAYOUT.namespaces["diggs"] == DIGGS
    borehole = LAYOUT.element("Borehole")
    assert (borehole.tag, borehole.id) == (f"{{{DIGGS}}}Borehole", "required")
    assert borehole.children.index(borehole.by_local["referencePoint"]) < borehole.children.index(
        borehole.by_local["centerLine"]
    )
    assert LAYOUT.element("Casing").interval == f"{{{DIGGS}}}casingLocation"
    assert LAYOUT.element("ResultSet").id is None
    assert load_layout("3.0.0") is LAYOUT  # read once


def test_a_broken_layout_is_a_resource_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_layout.tomllib, "loads", lambda text: {"format": 99})
    with pytest.raises(ResourceError, match=r"layout-3\.0\.0\.toml is unreadable"):
        load_layout.__wrapped__("3.0.0")
    with pytest.raises(ValueError, match=r"the writer targets DIGGS 3\.0\.0"):
        load_layout.__wrapped__("2.6")


# --- internal, documented, linear -----------------------------------------------------


def test_the_writer_is_internal() -> None:
    assert "write_model" not in pydiggs.__all__
    assert not hasattr(pydiggs, "write_model")
    code = "import pydiggs, sys; print('pydiggs._writer' in sys.modules)"
    done = subprocess.run(  # noqa: S603 - our own interpreter, fixed code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(ROOT / "src")},
    )
    assert done.stdout.strip() == "False"


@pytest.mark.parametrize("module", [_writer, _layout, _emit], ids=lambda m: m.__name__)
def test_docstring_examples_run(module: Any) -> None:
    result = doctest.testmod(module)
    assert result.failed == 0
    assert result.attempted > 0 or module is _emit


def _many(count: int) -> Model:
    """A site with ``count`` more tests, each a table of ``count`` rows."""
    model = _site()
    cpt = model.measurements[2]
    depths = tuple(float(n) for n in range(count))
    table = ResultTable(
        domain=Domain(
            shape="point",
            index=(Column(name="position", key="position", uom="m", type="number", values=depths),),
            crs="#BH01-lrs",
        ),
        columns=(_column("tip_resistance", depths, uom="MPa"),),
        row_count=count,
    )
    tests = tuple(replace(cpt, key=f"X{n}", gml_id=f"X{n}", results=(table,)) for n in range(count))
    links = tuple(
        _link(test.key, relation, target)
        for test in tests
        for relation, target in (("project", "P1"), ("samplingFeature", "BH01"))
    )
    return replace(model, measurements=(*model.measurements, *tests), links=(*model.links, *links))


@pytest.mark.scaling
def test_output_grows_with_the_model_not_faster() -> None:
    small, large = (len(write_model(_many(count))) for count in (20, 40))
    base = len(write_model(_site()))
    # 2x the tests, each with 2x the rows: 2x the elements and 4x the cells, no more.
    assert (large - base) / (small - base) < 4.2
    one, two = (sum(1 for _ in _parse(write_model(_many(count))).iter()) for count in (20, 40))
    nodes = sum(1 for _ in _parse(write_model(_site())).iter())
    assert (two - nodes) == 2 * (one - nodes)


# --- property 5 over generated models ---------------------------------------------------

_TEXT = (
    st.text(
        alphabet=st.sampled_from("abcXYZ019 .,;:-_/()[]{}<>&\"'#%+=*!?@\\|~^$\n\téß中Ж\U0001f600"),
        min_size=1,
        max_size=24,
    )
    .map(str.strip)
    .filter(bool)
)
_WORD = st.text(alphabet="abcXYZ019._-/<>&'\"#%é", min_size=1, max_size=10)
_NUMBER = st.floats(allow_nan=False, allow_infinity=False, width=64)
_DEPTH = st.integers(min_value=1, max_value=99_999).map(lambda n: f"{n / 100:.2f}")
_CELLS: dict[str, st.SearchStrategy[Any]] = {
    "number": _NUMBER,
    "string": _WORD,
    "boolean": st.booleans(),
    "datetime": st.sampled_from(["2025-03-04", "2025-03-04T10:15:00", "2025-03-04T10:15:00Z"]),
}


@st.composite
def _tables(draw: st.DrawFn, lrs: str) -> ResultTable:
    """A result table with any mix of column types and one of the four kinds of place."""
    place = draw(st.sampled_from(["point", "extent", "intervals", "positions"]))
    least = {"intervals": 2, "positions": 1}.get(place, 0)  # what a row index needs
    rows = draw(st.integers(min_value=least, max_value=5))
    columns = []
    for number in range(draw(st.integers(min_value=1, max_value=4))):
        kind = draw(st.sampled_from(sorted(_CELLS)))
        values = draw(st.lists(st.none() | _CELLS[kind], min_size=rows, max_size=rows))
        if number == 0:  # a row needs one value, or its tuple would be empty
            values = [draw(_CELLS[kind]) if v is None else v for v in values]
        columns.append(
            Column(
                name=draw(_TEXT),
                key=f"gen_{number}",
                definition=draw(st.sampled_from(["urn:example:generated", "urn:example:x#"])),
                uom=draw(st.none() | st.sampled_from(["kPa", "%", "m"])),
                type=kind,
                null_value=draw(st.none() | st.just("-999")),
                values=tuple(values),
            )
        )
    domain, location, extent = Domain(), None, None
    if place == "point":
        location = Position(coordinates=(draw(_NUMBER),), dim=1, crs=lrs)
        domain = Domain(shape="point")
    elif place == "extent":
        extent = _depths(draw(_DEPTH), draw(_DEPTH), lrs)
        domain = Domain(shape="interval", crs=lrs)
    else:
        names = ["top", "base"] if place == "intervals" else ["position"]
        index = tuple(
            Column(
                name=name,
                key=name,
                uom="m",
                type="number",
                values=tuple(draw(st.lists(_NUMBER, min_size=rows, max_size=rows))),
            )
            for name in names
        )
        shape = "interval" if place == "intervals" else "point"
        domain = Domain(shape=shape, index=index, crs=lrs)
    return ResultTable(
        domain=domain, columns=tuple(columns), row_count=rows, location=location, extent=extent
    )


@st.composite
def _models(draw: st.DrawFn) -> Model:
    """A site of generated boreholes, each with a casing, a sample, a layer and tests."""
    target = {"investigationTarget": "Natural Ground"}
    features, activities, samples, observations, tests = [], [], [], [], []
    links: list[Link] = []
    crs = {}
    for number in range(draw(st.integers(min_value=1, max_value=3))):
        hole = f"H{number}"
        lrs = f"#{hole}-lrs"
        crs[lrs] = _local_crs(lrs)
        names = tuple(draw(st.lists(_TEXT, min_size=1, max_size=3)))  # DIGGS requires one
        top = (draw(_NUMBER), draw(_NUMBER), draw(_NUMBER))
        casing = [
            _node(
                Component,
                "Casing",
                draw(st.none() | st.just(f"{hole}-c{n}")),
                key=f"{hole}-c{n}",
                role="casing",
                names=tuple(draw(st.lists(_TEXT, max_size=1))),
                params={"casingOutsideDiameter": _quantity(draw(_DEPTH), "mm")},
                interval=_depths(draw(_DEPTH), draw(_DEPTH), lrs),
            )
            for n in range(draw(st.integers(min_value=0, max_value=2)))
        ]
        features.append(
            _node(
                Feature,
                "Borehole",
                hole,
                names=names,
                description=draw(st.none() | _TEXT),
                remarks=tuple(draw(st.lists(_TEXT, max_size=2))),
                params=target,
                location=Position(coordinates=top, dim=3, crs=OSGB),  # required too
                centerline=(
                    Position(coordinates=top, dim=3, crs=OSGB),
                    Position(coordinates=(top[0], top[1], draw(_NUMBER)), dim=3, crs=OSGB),
                ),
                total_depth=draw(st.none() | _DEPTH.map(lambda t: _quantity(t, "m"))),
                lrs=lrs,
                when_constructed=draw(
                    st.none()
                    | st.sampled_from(
                        [TimeSpan(start="2025-03-04"), TimeSpan(end="2025-03-05T12:00:00")]
                    )
                ),
                construction=tuple(casing),
            )
        )
        links += [_link(hole, "project", "P1"), _link(hole, "linearElement", hole, f"#{hole}-cl")]
        if draw(st.booleans()):
            activity = _node(
                SamplingActivity,
                "SamplingActivity",
                f"{hole}-sa",
                kind="sampling_activity",
                params={**target, "activityType": "collect"},
                feature=hole,
                interval=_depths(draw(_DEPTH), draw(_DEPTH), lrs),
                method=draw(st.none() | _TEXT.map(lambda t: Code(value=t))),
                recovery=draw(st.none() | _DEPTH.map(lambda t: _quantity(t, "m"))),
            )
            activities.append(activity)
            links += [_link(activity.key, "project", "P1")]
            links += [_link(activity.key, "samplingFeature", hole)]
            for n in range(draw(st.integers(min_value=0, max_value=2))):
                key = f"{hole}-s{n}"
                samples.append(
                    _node(
                        Sample,
                        "Sample",
                        key,
                        kind="sample",
                        names=(draw(_TEXT),),
                        feature=hole,
                        sampling_activity=activity.key,
                        interval=activity.interval,
                        method=activity.method,
                        recovery=activity.recovery,
                    )
                )
                links += [_link(key, "project", "P1"), _link(key, "samplingActivity", activity.key)]
                links += [_link(key, "sampleProduced", activity.key, f"#{key}-produced")]
        if draw(st.booleans()):
            described = draw(_TEXT)
            lithology = _node(
                Component,
                "Lithology",
                None,
                key=f"{hole}-lith",
                role="primaryLithology",
                params={
                    "classificationCode": Code(value=draw(_WORD), code_space="urn:example:uscs"),
                    "lithDescription": described,
                },
            )
            layer = _node(
                LithologyLayer,
                "LithologyObservation",
                f"{hole}-layer",
                kind="lithology_layer",
                description=described,
                interval=_depths(draw(_DEPTH), draw(_DEPTH), lrs),
                classification=(lithology.params["classificationCode"],),
                geologic_unit=draw(st.none() | _TEXT),
                components=(lithology,),
            )
            observations.append(
                _node(
                    Observation,
                    "LithologySystem",
                    f"{hole}-log",
                    params={"lithologyClassificationType": "USCS"},
                    system="LithologySystem",
                    feature=hole,
                    layers=(layer,),
                )
            )
            links += [_link(f"{hole}-log", "project", "P1")]
            links += [_link(f"{hole}-log", "samplingFeature", hole)]
        for n in range(draw(st.integers(min_value=0, max_value=2))):
            key = f"{hole}-t{n}"
            tests.append(
                _node(
                    Measurement,
                    "Test",
                    key,
                    names=tuple(draw(st.lists(_TEXT, max_size=1))),
                    params=target,
                    feature=hole,
                    result_time=draw(st.none() | st.just(TimeSpan(start="2025-03-04T10:15:00"))),
                    results=(draw(_tables(lrs)),),
                )
            )
            links += [_link(key, "project", "P1"), _link(key, "samplingFeature", hole)]
    return _model(
        projects=(_node(Project, "Project", "P1", kind="project", names=(draw(_TEXT),)),),
        features=tuple(features),
        sampling_activities=tuple(activities),
        samples=tuple(samples),
        observations=tuple(observations),
        measurements=tuple(tests),
        crs=crs,
        links=tuple(links),
    )


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(model=_models())
def test_generated_models_round_trip_and_validate(model: Model) -> None:
    xml, back = _round_trip(model)
    assert xml == write_model(model)
    assert _errors(pydiggs.validate_bytes(xml)) == []
    assert not [note for note in back.notes if note.severity is pydiggs.Severity.ERROR]
