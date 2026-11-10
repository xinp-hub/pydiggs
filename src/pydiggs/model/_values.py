"""Value classes of diggs-model: quantities, codes, positions, links, result tables.

Every class is a frozen, slotted, keyword-only dataclass. Sequences are tuples and
mappings are read-only, so a :class:`~pydiggs.model.Model` can be shared between threads.
The JSON form of each class is its fields, in declaration order (see ``_codec``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

__all__ = [
    "Code",
    "Column",
    "CrsComponent",
    "CrsRef",
    "Domain",
    "ExtraNode",
    "Geographic",
    "Interval",
    "Link",
    "Param",
    "Position",
    "Provenance",
    "Quantity",
    "ResultTable",
    "SiValue",
    "SourceInfo",
    "TimeSpan",
]

_EMPTY: Final[Mapping[str, object]] = MappingProxyType({})

#: A cell of a result table: a number, a string, a boolean, or missing.
Cell = float | str | bool | None


def _frozen_map(value: Mapping[str, object]) -> Mapping[str, object]:
    return value if isinstance(value, MappingProxyType) else MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Where a node is: the findings ``xpath`` form and its line (1-based)."""

    xpath: str
    line: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtraNode:
    """An element no typed field consumed, kept in document order."""

    qname: str
    ns: str | None = None
    attrs: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    text: str | None = None
    children: tuple[ExtraNode, ...] = ()
    line: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _frozen_map(self.attrs))


@dataclass(frozen=True, slots=True, kw_only=True)
class SiValue:
    """A value converted to the SI base unit of its quantity."""

    value: float
    uom: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Quantity:
    """A reported value: the text exactly as written, its number and unit.

    ``si`` is present only when the unit converts; nothing is converted silently.
    """

    text: str
    value: float | None = None
    uom: str | None = None
    si: SiValue | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Code:
    """A coded value and the code space it comes from."""

    value: str
    code_space: str | None = None


#: A simple parameter: a quantity (it had a ``uom``), a code (a ``codeSpace``) or text.
Param = Quantity | Code | str


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSpan:
    """A time or time interval, each end exactly as written (ISO 8601 in valid files)."""

    start: str | None = None
    end: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Interval:
    """A depth or distance interval along a linear referencing system (``lrs``)."""

    top: Quantity | None = None
    base: Quantity | None = None
    lrs: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Geographic:
    """Longitude and latitude in degrees, in the CRS's own datum (never shifted)."""

    lon: float
    lat: float
    h: float | None = None
    datum: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Position:
    """Coordinates as written, in the CRS's own axis order.

    ``geographic`` is filled only for geographic CRSs pyDIGGS knows; a suspected axis
    swap is corrected there (and only there), with a note.
    """

    coordinates: tuple[float, ...]
    dim: int
    crs: str | None = None
    geographic: Geographic | None = None
    transformed_by: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CrsComponent:
    """One component (horizontal or vertical) of a coordinate reference system.

    ``type`` is ``geographic_2d``, ``geographic_3d``, ``geocentric``, ``projected``,
    ``vertical`` or ``compound``; ``unit`` is the axes' unit when they share one;
    ``axis_order`` names the axes in order (``lat,lon``, ``east,north``, ``up``);
    ``components`` are a compound CRS's parts. Facts come from pyDIGGS's EPSG subset
    (:mod:`pydiggs.crs`); a code not in it has only ``epsg``.
    """

    epsg: int | None = None
    name: str | None = None
    type: str | None = None
    unit: str | None = None
    axis_order: str | None = None
    datum: str | None = None
    components: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CrsRef:
    """What an ``srsName`` refers to.

    ``form`` is how it is written: ``epsg``, ``diggs`` (the DIGGS CRS registry),
    ``ogc`` (CRS84, CRS83, CRS27), ``local`` (``local`` is the ``gml:id`` of a CRS in
    the file, such as a borehole's linear referencing system) or ``unknown``. ``known``
    says whether pyDIGGS found what it names. ``unit`` is the coordinates' unit when
    the CRS states one (a projected CRS's, or a linear referencing method's ``units``).
    """

    srs_name: str
    form: str
    name: str | None = None
    horizontal: CrsComponent | None = None
    vertical: CrsComponent | None = None
    axis_order: str | None = None
    unit: str | None = None
    known: bool = False
    local: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    """One reference between nodes: ``source`` names ``target`` through ``href``.

    ``relation`` is the referring element's name without ``Ref``. ``target`` is the key
    of the node that contains the referenced element, or None when it is not in the file.
    """

    source: str
    relation: str
    target: str | None
    href: str
    resolved: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Domain:
    """What a result table's rows are indexed by.

    ``index`` holds one column per coordinate when the result's location gives every
    row its own place: ``position`` (or ``position_1`` … for more axes) for a
    multi-point location, ``top`` and ``base`` for one interval per row. Their values
    are in ``crs``, the location's ``srsName``.
    """

    shape: str = "none"
    index: tuple[Column, ...] = ()
    crs: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Column:
    """One column of a result table.

    ``key`` is the dictionary key of the property (the ``propertyClass`` code-space
    fragment, or its text), the stable way to find a column. ``si_factor`` and
    ``si_offset`` convert a value to ``si_uom`` as ``value * factor + offset``.
    """

    name: str
    key: str | None = None
    definition: str | None = None
    uom: str | None = None
    type: str = "string"
    null_value: str | None = None
    values: tuple[Cell, ...] = ()
    si_uom: str | None = None
    si_factor: float | None = None
    si_offset: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultTable:
    """One result set of a measurement.

    ``location`` is the single point and ``extent`` the single interval the whole table
    was measured at, when the result gives one.
    """

    domain: Domain = field(default_factory=Domain)
    columns: tuple[Column, ...] = ()
    row_count: int = 0
    location: Position | None = None
    extent: Interval | None = None

    def column(self, key: str) -> Column | None:
        """The first column whose ``key`` (or, failing that, ``name``) is ``key``."""
        for column in self.columns:
            if column.key == key:
                return column
        return next((c for c in self.columns if c.name == key), None)

    def to_records(self) -> list[dict[str, Cell]]:
        """The rows as dicts, keyed by column key (or name when it has none)."""
        names = [c.key or c.name for c in self.columns]
        return [
            {name: column.values[i] for name, column in zip(names, self.columns, strict=True)}
            for i in range(self.row_count)
        ]


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceInfo:
    """The file a model was read from."""

    name: str | None
    sha256: str
    size: int
    diggs_version: str
    namespace: str | None
    detected: bool
