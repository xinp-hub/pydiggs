"""A declared ``nullValue`` in the conformance suite: every class reads it as an empty cell.

Suite 1.0-beta.2 read it as a value in the classes pyDIGGS 1.17 released (spt, cpt,
lab-index, deep-foundations). Suite 1.0-beta.3 reads it as empty everywhere, and a
challenge of suite 1.0-beta.2 is still graded as that suite graded it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from lxml import etree

from pydiggs import conformance
from pydiggs.conformance import _challenge, _registry
from pydiggs.errors import DocumentError

DIGGS = "http://diggsml.org/schemas/3"
NS = {"d": DIGGS}
MARK = "-999"

#: Class family -> the properties its challenge leaves empty somewhere.
ADDED_LATER = {
    "dissipation": ("pore_pressure_u2", "t50"),
    "in-situ-strength": ("shear_strength_undrained_residual", "c_pressure"),
    "monitoring": ("temperature",),
    "mwd": ("fluid_injection_pressure",),
}
#: The families released with suite 1.0-beta.2, which read the mark as a value then.
RELEASED = {
    "spt": ("n_value",),
    "cpt": ("sleeve_friction",),
    "lab-index": ("liquid_limit",),
    "deep-foundations": ("stroke",),
}
ALL = {**ADDED_LATER, **RELEASED}
#: The SPT and CPT challenges leave no cell empty, so the tables have nothing to mark.
#: Their files get a second column of the property, empty: a second reading that was not
#: taken. Unmarked and undeclared, a number there is a second value where the source has
#: one, which must agree with it (``conformance.value-differs``); in the other families
#: it is a value where the source has none (``conformance.value-invented``).
SECOND_COLUMN = {"spt": "n_value", "cpt": "sleeve_friction"}
FIXTURES = Path(__file__).parent / "fixtures" / "conformance" / "suite-1.0-beta.2"
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


def _with_a_second_column(xml: bytes, key: str) -> bytes:
    """``xml`` with a second column of ``key`` in its first result table, every cell empty."""
    root = etree.fromstring(xml)
    for result in root.iter(f"{{{DIGGS}}}ResultSet"):
        declared = result.findall(".//d:Property", NS)
        first = next(
            (p for p in declared if p.findtext("d:propertyClass", namespaces=NS) == key), None
        )
        values = result.find("d:dataValues", NS)
        if first is None or values is None or not values.text:
            continue
        added = copy.deepcopy(first)
        added.set("index", str(len(declared) + 1))
        name = added.find("d:propertyName", NS)
        assert name is not None
        name.text = f"{name.text}_2"
        declared[-1].addnext(added)  # after the last column: its cell ends each row
        values.text = " ".join(row + "," for row in values.text.split())
        return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
    raise AssertionError(f"no result table with {key}")


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


def _case(
    family: str, *, declared: bool, suite: str = conformance.SUITE_VERSION
) -> tuple[conformance.Challenge, bytes, int]:
    """A challenge of ``family``, and its reference with every empty cell written as MARK."""
    seed = SEEDS.get(family, "demo")
    info = conformance.class_info(f"{family}-export")
    challenge = _challenge._make(info, "Demo", "1", seed, suite)
    xml = challenge.reference()
    if family == "lab-index":
        xml = _with_an_empty_liquid_limit(xml)
    if family in SECOND_COLUMN:
        xml = _with_a_second_column(xml, SECOND_COLUMN[family])
    marked, written = _marked(xml, ALL[family], declared=declared)
    assert written >= len(ALL[family])  # every property named has an empty cell
    return challenge, marked, written


@pytest.mark.parametrize("family", sorted(ALL))
def test_an_empty_cell_written_as_the_declared_null_value_passes(family: str) -> None:
    # Found in review: only the MWD reader skipped a column's nullValue, so a correct
    # dissipation or vane file that marks its empty cells failed as inventing values.
    # Since suite 1.0-beta.3 the classes released before it read the mark as well.
    challenge, xml, _written = _case(family, declared=True)
    assert not _fidelity(challenge, xml)
    assert conformance.grade(challenge, xml).passed


@pytest.mark.parametrize("family", sorted(ALL))
def test_the_same_number_with_no_null_value_declared_still_fails(family: str) -> None:
    challenge, xml, written = _case(family, declared=False)
    items = _fidelity(challenge, xml)
    assert len(items) == written
    wrong = "value-differs" if family in SECOND_COLUMN else "value-invented"
    assert {item.check for item in items} == {f"conformance.{wrong}"}
    assert not conformance.grade(challenge, xml).passed


def test_suite_1_0_beta_3_is_the_suite_that_reads_the_mark_everywhere() -> None:
    # The published rule: a file is graded the same way for as long as the suite version
    # is the same (docs/conformance.md). Reading the mark in the classes pyDIGGS 1.17
    # released changed how a file is graded, so it came with a new suite version, and a
    # new suite version draws new data: the token of a seed is no longer 1.0-beta.2's.
    assert conformance.SUITE_VERSION == "1.0-beta.3"
    assert conformance.GRADED_SUITES == ("1.0-beta.2", "1.0-beta.3")
    info = conformance.class_info("spt-export")
    assert _challenge._token(info, "demo") != _challenge._token(info, "demo", "1.0-beta.2")
    for family in RELEASED:
        challenge, xml, _written = _case(family, declared=True)
        result = conformance.grade(challenge, xml)
        assert result.passed and result.to_dict()["suite_version"] == "1.0-beta.3"


# --- a challenge of suite 1.0-beta.2 is still graded as that suite graded it ------------


def _fixture(name: str) -> dict[str, object]:
    loaded = json.loads((FIXTURES / name).read_text("utf-8"))
    assert loaded["suite_version"] == "1.0-beta.2" and loaded["tool"]["version"] == "1.17.0"
    return dict(loaded)


def _reproduced(evidence: dict[str, object], challenge: conformance.Challenge, data: bytes) -> None:
    """Grading again gives ``evidence`` back: verdict, stages, counts, expected and got.

    The fixtures were written by pyDIGGS as it was while suite 1.0-beta.2 was its own
    (it reports version 1.17.0). The digest of the submission is left out of the
    comparison here, and only here: these
    submissions are files pyDIGGS writes, and a later release may write the same data
    with other bytes. Everything graded is compared.
    """
    outcome = conformance.verify(evidence, challenge, data)
    assert set(outcome.differences) <= {"submission"}, outcome.differences
    assert outcome.result.suite_version == "1.0-beta.2"
    assert outcome.result.challenge_id == evidence["challenge"]["id"]  # type: ignore[index]


@pytest.mark.parametrize("family", sorted(RELEASED))
def test_evidence_of_suite_1_0_beta_2_is_still_reproduced(family: str) -> None:
    evidence = _fixture(f"{family}-export.evidence.json")
    info = conformance.class_info(f"{family}-export")
    challenge = _challenge._make(info, "Demo", "1", "demo", "1.0-beta.2")
    assert challenge.manifest["suite_version"] == challenge.suite_version == "1.0-beta.2"
    _reproduced(evidence, challenge, challenge.reference())
    assert conformance.verify(evidence, challenge, challenge.reference()).ok


def test_import_evidence_of_suite_1_0_beta_2_is_still_reproduced(tmp_path: Path) -> None:
    evidence = _fixture("spt-import.evidence.json")
    info = conformance.class_info("spt-import")
    folder = _challenge._make(info, "Demo", "1", "demo", "1.0-beta.2").write(tmp_path / "c")
    challenge = conformance.load(folder)  # a folder a 1.0-beta.2 release made still loads
    assert challenge.suite_version == "1.0-beta.2"
    asked = _registry.questions(challenge.dataset(), challenge.stream)
    answers = "question_id,answer\n" + "".join(f'{q.id},"{q.expected}"\n' for q in asked)
    assert conformance.verify(evidence, folder, answers.encode()).ok


@pytest.mark.parametrize("family", ["lab-index", "deep-foundations"])
def test_a_challenge_of_suite_1_0_beta_2_still_reads_the_mark_as_a_value(family: str) -> None:
    # The same file, the same challenge, the same verdict: suite 1.0-beta.2 read a
    # nullValue-marked cell as a value in these classes, and its challenges still do.
    challenge, xml, written = _case(family, declared=True, suite="1.0-beta.2")
    items = _fidelity(challenge, xml)
    assert len(items) == written
    assert {item.check for item in items} == {"conformance.value-invented"}
    _reproduced(_fixture(f"{family}-export.null-marked.evidence.json"), challenge, xml)


def _zero_declared_null(xml: bytes) -> tuple[bytes, int]:
    """``xml`` with every blow count column declaring 0 as its ``nullValue``.

    Returns the file and how many blow counts of 0 (weight-of-hammer tests) it holds.
    """
    root = etree.fromstring(xml)
    zeros = 0
    for result in root.iter(f"{{{DIGGS}}}ResultSet"):
        values = result.find("d:dataValues", NS)
        for prop in result.iterfind(".//d:Property", NS):
            if prop.findtext("d:propertyClass", namespaces=NS) == "n_value":
                etree.SubElement(prop, _q("nullValue")).text = "0"
                zeros += values is not None and values.text == "0"
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8")), zeros


def test_a_value_the_file_itself_declares_null_is_missing_since_1_0_beta_3() -> None:
    # The one kind of file that passed suite 1.0-beta.2 and fails 1.0-beta.3: it declares
    # as nullValue a number it also reports as a value. DIGGS says such a cell is missing,
    # so the weight-of-hammer blow counts of 0 are no longer in the file.
    info = conformance.class_info("spt-export")
    for suite, missing in (("1.0-beta.2", False), ("1.0-beta.3", True)):
        challenge = _challenge._make(info, "Demo", "1", "demo", suite)
        xml, zeros = _zero_declared_null(challenge.reference())
        assert zeros >= 1
        items = _fidelity(challenge, xml)
        assert len(items) == (zeros if missing else 0)
        assert {item.check for item in items} <= {"conformance.fact-missing"}


def test_a_suite_this_release_does_not_grade_is_refused(tmp_path: Path) -> None:
    info = conformance.class_info("spt-export")
    folder = _challenge._make(info, "Demo", "1", "demo", "1.0-beta.2").write(tmp_path / "c")
    manifest = json.loads((folder / "challenge.json").read_text("utf-8"))
    manifest["suite_version"] = "1.0-beta.1"
    (folder / "challenge.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DocumentError) as caught:
        conformance.load(folder)
    assert caught.value.kind == "suite-version"
    assert "1.0-beta.2 and 1.0-beta.3" in str(caught.value)


@pytest.mark.parametrize("family", sorted(ALL))
def test_accept_still_accepts_a_file_that_marks_its_empty_cells(family: str) -> None:
    _challenge, xml, _written = _case(family, declared=True)
    result = conformance.accept(xml, family)
    assert result.accepted, result.to_text()
