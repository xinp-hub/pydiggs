"""Read DIGGS bytes into a :class:`~pydiggs.model.Model` (the read API).

The document is parsed with a pull parser, one top-level member at a time: when a
member's end tag arrives its subtree is mapped to JSON-shaped dicts, then cleared and
dropped, so the tree never holds more than one member. Keys, links and CRS references
are resolved after the pass, over Python indexes rather than XML, and the result is
built with :meth:`Model.from_dict`, the same decoder JSON goes through.

Reading is lenient and never says whether a file is valid: what it had to work around
becomes a note (a findings :class:`~pydiggs.Finding`). It raises
:class:`~pydiggs.errors.DocumentError` only when no model can be built at all.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from functools import cache, lru_cache
from importlib import resources
from itertools import chain, islice
from typing import Any, Final

from lxml import etree

from pydiggs import _geo_checks, _xml, crs
from pydiggs.detect import NAMESPACE_PROFILES
from pydiggs.errors import DocumentError
from pydiggs.findings import rules
from pydiggs.findings.keys import normalise_xpath
from pydiggs.findings.model import Finding, Severity, Step
from pydiggs.findings.run import MAX_NAMESPACE_CHARS, _Cancelled
from pydiggs.model._model import MODEL_SCHEMA_VERSION, Model, _encode
from pydiggs.model._values import CrsRef, ExtraNode
from pydiggs.units import UnitConverter

__all__ = ["read_document"]

_GML: Final = "http://www.opengis.net/gml/3.2"
_GML_ID: Final = f"{{{_GML}}}id"
_XLINK_HREF: Final = "{http://www.w3.org/1999/xlink}href"
#: Where the schema of a file is: the one attribute of the root element not compared.
_SCHEMA_LOCATION: Final = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"
_CHUNK: Final = 64 * 1024

#: Top-level member property -> model collection.
_CATEGORY: Final = {
    "documentInformation": "document",
    "project": "projects",
    "businessAssociate": "associates",
    "samplingFeature": "features",
    "samplingActivity": "sampling_activities",
    "sample": "samples",
    "observation": "observations",
    "measurement": "measurements",
}
#: Location element -> result-table domain shape.
_SHAPES: Final = {
    "PointLocation": "point",
    "MultiPointLocation": "point",
    "LinearExtent": "interval",
    "TimeInterval": "temporal",
    "TimeInstant": "temporal",
    "RectifiedGrid": "grid2d",
}
#: Feature properties whose objects become ``construction`` components.
_CONSTRUCTION: Final = frozenset(
    {
        "backfill",
        "casing",
        "chiseling",
        "constructionEvent",
        "constructionMethod",
        "drillAdvancement",
        "flush",
        "holeDiameter",
        "installationConstructionEvent",
        "installationEquipment",
        "maintenanceEvent",
        "opening",
        "wellAbandonment",
        "wellCasing",
    }
)
#: Geometry objects: a component's place, never a component of its own.
_GEOMETRY: Final = frozenset(
    {
        "LinearExtent",
        "MultiCurve",
        "MultiPointLocation",
        "PointLocation",
        "RectifiedGrid",
        "LineString",
        "Point",
    }
)
#: Time objects: a component's or activity's time.
_TIME: Final = frozenset({"TimeInstant", "TimeInterval", "TimePeriod"})
#: Point locations a component holds besides the one that places it (a water strike's
#: casing and hole depths at the strike): kept in ``extra``, never read as its location.
_OTHER_POINTS: Final = frozenset({"bottomCasing", "bottomHole"})
#: Time domains of result tables: read as their index, never kept twice.
_TIME_DOMAINS: Final = frozenset({"TimePositionList", "TimeIntervalList", "MultiTimeInstant"})
#: ``unit`` of a TimeIntervalList -> unit symbol.
_TIME_UNITS: Final = {"second": "s", "minute": "min", "hour": "h", "day": "d"}
#: Typed reference fields and the link relation each one follows.
_TYPED_REFS: Final = (
    ("feature", "samplingFeature"),
    ("sampling_activity", "samplingActivity"),
    ("specimen", "specimen"),
    ("parent", "samplingFeature"),
)
#: CRSs a file defines for itself, by element.
_LOCAL_CRS: Final = frozenset(
    {"LinearSpatialReferenceSystem", "LocalCartesianCRS", "EngineeringCRS"}
)
#: Header properties: read into the header, never components.
_HEADER: Final = frozenset({"name", "description", "remark"})
#: Attributes a simple value may carry and still be a ``params`` entry.
_PARAM_ATTRS: Final = frozenset({"uom", "codeSpace"})
#: Lithology classification codes, in the order DIGGS lists them.
_CLASSIFICATION: Final = ("classificationCode", "classificationSymbol", "legendCode")
_NUMBER_TYPES: Final = frozenset(
    {"integer", "int", "double", "decimal", "float", "number", "long", "short", "real"}
)
_BOOLEAN_TYPES: Final = frozenset({"boolean", "bool"})
_DATETIME_TYPES: Final = frozenset({"datetime", "date", "time", "gyear"})
_CAMEL: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
#: The work limits of one read. A model may hold ``LIMIT_BASE`` nodes, and as many
#: elements in ``extra``, plus one of each per ``LIMIT_BYTES_PER_ITEM`` bytes of input.
#: The largest real file (6.5 MB) has about 5,000 of each, over 1,000 bytes apiece; a
#: file made of tiny unknown elements has one per 4 bytes, and each costs a few hundred
#: bytes of JSON. The limits stop that file and scale with honest large ones.
LIMIT_BASE: Final = 100_000
LIMIT_BYTES_PER_ITEM: Final = 200
#: How many empty cells the result tables of one read may be given: a row shorter than
#: the declared properties is padded with a null per missing value, so a few bytes of
#: ``Property`` elements over a few bytes of one-value rows ask for columns times rows
#: cells (20,000 properties over 1,000,000 rows: 2x10^10 cells from a few MB). At most
#: the larger of ``PADDED_CELLS_BASE`` and ``PADDED_CELLS_PER_BYTE`` per byte of input.
#: Over the vendored official files, the conformance corpus and the conformance
#: challenge references the most is 1,683 (the 6.5 MB Inspector file) and the highest
#: ratio 0.00053 per byte (221 in a 420 kB CPT example): the bound is over 1,900 times
#: both. A null costs 8 bytes in memory and about 5 in JSON, about what a value written
#: in the file costs, so the padding grows no faster than the file.
PADDED_CELLS_BASE: Final = 4_000_000
PADDED_CELLS_PER_BYTE: Final = 1
#: Deepest nesting kept in ``extra`` (real files reach about 11).
MAX_EXTRA_DEPTH: Final = 64
#: How many ``extra`` elements pass between checks of the time budget and cancellation.
_CHECK_EVERY: Final = 1024
#: An identified element is given a place (see :meth:`_Reader._place`) only this few steps
#: below its record and with a path this short; real ones are two or three steps down.
MAX_PLACE_STEPS: Final = 16
MAX_PLACE_CHARS: Final = 120
_LIMIT_TEXT: Final = {
    "nodes": "the file has more objects than a file of its size may",
    "extra_elements": (
        "more elements have no typed field in the model than a file of its size may have"
    ),
    "extra_depth": f"elements with no typed field are nested more than {MAX_EXTRA_DEPTH} deep",
    "padded_cells": (
        "result rows leave more cells empty, under the properties declared, than a file"
        " of its size may"
    ),
}


class _Enough(Exception):  # noqa: N818 - control flow inside this module only
    """The content the model lacks has reached the work limit of the read."""


class _LimitError(Exception):
    """A work limit stopped the read inside a member; ``name`` goes into ``limits_hit``."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


#: Duplicated gml:ids noted one by one before the rest are summarised.
_MAX_ID_NOTES: Final = 100
_FLOAT: Final = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


#: The keys that mark an interval and a result table for :meth:`_Reader._fill_units`.
_INTERVAL_KEYS: Final = frozenset({"top", "base", "lrs"})
_TABLE_KEYS: Final = frozenset({"shape", "index", "crs"})


@lru_cache(maxsize=4096)
def _local(tag: object) -> str:
    """The local name of a tag (cached, bounded: tags repeat, but come from the file)."""
    return str(tag).rpartition("}")[2]


def _qname(el: etree._Element) -> str:
    local = _local(el.tag)
    return f"{el.prefix}:{local}" if el.prefix else local


def _elements(el: etree._Element) -> Iterator[etree._Element]:
    return (c for c in el if isinstance(c.tag, str))


def _child(el: etree._Element, local: str) -> etree._Element | None:
    return next((c for c in _elements(el) if _local(c.tag) == local), None)


def _text(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    text = (el.text or "").strip()
    return text or None


def _number(text: str) -> float | None:
    """A finite number written in plain decimal or exponent form, else None."""
    if not _FLOAT.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _kind(element: str) -> str:
    return _CAMEL.sub("_", element).lower()


@cache
def _converter() -> UnitConverter:
    return UnitConverter()


@cache
def _aliases() -> dict[tuple[str, str], str]:
    text = resources.files("pydiggs._reader").joinpath("aliases.toml").read_text("utf-8")
    table: dict[tuple[str, str], str] = {}
    for alias in tomllib.loads(text).get("alias", []):
        for version in alias["versions"]:
            table[(version, alias["from"])] = alias["to"]
    return table


def _object(prop: etree._Element) -> etree._Element | None:
    """The one object element a property holds (``<casing><Casing/></casing>``)."""
    children = list(_elements(prop))
    if len(children) != 1 or prop.get(_XLINK_HREF) is not None:
        return None
    local = _local(children[0].tag)
    return children[0] if local[:1].isupper() else None


def _is_table_part(el: etree._Element) -> bool:
    """A result set or a time domain, or a property holding one: read into ``results``."""
    local = _local(el.tag)
    if local.endswith("ResultSet") or local in _TIME_DOMAINS:
        return True
    obj = _object(el)
    return obj is not None and (
        _local(obj.tag).endswith("ResultSet") or _local(obj.tag) in _TIME_DOMAINS
    )


def _plain_reference(el: etree._Element, text: str | None) -> bool:
    return (
        text is None and list(el.attrib) == [_XLINK_HREF] and not any(True for _ in _elements(el))
    )


#: Tells the reader that a typed field holds the text of an element (``""``) or one of
#: its attributes, so it is not content the model lacks (see :meth:`_Reader._took`).
Took = Callable[[etree._Element | None, str], None]


def _not_noted(_el: etree._Element | None, _what: str = "") -> None:
    return None


def _time_index(domain: etree._Element, rows: int, took: Took = _not_noted) -> list[dict[str, Any]]:
    """The time of each row, from a result's ``timeDomain``, when it has one per row."""
    obj = next(_elements(domain), None)
    if obj is None or rows == 0:
        return []
    local = _local(obj.tag)
    if local == "TimeIntervalList":
        listed = _child(obj, "timeIntervalList")
        values = [_number(t) for t in (_text(listed) or "").split()]
        if len(values) != rows or any(v is None for v in values):
            return []
        unit = obj.get("unit")
        took(listed, "")
        took(obj, "unit")
        return [_index_column("elapsed_time", values, _TIME_UNITS.get(unit or "", unit))]
    if local == "TimePositionList":
        sources = [_child(obj, "timePositionList")]
        times = (_text(sources[0]) or "").split()
    elif local == "MultiTimeInstant":
        sources = [
            e
            for e in obj.iter(etree.Element)
            if _local(e.tag) == "timePosition" and _text(e) is not None
        ]
        times = [_text(e) or "" for e in sources]
    else:
        return []
    if len(times) != rows:
        return []
    for source in sources:
        took(source, "")
    column = _index_column("time", [], None)
    column.update(type="datetime", values=times)
    return [column]


def _point_index(
    values: list[float], dim: int, rows: int, labels: list[str]
) -> list[dict[str, Any]]:
    """One column per axis when a multi-point location gives every row a position."""
    if rows == 0 or len(values) != dim * rows:
        return []
    names = ["position"] if dim == 1 else [f"position_{i + 1}" for i in range(dim)]
    return [
        _index_column(name, values[i::dim], labels[i] if i < len(labels) else None)
        for i, name in enumerate(names)
    ]


def _column_kind(declared: str, cells: list[str]) -> str:
    """The kind of value a column reads: from its declared type, else from its cells."""
    if declared in _NUMBER_TYPES:
        return "number"
    if declared in _BOOLEAN_TYPES:
        return "boolean"
    if declared in _DATETIME_TYPES:
        return "datetime"
    if declared:
        return "string"
    return "number" if all(_number(c) is not None for c in cells if c) and any(cells) else "string"


def _spread(values: list[Any], rows: list[int], count: int) -> list[Any]:
    """A column of ``count`` rows: ``values`` in their ``rows`` (ascending), null elsewhere."""
    if len(rows) == count:
        return values  # every row has a value for the column
    column: list[Any] = [None] * count
    for n, value in zip(rows, values, strict=True):
        column[n] = value
    return column


def _index_column(name: str, values: Sequence[float | None], uom: str | None) -> dict[str, Any]:
    affine = _converter()._affine(uom) if uom else None
    return {
        "name": name,
        "key": name,
        "definition": None,
        "uom": uom,
        "type": "number",
        "null_value": None,
        "values": values,
        "si_uom": None if affine is None else affine[0],
        "si_factor": None if affine is None else affine[1],
        "si_offset": None if affine is None else affine[2],
    }


def _code(el: etree._Element | None, took: Took = _not_noted) -> dict[str, Any] | None:
    text = _text(el)
    if el is None or text is None:
        return None
    took(el, "")
    took(el, "codeSpace")
    return {"value": text, "code_space": el.get("codeSpace")}


def _codes(lithology: etree._Element, took: Took = _not_noted) -> list[dict[str, Any]]:
    """A lithology's classification codes, in document order."""
    names = {*_CLASSIFICATION, "lithologyClassification"}
    return [
        code
        for e in _elements(lithology)
        if _local(e.tag) in names and (code := _code(e, took)) is not None
    ]


def _method(holder: etree._Element | None, took: Took = _not_noted) -> dict[str, Any] | None:
    """A method property as a code: the specification's name, or the property's text."""
    if holder is None:
        return None
    named = next((e for e in holder.iter(etree.Element) if _local(e.tag) == "name"), None)
    source = named if named is not None and _text(named) else holder
    return _code(source, took)


def _time(holder: etree._Element, took: Took = _not_noted) -> dict[str, Any] | None:
    """The first time instant or interval in ``holder``, ends as written."""
    for el in holder.iter(etree.Element):
        local = _local(el.tag)
        if local not in _TIME:
            continue
        start = end = None
        for child in el.iter(etree.Element):
            name = _local(child.tag)
            if name in ("start", "beginPosition", "timePosition") and start is None:
                start = _text(child)
                took(child, "")
            elif name in ("end", "endPosition") and end is None:
                end = _text(child)
                took(child, "")
        return {"start": start, "end": end}
    return None


def _attr_name(el: etree._Element, name: str) -> str:
    if not name.startswith("{"):
        return name
    ns, _, local = name[1:].partition("}")
    prefix = next((p for p, uri in el.nsmap.items() if uri == ns and p), None)
    return f"{prefix}:{local}" if prefix else name


class _Reader:
    """One read: the streaming pass, the per-member mappers and the final resolution."""

    #: The null cells short rows of result tables have been padded with so far.
    padded_cells: int = 0

    def __init__(
        self,
        data: bytes,
        *,
        name: str | None,
        diggs_version: str | None,
        results: bool,
        field_provenance: bool = False,
        index_ids: bool = False,
    ) -> None:
        self.data = data
        self.field_provenance = field_provenance
        self.name = name
        self.requested = diggs_version
        self.results = results
        self.version = diggs_version or "3.0.0"
        self.namespace: str | None = None
        self.collections: dict[str, list[dict[str, Any]]] = {
            c: [] for c in _CATEGORY.values() if c != "document"
        }
        self.collections["others"] = []
        self.document: dict[str, Any] | None = None
        self.layers: list[dict[str, Any]] = []
        self.components: list[dict[str, Any]] = []
        self.local_crs: dict[str, tuple[str | None, str | None]] = {}
        #: How many coordinate systems the file defines with each name and unit.
        self.crs_defined: Counter[tuple[str | None, str | None]] = Counter()
        self.id_owner: dict[str, str] = {}
        self.id_count: Counter[str] = Counter()
        self.id_repeat: dict[str, int | None] = {}  # duplicated id -> line of its 2nd use
        #: With ``index_ids``: where each identified element below a record sits, as the
        #: key of that record and the element's path below it. Not part of the model.
        self.id_places: dict[str, tuple[str, str]] | None = {} if index_ids else None
        #: With ``index_ids``: each identified element that got no place there (see
        #: :meth:`_mark`), as the key of its record and its mark. Not part of the model.
        self.id_marks: dict[str, list[tuple[str, str]]] = {}
        self.extra_ids: set[str] = set()
        self._child_names: dict[etree._Element, Counter[str]] = {}
        self.extra_count = 0
        self.node_count = 0
        self.limit = LIMIT_BASE + len(data) // LIMIT_BYTES_PER_ITEM
        self.deadline: float | None = None
        self.should_stop: Callable[[], bool] | None = None
        self.raw_links: list[tuple[str, str, str, int | None, str]] = []
        self.srs_first: dict[str, tuple[str, int | None]] = {}
        self.axis: dict[str, list[Any]] = {}
        self.notes: list[Finding] = []
        self.complete = True
        self.limits_hit: list[str] = []
        self.final_places: dict[str, tuple[str, str]] = {}
        self.final_marks: dict[str, tuple[tuple[str, str], ...]] = {}
        #: With ``index_ids``: what typed fields hold of each element of the member being
        #: read (``""`` for its text, else an attribute), and the elements kept whole in
        #: ``extra``. What is in neither is the content the model lacks (:meth:`_left`).
        self._masks: dict[etree._Element, set[str]] | None = {} if index_ids else None
        self._kept: set[etree._Element] = set()
        #: Result values no column holds, per ``dataValues``: row, column, text.
        self._cells: dict[etree._Element, list[tuple[int, int, str]]] = {}
        #: The ``typeData`` each column was read from, with the kind of value it gave.
        self._declared: dict[etree._Element, str] = {}
        #: The ``dataValues`` whose values the columns hold (see :data:`SPELLED`).
        self._spelled: set[etree._Element] = set()
        #: The content the model lacks, by the key of the record it belongs to.
        self.unread: dict[str, list[ExtraNode]] = {}
        #: How many elements each record has that typed fields hold in full, by the
        #: names from the record down to the element.
        self.names: dict[str, Counter[str]] = {}
        self.final_names: dict[str, dict[str, int]] = {}
        self.unread_count = 0
        self.unread_seen = 0
        self.unread_complete = True
        self.final_unread: dict[str, tuple[ExtraNode, ...]] = {}
        #: With ``index_ids``: the attributes of the root element, and the text between,
        #: before and after its members, in the order of the file (see :meth:`_outside`).
        self._root_qname = "Diggs"
        self._root_attrs: dict[str, str] = {}
        self._between: list[str] = []
        self._root_text_read = False

    def _took(self, el: etree._Element | None, what: str = "") -> None:
        """Note that a typed field holds the text of ``el`` (``""``) or one attribute of it.

        Only what a field holds in full is noted: a value read in part, or read and not
        kept, stays content the model lacks.
        """
        if self._masks is not None and el is not None:
            self._masks.setdefault(el, set()).add(what)

    # --- notes ----------------------------------------------------------------------

    def note(self, rule: str, message: str, *, line: int | None, xpath: str | None) -> None:
        severity = rules._SEVERITIES.get(rule, (Severity.INFO,))[0]
        self.notes.append(
            Finding(
                step=Step(rule.split(".", 1)[0]),
                rule_id=rule,
                severity=severity,
                message=message,
                line=line,
                xpath=xpath,
            )
        )

    # --- the pass -------------------------------------------------------------------

    def run(self, deadline: float | None, should_stop: Callable[[], bool] | None = None) -> Model:
        self.deadline = deadline
        self.should_stop = should_stop
        entity = _xml.markup_entity(self.data)
        if entity is not None:
            finding = Finding(
                step=Step.STRUCTURE,
                rule_id=rules.MARKUP_ENTITY,
                severity=Severity.ERROR,
                message=f'The internal DTD declares entity "{entity}" with markup in its value.',
            )
            raise DocumentError("unsafe-entity", finding.message, finding)
        try:
            self._stream()
        except etree.XMLSyntaxError as exc:
            line = exc.lineno if isinstance(exc.lineno, int) and exc.lineno > 0 else None
            finding = Finding(
                step=Step.STRUCTURE,
                rule_id=rules.WELL_FORMED,
                severity=Severity.ERROR,
                message=f"The file is not well-formed XML: {exc.msg}",
                line=line,
            )
            raise DocumentError("malformed", finding.message, finding) from exc
        return Model.from_dict(self._finish())

    def _tick(self) -> None:
        """Between members, and every so often inside one: cancelled, or out of time?"""
        if self.should_stop is not None and self.should_stop():
            raise _Cancelled
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise _LimitError("time_budget")

    def _stream(self) -> None:
        parser = etree.XMLPullParser(
            events=("start", "end", "start-ns"),
            resolve_entities="internal",
            load_dtd=False,
            no_network=True,
            huge_tree=False,
        )
        depth = 0
        root: etree._Element | None = None
        root_step = ""
        counts: Counter[tuple[str | None, str]] = Counter()
        for start in range(0, max(len(self.data), 1), _CHUNK):
            parser.feed(self.data[start : start + _CHUNK])
            for event, el in parser.read_events():
                if event == "start-ns":
                    self._check_namespace(el)
                    continue
                if event == "start":
                    depth += 1
                    if depth == 1:
                        root = el
                        root_step = self._root(el)
                    continue
                if depth == 2 and root is not None and isinstance(el.tag, str):  # noqa: PLR2004
                    key = (el.prefix, _local(el.tag))
                    counts[key] += 1
                    try:
                        self._member(el, f"/{root_step}/{_qname(el)}[{counts[key]}]")
                        # The text after a member is not read yet (the parser has only
                        # just closed it): it stays with the member, and is taken with
                        # the text after the members before it once the next one is read.
                        el.clear(keep_tail=True)
                        self._outside(root, el)
                        while el.getprevious() is not None:
                            del root[0]
                        self._tick()
                    except _LimitError as limit:
                        self._stop(el, limit.name)
                        return
                depth -= 1
        parser.close()
        if root is None:
            raise etree.XMLSyntaxError("Document is empty", 0, 1, 1)
        self._outside(root, None)

    def _root(self, el: etree._Element) -> str:
        if _local(el.tag) != "Diggs":
            finding = Finding(
                step=Step.STRUCTURE,
                rule_id=rules.DIGGS_ROOT,
                severity=Severity.ERROR,
                message=f"The root element is {_qname(el)}, not a DIGGS Diggs element.",
                line=el.sourceline,
            )
            raise DocumentError("not-diggs", finding.message, finding)
        ns = etree.QName(el).namespace
        self.namespace = ns
        if self.requested is None:
            self.version = NAMESPACE_PROFILES.get(ns or "", "3.0.0")
        if self._masks is not None:
            self._root_qname = _qname(el)
            self._root_attrs = {
                _attr_name(el, str(name)): str(value)
                for name, value in el.attrib.items()
                if name not in (_GML_ID, _SCHEMA_LOCATION)
            }
        return f"{_qname(el)}[1]"

    def _outside(self, root: etree._Element, last: etree._Element | None) -> None:
        """Keep the text of the root element that is outside every member.

        That is the text before the first member and the text after each member,
        comment and processing instruction, read only once the parser is past it: the
        text after the children of ``root`` before ``last`` (the member just read), or
        after all of them at the end of the file (``last`` None). Those children are
        dropped next, and their text with them. Only for a comparison (``index_ids``).
        """
        if self._masks is None:
            return
        if not self._root_text_read:
            self._root_text_read = True
            if root.text and (text := root.text.strip()):
                self._between.append(text)
        for child in root:
            if child is last:
                break
            if child.tail and (tail := child.tail.strip()):
                self._between.append(tail)

    @staticmethod
    def _check_namespace(declaration: Any) -> None:
        """Refuse a namespace URI longer than ``validate`` accepts: every element repeats it."""
        uri = declaration[1] if isinstance(declaration, tuple) else ""
        if len(uri or "") > MAX_NAMESPACE_CHARS:
            finding = Finding(
                step=Step.STRUCTURE,
                rule_id=rules.NAMESPACE_TOO_LONG,
                severity=Severity.ERROR,
                message=(
                    f"The file declares a namespace URI of {len(uri):,} characters, more than"
                    f" the {MAX_NAMESPACE_CHARS:,} pyDIGGS accepts (real ones are under 100)."
                ),
            )
            raise DocumentError("namespace-too-long", finding.message, finding)

    def _stop(self, el: etree._Element, limit: str) -> None:
        """Stop reading at ``limit``: a member cut short and the later ones are not read."""
        self.complete = False
        self.limits_hit.append(limit)
        if limit == "time_budget":
            self.note(
                rules.TIMEOUT,
                "Reading stopped at its time budget; members after this point were not read.",
                line=el.sourceline,
                xpath=None,
            )
            return
        self.note(
            rules.READ_LIMIT,
            f"Reading stopped: {_LIMIT_TEXT[limit]}. The member at this point and those"
            " after it were not read.",
            line=el.sourceline,
            xpath=None,
        )

    # --- members --------------------------------------------------------------------

    def _member(self, prop: etree._Element, xpath: str) -> None:
        objects = list(_elements(prop))
        nested: dict[etree._Element, str] = {}
        if not objects:
            # A member reference (<projectRef xlink:href=.../>), or a property of the
            # document itself (gml:name, gml:description), whose text is its value.
            linked = prop.get(_XLINK_HREF) is not None
            node = self._header(prop, xpath, kind="reference" if linked else "property")
            value = None if linked else self._param(prop, xpath)
            if value:
                node["params"] = {**node["params"], _local(prop.tag): value}
            node["results"] = []
            self.collections["others"].append(node)
            nested[prop] = node["key"]
            self._scan(prop, xpath, node["key"], nested, base=len(xpath))
            self._lacking(prop, node["key"], nested)
            self._child_names.clear()
            return
        category = _CATEGORY.get(_local(prop.tag), "others")
        owner = ""
        seen: Counter[tuple[str | None, str]] = Counter()
        for index, obj in enumerate(objects):
            key = (obj.prefix, _local(obj.tag))
            seen[key] += 1
            obj_xpath = f"{xpath}/{_qname(obj)}[{seen[key]}]"
            kind = category if index == 0 else "others"
            node = self._build(kind, obj, obj_xpath, nested)
            nested[obj] = node["key"]
            if index == 0:
                owner = node["key"]
            if kind == "document":
                self.document = node
            else:
                self.collections[kind].append(node)
        self._scan(prop, xpath, owner, nested, base=len(xpath))
        self._lacking(prop, owner, nested)
        self._child_names.clear()

    def _scan(
        self,
        el: etree._Element,
        xpath: str,
        owner: str,
        nested: dict[etree._Element, str],
        *,
        base: int,
        marking: bool = False,
    ) -> str | None:
        """Record every id, link and srsName in ``el``'s subtree, with its owning node.

        ``base`` is how much of ``xpath`` leads to the record that holds ``el``. Inside
        an element that is being marked (``marking``, see :meth:`_mark`) the digest of
        what ``el`` holds is returned; nothing is digested anywhere else.
        """
        if el in nested:
            owner, base = nested[el], len(xpath)
        gml_id = el.get(_GML_ID)
        marked = False
        if gml_id:
            self.id_owner.setdefault(gml_id, owner)
            self.id_count[gml_id] += 1
            if self.id_count[gml_id] == 2:  # noqa: PLR2004 - the first repeat
                self.id_repeat[gml_id] = el.sourceline
            if self.id_places is not None and el not in nested:
                marked = not self._index_id(el, gml_id, owner, nested)
        href = el.get(_XLINK_HREF)
        if href is not None:
            relation = _local(el.tag)
            relation = relation.removesuffix("Ref") or relation
            self.raw_links.append((owner, relation, href.strip(), el.sourceline, xpath))
        srs = el.get("srsName")
        if srs and srs not in self.srs_first:
            self.srs_first[srs] = (xpath, el.sourceline)
        if gml_id and _local(el.tag) in _LOCAL_CRS:
            described = _local_crs(el)
            self.local_crs.setdefault(gml_id, described)
            self.crs_defined[described] += 1
        marking = marking or marked
        below: list[str | None] = []
        # A plain dict: this runs for every element, and a Counter costs more to make.
        counts: dict[tuple[str | None, str], int] = {}
        for child in _elements(el):
            key = (child.prefix, _local(child.tag))
            counts[key] = position = counts.get(key, 0) + 1
            held = self._scan(
                child,
                f"{xpath}/{_qname(child)}[{position}]",
                owner,
                nested,
                base=base,
                marking=marking,
            )
            if marking:
                below.append(held)
        if not marking:
            return None
        digest = _held(el, below)
        if marked and gml_id:
            self._mark(gml_id, owner, xpath[base:], digest)
        return digest

    def _lacking(self, prop: etree._Element, owner: str, nested: dict[etree._Element, str]) -> None:
        """Collect what the member holds that its records do not (see :meth:`_left`)."""
        masks = self._masks
        if masks is None:
            return
        try:
            if self.unread_complete:
                home = etree.QName(prop).namespace
                names = self.names.setdefault(owner, Counter())
                node, _ = self._left(prop, nested, (_local(prop.tag), names, home))
                if node is not None:
                    self.unread.setdefault(owner, []).append(node)
        except _Enough:
            self.unread_complete = False
        finally:
            masks.clear()
            self._kept.clear()
            self._cells.clear()
            self._declared.clear()
            self._spelled.clear()

    def _left(
        self,
        el: etree._Element,
        nested: dict[etree._Element, str],
        at: tuple[str, Counter[str], str | None],
    ) -> tuple[ExtraNode | None, str]:
        """What ``el`` holds that the model does not: the rest of it, as an ``extra`` element.

        Returns that element, if anything is left, and a digest of what typed fields
        hold of ``el`` and of everything below it (empty when they hold nothing).

        A typed field reads part of an element (a position out of a point location) and
        the model keeps that part; the element is not kept in ``extra``. What is left is
        every attribute and text that no field holds (:meth:`_took`), in every element
        that ``extra`` does not keep whole. Ids are left out, and so is a link that is
        all its element has: an id is not compared, and every link is one of the model's
        ``links``. An element with
        nothing left is left out with them, unless it never had anything: an empty
        element is there or not.

        A record nested in ``el`` is given its own (by its key), so each record has what
        it lacks. The elements that have something left are counted against the work
        limit of the read, as those of ``extra`` are, and stopped by it
        (:class:`_Enough`); every element looked at counts toward the time budget.

        An element that is left out because typed fields hold all of it is still an
        element of the file: it is counted, by the names of the elements from its record
        down to it (``at``: that path, the counts of its record, and the namespace a
        name is written without). So an element that is renamed, added or removed is
        seen whatever it holds. Each path counts against the same work limit.

        What is left of an element stays tied to what typed fields hold of it: the
        digest is kept with the element (:data:`HELD`), and the comparison tells
        same-named siblings apart by it. Without it, two names that exchanged their
        ``codeSpace`` would leave the same two attributes behind, in any order. Text
        after a comment or a child element is no field's either: it is kept as
        :data:`TAIL`.
        """
        path, names = at[0], at[1]
        self.unread_seen += 1
        if self.unread_seen % _CHECK_EVERY == 0:
            self._tick()
        took = (self._masks or {}).get(el, ())
        attrs = {
            _attr_name(el, str(name)): str(value)
            for name, value in el.attrib.items()
            if name not in (_GML_ID, _XLINK_HREF) and str(name) not in took
        }
        href = el.get(_XLINK_HREF)
        text = None if "" in took else _text(el)
        children, below, tail = self._left_below(el, nested, at)
        if tail:
            attrs[TAIL] = tail
        own = sorted(
            (name, value)
            for name in took
            if name != "srsName"  # an id of the file, compared by what it names
            and (value := _text(el) if not name else el.get(name)) is not None
        )
        held = _digest_of((own, sorted(below))) if own or below else ""
        bare = not el.attrib and not took and not len(el)
        # A result set's values say how they are written even when no attribute is
        # there to say it (the defaults apply): their element is always left.
        left = bool(attrs or text is not None or children or bare or el in self._spelled)
        if left:
            self.unread_count += 1 + len(self._cells.get(el, ()))
        elif el not in nested:
            self.unread_count += path not in names
            names[path] += 1
        if self.unread_count > self.limit:
            raise _Enough
        if not left:
            return None, held
        if held:
            attrs[HELD] = held
        if el in self._spelled:
            attrs[SPELLED] = " ".join(_SEPARATORS)
        kind = self._declared.get(el)
        if kind is not None and text is not None:
            attrs[DECLARED] = kind
        if href is not None:
            # A link is one of the record's ``links``, which do not say which element
            # holds it. Where the element has more than the link, the link stays with
            # it, compared by what it names: two such elements that exchange their
            # links are then seen.
            attrs[_attr_name(el, _XLINK_HREF)] = str(href)
        node = ExtraNode(
            qname=_qname(el),
            ns=etree.QName(el).namespace,
            attrs=attrs,
            text=text,
            children=tuple(children),
            line=el.sourceline,
        )
        return node, held

    def _left_below(
        self,
        el: etree._Element,
        nested: dict[etree._Element, str],
        at: tuple[str, Counter[str], str | None],
    ) -> tuple[list[ExtraNode], list[str], str]:
        """What is left of the children of ``el`` (see :meth:`_left`), and of its values.

        With it, the digests of what typed fields hold below ``el`` (not below a record
        nested in it, which stands for itself), and the text that follows the children,
        comments and processing instructions of ``el``.
        """
        path, names, home = at
        children: list[ExtraNode] = []
        helds: list[str] = []
        tails: list[str] = []
        for child in el:
            if child.tail and (tail := child.tail.strip()):
                tails.append(tail)
            if not isinstance(child.tag, str):
                continue
            if child in self._kept:
                # ``extra`` keeps the element and its text, and no text after a child.
                if inside := _tails(child):
                    kept = ExtraNode(
                        qname=_qname(child), ns=etree.QName(child).namespace, attrs={TAIL: inside}
                    )
                    children.append(kept)
                continue
            step = _local(child.tag) if etree.QName(child).namespace == home else str(child.tag)
            if child in nested:
                below = (step, self.names.setdefault(nested[child], Counter()), home)
            else:
                below = (_short(f"{path}.{step}"), names, home)
            node, held = self._left(child, nested, below)
            if child in nested:
                if node is not None:
                    self.unread.setdefault(nested[child], []).append(node)
                continue
            if held:
                helds.append(held)
            if node is not None:
                children.append(node)
        for row, column, cell in self._cells.get(el, ()):
            # A value no column holds, as it is written, with where it stands; or how
            # many values a row has that is shorter than the columns.
            children.append(
                ExtraNode(qname="value", attrs={"row": str(row), "column": str(column)}, text=cell)
                if column
                else ExtraNode(qname="row", attrs={"row": str(row), "values": cell})
            )
        return children, helds, " ".join(tails)

    def _index_id(
        self, el: etree._Element, gml_id: str, owner: str, nested: dict[etree._Element, str]
    ) -> bool:
        """Record the place of an identified element that is not itself a record.

        False when the element has to be marked instead (see :meth:`_mark`): it got no
        place, or an element before it has its id (whichever of them the model keeps).
        """
        places = self.id_places
        if places is None:
            return True
        if self.id_count[gml_id] > 1:
            return False
        if gml_id in self.extra_ids:
            return True  # the model keeps the element itself, which says more
        if len(places) >= self.limit:
            return False  # past the work limit nothing more is indexed
        place = self._place(el, nested)
        if place is None:
            return False
        places[gml_id] = (owner, place)
        return True

    def _mark(self, gml_id: str, owner: str, below: str, digest: str) -> None:
        """Note where an identified element with no place of its own sits, and what it holds.

        A place (:meth:`_place`) names one element whatever the order of the file. An
        element without one (a same-named sibling on the way, too deep, past the work
        limit, an id used twice) is told by its position among its siblings below its
        record (``below``, the rest of its XPath) and by a digest of what it holds. The
        position alone would not do: two same-named siblings can change places while a
        reference moves from one to the other. Neither part has an id in it, so a mark
        is the same when only ids changed, and two elements that exchange their ids
        exchange their marks. The cost is one digest per element inside a marked one,
        each made once: bounded by the size of the member.
        """
        mark = hashlib.blake2b(
            repr((below, digest)).encode("utf-8", "surrogatepass"), digest_size=16
        ).hexdigest()
        self.id_marks.setdefault(gml_id, []).append((owner, mark))

    def _place(self, el: etree._Element, nested: dict[etree._Element, str]) -> str | None:
        """The path of ``el`` below the record that holds it, when no step could be another.

        Each step is the local name of an element that is the only child of its parent
        with that name, so the path names one element whatever the order of the file and
        whatever its ``gml:id``. An element with a same-named sibling anywhere on the way
        has no place: which of the two is meant would rest on their order. Nor has one
        that is deep below its record, or outside every record. The children of a parent
        are counted once per member, so the work is bounded by the size of the member.
        """
        steps: list[str] = []
        size = 0
        node = el
        while node not in nested:
            parent = node.getparent()
            if parent is None or parent.getparent() is None or len(steps) >= MAX_PLACE_STEPS:
                return None
            names = self._child_names.get(parent)
            if names is None:
                names = self._child_names[parent] = Counter(
                    _local(child.tag) for child in _elements(parent)
                )
            name = _local(node.tag)
            size += len(name) + 1
            if names[name] != 1 or size > MAX_PLACE_CHARS:
                return None
            steps.append(name)
            node = parent
        return "/".join(reversed(steps))

    # --- mappers --------------------------------------------------------------------

    def _build(
        self,
        kind: str,
        obj: etree._Element,
        xpath: str,
        nested: dict[etree._Element, str],
    ) -> dict[str, Any]:
        mapper: Callable[..., dict[str, Any]] = getattr(self, f"_map_{kind}")
        return mapper(obj, xpath, nested)

    def _header(
        self,
        el: etree._Element,
        xpath: str,
        *,
        kind: str | None = None,
        consumed: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        self.node_count += 1
        if self.node_count > self.limit:
            raise _LimitError("nodes")
        if self.node_count % _CHECK_EVERY == 0:
            self._tick()
        element = _local(el.tag)
        element = _aliases().get((self.version, element), element)
        named = [c for c in _elements(el) if _local(c.tag) == "name" and _text(c)]
        names = [_text(c) for c in named]
        noted = [
            content
            for c in _elements(el)
            if _local(c.tag) == "remark"
            for content in c.iter(f"{{{etree.QName(c).namespace}}}content")
            if _text(content)
        ]
        remarks = [_text(content) for content in noted]
        described = _child(el, "description")
        for read in (*named, *noted, described):
            self._took(read)
        skip = {"name", "description", "remark", *consumed}
        params, rest = self._params(el, xpath, skip)
        return {
            "key": "@" + xpath,
            "gml_id": el.get(_GML_ID),
            "element": element,
            "kind": kind or _kind(element),
            "name": names[0] if names else None,
            "names": names,
            "description": _text(described),
            "remarks": remarks,
            "params": params,
            "provenance": {
                "xpath": xpath,
                "line": el.sourceline,
                "fields": _field_locations(el, rest) if self.field_provenance else None,
            },
            "extra": [self._extra(c) for c in rest],
        }

    def _params(
        self, el: etree._Element, xpath: str, skip: set[str] | frozenset[str]
    ) -> tuple[dict[str, Any], list[etree._Element]]:
        """Split ``el``'s children (except ``skip``) into simple values and the rest.

        A simple value has no child elements, appears once, has text, and carries no
        attribute but ``uom`` or ``codeSpace``. A plain reference (no text, no children,
        only ``xlink:href``) is neither: it is already one of the model's ``links``.
        """
        children = [c for c in _elements(el) if _local(c.tag) not in skip]
        seen = Counter(_local(c.tag) for c in children)
        params: dict[str, Any] = {}
        rest: list[etree._Element] = []
        for child in children:
            name = _local(child.tag)
            attrs = {_local(k) for k in child.attrib}
            text = _text(child)
            if _plain_reference(child, text) or _is_table_part(child):
                continue
            if (
                seen[name] == 1
                and text is not None
                and attrs <= _PARAM_ATTRS
                and not any(True for _ in _elements(child))
            ):
                params[name] = self._param(child, xpath)
            else:
                rest.append(child)
        return params, rest

    def _param(self, el: etree._Element, xpath: str) -> Any:
        if el.get("uom") is not None:
            return self._quantity(el, xpath)
        text = _text(el) or ""
        code_space = el.get("codeSpace")
        self._took(el)
        self._took(el, "codeSpace")
        return text if code_space is None else {"value": text, "code_space": code_space}

    def _extra(self, el: etree._Element, depth: int = 1) -> dict[str, Any]:
        """An element no typed field holds, kept whole; counted against the work limits."""
        self.extra_count += 1
        if depth > MAX_EXTRA_DEPTH:
            raise _LimitError("extra_depth")
        if self.extra_count > self.limit:
            raise _LimitError("extra_elements")
        if self.extra_count % _CHECK_EVERY == 0:
            self._tick()
        if self.id_places is not None and (kept := el.get(_GML_ID)):
            self.extra_ids.add(kept)
        if depth == 1 and self._masks is not None:
            self._kept.add(el)
        return {
            "qname": _qname(el),
            "ns": etree.QName(el).namespace,
            "attrs": {_attr_name(el, str(k)): str(v) for k, v in el.attrib.items()},
            "text": _text(el),
            "children": [self._extra(c, depth + 1) for c in _elements(el)],
            "line": el.sourceline,
        }

    # --- components -----------------------------------------------------------------

    def _components(
        self,
        holder: etree._Element,
        xpath: str,
        roles: frozenset[str] | None,
        nested: dict[etree._Element, str],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Components for ``holder``'s property children (``roles``, or every object).

        A property child holds one object element: ``<casing><Casing>…``. Geometry and
        time objects are places and times, not components. Returns the components and
        the property names used.
        """
        out: list[dict[str, Any]] = []
        used: set[str] = set()
        counts: Counter[tuple[str | None, str]] = Counter()
        for prop in _elements(holder):
            key = (prop.prefix, _local(prop.tag))
            counts[key] += 1
            role = key[1]
            if role in _HEADER or (roles is not None and role not in roles):
                continue
            obj = _object(prop)
            if obj is None or _local(obj.tag) in _GEOMETRY | _TIME or _is_table_part(prop):
                continue
            prop_xpath = f"{xpath}/{_qname(prop)}[{counts[key]}]"
            out.append(self._component(obj, f"{prop_xpath}/{_qname(obj)}[1]", role, nested))
            used.add(role)
        return out, used

    def _component(
        self, el: etree._Element, xpath: str, role: str, nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        interval_prop = location_prop = time_prop = None
        for prop in _elements(el):
            obj = _object(prop)
            if obj is None:
                continue
            local = _local(obj.tag)
            if local == "LinearExtent" and interval_prop is None:
                interval_prop = prop
            elif (
                local == "PointLocation"
                and location_prop is None
                and _local(prop.tag) not in _OTHER_POINTS
            ):
                location_prop = prop
            elif local in _TIME and time_prop is None:
                time_prop = prop
        places = [p for p in (interval_prop, location_prop, time_prop) if p is not None]
        children, _ = self._components(el, xpath, None, nested)
        consumed = {_local(p.tag) for p in places}
        consumed |= {
            _local(p.tag)
            for p in _elements(el)
            if (obj := _object(p)) is not None and _local(obj.tag) not in _GEOMETRY | _TIME
        }
        node = self._header(el, xpath, consumed=frozenset(consumed))
        # Repeated place or time properties beyond the first stay in ``extra``.
        node["extra"].extend(
            self._extra(p)
            for p in _elements(el)
            if _local(p.tag) in consumed
            and p not in places
            and (obj := _object(p)) is not None
            and _local(obj.tag) in _GEOMETRY | _TIME
        )
        pos = None if location_prop is None else self._first(location_prop, "pos")
        node.update(
            role=role,
            interval=self._interval(interval_prop),
            location=None if pos is None else self._position(pos, xpath),
            time=None if time_prop is None else _time(time_prop, self._took),
            components=children,
        )
        nested[el] = node["key"]
        self.components.append(node)
        return node

    def _map_document(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="document_info", consumed=frozenset({"creationDate"}))
        created = _child(obj, "creationDate")
        node["creation_date"] = _text(created)
        self._took(created)
        return node

    def _map_projects(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="project")
        locality = _child(obj, "locality")
        place = None if locality is None else next(_elements(locality), None)
        node["client"] = None
        sources = [] if place is None else [_child(place, "description"), _child(place, "name")]
        source = next((e for e in sources if _text(e)), None)
        node["locality"] = _text(source)
        self._took(source)
        return node

    def _map_associates(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="associate")
        node["roles"] = [
            code
            for r in obj.iter(etree.Element)
            if _local(r.tag) == "rolePerformed" and (code := _code(r, self._took)) is not None
        ]
        return node

    def _map_features(
        self, obj: etree._Element, xpath: str, nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        construction, built = self._components(obj, xpath, _CONSTRUCTION, nested)
        strikes, struck = self._components(obj, xpath, frozenset({"waterStrike"}), nested)
        consumed = frozenset(
            {"referencePoint", "centerLine", "totalMeasuredDepth", "whenConstructed"}
            | built
            | struck
        )
        node = self._header(obj, xpath, consumed=consumed)
        constructed = _child(obj, "whenConstructed")
        node["when_constructed"] = None if constructed is None else _time(constructed, self._took)
        point = _child(obj, "referencePoint")
        pos = None if point is None else self._first(point, "pos")
        node["location"] = None if pos is None else self._position(pos, xpath)
        line = _child(obj, "centerLine")
        pos_list = None if line is None else self._first(line, "posList")
        node["centerline"] = None if pos_list is None else self._positions(pos_list, xpath)
        depth = _child(obj, "totalMeasuredDepth")
        node["total_depth"] = None if depth is None else self._quantity(depth, xpath)
        lrs = next(
            (
                el.get(_GML_ID)
                for el in obj.iter(etree.Element)
                if _local(el.tag) == "LinearSpatialReferenceSystem" and el.get(_GML_ID)
            ),
            None,
        )
        node["lrs"] = None if lrs is None else f"#{lrs}"
        node["construction"] = construction
        node["water_strikes"] = strikes
        node["parent"] = None
        return node

    def _map_sampling_activities(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        consumed = frozenset({"samplingLocation", "totalSampleRecoveryLength", "samplingDate"})
        node = self._header(obj, xpath, kind="sampling_activity", consumed=consumed)
        node["feature"] = None
        node["interval"] = self._interval(_child(obj, "samplingLocation"))
        node["method"] = _method(_child(obj, "samplingMethod"), self._took)
        recovery = _child(obj, "totalSampleRecoveryLength")
        node["recovery"] = None if recovery is None else self._quantity(recovery, xpath)
        date = _child(obj, "samplingDate")
        node["date"] = None if date is None else _time(date, self._took)
        return node

    def _map_samples(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(
            obj, xpath, kind="sample", consumed=frozenset({"samplingLocation", "classification"})
        )
        node.update(feature=None, sampling_activity=None, method=None, recovery=None)
        node["interval"] = self._interval(_child(obj, "samplingLocation"))
        node["type"] = _code(_child(obj, "classification"), self._took)
        return node

    def _map_observations(
        self, obj: etree._Element, xpath: str, nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, consumed=frozenset({"lithologyObservation"}))
        node["system"] = _local(obj.tag)
        node["feature"] = None
        layers: list[dict[str, Any]] = []
        counts: Counter[tuple[str | None, str]] = Counter()
        for prop in _elements(obj):
            key = (prop.prefix, _local(prop.tag))
            counts[key] += 1
            if key[1] != "lithologyObservation":
                continue
            prop_xpath = f"{xpath}/{_qname(prop)}[{counts[key]}]"
            layer_seen: Counter[tuple[str | None, str]] = Counter()
            for layer_el in _elements(prop):
                layer_key = (layer_el.prefix, _local(layer_el.tag))
                layer_seen[layer_key] += 1
                layer_xpath = f"{prop_xpath}/{_qname(layer_el)}[{layer_seen[layer_key]}]"
                layer = self._layer(layer_el, layer_xpath, nested)
                nested[layer_el] = layer["key"]
                layers.append(layer)
        node["layers"] = layers
        return node

    def _layer(
        self, el: etree._Element, xpath: str, nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        roles = frozenset({"primaryLithology", "componentLithology"})
        components, used = self._components(el, xpath, roles, nested)
        node = self._header(
            el,
            xpath,
            kind="lithology_layer",
            consumed=frozenset({"location", "unitName"} | used),
        )
        node["interval"] = self._interval(_child(el, "location"))
        unit = _child(el, "unitName")
        node["geologic_unit"] = _text(unit)
        self._took(unit)
        holder = _child(el, "primaryLithology")
        primary = None if holder is None else _object(holder)
        node["classification"] = [] if primary is None else _codes(primary, self._took)
        if node["description"] is None and primary is not None:
            described = _child(primary, "lithDescription")
            node["description"] = _text(described)
            self._took(described)
        colours = [] if primary is None else list(primary.iter(etree.Element))
        colour = next(
            (
                e
                for name in ("colorCode", "colorName")
                for e in colours
                if _local(e.tag) == name and _text(e) is not None
            ),
            None,
        )
        node["colour"] = _code(colour, self._took)
        node["components"] = components
        return node

    def _map_measurements(
        self, obj: etree._Element, xpath: str, nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, consumed=frozenset({"procedure", "outcome", "resultTime"}))
        result_time = _child(obj, "resultTime")
        node.update(
            feature=None,
            samples=[],
            specimen=None,
            result_time=None if result_time is None else _time(result_time, self._took),
        )
        holders = [c for c in _elements(obj) if _local(c.tag) == "procedure"]
        procedure = None if not holders else next(_elements(holders[0]), None)
        node["procedure"] = (
            None
            if procedure is None
            else self._procedure(procedure, f"{xpath}/{_qname(holders[0])}[1]", nested)
        )
        node["extra"].extend(self._extra(c) for c in holders[1:])
        tables = self._tables(obj, xpath)
        node["results"] = tables
        return node

    def _map_others(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath)
        node["results"] = self._tables(obj, xpath)
        return node

    def _tables(self, obj: etree._Element, xpath: str) -> list[dict[str, Any]]:
        """Every result set (a ``*ResultSet``, i.e. a ``gml:DataBlock``) under ``obj``."""
        return [
            self._table(el, xpath)
            for el in obj.iter(etree.Element)
            if _local(el.tag).endswith("ResultSet")
        ]

    # --- values ---------------------------------------------------------------------

    def _procedure(
        self, el: etree._Element, holder_xpath: str, nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        xpath = f"{holder_xpath}/{_qname(el)}[1]"
        components, used = self._components(el, xpath, None, nested)
        params, rest = self._params(el, xpath, used)
        element = _local(el.tag)
        return {
            "type": _aliases().get((self.version, element), element),
            "params": params,
            "components": components,
            "extra": [self._extra(c) for c in rest],
        }

    def _quantity(self, el: etree._Element, xpath: str) -> dict[str, Any]:
        text = (el.text or "").strip()
        value = _number(text)
        if text and value is None:
            self.note(
                rules.VALUE_UNPARSED,
                f'{_local(el.tag)} "{text[:80]}" is not a number; kept as text.',
                line=el.sourceline,
                xpath=xpath,
            )
        uom = el.get("uom")
        self._took(el)
        self._took(el, "uom")
        si = None
        if value is not None and uom:
            base = _converter().base_unit(uom)
            converted = None if base is None else _converter().to_base(value, uom)
            if base is not None and converted is not None and math.isfinite(converted):
                si = {"value": converted, "uom": base}
        return {"text": text, "value": value, "uom": uom, "si": si}

    def _interval(self, holder: etree._Element | None) -> dict[str, Any] | None:
        if holder is None:
            return None
        extent = next((e for e in holder.iter(etree.Element) if _local(e.tag) == "posList"), None)
        if extent is None:
            return None
        tokens = (extent.text or "").split()
        if len(tokens) != 2:  # noqa: PLR2004 - a 1-D interval: top and base
            return None
        srs = self._srs(extent)
        self._took(extent)
        top, base = ({"text": t, "value": _number(t), "uom": None, "si": None} for t in tokens)
        return {"top": top, "base": base, "lrs": srs}

    @staticmethod
    def _first(el: etree._Element, local: str) -> etree._Element | None:
        return next((e for e in el.iter(etree.Element) if _local(e.tag) == local), None)

    @staticmethod
    def _inherited_by(el: etree._Element, attr: str) -> etree._Element | None:
        """The element ``attr`` is inherited from: ``el`` or its nearest ancestor with it."""
        node: etree._Element | None = el
        while node is not None:
            if node.get(attr):
                return node
            node = node.getparent()
        return None

    @staticmethod
    def _inherited(el: etree._Element, attr: str) -> str | None:
        node: etree._Element | None = el
        while node is not None:
            value = node.get(attr)
            if value:
                return str(value)
            node = node.getparent()
        return None

    def _srs(self, el: etree._Element) -> str | None:
        node: etree._Element | None = el
        while node is not None:
            value = node.get("srsName")
            if value:
                self._took(node, "srsName")
                return str(value)
            node = node.getparent()
        return None

    def _dim(self, el: etree._Element, count: int, *, single: bool) -> int:
        stated = self._inherited(el, "srsDimension")
        if stated and stated.isdecimal() and int(stated) > 0:
            return int(stated)
        labels = self._inherited(el, "uomLabels")
        if labels:
            return len(labels.split())
        return count if single else 3

    def _coords(self, el: etree._Element, xpath: str) -> list[float] | None:
        tokens = (el.text or "").split()
        values = [_number(t) for t in tokens]
        if not tokens or any(v is None for v in values):
            self.note(
                rules.VALUE_UNPARSED,
                f"{_local(el.tag)} has a coordinate that is not a number.",
                line=el.sourceline,
                xpath=xpath,
            )
            return None
        return [float(v) for v in values if v is not None]

    def _position(self, el: etree._Element, xpath: str) -> dict[str, Any] | None:
        coords = self._coords(el, xpath)
        if coords is None:
            return None
        dim = self._dim(el, len(coords), single=True)
        if dim != len(coords):
            return None
        self._took(el)
        return self._make_position(coords, dim, self._srs(el), el, xpath)

    def _positions(self, el: etree._Element, xpath: str) -> list[dict[str, Any]] | None:
        coords = self._coords(el, xpath)
        if coords is None:
            return None
        dim = self._dim(el, len(coords), single=False)
        if dim < 1 or len(coords) % dim:
            return None
        self._took(el)
        srs = self._srs(el)
        out = []
        for i in range(0, len(coords), dim):
            position = self._make_position(coords[i : i + dim], dim, srs, el, xpath)
            out.append(position)
        return out

    def _make_position(
        self,
        coords: list[float],
        dim: int,
        srs: str | None,
        el: etree._Element,
        xpath: str,
    ) -> dict[str, Any]:
        geographic = None
        horizontal = None if srs is None else _crs_of(srs).horizontal
        geographic_crs = horizontal is not None and (horizontal.type or "").startswith("geographic")
        if horizontal is not None and geographic_crs and dim >= 2:  # noqa: PLR2004
            lon_first = (horizontal.axis_order or "lat,lon").startswith("lon")
            lat, lon = (coords[1], coords[0]) if lon_first else (coords[0], coords[1])
            if abs(lat) > 90 >= abs(lon):  # noqa: PLR2004 - latitude bound
                lat, lon = lon, lat
                record = self.axis.setdefault(
                    str(srs), [el.sourceline, xpath, coords[0], coords[1], lat, lon, 0]
                )
                record[6] += 1
            height = coords[2] if dim >= 3 else None  # noqa: PLR2004
            geographic = {"lon": lon, "lat": lat, "h": height, "datum": horizontal.datum}
        return {
            "coordinates": coords,
            "dim": dim,
            "crs": srs,
            "geographic": geographic,
            "transformed_by": None,
        }

    def _table(self, rs: etree._Element, xpath: str) -> dict[str, Any]:
        props = [p for p in rs.iter(etree.Element) if _local(p.tag).endswith("Property")]
        values_el = self._first(rs, "dataValues")
        tuples: list[str] = []
        cs = ","
        if values_el is not None:
            ts = values_el.get("ts") or " "
            cs = values_el.get("cs", ",") or ","
            body = (values_el.text or "").strip()
            if body:
                tuples = (
                    body.split()
                    if ts.isspace()
                    else [t.strip() for t in body.split(ts) if t.strip()]
                )
        rows = [t.split(cs) for t in tuples]
        width = len(props)
        if any(len(r) != width for r in rows):
            self.note(
                rules.CONTEXT_RULES[103][1],
                f"A result row has a different number of values than the {width} declared"
                " properties; short rows are padded with nulls, long rows cut.",
                line=rs.sourceline,
                xpath=xpath,
            )
        columns, unparsed, unread = self._columns(props, rows)
        if unparsed:
            self.note(
                rules.VALUE_UNPARSED,
                f"{unparsed} result value(s) are not numbers in numeric columns; read as null.",
                line=rs.sourceline,
                xpath=xpath,
            )
        self._took_values(values_el, rows, unread)
        holder = rs.getparent()
        holder = None if holder is None else holder.getparent()
        location = None if holder is None else _child(holder, "location")
        shape_el = None if location is None else next(_elements(location), None)
        shape = "none" if shape_el is None else _SHAPES.get(_local(shape_el.tag), "none")
        point = None
        if shape_el is not None and _local(shape_el.tag) == "PointLocation":
            pos = self._first(shape_el, "pos")
            point = None if pos is None else self._position(pos, xpath)
        index, extent, crs = self._index(shape_el, len(rows))
        time_domain = None if holder is None else _child(holder, "timeDomain")
        if not index and time_domain is not None:
            index = _time_index(time_domain, len(rows), self._took)
            if index:
                shape = "temporal"
        if not self.results:
            for column in index:
                column["values"] = []
        return {
            "domain": {"shape": shape, "index": index, "crs": crs},
            "columns": columns,
            "row_count": len(rows),
            "location": point,
            "extent": extent,
        }

    def _columns(
        self, props: list[etree._Element], rows: list[list[str]]
    ) -> tuple[list[dict[str, Any]], int, list[set[int]]]:
        """The columns of a result set, how many values they could not read, and where.

        Each column is given only the values the rows hold for it, with the rows they
        are in: a short row reaches only its first columns, so the work is that of the
        values written, not of the declared columns times the rows. A row's missing
        values are nulls, filled in at once (see :func:`_spread`). The third value has,
        per column, the rows of the values it read as null (only when the content the
        model lacks is collected).

        Those nulls are counted across the read, before any is made: past the bound
        (see :data:`PADDED_CELLS_BASE`) the read stops (``padded_cells``). Without the
        values (``results=False``) none is made, and none is counted.
        """
        width = len(props)
        if self.results:
            written = sum(min(len(row), width) for row in rows)
            self.padded_cells += width * len(rows) - written
            if self.padded_cells > max(PADDED_CELLS_BASE, PADDED_CELLS_PER_BYTE * len(self.data)):
                raise _LimitError("padded_cells")
        at: list[list[int]] = [[] for _ in props]
        held: list[list[str]] = [[] for _ in props]
        for n, row in enumerate(rows):
            for i, raw in enumerate(row[:width]):
                at[i].append(n)
                held[i].append(raw.strip())
        columns = []
        unparsed = 0
        unread: list[set[int]] = []
        for i, prop in enumerate(props):
            column, bad = self._column(prop, held[i])
            unparsed += bad
            parsed = column["values"]
            if self._masks is not None:
                read = zip(at[i], held[i], parsed, strict=True)
                unread.append({n for n, cell, value in read if cell and value is None})
            column["values"] = _spread(parsed, at[i], len(rows)) if self.results else []
            columns.append(column)
            self._tick()  # a table of many columns is work of its own
        return columns, unparsed, unread

    def _took_values(
        self,
        values_el: etree._Element | None,
        rows: list[list[str]],
        unread: list[set[int]],
    ) -> None:
        """Note that the columns hold the values of a result set, and which they do not.

        The columns hold every value but those they could not: a value read as null (not
        a number in a numeric column, not a boolean in a boolean one; ``unread`` has
        their rows, per column) and a value beyond the declared columns, empty or not (a
        separator after the last value is one more). Those stay as they are written,
        each with its row and column.
        """
        if values_el is None or self._masks is None:
            return
        self._took(values_el)
        self._spelled.add(values_el)
        width = len(unread)
        cells = (
            (n + 1, i + 1, raw.strip())
            for n, row in enumerate(rows)
            for i, raw in enumerate(row)
            if i >= width or n in unread[i]
        )
        # A row with fewer values than columns is padded with nulls: how many it has is
        # written down (as column 0), so that one more separator is one more value.
        short = ((n + 1, 0, str(len(row))) for n, row in enumerate(rows) if len(row) < width)
        every = chain(cells, short)
        lost = list(islice(every, self.limit + 1))  # more than that is over the limit
        if lost:
            self._cells[values_el] = lost

    def _pos_list(
        self, shape_el: etree._Element | None
    ) -> tuple[str, etree._Element, list[float]] | None:
        """A multi-point or extent location's ``posList`` and its numbers, if all parse."""
        local = None if shape_el is None else _local(shape_el.tag)
        pos_list = None if shape_el is None else self._first(shape_el, "posList")
        if local not in ("MultiPointLocation", "LinearExtent") or pos_list is None:
            return None
        values = [_number(t) for t in (pos_list.text or "").split()]
        numbers = [v for v in values if v is not None]
        if not numbers or len(numbers) != len(values):
            return None
        return str(local), pos_list, numbers

    def _index(
        self, shape_el: etree._Element | None, rows: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
        """Index columns (one place per row) or a single extent, from a result location.

        A multi-point location with ``rows`` positions gives ``position`` columns; an
        extent with two values per row gives ``top`` and ``base``; an extent with two
        values for several rows, or one row, is the table's ``extent``.
        """
        found = self._pos_list(shape_el)
        if found is None:
            return [], None, None
        local, pos_list, numbers = found
        crs = self._srs(pos_list)
        labelled = self._inherited_by(pos_list, "uomLabels")
        labels = (labelled.get("uomLabels") or "").split() if labelled is not None else []
        if local == "MultiPointLocation":
            dim = self._dim(pos_list, 1, single=True)
            points = _point_index(numbers, dim, rows, labels)
            if points:
                self._took(pos_list)
                if len(labels) <= dim:
                    # Every label is the unit of one index column: the columns hold them.
                    self._took(labelled, "uomLabels")
            return points, None, crs
        uom = labels[0] if labels else None
        # One label is the unit of the top and the base below: they hold it, when they
        # are read. Otherwise nothing reads it, and it stays to compare.
        took = self._took if len(labels) == 1 else _not_noted
        if rows > 1 and len(numbers) == 2 * rows:
            self._took(pos_list)
            took(labelled, "uomLabels")
            return (
                [
                    _index_column("top", numbers[0::2], uom),
                    _index_column("base", numbers[1::2], uom),
                ],
                None,
                crs,
            )
        if len(numbers) == 2:  # noqa: PLR2004 - one top and base
            top, base = (
                {"text": t, "value": _number(t), "uom": None, "si": None}
                for t in (pos_list.text or "").split()
            )
            if uom:
                top.update(self._with_unit(top, uom))
                base.update(self._with_unit(base, uom))
            self._took(pos_list)
            took(labelled, "uomLabels")
            return [], {"top": top, "base": base, "lrs": crs}, crs
        return [], None, crs

    def _column(self, prop: etree._Element, cells: list[str]) -> tuple[dict[str, Any], int]:
        pclass = _child(prop, "propertyClass")
        code_space = None if pclass is None else pclass.get("codeSpace")
        key = _text(pclass)
        if code_space and "#" in code_space:
            key = code_space.rpartition("#")[2] or key
        declared_el = _child(prop, "typeData")
        declared = (_text(declared_el) or "").lower()
        uom_el = _child(prop, "uom")
        uom = _text(uom_el) or prop.get("uom")
        named, null = _child(prop, "propertyName"), _child(prop, "nullValue")
        # What the column holds as written: its name, unit, null value and definition,
        # and the class when that is its key. The declared type is read into a kind
        # (``double`` and ``float`` are both numbers), so it is not.
        for held in (named, null, pclass if key == _text(pclass) else None):
            self._took(held)
        self._took(pclass, "codeSpace")
        self._took(*((uom_el, "") if _text(uom_el) else (prop, "uom")))
        kind = _column_kind(declared, cells)
        if declared and declared_el is not None and self._masks is not None:
            self._declared[declared_el] = kind
        values: list[Any] = []
        bad = 0
        for cell in cells:
            if not cell:
                values.append(None)
            elif kind == "number":
                number = _number(cell)
                bad += number is None
                values.append(number)
            elif kind == "boolean":
                values.append(
                    {"true": True, "1": True, "false": False, "0": False}.get(cell.lower())
                )
            else:
                values.append(cell)
        affine = _converter()._affine(uom) if uom and kind == "number" else None
        return (
            {
                "name": _text(named) or "",
                "key": key,
                "definition": code_space,
                "uom": uom,
                "type": kind,
                "null_value": _text(null),
                "values": values,
                "si_uom": None if affine is None else affine[0],
                "si_factor": None if affine is None else affine[1],
                "si_offset": None if affine is None else affine[2],
            },
            bad,
        )

    # --- resolution -----------------------------------------------------------------

    def _finish(self) -> dict[str, Any]:
        rename = self._final_keys()
        self.final_places = self._final_places(rename)
        self.final_marks = self._final_marks(rename)
        self.final_unread = {
            rename[key]: tuple(nodes) for key, nodes in self.unread.items() if key in rename
        }
        root = self._root_left()
        if root is not None:
            self.final_unread[ROOT] = (root,)
        self.final_names = {
            rename[key]: dict(names) for key, names in self.names.items() if names and key in rename
        }
        links = self._links(rename)
        self._typed_refs(links)
        crs_table = self._crs()
        self._duplicate_ids()
        for name in ("document", *self.collections):
            self._fill_units(
                self.document if name == "document" else self.collections[name], crs_table
            )
        for srs, (line, xpath, first, second, lat, lon, count) in self.axis.items():
            more = f" {count} positions look the same." if count > 1 else ""
            self.note(
                rules.CONTEXT_RULES[105][1],
                f"Coordinates ({first:.6g}, {second:.6g}) under {srs[:120]} put a latitude"
                f" beyond 90 degrees in the CRS's axis order; they are read as latitude"
                f" {lat:.6g}, longitude {lon:.6g} in `geographic`.{more}",
                line=line,
                xpath=xpath,
            )
        from pydiggs import __version__  # noqa: PLC0415 - avoid an import cycle

        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "tool": {"name": "pydiggs", "version": __version__},
            "source": {
                "name": self.name,
                "sha256": hashlib.sha256(self.data).hexdigest(),
                "size": len(self.data),
                "diggs_version": self.version,
                "namespace": self.namespace,
                "detected": self.requested is None,
            },
            "complete": self.complete,
            "limits_hit": self.limits_hit,
            "document": self.document,
            **self.collections,
            "crs": crs_table,
            "links": links,
            "notes": [_finding_dict(n) for n in self.notes],
        }

    def _root_left(self) -> ExtraNode | None:
        """What the file holds outside every record, as one element named as its root.

        Its attributes (not its ``gml:id``, nor where its schema is) and the text outside
        every member, joined. Counted against the work limit like what records lack.
        """
        if self._masks is None or not (self._root_attrs or self._between):
            return None
        self.unread_count += 1
        if self.unread_count > self.limit:
            self.unread_complete = False
            return None
        return ExtraNode(
            qname=self._root_qname,
            ns=self.namespace,
            attrs=dict(self._root_attrs),
            text=" ".join(self._between) or None,
        )

    def _nodes(self) -> Iterator[dict[str, Any]]:
        if self.document is not None:
            yield self.document
        for nodes in self.collections.values():
            yield from nodes
        for observation in self.collections["observations"]:
            yield from observation["layers"]
        yield from self.components

    def _final_keys(self) -> dict[str, str]:
        rename: dict[str, str] = {}
        taken: set[str] = set()
        nodes = list(self._nodes())
        for node in nodes:
            gml_id = node["gml_id"]
            if (
                gml_id
                and not gml_id.startswith("@")
                and self.id_count[gml_id] == 1
                and gml_id not in taken
            ):
                final = gml_id
            else:
                final = "@" + normalise_xpath(node["provenance"]["xpath"])
                if final in taken:
                    final = node["key"]
                base, n = final, 1
                while final in taken:  # only when a gml:id mimics an xpath key
                    n += 1
                    final = f"{base}#{n}"
            taken.add(final)
            rename[node["key"]] = final
            node["key"] = final
        return rename

    def _final_places(self, rename: dict[str, str]) -> dict[str, tuple[str, str]]:
        """The indexed ids that name one element for certain, with the final key of its record.

        An id that two elements have names neither. One the model keeps in ``extra`` is
        left out too: the model has the element itself, which says more than its place.
        """
        return {
            gml_id: (rename[owner], place)
            for gml_id, (owner, place) in (self.id_places or {}).items()
            if self.id_count[gml_id] == 1 and gml_id not in self.extra_ids and owner in rename
        }

    def _final_marks(self, rename: dict[str, str]) -> dict[str, tuple[tuple[str, str], ...]]:
        """The marked ids, each with every element that has it, by the final key of its record.

        An id two elements have names them both: where each sits is part of what a
        reference to it means. Every element with the id but the first is marked. The
        first may have a place, and is then listed first; or it is marked itself; or
        the model keeps it in ``extra``, where the comparison finds it.
        """
        out: dict[str, tuple[tuple[str, str], ...]] = {}
        for gml_id, marks in self.id_marks.items():
            first = (self.id_places or {}).get(gml_id)
            every = [*([(first[0], f"at:{first[1]}")] if first else []), *marks]
            if all(owner in rename for owner, _ in every):
                out[gml_id] = tuple((rename[owner], mark) for owner, mark in every)
        return out

    def _links(self, rename: dict[str, str]) -> list[dict[str, Any]]:
        links = []
        remote_seen: set[str] = set()
        for owner, relation, href, line, xpath in self.raw_links:
            target = None
            if href.startswith("#") and len(href) > 1:
                holder = self.id_owner.get(href[1:])
                if holder is None:
                    self.note(
                        rules.CONTEXT_RULES[102][1],
                        f'xlink:href "{href[:120]}" does not resolve to a gml:id in this file.',
                        line=line,
                        xpath=xpath,
                    )
                else:
                    target = rename[holder]
            elif _geo_checks._REMOTE.match(href) and not _geo_checks._CODE_LISTS.match(href):
                document = href.split("#", 1)[0]
                if document not in remote_seen:
                    remote_seen.add(document)
                    self.note(
                        rules.CONTEXT_RULES[107][1],
                        f'Links into "{document[:120]}" are not followed; pyDIGGS never'
                        " fetches other documents.",
                        line=line,
                        xpath=xpath,
                    )
            links.append(
                {
                    "source": rename[owner],
                    "relation": relation,
                    "target": target,
                    "href": href,
                    "resolved": target is not None,
                }
            )
        return links

    def _typed_refs(self, links: list[dict[str, Any]]) -> None:
        targets: dict[tuple[str, str], list[str]] = {}
        for link in links:
            if link["target"] is not None:
                targets.setdefault((link["source"], link["relation"]), []).append(link["target"])
        for node in self._nodes():
            key = node["key"]
            for field, relation in _TYPED_REFS:
                if field in node:
                    found = targets.get((key, relation), [])
                    node[field] = next(iter(found), None)
            if "samples" in node:
                node["samples"] = list(dict.fromkeys(targets.get((key, "sample"), [])))
        self._copy_from_activities()

    def _copy_from_activities(self) -> None:
        """A sample's depth, method and recovery are recorded on its sampling activity."""
        activities = {a["key"]: a for a in self.collections["sampling_activities"]}
        for sample in self.collections["samples"]:
            activity = activities.get(sample["sampling_activity"] or "")
            if activity is None:
                continue
            for name in ("interval", "method", "recovery"):
                if sample[name] is None:
                    sample[name] = activity[name]
            if sample["feature"] is None:
                sample["feature"] = activity["feature"]

    def _duplicate_ids(self) -> None:
        """One note per duplicated gml:id (at most ``_MAX_ID_NOTES``, then a summary)."""
        repeated = list(self.id_repeat.items())
        for gml_id, line in repeated[:_MAX_ID_NOTES]:
            self.note(
                rules.DUPLICATE_GML_ID,
                f'gml:id "{gml_id[:120]}" is used by {self.id_count[gml_id]} elements; each'
                " is keyed by its xpath, and a link to it resolves to the first.",
                line=line,
                xpath=None,
            )
        if len(repeated) > _MAX_ID_NOTES:
            self.note(
                rules.DUPLICATE_GML_ID,
                f"{len(repeated) - _MAX_ID_NOTES} more gml:ids are used more than once.",
                line=None,
                xpath=None,
            )

    def _crs(self) -> dict[str, Any]:
        table: dict[str, Any] = {}
        ids = frozenset(self.id_count)
        for srs, (xpath, line) in self.srs_first.items():
            ref = crs.parse(srs, local_ids=ids)
            if ref.form == "unknown" or (ref.form == "local" and not ref.known):
                self.note(
                    rules.CONTEXT_RULES[106][1],
                    f'srsName "{srs[:120]}" is not a recognised EPSG or DIGGS reference nor'
                    " the gml:id of an element in this file.",
                    line=line,
                    xpath=xpath,
                )
            entry = _encode(ref)
            if ref.form == "local" and ref.local in self.local_crs:
                entry["name"], entry["unit"] = self.local_crs[ref.local]
            table[srs] = entry
        return table

    def _fill_units(self, value: Any, table: dict[str, Any]) -> None:
        """Give depths along a linear referencing system the unit it states.

        DIGGS writes an interval's top and base, and a table's index, as bare numbers
        in the units of the system they refer to; the unit is copied onto them.
        """
        if isinstance(value, list):
            for item in value:
                self._fill_units(item, table)
            return
        if not isinstance(value, dict):
            return
        if value.keys() >= _INTERVAL_KEYS:
            self._interval_units(value, _local_unit(table.get(value["lrs"] or "")))
            return
        if value.keys() >= _TABLE_KEYS:
            _index_units(value["index"], _local_unit(table.get(value["crs"] or "")))
            return
        for name, item in value.items():
            # A column's values are cells (numbers, text, booleans, nulls), never an
            # interval or a table, and there are rows times columns of them: not walked.
            if name not in ("params", "extra", "values") and isinstance(item, dict | list):
                self._fill_units(item, table)

    def _interval_units(self, interval: dict[str, Any], unit: str | None) -> None:
        for end in (interval["top"], interval["base"]):
            if unit and isinstance(end, dict) and end.get("uom") is None:
                end.update(self._with_unit(end, unit))

    @staticmethod
    def _with_unit(quantity: dict[str, Any], unit: str) -> dict[str, Any]:
        value = quantity["value"]
        si = None
        base = _converter().base_unit(unit)
        if value is not None and base is not None:
            converted = _converter().to_base(value, unit)
            if converted is not None and math.isfinite(converted):
                si = {"value": converted, "uom": base}
        return {"uom": unit, "si": si}


@lru_cache(maxsize=1024)
def _crs_of(srs: str) -> CrsRef:
    """What an ``srsName`` names, from the name alone (cached, bounded: files repeat them)."""
    return crs.parse(srs)


def _field_locations(el: etree._Element, rest: list[etree._Element]) -> dict[str, dict[str, Any]]:
    """Where each child ``el`` read a value from: every child not kept in ``extra``."""
    kept = set(rest)
    steps: Counter[str] = Counter()
    names: Counter[str] = Counter()
    fields: dict[str, dict[str, Any]] = {}
    for child in _elements(el):
        step = _qname(child)
        steps[step] += 1
        if child in kept:
            continue
        local = _local(child.tag)
        names[local] += 1
        key = local if names[local] == 1 else f"{local}[{names[local]}]"
        fields[key] = {"xpath": f"{step}[{steps[step]}]", "line": child.sourceline}
    return fields


#: In what is left of an element (:meth:`_Reader._left`): the digest of what typed fields
#: hold of it, and the text after its children and comments. Neither can be the name of
#: an attribute of a file; ``pydiggs.diff`` knows both.
HELD: Final = "\x00held"
TAIL: Final = "#tail"
#: The key, in what records lack, of what the file holds outside every record (the
#: attributes of its root element and the text between its members). No record has it:
#: a key is a ``gml:id`` or starts with ``@``, and no XML text holds ``\x00``.
ROOT: Final = "\x00root"
#: On the ``dataValues`` whose values the columns hold: the attributes that only say how
#: those values are written (separators, decimal mark). The columns hold what the values
#: read as, so another spelling that reads the same values is no change.
SPELLED: Final = "\x00spelled"
#: On a ``typeData`` a column's kind was read from: that kind (``number``, ``boolean``,
#: ``datetime``, ``string``). The column says what its values read as; the text says how
#: the type was declared.
DECLARED: Final = "\x00declared"
#: The attributes of a ``dataValues`` that say how its values are written.
_SEPARATORS: Final = ("cs", "ts", "decimal")


def _digest_of(value: object) -> str:
    return hashlib.blake2b(repr(value).encode("utf-8", "surrogatepass"), digest_size=12).hexdigest()


def _tails(root: etree._Element) -> str:
    """The text after each child, comment and processing instruction inside ``root``."""
    return " ".join(
        tail
        for node in root.iter()
        if node is not root and node.tail and (tail := node.tail.strip())
    )


#: Longest path of element names kept as it is (see :func:`_short`).
_MAX_NAMES: Final = 600


def _short(path: str) -> str:
    """``path``, or its start, a digest of all of it and its end when it is very long."""
    if len(path) <= _MAX_NAMES:
        return path
    digest = hashlib.blake2b(path.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()
    return f"{path[:150]}…#{digest}…{path[-300:]}"


def _held(el: etree._Element, below: list[str | None]) -> str:
    """A digest of what an element holds: its name, attributes, text and children.

    Its own ``gml:id`` is left out, and so is the order of its children, as everywhere
    in a model. ``below`` is the digest of each child.
    """
    record = (
        _local(el.tag),
        etree.QName(el).namespace or "",
        sorted((str(name), str(value)) for name, value in el.attrib.items() if name != _GML_ID),
        (el.text or "").strip(),
        sorted(item or "" for item in below),
    )
    return hashlib.blake2b(
        repr(record).encode("utf-8", "surrogatepass"), digest_size=16
    ).hexdigest()


def _local_crs(el: etree._Element) -> tuple[str | None, str | None]:
    """A CRS defined in the file: its name and the units it states."""
    name = _text(next((e for e in _elements(el) if _local(e.tag) == "name"), None))
    method = next(
        (e for e in el.iter(etree.Element) if _local(e.tag) == "LinearReferencingMethod"), None
    )
    units = None if method is None else _text(_child(method, "units"))
    if units is None:  # a method from the DIGGS registry: <lrm xlink:href="…lrm.xml#md_ft"/>
        lrm = next((e for e in el.iter(etree.Element) if _local(e.tag) == "lrm"), None)
        href = None if lrm is None else lrm.get(_XLINK_HREF)
        units = None if href is None else crs._lrm_units(href)
    if name is None and method is not None:
        name = _text(_child(method, "name"))
    return name, units


def _index_units(columns: list[dict[str, Any]], unit: str | None) -> None:
    if not unit:
        return
    affine = _converter()._affine(unit)
    for column in columns:
        if column["uom"] is None and column["type"] == "number":
            column["uom"] = unit
            if affine is not None:
                column.update(si_uom=affine[0], si_factor=affine[1], si_offset=affine[2])


def _local_unit(entry: dict[str, Any] | None) -> str | None:
    if entry is None or entry.get("form") != "local":
        return None
    unit = entry.get("unit")
    return unit if isinstance(unit, str) else None


def _finding_dict(finding: Finding) -> dict[str, Any]:
    from pydiggs.findings._json import finding_entry  # noqa: PLC0415

    return finding_entry(finding)


def read_document(
    data: bytes,
    *,
    name: str | None,
    diggs_version: str | None,
    results: bool,
    time_budget: float | None,
    field_provenance: bool = False,
    should_stop: Callable[[], bool] | None = None,
    id_places: dict[str, tuple[str, str]] | None = None,
    crs_defined: Counter[tuple[str | None, str | None]] | None = None,
    id_marks: dict[str, tuple[tuple[str, str], ...]] | None = None,
    unread: dict[str, tuple[ExtraNode, ...]] | None = None,
    names: dict[str, dict[str, int]] | None = None,
) -> Model:
    """Read checked bytes into a model (the shared core of ``read`` and ``read_bytes``).

    ``should_stop`` is the adapters' cancellation hook: checked between members and
    every so often inside one; when it returns True the read ends with ``_Cancelled``.

    ``id_places``, when given, is filled in the same pass with what the model does not
    keep: for the ``gml:id`` of each element a typed field read (a centre line, a
    reference point), the key of the record that holds the element and the element's
    path below it. Only ids that name one element for certain are in it (see
    :meth:`_Reader._place`); ``pydiggs.diff`` compares a reference to such an element by
    it. ``id_marks`` is filled with the ids of such elements that could not be placed
    that way, each with the record, position and content of every element that has it
    (see :meth:`_Reader._mark`). ``unread`` is filled with what each record holds in the
    file and not in the model, by the record's key (see :meth:`_Reader._left`), and with
    what the file holds outside every record under the key :data:`ROOT`; the key ``""``
    is there, with nothing, when a work limit cut that short. ``names`` is filled
    with how many elements each record has that typed fields hold in full, by the names
    of the elements from the record down to them. ``crs_defined`` is
    filled with how many coordinate systems the file defines with each name and unit,
    referenced or not (the model's ``crs`` table has only the referenced ones). The
    model is the same with or without.
    """
    deadline = None if time_budget is None else time.monotonic() + time_budget
    reader = _Reader(
        data,
        name=name,
        diggs_version=diggs_version,
        results=results,
        field_provenance=field_provenance,
        index_ids=id_places is not None,
    )
    model = reader.run(deadline, should_stop)
    if id_places is not None:
        id_places.update(reader.final_places)
    if id_marks is not None:
        id_marks.update(reader.final_marks)
    if unread is not None:
        unread.update(reader.final_unread)
        if not reader.unread_complete:
            unread[""] = ()
    if names is not None:
        names.update(reader.final_names)
    if crs_defined is not None:
        crs_defined.update(reader.crs_defined)
    return model
