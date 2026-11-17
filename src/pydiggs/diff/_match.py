"""Node identity across two files: two passes, and never a guess.

Pass 1 pairs nodes with the same ``gml_id`` and the same element. Pass 2 pairs the nodes
still unmatched on both sides by a natural key (a borehole's kind and name; a sample's
borehole, name and top; a component's holder, role and index). A natural key that more
than one node has, on either side, is never matched: those nodes are reported as removed
and added, and the key is returned as ambiguous. Natural keys refer to other nodes by the
pair they belong to, so pass 2 runs level by level (features before the samples that sit in
them). Pure.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Final

from pydiggs.model import (
    Associate,
    Component,
    DocumentInfo,
    Feature,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Observation,
    Other,
    Project,
    Quantity,
    Sample,
    SamplingActivity,
)

__all__ = ["COLLECTIONS", "Ambiguity", "Info", "Pair", "Pairing", "enumerate_nodes", "match"]

#: Every collection, in the order a diff lists them.
COLLECTIONS: Final = (
    "document",
    "projects",
    "associates",
    "features",
    "sampling_activities",
    "samples",
    "observations",
    "layers",
    "measurements",
    "others",
    "components",
)

_BY_TYPE: Final[dict[type, tuple[str, int]]] = {
    DocumentInfo: ("document", 0),
    Project: ("projects", 0),
    Associate: ("associates", 0),
    Feature: ("features", 0),
    Other: ("others", 0),
    SamplingActivity: ("sampling_activities", 1),
    Observation: ("observations", 1),
    Sample: ("samples", 2),
    LithologyLayer: ("layers", 2),
    Measurement: ("measurements", 3),
}
#: Components are matched after their holders: level 4 for a holder's own, then deeper.
_COMPONENT_LEVEL: Final = 4
_LISTS: Final = (
    "projects",
    "associates",
    "features",
    "sampling_activities",
    "samples",
    "observations",
    "measurements",
    "others",
)


@dataclass(slots=True)
class Info:
    """A node with what matching needs to know about where it sits."""

    node: Node
    collection: str
    level: int
    parent: Node | None = None
    ordinal: int = 0

    @property
    def key(self) -> str:
        return self.node.key


@dataclass(frozen=True, slots=True)
class Pair:
    """Two nodes that are the same node, and how that was settled."""

    old: Info
    new: Info
    by: str


@dataclass(frozen=True, slots=True)
class Ambiguity:
    """A natural key with more than one node on a side, so none of them was matched."""

    collection: str
    label: str
    old_count: int
    new_count: int


@dataclass(slots=True)
class Pairing:
    """The outcome of matching: pairs, leftovers on each side and ambiguous keys."""

    pairs: list[Pair]
    old_only: list[Info]
    new_only: list[Info]
    ambiguities: list[Ambiguity]
    old_pair: dict[str, int]
    new_pair: dict[str, int]


def _components_of(node: Node) -> tuple[Component, ...]:
    if isinstance(node, Feature):
        return node.construction + node.water_strikes
    if isinstance(node, LithologyLayer | Component):
        return node.components
    if isinstance(node, Measurement) and node.procedure is not None:
        return node.procedure.components
    return ()


def _component_infos(holder: Node, base_level: int) -> Iterator[Info]:
    """Every component nested in ``holder``, depth first, without recursion."""
    stack: list[tuple[Node, int]] = [(holder, base_level)]
    while stack:
        current, level = stack.pop()
        counts: dict[str, int] = defaultdict(int)
        batch: list[Info] = []
        for component in _components_of(current):
            counts[component.role] += 1
            batch.append(Info(component, "components", level, current, counts[component.role]))
        yield from batch
        stack.extend((info.node, level + 1) for info in reversed(batch))


def enumerate_nodes(model: Model) -> list[Info]:
    """Every node of ``model`` once, with its collection, match level and holder."""
    infos: list[Info] = []
    if model.document is not None:
        infos.append(Info(model.document, "document", 0))
    for name in _LISTS:
        for node in getattr(model, name):
            collection, level = _BY_TYPE[type(node)]
            infos.append(Info(node, collection, level))
    for observation in model.observations:
        infos.extend(Info(layer, "layers", 2, observation) for layer in observation.layers)
    holders = [i.node for i in infos if isinstance(i.node, Feature | LithologyLayer | Measurement)]
    for holder in holders:
        infos.extend(_component_infos(holder, _COMPONENT_LEVEL))
    return infos


def _q(quantity: Quantity | None) -> str | None:
    if quantity is None:
        return None
    return quantity.text if quantity.value is None else f"{quantity.value:.9g}"


def _plain(value: Any) -> Any:
    if isinstance(value, Quantity):
        return _q(value)
    return getattr(value, "value", value)


def _measurement_where(node: Measurement) -> Any:
    for table in node.results:
        if table.extent is not None and table.extent.top is not None:
            return _q(table.extent.top)
        if table.location is not None:
            return tuple(f"{c:.9g}" for c in table.location.coordinates)
    return None


Token = Callable[[str | None], str]


def natural_key(info: Info, tok: Token) -> tuple[Any, ...]:  # noqa: PLR0911
    """The natural key of a node; references to other nodes are tokens of their pair."""
    node = info.node
    element = node.element
    if isinstance(node, Feature):
        return (element, node.kind, node.name)
    if isinstance(node, SamplingActivity | Sample):
        top = _q(node.interval.top) if node.interval else None
        return (element, tok(node.feature), node.name, top)
    if isinstance(node, Observation):
        return (element, tok(node.feature), node.system, node.name)
    if isinstance(node, LithologyLayer):
        feature = getattr(info.parent, "feature", None)
        interval = node.interval
        return (
            element,
            tok(feature),
            _q(interval.top) if interval else None,
            _q(interval.base) if interval else None,
        )
    if isinstance(node, Measurement):
        procedure = node.procedure.type if node.procedure else None
        samples = tuple(sorted(tok(s) for s in node.samples))
        return (element, tok(node.feature), procedure, _measurement_where(node), samples)
    if isinstance(node, Component):
        index = node.params.get("index")
        where = _plain(index) if index is not None else info.ordinal
        holder = info.parent.key if info.parent is not None else None
        return (element, tok(holder), node.role, where)
    return (element, node.name)


def _label(key: tuple[Any, ...]) -> str:
    text = " / ".join("-" if part is None else str(part) for part in key)
    return text if len(text) <= 200 else text[:200]  # noqa: PLR2004 - the diff's shown length


def match(  # noqa: PLR0912 - two passes, level by level
    old: list[Info],
    new: list[Info],
    mode: str,
    tick: Callable[[int], None],
) -> Pairing:
    """Match the nodes of two files. ``mode`` is ``auto``, ``gml-id`` or ``natural``."""
    pairing = Pairing([], [], [], [], {}, {})
    matched_old: set[str] = set()
    matched_new: set[str] = set()

    def link(a: Info, b: Info, by: str) -> None:
        number = len(pairing.pairs)
        pairing.pairs.append(Pair(a, b, by))
        pairing.old_pair[a.key] = number
        pairing.new_pair[b.key] = number
        matched_old.add(a.key)
        matched_new.add(b.key)

    tick(len(old) + len(new))
    if mode in ("auto", "gml-id"):
        by_id = {
            (i.node.gml_id, i.node.element): i
            for i in new
            if i.node.gml_id is not None and i.node.key == i.node.gml_id
        }
        for a in old:
            if a.node.gml_id is None or a.node.key != a.node.gml_id:
                continue
            b = by_id.get((a.node.gml_id, a.node.element))
            if b is not None:
                link(a, b, "gml_id")

    if mode in ("auto", "natural"):

        def token_for(pair_of: dict[str, int], tag: str) -> Token:
            def token(key: str | None) -> str:
                if key is None:
                    return ""
                number = pair_of.get(key)
                return f"={number}" if number is not None else f"!{tag}:{key}"

            return token

        old_token = token_for(pairing.old_pair, "old")
        new_token = token_for(pairing.new_pair, "new")
        levels = sorted({i.level for i in (*old, *new)})
        old_by_level: dict[int, list[Info]] = defaultdict(list)
        new_by_level: dict[int, list[Info]] = defaultdict(list)
        for info in old:
            old_by_level[info.level].append(info)
        for info in new:
            new_by_level[info.level].append(info)
        for level in levels:
            free_old: dict[tuple[Any, ...], list[Info]] = defaultdict(list)
            free_new: dict[tuple[Any, ...], list[Info]] = defaultdict(list)
            for info in old_by_level[level]:
                if info.key not in matched_old:
                    free_old[(info.collection, *natural_key(info, old_token))].append(info)
            for info in new_by_level[level]:
                if info.key not in matched_new:
                    free_new[(info.collection, *natural_key(info, new_token))].append(info)
            tick(len(free_old) + len(free_new))
            for key, olds in free_old.items():
                news = free_new.get(key)
                if news is None:
                    continue
                if len(olds) == 1 and len(news) == 1:
                    link(olds[0], news[0], "natural")
                else:
                    pairing.ambiguities.append(
                        Ambiguity(key[0], _label(key[1:]), len(olds), len(news))
                    )
    pairing.ambiguities.sort(key=lambda x: (x.collection, x.label))
    pairing.old_only = [i for i in old if i.key not in matched_old]
    pairing.new_only = [i for i in new if i.key not in matched_new]
    return pairing
