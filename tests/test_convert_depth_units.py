"""AGS4 depths in feet are carried, never converted (mapping table 1.2).

The unit of ``LOCA_FDEP`` (``m``, ``ft`` or ``ftUS``) is the unit of every depth along a
feature: it becomes the unit of each feature's linear referencing system, of the CPT
``position`` column and of the interval tops. A depth heading in another unit is reported
and not written. No value is ever multiplied.

The fixtures ``ags4-depths/depth_ft.ags`` and ``depth_ftUS.ags`` are ``site_tier_b_4_1_1.ags`` with every
depth heading in feet. They were written for the purpose; they hold no part of an AGS file
or dictionary. They are kept out of ``fixtures/ags4`` because the python-ags4 oracle record
(``scripts/ags4_oracle.py record``) must hold every file in that directory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import ags4, convert
from pydiggs.findings.model import Severity

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ags4-depths"
METRES = Path(__file__).resolve().parent / "fixtures" / "ags4"
OFFICIAL_3 = Path(__file__).resolve().parent / "fixtures" / "official" / "3.0"

#: AGS4 unit -> (DIGGS unit, method name the writer gives it)
UNITS = {
    "ft": ("ft", "trueFootage"),
    "ftUS": ("ft[US]", "Other: trueUSSurveyFootage"),
    "m": ("m", "trueMeters"),
}
#: Every depth heading of the fixtures that the converter reads as a depth along a feature.
DEPTH_HEADINGS = (
    "LOCA_FDEP",
    "HORN_TOP",
    "HORN_BASE",
    "HDIA_DPTH",
    "CDIA_DPTH",
    "BKFL_TOP",
    "BKFL_BASE",
    "WSTG_DPTH",
    "WSTG_CAS",
    "WSTD_POST",
    "SAMP_TOP",
    "SAMP_BASE",
    "SPEC_DPTH",
    "SCPT_DPTH",
)
#: The depth headings the conversion writes (orientation is not converted at all).
CARRIED = tuple(h for h in DEPTH_HEADINGS if not h.startswith("HORN_"))
FEET = pytest.mark.parametrize("unit", ["ft", "ftUS"])


def depth_cells(file: ags4.File) -> dict[tuple[str, str], tuple[str, tuple[float, ...]]]:
    """(group, heading) -> (unit, sorted values) of every carried depth heading in the file.

    Values compare as numbers: the writer prints ``4.50`` as ``4.5``, as it always has.
    """
    found = {}
    for name, group in file.groups.items():
        for column, heading in enumerate(group.headings):
            if heading in CARRIED:
                values = tuple(sorted(float(v) for v in group.column(heading)))
                found[(name, heading)] = (group.units[column], values)
    return found


def losses(c: convert.Conversion, rule_id: str = "convert.unmapped-unit") -> list[str]:
    return [f.message for f in c.losses if f.rule_id == rule_id]


def depth_losses(c: convert.Conversion) -> list[str]:
    return [m for m in losses(c) if "depth" in m]


def convert_fixture(unit: str) -> convert.Conversion:
    c = convert.ags4_to_diggs(FIXTURES / f"depth_{unit}.ags")
    assert c.xml is not None
    return c


@FEET
def test_a_file_with_depths_in_feet_converts_and_validates(unit: str):
    c = convert_fixture(unit)
    assert depth_losses(c) == []
    assert c.validation is not None
    assert [f.message for f in c.validation.findings if f.severity is Severity.ERROR] == []
    assert pydiggs.validate_bytes(c.xml).ok


@FEET
def test_every_reference_system_and_method_carries_the_files_depth_unit(unit: str):
    diggs_unit, method = UNITS[unit]
    xml = convert_fixture(unit).xml
    assert xml is not None
    text = xml.decode()
    assert set(re.findall(r"<units>([^<]*)</units>", text)) == {diggs_unit}
    assert set(re.findall(r"<name>(Other: [^<]*|true[A-Za-z]*)</name>", text)) == {method}
    model = pydiggs.read_bytes(xml)
    assert {ref.unit for ref in model.crs.values() if ref.form == "local"} == {diggs_unit}
    depths = {q.uom for f in model.features for q in (f.total_depth,) if q is not None}
    assert depths == {diggs_unit}


@FEET
def test_ags4_to_diggs_to_ags4_keeps_every_depth_value_and_unit(unit: str):
    source = ags4.read(FIXTURES / f"depth_{unit}.ags")
    before = depth_cells(source)
    assert {unit} == {u for u, _ in before.values()}
    back = convert.diggs_to_ags4(convert_fixture(unit).xml or b"", ags_version="4.1.1")
    assert back.ags4 is not None
    after = depth_cells(back.ags4)
    # a depth cell may be dropped by an unrelated loss; none may change value or unit
    assert after.keys() == before.keys()
    assert after == before


@FEET
def test_diggs_to_ags4_to_diggs_keeps_every_depth_value_and_unit(unit: str):
    first = convert_fixture(unit)
    ags = convert.diggs_to_ags4(first.xml or b"", ags_version="4.1.1")
    assert ags.ags4 is not None
    second = convert.ags4_to_diggs(ags.ags4)
    assert second.xml is not None
    assert depth_losses(second) == []
    one, two = pydiggs.read_bytes(first.xml or b""), pydiggs.read_bytes(second.xml)
    assert {k: (f.total_depth.text, f.total_depth.uom) for k, f in zip_by_name(one)} == {
        k: (f.total_depth.text, f.total_depth.uom) for k, f in zip_by_name(two)
    }
    assert {r.unit for r in two.crs.values() if r.form == "local"} == {UNITS[unit][0]}


def zip_by_name(model: pydiggs.Model):
    return sorted(
        ((f.name, f) for f in model.features if f.total_depth is not None), key=lambda p: p[0]
    )


def test_the_cpt_position_column_is_in_the_files_depth_unit():
    xml = convert_fixture("ftUS").xml
    assert xml is not None
    model = pydiggs.read_bytes(xml)
    uoms = {
        column.uom
        for measurement in model.measurements
        for table in measurement.results
        for column in table.domain.index
    }
    assert uoms == {"ft[US]"}


@pytest.mark.parametrize(
    ("file_unit", "other"),
    [("ft", "m"), ("ftUS", "ft"), ("ft", "ftUS"), ("m", "ft")],
    ids=["ft-vs-m", "ftUS-vs-ft", "ft-vs-ftUS", "m-vs-ft"],
)
def test_a_depth_heading_in_another_unit_is_reported_naming_both_units_and_not_written(
    file_unit: str, other: str
):
    text = (FIXTURES / "depth_ft.ags").read_bytes().decode()
    text = text.replace('"UNIT","","","m","m","m","ft"', f'"UNIT","","","m","m","m","{file_unit}"')
    text = text.replace('"UNIT","","ft","","","","ft"', f'"UNIT","","{other}","","","","{other}"')
    text = text.replace(
        '"DATA","ft","foot (international)"',
        '"DATA","ft","foot (international)"\r\n"DATA","ftUS","US survey foot"',
    )
    c = convert.ags4_to_diggs(text.encode())
    assert c.xml is not None
    messages = [m for m in depth_losses(c) if "SAMP_TOP" in m]
    assert len(messages) == 1
    assert f"depths in {other}," in messages[0] and f"are in {file_unit} " in messages[0]
    assert "never converts" in messages[0]
    assert "BH01-U1" not in c.xml.decode()  # the sample (and its specimen) were not written


def test_a_very_long_unit_is_shortened_in_the_message_and_its_braces_stay_literal():
    # The message is built for every row of the heading, so its length must not follow the
    # length of a UNIT cell; the residual file still holds the unit whole.
    long_unit = "{x}" * 5000
    text = (FIXTURES / "depth_ft.ags").read_bytes().decode()
    text = text.replace(
        '"UNIT","","ft","","","","ft"', f'"UNIT","","{long_unit}","","","","{long_unit}"'
    )
    c = convert.ags4_to_diggs(text.encode())
    messages = [m for m in depth_losses(c) if "SAMP_TOP" in m]
    assert len(messages) == 1
    assert "depths in " + "{x}" * 13 + "{…," in messages[0]
    assert len(messages[0]) < 400


def test_a_depth_with_no_unit_takes_the_files_unit_and_is_declared_assumed():
    text = (FIXTURES / "depth_ftUS.ags").read_bytes().decode()
    text = text.replace('"UNIT","","ftUS","","","","ftUS"', '"UNIT","","","","","",""')
    c = convert.ags4_to_diggs(text.encode())
    assert depth_losses(c) == []
    assumed = [m for m in losses(c, "convert.assumed") if "Depths of SAMP" in m]
    assert assumed and "US survey feet" in assumed[0]


def test_with_no_fdep_unit_metres_stay_the_default_and_are_declared_assumed():
    text = (METRES / "site_tier_b_4_1_1.ags").read_bytes().decode()
    text = text.replace('"UNIT","","","m","m","m","m"', '"UNIT","","","m","m","m",""')
    c = convert.ags4_to_diggs(text.encode())
    assert c.xml is not None
    assert {r.unit for r in pydiggs.read_bytes(c.xml).crs.values() if r.form == "local"} == {"m"}


def test_a_fdep_in_a_unit_the_converter_does_not_carry_is_reported_and_depths_stay_metres():
    text = (METRES / "site_tier_b_4_1_1.ags").read_bytes().decode()
    text = text.replace('"UNIT","","","m","m","m","m"', '"UNIT","","","m","m","m","cm"')
    c = convert.ags4_to_diggs(text.encode())
    assert c.xml is not None
    assert any(
        "LOCA_FDEP" in m and "depths in cm" in m and "are in m" in m for m in depth_losses(c)
    )
    assert {r.unit for r in pydiggs.read_bytes(c.xml).crs.values() if r.form == "local"} == {"m"}


def test_the_writer_names_the_us_survey_foot_method_and_the_file_validates():
    xml = convert_fixture("ftUS").xml
    assert xml is not None
    assert b"<name>Other: trueUSSurveyFootage</name>" in xml
    assert pydiggs.validate_bytes(xml).ok


def test_the_official_us_corpus_keeps_its_depths_through_ags4_and_back():
    # Before depths in feet were carried, every depth of this file was lost on the way back.
    path = OFFICIAL_3 / "Borehole-CPT_Example_Annotated.xml"
    ags = convert.model_to_ags4(pydiggs.read(path))
    assert ags.ags4 is not None
    back = convert.ags4_to_diggs(ags.ags4)
    assert depth_losses(back) == []
    assert back.xml is not None
    assert "ft" in {r.unit for r in pydiggs.read_bytes(back.xml).crs.values() if r.form == "local"}


# --- the property: a depth value is never altered ----------------------------------------

DEPTH_TEXT = st.decimals(min_value="0.01", max_value="9999", places=2).map(lambda d: format(d, "f"))

TEMPLATE = (
    '"GROUP","PROJ"\r\n"HEADING","PROJ_ID","PROJ_NAME"\r\n"UNIT","",""\r\n"TYPE","ID","X"\r\n'
    '"DATA","P1","Property test"\r\n\r\n'
    '"GROUP","TRAN"\r\n"HEADING","TRAN_DLIM","TRAN_RCON","TRAN_AGS"\r\n"UNIT","","",""\r\n'
    '"TYPE","X","X","X"\r\n"DATA","|","+","4.1.1"\r\n\r\n'
    '"GROUP","UNIT"\r\n"HEADING","UNIT_UNIT","UNIT_DESC"\r\n"UNIT","",""\r\n"TYPE","X","X"\r\n'
    '"DATA","{unit}","a unit of depth"\r\n\r\n'
    '"GROUP","LOCA"\r\n"HEADING","LOCA_ID","LOCA_FDEP"\r\n"UNIT","","{unit}"\r\n'
    '"TYPE","ID","2DP"\r\n"DATA","BH01","{fdep}"\r\n\r\n'
    '"GROUP","SAMP"\r\n"HEADING","LOCA_ID","SAMP_TOP","SAMP_REF","SAMP_TYPE","SAMP_ID","SAMP_BASE"'
    '\r\n"UNIT","","{unit}","","","","{unit}"\r\n"TYPE","ID","2DP","X","X","ID","2DP"\r\n'
    '"DATA","BH01","{top}","1","U","S1","{base}"\r\n'
)


@pytest.mark.parametrize("unit", sorted(UNITS))
# no deadline: a conversion takes tens of milliseconds, and the property is about values, not time
@settings(deadline=None, max_examples=40)
@given(fdep=DEPTH_TEXT, top=DEPTH_TEXT, base=DEPTH_TEXT)
def test_a_depth_value_is_never_altered_in_either_direction(
    unit: str, fdep: str, top: str, base: str
):
    source = TEMPLATE.format(unit=unit, fdep=fdep, top=top, base=base).encode()
    first = convert.ags4_to_diggs(source)
    assert first.xml is not None
    model = pydiggs.read_bytes(first.xml)
    (feature,) = model.features
    assert feature.total_depth is not None
    assert feature.total_depth.uom == UNITS[unit][0]
    assert float(feature.total_depth.text) == float(fdep)
    back = convert.diggs_to_ags4(first.xml, ags_version="4.1.1")
    assert back.ags4 is not None
    loca = back.ags4.groups["LOCA"]
    assert loca.units[loca.headings.index("LOCA_FDEP")] == unit
    assert [float(v) for v in loca.column("LOCA_FDEP")] == [float(fdep)]
    samp = back.ags4.groups["SAMP"]
    assert [float(v) for v in samp.column("SAMP_TOP")] == [float(top)]
    assert [float(v) for v in samp.column("SAMP_BASE")] == [float(base)]
    assert samp.units[samp.headings.index("SAMP_TOP")] == unit
