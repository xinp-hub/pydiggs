"""DIGGS to AGS4: ``diggs_to_ags4`` and ``model_to_ags4``, the residual file, the formula guard."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings

import pydiggs
from pydiggs import ags4, convert
from pydiggs.convert import _from_model
from pydiggs.convert._mapping import load_mapping
from pydiggs.errors import DocumentError
from pydiggs.model import Component, Feature, Quantity

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
TIER_A = FIXTURES / "convert_tier_a.ags"
TIER_B = FIXTURES / "site_tier_b_4_1_1.ags"
TIER_B_42 = FIXTURES / "site_tier_b_4_2.ags"
OFFICIAL_3 = ROOT / "tests" / "fixtures" / "official" / "3.0"


@pytest.fixture(scope="module")
def tier_b() -> convert.Conversion:
    return convert.ags4_to_diggs(TIER_B)


def ags4_errors(report: pydiggs.Report | None) -> list[str]:
    assert report is not None
    return [f.rule_id for f in report.findings if f.severity is pydiggs.Severity.ERROR]


# --- Tier B to DIGGS ----------------------------------------------------------------


def test_tier_b_groups_convert_to_valid_diggs(tier_b: convert.Conversion):
    assert tier_b.validation is not None and tier_b.validation.ok
    borehole = tier_b.model.get("BH01")
    assert isinstance(borehole, Feature)
    held = {(c.element, c.role) for c in borehole.construction}
    assert held == {
        ("Backfill", "backfill"),
        ("Casing", "casing"),
        ("BoreholeDiameter", "holeDiameter"),
    }
    diameters = [c for c in borehole.construction if c.element == "BoreholeDiameter"]
    assert [(c.interval.top.text, c.interval.base.text) for c in diameters if c.interval] == [
        ("0", "6.00"),
        ("6.00", "15.00"),
    ]
    assert [c.params["diameter"].text for c in diameters] == ["200", "150"]  # type: ignore[union-attr]
    (strike,) = borehole.water_strikes
    (reading,) = strike.components
    assert reading.element == "WaterStrikeReading"
    assert reading.params["dateTime"] == "2026-11-02T10:15:00"
    assert reading.location is not None and reading.location.coordinates == (4.5,)
    later = [(c.params["elapsedTime"].text, c.location.coordinates) for c in reading.components]  # type: ignore[union-attr]
    assert later == [("5", (4.2,)), ("20", (3.9,))]
    (backfill,) = [c for c in borehole.construction if c.element == "Backfill"]
    assert [c.params["backfillMaterial"] for c in backfill.components] == ["Sand", "Gravel"]
    tests = {m.procedure.type: m for m in tier_b.model.measurements if m.procedure}
    density = tests["LabDensityTest"].results[0]
    assert [(c.key, c.uom, c.values) for c in density.columns] == [
        ("bulk_density", "Mg/m3", (1.98,)),
        ("dry_density", "Mg/m3", (1.62,)),
    ]
    cone = tests["StaticConePenetrationTest"].results[0]
    assert cone.row_count == 3 and cone.domain.index[0].values == (0.5, 1.0, 1.5)
    assert [c.key for c in cone.columns] == [
        "tip_resistance",
        "sleeve_friction",
        "pore_pressure_u2",
    ]


def test_tier_b_losses_name_what_diggs_cannot_hold(tier_b: convert.Conversion):
    paths = {f.path for f in tier_b.losses if f.rule_id == "convert.unmapped-heading"}
    assert {"HORN/1/HORN_INCL", "CDIA/1/CDIA_DIAM", "BKFL/1/BKFL_LEG"} <= paths
    assert "WSTG/1/WSTG_CAS" not in paths  # DIGGS bottomCasing
    assumed = " ".join(f.message for f in tier_b.losses if f.rule_id == "convert.assumed")
    assert "start at the base of the previous one" in assumed
    assert "from the surface (0)" in assumed


def test_cpt_in_4_2_uses_cptg_and_cptt():
    c = convert.ags4_to_diggs(TIER_B_42)
    (cone,) = [
        m for m in c.model.measurements if m.procedure and m.procedure.type.startswith("Static")
    ]
    assert c.trace[cone.key].group == "CPTG"
    assert cone.results[0].row_count == 3


def test_a_hole_diameter_row_without_a_diameter_is_not_converted():
    source = ags4.read(TIER_B)
    hdia = source.groups["HDIA"]
    rows = (hdia.rows[0], (*hdia.rows[1][:2], ""))
    groups = [replace(hdia, rows=rows) if g.name == "HDIA" else g for g in source.groups.values()]
    c = convert.ags4_to_diggs(ags4.File(groups=groups))
    assert c.validation is not None and c.validation.ok
    assert any(f.rule_id == "convert.incomplete" and f.path == "HDIA/2" for f in c.losses)


def test_orphan_water_strike_readings_and_cone_readings_are_reported():
    source = ags4.read(TIER_B)
    groups = []
    for g in source.groups.values():
        extra = (("NOPE", *g.rows[0][1:]),) if g.name in ("WSTD", "SCPT") else ()
        groups.append(replace(g, rows=(*g.rows, *extra)))
    c = convert.ags4_to_diggs(ags4.File(groups=groups))
    orphans = {f.path for f in c.losses if f.rule_id == "convert.orphan-row"}
    assert {"WSTD/3", "SCPT/4"} <= orphans


# --- the residual file ----------------------------------------------------------------


def test_the_residual_file_holds_exactly_what_was_not_converted(tier_b: convert.Conversion):
    residual = tier_b.residual
    assert residual is not None
    assert "HORN" in residual.groups and "HDIA" not in residual.groups
    loca = residual.groups["LOCA"]  # only the CPT location's code, which has no DIGGS code
    assert loca.headings == ("LOCA_ID", "LOCA_TYPE")
    assert loca.rows == (("CPT01", "CPT"),)
    horn = residual.groups["HORN"]
    assert horn.headings == ("LOCA_ID", "HORN_TOP", "HORN_BASE", "HORN_ORNT", "HORN_INCL")
    cdia = residual.groups["CDIA"]
    assert cdia.headings == ("LOCA_ID", "CDIA_DPTH", "CDIA_DIAM")  # keys and the lost value
    assert cdia.rows == (("BH01", "6.00", "200"),)
    assert ags4.read_bytes(ags4.dumps(residual)) == residual


def test_a_lossless_conversion_has_an_empty_residual_file():
    loca = ags4.Group(
        name="LOCA",
        headings=("LOCA_ID", "LOCA_TYPE"),
        units=("", ""),
        types=("ID", "PA"),
        rows=[("BH1", "TP")],
    )
    proj = ags4.Group(name="PROJ", headings=("PROJ_ID",), units=("",), types=("ID",), rows=[("P",)])
    tran = ags4.Group(
        name="TRAN",
        headings=("TRAN_DATE", "TRAN_AGS"),
        units=("yyyy-mm-dd", ""),
        types=("DT", "X"),
        rows=[("2026-01-01", "4.1.1")],
    )
    c = convert.ags4_to_diggs(ags4.File(groups=[proj, tran, loca]))
    assert c.residual is not None
    assert dict(c.residual.groups) == {}


# --- DIGGS to AGS4 ------------------------------------------------------------------


def test_model_to_ags4_writes_a_file_that_passes_the_check(tier_b: convert.Conversion):
    out = convert.model_to_ags4(tier_b.model)
    assert out.ags4 is not None and out.xml is None and out.residual is None
    assert ags4_errors(out.validation) == []
    assert out.ags4.version == "4.2"  # the default
    assert list(out.ags4.groups)[:5] == ["PROJ", "TRAN", "ABBR", "TYPE", "UNIT"]
    loca = out.ags4.groups["LOCA"]
    assert loca.records()[0]["LOCA_TYPE"] == "CP"
    abbr = out.ags4.groups["ABBR"].records()
    assert {(r["ABBR_HDNG"], r["ABBR_CODE"]) for r in abbr} == {
        ("LOCA_TYPE", "CP"),
        ("SAMP_TYPE", "U"),
    }
    types = {r["TYPE_TYPE"] for r in out.ags4.groups["TYPE"].records()}
    used = {t for g in out.ags4.groups.values() for t in g.types}
    assert used <= types
    units = {r["UNIT_UNIT"] for r in out.ags4.groups["UNIT"].records()}
    assert {u for g in out.ags4.groups.values() for u in g.units if u} <= units
    assert out.report.steps[pydiggs.Step.CONVERT] == "passed"
    tran = out.ags4.groups["TRAN"].records()[0]
    assert "mapping table 1.0" in tran["TRAN_REM"]


def test_the_ags4_version_selects_the_cone_groups(tier_b: convert.Conversion):
    old = convert.model_to_ags4(tier_b.model, ags_version="4.1.1").ags4
    new = convert.model_to_ags4(tier_b.model, ags_version="4.2").ags4
    assert old is not None and new is not None
    assert {"SCPG", "SCPT"} <= set(old.groups) and not {"CPTG", "CPTT"} & set(old.groups)
    assert {"CPTG", "CPTT"} <= set(new.groups) and not {"SCPG", "SCPT"} & set(new.groups)
    assert [r["CPTT_REDN"] for r in new.groups["CPTT"].records()] == ["1", "2", "3"]
    assert new.version == "4.2"


def test_only_ags4_4_1_1_and_4_2_are_written(tier_b: convert.Conversion):
    with pytest.raises(ValueError, match=r"4\.1\.1, 4\.2"):
        convert.model_to_ags4(tier_b.model, ags_version="4.0.4")
    with pytest.raises(ValueError, match=r"4\.1\.1, 4\.2"):
        convert.diggs_to_ags4(b"<x/>", ags_version="3.1")
    with pytest.raises(ValueError, match="formula_guard"):
        convert.model_to_ags4(tier_b.model, formula_guard="quote")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"pydiggs\.Model"):
        convert.model_to_ags4(ags4.read(TIER_B))  # type: ignore[arg-type]


def test_diggs_to_ags4_reads_every_source_kind(tmp_path: Path, tier_b: convert.Conversion):
    assert tier_b.xml is not None
    path = tmp_path / "site.xml"
    path.write_bytes(tier_b.xml)
    expected = ags4.dumps(convert.model_to_ags4(tier_b.model).ags4)  # type: ignore[arg-type]
    for source in (path, str(path), tier_b.xml, io.BytesIO(tier_b.xml)):
        out = convert.diggs_to_ags4(source, name="site.xml")
        assert out.ags4 is not None
        assert ags4.dumps(out.ags4) == expected
    with pytest.raises(DocumentError):
        convert.diggs_to_ags4(b"<notdiggs/>")


def test_save_writes_the_output_of_either_direction(tmp_path: Path, tier_b: convert.Conversion):
    tier_b.save(tmp_path / "out.xml")
    assert (tmp_path / "out.xml").read_bytes() == tier_b.xml
    out = convert.model_to_ags4(tier_b.model)
    out.save(tmp_path / "out.ags")
    assert ags4.read(tmp_path / "out.ags") == out.ags4


def test_diggs_values_with_no_ags4_target_are_unmapped_elements(tier_b: convert.Conversion):
    borehole = tier_b.model.get("BH01")
    assert isinstance(borehole, Feature)
    casing = next(c for c in borehole.construction if c.element == "Casing")
    material = replace(
        casing,
        params={
            "casingMaterial": "steel",
            "casingWallThickness": Quantity(text="5", value=5.0, uom="mm"),
        },
    )
    changed = replace(
        borehole,
        params={**borehole.params, "investigationTarget": "Natural Ground"},
        construction=tuple(material if c is casing else c for c in borehole.construction),
    )
    model = replace(
        tier_b.model, features=tuple(changed if f is borehole else f for f in tier_b.model.features)
    )
    out = convert.model_to_ags4(model)
    lost = [f for f in out.losses if f.rule_id == "convert.unmapped-element"]
    text = " ".join(f.message for f in lost)
    assert "params.investigationTarget of Borehole" in text
    assert "params.casingMaterial of Casing" in text
    assert all(f.xpath for f in lost)
    assert ags4_errors(out.validation) == []


def test_every_model_item_is_written_or_named_on_official_diggs_3_files():
    mapping = load_mapping()
    for path in sorted(OFFICIAL_3.glob("*.xml")):
        model = pydiggs.read(path)
        exported = _from_model.map_model(model, mapping, ags_version="4.1.1")
        ledger = exported.ledger
        assert all(ledger.is_done(item) for item in ledger.items), path.name
        out = convert.model_to_ags4(model)
        assert [e for e in ags4_errors(out.validation) if e != "ags4.rule-1"] == [], path.name


def test_conversion_to_ags4_is_deterministic(tier_b: convert.Conversion):
    one = convert.model_to_ags4(tier_b.model).ags4
    two = convert.model_to_ags4(convert.ags4_to_diggs(TIER_B).model).ags4
    assert one is not None and two is not None
    assert ags4.dumps(one) == ags4.dumps(two)


# --- formula-like text (Q9) ------------------------------------------------------------


def _with_remark(c: convert.Conversion, text: str) -> pydiggs.Model:
    borehole = c.model.get("BH01")
    assert isinstance(borehole, Feature)
    changed = replace(borehole, remarks=(text,))
    return replace(
        c.model, features=tuple(changed if f is borehole else f for f in c.model.features)
    )


def test_formula_like_text_is_written_unchanged_and_reported(tier_b: convert.Conversion):
    out = convert.model_to_ags4(_with_remark(tier_b, "=HYPERLINK(1)"))
    assert out.ags4 is not None
    assert out.ags4.groups["LOCA"].records()[0]["LOCA_REM"] == "=HYPERLINK(1)"
    found = [f for f in out.report.findings if f.rule_id == "ags4.formula-text"]
    assert len(found) == 1 and "written unchanged" in found[0].message
    assert found[0].path == "LOCA"
    checked = [f.rule_id for f in out.validation.findings]  # type: ignore[union-attr]
    assert "ags4.formula-text" in checked


def test_the_prefix_guard_prefixes_text_and_reports_it(tier_b: convert.Conversion):
    out = convert.model_to_ags4(_with_remark(tier_b, "@cmd"), formula_guard="prefix")
    assert out.ags4 is not None
    records = out.ags4.groups["LOCA"].records()
    assert records[0]["LOCA_REM"] == "'@cmd"
    assert records[0]["LOCA_ID"] == "BH01"  # keys are never changed
    messages = [f.message for f in out.report.findings if f.rule_id == "ags4.formula-text"]
    assert any("apostrophe" in m for m in messages)
    assert "ags4.formula-text" not in [f.rule_id for f in out.validation.findings]  # type: ignore[union-attr]


def test_a_negative_number_is_not_formula_text():
    assert not _from_model.formula_like("-12.5")
    assert _from_model.formula_like("-dense")
    assert not _from_model.formula_like("=")


def test_a_line_break_is_reported_not_written(tier_b: convert.Conversion):
    out = convert.model_to_ags4(_with_remark(tier_b, "two\nlines"))
    assert out.ags4 is not None
    assert "LOCA_REM" not in out.ags4.groups["LOCA"].headings
    assert any("line break" in f.message for f in out.losses)
    assert ags4_errors(out.validation) == []


# --- generated files: every loss listed, both ways ------------------------------------


def _strategies():
    from tests.test_convert_to_diggs import _files

    return _files()


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_strategies())
def test_generated_files_round_trip_with_every_loss_listed(file: ags4.File):
    from tests.test_convert_round_trip import round_trip

    tally, first, second = round_trip(file, "4.1.1")
    assert tally.unaccounted == []
    assert second.validation is not None
    assert [
        f.rule_id
        for f in second.validation.findings
        if f.severity is pydiggs.Severity.ERROR and f.rule_id != "ags4.rule-1"
    ] == []
    assert first.residual is not None


def test_a_component_the_writer_cannot_place_is_not_claimed():
    """A casing with no depth: AGS4 keys CDIA by depth, so the record is reported."""
    c = convert.ags4_to_diggs(TIER_B)
    borehole = c.model.get("BH01")
    assert isinstance(borehole, Feature)
    bare = Component(
        key="bare-casing",
        gml_id="bare-casing",
        element="Casing",
        kind="casing",
        name=None,
        names=(),
        description=None,
        remarks=("no depth",),
        provenance=borehole.provenance,
        extra=(),
        role="casing",
    )
    changed = replace(borehole, construction=(*borehole.construction, bare))
    model = replace(
        c.model, features=tuple(changed if f is borehole else f for f in c.model.features)
    )
    out = convert.model_to_ags4(model)
    assert any("without the depth" in f.message for f in out.losses)
    exported = _from_model.map_model(model, load_mapping(), ags_version="4.1.1")
    assert not set(exported.ledger.named) & exported.ledger.used


def test_coordinates_in_another_unit_than_their_heading_are_reported_not_claimed():
    """Found by the generated round trip: a location with no CRS after one in metres."""
    from tests.test_convert_round_trip import round_trip
    from tests.test_convert_to_diggs import LOCA_SPEC, group

    rows = [("BH1", "CP", "1", "2", "OSGB", "", ""), ("BH2", "", "1", "1", "", "", "")]
    file = ags4.File(groups=[group("LOCA", LOCA_SPEC, rows)])
    tally, _first, second = round_trip(file, "4.2")
    assert tally.unaccounted == []
    assert any("another unit" in f.message for f in second.losses)
