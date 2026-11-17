"""Field, parameter, link and ``extra`` comparison, with the tolerance rules.

A node is flattened to a map from a path (``name``, ``params.hammerType``,
``interval.top``, ``extra.role[1].Role[1]#text``) to a leaf, and two such maps are compared
path by path. Nested nodes (components, lithology layers) are never part of their holder's
map: they are nodes of their own, matched and compared separately. Pure.

Leaves are text, numbers, booleans, :class:`~pydiggs.model.Quantity`,
:class:`~pydiggs.model.Code`, tuples of those, and :class:`Ref` (a reference to another
node or to a coordinate reference system, equal when it refers to the same thing).
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Final

from pydiggs.model import Code, ExtraNode, Link, Model, Node, Quantity

__all__ = ["Ref", "Side", "Tol", "close", "diff_maps", "flatten", "put", "same", "seg", "show"]

#: Longest text, in characters, that a shown value or a path segment keeps.
MAX_SHOWN: Final = 200
#: ``extra`` elements nested deeper than this are compared as one digest (the reader stops
#: at 64 too), so a hostile model cannot make paths grow without bound.
MAX_EXTRA_DEPTH: Final = 64

CHANGED: Final = "changed"
REFORMATTED: Final = "reformatted"

#: Fields of a node that are not values of the node itself.
_SKIP: Final = frozenset(
    {
        "key",
        "provenance",
        "extra",
        "params",
        "element",
        "kind",
        "components",
        "construction",
        "water_strikes",
        "layers",
        "results",
        "procedure",
        "si",
        "geographic",
        "transformed_by",
        "dim",
    }
)
#: Fields that hold the key of another node.
_NODE_REFS: Final = frozenset({"feature", "sampling_activity", "parent", "specimen"})
#: Fields that name a coordinate reference system.
_CRS_REFS: Final = frozenset({"lrs", "crs"})


@dataclass(frozen=True, slots=True)
class Tol:
    """The numeric tolerance: ``|a - b| <= max(abs_tol, rel_tol * max(|a|, |b|))``."""

    rel: float = 1e-9
    abs: float = 0.0


def close(a: float, b: float, tol: Tol) -> bool:
    """Whether two numbers are equal within ``tol`` (NaN equals NaN; infinities only themselves)."""
    if a == b:
        return True
    if math.isnan(a) and math.isnan(b):
        return True
    if math.isinf(a) or math.isinf(b):
        return False
    return abs(a - b) <= max(tol.abs, tol.rel * max(abs(a), abs(b)))


class Ref:
    """A reference, equal to another when it refers to the same thing.

    ``token`` says what it refers to (a matched pair, a node only one file has, a
    coordinate reference system's definition); ``shown`` is what a person reads.
    """

    __slots__ = ("shown", "token")

    def __init__(self, token: str, shown: str) -> None:
        self.token = token
        self.shown = shown

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ref) and other.token == self.token

    def __hash__(self) -> int:
        return hash(self.token)

    def __repr__(self) -> str:
        return f"Ref({self.token!r}, {self.shown!r})"


def seg(text: str) -> str:
    """A path segment: ``text``, or its first 180 characters plus a digest when longer than 200."""
    if len(text) <= MAX_SHOWN:
        return text
    digest = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=6).hexdigest()
    return f"{text[:180]}…#{digest}"


class Side:
    """One file of the comparison: how its references resolve once the nodes are matched."""

    def __init__(self, model: Model, tag: str, pair_of: Mapping[str, int]) -> None:
        self.model = model
        self.tag = tag
        self.pair_of = pair_of
        self._links: dict[str, dict[str, list[Link]]] | None = None

    def node_ref(self, key: str) -> Ref:
        """A reference to the node ``key``: equal across files when the nodes are matched."""
        pair = self.pair_of.get(key)
        node = self.model.get(key)
        shown = key if node is None else (node.name or node.key)
        token = f"={pair}" if pair is not None else f"!{self.tag}:{key}"
        return Ref(token, shown)

    def crs_ref(self, srs_name: str) -> Ref:
        """A reference to a coordinate reference system, by what it is, not what it is called."""
        crs = self.model.crs.get(srs_name)
        if crs is None:
            return Ref(f"?{srs_name}", srs_name)
        horizontal = crs.horizontal.epsg if crs.horizontal else None
        vertical = crs.vertical.epsg if crs.vertical else None
        parts: tuple[object, ...] = (
            crs.form,
            crs.name,
            crs.unit,
            crs.axis_order,
            horizontal,
            vertical,
            crs.known,
        )
        if crs.form not in ("local", "epsg"):
            parts = (*parts, crs.srs_name)
        return Ref(repr(parts), srs_name)

    def links_of(self, key: str) -> dict[str, list[Link]]:
        """The links whose source is ``key``, by relation."""
        if self._links is None:
            grouped: dict[str, dict[str, list[Link]]] = defaultdict(lambda: defaultdict(list))
            for link in self.model.links:
                grouped[link.source][link.relation].append(link)
            self._links = grouped
        return self._links.get(key, {})

    def link_ref(self, link: Link) -> Ref:
        if link.target is not None:
            return self.node_ref(link.target)
        return Ref(f"h:{link.href}", link.href)


_FIELD_NAMES: dict[Any, tuple[str, ...]] = {}


def _field_names(cls: type[Any]) -> tuple[str, ...]:
    """The fields of a record class that are values of it, looked up once per class."""
    names = _FIELD_NAMES.get(cls)
    if names is None:
        names = _FIELD_NAMES[cls] = tuple(f.name for f in fields(cls) if f.name not in _SKIP)
    return names


def _is_node_tuple(value: tuple[Any, ...]) -> bool:
    return bool(value) and isinstance(value[0], Node)


def put(out: dict[str, Any], path: str, name: str, value: Any, side: Side) -> None:
    """Add the leaves of one field value to ``out`` (nothing for None or a nested node)."""
    if value is None:
        return
    if name in _NODE_REFS and isinstance(value, str):
        out[path] = side.node_ref(value)
    elif name in _CRS_REFS and isinstance(value, str):
        out[path] = side.crs_ref(value)
    elif name == "samples" and isinstance(value, tuple):
        out[path] = tuple(sorted((side.node_ref(k) for k in value), key=lambda r: r.token))
    elif isinstance(value, str | bool | int | float | Quantity | Code):
        out[path] = value
    elif isinstance(value, tuple):
        if _is_node_tuple(value):
            return
        if value and is_dataclass(value[0]):
            for i, item in enumerate(value):
                _put_record(out, f"{path}[{i}]", item, side)
        else:
            out[path] = value
    elif is_dataclass(value):
        _put_record(out, path, value, side)


def _put_record(out: dict[str, Any], path: str, record: Any, side: Side) -> None:
    if isinstance(record, Node | ExtraNode):
        return
    for name in _field_names(type(record)):
        put(out, f"{path}.{name}", name, getattr(record, name), side)


def _params(out: dict[str, Any], prefix: str, params: Mapping[str, Any]) -> None:
    for name, value in params.items():
        out[f"{prefix}.{seg(name)}"] = value


def _digest(item: ExtraNode) -> str:
    """A digest of a whole ``extra`` subtree, walked without recursion."""
    hasher = hashlib.blake2b(digest_size=16)
    stack = [item]
    while stack:
        node = stack.pop()
        record = (node.qname, node.ns, tuple(sorted(node.attrs.items())), node.text)
        hasher.update(repr((record, len(node.children))).encode("utf-8", "surrogatepass"))
        stack.extend(reversed(node.children))
    return hasher.hexdigest()


def put_extra(out: dict[str, Any], prefix: str, nodes: Iterable[ExtraNode]) -> None:
    """Flatten ``extra`` elements, in any order; same-named siblings are numbered."""
    stack: list[tuple[str, Iterable[ExtraNode], int]] = [(prefix, nodes, 0)]
    while stack:
        base, items, depth = stack.pop()
        seen: Counter[str] = Counter()
        for item in items:
            local = item.qname.rpartition(":")[2]
            seen[local] += 1
            path = f"{base}.{seg(local)}[{seen[local]}]"
            if depth >= MAX_EXTRA_DEPTH:
                out[f"{path}#deep"] = _digest(item)
                continue
            if item.ns is not None:
                out[f"{path}#ns"] = item.ns
            for key, value in item.attrs.items():
                out[f"{path}@{seg(key)}"] = value
            if item.text is not None:
                out[f"{path}#text"] = item.text
            if item.children:
                stack.append((path, item.children, depth + 1))


def flatten(node: Node, side: Side) -> dict[str, Any]:
    """Every value of ``node`` that takes part in a comparison, by path."""
    out: dict[str, Any] = {}
    for name in _field_names(type(node)):
        put(out, name, name, getattr(node, name), side)
    _params(out, "params", node.params)
    put_extra(out, "extra", node.extra)
    for relation, links in side.links_of(node.key).items():
        refs = sorted((side.link_ref(link) for link in links), key=lambda r: r.token)
        out[f"links.{seg(relation)}"] = tuple(refs)
    procedure = getattr(node, "procedure", None)
    if procedure is not None:
        out["procedure.type"] = procedure.type
        _params(out, "procedure.params", procedure.params)
        put_extra(out, "procedure.extra", procedure.extra)
    return out


def same(a: Any, b: Any, tol: Tol) -> str | None:  # noqa: PLR0911 - one return per kind
    """None when equal, ``"reformatted"`` when only the text differs, else ``"changed"``."""
    if isinstance(a, Ref) or isinstance(b, Ref):
        return None if a == b else CHANGED
    if isinstance(a, Quantity) and isinstance(b, Quantity):
        if a.uom != b.uom:
            return CHANGED
        if a.value is None or b.value is None:
            return None if a.value is None and b.value is None and a.text == b.text else CHANGED
        if not close(a.value, b.value, tol):
            return CHANGED
        return REFORMATTED if a.text != b.text else None
    if isinstance(a, tuple) and isinstance(b, tuple):
        if len(a) != len(b):
            return CHANGED
        verdict = None
        for x, y in zip(a, b, strict=True):
            one = same(x, y, tol)
            if one == CHANGED:
                return CHANGED
            verdict = verdict or one
        return verdict
    if type(a) is bool or type(b) is bool:
        return None if type(a) is type(b) and a == b else CHANGED
    if isinstance(a, int | float) and isinstance(b, int | float):
        return None if close(float(a), float(b), tol) else CHANGED
    return None if type(a) is type(b) and a == b else CHANGED


def _clip(text: str) -> tuple[str, bool]:
    return (text, False) if len(text) <= MAX_SHOWN else (text[:MAX_SHOWN], True)


def show(value: Any) -> tuple[Any, bool]:  # noqa: PLR0911 - one return per kind
    """A leaf as a JSON value, and whether it was cut (text to 200 characters, lists to 200)."""
    if value is None or isinstance(value, bool | int):
        return value, False
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else (repr(value), False)
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, Ref):
        return _clip(value.shown)
    if isinstance(value, Quantity):
        return _clip(f"{value.text} {value.uom}" if value.uom else value.text)
    if isinstance(value, Code):
        return _clip(
            value.value if value.code_space is None else f"{value.value} ({value.code_space})"
        )
    if isinstance(value, tuple):
        head = value[:MAX_SHOWN]
        shown = [show(v) for v in head]
        return [v for v, _ in shown], len(value) > MAX_SHOWN or any(cut for _, cut in shown)
    return _clip(str(value))


Emit = Callable[[str, str, Any, Any], None]


def diff_maps(old: Mapping[str, Any], new: Mapping[str, Any], tol: Tol, emit: Emit) -> int:
    """Compare two leaf maps path by path; ``emit(path, op, old, new)`` for each difference.

    Returns how many leaves were compared (the work done).
    """
    paths = sorted(old.keys() | new.keys())
    for path in paths:
        if path not in new:
            emit(path, "removed", old[path], None)
        elif path not in old:
            emit(path, "added", None, new[path])
        else:
            verdict = same(old[path], new[path], tol)
            if verdict is not None:
                emit(path, verdict, old[path], new[path])
    return len(paths)
