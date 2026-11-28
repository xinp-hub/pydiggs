"""Pressuremeter tests: the reported results of each test and its unload-reload loops.

A seeded, synthetic programme of two or three borings with pressuremeter tests at
several depths. Each test reports, as the test report gives them, the in-situ horizontal
stress, the pressuremeter (Menard) modulus and the limit pressure, with the probe's
diameter; a test may have one or two unload-reload loops, each with its shear modulus,
mean pressure, pressure range, mean strain and strain range. The data is written for the
suite and is not from any project. Every value is carried as reported: the suite works
out no modulus from a curve, extrapolates no limit pressure and derives nothing from a
loop.

Traps a correct exporter passes without noticing: one test that was ended before the
limit pressure was reached (its limit pressure is empty, not zero and not worked out);
tests with no unload-reload loop, which have none in the file; pressures in kPa beside
moduli in MPa for about half the seeds, and pressures in psi beside moduli in tons-force
per square foot for the others; and a boring name with a space.

DIGGS 3.0.0 shapes the file: each test is a ``Test`` whose result sits at the test depth
and whose procedure is a ``PressuremeterTest`` with its ``pressuremeterDiameter`` and one
``trial`` (a ``PressuremeterTestTrial``) per unload-reload loop, numbered by ``trialNo``.
The result columns are properties the DIGGS property dictionary defines for a
``PressuremeterTest``: ``insitu_horizontal_stress``, ``menard_modulus`` and
``limit_pressure``. The dictionary defines no creep pressure and no property for the
readings of the pressure-volume curve, so the suite carries and grades neither.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from lxml import etree

from pydiggs.conformance import _facts
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._insitu import (
    _NSMAP,
    _SCHEMA_LOCATION,
    _count_question,
    _result,
    _scaled,
    _test,
)
from pydiggs.conformance._piles import _add, _float, _place, _qname, _records, _units
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project
from pydiggs.model import Model

__all__ = [
    "LOOPS",
    "PROCEDURE",
    "RESULTS",
    "SCENARIO",
    "TRIAL",
    "build",
    "facts",
    "loop_key",
    "observe",
    "questions",
    "reference_xml",
]

#: The scenario's name, as a :class:`Dataset` states it.
SCENARIO: Final = "pressuremeter"
PROCEDURE: Final = "PressuremeterTest"
#: The role of an unload-reload loop in the procedure, and the number that names it.
TRIAL: Final = "trial"
_TRIAL_NO: Final = "trialNo"
_DIAMETER: Final = "pressuremeterDiameter"
#: The most unload-reload loops one test of the scenario has.
LOOPS: Final = 2

#: Test-table column -> its property in the DIGGS property dictionary, in file order.
RESULTS: Final = (
    ("horizontal_stress", "insitu_horizontal_stress"),
    ("menard_modulus", "menard_modulus"),
    ("limit_pressure", "limit_pressure"),
)
#: Loop-table column -> the element of a ``PressuremeterTestTrial`` that holds it.
_LOOP_COLUMNS: Final = (
    ("shear_modulus", "shearModulus"),
    ("mean_strain", "meanStrain"),
    ("strain_range", "strainRange"),
    ("mean_pressure", "meanPressure"),
    ("pressure_range", "pressureRange"),
)
_POSITIVE_INTEGER: Final = re.compile(r"\+?\d{1,9}")


def loop_key(number: int, element: str) -> str:
    """The key a value of unload-reload loop ``number`` is observed under.

    Examples:
        >>> loop_key(2, "shearModulus")
        'trial[2]:@shearModulus'
    """
    return f"{TRIAL}[{number}]:@{element}"


@dataclass(frozen=True, slots=True)
class _System:
    """The units of one dataset and the ranges its numbers are drawn from."""

    length: str
    size: str
    pressure: str
    modulus: str
    step: Decimal
    probes: tuple[str, ...]
    stress: tuple[int, int, int]
    limit: tuple[int, int, int]
    menard: tuple[int, int, int]
    shear: tuple[int, int, int]
    mean: tuple[int, int, int]
    swing: tuple[int, int, int]


_METRIC: Final = _System(
    length="m",
    size="mm",
    pressure="kPa",
    modulus="MPa",
    step=Decimal("0.50"),
    probes=("44", "58", "74"),
    stress=(20, 250, 0),  # 20 to 250 kPa
    limit=(30, 350, -1),  # 300 to 3500 kPa, to the nearest 10
    menard=(20, 600, 1),  # 2.0 to 60.0 MPa
    shear=(50, 1500, 1),  # 5.0 to 150.0 MPa
    mean=(30, 150, -1),  # 300 to 1500 kPa, to the nearest 10
    swing=(5, 25, -1),  # 50 to 250 kPa, to the nearest 10
)
_IMPERIAL: Final = _System(
    length="ft",
    size="in",
    pressure="psi",
    modulus="tonf[US]/ft2",
    step=Decimal("1.00"),
    probes=("1.73", "2.28", "2.91"),
    stress=(30, 360, 1),  # 3.0 to 36.0 psi
    limit=(45, 500, 0),  # 45 to 500 psi
    menard=(20, 600, 0),  # 20 to 600 tsf
    shear=(50, 1500, 0),  # 50 to 1500 tsf
    mean=(45, 220, 0),  # 45 to 220 psi
    swing=(8, 40, 0),  # 8 to 40 psi
)
#: Source unit -> (factor to SI, SI unit), for every unit the tables use.
_SI: Final = {
    "": (1.0, ""),
    "m": (1.0, "m"),
    "ft": (0.3048, "m"),
    "mm": (0.001, "m"),
    "in": (0.0254, "m"),
    "kPa": (1000.0, "Pa"),
    "MPa": (1_000_000.0, "Pa"),
    "psi": (4.4482216152605 / 0.0254**2, "Pa"),
    # A US ton-force is 2000 pounds-force.
    "tonf[US]/ft2": (2000 * 4.4482216152605 / 0.3048**2, "Pa"),
}


# --- the dataset ------------------------------------------------------------------------


def _loop_counts(rng: Rng, tests: int) -> list[int]:
    """How many loops each test has: at least one test with none, one with the most."""
    counts = [rng.integer(0, LOOPS) for _ in range(tests)]
    none = rng.integer(0, tests - 1)
    most = (none + rng.integer(1, tests - 1)) % tests
    counts[none], counts[most] = 0, LOOPS
    return counts


def _tests(rng: Rng, names: tuple[str, ...], system: _System) -> tuple[Table, Table]:
    """Three to five tests per boring; one test in the programme has no limit pressure."""
    tests: list[tuple[str, ...]] = []
    for name in names:
        here = rng.fork(name)
        probe = here.choice(system.probes)
        depth = system.step * here.integer(4, 12)
        for _ in range(here.integer(3, 5)):
            tests.append(
                (
                    name,
                    _fixed(depth, 2),
                    probe,
                    _scaled(here, system.stress),
                    _scaled(here, system.menard),
                    _scaled(here, system.limit),
                )
            )
            depth += system.step * here.integer(2, 6)
    unreached = rng.integer(0, len(tests) - 1)
    tests[unreached] = (*tests[unreached][:5], "")
    loops: list[tuple[str, ...]] = []
    drawn = rng.fork("loops")
    for test, count in zip(tests, _loop_counts(drawn, len(tests)), strict=True):
        # Every number of a loop is drawn, as a test report states it; none is worked
        # out from another.
        loops.extend(
            (
                test[0],
                test[1],
                str(number),
                _scaled(drawn, system.shear),
                _fixed(Decimal(drawn.integer(50, 800)).scaleb(-4), 4),
                _fixed(Decimal(drawn.integer(10, 100)).scaleb(-4), 4),
                _scaled(drawn, system.mean),
                _scaled(drawn, system.swing),
            )
            for number in range(1, count + 1)
        )
    where = (
        Field("location_id", "", "The boring the test is in"),
        Field("test_depth", system.length, "Depth of the centre of the probe"),
    )
    return (
        Table(
            "pmt_tests",
            (
                *where,
                Field("probe_diameter", system.size, "Diameter of the probe, uninflated"),
                Field(
                    "horizontal_stress",
                    system.pressure,
                    "In-situ horizontal stress, as reported",
                ),
                Field("menard_modulus", system.modulus, "Pressuremeter modulus, as reported"),
                Field(
                    "limit_pressure",
                    system.pressure,
                    "Limit pressure, as reported; empty when the test was ended before it"
                    " was reached. It is not worked out from anything",
                ),
            ),
            tuple(tests),
        ),
        Table(
            "pmt_loops",
            (
                *where,
                Field("loop", "", "Number of the unload-reload loop in its test, from 1"),
                Field("shear_modulus", system.modulus, "Unload-reload shear modulus of the loop"),
                Field("mean_strain", "", "Mean strain of the loop, a ratio"),
                Field("strain_range", "", "Strain range of the loop, a ratio"),
                Field("mean_pressure", system.pressure, "Mean pressure of the loop"),
                Field("pressure_range", system.pressure, "Pressure range of the loop"),
            ),
            tuple(loops),
        ),
    )


def build(seed_stream: str, token: str) -> Dataset:
    """The pressuremeter dataset for ``seed_stream``; the same arguments give the same data.

    Examples:
        >>> data = build("1.0-beta.3|pressuremeter|demo", "ABC123")
        >>> [t.name for t in data.tables]
        ['project', 'locations', 'pmt_tests', 'pmt_loops']
        >>> data == build("1.0-beta.3|pressuremeter|demo", "ABC123")
        True
    """
    rng = Rng(seed_stream)
    system = _IMPERIAL if rng.integer(0, 1) == 1 else _METRIC
    number = rng.integer(1, 60)
    names = (f"PM {number}A", f"BH-{number + 1:02d}", f"BH-{number + 2:02d}")
    borings = names[: rng.integer(2, 3)]
    tests, loops = _tests(rng.fork("tests"), borings, system)
    deepest: dict[str, Decimal] = {}
    for name, depth, *_ in tests.rows:
        deepest[name] = max(deepest.get(name, Decimal(0)), Decimal(depth))
    extra = Decimal(2) if system.length == "m" else Decimal(5)
    located = _locations(rng.fork("loc"), borings, "borehole", system.length)
    fields = (*located.fields, Field("total_depth", system.length, "Final depth of the hole"))
    rows = tuple((*r, _fixed(deepest[r[0]] + extra, 2)) for r in located.rows)
    locations = Table("locations", fields, rows)
    return Dataset(SCENARIO, token, (_project(rng.fork("proj"), token), locations, tests, loops))


# --- the answer key ---------------------------------------------------------------------


def _loops_of(dataset: Dataset) -> dict[tuple[str, str], dict[int, dict[str, str]]]:
    """(boring, test depth as written) -> loop number -> the loop's row."""
    out: dict[tuple[str, str], dict[int, dict[str, str]]] = defaultdict(dict)
    for row in _records(dataset.table("pmt_loops")):
        out[(row["location_id"], row["test_depth"])][int(row["loop"])] = row
    return out


def _number(text: str, unit: str) -> tuple[float, float]:
    factor = _SI[unit][0]
    return float(Decimal(text)) * factor, _facts._half_last_place(text, factor)


def _fact(where: tuple[str, str, str], what: tuple[str, str], text: str, unit: str) -> Fact:
    """One value; ``where`` is (boring, depth as written, depth unit), ``what`` (name, key).

    An empty cell is an ``absent`` fact: a file that gives a value there invents it.
    """
    location, depth_text, depth_unit = where
    name, key = what
    depth, depth_tol = _number(depth_text, depth_unit)
    value, tol = _number(text or "0", unit)
    place = f"at {depth_text} {depth_unit} in {location!r}"
    shown = name.replace("/", " ")
    fact = Fact(
        id=f"{SCENARIO}/{location}/{depth_text}/{name}",
        group="fidelity",
        kind="value",
        shown=f"{shown} {text} {unit}".rstrip() + f" {place}",
        feature=location,
        procedure=PROCEDURE,
        key=key,
        depth=depth,
        depth_tol=depth_tol,
        value=value,
        tol=tol,
        si_unit=_SI[unit][1],
    )
    if text:
        return fact
    return replace(fact, kind="absent", shown=f"no {shown} {place} (the source has none)")


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of a pressuremeter ``dataset``, from its tables alone.

    Core: the project's name, every boring and its depth. Fidelity, at each test depth:
    the in-situ horizontal stress, the pressuremeter modulus, the limit pressure and the
    probe's diameter; and the five values of each unload-reload loop, by the loop's
    number. An empty cell is an ``absent`` fact, and so is every value of a loop the
    test does not have, up to :data:`LOOPS` loops a test.

    Examples:
        >>> key = facts(build("1.0-beta.3|pressuremeter|demo", "ABC123"))
        >>> key[0].id, sum(f.key == "limit_pressure" and f.kind == "absent" for f in key)
        ('core/project', 1)
    """
    out = _facts.core_facts(dataset)
    tests, loops = dataset.table("pmt_tests"), dataset.table("pmt_loops")
    units, loop_units = _units(tests), _units(loops)
    made = _loops_of(dataset)
    for row in _records(tests):
        where = (row["location_id"], row["test_depth"], units["test_depth"])
        out.extend(
            _fact(where, (column, key), row[column], units[column]) for column, key in RESULTS
        )
        column = "probe_diameter"
        out.append(_fact(where, (column, f"@{_DIAMETER}"), row[column], units[column]))
        of_test = made.get((row["location_id"], row["test_depth"]), {})
        for number in range(1, LOOPS + 1):
            loop = of_test.get(number)
            out.extend(
                _fact(
                    where,
                    (f"loop-{number}/{column}", loop_key(number, element)),
                    loop[column] if loop else "",
                    loop_units[column],
                )
                for column, element in _LOOP_COLUMNS
            )
    return tuple(out)


# --- the import questions ---------------------------------------------------------------


def _number_question(qid: str, text: str, source: tuple[str, str], asked: str) -> Question:
    """A question whose answer is a source value in the unit ``asked``."""
    value = float(Decimal(source[0])) * _SI[source[1]][0] / _SI[asked][0]
    half = _facts._half_last_place(source[0], _SI[source[1]][0]) / _SI[asked][0]
    tol = max(half, abs(value) * 0.005)
    return Question(qid, qid.partition(".")[0], "number", text, asked, _facts._fmt(value), tol)


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for a pressuremeter ``dataset``.

    Examples:
        >>> asked = questions(build("s", "ABC123"), "s")
        >>> asked[0].id, asked[-1].id
        ('core.project-name', 'robustness.defect')
    """
    rng = Rng(stream + "/questions")
    out = _facts.core_questions(dataset, rng)
    tests = _records(dataset.table("pmt_tests"))
    units = _units(dataset.table("pmt_tests"))
    length, pressure, modulus = (
        units["test_depth"],
        units["limit_pressure"],
        units["menard_modulus"],
    )
    other_pressure = "psi" if pressure == "kPa" else "kPa"
    other_modulus = "tonf[US]/ft2" if modulus == "MPa" else "MPa"

    def at(row: dict[str, str]) -> str:
        return f"of the pressuremeter test at {row['test_depth']} {length} in {row['location_id']}"

    out.append(
        _count_question(
            "values.tests", "How many pressuremeter tests does the file hold?", str(len(tests))
        )
    )
    limit = rng.choice([r for r in tests if r["limit_pressure"]])
    out.append(
        _number_question(
            "values.limit-pressure",
            f"What is the limit pressure {at(limit)}?",
            (limit["limit_pressure"], pressure),
            other_pressure,
        )
    )
    stiff = rng.choice(tests)
    out.append(
        _number_question(
            "values.modulus",
            f"What is the pressuremeter (Menard) modulus {at(stiff)}?",
            (stiff["menard_modulus"], modulus),
            other_modulus,
        )
    )
    unreached = next(r for r in tests if not r["limit_pressure"])
    out.append(
        _number_question(
            "values.no-limit-pressure",
            f"One pressuremeter test in {unreached['location_id']} reports no limit"
            " pressure. At what depth is it?",
            (unreached["test_depth"], length),
            length,
        )
    )
    loops = _records(dataset.table("pmt_loops"))
    looped = {(r["location_id"], r["test_depth"]) for r in loops}
    out.append(
        _count_question(
            "values.tests-with-loops",
            "How many of the pressuremeter tests have at least one unload-reload loop?",
            str(len(looped)),
        )
    )
    loop = rng.choice(loops)
    out.append(
        _number_question(
            "values.loop-modulus",
            f"What is the shear modulus of unload-reload loop {loop['loop']} {at(loop)}?",
            (loop["shear_modulus"], modulus),
            other_modulus,
        )
    )
    out.append(_facts.defect_question(rng))
    return tuple(out)


# --- the reference file -----------------------------------------------------------------


def _write_test(
    root: etree._Element,
    ident: str,
    refs: tuple[str, str],
    row: dict[str, str],
    units: dict[str, str],
) -> etree._Element:
    """One ``Test`` at its depth, and its ``PressuremeterTest`` with the probe's size."""
    test = _test(root, ident, refs)
    columns = tuple((key, key, units[column]) for column, key in RESULTS)
    values: list[tuple[str, ...]] = [tuple(row[column] for column, _ in RESULTS)]
    _result(test, ident, (f"{refs[1]}-lrs", row["test_depth"]), columns, values)
    procedure = _add(_add(test, "procedure"), PROCEDURE, gml__id=f"{ident}.procedure")
    _add(procedure, _DIAMETER, row["probe_diameter"], uom=units["probe_diameter"])
    return procedure


def _write_loop(
    procedure: etree._Element, ident: str, row: dict[str, str], units: dict[str, str]
) -> None:
    """One unload-reload loop: a ``trial`` of the procedure, numbered by ``trialNo``."""
    trial = _add(_add(procedure, TRIAL), "PressuremeterTestTrial", gml__id=ident)
    _add(trial, _TRIAL_NO, row["loop"])
    for column, element in _LOOP_COLUMNS:
        if units[column]:
            _add(trial, element, row[column], uom=units[column])
        else:
            _add(trial, element, row[column])


def reference_xml(dataset: Dataset) -> bytes:
    """A pressuremeter ``dataset`` as a valid DIGGS 3.0.0 document, values in their units.

    Each boring is a ``Borehole`` with its own linear referencing system; each test is a
    ``Test`` at its depth with a ``PressuremeterTest`` procedure, and each unload-reload
    loop a ``trial`` of that procedure. The same dataset always gives the same bytes.

    Examples:
        >>> import pydiggs
        >>> xml = reference_xml(build("1.0-beta.3|pressuremeter|demo", "ABC123"))
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
        ident = idents[row["location_id"]] = f"location-{i}"
        feature = _add(_add(root, "samplingFeature"), "Borehole", gml__id=ident)
        _add(feature, "gml:name", row["location_id"])
        _add(feature, "projectRef", xlink__href=f"#{project}")
        _place(feature, ident, row, unit)
        _add(feature, "totalMeasuredDepth", row["total_depth"], uom=unit)
    tests, loops = dataset.table("pmt_tests"), dataset.table("pmt_loops")
    made = _loops_of(dataset)
    for i, row in enumerate(_records(tests), start=1):
        ident = f"pressuremeter-{i}"
        refs = (project, idents[row["location_id"]])
        procedure = _write_test(root, ident, refs, row, _units(tests))
        of_test = made.get((row["location_id"], row["test_depth"]), {})
        for number in sorted(of_test):
            _write_loop(procedure, f"{ident}.loop-{number}", of_test[number], _units(loops))
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))


# --- what a file is observed to hold ----------------------------------------------------


def _loop_number(held: object) -> int | None:
    """The number a ``trialNo`` gives: an ``xs:positiveInteger``, else None."""
    text = held.strip() if isinstance(held, str) else ""
    if not _POSITIVE_INTEGER.fullmatch(text):
        return None
    return int(text) or None


def _loop_value(held: object) -> tuple[float, str] | None:
    """A value of a loop in SI: a measure with its unit, or a plain number (a strain)."""
    if isinstance(held, str):
        number = _float(held)
        return None if number is None else (number, "")
    return _facts._param_si(held)


def observe(model: Model) -> Observed:
    """What a DIGGS file holds of pressuremeter tests, for :func:`_facts.match`.

    Everything the generic observation reads (results at their depths, and procedure
    parameters such as ``pressuremeterDiameter`` as ``@<name>`` at every depth their test
    has a result at), plus the values of each unload-reload loop: the elements of each
    ``trial`` of a ``PressuremeterTest``, keyed by the loop's ``trialNo``
    (``trial[1]:@shearModulus``) at the test's depths. A trial with no ``trialNo`` names
    no loop and is not read, and neither is a value whose unit the reader cannot
    convert. The model is untrusted: at most ``_facts.MAX_PLACED`` loop readings are
    made, each distinct value of a loop once per depth.

    Examples:
        >>> import pydiggs
        >>> data = build("1.0-beta.3|pressuremeter|demo", "ABC123")
        >>> seen = observe(pydiggs.read_bytes(reference_xml(data)))
        >>> all(_facts.match(f, seen).status == "found" for f in facts(data))
        True
    """
    seen = _facts.observe(model, nulls=True)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for where, found in seen.readings.items():
        readings[where].extend(found)
    placing = _facts._Placing()
    elements = tuple(element for _, element in _LOOP_COLUMNS)
    for measurement in model.measurements:
        procedure = measurement.procedure
        if procedure is None or procedure.type != PROCEDURE:
            continue
        feature = _facts._feature_key(model, measurement)
        if feature is None:
            continue
        depths = {
            d
            for table in measurement.results
            for d in _facts._depths(model, table)
            if d is not None
        }
        values = []
        for component in procedure.components:
            number = _loop_number(component.params.get(_TRIAL_NO))
            if component.role != TRIAL or number is None:
                continue
            for element in elements:
                held = _loop_value(component.params.get(element))
                if held is not None:
                    values.append((loop_key(number, element), held[0], held[1]))
        for key, depth, value, unit in placing.spread(values, depths):
            readings[(feature, PROCEDURE, key)].append((depth, value, unit))
    for found in readings.values():
        found.sort()
    return Observed(seen.project_names, seen.named, seen.totals, dict(readings))
