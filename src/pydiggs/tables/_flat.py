"""Features without a centerline: a station and a trench wall.

DIGGS 3.0.0 gives a ``Station`` and a ``TrenchWall`` no centerline, so nothing is placed
by a depth along them. A trench wall has instead a ``featureExtent``, the outline of the
wall face, which DIGGS requires; a station has ``whenOccupied`` where a borehole has
``whenConstructed``. The model keeps both elements as given (in the feature's ``extra``);
the profile gives them columns. This module turns one into the other, text for text: no
coordinate is converted, reordered or closed.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Final

from pydiggs.model import ExtraNode, TimeSpan

__all__ = [
    "NO_DEPTH_AXIS",
    "extent_node",
    "extent_of",
    "occupied_node",
    "occupied_of",
    "ring_of",
    "same_ring",
]

#: The feature elements the profile writes with no centerline and no depth axis.
NO_DEPTH_AXIS: Final = ("Station", "TrenchWall")

_DIGGS_NS: Final = "http://diggsml.org/schemas/3"
_GML_NS: Final = "http://www.opengis.net/gml/3.2"
_POLYGON: Final = re.compile(r"\s*POLYGON\s*(Z\s*)?\(\s*\(([^()]*)\)\s*\)\s*", re.IGNORECASE)
_NUMBER: Final = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_DIMS: Final = (2, 3)
#: A closed ring has at least three distinct positions and the first one again.
_MIN_POSITIONS: Final = 4

#: A ring: the number of coordinates of a position, and every coordinate as written.
Ring = tuple[int, tuple[str, ...]]


def ring_of(text: str) -> Ring | str:
    """The ring of a polygon written as well-known text, or what is wrong with it.

    The profile reads ``POLYGON ((x y, x y, …))`` and ``POLYGON Z ((x y z, …))``: one
    closed ring, every position with two or with three coordinates. Each coordinate is
    kept as the text it was written with.

    Examples:
        >>> ring_of("POLYGON ((0 0, 4.50 0, 4.50 2, 0 0))")
        (2, ('0', '0', '4.50', '0', '4.50', '2', '0', '0'))
        >>> ring_of("POLYGON ((0 0, 4 0, 4 2, 0 2))")
        'its ring is not closed (the last position must repeat the first)'
    """
    match = _POLYGON.fullmatch(text)
    if match is None:
        return "it is not a POLYGON with one ring"
    positions = [tuple(part.split()) for part in match.group(2).split(",")]
    problem = _problem(positions, three=bool(match.group(1)))
    if problem is not None:
        return problem
    return len(positions[0]), tuple(token for position in positions for token in position)


def _problem(positions: list[tuple[str, ...]], *, three: bool) -> str | None:
    """What is wrong with the positions of a ring, or None."""
    dim = len(positions[0])
    problem = None
    if dim not in _DIMS or (three and dim != _DIMS[1]):
        problem = "a position has two coordinates (x y), or three (x y z)"
    elif any(len(position) != dim for position in positions):
        problem = "its positions do not all have the same number of coordinates"
    elif not all(_NUMBER.fullmatch(token) for position in positions for token in position):
        problem = "a coordinate is not a number"
    elif len(positions) < _MIN_POSITIONS:
        problem = "a ring has at least four positions"
    elif not _same_tokens(positions[0], positions[-1]):
        problem = "its ring is not closed (the last position must repeat the first)"
    return problem


def _same_tokens(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return len(a) == len(b) and all(Decimal(x) == Decimal(y) for x, y in zip(a, b, strict=True))


def same_ring(sent: str, got: str) -> bool:
    """True when two polygons hold the same positions, however each is spaced.

    Examples:
        >>> same_ring("polygon((0 0,4 0,4 2,0 0))", "POLYGON ((0 0, 4 0, 4.0 2, 0 0))")
        True
    """
    a, b = ring_of(sent), ring_of(got)
    if isinstance(a, str) or isinstance(b, str):
        return False
    return a[0] == b[0] and _same_tokens(a[1], b[1])


def _ring_text(dim: int, tokens: tuple[str, ...]) -> str:
    positions = (" ".join(tokens[at : at + dim]) for at in range(0, len(tokens), dim))
    return f"POLYGON {'Z ' if dim == _DIMS[1] else ''}(({', '.join(positions)}))"


def extent_node(ring: Ring, crs: str | None) -> ExtraNode:
    """The ``featureExtent`` of a planar feature: a ``PlanarSurface`` with one outer ring."""
    dim, tokens = ring
    attrs = {"srsDimension": str(dim)}
    if crs is not None:
        attrs["srsName"] = crs
    pos_list = ExtraNode(qname="gml:posList", ns=_GML_NS, text=" ".join(tokens))
    linear_ring = ExtraNode(qname="gml:LinearRing", ns=_GML_NS, children=(pos_list,))
    exterior = ExtraNode(qname="gml:exterior", ns=_GML_NS, children=(linear_ring,))
    surface = ExtraNode(qname="PlanarSurface", ns=_DIGGS_NS, attrs=attrs, children=(exterior,))
    return ExtraNode(qname="featureExtent", ns=_DIGGS_NS, children=(surface,))


def _local(extra: ExtraNode, ns: str) -> str | None:
    return extra.qname.rpartition(":")[2] if extra.ns == ns else None


def _is_id(name: str) -> bool:
    return name.rpartition(":")[2] == "id" or name.endswith("}id")


def _only(extra: ExtraNode, ns: str, local: str, attrs: tuple[str, ...] = ()) -> ExtraNode | None:
    """The one child of ``extra``, when it is ``local`` and ``extra`` holds nothing else."""
    if (extra.text or "").strip() or len(extra.children) != 1:
        return None
    if any(name not in attrs and not _is_id(name) for name in extra.attrs):
        return None
    child = extra.children[0]
    return child if _local(child, ns) == local else None


def extent_of(extra: ExtraNode) -> tuple[str, str | None] | None:
    """A ``featureExtent`` as well-known text and its srsName, when it is one plain ring.

    Anything more (a hole, a surface in several patches, a ring made of referenced
    curves, an uncertainty) gives None: such an extent has no column.

    Examples:
        >>> extent_of(extent_node((2, ("0", "0", "4", "0", "4", "2", "0", "0")), "#wall"))
        ('POLYGON ((0 0, 4 0, 4 2, 0 0))', '#wall')
    """
    if _local(extra, _DIGGS_NS) != "featureExtent":
        return None
    surface = _only(extra, _DIGGS_NS, "PlanarSurface")
    exterior = (
        None
        if surface is None
        else _only(surface, _GML_NS, "exterior", ("srsDimension", "srsName"))
    )
    linear_ring = None if exterior is None else _only(exterior, _GML_NS, "LinearRing")
    pos_list = None if linear_ring is None else _only(linear_ring, _GML_NS, "posList")
    if surface is None or pos_list is None or pos_list.attrs or pos_list.children:
        return None
    dim = surface.attrs.get("srsDimension")
    tokens = tuple((pos_list.text or "").split())
    if dim not in ("2", "3") or not tokens or len(tokens) % int(dim):
        return None
    text = _ring_text(int(dim), tokens)
    return None if isinstance(ring_of(text), str) else (text, surface.attrs.get("srsName"))


def occupied_node(span: TimeSpan) -> ExtraNode:
    """The ``whenOccupied`` of a station: a ``TimeInterval`` with its start and its end.

    DIGGS requires ``start``; when only the end is known it is written empty, as the
    writer does for every other time.
    """
    parts = [ExtraNode(qname="start", ns=_DIGGS_NS, text=span.start)]
    if span.end is not None:
        parts.append(ExtraNode(qname="end", ns=_DIGGS_NS, text=span.end))
    interval = ExtraNode(qname="TimeInterval", ns=_DIGGS_NS, children=tuple(parts))
    return ExtraNode(qname="whenOccupied", ns=_DIGGS_NS, children=(interval,))


def occupied_of(extra: ExtraNode) -> TimeSpan | None:
    """A ``whenOccupied`` as a start and an end, when it holds nothing else.

    Examples:
        >>> occupied_of(occupied_node(TimeSpan(start="2021-05-23")))
        TimeSpan(start='2021-05-23', end=None)
    """
    if _local(extra, _DIGGS_NS) != "whenOccupied" or (extra.text or "").strip() or extra.attrs:
        return None
    if len(extra.children) != 1 or _local(extra.children[0], _DIGGS_NS) != "TimeInterval":
        return None
    interval = extra.children[0]
    if (interval.text or "").strip() or not all(_is_id(name) for name in interval.attrs):
        return None
    found: dict[str, str | None] = {}
    for child in interval.children:
        local = _local(child, _DIGGS_NS)
        if local not in ("start", "end") or local in found or child.attrs or child.children:
            return None
        found[local] = (child.text or "").strip() or None
    if not any(found.values()):
        return None
    return TimeSpan(start=found.get("start"), end=found.get("end"))
