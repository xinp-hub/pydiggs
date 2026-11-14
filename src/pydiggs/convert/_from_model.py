"""A diggs-model :class:`~pydiggs.model.Model` to AGS4, by the mapping table. Pure.

:func:`map_model` is the inverse of :mod:`._to_model`, driven by the same table: each
heading is written from the DIGGS value the table reads it into (headings whose
``direction`` is ``to-diggs`` are skipped). It does no I/O and reads no clock, so the same
model always gives the same file.

Accounting is at model level. Every *item* of the model is either written into a cell or
named by a ``convert`` finding: each name and remark, each ``params`` entry, each typed
field that holds a value, each ``extra`` node, each link that is not structure, and each
cell of a result table. Keys, ``kind``, ``provenance``, ``gml:id`` values, the shortcuts
the reader derives (a layer's description and codes, a sample's interval) and the links
that place a record (``project``, ``samplingFeature``, ``sample`` …) are structure, not
items: the AGS4 keys carry them.

AGS4 has no place for the TYPE of a heading other than the data dictionary, which pyDIGGS
does not bundle; the converter gives each written heading the TYPE its values have
(``ID`` for an identifier, ``PA`` for a code listed in ABBR, ``DT`` for a date, ``nDP``
for numbers with ``n`` decimal places, ``U`` for numbers of mixed form, ``X`` for text).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields
from typing import Final

from pydiggs.ags4 import File, Group
from pydiggs.convert import _ledger as L  # noqa: N812 - rule-id constants
from pydiggs.convert._ledger import literal
from pydiggs.convert._mapping import GroupRule, HeadingRule, MappingTable
from pydiggs.convert._to_model import (
    AGS4_CODE_SPACE,
    CLASSIFICATION_DEFAULT,
    PROJECT_DEFAULT,
    written_number,
)
from pydiggs.convert._values import parse_number
from pydiggs.findings.model import Finding, Step
from pydiggs.model import (
    Code,
    Column,
    Component,
    DocumentInfo,
    Feature,
    Interval,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Observation,
    Param,
    Position,
    Project,
    Quantity,
    ResultTable,
    Sample,
    SamplingActivity,
    TimeSpan,
)

__all__ = ["Exported", "ModelLedger", "formula_like", "map_model"]

#: Relations that place a record; the AGS4 keys carry them, so they are not items.
_STRUCTURE: Final = frozenset(
    {
        "project",
        "samplingFeature",
        "sample",
        "samplingActivity",
        "sampleProduced",
        "linearElement",
        "lrm",
        "referencePoint",
    }
)
#: Typed fields that are not items: structure, or shortcuts the reader derives.
_NOT_ITEMS: Final = frozenset(
    {
        "key",
        "gml_id",
        "element",
        "kind",
        "name",
        "provenance",
        "role",
        "lrs",
        "feature",
        "sampling_activity",
        "samples",
        "components",
        "construction",
        "water_strikes",
        "layers",
        "system",
    }
)
#: Shortcuts the reader copies from elsewhere, per node class.
_DERIVED: Final[Mapping[type, frozenset[str]]] = {
    LithologyLayer: frozenset({"description", "classification", "colour"}),
    Sample: frozenset({"interval", "method", "recovery"}),
}
_LINE_BREAK: Final = re.compile(r"[\r\n]")
#: ``extra`` elements that are structure (see :func:`_helper`).
_HELPER_EXTRA: Final = frozenset({"linearReferencing", "sampleProduced"})
#: Text a spreadsheet takes for a formula: the start of a value.
_FORMULA_START: Final = ("=", "+", "@", "-")
_U_NUMBER: Final = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_DECIMALS: Final = re.compile(r"[+-]?\d+(?:\.(\d+))?")
_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_TIME: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_SAFE_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}")
#: Headings whose values are identifiers (TYPE ``ID``).
_ID_HEADINGS: Final = frozenset({"LOCA_ID", "SAMP_ID", "PROJ_ID"})
#: Headings whose values are codes of the code table (TYPE ``PA``, listed in ABBR).
_PA_HEADINGS: Final = frozenset({"LOCA_TYPE", "SAMP_TYPE", "GRAT_TYPE"})
#: The one limit AGS4 lets hold NP for a non-plastic soil (type XN; LL and PI are 0DP).
_NP_HEADING: Final = "LLPL_PL"
_DIGGS_NS: Final = "http://diggsml.org/schemas/3"
#: The EPSG code at the end of an srsName.
_EPSG_URI: Final = re.compile(r"http://www\.opengis\.net/def/crs/EPSG/0/(\d{1,6})")
#: Descriptions of the data types the converter writes (its own words).
_TYPE_TEXT: Final = {
    "ID": "Unique identifier",
    "PA": "Text listed in the ABBR group",
    "X": "Text",
    "DT": "Date and time in ISO 8601 form",
    "U": "Number of variable format",
}
#: A water strike's role on a feature, and the role of its one reading.
_STRIKE: Final = "waterStrike"


def formula_like(value: str) -> bool:
    """True for text a spreadsheet reads as a formula: ``=``, ``+``, ``@``, or ``-`` not a number.

    Examples:
        >>> (
        ...     formula_like("=SUM(A1)"),
        ...     formula_like("-1.5"),
        ...     formula_like("-dense"),
        ...     formula_like("x"),
        ... )
        (True, False, True, False)
    """
    if len(value) < 2 or not value.startswith(_FORMULA_START):  # noqa: PLR2004
        return False
    return not (value[0] in "+-" and _U_NUMBER.fullmatch(value) is not None)


def _label(text: str) -> str:
    return text if _SAFE_NAME.fullmatch(text) else literal(text)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


# --- the model-level ledger ----------------------------------------------------------

#: One item: the key of the node that holds it and where in the node it is.
Item = tuple[str, str]


@dataclass(slots=True)
class _Issue:
    rule: str
    template: str
    element: str
    field: str
    xpath: str | None
    line: int | None
    items: int = 0
    times: int = 0


def _category(path: str) -> str:
    """``results[0].tip_resistance[3]`` -> ``results.tip_resistance``: what a finding names."""
    return re.sub(r"\[\d+\]", "", path)


class ModelLedger:
    """Every item of a model: written into a cell, or named by an issue."""

    def __init__(self, model: Model) -> None:
        self.model = model
        self.items: dict[Item, Node] = {}
        self.by_node: dict[str, list[str]] = {}
        self.used: set[Item] = set()
        self.named: dict[Item, int] = {}
        self._issues: list[_Issue] = []
        self._index: dict[tuple[str, str, str, str], int] = {}
        for node in model.nodes():
            for path in _items(node):
                self.items[(node.key, path)] = node
        for number, link in enumerate(model.links):
            if link.relation not in _STRUCTURE:
                source = model.get(link.source)
                if source is not None:
                    self.items[(source.key, f"link.{link.relation}[{number}]")] = source
        for key, path in self.items:
            self.by_node.setdefault(key, []).append(path)

    def use(self, node: Node, path: str) -> None:
        """Mark ``path`` of ``node`` (and everything under it) as written."""
        for item in self._under(node.key, path):
            self.used.add(item)

    def _under(self, key: str, path: str) -> Iterator[Item]:
        if (key, path) in self.items:
            yield key, path
            return
        for found in self.by_node.get(key, ()):
            if found.startswith((f"{path}.", f"{path}[")):
                yield key, found

    def lose(self, rule: str, node: Node, path: str | None, template: str) -> None:
        """Name ``path`` of ``node`` (all of the node for None) as not written."""
        element = node.element
        found = list(self.items_of(node.key) if path is None else self._under(node.key, path))
        if not found and path is not None:
            return
        shown = "the record" if path is None else _category(path)
        key = (rule, template, element, shown)
        index = self._index.get(key)
        if index is None:
            index = len(self._issues)
            self._issues.append(
                _Issue(
                    rule,
                    template,
                    element,
                    shown,
                    node.provenance.xpath or None,
                    node.provenance.line,
                )
            )
            self._index[key] = index
        issue = self._issues[index]
        issue.times += 1
        for item in found:
            if item not in self.named and item not in self.used:
                self.named[item] = index
                issue.items += 1

    def note(self, rule: str, element: str, template: str) -> None:
        """Report something that names no item (a default written, a value reformatted)."""
        key = (rule, template, element, "")
        index = self._index.get(key)
        if index is None:
            index = len(self._issues)
            self._issues.append(_Issue(rule, template, element, "", None, None))
            self._index[key] = index
        self._issues[index].times += 1

    def items_of(self, key: str) -> Iterator[Item]:
        return ((key, path) for path in self.by_node.get(key, ()))

    def is_done(self, item: Item) -> bool:
        return item in self.used or item in self.named

    def finish(self) -> None:
        """Name every item nothing wrote, one finding per element and field."""
        for item, node in self.items.items():
            if not self.is_done(item):
                self.lose(
                    L.UNMAPPED_ELEMENT,
                    node,
                    item[1],
                    "Values of {field} of {element} have no AGS4 target in the mapping table;"
                    " not converted ({values}).",
                )

    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for issue in self._issues:
            message = issue.template.format(
                element=_label(issue.element),
                field=_label(issue.field) if issue.field != "the record" else "the record",
                values=_count(issue.items, "value"),
                times=_count(issue.times, "time"),
            )
            out.append(
                Finding(
                    step=Step.CONVERT,
                    rule_id=issue.rule,
                    severity=L.RULES[issue.rule],
                    message=message,
                    line=issue.line,
                    xpath=issue.xpath,
                )
            )
        return out


def _value_items(prefix: str, value: object) -> Iterator[str]:
    """The item paths of one typed field's value."""
    if value is None or value in ((), ""):
        return
    if isinstance(value, tuple):
        for number, entry in enumerate(value):
            yield from _value_items(f"{prefix}[{number}]", entry)
        return
    if isinstance(value, Interval | TimeSpan):
        ends = ("top", "base") if isinstance(value, Interval) else ("start", "end")
        yield from (f"{prefix}.{end}" for end in ends if getattr(value, end))
        return
    if isinstance(value, ResultTable):
        yield from _table_items(prefix, value)
        return
    yield prefix


def _table_items(prefix: str, table: ResultTable) -> Iterator[str]:
    """Each cell of a result table (its index too), and its place."""
    for column in (*table.domain.index, *table.columns):
        name = column.key or column.name
        for row, cell in enumerate(column.values):
            if cell is not None:
                yield f"{prefix}.{name}[{row}]"
    if table.location is not None:
        yield f"{prefix}.location"
    if table.extent is not None:
        yield from _value_items(f"{prefix}.extent", table.extent)


def _helper(node: Node, qname: str) -> bool:
    """True for an ``extra`` element that is structure, or a typed field read twice.

    A feature's linear referencing system and a sampling activity's ``sampleProduced``
    are what the AGS4 keys carry; a project's ``locality`` is also its typed field.
    """
    if qname in _HELPER_EXTRA:
        return True
    return qname == "locality" and isinstance(node, Project) and node.locality is not None


def _items(node: Node) -> Iterator[str]:
    """Every item of ``node`` (not of the nodes it holds: they are nodes of their own)."""
    derived = _DERIVED.get(type(node), frozenset())
    for number, text in enumerate(node.names):
        if text:
            yield f"names[{number}]"
    for spec in fields(node):
        name = spec.name
        if name in _NOT_ITEMS or name in derived or name == "names":
            continue
        value = getattr(node, name)
        if name == "params":
            yield from (f"params.{param}" for param in value)
        elif name == "extra":
            yield from (
                f"extra[{n}]" for n, extra in enumerate(value) if not _helper(node, extra.qname)
            )
        elif name == "procedure" and value is not None:
            yield from (f"procedure.params.{param}" for param in value.params)
            yield from (f"procedure.extra[{n}]" for n in range(len(value.extra)))
        else:
            yield from _value_items(name, value)


# --- the AGS4 file being built -------------------------------------------------------


@dataclass(slots=True)
class _Table:
    """One AGS4 group under construction: its rows as dicts, and each heading's unit."""

    rule: GroupRule | None
    name: str
    rows: list[dict[str, str]] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)
    #: Headings written even when every value is empty (a REQUIRED heading left empty).
    forced: set[str] = field(default_factory=set)

    def heading_order(self, extra: tuple[str, ...] = ()) -> list[str]:
        listed = list(self.rule.headings) if self.rule is not None else []
        used = {h for row in self.rows for h, v in row.items() if v != ""} | self.forced
        keys = list(self.rule.keys) if self.rule is not None else []
        order = [h for h in listed if h in used or h in keys]
        order += [h for h in extra if h not in order and h in used]
        return order


@dataclass(slots=True)
class Exported:
    """What :func:`map_model` made: the file, its findings, and the cells the guard changed."""

    file: File
    ledger: ModelLedger
    findings: list[Finding]
    guarded: int = 0


def _type_of(heading: str, values: list[str], kinds: Mapping[str, str]) -> str:
    """The TYPE the values of ``heading`` have."""
    present = [v for v in values if v != ""]
    if heading in _ID_HEADINGS:
        return "ID"
    if heading in _PA_HEADINGS:
        return "PA"
    kind = kinds.get(heading, "X")
    if kind == "DT":
        dated = all(_DATE.fullmatch(v) for v in present)
        timed = all(_DATE_TIME.fullmatch(v) for v in present)
        return "DT" if dated or timed else "X"
    if kind == "number" and present:
        return _number_type(present)
    return "X"


def _number_type(values: list[str]) -> str:
    """``nDP`` when every number has ``n`` decimal places, else ``U`` (or ``X`` for text)."""
    matches = [_DECIMALS.fullmatch(value) for value in values]
    if any(match is None for match in matches):
        return "U" if all(_U_NUMBER.fullmatch(v) for v in values) else "X"
    places = {len(match.group(1) or "") for match in matches if match is not None}
    return f"{places.pop()}DP" if len(places) == 1 else "U"


def _date_unit(values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if all(_DATE.fullmatch(v) for v in present):
        return "yyyy-mm-dd"
    return "yyyy-mm-ddThh:mm:ss"


class _Exporter:
    """One conversion: the table, the ledger, and the AGS4 groups built so far."""

    def __init__(
        self, model: Model, mapping: MappingTable, ags_version: str, formula_guard: str | None
    ) -> None:
        self.model = model
        self.mapping = mapping
        self.ags_version = ags_version
        self.guard = formula_guard
        self.ledger = ModelLedger(model)
        self.tables: dict[str, _Table] = {}
        self.kinds: dict[str, str] = {}
        self.abbr: dict[tuple[str, str], str] = {}
        self.loca_id: dict[str, str] = {}
        self.sample_keys: dict[str, dict[str, str]] = {}
        self.guarded = 0
        units: dict[str, str] = {}
        for ags, diggs in mapping.units.items():
            units.setdefault(diggs, ags)
        self.units = units

    # --- plumbing -----------------------------------------------------------------------

    def table(self, name: str) -> _Table:
        found = self.tables.get(name)
        if found is None:
            found = _Table(rule=self.mapping.groups.get(name), name=name)
            self.tables[name] = found
        return found

    def unit(self, uom: str | None) -> str | None:
        """The AGS4 unit of a DIGGS uom; "" for none; None when the unit table has none."""
        if uom is None or uom == "":
            return ""
        return self.units.get(uom)

    def put(
        self,
        row: dict[str, str],
        table: _Table,
        heading: str,
        text: str,
        *,
        kind: str = "X",
        unit: str = "",
    ) -> bool:
        """Write ``text`` under ``heading``; False (nothing written) when its unit clashes."""
        known = table.units.get(heading)
        if known is not None and known != unit:
            return False
        table.units[heading] = unit
        self.kinds.setdefault(heading, kind)
        row[heading] = text
        return True

    def put_item(  # noqa: PLR0917  # one value's place, passed down
        self,
        row: dict[str, str],
        table: _Table,
        heading: str,
        node: Node,
        path: str,
        value: object,
    ) -> None:
        """Write one model value (text, code, quantity or date) and account for it."""
        if value is None:
            return
        if isinstance(value, Quantity):
            unit = self.unit(value.uom)
            if unit is None:
                self.ledger.lose(
                    L.UNMAPPED_UNIT,
                    node,
                    path,
                    "Values of {field} of {element} are in a unit with no entry in the unit"
                    " table; not converted ({values}).",
                )
                return
            if not self.put(row, table, heading, value.text, kind="number", unit=unit):
                self._clash(node, path)
                return
        elif isinstance(value, Code | str) and _LINE_BREAK.search(
            value.value if isinstance(value, Code) else value
        ):
            self.ledger.lose(
                L.UNMAPPED_ELEMENT,
                node,
                path,
                "Values of {field} of {element} hold a line break, which an AGS4 value cannot"
                " (Rule 6); not converted ({values}).",
            )
            return
        elif isinstance(value, Code):
            self.put(row, table, heading, value.value)
        elif isinstance(value, float):
            self.put(row, table, heading, written_number(value), kind="number")
        else:
            self.put(row, table, heading, str(value))
        self.ledger.use(node, path)

    def _clash(self, node: Node, path: str) -> None:
        self.ledger.lose(
            L.UNMAPPED_UNIT,
            node,
            path,
            "Values of {field} of {element} are in another unit than the rest of their AGS4"
            " heading, which has one unit; not converted ({values}).",
        )

    def headings(self, rule: GroupRule, to: str, target: str | None = None) -> list[HeadingRule]:
        return [
            h
            for h in rule.headings.values()
            if h.to == to
            and h.direction in ("both", "to-ags4")
            and (target is None or h.target == target)
        ]

    def first(self, rule: GroupRule, target: str) -> str | None:
        found = self.headings(rule, "field", target)
        return found[0].name if found else None

    def text_fields(self, row: dict[str, str], table: _Table, node: Node) -> None:
        """Description and the first remark, where the group has a heading for them."""
        rule = table.rule
        if rule is None:
            return
        heading = self.first(rule, "description")
        if heading is not None and node.description:
            self.put_item(row, table, heading, node, "description", node.description)
        heading = self.first(rule, "remark")
        if heading is not None and node.remarks:
            self.put_item(row, table, heading, node, "remarks[0]", node.remarks[0])

    def params(  # noqa: PLR0917  # one record's place, passed down
        self,
        row: dict[str, str],
        table: _Table,
        element: str,
        node: Node,
        params: Mapping[str, Param],
        prefix: str = "params",
    ) -> None:
        """The ``param`` headings of the group whose target element is ``element``."""
        if table.rule is None:
            return
        for heading in self.headings(table.rule, "param"):
            target_element, _, child = str(heading.target).partition("/")
            if target_element != element or child not in params:
                continue
            value = params[child]
            if heading.name in _PA_HEADINGS or heading.name == "SAMP_TYPE":
                continue
            if isinstance(value, Code) and child == "internalIdentifier":
                value = value.value
            self.put_item(row, table, heading.name, node, f"{prefix}.{child}", value)

    def position_depth(self, position: Position | None) -> str | None:
        """The depth a one-dimensional position along a feature gives, as written text."""
        if position is None or position.dim != 1 or not position.coordinates:
            return None
        return written_number(position.coordinates[0])

    # --- the document and the project -------------------------------------------------------

    def transmission(self) -> None:
        table = self.table("TRAN")
        row: dict[str, str] = {}
        document = self.model.document
        date = document.creation_date if isinstance(document, DocumentInfo) else None
        if date:
            self.put(row, table, "TRAN_DATE", date, kind="DT")
            table.units["TRAN_DATE"] = _date_unit([date])
            self.ledger.use(document, "creation_date")  # type: ignore[arg-type]
        else:
            self.put(row, table, "TRAN_DATE", "", kind="DT")
            table.forced.add("TRAN_DATE")
            self.ledger.note(
                L.ASSUMED,
                "DocumentInformation",
                "The document gives no known creation date, so TRAN_DATE, a REQUIRED AGS4"
                " heading, was left empty.",
            )
        if isinstance(document, DocumentInfo):
            self.text_fields(row, table, document)
        self.put(row, table, "TRAN_AGS", self.ags_version)
        self.put(
            row,
            table,
            "TRAN_REM",
            "Converted from DIGGS by pyDIGGS, conversion mapping table"
            f" {self.mapping.mapping_version}",
        )
        table.rows.append(row)

    def projects(self) -> None:
        table = self.table("PROJ")
        rule = table.rule
        assert rule is not None  # noqa: S101 - PROJ is a listed group
        if not self.model.projects:
            table.rows.append({"PROJ_ID": PROJECT_DEFAULT})
            self.kinds["PROJ_ID"] = "X"
            self.ledger.note(
                L.ASSUMED,
                "Project",
                "The document has no project, and AGS4 requires one PROJ row; a project named"
                f" {PROJECT_DEFAULT!r} was written.",
            )
            return
        project = self.model.projects[0]
        row: dict[str, str] = {}
        names = list(project.names)
        if names:
            self.put_item(row, table, "PROJ_ID", project, "names[0]", names[0])
        for number, heading in enumerate(self.headings(rule, "field", "name"), start=1):
            if number < len(names):
                self.put_item(row, table, heading.name, project, f"names[{number}]", names[number])
        locality = self.first(rule, "locality")
        if locality is not None and project.locality:
            self.put_item(row, table, locality, project, "locality", project.locality)
        self.text_fields(row, table, project)
        table.rows.append(row)
        for other in self.model.projects[1:]:
            self.ledger.lose(
                L.UNMAPPED_ELEMENT,
                other,
                None,
                "Projects after the first: AGS4 has one PROJ row (Rule 13); not converted"
                " ({values}).",
            )

    # --- locations and what they hold ----------------------------------------------------

    def features(self) -> None:
        table = self.table("LOCA")
        taken: set[str] = set()
        for feature in self.model.features:
            row: dict[str, str] = {}
            loca_id = feature.names[0] if feature.names else (feature.gml_id or feature.key)
            if loca_id in taken:
                self.ledger.lose(
                    L.UNMAPPED_ELEMENT,
                    feature,
                    None,
                    "Locations whose name another location already has: an AGS4 LOCA_ID is"
                    " unique; not converted ({values}).",
                )
                continue
            taken.add(loca_id)
            self.loca_id[feature.key] = loca_id
            self.put(row, table, "LOCA_ID", loca_id)
            if feature.names:
                self.ledger.use(feature, "names[0]")
            self.feature_type(row, table, feature)
            self.location(row, table, feature)
            if feature.total_depth is not None:
                self.put_item(row, table, "LOCA_FDEP", feature, "total_depth", feature.total_depth)
            if feature.when_constructed is not None:
                if feature.when_constructed.start:
                    self.put(row, table, "LOCA_STAR", feature.when_constructed.start, kind="DT")
                    self.ledger.use(feature, "when_constructed.start")
                if feature.when_constructed.end:
                    self.put(row, table, "LOCA_ENDD", feature.when_constructed.end, kind="DT")
                    self.ledger.use(feature, "when_constructed.end")
            self.text_fields(row, table, feature)
            table.rows.append(row)
            self.feature_components(feature)

    def feature_type(self, row: dict[str, str], table: _Table, feature: Feature) -> None:
        kind = feature.params.get("boreholeType")
        for (heading, ags), rule in self.mapping.codes.items():
            if heading != "LOCA_TYPE" or rule.element != feature.element:
                continue
            listed = rule.code is not None and isinstance(kind, Code) and rule.code == kind.value
            if listed and kind.code_space == self.mapping.code_space(str(rule.code_list)):  # type: ignore[union-attr]
                self.put(row, table, "LOCA_TYPE", ags)
                self.abbr[("LOCA_TYPE", ags)] = f"{rule.element}, boreholeType {rule.code}"
                self.ledger.use(feature, "params.boreholeType")
                return
            if kind is None and rule.exact:
                self.put(row, table, "LOCA_TYPE", ags)
                self.abbr[("LOCA_TYPE", ags)] = str(rule.element)
                return
        if kind is not None:
            self.ledger.lose(
                L.UNMAPPED_CODE,
                feature,
                "params.boreholeType",
                "Values of {field} of {element} have no AGS4 code in the code table; not"
                " converted ({values}).",
            )

    def location(self, row: dict[str, str], table: _Table, feature: Feature) -> None:
        position = feature.location
        if position is None:
            return
        if position.crs is not None and position.crs == feature.lrs:
            if all(c == 0 for c in position.coordinates):
                self.ledger.use(feature, "location")  # the top of the hole, along itself
                self.centerline(feature)
            return
        srs = position.crs
        geographic = {srs for (h, _), srs in self.mapping.crs.items() if h == "LOCA_LLZ"}
        if srs in geographic and position.dim == 2:  # noqa: PLR2004
            datum = next(
                a for (h, a), s in self.mapping.crs.items() if h == "LOCA_LLZ" and s == srs
            )
            lat, lon = position.coordinates
            self.put(row, table, "LOCA_LAT", written_number(lat), kind="number")
            self.put(row, table, "LOCA_LON", written_number(lon), kind="number")
            self.put(row, table, "LOCA_LLZ", datum)
            self.epsg_heading(row, table, "LOCA_LLSI", srs)
            self.ledger.use(feature, "location")
            self.centerline(feature)
            return
        vertical: str | None = None
        horizontal = self.mapping.crs_horizontal.get(srs or "")
        if horizontal is not None and position.dim == 3:  # noqa: PLR2004
            vertical = next(
                a for (h, a), s in self.mapping.crs.items() if h == "LOCA_NATD" and s == srs
            )
            srs = horizontal
        reference = self.grid_reference(srs)
        ref = self.model.crs.get(position.crs or "")
        horizontal_type = None if ref is None or ref.horizontal is None else ref.horizontal.type
        if (
            reference is None
            or position.dim not in (2, 3)
            or (horizontal_type is not None and horizontal_type != "projected")
        ):
            self.ledger.lose(
                L.UNMAPPED_ELEMENT,
                feature,
                "location",
                "Locations of {element} in a CRS AGS4 grid coordinates cannot name; not converted"
                " ({values}).",
            )
            return
        axis = self.mapping.crs_units.get(srs or "") or (None if ref is None else ref.unit)
        unit = self.unit(axis) if axis is not None else ""
        if unit is None:
            unit = ""
        headings = ("LOCA_NATE", "LOCA_NATN", "LOCA_GL")[: position.dim]
        if any(table.units.get(h, unit) != unit for h in headings):
            # an AGS4 heading has one unit: coordinates in another one are not written
            self._clash(feature, "location")
            return
        for heading, value in zip(headings, position.coordinates, strict=True):
            self.put(row, table, heading, written_number(value), kind="number", unit=unit)
        if reference:
            self.put(row, table, "LOCA_GREF", reference)
        if vertical is not None:
            self.put(row, table, "LOCA_NATD", vertical)
        self.epsg_heading(row, table, "LOCA_NSRI", srs)
        self.ledger.use(feature, "location")
        self.centerline(feature)

    def epsg_heading(
        self, row: dict[str, str], table: _Table, heading: str, srs: str | None
    ) -> None:
        """The EPSG code of ``srs`` in an AGS4 4.2 heading (LOCA_NSRI, LOCA_LLSI), for 4.2 only."""
        match = None if srs is None else _EPSG_URI.fullmatch(srs)
        if self.ags_version == "4.2" and match is not None:
            self.put(row, table, heading, str(int(match.group(1))), kind="number")

    def grid_reference(self, srs: str | None) -> str | None:
        """The LOCA_GREF text of a grid CRS ("" for none), or None when there is none."""
        if srs is None:
            return ""
        for (heading, ags), listed in self.mapping.crs.items():
            if heading == "LOCA_GREF" and listed == srs:
                return ags
        match = re.fullmatch(r"http://www\.opengis\.net/def/crs/EPSG/0/(\d{1,6})", srs)
        if match is not None:
            return f"EPSG:{match.group(1)}"
        return None

    def centerline(self, feature: Feature) -> None:
        """A vertical centre line is implied by the location; any other is reported."""
        line = feature.centerline
        location = feature.location
        if not line or location is None:
            return
        same = all(
            p.crs == location.crs and p.coordinates[:2] == location.coordinates[:2] for p in line
        )
        if same or (len(line) == 1 and line[0] == location):
            self.ledger.use(feature, "centerline")

    def feature_components(self, feature: Feature) -> None:
        for component in (*feature.construction, *feature.water_strikes):
            rule = self.component_rule(component)
            if rule is None:
                continue
            if rule.holder is not None:
                for layer in component.components:
                    if layer.element == rule.element and layer.role == rule.role:
                        self.component_row(rule, feature, layer)
            elif rule.role == _STRIKE:
                self.strike(rule, feature, component)
            else:
                self.component_row(rule, feature, component)
        rules = [r for r in self.mapping.groups.values() if r.interval_top is not None]
        for rule in rules:
            self.check_tops(rule, feature)

    def component_rule(self, component: Component) -> GroupRule | None:
        for rule in self.mapping.groups.values():
            if rule.pattern != "feature-component" or rule.parent != "LOCA":
                continue
            if rule.holder is not None and (component.element, component.role) == (
                rule.holder,
                rule.holder_role,
            ):
                return rule
            if rule.holder is None and (component.element, component.role) == (
                rule.element,
                rule.role,
            ):
                return rule
        return None

    def component_row(self, rule: GroupRule, feature: Feature, component: Component) -> None:
        table = self.table(rule.name)
        row = {"LOCA_ID": self.loca_id[feature.key]}
        self.kinds["LOCA_ID"] = "ID"
        interval = component.interval
        base = self.first(rule, "interval.base")
        top = self.first(rule, "interval.top")
        if interval is not None:
            if base is not None and interval.base is not None:
                self.put_item(row, table, base, component, "interval.base", interval.base)
            if top is not None and interval.top is not None:
                self.put_item(row, table, top, component, "interval.top", interval.top)
        self.params(row, table, str(rule.element), component, component.params)
        self.text_fields(row, table, component)
        if any(h in row for h in rule.keys if h != "LOCA_ID"):
            table.rows.append(row)
            if interval is not None and top is None and rule.interval_top is not None:
                self.pending_tops.setdefault((rule.name, feature.key), []).append(component)
        else:
            for item in list(self.ledger.items_of(component.key)):
                self.ledger.used.discard(item)
            self.ledger.lose(
                L.UNMAPPED_ELEMENT,
                component,
                None,
                "Records of {element} without the depth AGS4 keys them by; not converted"
                " ({values}).",
            )

    pending_tops: dict[tuple[str, str], list[Component]]

    def check_tops(self, rule: GroupRule, feature: Feature) -> None:
        """A section's top is written only as the base of the one before (or the surface)."""
        components = self.pending_tops.pop((rule.name, feature.key), [])

        def base(c: Component) -> float:
            value = None if c.interval is None or c.interval.base is None else c.interval.base.value
            return float("inf") if value is None else value

        previous: Quantity | None = None
        for component in sorted(components, key=base):
            interval = component.interval
            top = None if interval is None else interval.top
            expected = 0.0 if rule.interval_top == "surface" or previous is None else previous.value
            if top is not None and top.value == expected:
                self.ledger.use(component, "interval.top")
            elif top is not None:
                self.ledger.lose(
                    L.UNMAPPED_ELEMENT,
                    component,
                    "interval.top",
                    "Tops of {element} that are not the base of the section above (or the"
                    " surface): AGS4 gives only the base; not converted ({values}).",
                )
            previous = None if interval is None else interval.base

    def strike(self, rule: GroupRule, feature: Feature, component: Component) -> None:
        table = self.table(rule.name)
        readings = [c for c in component.components if c.element == "WaterStrikeReading"]
        reading = readings[0] if readings else None
        depth = None if reading is None else self.position_depth(reading.location)
        if reading is None or depth is None:
            self.ledger.lose(
                L.UNMAPPED_ELEMENT,
                component,
                None,
                "Water strikes with no depth of the strike, which AGS4 keys them by; not"
                " converted ({values}).",
            )
            return
        row = {"LOCA_ID": self.loca_id[feature.key]}
        heading = self.first(rule, "strike.location")
        if heading is None:  # pragma: no cover - the table lists it
            return
        self.put(row, table, heading, depth, kind="number", unit="m")
        self.ledger.use(reading, "location")
        when = reading.params.get("dateTime")
        heading = self.first(rule, "strike.dateTime")
        if heading is not None and isinstance(when, str):
            self.put(row, table, heading, when, kind="DT")
            self.ledger.use(reading, "params.dateTime")
        seal = self.position_depth(component.location)
        heading = self.first(rule, "location")
        if heading is not None and seal is not None:
            self.put(row, table, heading, seal, kind="number", unit="m")
            self.ledger.use(component, "location")
        self.places(row, table, rule, component)
        self.text_fields(row, table, component)
        table.rows.append(row)
        data = self.mapping.groups.get(str(rule.data))
        if data is None:  # pragma: no cover - the table lists it
            return
        later = self.table(data.name)
        for linked in reading.components:
            if linked.element != data.element:
                continue
            post = self.position_depth(linked.location)
            minutes = linked.params.get("elapsedTime")
            if post is None or not isinstance(minutes, Quantity):
                self.ledger.lose(
                    L.UNMAPPED_ELEMENT,
                    linked,
                    None,
                    "Water strike readings with no elapsed time or no depth to water, which AGS4"
                    " needs; not converted ({values}).",
                )
                continue
            out = {"LOCA_ID": row["LOCA_ID"], "WSTG_DPTH": depth}
            later.units["WSTG_DPTH"] = "m"
            self.params(out, later, str(data.element), linked, linked.params)
            heading = self.first(data, "location")
            if heading is not None:
                self.put(out, later, heading, post, kind="number", unit="m")
                self.ledger.use(linked, "location")
            self.text_fields(out, later, linked)
            later.rows.append(out)

    def places(
        self, row: dict[str, str], table: _Table, rule: GroupRule, component: Component
    ) -> None:
        """Further point locations of a record that the table names (``bottomCasing``)."""
        for number, extra in enumerate(component.extra):
            heading = self.first(rule, extra.qname)
            if heading is None or extra.ns != _DIGGS_NS or len(extra.children) != 1:
                continue
            point = extra.children[0]
            pos = [c for c in point.children if c.qname == "gml:pos"]
            depth = None if len(pos) != 1 or pos[0].text is None else _depth(pos[0].text)
            lrs = point.attrs.get("srsName")
            if point.qname != "PointLocation" or depth is None or lrs != self.lrs_of(component):
                continue
            self.put(row, table, heading, depth, kind="number", unit="m")
            self.ledger.use(component, f"extra[{number}]")

    def lrs_of(self, component: Component) -> str | None:
        """The linear referencing system the record's own point location is in."""
        for feature in self.model.features:
            if any(c is component for c in feature.water_strikes):
                return feature.lrs
        return None

    # --- lithology ---------------------------------------------------------------------

    def geology(self) -> None:
        table = self.table("GEOL")
        rule = table.rule
        assert rule is not None  # noqa: S101 - GEOL is a listed group
        for observation in self.model.observations:
            if observation.element != "LithologySystem" or observation.feature not in self.loca_id:
                continue
            kind = observation.params.get("lithologyClassificationType")
            if kind == CLASSIFICATION_DEFAULT:
                self.ledger.use(observation, "params.lithologyClassificationType")
            for layer in observation.layers:
                self.layer(table, rule, observation, layer)

    def layer(
        self, table: _Table, rule: GroupRule, observation: Observation, layer: LithologyLayer
    ) -> None:
        interval = layer.interval
        if interval is None or interval.top is None or interval.base is None:
            return
        row = {"LOCA_ID": self.loca_id[str(observation.feature)]}
        self.put_item(row, table, "GEOL_TOP", layer, "interval.top", interval.top)
        self.put_item(row, table, "GEOL_BASE", layer, "interval.base", interval.base)
        primary = next((c for c in layer.components if c.role == "primaryLithology"), None)
        if primary is not None:
            self.params(row, table, "Lithology", primary, primary.params)
        if layer.geologic_unit:
            heading = self.first(rule, "geologic_unit")
            if heading is not None:
                self.put_item(row, table, heading, layer, "geologic_unit", layer.geologic_unit)
        self.text_fields(row, table, layer)
        table.rows.append(row)

    # --- samples -----------------------------------------------------------------------

    def samples(self) -> None:
        table = self.table("SAMP")
        rule = table.rule
        assert rule is not None  # noqa: S101 - SAMP is a listed group
        for sample in self.model.samples:
            activity = self.model.get(sample.sampling_activity or "")
            if not isinstance(activity, SamplingActivity) or activity.feature not in self.loca_id:
                continue
            interval = activity.interval
            if interval is None or interval.top is None:
                continue
            row = {"LOCA_ID": self.loca_id[activity.feature or ""]}
            self.put_item(row, table, "SAMP_TOP", activity, "interval.top", interval.top)
            if interval.base is not None:
                self.put_item(row, table, "SAMP_BASE", activity, "interval.base", interval.base)
            reference = sample.params.get("internalIdentifier")
            own = not isinstance(reference, Code) or reference.code_space in (
                None,
                f"{AGS4_CODE_SPACE}SAMP_REF",
            )
            if reference is not None and own:
                text = reference.value if isinstance(reference, Code) else str(reference)
                self.put_item(row, table, "SAMP_REF", sample, "params.internalIdentifier", text)
            self.sample_type(row, table, sample)
            label = " ".join(
                v
                for v in (
                    row["LOCA_ID"],
                    row.get("SAMP_TOP", ""),
                    row.get("SAMP_REF", ""),
                    row.get("SAMP_TYPE", ""),
                )
                if v
            )
            if sample.names and sample.names[0] != label:
                self.put_item(row, table, "SAMP_ID", sample, "names[0]", sample.names[0])
            elif sample.names:
                self.ledger.use(sample, "names[0]")
            if activity.date is not None and activity.date.start:
                self.put(row, table, "SAMP_DTIM", activity.date.start, kind="DT")
                self.ledger.use(activity, "date")
            if activity.params.get("activityType") == "collect":
                self.ledger.use(activity, "params.activityType")
            self.params(row, table, "SamplingActivity", activity, activity.params)
            self.text_fields(row, table, sample)
            table.rows.append(row)
            self.sample_keys[sample.key] = {
                h: row.get(h, "")
                for h in ("LOCA_ID", "SAMP_TOP", "SAMP_REF", "SAMP_TYPE", "SAMP_ID")
            }

    def sample_type(self, row: dict[str, str], table: _Table, sample: Sample) -> None:
        kind = sample.params.get("type")
        if kind is None:
            return
        if isinstance(kind, str):
            self.put_item(row, table, "SAMP_TYPE", sample, "params.type", kind)
            self.abbr.setdefault(("SAMP_TYPE", kind), "sample type")
            return
        for (heading, ags), rule in self.mapping.codes.items():
            if (
                heading == "SAMP_TYPE"
                and isinstance(kind, Code)
                and rule.code == kind.value
                and kind.code_space == self.mapping.code_space(str(rule.code_list))
            ):
                self.put_item(row, table, "SAMP_TYPE", sample, "params.type", ags)
                self.abbr[("SAMP_TYPE", ags)] = f"sampleType {rule.code}"
                return
        self.ledger.lose(
            L.UNMAPPED_CODE,
            sample,
            "params.type",
            "Values of {field} of {element} have no AGS4 code in the code table; not converted"
            " ({values}).",
        )

    # --- tests -------------------------------------------------------------------------

    def test_rule(self, measurement: Measurement) -> GroupRule | None:
        procedure = None if measurement.procedure is None else measurement.procedure.type
        data = {r.data for r in self.mapping.groups.values() if r.data}
        for rule in self.mapping.groups.values():
            if (
                rule.procedure == procedure
                and rule.pattern in ("test-per-row", "test-table")
                and rule.name not in data
                and rule.written_for(self.ags_version)
            ):
                return rule
        return None

    def tests(self) -> None:
        counters: dict[tuple[str, str], int] = {}
        for measurement in self.model.measurements:
            rule = self.test_rule(measurement)
            if rule is None or measurement.feature not in self.loca_id or not measurement.results:
                continue
            row = self.test_keys(rule, measurement)
            if row is None:
                continue
            table = self.table(rule.name)
            procedure = measurement.procedure
            if procedure is not None:
                for name, value in rule.procedure_params.items():
                    if procedure.params.get(name) == value:
                        self.ledger.use(measurement, f"procedure.params.{name}")
                self.params(
                    row, table, procedure.type, measurement, procedure.params, "procedure.params"
                )
            self.text_fields(row, table, measurement)
            data = None if rule.data is None else self.mapping.groups.get(rule.data)
            if data is not None and any(h.to == "column" for h in data.headings.values()):
                tesn = rule.keys[1]
                if measurement.names:
                    self.put_item(row, table, tesn, measurement, "names[0]", measurement.names[0])
                else:
                    count = counters.get((rule.name, row["LOCA_ID"]), 0) + 1
                    counters[(rule.name, row["LOCA_ID"])] = count
                    self.put(row, table, tesn, str(count))
                    self.ledger.note(
                        L.ASSUMED,
                        measurement.element,
                        f"Tests with no name were numbered 1, 2, 3 ... in {rule.name}"
                        f" ({tesn}), which AGS4 keys them by ({{times}}).",
                    )
                self.series(rule, data, row, measurement)
            else:
                named = self.first(rule, "name")
                if named is not None and measurement.names:
                    self.put_item(row, table, named, measurement, "names[0]", measurement.names[0])
                self.single(rule, table, row, measurement)
                if data is not None and procedure is not None:
                    self.gradings(rule, data, row, measurement)
            table.rows.append(row)

    def test_keys(self, rule: GroupRule, measurement: Measurement) -> dict[str, str] | None:
        row = {"LOCA_ID": self.loca_id[str(measurement.feature)]}
        if rule.parent != "SAMP":
            return row
        keys = self.sample_keys.get(measurement.samples[0]) if measurement.samples else None
        if keys is None:
            self.ledger.lose(
                L.UNMAPPED_ELEMENT,
                measurement,
                None,
                "Tests on a sample that has no SAMP row (or on no sample): AGS4 keys a"
                " laboratory test by its sample; not converted ({values}).",
            )
            return None
        row.update(keys)
        table = self.table(rule.name)
        samples = self.table("SAMP")
        for heading in keys:
            if heading in samples.units:
                table.units[heading] = samples.units[heading]
            self.kinds.setdefault(heading, "X")
        return row

    def single(
        self, rule: GroupRule, table: _Table, row: dict[str, str], measurement: Measurement
    ) -> None:
        """One result table of one row: its place and its columns."""
        result = measurement.results[0]
        place = self.first(rule, "location")
        depth = self.position_depth(result.location)
        if place is not None and depth is not None:
            self.put(row, table, place, depth, kind="number", unit="m")
            self.ledger.use(measurement, "results[0].location")
        elif place is not None and result.extent is not None and result.extent.top is not None:
            self.put_item(
                row, table, place, measurement, "results[0].extent.top", result.extent.top
            )
            if result.extent.base is not None and result.extent.base != result.extent.top:
                self.ledger.used.discard((measurement.key, "results[0].extent.top"))
        if result.row_count != 1:
            return
        for column in result.columns:
            self.column(row, table, rule, measurement, column, 0, "results[0]")

    def column(  # noqa: PLR0917  # one cell's place, passed down
        self,
        row: dict[str, str],
        table: _Table,
        rule: GroupRule,
        node: Node,
        column: Column,
        index: int,
        prefix: str,
    ) -> None:
        value = column.values[index]
        path = f"{prefix}.{column.key or column.name}[{index}]"
        if value is None:
            return
        if column.key == "non_plastic" and value is True and rule.name == "LLPL":
            if row.get(_NP_HEADING, "") == "":
                self.put(row, table, _NP_HEADING, "NP")
            self.ledger.use(node, path)
            self.ledger.note(
                L.ASSUMED,
                "LLPL",
                "Non-plastic soil was written as NP in LLPL_PL, the one limit AGS4 lets hold"
                " text; LLPL_LL and LLPL_PI were left empty ({times}).",
            )
            return
        heading = next(
            (h.name for h in self.headings(rule, "column") if h.property == column.key), None
        )
        if heading is None or not isinstance(value, float | int) or isinstance(value, bool):
            return
        unit = self.unit(column.uom)
        if unit is None:
            self.ledger.lose(
                L.UNMAPPED_UNIT,
                node,
                path,
                "Values of {field} of {element} are in a unit with no entry in the unit table;"
                " not converted ({values}).",
            )
            return
        if not self.put(
            row, table, heading, written_number(float(value)), kind="number", unit=unit
        ):
            self._clash(node, path)
            return
        self.ledger.use(node, path)

    def series(
        self, rule: GroupRule, data: GroupRule, row: dict[str, str], measurement: Measurement
    ) -> None:
        """A cone test's readings: one data row per row of its result table."""
        result = measurement.results[0]
        index = result.domain.index
        if len(index) != 1 or index[0].name != "position":
            return
        table = self.table(data.name)
        depth_heading = self.first(data, "index.position")
        if depth_heading is None:  # pragma: no cover - the table lists it
            return
        unit = self.unit(index[0].uom) or ""
        numbered = "CPTT_REDN" in data.headings
        for number in range(result.row_count):
            out = {h: row.get(h, "") for h in rule.keys}
            position = index[0].values[number]
            if position is None:
                continue
            if numbered:
                self.put(out, table, "CPTT_REDN", str(number + 1), kind="number")
            self.put(
                out, table, depth_heading, written_number(float(position)), kind="number", unit=unit
            )
            self.ledger.use(measurement, f"results[0].position[{number}]")
            for column in result.columns:
                self.column(out, table, data, measurement, column, number, "results[0]")
            table.rows.append(out)
        if numbered and result.row_count:
            self.ledger.note(
                L.ASSUMED,
                measurement.element,
                "Cone readings were numbered 1, 2, 3 ... by depth in CPTT_REDN, which AGS4 4.2"
                " keys them by ({times}).",
            )

    def gradings(
        self, rule: GroupRule, data: GroupRule, row: dict[str, str], measurement: Measurement
    ) -> None:
        """A particle size test's readings: one GRAT row per sieve or hydrometer reading."""
        procedure = measurement.procedure
        if procedure is None:  # pragma: no cover - the caller checked
            return
        table = self.table(data.name)
        exact = {
            rule_.element: ags
            for (heading, ags), rule_ in self.mapping.codes.items()
            if heading == "GRAT_TYPE" and rule_.exact
        }
        for analysis in procedure.components:
            for reading in analysis.components:
                if reading.element not in ("Grading", "Sedimentation"):
                    continue
                out = {h: row.get(h, "") for h in rule.keys}
                size = "particleSize" if reading.element == "Grading" else "particleDiameter"
                params = dict(reading.params)
                if size in params:
                    params["particleSize"] = params.pop(size)
                for heading in self.headings(data, "param"):
                    child = str(heading.target).partition("/")[2]
                    source = size if child == "particleSize" else child
                    if child in params:
                        self.put_item(
                            out, table, heading.name, reading, f"params.{source}", params[child]
                        )
                if "GRAT_SIZE" not in out:
                    continue
                code = exact.get(reading.element)
                if code is not None:
                    self.put(out, table, "GRAT_TYPE", code)
                    self.abbr[("GRAT_TYPE", code)] = str(reading.element)
                self.text_fields(out, table, reading)
                table.rows.append(out)

    # --- the file ------------------------------------------------------------------------

    def run(self) -> None:
        self.pending_tops = {}
        self.transmission()
        self.projects()
        self.features()
        self.geology()
        self.samples()
        self.tests()
        for observation in self.model.observations:
            if observation.element != "LithologySystem":
                self.ledger.lose(
                    L.UNMAPPED_ELEMENT,
                    observation,
                    None,
                    "Observations of {element} have no AGS4 group in the mapping table; not"
                    " converted ({values}).",
                )
        self.ledger.finish()

    def guard_value(self, heading: str, value: str, keys: frozenset[str]) -> str:
        if self.guard != "prefix" or heading in keys or not formula_like(value):
            return value
        if self.kinds.get(heading, "X") != "X" or heading in _PA_HEADINGS | _ID_HEADINGS:
            return value
        self.guarded += 1
        return f"'{value}"

    def file(self) -> File:
        order = ["PROJ", "TRAN", "ABBR", "TYPE", "UNIT"]
        built: dict[str, Group] = {}
        types_used: set[str] = set()
        units_used: set[str] = set()
        for name, table in self.tables.items():
            if not table.rows:
                continue
            headings = table.heading_order(tuple(h for row in table.rows for h in row))
            keys = frozenset(table.rule.keys if table.rule is not None else ())
            rows = tuple(
                tuple(self.guard_value(h, row.get(h, ""), keys) for h in headings)
                for row in table.rows
            )
            types = tuple(
                _type_of(h, [r[i] for r in rows], self.kinds) for i, h in enumerate(headings)
            )
            units = []
            for i, heading in enumerate(headings):
                unit = table.units.get(heading, "")
                if types[i] == "DT":
                    unit = _date_unit([r[i] for r in rows])
                units.append(unit)
            types_used.update(types)
            units_used.update(u for u in units if u)
            built[name] = Group(
                name=name,
                headings=tuple(headings),
                units=tuple(units),
                types=tuple(types),
                rows=rows,
            )
        used_codes: dict[tuple[str, str], str] = {}
        for (heading, code), text in self.abbr.items():
            if not any(heading in g.headings and code in g.column(heading) for g in built.values()):
                continue
            used_codes[(heading, code)] = text
            for part in code.split("+") if "+" in code else ():
                used_codes.setdefault((heading, part), f"part of {code}")
        abbreviations = sorted((h, c, d) for (h, c), d in used_codes.items())
        if abbreviations:
            built["ABBR"] = Group(
                name="ABBR",
                headings=("ABBR_HDNG", "ABBR_CODE", "ABBR_DESC"),
                units=("", "", ""),
                types=("X", "X", "X"),
                rows=tuple(abbreviations),
            )
            types_used.add("X")
        types_used |= {"X"}
        built["TYPE"] = Group(
            name="TYPE",
            headings=("TYPE_TYPE", "TYPE_DESC"),
            units=("", ""),
            types=("X", "X"),
            rows=tuple((t, _type_description(t)) for t in sorted(types_used)),
        )
        built["UNIT"] = Group(
            name="UNIT",
            headings=("UNIT_UNIT", "UNIT_DESC"),
            units=("", ""),
            types=("X", "X"),
            rows=tuple((u, "") for u in sorted(units_used)),
        )
        groups = [built.pop(name) for name in order if name in built]
        groups.extend(built.values())
        return File(groups=groups)


def _depth(text: str) -> str | None:
    """A depth as the converter writes numbers, or None when ``text`` is not one."""
    value = parse_number(text.strip())
    return None if value is None else written_number(value)


def _type_description(data_type: str) -> str:
    found = _TYPE_TEXT.get(data_type)
    if found is not None:
        return found
    match = re.fullmatch(r"(\d+)DP", data_type)
    if match is not None:
        return f"Number with {match.group(1)} decimal places"
    return data_type


def map_model(
    model: Model,
    mapping: MappingTable,
    *,
    ags_version: str,
    formula_guard: str | None = None,
) -> Exported:
    """The AGS4 file of ``model`` by ``mapping``, with the model ledger and its findings."""
    exporter = _Exporter(model, mapping, ags_version, formula_guard)
    exporter.run()
    file = exporter.file()
    return Exported(
        file=file,
        ledger=exporter.ledger,
        findings=exporter.ledger.findings(),
        guarded=exporter.guarded,
    )
