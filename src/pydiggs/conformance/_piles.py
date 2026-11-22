"""Deep foundations: piles and their pile driving records, a scenario of the suite.

A seeded, synthetic pier of two or three driven piles beside one test boring: each pile's
type, size, lengths and nominal capacity, the hammer, and a driving record of 15 to 40
increments per pile (the depth of the pile tip at the end of each increment, the blows it
took, the increment and the hammer stroke). The data is written for the suite and is not
from any project.

Traps a correct exporter passes without noticing: a pile name with a space, lengths in
feet with sizes in inches for about half the seeds, a short last increment with a very
high blow count (refusal), an increment where the pile ran (0 or 1 blow; no stroke when
it took none, and an empty cell is not a zero), and section sizes in a unit other than
the depths.

DIGGS 3.0.0 shapes the file: a pile must reference a boring, sounding, trial pit or
station (``samplingFeatureRef``), so the test boring is part of the data; one
``PileDrivingActivity`` refers to exactly one pile, so there is one activity per pile;
and the driving record is a ``PileDrivingRecord`` whose ``pileTipLocation`` gives one tip
depth per result row, in the pile's own linear referencing system, with its values coded
in the pile-driving property dictionary (``pil_properties.xml``).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import cache
from typing import Final

from lxml import etree

from pydiggs.conformance import _facts
from pydiggs.conformance._facts import DEFECTS, Fact, Observed, Question
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project
from pydiggs.model import ExtraNode, Feature, Model
from pydiggs.units import UnitConverter

__all__ = ["RECORD", "SCENARIO", "build", "facts", "observe", "questions", "reference_xml"]

#: The scenario's name, as a :class:`Dataset` states it.
SCENARIO: Final = "piles"
#: The "procedure" a driving record's readings are kept under in :class:`Observed`.
RECORD: Final = "PileDrivingRecord"

_DIGGS: Final = "http://diggsml.org/schemas/3"
_GML: Final = "http://www.opengis.net/gml/3.2"
_GLR: Final = "http://www.opengis.net/gml/3.3/lr"
_XLINK: Final = "http://www.w3.org/1999/xlink"
_XSI: Final = "http://www.w3.org/2001/XMLSchema-instance"
_NSMAP: Final = {None: _DIGGS, "gml": _GML, "glr": _GLR, "xlink": _XLINK, "xsi": _XSI}
_PREFIXES: Final = {"gml": _GML, "glr": _GLR, "xlink": _XLINK, "xsi": _XSI}
_SCHEMA_LOCATION: Final = "http://diggsml.org/schemas/3 https://diggsml.org/schemas/3.0.0/Diggs.xsd"
_PIL: Final = "https://diggsml.org/def/codes/DIGGS/0.1/pil_properties.xml"
_ID_SPACE: Final = "urn:x-pydiggs:id"
_LRM_NAMES: Final = {"m": "trueMeters", "ft": "trueFootage"}

#: Pile type (as the table says it) -> its DIGGS 3.0.0 element.
ELEMENTS: Final = {
    "steel pipe": "SteelPipePile",
    "steel H": "SteelHPile",
    "concrete": "ConcretePile",
}
#: What the ``width`` of each pile type is.
_WIDTH: Final = {"steel pipe": "outside diameter", "steel H": "flange width", "concrete": "side"}
#: Source unit -> (factor to SI, SI unit), for every unit the pile tables use.
_SI: Final = {
    "": (1.0, ""),
    "m": (1.0, "m"),
    "ft": (0.3048, "m"),
    "mm": (0.001, "m"),
    "in": (0.0254, "m"),
    "kN": (1000.0, "N"),
    "klbf": (4448.2216152605, "N"),  # 1000 international pounds-force, exact
}
#: Driving-table column -> its key in the pile-driving dictionary.
_RECORD_KEYS: Final = (
    ("blows", "blow_count"),
    ("pen_increment", "pen_increment"),
    ("stroke", "stroke"),
)
#: Pile-table column -> the key its value is observed under (a pile parameter).
_PILE_KEYS: Final = (
    ("total_length", "@totalPileLength"),
    ("embedded_length", "@lengthBelowGroundSurface"),
    ("width", "@width"),
    ("wall_thickness", "@wallThickness"),
    ("nominal_capacity", "@nominalCapacity"),
)
#: At most this many nodes of the model's unconsumed (``extra``) elements are looked at.
_MAX_NODES: Final = 2_000_000
_NUMBER: Final = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


@dataclass(frozen=True, slots=True)
class _System:
    """The units of one dataset and the ranges its numbers are drawn from."""

    length: str
    size: str
    force: str
    step: Decimal
    partials: tuple[str, ...]
    start: tuple[int, int]
    stroke: tuple[int, int, int]
    stickup: tuple[int, int]
    capacity: tuple[int, int]
    ram: tuple[int, int]
    sections: dict[str, tuple[tuple[str, str, str], ...]]


_METRIC: Final = _System(
    length="m",
    size="mm",
    force="kN",
    step=Decimal("0.25"),
    partials=("0.05", "0.10", "0.15"),
    start=(16, 48),
    stroke=(120, 300, 2),
    stickup=(30, 200),
    capacity=(80, 250),
    ram=(200, 600),
    sections={
        "steel pipe": (
            ("PP324x9.5", "324", "9.5"),
            ("PP406x12.7", "406", "12.7"),
            ("PP508x12.7", "508", "12.7"),
            ("PP610x15.9", "610", "15.9"),
        ),
        "steel H": (("HP250x62", "256", ""), ("HP310x79", "306", ""), ("HP360x108", "370", "")),
        "concrete": (("PC305", "305", ""), ("PC356", "356", ""), ("PC406", "406", "")),
    },
)
_IMPERIAL: Final = _System(
    length="ft",
    size="in",
    force="klbf",
    step=Decimal("1.00"),
    partials=("0.25", "0.50", "0.75"),
    start=(10, 25),
    stroke=(40, 100, 1),
    stickup=(100, 600),
    capacity=(18, 56),
    ram=(45, 135),
    sections={
        "steel pipe": (
            ("PP12.75x0.375", "12.75", "0.375"),
            ("PP16x0.500", "16.00", "0.500"),
            ("PP20x0.500", "20.00", "0.500"),
            ("PP24x0.625", "24.00", "0.625"),
        ),
        "steel H": (("HP10x42", "10.08", ""), ("HP12x53", "12.05", ""), ("HP14x73", "14.59", "")),
        "concrete": (
            ("PC12", "12", ""),
            ("PC14", "14", ""),
            ("PC16", "16", ""),
            ("PC18", "18", ""),
        ),
    },
)


# --- the dataset ------------------------------------------------------------------------


def _scaled(rng: Rng, span: tuple[int, int], places: int) -> Decimal:
    return Decimal(rng.integer(*span)).scaleb(-places)


def _drive(rng: Rng, name: str, system: _System) -> list[tuple[str, ...]]:
    """The driving record of one pile: one row per increment, tip depth at its end."""
    count = rng.integer(15, 40)
    run_at = rng.integer(2, count - 4)
    tip = system.step * rng.integer(*system.start)
    blows = rng.integer(2, 8)
    low, high, places = system.stroke
    rows: list[tuple[str, ...]] = []
    for i in range(count):
        last = i == count - 1
        increment = Decimal(rng.choice(system.partials)) if last else system.step
        tip += increment
        if i == run_at:
            taken = rng.integer(0, 1)  # the pile ran
        elif last:
            taken = rng.integer(60, 150)  # refusal over a short increment
        else:
            blows = min(59, max(2, blows + rng.integer(-2, 4)))
            taken = blows
        stroke = "" if taken == 0 else _fixed(_scaled(rng, (low, high), places), places)
        rows.append((name, _fixed(tip, 2), str(taken), _fixed(increment, 2), stroke))
    return rows


def _pile_row(
    rng: Rng, name: str, boring: tuple[str, ...], embedded: str, system: _System
) -> tuple[str, ...]:
    kind = rng.choice(tuple(ELEMENTS))
    designation, width, wall = rng.choice(system.sections[kind])
    east = Decimal(boring[2]) + rng.integer(3, 40)
    north = Decimal(boring[3]) + rng.integer(3, 40)
    ground = Decimal(boring[4]) + _scaled(rng, (-30, 30), 2)
    cutoff = ground - _scaled(rng, (20, 150), 2)
    total = Decimal(embedded) + _scaled(rng, system.stickup, 2)
    tip = rng.choice(("open", "closed")) if kind == "steel pipe" else ""
    capacity = str(rng.integer(*system.capacity) * 10)
    return (
        name,
        kind,
        boring[0],
        _fixed(east, 2),
        _fixed(north, 2),
        _fixed(ground, 2),
        _fixed(cutoff, 2),
        _fixed(total, 2),
        embedded,
        designation,
        width,
        wall,
        tip,
        capacity,
    )


def _piles(rng: Rng, embedded: dict[str, str], boring: tuple[str, ...], system: _System) -> Table:
    unit, size = system.length, system.size
    rows = tuple(
        _pile_row(rng.fork(name), name, boring, depth, system) for name, depth in embedded.items()
    )
    return Table(
        "piles",
        (
            Field("pile_id", "", "The pile's name; carry it exactly"),
            Field("pile_type", "", "steel pipe, steel H or concrete (square, solid)"),
            Field("boring_id", "", "The test boring the pile is designed from"),
            Field("easting", unit, "Easting of the pile on the site grid"),
            Field("northing", unit, "Northing of the pile on the site grid"),
            Field("ground_level", unit, "Ground level at the pile"),
            Field("cutoff_level", unit, "Level the pile is cut off at"),
            Field("total_length", unit, "Total length of the pile as installed"),
            Field("embedded_length", unit, "Length of the pile below the ground surface"),
            Field("designation", "", "The pile's size designation"),
            Field("width", size, "Outside diameter (pipe), flange width (H) or side (square)"),
            Field("wall_thickness", size, "Wall thickness of a pipe pile; empty for others"),
            Field("tip", "", "open or closed (pipe piles); empty for others"),
            Field("nominal_capacity", system.force, "Nominal capacity of the pile"),
        ),
        rows,
    )


def _boring(rng: Rng, name: str, deepest: Decimal, system: _System) -> Table:
    located = _locations(rng, (name,), "borehole", system.length)
    extra = Decimal(5) if system.length == "m" else Decimal(15)
    depth = _fixed((deepest + extra).quantize(Decimal(1)), 2)
    fields = (*located.fields, Field("total_depth", system.length, "Final depth of the hole"))
    return Table(located.name, fields, ((*located.rows[0], depth),))


def _hammer(rng: Rng, system: _System) -> Table:
    places = system.stroke[2]
    rated = _fixed(_scaled(rng, (system.stroke[1] + 10, system.stroke[1] + 60), places), places)
    return Table(
        "hammer",
        (
            Field("hammer_id", "", "The hammer every pile was driven with"),
            Field("make", "", "Make"),
            Field("model", "", "Model"),
            Field("hammer_type", "", "Hammer type"),
            Field("ram_weight", system.force, "Weight of the ram"),
            Field("rated_stroke", system.length, "Rated (maximum) stroke"),
        ),
        (
            (
                f"H-{rng.integer(1, 9)}",
                rng.choice(("Hammerline", "Pileworks", "Northdrive")),
                f"DH-{rng.integer(20, 80)}",
                "diesel",
                _fixed(_scaled(rng, system.ram, 1), 1),
                rated,
            ),
        ),
    )


def _driving_table(rows: list[tuple[str, ...]], system: _System) -> Table:
    unit = system.length
    return Table(
        "driving",
        (
            Field("pile_id", "", "The pile"),
            Field("tip_depth", unit, "Depth of the pile tip below ground at the increment's end"),
            Field("blows", "", "Blows for the increment"),
            Field("pen_increment", unit, "Penetration of the increment"),
            Field("stroke", unit, "Hammer stroke; empty when the increment took no blow"),
        ),
        tuple(rows),
    )


def build(seed_stream: str, token: str) -> Dataset:
    """The piles dataset for ``seed_stream``; the same arguments give the same data.

    Examples:
        >>> data = build("1.0-beta.1|piles|demo", "ABC123")
        >>> [t.name for t in data.tables]
        ['project', 'locations', 'piles', 'hammer', 'driving']
        >>> data == build("1.0-beta.1|piles|demo", "ABC123")
        True
    """
    rng = Rng(seed_stream)
    system = _IMPERIAL if rng.integer(0, 1) == 1 else _METRIC
    number = rng.integer(1, 80)
    names = (f"P-{number}", f"P {number + 1}", f"TP-{number + 2}A")[: rng.integer(2, 3)]
    boring_name = f"TB-{rng.integer(1, 40):02d}"
    driving: list[tuple[str, ...]] = []
    embedded: dict[str, str] = {}
    for name in names:
        rows = _drive(rng.fork("drive/" + name), name, system)
        driving.extend(rows)
        embedded[name] = rows[-1][1]
    deepest = max(Decimal(depth) for depth in embedded.values())
    locations = _boring(rng.fork("loc"), boring_name, deepest, system)
    return Dataset(
        SCENARIO,
        token,
        (
            _project(rng.fork("proj"), token),
            locations,
            _piles(rng.fork("piles"), embedded, locations.rows[0], system),
            _hammer(rng.fork("hammer"), system),
            _driving_table(driving, system),
        ),
    )


def _records(table: Table) -> list[dict[str, str]]:
    names = [f.name for f in table.fields]
    return [dict(zip(names, row, strict=True)) for row in table.rows]


def _units(table: Table) -> dict[str, str]:
    return {f.name: f.unit for f in table.fields}


# --- the answer key ---------------------------------------------------------------------


def _number(text: str, unit: str) -> tuple[float, float]:
    factor = _SI[unit][0]
    return float(Decimal(text)) * factor, _facts._half_last_place(text, factor)


def _core(dataset: Dataset) -> list[Fact]:
    project = dataset.table("project").rows[0][1]
    out = [
        Fact(
            id="core/project",
            group="core",
            kind="project-name",
            shown=f"project named {project!r}",
            text=project,
        )
    ]
    locations = dataset.table("locations")
    unit = _units(locations)["total_depth"]
    for row in _records(locations):
        name = row["location_id"]
        value, tol = _number(row["total_depth"], unit)
        out += [
            Fact(f"core/{name}", "core", "location", f"location {name!r}", feature=name),
            Fact(
                f"core/{name}/total-depth",
                "core",
                "total-depth",
                f"total depth {row['total_depth']} {unit} of {name!r}",
                feature=name,
                value=value,
                tol=tol,
                si_unit=_SI[unit][1],
            ),
        ]
    out += [
        Fact(f"core/{name}", "core", "location", f"pile {name!r}", feature=name)
        for name in dataset.table("piles").column("pile_id")
    ]
    return out


def _value(
    where: tuple[str, str, str, str], column: str, text: str, unit: str, *, key: str
) -> Fact:
    """A fidelity fact. ``where`` is (pile, procedure, depth as written, depth unit).

    A pile's own values have no depth (``""``): they are looked up at depth 0.
    """
    pile, procedure, depth_text, depth_unit = where
    depth, depth_tol = _number(depth_text, depth_unit) if depth_text else (0.0, 0.0)
    value, tol = _number(text, unit)
    shown_unit = f" {unit}" if unit else ""
    place = f" at tip depth {depth_text} {depth_unit}" if depth_text else ""
    return Fact(
        id=f"piles/{pile}/{depth_text}/{column}" if depth_text else f"piles/{pile}/{column}",
        group="fidelity",
        kind="value",
        shown=f"{column} {text}{shown_unit}{place} of pile {pile!r}",
        feature=pile,
        procedure=procedure,
        key=key,
        depth=depth,
        depth_tol=depth_tol,
        value=value,
        tol=0.0 if column == "blows" else tol,  # a blow count is exact
        si_unit=_SI[unit][1],
    )


def _fact(where: tuple[str, str, str, str], column: str, text: str, unit: str, *, key: str) -> Fact:
    """The fact of one cell; an empty cell is an ``absent`` fact.

    The source leaves a value empty on purpose (no stroke when an increment took no blow,
    no wall thickness on an H or concrete pile): a file that gives one there, a zero
    say, invents it, and :func:`_facts.match` reports ``conformance.value-invented``.
    """
    if text:
        return _value(where, column, text, unit, key=key)
    pile, _, depth_text, depth_unit = where
    place = f" at tip depth {depth_text} {depth_unit}" if depth_text else ""
    return replace(
        _value(where, column, "0", unit, key=key),
        kind="absent",
        shown=f"no {column}{place} of pile {pile!r} (the source leaves it empty)",
    )


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of a piles ``dataset``, from its tables alone.

    Core: the project's name, the test boring and its depth, and every pile by name.
    Fidelity: each pile's lengths, width, wall thickness and nominal capacity (at depth
    0), and each driving increment's blows, penetration and stroke at its tip depth. An
    empty cell (a stroke where no blow was struck, the wall thickness of a pile that has
    none) is an ``absent`` fact: the file must give no value there.

    Examples:
        >>> key = facts(build("1.0-beta.1|piles|demo", "ABC123"))
        >>> key[0].id, key[0].group
        ('core/project', 'core')
        >>> any(f.key == "blow_count" and f.tol == 0.0 for f in key)
        True
    """
    out = _core(dataset)
    piles = dataset.table("piles")
    units = _units(piles)
    for row in _records(piles):
        name, element = row["pile_id"], ELEMENTS[row["pile_type"]]
        out.extend(
            _fact((name, element, "", ""), column, row[column], units[column], key=key)
            for column, key in _PILE_KEYS
        )
    driving = dataset.table("driving")
    units = _units(driving)
    for row in _records(driving):
        name, depth = row["pile_id"], row["tip_depth"]
        where = (name, RECORD, depth, units["tip_depth"])
        out.extend(
            _fact(where, column, row[column], units[column], key=key)
            for column, key in _RECORD_KEYS
        )
    return tuple(out)


# --- the import questions ---------------------------------------------------------------


def _number_question(qid: str, text: str, source: tuple[str, str], asked: str) -> Question:
    """A question whose answer is the source value ``(text, unit)`` in the unit ``asked``."""
    expected, tol = _facts._convert(source[0], source[1], asked)
    return Question(qid, qid.partition(".")[0], "number", text, asked, expected, tol)


def _count_question(qid: str, text: str, expected: str) -> Question:
    return Question(qid, qid.partition(".")[0], "count", text, "", expected, 0.0)


def _value_questions(dataset: Dataset, rng: Rng) -> list[Question]:
    piles = {row["pile_id"]: row for row in _records(dataset.table("piles"))}
    rows = _records(dataset.table("driving"))
    length = _units(dataset.table("driving"))["tip_depth"]
    size = _units(dataset.table("piles"))["width"]
    other_length = "m" if length == "ft" else "ft"
    other_size = "mm" if size == "in" else "in"
    pile = rng.choice(tuple(piles))
    mine = [r for r in rows if r["pile_id"] == pile]
    ordinary = rng.choice([r for r in mine[:-1] if int(r["blows"]) > 1])
    ran = next(r for r in mine if int(r["blows"]) <= 1)
    out = [
        _count_question(
            f"values.{label}",
            f"How many blows did the increment of pile {pile} ending at {r['tip_depth']}"
            f" {length} take?",
            r["blows"],
        )
        for label, r in (("blow-count", ordinary), ("blow-count-low", ran))
    ]
    out.append(
        _count_question(
            "values.last-blows",
            f"How many blows did the last increment of pile {pile} take?",
            mine[-1]["blows"],
        )
    )
    deep = rng.choice(tuple(piles))
    last = [r for r in rows if r["pile_id"] == deep][-1]
    out.append(
        _number_question(
            "values.deepest",
            f"At what depth is the tip of pile {deep} at the end of driving?",
            (last["tip_depth"], length),
            other_length,
        )
    )
    sized = piles[rng.choice(tuple(piles))]
    out.append(
        _number_question(
            "values.width",
            f"What is the {_WIDTH[sized['pile_type']]} of pile {sized['pile_id']}?",
            (sized["width"], size),
            other_size,
        )
    )
    return out


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for a piles ``dataset``.

    Which piles and increments are asked about, and which defect the second file has,
    depend on ``stream``; the last question is the defect, as in every scenario.

    Examples:
        >>> asked = questions(build("s", "ABC123"), "s")
        >>> asked[0].id, asked[-1].id
        ('core.project-name', 'robustness.defect')
    """
    rng = Rng(stream + "/questions")
    piles = dataset.table("piles")
    names = piles.column("pile_id")
    unit = _units(piles)["embedded_length"]
    chosen = rng.choice(names)
    embedded = dict(zip(names, piles.column("embedded_length"), strict=True))[chosen]
    out = [
        Question(
            "core.project-name",
            "core",
            "text",
            "What is the project's name?",
            "",
            dataset.table("project").rows[0][1],
        ),
        _count_question("core.piles", "How many piles does the file hold?", str(len(names))),
        _number_question(
            "core.embedded-length",
            f"What is the length of pile {chosen} below the ground surface?",
            (embedded, unit),
            "m" if unit == "ft" else "ft",
        ),
        *_value_questions(dataset, rng),
    ]
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


# --- the reference file -----------------------------------------------------------------


def _qname(name: str) -> str:
    prefix, _, local = name.rpartition(":")
    return f"{{{_PREFIXES[prefix] if prefix else _DIGGS}}}{local}"


def _add(
    parent: etree._Element, name: str, text: str | None = None, **attrs: str
) -> etree._Element:
    """A child ``name`` of ``parent``; attribute names use ``__`` for a prefix colon."""
    element = etree.SubElement(parent, _qname(name))
    for key, value in attrs.items():
        element.set(_qname(key.replace("__", ":")) if "__" in key else key, value)
    if text is not None:
        element.text = text
    return element


def _place(parent: etree._Element, ident: str, row: dict[str, str], unit: str) -> None:
    """Reference point, centre line and linear referencing system of a feature."""
    where = f"{row['easting']} {row['northing']} {row['ground_level']}"
    point = _add(_add(parent, "referencePoint"), "PointLocation", gml__id=f"{ident}.point")
    point.set("srsDimension", "3")
    _add(point, "gml:pos", where)
    line = _add(_add(parent, "centerLine"), "LinearExtent", gml__id=f"{ident}.centerLine")
    line.set("srsDimension", "3")
    _add(line, "gml:posList", where)
    lrs = _add(_add(parent, "linearReferencing"), "LinearSpatialReferenceSystem")
    lrs.set(_qname("gml:id"), f"{ident}-lrs")
    _add(lrs, "gml:identifier", f"{ident}-lrs", codeSpace=_ID_SPACE)
    _add(lrs, "glr:linearElement", xlink__href=f"#{ident}.centerLine")
    method = _add(_add(lrs, "lrm"), "LinearReferencingMethod", gml__id=f"{ident}-lrs.lrm")
    _add(method, "gml:identifier", f"{ident}-lrs.lrm", codeSpace=_ID_SPACE)
    _add(method, "name", _LRM_NAMES[unit])
    _add(method, "type", "absolute")
    _add(method, "units", unit)


def _write_boring(root: etree._Element, dataset: Dataset, project: str) -> str:
    table = dataset.table("locations")
    row = _records(table)[0]
    unit = _units(table)["total_depth"]
    boring = _add(_add(root, "samplingFeature"), "Borehole", gml__id="boring-1")
    _add(boring, "gml:name", row["location_id"])
    _add(boring, "projectRef", xlink__href=f"#{project}")
    _place(boring, "boring-1", row, unit)
    _add(boring, "totalMeasuredDepth", row["total_depth"], uom=unit)
    return "boring-1"


def _write_pile(
    root: etree._Element,
    ident: str,
    row: dict[str, str],
    units: dict[str, str],
    refs: tuple[str, str],
) -> None:
    project, boring = refs
    length, size = units["total_length"], units["width"]
    pile = _add(_add(root, "samplingFeature"), ELEMENTS[row["pile_type"]], gml__id=ident)
    _add(pile, "gml:name", row["pile_id"])
    _add(pile, "projectRef", xlink__href=f"#{project}")
    _place(pile, ident, row, length)
    _add(pile, "samplingFeatureRef", xlink__href=f"#{boring}")
    _add(pile, "groundSurfaceElevation", row["ground_level"], uom=length)
    _add(pile, "cutoffElevation", row["cutoff_level"], uom=length)
    _add(pile, "totalPileLength", row["total_length"], uom=length)
    _add(pile, "lengthBelowGroundSurface", row["embedded_length"], uom=length)
    taper = _add(_add(pile, "taperInterval"), "Taper")
    extent = _add(_add(taper, "intervalLocation"), "LinearExtent", gml__id=f"{ident}.taper")
    extent.set("srsName", f"#{ident}-lrs")
    extent.set("srsDimension", "1")
    # The whole embedded length, from the ground surface (the origin of the pile's LRS).
    _add(extent, "gml:posList", f"0 {row['embedded_length']}")
    _add(taper, "widthAtTop", row["width"], uom=size)
    _add(taper, "widthAtBottom", row["width"], uom=size)
    _add(pile, "nominalCapacity", row["nominal_capacity"], uom=units["nominal_capacity"])
    _add(pile, "pileSizeDesignation", row["designation"])
    if row["pile_type"] == "steel pipe":
        _add(pile, "wallThickness", row["wall_thickness"], uom=units["wall_thickness"])
        _add(pile, "openEnded", "true" if row["tip"] == "open" else "false")
    elif row["pile_type"] == "concrete":
        _add(pile, "shape", "square solid")


def _write_hammer(activity: etree._Element, ident: str, hammer: Table) -> None:
    row, units = _records(hammer)[0], _units(hammer)
    element = _add(_add(activity, "hammer"), "PileDriveHammer", gml__id=ident)
    _add(element, "gml:name", row["hammer_id"])
    _add(element, "make", row["make"])
    _add(element, "modelNumber", row["model"])
    _add(element, "hammerType", row["hammer_type"])
    _add(element, "ramWeight", row["ram_weight"], uom=units["ram_weight"])
    _add(element, "ratedStroke", row["rated_stroke"], uom=units["rated_stroke"])


def _write_results(record: etree._Element, rows: list[dict[str, str]], unit: str) -> None:
    result = _add(_add(record, "pileDrivingRecordResults"), "ResultSet")
    properties = _add(_add(_add(result, "parameters"), "PropertyParameters"), "properties")
    for index, (column, key) in enumerate(_RECORD_KEYS, start=1):
        prop = _add(properties, "Property", index=str(index))
        _add(prop, "propertyName", column)
        _add(prop, "typeData", "integer" if column == "blows" else "double")
        _add(prop, "propertyClass", key, codeSpace=f"{_PIL}#{key}")
        if column != "blows":
            _add(prop, "uom", unit)
    values = " ".join(",".join(row[c] for c, _ in _RECORD_KEYS) for row in rows)
    _add(result, "dataValues", values, cs=",", ts=" ")


def _write_activity(
    root: etree._Element, ident: str, rows: list[dict[str, str]], context: tuple[str, str, Table]
) -> None:
    project, unit, hammer = context
    activity = _add(
        _add(root, "constructionActivity"), "PileDrivingActivity", gml__id=f"{ident}-driving"
    )
    _add(activity, "projectRef", xlink__href=f"#{project}")
    _add(activity, "samplingFeatureRef", xlink__href=f"#{ident}")
    _write_hammer(activity, f"{ident}-hammer", hammer)
    record = _add(
        _add(activity, "pileDrivingRecord"), "PileDrivingRecord", gml__id=f"{ident}-record"
    )
    tips = _add(_add(record, "pileTipLocation"), "MultiPointLocation", gml__id=f"{ident}-tips")
    tips.set("srsName", f"#{ident}-lrs")
    tips.set("srsDimension", "1")
    _add(tips, "gml:posList", " ".join(row["tip_depth"] for row in rows))
    _write_results(record, rows, unit)
    _add(record, "recordType", "manual")
    _add(record, "hammerRef", xlink__href=f"#{ident}-hammer")


def reference_xml(dataset: Dataset) -> bytes:
    """A piles ``dataset`` as a valid DIGGS 3.0.0 document, every value in its table's unit.

    The test boring is a ``Borehole``; each pile is a ``SteelPipePile``, ``SteelHPile``
    or ``ConcretePile`` with its own linear referencing system (origin at the ground
    surface) and refers to the boring; each pile has one ``PileDrivingActivity`` with
    its hammer and one ``PileDrivingRecord`` whose ``pileTipLocation`` gives the tip
    depth of every result row. The same dataset always gives the same bytes.

    Examples:
        >>> import pydiggs
        >>> xml = reference_xml(build("1.0-beta.1|piles|demo", "ABC123"))
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
    boring = _write_boring(root, dataset, project)
    piles = dataset.table("piles")
    idents = {name: f"pile-{i}" for i, name in enumerate(piles.column("pile_id"), start=1)}
    for row in _records(piles):
        _write_pile(root, idents[row["pile_id"]], row, _units(piles), (project, boring))
    driving = dataset.table("driving")
    rows = _records(driving)
    context = (project, _units(driving)["tip_depth"], dataset.table("hammer"))
    for name, ident in idents.items():
        _write_activity(root, ident, [r for r in rows if r["pile_id"] == name], context)
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))


# --- what a file is observed to hold ----------------------------------------------------


class _Budget:
    """How many more ``extra`` nodes may be looked at; shared by one observation."""

    def __init__(self, limit: int) -> None:
        self.left = limit

    def spend(self) -> bool:
        self.left -= 1
        return self.left >= 0


def _walk(roots: tuple[ExtraNode, ...], budget: _Budget) -> Iterator[ExtraNode]:
    """Every node under ``roots``, depth first, without recursion and within ``budget``."""
    stack = list(reversed(roots))
    while stack and budget.spend():
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _is(node: ExtraNode, local: str, ns: str = _DIGGS) -> bool:
    return node.ns == ns and node.qname.rpartition(":")[2] == local


def _child(node: ExtraNode | None, local: str, ns: str = _DIGGS) -> ExtraNode | None:
    if node is None:
        return None
    return next((c for c in node.children if _is(c, local, ns)), None)


def _path(node: ExtraNode | None, *steps: str) -> ExtraNode | None:
    for step in steps:
        prefix, _, local = step.rpartition(":")
        node = _child(node, local, _PREFIXES[prefix] if prefix else _DIGGS)
    return node


def _float(text: str) -> float | None:
    """``text`` as a finite number, or None: a strict decimal, never ``nan`` or ``1_0``."""
    text = text.strip()
    if not _NUMBER.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


@cache
def _converter() -> UnitConverter:
    return UnitConverter()


def _to_si(value: float, uom: str | None) -> tuple[float, str] | None:
    """``value`` in ``uom`` as (SI value, SI unit); None for a unit that cannot convert."""
    if not uom:
        return value, ""
    converter = _converter()
    base, si = converter.base_unit(uom), converter.to_base(value, uom)
    if base is None or si is None or not math.isfinite(si):
        return None
    return si, base


def _pile_readings(pile: Feature, budget: _Budget) -> Iterator[tuple[str, float, str]]:
    """A pile's parameters and its taper widths: (key, SI value, SI unit)."""
    for name, held in pile.params.items():
        found = _facts._param_si(held)
        if found is not None:
            yield "@" + name, found[0], found[1]
    for node in _walk(pile.extra, budget):
        if _is(node, "widthAtTop") or _is(node, "widthAtBottom"):
            number = _float(node.text or "")
            found = None if number is None else _to_si(number, node.attrs.get("uom"))
            if found is not None:
                yield "@width", found[0], found[1]


def _tip_depths(model: Model, record: ExtraNode) -> list[float] | None:
    """The record's tip depths in metres, if it gives them.

    The positions are in the unit of the linear referencing system they name; a system
    whose unit is unknown, or a position that is not a number, gives none.
    """
    points = _path(record, "pileTipLocation", "MultiPointLocation")
    pos_list = _path(points, "gml:posList")
    if pos_list is None or points is None or points.attrs.get("srsDimension", "1") != "1":
        return None
    factor = _facts._crs_factor(model, points.attrs.get("srsName"))
    numbers = [_float(t) for t in (pos_list.text or "").split()]
    if factor is None or any(n is None for n in numbers):
        return None
    return [n * factor for n in numbers if n is not None]


def _columns(result: ExtraNode, budget: _Budget) -> list[tuple[str, str | None]]:
    """(dictionary key, unit) of each declared property, in document order."""
    properties = _path(result, "parameters", "PropertyParameters", "properties")
    out: list[tuple[str, str | None]] = []
    for prop in () if properties is None else properties.children:
        if not budget.spend():
            break
        if not _is(prop, "Property"):
            continue
        pclass, uom = _child(prop, "propertyClass"), _child(prop, "uom")
        code_space = "" if pclass is None else pclass.attrs.get("codeSpace", "")
        key = code_space.partition("#")[2] or (
            "" if pclass is None else (pclass.text or "").strip()
        )
        out.append((key, (uom.text or "").strip() or None if uom is not None else None))
    return out


def _rows(values: ExtraNode | None) -> list[list[str]]:
    """The tuples of a ``dataValues``, split the way pyDIGGS's reader splits them."""
    if values is None:
        return []
    ts = values.attrs.get("ts") or " "
    cs = values.attrs.get("cs", ",") or ","
    body = (values.text or "").strip()
    if not body:
        return []
    tuples = body.split() if ts.isspace() else [t.strip() for t in body.split(ts) if t.strip()]
    return [t.split(cs) for t in tuples]


def _record_readings(
    model: Model, record: ExtraNode, budget: _Budget
) -> list[tuple[str, float, float, str]]:
    """Every numeric value of a driving record: (key, tip depth m, SI value, SI unit).

    Only the cells a row holds are looked at, each charged to ``budget``: the work is
    linear in the size of the file, however many properties are declared.
    """
    depths = _tip_depths(model, record)
    result = _path(record, "pileDrivingRecordResults")
    result = next(
        (c for c in (result.children if result else ()) if c.qname.endswith("ResultSet")), None
    )
    if depths is None or result is None:
        return []
    rows = _rows(_child(result, "dataValues"))
    if len(rows) != len(depths):
        return []  # values that cannot be placed are not read
    columns = _columns(result, budget)
    out: list[tuple[str, float, float, str]] = []
    for depth, row in zip(depths, rows, strict=True):
        for (key, uom), cell in zip(columns, row, strict=False):
            if not budget.spend():
                return out
            number = _float(cell)
            found = None if number is None else _to_si(number, uom)
            if found is not None:
                out.append((key, depth, found[0], found[1]))
    return out


def _pile_of(record: ExtraNode, refs: list[str], lrs_owners: dict[str, set[str]]) -> str | None:
    """The pile a record is of, or None when it cannot be placed.

    The activity's ``samplingFeatureRef`` is DIGGS's statement of which pile was driven,
    so a record belongs to the one pile its activity names (``refs``). The pile whose
    linear referencing system its tip locations use (another feature's system, such as
    a boring's, does not count) is a check: when it is a different pile, or when two
    piles own that system, the record is not placed. Only when the activity names no
    pile does the owner of the system place it.
    """
    points = _path(record, "pileTipLocation", "MultiPointLocation")
    srs = None if points is None else points.attrs.get("srsName")
    owners = lrs_owners.get(srs, set()) if srs else set()
    if len(owners) > 1 or len(refs) > 1:
        return None
    owner = next(iter(owners), None)
    if refs:
        return refs[0] if owner in (None, refs[0]) else None
    return owner


def _owners(
    model: Model, piles: dict[str, Feature]
) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Which piles own each linear referencing system, and which piles each node names.

    Both are built in one pass over the features and the links, so placing records stays
    linear in the size of the file.
    """
    lrs_owners: dict[str, set[str]] = defaultdict(set)
    for pile in piles.values():
        if pile.lrs:
            lrs_owners[pile.lrs].add(pile.key)
    named: dict[str, dict[str, None]] = defaultdict(dict)  # an ordered set per node
    for link in model.links:
        if link.relation == "samplingFeature" and link.target in piles:
            named[link.source][link.target] = None
    return lrs_owners, {source: list(targets) for source, targets in named.items()}


def observe(model: Model) -> Observed:
    """What a DIGGS file holds of piles and driving records, for :func:`_facts.match`.

    Everything the generic observation reads (project names, features by name, total
    depths), plus each pile's parameters keyed ``(pile, its element, "@<param>")`` at
    depth 0 (taper widths as ``@width``), and each driving record's values keyed
    ``(pile, "PileDrivingRecord", <property key>)`` at the tip depth of their row, in
    metres. A record belongs to the one pile its activity refers to; it is not read when
    the pile whose linear referencing system its tip locations use is another pile, and
    placed by that pile only when the activity names none. Numbers become SI
    only through the units the file states; a value whose unit or depth cannot be
    converted is left out. The model is untrusted: at most ``_MAX_NODES`` unconsumed
    elements are looked at, without recursion.

    Examples:
        >>> import pydiggs
        >>> data = build("1.0-beta.1|piles|demo", "ABC123")
        >>> seen = observe(pydiggs.read_bytes(reference_xml(data)))
        >>> all(_facts.match(f, seen).status == "found" for f in facts(data))
        True
    """
    seen = _facts.observe(model)
    readings: dict[tuple[str, str, str], list[tuple[float, float, str]]] = defaultdict(list)
    for where, found in seen.readings.items():
        readings[where].extend(found)
    piles = {f.key: f for f in model.features if f.element.endswith("Pile")}
    lrs_owners, refs = _owners(model, piles)
    budget = _Budget(_MAX_NODES)
    for pile in piles.values():
        for key, value, unit in _pile_readings(pile, budget):
            readings[(pile.key, pile.element, key)].append((0.0, value, unit))
    for other in model.others:
        for node in _walk(other.extra, budget):
            if not _is(node, RECORD):
                continue
            pile_key = _pile_of(node, refs.get(other.key, []), lrs_owners)
            if pile_key is None:
                continue
            for key, depth, value, unit in _record_readings(model, node, budget):
                readings[(pile_key, RECORD, key)].append((depth, value, unit))
    # When the budget runs out the rest is not read, so its facts show as missing:
    # Observed has no field to say so, and a file that large fails grading either way.
    for found in readings.values():
        found.sort()
    return Observed(seen.project_names, seen.named, seen.totals, dict(readings))
