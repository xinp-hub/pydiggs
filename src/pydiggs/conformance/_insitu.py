"""In-situ strength tests: field vane tests and a flat plate dilatometer sounding.

A seeded, synthetic programme of one or two borings with field vane tests (peak and
remoulded: the maximum torque and the undrained shear strength of each, as the field
sheet reports them, with the vane's size) and one dilatometer sounding (the A, B and C
readings at each depth, with the membrane calibrations). The data is written for the
suite and is not from any project. Every value is carried as reported: the suite
computes no strength from a torque, no sensitivity and no dilatometer index.

Traps a correct exporter passes without noticing: a vane test with no remoulded reading
(its remoulded torque and strength are empty, not zero); C readings at only a few depths
of the sounding (the other cells are empty); strengths in pounds per square foot,
torques in pound-force feet, vane sizes in inches and dilatometer readings in psi for
about half the seeds, and kPa, N.m, mm and bar for the others; and a boring name with a
space.

DIGGS 3.0.0 shapes the file: each vane test is a ``Test`` whose result sits at the test
depth and whose procedure is an ``InsituVaneTest`` holding the peak and remoulded torques
as ``intactVaneTest`` and ``remoldedVaneTest`` measurement data, and the vane's size as
``vaneDiameter`` and ``vaneHeight``. The DIGGS property dictionary has no remoulded vane
strength: the peak strength is ``shear_strength_undrained`` and the remoulded strength is
carried as ``shear_strength_undrained_residual``, the dictionary's strength after large
shear displacement, which is what a remoulded vane reading is. The sounding is one
``Test`` whose ``FlatPlateDilatometerTest`` gives ``deltaA`` and ``deltaB`` and whose
result rows (``a_pressure``, ``b_pressure``, ``c_pressure``) sit at their depths.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from lxml import etree

from pydiggs.conformance import _facts
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._piles import _add, _place, _qname, _records, _units
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project
from pydiggs.model import Model

__all__ = [
    "DILATOMETER",
    "INTACT",
    "REMOLDED",
    "SCENARIO",
    "VANE",
    "build",
    "facts",
    "observe",
    "questions",
    "reference_xml",
]

#: The scenario's name, as a :class:`Dataset` states it.
SCENARIO: Final = "in-situ-strength"
VANE: Final = "InsituVaneTest"
DILATOMETER: Final = "FlatPlateDilatometerTest"

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

_LBF: Final = 4.4482216152605  # newtons, exact (international pound-force)
#: Source unit -> (factor to SI, SI unit), for every unit the tables use.
_SI: Final = {
    "": (1.0, ""),
    "m": (1.0, "m"),
    "ft": (0.3048, "m"),
    "mm": (0.001, "m"),
    "in": (0.0254, "m"),
    "kPa": (1000.0, "Pa"),
    "bar": (100_000.0, "Pa"),
    "lbf/ft2": (_LBF / 0.3048**2, "Pa"),
    "psi": (_LBF / 0.0254**2, "Pa"),
    "N.m": (1.0, "N.m"),
    "lbf.ft": (_LBF * 0.3048, "N.m"),
}
#: The measurement-data roles of an ``InsituVaneTest`` and the torque each holds.
INTACT: Final = "intactVaneTest"
REMOLDED: Final = "remoldedVaneTest"
_TORQUE: Final = "maximumMeasuredTorque"
#: Vane-table column -> the key its value is observed under.
_VANE_KEYS: Final = (
    ("peak_strength", "shear_strength_undrained"),
    ("remoulded_strength", "shear_strength_undrained_residual"),
    ("peak_torque", f"{INTACT}:@{_TORQUE}"),
    ("remoulded_torque", f"{REMOLDED}:@{_TORQUE}"),
    ("vane_diameter", "@vaneDiameter"),
    ("vane_height", "@vaneHeight"),
)
#: Dilatometer-table column -> its key in the DIGGS property dictionary.
_DMT_KEYS: Final = (
    ("a_reading", "a_pressure"),
    ("b_reading", "b_pressure"),
    ("c_reading", "c_pressure"),
)
_CALIBRATION_KEYS: Final = (("delta_a", "@deltaA"), ("delta_b", "@deltaB"))


@dataclass(frozen=True, slots=True)
class _System:
    """The units of one dataset and the ranges its numbers are drawn from."""

    length: str
    size: str
    strength: str
    torque: str
    reading: str
    step: Decimal
    vanes: tuple[tuple[str, str], ...]
    peak: tuple[int, int, int]
    torques: tuple[int, int, int]
    a_reading: tuple[int, int, int]


_METRIC: Final = _System(
    length="m",
    size="mm",
    strength="kPa",
    torque="N.m",
    reading="bar",
    step=Decimal("0.20"),
    vanes=(("65", "130"), ("55", "110"), ("75", "150")),
    peak=(80, 900, 1),  # 8.0 to 90.0 kPa
    torques=(150, 1800, 1),  # 15.0 to 180.0 N.m
    a_reading=(80, 900, 2),  # 0.80 to 9.00 bar
)
_IMPERIAL: Final = _System(
    length="ft",
    size="in",
    strength="lbf/ft2",
    torque="lbf.ft",
    reading="psi",
    step=Decimal("0.50"),
    vanes=(("2.50", "5.00"), ("2.00", "4.00"), ("3.00", "6.00")),
    peak=(17, 190, -1),  # 170 to 1900 psf, to the nearest 10
    torques=(110, 1300, 1),  # 11.0 to 130.0 lbf.ft
    a_reading=(120, 1300, 1),  # 12.0 to 130.0 psi
)


# --- the dataset ------------------------------------------------------------------------


def _scaled(rng: Rng, span: tuple[int, int, int]) -> str:
    low, high, places = span
    return _fixed(Decimal(rng.integer(low, high)).scaleb(-places), max(places, 0))


def _vane_tests(rng: Rng, names: tuple[str, ...], system: _System) -> Table:
    """Three to five vane tests per boring; one test in the programme has no remoulded."""
    rows: list[tuple[str, ...]] = []
    for name in names:
        here = rng.fork(name)
        diameter, height = rng.choice(system.vanes)
        depth = system.step * 5 * here.integer(4, 12)
        for _ in range(here.integer(3, 5)):
            peak, torque = _scaled(here, system.peak), _scaled(here, system.torques)
            # A remoulded reading is a third to two thirds of its peak, as a field
            # sheet reports both; it is drawn, not worked out from anything.
            share = Decimal(here.integer(15, 65)) / 100
            remoulded = _fixed(Decimal(peak) * share, max(system.peak[2], 0))
            remoulded_torque = _fixed(Decimal(torque) * share, system.torques[2])
            rows.append(
                (
                    name,
                    _fixed(depth, 2),
                    diameter,
                    height,
                    torque,
                    peak,
                    remoulded_torque,
                    remoulded,
                )
            )
            depth += system.step * 5 * here.integer(2, 6)
    missing = rng.integer(0, len(rows) - 1)
    rows[missing] = (*rows[missing][:6], "", "")
    return Table(
        "vane_tests",
        (
            Field("location_id", "", "The boring the test is in"),
            Field("test_depth", system.length, "Depth of the centre of the vane"),
            Field("vane_diameter", system.size, "Vane diameter"),
            Field("vane_height", system.size, "Vane height"),
            Field("peak_torque", system.torque, "Maximum torque, intact (peak) test"),
            Field("peak_strength", system.strength, "Peak undrained shear strength, as reported"),
            Field(
                "remoulded_torque",
                system.torque,
                "Maximum torque, remoulded test; empty when no remoulded test was made",
            ),
            Field(
                "remoulded_strength",
                system.strength,
                "Remoulded undrained shear strength, as reported; empty when no remoulded"
                " test was made. DIGGS 3.0.0 has no remoulded property: give it as"
                " shear_strength_undrained_residual",
            ),
        ),
        tuple(rows),
    )


def _dilatometer(rng: Rng, name: str, system: _System) -> tuple[Table, Table]:
    """One sounding: the membrane calibrations, then A, B and C at each depth."""
    count = rng.integer(10, 24)
    depth = system.step * rng.integer(5, 10)
    with_c = {rng.integer(1, count - 1) for _ in range(3)}
    rows: list[tuple[str, ...]] = []
    for i in range(count):
        a = Decimal(_scaled(rng, system.a_reading))
        b = a * Decimal(rng.integer(140, 400)) / 100
        places = system.a_reading[2]
        c = _fixed(a * Decimal(rng.integer(30, 90)) / 100, places) if i in with_c else ""
        rows.append((name, _fixed(depth, 2), _fixed(a, places), _fixed(b, places), c))
        depth += system.step
    places = system.a_reading[2]
    span = (5, 30) if system.reading == "bar" else (10, 50)
    calibration = (
        name,
        _fixed(Decimal(rng.integer(*span)).scaleb(-places), places),
        _fixed(Decimal(rng.integer(*span)).scaleb(-places) * 2, places),
    )
    tests = Table(
        "dmt_tests",
        (
            Field("location_id", "", "The sounding"),
            Field("delta_a", system.reading, "Membrane calibration, delta A"),
            Field("delta_b", system.reading, "Membrane calibration, delta B"),
        ),
        (calibration,),
    )
    readings = Table(
        "dmt_readings",
        (
            Field("location_id", "", "The sounding"),
            Field("depth", system.length, "Depth of the blade"),
            Field("a_reading", system.reading, "A reading (lift-off)"),
            Field("b_reading", system.reading, "B reading (expansion)"),
            Field("c_reading", system.reading, "C reading (closing); empty where not taken"),
        ),
        tuple(rows),
    )
    return tests, readings


def build(seed_stream: str, token: str) -> Dataset:
    """The in-situ strength dataset for ``seed_stream``; the same arguments give the same data.

    Examples:
        >>> data = build("1.0-beta.2|in-situ-strength|demo", "ABC123")
        >>> [t.name for t in data.tables]
        ['project', 'locations', 'vane_tests', 'dmt_tests', 'dmt_readings']
        >>> data == build("1.0-beta.2|in-situ-strength|demo", "ABC123")
        True
    """
    rng = Rng(seed_stream)
    system = _IMPERIAL if rng.integer(0, 1) == 1 else _METRIC
    number = rng.integer(1, 60)
    borings = (f"VB {number}A", f"BH-{number + 1:02d}")[: rng.integer(1, 2)]
    sounding = f"DMT-{number + 2:02d}"
    vane = _vane_tests(rng.fork("vane"), borings, system)
    dmt_tests, dmt = _dilatometer(rng.fork("dmt"), sounding, system)
    deepest: dict[str, Decimal] = {}
    for name, depth, *_ in (*vane.rows, *dmt.rows):
        deepest[name] = max(deepest.get(name, Decimal(0)), Decimal(depth))
    extra = Decimal(2) if system.length == "m" else Decimal(5)
    located = _locations(rng.fork("loc"), (*borings, sounding), "borehole", system.length)
    kinds = {sounding: "sounding"}
    fields = (*located.fields, Field("total_depth", system.length, "Final depth of the hole"))
    rows = tuple(
        (r[0], kinds.get(r[0], r[1]), *r[2:], _fixed(deepest[r[0]] + extra, 2))
        for r in located.rows
    )
    locations = Table("locations", fields, rows)
    return Dataset(
        SCENARIO, token, (_project(rng.fork("proj"), token), locations, vane, dmt_tests, dmt)
    )


# --- the answer key ---------------------------------------------------------------------


def _number(text: str, unit: str) -> tuple[float, float]:
    factor = _SI[unit][0]
    return float(Decimal(text)) * factor, _facts._half_last_place(text, factor)


def _fact(where: tuple[str, str, str, str], column: str, text: str, unit: str, *, key: str) -> Fact:
    """One value; ``where`` is (location, procedure, depth as written, depth unit).

    An empty cell is an ``absent`` fact: a file that gives a value there invents it.
    """
    location, procedure, depth_text, depth_unit = where
    depth, depth_tol = _number(depth_text, depth_unit)
    value, tol = _number(text or "0", unit)
    place = f"at {depth_text} {depth_unit} in {location!r}"
    fact = Fact(
        id=f"{SCENARIO}/{location}/{depth_text}/{column}",
        group="fidelity",
        kind="value",
        shown=f"{column} {text} {unit} {place}",
        feature=location,
        procedure=procedure,
        key=key,
        depth=depth,
        depth_tol=depth_tol,
        value=value,
        tol=tol,
        si_unit=_SI[unit][1],
    )
    if text:
        return fact
    return replace(fact, kind="absent", shown=f"no {column} {place} (the source leaves it empty)")


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of an in-situ strength ``dataset``, from its tables alone.

    Core: the project's name, every boring and sounding and its depth. Fidelity: each
    vane test's peak and remoulded strength and torque and the vane's size at the test
    depth; each dilatometer reading at its depth; and the membrane calibrations at the
    first reading's depth. An empty cell is an ``absent`` fact.

    Examples:
        >>> key = facts(build("1.0-beta.2|in-situ-strength|demo", "ABC123"))
        >>> key[0].id, any(f.procedure == "InsituVaneTest" for f in key)
        ('core/project', True)
    """
    out = _facts.core_facts(dataset)
    vane = dataset.table("vane_tests")
    units = _units(vane)
    for row in _records(vane):
        where = (row["location_id"], VANE, row["test_depth"], units["test_depth"])
        out.extend(
            _fact(where, column, row[column], units[column], key=key) for column, key in _VANE_KEYS
        )
    readings = dataset.table("dmt_readings")
    units = _units(readings)
    first: dict[str, str] = {}
    for row in _records(readings):
        first.setdefault(row["location_id"], row["depth"])
        where = (row["location_id"], DILATOMETER, row["depth"], units["depth"])
        out.extend(
            _fact(where, column, row[column], units[column], key=key) for column, key in _DMT_KEYS
        )
    calibrations = dataset.table("dmt_tests")
    for row in _records(calibrations):
        where = (row["location_id"], DILATOMETER, first[row["location_id"]], units["depth"])
        out.extend(
            _fact(where, column, row[column], _units(calibrations)[column], key=key)
            for column, key in _CALIBRATION_KEYS
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
    return Question(qid, qid.partition(".")[0], "count", text, "", expected)


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for an in-situ strength ``dataset``.

    Examples:
        >>> asked = questions(build("s", "ABC123"), "s")
        >>> asked[0].id, asked[-1].id
        ('core.project-name', 'robustness.defect')
    """
    rng = Rng(stream + "/questions")
    out = _facts.core_questions(dataset, rng)
    vane = _records(dataset.table("vane_tests"))
    units = _units(dataset.table("vane_tests"))
    length, strength = units["test_depth"], units["peak_strength"]
    other = "kPa" if strength == "lbf/ft2" else "lbf/ft2"
    out.append(
        _count_question(
            "values.vane-tests", "How many vane tests does the file hold?", str(len(vane))
        )
    )
    peak = rng.choice(vane)
    at = f"of the vane test at {peak['test_depth']} {length} in {peak['location_id']}"
    out.append(
        _number_question(
            "values.peak",
            f"What is the peak undrained shear strength {at}?",
            (peak["peak_strength"], strength),
            other,
        )
    )
    remoulded = rng.choice([r for r in vane if r["remoulded_strength"]])
    at = f"of the vane test at {remoulded['test_depth']} {length} in {remoulded['location_id']}"
    out.append(
        _number_question(
            "values.remoulded",
            f"What is the remoulded undrained shear strength {at}?",
            (remoulded["remoulded_strength"], strength),
            other,
        )
    )
    missing = next(r for r in vane if not r["remoulded_strength"])
    out.append(
        _number_question(
            "values.no-remoulded",
            f"One vane test in {missing['location_id']} has no remoulded reading. At what"
            " depth is it?",
            (missing["test_depth"], length),
            length,
        )
    )
    readings = _records(dataset.table("dmt_readings"))
    reading_unit = _units(dataset.table("dmt_readings"))["a_reading"]
    sounding = readings[0]["location_id"]
    out.append(
        _count_question(
            "values.dmt-readings",
            f"At how many depths does the dilatometer sounding {sounding} give readings?",
            str(len(readings)),
        )
    )
    row = rng.choice(readings)
    out.append(
        _number_question(
            "values.dmt-b",
            f"What is the B reading at {row['depth']} {length} in {sounding}?",
            (row["b_reading"], reading_unit),
            "kPa",
        )
    )
    out.append(
        _count_question(
            "values.dmt-c",
            f"At how many depths does {sounding} give a C reading?",
            str(sum(1 for r in readings if r["c_reading"])),
        )
    )
    out.append(_facts.defect_question(rng))
    return tuple(out)


# --- the reference file -----------------------------------------------------------------


def _result(
    test: etree._Element,
    ident: str,
    where: tuple[str, str],
    columns: tuple[tuple[str, str, str], ...],
    rows: list[tuple[str, ...]],
) -> None:
    """A ``TestResult`` at one depth or at one depth per row, with its ``ResultSet``."""
    lrs, depths = where
    outcome = _add(_add(test, "outcome"), "TestResult", gml__id=f"{ident}.result")
    shape = "PointLocation" if len(rows) == 1 else "MultiPointLocation"
    point = _add(_add(outcome, "location"), shape, gml__id=f"{ident}.location")
    point.set("srsName", f"#{lrs}")
    point.set("srsDimension", "1")
    _add(point, "gml:pos" if len(rows) == 1 else "gml:posList", depths)
    result = _add(_add(outcome, "results"), "ResultSet")
    properties = _add(_add(_add(result, "parameters"), "PropertyParameters"), "properties")
    for index, (name, key, uom) in enumerate(columns, start=1):
        prop = _add(properties, "Property", index=str(index))
        _add(prop, "propertyName", name)
        _add(prop, "typeData", "double")
        _add(prop, "propertyClass", key, codeSpace=f"{_PROPERTIES}#{key}")
        _add(prop, "uom", uom)
    _add(result, "dataValues", " ".join(",".join(row) for row in rows), cs=",", ts=" ")


def _test(root: etree._Element, ident: str, refs: tuple[str, str]) -> etree._Element:
    project, feature = refs
    test = _add(_add(root, "measurement"), "Test", gml__id=ident)
    _add(test, "projectRef", xlink__href=f"#{project}")
    _add(test, "samplingFeatureRef", xlink__href=f"#{feature}")
    return test


def _write_vane(
    root: etree._Element,
    ident: str,
    row: dict[str, str],
    units: dict[str, str],
    refs: tuple[str, str],
) -> None:
    test = _test(root, ident, refs)
    columns = (
        ("peak_strength", "shear_strength_undrained", units["peak_strength"]),
        ("remoulded_strength", "shear_strength_undrained_residual", units["remoulded_strength"]),
    )
    values: list[tuple[str, ...]] = [(row["peak_strength"], row["remoulded_strength"])]
    _result(test, ident, (f"{refs[1]}-lrs", row["test_depth"]), columns, values)
    procedure = _add(_add(test, "procedure"), VANE, gml__id=f"{ident}.procedure")
    for role, column in ((INTACT, "peak_torque"), (REMOLDED, "remoulded_torque")):
        if row[column]:
            data = _add(_add(procedure, role), "VaneTestMeasurementData", gml__id=f"{ident}.{role}")
            _add(data, _TORQUE, row[column], uom=units[column])
    _add(procedure, "vaneDiameter", row["vane_diameter"], uom=units["vane_diameter"])
    _add(procedure, "vaneHeight", row["vane_height"], uom=units["vane_height"])


def _write_dilatometer(
    root: etree._Element, dataset: Dataset, ident: str, refs: tuple[str, str]
) -> None:
    calibration, readings = dataset.table("dmt_tests"), dataset.table("dmt_readings")
    units = _units(readings)
    test = _test(root, ident, refs)
    rows = _records(readings)
    columns = tuple((key, key, units[column]) for column, key in _DMT_KEYS)
    values = [tuple(r[c] for c, _ in _DMT_KEYS) for r in rows]
    _result(test, ident, (f"{refs[1]}-lrs", " ".join(r["depth"] for r in rows)), columns, values)
    procedure = _add(_add(test, "procedure"), DILATOMETER, gml__id=f"{ident}.procedure")
    row, calibration_units = _records(calibration)[0], _units(calibration)
    _add(procedure, "deltaA", row["delta_a"], uom=calibration_units["delta_a"])
    _add(procedure, "deltaB", row["delta_b"], uom=calibration_units["delta_b"])


def reference_xml(dataset: Dataset) -> bytes:
    """An in-situ strength ``dataset`` as a valid DIGGS 3.0.0 document, values in their units.

    Each boring is a ``Borehole`` and the dilatometer sounding a ``Sounding``, each with
    its own linear referencing system; each vane test is a ``Test`` at its depth with an
    ``InsituVaneTest`` procedure; the sounding's readings are one ``Test`` with a
    ``FlatPlateDilatometerTest`` procedure. The same dataset always gives the same bytes.

    Examples:
        >>> import pydiggs
        >>> xml = reference_xml(build("1.0-beta.2|in-situ-strength|demo", "ABC123"))
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
        element = "Sounding" if row["kind"] == "sounding" else "Borehole"
        feature = _add(_add(root, "samplingFeature"), element, gml__id=ident)
        _add(feature, "gml:name", row["location_id"])
        _add(feature, "projectRef", xlink__href=f"#{project}")
        _place(feature, ident, row, unit)
        _add(feature, "totalMeasuredDepth", row["total_depth"], uom=unit)
    vane = dataset.table("vane_tests")
    for i, row in enumerate(_records(vane), start=1):
        refs = (project, idents[row["location_id"]])
        _write_vane(root, f"vane-{i}", row, _units(vane), refs)
    sounding = dataset.table("dmt_tests").rows[0][0]
    _write_dilatometer(root, dataset, "dilatometer-1", (project, idents[sounding]))
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))


# --- what a file is observed to hold ----------------------------------------------------


def observe(model: Model) -> Observed:
    """What a DIGGS file holds of vane and dilatometer tests, for :func:`_facts.match`.

    Everything the generic observation reads (results at their depths, and procedure
    parameters such as ``vaneDiameter`` or ``deltaA`` as ``@<name>`` at every depth
    their test has a result at), plus the maximum torque of each vane test's intact and
    remoulded measurement data, keyed ``intactVaneTest:@maximumMeasuredTorque`` and
    ``remoldedVaneTest:@maximumMeasuredTorque`` at the test's depths. A torque whose
    unit the reader cannot convert is not read. The model is untrusted: at most
    ``_facts.MAX_PLACED`` torque readings are made, each distinct torque of a role once
    per depth.

    Examples:
        >>> import pydiggs
        >>> data = build("1.0-beta.2|in-situ-strength|demo", "ABC123")
        >>> seen = observe(pydiggs.read_bytes(reference_xml(data)))
        >>> all(_facts.match(f, seen).status == "found" for f in facts(data))
        True
    """
    seen = _facts.observe(model, nulls=True)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for where, found in seen.readings.items():
        readings[where].extend(found)
    placing = _facts._Placing()
    for measurement in model.measurements:
        procedure = measurement.procedure
        if procedure is None or procedure.type != VANE:
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
        torques = []
        for component in procedure.components:
            if component.role not in (INTACT, REMOLDED):
                continue
            torque = _facts._param_si(component.params.get(_TORQUE))
            if torque is not None:
                torques.append((f"{component.role}:@{_TORQUE}", torque[0], torque[1]))
        # Each distinct torque of a role once per depth, within the observation's limit:
        # a test with thousands of components and thousands of rows is not multiplied out.
        for key, depth, value, unit in placing.spread(torques, depths):
            readings[(feature, VANE, key)].append((depth, value, unit))
    for found in readings.values():
        found.sort()
    return Observed(seen.project_names, seen.named, seen.totals, dict(readings))
