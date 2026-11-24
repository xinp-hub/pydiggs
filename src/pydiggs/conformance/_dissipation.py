"""Pore pressure dissipation tests, a scenario of the suite.

A seeded, synthetic programme of two piezocone soundings with one or two dissipation tests
each: the test depth, the equilibrium pore pressure (u0), u50 and t50 as the field report
gives them, and the dissipation series itself (pore pressure u1 and u2 against the time
since the cone stopped). The data is written for the suite and is not from any project.
Every value is carried as reported: the suite computes no t50, u50 or coefficient.

Traps a correct exporter passes without noticing: elapsed times in minutes (with two
decimals) for about half the seeds and in seconds for the others, t50 in the same unit
as its series; one test stopped before half the excess pore pressure dissipated, so its
t50 is empty (not a zero, and not a time the exporter works out); one reading of the
series with no u2 (a logger dropout) while its u1 is there; and a sounding name with a
space.

DIGGS 3.0.0 shapes the file: each test is a ``Test`` whose procedure is a
``PorePressureDissipationTest``. The test's own result (u0, u50, t50) sits at the test
depth in the sounding's linear referencing system; the series is a ``TemporalResult`` in
the procedure's ``dissipationTimeSeries``, its rows placed by a ``TimeIntervalList``
(elapsed time, ``unit="second"`` or ``"minute"``). The properties are ``pore_pressure_equil``,
``u50``, ``t50``, ``pore_pressure_u1`` and ``pore_pressure_u2`` of the DIGGS property
dictionary.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from typing import Final

from lxml import etree

from pydiggs.conformance import _facts
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._piles import _add, _place, _qname, _records, _units
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project
from pydiggs.model import Model, ResultTable

__all__ = [
    "PROCEDURE",
    "SCENARIO",
    "build",
    "facts",
    "observe",
    "questions",
    "reference_xml",
    "series_key",
]

#: The scenario's name, as a :class:`Dataset` states it.
SCENARIO: Final = "dissipation"
#: The DIGGS procedure of every test of the scenario.
PROCEDURE: Final = "PorePressureDissipationTest"

_DIGGS: Final = "http://diggsml.org/schemas/3"
_NSMAP: Final = {
    None: _DIGGS,
    "gml": "http://www.opengis.net/gml/3.2",
    "glr": "http://www.opengis.net/gml/3.3/lr",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
_SCHEMA_LOCATION: Final = "http://diggsml.org/schemas/3 https://diggsml.org/schemas/3.0.0/Diggs.xsd"
_PROPERTIES: Final = "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"

#: Source unit -> (factor to SI, SI unit), for every unit the tables use.
_SI: Final = {
    "": (1.0, ""),
    "m": (1.0, "m"),
    "ft": (0.3048, "m"),
    "kPa": (1000.0, "Pa"),
    "s": (1.0, "s"),
    "min": (60.0, "s"),
}
#: A time unit of the tables -> the ``gml:TimeUnitType`` a ``TimeIntervalList`` names.
_TIME_UNITS: Final = {"s": "second", "min": "minute"}
#: Elapsed times a series is read at, by unit: every value of one unit has the same
#: number of decimals, so the tolerance of each is far below the step between two.
_TIMES: Final = {
    "s": (
        "0", "5", "10", "15", "20", "30", "45", "60", "90", "120", "180", "240", "300", "420",
        "600", "900", "1200", "1800", "2700", "3600",
    ),
    "min": (
        "0.00", "0.10", "0.25", "0.50", "0.75", "1.00", "1.50", "2.00", "3.00", "4.00",
        "5.00", "7.00", "10.00", "15.00", "20.00", "30.00", "45.00", "60.00",
    ),
}  # fmt: skip
#: Test-table column -> its key in the DIGGS property dictionary.
_SUMMARY_KEYS: Final = (("u0", "pore_pressure_equil"), ("u50", "u50"), ("t50", "t50"))
#: Series-table column -> its key in the DIGGS property dictionary (also the column name).
_SERIES_KEYS: Final = ("pore_pressure_u1", "pore_pressure_u2")


# --- the dataset ------------------------------------------------------------------------


def _series(
    rng: Rng, times: tuple[str, ...], early: bool, unit: str
) -> tuple[list[tuple[str, str, str]], tuple[str, str, str]]:
    """The readings of one test, and its reported (u0, u50, t50).

    The pore pressure falls from its value when the cone stopped towards u0; a test
    stopped ``early`` ends before half the excess has gone, so it reports no t50.
    """
    u0 = Decimal(rng.integer(300, 2500)) / 10
    initial = u0 + Decimal(rng.integer(1500, 8000)) / 10
    ratio = Decimal(rng.integer(120, 180)) / 100  # u1 over u2, above u0
    factor = Decimal(_SI[unit][0])
    count = rng.integer(9, 12) if early else rng.integer(12, len(times))
    last = Decimal(times[count - 1]) * factor
    # The time (in seconds) the excess halves; after the last reading when stopped early.
    half = last * Decimal(rng.integer(20, 40)) / 10 if early else last / rng.integer(4, 12)
    rows: list[tuple[str, str, str]] = []
    for text in times[:count]:
        elapsed = Decimal(text) * factor
        excess = (initial - u0) * half / (half + elapsed)
        noise = Decimal(rng.integer(-5, 5)) / 10
        rows.append((text, _fixed(u0 + excess * ratio + noise, 1), _fixed(u0 + excess, 1)))
    u50 = (initial + u0) / 2
    reached = next((t for t, _u1, u2 in rows if Decimal(u2) <= u50), "")
    return rows, (_fixed(u0, 1), _fixed(u50, 1), "" if early else reached)


def _tests(rng: Rng, names: tuple[str, ...], unit: str, time_unit: str) -> tuple[Table, Table]:
    """One or two tests per sounding; one stopped early, one reading with no u2."""
    # 3 to 25 m in steps of 0.05 m, or 10 to 80 ft in steps of 0.1 ft.
    step, low, high = (Decimal("0.10"), 100, 800) if unit == "ft" else (Decimal("0.05"), 60, 500)
    placed: list[tuple[str, str]] = []
    for name in names:
        here = rng.fork("depths/" + name)
        depths = sorted({here.integer(low, high) for _ in range(here.integer(1, 2))})
        placed.extend((name, _fixed(step * d, 2)) for d in depths)
    early = rng.integer(0, len(placed) - 1)
    dropout = rng.integer(0, len(placed) - 1)
    tests: list[tuple[str, ...]] = []
    readings: list[tuple[str, ...]] = []
    for i, (name, depth) in enumerate(placed):
        rows, summary = _series(
            rng.fork(f"{name}/{depth}"), _TIMES[time_unit], i == early, time_unit
        )
        if i == dropout:
            at = rng.integer(2, len(rows) - 2)
            rows[at] = (rows[at][0], rows[at][1], "")
            if summary[2] == rows[at][0]:  # t50 is read off a reading that is there
                after = next(
                    t for t, _u1, u2 in rows[at + 1 :] if Decimal(u2) <= Decimal(summary[1])
                )
                summary = (summary[0], summary[1], after)
        tests.append((name, depth, *summary))
        readings.extend((name, depth, *row) for row in rows)
    test_table = Table(
        "dissipation_tests",
        (
            Field("location_id", "", "The sounding the test is in"),
            Field("test_depth", unit, "Depth of the cone during the test"),
            Field("u0", "kPa", "Equilibrium pore pressure u0, as reported"),
            Field("u50", "kPa", "Pore pressure at 50 % dissipation (u50), as reported"),
            Field("t50", time_unit, "Time to 50 % dissipation; empty when not reached"),
        ),
        tuple(tests),
    )
    series = Table(
        "dissipation_readings",
        (
            Field("location_id", "", "The sounding"),
            Field("test_depth", unit, "Depth of the test the reading belongs to"),
            Field("elapsed_time", time_unit, "Time since the cone stopped"),
            Field("pore_pressure_u1", "kPa", "Pore pressure u1 (on the cone face)"),
            Field("pore_pressure_u2", "kPa", "Pore pressure u2 (behind the cone); may be empty"),
        ),
        tuple(readings),
    )
    return test_table, series


def build(seed_stream: str, token: str) -> Dataset:
    """The dissipation dataset for ``seed_stream``; the same arguments give the same data.

    Examples:
        >>> data = build("1.0-beta.2|dissipation|demo", "ABC123")
        >>> [t.name for t in data.tables]
        ['project', 'locations', 'dissipation_tests', 'dissipation_readings']
        >>> data == build("1.0-beta.2|dissipation|demo", "ABC123")
        True
    """
    rng = Rng(seed_stream)
    unit = "ft" if rng.integer(0, 1) == 1 else "m"
    time_unit = "min" if rng.integer(0, 1) == 1 else "s"
    number = rng.integer(1, 60)
    names = (f"CPT-{number:02d}", f"CPT {number + 1}A")
    tests, series = _tests(rng.fork("tests"), names, unit, time_unit)
    locations = _locations(rng.fork("loc"), names, "sounding", unit)
    extra = Decimal(5) if unit == "m" else Decimal(15)
    totals = {
        name: _fixed(max(Decimal(r[1]) for r in tests.rows if r[0] == name) + extra, 2)
        for name in names
    }
    fields = (*locations.fields, Field("total_depth", unit, "Final depth of the sounding"))
    located = Table("locations", fields, tuple((*r, totals[r[0]]) for r in locations.rows))
    return Dataset(SCENARIO, token, (_project(rng.fork("proj"), token), located, tests, series))


# --- the answer key ---------------------------------------------------------------------


def _number(text: str, unit: str) -> tuple[float, float]:
    factor = _SI[unit][0]
    return float(Decimal(text)) * factor, _facts._half_last_place(text, factor)


def series_key(ordinal: int, key: str) -> str:
    """The key the ``ordinal``-th test's series values of ``key`` are observed under.

    A sounding's tests are numbered from 1 in depth order: a series is in its
    procedure, which has no depth of its own, so it is placed by its test's rank.
    """
    return f"series {ordinal}:{key}"


def _fact(
    where: tuple[str, str, str], column: str, text: str, unit: str, *, key: str, place: str
) -> Fact:
    """One value of a location; ``where`` is (location, axis as written, axis unit).

    The axis is the test depth for a test's own result and the elapsed time for a
    reading of its series. An empty cell is an ``absent`` fact.
    """
    location, axis_text, axis_unit = where
    axis, axis_tol = _number(axis_text, axis_unit)
    value, tol = _number(text or "0", unit)
    fact = Fact(
        id=f"{SCENARIO}/{location}/{place}/{column}",
        group="fidelity",
        kind="value",
        shown=f"{column} {text} {unit} {place} in {location!r}",
        feature=location,
        procedure=PROCEDURE,
        key=key,
        depth=axis,
        depth_tol=axis_tol,
        value=value,
        tol=tol,
        si_unit=_SI[unit][1],
    )
    if text:
        return fact
    return replace(
        fact,
        kind="absent",
        shown=f"no {column} {place} in {location!r} (the source leaves it empty)",
    )


def _ordinals(dataset: Dataset) -> dict[tuple[str, str], int]:
    """(sounding, test depth) -> the test's rank by depth in its sounding, from 1."""
    out: dict[tuple[str, str], int] = {}
    tests = dataset.table("dissipation_tests")
    for name, depth in sorted(
        {(r[0], r[1]) for r in tests.rows}, key=lambda r: (r[0], Decimal(r[1]))
    ):
        out[(name, depth)] = 1 + sum(1 for n, _ in out if n == name)
    return out


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of a dissipation ``dataset``, from its tables alone.

    Core: the project's name, every sounding and its depth. Fidelity: each test's u0,
    u50 and t50 at its depth, and each reading of each series (u1 and u2) at its
    elapsed time, in the series of the test of that rank in its sounding. An empty cell
    (a t50 not reached, a u2 the logger lost) is an ``absent`` fact.

    Examples:
        >>> key = facts(build("1.0-beta.2|dissipation|demo", "ABC123"))
        >>> key[0].id, sum(f.kind == "absent" for f in key)
        ('core/project', 2)
    """
    out = _facts.core_facts(dataset)
    tests = dataset.table("dissipation_tests")
    units = _units(tests)
    for row in _records(tests):
        where = (row["location_id"], row["test_depth"], units["test_depth"])
        place = f"at {row['test_depth']} {units['test_depth']}"
        out.extend(
            _fact(where, column, row[column], units[column], key=key, place=place)
            for column, key in _SUMMARY_KEYS
        )
    ordinals = _ordinals(dataset)
    readings = dataset.table("dissipation_readings")
    units = _units(readings)
    for row in _records(readings):
        name, depth, elapsed = row["location_id"], row["test_depth"], row["elapsed_time"]
        rank = ordinals[(name, depth)]
        place = f"at {elapsed} {units['elapsed_time']} of the test at {depth} {units['test_depth']}"
        out.extend(
            _fact(
                (name, elapsed, units["elapsed_time"]),
                column,
                row[column],
                units[column],
                key=series_key(rank, column),
                place=place,
            )
            for column in _SERIES_KEYS
        )
    return tuple(out)


# --- the import questions ---------------------------------------------------------------


def _convert(text: str, unit: str, asked: str) -> tuple[str, float]:
    """A source value in the unit a question asks for, and the tolerance of an answer."""
    value = float(Decimal(text)) * _SI[unit][0] / _SI[asked][0]
    half = _facts._half_last_place(text, _SI[unit][0]) / _SI[asked][0]
    return _facts._fmt(value), max(half, abs(value) * 0.005)


def _number_question(qid: str, text: str, source: tuple[str, str], asked: str) -> Question:
    expected, tol = _convert(source[0], source[1], asked)
    return Question(qid, qid.partition(".")[0], "number", text, asked, expected, tol)


def _of_test(test: dict[str, str], unit: str) -> str:
    return f"of the dissipation test at {test['test_depth']} {unit} in {test['location_id']}"


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for a dissipation ``dataset``.

    Examples:
        >>> asked = questions(build("s", "ABC123"), "s")
        >>> asked[0].id, asked[-1].id
        ('core.project-name', 'robustness.defect')
    """
    rng = Rng(stream + "/questions")
    out = _facts.core_questions(dataset, rng)
    tests = _records(dataset.table("dissipation_tests"))
    readings = _records(dataset.table("dissipation_readings"))
    units = _units(dataset.table("dissipation_readings"))
    depth_unit, time_unit = units["test_depth"], units["elapsed_time"]
    other_time = "s" if time_unit == "min" else "min"
    out.append(
        Question(
            "values.tests",
            "values",
            "count",
            "How many pore pressure dissipation tests does the file hold?",
            "",
            str(len(tests)),
        )
    )
    reached = rng.choice([t for t in tests if t["t50"]])
    at = _of_test(reached, depth_unit)
    out.append(
        _number_question(
            "values.t50", f"What is t50 {at}?", (reached["t50"], time_unit), other_time
        )
    )
    out.append(
        _number_question(
            "values.u0",
            f"What is the equilibrium pore pressure u0 {at}?",
            (reached["u0"], "kPa"),
            "kPa",
        )
    )
    chosen = rng.choice(tests)
    mine = [
        r
        for r in readings
        if (r["location_id"], r["test_depth"]) == (chosen["location_id"], chosen["test_depth"])
    ]
    at = _of_test(chosen, depth_unit)
    out.append(
        Question(
            "values.readings",
            "values",
            "count",
            f"How many readings does the series {at} hold?",
            "",
            str(len(mine)),
        )
    )
    reading = rng.choice([r for r in mine if r["pore_pressure_u2"]])
    out.append(
        _number_question(
            "values.u2",
            f"What is the pore pressure u2 {reading['elapsed_time']} {time_unit} after the cone"
            f" stopped, in the series {at}?",
            (reading["pore_pressure_u2"], "kPa"),
            "kPa",
        )
    )
    early = next(t for t in tests if not t["t50"])
    out.append(
        Question(
            "values.not-reached",
            "values",
            "text",
            "One test was stopped before 50 % dissipation and gives no t50. In which"
            " sounding is it?",
            "",
            early["location_id"],
        )
    )
    out.append(_facts.defect_question(rng))
    return tuple(out)


# --- the reference file -----------------------------------------------------------------


def _write_sounding(
    root: etree._Element, ident: str, row: dict[str, str], unit: str, project: str
) -> None:
    sounding = _add(_add(root, "samplingFeature"), "Sounding", gml__id=ident)
    _add(sounding, "gml:name", row["location_id"])
    _add(sounding, "projectRef", xlink__href=f"#{project}")
    _place(sounding, ident, row, unit)
    _add(sounding, "totalMeasuredDepth", row["total_depth"], uom=unit)


def _result_set(
    parent: etree._Element, columns: tuple[tuple[str, str, str], ...], rows: list[tuple[str, ...]]
) -> None:
    """A ``ResultSet``: one property per (name, dictionary key, unit), then the rows."""
    result = _add(_add(parent, "results"), "ResultSet")
    properties = _add(_add(_add(result, "parameters"), "PropertyParameters"), "properties")
    for index, (name, key, uom) in enumerate(columns, start=1):
        prop = _add(properties, "Property", index=str(index))
        _add(prop, "propertyName", name)
        _add(prop, "typeData", "double")
        _add(prop, "propertyClass", key, codeSpace=f"{_PROPERTIES}#{key}")
        _add(prop, "uom", uom)
    _add(result, "dataValues", " ".join(",".join(row) for row in rows), cs=",", ts=" ")


def _write_test(
    root: etree._Element,
    ident: str,
    test: dict[str, str],
    readings: list[dict[str, str]],
    context: tuple[str, str, dict[str, str]],
) -> None:
    project, sounding, units = context
    measurement = _add(_add(root, "measurement"), "Test", gml__id=ident)
    _add(measurement, "projectRef", xlink__href=f"#{project}")
    _add(measurement, "samplingFeatureRef", xlink__href=f"#{sounding}")
    outcome = _add(_add(measurement, "outcome"), "TestResult", gml__id=f"{ident}.result")
    point = _add(_add(outcome, "location"), "PointLocation", gml__id=f"{ident}.location")
    point.set("srsName", f"#{sounding}-lrs")
    point.set("srsDimension", "1")
    _add(point, "gml:pos", test["test_depth"])
    summary = tuple((column, key, units[column]) for column, key in _SUMMARY_KEYS)
    _result_set(outcome, summary, [tuple(test[c] for c, _ in _SUMMARY_KEYS)])
    procedure = _add(_add(measurement, "procedure"), PROCEDURE, gml__id=f"{ident}.procedure")
    temporal = _add(
        _add(procedure, "dissipationTimeSeries"), "TemporalResult", gml__id=f"{ident}.series"
    )
    times = _add(_add(temporal, "timeDomain"), "TimeIntervalList", gml__id=f"{ident}.times")
    times.set("unit", _TIME_UNITS[units["elapsed_time"]])
    _add(times, "timeIntervalList", " ".join(r["elapsed_time"] for r in readings))
    columns = tuple((key, key, "kPa") for key in _SERIES_KEYS)
    _result_set(temporal, columns, [tuple(r[key] for key in _SERIES_KEYS) for r in readings])


def reference_xml(dataset: Dataset) -> bytes:
    """A dissipation ``dataset`` as a valid DIGGS 3.0.0 document, values in their units.

    Each sounding is a ``Sounding`` with its own linear referencing system; each test is
    a ``Test`` whose result (u0, u50, t50) sits at the test depth and whose
    ``PorePressureDissipationTest`` procedure holds the series as a ``TemporalResult``.
    The same dataset always gives the same bytes.

    Examples:
        >>> import pydiggs
        >>> xml = reference_xml(build("1.0-beta.2|dissipation|demo", "ABC123"))
        >>> pydiggs.validate_bytes(xml).ok
        True
    """
    project_row = dataset.table("project").rows[0]
    project = f"project-{project_row[0]}"
    root = etree.Element(_qname("Diggs"), nsmap=_NSMAP)
    root.set(_qname("xsi:schemaLocation"), _SCHEMA_LOCATION)
    root.set(_qname("gml:id"), "Diggs")
    info = _add(_add(root, "documentInformation"), "DocumentInformation")
    _add(info, "creationDate", indeterminatePosition="unknown")
    holder = _add(_add(root, "project"), "Project", gml__id=project)
    _add(holder, "gml:name", project_row[0])
    _add(holder, "gml:name", project_row[1])
    locations = dataset.table("locations")
    unit = _units(locations)["total_depth"]
    idents: dict[str, str] = {}
    for i, row in enumerate(_records(locations), start=1):
        idents[row["location_id"]] = f"sounding-{i}"
        _write_sounding(root, f"sounding-{i}", row, unit, project)
    units = {
        **_units(dataset.table("dissipation_tests")),
        **_units(dataset.table("dissipation_readings")),
    }
    readings = _records(dataset.table("dissipation_readings"))
    for i, test in enumerate(_records(dataset.table("dissipation_tests")), start=1):
        mine = [
            r
            for r in readings
            if (r["location_id"], r["test_depth"]) == (test["location_id"], test["test_depth"])
        ]
        context = (project, idents[test["location_id"]], units)
        _write_test(root, f"dissipation-{i}", test, mine, context)
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))


# --- what a file is observed to hold ----------------------------------------------------


def _times(table: ResultTable) -> list[float | None] | None:
    """The elapsed time of every row of a series in seconds, or None if it gives none."""
    if table.domain.shape != "temporal":
        return None
    index = next((c for c in table.domain.index if c.name == "elapsed_time"), None)
    if index is None or index.si_factor is None or index.si_uom != "s":
        return None
    offset = index.si_offset or 0.0
    numbers = [_facts._number_of(v) for v in index.values]
    return [None if n is None else n * index.si_factor + offset for n in numbers]


def _series_readings(table: ResultTable) -> Iterator[tuple[str, float, float, str]]:
    """Every numeric value of a series: (key, elapsed seconds, SI value, SI unit).

    A cell that is empty, not a number, or the column's ``nullValue`` is not a reading.
    """
    times = _times(table)
    if times is None:
        return
    for column in table.columns:
        how = _facts._column_si(column)
        if how is None:
            continue
        factor, offset, unit = how
        key = column.key or column.name
        null = _facts._null(column)
        for elapsed, cell in zip(times, column.values, strict=False):
            value = None if isinstance(cell, bool) else _facts._cell_number(column, cell, null)
            if value is not None and elapsed is not None:
                yield key, elapsed, value * factor + offset, unit


def observe(model: Model) -> Observed:
    """What a DIGGS file holds of dissipation tests, for :func:`_facts.match`.

    Everything the generic observation reads, which includes each test's own result at
    its depth, plus every series: the values of a ``TemporalResult`` whose rows have an
    elapsed time, keyed ``(sounding, "PorePressureDissipationTest", "series <n>:<key>")``
    at that time in seconds, where ``n`` is the test's rank by depth among the sounding's
    dissipation tests (a test whose depth the file does not give comes last, in file
    order). A series in a unit of time the reader cannot convert is not read.

    Examples:
        >>> import pydiggs
        >>> data = build("1.0-beta.2|dissipation|demo", "ABC123")
        >>> seen = observe(pydiggs.read_bytes(reference_xml(data)))
        >>> all(_facts.match(f, seen).status == "found" for f in facts(data))
        True
    """
    seen = _facts.observe(model, nulls=True)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for where, found in seen.readings.items():
        readings[where].extend(found)
    ranked: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for order, measurement in enumerate(model.measurements):
        if measurement.procedure is None or measurement.procedure.type != PROCEDURE:
            continue
        feature = _facts._feature_key(model, measurement)
        if feature is None:
            continue
        depths = (
            d
            for table in measurement.results
            if table.domain.shape != "temporal"
            for d in _facts._depths(model, table)
            if d is not None
        )
        ranked[feature].append((next(depths, math.inf), order))
    measurements = model.measurements
    for feature, tests in ranked.items():
        for rank, (_depth, order) in enumerate(sorted(tests), start=1):
            for table in measurements[order].results:
                for key, elapsed, value, unit in _series_readings(table):
                    readings[(feature, PROCEDURE, series_key(rank, key))].append(
                        (elapsed, value, unit)
                    )
    for found in readings.values():
        found.sort()
    return Observed(seen.project_names, seen.named, seen.totals, dict(readings))
