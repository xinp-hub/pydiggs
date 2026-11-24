"""What a delivered DIGGS file of one family must hold: the published acceptance requirements.

Every requirement has an id (``accept.*``), a sentence, and the part of DIGGS 3.0.0 or of
its dictionaries it comes from. None is engineering judgement: a requirement says that a
file holds what DIGGS defines for the family (its elements, the properties the DIGGS
dictionary defines for its procedures, the features DIGGS ties them to), never that a value
is plausible or enough.

A family plugs in through :func:`pydiggs.conformance._registry.acceptance`: a family whose
catalogue elements are test procedures gets :func:`test_profile`; a family with its own
shape (piles and driving records) has its own profile here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Final

from pydiggs.conformance import _facts, _piles
from pydiggs.conformance._catalogue import Family
from pydiggs.model import ExtraNode, Feature, Measurement, Model
from pydiggs.model._values import Column

__all__ = [
    "COMMON",
    "Check",
    "Profile",
    "Requirement",
    "defined_properties",
    "pile_profile",
    "test_profile",
]

#: At most this many failures are listed per requirement (all are counted).
MAX_ITEMS: Final = 200

_PROPERTIES: Final = "properties.xml"
_PILE_PROPERTIES: Final = "codes/pil_properties.xml"
_DIGGS_NS: Final = "http://diggsml.org/schemas/3"
_GML_NS: Final = "http://www.opengis.net/gml/3.2"


@dataclass(frozen=True, slots=True)
class Requirement:
    """One published requirement: its id, what must hold, and where DIGGS says so."""

    id: str
    text: str
    source: str

    def to_dict(self) -> dict[str, str]:
        """The requirement as plain data."""
        return {"id": self.id, "text": self.text, "source": self.source}


@dataclass(frozen=True, slots=True)
class Check:
    """What one requirement found in a file: how many things held, and the ones that did not.

    ``items`` is (what, message) per failure, at most :data:`MAX_ITEMS`; ``failed`` counts
    them all.
    """

    passed: int
    total: int
    items: tuple[tuple[str, str], ...] = ()
    failed: int = 0


#: A content check: the model read from the file -> what it found.
Checker = Callable[[Model], Check]


@dataclass(frozen=True, slots=True)
class Profile:
    """The requirements of one family, each with its check on a read file."""

    family: Family
    checks: tuple[tuple[Requirement, Checker], ...]

    @property
    def requirements(self) -> tuple[Requirement, ...]:
        """Every requirement of the family, the file-level ones (:data:`COMMON`) first."""
        return (*COMMON, *(requirement for requirement, _ in self.checks))


#: The requirements every family shares; :func:`pydiggs.conformance.accept` checks them.
COMMON: Final = (
    Requirement(
        "accept.diggs-3",
        "The file is a DIGGS 3 document, read to its end within the reader's limits.",
        "DIGGS 3.0.0: the root element Diggs in the namespace http://diggsml.org/schemas/3.",
    ),
    Requirement(
        "accept.valid",
        "The file has no validation error: XML Schema, dictionaries, Schematron business"
        " rules and cross-references (warnings are allowed).",
        "DIGGS 3.0.0 XML Schema (Diggs.xsd), the DIGGS dictionaries (properties, units of"
        " measure, codes) and the DIGGS Schematron rules.",
    ),
)


def _check(failures: list[tuple[str, str]], total: int) -> Check:
    return Check(total - len(failures), total, tuple(failures[:MAX_ITEMS]), len(failures))


def _name(node: Measurement | Feature) -> str:
    return node.name or node.gml_id or node.key


# ---------------------------------------------------------------------------- dictionary


@cache
def defined_properties(resource: str, element: str) -> frozenset[str]:
    """The properties a bundled DIGGS dictionary defines for ``element``.

    A property is defined for an element when one of its ``Occurrence`` entries has a
    ``conditionalElementXpath`` that ends in ``diggs:<element>``: on a procedure
    (``ancestor::diggs:measurement//diggs:procedure/diggs:DrivenPenetrationTest``) or on
    an ancestor (``ancestor::diggs:PileDrivingRecord``).

    Examples:
        >>> sorted(defined_properties("properties.xml", "DrivenPenetrationTest"))[:3]
        ['n1_60', 'n60', 'n_value']
    """
    from pydiggs import _xml  # noqa: PLC0415  # lxml loads on demand

    data = resources.files("pydiggs.dictionaries").joinpath(resource).read_bytes()
    root = _xml.parse_bytes(data, None).getroot()
    ending = re.compile(rf"diggs:{re.escape(element)}\s*$")
    found = set()
    gml_id = f"{{{_GML_NS}}}id"
    for definition in root.iter(f"{{{_DIGGS_NS}}}Definition"):
        ident = definition.get(gml_id)
        if not ident:
            continue
        for xpath in definition.iter(f"{{{_DIGGS_NS}}}conditionalElementXpath"):
            if ending.search(xpath.text or ""):
                found.add(ident)
                break
    return frozenset(found)


# ---------------------------------------------------------------------------- test families


def _tests(model: Model, procedures: frozenset[str]) -> list[Measurement]:
    return [m for m in model.measurements if m.procedure and m.procedure.type in procedures]


def _reports(column: Column) -> bool:
    """Whether ``column`` holds a value: a cell neither empty nor its declared nullValue."""
    return any(_facts._filled(column))


def test_profile(family: Family) -> Profile:
    """The requirements of a family whose catalogue elements are DIGGS test procedures."""
    procedures = frozenset(family.elements)
    shown = ", ".join(family.elements)
    which = f"one of {shown}" if len(family.elements) > 1 else shown

    def present(model: Model) -> Check:
        found = bool(_tests(model, procedures))
        failures = [] if found else [("file", f"no test whose procedure is {which}")]
        return _check(failures, 1)

    def located(model: Model) -> Check:
        failures = []
        tests = _tests(model, procedures)
        for test in tests:
            key = _facts._feature_key(model, test)
            if not isinstance(model.get(key) if key else None, Feature):
                failures.append(
                    (
                        _name(test),
                        f"the {test.procedure.type if test.procedure else ''} test names no"
                        " borehole, sounding or other sampling feature in the file, directly"
                        " or through its sample",
                    )
                )
        return _check(failures, len(tests))

    def results(model: Model) -> Check:
        failures = []
        tests = _tests(model, procedures)
        for test in tests:
            procedure = test.procedure.type if test.procedure else ""
            defined = defined_properties(_PROPERTIES, procedure)
            if not any(
                (column.key or column.name) in defined and _reports(column)
                for table in test.results
                for column in table.columns
            ):
                failures.append(
                    (
                        _name(test),
                        f"the {procedure} test reports no value of a property the DIGGS"
                        f" properties dictionary defines for {procedure}",
                    )
                )
        return _check(failures, len(tests))

    def positioned(model: Model) -> Check:
        failures = []
        total = 0
        for test in _tests(model, procedures):
            defined = defined_properties(_PROPERTIES, test.procedure.type if test.procedure else "")
            unplaced = 0
            counted = False
            for table in test.results:
                columns = [c for c in table.columns if (c.key or c.name) in defined]
                if not columns:
                    continue
                depths = _facts._depths(model, table)
                for column in columns:
                    for index, filled in enumerate(_facts._filled(column)):
                        if not filled:
                            continue
                        counted = True
                        if index >= len(depths) or depths[index] is None:
                            unplaced += 1
            total += counted
            if unplaced:
                failures.append(
                    (
                        _name(test),
                        f"{unplaced} result value(s) have no position along the feature in a"
                        " length unit DIGGS can convert",
                    )
                )
        return _check(failures, total)

    title = family.title.lower()
    return Profile(
        family,
        (
            (
                Requirement(
                    "accept.present",
                    f"The file holds at least one test of {title}: a measurement whose"
                    f" procedure is {which}.",
                    f"DIGGS 3.0.0 procedure elements {shown} (the suite's class catalogue).",
                ),
                present,
            ),
            (
                Requirement(
                    "accept.located",
                    "Every such test names the sampling feature (borehole, sounding) it was"
                    " made in, directly or through the sample it was made on, and that"
                    " feature is in the file.",
                    "DIGGS 3.0.0 AbstractMeasurementType: samplingFeatureRef and sampleRef;"
                    " SampleType: samplingFeatureRef.",
                ),
                located,
            ),
            (
                Requirement(
                    "accept.result",
                    "Every such test reports at least one value of a property that the DIGGS"
                    " properties dictionary defines for its procedure. A cell that is empty,"
                    " or is the nullValue its property declares, is not a value.",
                    "DIGGS 3.0.0 TestType: outcome (required); DIGGS properties dictionary"
                    " (properties.xml): each property's Occurrence conditionalElementXpath"
                    " naming the procedure.",
                ),
                results,
            ),
            (
                Requirement(
                    "accept.position",
                    "Every such result value is placed along its feature: a depth or"
                    " position in a length unit DIGGS can convert.",
                    "DIGGS 3.0.0 TestType: outcome holds 'the associated locations that the"
                    " measurement results relate to'; DIGGS units of measure dictionary.",
                ),
                positioned,
            ),
        ),
    )


# ---------------------------------------------------------------------------- drilling logs

#: The element a measurement-while-drilling log's procedure is, and its property dictionary.
_MWD_PROCEDURE: Final = "MWDProcedure"
_MWD_PROPERTIES: Final = "codes/mwd_properties.xml"
#: The MWD dictionary's properties that place a reading (where, when): not drilling channels.
_MWD_PLACES: Final = frozenset({"depth", "time"})


def mwd_profile(family: Family) -> Profile:
    """The requirements of the measurement-while-drilling family.

    A drilling log is a measurement element of its own (``MeasurementWhileDrilling``), not a
    test with one of the catalogue's procedures, and its channels are defined in the DIGGS
    MWD dictionary. DIGGS lets a log be placed by depth or by time, so no position is asked;
    for the same reason the dictionary's ``depth`` and ``time`` are not counted as results.
    """
    elements = frozenset(family.elements)
    shown = ", ".join(family.elements)

    def logs(model: Model) -> list[Measurement]:
        return [m for m in model.measurements if m.element in elements]

    def present(model: Model) -> Check:
        failures = [] if logs(model) else [("file", f"no {shown} measurement")]
        return _check(failures, 1)

    def located(model: Model) -> Check:
        found = logs(model)
        failures = []
        for log in found:
            key = _facts._feature_key(model, log)
            if not isinstance(model.get(key) if key else None, Feature):
                failures.append(
                    (_name(log), "the drilling log names no borehole or sounding in the file")
                )
        return _check(failures, len(found))

    def results(model: Model) -> Check:
        defined = defined_properties(_MWD_PROPERTIES, _MWD_PROCEDURE) - _MWD_PLACES
        found = logs(model)
        message = (
            "the drilling log reports no value of a drilling channel: a property the DIGGS"
            " MWD dictionary defines, other than depth and time"
        )
        failures = [
            (_name(log), message)
            for log in found
            if not any(
                (column.key or column.name) in defined and _reports(column)
                for table in log.results
                for column in table.columns
            )
        ]
        return _check(failures, len(found))

    return Profile(
        family,
        (
            (
                Requirement(
                    "accept.present",
                    f"The file holds at least one drilling log: a {shown} measurement.",
                    f"DIGGS 3.0.0 Core.xsd: the measurement element {shown}.",
                ),
                present,
            ),
            (
                Requirement(
                    "accept.located",
                    "Every drilling log names the borehole or sounding it was recorded in,"
                    " and that feature is in the file.",
                    "DIGGS 3.0.0 AbstractMeasurementType: samplingFeatureRef.",
                ),
                located,
            ),
            (
                Requirement(
                    "accept.result",
                    "Every drilling log reports at least one value of a drilling channel: a"
                    " property that the DIGGS MWD dictionary defines, other than depth and"
                    " time, which say where and when a reading was taken.",
                    "DIGGS 3.0.0 MeasurementWhileDrillingType: outcome (required); DIGGS MWD"
                    " dictionary (mwd_properties.xml): each property's Occurrence"
                    " conditionalElementXpath naming MWDProcedure.",
                ),
                results,
            ),
        ),
    )


# ---------------------------------------------------------------------------- piles


def _pile_records(model: Model) -> list[tuple[str, ExtraNode, list[str]]]:
    """Every PileDrivingRecord: (the activity's name, the record node, the piles it names)."""
    piles = {f.key: f for f in model.features if f.element.endswith("Pile")}
    _, refs = _piles._owners(model, piles)
    budget = _piles._Budget(_piles._MAX_NODES)
    out: list[tuple[str, ExtraNode, list[str]]] = []
    for other in model.others:
        out.extend(
            (other.name or other.gml_id or other.key, node, refs.get(other.key, []))
            for node in _piles._walk(other.extra, budget)
            if _piles._is(node, _piles.RECORD)
        )
    return out


def pile_profile(family: Family) -> Profile:
    """The requirements of the piles and driving records family."""
    pile_elements = tuple(e for e in family.elements if e.endswith("Pile"))
    shown = ", ".join(pile_elements)
    record = _piles.RECORD

    def present(model: Model) -> Check:
        failures = []
        if not any(f.element in pile_elements for f in model.features):
            failures.append(("file", f"no pile: none of {shown}"))
        if not _pile_records(model):
            failures.append(("file", f"no {record}"))
        return _check(failures, 2)

    def belongs(model: Model) -> Check:
        piles = {f.key: f for f in model.features if f.element in pile_elements}
        lrs_owners, _ = _piles._owners(model, piles)
        failures = []
        records = _pile_records(model)
        for activity, node, refs in records:
            # Strict: the activity itself must name the pile (the reader may also place a
            # record by its tip locations' frame; a deliverable must say it).
            if len(refs) != 1 or _piles._pile_of(node, refs, lrs_owners) != refs[0]:
                failures.append(
                    (
                        activity,
                        f"a {record} whose activity names no single pile of the file, or"
                        " whose tip locations are on another pile",
                    )
                )
        return _check(failures, len(records))

    def results(model: Model) -> Check:
        defined = defined_properties(_PILE_PROPERTIES, record)
        budget = _piles._Budget(_piles._MAX_NODES)
        failures = []
        records = _pile_records(model)
        for activity, node, _ in records:
            keys = {r[0] for r in _piles._record_readings(model, node, budget, nulls=True)}
            if not keys & defined:
                failures.append(
                    (
                        activity,
                        f"a {record} with no value of a property the DIGGS pile-driving"
                        f" dictionary defines for {record}, placed at a pile tip depth",
                    )
                )
        return _check(failures, len(records))

    return Profile(
        family,
        (
            (
                Requirement(
                    "accept.present",
                    f"The file holds at least one pile ({shown}) and at least one {record}.",
                    "DIGGS 3.0.0 DeepFoundation.xsd: the pile elements and PileDrivingRecord.",
                ),
                present,
            ),
            (
                Requirement(
                    "accept.located",
                    f"Every {record} belongs to one pile of the file: the PileDrivingActivity"
                    " that holds it names that pile, and its tip locations are on no other pile.",
                    "DIGGS 3.0.0 PileDrivingActivityType ('the process of installing a pile by"
                    " driving'): samplingFeatureRef, exactly one"
                    " (AbstractConstructionActivityType).",
                ),
                belongs,
            ),
            (
                Requirement(
                    "accept.result",
                    f"Every {record} reports at least one value of a property the DIGGS"
                    f" pile-driving dictionary defines for {record}, at a pile tip depth. A"
                    " cell that is empty, or is the nullValue its property declares, is not"
                    " a value.",
                    "DIGGS pile-driving dictionary (pil_properties.xml): Occurrence"
                    f" conditionalElementXpath ancestor::diggs:{record}; DIGGS 3.0.0"
                    " PileDrivingRecord pileTipLocation.",
                ),
                results,
            ),
        ),
    )
