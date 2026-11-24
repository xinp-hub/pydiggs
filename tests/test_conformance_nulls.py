"""A declared ``nullValue`` in the conformance suite: which classes read it as an empty cell."""

from __future__ import annotations

import pytest
from lxml import etree

from pydiggs import conformance

DIGGS = "http://diggsml.org/schemas/3"
NS = {"d": DIGGS}
MARK = "-999"

#: Class family -> the properties its challenge leaves empty somewhere.
READ_AS_EMPTY = {
    "dissipation": ("pore_pressure_u2", "t50"),
    "in-situ-strength": ("shear_strength_undrained_residual", "c_pressure"),
    "mwd": ("fluid_injection_pressure",),
}
#: The families released with suite 1.0-beta.2, which read the mark as a value.
READ_AS_A_VALUE = {
    "lab-index": ("liquid_limit",),
    "deep-foundations": ("stroke",),
}
ALL = {**READ_AS_EMPTY, **READ_AS_A_VALUE}
#: A seed whose challenge leaves a cell of those properties empty, where "demo" does not.
SEEDS = {"deep-foundations": "other"}
PROPERTIES = "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"


def _with_an_empty_liquid_limit(xml: bytes) -> bytes:
    """``xml`` with a liquid limit column, one empty cell in it, on the non-plastic test.

    pyDIGGS's own laboratory export has no column where a value was not measured, so no
    cell to mark; an exporter that writes one column set for every test has.
    """
    root = etree.fromstring(xml)
    for result in root.iter(f"{{{DIGGS}}}ResultSet"):
        keys = [p.findtext("d:propertyClass", namespaces=NS) for p in result.iter(_q("Property"))]
        values = result.find("d:dataValues", NS)
        if keys != ["non_plastic"] or values is None or values.text != "true":
            continue
        properties = result.find(".//d:properties", NS)
        assert properties is not None
        added = etree.SubElement(properties, _q("Property"), index="2")
        etree.SubElement(added, _q("propertyName")).text = "LLPL_LL"
        etree.SubElement(added, _q("typeData")).text = "integer"
        coded = etree.SubElement(added, _q("propertyClass"))
        coded.set("codeSpace", f"{PROPERTIES}#liquid_limit")
        coded.text = "liquid_limit"
        values.text = "true,"
        return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
    raise AssertionError("no non-plastic test")


def _q(local: str) -> str:
    return f"{{{DIGGS}}}{local}"


def _marked(xml: bytes, keys: tuple[str, ...], *, declared: bool) -> tuple[bytes, int]:
    """``xml`` with every empty cell of the ``keys`` columns written as ``MARK``.

    With ``declared``, each such column says so (``<nullValue>-999</nullValue>``), as
    DIGGS 3.0.0 lets a property do. Returns the file and how many cells were written.
    """
    root = etree.fromstring(xml)
    written = 0
    for result in root.iter(f"{{{DIGGS}}}ResultSet"):
        values = result.find("d:dataValues", NS)
        if values is None or not values.text:
            continue
        rows = [row.split(",") for row in values.text.split()]
        for index, prop in enumerate(result.iterfind(".//d:Property", NS)):
            if prop.findtext("d:propertyClass", namespaces=NS) not in keys:
                continue
            empty = [row for row in rows if index < len(row) and row[index] == ""]
            for row in empty:
                row[index] = MARK
            written += len(empty)
            if empty and declared:
                etree.SubElement(prop, f"{{{DIGGS}}}nullValue").text = MARK
        values.text = " ".join(",".join(row) for row in rows)
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8")), written


def _fidelity(challenge: conformance.Challenge, xml: bytes) -> list[conformance.Item]:
    result = conformance.grade(challenge, xml)
    assert result.stage("validation").status == "pass", result.to_text()
    assert result.stage("core").status == "pass", result.to_text()
    return list(result.stage("fidelity").items)


def _case(family: str, *, declared: bool) -> tuple[conformance.Challenge, bytes, int]:
    """A challenge of ``family``, and its reference with every empty cell written as MARK."""
    seed = SEEDS.get(family, "demo")
    challenge = conformance.new(f"{family}-export", product="Demo", version="1", seed=seed)
    xml = challenge.reference()
    if family == "lab-index":
        xml = _with_an_empty_liquid_limit(xml)
    marked, written = _marked(xml, ALL[family], declared=declared)
    assert written >= len(ALL[family])  # every property named has an empty cell
    return challenge, marked, written


@pytest.mark.parametrize("family", sorted(READ_AS_EMPTY))
def test_an_empty_cell_written_as_the_declared_null_value_passes(family: str) -> None:
    # Found in review: only the MWD reader skipped a column's nullValue, so a correct
    # dissipation or vane file that marks its empty cells failed as inventing values.
    challenge, xml, _written = _case(family, declared=True)
    assert not _fidelity(challenge, xml)
    assert conformance.grade(challenge, xml).passed


@pytest.mark.parametrize("family", sorted(ALL))
def test_the_same_number_with_no_null_value_declared_is_invented(family: str) -> None:
    challenge, xml, written = _case(family, declared=False)
    items = _fidelity(challenge, xml)
    assert len(items) == written
    assert {item.check for item in items} == {"conformance.value-invented"}


@pytest.mark.parametrize("family", sorted(READ_AS_A_VALUE))
def test_the_classes_of_suite_1_0_beta_2_still_read_the_mark_as_a_value(family: str) -> None:
    # pyDIGGS 1.17 released lab-index and deep-foundations under suite 1.0-beta.2 reading
    # a nullValue-marked cell as a value, and a file is graded the same way for as long
    # as the suite version is the same (docs/conformance.md). Reading the mark in these
    # classes is right, and is a change of suite version: this test fails when one is
    # made without the other.
    assert conformance.SUITE_VERSION == "1.0-beta.2"
    challenge, xml, written = _case(family, declared=True)
    items = _fidelity(challenge, xml)
    assert len(items) == written
    assert {item.check for item in items} == {"conformance.value-invented"}


@pytest.mark.parametrize("family", sorted(ALL))
def test_accept_still_accepts_a_file_that_marks_its_empty_cells(family: str) -> None:
    _challenge, xml, _written = _case(family, declared=True)
    result = conformance.accept(xml, family)
    assert result.accepted, result.to_text()
