"""Build the lxml tree of a DIGGS document from a model, one node kind at a time.

Every element is made through lxml's API and every value is set as text or as an
attribute, so nothing from the model can become markup. Each object's children are
collected as *parts* (a child tag and a function that fills the new child) and appended
in the order the layout gives, which is the order the schema wants.

The writer is the inverse of the reader for typed fields, ``params``, components, links
and result tables. What the reader derives (keys, SI values, the CRS table, a sample's
depth copied from its sampling activity, a layer's shortcuts to its primary lithology) is
never written; where a derived field disagrees with what it is derived from, the model
is refused, so nothing is dropped silently.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from functools import cache, partial
from typing import Final

from lxml import etree

from pydiggs import _xml
from pydiggs._bundled import _DEFAULT_DICTIONARY
from pydiggs._writer._layout import ElementLayout, Ids, Layout
from pydiggs.errors import ResourceError
from pydiggs.model import (
    Code,
    Column,
    Component,
    DocumentInfo,
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
    Quantity,
    ResultTable,
    Sample,
    SamplingActivity,
    TimeSpan,
)

__all__ = ["build_tree"]

_XSI: Final = "http://www.w3.org/2001/XMLSchema-instance"
_GML_ID: Final = "{http://www.opengis.net/gml/3.2}id"
#: Prefixes declared on the root, in this order (``None`` is the default namespace).
_ROOT_PREFIXES: Final = ("diggs", "glr", "gml", "xlink")
#: ``xsi:schemaLocation`` of each target version: the published schema, as the examples.
_SCHEMA_LOCATION: Final = {
    "3.0.0": "http://diggsml.org/schemas/3 https://diggsml.org/schemas/3.0.0/Diggs.xsd",
}
#: Column type -> the ``typeData`` the reader maps back to it.
_TYPE_DATA: Final = {
    "number": "double",
    "string": "string",
    "boolean": "boolean",
    "datetime": "dateTime",
}
#: Typed reference fields and the link relation each is derived from (as the reader).
_TYPED_REFS: Final = (
    ("feature", "samplingFeature"),
    ("sampling_activity", "samplingActivity"),
    ("specimen", "specimen"),
    ("parent", "samplingFeature"),
)
#: The values of ``dataValues``: cells are separated by ``_CS``, rows by ``_TS``.
_CS: Final = ","
_TS: Final = " "
#: Lithology classification codes, in the order DIGGS lists them (as the reader).
_CLASSIFICATION: Final = ("classificationCode", "classificationSymbol", "legendCode")
#: Unit of a linear referencing system -> the DIGGS 3 name of its method (true distance
#: along the centerline). Other units have no name in ``LinearReferencingMethodNameEnum``.
_LRM_NAMES: Final = {
    "m": "trueMeters",
    "ft": "trueFootage",
    "cm": "trueCentimeters",
    "in": "trueInches",
}
_LRM_TYPE: Final = "absolute"
#: ``codeSpace`` of the identifiers the writer gives the reference systems it makes.
_ID_CODE_SPACE: Final = "urn:x-pydiggs:id"

Fill = Callable[[etree._Element], None]
Part = tuple[str, Fill]


def _where(node: Node) -> str:
    return f"{node.element} {node.key!r}"


def _checked(value: str, where: str) -> str:
    """``value`` when reading it back returns it unchanged (the reader strips text)."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            f"{where}: text {value!r:.80} is empty or has surrounding white space, which"
            " reading back would not return"
        )
    return value


def _token(value: str, where: str) -> str:
    """``value`` when it is one token of a white-space separated list."""
    if not isinstance(value, str) or not value or len(value.split()) != 1 or value != value.strip():
        raise ValueError(f"{where}: {value!r:.80} is not a single value without white space")
    return value


def _number(value: object, where: str) -> str:
    """A finite number in plain decimal or exponent form (what the reader parses)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{where}: {value!r:.80} is not a finite number")
    number = float(value)
    if number.is_integer() and abs(number) < 1e15:  # noqa: PLR2004 - exact as an integer
        return str(int(number))
    return repr(number)


def _set_text(text: str, where: str, el: etree._Element) -> None:
    el.text = _checked(text, where)


def _set_code(code: Code, where: str, el: etree._Element) -> None:
    el.text = _checked(code.value, where)
    if code.code_space is not None:
        el.set("codeSpace", code.code_space)


def _set_quantity(quantity: Quantity, where: str, el: etree._Element) -> None:
    el.text = _checked(quantity.text, where)
    if quantity.uom is not None:
        el.set("uom", quantity.uom)


def _set_param(value: Param, where: str, el: etree._Element) -> None:
    if isinstance(value, Quantity):
        if value.uom is None:
            raise ValueError(f"{where}: a quantity in params needs a uom (else it reads as text)")
        _set_quantity(value, where, el)
    elif isinstance(value, Code):
        if value.code_space is None:
            raise ValueError(f"{where}: a code in params needs a code_space (else it is text)")
        _set_code(value, where, el)
    else:
        _set_text(value, where, el)


class _Emitter:
    """One write: ``gml:id`` assignment, then the tree, member by member."""

    def __init__(self, model: Model, layout: Layout) -> None:
        self.model = model
        self.layout = layout
        self.ids = Ids()
        self.node_id: dict[str, str | None] = {}
        self.links: dict[str, list[Link]] = {}
        for link in model.links:
            self.links.setdefault(link.source, []).append(link)
        self.gml_id = layout.tag("gml:id")
        self.href = layout.tag("xlink:href")
        #: Feature key -> the id of its centerline (what its lrs is measured along).
        self.line_id: dict[str, str] = {}
        #: Sample key -> the id of the SampleProduced its sampling activity holds for it.
        self.produced_id: dict[str, str] = {}
        #: Sampling activity key -> the SampleProduced ids to write in it, in sample order.
        self.produced: dict[str, list[str]] = {}

    # --- ids ------------------------------------------------------------------------

    def _assign_ids(self) -> str:
        """Take every id the model gives, then make up the missing required ones."""
        nodes = list(self.model.nodes())
        for node in nodes:
            where = _where(node)
            if node.extra:
                raise ValueError(f"{where}: `extra` is not empty; this writer does not write it")
            if node.gml_id is not None:
                if self.layout.element(node.element).id is None:
                    raise ValueError(f"{where}: {node.element} takes no gml:id")
                self.ids.claim(node.gml_id, where)
        for feature in self.model.features:
            self._claim_lrs(feature)
        for sample in self.model.samples:
            given = self._own_link(sample, "sampleProduced", sample.sampling_activity)
            if given is not None:
                self.ids.claim(given, f"{_where(sample)} sampleProduced link")
                self.produced_id[sample.key] = given
        root_id = self.ids.new("Diggs")
        for node in nodes:
            made = None
            if node.gml_id is None and self.layout.element(node.element).id == "required":
                made = self.ids.new(node.element)
            self.node_id[node.key] = node.gml_id or made
        self._make_helper_ids()
        return root_id

    def _make_helper_ids(self) -> None:
        """Ids of the centerlines and ``SampleProduced`` elements no link has named."""
        for feature in self.model.features:
            if feature.centerline is not None and feature.key not in self.line_id:
                self.line_id[feature.key] = self.ids.new(f"{self._base(feature)}.centerLine")
        for sample in self.model.samples:
            activity = self.model.get(sample.sampling_activity or "")
            if not isinstance(activity, SamplingActivity):
                raise ValueError(
                    f"{_where(sample)}: DIGGS {self.layout.diggs_version} requires a sample"
                    " to name its sampling activity"
                )
            if sample.key not in self.produced_id:
                made_id = self.ids.new(f"{self._base(activity)}.sampleProduced")
                self.produced_id[sample.key] = made_id
            self.produced.setdefault(activity.key, []).append(self.produced_id[sample.key])

    def _own_link(self, node: Node, relation: str, target: str | None) -> str | None:
        """The id a ``relation`` link of ``node`` names inside ``target``, when it has one.

        Such a link points at an element the writer makes inside the target node (a
        feature's centerline, a sampling activity's ``SampleProduced``), so its ``href``
        gives that element's id rather than the target's own.
        """
        found = [link for link in self.links.get(node.key, ()) if link.relation == relation]
        if not found:
            return None
        link = found[0]
        if len(found) > 1 or link.target is None or link.target != target:
            raise ValueError(
                f"{_where(node)}: its {relation!r} link must be one link to {target!r}"
            )
        if not link.href.startswith("#"):
            raise ValueError(f"{_where(node)}: its {relation!r} link must have a '#id' href")
        return link.href[1:]

    def _claim_lrs(self, feature: Feature) -> None:
        if feature.lrs is None:
            return
        where = _where(feature)
        if not feature.lrs.startswith("#"):
            raise ValueError(f"{where}: lrs {feature.lrs!r:.80} must be '#' and an id")
        if feature.centerline is None:
            raise ValueError(f"{where}: a linear referencing system (lrs) needs a centerline")
        self.ids.claim(feature.lrs[1:], f"{where} lrs")
        given = self._own_link(feature, "linearElement", feature.key)
        if given is not None:
            self.ids.claim(given, f"{where} linearElement link")
            self.line_id[feature.key] = given

    def _made(self, lay: ElementLayout, base: str) -> str | None:
        return self.ids.new(base) if lay.id == "required" else None

    # --- building blocks ------------------------------------------------------------

    def _child(self, lay: ElementLayout, local: str, where: str) -> str:
        tag = lay.by_local.get(local)
        if tag is None:
            raise ValueError(
                f"{where}: DIGGS {self.layout.diggs_version} {lay.name} has no child"
                f" element {local!r}"
            )
        return tag

    def _fill(
        self, obj: etree._Element, lay: ElementLayout, parts: Sequence[Part], where: str
    ) -> None:
        """Append ``parts`` to ``obj`` in the layout's order (stable within one child)."""
        groups: dict[str, list[Fill]] = {}
        for tag, fill in parts:
            groups.setdefault(tag, []).append(fill)
        for tag in lay.children:
            if not groups:
                break
            for fill in groups.pop(tag, ()):
                fill(etree.SubElement(obj, tag))
        if groups:  # only when a part was not made with _child
            raise ValueError(f"{where}: {lay.name} has no child element {next(iter(groups))}")

    def _object(
        self,
        name: str,
        base: str,
        parts: Sequence[Part],
        where: str,
        parent: etree._Element,
    ) -> etree._Element:
        """Append object ``name`` to ``parent``, with a made-up id when it needs one."""
        lay = self.layout.element(name)
        obj = etree.SubElement(parent, lay.tag)
        made = self._made(lay, base)
        if made is not None:
            obj.set(self.gml_id, made)
        self._fill(obj, lay, parts, where)
        return obj

    def _coordinates(self, position: Position, where: str) -> list[str]:
        if position.dim != len(position.coordinates) or position.dim < 1:
            raise ValueError(f"{where}: a position's dim must be its number of coordinates")
        return [_number(value, where) for value in position.coordinates]

    def _geometry(
        self,
        element: str,
        tokens: Sequence[str],
        prop: etree._Element,
        *,
        srs: str | None,
        dim: int,
        gml_id: str,
        where: str,
    ) -> None:
        """``<prop><element gml:id srsName srsDimension><gml:pos or gml:posList>``.

        The CRS and dimension go on the geometry object, which is DIGGS best practice
        (the reader finds them on the object or on the coordinate element).
        """
        lay = self.layout.element(element)
        obj = etree.SubElement(prop, lay.tag)
        obj.set(self.gml_id, gml_id)
        if srs is not None:
            obj.set("srsName", srs)
        obj.set("srsDimension", str(dim))
        child = "pos" if element == "PointLocation" else "posList"
        etree.SubElement(obj, self._child(lay, child, where)).text = " ".join(tokens)

    def _point(self, position: Position, base: str, where: str, prop: etree._Element) -> None:
        self._geometry(
            "PointLocation",
            self._coordinates(position, where),
            prop,
            srs=position.crs,
            dim=position.dim,
            gml_id=self.ids.new(base),
            where=where,
        )

    def _interval(self, interval: Interval, base: str, where: str, prop: etree._Element) -> None:
        if interval.top is None or interval.base is None:
            raise ValueError(f"{where}: an interval needs both a top and a base")
        tokens = [_token(interval.top.text, where), _token(interval.base.text, where)]
        self._geometry(
            "LinearExtent",
            tokens,
            prop,
            srs=interval.lrs,
            dim=1,
            gml_id=self.ids.new(base),
            where=where,
        )

    def _time(self, span: TimeSpan, base: str, where: str, prop: etree._Element) -> None:
        """``<prop><TimeInterval><start/><end/>``.

        DIGGS requires ``start``; when only the end is known it is written empty, as the
        official examples do, and reads back as no start.
        """
        if span.start is None and span.end is None:
            raise ValueError(f"{where}: a time needs a start or an end")
        lay = self.layout.element("TimeInterval")

        def start(el: etree._Element) -> None:
            if span.start is not None:
                _set_text(span.start, where, el)

        parts: list[Part] = [(self._child(lay, "start", where), start)]
        if span.end is not None:
            parts.append((self._child(lay, "end", where), partial(_set_text, span.end, where)))
        self._object("TimeInterval", base, parts, where, prop)

    def _remark(self, text: str, where: str, prop: etree._Element) -> None:
        lay = self.layout.element("Remark")
        part = (self._child(lay, "content", where), partial(_set_text, text, where))
        self._object("Remark", "Remark", [part], where, prop)

    def _link(self, link: Link, where: str, el: etree._Element) -> None:
        if link.target is None:
            el.set(self.href, _checked(link.href, where))
            return
        target = self.model.get(link.target)
        written = self.node_id.get(link.target)
        if target is None or written is None:
            raise ValueError(f"{where}: the target of link {link.relation!r} has no gml:id")
        if target.gml_id is not None and link.href != f"#{target.gml_id}":
            raise ValueError(
                f"{where}: link {link.relation!r} has href {link.href!r:.80}, not the"
                f" gml:id of its target {link.target!r:.80}"
            )
        el.set(self.href, f"#{written}")

    # --- nodes ----------------------------------------------------------------------

    def _header(self, node: Node, lay: ElementLayout, where: str) -> list[Part]:
        """The parts every node has: description, names, remarks, params and links."""
        parts: list[Part] = []
        if node.description is not None and not isinstance(node, LithologyLayer):
            tag = self._child(lay, "description", where)
            parts.append((tag, partial(_set_text, node.description, where)))
        names = node.names or (() if node.name is None else (node.name,))
        if (node.name is None) != (not names) or (names and node.name != names[0]):
            raise ValueError(f"{where}: name must be the first of names")
        parts.extend(
            (self._child(lay, "name", where), partial(_set_text, name, where)) for name in names
        )
        parts.extend(
            (self._child(lay, "remark", where), partial(self._remark, remark, where))
            for remark in node.remarks
        )
        parts.extend(self._param(lay, name, value, where) for name, value in node.params.items())
        self._check_refs(node, where)
        for link in self.links.get(node.key, ()):
            if (
                isinstance(node, Feature) and node.lrs and link.relation in ("linearElement", "lrm")
            ) or (isinstance(node, Sample) and link.relation == "sampleProduced"):
                continue  # written with the element it names (see _own_link, _lrs)
            holder = lay.by_local.get(f"{link.relation}Ref") or lay.by_local.get(link.relation)
            if holder is None:
                raise ValueError(
                    f"{where}: {lay.name} has no child element for link {link.relation!r}"
                )
            parts.append((holder, partial(self._link, link, where)))
        return parts

    def _param(self, lay: ElementLayout, name: str, value: Param, where: str) -> Part:
        at = f"{where} params[{name!r:.80}]"
        return self._child(lay, name, at), partial(_set_param, value, at)

    def _check_refs(self, node: Node, where: str) -> None:
        """A typed reference must be backed by a link: links are what gets written."""
        linked = {(link.relation, link.target) for link in self.links.get(node.key, ())}
        for name, relation in _TYPED_REFS:
            value = getattr(node, name, None)
            if value is None or (relation, value) in linked:
                continue
            if isinstance(node, Sample) and name == "feature":
                continue  # copied from the sampling activity; checked in _sample
            raise ValueError(f"{where}: {name} {value!r:.80} has no {relation!r} link")
        for sample in getattr(node, "samples", ()):
            if ("sample", sample) not in linked:
                raise ValueError(f"{where}: samples entry {sample!r:.80} has no 'sample' link")

    def _node(self, node: Node, parts: list[Part], where: str, prop: etree._Element) -> None:
        """Append ``node``'s object element to ``prop`` and fill it with ``parts``."""
        lay = self.layout.element(node.element)
        obj = etree.SubElement(prop, lay.tag)
        written = self.node_id[node.key]
        if written is not None:
            obj.set(self.gml_id, written)
        self._fill(obj, lay, parts, where)

    def _component_part(self, lay: ElementLayout, component: Component, where: str) -> Part:
        return self._child(lay, component.role, where), partial(self._component, component)

    def _component(self, component: Component, prop: etree._Element) -> None:
        lay = self.layout.element(component.element)
        where = _where(component)
        base = self.node_id[component.key] or f"{component.element}"
        parts = self._header(component, lay, where)
        both = component.interval is not None and component.location is not None
        if both and lay.interval == lay.location:
            raise ValueError(
                f"{where}: {lay.name} holds its interval and its location in the same child"
                " element; give one of them"
            )
        for value, tag, fill in (
            (component.interval, lay.interval, self._interval),
            (component.location, lay.location, self._point),
            (component.time, lay.time, self._time),
        ):
            if value is None:
                continue
            if tag is None:
                raise ValueError(
                    f"{where}: {lay.name} has no child element for its"
                    f" {type(value).__name__.lower()}"
                )
            suffix = tag.rpartition("}")[2]
            parts.append((tag, partial(fill, value, f"{base}.{suffix}", where)))  # type: ignore[arg-type]
        parts.extend(self._component_part(lay, child, where) for child in component.components)
        self._node(component, parts, where, prop)

    def _document(self, node: DocumentInfo, prop: etree._Element) -> None:
        lay, where = self.layout.element(node.element), _where(node)
        if node.creation_date is None:
            raise ValueError(f"{where}: DIGGS requires a creation_date")
        parts = self._header(node, lay, where)
        tag = self._child(lay, "creationDate", where)
        parts.append((tag, partial(_set_text, node.creation_date, where)))
        self._node(node, parts, where, prop)

    def _project(self, node: Project, prop: etree._Element) -> None:
        lay, where = self.layout.element(node.element), _where(node)
        if node.client is not None:
            raise ValueError(f"{where}: client has no DIGGS element the reader fills it from")
        parts = self._header(node, lay, where)
        if node.locality is not None:
            tag = self._child(lay, "locality", where)
            parts.append((tag, partial(self._locality, node.locality, self._base(node), where)))
        self._node(node, parts, where, prop)

    def _locality(self, text: str, base: str, where: str, prop: etree._Element) -> None:
        lay = self.layout.element("Locality")
        part = (self._child(lay, "description", where), partial(_set_text, text, where))
        self._object("Locality", f"{base}.locality", [part], where, prop)

    def _base(self, node: Node) -> str:
        """The stem of the ids made up for ``node``'s own sub-objects."""
        return self.node_id[node.key] or node.element

    def _feature(self, node: Feature, prop: etree._Element) -> None:
        lay, where, base = self.layout.element(node.element), _where(node), self._base(node)
        parts = self._header(node, lay, where)
        if node.location is not None:
            tag = self._child(lay, "referencePoint", where)
            parts.append(
                (tag, partial(self._point, node.location, f"{base}.referencePoint", where))
            )
        if node.centerline is not None:
            line_id = self.line_id[node.key]
            tag = self._child(lay, "centerLine", where)
            parts.append((tag, partial(self._centerline, node.centerline, line_id, where)))
            if node.lrs is not None:
                tag = self._child(lay, "linearReferencing", where)
                parts.append((tag, partial(self._lrs, node, line_id, where)))
        if node.when_constructed is not None:
            tag = self._child(lay, "whenConstructed", where)
            fill = partial(self._time, node.when_constructed, f"{base}.whenConstructed", where)
            parts.append((tag, fill))
        if node.total_depth is not None:
            tag = self._child(lay, "totalMeasuredDepth", where)
            parts.append((tag, partial(_set_quantity, node.total_depth, where)))
        for component in (*node.construction, *node.water_strikes):
            parts.append(self._component_part(lay, component, where))
        self._node(node, parts, where, prop)

    def _centerline(
        self, positions: Sequence[Position], line_id: str, where: str, prop: etree._Element
    ) -> None:
        if not positions:
            raise ValueError(f"{where}: a centerline needs at least one position")
        first = positions[0]
        tokens: list[str] = []
        for position in positions:
            if position.crs != first.crs or position.dim != first.dim:
                raise ValueError(f"{where}: a centerline's positions must share one crs and dim")
            tokens.extend(self._coordinates(position, where))
        self._geometry(
            "LinearExtent", tokens, prop, srs=first.crs, dim=first.dim, gml_id=line_id, where=where
        )

    def _lrs(self, node: Feature, line_id: str, where: str, prop: etree._Element) -> None:
        """The feature's linear referencing system: measured along its centerline.

        Its method is the one an external ``lrm`` link of the feature names (the DIGGS
        registry, as the official examples do); without such a link the method is written
        in place, with the unit the model's CRS table gives for ``lrs`` (which is where
        the reader puts it).
        """
        assert node.lrs is not None  # noqa: S101 - the caller checked
        system = self.layout.element("LinearSpatialReferenceSystem")
        lrs_id = node.lrs[1:]
        registry = self._registry_method(node)

        def identifier(el: etree._Element) -> None:
            el.text = lrs_id
            el.set("codeSpace", _ID_CODE_SPACE)

        parts: list[Part] = [
            (self.layout.tag("gml:identifier"), identifier),
            (self.layout.tag("glr:linearElement"), partial(self._set_href, line_id)),
        ]
        if registry is not None:
            parts.append((self.layout.tag("lrm"), partial(self._link, registry, where)))
        else:
            parts.append((self.layout.tag("lrm"), partial(self._method_in_place, node, where)))
        obj = etree.SubElement(prop, system.tag)
        obj.set(self.gml_id, lrs_id)
        self._fill(obj, system, parts, where)

    def _registry_method(self, node: Feature) -> Link | None:
        """The feature's link to a linear referencing method defined outside the file."""
        if node.lrs is None:
            return None
        found = [link for link in self.links.get(node.key, ()) if link.relation == "lrm"]
        if len(found) > 1 or any(link.target is not None for link in found):
            raise ValueError(f"{_where(node)}: its 'lrm' link must be one external link")
        return found[0] if found else None

    def _method_in_place(self, node: Feature, where: str, prop: etree._Element) -> None:
        ref = self.model.crs.get(node.lrs or "")
        if ref is None or ref.unit is None:
            raise ValueError(f"{where}: model.crs[{node.lrs!r}] must give the unit of its lrs")
        name = ref.name if ref.name in _LRM_NAMES.values() else _LRM_NAMES.get(ref.unit)
        if name is None:
            raise ValueError(
                f"{where}: DIGGS {self.layout.diggs_version} names no linear referencing"
                f" method for unit {ref.unit!r:.40} (it has {', '.join(_LRM_NAMES)})"
            )
        method_id = f"{(node.lrs or '#')[1:]}.lrm"

        def identifier(el: etree._Element) -> None:
            el.text = method_id
            el.set("codeSpace", _ID_CODE_SPACE)

        parts: list[Part] = [
            (self.layout.tag("gml:identifier"), identifier),
            (self.layout.tag("name"), partial(_set_text, name, where)),
            (self.layout.tag("type"), partial(_set_text, _LRM_TYPE, where)),
            (self.layout.tag("units"), partial(_set_text, ref.unit, where)),
        ]
        self._object("LinearReferencingMethod", method_id, parts, where, prop)

    def _activity(self, node: SamplingActivity, prop: etree._Element) -> None:
        lay, where, base = self.layout.element(node.element), _where(node), self._base(node)
        parts = self._header(node, lay, where)
        if node.interval is not None:
            tag = self._child(lay, "samplingLocation", where)
            fill = partial(self._interval, node.interval, f"{base}.samplingLocation", where)
            parts.append((tag, fill))
        if node.method is not None:
            tag = self._child(lay, "samplingMethod", where)
            parts.append((tag, partial(self._method, node.method, f"{base}.samplingMethod", where)))
        if node.recovery is not None:
            tag = self._child(lay, "totalSampleRecoveryLength", where)
            parts.append((tag, partial(_set_quantity, node.recovery, where)))
        if node.date is not None:
            tag = self._child(lay, "samplingDate", where)
            parts.append((tag, partial(self._time, node.date, f"{base}.samplingDate", where)))
        for produced in self.produced.get(node.key, ()):
            tag = self._child(lay, "sampleProduced", where)
            parts.append((tag, partial(self._produced, node, produced, where)))
        self._node(node, parts, where, prop)

    def _produced(
        self, activity: SamplingActivity, produced: str, where: str, prop: etree._Element
    ) -> None:
        """``<sampleProduced><SampleProduced gml:id>``: what a sample's reference names.

        DIGGS wants it to say where or when the sample was taken: the activity's interval,
        or failing that its date.
        """
        lay = self.layout.element("SampleProduced")
        obj = etree.SubElement(prop, lay.tag)
        obj.set(self.gml_id, produced)
        if activity.interval is not None:
            fill = partial(self._interval, activity.interval, f"{produced}.location", where)
            part = (self._child(lay, "location", where), fill)
        elif activity.date is not None:
            fill = partial(self._time, activity.date, f"{produced}.sampleTime", where)
            part = (self._child(lay, "sampleTime", where), fill)
        else:
            raise ValueError(
                f"{where}: a sampling activity that produces a sample needs an interval or a date"
            )
        self._fill(obj, lay, [part], where)

    def _set_href(self, gml_id: str, el: etree._Element) -> None:
        el.set(self.href, f"#{gml_id}")

    def _method(self, code: Code, base: str, where: str, prop: etree._Element) -> None:
        """A method as a ``Specification`` named by the code (what the reader reads)."""
        lay = self.layout.element("Specification")
        part = (self._child(lay, "name", where), partial(_set_code, code, where))
        self._object("Specification", base, [part], where, prop)

    def _sample(self, node: Sample, prop: etree._Element) -> None:
        lay, where = self.layout.element(node.element), _where(node)
        activity = self.model.get(node.sampling_activity or "")
        for name in ("interval", "method", "recovery", "feature"):
            value = getattr(node, name)
            if value is not None and getattr(activity, name, None) != value:
                raise ValueError(
                    f"{where}: DIGGS {self.layout.diggs_version} records a sample's {name} on"
                    " its sampling activity, whose value is different or missing"
                )
        parts = self._header(node, lay, where)
        tag = self._child(lay, "sampleProducedRef", where)
        parts.append((tag, partial(self._set_href, self.produced_id[node.key])))
        if node.type is not None:
            tag = self._child(lay, "classification", where)
            parts.append((tag, partial(_set_code, node.type, where)))
        self._node(node, parts, where, prop)

    def _observation(self, node: Observation, prop: etree._Element) -> None:
        lay, where = self.layout.element(node.element), _where(node)
        if node.system != node.element:
            raise ValueError(f"{where}: system must be the element name")
        parts = self._header(node, lay, where)
        for layer in node.layers:
            tag = self._child(lay, "lithologyObservation", where)
            parts.append((tag, partial(self._layer, layer)))
        self._node(node, parts, where, prop)

    def _layer(self, layer: LithologyLayer, prop: etree._Element) -> None:
        lay, where, base = self.layout.element(layer.element), _where(layer), self._base(layer)
        self._check_layer(layer, where)
        parts = self._header(layer, lay, where)
        if layer.interval is not None:
            tag = self._child(lay, "location", where)
            parts.append((tag, partial(self._interval, layer.interval, f"{base}.location", where)))
        if layer.geologic_unit is not None:
            tag = self._child(lay, "unitName", where)
            parts.append((tag, partial(_set_text, layer.geologic_unit, where)))
        parts.extend(self._component_part(lay, c, where) for c in layer.components)
        self._node(layer, parts, where, prop)

    def _check_layer(self, layer: LithologyLayer, where: str) -> None:
        """A layer's shortcuts must be what the reader derives from its primary lithology."""
        primary = next((c for c in layer.components if c.role == "primaryLithology"), None)
        params = {} if primary is None else primary.params
        described = params.get("lithDescription")
        if layer.description is not None and layer.description != described:
            raise ValueError(f"{where}: description is not its primary lithology's lithDescription")
        codes = tuple(
            value if isinstance(value, Code) else Code(value=value)
            for name in _CLASSIFICATION
            if isinstance(value := params.get(name), Code | str)
        )
        if layer.classification != codes:
            raise ValueError(
                f"{where}: classification is not its primary lithology's classification codes"
            )
        if layer.colour is not None and layer.colour not in _colours(primary):
            raise ValueError(f"{where}: colour is not a colorCode or colorName of its lithology")

    def _measurement(self, node: Measurement, prop: etree._Element) -> None:
        lay, where, base = self.layout.element(node.element), _where(node), self._base(node)
        if len(node.results) != 1 or node.element != "Test":
            raise ValueError(
                f"{where}: this writer writes a Test with exactly one result table"
                f" (got {node.element} with {len(node.results)})"
            )
        parts = self._header(node, lay, where)
        if node.result_time is not None:
            tag = self._child(lay, "resultTime", where)
            parts.append((tag, partial(self._time, node.result_time, f"{base}.resultTime", where)))
        tag = self._child(lay, "outcome", where)
        parts.append((tag, partial(self._outcome, node.results[0], base, where)))
        if node.procedure is not None:
            tag = self._child(lay, "procedure", where)
            parts.append((tag, partial(self._procedure, node.procedure, base, where)))
        self._node(node, parts, where, prop)

    def _procedure(self, procedure: Procedure, base: str, where: str, prop: etree._Element) -> None:
        lay = self.layout.element(procedure.type)
        where = f"{where} procedure {procedure.type}"
        if procedure.extra:
            raise ValueError(f"{where}: `extra` is not empty; this writer does not write it")
        parts = [self._param(lay, name, value, where) for name, value in procedure.params.items()]
        parts.extend(self._component_part(lay, c, where) for c in procedure.components)
        self._object(procedure.type, f"{base}.procedure", parts, where, prop)

    # --- result tables --------------------------------------------------------------

    def _outcome(self, table: ResultTable, base: str, where: str, prop: etree._Element) -> None:
        """``<outcome><TestResult><location/><results><ResultSet>…``."""
        where = f"{where} results"
        if not table.columns:
            raise ValueError(f"{where}: a result table needs at least one column")
        for column in (*table.domain.index, *table.columns):
            if len(column.values) != table.row_count:
                raise ValueError(
                    f"{where}: column {column.name!r:.80} has {len(column.values)} values"
                    f" for {table.row_count} rows"
                )
        lay = self.layout.element("TestResult")
        parts: list[Part] = []
        location = self._table_location(table, f"{base}.location", where)
        if location is None:
            raise ValueError(
                f"{where}: a DIGGS test result needs a location: a row index, a location"
                " or an extent"
            )
        parts.append((self._child(lay, "location", where), location))
        parts.append(
            (self._child(lay, "results", where), partial(self._results, table, base, where))
        )
        self._object("TestResult", f"{base}.outcome", parts, where, prop)

    def _table_location(self, table: ResultTable, base: str, where: str) -> Fill | None:
        """Where the table was measured: one place per row, one point or one interval."""
        index = table.domain.index
        names = [column.name for column in index]
        given = sum(x is not None for x in (index or None, table.location, table.extent))
        if given > 1:
            raise ValueError(f"{where}: give a row index, a location or an extent, not several")
        if table.location is not None:
            return partial(self._point, table.location, base, where)
        if table.extent is not None:
            extent = table.extent
            if extent.lrs != table.domain.crs:
                raise ValueError(f"{where}: domain.crs must be the extent's lrs")
            return partial(self._interval, extent, base, where)
        if not index:
            return None
        rows = range(table.row_count)
        dim = len(index)
        positions = ["position"] if dim == 1 else [f"position_{i + 1}" for i in range(dim)]
        if names == ["top", "base"]:
            if table.row_count < 2:  # noqa: PLR2004 - one row reads back as an extent
                raise ValueError(f"{where}: a top/base index needs two rows or more; use extent")
            element, dim = "LinearExtent", 1
        elif names == positions:
            if table.row_count < 1:
                raise ValueError(f"{where}: a row index needs at least one row")
            element = "MultiPointLocation"
        else:
            raise ValueError(f"{where}: this writer has no location for a row index of {names}")
        tokens = [_number(column.values[row], where) for row in rows for column in index]
        return partial(
            self._geometry,
            element,
            tokens,
            srs=table.domain.crs,
            dim=dim,
            gml_id=self.ids.new(base),
            where=where,
        )

    def _results(self, table: ResultTable, base: str, where: str, prop: etree._Element) -> None:
        result_set = self.layout.element("ResultSet")
        parameters = self.layout.element("PropertyParameters")

        def properties(el: etree._Element) -> None:
            for number, column in enumerate(table.columns, start=1):
                self._property(column, number, base, where, el)

        def holder(el: etree._Element) -> None:
            part = (self._child(parameters, "properties", where), properties)
            self._object("PropertyParameters", f"{base}.parameters", [part], where, el)

        def values(el: etree._Element) -> None:
            el.set("cs", _CS)
            el.set("ts", _TS)
            if table.row_count:
                el.text = _TS.join(self._rows(table, where))

        parts = [
            (self._child(result_set, "parameters", where), holder),
            (self._child(result_set, "dataValues", where), values),
        ]
        self._object("ResultSet", f"{base}.results", parts, where, prop)

    def _property(
        self, column: Column, number: int, base: str, where: str, holder: etree._Element
    ) -> None:
        lay = self.layout.element("Property")
        where = f"{where} column {column.name!r:.80}"
        type_data = _TYPE_DATA.get(column.type)
        if type_data is None:
            raise ValueError(f"{where}: unknown column type {column.type!r:.80}")
        if column.type == "number" and column.key in integer_properties():
            type_data = "integer"  # the dictionary check wants the property's own data type
        key, definition = column.key, column.definition
        if key is None or definition is None:
            raise ValueError(
                f"{where}: DIGGS requires a propertyClass with a codeSpace, so a column needs"
                " a key and a definition"
            )
        if "#" in definition and definition.rpartition("#")[2] not in ("", key):
            raise ValueError(f"{where}: key must be the fragment of its definition")

        def property_class(el: etree._Element) -> None:
            el.text = _checked(key, where)
            el.set("codeSpace", definition)

        parts: list[Part] = [
            (self._child(lay, "typeData", where), partial(_set_text, type_data, where)),
            (self._child(lay, "propertyClass", where), property_class),
        ]
        if column.name:  # the reader gives "" to a property without a name
            tag = self._child(lay, "propertyName", where)
            parts.append((tag, partial(_set_text, column.name, where)))
        if column.uom is not None:
            parts.append((self._child(lay, "uom", where), partial(_set_text, column.uom, where)))
        if column.null_value is not None:
            tag = self._child(lay, "nullValue", where)
            parts.append((tag, partial(_set_text, column.null_value, where)))
        obj = self._object("Property", f"{base}.p{number}", parts, where, holder)
        obj.set("index", str(number))

    def _rows(self, table: ResultTable, where: str) -> Iterator[str]:
        """Each row of ``table`` as the text of one tuple."""
        columns = [
            [_cell(value, column, where) for value in column.values] for column in table.columns
        ]
        for row in range(table.row_count):
            text = _CS.join(cells[row] for cells in columns)
            if not text:
                raise ValueError(
                    f"{where}: row {row + 1} of a one-column table is null, which a"
                    " white-space separated list cannot hold"
                )
            yield text

    # --- the document ---------------------------------------------------------------

    def run(self) -> etree._Element:
        model, layout = self.model, self.layout
        if model.associates or model.others:
            raise ValueError(
                "this writer writes no top-level business associates or untyped members"
                " (`associates` and `others` must be empty)"
            )
        if model.document is None:
            raise ValueError("DIGGS requires document information (model.document)")
        root_id = self._assign_ids()
        lay = layout.element("Diggs")
        nsmap: dict[str | None, str] = {
            (None if prefix == "diggs" else prefix): layout.namespaces[prefix]
            for prefix in _ROOT_PREFIXES
        }
        nsmap["xsi"] = _XSI
        root = etree.Element(lay.tag, nsmap=nsmap)
        root.set(f"{{{_XSI}}}schemaLocation", _SCHEMA_LOCATION[layout.diggs_version])
        root.set(self.gml_id, root_id)
        members: list[tuple[str, Fill]] = [
            ("documentInformation", partial(self._document, model.document))
        ]
        members += [("project", partial(self._project, n)) for n in model.projects]
        members += [("samplingFeature", partial(self._feature, n)) for n in model.features]
        members += [
            ("samplingActivity", partial(self._activity, n)) for n in model.sampling_activities
        ]
        members += [("sample", partial(self._sample, n)) for n in model.samples]
        members += [("observation", partial(self._observation, n)) for n in model.observations]
        members += [("measurement", partial(self._measurement, n)) for n in model.measurements]
        parts = [(self._child(lay, name, "Diggs"), fill) for name, fill in members]
        self._fill(root, lay, parts, "Diggs")
        return root


def _colours(component: Component | None) -> Iterator[Code]:
    """Every ``colorCode`` and ``colorName`` in a lithology and its nested records."""
    if component is None:
        return
    for name in ("colorCode", "colorName"):
        value = component.params.get(name)
        if isinstance(value, Code | str):
            yield value if isinstance(value, Code) else Code(value=value)
    for child in component.components:
        yield from _colours(child)


@cache
def integer_properties() -> frozenset[str]:
    """Keys of the bundled DIGGS property dictionary whose data type is ``integer``.

    A number column of such a property is declared ``integer``, not ``double``: the
    dictionary check of :func:`pydiggs.validate` compares ``typeData`` with the
    dictionary, and the model only says "number".

    Raises:
        ResourceError: The bundled property dictionary is missing or broken.
    """
    try:
        root = _xml.parse(_DEFAULT_DICTIONARY).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ResourceError(f"bundled property dictionary is unreadable: {exc}") from exc
    keys = set()
    for definition in root.iter("{*}Definition"):
        data_type = next(definition.iter("{*}dataType"), None)
        key = definition.get(_GML_ID)
        if key and data_type is not None and (data_type.text or "").strip().lower() == "integer":
            keys.add(key)
    return frozenset(keys)


def _cell(value: object, column: Column, where: str) -> str:
    """One cell as ``dataValues`` text; a missing cell is empty."""
    if value is None:
        return ""
    if column.type == "number":
        return _number(value, where)
    if column.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{where}: {value!r:.80} is not a boolean")
        return "true" if value else "false"
    if not isinstance(value, str) or _CS in value:
        raise ValueError(f"{where}: cell {value!r:.80} is not text without a comma")
    return _token(value, where)


def build_tree(model: Model, layout: Layout) -> etree._Element:
    """The root ``Diggs`` element of ``model``, laid out by ``layout``.

    Raises:
        ValueError: The model holds something this writer cannot write so that reading
            it back returns it (see :func:`pydiggs._writer.write_model`).
    """
    return _Emitter(model, layout).run()
