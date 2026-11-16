"""The US survey foot is the AGS4 unit ftUS, in both directions (mapping table 1.1).

AGS4 4.2 section 6.2 and Rule 15 let a project define an additional unit and forbid
redefining a standard one, so plain ``ft`` stays the international foot and the US survey
foot is the project-defined ``ftUS`` (the EPSG abbreviation), described in the UNIT group.
Values are never converted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydiggs import ags4, convert
from pydiggs.convert._mapping import load_mapping
from pydiggs.findings.model import Severity

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ags4"
EPSG = "http://www.opengis.net/def/crs/EPSG/0/"
POINT = (
    '<PointLocation gml:id="BH01.referencePoint" srsName="' + EPSG + '27700" srsDimension="3">\n'
    "          <gml:pos>451200 206500 12.5</gml:pos>"
)
DESCRIPTION = "US survey foot (1200/3937 m)"


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


def in_us_feet_cone(xml: str) -> str:
    i = xml.index('<Sounding gml:id="CPT01">')
    return xml[:i] + xml[i:].replace("<units>m</units>", "<units>ft[US]</units>", 1)


def at(xml: str, code: str, dim: int, pos: str) -> str:
    assert POINT in xml
    return xml.replace(
        POINT,
        f'<PointLocation gml:id="BH01.referencePoint" srsName="{EPSG}{code}"'
        f' srsDimension="{dim}">\n          <gml:pos>{pos}</gml:pos>',
    )


def unit_rows(file: ags4.File) -> dict[str, str]:
    group = file.groups["UNIT"]
    return dict(zip(group.column("UNIT_UNIT"), group.column("UNIT_DESC"), strict=True))


def column(file: ags4.File, group: str, heading: str) -> tuple[str, ...]:
    return file.groups[group].column(heading)


def unit(file: ags4.File, group: str, heading: str) -> str:
    g = file.groups[group]
    return g.units[g.headings.index(heading)]


def test_the_table_maps_ftus_to_the_us_survey_foot_and_ft_to_the_international_foot():
    units = load_mapping().units
    assert units["ftUS"] == "ft[US]"
    assert units["ft"] == "ft"


@pytest.mark.parametrize("version", ["4.1.1", "4.2"])
def test_a_cone_in_us_feet_defines_ftus_in_the_unit_group(tier_b: str, version: str):
    c = convert.diggs_to_ags4(in_us_feet_cone(tier_b).encode(), ags_version=version)
    assert c.ags4 is not None
    assert "ftUS" in unit_rows(c.ags4)
    assert unit_rows(c.ags4)["ftUS"] == DESCRIPTION
    # Rule 15: every unit used is defined in UNIT, so the file passes the AGS4 check
    assert c.validation is not None
    assert [f.message for f in c.validation.findings if f.severity is Severity.ERROR] == []


def test_a_standard_unit_gets_no_description_from_the_table(tier_b: str):
    c = convert.diggs_to_ags4(in_us_feet_cone(tier_b).encode(), ags_version="4.1.1")
    assert c.ags4 is not None
    rows = unit_rows(c.ags4)
    assert rows["m"] == "" or rows["m"] != DESCRIPTION
    assert "ft" not in rows  # the international foot is not used by this file


@pytest.mark.parametrize(
    ("uom", "ags_unit"), [("ft[US]", "ftUS"), ("ft", "ft")], ids=["us-survey-foot", "international"]
)
def test_depths_in_feet_are_written_to_ags4_but_never_converted_to_metres_on_the_way_back(
    tier_b: str, uom: str, ags_unit: str
):
    # DIGGS to AGS4 keeps the foot of the source. AGS4 to DIGGS writes depths along a feature
    # in metres only, so a depth in any foot is reported and left out rather than converted.
    i = tier_b.index('<Sounding gml:id="CPT01">')
    xml = tier_b[:i] + tier_b[i:].replace("<units>m</units>", f"<units>{uom}</units>", 1)
    first = convert.diggs_to_ags4(xml.encode(), ags_version="4.1.1")
    assert first.ags4 is not None
    assert unit(first.ags4, "SCPT", "SCPT_DPTH") == ags_unit
    assert column(first.ags4, "SCPT", "SCPT_DPTH") == ("0.5", "1", "1.5")  # never converted

    back = convert.ags4_to_diggs(first.ags4)
    lost = [f.message for f in back.losses if f.rule_id == "convert.unmapped-unit"]
    assert lost and "depths in a unit other than m" in lost[0]
    assert back.xml is not None
    assert "<gml:pos>0.5" not in back.xml.decode()  # no depth was written as if it were metres


@pytest.mark.parametrize("code", ["2263", "6539"], ids=["epsg-2263", "epsg-6539"])
def test_a_us_foot_crs_writes_coordinates_in_ftus_with_a_unit_row(tier_a: str, code: str):
    c = convert.diggs_to_ags4(at(tier_a, code, 2, "987000 200000").encode())
    assert c.ags4 is not None
    assert unit(c.ags4, "LOCA", "LOCA_NATE") == "ftUS"
    assert unit(c.ags4, "LOCA", "LOCA_NATN") == "ftUS"
    assert "987000" in column(c.ags4, "LOCA", "LOCA_NATE")
    assert "200000" in column(c.ags4, "LOCA", "LOCA_NATN")
    assert unit_rows(c.ags4)["ftUS"] == DESCRIPTION
    # the other features are in metres, which a column in ftUS cannot hold: named, not mixed
    lost = [f.message for f in c.losses if f.rule_id == "convert.unmapped-unit"]
    assert lost and all("another unit" in m for m in lost)


def test_a_us_foot_location_round_trips_without_converting_values(tier_a: str):
    first = convert.diggs_to_ags4(at(tier_a, "2263", 2, "987000 200000").encode())
    assert first.ags4 is not None
    back = convert.ags4_to_diggs(first.ags4)
    assert back.xml is not None
    xml = back.xml.decode()
    assert "987000 200000" in xml
    assert "EPSG/0/2263" in xml
    again = convert.model_to_ags4(back.model)
    assert again.ags4 is not None
    assert unit(again.ags4, "LOCA", "LOCA_NATE") == "ftUS"
    assert "987000" in column(again.ags4, "LOCA", "LOCA_NATE")
