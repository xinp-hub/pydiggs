"""AGS4 to a diggs-model :class:`~pydiggs.model.Model`, by the mapping table. Pure.

:func:`map_file` turns an :class:`~pydiggs.ags4.File` into the model the internal writer
writes, the AGS4 row each node came from, and the ledger of source cells. It does no I/O
and reads no clock (a deadline, when given, is checked through the ``clock`` passed in):
dates and identifiers come from the file, so the same file always gives the same model.

The model is shaped as the writer needs it: every node it holds can be written as DIGGS
3.0.0. What DIGGS requires and the file does not give is either written as a documented
default (``convert.assumed``) or the record is left out and its cells are named
(``convert.incomplete``, ``convert.orphan-row``). :func:`drop` removes a node the writer
still refuses, with everything that refers to it, and names the cells it held.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Final, TypeVar

from pydiggs._writer._layout import load_layout
from pydiggs.ags4 import File, Group
from pydiggs.convert import _ledger as L  # noqa: N812 - rule-id constants
from pydiggs.convert._ledger import Ledger
from pydiggs.convert._mapping import (
    PROPERTIES_URL,
    GroupRule,
    MappingTable,
    measure_facts,
    property_facts,
)
from pydiggs.convert._values import IdMaker, parse_date, parse_degrees, parse_number, xml_text
from pydiggs.model import (
    Code,
    Column,
    Component,
    CrsRef,
    DocumentInfo,
    Domain,
    ExtraNode,
    Feature,
    Interval,
    Link,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Observation,
    Param,
    Position,
    Procedure,
    Project,
    Provenance,
    Quantity,
    ResultTable,
    Sample,
    SamplingActivity,
    SourceInfo,
    TimeSpan,
)

__all__ = ["Built", "Origin", "components_of", "drop", "map_file", "written_number"]

_N = TypeVar("_N", bound=Node)

#: The DIGGS version the converter writes.
DIGGS_VERSION: Final = "3.0.0"
#: The unit of every depth along a feature (the AGS4 depth unit).
DEPTH_UNIT: Final = "m"
#: ``codeSpace`` of the AGS4 identifiers DIGGS holds as a coded identifier.
AGS4_CODE_SPACE: Final = "urn:x-pydiggs:ags4:"
#: Written for the lithology classification type DIGGS requires and AGS4 does not state.
CLASSIFICATION_DEFAULT: Final = "unspecified"
#: Written for the project when the file has no PROJ row.
PROJECT_DEFAULT: Final = "unnamed project"
#: A unit the converter cannot write with its value: the value is carried, the unit named.
_NO_PROPERTY_UNIT: Final = (
    "The unit of {heading} in {group} was not written: the DIGGS property it maps to has no"
    " unit, so the values were written without it ({values})."
)
_NO_AXIS_UNIT: Final = (
    "The unit of {heading} in {group} was not written: the coordinates are in no CRS whose"
    " axes the code table gives a unit for, so the values were written without it"
    " ({values})."
)
#: How the creation date is written when TRAN_DATE is missing or unreadable: as unknown
#: (``indeterminatePosition="unknown"``), which the model holds as no date.
DATE_UNKNOWN: Final = "unknown"
#: The owner of a cell read before the node that will hold it is made.
_PENDING: Final = "\x00pending"
#: How many rows pass between two looks at the deadline.
_CHECK_EVERY: Final = 256
_EPSG: Final = "http://www.opengis.net/def/crs/EPSG/0/"
#: Values of a limit heading that mean "non-plastic" (the property non_plastic).
_NON_PLASTIC: Final = frozenset({"NP"})
_LIMITS: Final = frozenset({"liquid_limit", "plastic_limit", "plasticity_index"})
#: Penetrometer types (SCPG_TYPE, CPTG_TYPE) whose resistance is not a cone resistance.
_NOT_CONES: Final = frozenset({"BALL", "T-BAR"})
#: The length of a full SPT test drive, in mm (DIGGS n_value is blows per foot).
_TEST_DRIVE_MM: Final = 300.0
_DIGGS_NS: Final = "http://diggsml.org/schemas/3"
_GML_NS: Final = "http://www.opengis.net/gml/3.2"
#: The element a GRAT row is written as -> the name of its particle size there.
_SIZE_FIELDS: Final = {"Grading": "particleSize", "Sedimentation": "particleDiameter"}
#: Reading element -> (the analysis that holds it, that analysis's role, the reading's role).
_HOLDERS: Final = {
    "Grading": ("SieveAnalysis", "sieveAnalysis", "gradingData"),
    "Sedimentation": ("Hydrometer", "hydrometer", "sedimentationData"),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Origin:
    """Where a DIGGS node came from: an AGS4 group, a DATA row (from 1) and its line.

    ``line`` is the line of that DATA row in the source file, or None for a file built
    in code.
    """

    group: str
    row: int
    line: int | None = None


@dataclass(slots=True)
class Built:
    """What :func:`map_file` built: the model, where each node came from, and the ledger."""

    model: Model
    trace: dict[str, Origin]
    ledger: Ledger
    complete: bool = True
    #: Each node's parents: the keys it links to (a node is dropped with its parents).
    parents: dict[str, set[str]] = field(default_factory=dict)


class _Stop(Exception):  # noqa: N818 - control flow, never escapes
    """The deadline passed."""


def written_number(value: float) -> str:
    """``value`` as the writer writes it: an integer without a decimal point, else ``repr``."""
    if value.is_integer() and abs(value) < 1e15:  # noqa: PLR2004 - exact as an integer
        return str(int(value))
    return repr(value)


def _kind(element: str) -> str:
    out = []
    for i, ch in enumerate(element):
        if ch.isupper() and i and (element[i - 1].islower() or element[i - 1].isdigit()):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _node(cls: type[_N], key: str, element: str, **fields: Any) -> _N:
    names = tuple(fields.pop("names", ()))
    return cls(
        key=key,
        gml_id=fields.pop("gml_id", key),
        element=element,
        kind=str(fields.pop("kind", _kind(element))),
        name=names[0] if names else None,
        names=names,
        description=fields.pop("description", None),
        remarks=tuple(fields.pop("remarks", ())),
        params=fields.pop("params", {}),
        provenance=Provenance(xpath=""),
        extra=tuple(fields.pop("extra", ())),
        **fields,
    )


class _Row:
    """One DATA row of one group, read through the ledger."""

    def __init__(self, mapper: _Mapper, group: Group, index: int) -> None:
        self.mapper = mapper
        self.group = group
        self.index = index

    @property
    def ordinal(self) -> int:
        return self.index + 1

    def get(self, heading: str) -> str:
        return self.mapper.ledger.value(self.group.name, self.index, heading)

    def unit(self, heading: str) -> str:
        column = self.mapper.ledger.column(self.group.name, heading)
        units = self.group.units
        return units[column] if column is not None and column < len(units) else ""

    def use(self, heading: str, owner: str) -> None:
        self.mapper.ledger.use(self.group.name, self.index, heading, owner)

    def unuse(self, heading: str, owner: str) -> None:
        self.mapper.ledger.unuse(self.group.name, self.index, heading, owner)

    def lose(self, rule: str, heading: str | None, template: str) -> None:
        self.mapper.ledger.lose(rule, self.group.name, self.index, heading, template)

    def unit_lost(self, heading: str, template: str) -> None:
        """Name the UNIT of a value written without it (the value itself is carried).

        The cell is named as well as used, so the residual file keeps it with its unit.
        """
        if self.unit(heading).strip():
            self.lose(L.UNMAPPED_UNIT, heading, template)

    def key(self, headings: Iterable[str]) -> tuple[str, ...]:
        return tuple(self.get(heading) for heading in headings)

    # --- values -------------------------------------------------------------------------

    def text(self, heading: str, owner: str) -> str | None:
        """The value as DIGGS text, used by ``owner``; None when it is null or empty."""
        raw = self.get(heading)
        if raw == "":
            return None
        cleaned = xml_text(raw)
        if not cleaned:
            self.lose(
                L.UNPARSED,
                heading,
                "Values of {heading} in {group} hold only white space or characters XML cannot"
                " hold; not converted ({values}).",
            )
            return None
        if cleaned != raw:
            self.mapper.ledger.note(
                L.REFORMATTED,
                self.group.name,
                "Surrounding white space or characters XML cannot hold were removed from values"
                " of {group}; the text is otherwise as written ({times}).",
                self.index,
            )
        self.use(heading, owner)
        return cleaned

    def number_text(self, heading: str, owner: str) -> tuple[float, str] | None:
        """The value as a number and its text, used by ``owner``; None when null or unparsed."""
        raw = self.get(heading)
        if raw == "":
            return None
        value = parse_number(raw.strip())
        if value is None:
            self.lose(
                L.UNPARSED,
                heading,
                "Values of {heading} in {group} are not numbers; not converted ({values}).",
            )
            return None
        self.use(heading, owner)
        return value, raw.strip()

    def number(self, heading: str, owner: str) -> float | None:
        """The value as a number written in DIGGS's shortest form (reported when it differs)."""
        found = self.number_text(heading, owner)
        if found is None:
            return None
        value, text = found
        if written_number(value) != text:
            self.mapper.ledger.note(
                L.REFORMATTED,
                self.group.name,
                "Numbers of {group} were written in a shorter form, the same value ({times}).",
                self.index,
            )
        return value

    def date(self, heading: str, owner: str) -> str | None:
        raw = self.get(heading)
        if raw == "":
            return None
        value = parse_date(raw.strip(), self.unit(heading))
        if value is None:
            self.lose(
                L.UNPARSED,
                heading,
                "Values of {heading} in {group} are not dates pyDIGGS reads (yyyy-mm-dd,"
                " yyyy-mm-ddThh:mm[:ss]); not converted ({values}).",
            )
            return None
        if value != raw:
            self.mapper.ledger.note(
                L.REFORMATTED,
                self.group.name,
                "Dates or times of {group} were written in ISO 8601 form, the same instant"
                " ({times}).",
                self.index,
            )
        self.use(heading, owner)
        return value

    def uom(self, heading: str) -> str | None:
        """The DIGGS unit of ``heading``'s UNIT, or None (reported) when it has none."""
        unit = self.unit(heading)
        mapped = self.mapper.mapping.units.get(unit)
        if mapped is None:
            template = (
                "Values of {heading} in {group} have no unit, and DIGGS needs one; not converted"
                " ({values})."
                if unit == ""
                else "Values of {heading} in {group} are in a unit with no entry in the unit"
                " table; not converted ({values})."
            )
            self.lose(L.UNMAPPED_UNIT, heading, template)
        return mapped

    def measure(self, heading: str, owner: str, quantity: str) -> Quantity | str | None:
        """The value as a ``diggs:<quantity>MeasureType``; None (reported) when it cannot be.

        The unit must be one DIGGS 3.0.0 allows for that measure: a length is not
        written as a hammer efficiency, nor a value without a unit as a particle size.
        A number with no UNIT is written as it is, with no unit, where the measure's
        unit is optional; it is never given one.
        """
        if self.get(heading) == "":
            return None
        facts = measure_facts(quantity)
        if self.unit(heading) == "" and not facts.required:
            found = self.number_text(heading, owner)
            return None if found is None else found[1]
        uom = self.uom(heading)
        if uom is None:
            return None
        if uom not in facts.uoms:
            self.lose(
                L.UNMAPPED_UNIT,
                heading,
                "Values of {heading} in {group} are in a unit DIGGS 3.0.0 does not allow for"
                " the element they map to; not converted ({values}).",
            )
            return None
        found = self.number_text(heading, owner)
        if found is None:
            return None
        value, text = found
        return Quantity(text=text, value=value, uom=uom)

    def depth(self, heading: str, owner: str) -> Quantity | None:
        """A depth along the feature, in metres."""
        if self.get(heading) == "":
            return None
        unit = self.unit(heading)
        if unit not in ("", DEPTH_UNIT):
            self.lose(
                L.UNMAPPED_UNIT,
                heading,
                "Values of {heading} in {group} are depths in a unit other than m, which the"
                " converter does not write; not converted ({values}).",
            )
            return None
        found = self.number_text(heading, owner)
        if found is None:
            return None
        if unit == "":
            self.mapper.ledger.note(
                L.ASSUMED,
                self.group.name,
                "Depths of {group} have no unit; they were written in metres ({times}).",
                self.index,
            )
        value, text = found
        return Quantity(text=text, value=value, uom=DEPTH_UNIT)


@dataclass(slots=True)
class _Feature:
    key: str
    lrs: str


class _Mapper:
    """One conversion: the mapping, the ledger, ids, and the nodes built so far."""

    def __init__(
        self,
        file: File,
        mapping: MappingTable,
        deadline: float | None,
        clock: Callable[[], float],
    ) -> None:
        self.file = file
        self.mapping = mapping
        self.ledger = Ledger(file)
        self.ids = IdMaker()
        #: The document information's key, taken first so no record can take it.
        self.document_key = self.ids.make("document")
        self.deadline = deadline
        self.clock = clock
        self.ticks = 0
        self.trace: dict[str, Origin] = {}
        self.parents: dict[str, set[str]] = {}
        self.links: list[Link] = []
        self.document: DocumentInfo | None = None
        self.projects: list[Project] = []
        self.features: list[Feature] = []
        self.feature_by_id: dict[str, _Feature] = {}
        self.crs: dict[str, CrsRef] = {}
        self.activities: list[SamplingActivity] = []
        self.samples: list[Sample] = []
        self.sample_by_key: dict[tuple[str, ...], tuple[str, _Feature]] = {}
        self.observations: list[Observation] = []
        self.measurements: list[Measurement] = []
        self.complete = True
        self.user_groups: set[str] = set()
        self.user_headings: set[tuple[str, str]] = set()
        #: The base of the last section of a group along a feature (for interval_top).
        self.previous_base: dict[tuple[str, str], Quantity] = {}
        #: True when the last test row's results were all left out on purpose (withheld).
        self.only_withheld = False
        #: The concatenation character of joined codes (TRAN_RCON, "+" by default).
        tran = file.groups.get("TRAN")
        records = tran.records() if tran is not None else []
        self.joiner = (records[0].get("TRAN_RCON", "") if records else "") or "+"

    # --- plumbing -----------------------------------------------------------------------

    def tick(self) -> None:
        self.ticks += 1
        if (
            self.deadline is not None
            and self.ticks % _CHECK_EVERY == 0
            and self.clock() > self.deadline
        ):
            raise _Stop

    def rows(self, group: str) -> Iterable[_Row]:
        found = self.file.groups.get(group)
        if found is None:
            return
        for index in range(len(found.rows)):
            self.tick()
            yield _Row(self, found, index)

    def origin(self, key: str, row: _Row) -> None:
        self.trace[key] = Origin(
            group=row.group.name, row=row.ordinal, line=L.row_line(row.group, row.index)
        )

    def link(self, source: str, relation: str, target: str) -> None:
        self.links.append(
            Link(source=source, relation=relation, target=target, href=f"#{target}", resolved=True)
        )
        self.parents.setdefault(source, set()).add(target)

    def project(self) -> str:
        if not self.projects:
            key = self.ids.make("project")
            self.projects.append(_node(Project, key, "Project", names=(PROJECT_DEFAULT,)))
            self.ledger.note(
                L.ASSUMED,
                "PROJ" if "PROJ" in self.file.groups else next(iter(self.file.groups)),
                "The file has no PROJ row; DIGGS requires every feature, sample and test to"
                f" name its project, so a project named {PROJECT_DEFAULT!r} was written.",
            )
        return self.projects[0].key

    def params(self, row: _Row, rule: GroupRule, element: str, owner: str) -> dict[str, Param]:
        """The ``param`` headings of ``rule`` whose target element is ``element``."""
        out: dict[str, Param] = {}
        for heading in rule.headings.values():
            if heading.to != "param" or heading.target is None:
                continue
            target_element, _, child = heading.target.partition("/")
            if target_element != element or row.get(heading.name) == "":
                continue
            if heading.quantity is not None:
                quantity = row.measure(heading.name, owner, heading.quantity)
                if quantity is not None:
                    out[child] = quantity
            else:
                text = row.text(heading.name, owner)
                if text is not None:
                    out[child] = text
        return out

    def fields(self, rule: GroupRule, target: str) -> list[str]:
        """The headings of ``rule`` that fill field ``target``, in table order."""
        return [h.name for h in rule.headings.values() if h.to == "field" and h.target == target]

    def remarks(self, row: _Row, rule: GroupRule, owner: str) -> tuple[str, ...]:
        found = (row.text(heading, owner) for heading in self.fields(rule, "remark"))
        return tuple(text for text in found if text is not None)

    def first_text(self, row: _Row, rule: GroupRule, target: str, owner: str) -> str | None:
        for heading in self.fields(rule, target):
            text = row.text(heading, owner)
            if text is not None:
                return text
        return None

    def feature_of(self, row: _Row) -> _Feature | None:
        """The feature a row names in LOCA_ID; None (reported as an orphan) when there is none."""
        found = self.feature_by_id.get(row.get("LOCA_ID"))
        if found is None:
            row.lose(
                L.ORPHAN_ROW,
                None,
                "Rows of {group} name a LOCA_ID that no converted LOCA row has; not converted"
                " ({rows}).",
            )
        return found

    # --- the document, projects and lookups ---------------------------------------------

    def lookups(self) -> None:
        dictionary = self.file.groups.get("DICT")
        if dictionary is not None:
            for record in dictionary.records():
                kind = record.get("DICT_TYPE", "").strip().upper()
                group = record.get("DICT_GRP", "")
                if kind == "GROUP":
                    self.user_groups.add(group)
                elif kind == "HEADING":
                    self.user_headings.add((group, record.get("DICT_HDNG", "")))
        for name, rule in self.mapping.groups.items():
            if rule.pattern != "lookup" or name not in self.file.groups:
                continue
            self.use_format(name, rule)

    def use_format(self, group: str, rule: GroupRule) -> None:
        """Mark the ``format`` headings of ``group`` as read (they are not data)."""
        formats = [h.name for h in rule.headings.values() if h.to == "format"]
        for row in self.rows(group):
            for heading in formats:
                row.use(heading, "")

    def transmission(self) -> None:
        rule = self.mapping.groups["TRAN"]
        key = self.document_key
        date: str | None = None
        description: str | None = None
        tran = self.file.groups.get("TRAN")
        if tran is not None:
            self.use_format("TRAN", rule)
            for row in self.rows("TRAN"):
                if row.index == 0:
                    date = row.date("TRAN_DATE", key)
                    description = self.first_text(row, rule, "description", key)
                else:
                    row.lose(
                        L.UNMAPPED_HEADING,
                        None,
                        "Rows of {group} after the first: DIGGS has one document information; not"
                        " converted ({rows}).",
                    )
        self.document = self.document_info(date, description)

    def document_info(self, date: str | None, description: str | None) -> DocumentInfo:
        """The document information, with the default creation date when there is none."""
        if date is None:
            self.ledger.note(
                L.ASSUMED,
                "TRAN" if "TRAN" in self.file.groups else next(iter(self.file.groups)),
                "DIGGS requires a creation date and TRAN_DATE gives none that can be read; the"
                f' creation date was written as unknown (indeterminatePosition="{DATE_UNKNOWN}").',
            )
        return _node(
            DocumentInfo,
            self.document_key,
            "DocumentInformation",
            gml_id=None,
            kind="document_info",
            description=description,
            creation_date=date,
        )

    def projects_(self) -> None:
        rule = self.mapping.groups["PROJ"]
        for row in self.rows("PROJ"):
            key = self.ids.make(f"project-{row.get('PROJ_ID')}")
            names = [n for h in ("PROJ_ID",) if (n := row.text(h, key)) is not None]
            name = self.first_text(row, rule, "name", key)
            if name is not None and name not in names:
                names.append(name)
            if not names:
                names = [PROJECT_DEFAULT]
                self.ledger.note(
                    L.ASSUMED,
                    "PROJ",
                    "PROJ rows with neither PROJ_ID nor PROJ_NAME: DIGGS requires a project name,"
                    " so"
                    f" {PROJECT_DEFAULT!r} was written ({{times}}).",
                    row.index,
                )
            project = _node(
                Project,
                key,
                "Project",
                kind="project",
                names=names,
                remarks=self.remarks(row, rule, key),
                locality=self.first_text(row, rule, "locality", key),
            )
            self.projects.append(project)
            self.origin(key, row)

    # --- features -----------------------------------------------------------------------

    def features_(self) -> None:
        rule = self.mapping.groups["LOCA"]
        for row in self.rows("LOCA"):
            loca_id = row.get("LOCA_ID")
            name = xml_text(loca_id)
            if not name:
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} have no LOCA_ID, and DIGGS requires a feature name; not"
                    " converted ({rows}).",
                )
                continue
            key = self.ids.make(loca_id)
            row.use("LOCA_ID", key)
            element, params = self.feature_kind(row, key)
            location = self.location(row, key)
            project = self.project()
            lrs = f"#{self.ids.make(f'{key}-lrs')}"
            if location is None:
                location = Position(coordinates=(0.0,), dim=1, crs=lrs)
                self.ledger.note(
                    L.ASSUMED,
                    "LOCA",
                    "LOCA rows with no coordinates that can be written: DIGGS requires a"
                    " location, so the top of the hole along its own depth axis was written"
                    " ({times}).",
                    row.index,
                )
            start = row.date("LOCA_STAR", key)
            end = row.date("LOCA_ENDD", key)
            feature = _node(
                Feature,
                key,
                element,
                names=(name,),
                description=self.first_text(row, rule, "description", key),
                remarks=self.remarks(row, rule, key),
                params=params,
                location=location,
                centerline=(location,),
                total_depth=self.total_depth(row, key),
                lrs=lrs,
                when_constructed=None
                if start is None and end is None
                else TimeSpan(start=start, end=end),
            )
            self.features.append(feature)
            self.feature_by_id.setdefault(loca_id, _Feature(key=key, lrs=lrs))
            self.crs[lrs] = CrsRef(
                srs_name=lrs, form="local", unit=DEPTH_UNIT, known=True, local=lrs[1:]
            )
            self.link(key, "project", project)
            self.origin(key, row)

    def total_depth(self, row: _Row, key: str) -> Quantity | None:
        """LOCA_FDEP, which DIGGS (its Schematron rules) requires to be positive."""
        value = parse_number(row.get("LOCA_FDEP").strip())
        if value is not None and value <= 0:
            row.lose(
                L.UNPARSED,
                "LOCA_FDEP",
                "Values of {heading} in {group} are not positive, and DIGGS requires a"
                " positive total depth; not converted ({values}).",
            )
            return None
        return row.depth("LOCA_FDEP", key)

    def feature_kind(self, row: _Row, key: str) -> tuple[str, dict[str, Param]]:
        code = row.get("LOCA_TYPE").strip()
        if code == "":
            self.ledger.note(
                L.ASSUMED,
                "LOCA",
                "LOCA rows with no LOCA_TYPE were written as Borehole ({times}).",
                row.index,
            )
            return "Borehole", {}
        rule = self.mapping.code("LOCA_TYPE", code, self.joiner)
        if rule is None or rule.element is None:
            row.lose(
                L.UNMAPPED_CODE,
                "LOCA_TYPE",
                "Values of {heading} in {group} have no entry in the code table; those locations"
                " were written as Borehole, without the code ({values}).",
            )
            return "Borehole", {}
        params: dict[str, Param] = {}
        if rule.code is not None and rule.code_list is not None:
            params["boreholeType"] = Code(
                value=rule.code, code_space=self.mapping.code_space(rule.code_list)
            )
        elif not rule.exact:
            row.lose(
                L.UNMAPPED_CODE,
                "LOCA_TYPE",
                "Values of {heading} in {group} have no DIGGS code; those locations were written"
                " as their DIGGS element, without the code ({values}).",
            )
            return rule.element, params
        row.use("LOCA_TYPE", key)
        return rule.element, params

    def grid_crs(self, row: _Row, heading: str) -> tuple[bool, str | None]:
        """The CRS a reference-system heading names: (known, srsName)."""
        text = row.get(heading).strip()
        if text == "":
            return False, None
        srs = self.mapping.crs_of(heading, text)
        if srs is None:
            prefix, _, code = text.partition(":")
            if prefix.upper() == "EPSG" and code.isdigit() and 0 < len(code) <= 6:  # noqa: PLR2004
                srs = f"{_EPSG}{int(code)}"
        return srs is not None, srs

    def location(self, row: _Row, key: str) -> Position | None:
        easting, northing = row.get("LOCA_NATE"), row.get("LOCA_NATN")
        if easting and northing:
            grid = self.grid(row, key)
            if grid is not None:
                for heading in ("LOCA_LAT", "LOCA_LON", "LOCA_LLZ", "LOCA_LLSI"):
                    if row.get(heading):
                        row.lose(
                            L.UNMAPPED_HEADING,
                            heading,
                            "Values of {heading} in {group} were not converted: the location was"
                            " taken from LOCA_NATE and LOCA_NATN, and DIGGS holds one ({values}).",
                        )
                return grid
        return self.geographic(row, key)

    def grid(self, row: _Row, key: str) -> Position | None:
        """LOCA_NATE, LOCA_NATN (and LOCA_GL) in the CRS LOCA_GREF names; None when not written.

        A coordinate is written only in the unit of the CRS's axes (the code table gives
        it): a value in another unit is reported, never relabelled. When one coordinate
        cannot be written, neither is, and both are reported.
        """
        _known, gref = self.grid_crs(row, "LOCA_GREF")
        nsri = self.epsg_heading(row, "LOCA_NSRI")
        srs = gref if nsri is None else f"{_EPSG}{nsri}"
        axis = None if srs is None else self.mapping.crs_units.get(srs)
        headings = ("LOCA_NATE", "LOCA_NATN")
        units_ok = [self.axis_unit(row, heading, axis) for heading in headings]
        if not all(units_ok):
            for heading, ok in zip(headings, units_ok, strict=True):
                if ok:
                    row.lose(
                        L.UNMAPPED_HEADING,
                        heading,
                        "Values of {heading} in {group} were not converted: the other grid"
                        " coordinate of the row could not be written ({values}).",
                    )
            return None
        found = [row.number(heading, key) for heading in headings]
        if found[0] is None or found[1] is None:
            for heading, value in zip(headings, found, strict=True):
                if value is not None:
                    row.unuse(heading, key)
                    row.lose(
                        L.UNMAPPED_HEADING,
                        heading,
                        "Values of {heading} in {group} were not converted: the other grid"
                        " coordinate of the row could not be written ({values}).",
                    )
            return None
        if nsri is None:
            self.grid_reference(row, key, srs, axis)
        else:
            self.national_reference(row, key, srs, gref, axis)
        coordinates = [found[0], found[1]]
        written = list(headings)
        if row.get("LOCA_GL") and self.axis_unit(row, "LOCA_GL", axis):
            level = row.number("LOCA_GL", key)
            if level is not None:
                coordinates.append(level)
                written.append("LOCA_GL")
                srs = self.vertical(row, key, srs)
        if axis is None:  # no CRS states the axes' unit, so the UNIT is not carried
            for heading in written:
                row.unit_lost(heading, _NO_AXIS_UNIT)
        return Position(coordinates=tuple(coordinates), dim=len(coordinates), crs=srs)

    def epsg_heading(self, row: _Row, heading: str) -> int | None:
        """An AGS4 4.2 EPSG-code heading (LOCA_NSRI, LOCA_LLSI) as a number; None when empty.

        A value that is not a whole positive number of at most six digits is reported.
        """
        text = row.get(heading).strip()
        if text == "":
            return None
        if text.isascii() and text.isdigit() and len(text) <= 6 and int(text) > 0:  # noqa: PLR2004
            return int(text)
        row.lose(
            L.UNPARSED,
            heading,
            "Values of {heading} in {group} are not EPSG codes; not converted ({values}).",
        )
        return None

    def national_reference(
        self, row: _Row, key: str, srs: str | None, gref: str | None, axis: str | None
    ) -> None:
        """Use LOCA_NSRI, and LOCA_GREF where it names the same CRS (else report it)."""
        row.use("LOCA_NSRI", key)
        if row.get("LOCA_GREF").strip():
            if gref == srs:
                row.use("LOCA_GREF", key)
            else:
                row.lose(
                    L.UNMAPPED_CODE,
                    "LOCA_GREF",
                    "Values of {heading} in {group} disagree with LOCA_NSRI, whose EPSG code"
                    " was used; not converted ({values}).",
                )
        if axis is None:
            self.ledger.note(
                L.ASSUMED,
                "LOCA",
                "LOCA rows whose LOCA_NSRI is an EPSG code the code table does not list: the"
                " grid coordinates were written as given in that CRS, without checking their"
                " unit against its axes ({times}).",
                row.index,
            )

    def vertical(self, row: _Row, key: str, srs: str | None) -> str | None:
        """The CRS of a point with a ground level: a compound one when LOCA_NATD allows it.

        With a vertical datum the code table lists for the grid CRS (Ordnance Datum Newlyn
        with British National Grid), the point is written in the compound CRS. Otherwise it
        is written in the grid CRS, which does not state the datum of the ground level, and
        that is reported.
        """
        datum = row.get("LOCA_NATD").strip()
        compound = self.mapping.crs_of("LOCA_NATD", datum) if datum else None
        if (
            compound is not None
            and srs is not None
            and self.mapping.crs_horizontal.get(compound) == srs
        ):
            row.use("LOCA_NATD", key)
            return compound
        if datum:
            row.lose(
                L.UNMAPPED_CODE,
                "LOCA_NATD",
                "Values of {heading} in {group} name no vertical datum the code table lists"
                " for the grid CRS of the row; not converted ({values}).",
            )
        self.ledger.note(
            L.ASSUMED,
            "LOCA",
            "Ground levels (LOCA_GL) of {group} were written as the third coordinate of a CRS"
            " that does not state their vertical datum ({times}).",
            row.index,
        )
        return srs

    def grid_reference(self, row: _Row, key: str, srs: str | None, axis: str | None) -> None:
        """Use or report LOCA_GREF, and say what is assumed about the coordinates' unit."""
        known = srs is not None
        if known:
            row.use("LOCA_GREF", key)
        elif row.get("LOCA_GREF").strip() == "":
            self.ledger.note(
                L.ASSUMED,
                "LOCA",
                "LOCA rows with an empty LOCA_GREF: the grid coordinates were written with no CRS,"
                " so neither their reference system nor their unit is stated ({times}).",
                row.index,
            )
        else:
            row.lose(
                L.UNMAPPED_CODE,
                "LOCA_GREF",
                "Values of {heading} in {group} name no CRS the code table lists; the coordinates"
                " were written with no CRS, so their unit is not stated ({values}).",
            )
        if known and axis is None:
            self.ledger.note(
                L.ASSUMED,
                "LOCA",
                "LOCA rows whose LOCA_GREF is an EPSG code the code table does not list: the"
                " grid coordinates were written as given in that CRS, without checking their"
                " unit against its axes ({times}).",
                row.index,
            )

    def axis_unit(self, row: _Row, heading: str, axis: str | None) -> bool:
        """True when ``heading``'s UNIT can be written in a CRS whose axes are in ``axis``.

        An empty UNIT is taken as the CRS's (reported); a UNIT with no entry in the unit
        table, or another unit than the axes', is reported and False. When the axis
        unit is not known (no CRS, or one the code table does not list), a unit the
        table lists is accepted, and :meth:`grid_reference` says it was not checked.
        """
        unit = row.unit(heading)
        if unit == "":
            if axis is not None:
                self.ledger.note(
                    L.ASSUMED,
                    "LOCA",
                    "Grid coordinates or ground levels of {group} have no unit; they were"
                    " written as given, in the unit of the axes of their CRS ({times}).",
                    row.index,
                )
            return True
        mapped = self.mapping.units.get(unit)
        if mapped is not None and axis in (None, mapped):
            return True
        row.lose(
            L.UNMAPPED_UNIT,
            heading,
            "Values of {heading} in {group} are in a unit that is not the unit of the axes of"
            " the CRS LOCA_GREF names (or has no entry in the unit table); values are never"
            " converted, so they were not written ({values}).",
        )
        return False

    def geographic(self, row: _Row, key: str) -> Position | None:
        lat_text, lon_text = row.get("LOCA_LAT").strip(), row.get("LOCA_LON").strip()
        if not (lat_text and lon_text):
            return None
        lat, lon = parse_degrees(lat_text), parse_degrees(lon_text)
        if lat is None or lon is None:
            for heading in ("LOCA_LAT", "LOCA_LON"):
                row.lose(
                    L.UNPARSED,
                    heading,
                    "Values of {heading} in {group} are not latitudes or longitudes pyDIGGS reads"
                    " (decimal degrees, or dd:mm:ss.ss and N, S, E or W); not converted"
                    " ({values}).",
                )
            return None
        row.use("LOCA_LAT", key)
        row.use("LOCA_LON", key)
        if lat_text != str(lat) or lon_text != str(lon):
            self.ledger.note(
                L.REFORMATTED,
                "LOCA",
                "Latitudes or longitudes of {group} were written in decimal degrees ({times}).",
                row.index,
            )
        known, srs = self.grid_crs(row, "LOCA_LLZ")
        llsi = self.geographic_srs(row, key, known, srs)
        if llsi is not None:
            srs = llsi
        elif known:
            row.use("LOCA_LLZ", key)
        else:
            if row.get("LOCA_LLZ").strip():
                row.lose(
                    L.UNMAPPED_CODE,
                    "LOCA_LLZ",
                    "Values of {heading} in {group} name no datum the code table lists; not"
                    " converted ({values}).",
                )
            srs = f"{_EPSG}4326"
            self.ledger.note(
                L.ASSUMED,
                "LOCA",
                "LOCA_LAT and LOCA_LON were written as WGS 84 (EPSG:4326), because LOCA_LLZ names"
                " no datum the code table lists ({times}).",
                row.index,
            )
        if row.get("LOCA_GL"):
            row.lose(
                L.UNMAPPED_HEADING,
                "LOCA_GL",
                "Values of {heading} in {group} were not converted: a ground level is written"
                " only with grid coordinates ({values}).",
            )
        return Position(coordinates=(lat, lon), dim=2, crs=srs)

    def geographic_srs(self, row: _Row, key: str, known: bool, llz: str | None) -> str | None:
        """The CRS LOCA_LLSI names, when it is a datum the code table lists (else None).

        LOCA_LLZ is used too when it names the same datum, and reported when it does not.
        """
        number = self.epsg_heading(row, "LOCA_LLSI")
        if number is None:
            return None
        srs = f"{_EPSG}{number}"
        listed = {s for (h, _), s in self.mapping.crs.items() if h == "LOCA_LLZ"}
        if srs not in listed:
            row.lose(
                L.UNMAPPED_CODE,
                "LOCA_LLSI",
                "Values of {heading} in {group} name no latitude and longitude datum the code"
                " table lists; not converted ({values}).",
            )
            return None
        row.use("LOCA_LLSI", key)
        if row.get("LOCA_LLZ").strip():
            if known and llz == srs:
                row.use("LOCA_LLZ", key)
            else:
                row.lose(
                    L.UNMAPPED_CODE,
                    "LOCA_LLZ",
                    "Values of {heading} in {group} disagree with LOCA_LLSI, whose EPSG code was"
                    " used; not converted ({values}).",
                )
        return srs

    # --- observations and samples -------------------------------------------------------

    def geology(self) -> None:
        rule = self.mapping.groups["GEOL"]
        layers: dict[str, list[LithologyLayer]] = {}
        origins: dict[str, _Row] = {}
        for row in self.rows("GEOL"):
            feature = self.feature_of(row)
            if feature is None:
                continue
            key = self.ids.make(f"{feature.key}-GEOL-{row.ordinal}")
            interval = self.interval(row, key, feature.lrs, "GEOL_TOP", "GEOL_BASE")
            params: dict[str, Param] = self.params(row, rule, "Lithology", key)
            if interval is None or "lithDescription" not in params:
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} lack a top, a base or a description (GEOL_DESC), which DIGGS"
                    " requires of a lithology layer; not converted ({rows}).",
                )
                self.ledger.forget(key)
                continue
            row.use("LOCA_ID", key)
            legend = params.get("legendCode")
            lithology = _node(
                Component,
                f"{key}.lithology",
                "Lithology",
                gml_id=None,
                role="primaryLithology",
                params=params,
            )
            layer = _node(
                LithologyLayer,
                key,
                "LithologyObservation",
                kind="lithology_layer",
                description=params["lithDescription"],
                remarks=self.remarks(row, rule, key),
                interval=interval,
                classification=() if legend is None else (Code(value=str(legend)),),
                geologic_unit=self.first_text(row, rule, "geologic_unit", key),
                components=(lithology,),
            )
            layers.setdefault(feature.key, []).append(layer)
            origins.setdefault(feature.key, row)
            self.origin(key, row)
        project = self.project() if layers else ""
        for feature_key, found in layers.items():
            key = self.ids.make(f"{feature_key}-GEOL")
            self.observations.append(
                _node(
                    Observation,
                    key,
                    "LithologySystem",
                    params={"lithologyClassificationType": CLASSIFICATION_DEFAULT},
                    system="LithologySystem",
                    feature=feature_key,
                    layers=tuple(found),
                )
            )
            self.link(key, "project", project)
            self.link(key, "samplingFeature", feature_key)
            for layer in found:
                self.parents.setdefault(layer.key, set()).add(key)
            self.origin(key, origins[feature_key])
            self.ledger.note(
                L.ASSUMED,
                "GEOL",
                "DIGGS requires a lithology classification type, which AGS4 does not state;"
                f" {CLASSIFICATION_DEFAULT!r} was written ({{times}}).",
            )

    def interval(
        self, row: _Row, key: str, lrs: str, top_heading: str, base_heading: str
    ) -> Interval | None:
        top = row.depth(top_heading, key)
        base = row.depth(base_heading, key)
        if top is None or base is None:
            return None
        return Interval(top=top, base=base, lrs=lrs)

    def samples_(self) -> None:
        rule = self.mapping.groups["SAMP"]
        for row in self.rows("SAMP"):
            feature = self.feature_of(row)
            if feature is None:
                continue
            sample_key = row.key(rule.keys)
            loca_id, top_text, ref, kind, sample_id = sample_key
            label = xml_text(sample_id) or " ".join(
                x for x in (xml_text(v) for v in (loca_id, top_text, ref, kind)) if x
            )
            key = self.ids.make(sample_id or f"{loca_id}-{top_text}-{ref}-{kind}")
            activity_key = self.ids.make(f"{key}-sampling")
            top = row.depth("SAMP_TOP", activity_key)
            if top is None:
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} have no SAMP_TOP that can be read, and DIGGS requires where"
                    " a sample was taken; not converted ({rows}).",
                )
                self.ledger.forget(activity_key)
                continue
            base = row.depth("SAMP_BASE", activity_key)
            if base is None:
                base = top
                self.ledger.note(
                    L.ASSUMED,
                    "SAMP",
                    "Samples with no SAMP_BASE: the sampling interval was written from SAMP_TOP"
                    " to SAMP_TOP ({times}).",
                    row.index,
                )
            for heading in ("LOCA_ID", "SAMP_ID"):
                row.use(heading, key)
            project = self.project()
            activity = _node(
                SamplingActivity,
                activity_key,
                "SamplingActivity",
                kind="sampling_activity",
                params={
                    "activityType": "collect",
                    **self.params(row, rule, "SamplingActivity", activity_key),
                },
                feature=feature.key,
                interval=Interval(top=top, base=base, lrs=feature.lrs),
                date=self.sample_date(row, activity_key),
            )
            params: dict[str, Param] = {}
            reference = row.text("SAMP_REF", key)
            if reference is not None:
                params["internalIdentifier"] = Code(
                    value=reference, code_space=f"{AGS4_CODE_SPACE}SAMP_REF"
                )
            sample_type = self.sample_type(row, key)
            if sample_type is not None:
                params["type"] = sample_type
            sample = _node(
                Sample,
                key,
                "Sample",
                kind="sample",
                names=(label,),
                description=self.first_text(row, rule, "description", key),
                remarks=self.remarks(row, rule, key),
                params=params,
                sampling_activity=activity_key,
            )
            self.activities.append(activity)
            self.samples.append(sample)
            self.sample_by_key.setdefault(sample_key, (key, feature))
            self.link(activity_key, "project", project)
            self.link(activity_key, "samplingFeature", feature.key)
            self.link(key, "project", project)
            self.link(key, "samplingActivity", activity_key)
            self.origin(activity_key, row)
            self.origin(key, row)

    def sample_date(self, row: _Row, key: str) -> TimeSpan | None:
        date = row.date("SAMP_DTIM", key)
        return None if date is None else TimeSpan(start=date)

    def sample_type(self, row: _Row, key: str) -> Param | None:
        code = row.text("SAMP_TYPE", key)
        if code is None:
            return None
        rule = self.mapping.code("SAMP_TYPE", code, self.joiner)
        if rule is not None and rule.code is not None and rule.code_list is not None:
            if (rule.heading, rule.ags) in self.mapping.shared:
                row.unuse("SAMP_TYPE", key)
                row.lose(
                    L.UNMAPPED_CODE,
                    "SAMP_TYPE",
                    "Values of {heading} in {group} are AGS4 codes that share their DIGGS sample"
                    " type with another AGS4 code (such as TW and UT); the DIGGS code was"
                    " written, and the AGS4 code is reported because DIGGS does not tell them"
                    " apart ({values}).",
                )
            return Code(value=rule.code, code_space=self.mapping.code_space(rule.code_list))
        self.ledger.note(
            L.UNMAPPED_CODE,
            "SAMP",
            "SAMP_TYPE codes with no entry in the code table were written as text, without a"
            " DIGGS code list ({times}).",
            row.index,
        )
        return code

    # --- tests --------------------------------------------------------------------------

    def tests(self, group: str) -> None:
        rule = self.mapping.groups[group]
        if rule.data is not None and self.is_series(rule):
            self.series_tests(rule)
            return
        gradings = self.gradings(rule) if rule.data is not None else {}
        for row in self.rows(group):
            parent = self.parent_of(row, rule)
            if parent is None:
                continue
            sample, feature = parent
            key = self.ids.make(f"{group}-{row.ordinal}")
            components = gradings.pop(row.key(rule.keys), ())
            measurement = self.test(row, rule, key, (feature, sample), components)
            if measurement is None and self.only_withheld:
                self.abandon(key, components)
                row.lose(
                    L.UNMAPPED_HEADING,
                    None,
                    "Rows of {group} whose results were all left out (each is reported) have no"
                    " other result DIGGS can hold as a test; not converted ({rows}).",
                )
                continue
            if measurement is None:
                self.abandon(key, components)
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} have no result value or no depth that can be written, and"
                    " DIGGS requires a test result with a location; not converted ({rows}).",
                )
                continue
            for heading in rule.keys:
                row.use(heading, key)
            self.measurements.append(measurement)
            self.link(key, "project", self.project())
            self.link(key, "samplingFeature", feature.key)
            if sample is not None:
                self.link(key, "sample", sample)
            self.origin(key, row)
        for left in gradings.values():
            self.abandon(
                None,
                left,
                L.ORPHAN_ROW,
                "Rows of {group} name a test that no converted row of its general group has; not"
                " converted ({rows}).",
            )

    def parent_of(self, row: _Row, rule: GroupRule) -> tuple[str | None, _Feature] | None:
        """The sample (for a test on a sample) and the feature a test row belongs to."""
        if rule.parent != "SAMP":
            feature = self.feature_of(row)
            return None if feature is None else (None, feature)
        found = self.sample_by_key.get(row.key(self.mapping.groups["SAMP"].keys))
        if found is None:
            row.lose(
                L.ORPHAN_ROW,
                None,
                "Rows of {group} name a sample that no converted SAMP row has; not converted"
                " ({rows}).",
            )
            return None
        return found

    def abandon(
        self,
        key: str | None,
        components: tuple[Component, ...],
        rule: str = L.INCOMPLETE,
        template: str = "Rows of {group} belong to a test that was not converted; not converted"
        " ({rows}).",
    ) -> None:
        """Forget a test that is not written, and name the data-group rows it would hold."""
        if key is not None:
            self.ledger.forget(key)
        for component in components:
            self.trace.pop(component.key, None)
            for child in component.components:
                self.ledger.forget(child.key)
                origin = self.trace.pop(child.key)
                self.ledger.lose(rule, origin.group, origin.row - 1, None, template)

    def test(
        self,
        row: _Row,
        rule: GroupRule,
        key: str,
        parent: tuple[_Feature, str | None],
        components: tuple[Component, ...],
    ) -> Measurement | None:
        feature, sample = parent
        lrs = feature.lrs
        depth: float | None = None
        placed = self.fields(rule, "location")
        for heading in (*placed, "SAMP_TOP"):
            found = row.depth(heading, key) if row.get(heading) else None
            if found is not None:
                depth = found.value
                if heading == "SAMP_TOP" and placed:
                    self.ledger.note(
                        L.ASSUMED,
                        row.group.name,
                        "Tests of {group} with no depth of their own were placed at the top of"
                        " their sample (SAMP_TOP) ({times}).",
                        row.index,
                    )
                break
        columns = self.columns(row, rule, key)
        if depth is None or not columns:
            return None
        procedure = Procedure(
            type=str(rule.procedure),
            params={**rule.procedure_params, **self.params(row, rule, str(rule.procedure), key)},
            components=components,
        )
        name = self.first_text(row, rule, "name", key)
        return _node(
            Measurement,
            key,
            "Test",
            names=() if name is None else (name,),
            description=self.first_text(row, rule, "description", key),
            remarks=self.remarks(row, rule, key),
            params=self.params(row, rule, "Test", key),
            feature=feature.key,
            samples=() if sample is None else (sample,),
            procedure=procedure,
            results=(
                ResultTable(
                    domain=Domain(shape="point"),
                    columns=tuple(columns),
                    row_count=1,
                    location=Position(coordinates=(depth,), dim=1, crs=lrs),
                ),
            ),
        )

    def withheld(self, row: _Row, rule: GroupRule) -> dict[str, str]:
        """Result headings of ``row`` not written on purpose, each with its finding's template.

        A water content or bulk density that AGS4 says is not natural (LNMC_ISNT = N, a
        recompacted LDEN specimen) is not written as the DIGGS property for natural
        conditions; an SPT N from a test drive that stopped short of 300 mm is not written
        as DIGGS n_value, which is blows per foot.
        """
        out: dict[str, str] = {}
        if rule.name == "LNMC" and row.get("LNMC_ISNT").strip().upper() == "N":
            out["LNMC_MC"] = (
                "Values of {heading} in {group} are not natural water contents (LNMC_ISNT is"
                " N), and DIGGS water_content_natural is for natural conditions; not converted"
                " ({values})."
            )
        if rule.name == "LDEN" and row.get("LDEN_COND").strip().upper() == "RECOMPACTED":
            for heading in ("LDEN_BDEN", "LDEN_MC"):
                out[heading] = (
                    "Values of {heading} in {group} are of a recompacted specimen (LDEN_COND),"
                    " and the DIGGS property is for natural conditions; not converted ({values})."
                )
        if rule.name == "ISPT" and self.short_drive(row):
            for heading in ("ISPT_NVAL", "ISPT_N60"):
                out[heading] = (
                    "Values of {heading} in {group} are from a test drive that stopped short of"
                    " 300 mm (ISPT_PEN3 to ISPT_PEN6), and DIGGS n_value and n60 are blows per"
                    " 300 mm; not converted, the drive is described in ISPT_REP ({values})."
                )
        return out

    @staticmethod
    def short_drive(row: _Row) -> bool:
        """True when the test drive increments are given and sum to less than 300 mm."""
        given = [row.get(f"ISPT_PEN{n}").strip() for n in range(3, 7)]
        given = [text for text in given if text]
        numbers = [parse_number(text) for text in given]
        if not given or any(n is None for n in numbers):
            return False
        return sum(n for n in numbers if n is not None) < _TEST_DRIVE_MM

    def columns(self, row: _Row, rule: GroupRule, key: str) -> list[Column]:
        facts = property_facts()
        out: list[Column] = []
        non_plastic = False
        withheld = self.withheld(row, rule)
        self.only_withheld = False
        for heading in rule.headings.values():
            if heading.to != "column" or heading.property is None or not row.get(heading.name):
                continue
            if heading.name in withheld:
                row.lose(L.UNMAPPED_HEADING, heading.name, withheld[heading.name])
                self.only_withheld = True
                continue
            prop = heading.property
            if prop in _LIMITS and row.get(heading.name).strip().upper() in _NON_PLASTIC:
                row.use(heading.name, key)
                non_plastic = True
                continue
            fact = facts.get(prop)
            uom = None
            if fact is not None and fact.has_unit:
                uom = row.uom(heading.name)
                if uom is None:
                    continue
            found = row.number(heading.name, key)
            if found is None:
                continue
            if uom is None:
                row.unit_lost(heading.name, _NO_PROPERTY_UNIT)
            out.append(
                Column(
                    name=heading.name,
                    key=prop,
                    definition=f"{PROPERTIES_URL}#{prop}",
                    uom=uom,
                    type="number",
                    values=(found,),
                )
            )
        if out or non_plastic:
            self.only_withheld = False
        if non_plastic:
            out.append(
                Column(
                    name="non_plastic",
                    key="non_plastic",
                    definition=f"{PROPERTIES_URL}#non_plastic",
                    type="boolean",
                    values=(True,),
                )
            )
        return out

    def grat_method(self, row: _Row, key: str) -> str | None:
        """The element a data-group row is written as, from GRAT_TYPE; None when not written.

        Only methods the code table names are written: sieving as a ``Grading`` of a sieve
        analysis, sedimentation by hydrometer as a ``Sedimentation`` reading. An empty
        GRAT_TYPE is taken as sieving and reported; any other method (a pipette, or a code
        the table does not list) is not converted, so no reading is filed under the wrong
        method.
        """
        code = row.get("GRAT_TYPE").strip()
        if code == "":
            self.ledger.note(
                L.ASSUMED,
                row.group.name,
                "Rows of {group} with no GRAT_TYPE were written as sieve gradings ({times}).",
                row.index,
            )
            return "Grading"
        found = self.mapping.code("GRAT_TYPE", code, self.joiner)
        if found is None or found.element not in _SIZE_FIELDS:
            row.lose(
                L.UNMAPPED_CODE,
                None,
                "Rows of {group} whose GRAT_TYPE names a method with no DIGGS target in the"
                " code table (such as a pipette analysis) were not converted ({rows}).",
            )
            return None
        if found.exact:
            row.use("GRAT_TYPE", key)
        else:
            row.lose(
                L.UNMAPPED_CODE,
                "GRAT_TYPE",
                "Values of {heading} in {group} name a method DIGGS does not tell apart (such as"
                " wet or dry sieving); those rows were written as sieve gradings, without the"
                " code ({values}).",
            )
        return found.element

    def gradings(self, rule: GroupRule) -> dict[tuple[str, ...], tuple[Component, ...]]:
        """The rows of ``rule``'s data group: a sieve analysis and a hydrometer analysis per key."""
        data = self.mapping.groups[str(rule.data)]
        by_key: dict[tuple[str, ...], dict[str, list[Component]]] = {}
        for row in self.rows(data.name):
            key = self.ids.make(f"{data.name}-{row.ordinal}")
            element = self.grat_method(row, key)
            if element is None:
                continue
            size_name = _SIZE_FIELDS[element]
            params = {
                (size_name if child == "particleSize" else child): value
                for child, value in self.params(row, data, "Grading", key).items()
            }
            if size_name not in params:
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} have no particle size that can be written, which DIGGS"
                    " requires of a grading; not converted ({rows}).",
                )
                self.ledger.forget(key)
                continue
            for heading in data.headings.values():
                if heading.to == "link":
                    row.use(heading.name, key)
            reading = _node(
                Component,
                key,
                element,
                role=_HOLDERS[element][2],
                remarks=self.remarks(row, data, key),
                params=params,
            )
            by_key.setdefault(row.key(rule.keys), {}).setdefault(element, []).append(reading)
            self.origin(key, row)
        out: dict[tuple[str, ...], tuple[Component, ...]] = {}
        for test_key, found in by_key.items():
            holders: list[Component] = []
            for element, readings in found.items():
                holder, role, _ = _HOLDERS[element]
                key = self.ids.make(f"{readings[0].key}-{role}")
                holders.append(_node(Component, key, holder, role=role, components=tuple(readings)))
            out[test_key] = tuple(holders)
        return out

    # --- a test and its readings: a cone test ---------------------------------------------

    def is_series(self, rule: GroupRule) -> bool:
        """True when ``rule``'s data group holds result columns: one row per reading."""
        data = self.mapping.groups[str(rule.data)]
        return any(h.to == "column" for h in data.headings.values())

    def readings(self, rule: GroupRule) -> dict[tuple[str, ...], list[tuple[_Row, float]]]:
        """The data-group rows that can be written, with their depth, by test key."""
        data = self.mapping.groups[str(rule.data)]
        index = next(h.name for h in data.headings.values() if h.target == "index.position")
        columns = [h.name for h in data.headings.values() if h.to == "column"]
        out: dict[tuple[str, ...], list[tuple[_Row, float]]] = {}
        for row in self.rows(data.name):
            depth = row.depth(index, _PENDING) if row.get(index) else None
            if depth is None or not any(row.get(h) for h in columns):
                row.unuse(index, _PENDING)
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} have no depth or no reading that can be written, and DIGGS"
                    " requires both of a result row; not converted ({rows}).",
                )
                continue
            out.setdefault(row.key(rule.keys), []).append((row, float(depth.value or 0.0)))
        return out

    def series_tests(self, rule: GroupRule) -> None:
        data = self.mapping.groups[str(rule.data)]
        found = self.readings(rule)
        for row in self.rows(rule.name):
            parent = self.parent_of(row, rule)
            if parent is None:
                continue
            _sample, feature = parent
            key = self.ids.make(f"{rule.name}-{row.ordinal}")
            readings = found.pop(row.key(rule.keys), [])
            kind = next((h for h in rule.headings if h.endswith("_TYPE")), None)
            if kind is not None and row.get(kind).strip().upper() in _NOT_CONES:
                self.not_a_cone(row, data, readings)
                continue
            table = self.series_table(data, readings, key, feature.lrs)
            if table is None:
                for reading, _depth in readings:
                    self.forget_pending(reading, data)
                    reading.lose(
                        L.INCOMPLETE,
                        None,
                        "Rows of {group} belong to a test that was not converted; not converted"
                        " ({rows}).",
                    )
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} have no reading that can be written, and DIGGS requires a"
                    " test result; not converted ({rows}).",
                )
                continue
            for heading in rule.keys:
                row.use(heading, key)
            name = self.first_text(row, rule, "name", key)
            self.measurements.append(
                _node(
                    Measurement,
                    key,
                    "Test",
                    names=() if name is None else (name,),
                    remarks=self.remarks(row, rule, key),
                    feature=feature.key,
                    procedure=Procedure(
                        type=str(rule.procedure),
                        params={
                            **rule.procedure_params,
                            **self.params(row, rule, str(rule.procedure), key),
                        },
                    ),
                    results=(table,),
                )
            )
            self.link(key, "project", self.project())
            self.link(key, "samplingFeature", feature.key)
            self.origin(key, row)
            for reading, _depth in readings:
                for heading in data.keys:
                    reading.use(heading, key)
        for left in found.values():
            for reading, _depth in left:
                self.forget_pending(reading, data)
                reading.lose(
                    L.ORPHAN_ROW,
                    None,
                    "Rows of {group} name a test that no converted row of its general group has;"
                    " not converted ({rows}).",
                )

    def not_a_cone(self, row: _Row, data: GroupRule, readings: list[tuple[_Row, float]]) -> None:
        """Report a ball or T-bar test and its readings: their resistance is not a cone's."""
        for reading, _depth in readings:
            self.forget_pending(reading, data)
            reading.lose(
                L.UNMAPPED_CODE,
                None,
                "Rows of {group} are readings of a ball or T-bar penetrometer test, whose"
                " resistance (q_m) is not the cone resistance DIGGS tip_resistance holds; not"
                " converted ({rows}).",
            )
        row.lose(
            L.UNMAPPED_CODE,
            None,
            "Rows of {group} are ball or T-bar penetrometer tests, whose resistance (q_m) is not"
            " the cone resistance DIGGS tip_resistance holds; not converted ({rows}).",
        )

    def series_table(
        self, data: GroupRule, readings: list[tuple[_Row, float]], key: str, lrs: str
    ) -> ResultTable | None:
        """The result table of one test: a row per reading, indexed by depth."""
        if not readings:
            return None
        facts = property_facts()
        index = next(h.name for h in data.headings.values() if h.target == "index.position")
        columns: list[Column] = []
        for heading in data.headings.values():
            if heading.to != "column" or heading.property is None:
                continue
            prop = heading.property
            values: list[float | None] = []
            uom: str | None = None
            for reading, _depth in readings:
                value: float | None = None
                if reading.get(heading.name):
                    fact = facts.get(prop)
                    unit = reading.uom(heading.name) if fact is not None and fact.has_unit else ""
                    if unit is not None:
                        value = reading.number(heading.name, key)
                        uom = unit or None
                        if value is not None and not unit:
                            reading.unit_lost(heading.name, _NO_PROPERTY_UNIT)
                values.append(value)
            if any(v is not None for v in values):
                columns.append(
                    Column(
                        name=heading.name,
                        key=prop,
                        definition=f"{PROPERTIES_URL}#{prop}",
                        uom=uom,
                        type="number",
                        values=tuple(values),
                    )
                )
        if not columns:
            return None
        depths: list[float] = []
        for reading, depth in readings:
            reading.unuse(index, _PENDING)
            reading.number(index, key)
            depths.append(depth)
        position = Column(
            name="position", key="position", uom=DEPTH_UNIT, type="number", values=tuple(depths)
        )
        return ResultTable(
            domain=Domain(shape="point", index=(position,), crs=lrs),
            columns=tuple(columns),
            row_count=len(readings),
        )

    def forget_pending(self, row: _Row, data: GroupRule) -> None:
        """Release the cells a reading held while its test was not yet made."""
        for heading in data.headings:
            row.unuse(heading, _PENDING)

    # --- records a feature holds: hole diameter, casing, backfill, water strikes --------

    def point(self, row: _Row, heading: str, key: str, lrs: str) -> Position | None:
        """A depth along the feature as a point in its linear referencing system."""
        found = row.depth(heading, key)
        if found is None or found.value is None:
            return None
        if written_number(found.value) != found.text:
            self.ledger.note(
                L.REFORMATTED,
                row.group.name,
                "Numbers of {group} were written in a shorter form, the same value ({times}).",
                row.index,
            )
        return Position(coordinates=(found.value,), dim=1, crs=lrs)

    def feature_components(self) -> None:
        """Hole diameters, casings, backfill and water strikes, onto the features."""
        held: dict[str, list[Component]] = {}
        strikes: dict[str, list[Component]] = {}
        for name, rule in self.mapping.groups.items():
            if rule.pattern != "feature-component" or rule.element is None:
                continue
            if rule.parent != "LOCA" or name not in self.file.groups:
                continue
            target = strikes if rule.role == "waterStrike" else held
            for feature_key, component in self.components(rule):
                target.setdefault(feature_key, []).append(component)
        self.features = [
            replace(
                feature,
                construction=tuple(held.get(feature.key, ())),
                water_strikes=tuple(strikes.get(feature.key, ())),
            )
            for feature in self.features
        ]

    def holds(self, feature: str, role: str) -> bool:
        element = next(f.element for f in self.features if f.key == feature)
        return role in load_layout(DIGGS_VERSION).element(element).by_local

    def components(self, rule: GroupRule) -> list[tuple[str, Component]]:
        """The components of ``rule``'s rows, by the key of the feature that holds each."""
        by_feature: dict[str, list[_Row]] = {}
        for row in self.rows(rule.name):
            feature = self.feature_of(row)
            if feature is None:
                continue
            if not self.holds(feature.key, rule.holder_role or str(rule.role)):
                row.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} belong to a location whose DIGGS element cannot hold them;"
                    " not converted ({rows}).",
                )
                continue
            by_feature.setdefault(feature.key, []).append(row)
        out: list[tuple[str, Component]] = []
        readings = self.strike_readings(rule) if rule.data is not None else {}
        for feature_key, rows in by_feature.items():
            lrs = self.feature_lrs(feature_key)
            made = [
                c
                for c in (
                    self.component(rule, row, lrs, readings) for row in self.ordered(rule, rows)
                )
                if c is not None
            ]
            if rule.holder is not None and made:
                key = self.ids.make(f"{feature_key}-{rule.holder_role}")
                holder = _node(
                    Component, key, rule.holder, role=str(rule.holder_role), components=tuple(made)
                )
                out.append((feature_key, holder))
            else:
                out.extend((feature_key, c) for c in made)
        for left in readings.values():
            for reading in left:
                reading.lose(
                    L.ORPHAN_ROW,
                    None,
                    "Rows of {group} name a water strike that no converted row of its general"
                    " group has; not converted ({rows}).",
                )
        return out

    def feature_lrs(self, feature_key: str) -> str:
        return next(f.lrs for f in self.feature_by_id.values() if f.key == feature_key)

    def ordered(self, rule: GroupRule, rows: list[_Row]) -> list[_Row]:
        """Rows by depth when the interval starts at the previous row's base, else as given."""
        if rule.interval_top != "previous-base":
            return rows
        base = self.fields(rule, "interval.base")[0]

        def depth(row: _Row) -> float:
            value = parse_number(row.get(base).strip())
            return float("inf") if value is None else value

        return sorted(rows, key=depth)

    def component(
        self,
        rule: GroupRule,
        row: _Row,
        lrs: str,
        readings: dict[tuple[str, ...], list[_Row]],
    ) -> Component | None:
        key = self.ids.make(f"{row.get('LOCA_ID')}-{rule.name}-{row.ordinal}")
        params = self.params(row, rule, str(rule.element), key)
        interval: Interval | None = None
        location: Position | None = None
        components: tuple[Component, ...] = ()
        complete = True
        if self.fields(rule, "interval.base"):
            interval = self.component_interval(rule, row, key, lrs)
            complete = interval is not None
        if self.fields(rule, "location"):
            heading = self.fields(rule, "location")[0]
            location = self.point(row, heading, key, lrs) if row.get(heading) else None
        extra: tuple[ExtraNode, ...] = ()
        if rule.role == "waterStrike":
            strike = self.strike(rule, row, key, lrs)
            complete = strike is not None
            if strike is not None:
                later = tuple(self.later_readings(rule, row, readings, lrs))
                components = (replace(strike, components=later),)
                extra = self.places(rule, row, key, lrs)
        required = [p.split("/")[1] for p in self.required_params(rule)]
        if not complete or any(name not in params for name in required):
            self.ledger.forget(key)
            for component in (*components, *(c for x in components for c in x.components)):
                self.ledger.forget(component.key)
                self.trace.pop(component.key, None)
            row.lose(
                L.INCOMPLETE,
                None,
                "Rows of {group} lack a value DIGGS requires of the record (a depth, or "
                + (", ".join(required) or "a reading")
                + "); not converted ({rows}).",
            )
            return None
        for heading in rule.keys:
            row.use(heading, key)
        self.origin(key, row)
        return _node(
            Component,
            key,
            str(rule.element),
            role=str(rule.role),
            remarks=self.remarks(row, rule, key),
            params=params,
            interval=interval,
            location=location,
            components=components,
            extra=extra,
        )

    def places(self, rule: GroupRule, row: _Row, key: str, lrs: str) -> tuple[ExtraNode, ...]:
        """Depths along the feature that DIGGS holds as a further point location of a record.

        The table names each by its DIGGS element (WSTG_CAS: ``bottomCasing``). The model
        has no typed field for them, so each is written as the element itself, as
        :func:`pydiggs.read` gives it back in ``extra``.
        """
        out: list[ExtraNode] = []
        for heading in rule.headings.values():
            if heading.to != "field" or heading.target != "bottomCasing":
                continue
            point = self.point(row, heading.name, key, lrs) if row.get(heading.name) else None
            if point is None:
                continue
            pos = ExtraNode(qname="gml:pos", ns=_GML_NS, text=written_number(point.coordinates[0]))
            location = ExtraNode(
                qname="PointLocation",
                ns=_DIGGS_NS,
                attrs={"srsName": lrs, "srsDimension": "1"},
                children=(pos,),
            )
            out.append(ExtraNode(qname=heading.target, ns=_DIGGS_NS, children=(location,)))
        return tuple(out)

    def required_params(self, rule: GroupRule) -> list[str]:
        """The ``param`` targets of ``rule`` its DIGGS element requires."""
        lay = load_layout(DIGGS_VERSION).element(str(rule.element))
        needed = {tag for need in lay.required for tag in need}
        out = []
        for heading in rule.headings.values():
            if heading.to == "param" and heading.target is not None:
                child = heading.target.split("/")[1]
                if lay.by_local.get(child) in needed:
                    out.append(heading.target)
        return out

    def component_interval(self, rule: GroupRule, row: _Row, key: str, lrs: str) -> Interval | None:
        base = row.depth(self.fields(rule, "interval.base")[0], key)
        if base is None:
            return None
        tops = self.fields(rule, "interval.top")
        if tops:
            top = row.depth(tops[0], key)
            return None if top is None else Interval(top=top, base=base, lrs=lrs)
        top = (
            self.previous_base.get((rule.name, lrs))
            if rule.interval_top == "previous-base"
            else None
        )
        if top is None:
            top = Quantity(text="0", value=0.0, uom=DEPTH_UNIT)
            self.ledger.note(
                L.ASSUMED,
                rule.name,
                "AGS4 gives only the base depth of each section of {group}; "
                + (
                    "each casing was taken to run from the surface (0) to that depth"
                    if rule.interval_top == "surface"
                    else "each section was taken to start at the base of the previous one, the"
                    " first at the surface (0)"
                )
                + " ({times}).",
                row.index,
            )
        if rule.interval_top == "previous-base":
            self.previous_base[(rule.name, lrs)] = base
        return Interval(top=top, base=base, lrs=lrs)

    def strike(self, rule: GroupRule, row: _Row, key: str, lrs: str) -> Component | None:
        """The first reading of a water strike: the water at the strike depth, when struck."""
        depth = self.fields(rule, "strike.location")[0]
        location = self.point(row, depth, key, lrs) if row.get(depth) else None
        if location is None:
            return None
        reading_key = self.ids.make(f"{key}-strike")
        params: dict[str, Param] = {}
        for heading in self.fields(rule, "strike.dateTime"):
            date = row.date(heading, key)
            if date is not None:
                params["dateTime"] = date
        self.origin(reading_key, row)
        return _node(
            Component,
            reading_key,
            "WaterStrikeReading",
            role="waterStrikeReadings",
            params=params,
            location=location,
        )

    def strike_readings(self, rule: GroupRule) -> dict[tuple[str, ...], list[_Row]]:
        data = self.mapping.groups[str(rule.data)]
        out: dict[tuple[str, ...], list[_Row]] = {}
        for row in self.rows(data.name):
            out.setdefault(row.key(rule.keys), []).append(row)
        return out

    def later_readings(
        self,
        rule: GroupRule,
        row: _Row,
        readings: dict[tuple[str, ...], list[_Row]],
        lrs: str,
    ) -> list[Component]:
        """The readings after the strike: one per WSTD row of the same strike."""
        data = self.mapping.groups[str(rule.data)]
        out: list[Component] = []
        for reading in readings.pop(row.key(rule.keys), []):
            key = self.ids.make(f"{row.get('LOCA_ID')}-{data.name}-{reading.ordinal}")
            heading = self.fields(data, "location")[0]
            location = self.point(reading, heading, key, lrs) if reading.get(heading) else None
            if location is None:
                self.ledger.forget(key)
                reading.lose(
                    L.INCOMPLETE,
                    None,
                    "Rows of {group} give no depth to water that can be written, which DIGGS"
                    " requires of a water strike reading; not converted ({rows}).",
                )
                continue
            params = self.params(reading, data, "LinkedWSReading", key)
            for name in data.keys:
                if name in rule.keys or reading.get(name) == "":
                    reading.use(name, key)
            self.origin(key, reading)
            out.append(
                _node(
                    Component,
                    key,
                    "LinkedWSReading",
                    role="linkedReading",
                    remarks=self.remarks(reading, data, key),
                    params=params,
                    location=location,
                )
            )
        return out

    # --- what is left -------------------------------------------------------------------

    def finish(self) -> None:
        """Name every cell nothing used: unmapped groups and headings, user-defined content."""
        mapped = self.mapping.groups
        pending: dict[tuple[str, int], list[int]] = {}
        for cell in self.ledger.cells():
            if not self.ledger.is_done(cell):
                pending.setdefault(cell[:2], []).append(cell[2])
        for (group, row), columns in pending.items():
            rule = mapped.get(group)
            if rule is None:
                if group in self.user_groups:
                    self.ledger.lose(
                        L.USER_DEFINED,
                        group,
                        row,
                        None,
                        "Group {group} is user-defined (in DICT): reported, never interpreted;"
                        " not converted ({rows}).",
                    )
                else:
                    self.ledger.lose(
                        L.UNMAPPED_GROUP,
                        group,
                        row,
                        None,
                        "Group {group} has no mapping to DIGGS; not converted ({rows}).",
                    )
                continue
            headings = self.file.groups[group].headings
            for column in columns:
                self.leftover(rule, group, row, headings[column], column)

    def leftover(self, rule: GroupRule, group: str, row: int, heading: str, column: int) -> None:
        """Name the value in ``column`` (a heading may be repeated, so not by its name)."""
        listed = rule.headings.get(heading)
        if listed is not None and listed.to == "none":
            template = (
                "Heading {heading} of {group} has no DIGGS target ("
                + str(listed.reason)
                + "); not converted ({values})."
            )
            self.ledger.lose(L.UNMAPPED_HEADING, group, row, heading, template, column=column)
        elif listed is not None:
            self.ledger.lose(
                L.UNMAPPED_HEADING,
                group,
                row,
                heading,
                "Values of {heading} in {group} were not converted: the record they belong to was"
                " written without them ({values}).",
                column=column,
            )
        elif (group, heading) in self.user_headings:
            self.ledger.lose(
                L.USER_DEFINED,
                group,
                row,
                heading,
                "Heading {heading} of {group} is user-defined (in DICT): reported, never"
                " interpreted; not converted ({values}).",
                column=column,
            )
        else:
            self.ledger.lose(
                L.UNMAPPED_HEADING,
                group,
                row,
                heading,
                "Heading {heading} of {group} is not in the mapping table; not converted"
                " ({values}).",
                column=column,
            )

    # --- the whole file -----------------------------------------------------------------

    def run(self) -> None:
        try:
            self.lookups()
            self.transmission()
            self.projects_()
            self.features_()
            self.feature_components()
            if "GEOL" in self.file.groups:
                self.geology()
            if "SAMP" in self.file.groups:
                self.samples_()
            data = {rule.data for rule in self.mapping.groups.values() if rule.data}
            for name, rule in self.mapping.groups.items():
                tested = rule.pattern in ("test-per-row", "test-table") and name not in data
                if tested and name in self.file.groups:
                    self.tests(name)
        except _Stop:
            self.complete = False
            self.stopped()
            self.ledger.note(
                L.INCOMPLETE,
                next(iter(self.file.groups)),
                "The conversion ran out of its time budget; the output is partial.",
            )
            for cell in list(self.ledger.cells()):
                if not self.ledger.is_done(cell):
                    group, row, _ = cell
                    self.ledger.lose(
                        L.INCOMPLETE,
                        group,
                        row,
                        None,
                        "Rows of {group} were not converted: the time budget ran out ({rows}).",
                    )
        if self.complete:
            self.finish()

    def stopped(self) -> None:
        """After the deadline: keep only what is in the model, so nothing is claimed unwritten.

        A step stopped part way may hold nodes it had not yet added to the model (the
        layers of a feature, the readings of a test) and the cells they used. Those
        cells are released, so the time-budget finding names them; the document
        information is made (with no source value) if the stop came before it.
        """
        if self.document is None:
            self.ledger.forget(self.document_key)
            self.document = self.document_info(None, None)
        written = self.written()
        for owners in self.ledger.used.values():
            owners.intersection_update(written)
        self.trace = {key: origin for key, origin in self.trace.items() if key in written}

    def written(self) -> set[str]:
        """The key of every node and component the model will hold, and "" (format)."""
        nodes: list[Node] = [
            *self.projects,
            *self.features,
            *self.activities,
            *self.samples,
            *self.observations,
            *self.measurements,
        ]
        nodes += [layer for observation in self.observations for layer in observation.layers]
        keys = {"", self.document_key, *(node.key for node in nodes)}
        keys |= {component.key for node in nodes for component in components_of(node)}
        return keys

    def model(self, name: str | None) -> Model:
        if self.document is None:  # pragma: no cover - run() always makes one
            raise AssertionError
        return Model(
            schema_version="1.0",
            tool={"name": "pydiggs.convert"},
            source=SourceInfo(
                name=name, sha256="", size=0, diggs_version="3.0.0", namespace=None, detected=False
            ),
            complete=self.complete,
            document=self.document,
            projects=tuple(self.projects),
            features=tuple(self.features),
            sampling_activities=tuple(self.activities),
            samples=tuple(self.samples),
            observations=tuple(self.observations),
            measurements=tuple(self.measurements),
            crs=MappingProxyType(self.crs),
            links=tuple(self.links),
        )


def map_file(
    file: File,
    mapping: MappingTable,
    *,
    name: str | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = lambda: 0.0,
) -> Built:
    """Build the model of ``file`` by ``mapping``, with its trace and its ledger.

    ``deadline`` is a time on ``clock``; when it passes, mapping stops, the model holds
    what was built, every cell not yet used is named ``convert.incomplete``, and
    ``Built.complete`` is False. The file must have at least one group.
    """
    mapper = _Mapper(file, mapping, deadline, clock)
    mapper.run()
    return Built(
        model=mapper.model(name),
        trace=mapper.trace,
        ledger=mapper.ledger,
        complete=mapper.complete,
        parents=mapper.parents,
    )


def components_of(node: Node) -> list[Component]:
    """Every component ``node`` holds, however deeply (a procedure's too)."""
    stack: list[Component] = []
    if isinstance(node, Feature):
        stack += [*node.construction, *node.water_strikes]
    elif isinstance(node, LithologyLayer):
        stack += node.components
    elif isinstance(node, Measurement) and node.procedure is not None:
        stack += node.procedure.components
    found: list[Component] = []
    while stack:
        component = stack.pop()
        found.append(component)
        stack.extend(component.components)
    return found


def _descendants(model: Model, keys: set[str], parents: Mapping[str, set[str]]) -> set[str]:
    """``keys`` and every node that refers to one of them, however indirectly."""
    out = set(keys)
    nodes = [node.key for node in model.nodes()]
    changed = True
    while changed:
        changed = False
        for key in nodes:
            if key not in out and parents.get(key, set()) & out:
                out.add(key)
                changed = True
    return out


def drop(built: Built, key: str) -> Built:
    """``built`` without node ``key`` and every node that refers to it.

    Each source cell only those nodes held is named ``convert.incomplete``: the writer
    refused the node, so the record is not in the output.
    """
    model = built.model
    gone = _descendants(model, {key}, built.parents)
    template = (
        "Values of {group} were not converted: DIGGS 3.0.0 requires something of the record"
        " they belong to that the source does not give ({values})."
    )
    for node in list(model.nodes()):
        if node.key in gone:
            gone |= {component.key for component in components_of(node)}
    for node in model.nodes():
        if node.key in gone:
            built.ledger.release(node.key, L.INCOMPLETE, template)

    def keep(nodes: tuple[_N, ...]) -> tuple[_N, ...]:
        return tuple(node for node in nodes if node.key not in gone)

    observations = tuple(
        replace(o, layers=tuple(layer for layer in o.layers if layer.key not in gone))
        for o in model.observations
        if o.key not in gone
    )
    observations = tuple(o for o in observations if o.layers)
    kept = {o.key for o in observations}
    gone |= {o.key for o in model.observations} - kept
    rebuilt = replace(
        model,
        projects=keep(model.projects),
        features=keep(model.features),
        sampling_activities=keep(model.sampling_activities),
        samples=keep(model.samples),
        observations=observations,
        measurements=keep(model.measurements),
        links=tuple(
            link for link in model.links if link.source not in gone and link.target not in gone
        ),
    )
    trace = {k: v for k, v in built.trace.items() if k not in gone and rebuilt.get(k) is not None}
    return Built(
        model=rebuilt,
        trace=trace,
        ledger=built.ledger,
        complete=built.complete,
        parents=built.parents,
    )
