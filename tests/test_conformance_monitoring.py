"""The monitoring classes of the conformance suite: piezometer and logger time series."""

from __future__ import annotations

import doctest
import json
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
from pydiggs.conformance import _challenge, _facts, _monitoring, _registry
from pydiggs.conformance._scenario import Dataset, Table

EXPORT, IMPORT = "monitoring-export", "monitoring-import"
SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)
OFFICIAL = Path(__file__).parent / "fixtures" / "official" / "3.0" / "Piezometer_Example.xml"
Row = dict[str, str]


def _challenge_for(class_id: str, seed: str = "demo") -> conformance.Challenge:
    return conformance.new(class_id, product="Demo Logger", version="2.0", seed=seed)


def _dataset(seed: str = "demo") -> Dataset:
    return _monitoring.build(f"1.0-beta.3|monitoring|{seed}", "ABC123")


def _records(dataset: Dataset, name: str) -> list[Row]:
    table = dataset.table(name)
    names = [f.name for f in table.fields]
    return [dict(zip(names, row, strict=True)) for row in table.rows]


def _rows(dataset: Dataset) -> list[Row]:
    return _records(dataset, "readings")


def _with(dataset: Dataset, name: str, rows: list[Row]) -> Dataset:
    """``dataset`` with the rows of its table ``name`` replaced by ``rows``."""
    table = dataset.table(name)
    names = [f.name for f in table.fields]
    changed = Table(table.name, table.fields, tuple(tuple(r[n] for n in names) for r in rows))
    return Dataset(
        dataset.scenario,
        dataset.token,
        tuple(changed if t.name == table.name else t for t in dataset.tables),
    )


def _seed_with(wanted: Callable[[Dataset], bool]) -> str:
    """A seed whose challenge's dataset is as ``wanted`` (the suite draws units and zones)."""
    for n in range(200):
        if wanted(_challenge_for(EXPORT, f"s{n}").dataset()):
            return f"s{n}"
    raise AssertionError("no such seed in 200")


def _in(length: str) -> Callable[[Dataset], bool]:
    """Whether a dataset's depths are in ``length`` (m or ft)."""

    def wanted(dataset: Dataset) -> bool:
        return dataset.table("readings").fields[2].unit == length

    return wanted


def _zone(dataset: Dataset) -> str:
    stamp = _rows(dataset)[0]["time"]
    return "Z" if stamp.endswith("Z") else stamp[-6:]


def _fidelity(challenge: conformance.Challenge, xml: bytes) -> list[conformance.Item]:
    result = conformance.grade(challenge, xml)
    assert result.stage("validation").status == "pass", result.to_text()
    return list(result.stage("fidelity").items)


def _right_answers(challenge: conformance.Challenge) -> dict[str, str]:
    stream = _challenge._stream(challenge.info, challenge.seed)
    return {q.id: q.expected for q in _registry.questions(challenge.dataset(), stream)}


# --- the catalogue and the dataset ------------------------------------------------------


def test_the_monitoring_classes_are_in_the_catalogue() -> None:
    ids = [c.id for c in conformance.classes()]
    assert ids[-2:] == [EXPORT, IMPORT] and len(ids) == 18
    family = next(f for f in conformance.families() if f.id == "monitoring")
    assert family.testable and family.scenario == _monitoring.SCENARIO
    assert {"Monitor", "WaterLevelMonitoring", "Well"} <= set(family.elements)
    assert family.note is not None and "not graded yet" in family.note
    assert sum(f.testable for f in conformance.families()) == 9


def test_the_suite_version_did_not_change_with_the_new_classes() -> None:
    # Adding a class grades no file of another class differently, so the suite keeps its
    # version, as it did when the mwd, dissipation and in-situ strength classes joined.
    assert conformance.SUITE_VERSION == "1.0-beta.3"
    assert conformance.GRADED_SUITES == ("1.0-beta.2", "1.0-beta.3")
    assert _challenge_for(EXPORT).manifest["suite_version"] == "1.0-beta.3"


def test_the_scenario_uses_what_the_dictionary_defines_for_the_procedure() -> None:
    from pydiggs.conformance._requirements import defined_properties

    defined = defined_properties("properties.xml", _monitoring.PROCEDURE)
    assert defined == frozenset(_monitoring.CHANNELS)


def test_every_dataset_has_its_traps() -> None:
    units, zones = set(), set()
    for n in range(60):
        dataset = _dataset(f"t{n}")
        rows = _rows(dataset)
        installed = {r["location_id"]: r for r in _records(dataset, "piezometers")}
        fields = {f.name: f.unit for f in dataset.table("readings").fields}
        units.add((fields["water_depth"], fields["water_elev"], fields["temperature"]))
        zones.add(_zone(dataset))
        names = dataset.table("locations").column("location_id")
        assert 2 <= len(names) <= 3 and any(" " in name for name in names)
        assert list(installed) == list(names)
        holed = {r["location_id"] for r in rows if not r["temperature"]}
        assert len(holed) == 1  # one piezometer's temperature channel dropped out
        assert 2 <= sum(not r["temperature"] for r in rows) <= 4
        assert sum(Decimal(r["water_depth"]) == 0 for r in rows) == 1  # one real zero
        totals = dict(zip(names, dataset.table("locations").column("total_depth"), strict=True))
        for name in names:
            mine = [r for r in rows if r["location_id"] == name]
            assert 24 <= len(mine) <= 48
            assert len({_zone_of(r["time"]) for r in mine}) == 1
            instants = [_at(r["time"]) for r in mine]
            assert instants == sorted(set(instants))
            steps = {b - a for a, b in pairwise(instants)}
            interval = int(installed[name]["reading_interval"]) * 60
            assert len(steps) == 2 and min(steps) == interval  # regular, and one stop
            assert 3 * interval <= max(steps) <= 7 * interval
            sensor = Decimal(installed[name]["sensor_depth"])
            assert all(Decimal(r["water_depth"]) < sensor for r in mine)
            assert sensor < Decimal(totals[name])
            assert _at(installed[name]["datum_from"]) < instants[0]
            assert all(r["water_depth"] and r["water_elev"] for r in mine)
    assert units == {("m", "m", "degC"), ("ft", "ft", "degF")}
    assert "Z" in zones and len(zones) > 2  # UTC, and more than one offset


def _zone_of(stamp: str) -> str:
    return "Z" if stamp.endswith("Z") else stamp[-6:]


def _at(stamp: str) -> float:
    """The instant of a time of the tables."""
    seconds = _monitoring.instant(stamp)
    assert seconds is not None, stamp
    return seconds


def test_the_key_comes_from_the_tables_alone() -> None:
    dataset = _dataset()
    key = _monitoring.facts(dataset)
    rows = _rows(dataset)
    assert len({f.id for f in key}) == len(key)
    fidelity = [f for f in key if f.group == "fidelity"]
    values = [f for f in fidelity if f.kind == "value"]
    filled = sum(1 for r in rows for c in _monitoring.CHANNELS if r[c])
    series = len(dataset.table("piezometers").rows)
    assert len(values) == filled + series  # every cell that holds a value, each sensor depth
    empty = sum(1 for r in rows if not r["temperature"])
    stopped = [f for f in fidelity if f.kind == "absent" and f.key == "water_depth"]
    # At each time the logger was stopped: no value of any channel.
    absent = [f for f in fidelity if f.kind == "absent"]
    assert len(absent) == empty + len(stopped) * len(_monitoring.CHANNELS)
    assert 2 * series <= len(stopped) <= 6 * series  # two to six per series
    assert {f.key for f in absent} == set(_monitoring.CHANNELS)
    assert {f.si_unit for f in values} == {"m", "K"}


# --- times ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "same"),
    [
        ("2024-03-05T09:30:00-05:00", "2024-03-05T14:30:00Z"),
        ("2024-03-05T20:00:00+05:30", "2024-03-05T14:30:00Z"),
        ("2024-03-06T00:30:00+10:00", "2024-03-05T14:30:00Z"),
        ("2024-03-05T14:30:00+00:00", "2024-03-05T14:30:00Z"),
        ("2024-03-05T14:30:00.000Z", "2024-03-05T14:30:00Z"),
        ("2024-02-29T24:00:00Z", "2024-03-01T00:00:00Z"),
        ("2023-12-31T19:00:00-05:00", "2024-01-01T00:00:00Z"),
    ],
)
def test_two_forms_of_one_instant_are_the_same_time(text: str, same: str) -> None:
    assert _monitoring.instant(text) == _monitoring.instant(same) is not None


@pytest.mark.parametrize(
    "text",
    [
        "2024-03-05T14:30:00",  # no zone: local time somewhere
        "2024-03-05",
        "2024-03-05 14:30:00Z",
        "2024-03-05T14:30Z",
        "2024-02-30T00:00:00Z",
        "2024-03-05T24:00:01Z",
        "2024-03-05T25:00:00Z",
        "2024-03-05T14:60:00Z",
        "2024-03-05T14:30:60Z",
        "2024-03-05T14:30:00+14:01",
        "2024-03-05T14:30:00+05:60",
        "0000-01-01T00:00:00Z",
        "yesterday",
        "",
        "2024-03-05T14:30:00." + "0" * 80 + "Z",
    ],
)
def test_what_names_no_instant_is_not_a_time(text: str) -> None:
    assert _monitoring.instant(text) is None


def test_an_instant_keeps_a_fraction_of_a_second() -> None:
    whole = _monitoring.instant("2025-06-01T00:00:00Z")
    assert whole is not None
    assert _monitoring.instant("2025-06-01T00:00:00.25Z") == whole + 0.25
    assert _monitoring.instant("2025-06-01T00:00:01Z") == whole + 1


# --- the reference file -----------------------------------------------------------------


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_file_of_any_seed_is_valid_and_holds_every_fact(seed: str) -> None:
    dataset = _dataset(seed)
    xml = _monitoring.reference_xml(dataset)
    report = pydiggs.validate_bytes(xml)
    assert report.ok
    # The only warnings: no srsName on the site-grid positions, as for SPT, CPT and piles.
    assert {f.rule_id for f in report.findings} <= {"geometry.srs-attributes"}
    seen = _monitoring.observe(pydiggs.read_bytes(xml))
    key = _monitoring.facts(dataset)
    missed = [f.id for f in key if _facts.match(f, seen).status != "found"]
    assert not missed


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_export_of_any_seed_passes(seed: str) -> None:
    challenge = _challenge_for(EXPORT, seed)
    result = conformance.grade(challenge, challenge.reference())
    assert result.passed, result.to_text()
    assert all(stage.failed == 0 for stage in result.stages)


def test_the_reference_file_is_the_same_for_the_same_dataset() -> None:
    assert _monitoring.reference_xml(_dataset()) == _monitoring.reference_xml(_dataset())
    assert _monitoring.reference_xml(_dataset()) != _monitoring.reference_xml(_dataset("other"))


def test_the_reference_file_is_shaped_as_diggs_3_0_0_requires_a_monitor() -> None:
    dataset = _dataset()
    xml = _monitoring.reference_xml(dataset).decode()
    series = len(dataset.table("piezometers").rows)
    assert xml.count("<Monitor ") == series == xml.count("<WaterLevelMonitoring ")
    # What MonitorType requires: a monitorLocation and an outcome (a TemporalResult with
    # a timeDomain and results); what WaterLevelMonitoringType requires: a ReferencePoint
    # with a time and an rpLocation. The official piezometer example has the same parts.
    official = OFFICIAL.read_text("utf-8")
    for part in (
        "<monitorLocation xlink:href=",
        "<TemporalResult ",
        "<TimePositionList ",
        "<timePositionList>",
        "<referencePoints>",
        "<ReferencePoint",
        "<rpLocation xlink:href=",
        "<Sensor ",
        "<Detector ",
    ):
        assert xml.count(part) == series, part
        assert part in official, part
    for key in _monitoring.CHANNELS:
        assert xml.count(f'properties.xml#{key}">') == series
        assert f"properties.xml#{key}" in official
    assert "&amp; Approach" in xml
    # Every reference resolves to the kind of thing it names.
    for target in re.findall(r'<monitorLocation xlink:href="#([^"]+)"', xml):
        assert f'<PointLocation gml:id="{target}" srsName="#boring-' in xml
    for target in re.findall(r'<rpLocation xlink:href="#([^"]+)"', xml):
        assert f'<PointLocation gml:id="{target}" srsDimension="3">' in xml


def test_the_reference_file_carries_every_time_as_the_tables_write_it() -> None:
    dataset = _dataset(_seed_with(lambda d: _zone(d) != "Z"))
    xml = _monitoring.reference_xml(dataset).decode()
    written = [t for block in re.findall(r"<timePositionList>([^<]*)<", xml) for t in block.split()]
    assert written == [r["time"] for r in _rows(dataset)]
    assert all(t.endswith(_zone(dataset)) for t in written)


@pytest.mark.parametrize("defect", _facts.DEFECTS)
def test_every_defect_of_the_suite_can_be_planted_and_is_reported(defect: str) -> None:
    xml = _monitoring.reference_xml(_dataset())
    rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[defect]
    report = pydiggs.validate_bytes(_challenge._defective(xml, defect))
    assert any(f.rule_id.startswith(rule) for f in report.findings)


# --- what is read from a file -----------------------------------------------------------


def test_the_official_example_has_no_instants_because_its_times_have_no_zone() -> None:
    model = pydiggs.read(OFFICIAL)
    seen = _monitoring.observe(model)
    (feature,) = seen.named["PZ-B-03-23 Piezometer"]
    # Its times are written without an offset, so no reading is placed at an instant;
    # its monitorLocation is a well opening's interval, not a point, so no sensor depth.
    assert not [k for k in seen.readings if k[0] == feature and k[1] == _monitoring.PROCEDURE]
    (monitor,) = [m for m in model.measurements if m.element == "Monitor"]
    (table,) = monitor.results
    assert table.domain.shape == "temporal" and table.row_count > 1000
    assert {c.key for c in table.columns} == set(_monitoring.CHANNELS)


def test_the_official_example_is_read_once_its_times_say_their_zone() -> None:
    text = OFFICIAL.read_text("utf-8")
    start = text.index("<timePositionList>") + len("<timePositionList>")
    end = text.index("</timePositionList>")
    zoned = " ".join(f"{t}-05:00" for t in text[start:end].split())
    seen = _monitoring.observe(pydiggs.read_bytes((text[:start] + zoned + text[end:]).encode()))
    (feature,) = seen.named["PZ-B-03-23 Piezometer"]
    depth = seen.readings[(feature, _monitoring.PROCEDURE, "water_depth")]
    when = _monitoring.instant("2024-11-05T18:00:01Z")  # 13:00:01 at -05:00, the first row
    (first,) = [r for r in depth if r[0] == when]
    assert first[1:] == (pytest.approx(23.683084983767312 * 0.3048), "m")
    warmth = seen.readings[(feature, _monitoring.PROCEDURE, "temperature")]
    (kelvin,) = [r for r in warmth if r[0] == when]
    assert kelvin[1:] == (pytest.approx((56.97303791760282 + 459.67) * 5 / 9), "K")


def _retimed(xml: bytes, change: Callable[[str], str]) -> bytes:
    """``xml`` with every time of every series rewritten by ``change``."""

    def block(match: re.Match[str]) -> str:
        return match.group(1) + " ".join(change(t) for t in match.group(2).split()) + "<"

    changed = re.sub(r"(<timePositionList>)([^<]*)<", block, xml.decode())
    assert changed.encode() != xml
    return changed.encode()


def _shifted(stamp: str, zone: str) -> str:
    """The instant of ``stamp`` written with the offset ``zone``."""
    return _monitoring._stamp(int(_at(stamp) // 60), zone)


@pytest.mark.parametrize("zone", ["Z", "+00:00", "-03:30", "+13:00"])
def test_any_offset_that_names_the_same_instant_passes(zone: str) -> None:
    challenge = _challenge_for(EXPORT, _seed_with(lambda d: _zone(d) not in ("Z", "-03:30")))

    def rewritten(stamp: str) -> str:
        if zone == "+00:00":
            return _shifted(stamp, "Z")[:-1] + zone
        return _shifted(stamp, zone)

    xml = _retimed(challenge.reference(), rewritten)
    assert not _fidelity(challenge, xml)
    assert conformance.grade(challenge, xml).passed


def test_a_time_with_its_offset_dropped_is_not_found() -> None:
    challenge = _challenge_for(EXPORT)
    xml = _retimed(challenge.reference(), lambda t: t[:19])
    fidelity = conformance.grade(challenge, xml).stage("fidelity")
    readings = [
        f
        for f in _monitoring.facts(challenge.dataset())
        if f.kind == "value" and f.key != _monitoring.SENSOR_DEPTH and f.group == "fidelity"
    ]
    assert {i.check for i in fidelity.items} == {"conformance.fact-missing"}
    assert fidelity.failed == len(readings)  # every reading, and nothing else


def test_an_offset_changed_without_moving_the_clock_fails() -> None:
    challenge = _challenge_for(EXPORT)
    other = "+02:00" if _zone(challenge.dataset()) != "+02:00" else "Z"
    xml = _retimed(challenge.reference(), lambda t: t[:19] + other)
    result = conformance.grade(challenge, xml)
    fidelity = result.stage("fidelity")
    # Every reading is at another instant. Where it falls on a time of another reading
    # the value differs, where it falls on no reading's time the source value is
    # missing, and where it falls on a time the logger was stopped it is invented.
    assert not result.passed and fidelity.failed > 0
    assert {i.check for i in fidelity.items} <= {
        "conformance.fact-missing",
        "conformance.value-differs",
        "conformance.value-invented",
    }


def test_a_time_one_second_off_is_another_time() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    first = _rows(challenge.dataset())[0]["time"]
    late = first[:17] + "01" + first[19:]  # the same minute, one second later
    assert _at(late) == _at(first) + 1 and xml.count(first) == 1
    items = _fidelity(challenge, xml.replace(first, late).encode())
    assert {i.check for i in items} == {"conformance.fact-missing"}
    assert {i.id.rpartition("/")[2] for i in items} <= set(_monitoring.CHANNELS)
    assert len({i.id.rpartition("/")[0] for i in items}) == 1  # the one reading


def test_a_null_value_marks_a_missing_reading() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    unit = challenge.dataset().table("readings").fields[4].unit
    marked = re.sub(r",(?=[ <])", ",-999", xml)  # each empty temperature cell
    assert marked != xml
    declared = marked.replace(
        f"<uom>{unit}</uom>", f"<uom>{unit}</uom>\n<nullValue>-999</nullValue>"
    )
    assert declared.count("<nullValue>") == xml.count("<Monitor ")
    assert not _fidelity(challenge, declared.encode())
    # The same -999 with no nullValue is a reading the source never had.
    items = _fidelity(challenge, marked.encode())
    assert items and {i.check for i in items} == {"conformance.value-invented"}
    assert all(i.id.endswith("/temperature") for i in items)


def _in_other_units(xml: str, units: dict[str, str]) -> str:
    """The file with its depths in millimetres or inches and its temperatures in kelvin."""
    length, warmth = units["water_depth"], units["temperature"]
    small, per = ("mm", Decimal(1000)) if length == "m" else ("in", Decimal(12))
    offset = Decimal("273.15") if warmth == "degC" else Decimal("459.67")
    scale = Decimal(1) if warmth == "degC" else Decimal(5) / Decimal(9)

    def cells(match: re.Match[str]) -> str:
        rows = []
        for row in match.group(2).split():
            depth, elev, temp = row.split(",")
            kelvin = f"{(Decimal(temp) + offset) * scale:.4f}" if temp else ""
            rows.append(f"{Decimal(depth) * per},{Decimal(elev) * per},{kelvin}")
        return match.group(1) + " ".join(rows) + "<"

    changed = re.sub(r"(<dataValues[^>]*>)([^<]*)<", cells, xml)
    changed = changed.replace(f"<uom>{length}</uom>", f"<uom>{small}</uom>")
    return changed.replace(f"<uom>{warmth}</uom>", "<uom>K</uom>")


@pytest.mark.parametrize("length", ["m", "ft"])
def test_any_declared_unit_passes(length: str) -> None:
    seed = _seed_with(_in(length))
    challenge = _challenge_for(EXPORT, seed)
    units = {f.name: f.unit for f in challenge.dataset().table("readings").fields}
    xml = _in_other_units(challenge.reference().decode(), units)
    assert "<uom>K</uom>" in xml and f"<uom>{length}</uom>" not in xml
    result = conformance.grade(challenge, xml.encode())
    assert result.passed, result.to_text()


def test_a_unit_mislabelled_fails() -> None:
    challenge = _challenge_for(EXPORT)
    xml = challenge.reference().decode()
    unit = challenge.dataset().table("readings").fields[4].unit
    other = "degF" if unit == "degC" else "degC"
    changed = xml.replace(f"<uom>{unit}</uom>", f"<uom>{other}</uom>", 1)
    assert changed != xml
    items = _fidelity(challenge, changed.encode())
    assert items and {i.check for i in items} == {"conformance.value-differs"}
    assert all(i.id.endswith("/temperature") for i in items)
    assert len({i.id.split("/")[1] for i in items}) == 1  # the one series


# --- each trap, failed on purpose -------------------------------------------------------


def _changed(challenge: conformance.Challenge, change: Callable[[list[Row]], None]) -> bytes:
    rows = _rows(challenge.dataset())
    change(rows)
    return _monitoring.reference_xml(_with(challenge.dataset(), "readings", rows))


def _temperature_as_zero(rows: list[Row]) -> None:
    for row in rows:
        row["temperature"] = row["temperature"] or "0"


def _zero_dropped(rows: list[Row]) -> None:
    for row in rows:
        if Decimal(row["water_depth"]) == 0:
            row["water_depth"] = ""


def _level_changed(rows: list[Row]) -> None:
    row = next(r for r in rows[3:] if Decimal(r["water_depth"]) != 0)
    row["water_depth"] = str(Decimal(row["water_depth"]) + Decimal("0.01"))


def _gap_filled(rows: list[Row], *, without: tuple[str, ...] = ()) -> None:
    """A reading invented at the first time the first logger was stopped, as filling would."""
    instants = [_at(r["time"]) for r in rows]
    interval = instants[1] - instants[0]
    for i, (before, after) in enumerate(pairwise(rows)):
        same = before["location_id"] == after["location_id"]
        if same and instants[i + 1] - instants[i] > interval:
            filler = dict(before)
            zone = _zone_of(before["time"])
            filler["time"] = _monitoring._stamp(int((instants[i] + interval) // 60), zone)
            filler.update(dict.fromkeys(without, ""))
            rows.insert(i + 1, filler)
            return
    raise AssertionError("no stop")


@pytest.mark.parametrize(
    ("change", "check", "column"),
    [
        (_temperature_as_zero, "conformance.value-invented", "temperature"),
        (_zero_dropped, "conformance.fact-missing", "water_depth"),
        (_level_changed, "conformance.value-differs", "water_depth"),
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


def test_a_reading_invented_where_the_logger_was_stopped_fails_in_every_cell() -> None:
    challenge = _challenge_for(EXPORT)
    items = _fidelity(challenge, _changed(challenge, _gap_filled))
    assert {i.check for i in items} == {"conformance.value-invented"}
    assert {i.id.rpartition("/")[2] for i in items} == set(_monitoring.CHANNELS)
    assert len({i.id.rpartition("/")[0] for i in items}) == 1  # all at the one time


@pytest.mark.parametrize("kept", sorted(_monitoring.CHANNELS))
def test_a_reading_invented_in_the_stop_fails_whatever_it_leaves_empty(kept: str) -> None:
    challenge = _challenge_for(EXPORT)
    without = tuple(c for c in _monitoring.CHANNELS if c != kept)

    def invented(rows: list[Row]) -> None:
        _gap_filled(rows, without=without)

    items = _fidelity(challenge, _changed(challenge, invented))
    assert [(i.check, i.id.rpartition("/")[2]) for i in items] == [
        ("conformance.value-invented", kept)
    ]


def test_a_series_filled_to_its_regular_interval_fails_at_every_filled_time() -> None:
    challenge = _challenge_for(EXPORT)
    dataset = challenge.dataset()
    stopped = [
        f
        for f in _monitoring.facts(dataset)
        if f.kind == "absent" and "the logger was stopped" in f.shown
    ]

    def filled(rows: list[Row]) -> None:
        intervals = {
            r["location_id"]: int(r["reading_interval"]) for r in _records(dataset, "piezometers")
        }
        out: list[Row] = []
        for before, after in pairwise([*rows, {"location_id": "", "time": ""}]):
            out.append(before)
            if before["location_id"] != after["location_id"]:
                continue
            step = intervals[before["location_id"]] * 60
            at = _at(before["time"]) + step
            while at < _at(after["time"]):
                stamp = _monitoring._stamp(int(at // 60), _zone_of(before["time"]))
                out.append({**before, "time": stamp})
                at += step
        rows[:] = out

    items = _fidelity(challenge, _changed(challenge, filled))
    assert {i.check for i in items} == {"conformance.value-invented"}
    # Every channel at every filled time, less a temperature the filled row copies as empty.
    assert len(stopped) - 4 <= len(items) <= len(stopped)
    assert {i.id for i in items} <= {f.id for f in stopped}


def test_a_sensor_at_another_depth_differs_and_one_with_no_place_is_missing() -> None:
    challenge = _challenge_for(EXPORT)
    dataset = challenge.dataset()
    installed = _records(dataset, "piezometers")
    name, depth = installed[0]["location_id"], installed[0]["sensor_depth"]
    installed[0]["sensor_depth"] = str(Decimal(depth) + Decimal("0.5"))
    moved = _monitoring.reference_xml(_with(dataset, "piezometers", installed))
    (item,) = _fidelity(challenge, moved)
    assert (item.id, item.check) == (
        f"monitoring/{name}/sensor-depth",
        "conformance.value-differs",
    )
    # A monitorLocation that names the borehole says where the series is, not how deep.
    xml = challenge.reference().decode()
    unplaced = xml.replace(
        '<monitorLocation xlink:href="#monitor-1.sensor-location"/>',
        '<monitorLocation xlink:href="#boring-1"/>',
    )
    assert unplaced != xml
    (item,) = _fidelity(challenge, unplaced.encode())
    assert (item.id, item.check) == (
        f"monitoring/{name}/sensor-depth",
        "conformance.fact-missing",
    )


def test_a_sensor_depth_given_in_the_monitor_location_itself_is_read() -> None:
    challenge = _challenge_for(EXPORT)
    dataset = challenge.dataset()
    xml = challenge.reference().decode()
    for i, row in enumerate(_records(dataset, "piezometers"), start=1):
        inline = (
            f'<monitorLocation><PointLocation gml:id="monitor-{i}.at" srsName="#boring-{i}-lrs"'
            f' srsDimension="1"><gml:pos>{row["sensor_depth"]}</gml:pos></PointLocation>'
            "</monitorLocation>"
        )
        old = f'<monitorLocation xlink:href="#monitor-{i}.sensor-location"/>'
        assert old in xml
        xml = xml.replace(old, inline)
        # The detector no longer says where it is: the monitorLocation alone does.
        xml = re.sub(
            rf'<detectorLocation>\s*<PointLocation gml:id="monitor-{i}\.sensor-location".*?'
            r"</detectorLocation>",
            "",
            xml,
            flags=re.S,
        )
    assert "<detectorLocation>" not in xml
    result = conformance.grade(challenge, xml.encode())
    assert result.passed, result.to_text()


def test_a_series_without_its_procedure_is_not_read_as_water_levels() -> None:
    # DIGGS leaves a Monitor's procedure optional, and its dictionary defines the three
    # properties for a WaterLevelMonitoring procedure: the suite looks for them there.
    challenge = _challenge_for(EXPORT)
    xml = re.sub(r"<procedure>.*?</procedure>", "", challenge.reference().decode(), flags=re.S)
    xml = re.sub(r'xlink:href="#monitor-(\d)\.sensor-location"', r'xlink:href="#boring-\1"', xml)
    result = conformance.grade(challenge, xml.encode())
    assert result.stage("validation").status == result.stage("core").status == "pass"
    fidelity = result.stage("fidelity")
    values = [f for f in _monitoring.facts(challenge.dataset()) if f.group == "fidelity"]
    assert {i.check for i in fidelity.items} == {"conformance.fact-missing"}
    assert fidelity.failed == sum(f.kind == "value" for f in values) and not result.passed


def test_a_renamed_piezometer_is_missing() -> None:
    challenge = _challenge_for(EXPORT)
    name = next(n for n in challenge.dataset().table("locations").column("location_id") if " " in n)
    xml = challenge.reference().decode().replace(f">{name}<", f">{name.replace(' ', '-')}<")
    result = conformance.grade(challenge, xml.encode())
    assert not result.passed
    assert {i.check for i in result.stage("core").items} == {"conformance.fact-missing"}


def test_values_placed_by_depth_are_not_read_as_readings_at_a_time() -> None:
    # The generic observation places a test's values by depth. A water level is placed
    # by time, so nothing of this procedure is taken from it.
    seen = _monitoring.observe(pydiggs.read_bytes(_challenge_for(EXPORT).reference()))
    monitored = [k for k in seen.readings if k[1] == _monitoring.PROCEDURE]
    assert {k[2] for k in monitored} == {*_monitoring.CHANNELS, _monitoring.SENSOR_DEPTH}
    assert all(len(seen.readings[k]) == 1 for k in monitored if k[2] == _monitoring.SENSOR_DEPTH)


# --- the challenges ---------------------------------------------------------------------


def test_an_export_challenge_is_a_tables_package_that_the_kit_passes(tmp_path: Path) -> None:
    challenge = _challenge_for(EXPORT)
    folder = challenge.write(tmp_path / "challenge")
    descriptor = json.loads((folder / "datapackage.json").read_text())
    names = {resource["name"] for resource in descriptor["resources"]}
    assert {
        "projects",
        "features",
        "measurements",
        "components",
        "links",
        "results_WaterLevelMonitoring",
    } <= names
    assert not (folder / "columns.csv").exists() and not (folder / "extra.csv").exists()
    results = (folder / "results_WaterLevelMonitoring.csv").read_text().splitlines()
    assert any(line.endswith(",") for line in results)  # an empty cell stays empty
    first = _rows(challenge.dataset())[0]["time"]
    assert any(first in line for line in results)  # the time as the tables write it
    assert "Monitor" in (folder / "measurements.csv").read_text()
    readme = (folder / "README.md").read_text()
    assert "compared as an instant" in readme and "monitorLocation" in readme
    made = tables.to_model(tables.read_package(folder))
    result = conformance.grade(challenge, pydiggs.write_bytes(made.model))
    assert result.passed, result.to_text()


def test_the_readme_of_another_class_is_as_it_was() -> None:
    readme = _challenge_for("mwd-export").files["README.md"].decode()
    assert "instant" not in readme and "monitorLocation" not in readme


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
    challenge = _challenge_for(IMPORT, _seed_with(lambda d: _zone(d) != "Z"))
    model = pydiggs.read_bytes(challenge.files["challenge.xml"])
    answers = _right_answers(challenge)
    sheet = challenge.files["answers.csv"].decode()
    series = {m.feature: m for m in model.measurements if m.element == "Monitor"}
    assert int(answers["values.series"]) == len(series)
    named = {f.name: f.key for f in model.features}
    asked = re.search(r"depth to water at (\S+) \(UTC\) in (.+?)\?", sheet)
    assert asked is not None
    when, name = asked.groups()
    (table,) = series[named[name]].results
    assert int(answers["values.readings"]) == table.row_count
    # The question gives the time in UTC; the file writes it with its offset.
    times = list(table.domain.index[0].values)
    assert when.endswith("Z") and when not in times
    (row,) = [i for i, t in enumerate(times) if _monitoring.instant(t) == _monitoring.instant(when)]
    columns = {c.key: c for c in table.columns}
    elevation = columns["water_elev"].values[row]
    assert isinstance(elevation, float)
    assert float(answers["values.water-elevation"]) == pytest.approx(elevation)
    lost = re.search(r"readings of (.+?) is no temperature", sheet)
    assert lost is not None
    (other,) = series[named[lost.group(1)]].results
    warmth = next(c for c in other.columns if c.key == "temperature")
    assert int(answers["values.no-temperature"]) == sum(v is None for v in warmth.values)


@pytest.mark.parametrize(
    ("question", "wrong"),
    [
        ("values.no-temperature", lambda v: "0"),
        ("values.series", lambda v: str(int(v) + 1)),
        ("values.sensor-depth", lambda v: str(float(v) * 1.5)),
        ("values.temperature", lambda v: str(float(v) + 5)),
        ("values.water-depth", lambda v: str(float(v) + 1)),
    ],
)
def test_a_wrong_import_answer_is_named(question: str, wrong: Callable[[str], str]) -> None:
    challenge = _challenge_for(IMPORT)
    answers = _right_answers(challenge)
    answers[question] = wrong(answers[question])
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert not result.passed
    assert [i.id for i in result.stage("values").items] == [question]


def test_a_temperature_is_asked_in_the_other_scale() -> None:
    for length in ("m", "ft"):
        seed = _seed_with(_in(length))
        challenge = _challenge_for(IMPORT, seed)
        stream = _challenge._stream(challenge.info, challenge.seed)
        asked = {q.id: q for q in _registry.questions(challenge.dataset(), stream)}
        question = asked["values.temperature"]
        when = re.search(r"at (\S+) \(UTC\) in (.+)\?", question.text)
        assert when is not None
        row = next(
            r
            for r in _rows(challenge.dataset())
            if r["location_id"] == when.group(2)
            and _monitoring.instant(r["time"]) == _monitoring.instant(when.group(1))
        )
        source = float(row["temperature"])
        if length == "m":
            assert question.unit == "degF"
            assert float(question.expected) == pytest.approx(source * 9 / 5 + 32)
        else:
            assert question.unit == "degC"
            assert float(question.expected) == pytest.approx((source - 32) * 5 / 9)
        assert question.tol >= 0.05 * (9 / 5 if length == "m" else 5 / 9) - 1e-12


# --- acceptance -------------------------------------------------------------------------


def _statuses(result: conformance.Acceptance) -> dict[str, str]:
    return {o.requirement.id: o.status for o in result.outcomes}


def test_the_family_has_its_own_requirements() -> None:
    ids = [r.id for r in conformance.requirements("monitoring")]
    assert ids == [
        "accept.diggs-3",
        "accept.valid",
        "accept.present",
        "accept.located",
        "accept.result",
        "accept.time",
    ]


def test_the_official_piezometer_example_meets_the_content_requirements() -> None:
    # DIGGS allows a time with no zone, and a monitorLocation that is an interval: the
    # owner's check asks for what DIGGS defines, not for what a challenge grades.
    statuses = _statuses(conformance.accept(OFFICIAL, "monitoring"))
    content = ("accept.present", "accept.located", "accept.result", "accept.time")
    assert [statuses[r] for r in content] == ["pass"] * 4


def test_a_file_with_no_series_is_not_a_monitoring_delivery() -> None:
    spt = conformance.new("spt-export", product="D", version="1", seed="demo").reference()
    result = conformance.accept(spt, "monitoring")
    assert not result.accepted
    assert _statuses(result)["accept.present"] == "fail"


def test_a_series_of_other_properties_does_not_report_a_water_level() -> None:
    xml = _challenge_for(EXPORT).reference().decode()
    for key in _monitoring.CHANNELS:
        xml = xml.replace(
            f'properties.xml#{key}">{key}<', 'properties.xml#pore_pressure_u2">pore_pressure_u2<'
        )
    statuses = _statuses(conformance.accept(xml.encode(), "monitoring"))
    assert statuses["accept.result"] == "fail"


def test_a_series_whose_values_are_all_null_marks_reports_nothing() -> None:
    xml = _challenge_for(EXPORT).reference().decode()
    xml = re.sub(
        r"(<dataValues[^>]*>)([^<]*)<",
        lambda m: m.group(1) + " ".join(",," for _ in m.group(2).split()) + "<",
        xml,
    )
    result = conformance.accept(xml.encode(), "monitoring")
    statuses = _statuses(result)
    assert statuses["accept.result"] == "fail"
    time = next(o for o in result.outcomes if o.requirement.id == "accept.time")
    assert time.total == 0  # nothing reported, so nothing to place


def test_a_series_with_fewer_times_than_rows_is_not_placed_in_time() -> None:
    xml = _challenge_for(EXPORT).reference().decode()
    first = _rows(_challenge_for(EXPORT).dataset())[0]["time"]
    assert xml.count(f">{first} ") == 1
    cut = xml.replace(f">{first} ", ">", 1)
    result = conformance.accept(cut.encode(), "monitoring")
    statuses = _statuses(result)
    assert statuses["accept.time"] == "fail" and not result.accepted
    (outcome,) = [o for o in result.outcomes if o.requirement.id == "accept.time"]
    assert outcome.failed == 1 and outcome.total == xml.count("<Monitor ")


def test_docstring_examples_run() -> None:
    failed, attempted = doctest.testmod(_monitoring, optionflags=doctest.ELLIPSIS)
    assert failed == 0 and attempted > 0
