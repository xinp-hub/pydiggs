"""AGS4 to DIGGS 3.0.0 (``pydiggs.convert.ags4_to_diggs``; the 1.9 preview home is an alias).

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
from pydiggs import ags4, convert
from pydiggs.convert import _api, _from_model, _ledger, _mapping, _to_model, _values
from pydiggs.convert._mapping import load_mapping
from pydiggs.convert._to_model import Built, drop, map_file
from pydiggs.errors import DocumentError
from pydiggs.preview import convert as preview_convert

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


def output_errors(c: convert.Conversion) -> list[tuple[str, str]]:
    """The errors of ``c.validation``, or of validating ``c.xml`` again when the time
    budget ran out during validation (the budget, not the output, failed it then)."""
    if any(rule == "structure.timeout" for rule, _ in errors(c.validation)):
        return errors(pydiggs.validate_bytes(c.xml))
    return errors(c.validation)


# --- property 4: every cell used by a written node, or named by a finding ----------------


def accounted(file: ags4.File, **kw: Any) -> tuple[bytes, Built]:
    """Convert ``file`` as ags4_to_diggs does, and check every cell is accounted for."""
    built = map_file(file, load_mapping(), **kw)
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
    assert tier_a.mapping_version == "1.0"
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
    # the ground level's vertical datum is stated: the point is in OSGB36 + ODN height
    datum = [*LOCA_SPEC, ("LOCA_NATD", "", "X")]
    loca = group("LOCA", datum, [("BH1", "CP", "451200", "206500", "OSGB", "12", "15", "ODN")])
    c = convert_file(ags_file(PROJ, TRAN, loca))
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
    assert any('indeterminatePosition="unknown"' in m for m in messages)
    assert c.model.document is not None and c.model.document.creation_date is None
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
    assumed = [f.message for f in c.losses if f.rule_id == "convert.assumed"]
    assert any("SAMP_TOP to SAMP_TOP" in m for m in assumed)
    assert any("placed at the top of their sample" in m for m in assumed)
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
    codes = [f for f in tier_a.losses if f.rule_id == "convert.unmapped-code"]
    wet_or_dry = [f for f in codes if f.path == "GRAT/1/GRAT_TYPE"]
    assert len(wet_or_dry) == 1 and "(2 values)" in wet_or_dry[0].message
    (pipette,) = [f for f in codes if f.path == "GRAT/5"]
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
        "GRAG", [*keys, ("GRAG_CLAY", "%", "0DP")], [("BH1", "1.00", "1", "B", "S1", "20")]
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
        [*LOCA_SPEC, ("LOCA_REM", "", "X")],
        [("BH1", "CP", "1", "2", "OSGB", "", "", " note\x01 <b>&amp; ")],
    )
    proj = group("PROJ", [("PROJ_ID", "", "ID"), ("PROJ_NAME", "", "X")], [("P1", "\x02")])
    c = convert_file(ags_file(proj, TRAN, loca))
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
    assert output_errors(c) == []


# --- review of the preview (PR 335): one test per finding --------------------------------

SAMP_KEYS: Spec = [
    ("LOCA_ID", "", "ID"),
    ("SAMP_TOP", "m", "2DP"),
    ("SAMP_REF", "", "X"),
    ("SAMP_TYPE", "", "PA"),
    ("SAMP_ID", "", "ID"),
]
SAMP = group("SAMP", SAMP_KEYS, [("BH1", "1.00", "1", "D", "S1")])
GRAG = group(
    "GRAG", [*SAMP_KEYS, ("GRAG_SAND", "%", "0DP")], [("BH1", "1.00", "1", "D", "S1", "40")]
)


def _ispt(spec: Spec, rows: Sequence[Sequence[str]]) -> ags4.Group:
    return group("ISPT", [("LOCA_ID", "", "ID"), *spec], rows)


def _cell(built: Built, group_name: str, row: int, heading: str) -> tuple[str, int, int]:
    column = built.ledger.column(group_name, heading)
    assert column is not None
    return group_name, row, column


def _paths(c: convert.Conversion, rule_id: str) -> set[str | None]:
    return {f.path for f in c.report.findings if f.rule_id == rule_id}


def _xml_char(code: int) -> bool:
    """The XML 1.0 ``Char`` production."""
    return (
        code in (0x09, 0x0A, 0x0D)
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    )


def test_xml_text_removes_exactly_the_characters_xml_1_0_forbids():
    # Finding 1 (CodeQL, a character-class range): the class is now a list of code points;
    # what is removed is exactly the complement of XML 1.0's Char, as before.
    for code in [*range(0x11000), 0x10FFFF]:
        text = f"a{chr(code)}b"
        assert _values.xml_text(text) == (text if _xml_char(code) else "ab"), hex(code)


def test_a_param_is_written_only_in_a_unit_diggs_allows_for_its_element():
    # Finding 2: ISPT_ERAT in m is not a hammer efficiency.
    spec: Spec = [("ISPT_TOP", "m", "2DP"), ("ISPT_NVAL", "", "0DP"), ("ISPT_ERAT", "m", "0DP")]
    c = convert_file(ags_file(PROJ, TRAN, LOCA, _ispt(spec, [("BH1", "1.50", "12", "60")])))
    assert errors(c.validation) == []
    assert _paths(c, "convert.unmapped-unit") == {"ISPT/1/ISPT_ERAT"}
    assert b"hammerEfficiency" not in c.xml
    spec = [("ISPT_TOP", "m", "2DP"), ("ISPT_NVAL", "", "0DP"), ("ISPT_ERAT", "%", "0DP")]
    c = convert_file(ags_file(PROJ, TRAN, LOCA, _ispt(spec, [("BH1", "1.50", "12", "60")])))
    assert b'uom="%"' in c.xml and "convert.unmapped-unit" not in rules(c)


def test_grid_coordinates_are_written_only_in_the_unit_of_the_crs_axes():
    # Finding 3: OSGB grid coordinates in ft are not EPSG:27700 metres.
    spec = [(h, "ft" if u == "m" else u, t) for h, u, t in LOCA_SPEC]
    loca = group("LOCA", spec, [("BH1", "CP", "451200", "206500", "OSGB", "12", "")])
    file = ags_file(PROJ, TRAN, loca)
    accounted(file)
    c = convert_file(file)
    assert errors(c.validation) == []
    assert c.model.features[0].location is not None
    assert c.model.features[0].location.crs != OSGB
    assert b"451200" not in c.xml and b"206500" not in c.xml
    assert _paths(c, "convert.unmapped-unit") == {"LOCA/1/LOCA_NATE", "LOCA/1/LOCA_NATN"}
    # A ground level in another unit than the axes is left out; the grid position is kept.
    spec = [*LOCA_SPEC[:5], ("LOCA_GL", "ft", "2DP"), LOCA_SPEC[6]]
    loca = group("LOCA", spec, [("BH1", "CP", "451200", "206500", "OSGB", "12", "")])
    c = convert_file(ags_file(PROJ, TRAN, loca))
    location = c.model.features[0].location
    assert location is not None and location.coordinates == (451200.0, 206500.0)
    assert location.crs == OSGB
    assert _paths(c, "convert.unmapped-unit") == {"LOCA/1/LOCA_GL"}


def test_a_grid_coordinate_whose_pair_cannot_be_written_is_not_claimed():
    # Finding 4: LOCA_NATE parses, LOCA_NATN does not; the feature is written without
    # either, so LOCA_NATE must be named, not held by the feature.
    loca = group("LOCA", LOCA_SPEC, [("BH1", "CP", "451200", "abc", "OSGB", "12", "15")])
    xml, built = accounted(ags_file(PROJ, TRAN, loca))
    easting = _cell(built, "LOCA", 0, "LOCA_NATE")
    assert not built.ledger.used.get(easting) and easting in built.ledger.named
    assert built.ledger.used.get(_cell(built, "LOCA", 0, "LOCA_ID"))
    assert b"451200" not in xml
    assert built.model.features[0].total_depth is not None


def test_a_test_depth_is_read_as_a_depth():
    # Finding 5: a test depth in ft is not written as metres; one with no unit is assumed m.
    spec: Spec = [("ISPT_TOP", "ft", "2DP"), ("ISPT_NVAL", "", "0DP")]
    c = convert_file(ags_file(PROJ, TRAN, LOCA, _ispt(spec, [("BH1", "5.00", "12")])))
    assert c.model.measurements == ()
    assert "ISPT/1/ISPT_TOP" in _paths(c, "convert.unmapped-unit")
    assert errors(c.validation) == []
    spec = [("ISPT_TOP", "", "2DP"), ("ISPT_NVAL", "", "0DP")]
    c = convert_file(ags_file(PROJ, TRAN, LOCA, _ispt(spec, [("BH1", "5.00", "12")])))
    assert len(c.model.measurements) == 1
    assert any("Depths of ISPT have no unit" in f.message for f in c.losses)


def test_a_measure_with_no_unit_is_written_without_one_only_when_diggs_allows_it():
    # Findings 2 and 6, the other side: dryingTemperature may have no uom, so a number
    # with no UNIT is carried as written; a value that is not a number is reported (it
    # used to be written as text into a numeric element, which DIGGS 3.0.0 refuses).
    spec = [*SAMP_KEYS, ("LNMC_MC", "%", "0DP"), ("LNMC_TEMP", "", "0DP")]
    rows = [
        ("BH1", "1.00", "1", "D", "S1", "20", "105"),
        ("BH1", "1.00", "1", "D", "S1", "21", "hot"),
    ]
    file = ags_file(PROJ, TRAN, LOCA, SAMP, group("LNMC", spec, rows))
    accounted(file)
    c = convert_file(file)
    assert errors(c.validation) == []
    assert b">105</" in c.xml and b"hot" not in c.xml
    assert _paths(c, "convert.value-unparsed") == {"LNMC/2/LNMC_TEMP"}


def test_a_grading_size_without_a_unit_is_not_written():
    # Finding 6: particleSize requires a uom.
    grat = group(
        "GRAT",
        [*SAMP_KEYS, ("GRAT_SIZE", "", "3SF"), ("GRAT_PERP", "%", "0DP")],
        [("BH1", "1.00", "1", "D", "S1", "2", "50")],
    )
    file = ags_file(PROJ, TRAN, LOCA, SAMP, GRAG, grat)
    accounted(file)
    c = convert_file(file)
    assert errors(c.validation) == []
    assert b"particleSize" not in c.xml
    assert "GRAT/1/GRAT_SIZE" in _paths(c, "convert.unmapped-unit")


def test_a_deadline_in_geology_or_gradings_releases_what_was_not_written(
    monkeypatch: pytest.MonkeyPatch,
):
    # Finding 7: layers and readings still buffered when the deadline passes are not in
    # the model, so the cells they used must be named convert.incomplete.
    geol = group(
        "GEOL",
        [
            ("LOCA_ID", "", "ID"),
            ("GEOL_TOP", "m", "2DP"),
            ("GEOL_BASE", "m", "2DP"),
            ("GEOL_DESC", "", "X"),
        ],
        [("BH1", str(i), str(i + 1), "CLAY") for i in range(10)],
    )
    monkeypatch.setattr(_to_model, "_CHECK_EVERY", 6)
    _, built = accounted(ags_file(PROJ, TRAN, LOCA, geol), deadline=5.0, clock=lambda: 10.0)
    assert not built.complete and built.model.observations == ()
    assert built.ledger.named.get(_cell(built, "GEOL", 0, "GEOL_DESC")) is not None
    assert not any(key.startswith("BH1-GEOL") for key in built.trace)
    grat = group(
        "GRAT",
        [*SAMP_KEYS, ("GRAT_SIZE", "mm", "3SF"), ("GRAT_PERP", "%", "0DP")],
        [("BH1", "1.00", "1", "D", "S1", str(i + 1), "50") for i in range(10)],
    )
    monkeypatch.setattr(_to_model, "_CHECK_EVERY", 8)
    _, built = accounted(
        ags_file(PROJ, TRAN, LOCA, SAMP, GRAG, grat), deadline=5.0, clock=lambda: 10.0
    )
    assert not built.complete and built.model.measurements == ()
    assert not any(key.startswith("GRAT") for key in built.trace)


def test_a_repeated_heading_is_accounted_for_in_every_column():
    # Finding 8: the second LOCA_REM is named, not hidden behind the first.
    loca = ags4.Group(
        name="LOCA",
        headings=("LOCA_ID", "LOCA_REM", "LOCA_REM"),
        units=("", "", ""),
        types=("ID", "X", "X"),
        rows=[("BH1", "", "second")],
    )
    _, built = accounted(ags_file(PROJ, TRAN, loca))
    assert (("LOCA", 0, 2)) in built.ledger.named


BLANK_LINES = (
    b'"GROUP","PROJ"\r\n"HEADING","PROJ_ID"\r\n"UNIT",""\r\n"TYPE","ID"\r\n"DATA","P1"\r\n\r\n'
    b'"GROUP","TRAN"\r\n"HEADING","TRAN_ISNO","TRAN_AGS"\r\n"UNIT","",""\r\n"TYPE","X","X"\r\n'
    b'"DATA","1","4.1.1"\r\n\r\n'
    b'"GROUP","LOCA"\r\n"HEADING","LOCA_ID","LOCA_STAT"\r\n"UNIT","",""\r\n"TYPE","ID","X"\r\n'
    b'"DATA","BH1","x"\r\n\r\n\r\n'
    b'"DATA","BH2",""\r\n'
    b"\r\n"
    b'"DATA","BH3","y"\r\n'
)


def test_trace_and_findings_give_the_real_line_of_each_data_row():
    # Finding 9: blank lines between DATA rows; lines are 17, 20 and 22.
    c = convert.ags4_to_diggs(BLANK_LINES, name="blank.ags")
    assert [c.trace[k].line for k in ("BH1", "BH2", "BH3")] == [17, 20, 22]
    stat = [f for f in c.losses if f.path is not None and f.path.endswith("LOCA_STAT")]
    assert [f.line for f in stat] == [17]
    lines = BLANK_LINES.split(b"\r\n")
    assert all(lines[n - 1].startswith(b'"DATA"') for n in (17, 20, 22))
    assert _ledger.row_line(ags4.read_bytes(BLANK_LINES).groups["LOCA"], 2) == 22
    assert _ledger.row_line(LOCA, 0) is None  # built in code: no line


def test_a_record_may_be_named_document():
    # Finding 10: the document information's key is taken first.
    loca = group("LOCA", LOCA_SPEC, [("document", "CP", "451200", "206500", "OSGB", "12", "15")])
    file = ags_file(PROJ, TRAN, loca)
    accounted(file)
    c = convert_file(file)
    assert [f.name for f in c.model.features] == ["document"]
    assert errors(c.validation) == []


def test_lrs_ids_do_not_collide_with_feature_ids():
    # Finding 11: BH1's LRS and the feature BH1-lrs.
    for order in (("BH1", "BH1-lrs"), ("BH1-lrs", "BH1")):
        rows = [(name, "CP", "1", "2", "OSGB", "", "") for name in order]
        file = ags_file(PROJ, TRAN, group("LOCA", LOCA_SPEC, rows))
        accounted(file)
        c = convert_file(file)
        assert [f.name for f in c.model.features] == list(order)
        assert errors(c.validation) == []


@pytest.mark.parametrize("where", ["lookups", "transmission"])
def test_a_deadline_before_the_document_gives_a_partial_conversion(
    where: str, monkeypatch: pytest.MonkeyPatch
):
    # Finding 12: a deadline in lookups() or transmission() still gives document information.
    abbr = group(
        "ABBR",
        [("ABBR_HDNG", "", "X"), ("ABBR_CODE", "", "X"), ("ABBR_DESC", "", "X")],
        [("SAMP_TYPE", f"C{i}", "code") for i in range(10)],
    )
    tran = group(
        "TRAN",
        [("TRAN_DATE", "yyyy-mm-dd", "DT"), ("TRAN_AGS", "", "X")],
        [("2026-11-12", "4.1.1")] * 10,
    )
    file = ags_file(abbr, PROJ, TRAN, LOCA) if where == "lookups" else ags_file(PROJ, tran, LOCA)
    monkeypatch.setattr(_to_model, "_CHECK_EVERY", 5)
    xml, built = accounted(file, deadline=5.0, clock=lambda: 10.0)
    assert not built.complete
    assert built.model.document.creation_date is None  # written as unknown
    assert built.ledger.named.get(("TRAN", 0, 0)) is not None
    assert not built.ledger.used.get(("TRAN", 0, 0))
    valid(xml)


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("2026-02-30", None),
        ("2025-02-29", None),
        ("2024-02-29", "2024-02-29"),
        ("2026-04-31", None),
        ("2026-00-10", None),
        ("0000-01-01", None),
        ("2026-03-04T10:15+99:99", None),
        ("2026-03-04T10:15+14:00", "2026-03-04T10:15:00+14:00"),
        ("2026-03-04T10:15-14:00", "2026-03-04T10:15:00-14:00"),
        ("2026-03-04T10:15+14:01", None),
        ("2026-03-04T10:15+13:60", None),
        ("2026-03-04T10:15Z", "2026-03-04T10:15:00Z"),
        ("2026-02-30T10:15", None),
        ("30/02/2026", None),
        ("٢٠٢٦-01-01", None),
    ],
)
def test_dates_are_real_calendar_days_with_a_bounded_zone(text: str, parsed: str | None):
    # Finding 13.
    assert _values.parse_date(text, "dd/mm/yyyy") == parsed


def test_an_impossible_date_is_reported_as_unparsed():
    spec = [*SAMP_KEYS, ("SAMP_DTIM", "yyyy-mm-dd", "DT")]
    samp = group("SAMP", spec, [("BH1", "1.00", "1", "D", "S1", "2026-02-30")])
    c = convert_file(ags_file(PROJ, TRAN, LOCA, samp))
    assert _paths(c, "convert.value-unparsed") == {"SAMP/1/SAMP_DTIM"}
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


# --- hostile inputs: the whole contract, end to end ---------------------------------------

_HOSTILE_UNITS = st.sampled_from(["", "m", "ft", "mm", "%", "kPa", "furlong", "deg", "dd/mm/yyyy"])
_HOSTILE_IDS = st.sampled_from(
    ["BH1", "BH1-lrs", "BH1-lrs-2", "document", "project", "BH1-GEOL", "ISPT-1", "1 2", ""]
)
_HOSTILE_VALUES = st.sampled_from(
    [
        "",
        "1",
        "2.50",
        "-3",
        "abc",
        "NP",
        "2026-02-30",
        "2025-02-29",
        "2024-02-29",
        "2026-04-31",
        "2026-03-04T10:15+99:99",
        "2026-03-04T10:15+14:00",
        "31/04/2026",
        "OSGB",
        "EPSG:2263",
        "WGS84",
        "CP",
        "HY",
        "D",
        "S1",
        "١٢",
        'say "x"',
    ]
)
_HOSTILE_GROUPS: dict[str, Spec] = {
    **_GROUPS,
    "LOCA": [
        *LOCA_SPEC,
        ("LOCA_REM", "", "X"),
        ("LOCA_LAT", "", "X"),
        ("LOCA_LON", "", "X"),
        ("LOCA_LLZ", "", "PA"),
    ],
    "ISPT": [
        ("LOCA_ID", "", "ID"),
        ("ISPT_TOP", "m", "2DP"),
        ("ISPT_NVAL", "", "0DP"),
        ("ISPT_ERAT", "%", "0DP"),
        ("ISPT_NPEN", "mm", "0DP"),
    ],
    "GRAT": [*_GROUPS["GRAT"], ("GRAT_TYPE", "", "PA")],
    "ABBR": [("ABBR_HDNG", "", "X"), ("ABBR_CODE", "", "X")],
}


@st.composite
def _hostile(draw: st.DrawFn) -> tuple[bytes, int]:
    """AGS4 text with odd units, repeated headings, blank lines, colliding ids and bad dates;
    and how many looks at the clock pass before the deadline (-1: none)."""
    groups = [PROJ, TRAN] if draw(st.booleans()) else []
    for name, spec in _HOSTILE_GROUPS.items():
        if name != "LOCA" and not draw(st.booleans()):
            continue
        headings = [(h, draw(_HOSTILE_UNITS) if u else u, t) for h, u, t in spec]
        if draw(st.booleans()):
            headings.append(draw(st.sampled_from(headings[1:])))  # a repeated heading
        rows = []
        for _ in range(draw(st.integers(0 if name != "LOCA" else 1, 3))):
            row = [draw(_HOSTILE_IDS)] + [draw(_HOSTILE_VALUES) for _ in headings[1:]]
            if name == "LOCA" and draw(st.booleans()):
                row[1:5] = ["CP", "451200", "206500", "OSGB"]
            rows.append(row)
        groups.append(group(name, headings, rows))
    lines = ags4.dumps(ags_file(*groups)).split(b"\r\n")
    blanks = draw(st.lists(st.integers(0, len(lines)), max_size=4))
    for at in sorted(blanks, reverse=True):
        lines.insert(at, b"")
    return b"\r\n".join(lines), draw(st.integers(-1, 12))


class _Clock:
    """``time.monotonic`` for :mod:`pydiggs.convert._api`: past the deadline after ``looks``."""

    def __init__(self, looks: int) -> None:
        self.looks = looks

    def monotonic(self) -> float:
        self.looks -= 1
        return 0.0 if self.looks >= 0 else 1e9


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(_hostile())
def test_hostile_files_convert_to_valid_diggs_with_every_cell_accounted_for(
    case: tuple[bytes, int],
):
    data, looks = case
    captured: list[Built] = []
    write = _api._write

    def keep(built: Built, version: str) -> tuple[bytes, Built]:
        xml, built = write(built, version)
        captured.append(built)
        return xml, built

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_api, "_write", keep)
        patch.setattr(_to_model, "_CHECK_EVERY", 1)
        if looks >= 0:
            patch.setattr(_api, "time", _Clock(looks + 1))  # +1: the start of the budget
        try:
            c = convert.ags4_to_diggs(data, name="hostile.ags", time_budget=1.0)
        except (DocumentError, pydiggs.InstanceReadError):
            return  # the reader's documented refusals
    assert output_errors(c) == []
    (built,) = captured
    ledger = built.ledger
    for cell in ledger.cells():
        owners = ledger.used.get(cell, set())
        assert owners or cell in ledger.named, f"cell {cell} is neither used nor named"
        for owner in owners - {""}:
            assert built.model.get(owner) is not None or any(
                component.key == owner
                for node in built.model.nodes()
                for component in _to_model.components_of(node)
            ), f"cell {cell} is held by {owner!r}, which is not written"
    lines = data.split(b"\r\n")
    for origin in c.trace.values():
        assert origin.line is not None and lines[origin.line - 1].startswith(b'"DATA"')
    for finding in c.report.findings:
        if finding.step is pydiggs.Step.CONVERT and finding.line is not None:
            assert lines[finding.line - 1].startswith((b'"DATA"', b'"GROUP"'))


# --- surfaces ---------------------------------------------------------------------------


def test_importing_pydiggs_or_the_preview_does_not_import_or_warn():
    code = (
        "import sys, warnings\n"
        "warnings.simplefilter('error')\n"
        "import pydiggs\n"
        "print('pydiggs.convert' in sys.modules, 'pydiggs.ags4' in sys.modules)\n"
        "from pydiggs.preview import convert\n"
        "print(sorted(convert.__all__))\n"
        "from pydiggs import convert as stable\n"
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


def test_the_stable_convert_package_exports_its_public_names():
    import pydiggs.convert

    assert pydiggs.convert.__all__ == [
        "MAPPING_VERSION",
        "Conversion",
        "Origin",
        "ags4_to_diggs",
        "diggs_to_ags4",
        "mapping_info",
        "model_to_ags4",
    ]
    assert pydiggs.convert is convert


def test_the_1_9_preview_names_are_deprecated_aliases_of_the_stable_ones():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from pydiggs.preview import convert as preview  # importing does not warn

    for name in preview.__all__:
        with pytest.warns(DeprecationWarning, match=f"pydiggs.convert.{name}"):
            assert getattr(preview, name) is getattr(convert, name)
    assert set(preview.__all__) <= set(dir(preview))
    with pytest.raises(AttributeError):
        preview.nothing  # noqa: B018


def test_the_convert_rules_are_in_the_catalogue():
    catalogue = {r.id: r for r in pydiggs.rule_catalogue()}
    for rule_id, severity in _ledger.RULES.items():
        assert catalogue[rule_id].severities == (severity,)
        assert catalogue[rule_id].step is pydiggs.Step.CONVERT


@pytest.mark.parametrize(
    "module",
    [convert, _api, _ledger, _mapping, _values, _to_model, _from_model, preview_convert],
    ids=lambda m: m.__name__,
)
def test_docstring_examples_run(module: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    result = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert result.failed == 0


# --- 1.9.0 pre-release review: limits on the read-back and the time budget -------------


def _sites(n: int) -> ags4.File:
    """``n`` boreholes with one sample each."""
    loca = [(f"BH{i}", "CP", "451200", "206500", "OSGB", "12", "15") for i in range(n)]
    samp = [(f"BH{i}", "1.00", "1", "D", "S1") for i in range(n)]
    return ags_file(PROJ, TRAN, group("LOCA", LOCA_SPEC, loca), group("SAMP", SAMP_KEYS, samp))


def test_output_larger_than_the_default_limit_is_still_read_back_and_validated(
    monkeypatch: pytest.MonkeyPatch,
):
    # The XML is pyDIGGS's own, often ~19 times the AGS4 input: the readers' default
    # 100 MiB limit (made small here) must not refuse it.
    assert _api.validate_bytes.__kwdefaults__ is not None
    assert _api.read_model.__kwdefaults__ is not None
    monkeypatch.setitem(_api.validate_bytes.__kwdefaults__, "max_bytes", 1_000)
    monkeypatch.setitem(_api.read_model.__kwdefaults__, "max_bytes", 1_000)
    c = convert.ags4_to_diggs(_sites(3))
    assert len(c.xml) > 1_000
    assert c.complete
    assert errors(c.validation) == []
    assert {"BH0", "BH1", "BH2"} <= {f.key for f in c.model.features}


class _SteppedClock:
    """``time.monotonic`` for :mod:`pydiggs.convert._api` that only moves when told to."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def _timed(monkeypatch: pytest.MonkeyPatch, writing: float) -> dict[str, float | None]:
    """Make writing take ``writing`` seconds and validating 3; record the budgets passed."""
    clock = _SteppedClock()
    budgets: dict[str, float | None] = {}
    write, validate, read = _api._write, _api.validate_bytes, _api.read_model

    def slow_write(built: Built, version: str) -> tuple[bytes, Built]:
        clock.now += writing
        return write(built, version)

    def slow_validate(xml: bytes, **kw: Any) -> pydiggs.Report:
        budgets["validate"] = kw.get("time_budget")
        clock.now += 3.0
        return validate(xml, **kw)

    def spy_read(xml: bytes, **kw: Any) -> pydiggs.Model:
        budgets["read"] = kw.get("time_budget")
        return read(xml, **kw)

    monkeypatch.setattr(_api, "time", clock)
    monkeypatch.setattr(_api, "_write", slow_write)
    monkeypatch.setattr(_api, "validate_bytes", slow_validate)
    monkeypatch.setattr(_api, "read_model", spy_read)
    return budgets


def test_the_time_budget_also_covers_validation_and_the_read_back(
    monkeypatch: pytest.MonkeyPatch,
):
    budgets = _timed(monkeypatch, writing=2.0)
    c = convert.ags4_to_diggs(_sites(2), time_budget=10.0)
    assert budgets == {"validate": pytest.approx(8.0), "read": pytest.approx(5.0)}
    assert c.complete
    assert errors(c.validation) == []


def test_a_spent_time_budget_gives_a_partial_model_and_a_timed_out_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    budgets = _timed(monkeypatch, writing=20.0)
    c = convert.ags4_to_diggs(_sites(2), time_budget=10.0)
    assert budgets["validate"] is not None and 0 < budgets["validate"] < 1e-6
    assert budgets["read"] is not None and 0 < budgets["read"] < 1e-6
    assert not c.complete
    assert c.validation is not None
    assert "structure.timeout" in {f.rule_id for f in c.validation.findings}
    assert not c.model.complete
    assert errors(pydiggs.validate_bytes(c.xml)) == []  # the XML itself is whole
    no_budget = convert.ags4_to_diggs(_sites(2))
    assert c.xml == no_budget.xml


def _convert_work(file: ags4.File) -> int:
    """Lines of pydiggs.convert code executed to convert ``file``: a deterministic count."""
    count = 0
    folder = str(Path(_api.__file__).parent)

    def tracer(frame: Any, event: str, arg: Any) -> Any:
        nonlocal count
        if not frame.f_code.co_filename.startswith(folder):
            return None
        if event == "line":
            count += 1
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        convert.ags4_to_diggs(file, validate=False)
    finally:
        sys.settrace(previous)
    return count


@pytest.mark.scaling
def test_conversion_work_is_linear_in_the_number_of_rows():
    small, large = _convert_work(_sites(100)), _convert_work(_sites(200))
    assert large <= 2.3 * small, (small, large)


def test_a_depth_in_another_unit_is_reported_without_preview_wording():
    loca = group("LOCA", LOCA_SPEC, [("BH1", "", "1", "2", "", "", "")])
    ispt = group(
        "ISPT",
        [("LOCA_ID", "", "ID"), ("ISPT_TOP", "ft", "2DP"), ("ISPT_NVAL", "", "0DP")],
        [("BH1", "3.00", "12")],
    )
    c = convert_file(ags_file(PROJ, TRAN, loca, ispt))
    found = [f for f in c.losses if f.path == "ISPT/1/ISPT_TOP"]
    assert [f.rule_id for f in found] == ["convert.unmapped-unit"]
    assert "preview" not in found[0].message
    assert "the converter does not write" in found[0].message
