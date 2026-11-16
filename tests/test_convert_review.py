"""The owner-approved review of mapping table 1.0: one test per change, both directions.

Each test names the change of the review brief (2026-11-13, "Changes to make before
release") it checks, and failed before that change was made.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

import pytest

import pydiggs
from pydiggs import ags4, convert
from pydiggs._writer import write_model
from pydiggs.convert._mapping import load_mapping
from pydiggs.convert._to_model import map_file
from pydiggs.model import Column, Feature, Measurement

Spec = Sequence[tuple[str, str, str]]
OSGB = "http://www.opengis.net/def/crs/EPSG/0/27700"
EPSG = "http://www.opengis.net/def/crs/EPSG/0/"
BOREHOLE_TYPE = "https://diggsml.org/def/codes/DIGGS/0.1/boreholeType.xml"
SAMPLE_TYPE = "https://diggsml.org/def/codes/DIGGS/0.1/sampleType.xml"
SAMPLE_KEYS: Spec = [
    ("LOCA_ID", "", "ID"),
    ("SAMP_TOP", "m", "2DP"),
    ("SAMP_REF", "", "X"),
    ("SAMP_TYPE", "", "PA"),
    ("SAMP_ID", "", "ID"),
]
SPECIMEN: Spec = [*SAMPLE_KEYS, ("SPEC_REF", "", "X"), ("SPEC_DPTH", "m", "2DP")]


def group(name: str, spec: Spec, rows: Sequence[Sequence[str]]) -> ags4.Group:
    return ags4.Group(
        name=name,
        headings=tuple(h for h, _, _ in spec),
        units=tuple(u for _, u, _ in spec),
        types=tuple(t for _, _, t in spec),
        rows=[tuple(row) for row in rows],
    )


PROJ = group("PROJ", [("PROJ_ID", "", "ID")], [("P1",)])


def tran(rcon: str = "+", date: str = "2026-11-13") -> ags4.Group:
    spec = [("TRAN_DATE", "yyyy-mm-dd", "DT"), ("TRAN_AGS", "", "X"), ("TRAN_RCON", "", "X")]
    return group("TRAN", spec, [(date, "4.2", rcon)])


def loca(rows: Sequence[Sequence[str]], *extra: tuple[str, str, str]) -> ags4.Group:
    spec = [("LOCA_ID", "", "ID"), ("LOCA_TYPE", "", "PA"), *extra]
    return group("LOCA", spec, rows)


BH1 = loca([("BH1", "CP")])
SAMP = group("SAMP", SAMPLE_KEYS, [("BH1", "1.00", "1", "D", "S1")])


def run(*groups: ags4.Group, base: tuple[ags4.Group, ...] = (PROJ,)) -> convert.Conversion:
    names = {g.name for g in groups}
    head = [g for g in (*base, tran()) if g.name not in names]
    c = convert.ags4_to_diggs(ags4.File(groups=[*head, *groups]))
    assert c.validation is not None
    errors = [f.message for f in c.validation.findings if f.severity is pydiggs.Severity.ERROR]
    assert errors == []
    return c


def losses(c: convert.Conversion, rule: str | None = None) -> dict[str, str]:
    return {str(f.path): f.message for f in c.losses if rule is None or f.rule_id == rule}


def feature(c: convert.Conversion, key: str = "BH1") -> Feature:
    found = c.model.get(key)
    assert isinstance(found, Feature)
    return found


def test_default_ags4_version_is_4_2_and_4_1_1_is_selectable():
    c = run(BH1)
    out = convert.model_to_ags4(c.model).ags4
    assert out is not None and out.version == "4.2"
    assert out.groups["TRAN"].records()[0]["TRAN_AGS"] == "4.2"
    old = convert.model_to_ags4(c.model, ags_version="4.1.1").ags4
    assert old is not None and old.version == "4.1.1"
    assert "4.1.1" in (convert.model_to_ags4.__doc__ or "")
    # the docstring names the BGS validator by its host: compared whole, not as a substring
    doc = " ".join((convert.diggs_to_ags4.__doc__ or "").split())
    named = re.search(r"The BGS AGS validator \(([^)]*)\) and python-ags4 1\.2\.0", doc)
    assert named is not None and named.group(1) == "agsapi.bgs.ac.uk"


# --- change 1: LOCA_TYPE codes ------------------------------------------------------------


def test_change_01_scp_is_a_sounding_and_the_exact_borehole_types_are_mapped():
    c = run(loca([("BH1", "SCP"), ("BH2", "AUGH"), ("BH3", "VC"), ("BH4", "TT")]))
    assert feature(c, "BH1").element == "Sounding"
    assert feature(c, "BH2").params["boreholeType"] == pydiggs.model.Code(
        value="HSA", code_space=BOREHOLE_TYPE
    )
    assert feature(c, "BH3").params["boreholeType"].value == "VC"  # type: ignore[union-attr]
    assert feature(c, "BH4").element == "TrialPit"
    # SCP and TT are written as their element; the code itself is reported
    (code,) = losses(c, "convert.unmapped-code").values()
    assert "their DIGGS element, without the code (2 values)" in code
    back = convert.model_to_ags4(c.model).ags4
    assert back is not None
    assert [r["LOCA_TYPE"] for r in back.groups["LOCA"].records()] == ["", "AUGH", "VC", "TP"]


# --- change 2: SAMP_TYPE codes ------------------------------------------------------------


def test_change_02_samp_type_codes_follow_the_ags_list():
    rows = [
        ("BH1", "1.00", "1", code, f"S{n}")
        for n, code in enumerate(("L", "SPTLS", "M", "TW", "EW", "WS", "SPT"), start=1)
    ]
    c = run(BH1, group("SAMP", SAMPLE_KEYS, rows))
    kinds = [c.model.get(f"S{n}").params["type"] for n in range(1, 8)]  # type: ignore[union-attr]
    assert [k.value if hasattr(k, "value") else k for k in kinds] == [
        "windowless",
        "split_barrel_liner",
        "triple_tube_soil",
        "thin_walled_tube",
        "water_sample",
        "WS",  # not AGS SAMP_TYPE codes: written as text and reported
        "SPT",
    ]
    assert kinds[0] == pydiggs.model.Code(value="windowless", code_space=SAMPLE_TYPE)
    shared = [m for m in losses(c, "convert.unmapped-code").values() if "share" in m]
    assert shared and "2 values" in shared[0]  # TW and EW: DIGGS does not tell them apart
    back = convert.model_to_ags4(c.model).ags4
    assert back is not None
    assert [r["SAMP_TYPE"] for r in back.groups["SAMP"].records()][3:5] == ["UT", "W"]


# --- change 3: codes are not case sensitive, and joined codes match in any order ----------


def test_change_03_codes_match_case_insensitively_and_by_concatenation():
    rows = [("BH1", "cp+rc"), ("BH2", "RC+CP"), ("BH3", "tp")]
    c = run(loca(rows), group("SAMP", SAMPLE_KEYS, [("BH1", "1.00", "1", "u", "S1")]))
    assert feature(c, "BH1").params["boreholeType"].value == "CP-RC"  # type: ignore[union-attr]
    assert feature(c, "BH2").params["boreholeType"].value == "CP-RC"  # type: ignore[union-attr]
    assert feature(c, "BH3").element == "TrialPit"
    assert c.model.get("S1").params["type"].value == "open_tube"  # type: ignore[union-attr]
    assert "convert.unmapped-code" not in {f.rule_id for f in c.losses}
    joined = convert.ags4_to_diggs(
        ags4.File(groups=[PROJ, tran(rcon=";"), loca([("BH1", "CP;RC")])])
    )
    assert feature(joined).params["boreholeType"].value == "CP-RC"  # type: ignore[union-attr]
    grid = [("LOCA_NATE", "m", "2DP"), ("LOCA_NATN", "m", "2DP"), ("LOCA_GREF", "", "PA")]
    lower = run(loca([("BH1", "CP", "451200", "206500", "osgb")], *grid))
    assert feature(lower).location.crs == OSGB  # type: ignore[union-attr]


# --- change 4: OSI, and the EPSG codes of AGS4 4.2 ------------------------------------------

GRID = [("LOCA_NATE", "m", "2DP"), ("LOCA_NATN", "m", "2DP"), ("LOCA_GREF", "", "PA")]


def test_change_04_osi_and_the_4_2_epsg_headings():
    osi = run(loca([("BH1", "CP", "300000", "200000", "OSI")], *GRID))
    assert feature(osi).location.crs == f"{EPSG}29903"  # type: ignore[union-attr]
    spec = [*GRID, ("LOCA_NSRI", "", "0DP")]
    agree = run(loca([("BH1", "CP", "451200", "206500", "OSGB", "27700")], *spec))
    assert feature(agree).location.crs == OSGB  # type: ignore[union-attr]
    assert not losses(agree, "convert.unmapped-code")
    clash = run(loca([("BH1", "CP", "700000", "700000", "OSGB", "2157")], *spec))
    assert feature(clash).location.crs == f"{EPSG}2157"  # type: ignore[union-attr]
    assert "disagree with LOCA_NSRI" in losses(clash, "convert.unmapped-code")["LOCA/1/LOCA_GREF"]
    only = run(loca([("BH1", "CP", "451200", "206500", "", "27700")], *spec))
    assert feature(only).location.crs == OSGB  # type: ignore[union-attr]
    geo = [("LOCA_LAT", "", "X"), ("LOCA_LON", "", "X"), ("LOCA_LLZ", "", "X")]
    latlon = run(
        loca([("BH1", "CP", "52.5", "-1.25", "WGS84", "4258")], *geo, ("LOCA_LLSI", "", "0DP"))
    )
    assert feature(latlon).location.crs == f"{EPSG}4258"  # type: ignore[union-attr]
    assert "LOCA/1/LOCA_LLZ" in losses(latlon, "convert.unmapped-code")
    back = convert.model_to_ags4(agree.model).ags4
    assert back is not None
    record = back.groups["LOCA"].records()[0]
    assert (record["LOCA_GREF"], record["LOCA_NSRI"]) == ("OSGB", "27700")
    old = convert.model_to_ags4(agree.model, ags_version="4.1.1").ags4
    assert old is not None and "LOCA_NSRI" not in old.groups["LOCA"].headings


# --- change 5: LOCA rows ---------------------------------------------------------------------


def test_change_05_loca_loca_is_a_grouping_and_the_rows_follow_the_dictionary():
    c = run(loca([("BH1", "CP", "Sub-station 1")], ("LOCA_LOCA", "", "X")))
    assert feature(c).description is None
    assert "LOCA/1/LOCA_LOCA" in losses(c, "convert.unmapped-heading")
    headings = load_mapping().groups["LOCA"].headings
    assert {"LOCA_CLST", "LOCA_GLDT", "LOCA_VSSL", "LOCA_NSRI", "LOCA_LSRI", "LOCA_LLSI"} <= set(
        headings
    )
    # in no AGS4 edition 4.0.3 to 4.2 (checked against the published dictionaries)
    assert not {"LOCA_CLNT", "LOCA_CHKG", "LOCA_APPG", "LOCA_PDEP"} & set(headings)


# --- change 6: ground level and its vertical datum ---------------------------------------------

LEVEL = [*GRID, ("LOCA_GL", "m", "2DP"), ("LOCA_NATD", "", "X")]


@pytest.mark.parametrize("datum", ["OD Newlyn", "odn", "Ordnance Datum Newlyn"])
def test_change_06_odn_ground_levels_are_written_in_the_compound_crs(datum: str):
    c = run(loca([("BH1", "CP", "451200", "206500", "OSGB", "12.5", datum)], *LEVEL))
    location = feature(c).location
    assert location is not None and location.crs == f"{EPSG}7405" and location.dim == 3
    assert not [m for m in losses(c).values() if "vertical datum" in m]
    back = convert.model_to_ags4(c.model).ags4
    assert back is not None
    record = back.groups["LOCA"].records()[0]
    assert (record["LOCA_GREF"], record["LOCA_NATD"], record["LOCA_GL"]) == (
        "OSGB",
        "OD Newlyn",
        "12.5",
    )


def test_change_06_a_ground_level_without_a_stated_datum_is_reported():
    c = run(loca([("BH1", "CP", "451200", "206500", "OSGB", "12.5", "")], *LEVEL))
    assert feature(c).location.crs == OSGB  # type: ignore[union-attr]
    assumed = losses(c, "convert.assumed").values()
    assert any("does not state their vertical datum" in m for m in assumed)
    other = run(loca([("BH1", "CP", "451200", "206500", "OSGB", "12.5", "Malin")], *LEVEL))
    assert "LOCA/1/LOCA_NATD" in losses(other, "convert.unmapped-code")


# --- change 7: GEOL ----------------------------------------------------------------------------


def test_change_07_geol_form_is_the_unit_name_and_geol_geol_a_project_code():
    spec = [
        ("LOCA_ID", "", "ID"),
        ("GEOL_TOP", "m", "2DP"),
        ("GEOL_BASE", "m", "2DP"),
        ("GEOL_DESC", "", "X"),
        ("GEOL_GEOL", "", "PA"),
        ("GEOL_FORM", "", "X"),
        ("GEOL_BNDF", "", "PA"),
    ]
    rows = [("BH1", "0", "2", "Stiff CLAY", "LC", "London Clay Formation", "WELL-DEFINED")]
    c = run(BH1, group("GEOL", spec, rows))
    layer = c.model.observations[0].layers[0]
    assert layer.geologic_unit == "London Clay Formation"
    lost = losses(c, "convert.unmapped-heading")
    assert "project geology code" in lost["GEOL/1/GEOL_GEOL"]
    assert "GEOL/1/GEOL_BNDF" in lost
    back = convert.model_to_ags4(c.model).ags4
    assert back is not None
    record = back.groups["GEOL"].records()[0]
    assert record["GEOL_FORM"] == "London Clay Formation" and "GEOL_GEOL" not in record


# --- change 8: GRAG fractions ----------------------------------------------------------------


def test_change_08_grag_fractions_are_not_converted_either_way():
    spec = [
        *SPECIMEN,
        ("GRAG_GRAV", "%", "0DP"),
        ("GRAG_SAND", "%", "0DP"),
        ("GRAG_FINE", "%", "0DP"),
        ("GRAG_CLAY", "%", "0DP"),
        ("GRAG_CC", "", "1SF"),
    ]
    rows = [("BH1", "1.00", "1", "D", "S1", "A", "1.00", "30", "50", "20", "5", "2")]
    c = run(BH1, SAMP, group("GRAG", spec, rows))
    test = next(
        m for m in c.model.measurements if m.procedure and m.procedure.type.startswith("Part")
    )
    assert [col.key for col in test.results[0].columns] == [
        "clay_percent_2_micron",
        "coef_curvature",
    ]
    lost = losses(c, "convert.unmapped-heading")
    assert "ISO 14688-1" in lost["GRAG/1/GRAG_GRAV"]
    assert {"GRAG/1/GRAG_SAND", "GRAG/1/GRAG_FINE"} <= set(lost)
    gravel = Column(name="g", key="percent_gravel", uom="%", type="number", values=(30.0,))
    results = test.results[0]
    changed = replace(test, results=(replace(results, columns=(*results.columns, gravel)),))
    model = replace(
        c.model,
        measurements=tuple(changed if m is test else m for m in c.model.measurements),
    )
    out = convert.model_to_ags4(model)
    assert out.ags4 is not None and "GRAG_GRAV" not in out.ags4.groups["GRAG"].headings
    assert any("percent_gravel" in f.message for f in out.losses)


# --- change 9: ISPT ----------------------------------------------------------------------------

ISPT: Spec = [
    ("LOCA_ID", "", "ID"),
    ("ISPT_TOP", "m", "2DP"),
    ("ISPT_NVAL", "", "0DP"),
    ("ISPT_N60", "", "0DP"),
    ("ISPT_REP", "", "X"),
    ("ISPT_PEN3", "mm", "0DP"),
    ("ISPT_PEN4", "mm", "0DP"),
    ("ISPT_PEN5", "mm", "0DP"),
    ("ISPT_PEN6", "mm", "0DP"),
    ("ISPT_WAT", "m", "X"),
]


def test_change_09_n60_and_an_incomplete_test_drive():
    rows = [
        ("BH1", "1.00", "30", "28", "N=30", "75", "75", "75", "75", "Dry"),
        ("BH1", "2.00", "50", "47", "25/75 50/150", "75", "75", "", "", ""),
        ("BH1", "3.00", "50", "", "50/225", "", "", "", "", ""),
    ]
    c = run(BH1, group("ISPT", ISPT, rows))
    tests = [m for m in c.model.measurements if isinstance(m, Measurement)]
    assert tests[0].results[0].to_records() == [{"n_value": 30.0, "n60": 28.0}]
    assert [t.results[0].location.coordinates for t in tests] == [(1.0,), (3.0,)]  # type: ignore[union-attr]
    lost = losses(c, "convert.unmapped-heading")
    assert "short of 300 mm" in lost["ISPT/2/ISPT_NVAL"] and "ISPT/2/ISPT_N60" in lost
    assert "all left out" in lost["ISPT/2"]
    assert "ISPT/1/ISPT_WAT" in losses(c, "convert.value-unparsed")  # "Dry" is reported


# --- changes 10 and 11: LLPL ------------------------------------------------------------------


def test_change_10_np_is_written_in_llpl_pl_only():
    spec = [*SPECIMEN, ("LLPL_LL", "%", "0DP"), ("LLPL_PL", "%", "XN"), ("LLPL_PI", "%", "0DP")]
    rows = [("BH1", "1.00", "1", "D", "S1", "A", "1.00", "NP", "NP", "NP")]
    c = run(BH1, SAMP, group("LLPL", spec, rows))
    back = convert.model_to_ags4(c.model)
    assert back.ags4 is not None
    record = back.ags4.groups["LLPL"].records()[0]
    assert (record.get("LLPL_LL", ""), record["LLPL_PL"], record.get("LLPL_PI", "")) == (
        "",
        "NP",
        "",
    )
    assert any("LLPL_PL" in f.message for f in back.losses if f.rule_id == "convert.assumed")


def test_change_11_llpl_lists_the_4_1_1_headings():
    headings = load_mapping().groups["LLPL"].headings
    for name in ("TYPE", "POIN", "CONE", "1PRE", "1PCF", "SIZE", "PASS", "WC", "STAB", "STYP"):
        assert headings[f"LLPL_{name}"].to == "none", name


# --- changes 12 and 13: natural conditions -----------------------------------------------------


def test_change_12_a_water_content_that_is_not_natural_is_not_written_as_natural():
    spec = [*SPECIMEN, ("LNMC_MC", "%", "1DP"), ("LNMC_ISNT", "", "YN")]
    rows = [
        ("BH1", "1.00", "1", "D", "S1", "A", "1.00", "21.5", "Y"),
        ("BH1", "1.00", "1", "D", "S1", "B", "1.10", "30.1", "n"),
    ]
    c = run(BH1, SAMP, group("LNMC", spec, rows))
    natural = [
        m for m in c.model.measurements if m.procedure and m.procedure.type == "WaterContentTest"
    ]
    assert [m.results[0].to_records() for m in natural] == [[{"water_content_natural": 21.5}]]
    assert "LNMC_ISNT" in losses(c, "convert.unmapped-heading")["LNMC/2/LNMC_MC"]
    assert "LNMC_STYP" in load_mapping().groups["LNMC"].headings


def test_change_13_a_recompacted_density_is_not_written_as_natural():
    spec = [
        *SPECIMEN,
        ("LDEN_COND", "", "PA"),
        ("LDEN_MC", "%", "1DP"),
        ("LDEN_BDEN", "Mg/m3", "2DP"),
        ("LDEN_DDEN", "Mg/m3", "2DP"),
    ]
    rows = [("BH1", "1.00", "1", "D", "S1", "A", "1.00", "Recompacted", "18.0", "2.01", "1.70")]
    c = run(BH1, SAMP, group("LDEN", spec, rows))
    (test,) = [m for m in c.model.measurements if m.procedure]
    assert test.results[0].to_records() == [{"dry_density": 1.7}]
    lost = losses(c, "convert.unmapped-heading")
    assert "recompacted" in lost["LDEN/1/LDEN_BDEN"] and "LDEN/1/LDEN_MC" in lost


# --- change 14: WSTG_CAS -------------------------------------------------------------------------


def test_change_14_the_casing_depth_at_a_water_strike_is_bottom_casing():
    spec = [
        ("LOCA_ID", "", "ID"),
        ("WSTG_DPTH", "m", "2DP"),
        ("WSTG_CAS", "m", "2DP"),
    ]
    c = run(BH1, group("WSTG", spec, [("BH1", "4.50", "4.00")]))
    assert c.xml is not None and b"<bottomCasing>" in c.xml
    (strike,) = feature(c).water_strikes
    assert strike.location is None  # bottomCasing is not the seal location
    assert [e.qname for e in strike.extra] == ["bottomCasing"]
    assert "WSTG_CAS" not in str(losses(c))
    back = convert.model_to_ags4(c.model)
    assert back.ags4 is not None
    record = back.ags4.groups["WSTG"].records()[0]
    assert record["WSTG_CAS"] == "4" and "WSTG_SEAL" not in record


# --- change 15: CDIA ---------------------------------------------------------------------------


def test_change_15_the_casing_diameter_reason_says_what_ags4_says():
    reason = str(load_mapping().groups["CDIA"].headings["CDIA_DIAM"].reason)
    assert "nominal" not in reason and "inside or outside" in reason


# --- change 16: cone groups -------------------------------------------------------------------


def _cone(kind: str) -> convert.Conversion:
    cptg = group(
        "CPTG",
        [("LOCA_ID", "", "ID"), ("CPTG_TESN", "", "X"), ("CPTG_TYPE", "", "PA")],
        [("CPT1", "1", kind)],
    )
    spec = [
        ("LOCA_ID", "", "ID"),
        ("CPTG_TESN", "", "X"),
        ("CPTT_REDN", "", "0DP"),
        ("CPTT_DPTH", "m", "2DP"),
        ("CPTT_QC", "MPa", "3DP"),
        ("CPTT_RF", "%", "2DP"),
        ("CPTT_INCX", "deg", "1DP"),
        ("CPTT_TEMP", "DegC", "1DP"),
        ("CPTT_DUR", "s", "1DP"),
        ("CPTT_PH", "", "1DP"),
    ]
    rows = [("CPT1", "1", "1", "0.50", "1.250", "1.20", "0.5", "8.4", "10.0", "6.9")]
    return run(loca([("CPT1", "CPT")]), cptg, group("CPTT", spec, rows))


def test_change_16_cone_readings_with_a_diggs_property_are_mapped():
    c = _cone("CPTU")
    (test,) = c.model.measurements
    assert [col.key for col in test.results[0].columns] == [
        "tip_resistance",
        "inclination_x",
        "elapsed_time",
        "friction_ratio",
        "temperature",
    ]
    assert "property dictionary has no property" in losses(c)["CPTT/1/CPTT_PH"]
    headings = load_mapping().groups["CPTT"].headings
    assert {"CPTT_TPQC", "CPTT_FFD1", "CPTT_PID", "CPTT_FID", "CPTT_NGAM"} <= set(headings)
    assert {"CPTG_ANBY", "CPTG_DEV"} <= set(load_mapping().groups["CPTG"].headings)
    assert load_mapping().groups["SCPT"].headings["SCPT_FRR"].property == "friction_ratio"


@pytest.mark.parametrize("kind", ["BALL", "t-bar"])
def test_change_16_ball_and_t_bar_tests_are_reported_not_written(kind: str):
    c = _cone(kind)
    assert c.model.measurements == ()
    lost = losses(c, "convert.unmapped-code")
    assert "ball or T-bar" in lost["CPTG/1"] and "CPTT/1" in lost


# --- change 17: the creation date ---------------------------------------------------------------


def test_change_17_a_missing_tran_date_is_an_unknown_creation_date():
    c = convert.ags4_to_diggs(ags4.File(groups=[PROJ, BH1]))  # no TRAN at all
    assert c.xml is not None
    assert b'<creationDate indeterminatePosition="unknown"/>' in c.xml
    assert b"1970-01-01" not in c.xml
    assert c.validation is not None and c.validation.ok
    assert c.model.document is not None and c.model.document.creation_date is None
    assert any("unknown" in m for m in losses(c, "convert.assumed").values())
    back = convert.model_to_ags4(c.model)
    assert back.ags4 is not None
    tran_group = back.ags4.groups["TRAN"]
    assert "TRAN_DATE" in tran_group.headings and tran_group.records()[0]["TRAN_DATE"] == ""
    assert any("REQUIRED" in f.message for f in back.losses if f.rule_id == "convert.assumed")
    assert back.validation is not None and back.validation.ok


def test_change_17_the_writer_and_reader_round_trip_an_unknown_creation_date():
    built = map_file(ags4.File(groups=[PROJ, BH1]), load_mapping())
    assert built.model.document is not None and built.model.document.creation_date is None
    again = pydiggs.read_bytes(write_model(built.model, "3.0.0"))
    assert again.document is not None and again.document.creation_date is None


# --- change 18: the table is reviewed --------------------------------------------------------


def test_change_18_every_group_is_reviewed():
    mapping = load_mapping()
    assert {g.status for g in mapping.groups.values()} == {"reviewed"}
    assert mapping.mapping_version == convert.MAPPING_VERSION == "1.0"
