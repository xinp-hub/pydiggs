"""Node classes of diggs-model: every object a DIGGS file describes.

Every node starts with the same header (``key`` … ``extra``); typed fields follow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pydiggs.model._values import (
    Code,
    ExtraNode,
    Interval,
    Param,
    Position,
    Provenance,
    Quantity,
    ResultTable,
    TimeSpan,
)

__all__ = [
    "Associate",
    "Component",
    "DocumentInfo",
    "Feature",
    "LithologyLayer",
    "Measurement",
    "Node",
    "Observation",
    "Other",
    "Procedure",
    "Project",
    "Sample",
    "SamplingActivity",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Node:
    """The header every node has.

    ``key`` is the ``gml:id`` when it is present and unique in the file, otherwise
    ``"@"`` plus the node's XPath; it is unique within one model. ``element`` is the
    DIGGS element name (after version aliases); the name as written is in
    ``provenance.xpath``.

    ``params`` holds the node's simple values by DIGGS element name: every child that
    has no child elements, appears once, and carries no attribute but ``uom`` or
    ``codeSpace`` (a :class:`Quantity`, a :class:`Code` or text). ``extra`` keeps every
    other child element no typed field consumed, so nothing is dropped.
    """

    key: str
    gml_id: str | None
    element: str
    kind: str
    name: str | None
    names: tuple[str, ...]
    description: str | None
    remarks: tuple[str, ...]
    params: Mapping[str, Param] = field(default_factory=lambda: MappingProxyType({}))
    provenance: Provenance
    extra: tuple[ExtraNode, ...]

    def __post_init__(self) -> None:
        params = self.params
        if not isinstance(params, MappingProxyType):
            object.__setattr__(self, "params", MappingProxyType(dict(params)))


@dataclass(frozen=True, slots=True, kw_only=True)
class Component(Node):
    """A record nested inside another node, kept as a node of its own.

    A casing, a backfill, a water strike, a well opening, an SPT drive set, the cone of
    a CPT, a constituent of a lithology. ``role`` is the DIGGS property that holds it
    (``casing``, ``waterStrike``, ``driveSet`` …); ``interval``, ``location`` and
    ``time`` are where and when it applies, when it says; ``components`` are the
    records nested in it (a water strike's readings, a backfill's layers).
    """

    role: str = ""
    interval: Interval | None = None
    location: Position | None = None
    time: TimeSpan | None = None
    components: tuple[Component, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Procedure:
    """How a measurement was made: the procedure element and its parameters.

    ``params`` holds the procedure's simple parameters by DIGGS element name
    (``totalPenetration``, ``tipArea`` …); ``components`` its nested records (each SPT
    drive set, the cone, a sieve analysis).
    """

    type: str
    params: Mapping[str, Param] = field(default_factory=lambda: MappingProxyType({}))
    components: tuple[Component, ...] = ()
    extra: tuple[ExtraNode, ...] = ()

    def __post_init__(self) -> None:
        params = self.params
        if not isinstance(params, MappingProxyType):
            object.__setattr__(self, "params", MappingProxyType(dict(params)))


@dataclass(frozen=True, slots=True, kw_only=True)
class Feature(Node):
    """A sampling feature: a borehole, sounding, well, trial pit, station or other kind.

    ``construction`` holds how it was built (method, casing, backfill, hole diameter,
    flush, advancement, events; a well's casing, openings and installation events) and
    ``water_strikes`` the water strikes, each a :class:`Component`. ``parent`` is the
    key of the feature a well or installation sits in.
    """

    location: Position | None = None
    centerline: tuple[Position, ...] | None = None
    total_depth: Quantity | None = None
    lrs: str | None = None
    when_constructed: TimeSpan | None = None
    construction: tuple[Component, ...] = ()
    water_strikes: tuple[Component, ...] = ()
    parent: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SamplingActivity(Node):
    """A sampling activity: where, when and how samples were taken."""

    feature: str | None = None
    interval: Interval | None = None
    method: Code | None = None
    recovery: Quantity | None = None
    date: TimeSpan | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Sample(Node):
    """A sample.

    DIGGS records a sample's depth, method and recovery on the sampling activity that
    produced it; ``interval``, ``method`` and ``recovery`` are copied from there (see
    ``sampling_activity``). ``type`` is the sample's own ``classification``.
    """

    feature: str | None = None
    sampling_activity: str | None = None
    interval: Interval | None = None
    method: Code | None = None
    type: Code | None = None
    recovery: Quantity | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LithologyLayer(Node):
    """One layer of a lithology observation.

    ``components`` holds the layer's lithologies: its ``primaryLithology`` and any
    ``componentLithology``, each with its constituents and colour as nested components
    and its other values in ``params``. ``description``, ``classification`` and
    ``colour`` are shortcuts to the primary lithology's description, classification
    codes and colour. ``geologic_unit`` is the unit name the layer gives.
    """

    interval: Interval | None = None
    classification: tuple[Code, ...] = ()
    colour: Code | None = None
    geologic_unit: str | None = None
    components: tuple[Component, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation(Node):
    """An observation system; lithology systems carry typed layers."""

    system: str = ""
    feature: str | None = None
    layers: tuple[LithologyLayer, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Measurement(Node):
    """A test, monitor or survey, with its procedure and result tables."""

    feature: str | None = None
    samples: tuple[str, ...] = ()
    specimen: str | None = None
    result_time: TimeSpan | None = None
    procedure: Procedure | None = None
    results: tuple[ResultTable, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Project(Node):
    """A project."""

    client: str | None = None
    locality: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Associate(Node):
    """A business associate."""

    roles: tuple[Code, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentInfo(Node):
    """The document information member."""

    creation_date: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Other(Node):
    """A top-level member with no typed mapper yet: header, ``extra`` and result tables."""

    results: tuple[ResultTable, ...] = ()
