"""The in-situ test scenarios: pore pressure dissipation, and vane and dilatometer tests."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs import conformance, tables
from pydiggs.conformance import _challenge, _dissipation, _facts, _insitu, _registry
from pydiggs.conformance._scenario import Dataset

SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)
DIGGS = "http://diggsml.org/schemas/3"
GML = "http://www.opengis.net/gml/3.2"
NS = {"d": DIGGS, "gml": GML}
SCENARIOS = {_dissipation.SCENARIO: _dissipation, _insitu.SCENARIO: _insitu}


def _dataset(scenario: str, seed: str = "demo") -> Dataset:
    return SCENARIOS[scenario].build(f"1.0-beta.2|{scenario}|{seed}", "ABC123")


def _seed_where(scenario: str, found: Callable[[Dataset], bool]) -> str:
    for n in range(200):
        if found(_dataset(scenario, f"s{n}")):
            return f"s{n}"
    raise AssertionError(scenario)


def _rows(dataset: Dataset, table: str) -> list[dict[str, str]]:
    found = dataset.table(table)
    return [dict(zip([f.name for f in found.fields], row, strict=True)) for row in found.rows]


def _unit(dataset: Dataset, table: str, column: str) -> str:
    return next(f.unit for f in dataset.table(table).fields if f.name == column)


def _outcomes(dataset: Dataset, xml: bytes) -> dict[str, _facts.Outcome]:
    module = SCENARIOS[dataset.scenario]
    seen = module.observe(pydiggs.read_bytes(xml))
    return {f.id: _facts.match(f, seen) for f in module.facts(dataset)}


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


# --- both scenarios: the reference file -------------------------------------------------


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_dissipation_reference_of_any_seed_is_valid_and_holds_every_fact(seed: str) -> None:
    dataset = _dataset("dissipation", seed)
    xml = _dissipation.reference_xml(dataset)
    report = pydiggs.validate_bytes(xml)
    assert report.ok and not _errors(xml)
    # The only warnings: no srsName on the site-grid positions, as for the other scenarios.
    assert {f.rule_id for f in report.findings} <= {"geometry.srs-attributes"}
    assert not _failed(_outcomes(dataset, xml))


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_in_situ_strength_reference_of_any_seed_is_valid_and_holds_every_fact(
    seed: str,
) -> None:
    dataset = _dataset("in-situ-strength", seed)
    xml = _insitu.reference_xml(dataset)
    report = pydiggs.validate_bytes(xml)
    assert report.ok and not _errors(xml)
    assert {f.rule_id for f in report.findings} <= {"geometry.srs-attributes"}
    assert not _failed(_outcomes(dataset, xml))


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_reference_file_is_the_same_for_the_same_dataset(scenario: str) -> None:
    module = SCENARIOS[scenario]
    assert module.reference_xml(_dataset(scenario)) == module.reference_xml(_dataset(scenario))
    assert module.reference_xml(_dataset(scenario)) != module.reference_xml(
        _dataset(scenario, "other")
    )


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("defect", _facts.DEFECTS)
def test_every_defect_can_be_planted_and_is_reported(scenario: str, defect: str) -> None:
    xml = SCENARIOS[scenario].reference_xml(_dataset(scenario))
    rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[defect]
    planted = pydiggs.validate_bytes(_challenge._defective(xml, defect))
    assert any(f.rule_id.startswith(rule) for f in planted.findings)
    assert not any(f.rule_id.startswith(rule) for f in pydiggs.validate_bytes(xml).findings)


@pytest.mark.parametrize("class_id", ["dissipation-export", "in-situ-strength-export"])
def test_an_export_challenge_is_a_tables_package_that_the_kit_passes(
    class_id: str, tmp_path: Path
) -> None:
    challenge = _challenge_for(class_id)
    folder = challenge.write(tmp_path / "challenge")
    descriptor = json.loads((folder / "datapackage.json").read_text())
    names = {resource["name"] for resource in descriptor["resources"]}
    assert {"projects", "features", "measurements", "result_columns"} <= names
    made = tables.to_model(tables.read_package(folder))
    result = conformance.grade(challenge, pydiggs.write_bytes(made.model))
    assert result.passed, result.to_text()


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_reference_passes_both_classes(scenario: str) -> None:
    export = _challenge_for(f"{scenario}-export")
    assert conformance.grade(export, export.reference()).passed
    imported = _challenge_for(f"{scenario}-import")
    assert imported.files["challenge.xml"] == imported.reference()
    asked = _registry.questions(imported.dataset(), _challenge._stream(imported.info, "demo"))
    answers = json.dumps({q.id: q.expected for q in asked}).encode()
    assert conformance.grade(imported, answers).passed
    wrong = {q.id: q.expected for q in asked} | {asked[3].id: "123456"}
    result = conformance.grade(imported, json.dumps(wrong).encode())
    assert not result.passed
    assert [i.id for i in result.stage("values").items] == [asked[3].id]


# --- dissipation: the dataset -----------------------------------------------------------


def test_the_dissipation_dataset_has_its_traps() -> None:
    units, time_units = set(), set()
    for n in range(40):
        dataset = _dataset("dissipation", f"t{n}")
        tests = _rows(dataset, "dissipation_tests")
        readings = _rows(dataset, "dissipation_readings")
        units.add(_unit(dataset, "dissipation_tests", "test_depth"))
        time_unit = _unit(dataset, "dissipation_readings", "elapsed_time")
        time_units.add(time_unit)
        assert _unit(dataset, "dissipation_tests", "t50") == time_unit
        assert any(" " in name for name in dataset.table("locations").column("location_id"))
        assert 2 <= len(tests) <= 4
        assert sum(not t["t50"] for t in tests) == 1  # one test stopped early
        assert all(t["u0"] and t["u50"] for t in tests)
        empty = [r for r in readings if not r["pore_pressure_u2"]]
        assert len(empty) == 1 and empty[0]["pore_pressure_u1"]  # one logger dropout
        places = {len(r["elapsed_time"].partition(".")[2]) for r in readings}
        assert len(places) == 1  # every time of a series to the same precision
        for test in tests:
            mine = [
                r
                for r in readings
                if (r["location_id"], r["test_depth"]) == (test["location_id"], test["test_depth"])
            ]
            assert mine[0]["elapsed_time"] in ("0", "0.00")
            if test["t50"]:
                # t50 is a reading time of its series, at which u2 has fallen to u50
                reading = next(r for r in mine if r["elapsed_time"] == test["t50"])
                assert Decimal(reading["pore_pressure_u2"]) <= Decimal(test["u50"])
    assert units == {"m", "ft"} and time_units == {"s", "min"}


def _minute_seed() -> str:
    return _seed_where(
        "dissipation", lambda d: _unit(d, "dissipation_readings", "elapsed_time") == "min"
    )


def test_the_dissipation_reference_carries_the_values_in_the_tables_units() -> None:
    dataset = _dataset("dissipation", _minute_seed())
    xml = _dissipation.reference_xml(dataset).decode()
    assert 'unit="minute"' in xml and 'unit="second"' not in xml
    assert "<uom>min</uom>" in xml and "properties.xml#t50" in xml
    assert "&amp; Approach" in xml
    reading = _rows(dataset, "dissipation_readings")[1]
    assert f" {reading['elapsed_time']} " in xml  # carried as written, not in seconds


# --- dissipation: grading an export -----------------------------------------------------


def _only_failure(dataset: Dataset, xml: bytes, check: str) -> str:
    failed = _failed(_outcomes(dataset, xml))
    assert len(failed) == 1, failed
    (fact_id, outcome) = next(iter(failed.items()))
    assert outcome.check == check
    return fact_id


def test_an_invented_u2_reading_is_named() -> None:
    dataset = _dataset("dissipation")
    xml = _dissipation.reference_xml(dataset).decode()
    # The one series row with a u1 and no u2: a single number then a comma.
    changed, count = re.subn(r"(?<=[ >])(\d+\.\d),(?=[ <])", r"\1,0.0", xml)
    assert count == 1
    fact_id = _only_failure(dataset, changed.encode(), "conformance.value-invented")
    assert fact_id.endswith("/pore_pressure_u2")


def test_an_invented_t50_is_named() -> None:
    dataset = _dataset("dissipation")
    xml = _dissipation.reference_xml(dataset).decode()
    changed, count = re.subn(r"(>\d+\.\d,\d+\.\d,)(<)", r"\g<1>60\2", xml)
    assert count == 1
    fact_id = _only_failure(dataset, changed.encode(), "conformance.value-invented")
    assert fact_id.endswith("/t50")


def test_a_changed_reading_differs() -> None:
    dataset = _dataset("dissipation")
    xml = _dissipation.reference_xml(dataset).decode()
    changed = re.sub(
        r'(<dataValues cs="," ts=" ">\d+\.\d,)(\d+)\.',
        lambda m: f"{m.group(1)}{int(m.group(2)) + 7}.",
        xml,
        count=2,  # the first summary's u50, then the first series' first u2
    )
    failed = _failed(_outcomes(dataset, changed.encode()))
    assert {o.check for o in failed.values()} == {"conformance.value-differs"}
    assert len(failed) == 2


def _series_in_seconds(xml: bytes) -> bytes:
    """The file with every series and every t50 in seconds instead of minutes."""
    root = _tree(xml)
    for times in root.iter(f"{{{DIGGS}}}TimeIntervalList"):
        assert times.get("unit") == "minute"
        times.set("unit", "second")
        listed = times.find("d:timeIntervalList", NS)
        assert listed is not None and listed.text
        listed.text = " ".join(str(Decimal(t) * 60) for t in listed.text.split())
    for result in root.iter(f"{{{DIGGS}}}TestResult"):
        values = result.find(".//d:dataValues", NS)
        assert values is not None and values.text
        u0, u50, t50 = values.text.split(",")
        values.text = ",".join((u0, u50, str(Decimal(t50) * 60) if t50 else ""))
        for uom in result.iter(f"{{{DIGGS}}}uom"):
            if uom.text == "min":
                uom.text = "s"
    return _bytes(root)


def test_a_series_in_another_declared_time_unit_is_found() -> None:
    dataset = _dataset("dissipation", _minute_seed())
    xml = _series_in_seconds(_dissipation.reference_xml(dataset))
    assert b'unit="minute"' not in xml and not _errors(xml)
    assert not _failed(_outcomes(dataset, xml))


def test_minutes_written_as_seconds_fail() -> None:
    dataset = _dataset("dissipation", _minute_seed())
    xml = _dissipation.reference_xml(dataset).replace(b'unit="minute"', b'unit="second"')
    failed = _failed(_outcomes(dataset, xml))
    # Every failure is a reading of a series: the readings are now a few seconds apart,
    # so most are missing and those near the start crowd onto the first ones.
    assert failed and all(" min of the test at " in k for k in failed)
    checks = {o.check for o in failed.values()}
    assert checks == {"conformance.fact-missing", "conformance.value-differs"}


def _two_test_seed() -> str:
    def two(dataset: Dataset) -> bool:
        names = dataset.table("dissipation_tests").column("location_id")
        return any(names.count(n) == 2 for n in names)

    return _seed_where("dissipation", two)


def _tests_of(root: etree._Element, sounding: str) -> list[etree._Element]:
    found = []
    for test in root.iter(f"{{{DIGGS}}}Test"):
        ref = test.find("d:samplingFeatureRef", NS)
        if ref is not None and ref.get("{http://www.w3.org/1999/xlink}href") == f"#{sounding}":
            found.append(test)
    return found


def test_series_swapped_between_two_tests_of_a_sounding_are_not_found() -> None:
    dataset = _dataset("dissipation", _two_test_seed())
    root = _tree(_dissipation.reference_xml(dataset))
    names = dataset.table("locations").column("location_id")
    tests = dataset.table("dissipation_tests").column("location_id")
    sounding = f"sounding-{1 + next(i for i, n in enumerate(names) if tests.count(n) == 2)}"
    first, second = (t.find(".//d:dissipationTimeSeries", NS) for t in _tests_of(root, sounding))
    assert first is not None and second is not None
    parent_a, parent_b = first.getparent(), second.getparent()
    assert parent_a is not None and parent_b is not None
    parent_a.replace(first, second)
    parent_b.append(first)
    failed = _failed(_outcomes(dataset, _bytes(root)))
    assert failed and all("of the test at" in k for k in failed)
    assert not any(k.endswith(("/u0", "/u50", "/t50")) for k in failed)


def test_a_test_whose_depth_the_file_does_not_give_is_ranked_last() -> None:
    dataset = _dataset("dissipation", _two_test_seed())
    root = _tree(_dissipation.reference_xml(dataset))
    names = dataset.table("locations").column("location_id")
    tests = dataset.table("dissipation_tests").column("location_id")
    index = next(i for i, n in enumerate(names) if tests.count(n) == 2)
    deeper = _tests_of(root, f"sounding-{1 + index}")[1]
    result = deeper.find(".//d:TestResult", NS)
    location = None if result is None else result.find("d:location", NS)
    assert result is not None and location is not None
    result.remove(location)
    failed = _failed(_outcomes(dataset, _bytes(root)))
    # Its own result has no depth now, so it is missing; its series is still its own.
    assert failed and all(o.check == "conformance.fact-missing" for o in failed.values())
    assert all(k.endswith(("/u0", "/u50", "/t50")) for k in failed)


def test_a_series_whose_time_cannot_be_converted_is_not_read() -> None:
    dataset = _dataset("dissipation")
    xml = _dissipation.reference_xml(dataset)
    model = pydiggs.read_bytes(re.sub(rb'unit="(second|minute)"', b'unit="other:ticks"', xml))
    seen = _dissipation.observe(model)
    assert not any(key.startswith("series ") for _f, _p, key in seen.readings)


def test_questions_of_a_dissipation_challenge_are_answered_by_the_tables() -> None:
    for seed in ("demo", "s1", "s2", "s3"):
        dataset = _dataset("dissipation", seed)
        asked = {q.id: q for q in _dissipation.questions(dataset, seed)}
        tests = _rows(dataset, "dissipation_tests")
        readings = _rows(dataset, "dissipation_readings")
        time_unit = _unit(dataset, "dissipation_readings", "elapsed_time")
        assert asked["values.tests"].expected == str(len(tests))
        assert asked["values.not-reached"].expected == next(
            t["location_id"] for t in tests if not t["t50"]
        )
        t50 = asked["values.t50"]
        assert t50.unit == ("s" if time_unit == "min" else "min")
        factor = 60 if time_unit == "min" else Decimal(1) / 60
        assert any(
            t["t50"] and abs(Decimal(t["t50"]) * factor - Decimal(t50.expected)) < Decimal("1e-6")
            for t in tests
        )
        u2 = asked["values.u2"]
        assert any(Decimal(r["pore_pressure_u2"] or "-1") == Decimal(u2.expected) for r in readings)


# --- in-situ strength: the dataset ------------------------------------------------------


def test_the_in_situ_strength_dataset_has_its_traps() -> None:
    systems = set()
    for n in range(40):
        dataset = _dataset("in-situ-strength", f"t{n}")
        vane = _rows(dataset, "vane_tests")
        dmt = _rows(dataset, "dmt_readings")
        locations = _rows(dataset, "locations")
        systems.add(_unit(dataset, "vane_tests", "peak_strength"))
        assert any(" " in row["location_id"] for row in locations)
        assert [row["kind"] for row in locations][-1] == "sounding"
        missing = [r for r in vane if not r["remoulded_strength"]]
        assert len(missing) == 1 and not missing[0]["remoulded_torque"]
        assert all(r["peak_strength"] and r["peak_torque"] for r in vane)
        assert all(r["remoulded_torque"] for r in vane if r["remoulded_strength"])
        with_c = sum(1 for r in dmt if r["c_reading"])
        assert 1 <= with_c <= 3 < len(dmt)
        assert all(Decimal(r["b_reading"]) > Decimal(r["a_reading"]) for r in dmt)
        for row in locations:  # each hole is deeper than its deepest test
            deepest = max(
                Decimal(r.get("test_depth") or r["depth"])
                for r in (*vane, *dmt)
                if r["location_id"] == row["location_id"]
            )
            assert Decimal(row["total_depth"]) > deepest
    assert systems == {"kPa", "lbf/ft2"}


def _imperial_seed() -> str:
    return _seed_where(
        "in-situ-strength", lambda d: _unit(d, "vane_tests", "peak_strength") == "lbf/ft2"
    )


def test_the_in_situ_reference_carries_the_values_in_the_tables_units() -> None:
    dataset = _dataset("in-situ-strength", _imperial_seed())
    xml = _insitu.reference_xml(dataset).decode()
    vane = _rows(dataset, "vane_tests")[0]
    assert f'<vaneDiameter uom="in">{vane["vane_diameter"]}<' in xml
    assert f'<maximumMeasuredTorque uom="lbf.ft">{vane["peak_torque"]}<' in xml
    assert "<uom>lbf/ft2</uom>" in xml and "<uom>psi</uom>" in xml
    assert "properties.xml#shear_strength_undrained_residual" in xml
    assert xml.count("<remoldedVaneTest>") == len(_rows(dataset, "vane_tests")) - 1


# --- in-situ strength: grading an export ------------------------------------------------


def test_an_invented_remoulded_strength_is_named() -> None:
    dataset = _dataset("in-situ-strength")
    xml = _insitu.reference_xml(dataset).decode()
    changed, count = re.subn(r'(<dataValues cs="," ts=" ">[\d.]+,)(<)', r"\g<1>0\2", xml)
    assert count == 1
    fact_id = _only_failure(dataset, changed.encode(), "conformance.value-invented")
    assert fact_id.endswith("/remoulded_strength")


def test_an_invented_remoulded_torque_is_named() -> None:
    dataset = _dataset("in-situ-strength")
    root = _tree(_insitu.reference_xml(dataset))
    procedure = next(
        p
        for p in root.iter(f"{{{DIGGS}}}InsituVaneTest")
        if p.find("d:remoldedVaneTest", NS) is None
    )
    intact = procedure.find("d:intactVaneTest", NS)
    assert intact is not None
    remolded = etree.Element(f"{{{DIGGS}}}remoldedVaneTest")
    data = etree.SubElement(remolded, f"{{{DIGGS}}}VaneTestMeasurementData")
    data.set(f"{{{GML}}}id", "invented")
    torque = etree.SubElement(data, f"{{{DIGGS}}}maximumMeasuredTorque", uom="N.m")
    torque.text = "0"
    intact.addnext(remolded)
    xml = _bytes(root)
    assert not _errors(xml)
    fact_id = _only_failure(dataset, xml, "conformance.value-invented")
    assert fact_id.endswith("/remoulded_torque")


def test_an_invented_c_reading_is_named() -> None:
    dataset = _dataset("in-situ-strength")
    xml = _insitu.reference_xml(dataset).decode()
    changed = re.sub(r"(?<=[ >])([\d.]+,[\d.]+,)(?=[ <])", r"\g<1>0.0", xml, count=1)
    assert changed != xml
    fact_id = _only_failure(dataset, changed.encode(), "conformance.value-invented")
    assert fact_id.endswith("/c_reading")


def test_peak_and_remoulded_swapped_differ() -> None:
    dataset = _dataset("in-situ-strength")
    xml = _insitu.reference_xml(dataset).decode()
    swapped = (
        xml.replace("#shear_strength_undrained_residual", "#PLACEHOLDER")
        .replace("#shear_strength_undrained", "#shear_strength_undrained_residual")
        .replace("#PLACEHOLDER", "#shear_strength_undrained")
    )
    failed = _failed(_outcomes(dataset, swapped.encode()))
    assert failed and all(k.endswith(("/peak_strength", "/remoulded_strength")) for k in failed)
    assert {o.check for o in failed.values()} == {
        "conformance.value-differs",
        # The test with no remoulded value now has no peak, and an invented remoulded one.
        "conformance.fact-missing",
        "conformance.value-invented",
    }


def test_a_remoulded_strength_under_the_peak_key_contradicts_the_peak() -> None:
    dataset = _dataset("in-situ-strength")
    xml = _insitu.reference_xml(dataset).decode()
    changed = xml.replace("#shear_strength_undrained_residual", "#shear_strength_undrained")
    failed = _failed(_outcomes(dataset, changed.encode()))
    peaks = [k for k in failed if k.endswith("/peak_strength")]
    assert peaks and all(failed[k].check == "conformance.value-differs" for k in peaks)
    assert any(k.endswith("/remoulded_strength") for k in failed)


def test_strengths_in_another_declared_unit_are_found() -> None:
    dataset = _dataset("in-situ-strength", _imperial_seed())
    root = _tree(_insitu.reference_xml(dataset))
    psf = Decimal("47.88025898033584")  # Pa per lbf/ft2
    for result in root.iter(f"{{{DIGGS}}}TestResult"):
        uoms = result.findall(".//d:uom", NS)
        if [u.text for u in uoms] != ["lbf/ft2", "lbf/ft2"]:
            continue
        values = result.find(".//d:dataValues", NS)
        assert values is not None and values.text
        values.text = ",".join(
            f"{Decimal(v) * psf / 1000:.6f}" if v else "" for v in values.text.split(",")
        )
        for uom in uoms:
            uom.text = "kPa"
    xml = _bytes(root)
    assert b"<uom>lbf/ft2</uom>" not in xml and not _errors(xml)
    assert not _failed(_outcomes(dataset, xml))


def test_a_torque_in_a_unit_of_the_wrong_kind_is_not_read_as_right() -> None:
    dataset = _dataset("in-situ-strength")
    xml = _insitu.reference_xml(dataset)
    unit = _unit(dataset, "vane_tests", "peak_torque").encode()
    changed = xml.replace(b'<maximumMeasuredTorque uom="' + unit, b'<maximumMeasuredTorque uom="m')
    failed = _failed(_outcomes(dataset, changed))
    assert failed and all(k.endswith("_torque") for k in failed)
    assert {o.check for o in failed.values()} == {"conformance.value-differs"}
    assert all(o.got and o.got.endswith(" m") for o in failed.values())


def _long_vane_test(dataset: Dataset, rows: int, components: int, *, same: bool = False) -> bytes:
    """The reference with a first vane test of ``rows`` depths and ``components`` peak torques."""
    root = _tree(_insitu.reference_xml(dataset))
    test = root.find(".//d:Test", NS)
    assert test is not None
    point = test.find(".//d:PointLocation", NS)
    values = test.find(".//d:dataValues", NS)
    intact = test.find(f".//d:{_insitu.INTACT}", NS)
    assert point is not None and values is not None and intact is not None
    pos = point.find("gml:pos", NS)
    assert pos is not None
    point.tag, pos.tag = f"{{{DIGGS}}}MultiPointLocation", f"{{{GML}}}posList"
    pos.text = " ".join(f"{i / 100:.2f}" for i in range(1, rows + 1))
    values.text = " ".join("5,2" for _ in range(rows))
    torque = intact.find(".//d:maximumMeasuredTorque", NS)
    assert torque is not None
    for i in range(1, components):
        more = copy.deepcopy(intact)
        found = more.find(".//d:maximumMeasuredTorque", NS)
        assert found is not None
        found.text = torque.text if same else f"{i}.5"
        intact.addnext(more)
    return _bytes(root)


def _peak_torques(dataset: Dataset, xml: bytes) -> int:
    seen = _insitu.observe(pydiggs.read_bytes(xml))
    key = f"{_insitu.INTACT}:@maximumMeasuredTorque"
    return sum(len(found) for where, found in seen.readings.items() if where[2] == key)


def test_observing_a_vane_test_is_linear_in_its_rows_and_torques(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Found in review: every torque of a vane test was placed at every depth of its
    # rows, so n components on n rows made n * n readings from a file of size n.
    dataset = _dataset("in-situ-strength")
    others = _peak_torques(dataset, _insitu.reference_xml(dataset)) - 1
    assert _peak_torques(dataset, _long_vane_test(dataset, 60, 1)) == 60 + others
    # A torque repeated is one torque: a reading per depth, however often it is given.
    assert _peak_torques(dataset, _long_vane_test(dataset, 60, 40, same=True)) == 60 + others
    monkeypatch.setattr(_facts, "MAX_PLACED", 10_000)
    work = [_peak_torques(dataset, _long_vane_test(dataset, n, n)) for n in (300, 600)]
    # Twice the rows and twice the torques: the first torque at every depth and no more
    # readings than the limit after it, not four times as many (90,000 and 360,000).
    assert max(work) <= 600 + 10_000 + others
    assert work[1] <= 2 * work[0]
    assert min(work) >= 9_000  # the limit is used, in whole torques


def test_questions_of_an_in_situ_challenge_are_answered_by_the_tables() -> None:
    for seed in ("demo", "s1", "s2", "s3"):
        dataset = _dataset("in-situ-strength", seed)
        asked = {q.id: q for q in _insitu.questions(dataset, seed)}
        vane = _rows(dataset, "vane_tests")
        dmt = _rows(dataset, "dmt_readings")
        assert asked["values.vane-tests"].expected == str(len(vane))
        assert asked["values.dmt-readings"].expected == str(len(dmt))
        assert asked["values.dmt-c"].expected == str(sum(1 for r in dmt if r["c_reading"]))
        missing = next(r for r in vane if not r["remoulded_strength"])
        assert Decimal(asked["values.no-remoulded"].expected) == Decimal(missing["test_depth"])
        assert missing["location_id"] in asked["values.no-remoulded"].text
        strength = _unit(dataset, "vane_tests", "peak_strength")
        assert asked["values.peak"].unit == ("kPa" if strength == "lbf/ft2" else "lbf/ft2")
        assert asked["values.dmt-b"].unit == "kPa"
        assert asked["robustness.defect"].expected in _facts.DEFECTS


@pytest.mark.parametrize("module", [_dissipation, _insitu], ids=lambda m: m.__name__)
def test_docstring_examples_run(module: ModuleType) -> None:
    import doctest

    failed, attempted = doctest.testmod(module)
    assert attempted and not failed
