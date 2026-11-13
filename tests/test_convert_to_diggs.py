"""AGS4 to DIGGS 3.0.0 (the 1.9 preview, ``pydiggs.preview.convert``).

Property 4 of the conversion-core brief for this direction: every non-null source cell
of a Tier A file is carried into DIGGS by a node that is written, or named by a
``convert`` finding; and every output validates against DIGGS 3.0.0 with no error. The
property is checked on the synthetic fixtures (all data invented), on generated files,
and, when C0 has added it, on ``tests/fixtures/ags4/site_tier_a.ags``.
"""

from __future__ import annotations

import doctest
import io
import json
import os
import subprocess
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import ags4
from pydiggs.convert import _api, _ledger, _mapping, _to_model, _values
from pydiggs.convert._mapping import load_mapping
from pydiggs.convert._to_model import Built, drop, map_file
from pydiggs.errors import DocumentError
from pydiggs.preview import convert

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
TIER_A = FIXTURES / "convert_tier_a.ags"
SITE = FIXTURES / "site_tier_a.ags"
OSGB = "http://www.opengis.net/def/crs/EPSG/0/27700"

Spec = Sequence[tuple[str, str, str]]


def group(name: str, spec: Spec, rows: Sequence[Sequence[str]]) -> ags4.Group:
    """A group from ``(heading, unit, type)`` triples and rows of strings."""
    return ags4.Group(
        name=name,
        headings=tuple(h for h, _, _ in spec),
        units=tuple(u for _, u, _ in spec),
        types=tuple(t for _, _, t in spec),
        rows=[tuple(row) for row in rows],
    )


PROJ = group("PROJ", [("PROJ_ID", "", "ID"), ("PROJ_NAME", "", "X")], [("P1", "Test site")])
TRAN = group(
    "TRAN",
    [("TRAN_DATE", "yyyy-mm-dd", "DT"), ("TRAN_AGS", "", "X")],
    [("2026-11-12", "4.1.1")],
)
LOCA_SPEC: Spec = [
    ("LOCA_ID", "", "ID"),
    ("LOCA_TYPE", "", "PA"),
    ("LOCA_NATE", "m", "2DP"),
    ("LOCA_NATN", "m", "2DP"),
    ("LOCA_GREF", "", "PA"),
    ("LOCA_GL", "m", "2DP"),
    ("LOCA_FDEP", "m", "2DP"),
]
LOCA = group("LOCA", LOCA_SPEC, [("BH1", "CP", "451200", "206500", "OSGB", "12", "15")])


def ags_file(*groups: ags4.Group) -> ags4.File:
    return ags4.File(groups=groups)


def convert_file(file: ags4.File, **kw: Any) -> convert.Conversion:
    return convert.ags4_to_diggs(file, **kw)


def rules(c: convert.Conversion) -> set[str]:
    return {f.rule_id for f in c.report.findings if f.step is pydiggs.Step.CONVERT}


def errors(report: pydiggs.Report | None) -> list[tuple[str, str]]:
    assert report is not None
    return [(f.rule_id, f.message) for f in report.findings if f.severity is pydiggs.Severity.ERROR]


# --- property 4: every cell used by a written node, or named by a finding ----------------


def accounted(file: ags4.File) -> tuple[bytes, Built]:
    """Convert ``file`` as ags4_to_diggs does, and check every cell is accounted for."""
    built = map_file(file, load_mapping())
    xml, built = _api._write(built, "3.0.0")
    ledger, model = built.ledger, built.model
    findings = ledger.findings()
    for cell in ledger.cells():
        owners = ledger.used.get(cell, set())
        assert owners or cell in ledger.named, f"cell {cell} is neither used nor named"
        for owner in owners - {""}:
            assert model.get(owner) is not None, f"cell {cell} is used by {owner!r}, not written"
    assert all(index < len(findings) for index in ledger.named.values())
    for finding in findings:
        assert finding.step is pydiggs.Step.CONVERT
        assert finding.severity is _ledger.RULES[finding.rule_id]
        assert finding.path is not None
        assert finding.path.split("/")[0] in {_ledger.name(g) for g in file.groups}
    return xml, built


def valid(xml: bytes) -> None:
    report = pydiggs.validate_bytes(xml)
    assert errors(report) == []


def test_tier_a_fixture_every_cell_is_accounted_for_and_the_output_is_valid():
    file = ags4.read(TIER_A)
    xml, built = accounted(file)
    valid(xml)
    assert built.complete


def test_the_c1_fixtures_are_accounted_for_and_valid():
    for path in sorted(FIXTURES.glob("*.ags")):
        try:
            file = ags4.read(path)
        except DocumentError:
            continue  # AGS 3, which the reader refuses
        xml, _ = accounted(file)
        valid(xml)


@pytest.mark.skipif(not SITE.is_file(), reason="site_tier_a.ags arrives with C0")
def test_the_c0_site_fixture_is_accounted_for_and_valid():
    xml, _ = accounted(ags4.read(SITE))
    valid(xml)
    c = convert.ags4_to_diggs(SITE)
    assert c.complete
    assert errors(c.validation) == []


# --- the Tier A fixture, end to end -------------------------------------------------------


@pytest.fixture(scope="module")
def tier_a() -> convert.Conversion:
    return convert.ags4_to_diggs(TIER_A)


def test_tier_a_converts_to_valid_diggs_3_0_0(tier_a: convert.Conversion):
    assert tier_a.complete
    assert tier_a.validation is not None
    assert errors(tier_a.validation) == []
    assert tier_a.model.source.diggs_version == "3.0.0"
    assert tier_a.xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    assert tier_a.mapping_version == "0.1"
    model = tier_a.model
    assert [(f.key, f.element) for f in model.features] == [
        ("BH01", "Borehole"),
        ("BH02", "Borehole"),
        ("TP01", "TrialPit"),
    ]
    assert len(model.samples) == len(model.sampling_activities) == 3
    assert [layer.description for layer in model.observations[0].layers][:1] == [
        "MADE GROUND: brown sandy gravel"
    ]
    procedures = [m.procedure.type for m in model.measurements if m.procedure]
    assert procedures == [
        "DrivenPenetrationTest",
        "DrivenPenetrationTest",
        "AtterbergLimitsTest",
        "AtterbergLimitsTest",
        "WaterContentTest",
        "WaterContentTest",
        "ParticleSizeTest",
    ]


def test_tier_a_values_are_carried_as_reported(tier_a: convert.Conversion):
    model = tier_a.model
    bh01 = model.get("BH01")
    assert isinstance(bh01, pydiggs.model.Feature)
    assert bh01.location is not None
    assert bh01.location.coordinates == (451200.0, 206500.0, 12.5)
    assert bh01.location.crs == OSGB
    assert bh01.total_depth is not None and bh01.total_depth.text == "15.00"
    assert bh01.params["boreholeType"] == pydiggs.model.Code(
        value="CP", code_space="https://diggsml.org/def/codes/DIGGS/0.1/boreholeType.xml"
    )
    spt = model.get("ISPT-1")
    assert isinstance(spt, pydiggs.model.Measurement)
    assert spt.results[0].to_records() == [{"n_value": 32.0}]
    assert spt.procedure is not None
    assert spt.procedure.params["hammerEfficiency"].uom == "%"  # type: ignore[union-attr]
    assert spt.procedure.params["penetrationTestType"] == "SPT"
    limits = model.get("LLPL-2")
    assert isinstance(limits, pydiggs.model.Measurement)
    assert limits.results[0].to_records() == [{"non_plastic": True}]
    grading = model.get("GRAG-1")
    assert isinstance(grading, pydiggs.model.Measurement) and grading.procedure is not None
    sieve = grading.procedure.components[0]
    assert [c.params["particleSize"].text for c in sieve.components] == ["20.0", "2.00", "0.063"]  # type: ignore[union-attr]
    sample = model.get("BH01-U1")
    assert isinstance(sample, pydiggs.model.Sample)
    assert sample.params["type"] == pydiggs.model.Code(
        value="open_tube", code_space="https://diggsml.org/def/codes/DIGGS/0.1/sampleType.xml"
    )
    assert sample.interval is not None and sample.interval.top is not None
    assert sample.interval.top.text == "2.00"


def test_tier_a_trace_names_the_source_row(tier_a: convert.Conversion):
    assert tier_a.trace["BH01"] == convert.Origin(group="LOCA", row=1, line=60)
    assert tier_a.trace["ISPT-2"] == convert.Origin(group="ISPT", row=2, line=87)
    assert tier_a.trace["GRAT-3"].group == "GRAT"
    lines = TIER_A.read_bytes().split(b"\r\n")
    assert lines[59].startswith(b'"DATA","BH01"')
    assert lines[86].startswith(b'"DATA","BH01","9.00"')
    assert all(tier_a.model.get(key) is not None for key in tier_a.trace)


def test_tier_a_losses_are_listed(tier_a: convert.Conversion):
    assert not tier_a.lossless
    by_rule: dict[str, list[pydiggs.Finding]] = {}
    for finding in tier_a.losses:
        by_rule.setdefault(finding.rule_id, []).append(finding)
    assert set(by_rule) == {
        "convert.unmapped-heading",
        "convert.user-defined",
        "convert.unmapped-group",
        "convert.unmapped-code",
        "convert.assumed",
    }
    assert {f.path for f in by_rule["convert.user-defined"]} == {"LOCA/2/LOCA_XCOL", "XNOT/1"}
    assert [f.path for f in by_rule["convert.unmapped-group"]] == ["HDPH/1"]
    assert "not converted (2 rows)" in by_rule["convert.unmapped-group"][0].message
    assert all(f.severity is not pydiggs.Severity.INFO for f in tier_a.losses)
    reformatted = [f for f in tier_a.report.findings if f.rule_id == "convert.value-reformatted"]
    assert reformatted and all(f.severity is pydiggs.Severity.INFO for f in reformatted)


def test_the_report_is_an_ags4_findings_report(tier_a: convert.Conversion):
    report = tier_a.report
    assert report.format == "ags4"
    assert report.diggs_version is None
    assert report.path == "convert_tier_a.ags"
    assert report.steps == {pydiggs.Step.AGS4: "passed", pydiggs.Step.CONVERT: "passed"}
    document = report.to_document()
    jsonschema.validate(document, pydiggs.findings_schema())
    assert document["files"][0]["format"] == "ags4"
    assert {f["location"].get("path") for f in document["files"][0]["findings"]} >= {"HDPH/1"}


def test_conversion_is_deterministic(tier_a: convert.Conversion):
    again = convert.ags4_to_diggs(TIER_A.read_bytes(), name="convert_tier_a.ags")
    assert again.xml == tier_a.xml
    assert again.report.findings == tier_a.report.findings
    assert b"\n" in again.xml and b"<!DOCTYPE" not in again.xml


def test_every_source_kind_is_accepted(tmp_path: Path, tier_a: convert.Conversion):
    data = TIER_A.read_bytes()
    for source in (
        TIER_A,
        str(TIER_A),
        data,
        bytearray(data),
        memoryview(data),
        io.BytesIO(data),
        ags4.read(TIER_A),
    ):
        assert convert.ags4_to_diggs(source, validate=False).xml == tier_a.xml
    out = tmp_path / "site.xml"
    tier_a.save(out)
    assert out.read_bytes() == tier_a.xml
    assert convert.ags4_to_diggs(data, name="up/loads/x.ags", validate=False).report.path == "x.ags"
    assert (
        convert.ags4_to_diggs(ags4.read(TIER_A), name="../..").report.path == "convert_tier_a.ags"
    )


def test_only_diggs_3_0_0_is_written():
    with pytest.raises(ValueError, match=r"3\.0\.0"):
        convert.ags4_to_diggs(TIER_A, diggs_version="2.6")


def test_input_that_is_not_ags4_raises_what_the_reader_raises():
    with pytest.raises(DocumentError):
        convert.ags4_to_diggs(b"not an AGS4 file at all")
    with pytest.raises(pydiggs.InstanceReadError):
        convert.ags4_to_diggs(ROOT / "no-such-file.ags")
    with pytest.raises(TypeError):
        convert.ags4_to_diggs(ags4.read(TIER_A), name=3)  # type: ignore[arg-type]


# --- what is lost, and how it is reported -------------------------------------------------


def test_a_fully_mapped_file_is_lossless():
    c = convert_file(ags_file(PROJ, TRAN, LOCA))
    assert c.lossless, c.losses
    assert errors(c.validation) == []
    assert [f.rule_id for f in c.report.findings] == ["convert.value-reformatted"] or (
        c.report.findings == ()
    )


def _one(c: convert.Conversion, rule_id: str) -> pydiggs.Finding:
    found = [f for f in c.report.findings if f.rule_id == rule_id]
    assert len(found) == 1, (rule_id, c.report.findings)
    return found[0]


def test_no_proj_and_no_tran_date_are_written_as_documented_defaults():
    c = convert_file(ags_file(LOCA))
    messages = [f.message for f in c.losses if f.rule_id == "convert.assumed"]
    assert any("'unnamed project'" in m for m in messages)
    assert any("1970-01-01" in m for m in messages)
    assert c.model.document is not None and c.model.document.creation_date == "1970-01-01"
    assert errors(c.validation) == []


def test_a_location_without_coordinates_is_placed_on_its_own_depth_axis():
    loca = group("LOCA", LOCA_SPEC, [("BH1", "CP", "", "", "", "", "5")])
    c = convert_file(ags_file(PROJ, TRAN, loca))
    assert "top of the hole" in _one(c, "convert.assumed").message
    feature = c.model.features[0]
    assert feature.location is not None and feature.location.crs == "#BH1-lrs"
    assert errors(c.validation) == []


def test_codes_crs_and_units_without_an_entry_are_reported():
    loca = group(
        "LOCA",
        LOCA_SPEC,
        [
            ("BH1", "ZZ", "1", "2", "Local grid", "", ""),
            ("BH2", "WS", "1", "2", "EPSG:27700", "", ""),
            ("BH3", "", "1", "2", "", "", ""),
        ],
    )
    ispt = group(
        "ISPT",
        [
            ("LOCA_ID", "", "ID"),
            ("ISPT_TOP", "m", "2DP"),
            ("ISPT_NVAL", "", "0DP"),
            ("ISPT_ERAT", "furlong", "0DP"),
        ],
        [("BH1", "1.00", "12", "70")],
    )
    c = convert_file(ags_file(PROJ, TRAN, loca, ispt))
    paths = {(f.rule_id, f.path) for f in c.losses}
    assert ("convert.unmapped-code", "LOCA/1/LOCA_TYPE") in paths
    assert ("convert.unmapped-code", "LOCA/2/LOCA_TYPE") in paths  # WS: no DIGGS code
    assert ("convert.unmapped-code", "LOCA/1/LOCA_GREF") in paths
    assert ("convert.unmapped-unit", "ISPT/1/ISPT_ERAT") in paths
    assert [f.crs for f in (x.location for x in c.model.features) if f] == [None, OSGB, None]
    assert [f.element for f in c.model.features] == ["Borehole"] * 3
    assert errors(c.validation) == []


def test_values_that_do_not_parse_are_reported_not_guessed():
    ispt = group(
        "ISPT",
        [("LOCA_ID", "", "ID"), ("ISPT_TOP", "m", "2DP"), ("ISPT_NVAL", "", "0DP")],
        [("BH1", "1.00", "50/75"), ("BH1", "2,5", "20"), ("BH1", "3.00", "31")],
    )
    c = convert_file(ags_file(PROJ, TRAN, LOCA, ispt))
    unparsed = [f for f in c.losses if f.rule_id == "convert.value-unparsed"]
    assert {f.path for f in unparsed} == {"ISPT/1/ISPT_NVAL", "ISPT/2/ISPT_TOP"}
    incomplete = _one(c, "convert.incomplete")
    assert incomplete.path == "ISPT/1" and "(2 rows)" in incomplete.message
    assert [m.key for m in c.model.measurements] == ["ISPT-3"]
    assert errors(c.validation) == []


def test_rows_without_their_parent_are_orphans():
    samp = group(
        "SAMP",
        [
            ("LOCA_ID", "", "ID"),
            ("SAMP_TOP", "m", "2DP"),
            ("SAMP_REF", "", "X"),
            ("SAMP_TYPE", "", "PA"),
            ("SAMP_ID", "", "ID"),
        ],
        [("BH1", "1.00", "1", "D", "S1"), ("BH9", "2.00", "1", "D", "S2")],
    )
    lnmc = group(
        "LNMC",
        [
            ("LOCA_ID", "", "ID"),
            ("SAMP_TOP", "m", "2DP"),
            ("SAMP_REF", "", "X"),
            ("SAMP_TYPE", "", "PA"),
            ("SAMP_ID", "", "ID"),
            ("LNMC_MC", "%", "1DP"),
        ],
        [("BH1", "1.00", "1", "D", "S1", "20.5"), ("BH1", "1.00", "1", "D", "S9", "21")],
    )
    c = convert_file(ags_file(PROJ, TRAN, LOCA, samp, lnmc))
    orphans = {f.path for f in c.losses if f.rule_id == "convert.orphan-row"}
    assert orphans == {"SAMP/2", "LNMC/2"}
    assert [s.key for s in c.model.samples] == ["S1"]
    assert c.model.measurements[0].samples == ("S1",)
    assert "SAMP_TOP to SAMP_TOP" in _one(c, "convert.assumed").message
    assert errors(c.validation) == []
    assert c.report.steps[pydiggs.Step.CONVERT] == "failed"


def test_grading_rows_need_their_general_row_and_a_size():
    keys: Spec = [
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", "m", "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
    ]
    samp = group("SAMP", keys, [("BH1", "1.00", "1", "B", "S1")])
    grag = group("GRAG", [*keys, ("GRAG_GRAV", "%", "0DP")], [("BH1", "1.00", "1", "B", "S1", "")])
    grat = group(
        "GRAT",
        [*keys, ("GRAT_SIZE", "mm", "3SF"), ("GRAT_PERP", "%", "0DP")],
        [
            ("BH1", "1.00", "1", "B", "S1", "2", "50"),
            ("BH1", "1.00", "1", "B", "S9", "2", "40"),
            ("BH1", "1.00", "1", "B", "S1", "", "10"),
        ],
    )
    xml, built = accounted(ags_file(PROJ, TRAN, LOCA, samp, grag, grat))
    valid(xml)
    findings = {(f.rule_id, f.path) for f in built.ledger.findings()}
    assert ("convert.incomplete", "GRAG/1") in findings  # no result value
    assert ("convert.orphan-row", "GRAT/2") in findings
    assert ("convert.incomplete", "GRAT/3") in findings  # no particle size
    assert ("convert.incomplete", "GRAT/1") in findings  # its test was not written
    assert built.model.measurements == ()


def test_grading_rows_are_written_only_under_the_method_they_were_measured_by(
    tier_a: convert.Conversion,
):
    grading = tier_a.model.get("GRAG-1")
    assert isinstance(grading, pydiggs.model.Measurement) and grading.procedure is not None
    sieve, hydrometer = grading.procedure.components
    assert (sieve.element, sieve.role) == ("SieveAnalysis", "sieveAnalysis")
    assert [c.element for c in sieve.components] == ["Grading"] * 3  # WS, DS and empty
    assert (hydrometer.element, hydrometer.role) == ("Hydrometer", "hydrometer")
    (reading,) = hydrometer.components
    assert reading.element == "Sedimentation" and reading.role == "sedimentationData"
    assert {k: v.text for k, v in reading.params.items()} == {  # type: ignore[union-attr]
        "particleDiameter": "0.020",
        "percentPassing": "12",
    }
    assert tier_a.trace["GRAT-4"].row == 4
    assert "GRAT-5" not in tier_a.trace  # the pipette row
    pipette = _one(tier_a, "convert.unmapped-code")
    assert (
        pipette.path == "GRAT/5" and "pipette" in pipette.message and "(1 row)" in pipette.message
    )
    assumed = [f for f in tier_a.losses if f.rule_id == "convert.assumed" and f.path == "GRAT/3"]
    assert len(assumed) == 1 and "sieve gradings" in assumed[0].message


def test_an_unlisted_grading_method_is_not_filed_as_a_sieve_reading():
    keys: Spec = [
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", "m", "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
    ]
    samp = group("SAMP", keys, [("BH1", "1.00", "1", "B", "S1")])
    grag = group(
        "GRAG", [*keys, ("GRAG_GRAV", "%", "0DP")], [("BH1", "1.00", "1", "B", "S1", "20")]
    )
    grat = group(
        "GRAT",
        [*keys, ("GRAT_SIZE", "mm", "3SF"), ("GRAT_PERP", "%", "0DP"), ("GRAT_TYPE", "", "PA")],
        [
            ("BH1", "1.00", "1", "B", "S1", "0.002", "5", "PP"),
            ("BH1", "1.00", "1", "B", "S1", "0.01", "9", "WS+HY"),
        ],
    )
    xml, built = accounted(ags_file(PROJ, TRAN, LOCA, samp, grag, grat))
    valid(xml)
    (test,) = built.model.measurements
    assert test.procedure is not None and test.procedure.components == ()
    findings = [f for f in built.ledger.findings() if f.rule_id == "convert.unmapped-code"]
    assert [(f.path, "(2 rows)" in f.message) for f in findings] == [("GRAT/1", True)]


def test_geology_needs_a_depth_range_and_a_description():
    geol = group(
        "GEOL",
        [
            ("LOCA_ID", "", "ID"),
            ("GEOL_TOP", "m", "2DP"),
            ("GEOL_BASE", "m", "2DP"),
            ("GEOL_DESC", "", "X"),
            ("GEOL_LEG", "", "PA"),
        ],
        [
            ("BH1", "0.00", "1.00", "Soft CLAY", "201"),
            ("BH1", "1.00", "", "SAND", ""),
            ("BH1", "2.00", "3.00", "", "102"),
        ],
    )
    c = convert_file(ags_file(PROJ, TRAN, LOCA, geol))
    incomplete = _one(c, "convert.incomplete")
    assert incomplete.path == "GEOL/2" and "(2 rows)" in incomplete.message
    (layer,) = c.model.observations[0].layers
    assert layer.classification == (pydiggs.model.Code(value="201"),)
    assert errors(c.validation) == []


def test_lat_lon_are_used_without_grid_coordinates():
    spec: Spec = [
        *LOCA_SPEC[:2],
        ("LOCA_LAT", "", "X"),
        ("LOCA_LON", "", "X"),
        ("LOCA_LLZ", "", "X"),
    ]
    loca = group(
        "LOCA",
        spec,
        [("BH1", "CP", "52.5", "-1.25", "WGS84"), ("BH2", "CP", "52:30:00N", "1:15:00W", "")],
    )
    c = convert_file(ags_file(PROJ, TRAN, loca))
    locations = [f.location for f in c.model.features]
    assert [p.coordinates for p in locations if p] == [(52.5, -1.25), (52.5, -1.25)]
    assert all(p is not None and p.crs and p.crs.endswith("/4326") for p in locations)
    assert "WGS 84" in _one(c, "convert.assumed").message
    assert errors(c.validation) == []


def test_text_is_cleaned_of_what_xml_cannot_hold():
    loca = group(
        "LOCA",
        [*LOCA_SPEC, ("LOCA_REM", "", "X"), ("LOCA_LOCA", "", "X")],
        [("BH1", "CP", "1", "2", "OSGB", "", "", " note\x01 <b>&amp; ", "\x02")],
    )
    c = convert_file(ags_file(PROJ, TRAN, loca))
    assert c.model.features[0].remarks == ("note <b>&amp;",)
    assert {f.rule_id for f in c.report.findings} >= {
        "convert.value-reformatted",
        "convert.value-unparsed",
    }
    assert errors(c.validation) == []


def test_a_node_the_writer_refuses_is_dropped_and_its_cells_named(
    monkeypatch: pytest.MonkeyPatch,
):
    file = ags4.read(TIER_A)
    real = _api.write_model
    calls = {"n": 0}

    def refuse_once(model: pydiggs.Model, version: str = "3.0.0") -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("Grading 'GRAT-2': DIGGS 3.0.0 requires something")
        return real(model, version)

    monkeypatch.setattr(_api, "write_model", refuse_once)
    built = map_file(file, load_mapping())
    xml, built = _api._write(built, "3.0.0")
    assert built.model.get("GRAG-1") is None  # the test that holds the grading
    assert built.model.get("BH01") is not None
    findings = {(f.rule_id, f.path) for f in built.ledger.findings()}
    assert ("convert.incomplete", "GRAG/1") in findings
    assert ("convert.incomplete", "GRAT/1") in findings
    for cell in built.ledger.cells():
        assert built.ledger.used.get(cell) or cell in built.ledger.named
    valid(xml)


def test_a_refusal_that_names_no_node_is_a_bug_and_raises(monkeypatch: pytest.MonkeyPatch):
    def refuse(model: pydiggs.Model, version: str = "3.0.0") -> bytes:
        raise ValueError("DIGGS requires document information (model.document)")

    monkeypatch.setattr(_api, "write_model", refuse)
    with pytest.raises(ValueError, match="document information"):
        convert.ags4_to_diggs(TIER_A)


def test_drop_removes_a_feature_and_everything_that_refers_to_it():
    built = map_file(ags4.read(TIER_A), load_mapping())
    built = drop(built, "BH02")
    keys = {node.key for node in built.model.nodes()}
    assert "BH02" not in keys and "BH02-D1" not in keys and "LLPL-2" not in keys
    assert "BH01" in keys
    assert all(link.target != "BH02" for link in built.model.links)
    assert "BH02" not in built.trace
    for cell in built.ledger.cells():
        assert built.ledger.used.get(cell) or cell in built.ledger.named


def test_a_time_budget_gives_a_partial_conversion():
    rows = [(f"BH{i}", "CP", "1", "2", "OSGB", "", "") for i in range(600)]
    file = ags_file(PROJ, TRAN, group("LOCA", LOCA_SPEC, rows))
    clock = iter([0.0] + [10.0] * 10_000)
    built = map_file(file, load_mapping(), deadline=5.0, clock=lambda: next(clock))
    assert not built.complete
    assert 0 < len(built.model.features) < 600
    for cell in built.ledger.cells():
        assert built.ledger.used.get(cell) or cell in built.ledger.named
    assert any(f.rule_id == "convert.incomplete" for f in built.ledger.findings())
    c = convert.ags4_to_diggs(file, time_budget=1e-9)
    assert c.complete in (True, False)  # a fast machine may finish within one check
    assert errors(c.validation) == []


# --- generated files ------------------------------------------------------------------

_VALUES = st.sampled_from(
    [
        "",
        "1",
        "2.50",
        "-3",
        "1e2",
        "NP",
        "abc",
        "1,5",
        " 2 ",
        "<0.1",
        "2026-01-31",
        "2026-01-31T10:15",
        "31/01/2026",
        "x\x01y",
        "BH1",
        "BH2",
        "S1",
        "D",
        "U",
        "CP",
        "TP",
        "OSGB",
        "ZZ",
        "a b",
        "&<>\"'",
    ]
)
_IDS = st.sampled_from(["BH1", "BH2", "1 2", ""])
_GROUPS: dict[str, Spec] = {
    "LOCA": [*LOCA_SPEC, ("LOCA_REM", "", "X"), ("LOCA_STAR", "yyyy-mm-dd", "DT")],
    "GEOL": [
        ("LOCA_ID", "", "ID"),
        ("GEOL_TOP", "m", "2DP"),
        ("GEOL_BASE", "m", "2DP"),
        ("GEOL_DESC", "", "X"),
        ("GEOL_LEG", "", "PA"),
        ("GEOL_GEOL", "", "X"),
    ],
    "SAMP": [
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", "m", "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
        ("SAMP_BASE", "m", "2DP"),
        ("SAMP_DTIM", "yyyy-mm-dd", "DT"),
    ],
    "ISPT": [
        ("LOCA_ID", "", "ID"),
        ("ISPT_TOP", "m", "2DP"),
        ("ISPT_NVAL", "", "0DP"),
        ("ISPT_NPEN", "mm", "0DP"),
        ("ISPT_REP", "", "X"),
    ],
    "LLPL": [
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", "m", "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
        ("LLPL_LL", "%", "0DP"),
        ("LLPL_PL", "%", "0DP"),
    ],
    "GRAG": [
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", "m", "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
        ("GRAG_SAND", "%", "0DP"),
    ],
    "GRAT": [
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", "m", "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
        ("GRAT_SIZE", "mm", "3SF"),
        ("GRAT_PERP", "%", "0DP"),
    ],
    "XYZW": [("LOCA_ID", "", "ID"), ("XYZW_VAL", "", "X")],
}


@st.composite
def _files(draw: st.DrawFn) -> ags4.File:
    groups = [PROJ, TRAN] if draw(st.booleans()) else []
    for name, spec in _GROUPS.items():
        if name != "LOCA" and not draw(st.booleans()):
            continue
        rows = []
        for _ in range(draw(st.integers(0, 3))):
            row = [draw(_IDS)] + [draw(_VALUES) for _ in spec[1:]]
            if name == "LOCA" and draw(st.booleans()):
                row[1:5] = ["CP", "1", "2", "OSGB"]
            rows.append(row)
        groups.append(group(name, spec, rows))
    return ags_file(*groups)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(_files())
def test_generated_files_are_accounted_for_and_valid(file: ags4.File):
    xml, _ = accounted(file)
    valid(xml)


# --- surfaces ---------------------------------------------------------------------------


def test_importing_pydiggs_or_the_preview_does_not_import_or_warn():
    code = (
        "import sys, warnings\n"
        "warnings.simplefilter('error')\n"
        "import pydiggs\n"
        "print('pydiggs.convert' in sys.modules, 'pydiggs.ags4' in sys.modules)\n"
        "from pydiggs.preview import convert\n"
        "print(sorted(convert.__all__))\n"
    )
    result = subprocess.run(  # noqa: S603 - our own interpreter, fixed code
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "False False"
    assert json.loads(lines[1].replace("'", '"')) == [
        "Conversion",
        "MAPPING_VERSION",
        "Origin",
        "ags4_to_diggs",
        "mapping_info",
    ]


def test_the_stable_convert_package_exports_nothing_yet():
    import pydiggs.convert

    assert pydiggs.convert.__all__ == []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from pydiggs.preview import convert as again

    assert again is convert


def test_the_convert_rules_are_in_the_catalogue():
    catalogue = {r.id: r for r in pydiggs.rule_catalogue()}
    for rule_id, severity in _ledger.RULES.items():
        assert catalogue[rule_id].severities == (severity,)
        assert catalogue[rule_id].step is pydiggs.Step.CONVERT


@pytest.mark.parametrize(
    "module",
    [convert, _api, _ledger, _mapping, _values, _to_model],
    ids=lambda m: m.__name__,
)
def test_docstring_examples_run(module: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    result = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert result.failed == 0
