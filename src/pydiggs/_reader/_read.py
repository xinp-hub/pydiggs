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
from typing import Any, Final

from lxml import etree

from pydiggs import _geo_checks, _xml, crs
from pydiggs.detect import NAMESPACE_PROFILES
from pydiggs.errors import DocumentError
from pydiggs.findings import rules
from pydiggs.findings.keys import normalise_xpath
from pydiggs.findings.model import Finding, Severity, Step
from pydiggs.model._model import MODEL_SCHEMA_VERSION, Model, _encode
from pydiggs.model._values import CrsRef
from pydiggs.units import UnitConverter

__all__ = ["read_document"]

_GML: Final = "http://www.opengis.net/gml/3.2"
_GML_ID: Final = f"{{{_GML}}}id"
_XLINK_HREF: Final = "{http://www.w3.org/1999/xlink}href"
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
#: Duplicated gml:ids noted one by one before the rest are summarised.
_MAX_ID_NOTES: Final = 100
_FLOAT: Final = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


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


def _time_index(domain: etree._Element, rows: int) -> list[dict[str, Any]]:
    """The time of each row, from a result's ``timeDomain``, when it has one per row."""
    obj = next(_elements(domain), None)
    if obj is None or rows == 0:
        return []
    local = _local(obj.tag)
    if local == "TimeIntervalList":
        values = [_number(t) for t in (_text(_child(obj, "timeIntervalList")) or "").split()]
        if len(values) != rows or any(v is None for v in values):
            return []
        unit = obj.get("unit")
        return [_index_column("elapsed_time", values, _TIME_UNITS.get(unit or "", unit))]
    if local == "TimePositionList":
        times = (_text(_child(obj, "timePositionList")) or "").split()
    elif local == "MultiTimeInstant":
        times = [
            t
            for e in obj.iter(etree.Element)
            if _local(e.tag) == "timePosition" and (t := _text(e)) is not None
        ]
    else:
        return []
    if len(times) != rows:
        return []
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


def _code(el: etree._Element | None) -> dict[str, Any] | None:
    text = _text(el)
    if el is None or text is None:
        return None
    return {"value": text, "code_space": el.get("codeSpace")}


def _codes(lithology: etree._Element) -> list[dict[str, Any]]:
    """A lithology's classification codes, in document order."""
    names = {*_CLASSIFICATION, "lithologyClassification"}
    return [
        code
        for e in _elements(lithology)
        if _local(e.tag) in names and (code := _code(e)) is not None
    ]


def _method(holder: etree._Element | None) -> dict[str, Any] | None:
    """A method property as a code: the specification's name, or the property's text."""
    if holder is None:
        return None
    named = next((e for e in holder.iter(etree.Element) if _local(e.tag) == "name"), None)
    source = named if named is not None and _text(named) else holder
    text = _text(source)
    return None if text is None else {"value": text, "code_space": source.get("codeSpace")}


def _time(holder: etree._Element) -> dict[str, Any] | None:
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
            elif name in ("end", "endPosition") and end is None:
                end = _text(child)
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

    def __init__(
        self,
        data: bytes,
        *,
        name: str | None,
        diggs_version: str | None,
        results: bool,
        field_provenance: bool = False,
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
        self.id_owner: dict[str, str] = {}
        self.id_count: Counter[str] = Counter()
        self.id_repeat: dict[str, int | None] = {}  # duplicated id -> line of its 2nd use
        self.raw_links: list[tuple[str, str, str, int | None, str]] = []
        self.srs_first: dict[str, tuple[str, int | None]] = {}
        self.axis: dict[str, list[Any]] = {}
        self.notes: list[Finding] = []
        self.complete = True
        self.limits_hit: list[str] = []

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

    def run(self, deadline: float | None) -> Model:
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
            self._stream(deadline)
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

    def _stream(self, deadline: float | None) -> None:
        parser = etree.XMLPullParser(
            events=("start", "end"),
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
                if event == "start":
                    depth += 1
                    if depth == 1:
                        root = el
                        root_step = self._root(el)
                    continue
                if depth == 2 and root is not None and isinstance(el.tag, str):  # noqa: PLR2004
                    key = (el.prefix, _local(el.tag))
                    counts[key] += 1
                    self._member(el, f"/{root_step}/{_qname(el)}[{counts[key]}]")
                    el.clear()
                    while el.getprevious() is not None:
                        del root[0]
                    if deadline is not None and time.monotonic() > deadline:
                        self._stop(el)
                        return
                depth -= 1
        parser.close()
        if root is None:
            raise etree.XMLSyntaxError("Document is empty", 0, 1, 1)

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
        return f"{_qname(el)}[1]"

    def _stop(self, el: etree._Element) -> None:
        self.complete = False
        self.limits_hit.append("time_budget")
        self.note(
            rules.TIMEOUT,
            "Reading stopped at its time budget; members after this point were not read.",
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
            self._scan(prop, xpath, node["key"], nested)
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
        self._scan(prop, xpath, owner, nested)

    def _scan(
        self,
        el: etree._Element,
        xpath: str,
        owner: str,
        nested: dict[etree._Element, str],
    ) -> None:
        """Record every id, link and srsName in ``el``'s subtree, with its owning node."""
        owner = nested.get(el, owner)
        gml_id = el.get(_GML_ID)
        if gml_id:
            self.id_owner.setdefault(gml_id, owner)
            self.id_count[gml_id] += 1
            if self.id_count[gml_id] == 2:  # noqa: PLR2004 - the first repeat
                self.id_repeat[gml_id] = el.sourceline
        href = el.get(_XLINK_HREF)
        if href is not None:
            relation = _local(el.tag)
            relation = relation.removesuffix("Ref") or relation
            self.raw_links.append((owner, relation, href.strip(), el.sourceline, xpath))
        srs = el.get("srsName")
        if srs and srs not in self.srs_first:
            self.srs_first[srs] = (xpath, el.sourceline)
        if gml_id and _local(el.tag) in _LOCAL_CRS:
            self.local_crs.setdefault(gml_id, _local_crs(el))
        counts: Counter[tuple[str | None, str]] = Counter()
        for child in _elements(el):
            key = (child.prefix, _local(child.tag))
            counts[key] += 1
            self._scan(child, f"{xpath}/{_qname(child)}[{counts[key]}]", owner, nested)

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
        element = _local(el.tag)
        element = _aliases().get((self.version, element), element)
        names = [t for c in _elements(el) if _local(c.tag) == "name" and (t := _text(c))]
        remarks = [
            t
            for c in _elements(el)
            if _local(c.tag) == "remark"
            for content in c.iter(f"{{{etree.QName(c).namespace}}}content")
            if (t := _text(content))
        ]
        skip = {"name", "description", "remark", *consumed}
        params, rest = self._params(el, xpath, skip)
        return {
            "key": "@" + xpath,
            "gml_id": el.get(_GML_ID),
            "element": element,
            "kind": kind or _kind(element),
            "name": names[0] if names else None,
            "names": names,
            "description": _text(_child(el, "description")),
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
        return text if code_space is None else {"value": text, "code_space": code_space}

    def _extra(self, el: etree._Element) -> dict[str, Any]:
        return {
            "qname": _qname(el),
            "ns": etree.QName(el).namespace,
            "attrs": {_attr_name(el, str(k)): str(v) for k, v in el.attrib.items()},
            "text": _text(el),
            "children": [self._extra(c) for c in _elements(el)],
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
            elif local == "PointLocation" and location_prop is None:
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
            time=None if time_prop is None else _time(time_prop),
            components=children,
        )
        nested[el] = node["key"]
        self.components.append(node)
        return node

    def _map_document(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="document_info", consumed=frozenset({"creationDate"}))
        node["creation_date"] = _text(_child(obj, "creationDate"))
        return node

    def _map_projects(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="project")
        locality = _child(obj, "locality")
        place = None if locality is None else next(_elements(locality), None)
        node["client"] = None
        node["locality"] = (
            None
            if place is None
            else (_text(_child(place, "description")) or _text(_child(place, "name")))
        )
        return node

    def _map_associates(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="associate")
        node["roles"] = [
            {"value": t, "code_space": r.get("codeSpace")}
            for r in obj.iter(etree.Element)
            if _local(r.tag) == "rolePerformed" and (t := _text(r))
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
        node["when_constructed"] = None if constructed is None else _time(constructed)
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
        node["method"] = _method(_child(obj, "samplingMethod"))
        recovery = _child(obj, "totalSampleRecoveryLength")
        node["recovery"] = None if recovery is None else self._quantity(recovery, xpath)
        date = _child(obj, "samplingDate")
        node["date"] = None if date is None else _time(date)
        return node

    def _map_samples(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(
            obj, xpath, kind="sample", consumed=frozenset({"samplingLocation", "classification"})
        )
        node.update(feature=None, sampling_activity=None, method=None, recovery=None)
        node["interval"] = self._interval(_child(obj, "samplingLocation"))
        classification = _child(obj, "classification")
        text = _text(classification)
        node["type"] = (
            None
            if classification is None or text is None
            else {"value": text, "code_space": classification.get("codeSpace")}
        )
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
        node["geologic_unit"] = _text(_child(el, "unitName"))
        holder = _child(el, "primaryLithology")
        primary = None if holder is None else _object(holder)
        node["classification"] = [] if primary is None else _codes(primary)
        if node["description"] is None and primary is not None:
            node["description"] = _text(_child(primary, "lithDescription"))
        colours = [] if primary is None else list(primary.iter(etree.Element))
        node["colour"] = next(
            (
                code
                for name in ("colorCode", "colorName")
                for e in colours
                if _local(e.tag) == name and (code := _code(e)) is not None
            ),
            None,
        )
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
            result_time=None if result_time is None else _time(result_time),
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
        top, base = ({"text": t, "value": _number(t), "uom": None, "si": None} for t in tokens)
        return {"top": top, "base": base, "lrs": srs}

    @staticmethod
    def _first(el: etree._Element, local: str) -> etree._Element | None:
        return next((e for e in el.iter(etree.Element) if _local(e.tag) == local), None)

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
        return self._inherited(el, "srsName")

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
        return self._make_position(coords, dim, self._srs(el), el, xpath)

    def _positions(self, el: etree._Element, xpath: str) -> list[dict[str, Any]] | None:
        coords = self._coords(el, xpath)
        if coords is None:
            return None
        dim = self._dim(el, len(coords), single=False)
        if dim < 1 or len(coords) % dim:
            return None
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
        columns = []
        unparsed = 0
        for i, prop in enumerate(props):
            cells = [r[i].strip() if i < len(r) else "" for r in rows]
            column, bad = self._column(prop, cells)
            unparsed += bad
            columns.append(column)
        if unparsed:
            self.note(
                rules.VALUE_UNPARSED,
                f"{unparsed} result value(s) are not numbers in numeric columns; read as null.",
                line=rs.sourceline,
                xpath=xpath,
            )
        if not self.results:
            for column in columns:
                column["values"] = []
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
            index = _time_index(time_domain, len(rows))
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
        labels = (self._inherited(pos_list, "uomLabels") or "").split()
        if local == "MultiPointLocation":
            dim = self._dim(pos_list, 1, single=True)
            return _point_index(numbers, dim, rows, labels), None, crs
        uom = labels[0] if labels else None
        if rows > 1 and len(numbers) == 2 * rows:
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
            return [], {"top": top, "base": base, "lrs": crs}, crs
        return [], None, crs

    def _column(self, prop: etree._Element, cells: list[str]) -> tuple[dict[str, Any], int]:
        pclass = _child(prop, "propertyClass")
        code_space = None if pclass is None else pclass.get("codeSpace")
        key = _text(pclass)
        if code_space and "#" in code_space:
            key = code_space.rpartition("#")[2] or key
        declared = (_text(_child(prop, "typeData")) or "").lower()
        uom = _text(_child(prop, "uom")) or prop.get("uom")
        if declared in _NUMBER_TYPES:
            kind = "number"
        elif declared in _BOOLEAN_TYPES:
            kind = "boolean"
        elif declared in _DATETIME_TYPES:
            kind = "datetime"
        elif declared:
            kind = "string"
        else:
            kind = (
                "number"
                if all(_number(c) is not None for c in cells if c) and any(cells)
                else "string"
            )
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
                "name": _text(_child(prop, "propertyName")) or "",
                "key": key,
                "definition": code_space,
                "uom": uom,
                "type": kind,
                "null_value": _text(_child(prop, "nullValue")),
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
        if {"top", "base", "lrs"} <= value.keys():
            self._interval_units(value, _local_unit(table.get(value["lrs"] or "")))
            return
        if {"shape", "index", "crs"} <= value.keys():
            _index_units(value["index"], _local_unit(table.get(value["crs"] or "")))
            return
        for name, item in value.items():
            if name not in ("params", "extra") and isinstance(item, dict | list):
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
) -> Model:
    """Read checked bytes into a model (the shared core of ``read`` and ``read_bytes``)."""
    deadline = None if time_budget is None else time.monotonic() + time_budget
    reader = _Reader(
        data,
        name=name,
        diggs_version=diggs_version,
        results=results,
        field_provenance=field_provenance,
    )
    return reader.run(deadline)
