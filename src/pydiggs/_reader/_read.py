"""Read DIGGS bytes into a :class:`~pydiggs.model.Model` (the preview read API).

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
from collections.abc import Callable, Iterator
from functools import cache
from importlib import resources
from typing import Any, Final

from lxml import etree

from pydiggs import _geo_checks, _xml
from pydiggs.detect import NAMESPACE_PROFILES
from pydiggs.errors import DocumentError
from pydiggs.findings import rules
from pydiggs.findings.keys import normalise_xpath
from pydiggs.findings.model import Finding, Severity, Step
from pydiggs.model._model import MODEL_SCHEMA_VERSION, Model
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
_NUMBER_TYPES: Final = frozenset(
    {"integer", "int", "double", "decimal", "float", "number", "long", "short", "real"}
)
_BOOLEAN_TYPES: Final = frozenset({"boolean", "bool"})
_DATETIME_TYPES: Final = frozenset({"datetime", "date", "time", "gyear"})
_CAMEL: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FLOAT: Final = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _local(tag: object) -> str:
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


def _attr_name(el: etree._Element, name: str) -> str:
    if not name.startswith("{"):
        return name
    ns, _, local = name[1:].partition("}")
    prefix = next((p for p, uri in el.nsmap.items() if uri == ns and p), None)
    return f"{prefix}:{local}" if prefix else name


class _Reader:
    """One read: the streaming pass, the per-member mappers and the final resolution."""

    def __init__(
        self, data: bytes, *, name: str | None, diggs_version: str | None, results: bool
    ) -> None:
        self.data = data
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
        self.id_owner: dict[str, str] = {}
        self.id_count: Counter[str] = Counter()
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
            node = self._header(prop, xpath, kind="reference")
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
        href = el.get(_XLINK_HREF)
        if href is not None:
            relation = _local(el.tag)
            relation = relation.removesuffix("Ref") or relation
            self.raw_links.append((owner, relation, href.strip(), el.sourceline, xpath))
        srs = el.get("srsName")
        if srs and srs not in self.srs_first:
            self.srs_first[srs] = (xpath, el.sourceline)
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
        return {
            "key": "@" + xpath,
            "gml_id": el.get(_GML_ID),
            "element": element,
            "kind": kind or _kind(element),
            "name": names[0] if names else None,
            "names": names,
            "description": _text(_child(el, "description")),
            "remarks": remarks,
            "provenance": {"xpath": xpath, "line": el.sourceline},
            "extra": [self._extra(c) for c in _elements(el) if _local(c.tag) not in skip],
        }

    def _extra(self, el: etree._Element) -> dict[str, Any]:
        return {
            "qname": _qname(el),
            "ns": etree.QName(el).namespace,
            "attrs": {_attr_name(el, str(k)): str(v) for k, v in el.attrib.items()},
            "text": _text(el),
            "children": [self._extra(c) for c in _elements(el)],
            "line": el.sourceline,
        }

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
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        consumed = frozenset({"referencePoint", "centerLine", "totalMeasuredDepth"})
        node = self._header(obj, xpath, consumed=consumed)
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
        node["construction"] = []
        node["water_strikes"] = []
        node["parent"] = None
        return node

    def _map_sampling_activities(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="sampling_activity")
        node["feature"] = None
        node["interval"] = self._interval(_child(obj, "samplingLocation"))
        return node

    def _map_samples(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, kind="sample")
        node.update(feature=None, sampling_activity=None, method=None, type=None, recovery=None)
        node["interval"] = self._interval(_child(obj, "samplingLocation"))
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
            for layer_el in _elements(prop):
                layer = self._layer(layer_el, f"{prop_xpath}/{_qname(layer_el)}[1]")
                nested[layer_el] = layer["key"]
                layers.append(layer)
        node["layers"] = layers
        return node

    def _layer(self, el: etree._Element, xpath: str) -> dict[str, Any]:
        node = self._header(el, xpath, kind="lithology_layer", consumed=frozenset({"location"}))
        node["interval"] = self._interval(_child(el, "location"))
        codes = [
            {"value": t, "code_space": item.get("codeSpace")}
            for item in el.iter(etree.Element)
            if _local(item.tag) in ("legendCode", "lithologyClassification") and (t := _text(item))
        ]
        node["classification"] = codes
        if node["description"] is None:
            node["description"] = next(
                (
                    t
                    for item in el.iter(etree.Element)
                    if _local(item.tag) == "lithDescription" and (t := _text(item))
                ),
                None,
            )
        node["colour"] = None
        node["geologic_unit"] = None
        return node

    def _map_measurements(
        self, obj: etree._Element, xpath: str, _nested: dict[etree._Element, str]
    ) -> dict[str, Any]:
        node = self._header(obj, xpath, consumed=frozenset({"procedure", "outcome"}))
        node.update(feature=None, samples=[], specimen=None, result_time=None)
        holders = [c for c in _elements(obj) if _local(c.tag) == "procedure"]
        procedure = None if not holders else next(_elements(holders[0]), None)
        node["procedure"] = None if procedure is None else self._procedure(procedure, xpath)
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

    def _procedure(self, el: etree._Element, xpath: str) -> dict[str, Any]:
        element = _local(el.tag)
        params: dict[str, Any] = {}
        extra = []
        for child in _elements(el):
            name = _local(child.tag)
            if any(True for _ in _elements(child)) or name in params or name == "name":
                if name != "name":
                    extra.append(self._extra(child))
                continue
            if child.get("uom") is not None:
                params[name] = self._quantity(child, xpath)
            else:
                params[name] = _text(child)
        return {
            "type": _aliases().get((self.version, element), element),
            "params": params,
            "extra": extra,
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
        if stated and stated.isdigit() and int(stated) > 0:
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
        ref = _srs_ref(srs) if srs else None
        if ref is not None and ref.horizontal in _geo_checks.GEOGRAPHIC_EPSG and dim >= 2:  # noqa: PLR2004
            lat, lon = coords[0], coords[1]
            if abs(lat) > 90 >= abs(lon):  # noqa: PLR2004 - latitude bound
                lat, lon = lon, lat
                record = self.axis.setdefault(
                    str(srs), [el.sourceline, xpath, coords[0], coords[1], 0]
                )
                record[4] += 1
            height = coords[2] if dim >= 3 else None  # noqa: PLR2004
            geographic = {"lon": lon, "lat": lat, "h": height, "datum": None}
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
            ts = values_el.get("ts", " ")
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
        return {
            "domain": {"shape": shape, "index": []},
            "columns": columns,
            "row_count": len(rows),
            "location": point,
        }

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
        affine = _converter().affine(uom) if uom and kind == "number" else None
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
        crs = self._crs()
        for srs, (line, xpath, first, second, count) in self.axis.items():
            more = f" {count} positions look the same." if count > 1 else ""
            self.note(
                rules.CONTEXT_RULES[105][1],
                f"Coordinates ({first:.6g}, {second:.6g}) under {srs[:120]} look like"
                f" longitude, latitude; they are read as latitude {second:.6g}, longitude"
                f" {first:.6g} in `geographic`.{more}",
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
            "crs": crs,
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

    def _final_keys(self) -> dict[str, str]:
        rename: dict[str, str] = {}
        taken: set[str] = set()
        nodes = list(self._nodes())
        for node in nodes:
            gml_id = node["gml_id"]
            if gml_id and self.id_count[gml_id] == 1 and gml_id not in taken:
                final = gml_id
            else:
                final = "@" + normalise_xpath(node["provenance"]["xpath"])
                if final in taken:
                    final = node["key"]
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
            if "feature" in node:
                node["feature"] = next(iter(targets.get((key, "samplingFeature"), [])), None)
            if "sampling_activity" in node:
                node["sampling_activity"] = next(
                    iter(targets.get((key, "samplingActivity"), [])), None
                )
            if "samples" in node:
                node["samples"] = list(dict.fromkeys(targets.get((key, "sample"), [])))
            if "specimen" in node:
                node["specimen"] = next(iter(targets.get((key, "specimen"), [])), None)

    def _crs(self) -> dict[str, Any]:
        table: dict[str, Any] = {}
        for srs, (xpath, line) in self.srs_first.items():
            ref = _srs_ref(srs)
            local = ref.local_id if ref.kind == "local" else None
            known = ref.kind in ("epsg", "diggs", "ogc") or (
                local is not None and local in self.id_count
            )
            if not known:
                self.note(
                    rules.CONTEXT_RULES[106][1],
                    f'srsName "{srs[:120]}" is not a recognised EPSG or DIGGS reference nor'
                    " the gml:id of an element in this file.",
                    line=line,
                    xpath=xpath,
                )
            geographic = ref.horizontal in _geo_checks.GEOGRAPHIC_EPSG
            table[srs] = {
                "srs_name": srs,
                "form": ref.kind,
                "horizontal": None
                if ref.horizontal is None
                else {
                    "epsg": ref.horizontal,
                    "name": None,
                    "type": "geographic" if geographic else None,
                    "unit": None,
                },
                "vertical": None
                if ref.vertical is None
                else {"epsg": ref.vertical, "name": None, "type": "vertical", "unit": None},
                "axis_order": "lat,lon" if geographic else None,
                "known": known,
                "local": local,
            }
        return table


def _srs_ref(srs: str) -> _geo_checks.SrsRef:
    ref = _geo_checks.parse_srs_name(srs)
    if ref.kind == "local" and ref.local_id and ref.local_id.isdigit():
        return _geo_checks.SrsRef("epsg", int(ref.local_id))
    return ref


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
) -> Model:
    """Read checked bytes into a model (the shared core of ``read`` and ``read_bytes``)."""
    deadline = None if time_budget is None else time.monotonic() + time_budget
    reader = _Reader(data, name=name, diggs_version=diggs_version, results=results)
    return reader.run(deadline)
