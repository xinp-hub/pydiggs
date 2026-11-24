"""Measurement while drilling: drilling logs of rotary borings, a scenario of the suite.

A seeded, synthetic set of two or three borings, each with one drilling log recorded by
the rig: for every reading, the time since the log started, the depth of the bit, and the
drilling channels the rig logs (penetration rate, thrust on the bit, torque, rotation
speed of the drill string, flushing pressure). The data is written for the suite and is
not from any project.

Traps a correct exporter passes without noticing: a boring name with a space, depths in
feet for about half the seeds, a first reading at the collar where the penetration rate
and the rotation speed are a real zero (a zero is not an empty cell), a few readings in
one boring where the flushing pressure sensor recorded nothing (an empty cell is not a
zero), a gap in each log where recording paused for a rod change (the depth jumps and the
time jumps further; an exporter must not fill it with readings that were never taken),
and channels in units other than the usual SI ones (penetration rate in metres per hour,
flushing pressure in bar, torque in kN.m; or feet per minute, lbf, lbf.ft and psi).

DIGGS 3.0.0 shapes the file: each log is a ``MeasurementWhileDrilling`` on its boring,
with an ``MWDProcedure`` and one ``MWDResult`` whose domain is either a ``timeDomain``
(the readings placed by elapsed time) or a ``location``. Its result columns are coded in
the MWD property dictionary (``mwd_properties.xml``): ``depth``, ``penetration_rate``,
``crowd_downward_thrust``, ``torque``, ``rotation_shaft`` and ``fluid_injection_pressure``.
The reference file places the readings by elapsed time and carries the depth as the
``depth`` column, as the official DIGGS MWD example does.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from typing import Final

from pydiggs.conformance import _facts
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project
from pydiggs.errors import ResourceError
from pydiggs.model import Model, ResultTable
from pydiggs.model._values import Column

__all__ = [
    "CHANNELS",
    "PROCEDURE",
    "SCENARIO",
    "build",
    "facts",
    "observe",
    "profile_tables",
    "questions",
    "reference_xml",
]

#: The scenario's name, as a :class:`Dataset` states it.
SCENARIO: Final = "mwd"
#: The procedure a drilling log's readings are kept under in :class:`Observed`.
PROCEDURE: Final = "MWDProcedure"
_ELEMENT: Final = "MeasurementWhileDrilling"
_MWD: Final = "https://diggsml.org/def/codes/DIGGS/0.1/mwd_properties.xml#"
#: The key an elapsed time is observed under: the dictionary's ``time``.
_TIME: Final = "time"
#: The key of the column that gives each reading's depth.
_DEPTH: Final = "depth"

#: Source column -> (its key in the MWD dictionary, what it holds).
CHANNELS: Final = {
    "penetration_rate": ("penetration_rate", "Penetration rate of the bit"),
    "thrust": ("crowd_downward_thrust", "Thrust: the downward force on the bit"),
    "torque": ("torque", "Torque on the drill string"),
    "rotation_speed": ("rotation_shaft", "Rotation speed of the drill string"),
    "flushing_pressure": (
        "fluid_injection_pressure",
        "Flushing (fluid injection) pressure; empty when the sensor recorded nothing",
    ),
}

#: Source unit -> (factor to SI, SI unit), for every unit the scenario uses. Exact by
#: definition. The revolution per minute is 2 pi / 60 rad/s.
_SI: Final = {
    "": (1.0, ""),
    "m": (1.0, "m"),
    "ft": (0.3048, "m"),
    "mm": (0.001, "m"),
    "s": (1.0, "s"),
    "min": (60.0, "s"),
    "m/h": (1 / 3600, "m/s"),
    "m/min": (1 / 60, "m/s"),
    "mm/s": (0.001, "m/s"),
    "ft/min": (0.00508, "m/s"),
    "ft/h": (0.3048 / 3600, "m/s"),
    "kN": (1000.0, "N"),
    "lbf": (4.4482216152605, "N"),
    "kN.m": (1000.0, "N.m"),
    "N.m": (1.0, "N.m"),
    "lbf.ft": (1.3558179483314003, "N.m"),
    "rpm": (math.pi / 30, "rad/s"),
    "bar": (1e5, "Pa"),
    "kPa": (1e3, "Pa"),
    "psi": (6894.757293168361, "Pa"),
}
#: Units whose DIGGS conversion factor is an expression (``2*PI``) that pyDIGGS's unit
#: table does not evaluate, so the reader gives their columns no SI value.
_ANGULAR: Final = {"rpm": (math.pi / 30, "rad/s"), "rev/s": (2 * math.pi, "rad/s")}


@dataclass(frozen=True, slots=True)
class _Channel:
    """How one channel's values are drawn: unit, decimals, range and step, in last places."""

    unit: str
    places: int
    low: int
    high: int
    jitter: int


@dataclass(frozen=True, slots=True)
class _System:
    length: str
    step: Decimal
    channels: dict[str, _Channel]


_METRIC: Final = _System(
    length="m",
    step=Decimal("0.05"),
    channels={
        "penetration_rate": _Channel("m/h", 1, 20, 900, 40),
        "thrust": _Channel("kN", 1, 40, 450, 25),
        "torque": _Channel("kN.m", 2, 40, 750, 30),
        "rotation_speed": _Channel("rpm", 0, 40, 140, 6),
        "flushing_pressure": _Channel("bar", 1, 15, 300, 12),
    },
)
_IMPERIAL: Final = _System(
    length="ft",
    step=Decimal("0.20"),
    channels={
        "penetration_rate": _Channel("ft/min", 2, 10, 500, 20),
        "thrust": _Channel("lbf", 0, 900, 10_000, 400),
        "torque": _Channel("lbf.ft", 0, 300, 5500, 200),
        "rotation_speed": _Channel("rpm", 0, 40, 140, 6),
        "flushing_pressure": _Channel("psi", 0, 20, 430, 15),
    },
)


# --- the dataset ------------------------------------------------------------------------


def _scaled(units: int, places: int) -> str:
    return _fixed(Decimal(units).scaleb(-places), places)


def _log(rng: Rng, name: str, system: _System, dropout: bool) -> list[tuple[str, ...]]:
    """The drilling log of one boring: one row per reading, from the collar down.

    The first reading is at the collar (depth 0, nothing turning yet). Recording pauses
    once for a rod change: the depth jumps by three to six steps and the time by minutes.
    When ``dropout`` is set, two to four readings have no flushing pressure.
    """
    count = rng.integer(30, 60)
    gap_at = rng.integer(8, count - 8)
    lost = set()
    if dropout:
        start = rng.choice([i for i in range(2, count - 6) if abs(i - gap_at) > 1])
        lost = set(range(start, start + rng.integer(2, 4)))
    level = {
        column: rng.integer(c.low + (c.high - c.low) // 4, c.high - (c.high - c.low) // 4)
        for column, c in system.channels.items()
    }
    depth, elapsed = Decimal(0), Decimal(0)
    rows: list[tuple[str, ...]] = []
    for i in range(count):
        if i:
            depth += system.step * (rng.integer(3, 6) if i == gap_at else 1)
            elapsed += Decimal(rng.integer(20, 90)) / 10
            if i == gap_at:
                elapsed += Decimal(rng.integer(900, 2400)) / 10
        values = []
        for column, channel in system.channels.items():
            step = rng.integer(-channel.jitter, channel.jitter)
            level[column] = min(channel.high, max(channel.low, level[column] + step))
            units = level[column]
            if i == 0 and column in ("penetration_rate", "rotation_speed"):
                units = 0  # at the collar, before the bit turns: a real zero
            if column == "flushing_pressure" and i in lost:
                values.append("")
            else:
                values.append(_scaled(units, channel.places))
        rows.append((name, _fixed(elapsed, 1), _fixed(depth, 2), *values))
    return rows


def build(seed_stream: str, token: str) -> Dataset:
    """The MWD dataset for ``seed_stream``; the same arguments give the same data.

    Examples:
        >>> data = build("1.0-beta.2|mwd|demo", "ABC123")
        >>> [t.name for t in data.tables]
        ['project', 'locations', 'mwd_logs', 'mwd_data']
        >>> data == build("1.0-beta.2|mwd|demo", "ABC123")
        True
    """
    rng = Rng(seed_stream)
    system = _IMPERIAL if rng.integer(0, 1) == 1 else _METRIC
    number = rng.integer(1, 80)
    names = (f"RC-{number:02d}", f"RC {number + 1}", f"RC-{number + 2}A")[: rng.integer(2, 3)]
    dropout = rng.choice(names)
    rows: list[tuple[str, ...]] = []
    totals: dict[str, str] = {}
    for name in names:
        mine = _log(rng.fork("log/" + name), name, system, name == dropout)
        rows.extend(mine)
        totals[name] = mine[-1][2]
    located = _locations(rng.fork("loc"), names, "borehole", system.length)
    fields = (*located.fields, Field("total_depth", system.length, "Final depth of the hole"))
    locations = Table(located.name, fields, tuple((*r, totals[r[0]]) for r in located.rows))
    logs = Table(
        "mwd_logs",
        (
            Field("location_id", "", "The boring the log is of"),
            Field("log_id", "", "The log's name"),
        ),
        tuple((name, f"MWD {name}") for name in names),
    )
    data = Table(
        "mwd_data",
        (
            Field("location_id", "", "The boring"),
            Field("elapsed_time", "s", "Time since the log started"),
            Field("depth", system.length, "Depth of the bit at the reading"),
            *(
                Field(column, system.channels[column].unit, meaning)
                for column, (_key, meaning) in CHANNELS.items()
            ),
        ),
        tuple(rows),
    )
    return Dataset(SCENARIO, token, (_project(rng.fork("proj"), token), locations, logs, data))


def _records(table: Table) -> list[dict[str, str]]:
    names = [f.name for f in table.fields]
    return [dict(zip(names, row, strict=True)) for row in table.rows]


def _units(table: Table) -> dict[str, str]:
    return {f.name: f.unit for f in table.fields}


# --- the answer key ---------------------------------------------------------------------


def _number(text: str, unit: str) -> tuple[float, float]:
    factor = _SI[unit][0]
    return float(Decimal(text)) * factor, _facts._half_last_place(text, factor)


def _value(where: tuple[str, str, str], column: str, text: str, unit: str, key: str) -> Fact:
    """A fidelity fact: ``column`` = ``text`` ``unit`` in a boring at a depth."""
    boring, depth_text, depth_unit = where
    depth, depth_tol = _number(depth_text, depth_unit)
    value, tol = _number(text, unit)
    shown_unit = f" {unit}" if unit else ""
    return Fact(
        id=f"{SCENARIO}/{boring}/{depth_text}/{column}",
        group="fidelity",
        kind="value",
        shown=f"{column} {text}{shown_unit} at {depth_text} {depth_unit} in {boring!r}",
        feature=boring,
        procedure=PROCEDURE,
        key=key,
        depth=depth,
        depth_tol=depth_tol,
        value=value,
        tol=tol,
        si_unit=_SI[unit][1],
    )


def _absent(where: tuple[str, str, str], column: str, unit: str, key: str, why: str) -> Fact:
    """A value the source does not give there: the file must give none either."""
    boring, depth_text, depth_unit = where
    return replace(
        _value(where, column, "0", unit, key),
        kind="absent",
        shown=f"no {column} at {depth_text} {depth_unit} in {boring!r} ({why})",
    )


def _gaps(rows: list[dict[str, str]], step: Decimal) -> Iterator[tuple[str, str]]:
    """(boring, depth) of every step of a log's depth series where no reading was taken."""
    for before, after in pairwise(rows):
        if before["location_id"] != after["location_id"]:
            continue
        depth = Decimal(before["depth"]) + step
        while depth < Decimal(after["depth"]):
            yield before["location_id"], _fixed(depth, 2)
            depth += step


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of an MWD ``dataset``, from its tables alone.

    Core: the project's name, each boring and its depth. Fidelity: each reading's
    elapsed time and every channel at its depth. An empty cell (no flushing pressure)
    is an ``absent`` fact; so are the elapsed time and every channel at each step of a
    log where recording paused: a file that gives a reading there invents it. Nothing
    is known of depths below a log's last reading, so a reading added there is not
    looked for.

    Examples:
        >>> key = facts(build("1.0-beta.2|mwd|demo", "ABC123"))
        >>> key[0].id, key[0].group
        ('core/project', 'core')
        >>> sorted({f.kind for f in key if f.group == "fidelity"})
        ['absent', 'value']
    """
    out = _facts.core_facts(dataset)
    table = dataset.table("mwd_data")
    units = _units(table)
    depth_unit = units["depth"]
    rows = _records(table)
    for row in rows:
        where = (row["location_id"], row["depth"], depth_unit)
        out.append(_value(where, "elapsed_time", row["elapsed_time"], "s", _TIME))
        for column, (key, _meaning) in CHANNELS.items():
            if row[column]:
                out.append(_value(where, column, row[column], units[column], key))
            else:
                out.append(_absent(where, column, units[column], key, "the source leaves it empty"))
    step = Decimal("0.20") if depth_unit == "ft" else Decimal("0.05")
    for boring, depth in _gaps(rows, step):
        where = (boring, depth, depth_unit)
        # A row invented in the pause is wrong whichever cells it fills: the time and
        # every channel, not the penetration rate alone.
        out.append(_absent(where, "elapsed_time", "s", _TIME, "no reading"))
        out.extend(
            _absent(where, column, units[column], key, "no reading")
            for column, (key, _meaning) in CHANNELS.items()
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


def _count_question(qid: str, text: str, expected: str) -> Question:
    return Question(qid, qid.partition(".")[0], "count", text, "", expected, 0.0)


#: Source unit -> the other unit a question asks for it in.
_ASKED: Final = {
    "m": "ft",
    "ft": "m",
    "m/h": "m/min",
    "ft/min": "ft/h",
    "kN.m": "N.m",
    "lbf.ft": "N.m",
    "bar": "kPa",
    "psi": "kPa",
}


def _value_questions(dataset: Dataset, rng: Rng) -> list[Question]:
    table = dataset.table("mwd_data")
    units = _units(table)
    length = units["depth"]
    rows = _records(table)
    boring = rng.choice(dataset.table("locations").column("location_id"))
    mine = [r for r in rows if r["location_id"] == boring]
    row = rng.choice([r for r in mine[1:] if r["flushing_pressure"]])
    where = f"at {row['depth']} {length} in {boring}"
    out = [
        _count_question(
            "values.readings",
            f"How many readings does the drilling log of {boring} hold?",
            str(len(mine)),
        ),
        _number_question(
            "values.penetration-rate",
            f"What is the penetration rate {where}?",
            (row["penetration_rate"], units["penetration_rate"]),
            _ASKED[units["penetration_rate"]],
        ),
        _number_question(
            "values.torque",
            f"What is the torque {where}?",
            (row["torque"], units["torque"]),
            _ASKED[units["torque"]],
        ),
        _number_question(
            "values.rotation-speed",
            f"What is the rotation speed {where}, in revolutions per minute?",
            (row["rotation_speed"], "rpm"),
            "rpm",
        ),
        _number_question(
            "values.flushing-pressure",
            f"What is the flushing pressure {where}?",
            (row["flushing_pressure"], units["flushing_pressure"]),
            _ASKED[units["flushing_pressure"]],
        ),
    ]
    holed = next(r["location_id"] for r in rows if not r["flushing_pressure"])
    out.append(
        _count_question(
            "values.no-flushing-pressure",
            f"At how many readings of {holed} is no flushing pressure recorded?",
            str(sum(1 for r in rows if r["location_id"] == holed and not r["flushing_pressure"])),
        )
    )
    step = Decimal("0.20") if length == "ft" else Decimal("0.05")
    resumed = next(
        after
        for before, after in pairwise(mine)
        if Decimal(after["depth"]) - Decimal(before["depth"]) > step
    )
    out.append(
        _number_question(
            "values.resumed",
            f"Recording in {boring} pauses once (no readings over a depth interval)."
            " At what depth does it resume?",
            (resumed["depth"], length),
            _ASKED[length],
        )
    )
    return out


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for an MWD ``dataset``.

    Which boring and reading are asked about, and which defect the second file has,
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


def profile_tables(dataset: Dataset) -> dict[str, list[dict[str, str]]]:
    """The dataset as DIGGS tables profile tables (:mod:`pydiggs.tables`).

    One ``Borehole`` per boring, one ``MeasurementWhileDrilling`` per log, placed by
    elapsed time, with the depth and every channel as result columns coded in the MWD
    dictionary. Every value is the source's text; an empty cell stays empty.
    """
    project_id, project_name = dataset.table("project").rows[0]
    locations = dataset.table("locations")
    depth_unit = _units(locations)["total_depth"]
    features = {}
    for i, row in enumerate(_records(locations), start=1):
        features[row["location_id"]] = {
            "key": f"boring-{i}",
            "project": project_id,
            "element": "Borehole",
            "name": row["location_id"],
            "x": row["easting"],
            "y": row["northing"],
            "z": row["ground_level"],
            "total_depth": row["total_depth"],
            "total_depth_uom": depth_unit,
            "depth_uom": depth_unit,
        }
    logs = {
        row["location_id"]: {
            "key": f"mwd-{i}",
            "feature": features[row["location_id"]]["key"],
            "element": _ELEMENT,
            "procedure": PROCEDURE,
            "name": row["log_id"],
        }
        for i, row in enumerate(_records(dataset.table("mwd_logs")), start=1)
    }
    data = dataset.table("mwd_data")
    units = _units(data)
    columns = [{"procedure": PROCEDURE, "column": "elapsed_time", "uom": "s"}]
    for column, key, name in (
        ("depth", _DEPTH, "Depth"),
        *((c, key, c.replace("_", " ").capitalize()) for c, (key, _m) in CHANNELS.items()),
    ):
        columns.append(
            {
                "procedure": PROCEDURE,
                "column": key,
                "name": name,
                "property": key,
                "definition": _MWD + key,
                "uom": units[column],
            }
        )
    results = []
    for row in _records(data):
        result = {
            "measurement": logs[row["location_id"]]["key"],
            "elapsed_time": row["elapsed_time"],
            _DEPTH: row["depth"],
        }
        result.update({key: row[column] for column, (key, _m) in CHANNELS.items()})
        results.append(result)
    return {
        "projects": [{"key": project_id, "name": project_name}],
        "features": list(features.values()),
        "measurements": list(logs.values()),
        "result_columns": columns,
        "results_MWDProcedure": results,
    }


def reference_xml(dataset: Dataset) -> bytes:
    """An MWD ``dataset`` as a valid DIGGS 3.0.0 document, written by pyDIGGS's writer.

    The tables of :func:`profile_tables` through :func:`pydiggs.tables.to_model` and
    :func:`pydiggs.write_bytes`. The same dataset always gives the same bytes.

    Raises:
        ResourceError: The tables could not be written whole, or the result is not valid
            DIGGS. Either is a defect in the suite, never in a submission.

    Examples:
        >>> import pydiggs
        >>> xml = reference_xml(build("1.0-beta.2|mwd|demo", "ABC123"))
        >>> pydiggs.validate_bytes(xml).ok
        True
    """
    import pydiggs  # noqa: PLC0415  # the writer and the tables kit load on demand
    from pydiggs import tables  # noqa: PLC0415

    made = tables.to_model(profile_tables(dataset))
    if any(f.severity.value == "error" for f in made.findings):
        raise ResourceError("the mwd reference file could not be written")
    xml = pydiggs.write_bytes(made.model)
    if not pydiggs.validate_bytes(xml).ok:
        raise ResourceError("the mwd reference file is not valid DIGGS")
    return xml


# --- what a file is observed to hold ----------------------------------------------------


def _column_si(column: Column) -> tuple[float, float, str] | None:
    """How a cell of ``column`` becomes SI, as :func:`_facts._column_si`, plus rpm."""
    found = _facts._column_si(column)
    if found is None and column.uom in _ANGULAR:
        factor, unit = _ANGULAR[column.uom]
        return factor, 0.0, unit
    return found


def _row_depths(model: Model, table: ResultTable) -> list[float | None]:
    """Each row's depth in metres: from the ``depth`` column, else the table's domain."""
    column = next((c for c in table.columns if c.key == _DEPTH), None)
    if column is None:
        return _facts._depths(model, table)
    how = _column_si(column)
    if how is None or how[2] != "m":
        return [None] * len(column.values)
    factor, offset, _unit = how
    null = _facts._null(column)
    out: list[float | None] = []
    for cell in column.values:
        number = None if isinstance(cell, bool) else _facts._cell_number(column, cell, null)
        out.append(None if number is None else number * factor + offset)
    return out


def _table_readings(model: Model, table: ResultTable) -> Iterator[tuple[str, float, float, str]]:
    """Every numeric cell of a log's table: (key, depth in metres, SI value, SI unit).

    The elapsed times of a time domain are read under ``time``. A cell that is empty,
    not a number, or the column's ``nullValue`` is not a reading.
    """
    depths = _row_depths(model, table)
    columns: list[tuple[str, Column]] = [
        (_TIME, c) for c in table.domain.index if table.domain.shape == "temporal"
    ]
    columns += [(c.key or c.name, c) for c in table.columns if c.key != _DEPTH]
    for key, column in columns:
        how = _column_si(column)
        if how is None:
            continue
        factor, offset, unit = how
        null = _facts._null(column)
        for depth, cell in zip(depths, column.values, strict=False):
            number = None if isinstance(cell, bool) else _facts._cell_number(column, cell, null)
            if number is None or depth is None:
                continue
            yield key, depth, number * factor + offset, unit


def observe(model: Model) -> Observed:
    """What a DIGGS file holds of drilling logs, for :func:`_facts.match`.

    Everything the generic observation reads (project names, features by name, total
    depths), and each ``MWDProcedure`` measurement's result values keyed
    ``(feature, "MWDProcedure", <property key>)`` at the depth of their row, in metres.
    A row's depth is its ``depth`` column (the MWD dictionary's ``depth``) when the
    table has one, else the table's spatial domain. Numbers become SI only through the
    units the file states (revolutions per minute included); a value whose unit or
    depth cannot be converted is left out. pyDIGGS's reader gives the whole log, so no
    unconsumed content is walked.

    Examples:
        >>> import pydiggs
        >>> data = build("1.0-beta.2|mwd|demo", "ABC123")
        >>> seen = observe(pydiggs.read_bytes(reference_xml(data)))
        >>> all(_facts.match(f, seen).status == "found" for f in facts(data))
        True
    """
    seen = _facts.observe(model)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for measurement in model.measurements:
        if measurement.procedure is None or measurement.procedure.type != PROCEDURE:
            continue
        feature = _facts._feature_key(model, measurement)
        if feature is None:
            continue
        for table in measurement.results:
            for key, depth, value, unit in _table_readings(model, table):
                readings[(feature, PROCEDURE, key)].append((depth, value, unit))
    for found in readings.values():
        found.sort()
    return Observed(seen.project_names, seen.named, seen.totals, dict(readings))
