"""The measurement-while-drilling classes of the conformance suite."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import conformance, tables
from pydiggs.conformance import _challenge, _facts, _mwd, _registry
from pydiggs.conformance._scenario import Dataset, Table

EXPORT, IMPORT = "mwd-export", "mwd-import"
SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)
OFFICIAL = Path(__file__).parent / "fixtures" / "official" / "3.0" / "MWDExample.xml"
Row = dict[str, str]


def _challenge_for(class_id: str, seed: str = "demo") -> conformance.Challenge:
    return conformance.new(class_id, product="Demo Rig", version="3.1", seed=seed)


def _dataset(seed: str = "demo") -> Dataset:
    return _mwd.build(f"1.0-beta.2|mwd|{seed}", "ABC123")


def _rows(dataset: Dataset) -> list[Row]:
    table = dataset.table("mwd_data")
    names = [f.name for f in table.fields]
    return [dict(zip(names, row, strict=True)) for row in table.rows]


def _with_rows(dataset: Dataset, rows: list[Row]) -> Dataset:
    """``dataset`` with its readings replaced by ``rows``."""
    table = dataset.table("mwd_data")
    names = [f.name for f in table.fields]
    changed = Table(table.name, table.fields, tuple(tuple(r[n] for n in names) for r in rows))
    return Dataset(
        dataset.scenario,
        dataset.token,
        tuple(changed if t.name == table.name else t for t in dataset.tables),
    )


def _write(profile: dict[str, list[dict[str, str]]]) -> bytes:
    return pydiggs.write_bytes(tables.to_model(profile).model)


def _fidelity(challenge: conformance.Challenge, xml: bytes) -> list[conformance.Item]:
    result = conformance.grade(challenge, xml)
    assert result.stage("validation").status == "pass", result.to_text()
    return list(result.stage("fidelity").items)


def _right_answers(challenge: conformance.Challenge) -> dict[str, str]:
    stream = _challenge._stream(challenge.info, challenge.seed)
    return {q.id: q.expected for q in _registry.questions(challenge.dataset(), stream)}


# --- the catalogue and the dataset ------------------------------------------------------


def test_the_mwd_classes_are_in_the_catalogue() -> None:
    ids = [c.id for c in conformance.classes()]
    assert EXPORT in ids and IMPORT in ids
    family = next(f for f in conformance.families() if f.id == "mwd")
    assert family.testable and family.scenario == _mwd.SCENARIO
    assert family.elements == ("MeasurementWhileDrilling",)


def test_every_dataset_has_its_traps() -> None:
    units = set()
    for n in range(60):
        dataset = _dataset(f"t{n}")
        rows = _rows(dataset)
        units.add(dataset.table("mwd_data").fields[2].unit)
        names = dataset.table("locations").column("location_id")
        assert 2 <= len(names) <= 3 and any(" " in name for name in names)
        holed = {r["location_id"] for r in rows if not r["flushing_pressure"]}
        assert len(holed) == 1  # one boring's pressure sensor dropped out
        assert 2 <= sum(not r["flushing_pressure"] for r in rows) <= 4
        totals = dict(zip(names, dataset.table("locations").column("total_depth"), strict=True))
        for name in names:
            mine = [r for r in rows if r["location_id"] == name]
            assert 30 <= len(mine) <= 60
            first = mine[0]
            assert Decimal(first["depth"]) == 0 and Decimal(first["elapsed_time"]) == 0
            assert Decimal(first["penetration_rate"]) == 0 == Decimal(first["rotation_speed"])
            depths = [Decimal(r["depth"]) for r in mine]
            times = [Decimal(r["elapsed_time"]) for r in mine]
            assert depths == sorted(set(depths)) and times == sorted(set(times))
            steps = {b - a for a, b in pairwise(depths)}
            assert len(steps) == 2  # the regular step, and the one pause
            assert totals[name] == mine[-1]["depth"]
            assert all(r[c] for r in mine for c in _mwd.CHANNELS if c != "flushing_pressure")
    assert units == {"m", "ft"}


def test_the_key_comes_from_the_tables_alone() -> None:
    dataset = _dataset()
    key = _mwd.facts(dataset)
    rows = _rows(dataset)
    assert len({f.id for f in key}) == len(key)
    values = [f for f in key if f.kind == "value"]
    filled = sum(1 for r in rows for c in _mwd.CHANNELS if r[c])
    assert len(values) == filled + len(rows)  # every channel cell, and each elapsed time
    empty = sum(1 for r in rows if not r["flushing_pressure"])
    gaps = [f for f in key if f.kind == "absent" and f.key == "penetration_rate"]
    # At each step of a pause: no elapsed time and no value of any channel.
    paused = len(gaps) * (len(_mwd.CHANNELS) + 1)
    assert len([f for f in key if f.kind == "absent"]) == empty + paused
    assert {f.key for f in key if f.kind == "absent"} == {
        "time",
        *(dictionary_key for dictionary_key, _meaning in _mwd.CHANNELS.values()),
    }
    assert len(gaps) >= 2 * len(dataset.table("locations").rows)  # 2 to 5 per log


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_file_of_any_seed_is_valid_and_holds_every_fact(seed: str) -> None:
    dataset = _dataset(seed)
    xml = _mwd.reference_xml(dataset)
    report = pydiggs.validate_bytes(xml)
    assert report.ok
    # The only warnings: no srsName on the site-grid positions, as for SPT, CPT and piles.
    assert {f.rule_id for f in report.findings} <= {"geometry.srs-attributes"}
    seen = _mwd.observe(pydiggs.read_bytes(xml))
    missed = [f.id for f in _mwd.facts(dataset) if _facts.match(f, seen).status != "found"]
    assert not missed


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_export_of_any_seed_passes(seed: str) -> None:
    challenge = _challenge_for(EXPORT, seed)
    result = conformance.grade(challenge, challenge.reference())
    assert result.passed, result.to_text()
    assert all(stage.failed == 0 for stage in result.stages)


def test_the_reference_file_is_the_same_for_the_same_dataset() -> None:
    assert _mwd.reference_xml(_dataset()) == _mwd.reference_xml(_dataset())
    assert _mwd.reference_xml(_dataset()) != _mwd.reference_xml(_dataset("other"))


def test_the_reference_file_is_shaped_like_the_official_example() -> None:
    xml = _mwd.reference_xml(_dataset()).decode()
    assert xml.count("<MeasurementWhileDrilling ") == len(_dataset().table("mwd_logs").rows)
    assert '<TimeIntervalList unit="second">' in xml and "<MWDProcedure " in xml
    for key in ("depth", *(key for key, _meaning in _mwd.CHANNELS.values())):
        assert f"mwd_properties.xml#{key}" in xml
    assert "&amp; Approach" in xml


@pytest.mark.parametrize("defect", _facts.DEFECTS)
def test_every_defect_of_the_suite_can_be_planted_and_is_reported(defect: str) -> None:
    xml = _mwd.reference_xml(_dataset())
    rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[defect]
    report = pydiggs.validate_bytes(_challenge._defective(xml, defect))
    assert any(f.rule_id.startswith(rule) for f in report.findings)


# --- what is read from a file -----------------------------------------------------------


def test_the_official_example_is_read_by_depth_with_rpm_converted() -> None:
    seen = _mwd.observe(pydiggs.read(OFFICIAL))
    (feature,) = seen.named["TEST HOLE 1"]
    rate = seen.readings[(feature, _mwd.PROCEDURE, "penetration_rate")]
    assert len(rate) == 54 and rate[1][0] == pytest.approx(0.04 * 0.3048)
    assert rate[1][1:] == (pytest.approx(0.0100168 * 0.00508), "m/s")
    rotation = seen.readings[(feature, _mwd.PROCEDURE, "rotation_shaft")]
    assert rotation[1][1:] == (pytest.approx(109.452 * math.pi / 30), "rad/s")
    times = seen.readings[(feature, _mwd.PROCEDURE, "time")]
    assert times[-1][1:] == (758.5, "s")
    assert (feature, _mwd.PROCEDURE, "depth") not in seen.readings


def test_a_null_value_marks_a_missing_reading() -> None:
    challenge = _challenge_for(EXPORT)
    profile = _mwd.profile_tables(challenge.dataset())
    for row in profile["results_MWDProcedure"]:
        if not row["fluid_injection_pressure"]:
            row["fluid_injection_pressure"] = "9999"
    declared = [dict(c) for c in profile["result_columns"]]
    for column in declared:
        if column["column"] == "fluid_injection_pressure":
            column["null_value"] = "9999"
    assert not _fidelity(challenge, _write({**profile, "result_columns": declared}))
    # The same 9999 with no nullValue is a reading the source never had.
    items = _fidelity(challenge, _write(profile))
    assert items and {i.check for i in items} == {"conformance.value-invented"}
    assert all(i.id.endswith("/flushing_pressure") for i in items)


def test_any_declared_unit_passes() -> None:
    challenge = _challenge_for(EXPORT, "metric")
    dataset = challenge.dataset()
    assert dataset.table("mwd_data").fields[2].unit == "m"  # the seed is a metric one
    profile = _mwd.profile_tables(dataset)
    for row in profile["results_MWDProcedure"]:
        row["rotation_shaft"] = f"{float(row['rotation_shaft']) * math.pi / 30:.6f}"
        if row["fluid_injection_pressure"]:
            row["fluid_injection_pressure"] = f"{float(row['fluid_injection_pressure']) * 100:.2f}"
        row["penetration_rate"] = f"{float(row['penetration_rate']) / 60:.5f}"
    units = {
        "rotation_shaft": "rad/s",
        "fluid_injection_pressure": "kPa",
        "penetration_rate": "m/min",
    }
    for column in profile["result_columns"]:
        column["uom"] = units.get(column["column"], column["uom"])
    assert not _fidelity(challenge, _write(profile))


# --- each trap, failed on purpose -------------------------------------------------------


def _changed(challenge: conformance.Challenge, change: Callable[[list[Row]], None]) -> bytes:
    rows = _rows(challenge.dataset())
    change(rows)
    return _mwd.reference_xml(_with_rows(challenge.dataset(), rows))


def _pressure_as_zero(rows: list[Row]) -> None:
    for row in rows:
        row["flushing_pressure"] = row["flushing_pressure"] or "0"


def _collar_rate_dropped(rows: list[Row]) -> None:
    rows[0]["penetration_rate"] = ""


def _gap_filled(rows: list[Row], *, without: tuple[str, ...] = ()) -> None:
    """A reading invented at the first step of the first pause, as resampling would.

    The cells of the ``without`` columns are left empty in it.
    """
    step = Decimal(rows[1]["depth"]) - Decimal(rows[0]["depth"])
    for i, (before, after) in enumerate(pairwise(rows)):
        same = before["location_id"] == after["location_id"]
        if same and Decimal(after["depth"]) - Decimal(before["depth"]) > step:
            filler = dict(before)
            filler["depth"] = str(Decimal(before["depth"]) + step)
            filler["elapsed_time"] = str(Decimal(before["elapsed_time"]) + 1)
            filler.update(dict.fromkeys(without, ""))
            rows.insert(i + 1, filler)
            return
    raise AssertionError("no pause")


def _rate_changed(rows: list[Row]) -> None:
    rows[5]["penetration_rate"] = str(Decimal(rows[5]["penetration_rate"]) * 2)


@pytest.mark.parametrize(
    ("change", "check", "column"),
    [
        (_pressure_as_zero, "conformance.value-invented", "flushing_pressure"),
        (_collar_rate_dropped, "conformance.fact-missing", "penetration_rate"),
        (_rate_changed, "conformance.value-differs", "penetration_rate"),
    ],
)
def test_a_planted_mistake_fails_with_its_check(
    change: Callable[[list[Row]], None], check: str, column: str
) -> None:
    challenge = _challenge_for(EXPORT)
    items = _fidelity(challenge, _changed(challenge, change))
    assert items
    assert {i.check for i in items} == {check}, [i.id for i in items]
    assert all(i.id.endswith(f"/{column}") for i in items)


def test_a_reading_invented_in_the_pause_fails_in_every_cell_it_fills() -> None:
    challenge = _challenge_for(EXPORT)
    items = _fidelity(challenge, _changed(challenge, _gap_filled))
    assert {i.check for i in items} == {"conformance.value-invented"}
    assert {i.id.rpartition("/")[2] for i in items} == {"elapsed_time", *_mwd.CHANNELS}
    assert len({i.id.rpartition("/")[0] for i in items}) == 1  # all at the one depth


@pytest.mark.parametrize(
    "kept",
    ["elapsed_time", "thrust", "torque", "rotation_speed", "flushing_pressure"],
)
def test_a_reading_invented_in_the_pause_fails_whatever_it_leaves_empty(kept: str) -> None:
    # Found in review: only the penetration rate was looked for in the pause, so an
    # invented row that left it empty and filled the other channels passed.
    challenge = _challenge_for(EXPORT)
    without = tuple(c for c in _mwd.CHANNELS if c != kept)

    def invented(rows: list[Row]) -> None:
        _gap_filled(rows, without=without)

    items = _fidelity(challenge, _changed(challenge, invented))
    assert [i.check for i in items] == ["conformance.value-invented"] * len(items)
    # The time of a row is always written (it places the row), so it is always found.
    assert {i.id.rpartition("/")[2] for i in items} == {"elapsed_time", kept}


def test_a_log_with_times_and_no_depths_fails_fidelity() -> None:
    # docs/conformance.md, known limits: readings that cannot be placed are missing.
    challenge = _challenge_for(EXPORT)
    profile = _mwd.profile_tables(challenge.dataset())
    columns = [c for c in profile["result_columns"] if c["column"] != "depth"]
    rows = [
        {k: v for k, v in row.items() if k != "depth"} for row in profile["results_MWDProcedure"]
    ]
    xml = _write({**profile, "result_columns": columns, "results_MWDProcedure": rows})
    assert b"mwd_properties.xml#depth" not in xml and b"<timeDomain>" in xml
    result = conformance.grade(challenge, xml)
    assert result.stage("core").status == "pass"
    fidelity = result.stage("fidelity")
    assert fidelity.status == "fail" and not result.passed
    assert {i.check for i in fidelity.items} == {"conformance.fact-missing"}
    values = [f for f in _mwd.facts(challenge.dataset()) if f.kind == "value"]
    assert fidelity.failed == len(values)


def test_a_unit_mislabelled_fails() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    unit = challenge.dataset().table("mwd_data").fields[-1].unit
    changed = re.sub(
        rf"(#fluid_injection_pressure\">fluid_injection_pressure</propertyClass>\s*<uom>){re.escape(unit)}<",
        r"\1kPa<",
        xml,
        count=1,
    )
    assert changed != xml
    items = _fidelity(challenge, changed.encode())
    assert items and {i.check for i in items} == {"conformance.value-differs"}
    assert all(i.id.endswith("/flushing_pressure") for i in items)


def test_a_renamed_boring_is_missing() -> None:
    challenge = _challenge_for(EXPORT)
    name = next(n for n in challenge.dataset().table("locations").column("location_id") if " " in n)
    xml = challenge.reference().decode().replace(f">{name}<", f">{name.replace(' ', '-')}<")
    result = conformance.grade(challenge, xml.encode())
    assert not result.passed
    assert {i.check for i in result.stage("core").items} == {"conformance.fact-missing"}


# --- the challenges ---------------------------------------------------------------------


def test_an_export_challenge_is_a_tables_package_that_the_kit_passes(tmp_path: Path) -> None:
    challenge = _challenge_for(EXPORT)
    folder = challenge.write(tmp_path / "challenge")
    descriptor = json.loads((folder / "datapackage.json").read_text())
    names = {resource["name"] for resource in descriptor["resources"]}
    assert {"projects", "features", "measurements", "results_MWDProcedure"} <= names
    assert not (folder / "columns.csv").exists()
    results = (folder / "results_MWDProcedure.csv").read_text().splitlines()
    assert any(line.endswith(",") for line in results)  # an empty cell stays empty
    made = tables.to_model(tables.read_package(folder))
    result = conformance.grade(challenge, pydiggs.write_bytes(made.model))
    assert result.passed, result.to_text()


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


def test_the_questions_are_answered_from_the_file() -> None:
    challenge = _challenge_for(IMPORT)
    model = pydiggs.read_bytes(challenge.files["challenge.xml"])
    answers = _right_answers(challenge)
    logs = {m.feature: m for m in model.measurements}
    named = {f.name: f.key for f in model.features}
    boring = re.search(r"log of (.+) hold", challenge.files["answers.csv"].decode())
    assert boring is not None
    (table,) = logs[named[boring.group(1)]].results
    assert int(answers["values.readings"]) == table.row_count


@pytest.mark.parametrize(
    ("question", "wrong"),
    [
        ("values.no-flushing-pressure", lambda v: "0"),
        ("values.rotation-speed", lambda v: str(float(v) * math.pi / 30)),
        ("values.resumed", lambda v: str(float(v) * 1.5)),
    ],
)
def test_a_wrong_import_answer_is_named(question: str, wrong: Callable[[str], str]) -> None:
    challenge = _challenge_for(IMPORT)
    answers = _right_answers(challenge)
    answers[question] = wrong(answers[question])
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert not result.passed
    assert [i.id for i in result.stage("values").items] == [question]


def test_docstring_examples_run() -> None:
    import doctest

    failed, attempted = doctest.testmod(_mwd, optionflags=doctest.ELLIPSIS)
    assert failed == 0 and attempted > 0


def _placed_by_depth(match: re.Match[str]) -> str:
    """A log of the reference file placed by a 1D location instead of its times."""
    block = match.group(0)
    feature = re.search(r'samplingFeatureRef xlink:href="#([^"]+)"', block)
    values = re.search(r"<dataValues[^>]*>([^<]*)<", block)
    assert feature is not None and values is not None
    depths = " ".join(row.split(",")[0] for row in values.group(1).split())
    ident = feature.group(1)
    location = (
        f'<location><MultiPointLocation gml:id="{ident}-points" srsName="#{ident}-lrs"'
        f' srsDimension="1"><gml:posList>{depths}</gml:posList></MultiPointLocation></location>'
    )
    block = re.sub(r"<timeDomain>.*?</timeDomain>", location, block, flags=re.S)
    # The depths stay in the table, under another length property: not the depth column.
    return block.replace('#depth">depth<', '#penetration_length">penetration_length<')


def test_a_log_placed_by_its_location_is_read_at_those_depths() -> None:
    challenge = _challenge_for(EXPORT)
    xml = re.sub(
        r"<MeasurementWhileDrilling .*?</MeasurementWhileDrilling>",
        _placed_by_depth,
        challenge.reference().decode(),
        flags=re.S,
    )
    fidelity = conformance.grade(challenge, xml.encode()).stage("fidelity")
    items = fidelity.items
    # Every channel is found; only the elapsed times, which the file no longer has, are not.
    assert fidelity.failed == len(_rows(challenge.dataset()))
    assert {(i.id.rsplit("/", 1)[-1], i.check) for i in items} == {
        ("elapsed_time", "conformance.fact-missing")
    }
