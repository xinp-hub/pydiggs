"""The pressuremeter scenario: what each test reports, and its unload-reload loops."""

from __future__ import annotations

import copy
import json
import tomllib
from collections.abc import Callable
from decimal import Decimal
from importlib import resources
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import conformance, tables
from pydiggs.conformance import _challenge, _facts, _registry
from pydiggs.conformance import _pressuremeter as pmt
from pydiggs.conformance._requirements import defined_properties
from pydiggs.conformance._scenario import Dataset

SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)
DIGGS = "http://diggsml.org/schemas/3"
GML = "http://www.opengis.net/gml/3.2"
NS = {"d": DIGGS, "gml": GML}
EXPORT, IMPORT = "pressuremeter-export", "pressuremeter-import"
LOOP_ELEMENTS = ("shearModulus", "meanStrain", "strainRange", "meanPressure", "pressureRange")
Row = dict[str, str]


def _dataset(seed: str = "demo") -> Dataset:
    return pmt.build(f"1.0-beta.3|pressuremeter|{seed}", "ABC123")


def _seed_where(found: Callable[[Dataset], bool]) -> str:
    for n in range(200):
        if found(_dataset(f"s{n}")):
            return f"s{n}"
    raise AssertionError("no such seed")


def _rows(dataset: Dataset, table: str) -> list[Row]:
    found = dataset.table(table)
    return [dict(zip([f.name for f in found.fields], row, strict=True)) for row in found.rows]


def _unit(dataset: Dataset, table: str, column: str) -> str:
    return next(f.unit for f in dataset.table(table).fields if f.name == column)


def _metric(dataset: Dataset) -> bool:
    return _unit(dataset, "pmt_tests", "limit_pressure") == "kPa"


def _outcomes(dataset: Dataset, xml: bytes) -> dict[str, _facts.Outcome]:
    seen = pmt.observe(pydiggs.read_bytes(xml))
    return {f.id: _facts.match(f, seen) for f in pmt.facts(dataset)}


def _failed(outcomes: dict[str, _facts.Outcome]) -> dict[str, _facts.Outcome]:
    return {k: o for k, o in outcomes.items() if o.status != "found"}


def _errors(xml: bytes) -> list[str]:
    report = pydiggs.validate_bytes(xml)
    return [f"{f.rule_id}: {f.message}" for f in report.findings if f.severity.value == "error"]


def _challenge_for(class_id: str, seed: str = "demo") -> conformance.Challenge:
    return conformance.new(class_id, product="Demo", version="1", seed=seed)


def _tree(xml: bytes) -> etree._Element:
    return etree.fromstring(xml)


def _bytes(root: etree._Element) -> bytes:
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))


def _q(local: str) -> str:
    return f"{{{DIGGS}}}{local}"


def _procedures(root: etree._Element) -> list[etree._Element]:
    return list(root.iter(_q(pmt.PROCEDURE)))


def _trials(procedure: etree._Element) -> list[etree._Element]:
    return procedure.findall("d:trial/d:PressuremeterTestTrial", NS)


def _with_loops(root: etree._Element, count: int) -> etree._Element:
    """The first ``PressuremeterTest`` of ``root`` that has ``count`` loops."""
    return next(p for p in _procedures(root) if len(_trials(p)) == count)


def _valid_failures(dataset: Dataset, root: etree._Element) -> dict[str, _facts.Outcome]:
    """What a changed file fails, once it is shown to be valid DIGGS still."""
    xml = _bytes(root)
    assert not _errors(xml)
    return _failed(_outcomes(dataset, xml))


# --- the catalogue ----------------------------------------------------------------------


def test_the_pressuremeter_classes_are_in_the_catalogue() -> None:
    ids = [c.id for c in conformance.classes()]
    assert EXPORT in ids and IMPORT in ids and len(ids) == 18
    family = next(f for f in conformance.families() if f.id == "pressuremeter")
    assert family.testable and family.scenario == pmt.SCENARIO
    assert family.elements == (pmt.PROCEDURE,)
    assert family.note is not None and "not graded" in family.note
    assert sum(f.testable for f in conformance.families()) == 9


def test_the_procedure_moved_out_of_the_in_situ_strength_family() -> None:
    # An element is in one family. The family it left keeps its title, because the title
    # of a class is written into its evidence and that evidence must not change.
    family = next(f for f in conformance.families() if f.id == "in-situ-strength")
    assert pmt.PROCEDURE not in family.elements
    assert {"InsituVaneTest", "FlatPlateDilatometerTest"} <= set(family.elements)
    assert family.title == "Vane, pressuremeter, dilatometer and penetrometer tests"
    assert conformance.class_info("in-situ-strength-export").title == f"{family.title}: export"


def test_the_suite_version_did_not_change_with_the_new_classes() -> None:
    # Adding a class grades no file of another class differently, so the suite keeps its
    # version, as it did when the earlier classes joined.
    assert conformance.SUITE_VERSION == "1.0-beta.3"
    assert conformance.GRADED_SUITES == ("1.0-beta.2", "1.0-beta.3")
    assert _challenge_for(EXPORT).manifest["suite_version"] == "1.0-beta.3"


def test_the_results_are_properties_the_dictionary_defines_for_the_procedure() -> None:
    defined = defined_properties("properties.xml", pmt.PROCEDURE)
    assert {key for _, key in pmt.RESULTS} <= defined
    # Everything DIGGS 3.0.0's dictionary defines for a pressuremeter test. It has no
    # creep pressure and nothing for the readings of the pressure-volume curve, which is
    # why the scenario carries neither; a dictionary that gains one fails here.
    assert defined == {
        "coef_earth_pressure",
        "dilation_angle",
        "friction_angle_cv",
        "friction_angle_residual",
        "insitu_horizontal_stress",
        "limit_pressure",
        "menard_modulus",
        "modulus_shear",
        "shear_strength_drained",
        "shear_strength_undrained",
        "shear_strength_undrained_residual",
    }


def test_the_loop_is_what_diggs_3_0_0_gives_a_trial() -> None:
    text = resources.files("pydiggs").joinpath("_writer/layout-3.0.0.toml").read_text("utf-8")
    elements = tomllib.loads(text)["elements"]
    assert {"pressuremeterDiameter", pmt.TRIAL} <= set(elements[pmt.PROCEDURE]["children"])
    assert {"trialNo", *LOOP_ELEMENTS} <= set(elements["PressuremeterTestTrial"]["children"])
    assert [element for _, element in pmt._LOOP_COLUMNS] == list(LOOP_ELEMENTS)


# --- the dataset ------------------------------------------------------------------------


def test_every_dataset_has_its_traps() -> None:
    systems = set()
    for n in range(40):
        dataset = _dataset(f"t{n}")
        tests, loops = _rows(dataset, "pmt_tests"), _rows(dataset, "pmt_loops")
        names = [r["location_id"] for r in _rows(dataset, "locations")]
        assert len(names) in (2, 3) and " " in names[0]
        assert sum(1 for r in tests if not r["limit_pressure"]) == 1
        assert all(r["horizontal_stress"] and r["menard_modulus"] for r in tests)
        per_test: dict[tuple[str, str], list[str]] = {
            (r["location_id"], r["test_depth"]): [] for r in tests
        }
        for loop in loops:
            per_test[(loop["location_id"], loop["test_depth"])].append(loop["loop"])
            assert all(loop[column] for column, _ in pmt._LOOP_COLUMNS)
        counts = [len(found) for found in per_test.values()]
        assert min(counts) == 0 and max(counts) == pmt.LOOPS
        assert all(found == [str(i + 1) for i in range(len(found))] for found in per_test.values())
        for name in names:
            depths = [Decimal(r["test_depth"]) for r in tests if r["location_id"] == name]
            assert 3 <= len(depths) <= 5 and depths == sorted(set(depths))
        units = {f.name: f.unit for f in dataset.table("pmt_tests").fields}
        systems.add((units["test_depth"], units["limit_pressure"], units["menard_modulus"]))
    assert systems == {("m", "kPa", "MPa"), ("ft", "psi", "tonf[US]/ft2")}


def test_the_key_comes_from_the_tables_alone() -> None:
    dataset = _dataset()
    key = pmt.facts(dataset)
    tests, loops = _rows(dataset, "pmt_tests"), _rows(dataset, "pmt_loops")
    fidelity = [f for f in key if f.group == "fidelity"]
    assert {f.procedure for f in fidelity} == {pmt.PROCEDURE}
    # Per test: three results, the probe diameter, and five values for each of two loops.
    assert len(fidelity) == len(tests) * (4 + 5 * pmt.LOOPS)
    values = [f for f in fidelity if f.kind == "value"]
    assert len(values) == len(tests) * 4 - 1 + 5 * len(loops)
    assert {f.kind for f in fidelity} == {"value", "absent"}
    absent = [f for f in fidelity if f.kind == "absent"]
    assert sum(f.key == "limit_pressure" for f in absent) == 1
    assert len({f.id for f in key}) == len(key)
    units = {f.si_unit for f in fidelity}
    assert units == {"Pa", "m", ""}  # pressures and moduli, the probe, the strains


# --- the reference file -----------------------------------------------------------------


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_of_any_seed_is_valid_and_holds_every_fact(seed: str) -> None:
    dataset = _dataset(seed)
    xml = pmt.reference_xml(dataset)
    report = pydiggs.validate_bytes(xml)
    assert report.ok and not _errors(xml)
    # The only warnings: no srsName on the site-grid positions, as for the other scenarios.
    assert {f.rule_id for f in report.findings} <= {"geometry.srs-attributes"}
    assert not _failed(_outcomes(dataset, xml))


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_export_of_any_seed_passes(seed: str) -> None:
    challenge = _challenge_for(EXPORT, seed)
    result = conformance.grade(challenge, challenge.reference())
    assert result.passed, result.to_text()


def test_the_reference_file_is_the_same_for_the_same_dataset() -> None:
    assert pmt.reference_xml(_dataset()) == pmt.reference_xml(_dataset())
    assert pmt.reference_xml(_dataset()) != pmt.reference_xml(_dataset("other"))


def test_the_reference_carries_the_values_in_the_tables_units() -> None:
    for wanted in (True, False):
        dataset = _dataset(_seed_where(lambda d, wanted=wanted: _metric(d) == wanted))
        xml = pmt.reference_xml(dataset).decode()
        test = _rows(dataset, "pmt_tests")[0]
        loop = _rows(dataset, "pmt_loops")[0]
        size, pressure, modulus = ("mm", "kPa", "MPa") if wanted else ("in", "psi", "tonf[US]/ft2")
        assert f'<pressuremeterDiameter uom="{size}">{test["probe_diameter"]}<' in xml
        assert f"<uom>{pressure}</uom>" in xml and f"<uom>{modulus}</uom>" in xml
        assert f'<shearModulus uom="{modulus}">{loop["shear_modulus"]}<' in xml
        assert f'<meanPressure uom="{pressure}">{loop["mean_pressure"]}<' in xml
        assert f"<meanStrain>{loop['mean_strain']}</meanStrain>" in xml
        assert xml.count("<PressuremeterTestTrial ") == len(_rows(dataset, "pmt_loops"))
        for _, key in pmt.RESULTS:
            assert f"properties.xml#{key}" in xml


def test_the_reference_file_is_shaped_as_diggs_3_0_0_shapes_a_pressuremeter_test() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    tests = root.findall("d:measurement/d:Test", NS)
    assert len(tests) == len(_rows(dataset, "pmt_tests"))
    for test in tests:
        assert test.find("d:outcome/d:TestResult/d:location/d:PointLocation", NS) is not None
        procedure = test.find(f"d:procedure/d:{pmt.PROCEDURE}", NS)
        assert procedure is not None
        assert procedure[0].tag == _q("pressuremeterDiameter")
        numbers = [t.findtext("d:trialNo", namespaces=NS) for t in _trials(procedure)]
        assert numbers == [str(i + 1) for i in range(len(numbers))]
    assert not root.findall("d:samplingFeature/d:Sounding", NS)


@pytest.mark.parametrize("defect", _facts.DEFECTS)
def test_every_defect_of_the_suite_can_be_planted_and_is_reported(defect: str) -> None:
    xml = pmt.reference_xml(_dataset())
    rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[defect]
    planted = pydiggs.validate_bytes(_challenge._defective(xml, defect))
    assert any(f.rule_id.startswith(rule) for f in planted.findings)
    assert not any(f.rule_id.startswith(rule) for f in pydiggs.validate_bytes(xml).findings)


# --- grading an export: the reported values ---------------------------------------------


def _limit_cell(root: etree._Element, *, empty: bool) -> etree._Element:
    """The ``dataValues`` of the first test whose limit pressure is, or is not, empty."""
    for values in root.iter(_q("dataValues")):
        if (values.text or "").endswith(",") == empty:
            return values
    raise AssertionError("no such test")


@pytest.mark.parametrize("invented", ["0", "1250"])
def test_a_limit_pressure_the_test_did_not_reach_is_not_invented(invented: str) -> None:
    # A zero, or a value worked out from the rest of the test: either is a value the
    # test report does not state.
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    values = _limit_cell(root, empty=True)
    values.text = f"{values.text}{invented}"
    failed = _valid_failures(dataset, root)
    assert [o.check for o in failed.values()] == ["conformance.value-invented"]
    assert next(iter(failed)).endswith("/limit_pressure")


def test_a_changed_value_differs() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    values = _limit_cell(root, empty=False)
    stress, modulus, limit = (values.text or "").split(",")
    values.text = f"{stress},{Decimal(modulus) + 1},{limit}"
    failed = _valid_failures(dataset, root)
    assert [o.check for o in failed.values()] == ["conformance.value-differs"]
    assert next(iter(failed)).endswith("/menard_modulus")


def test_two_properties_swapped_differ() -> None:
    dataset = _dataset()
    xml = pmt.reference_xml(dataset).decode()
    swapped = (
        xml.replace("#insitu_horizontal_stress", "#PLACEHOLDER")
        .replace("#limit_pressure", "#insitu_horizontal_stress")
        .replace("#PLACEHOLDER", "#limit_pressure")
    )
    failed = _failed(_outcomes(dataset, swapped.encode()))
    assert failed and all(k.endswith(("/horizontal_stress", "/limit_pressure")) for k in failed)
    assert "conformance.value-differs" in {o.check for o in failed.values()}


def test_pressures_and_moduli_in_another_declared_unit_are_found() -> None:
    dataset = _dataset(_seed_where(_metric))
    root = _tree(pmt.reference_xml(dataset))
    # kPa written as MPa and MPa as kPa, numbers and units together: the same values.
    factor = {"kPa": (Decimal("0.001"), "MPa"), "MPa": (Decimal(1000), "kPa")}
    for result in root.iter(_q("ResultSet")):
        uoms = result.findall(".//d:uom", NS)
        values = result.find("d:dataValues", NS)
        assert values is not None and values.text
        cells = values.text.split(",")
        values.text = ",".join(
            str(Decimal(cell) * factor[uom.text or ""][0]) if cell else ""
            for cell, uom in zip(cells, uoms, strict=True)
        )
        for uom in uoms:
            uom.text = factor[uom.text or ""][1]
    for trial in root.iter(_q("PressuremeterTestTrial")):
        for held in trial:
            unit = held.get("uom")
            if unit:
                held.text = str(Decimal(held.text or "") * factor[unit][0])
                held.set("uom", factor[unit][1])
    xml = _bytes(root)
    assert b"<uom>MPa</uom><" not in pmt.reference_xml(dataset).replace(b"\n", b"")
    assert not _errors(xml) and not _failed(_outcomes(dataset, xml))


def test_a_unit_mislabelled_fails() -> None:
    dataset = _dataset(_seed_where(_metric))
    xml = pmt.reference_xml(dataset).replace(b"<uom>MPa</uom>", b"<uom>kPa</uom>")
    assert not _errors(xml)
    failed = _failed(_outcomes(dataset, xml))
    assert len(failed) == len(_rows(dataset, "pmt_tests"))
    assert all(k.endswith("/menard_modulus") for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-differs"}


def test_a_probe_diameter_in_a_unit_of_the_wrong_kind_is_not_read_as_right() -> None:
    dataset = _dataset()
    unit = _unit(dataset, "pmt_tests", "probe_diameter").encode()
    xml = pmt.reference_xml(dataset)
    changed = xml.replace(
        b'<pressuremeterDiameter uom="' + unit, b'<pressuremeterDiameter uom="kPa'
    )
    failed = _failed(_outcomes(dataset, changed))
    assert failed and all(k.endswith("/probe_diameter") for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-differs"}


def test_a_test_at_another_depth_is_missing_there() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    position = root.find(".//d:TestResult/d:location/d:PointLocation/gml:pos", NS)
    assert position is not None
    position.text = str(Decimal(position.text or "") + Decimal("0.25"))
    failed = _valid_failures(dataset, root)
    first = _rows(dataset, "pmt_tests")[0]
    assert failed and {o.check for o in failed.values()} == {"conformance.fact-missing"}
    assert all(f"/{first['location_id']}/{first['test_depth']}/" in k for k in failed)


def test_a_renamed_boring_is_missing() -> None:
    dataset = _dataset()
    name = _rows(dataset, "locations")[0]["location_id"]
    xml = pmt.reference_xml(dataset).replace(
        f"<gml:name>{name}<".encode(), f"<gml:name>{name.replace(' ', '')}<".encode()
    )
    failed = _failed(_outcomes(dataset, xml))
    assert f"core/{name}" in failed
    assert all(f"/{name}" in k for k in failed)


def test_a_test_under_another_procedure_is_not_read_as_a_pressuremeter_test() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    procedure = _with_loops(root, 0)
    replaced = etree.Element(_q("FlatPlateDilatometerTest"))
    replaced.set(f"{{{GML}}}id", procedure.get(f"{{{GML}}}id", ""))
    parent = procedure.getparent()
    assert parent is not None
    parent.replace(procedure, replaced)
    failed = _failed(_outcomes(dataset, _bytes(root)))
    assert failed and {o.check for o in failed.values()} == {"conformance.fact-missing"}
    assert len({k.rsplit("/", 1)[0] for k in failed}) == 1  # the values of that one test


# --- grading an export: the unload-reload loops -----------------------------------------


def _copied_loop(root: etree._Element, number: str) -> etree._Element:
    """A copy of some loop of the file as a ``trial`` numbered ``number``, not yet placed."""
    source = next(root.iter(_q(pmt.TRIAL)))
    added = copy.deepcopy(source)
    trial = added[0]
    trial.set(f"{{{GML}}}id", f"invented-{number}")
    held = trial.find("d:trialNo", NS)
    assert held is not None
    held.text = number
    return added


def test_a_loop_invented_on_a_test_with_none_is_named() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    _with_loops(root, 0).append(_copied_loop(root, "1"))
    failed = _valid_failures(dataset, root)
    assert len(failed) == 5 and all("/loop-1/" in k for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-invented"}


def test_a_second_loop_invented_on_a_test_with_one_is_named() -> None:
    dataset = _dataset(_seed_where(lambda d: _has_count(d, 1)))
    root = _tree(pmt.reference_xml(dataset))
    _with_loops(root, 1).append(_copied_loop(root, "2"))
    failed = _valid_failures(dataset, root)
    assert len(failed) == 5 and all("/loop-2/" in k for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-invented"}


def _has_count(dataset: Dataset, count: int) -> bool:
    found: dict[tuple[str, str], int] = {}
    for loop in _rows(dataset, "pmt_loops"):
        where = (loop["location_id"], loop["test_depth"])
        found[where] = found.get(where, 0) + 1
    return count in found.values()


def test_a_loop_numbered_past_the_scenarios_loops_is_not_caught() -> None:
    # The known limit the docs state: the key holds loops 1 and 2 of every test.
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    _with_loops(root, 0).append(_copied_loop(root, str(pmt.LOOPS + 1)))
    assert not _valid_failures(dataset, root)


def test_a_loop_left_out_is_missing() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    procedure = _with_loops(root, 2)
    procedure.remove(procedure.findall("d:trial", NS)[1])
    failed = _valid_failures(dataset, root)
    assert len(failed) == 5 and all("/loop-2/" in k for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.fact-missing"}


def test_a_loop_with_no_number_names_no_loop() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    trial = _trials(_with_loops(root, 2))[0]
    number = trial.find("d:trialNo", NS)
    assert number is not None
    trial.remove(number)
    failed = _valid_failures(dataset, root)
    assert len(failed) == 5 and all("/loop-1/" in k for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.fact-missing"}


def test_two_loops_with_their_numbers_swapped_differ() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    first, second = (t.find("d:trialNo", NS) for t in _trials(_with_loops(root, 2)))
    assert first is not None and second is not None
    first.text, second.text = "2", "1"
    failed = _valid_failures(dataset, root)
    assert failed and all("/loop-" in k for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-differs"}


def test_two_loops_of_one_number_that_disagree_differ() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    procedure = _with_loops(root, 2)
    second = _trials(procedure)[1].find("d:trialNo", NS)
    assert second is not None
    second.text = "1"
    failed = _valid_failures(dataset, root)
    checks = {k.split("/")[-2]: set() for k in failed}
    for key, outcome in failed.items():
        checks[key.split("/")[-2]].add(outcome.check)
    assert checks == {
        "loop-1": {"conformance.value-differs"},
        "loop-2": {"conformance.fact-missing"},
    }


def test_a_changed_loop_value_differs() -> None:
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    strain = next(root.iter(_q("strainRange")))
    strain.text = str(Decimal(strain.text or "") + Decimal("0.0010"))
    failed = _valid_failures(dataset, root)
    assert [o.check for o in failed.values()] == ["conformance.value-differs"]
    assert next(iter(failed)).endswith("/strain_range")


def test_a_strain_written_as_a_percentage_differs() -> None:
    # DIGGS types a loop's strains as plain numbers: 1.64 is not 0.0164.
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    for strain in root.iter(_q("meanStrain")):
        strain.text = str(Decimal(strain.text or "") * 100)
    failed = _valid_failures(dataset, root)
    assert len(failed) == len(_rows(dataset, "pmt_loops"))
    assert all(k.endswith("/mean_strain") for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-differs"}


@pytest.mark.parametrize(
    ("held", "number"),
    [("1", 1), ("+2", 2), (" 3 ", 3), ("007", 7), ("0", None), ("-1", None), ("1.0", None)],
)
def test_a_loop_number_is_a_positive_integer(held: str, number: int | None) -> None:
    assert pmt._loop_number(held) == number


def test_what_is_no_text_is_no_loop_number() -> None:
    assert pmt._loop_number(None) is None and pmt._loop_number(1) is None
    assert pmt._loop_number("") is None and pmt._loop_number("1" * 10) is None


def test_a_loop_value_that_is_no_number_is_not_read() -> None:
    assert pmt._loop_value("0.0164") == (0.0164, "")
    assert pmt._loop_value("nan") is None and pmt._loop_value("about 2") is None
    assert pmt._loop_value(None) is None


def test_observing_loops_is_bounded_by_the_placing_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # A test's own values are placed at each depth it has a row at; the observation's
    # limit bounds that, so a file with very many loops on a long test is not multiplied
    # out. Past the limit the rest is not read (its facts would show as missing).
    dataset = _dataset()
    root = _tree(pmt.reference_xml(dataset))
    procedure = _with_loops(root, 0)
    for number in range(1, 41):
        procedure.append(_copied_loop(root, str(number)))
    model = pydiggs.read_bytes(_bytes(root))

    def loop_readings() -> int:
        seen = pmt.observe(model)
        return sum(len(v) for (_, _, key), v in seen.readings.items() if key.startswith("trial["))

    in_file = 5 * len(_rows(dataset, "pmt_loops"))
    assert loop_readings() == in_file + 5 * 40
    monkeypatch.setattr(_facts, "MAX_PLACED", 20)
    # The first value of each test is always placed; the limit holds the rest.
    looped = {(r["location_id"], r["test_depth"]) for r in _rows(dataset, "pmt_loops")}
    assert loop_readings() <= 20 + len(looped) + 1


# --- the export challenge ---------------------------------------------------------------


def test_an_export_challenge_is_a_tables_package_that_the_kit_passes(tmp_path: Path) -> None:
    challenge = _challenge_for(EXPORT)
    folder = challenge.write(tmp_path / "challenge")
    descriptor = json.loads((folder / "datapackage.json").read_text())
    names = {resource["name"] for resource in descriptor["resources"]}
    assert {
        "projects",
        "features",
        "measurements",
        "result_columns",
        "components",
        "params",
        "results_PressuremeterTest",
    } <= names
    assert not (folder / "columns.csv").exists() and not (folder / "extra.csv").exists()
    results = (folder / "results_PressuremeterTest.csv").read_text().splitlines()
    assert results[0].split(",")[3:] == [key for _, key in pmt.RESULTS]
    assert sum(line.endswith(",") for line in results) == 1  # the limit pressure not reached
    components = (folder / "components.csv").read_text()
    loops = _rows(challenge.dataset(), "pmt_loops")
    assert components.count(",trial,PressuremeterTestTrial,") == len(loops)
    params = (folder / "params.csv").read_text()
    assert params.count(",trialNo,") == len(loops) and ",pressuremeterDiameter," in params
    readme = (folder / "README.md").read_text()
    assert "`trialNo`" in readme and "not worked out" in readme
    made = tables.to_model(tables.read_package(folder))
    result = conformance.grade(challenge, pydiggs.write_bytes(made.model))
    assert result.passed, result.to_text()


def test_the_readme_of_another_class_is_as_it_was() -> None:
    for class_id in ("in-situ-strength-export", "mwd-export"):
        readme = _challenge_for(class_id).files["README.md"].decode()
        assert "trialNo" not in readme and "PressuremeterTest" not in readme


def test_a_wrong_export_fails_the_class_with_its_checks() -> None:
    challenge = _challenge_for(EXPORT)
    root = _tree(challenge.reference())
    values = _limit_cell(root, empty=True)
    values.text = f"{values.text}0"
    result = conformance.grade(challenge, _bytes(root))
    assert not result.passed
    assert result.stage("validation").status == "pass" and result.stage("core").status == "pass"
    assert [i.check for i in result.stage("fidelity").items] == ["conformance.value-invented"]


# --- the import challenge ---------------------------------------------------------------


def _right_answers(challenge: conformance.Challenge) -> dict[str, str]:
    asked = _registry.questions(challenge.dataset(), challenge.stream)
    return {q.id: q.expected for q in asked}


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_right_answers_pass_and_the_defect_is_reported(seed: str) -> None:
    challenge = _challenge_for(IMPORT, seed)
    assert challenge.files["challenge.xml"] == challenge.reference()
    answers = _right_answers(challenge)
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert result.passed, result.to_text()
    assert result.stage("robustness").status == "pass"
    rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[answers["robustness.defect"]]
    report = pydiggs.validate_bytes(challenge.files["defective.xml"])
    assert any(f.rule_id.startswith(rule) for f in report.findings)


def test_the_questions_are_answered_by_the_tables() -> None:
    for seed in ("demo", "s1", "s2", "s3"):
        dataset = _dataset(seed)
        asked = {q.id: q for q in pmt.questions(dataset, seed)}
        assert list(asked) == [
            "core.project-name",
            "core.locations",
            "core.total-depth",
            "values.tests",
            "values.limit-pressure",
            "values.modulus",
            "values.no-limit-pressure",
            "values.tests-with-loops",
            "values.loop-modulus",
            "robustness.defect",
        ]
        tests, loops = _rows(dataset, "pmt_tests"), _rows(dataset, "pmt_loops")
        assert asked["values.tests"].expected == str(len(tests))
        unreached = next(r for r in tests if not r["limit_pressure"])
        assert Decimal(asked["values.no-limit-pressure"].expected) == Decimal(
            unreached["test_depth"]
        )
        assert unreached["location_id"] in asked["values.no-limit-pressure"].text
        looped = {(r["location_id"], r["test_depth"]) for r in loops}
        assert asked["values.tests-with-loops"].expected == str(len(looped))
        metric = _metric(dataset)
        assert asked["values.limit-pressure"].unit == ("psi" if metric else "kPa")
        assert asked["values.modulus"].unit == ("tonf[US]/ft2" if metric else "MPa")
        assert asked["values.loop-modulus"].unit == asked["values.modulus"].unit
        assert asked["robustness.defect"].expected in _facts.DEFECTS


def test_a_question_asks_for_a_source_value_in_the_other_unit() -> None:
    dataset = _dataset(_seed_where(_metric))
    asked = {q.id: q for q in pmt.questions(dataset, "demo")}
    question = asked["values.limit-pressure"]
    row = next(
        r
        for r in _rows(dataset, "pmt_tests")
        if r["limit_pressure"] and f"at {r['test_depth']} m in {r['location_id']}" in question.text
    )
    psi = 6894.757293168361  # Pa
    assert float(question.expected) == pytest.approx(float(row["limit_pressure"]) * 1000 / psi)
    assert question.tol >= abs(float(question.expected)) * 0.005


@pytest.mark.parametrize(
    "question",
    ["values.tests", "values.limit-pressure", "values.no-limit-pressure", "values.loop-modulus"],
)
def test_a_wrong_import_answer_is_named(question: str) -> None:
    challenge = _challenge_for(IMPORT)
    answers = _right_answers(challenge) | {question: "123456"}
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert not result.passed
    assert [i.id for i in result.stage("values").items] == [question]


# --- acceptance -------------------------------------------------------------------------


def _statuses(result: conformance.Acceptance) -> dict[str, str]:
    return {o.requirement.id: o.status for o in result.outcomes}


def test_the_family_has_the_requirements_of_a_test_family() -> None:
    found = conformance.requirements("pressuremeter")
    assert [r.id for r in found] == [
        "accept.diggs-3",
        "accept.valid",
        "accept.present",
        "accept.located",
        "accept.result",
        "accept.position",
    ]
    present = next(r for r in found if r.id == "accept.present")
    assert pmt.PROCEDURE in present.text


def test_the_reference_file_is_accepted_as_a_pressuremeter_delivery() -> None:
    result = conformance.accept(_challenge_for(EXPORT).reference(), "pressuremeter")
    assert result.accepted
    assert set(_statuses(result).values()) == {"pass"}


def test_a_pressuremeter_file_is_no_longer_an_in_situ_strength_delivery() -> None:
    reference = _challenge_for(EXPORT).reference()
    statuses = _statuses(conformance.accept(reference, "in-situ-strength"))
    assert statuses["accept.valid"] == "pass" and statuses["accept.present"] == "fail"
    vane = _challenge_for("in-situ-strength-export").reference()
    assert _statuses(conformance.accept(vane, "pressuremeter"))["accept.present"] == "fail"
    assert conformance.accept(vane, "in-situ-strength").accepted


def test_a_test_that_reports_only_null_marks_reports_nothing() -> None:
    reference = _challenge_for(EXPORT).reference()
    root = _tree(reference)
    result = next(root.iter(_q("ResultSet")))
    for declared in result.iter(_q("Property")):
        etree.SubElement(declared, _q("nullValue")).text = "-999"
    values = result.find("d:dataValues", NS)
    assert values is not None
    values.text = "-999,-999,-999"
    xml = _bytes(root)
    assert not _errors(xml)
    statuses = _statuses(conformance.accept(xml, "pressuremeter"))
    assert statuses["accept.present"] == "pass" and statuses["accept.result"] == "fail"


def test_docstring_examples_run() -> None:
    import doctest

    failed, attempted = doctest.testmod(pmt)
    assert attempted and not failed
