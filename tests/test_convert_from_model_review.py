"""DIGGS to AGS4, pre-release review of 1.11.0: values are written or named, never dropped.

Each test is the reviewer's reproduction of one finding: a silent loss (an empty key or
unit, a value counted as written that was not), a crash on untrusted input, or work that
grows faster than the input.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pydiggs import _service, ags4, convert
from pydiggs.ags4 import Group

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
UNMAPPED_UNIT = "convert.unmapped-unit"
EPSG = "http://www.opengis.net/def/crs/EPSG/0/"
POINT = (
    '<PointLocation gml:id="BH01.referencePoint" srsName="' + EPSG + '27700" srsDimension="3">\n'
    "          <gml:pos>451200 206500 12.5</gml:pos>"
)


@pytest.fixture(scope="module")
def tier_a() -> str:
    xml = convert.ags4_to_diggs(FIXTURES / "convert_tier_a.ags").xml
    assert xml is not None
    return xml.decode()


@pytest.fixture(scope="module")
def tier_b() -> str:
    xml = convert.ags4_to_diggs(FIXTURES / "site_tier_b_4_1_1.ags").xml
    assert xml is not None
    return xml.decode()


def rules(c: convert.Conversion, rule: str) -> list[str]:
    return [f.message for f in c.losses if f.rule_id == rule]


def column(c: convert.Conversion, group: str, heading: str) -> tuple[str, ...]:
    assert c.ags4 is not None
    return c.ags4.groups[group].column(heading)


def cells(c: convert.Conversion, group: str, heading: str) -> tuple[str, ...]:
    """The values under ``heading``; none when the group or heading was not written."""
    assert c.ags4 is not None
    g = c.ags4.groups.get(group)
    return () if g is None or heading not in g.headings else g.column(heading)


def unit(c: convert.Conversion, group: str, heading: str) -> str:
    assert c.ags4 is not None
    g = c.ags4.groups[group]
    return g.units[g.headings.index(heading)]


def with_location(xml: str, code: str, dim: int, pos: str) -> str:
    """BH01's reference point in EPSG ``code`` (another feature keeps its own CRS)."""
    assert POINT in xml
    srs = f' srsName="{EPSG}{code}"'
    return xml.replace(
        POINT,
        f'<PointLocation gml:id="BH01.referencePoint"{srs} srsDimension="{dim}">\n'
        f"          <gml:pos>{pos}</gml:pos>",
    )


# --- finding 1: a depth whose unit clashes is reported, never written with an empty key -----


def second_cone_in_feet(xml: str, uom: str = "ft") -> str:
    """A copy of the CPT01 sounding and its test as CPT02, whose LRS is in ``uom``."""
    s0 = xml.index('  <samplingFeature>\n    <Sounding gml:id="CPT01">')
    s1 = xml.index("</samplingFeature>", s0) + len("</samplingFeature>\n")
    sounding = (
        xml[s0:s1].replace("CPT01", "CPT02").replace("<units>m</units>", f"<units>{uom}</units>")
    )
    m0 = xml.index('  <measurement>\n    <Test gml:id="SCPG-1">')
    m1 = xml.index("</measurement>", m0) + len("</measurement>\n")
    test = (
        xml[m0:m1]
        .replace("SCPG-1", "SCPG-2")
        .replace("CPT01", "CPT02")
        .replace("<gml:name>1</gml:name>", "<gml:name>2</gml:name>")
    )
    return xml[:s1] + sounding + xml[s1:m1] + test + xml[m1:]


@pytest.mark.parametrize(
    ("version", "group", "depth"),
    [("4.2", "CPTT", "CPTT_DPTH"), ("4.1.1", "SCPT", "SCPT_DPTH")],
    ids=["ags-4.2", "ags-4.1.1"],
)
def test_a_cone_in_another_unit_is_reported_and_leaves_no_empty_depth(
    tier_b: str, version: str, group: str, depth: str
):
    c = convert.diggs_to_ags4(second_cone_in_feet(tier_b).encode(), ags_version=version)
    assert column(c, group, depth) == ("0.5", "1", "1.5")  # the first LRS (m) sets the unit
    assert unit(c, group, depth) == "m"
    assert set(column(c, group, "LOCA_ID")) == {"CPT01"}
    lost = rules(c, UNMAPPED_UNIT)
    assert lost and "another unit" in lost[0]
    assert not c.lossless
    assert c.ags4 is not None
    ags4.read_bytes(ags4.dumps(c.ags4))  # the file still reads


# --- finding 2: units of CRSs outside the code table, and unnamed units ---------------------


def test_a_utm_location_takes_its_unit_from_the_epsg_axis(tier_a: str):
    c = convert.diggs_to_ags4(with_location(tier_a, "32615", 3, "500000 4000000 200").encode())
    assert column(c, "LOCA", "LOCA_NATE")[0] == "500000"
    assert unit(c, "LOCA", "LOCA_NATE") == "m"
    assert rules(c, UNMAPPED_UNIT) == []


def test_a_us_foot_location_is_reported_not_written_without_a_unit(tier_a: str):
    # AGS4's unit table has no US survey foot, and writing "ft" would change the values
    c = convert.diggs_to_ags4(with_location(tier_a, "2263", 2, "987000 200000").encode())
    assert "987000" not in cells(c, "LOCA", "LOCA_NATE")
    assert any("unit" in m for m in rules(c, UNMAPPED_UNIT))


@pytest.mark.parametrize("code", ["2263", "99999"], ids=["us-foot", "unresolvable"])
def test_an_unresolved_crs_cannot_set_the_heading_unit_for_the_others(tier_a: str, code: str):
    c = convert.diggs_to_ags4(with_location(tier_a, code, 2, "987000 200000").encode())
    assert unit(c, "LOCA", "LOCA_NATE") == "m"
    assert set(cells(c, "LOCA", "LOCA_NATE")) - {""} == {"451260.5", "451230"}  # BH02, TP01
    assert any("987000" not in v for v in cells(c, "LOCA", "LOCA_NATE"))
    lost = rules(c, UNMAPPED_UNIT)
    assert lost and not any("another unit" in m for m in lost)


def test_two_crs_with_the_same_unit_are_both_written(tier_a: str):
    c = convert.diggs_to_ags4(with_location(tier_a, "32615", 3, "500000 4000000 200").encode())
    assert {"500000", "451230", "451260.5"} <= set(cells(c, "LOCA", "LOCA_NATE"))
    assert rules(c, UNMAPPED_UNIT) == []


def test_a_cone_in_us_feet_is_reported_and_writes_no_unitless_depth(tier_b: str):
    i = tier_b.index('<Sounding gml:id="CPT01">')
    xml = tier_b[:i] + tier_b[i:].replace("<units>m</units>", "<units>ft[US]</units>", 1)
    c = convert.diggs_to_ags4(xml.encode(), ags_version="4.1.1")
    assert "" not in cells(c, "SCPT", "SCPT_DPTH")
    assert unit(c, "SCPT", "SCPT_DPTH") != "" if cells(c, "SCPT", "SCPT_DPTH") else True
    assert any("unit" in m for m in rules(c, UNMAPPED_UNIT))
    assert not c.lossless


def test_a_cone_in_international_feet_is_written_in_ft(tier_b: str):
    i = tier_b.index('<Sounding gml:id="CPT01">')
    xml = tier_b[:i] + tier_b[i:].replace("<units>m</units>", "<units>ft</units>", 1)
    c = convert.diggs_to_ags4(xml.encode(), ags_version="4.1.1")
    assert unit(c, "SCPT", "SCPT_DPTH") == "ft"
    assert column(c, "SCPT", "SCPT_DPTH") == ("0.5", "1", "1.5")


# --- finding 3: the end of a sampling date is a loss ----------------------------------------


def test_the_end_of_a_sampling_date_is_reported_not_marked_written(tier_a: str):
    old = "<start>2026-03-02T10:15:00</start>"
    assert old in tier_a
    xml = tier_a.replace(old, old + "<end>2026-03-02T11:00:00</end>")
    c = convert.diggs_to_ags4(xml.encode())
    assert "2026-03-02T10:15:00" in column(c, "SAMP", "SAMP_DTIM")
    assert "2026-03-02T11:00:00" not in " ".join(column(c, "SAMP", "SAMP_DTIM"))
    ended = [f for f in c.losses if "date.end" in f.message]
    assert ended, [f.message for f in c.losses]


# --- finding 4: a name AGS4 cannot write is reported, not a crash ---------------------------


def sounding_named(xml: str, name: str) -> str:
    return xml.replace("<gml:name>CPT01</gml:name>", f"<gml:name>{name}</gml:name>", 1)


def test_a_location_name_with_a_line_break_falls_back_to_the_id_and_is_reported(tier_b: str):
    xml = sounding_named(tier_b, "CPT&#10;01")
    c = convert.diggs_to_ags4(xml.encode())
    assert c.ags4 is not None
    assert "CPT01" in column(c, "LOCA", "LOCA_ID")
    assert not any("\n" in v for v in column(c, "LOCA", "LOCA_ID"))
    assert any("line break" in f.message for f in c.losses)
    ags4.dumps(c.ags4)  # does not raise


def test_a_location_name_with_a_line_break_is_a_result_over_the_service(tier_b: str):
    xml = sounding_named(tier_b, "CPT&#10;01").encode()
    outcome = _service.convert(_service.ConvertRequest(content=xml, to="ags4"))
    assert outcome.code is None, outcome.detail


def test_a_location_name_with_a_line_break_is_a_result_on_the_command_line(
    tier_b: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    from pydiggs.cli import main

    source = tmp_path / "in.xml"
    source.write_bytes(sounding_named(tier_b, "CPT&#10;01").encode())
    out = tmp_path / "out.ags"
    code = main(["convert", str(source), "-o", str(out), "--to", "ags4"])
    capsys.readouterr()
    assert code in (0, 1) and out.exists()


# --- finding 5: the writer does linear work -------------------------------------------------


def distinct_sample_types(xml: str, n: int) -> str:
    a0 = xml.index("  <samplingActivity>")
    a1 = xml.index("</samplingActivity>", a0) + len("</samplingActivity>\n")
    s0 = xml.index("  <sample>")
    s1 = xml.index("</sample>", s0) + len("</sample>\n")
    activity, sample = xml[a0:a1], xml[s0:s1]
    activities, samples = [], []
    for i in range(n):
        activities.append(activity.replace("BH01-U1", f"S{i}"))
        text = sample.replace("BH01-U1", f"S{i}")
        samples.append(
            re.sub(r'<type codeSpace="[^"]*">open_tube</type>', f"<type>T{i}</type>", text)
        )
    return xml[:a0] + "".join(activities) + xml[a0:s0] + "".join(samples) + xml[s0:]


@pytest.mark.scaling
@pytest.mark.parametrize("n", [100, 400], ids=["small", "large"])
def test_distinct_free_text_sample_types_cost_linear_work(
    tier_a: str, n: int, monkeypatch: pytest.MonkeyPatch
):
    scanned = [0]
    original = Group.column

    def counting(self: Group, heading: str) -> tuple[str, ...]:
        scanned[0] += len(self.rows)
        return original(self, heading)

    monkeypatch.setattr(Group, "column", counting)
    work = []
    for size in (n, 2 * n):
        scanned[0] = 0
        convert.diggs_to_ags4(distinct_sample_types(tier_a, size).encode())
        work.append(scanned[0])
    assert work[1] <= 2.5 * work[0], (
        f"{n} -> {2 * n} sample types grew work {work[1] / work[0]:.1f}x"
    )


# --- finding 7: saving a conversion that has no output ----------------------------------------


def test_save_of_a_conversion_with_no_output_raises(tier_a: str, tmp_path: Path):
    from dataclasses import replace

    c = replace(convert.diggs_to_ags4(tier_a.encode()), ags4=None, xml=None)
    with pytest.raises(ValueError, match="no output to save"):
        c.save(tmp_path / "out")
    assert not (tmp_path / "out").exists()


# --- finding 6: the stable table does not call itself a preview ---------------------------------


def test_no_reason_in_the_mapping_table_says_preview():
    from pydiggs.convert._mapping import load_mapping

    mapping = load_mapping()
    reasons = [
        (group.name, heading.name, str(heading.reason))
        for group in mapping.groups.values()
        for heading in group.headings.values()
    ]
    assert reasons and not [r for r in reasons if "preview" in r[2].lower()]
