"""Node classes of diggs-model: every object a DIGGS file describes.

Every node starts with the same header (``key`` … ``extra``); typed fields follow.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydiggs.model._values import (
    Code,
    ExtraNode,
    Interval,
    Position,
    Procedure,
    Provenance,
    Quantity,
    ResultTable,
)

__all__ = [
    "Associate",
    "DocumentInfo",
    "Feature",
    "LithologyLayer",
    "Measurement",
    "Node",
    "Observation",
    "Other",
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
    ``provenance.xpath``. ``extra`` keeps every child element no typed field consumed.
    """

    key: str
    gml_id: str | None
    element: str
    kind: str
    name: str | None
    names: tuple[str, ...]
    description: str | None
    remarks: tuple[str, ...]
    provenance: Provenance
    extra: tuple[ExtraNode, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Feature(Node):
    """A sampling feature: a borehole, sounding, well, trial pit, station or other kind."""

    location: Position | None = None
    centerline: tuple[Position, ...] | None = None
    total_depth: Quantity | None = None
    lrs: str | None = None
    construction: tuple[ExtraNode, ...] = ()
    water_strikes: tuple[ExtraNode, ...] = ()
    parent: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SamplingActivity(Node):
    """A sampling activity."""

    feature: str | None = None
    interval: Interval | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Sample(Node):
    """A sample."""

    feature: str | None = None
    sampling_activity: str | None = None
    interval: Interval | None = None
    method: Code | None = None
    type: Code | None = None
    recovery: Quantity | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LithologyLayer(Node):
    """One layer of a lithology observation."""

    interval: Interval | None = None
    classification: tuple[Code, ...] = ()
    colour: str | None = None
    geologic_unit: str | None = None


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
    result_time: str | None = None
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
