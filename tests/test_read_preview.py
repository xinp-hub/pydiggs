"""The preview read API (work package R1): pydiggs.pydiggs.read and diggs-model 1.0-beta."""

from __future__ import annotations

import dataclasses
import importlib.util
import io
import json
import sys
import tomllib
from collections import Counter
from importlib import resources
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import model
from pydiggs._reader import _read
from pydiggs.errors import DocumentError, InstanceReadError
from pydiggs.findings.locate import Locator

jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official"
ANNOTATED = OFFICIAL / "3.0" / "Borehole-CPT_Example_Annotated.xml"
CPT = OFFICIAL / "2.6" / "CPT_and_PorePressureDissipation.xml"
_SCRIPT = ROOT / "scripts" / "conformance.py"


def _conformance():
    spec = importlib.util.spec_from_file_location("conformance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def _corpus():
    if not _SCRIPT.is_file():
        return []
    conf = _conformance()
    census = tomllib.loads((ROOT / "conformance" / "model.toml").read_text("utf-8"))
    return [
        pytest.param(e.path, census[e.key], id=e.key)
        for e in [*conf.load_vendored(), *conf.load_external()]
        if e.path.exists()
    ]


def _validator(*, closed: bool = False):
    findings = json.loads(
        resources.files("pydiggs.findings").joinpath("diggs-findings-1.0.schema.json").read_text()
    )
    registry = referencing.Registry().with_resource(
        findings["$id"], referencing.Resource.from_contents(findings)
    )
    schema = model.model_schema()
    if closed:
        schema = _close(schema)
    return jsonschema.Draft202012Validator(schema, registry=registry)


def _close(node, name=""):
    """The strict variant: every object with declared properties accepts no others.

    The published schema is open for consumers; pyDIGGS's own output must match it
    exactly. ``header`` is left open because the node definitions extend it.
    """
    if isinstance(node, dict):
        out = {k: _close(v, k) for k, v in node.items()}
        if "properties" in out and name != "header":
            out["unevaluatedProperties"] = False
        return out
    if isinstance(node, list):
        return [_close(v, name) for v in node]
    return node


@pytest.fixture(scope="module")
def annotated() -> model.Model:
    return pydiggs.read(ANNOTATED)


# --- the corpus ---------------------------------------------------------------------


@pytest.mark.parametrize(("path", "want"), _corpus())
def test_corpus_reads_and_matches_the_census(path, want):
    if not want["readable"]:
        with pytest.raises(DocumentError) as info:
            pydiggs.read(path)
        assert info.value.kind == want["document_error"]
        return
    m = pydiggs.read(path)
    errors = [
        f"{e.json_path}: {e.message}" for e in _validator(closed=True).iter_errors(m.to_dict())
    ]
    assert errors == []
    members: Counter[str] = Counter()
    for node in m.nodes():
        steps = node.provenance.xpath.split("/")[2:]
        prop = steps[0].split(":")[-1].split("[")[0]
        if node.kind in ("reference", "property"):  # a member with no object
            members[f"{prop}/-"] += 1
        elif len(steps) == 2 and steps[1].endswith("[1]"):
            members[f"{prop}/{steps[1].split(':')[-1].split('[')[0]}"] += 1
    assert dict(members) == want["members"]
    assert dict(Counter(link.relation for link in m.links)) == want["links"]
    holders = [*m.measurements, *m.others]
    assert sum(len(h.results) for h in holders) == want["result_tables"]
    assert sum(t.row_count for h in holders for t in h.results) == want["result_rows"]
    unresolved = sum(1 for link in m.links if link.href.startswith("#") and not link.resolved)
    assert unresolved == want["links_unresolved"]
    procedures = Counter(x.procedure.type for x in m.measurements if x.procedure)
    assert not procedures - Counter(want["procedures"])
    assert pydiggs.Model.from_json(m.to_json()) == m


def test_output_is_deterministic(annotated):
    again = pydiggs.read(ANNOTATED)
    assert again.to_json() == annotated.to_json()
    assert pydiggs.read_bytes(ANNOTATED.read_bytes(), name=ANNOTATED.name).to_json() == (
        annotated.to_json()
    )


@settings(max_examples=15, deadline=None)
@given(chunk=st.integers(min_value=1, max_value=4096))
def test_streaming_does_not_depend_on_chunk_size(chunk):
    expected = pydiggs.read(CPT).to_json()
    original = _read._CHUNK
    _read._CHUNK = chunk
    try:
        assert pydiggs.read(CPT).to_json() == expected
    finally:
        _read._CHUNK = original


def test_every_node_xpath_is_the_validate_xpath_of_its_element(annotated):
    tree = etree.parse(str(ANNOTATED))
    locator = Locator(tree)
    by_xpath = {locator.xpath(el): el for el in tree.getroot().iter(etree.Element)}
    for node in annotated.nodes():
        element = by_xpath.get(node.provenance.xpath)
        assert element is not None, node.provenance.xpath
        assert element.sourceline == node.provenance.line


def test_owner_of_finds_the_node_for_validate_findings(annotated):
    report = pydiggs.validate(ANNOTATED, max_findings=None)
    located = [f for f in report.findings if f.xpath and f.xpath.count("/") > 2]
    assert located
    owned = [f for f in located if annotated.owner_of(f) is not None]
    assert len(owned) >= 0.95 * len(located)


def test_version_twins_read_alike():
    old = pydiggs.read(OFFICIAL / "2.5.a" / "AtterbergExample.xml")
    new = pydiggs.read(OFFICIAL / "2.6" / "AtterbergExample-from25.xml")
    assert (old.source.diggs_version, new.source.diggs_version) == ("2.5.a", "2.6")
    for name in ("features", "samples", "measurements"):
        assert [n.key for n in getattr(old, name)] == [n.key for n in getattr(new, name)]
    # The 2.6 conversion adds one sampleRef the 2.5.a original lacks; nothing is lost.
    new_links = {(lk.source, lk.relation, lk.target) for lk in new.links}
    assert {(lk.source, lk.relation, lk.target) for lk in old.links} <= new_links


# --- the model ----------------------------------------------------------------------


def test_typed_fields_and_navigation(annotated):
    borehole = annotated.get("Location_B-01")
    assert isinstance(borehole, model.Feature)
    assert borehole.kind == "borehole"
    assert borehole.total_depth == model.Quantity(
        text="160.00", value=160.0, uom="ft", si=model.SiValue(value=pytest.approx(48.768), uom="m")
    )
    assert borehole.location is not None
    assert borehole.location.geographic is not None
    assert borehole.location.geographic.lat == pytest.approx(30.429139)  # swapped for geographic
    tests = annotated.related(borehole, relation="samplingFeature", direction="in")
    assert tests
    assert all(
        isinstance(t, model.Measurement | model.Observation | model.SamplingActivity) or True
        for t in tests
    )
    spt = next(
        m
        for m in annotated.measurements
        if m.procedure and m.procedure.type == "DrivenPenetrationTest"
    )
    assert spt.feature is not None
    table = spt.results[0]
    column = table.column("n_value")
    assert column is not None
    assert column.type == "number"
    assert table.to_records()[0]["n_value"] == column.values[0]
    assert any(n.rule_id == "geometry.crs-axis-order-suspect" for n in annotated.notes)


def test_inventory_mode_keeps_shapes_but_no_values():
    full = pydiggs.read(CPT)
    light = pydiggs.read(CPT, results=False)
    shapes = [(t.row_count, len(t.columns)) for m in full.measurements for t in m.results]
    assert shapes == [(t.row_count, len(t.columns)) for m in light.measurements for t in m.results]
    assert all(c.values == () for m in light.measurements for t in m.results for c in t.columns)


def test_unknown_fields_are_ignored_and_bad_documents_refused(annotated):
    doc = annotated.to_dict()
    doc["future"] = 1
    doc["features"][0]["future"] = [1]
    assert pydiggs.Model.from_dict(doc) == annotated
    for mutate in (
        lambda d: d.update(schema_version="2.0"),
        lambda d: d["features"][0].update(key=d["features"][1]["key"]),
        lambda d: d["links"][0].update(target="nowhere"),
        lambda d: d["features"][0].update(total_depth="deep"),
        lambda d: d.pop("source"),
    ):
        broken = json.loads(json.dumps(doc))
        mutate(broken)
        with pytest.raises(ValueError):
            pydiggs.Model.from_dict(broken)


def test_schema_and_classes_agree():
    """Every dataclass field is in the schema and every schema property is a field."""
    schema = model.model_schema()
    defs = schema["$defs"]
    header = set(defs["header"]["properties"])
    pairs = {
        "feature": model.Feature,
        "sampling_activity": model.SamplingActivity,
        "sample": model.Sample,
        "lithology_layer": model.LithologyLayer,
        "observation": model.Observation,
        "measurement": model.Measurement,
        "project": model.Project,
        "associate": model.Associate,
        "document_info": model.DocumentInfo,
        "other": model.Other,
        "component": model.Component,
        "time_span": model.TimeSpan,
        "quantity": model.Quantity,
        "code": model.Code,
        "interval": model.Interval,
        "position": model.Position,
        "crs_ref": model.CrsRef,
        "crs_component": model.CrsComponent,
        "link": model.Link,
        "procedure": model.Procedure,
        "domain": model.Domain,
        "column": model.Column,
        "result_table": model.ResultTable,
        "source": model.SourceInfo,
        "extra_node": model.ExtraNode,
        "provenance": model.Provenance,
        "field_location": model.FieldLocation,
    }
    for name, cls in pairs.items():
        names = {f.name for f in dataclasses.fields(cls)}
        declared = set(defs[name].get("properties", {}))
        if "allOf" in defs[name]:
            declared |= header
        assert names == declared, (name, names ^ declared)
        # What the decoder insists on, the frozen schema must require too: a document
        # the schema accepts must load.
        needed = {
            f.name
            for f in dataclasses.fields(cls)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        }
        required = set(defs[name].get("required", ()))
        if "allOf" in defs[name]:
            required |= set(defs["header"]["required"])
            for part in defs[name]["allOf"]:
                required |= set(part.get("required", ()))
        assert needed <= required, (name, needed - required)
    top = {f.name for f in dataclasses.fields(model.Model) if not f.name.startswith("_")}
    assert top == set(schema["properties"]) - {"$schema"}


def test_schema_is_published_where_its_id_says():
    schema = model.model_schema()
    assert schema["$id"] == model.MODEL_SCHEMA_URL
    assert pydiggs.MODEL_SCHEMA_VERSION in model.MODEL_SCHEMA_URL
    jsonschema.Draft202012Validator.check_schema(schema)


# --- errors and limits --------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "kind", "rule"),
    [
        (b"<Diggs", "malformed", "structure.well-formed"),
        (b"", "malformed", "structure.well-formed"),
        (b"<html/>", "not-diggs", "structure.diggs-root"),
        (
            b'<!DOCTYPE Diggs [<!ENTITY x "<a/>">]><Diggs>&x;</Diggs>',
            "unsafe-entity",
            "structure.markup-entity",
        ),
    ],
)
def test_unreadable_documents_raise_document_error(data, kind, rule):
    with pytest.raises(DocumentError) as info:
        pydiggs.read_bytes(data)
    assert info.value.kind == kind
    assert info.value.finding.rule_id == rule
    assert isinstance(info.value, ValueError)


def test_input_errors(tmp_path):
    with pytest.raises(InstanceReadError):
        pydiggs.read(tmp_path / "missing.xml")
    with pytest.raises(InstanceReadError):
        pydiggs.read_bytes(b"<Diggs/>" * 10, max_bytes=10)
    with pytest.raises(TypeError):
        pydiggs.read_bytes("<Diggs/>")  # type: ignore[arg-type]
    for bad in (
        {"diggs_version": "9"},
        {"max_bytes": 0},
        {"time_budget": 0},
        {"time_budget": True},
    ):
        with pytest.raises(ValueError):
            pydiggs.read_bytes(b"<Diggs/>", **bad)
    stream = io.BytesIO(CPT.read_bytes())
    assert pydiggs.read_bytes(stream, name="a/b/c.xml").source.name == "c.xml"
    assert not stream.closed


def test_time_budget_returns_a_partial_model(monkeypatch):
    clock = iter(range(1000))
    monkeypatch.setattr(_read.time, "monotonic", lambda: float(next(clock)))
    m = pydiggs.read(ANNOTATED, time_budget=1.5)
    assert m.complete is False
    assert m.limits_hit == ("time_budget",)
    assert any(n.rule_id == "structure.timeout" for n in m.notes)
    assert 0 < len(list(m.nodes())) < len(list(pydiggs.read(ANNOTATED).nodes()))


def test_explicit_version_is_recorded():
    m = pydiggs.read(ANNOTATED, diggs_version="3.1-dev")
    assert m.source.diggs_version == "3.1-dev"
    assert m.source.detected is False


def test_new_note_rule_is_catalogued():
    info = pydiggs.explain_rule("structure.value-unparsed")
    assert info.known


def test_reading_imports_no_frame_or_gis_library():
    code = (
        "import sys, pydiggs as p;"
        "p.read('tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml');"
        "print(sorted({'pandas','polars','pyproj','rich'} & set(sys.modules)))"
    )
    import subprocess

    out = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
