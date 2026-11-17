"""Field, parameter, link and ``extra`` comparison, with the tolerance rules.

A node is flattened to a map from a path (``name``, ``params.hammerType``,
``interval.top``, ``extra.role[1].Role[1]#text``) to a leaf, and two such maps are compared
path by path. Two matched nodes are flattened together, because the ``extra`` elements of
one are numbered against those of the other. Nested nodes (components, lithology layers)
are never part of their holder's map: they are nodes of their own, matched and compared
separately. Pure.

Leaves are text, numbers, booleans, :class:`~pydiggs.model.Quantity`,
:class:`~pydiggs.model.Code`, tuples of those, and :class:`Ref` (a reference to another
node or to a coordinate reference system, equal when it refers to the same thing).
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Final, TypeVar

from pydiggs.model import Code, ExtraNode, Feature, Link, Model, Node, Quantity

__all__ = [
    "Ref",
    "Side",
    "Tick",
    "Tol",
    "chunks",
    "close",
    "diff_maps",
    "flatten",
    "put",
    "put_extra",
    "same",
    "seg",
    "show",
]

T = TypeVar("T")

#: Reports units of work done; the engine's raises when the time budget has run out.
Tick = Callable[[int], None]

#: Longest text, in characters, that a shown value or a path segment keeps.
MAX_SHOWN: Final = 200
#: ``extra`` elements nested deeper than this are compared as one digest (the reader stops
#: at 64 too), so a hostile model cannot make paths grow without bound.
MAX_EXTRA_DEPTH: Final = 64
#: Most items handled between two reports of work, so no loop outruns the time budget.
CHUNK: Final = 256

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


def chunks(items: Sequence[T], tick: Tick) -> Iterator[Sequence[T]]:
    """``items`` in slices of at most :data:`CHUNK`, each reported as work before it is used."""
    for start in range(0, len(items), CHUNK):
        part = items[start : start + CHUNK]
        tick(len(part))
        yield part


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
        self._owners: dict[str, str | None] | None = None

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
        if crs.form == "local":
            # Every borehole's depth system has the same name and unit, so what tells two
            # of them apart is whose it is. The owner is named by its pair, so renaming
            # the system or its owner changes nothing; a system no node defines has only
            # its own name to go by.
            owner = self._local_owners().get(crs.local or "")
            parts = (*parts, crs.srs_name if owner is None else self.node_ref(owner).token)
        elif crs.form != "epsg":
            parts = (*parts, crs.srs_name)
        return Ref(repr(parts), srs_name)

    def _local_owners(self) -> dict[str, str | None]:
        """The node that defines each coordinate system written in the file, by ``gml:id``.

        A feature defines the linear referencing system in its ``lrs``; any node defines
        the identified elements kept in its ``extra``. An id two nodes define has no owner.
        """
        if self._owners is None:
            owners: dict[str, str | None] = {}

            def claim(gml_id: str, key: str) -> None:
                owners[gml_id] = key if owners.get(gml_id, key) == key else None

            for node in self.model.nodes():
                if isinstance(node, Feature) and node.lrs:
                    claim(node.lrs.removeprefix("#"), node.key)
                procedure = getattr(node, "procedure", None)
                stack = [*node.extra, *(procedure.extra if procedure is not None else ())]
                while stack:
                    item = stack.pop()
                    for name, value in item.attrs.items():
                        if name.rpartition(":")[2] == "id":
                            claim(value, node.key)
                    stack.extend(item.children)
            self._owners = owners
        return self._owners

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


def _no_tick(_: int) -> None:
    return None


def put(
    out: dict[str, Any], path: str, name: str, value: Any, side: Side, *, tick: Tick = _no_tick
) -> None:
    """Add the leaves of one field value to ``out`` (nothing for None or a nested node)."""
    if value is None:
        return
    tick(1)
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
                _put_record(out, f"{path}[{i}]", item, side, tick)
        else:
            out[path] = value
    elif is_dataclass(value):
        _put_record(out, path, value, side, tick)


def _put_record(out: dict[str, Any], path: str, record: Any, side: Side, tick: Tick) -> None:
    if isinstance(record, Node | ExtraNode):
        return
    for name in _field_names(type(record)):
        put(out, f"{path}.{name}", name, getattr(record, name), side, tick=tick)


def _params(out: dict[str, Any], prefix: str, params: Mapping[str, Any], tick: Tick) -> None:
    for name, value in params.items():
        tick(1)
        out[f"{prefix}.{seg(name)}"] = value


def _local(item: ExtraNode) -> str:
    return item.qname.rpartition(":")[2]


def _digests(roots: Iterable[ExtraNode], memo: dict[int, str], tick: Tick) -> None:
    """Put a digest of every ``extra`` subtree under ``roots`` in ``memo``, by ``id``.

    Two subtrees have the same digest when they hold the same elements in any order (a
    prefix is not part of an element, its namespace is). Walked without recursion, and
    each subtree once however often it is asked for.
    """
    stack = [(root, False) for root in roots]
    while stack:
        node, ready = stack.pop()
        if id(node) in memo:
            continue
        if not ready:
            tick(1)
            stack.append((node, True))
            stack.extend((child, False) for child in node.children)
            continue
        record = (
            _local(node),
            node.ns or "",
            sorted(node.attrs.items()),
            node.text,
            sorted(memo[id(child)] for child in node.children),
        )
        memo[id(node)] = hashlib.blake2b(
            repr(record).encode("utf-8", "surrogatepass"), digest_size=16
        ).hexdigest()


def _by_name(items: Iterable[ExtraNode]) -> dict[str, list[ExtraNode]]:
    groups: dict[str, list[ExtraNode]] = defaultdict(list)
    for item in items:
        groups[_local(item)].append(item)
    return groups


def _unlike(
    old: list[ExtraNode], new: list[ExtraNode], memo: dict[int, str], tick: Tick
) -> tuple[int, list[ExtraNode], list[ExtraNode]]:
    """Same-named siblings without the subtrees both sides hold, and how many those were.

    An element that repeats has no identity but its content, so its place among its
    siblings is not a difference: equal subtrees cancel out wherever they stand, and what
    is left on each side keeps its document order.
    """
    _digests((*old, *new), memo, tick)
    in_old = Counter(memo[id(item)] for item in old)
    in_new = Counter(memo[id(item)] for item in new)
    rest: list[list[ExtraNode]] = []
    for items, other in ((old, in_new), (new, in_old)):
        spare = Counter(other)
        kept = []
        for item in items:
            digest = memo[id(item)]
            if spare[digest] > 0:
                spare[digest] -= 1
            else:
                kept.append(item)
        rest.append(kept)
    return len(old) - len(rest[0]), rest[0], rest[1]


def _put_element(out: dict[str, Any], path: str, item: ExtraNode) -> None:
    # The namespace is written even when there is none, so that an element with no
    # namespace, attribute, text or child still has a leaf and is seen to come or go.
    out[f"{path}#ns"] = item.ns or ""
    for key, value in item.attrs.items():
        out[f"{path}@{seg(key)}"] = value
    if item.text is not None:
        out[f"{path}#text"] = item.text


def put_extra(
    outs: tuple[dict[str, Any], dict[str, Any]],
    prefix: str,
    old: Iterable[ExtraNode],
    new: Iterable[ExtraNode],
    tick: Tick = _no_tick,
) -> None:
    """Flatten the ``extra`` elements of both sides, in any order, into ``outs``.

    The two sides are numbered together, because a path has to mean the same element in
    both: same-named siblings that are equal on both sides take the first numbers and add
    nothing, and the rest follow in document order.
    """
    memo: dict[int, str] = {}
    stack: list[tuple[str, Sequence[ExtraNode], Sequence[ExtraNode], int]] = [
        (prefix, tuple(old), tuple(new), 0)
    ]
    while stack:
        base, olds, news, depth = stack.pop()
        old_groups, new_groups = _by_name(olds), _by_name(news)
        for name in old_groups.keys() | new_groups.keys():
            a, b = old_groups.get(name, []), new_groups.get(name, [])
            first = 1
            if a and b and len(a) + len(b) > 2:  # noqa: PLR2004 - one each needs no telling apart
                equal, a, b = _unlike(a, b, memo, tick)
                first += equal
            for n in range(max(len(a), len(b))):
                tick(1)
                path = f"{base}.{seg(name)}[{first + n}]"
                pair = (a[n] if n < len(a) else None, b[n] if n < len(b) else None)
                if depth >= MAX_EXTRA_DEPTH:
                    _digests((item for item in pair if item is not None), memo, tick)
                    for out, item in zip(outs, pair, strict=True):
                        if item is not None:
                            out[f"{path}#deep"] = memo[id(item)]
                    continue
                for out, item in zip(outs, pair, strict=True):
                    if item is not None:
                        _put_element(out, path, item)
                below = tuple(() if item is None else item.children for item in pair)
                if below[0] or below[1]:
                    stack.append((path, below[0], below[1], depth + 1))


def _put_node(node: Node, side: Side, tick: Tick) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _field_names(type(node)):
        put(out, name, name, getattr(node, name), side, tick=tick)
    _params(out, "params", node.params, tick)
    for relation, links in side.links_of(node.key).items():
        tick(len(links))
        refs = sorted((side.link_ref(link) for link in links), key=lambda r: r.token)
        out[f"links.{seg(relation)}"] = tuple(refs)
    procedure = getattr(node, "procedure", None)
    if procedure is not None:
        out["procedure.type"] = procedure.type
        _params(out, "procedure.params", procedure.params, tick)
    return out


def flatten(
    old: Node, new: Node, sides: tuple[Side, Side], tick: Tick = _no_tick
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Every value of two matched nodes that takes part in a comparison, by path."""
    outs = (_put_node(old, sides[0], tick), _put_node(new, sides[1], tick))
    put_extra(outs, "extra", old.extra, new.extra, tick)
    procedures = (getattr(old, "procedure", None), getattr(new, "procedure", None))
    below = tuple(() if procedure is None else procedure.extra for procedure in procedures)
    put_extra(outs, "procedure.extra", below[0], below[1], tick)
    return outs


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


def _same_leaf(a: Any, b: Any, tol: Tol, tick: Tick) -> str | None:
    """:func:`same`, with a long tuple (a centre line's coordinates) compared in slices."""
    if not (isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b) > CHUNK):
        return same(a, b, tol)
    verdict = None
    for start in range(0, len(a), CHUNK):
        tick(CHUNK)
        one = same(a[start : start + CHUNK], b[start : start + CHUNK], tol)
        if one == CHANGED:
            return CHANGED
        verdict = verdict or one
    return verdict


def diff_maps(
    old: Mapping[str, Any], new: Mapping[str, Any], tol: Tol, emit: Emit, tick: Tick = _no_tick
) -> None:
    """Compare two leaf maps path by path; ``emit(path, op, old, new)`` for each difference.

    Each leaf compared is one unit of work, reported to ``tick`` as it goes.
    """
    for part in chunks(sorted(old.keys() | new.keys()), tick):
        for path in part:
            if path not in new:
                emit(path, "removed", old[path], None)
            elif path not in old:
                emit(path, "added", None, new[path])
            else:
                verdict = _same_leaf(old[path], new[path], tol, tick)
                if verdict is not None:
                    emit(path, verdict, old[path], new[path])
