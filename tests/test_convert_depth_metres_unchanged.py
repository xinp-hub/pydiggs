"""Metre files convert exactly as they did before depth units were carried (mapping table 1.2).

The goldens were generated at the commit before AGS4 depths in feet were carried. A file
whose ``LOCA_FDEP`` is in metres, or has no unit, must still give the same DIGGS, the same
loss report and the same AGS4 on the way back, byte for byte (the mapping table number in
``TRAN_REM`` is the one thing that differs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydiggs import ags4, convert

HERE = Path(__file__).resolve().parent / "fixtures"
GOLDENS = HERE / "convert-metre-goldens"
NAMES = sorted(p.name.removesuffix(".xml") for p in GOLDENS.glob("*.xml"))


@pytest.mark.parametrize("name", NAMES)
def test_metre_files_convert_byte_identically_to_the_goldens(name: str):
    c = convert.ags4_to_diggs(HERE / "ags4" / f"{name}.ags")
    assert c.xml == (GOLDENS / f"{name}.xml").read_bytes()
    report = "".join(f"{loss.rule_id}|{loss.message}\n" for loss in c.losses)
    assert report == (GOLDENS / f"{name}.report").read_text()


@pytest.mark.parametrize("name", NAMES)
def test_metre_files_come_back_to_the_same_ags4(name: str):
    golden = GOLDENS / f"{name}.4.2.ags"
    if not golden.exists():
        pytest.skip("this fixture has no AGS4 golden (the conversion wrote no DIGGS)")
    xml = (GOLDENS / f"{name}.xml").read_bytes()
    back = convert.diggs_to_ags4(xml, ags_version="4.2")
    assert back.ags4 is not None
    got = ags4.dumps(back.ags4).replace(
        f"mapping table {convert.MAPPING_VERSION}".encode(), b"mapping table {VERSION}"
    )
    assert got == golden.read_bytes()
