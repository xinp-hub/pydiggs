"""The laboratory index tests classes of the conformance suite."""

from __future__ import annotations

import json
import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import conformance
from pydiggs.conformance import _challenge, _facts, _lab, _registry

EXPORT, IMPORT = "lab-index-export", "lab-index-import"
SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)


def _challenge_for(class_id: str, seed: str = "demo") -> conformance.Challenge:
    return conformance.new(class_id, product="Demo Lab", version="2.0", seed=seed)


def _right_answers(challenge: conformance.Challenge) -> dict[str, str]:
    stream = _challenge._stream(challenge.info, challenge.seed)
    return {q.id: q.expected for q in _registry.questions(challenge.dataset(), stream)}


def test_the_lab_classes_are_in_the_catalogue() -> None:
    ids = [c.id for c in conformance.classes()]
    assert EXPORT in ids and IMPORT in ids
    family = next(f for f in conformance.families() if f.id == "lab-index")
    assert family.testable and family.scenario == "lab"


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_file_of_any_seed_passes(seed: str) -> None:
    challenge = _challenge_for(EXPORT, seed)
    result = conformance.grade(challenge, challenge.reference())
    assert result.passed, result.to_text()
    assert all(stage.failed == 0 for stage in result.stages)


@settings(max_examples=40, deadline=None)
@given(seed=SEEDS)
def test_every_dataset_has_its_traps(seed: str) -> None:
    dataset = _registry.build("lab", seed, "TOKEN")
    tests = dataset.table("lab_tests")
    assert tests.column("liquid_limit").count("NP") == 1
    assert "" in tests.column("bulk_density") and any(tests.column("bulk_density"))
    curve = dataset.table("gradation")
    holes = set(dataset.table("locations").column("location_id"))
    assert set(curve.column("location_id")) == holes
    for hole in holes:
        passing = [int(r[4]) for r in curve.rows if r[0] == hole]
        assert passing == sorted(passing, reverse=True)
    clay = {r[0]: float(r[3]) for r in dataset.table("particle_size_tests").rows}
    finest = {r[0]: float(r[4]) for r in curve.rows if r[3] == "0.075"}
    assert all(clay[h] <= finest[h] for h in holes)


def test_a_percentage_is_found_written_as_a_plain_number_or_in_percent() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    assert 'uom="%"' in xml
    # The liquid limit is written as a plain number (its DIGGS property has no unit); the
    # same value written as a fraction with no unit is not a percent and must differ.
    match = re.search(
        r"#liquid_limit\">liquid_limit</propertyClass>.*?<dataValues[^>]*>([^<]+)<", xml, re.S
    )
    assert match is not None
    first = match.group(1).split(",")[0]
    as_fraction = xml.replace(
        f">{match.group(1)}<", f">{float(first) / 100}{match.group(1)[len(first) :]}<", 1
    )
    fidelity = conformance.grade(challenge, as_fraction.encode()).stage("fidelity")
    assert fidelity.status == "fail"
    assert any("liquid_limit" in item.id for item in fidelity.items)


def test_a_lost_non_plastic_flag_is_named() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    assert "non_plastic" in xml
    changed = re.sub(
        r"(#non_plastic\">non_plastic</propertyClass>.*?<dataValues[^>]*>)true",
        r"\1false",
        xml,
        count=1,
        flags=re.S,
    )
    assert changed != xml
    fidelity = conformance.grade(challenge, changed.encode()).stage("fidelity")
    assert [item.id.rsplit("/", 1)[-1] for item in fidelity.items] == ["non_plastic"]


def test_a_changed_grading_point_is_named() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    changed = re.sub(
        r'(<percentPassing uom="%">)(\d+)<',
        lambda m: f"{m.group(1)}{int(m.group(2)) - 7}<",
        xml,
        count=1,
    )
    fidelity = conformance.grade(challenge, changed.encode()).stage("fidelity")
    assert fidelity.status == "fail"
    assert (
        "sieve" in fidelity.items[0].id and fidelity.items[0].check == "conformance.value-differs"
    )


def test_an_invented_value_for_an_empty_cell_is_named() -> None:
    fact = _facts.Fact(
        id="lab/B-1/2.00/bulk_density",
        group="fidelity",
        kind="absent",
        shown="no bulk_density",
        feature="B-1",
        procedure="LabDensityTest",
        key="bulk_density",
        depth=2.0,
        si_unit="kg/m3",
    )
    empty = _facts.Observed(frozenset(), {"B-1": ["b1"]}, {}, {})
    assert _facts.match(fact, empty).status == "found"
    invented = _facts.Observed(
        frozenset(),
        {"B-1": ["b1"]},
        {},
        {("b1", "LabDensityTest", "bulk_density"): [(2.0, 0.0, "kg/m3")]},
    )
    outcome = _facts.match(fact, invented)
    assert (outcome.status, outcome.check) == ("differs", "conformance.value-invented")


def test_the_key_comes_from_the_tables_not_the_file() -> None:
    dataset = _registry.build("lab", "demo", "TOKEN")
    key = _lab.facts(dataset)
    tests = dataset.table("lab_tests")
    reported = sum(1 for row in tests.rows for cell in row[3:] if cell not in ("", "NP"))
    values = [f for f in key if f.kind == "value" and f.procedure != "ParticleSizeTest"]
    flags = [f for f in values if f.key == "non_plastic"]
    assert len(values) - len(flags) == reported and len(flags) == 1
    empty = sum(1 for row in tests.rows for cell in row[3:] if cell in ("", "NP"))
    assert sum(1 for f in key if f.kind == "absent") == empty
    assert sum(1 for f in key if f.kind == "not-flagged") == len(tests.rows) - 1


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_right_answers_pass_and_the_defect_is_reported(seed: str) -> None:
    challenge = _challenge_for(IMPORT, seed)
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


@pytest.mark.parametrize(
    ("question", "wrong"),
    [
        ("values.dry-density", lambda v: str(float(v) / 1000)),
        ("values.non-plastic", lambda v: v + "x"),
    ],
)
def test_a_wrong_import_answer_is_named(question: str, wrong: object) -> None:
    challenge = _challenge_for(IMPORT)
    answers = _right_answers(challenge)
    answers[question] = wrong(answers[question])  # type: ignore[operator]
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert not result.passed
    assert [i.id for i in result.stage("values").items] == [question]


@pytest.mark.parametrize("class_id", ["spt-export", "cpt-export", EXPORT])
def test_an_export_challenge_is_a_tables_package_that_the_kit_passes(
    class_id: str, tmp_path: object
) -> None:
    from pathlib import Path

    from pydiggs import tables

    challenge = _challenge_for(class_id)
    folder = challenge.write(Path(str(tmp_path)) / "challenge")
    descriptor = json.loads((folder / "datapackage.json").read_text())
    names = {resource["name"] for resource in descriptor["resources"]}
    assert {"projects", "features", "measurements"} <= names
    made = tables.to_model(tables.read_package(folder))
    result = conformance.grade(challenge, pydiggs.write_bytes(made.model))
    assert result.passed, result.to_text()


def _np_depth_and_plastic_depth(challenge: conformance.Challenge) -> tuple[str, str]:
    tests = challenge.dataset().table("lab_tests").rows
    non_plastic = next(r for r in tests if r[4] == "NP")
    plastic = next(r for r in tests if r[4] != "NP")
    return non_plastic[2], plastic[2]


def _atterberg_test(xml: str, depth: str, *, flag: bool) -> str:
    """A copy of the first Atterberg test of ``xml``, moved to ``depth``."""
    found = re.search(r"<measurement>\s*<Test .*?</measurement>", xml, re.S)
    tests = re.findall(r"<measurement>\s*<Test .*?</measurement>", xml, re.S)
    template = next(t for t in tests if "AtterbergLimitsTest" in t and ("non_plastic" in t) == flag)
    assert found is not None
    twin = re.sub(r'gml:id="([^"]+)"', r'gml:id="\1-twin"', template)
    return re.sub(r"(<gml:pos>)[^<]+(</gml:pos>)", rf"\g<1>{depth}\g<2>", twin, count=1)


def test_values_invented_for_the_non_plastic_specimen_are_named() -> None:
    challenge = _challenge_for(EXPORT)
    non_plastic, _plastic = _np_depth_and_plastic_depth(challenge)
    xml = challenge.reference().decode()
    twin = _atterberg_test(xml, non_plastic, flag=False)
    doubled = xml.replace("</Diggs>", "").rstrip() + "\n" + twin + "\n</Diggs>\n"
    result = conformance.grade(challenge, doubled.encode())
    checks = {i.check for i in result.stage("fidelity").items}
    assert "conformance.value-invented" in checks, result.to_text()


def test_a_plastic_specimen_flagged_non_plastic_is_named() -> None:
    challenge = _challenge_for(EXPORT)
    _non_plastic, plastic = _np_depth_and_plastic_depth(challenge)
    xml = challenge.reference().decode()
    twin = _atterberg_test(xml, plastic, flag=True)
    doubled = xml.replace("</Diggs>", "").rstrip() + "\n" + twin + "\n</Diggs>\n"
    result = conformance.grade(challenge, doubled.encode())
    items = result.stage("fidelity").items
    assert any(
        i.id.endswith("/non_plastic") and i.check == "conformance.value-invented" for i in items
    )


def test_water_content_on_a_density_test_is_found() -> None:
    fact = next(
        f
        for f in _lab.facts(_registry.build("lab", "demo", "T"))
        if f.key == "water_content_natural"
    )
    seen = _facts.Observed(
        frozenset(),
        {fact.feature: ["k"]},
        {},
        {("k", "LabDensityTest", "water_content_natural"): [(fact.depth, fact.value, "Euc")]},
    )
    assert _facts.match(fact, seen).status == "found"


def test_a_boolean_is_a_number_only_for_a_flag() -> None:
    challenge = _challenge_for("spt-export")
    xml = challenge.reference().decode()
    changed = re.sub(
        r"(#n_value\">n_value</propertyClass>.*?<dataValues[^>]*>)[0-9]+",
        r"\1true",
        xml,
        count=1,
        flags=re.S,
    )
    assert changed != xml
    fidelity = conformance.grade(challenge, changed.encode()).stage("fidelity")
    assert fidelity.status == "fail"
