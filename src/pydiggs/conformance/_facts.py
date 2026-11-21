"""The answer key of a challenge, and what a DIGGS file is observed to hold.

The key is built from the source tables alone, never from pyDIGGS's own DIGGS output, so
the converter and the reader are checked by the suite like any other software.

A *fact* is one thing the source says: the project's name, a location and its depth, a
value of a property at a depth. An export is graded by looking each fact up in what
:func:`observe` reads from the submitted file. Values are compared in SI units, so an
export may state any unit it declares. The tolerance of a fact is half a unit in the last
place of the source value: a value must agree with the source to the precision the source
gives it.

A *question* is a fact turned round for an import challenge: the vendor reads the DIGGS
file into their software and answers from what it shows.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from functools import cache

from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset
from pydiggs.model import Measurement, Model, Quantity, ResultTable
from pydiggs.model._values import Column

__all__ = ["Fact", "Observed", "Outcome", "Question", "facts", "match", "observe", "questions"]

#: Source unit -> factor to its SI unit. Exact by definition (the international foot and inch).
SI_FACTOR = {
    "": 1.0,
    "m": 1.0,
    "ft": 0.3048,
    "mm": 0.001,
    "in": 0.0254,
    "MPa": 1e6,
    "kPa": 1e3,
    "%": 0.01,
}

_PROCEDURE = {"spt": "DrivenPenetrationTest", "cpt": "StaticConePenetrationTest"}
#: Two numbers that differ by less than this share of the larger one are the same number.
_REL_TOL = 1e-9
#: The defects planted in the second file of an import challenge.
DEFECTS = ("broken-reference", "unknown-property", "invalid-unit")


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing the source says, and how closely an export must agree with it."""

    id: str
    group: str
    kind: str
    shown: str
    feature: str = ""
    procedure: str = ""
    key: str = ""
    depth: float = 0.0
    depth_tol: float = 0.0
    value: float = 0.0
    tol: float = 0.0
    text: str = ""
    #: The SI unit a matching value must be in ("" for a plain number).
    si_unit: str = ""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What grading found for one fact or one question.

    ``status`` is ``found``, ``missing`` or ``differs``; ``check`` is the id of the check
    that failed (None when found); ``got`` is what the file or the answer gave.
    """

    id: str
    group: str
    status: str
    shown: str
    check: str | None = None
    expected: str | None = None
    got: str | None = None


@dataclass(frozen=True, slots=True)
class Question:
    """One question of an import challenge."""

    id: str
    group: str
    kind: str
    text: str
    unit: str
    expected: str
    tol: float = 0.0


#: Source unit -> the SI unit its values are compared in.
SI_UNIT = {"": "", "m": "m", "ft": "m", "mm": "m", "in": "m", "MPa": "Pa", "kPa": "Pa", "%": "Euc"}


@dataclass(frozen=True, slots=True)
class Observed:
    """What a DIGGS file holds, as far as the classes of this suite look.

    Readings are kept once per feature, not once per name of a feature, so the work is
    linear in the size of the file however many names a feature has.
    """

    project_names: frozenset[str]
    #: Feature name -> the keys of the features so named.
    named: dict[str, list[str]]
    #: Feature key -> its total depth in metres (None when it gives none in a length unit).
    totals: dict[str, float | None]
    #: (feature key, procedure, property key) -> (depth in metres, SI value, SI unit),
    #: sorted by depth. A value with no unit has the SI unit "".
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]]


def _half_last_place(text: str, factor: float) -> float:
    exponent = Decimal(text).as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    # Decimal, not 10.0 ** -places: exact on every platform.
    return float(Decimal(5).scaleb(-places - 1)) * factor


def _number(text: str, unit: str) -> tuple[float, float]:
    factor = SI_FACTOR[unit]
    return float(Decimal(text)) * factor, _half_last_place(text, factor)


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of ``dataset``: core facts first, then one per source value."""
    out: list[Fact] = []
    project = dataset.table("project").rows[0][1]
    out.append(
        Fact(
            id="core/project",
            group="core",
            kind="project-name",
            shown=f"project named {project!r}",
            text=project,
        )
    )
    locations = dataset.table("locations")
    depth_unit = locations.fields[5].unit
    for row in locations.rows:
        name, total = row[0], row[5]
        out.append(
            Fact(
                id=f"core/{name}",
                group="core",
                kind="location",
                shown=f"location {name!r}",
                feature=name,
            )
        )
        value, tol = _number(total, depth_unit)
        out.append(
            Fact(
                id=f"core/{name}/total-depth",
                group="core",
                kind="total-depth",
                shown=f"total depth {total} {depth_unit} of {name!r}",
                feature=name,
                value=value,
                tol=tol,
                si_unit=SI_UNIT[depth_unit],
            )
        )
    procedure = _PROCEDURE[dataset.scenario]
    if dataset.scenario == "spt":
        table = dataset.table("spt")
        units = {f.name: f.unit for f in table.fields}
        columns: tuple[tuple[str, str], ...] = (
            ("n_value", "n_value"),
            ("penetration", "@totalPenetration"),
            ("hammer_efficiency", "@hammerEfficiency"),
        )
        depth_field = "depth_top"
    else:
        table = dataset.table("cpt_data")
        units = {f.name: f.unit for f in table.fields}
        columns = tuple((n, n) for n in ("tip_resistance", "sleeve_friction", "pore_pressure_u2"))
        depth_field = "depth"
    names = [f.name for f in table.fields]
    for row in table.rows:
        record = dict(zip(names, row, strict=True))
        depth_text = record[depth_field]
        depth, depth_tol = _number(depth_text, units[depth_field])
        where = f"at {depth_text} {units[depth_field]} in {record['location_id']!r}"
        for column, key in columns:
            value, tol = _number(record[column], units[column])
            if column == "n_value":
                tol = 0.0  # a blow count is exact
            unit = f" {units[column]}" if units[column] else ""
            out.append(
                Fact(
                    id=f"{dataset.scenario}/{record['location_id']}/{depth_text}/{column}",
                    group="fidelity",
                    kind="value",
                    shown=f"{column} {record[column]}{unit} {where}",
                    feature=record["location_id"],
                    procedure=procedure,
                    key=key,
                    depth=depth,
                    depth_tol=depth_tol,
                    value=value,
                    tol=tol,
                    si_unit=SI_UNIT[units[column]],
                )
            )
    return tuple(out)


@cache
def _length_factor(unit: str) -> float | None:
    """Metres per ``unit``, or None when ``unit`` is not a length."""
    if unit in ("m", "ft", "mm", "in"):
        return SI_FACTOR[unit]
    from pydiggs.units import UnitConverter  # noqa: PLC0415  # reads the unit dictionary once

    converter = UnitConverter()
    return converter.to_base(1.0, unit) if converter.base_unit(unit) == "m" else None


def _crs_factor(model: Model, crs: str | None) -> float | None:
    ref = model.crs.get(crs) if crs else None
    unit = ref.unit if ref is not None else None
    return _length_factor(unit) if unit else None


def _quantity_metres(quantity: Quantity | None, model: Model, crs: str | None) -> float | None:
    if quantity is None:
        return None
    if quantity.si is not None:
        return quantity.si.value if quantity.si.uom == "m" else None
    if quantity.value is None or quantity.uom is not None:
        return None  # a unit the reader could not convert is never read as metres
    factor = _crs_factor(model, crs)
    return quantity.value * factor if factor is not None else None


def _depths(model: Model, table: ResultTable) -> list[float | None]:
    """The depth of every row of ``table`` in metres, where the file gives one."""
    index = next((c for c in table.domain.index if c.name in ("position", "top")), None)
    if index is not None:
        if index.si_factor is not None:
            factor = index.si_factor if index.si_uom == "m" else None
        else:
            factor = None if index.uom else _crs_factor(model, table.domain.crs)
        numbers = [_number_of(v) for v in index.values]
        return [n * factor if n is not None and factor is not None else None for n in numbers]
    single: float | None = None
    if table.extent is not None and table.extent.top is not None:
        single = _quantity_metres(table.extent.top, model, table.extent.lrs)
    elif table.location is not None and table.location.dim == 1:
        factor = _crs_factor(model, table.location.crs)
        single = table.location.coordinates[0] * factor if factor is not None else None
    return [single] * max(table.row_count, 1)


def _number_of(value: object) -> float | None:
    """``value`` as a float when it is a finite number (never a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _feature_key(model: Model, measurement: Measurement) -> str | None:
    key = measurement.feature
    if key is None:
        for sample_key in measurement.samples:
            key = getattr(model.get(sample_key), "feature", None)
            if key:
                break
    return key


def _column_si(column: Column) -> tuple[float, float, str] | None:
    """How a cell of ``column`` becomes SI: factor, offset and SI unit; None if it cannot.

    A column with no unit is a plain number. A column whose unit the reader could not
    convert is left out: its numbers are never compared as if they were SI.
    """
    if column.si_factor is not None:
        return column.si_factor, column.si_offset or 0.0, column.si_uom or ""
    return None if column.uom else (1.0, 0.0, "")


def _param_si(held: object) -> tuple[float, str] | None:
    if not isinstance(held, Quantity):
        return None
    if held.si is not None:
        number, unit = _number_of(held.si.value), held.si.uom
    else:
        number, unit = (None if held.uom else _number_of(held.value)), ""
    return None if number is None else (number, unit)


def _measurement_readings(
    model: Model, measurement: Measurement
) -> list[tuple[str, float, float, str]]:
    """Every numeric result and procedure parameter: (key, depth, SI value, SI unit).

    A procedure parameter applies to every depth the measurement has a row at, so one
    test per drive and one test with a row per drive read the same.
    """
    procedure = measurement.procedure
    if procedure is None:
        return []
    out: list[tuple[str, float, float, str]] = []
    row_depths: dict[float, None] = {}
    for table in measurement.results:
        depths = _depths(model, table)
        row_depths.update((d, None) for d in depths if d is not None)
        for column in table.columns:
            how = _column_si(column)
            if how is None:
                continue
            factor, offset, unit = how
            key = column.key or column.name
            for depth, cell in zip(depths, column.values, strict=False):
                value = _number_of(cell)
                if value is not None and depth is not None:
                    out.append((key, depth, value * factor + offset, unit))
    for param, held in procedure.params.items():
        found = _param_si(held)
        if found is not None:
            out.extend(("@" + param, depth, found[0], found[1]) for depth in row_depths)
    return out


def observe(model: Model) -> Observed:
    """Read from ``model`` everything the answer keys of this suite look up."""
    project_names = frozenset(name for project in model.projects for name in project.names)
    named: dict[str, list[str]] = defaultdict(list)
    totals: dict[str, float | None] = {}
    for feature in model.features:
        totals[feature.key] = _quantity_metres(feature.total_depth, model, feature.lrs)
        for name in dict.fromkeys(feature.names):
            named[name].append(feature.key)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for measurement in model.measurements:
        feature_key = _feature_key(model, measurement)
        if feature_key is None or measurement.procedure is None:
            continue
        procedure = measurement.procedure.type
        for key, depth, value, unit in _measurement_readings(model, measurement):
            readings[(feature_key, procedure, key)].append((depth, value, unit))
    for found in readings.values():
        found.sort()
    return Observed(project_names, dict(named), totals, dict(readings))


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= max(tol, _REL_TOL * max(abs(a), abs(b)))


def _fmt(value: float, unit: str = "") -> str:
    return f"{value:.9g} {unit}".rstrip()


def _outcome(fact: Fact, status: str, check: str | None = None, got: str | None = None) -> Outcome:
    expected = None
    if check:
        expected = fact.text if fact.kind == "project-name" else _fmt(fact.value, fact.si_unit)
    return Outcome(fact.id, fact.group, status, fact.shown, check, expected, got)


def _among(fact: Fact, values: list[tuple[float, str]]) -> Outcome:
    """``fact`` against every value the file gives where the fact belongs.

    All of them must agree with the source, in the right kind of unit: a file that holds
    the right value and a wrong one at the same place does not pass.
    """
    if not values:
        return _outcome(fact, "missing", "conformance.fact-missing")
    for value, unit in values:
        if unit != fact.si_unit or not _close(value, fact.value, fact.tol):
            return _outcome(fact, "differs", "conformance.value-differs", _fmt(value, unit))
    return _outcome(fact, "found")


def _at_depth(
    found: list[tuple[float, float, str]], depth: float, tol: float
) -> list[tuple[float, str]]:
    """The readings of a depth-sorted list within ``tol`` of ``depth``."""
    reach = max(tol, _REL_TOL * abs(depth))
    start = bisect_left(found, (depth - reach, -math.inf, ""))
    out = []
    for reading_depth, value, unit in found[start:]:
        if reading_depth > depth + reach:
            break
        out.append((value, unit))
    return out


def match(fact: Fact, seen: Observed) -> Outcome:
    """Look ``fact`` up in ``seen``."""
    if fact.kind == "project-name":
        if fact.text in seen.project_names:
            return _outcome(fact, "found")
        got = ", ".join(sorted(seen.project_names)[:5]) or None
        return _outcome(fact, "differs" if got else "missing", "conformance.project-name", got)
    keys = seen.named.get(fact.feature, [])
    if fact.kind == "location":
        if keys:
            return _outcome(fact, "found")
        return _outcome(fact, "missing", "conformance.fact-missing")
    if fact.kind == "total-depth":
        totals = [seen.totals.get(key) for key in keys]
        return _among(fact, [(total, "m") for total in totals if total is not None])
    here: list[tuple[float, str]] = []
    for key in keys:
        found = seen.readings.get((key, fact.procedure, fact.key), [])
        here.extend(_at_depth(found, fact.depth, fact.depth_tol))
    return _among(fact, here)


def _convert(text: str, unit: str, asked: str) -> tuple[str, float]:
    """A source value in the unit a question asks for, and the tolerance of an answer."""
    value = float(Decimal(text)) * SI_FACTOR[unit] / SI_FACTOR[asked]
    half = _half_last_place(text, SI_FACTOR[unit]) / SI_FACTOR[asked]
    # An answer read off a screen is rounded: allow that, but never more than 0.5 %.
    return _fmt(value), max(half, abs(value) * 0.005)


def questions(dataset: Dataset, seed: str) -> tuple[Question, ...]:
    """The questions of the import challenge for ``dataset``.

    They ask for counts, for values at a depth (some in a unit the file does not use),
    for the project's name, and which defect the second file has. Which borings, depths
    and defect are asked about depends on ``seed``.
    """
    rng = Rng(seed + "/questions")
    locations = dataset.table("locations")
    names = locations.column("location_id")
    depth_unit = locations.fields[5].unit
    other_length = "m" if depth_unit == "ft" else "ft"
    out = [
        Question(
            "core.project-name",
            "core",
            "text",
            "What is the project's name?",
            "",
            dataset.table("project").rows[0][1],
        ),
        Question(
            "core.locations",
            "core",
            "count",
            "How many borings or soundings does the file hold?",
            "",
            str(len(names)),
        ),
    ]
    chosen = rng.choice(names)
    total = dict(zip(names, locations.column("total_depth"), strict=True))[chosen]
    expected, tol = _convert(total, depth_unit, other_length)
    out.append(
        Question(
            "core.total-depth",
            "core",
            "number",
            f"What is the total depth of {chosen}?",
            other_length,
            expected,
            tol,
        )
    )
    if dataset.scenario == "spt":
        table = dataset.table("spt")
        fields = [f.name for f in table.fields]
        rows = [dict(zip(fields, row, strict=True)) for row in table.rows]
        drive_unit = table.fields[3].unit
        other_drive = "mm" if drive_unit == "in" else "in"
        out.append(
            Question(
                "values.tests",
                "values",
                "count",
                "How many SPT tests does the file hold?",
                "",
                str(len(rows)),
            )
        )
        ordinary = rng.choice([r for r in rows if not r["remark"]])
        woh = rng.choice([r for r in rows if r["remark"] == "Weight of hammer"])
        refusal = rng.choice([r for r in rows if r["remark"] == "Refusal"])
        for label, row in (("n-value", ordinary), ("n-value-low", woh)):
            where = f"at {row['depth_top']} {depth_unit} in {row['location_id']}"
            out.append(
                Question(
                    f"values.{label}",
                    "values",
                    "count",
                    f"What is the SPT N value {where}?",
                    "",
                    row["n_value"],
                    0.0,
                )
            )
        expected, tol = _convert(refusal["penetration"], drive_unit, other_drive)
        where = f"at {refusal['depth_top']} {depth_unit} in {refusal['location_id']}"
        out.append(
            Question(
                "values.penetration",
                "values",
                "number",
                f"What is the total penetration of the test {where}?",
                other_drive,
                expected,
                tol,
            )
        )
        expected, tol = _convert(refusal["depth_top"], depth_unit, other_length)
        out.append(
            Question(
                "values.deepest",
                "values",
                "number",
                f"At what depth is the deepest SPT test in {refusal['location_id']}?",
                other_length,
                expected,
                tol,
            )
        )
        out.append(
            Question(
                "values.hammer-efficiency",
                "values",
                "number",
                f"What is the hammer efficiency in {ordinary['location_id']}?",
                "%",
                ordinary["hammer_efficiency"],
                0.5,
            )
        )
    else:
        table = dataset.table("cpt_data")
        fields = [f.name for f in table.fields]
        rows = [dict(zip(fields, row, strict=True)) for row in table.rows]
        sounding = rng.choice(names)
        mine = [r for r in rows if r["location_id"] == sounding]
        out.append(
            Question(
                "values.readings",
                "values",
                "count",
                f"How many readings does the cone test in {sounding} hold?",
                "",
                str(len(mine)),
            )
        )
        row = rng.choice(mine)
        where = f"at {row['depth']} {depth_unit} in {sounding}"
        expected, tol = _convert(row["tip_resistance"], "MPa", "kPa")
        out.append(
            Question(
                "values.tip-resistance",
                "values",
                "number",
                f"What is the cone resistance qc {where}?",
                "kPa",
                expected,
                tol,
            )
        )
        expected, tol = _convert(row["sleeve_friction"], "kPa", "kPa")
        out.append(
            Question(
                "values.sleeve-friction",
                "values",
                "number",
                f"What is the sleeve friction fs {where}?",
                "kPa",
                expected,
                tol,
            )
        )
        lowest = min(mine, key=lambda r: Decimal(r["pore_pressure_u2"]))
        expected, tol = _convert(lowest["pore_pressure_u2"], "kPa", "kPa")
        out.append(
            Question(
                "values.lowest-u2",
                "values",
                "number",
                f"What is the lowest pore pressure u2 in {sounding}?",
                "kPa",
                expected,
                tol,
            )
        )
        expected, tol = _convert(mine[-1]["depth"], depth_unit, other_length)
        out.append(
            Question(
                "values.deepest",
                "values",
                "number",
                f"At what depth is the last reading in {sounding}?",
                other_length,
                expected,
                tol,
            )
        )
    choices = ", ".join((*DEFECTS, "none"))
    out.append(
        Question(
            "robustness.defect",
            "robustness",
            "choice",
            f"defective.xml has one planted defect. Which does your software report? ({choices})",
            "",
            rng.choice(DEFECTS),
        )
    )
    return tuple(out)
