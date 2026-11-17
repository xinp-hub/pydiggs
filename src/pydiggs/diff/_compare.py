"""Field, parameter, link and ``extra`` comparison, with the tolerance rules.

A node is flattened to a map from a path (``name``, ``params.hammerType``,
``interval.top``, ``extra.role[1].Role[1]#text``) to a leaf, and two such maps are compared
path by path. Two matched nodes are flattened together, because the ``extra`` elements of
one are numbered against those of the other. Nested nodes (components, lithology layers)
are never part of their holder's map: they are nodes of their own, matched and compared
separately. Pure.

Leaves are text, numbers, booleans, :class:`~pydiggs.model.Quantity`,
:class:`~pydiggs.model.Code`, tuples of those, and :class:`Ref` (a reference to another
node, to an identified element inside ``extra`` or to a coordinate reference system, equal
when it refers to the same thing).

An id is not a value. The ``gml:id`` of an element inside ``extra`` is left out of the
comparison, and a local reference to one (``xlink:href="#x"``) is compared by the place of
the element it names (:class:`NestedIds`), never by the id's text. An element that a typed
field read (a centre line) is not in the model at all; a reference to one is compared by
the place the reader saw it at (:class:`Seen`), when both files were read for this
comparison, and as text otherwise.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Final, NamedTuple, TypeVar

from pydiggs.model import Code, ExtraNode, Feature, Link, Model, Node, Quantity

__all__ = [
    "NestedIds",
    "Ref",
    "Seen",
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
#: The op of a record's own ``gml:id`` when it differs between two paired records.
RENAMED: Final = "renamed"
#: The path of a record's own ``gml:id``. No other leaf has it: every other path of that
#: shape is a field of the model, and parameters, links and ``extra`` have a prefix.
OWN_ID: Final = "gml_id"

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


class Seen(NamedTuple):
    """What the reader saw of one file that its model does not keep.

    Only a comparison of two files has it; a :class:`~pydiggs.model.Model` comes with none.
    """

    #: The ``gml:id`` of each identified element a typed field read, to the key of the
    #: record holding the element and the element's path below it.
    places: Mapping[str, tuple[str, str]]
    #: How many coordinate systems the file defines with each name and unit, referenced
    #: or not (the model's ``crs`` table lists the referenced ones only).
    systems: Mapping[tuple[str | None, str | None], int]


class Ref:
    """A reference, equal to another when it refers to the same thing.

    ``token`` says what it refers to (a matched pair, a node only one file has, a
    coordinate reference system's definition); ``shown`` is what a person reads. A token
    that starts with ``?`` is the text of a reference that was not resolved.
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


def _attr_local(name: str) -> tuple[str, bool]:
    """The local name of an attribute, and whether it is in a namespace.

    The model keeps an attribute as ``prefix:local`` (or ``{namespace}local`` when the
    file declares no prefix), not with its namespace, so "in a namespace" is all that can
    be told: ``gml:id`` and ``g:id`` are the same attribute, a bare ``id`` is another.
    """
    local = name.rpartition("}")[2].rpartition(":")[2]
    return local, local != name


def own_id(name: str) -> bool:
    """Whether an attribute is the element's own id (``gml:id``, ``xml:id``)."""
    return _attr_local(name) == ("id", True)


def _is_href(name: str) -> bool:
    return _attr_local(name) == ("href", True)


def _local_target(href: str) -> str | None:
    """The id a reference into the same file names (``#x`` gives ``x``), else None."""
    return href[1:] if href.startswith("#") and len(href) > 1 else None


def _is_local_ref(name: str, value: str) -> bool:
    """Whether an attribute is a reference into the same file (``xlink:href``, ``srsName``)."""
    return (_is_href(name) or name == "srsName") and _local_target(value) is not None


def _crs_parts(crs: Any) -> tuple[object, ...]:
    """What the model knows of a coordinate reference system, apart from its name in the file."""
    horizontal = crs.horizontal.epsg if crs.horizontal else None
    vertical = crs.vertical.epsg if crs.vertical else None
    return (crs.form, crs.name, crs.unit, crs.axis_order, horizontal, vertical, crs.known)


class Side:
    """One file of the comparison: how its references resolve once the nodes are matched."""

    def __init__(self, model: Model, tag: str, pair_of: Mapping[str, int]) -> None:
        self.model = model
        self.tag = tag
        self.pair_of = pair_of
        #: 0 for the old file, 1 for the new one.
        self.which = 0 if tag == "old" else 1
        #: Set by the engine once both sides exist; without it nested ids stay text.
        self.nested: NestedIds | None = None
        #: What the reader saw beyond the model. Set by the engine when both files were
        #: read for the comparison; None compares what it would resolve as text.
        self.seen: Seen | None = None
        self._links: dict[str, dict[str, list[Link]]] | None = None
        self._owners: dict[str, str | None] | None = None
        self._sole: Counter[tuple[object, ...]] | None = None

    def node_ref(self, key: str) -> Ref:
        """A reference to the node ``key``: equal across files when the nodes are matched."""
        pair = self.pair_of.get(key)
        node = self.model.get(key)
        shown = key if node is None else (node.name or node.key)
        token = f"={pair}" if pair is not None else f"!{self.tag}:{key}"
        return Ref(token, shown)

    def crs_ref(self, srs_name: str, *, placed: bool = True) -> Ref:
        """A reference to a coordinate reference system, by what it is, not what it is called.

        ``placed`` is False while the elements inside ``extra`` are being placed: a
        system that is one of them is then told by its name in the file.
        """
        crs = self.model.crs.get(srs_name)
        if crs is None:
            token = self._lrs_token(srs_name)
            return Ref(f"?{srs_name}" if token is None else token, srs_name)
        parts = _crs_parts(crs)
        if crs.form == "epsg":
            return Ref(repr(parts), srs_name)
        which = self._local_token(crs, parts, placed) if crs.form == "local" else None
        if which is None:
            # Nothing says for certain which system this is: its name in the file decides.
            return Ref(f"?{(*parts, crs.srs_name)!r}", srs_name)
        return Ref(repr((*parts, which)), srs_name)

    def _local_token(self, crs: Any, parts: tuple[object, ...], placed: bool) -> str | None:
        """Which of the systems the file defines ``crs`` is, when that is certain.

        Every borehole's depth system has the same name and unit, so what tells two of
        them apart is whose it is: a feature has one (``lrs``), and the feature is named
        by its pair, so renaming the system or its owner changes nothing. Another system
        a node defines is an element inside ``extra`` and is told by its place there. A
        system no node keeps is told by where the reader saw it, or by what the model
        knows of it when the file defines no other system that reads the same. That
        takes the file: the model lists only the systems that are referenced, so between
        two models such a system has only its name to go by.
        """
        owner = self._local_owners().get(crs.local or "")
        if owner is not None:
            if getattr(self.model.get(owner), "lrs", None) == crs.srs_name:
                return self.node_ref(owner).token
            nested = self.nested_token(crs.srs_name) if placed else None
            return None if nested is None else f"n:{nested}"
        consumed = self.consumed_token(crs.srs_name)
        if consumed is not None:
            return consumed
        defined = 0 if self.seen is None else self.seen.systems.get((crs.name, crs.unit), 0)
        if crs.known and defined == 1 and self._sole_local()[parts] == 1:
            return "sole"
        return None

    def _sole_local(self) -> Counter[tuple[object, ...]]:
        """How many local systems no node defines read the same in the model."""
        if self._sole is None:
            owners = self._local_owners()
            self._sole = Counter(
                _crs_parts(crs)
                for crs in self.model.crs.values()
                if crs.form == "local" and owners.get(crs.local or "") is None
            )
        return self._sole

    def _lrs_token(self, href: str) -> str | None:
        """A matched feature's own linear referencing system, named by the feature.

        A feature has one (``lrs``), so the feature says which system it is, whatever
        the system's ``gml:id``.
        """
        target = _local_target(href)
        owner = None if target is None else self._local_owners().get(target)
        if owner is None or owner not in self.pair_of:
            return None
        if getattr(self.model.get(owner), "lrs", None) != href:
            return None
        return f"lrs:={self.pair_of[owner]}"

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

    def record_token(self, href: str) -> str | None:
        """The pair of the matched record a local reference names by its ``gml:id``."""
        target = _local_target(href)
        if target is None or target not in self.pair_of:
            return None
        node = self.model.get(target)
        if node is None or node.gml_id != target:
            return None
        return f"={self.pair_of[target]}"

    def nested_token(self, href: str) -> str | None:
        """The place of the element inside ``extra`` a local reference names, if it has one."""
        target = _local_target(href)
        if target is None or self.nested is None:
            return None
        return self.nested.token(self.which, target)

    def consumed_token(self, href: str) -> str | None:
        """The place of the element a typed field read that a local reference names.

        The model keeps neither that element nor its id, but the reader saw where it sat:
        in which record, and at which path below it, a path no other element of the
        record could have (every step is the only child of that name). The record is
        named by its pair, so the same place in two matched records is the same element
        whatever its id. An id the reader could not place for certain is not in
        ``places``, and neither is one the model keeps in ``extra``.
        """
        target = _local_target(href)
        if target is None or self.seen is None:
            return None
        found = self.seen.places.get(target)
        if found is None or found[0] not in self.pair_of:
            return None
        return f"x:{self.pair_of[found[0]]}/{found[1]}"

    def attr_ref(self, name: str, value: str, *, placed: bool = True) -> Ref | str:
        """An attribute inside ``extra``: a local reference by what it points to, else text.

        ``xlink:href`` and ``srsName`` are the references. One that names a matched
        record, a matched feature's linear referencing system, a coordinate system the
        model describes, an element a typed field read (when the reader placed it) or
        (with ``placed``) an identified element inside ``extra`` gives a :class:`Ref`.
        Anything else stays the text it is: another document, an id nothing in the file
        has, an id that two elements have, a record only one file has, an element a
        typed field read when there is no file to place it by.
        """
        if not _is_local_ref(name, value):
            return value
        if name == "srsName" and value in self.model.crs:
            return self.crs_ref(value, placed=placed)
        token = self.record_token(value) or self._lrs_token(value) or self.consumed_token(value)
        if token is None and placed:
            nested = self.nested_token(value)
            token = None if nested is None else f"n:{nested}"
        return value if token is None else Ref(token, value)

    def link_ref(self, link: Link) -> Ref:
        if link.target is None:
            return Ref(f"h:{link.href}", link.href)
        ref = self.node_ref(link.target)
        target = self.model.get(_local_target(link.href.strip()) or "")
        if target is not None and target.key == link.target and target.gml_id == target.key:
            return ref  # the link names the record itself, matched or not
        # The link names an element inside the target record, not the record: which
        # element is part of what it points to, and of what is shown (two links into one
        # record would read the same by the record's name alone).
        shown = f"{ref.shown} {link.href}"
        consumed = self.consumed_token(link.href)
        if consumed is not None:
            return Ref(consumed, shown)  # an element a typed field read, placed by the reader
        nested = self.nested_token(link.href)
        if nested is not None:
            return Ref(f"n:{nested}", shown)  # an element the model keeps, by its place
        # Which element of the record it names cannot be established: one of two equal
        # siblings, an id used twice, or an element a typed field read that has no place
        # (no file to place it by, or no path of its own). The record and the text of the
        # link decide, so a link moved to another such element is never called the same.
        return Ref(f"t:{ref.token}:{link.href}", shown)


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


class _Memo(dict[int, str]):
    """The digest of each ``extra`` subtree of one file, by ``id``.

    ``loose`` holds the subtrees with a local reference the digest took as text: what
    such a reference points to is not part of the digest. ``held`` holds the subtrees
    with a ``gml:id`` somewhere inside: a reference can point into those.
    """

    __slots__ = ("held", "loose")

    def __init__(self) -> None:
        super().__init__()
        self.loose: set[int] = set()
        self.held: set[int] = set()


def _digests(
    roots: Iterable[ExtraNode],
    memo: _Memo,
    tick: Tick,
    side: Side | None = None,
    *,
    placed: _Memo | None = None,
) -> None:
    """Put a digest of every ``extra`` subtree under ``roots`` in ``memo``, by ``id``.

    Two subtrees have the same digest when they hold the same elements in any order (a
    prefix is not part of an element, its namespace is). An element's own ``gml:id`` is
    not part of it, and a reference to a matched record or to a coordinate system counts
    as what it names, so two subtrees that differ only in ids are equal. A reference to
    an element inside ``extra`` stays its text here: where that element sits is only
    known once the digests are, so subtrees holding one are never called equal on the
    strength of a guess. Walked without recursion, and each subtree once however often it
    is asked for. ``memo`` is for one file: the same element may sit in both models and
    resolve differently.

    That is the digest elements are *placed* by. Once they are placed, ``placed`` (the
    memo of those digests) asks for the digest that says whether two subtrees are equal:
    the same, with every reference to an element inside ``extra`` taken as what it names.
    Only the subtrees with such a reference (``placed.loose``) are walked again.
    """
    stack = [(root, False) for root in roots]
    while stack:
        node, ready = stack.pop()
        if id(node) in memo:
            continue
        if placed is not None and id(node) not in placed.loose:
            memo[id(node)] = placed[id(node)]
            continue
        if not ready:
            tick(1)
            stack.append((node, True))
            stack.extend((child, False) for child in node.children)
            continue
        attrs: list[tuple[str, object]] = []
        loose = any(id(child) in memo.loose for child in node.children)
        held = any(id(child) in memo.held for child in node.children)
        for name, value in node.attrs.items():
            if own_id(name):
                held = True
                continue
            ref = value if side is None else side.attr_ref(name, value, placed=placed is not None)
            if isinstance(ref, Ref):
                # A tuple cannot be mistaken for the text of an attribute.
                attrs.append((name, ("ref", ref.token)))
                loose = loose or ref.token.startswith("?")
            else:
                attrs.append((name, ref))
                loose = loose or (side is not None and _is_local_ref(name, value))
        if loose:
            memo.loose.add(id(node))
        if held:
            memo.held.add(id(node))
        record = (
            _local(node),
            node.ns or "",
            sorted(attrs),
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


Memos = tuple[_Memo, _Memo]
Sides = tuple[Side | None, Side | None]
_NO_SIDES: Final[Sides] = (None, None)


def _unlike(
    old: list[ExtraNode], new: list[ExtraNode], memos: Memos, sides: Sides, tick: Tick
) -> tuple[tuple[list[ExtraNode], list[ExtraNode]], tuple[list[ExtraNode], list[ExtraNode]]]:
    # Equal by the digest elements are placed by; put_extra checks the references again.
    """Same-named siblings split in two: those only one side holds, and those both hold.

    An element that repeats has no identity but its content, so its place among its
    siblings is not a difference: equal subtrees cancel out wherever they stand, and what
    is left on each side keeps its document order.
    """
    _digests(old, memos[0], tick, sides[0])
    _digests(new, memos[1], tick, sides[1])
    in_old = Counter(memos[0][id(item)] for item in old)
    in_new = Counter(memos[1][id(item)] for item in new)
    rest: list[list[ExtraNode]] = []
    both: list[list[ExtraNode]] = []
    for items, memo, other in ((old, memos[0], in_new), (new, memos[1], in_old)):
        spare = Counter(other)
        kept, gone = [], []
        for item in items:
            digest = memo[id(item)]
            if spare[digest] > 0:
                spare[digest] -= 1
                gone.append(item)
            else:
                kept.append(item)
        rest.append(kept)
        both.append(gone)
    return (rest[0], rest[1]), (both[0], both[1])


def _put_element(out: dict[str, Any], path: str, item: ExtraNode, side: Side | None) -> None:
    # The namespace is written even when there is none, so that an element with no
    # namespace, attribute, text or child still has a leaf and is seen to come or go.
    out[f"{path}#ns"] = item.ns or ""
    for key, value in item.attrs.items():
        if own_id(key):
            continue  # which element this is in its file, not a value of it
        out[f"{path}@{seg(key)}"] = value if side is None else side.attr_ref(key, value)
    if item.text is not None:
        out[f"{path}#text"] = item.text


#: How :func:`_walk_extra` met an element: walked (its leaves are compared and its children
#: follow), equal on both sides (cancelled whole), or too deep (compared as one digest).
OPEN: Final = "open"
EQUAL: Final = "equal"
DEEP: Final = "deep"

#: The path, the element of each side, how it was met, and how deep it lies.
_Met = tuple[str, tuple[ExtraNode | None, ExtraNode | None], str, int]
#: The ``extra`` elements of the old and of the new side.
Both = tuple[Iterable[ExtraNode], Iterable[ExtraNode]]


#: Takes the same-named siblings of the two sides that nothing cancelled and gives back
#: those still to compare (see :func:`put_extra`).
Refine = Callable[[list[ExtraNode], list[ExtraNode]], tuple[list[ExtraNode], list[ExtraNode]]]


def _walk_extra(
    prefix: str,
    both: Both,
    sides: Sides,
    memos: Memos,
    tick: Tick,
    *,
    depth: int = 0,
    refine: Refine | None = None,
) -> Iterator[_Met]:
    """The ``extra`` elements of both sides with the path each one is compared at.

    The two sides are numbered together, because a path has to mean the same element in
    both: same-named siblings that are equal on both sides take the first numbers and add
    nothing (they are met as :data:`EQUAL`, one side at a time, at the path of their
    group), and the rest follow in document order, an old one with a new one. "Equal"
    is by the digest elements are placed by, which takes a reference to an element inside
    ``extra`` as text: :func:`put_extra` looks at those again.
    """
    stack: list[tuple[str, Sequence[ExtraNode], Sequence[ExtraNode], int]] = [
        (prefix, tuple(both[0]), tuple(both[1]), depth)
    ]
    while stack:
        base, olds, news, depth = stack.pop()
        old_groups, new_groups = _by_name(olds), _by_name(news)
        for name in old_groups.keys() | new_groups.keys():
            a, b = old_groups.get(name, []), new_groups.get(name, [])
            first = 1
            if a and b and len(a) + len(b) > 2:  # noqa: PLR2004 - one each needs no telling apart
                (a, b), (same_old, same_new) = _unlike(a, b, memos, sides, tick)
                first += len(same_old)
                if refine is not None and a and b:
                    before = len(a)
                    a, b = refine(a, b)
                    first += before - len(a)
                group = f"{base}.{seg(name)}"
                for item in same_old:
                    yield group, (item, None), EQUAL, depth
                for item in same_new:
                    yield group, (None, item), EQUAL, depth
            for n in range(max(len(a), len(b))):
                tick(1)
                path = f"{base}.{seg(name)}[{first + n}]"
                pair = (a[n] if n < len(a) else None, b[n] if n < len(b) else None)
                if depth >= MAX_EXTRA_DEPTH:
                    for deep, memo, side in zip(pair, memos, sides, strict=True):
                        if deep is not None:
                            _digests((deep,), memo, tick, side)
                    yield path, pair, DEEP, depth
                    continue
                yield path, pair, OPEN, depth
                below = tuple(() if item is None else item.children for item in pair)
                if below[0] or below[1]:
                    stack.append((path, below[0], below[1], depth + 1))


def put_extra(
    outs: tuple[dict[str, Any], dict[str, Any]],
    prefix: str,
    both: Both,
    tick: Tick = _no_tick,
    sides: Sides = _NO_SIDES,
) -> None:
    """Flatten the ``extra`` elements of both sides (old, new), in any order, into ``outs``.

    The paths are those of :func:`_walk_extra`. With ``sides``, a reference is compared
    by what it points to; without, as text.

    Same-named siblings that the walk cancelled as equal were equal with their references
    to elements inside ``extra`` read as text. Such a reference may name another element
    now (two points whose ids changed places), so the cancelled subtrees that hold one
    are compared once more, by what the references point to; those that no longer cancel
    are compared like any other pair, at the first numbers of their group, which the walk
    left free for them.

    The other way round, siblings that differ only in the text of such references (the
    ids were generated afresh) and stand in another order would be compared in document
    order, one role with another. Those are cancelled here by what their references
    point to, but only in a group where no sibling has a ``gml:id`` inside: an element
    with one is placed by its order among its siblings (:class:`NestedIds`), so taking a
    sibling out of that order could make references to two different elements look alike.

    An element too deep to walk is compared as one digest, of what its references point to.
    """
    memos: Memos = (_Memo(), _Memo())
    finals: Memos = (_Memo(), _Memo())

    def refine(a: list[ExtraNode], b: list[ExtraNode]) -> tuple[list[ExtraNode], list[ExtraNode]]:
        groups = ((a, memos[0]), (b, memos[1]))
        if any(id(item) in memo.held for items, memo in groups for item in items):
            return a, b
        if not all(any(id(item) in memo.loose for item in items) for items, memo in groups):
            return a, b  # nothing a resolved reference could make equal
        return _still_unlike((a, b), (memos, finals), sides, tick)

    todo: list[tuple[str, Both, int]] = [(prefix, both, 0)]
    while todo:
        base, below, start = todo.pop()
        cancelled: dict[tuple[str, int], tuple[list[ExtraNode], list[ExtraNode]]] = {}
        walk = _walk_extra(base, below, sides, memos, tick, depth=start, refine=refine)
        for path, pair, met, depth in walk:
            if met == EQUAL:
                for which, item in enumerate(pair):
                    if item is not None and id(item) in memos[which].loose:
                        cancelled.setdefault((path, depth), ([], []))[which].append(item)
                continue
            for which, (out, item, side) in enumerate(zip(outs, pair, sides, strict=True)):
                if item is None:
                    continue
                if met == DEEP:
                    _digests((item,), finals[which], tick, side, placed=memos[which])
                    out[f"{path}#deep"] = finals[which][id(item)]
                else:
                    _put_element(out, path, item, side)
        for (group, depth), (olds, news) in cancelled.items():
            todo.extend(
                _uncancelled(
                    outs, group, depth, (olds, news), (memos, finals), sides=sides, tick=tick
                )
            )


def _uncancelled(
    outs: tuple[dict[str, Any], dict[str, Any]],
    group: str,
    depth: int,
    both: tuple[list[ExtraNode], list[ExtraNode]],
    memos: tuple[Memos, Memos],
    *,
    sides: Sides,
    tick: Tick,
) -> Iterator[tuple[str, Both, int]]:
    """Compare the cancelled siblings of one group by what their references point to.

    Those that are still equal say nothing. The rest are written to ``outs`` an old one
    with a new one, in document order, and what lies below each pair is given back to be
    walked.
    """
    finals = memos[1]
    left = _still_unlike(both, memos, sides, tick)
    for n in range(max(len(left[0]), len(left[1]))):
        tick(1)
        path = f"{group}[{n + 1}]"
        pair = [items[n] if n < len(items) else None for items in left]
        for out, item, memo, side in zip(outs, pair, finals, sides, strict=True):
            if item is None:
                continue
            if depth >= MAX_EXTRA_DEPTH:
                out[f"{path}#deep"] = memo[id(item)]
            else:
                _put_element(out, path, item, side)
        if depth < MAX_EXTRA_DEPTH:
            below = [() if item is None else item.children for item in pair]
            if below[0] or below[1]:
                yield path, (below[0], below[1]), depth + 1


def _still_unlike(
    both: tuple[list[ExtraNode], list[ExtraNode]],
    memos: tuple[Memos, Memos],
    sides: Sides,
    tick: Tick,
) -> tuple[list[ExtraNode], list[ExtraNode]]:
    """The siblings of each side that none of the other side equals, references resolved."""
    placed, finals = memos
    for items, memo, base, side in zip(both, finals, placed, sides, strict=True):
        _digests(items, memo, tick, side, placed=base)
    old = _spare(both[0], finals[0], Counter(finals[1][id(item)] for item in both[1]))
    new = _spare(both[1], finals[1], Counter(finals[0][id(item)] for item in both[0]))
    return old, new


def _spare(items: list[ExtraNode], memo: _Memo, other: Counter[str]) -> list[ExtraNode]:
    """The ``items`` that no item of the other side cancels, in their order."""
    kept = []
    for item in items:
        digest = memo[id(item)]
        if other[digest] > 0:
            other[digest] -= 1
        else:
            kept.append(item)
    return kept


def _extras(old: Node, new: Node) -> Iterator[tuple[str, Both]]:
    """The ``extra`` lists of two matched nodes, each with the prefix of its paths."""
    yield "extra", (old.extra, new.extra)
    procedures = (getattr(old, "procedure", None), getattr(new, "procedure", None))
    below = [() if procedure is None else procedure.extra for procedure in procedures]
    yield "procedure.extra", (below[0], below[1])


def _node_extra(node: Node) -> Iterator[ExtraNode]:
    """Every element kept in the ``extra`` of ``node`` and of its procedure, at any depth."""
    procedure = getattr(node, "procedure", None)
    stack = [*node.extra, *(procedure.extra if procedure is not None else ())]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(item.children)


#: For each file, the place of each id inside ``extra`` (None: it has none).
_Places = tuple[dict[str, str | None], dict[str, str | None]]


class NestedIds:
    """Where each identified element inside ``extra`` sits, in both files.

    A ``gml:id`` inside ``extra`` (a geometry, a time instant) is not compared, so a
    reference to one cannot be compared as text either: the place of the element it names
    is. The place is the pair of the record that holds the element and the path the
    element is compared at, which is the same in both files for the same element whatever
    its id and whatever the order of its siblings. An element that was cancelled with an
    equal one on the other side has no path of its own; its place is its group and its
    content, down to the element.

    An id has no place, and a reference to it stays text, when more than one element of
    its file has the id, when a record has it, when another element shares the place (two
    equal siblings cannot be told apart), when its record is in one file only, or when it
    lies deeper than the depth compared. Built on first use: a file with no such
    reference never pays for it.
    """

    def __init__(
        self, pairs: Sequence[tuple[Node, Node]], sides: tuple[Side, Side], tick: Tick
    ) -> None:
        self._pairs = pairs
        self._sides = sides
        self._tick = tick
        self._places: _Places | None = None
        self._used: tuple[Counter[str], Counter[str]] = (Counter(), Counter())

    def token(self, which: int, gml_id: str) -> str | None:
        """The place of the element with ``gml_id`` in file ``which`` (0 old, 1 new)."""
        if self._places is None:
            self._places = self._build()
        return self._places[which].get(gml_id)

    def _claim(self, places: _Places, which: int, item: ExtraNode, place: str | None) -> None:
        """Give the ids of ``item`` its place; an id met twice has none."""
        ids = [value for name, value in item.attrs.items() if own_id(name)]
        if ids and place is not None:
            self._used[which][place] += 1
        for gml_id in ids:
            places[which][gml_id] = place if gml_id not in places[which] else None

    def _claim_equal(
        self, places: _Places, which: int, root: ExtraNode, place: str, memo: _Memo
    ) -> None:
        """Place every element of a subtree that was cancelled whole, by content."""
        stack = [(root, f"{place}~{memo[id(root)]}")]
        while stack:
            item, at = stack.pop()
            self._tick(1)
            self._claim(places, which, item, at)
            stack.extend(
                (child, f"{at}/{seg(_local(child))}~{memo[id(child)]}") for child in item.children
            )

    def _build(self) -> _Places:
        places: _Places = ({}, {})
        for old, new in self._pairs:
            number = self._sides[0].pair_of[old.key]
            for prefix, both in _extras(old, new):
                memos: Memos = (_Memo(), _Memo())
                walk = _walk_extra(prefix, both, self._sides, memos, self._tick)
                for path, pair, met, _ in walk:
                    for which, item in enumerate(pair):
                        if item is None or met == DEEP:
                            continue
                        place = f"{number}/{path}"
                        if met == OPEN:
                            self._claim(places, which, item, place)
                        else:
                            self._claim_equal(places, which, item, place, memos[which])
        for which, side in enumerate(self._sides):
            self._drop_unsure(places[which], side, self._used[which])
        return places

    def _drop_unsure(self, found: dict[str, str | None], side: Side, used: Counter[str]) -> None:
        """Take the place from every id that does not name one element for certain."""
        for node in side.model.nodes():
            self._tick(1)
            if node.gml_id is not None and node.gml_id in found:
                found[node.gml_id] = None  # a record has the id too
            if node.key not in side.pair_of:
                for item in _node_extra(node):
                    self._tick(1)
                    for name, value in item.attrs.items():
                        if own_id(name):
                            found[value] = None  # its record is in this file only
        for gml_id, place in found.items():
            if place is not None and used[place] > 1:
                found[gml_id] = None  # two elements share the place


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
    for prefix, both in _extras(old, new):
        put_extra(outs, prefix, both, tick, sides)
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
