"""Monitoring: water level series of piezometers and data loggers, a scenario of the suite.

A seeded, synthetic set of two or three piezometers, each a borehole with one sensor at a
depth and one series the data logger recorded: for every reading its time, the depth to
water below the reference point, the water elevation and the water temperature. The data
is written for the suite and is not from any project. Every value is carried as the logger
reports it: the suite works out no level, head or pressure.

Traps a correct exporter passes without noticing: a piezometer name with a space; depths
in feet with temperatures in degrees Fahrenheit for about half the seeds; times written
with an offset from UTC for about half the seeds and in UTC (``Z``) for the others (a time
is an instant, so dropping or changing the offset moves every reading); one reading where
the water stands at the reference point, a real zero (a zero is not an empty cell); a few
readings in one piezometer where the temperature channel recorded nothing (an empty cell
is not a zero); and one stretch in each series where the logger was stopped (an exporter
must not fill it with readings that were never taken).

DIGGS 3.0.0 shapes the file: each series is a ``Monitor`` on its borehole. A ``Monitor``
must have a ``monitorLocation`` and an ``outcome``, which is a ``TemporalResult``: a
``timeDomain`` (here a ``TimePositionList``, one ``xs:dateTime`` per row) and the
``results``. Its procedure is a ``WaterLevelMonitoring``, which must name at least one
``ReferencePoint`` (the datum the levels are measured from, with the time it applies
from). The DIGGS property dictionary defines three properties for that procedure, and the
scenario uses all three: ``water_depth``, ``water_elev`` and ``temperature``. The sensor
is a ``Sensor`` of the procedure whose ``Detector`` gives the sensor depth in the
borehole's linear referencing system; the ``monitorLocation`` refers to it, as the
official DIGGS piezometer example refers to the place its series applies to.
"""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from typing import Final

from lxml import etree

from pydiggs.conformance import _facts
from pydiggs.conformance._dissipation import _result_set
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._piles import _add, _place, _qname, _records, _units
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project
from pydiggs.model import Component, ExtraNode, Measurement, Model, ResultTable

__all__ = [
    "CHANNELS",
    "ELEMENT",
    "PROCEDURE",
    "SCENARIO",
    "SENSOR_DEPTH",
    "build",
    "facts",
    "instant",
    "observe",
    "questions",
    "reference_xml",
]

#: The scenario's name, as a :class:`Dataset` states it.
SCENARIO: Final = "monitoring"
#: The DIGGS measurement element of every series of the scenario.
ELEMENT: Final = "Monitor"
#: The DIGGS procedure of every series, and what its readings are kept under in
#: :class:`Observed`.
PROCEDURE: Final = "WaterLevelMonitoring"
#: The key a series' sensor depth is observed under (it is not a result property).
SENSOR_DEPTH: Final = "@sensorDepth"

_DIGGS: Final = "http://diggsml.org/schemas/3"
_NSMAP: Final = {
    None: _DIGGS,
    "gml": "http://www.opengis.net/gml/3.2",
    "glr": "http://www.opengis.net/gml/3.3/lr",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
_SCHEMA_LOCATION: Final = "http://diggsml.org/schemas/3 https://diggsml.org/schemas/3.0.0/Diggs.xsd"

#: Readings column -> what it holds. Each column's name is its key in the DIGGS property
#: dictionary, which defines exactly these three for ``WaterLevelMonitoring``.
CHANNELS: Final = {
    "water_depth": "Depth to water below the reference point (the ground level)",
    "water_elev": "Water elevation",
    "temperature": "Water temperature at the sensor; empty when the channel recorded nothing",
}

#: Source unit -> (factor, offset, SI unit): SI = value * factor + offset. Exact by
#: definition (the international foot; the Celsius and Fahrenheit scales).
_SI: Final = {
    "": (1.0, 0.0, ""),
    "m": (1.0, 0.0, "m"),
    "ft": (0.3048, 0.0, "m"),
    "degC": (1.0, 273.15, "K"),
    "degF": (5 / 9, 459.67 * 5 / 9, "K"),
}
#: Source unit -> the other unit a question asks for it in.
_ASKED: Final = {"m": "ft", "ft": "m", "degC": "degF", "degF": "degC"}

#: Instants are counted in seconds from here. A recent origin keeps the numbers small, so
#: the half second a time may differ by stays far above the rounding of the comparison.
_EPOCH: Final = datetime.date(2020, 1, 1).toordinal()
_DAY: Final = 86_400
#: Longest text looked at as a time. An ``xs:dateTime`` with a zone is 25 characters and
#: a fraction of a second adds a few more.
_MAX_TIME_CHARS: Final = 64
_DATE_TIME: Final = re.compile(
    r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)", re.ASCII
)
_HOURS: Final = 24
_SIXTY: Final = 60
#: The widest zone offset ``xs:dateTime`` allows, in minutes (14:00).
_MAX_ZONE_MINUTES: Final = 14 * 60
#: The offsets from UTC a dataset's times are written with; ``Z`` for about half the seeds.
_ZONES: Final = ("Z", "Z", "Z", "Z", "-05:00", "-08:00", "+01:00", "+05:30")
#: Minutes between two readings of a series.
_INTERVALS: Final = (15, 30, 60)


def instant(text: object) -> float | None:
    """The instant an ``xs:dateTime`` names, in seconds since 2020-01-01T00:00:00Z.

    Only a date and time of day with a zone is an instant: ``Z`` or an offset from UTC.
    Two texts that name the same instant give the same number, whatever offset each is
    written with. A time with no zone is local time somewhere and names no instant, so
    it gives None, as does anything that is not an ``xs:dateTime``.

    Examples:
        >>> instant("2024-03-05T09:30:00-05:00") == instant("2024-03-05T14:30:00Z")
        True
        >>> instant("2020-01-01T00:00:00.5Z"), instant("2019-12-31T24:00:00Z")
        (0.5, 0.0)
        >>> instant("2024-03-05T09:30:00"), instant("2024-02-30T00:00:00Z"), instant(5)
        (None, None, None)
    """
    if not isinstance(text, str) or len(text) > _MAX_TIME_CHARS:
        return None
    match = _DATE_TIME.fullmatch(text.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = float("0" + match.group(7)) if match.group(7) else 0.0
    try:
        days = datetime.date(year, month, day).toordinal() - _EPOCH
    except ValueError:
        return None
    # 24:00:00 is the end of the day, and nothing else of hour 24 is a time.
    end_of_day = (hour, minute, second, fraction) == (_HOURS, 0, 0, 0.0)
    if (hour >= _HOURS and not end_of_day) or minute >= _SIXTY or second >= _SIXTY:
        return None
    shift = _zone_minutes(match.group(8))
    if shift is None:
        return None
    return days * _DAY + hour * 3600 + minute * 60 + second + fraction - shift * 60


# --- the dataset ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _System:
    """The units of one dataset and the ranges its numbers are drawn from."""

    length: str
    temperature: str
    #: Sensor depth: step, and the lowest and highest count of steps.
    sensor: tuple[Decimal, int, int]
    #: The hole runs this many length units below the sensor, lowest and highest.
    sump: tuple[int, int]
    #: Water depth, in hundredths: shallowest start, and the most one reading moves.
    water: tuple[int, int]
    #: Temperature, in tenths: lowest, highest, and the most one reading moves.
    warmth: tuple[int, int, int]


_METRIC: Final = _System("m", "degC", (Decimal("0.5"), 12, 50), (1, 3), (60, 4), (80, 160, 2))
_IMPERIAL: Final = _System("ft", "degF", (Decimal(1), 20, 80), (3, 10), (200, 12), (460, 610, 3))


def _zone_minutes(zone: str) -> int | None:
    """Minutes east of UTC of a zone (``Z`` or ``+hh:mm``); None when it is not one."""
    if zone == "Z":
        return 0
    hours, minutes = int(zone[1:3]), int(zone[4:6])
    if minutes >= _SIXTY or hours * 60 + minutes > _MAX_ZONE_MINUTES:
        return None
    return -(hours * 60 + minutes) if zone[0] == "-" else hours * 60 + minutes


def _stamp(minutes: int, zone: str) -> str:
    """The time ``minutes`` after 2020-01-01T00:00Z, written with the offset ``zone``."""
    days, of_day = divmod(minutes + (_zone_minutes(zone) or 0), 24 * 60)
    day = datetime.date.fromordinal(_EPOCH + days)
    return f"{day.isoformat()}T{of_day // 60:02d}:{of_day % 60:02d}:00{zone}"


@dataclass(frozen=True, slots=True)
class _Plan:
    """What happens in one series: where the logger stops, and which cells are special."""

    count: int
    gap_at: int
    lost: frozenset[int]
    zero_at: int | None


#: A temperature dropout starts more than this many readings from where the logger stops.
_CLEAR: Final = 4


def _plan(rng: Rng, dropout: bool, zero: bool) -> _Plan:
    count = rng.integer(24, 48)
    gap_at = rng.integer(6, count - 6)
    lost: frozenset[int] = frozenset()
    if dropout:
        start = rng.choice([i for i in range(2, count - 5) if abs(i - gap_at) > _CLEAR])
        lost = frozenset(range(start, start + rng.integer(2, 4)))
    zero_at = rng.choice([i for i in range(1, count) if i != gap_at]) if zero else None
    return _Plan(count, gap_at, lost, zero_at)


def _series(
    rng: Rng, system: _System, context: tuple[str, str, Decimal, Decimal], plan: _Plan
) -> tuple[list[tuple[str, ...]], int]:
    """The readings of one piezometer, and the minutes between two of them.

    ``context`` is (name, zone, ground level, sensor depth). The logger stops once: the
    readings of two to six intervals are not there. The water stays above the sensor.
    """
    name, zone, ground, sensor = context
    interval = rng.choice(_INTERVALS)
    # From 2023 to 2025, starting on a quarter hour.
    minutes = (rng.integer(3 * 365, 6 * 365) * 24 * 60) + rng.integer(0, 95) * 15
    shallowest, wander = system.water
    deepest = int(sensor * 100) - shallowest
    level = rng.integer(shallowest, deepest)
    low, high, drift = system.warmth
    warmth = rng.integer(low, high)
    rows: list[tuple[str, ...]] = []
    for i in range(plan.count):
        if i:
            minutes += interval * (rng.integer(3, 7) if i == plan.gap_at else 1)
        level = min(deepest, max(shallowest, level + rng.integer(-wander, wander)))
        warmth = min(high, max(low, warmth + rng.integer(-drift, drift)))
        depth = Decimal(0) if i == plan.zero_at else Decimal(level) / 100
        rows.append(
            (
                name,
                _stamp(minutes, zone),
                _fixed(depth, 2),
                _fixed(ground - depth, 2),
                "" if i in plan.lost else _fixed(Decimal(warmth) / 10, 1),
            )
        )
    return rows, interval


def build(seed_stream: str, token: str) -> Dataset:
    """The monitoring dataset for ``seed_stream``; the same arguments give the same data.

    Examples:
        >>> data = build("1.0-beta.3|monitoring|demo", "ABC123")
        >>> [t.name for t in data.tables]
        ['project', 'locations', 'piezometers', 'readings']
        >>> data == build("1.0-beta.3|monitoring|demo", "ABC123")
        True
    """
    rng = Rng(seed_stream)
    system = _IMPERIAL if rng.integer(0, 1) == 1 else _METRIC
    zone = rng.choice(_ZONES)
    number = rng.integer(1, 80)
    names = (f"PZ-{number:02d}", f"PZ {number + 1}A", f"PZ-{number + 2}")[: rng.integer(2, 3)]
    dropout, zero = rng.choice(names), rng.choice(names)
    located = _locations(rng.fork("loc"), names, "borehole", system.length)
    grounds = dict(zip(names, located.column("ground_level"), strict=True))
    step, fewest, most = system.sensor
    installed: list[tuple[str, ...]] = []
    readings: list[tuple[str, ...]] = []
    totals: dict[str, str] = {}
    for name in names:
        here = rng.fork("series/" + name)
        sensor = step * here.integer(fewest, most)
        totals[name] = _fixed(sensor + here.integer(*system.sump), 2)
        plan = _plan(here.fork("plan"), name == dropout, name == zero)
        context = (name, zone, Decimal(grounds[name]), sensor)
        rows, interval = _series(here, system, context, plan)
        readings.extend(rows)
        installed.append(
            (
                name,
                f"VW-{here.integer(10_000, 99_999)}",
                _fixed(sensor, 2),
                str(interval),
                # The reference point is the datum from the day before the first reading.
                _stamp((_minutes(rows[0][1]) // 1440 - 1) * 1440, "Z"),
            )
        )
    fields = (*located.fields, Field("total_depth", system.length, "Final depth of the hole"))
    locations = Table(located.name, fields, tuple((*r, totals[r[0]]) for r in located.rows))
    piezometers = Table(
        "piezometers",
        (
            Field("location_id", "", "The borehole the piezometer is installed in"),
            Field("sensor_id", "", "The sensor's name"),
            Field("sensor_depth", system.length, "Depth of the sensor in the borehole"),
            Field("reading_interval", "min", "Time between two readings while the logger runs"),
            Field(
                "datum_from",
                "",
                "Since when the levels are measured from the reference point, the ground"
                " level at the location (ISO 8601)",
            ),
        ),
        tuple(installed),
    )
    data = Table(
        "readings",
        (
            Field("location_id", "", "The piezometer's borehole"),
            Field("time", "", "When the reading was taken (ISO 8601, with its offset from UTC)"),
            Field("water_depth", system.length, CHANNELS["water_depth"]),
            Field("water_elev", system.length, CHANNELS["water_elev"]),
            Field("temperature", system.temperature, CHANNELS["temperature"]),
        ),
        tuple(readings),
    )
    project = _project(rng.fork("proj"), token)
    return Dataset(SCENARIO, token, (project, locations, piezometers, data))


def _minutes(stamp: str) -> int:
    """Whole minutes from 2020-01-01T00:00Z to a time of the tables."""
    seconds = instant(stamp)
    if seconds is None:  # the tables hold only times this module wrote
        raise ValueError(f"{stamp!r} is not a time of the tables")
    return int(seconds // 60)


def _zone_of(stamp: str) -> str:
    return "Z" if stamp.endswith("Z") else stamp[-6:]


# --- the answer key ---------------------------------------------------------------------


def _number(text: str, unit: str) -> tuple[float, float]:
    factor, offset, _si = _SI[unit]
    return float(Decimal(text)) * factor + offset, _facts._half_last_place(text, factor)


def _reading(location: str, stamp: str, column: str, text: str, unit: str) -> Fact:
    """One cell of a reading; an empty cell is an ``absent`` fact (see :func:`_none`)."""
    if not text:
        return _none(location, stamp, column, unit, "the source leaves it empty")
    value, tol = _number(text, unit)
    return Fact(
        id=f"{SCENARIO}/{location}/{stamp}/{column}",
        group="fidelity",
        kind="value",
        shown=f"{column} {text} {unit} at {stamp} in {location!r}",
        feature=location,
        procedure=PROCEDURE,
        key=column,
        depth=float(_minutes(stamp) * 60),
        depth_tol=0.5,  # the tables give a time to the second
        value=value,
        tol=tol,
        si_unit=_SI[unit][2],
    )


def _none(location: str, stamp: str, column: str, unit: str, why: str) -> Fact:
    """A value the source does not give at a time: the file must give none either."""
    return replace(
        _reading(location, stamp, column, "0", unit),
        kind="absent",
        shown=f"no {column} at {stamp} in {location!r} ({why})",
    )


def _stopped(rows: list[dict[str, str]], intervals: dict[str, int]) -> Iterator[tuple[str, str]]:
    """(piezometer, time) of every reading a logger that never stopped would have taken."""
    for before, after in pairwise(rows):
        name = before["location_id"]
        if name != after["location_id"]:
            continue
        zone = _zone_of(before["time"])
        minutes = _minutes(before["time"]) + intervals[name]
        while minutes < _minutes(after["time"]):
            yield name, _stamp(minutes, zone)
            minutes += intervals[name]


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of a monitoring ``dataset``, from its tables alone.

    Core: the project's name, each borehole and its depth. Fidelity: each piezometer's
    sensor depth, and each reading's water depth, water elevation and temperature at
    its time. An empty cell (no temperature) is an ``absent`` fact; so is every channel
    at each time the logger was stopped: a file that gives a reading there invents it.
    Nothing is known of the time before a series' first reading or after its last, so
    a reading added there is not looked for.

    Examples:
        >>> key = facts(build("1.0-beta.3|monitoring|demo", "ABC123"))
        >>> key[0].id, key[0].group
        ('core/project', 'core')
        >>> sorted({f.kind for f in key if f.group == "fidelity"})
        ['absent', 'value']
    """
    out = _facts.core_facts(dataset)
    installed = dataset.table("piezometers")
    unit = _units(installed)["sensor_depth"]
    intervals: dict[str, int] = {}
    for row in _records(installed):
        name, text = row["location_id"], row["sensor_depth"]
        intervals[name] = int(row["reading_interval"])
        value, tol = _number(text, unit)
        out.append(
            Fact(
                id=f"{SCENARIO}/{name}/sensor-depth",
                group="fidelity",
                kind="value",
                shown=f"sensor at {text} {unit} in {name!r}",
                feature=name,
                procedure=PROCEDURE,
                key=SENSOR_DEPTH,
                value=value,
                tol=tol,
                si_unit="m",
            )
        )
    table = dataset.table("readings")
    units = _units(table)
    rows = _records(table)
    for row in rows:
        out.extend(
            _reading(row["location_id"], row["time"], column, row[column], units[column])
            for column in CHANNELS
        )
    for name, stamp in _stopped(rows, intervals):
        out.extend(
            _none(name, stamp, column, units[column], "the logger was stopped")
            for column in CHANNELS
        )
    return tuple(out)


# --- the import questions ---------------------------------------------------------------


def _number_question(qid: str, text: str, source: tuple[str, str], asked: str) -> Question:
    """A question whose answer is a source value, in the unit ``asked``."""
    written, unit = source
    factor, offset, _si = _SI[unit]
    to_factor, to_offset, _si = _SI[asked]
    value = (float(Decimal(written)) * factor + offset - to_offset) / to_factor
    half = _facts._half_last_place(written, factor) / to_factor
    # An answer read off a screen is rounded: allow that, but never more than 0.5 %.
    tol = max(half, abs(value) * 0.005)
    return Question(qid, qid.partition(".")[0], "number", text, asked, _facts._fmt(value), tol)


def _count_question(qid: str, text: str, expected: int) -> Question:
    return Question(qid, qid.partition(".")[0], "count", text, "", str(expected), 0.0)


def _utc(stamp: str) -> str:
    """A time of the tables as the same instant in UTC."""
    return _stamp(_minutes(stamp), "Z")


def _value_questions(dataset: Dataset, rng: Rng) -> list[Question]:
    installed = _records(dataset.table("piezometers"))
    table = dataset.table("readings")
    units = _units(table)
    length, warmth = units["water_depth"], units["temperature"]
    rows = _records(table)
    chosen = rng.choice(installed)
    name = chosen["location_id"]
    mine = [r for r in rows if r["location_id"] == name]
    row = rng.choice([r for r in mine if r["temperature"]])
    # The time is asked in UTC: a file that writes its times with an offset is read
    # right only by software that takes the offset into account.
    when = f"at {_utc(row['time'])} (UTC) in {name}"
    out = [
        _count_question(
            "values.series", "How many monitoring series does the file hold?", len(installed)
        ),
        _count_question(
            "values.readings", f"How many readings does the series of {name} hold?", len(mine)
        ),
        _number_question(
            "values.sensor-depth",
            f"At what depth is the sensor of {name}?",
            (chosen["sensor_depth"], length),
            _ASKED[length],
        ),
        _number_question(
            "values.water-depth",
            f"What is the depth to water {when}?",
            (row["water_depth"], length),
            _ASKED[length],
        ),
        _number_question(
            "values.water-elevation",
            f"What is the water elevation {when}?",
            (row["water_elev"], length),
            length,
        ),
        _number_question(
            "values.temperature",
            f"What is the temperature {when}?",
            (row["temperature"], warmth),
            _ASKED[warmth],
        ),
    ]
    holed = next(r["location_id"] for r in rows if not r["temperature"])
    out.append(
        _count_question(
            "values.no-temperature",
            f"At how many readings of {holed} is no temperature recorded?",
            sum(1 for r in rows if r["location_id"] == holed and not r["temperature"]),
        )
    )
    interval = int(chosen["reading_interval"])
    resumed = next(
        after
        for before, after in pairwise(mine)
        if _minutes(after["time"]) - _minutes(before["time"]) > interval
    )
    out.append(
        _number_question(
            "values.resumed",
            f"The logger of {name} takes a reading every {interval} minutes and was stopped"
            " once (readings are missing). What is the depth to water at the first reading"
            " after it started again?",
            (resumed["water_depth"], length),
            length,
        )
    )
    return out


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for a monitoring ``dataset``.

    Which piezometer and reading are asked about, and which defect the second file has,
    depend on ``stream``; the last question is the defect, as in every scenario.

    Examples:
        >>> asked = questions(build("s", "ABC123"), "s")
        >>> asked[0].id, asked[-1].id
        ('core.project-name', 'robustness.defect')
    """
    rng = Rng(stream + "/questions")
    out = _facts.core_questions(dataset, rng)
    out.extend(_value_questions(dataset, rng))
    out.append(_facts.defect_question(rng))
    return tuple(out)


# --- the reference file -----------------------------------------------------------------


def _write_series(
    root: etree._Element,
    ident: str,
    installed: dict[str, str],
    readings: list[dict[str, str]],
    context: tuple[str, str, dict[str, str]],
) -> None:
    """One ``Monitor``: where its sensor is, its readings by time, and its procedure."""
    project, boring, units = context
    monitor = _add(_add(root, "measurement"), ELEMENT, gml__id=ident)
    _add(monitor, "gml:name", f"{installed['location_id']} water levels")
    _add(monitor, "projectRef", xlink__href=f"#{project}")
    _add(monitor, "samplingFeatureRef", xlink__href=f"#{boring}")
    _add(monitor, "monitorLocation", xlink__href=f"#{ident}.sensor-location")
    result = _add(_add(monitor, "outcome"), "TemporalResult", gml__id=f"{ident}.result")
    times = _add(_add(result, "timeDomain"), "TimePositionList", gml__id=f"{ident}.times")
    _add(times, "timePositionList", " ".join(r["time"] for r in readings))
    columns = tuple((column, column, units[column]) for column in CHANNELS)
    _result_set(result, columns, [tuple(r[column] for column in CHANNELS) for r in readings])
    procedure = _add(_add(monitor, "procedure"), PROCEDURE, gml__id=f"{ident}.procedure")
    sensor = _add(_add(procedure, "testProcedureEquipment"), "Sensor", gml__id=f"{ident}.sensor")
    _add(sensor, "gml:name", installed["sensor_id"])
    detector = _add(_add(sensor, "detector"), "Detector", gml__id=f"{ident}.detector")
    point = _add(
        _add(detector, "detectorLocation"), "PointLocation", gml__id=f"{ident}.sensor-location"
    )
    point.set("srsName", f"#{boring}-lrs")
    point.set("srsDimension", "1")
    _add(point, "gml:pos", installed["sensor_depth"])
    datum = _add(_add(procedure, "referencePoints"), "ReferencePoint", gml__id=f"{ident}.datum")
    span = _add(_add(datum, "time"), "TimeInterval", gml__id=f"{ident}.datum.time")
    _add(span, "start", installed["datum_from"])
    _add(datum, "rpLocation", xlink__href=f"#{boring}.point")


def reference_xml(dataset: Dataset) -> bytes:
    """A monitoring ``dataset`` as a valid DIGGS 3.0.0 document, values in their units.

    Each piezometer is a ``Borehole`` with its own linear referencing system and one
    ``Monitor``: a ``TemporalResult`` with one time per reading, as the tables write it,
    and a ``WaterLevelMonitoring`` procedure that holds the sensor (its ``Detector`` at
    the sensor depth, which the ``monitorLocation`` refers to) and the reference point.
    The same dataset always gives the same bytes.

    Examples:
        >>> import pydiggs
        >>> xml = reference_xml(build("1.0-beta.3|monitoring|demo", "ABC123"))
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
        ident = idents[row["location_id"]] = f"boring-{i}"
        boring = _add(_add(root, "samplingFeature"), "Borehole", gml__id=ident)
        _add(boring, "gml:name", row["location_id"])
        _add(boring, "projectRef", xlink__href=f"#{project}")
        _place(boring, ident, row, unit)
        _add(boring, "totalMeasuredDepth", row["total_depth"], uom=unit)
    units = _units(dataset.table("readings"))
    readings = _records(dataset.table("readings"))
    for i, installed in enumerate(_records(dataset.table("piezometers")), start=1):
        name = installed["location_id"]
        mine = [r for r in readings if r["location_id"] == name]
        _write_series(root, f"monitor-{i}", installed, mine, (project, idents[name], units))
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))


# --- what a file is observed to hold ----------------------------------------------------


def _instants(table: ResultTable) -> list[float | None] | None:
    """The instant of every row of a series, or None when its rows are not placed by time."""
    if table.domain.shape != "temporal":
        return None
    index = next((c for c in table.domain.index if c.name == "time"), None)
    return None if index is None else [instant(v) for v in index.values]


def _series_readings(table: ResultTable) -> Iterator[tuple[str, float, float, str]]:
    """Every numeric value of a series: (key, instant, SI value, SI unit).

    A cell that is empty, not a number, or the column's ``nullValue`` is not a reading,
    and neither is a value whose time names no instant.
    """
    times = _instants(table)
    if times is None:
        return
    for column in table.columns:
        how = _facts._column_si(column)
        if how is None:
            continue
        factor, offset, unit = how
        key = column.key or column.name
        null = _facts._null(column)
        for when, cell in zip(times, column.values, strict=False):
            value = None if isinstance(cell, bool) else _facts._cell_number(column, cell, null)
            if value is not None and when is not None:
                yield key, when, value * factor + offset, unit


def _components(measurement: Measurement) -> dict[str, Component]:
    """Every component of a measurement's procedure, by key (nested ones included)."""
    found: dict[str, Component] = {}
    stack = list(measurement.procedure.components) if measurement.procedure else []
    while stack:
        component = stack.pop()
        found[component.key] = component
        stack.extend(component.components)
    return found


def _inline_depths(model: Model, nodes: tuple[ExtraNode, ...]) -> Iterator[float]:
    """The depth of each one-dimensional point a ``monitorLocation`` holds itself."""
    for node in nodes:
        if node.qname != "monitorLocation":
            continue
        for point in node.children:
            if point.qname != "PointLocation" or point.attrs.get("srsDimension") != "1":
                continue
            factor = _facts._crs_factor(model, point.attrs.get("srsName"))
            text = next((c.text for c in point.children if c.qname == "gml:pos"), None)
            try:
                number = _facts._number_of(float(text)) if text else None
            except ValueError:
                number = None
            if number is not None and factor is not None:
                yield number * factor


def _sensor_depths(
    model: Model, measurement: Measurement, referred: dict[str, list[str]]
) -> Iterator[float]:
    """The depth, in metres, of each point the series' ``monitorLocation`` gives.

    Either the point is in the ``monitorLocation`` itself, or the ``monitorLocation``
    refers to a part of the series' procedure (a detector) placed at one depth.
    """
    yield from _inline_depths(model, measurement.extra)
    targets = referred.get(measurement.key)
    if not targets:
        return
    components = _components(measurement)
    for target in targets:
        component = components.get(target)
        where = None if component is None else component.location
        if where is None or where.dim != 1:
            continue
        factor = _facts._crs_factor(model, where.crs)
        if factor is not None:
            yield where.coordinates[0] * factor


def observe(model: Model) -> Observed:
    """What a DIGGS file holds of monitoring series, for :func:`_facts.match`.

    Everything the generic observation reads (project names, features by name, total
    depths), and of each ``Monitor`` whose procedure is a ``WaterLevelMonitoring``:

    - every value of a result placed by time (a ``TimePositionList``, or a time per row
      in another form the reader takes), keyed ``(feature, "WaterLevelMonitoring",
      <property key>)`` at its instant, in seconds since 2020-01-01T00:00:00Z. A time
      with no zone names no instant, and its row is not read;
    - the sensor depth, keyed ``"@sensorDepth"``: the one-dimensional point the
      ``monitorLocation`` holds, or the place of the part of the procedure it refers to.

    Numbers become SI only through the units the file states; a cell that is its
    column's declared ``nullValue`` is empty.

    Examples:
        >>> import pydiggs
        >>> data = build("1.0-beta.3|monitoring|demo", "ABC123")
        >>> seen = observe(pydiggs.read_bytes(reference_xml(data)))
        >>> all(_facts.match(f, seen).status == "found" for f in facts(data))
        True
    """
    seen = _facts.observe(model, nulls=True)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for where, found in seen.readings.items():
        if where[1] != PROCEDURE:  # this procedure's values are placed by time, below
            readings[where].extend(found)
    referred: dict[str, list[str]] = defaultdict(list)
    for link in model.links:
        if link.relation == "monitorLocation" and link.target is not None:
            referred[link.source].append(link.target)
    for measurement in model.measurements:
        procedure = measurement.procedure
        if measurement.element != ELEMENT or procedure is None or procedure.type != PROCEDURE:
            continue
        feature = _facts._feature_key(model, measurement)
        if feature is None:
            continue
        for table in measurement.results:
            for key, when, value, unit in _series_readings(table):
                readings[(feature, PROCEDURE, key)].append((when, value, unit))
        for depth in _sensor_depths(model, measurement, referred):
            readings[(feature, PROCEDURE, SENSOR_DEPTH)].append((0.0, depth, "m"))
    for found in readings.values():
        found.sort()
    return Observed(seen.project_names, seen.named, seen.totals, dict(readings))
