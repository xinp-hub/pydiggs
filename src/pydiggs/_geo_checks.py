"""Geometry and reference checks adapted from the DIGGS File Inspector (work card DX-1.3).

Four checks, each reported as a context-check message so it flows through the same
limits and locations as the other context checks:

* ``geometry.reported-depth-mismatch`` (104): a feature's ``totalMeasuredDepth``
  differs from the length of its ``centerLine`` by more than 2 % (and more than
  5 cm), the Inspector's tolerance. Computed only when the file states the axis units
  itself (``uomLabels``, or the axes of a ``LocalCartesianCRS`` it defines); otherwise
  skipped, never guessed. pyDIGGS has no EPSG unit data yet.
* ``geometry.crs-axis-order-suspect`` (105): coordinates in a geographic CRS whose first
  value cannot be a latitude (absolute value over 90), so they look like
  longitude, latitude. Unlike the Inspector, a negative first value alone is not
  suspect: that would flag correct southern- and western-hemisphere sites.
* ``geometry.crs-unresolvable`` (106): an ``srsName`` that is neither a recognised EPSG
  or DIGGS CRS reference nor the ``gml:id`` of an element in the file.
* ``context.xlink-remote-unresolved`` (107, info): an ``xlink:href`` to another
  document. pyDIGGS never fetches it, so what it points at was not checked. DIGGS code
  lists (``diggsml.org/def/codes/``) are left to the dictionary step.

Reported once per distinct ``srsName`` (axis order and unresolvable) or remote
document, at its first occurrence, with a count; the depth check once per feature.
"""

from __future__ import annotations

import itertools
import math
import re
import urllib.parse
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final

from lxml import etree

from pydiggs.units import UnitConverter

if TYPE_CHECKING:
    from pydiggs.semantic import ContextValidationResult

__all__ = ["DEPTH_MISMATCH", "parse_srs_name", "run_geo_checks"]

DEPTH_MISMATCH: Final = 104
AXIS_ORDER_SUSPECT: Final = 105
CRS_UNRESOLVABLE: Final = 106
XLINK_REMOTE: Final = 107

_GML: Final = "http://www.opengis.net/gml/3.2"
_XLINK_HREF: Final = "{http://www.w3.org/1999/xlink}href"
_GML_ID: Final = f"{{{_GML}}}id"
_POS: Final = f"{{{_GML}}}pos"
_POS_LIST: Final = f"{{{_GML}}}posList"

#: The Inspector's tolerance: 2 % of the larger value, and never under 5 cm.
REL_TOLERANCE: Final = 0.02
ABS_TOLERANCE_M: Final = 0.05

#: Geographic 2D and 3D CRSs (EPSG codes only) seen in DIGGS files: WGS 84, NAD83 and
#: its realisations, NAD27, ETRS89, ED50, OSGB36, GDA94, GDA2020, Amersfoort. EPSG
#: orders their axes latitude, longitude.
GEOGRAPHIC_EPSG: Final = frozenset(
    {4326, 4979, 4269, 4267, 4258, 4937, 4230, 4277, 4283, 7844, 4289, 4617, 6318, 4759, 4152}
)
#: Geographic 3D CRSs: their positions have three values without a vertical component.
_GEOGRAPHIC_3D: Final = frozenset({4979, 4937})

#: Unit symbols seen in DIGGS files that the Energistics dictionary spells differently.
_UNIT_ALIASES: Final = {"ftUS": "ft[US]", "usft": "ft[US]", "us-ft": "ft[US]", "metre": "m"}

_EPSG_CODE: Final = r"(\d{1,9})"
_URN_EPSG: Final = re.compile(
    rf"urn:(?:x-)?ogc:def:crs:EPSG:(?:[\d.]*:)?{_EPSG_CODE}", re.IGNORECASE
)
#: OGC's own CRSs (CRS84 is WGS 84 longitude, latitude): recognised, never axis-checked.
_OGC_CRS: Final = re.compile(
    r"(?:urn:(?:x-)?ogc:def:crs:OGC:[\d.]*:|https?://www\.opengis\.net/def/crs/OGC/[\d.]+/)"
    r"CRS(?:84|83|27)",
    re.IGNORECASE,
)
_URN_COMPOUND: Final = re.compile(
    rf"urn:ogc:def:crs,crs:EPSG:[\d.]*:{_EPSG_CODE},crs:EPSG:[\d.]*:{_EPSG_CODE}", re.IGNORECASE
)
_HTTP_EPSG: Final = re.compile(
    rf"https?://www\.opengis\.net/def/crs/EPSG/[\d.]+/{_EPSG_CODE}", re.IGNORECASE
)
_HTTP_COMPOUND: Final = re.compile(
    r"https?://www\.opengis\.net/def/crs-compound\?1=([^&]+)&2=(.+)", re.IGNORECASE
)
_EPSG_SHORT: Final = re.compile(rf"EPSG:{_EPSG_CODE}", re.IGNORECASE)
_EPSG_XML_FRAGMENT: Final = re.compile(rf".*epsg\.xml#{_EPSG_CODE}", re.IGNORECASE)
_DIGGS_URN: Final = re.compile(
    rf"urn:(?:diggs:)?def:crs:DIGGS:[\d.]+:{_EPSG_CODE}(?:_{_EPSG_CODE})?", re.IGNORECASE
)
_DIGGS_REGISTRY: Final = re.compile(r"https?://(?:www\.)?diggsml\.org/def/crs/", re.IGNORECASE)
_CODE_LISTS: Final = re.compile(r"https?://(?:www\.)?diggsml\.org/def/codes/", re.IGNORECASE)
_REMOTE: Final = re.compile(r"(?:https?|ftp)://", re.IGNORECASE)

_MAX_SHOWN: Final = 200


@dataclass(frozen=True, slots=True)
class SrsRef:
    """What an ``srsName`` refers to.

    ``kind`` is ``"epsg"`` (``horizontal`` and maybe ``vertical`` EPSG codes),
    ``"diggs"`` (a DIGGS CRS registry entry), ``"ogc"`` (OGC CRS84, CRS83 or CRS27,
    longitude first), ``"local"`` (``local_id``, a ``gml:id``
    that must exist in the file) or ``"unknown"``.
    """

    kind: str
    horizontal: int | None = None
    vertical: int | None = None
    local_id: str | None = None


def parse_srs_name(value: str) -> SrsRef:
    """Classify an ``srsName`` without looking anything up.

    Examples:
        >>> parse_srs_name("urn:ogc:def:crs:EPSG::4326")
        SrsRef(kind='epsg', horizontal=4326, vertical=None, local_id=None)
        >>> parse_srs_name("urn:diggs:def:crs:DIGGS:0.1:4326_5714").vertical
        5714
        >>> parse_srs_name("#site-grid").local_id
        'site-grid'
        >>> parse_srs_name("http://example.org/crs.xml#a").kind
        'unknown'
        >>> compound = (
        ...     "https://www.opengis.net/def/crs-compound?1=http://www.opengis.net/def/crs/EPSG/0/"
        ...     "26911%262=http://www.opengis.net/def/crs/EPSG/0/5703"
        ... )
        >>> parse_srs_name(compound)
        SrsRef(kind='epsg', horizontal=26911, vertical=5703, local_id=None)
    """
    text = value.strip()
    epsg = _parse_epsg(text)
    if epsg is not None:
        return epsg
    if _DIGGS_REGISTRY.match(text):
        return SrsRef("diggs")
    if _OGC_CRS.fullmatch(text):
        return SrsRef("ogc")
    if text.startswith("#") and len(text) > 1:
        return SrsRef("local", local_id=text[1:])
    if text and not any(c in text for c in ":/#?") and not text[0].isdigit():
        return SrsRef("local", local_id=text)  # a bare id, as many 2.x files write it
    return SrsRef("unknown")


def _parse_epsg(text: str) -> SrsRef | None:
    """An EPSG reference (single or compound) in any of the recognised spellings."""
    found: tuple[int, int | None] | None = None
    if m := _URN_COMPOUND.fullmatch(text):
        found = int(m.group(1)), int(m.group(2))
    elif m := _HTTP_COMPOUND.fullmatch(urllib.parse.unquote(text)):
        first, second = parse_srs_name(m.group(1)), parse_srs_name(m.group(2))
        if first.kind != "epsg" or second.kind != "epsg" or first.horizontal is None:
            return SrsRef("unknown")
        found = first.horizontal, second.horizontal
    elif m := _DIGGS_URN.fullmatch(text):
        found = int(m.group(1)), int(m.group(2)) if m.group(2) else None
    else:
        for pattern in (_URN_EPSG, _HTTP_EPSG, _EPSG_SHORT, _EPSG_XML_FRAGMENT):
            if m := pattern.fullmatch(text):
                found = int(m.group(1)), None
                break
    return None if found is None else SrsRef("epsg", found[0], found[1])


def _resolve(ref: SrsRef, ids: dict[str, etree._Element]) -> SrsRef:
    """``#4326``-style fragments are EPSG codes unless the file defines that id."""
    local = ref.local_id
    if ref.kind == "local" and local is not None and local not in ids and local.isdecimal():
        return SrsRef("epsg", int(local))
    return ref


@cache
def _converter() -> UnitConverter:
    return UnitConverter()


def to_metres(symbol: str) -> float | None:
    """Metres per ``symbol``, or None when it is not a length unit pyDIGGS knows.

    Examples:
        >>> to_metres("ft")
        0.3048
        >>> round(to_metres("ftUS"), 7)
        0.3048006
        >>> to_metres("dega") is None
        True
    """
    conv = _converter()
    name = _UNIT_ALIASES.get(symbol.strip(), symbol.strip())
    if conv.base_unit(name) != "m":
        return None
    return conv.to_base(1.0, name)


def _numbers(text: str | None) -> list[float] | None:
    try:
        values = [float(t) for t in (text or "").split()]
    except ValueError:
        return None
    return values if all(math.isfinite(v) for v in values) else None


def _inherited(el: etree._Element, attr: str, stop: etree._Element | None = None) -> str | None:
    node: etree._Element | None = el
    while node is not None:
        value = node.get(attr)
        if value:
            return str(value)
        if node is stop:
            return None
        node = node.getparent()
    return None


def _dimension(el: etree._Element, ref: SrsRef) -> int:
    declared = _inherited(el, "srsDimension")
    if declared and declared.strip().isdecimal():
        return int(declared)
    if ref.vertical is not None or ref.horizontal in _GEOGRAPHIC_3D:
        return 3
    return 2


def _short(value: str) -> str:
    return value if len(value) <= _MAX_SHOWN else value[:_MAX_SHOWN] + "…"


def _fmt(value: float) -> str:
    return f"{value:.6g}"


@dataclass(slots=True)
class _Scan:
    ids: dict[str, etree._Element]
    srs_first: dict[str, tuple[etree._Element, int]]
    remote_first: dict[str, tuple[etree._Element, int]]
    positions: list[etree._Element]
    features: list[tuple[etree._Element, etree._Element, etree._Element]]


def _scan(root: etree._Element, result: ContextValidationResult) -> _Scan:
    """Collect what the checks need in one pass over the document.

    Every element and sibling visited is charged to ``result.work``; the scaling tests
    check that it grows linearly.
    """
    scan = _Scan({}, {}, {}, [], [])
    seen_features: set[etree._Element] = set()
    for el in root.iter(etree.Element):
        result.work += 1
        gid = el.get(_GML_ID)
        if gid:
            scan.ids.setdefault(gid, el)
        srs = el.get("srsName")
        if srs:
            first, count = scan.srs_first.get(srs, (el, 0))
            scan.srs_first[srs] = (first, count + 1)
        href = el.get(_XLINK_HREF)
        if href and _REMOTE.match(href) and not _CODE_LISTS.match(href):
            document = href.split("#", 1)[0]
            first, count = scan.remote_first.get(document, (el, 0))
            scan.remote_first[document] = (first, count + 1)
        tag = el.tag
        if tag in (_POS, _POS_LIST):
            scan.positions.append(el)
        elif isinstance(tag, str) and tag.endswith("}centerLine"):
            feature = el.getparent()
            # One sibling scan per feature, at its first centerLine: scanning again for
            # every centerLine would be quadratic in a crafted file.
            if feature is None or feature in seen_features:
                continue
            seen_features.add(feature)
            result.work += len(feature)
            depth = next(
                (
                    c
                    for c in feature
                    if isinstance(c.tag, str) and c.tag.endswith("}totalMeasuredDepth")
                ),
                None,
            )
            if depth is not None:
                scan.features.append((feature, el, depth))
    return scan


def _check_unresolvable(scan: _Scan, result: ContextValidationResult) -> None:
    for value, (el, count) in scan.srs_first.items():
        ref = _resolve(parse_srs_name(value), scan.ids)
        if ref.kind in ("epsg", "diggs", "ogc") or (
            ref.kind == "local" and ref.local_id in scan.ids
        ):
            continue
        times = f" ({count} uses)" if count > 1 else ""
        if ref.kind == "local":
            text = (
                f'srsName "{_short(value)}"{times} names a coordinate reference system by'
                f' id, but no element in this file has gml:id "{_short(ref.local_id or "")}".'
            )
        else:
            text = (
                f'srsName "{_short(value)}"{times} is not a recognised EPSG or DIGGS'
                " coordinate reference system, nor the gml:id of one defined in this file;"
                " its coordinates cannot be placed."
            )
        result.add("WARNING", result.path(el), text, CRS_UNRESOLVABLE, node=el)


def _check_axis_order(scan: _Scan, result: ContextValidationResult) -> None:
    refs: dict[str, SrsRef] = {}
    # Per srsName: the first suspect element, its suspect pair, and how many elements.
    suspects: dict[str, tuple[etree._Element, float, float, int]] = {}
    for el in scan.positions:
        srs = _inherited(el, "srsName")
        if not srs:
            continue
        ref = refs.get(srs)
        if ref is None:
            ref = refs[srs] = _resolve(parse_srs_name(srs), scan.ids)
        if ref.kind != "epsg" or ref.horizontal not in GEOGRAPHIC_EPSG:
            continue
        values = _numbers(el.text)
        if not values:
            continue
        dim = _dimension(el, ref)
        if dim < 2 or len(values) % dim:  # noqa: PLR2004 - a position needs two values
            continue
        pair = next(
            (
                (values[i], values[i + 1])
                for i in range(0, len(values), dim)
                if abs(values[i]) > 90 >= abs(values[i + 1])  # noqa: PLR2004 - latitude bound
            ),
            None,
        )
        if pair is not None:
            first_el, first, second, count = suspects.get(srs, (el, *pair, 0))
            suspects[srs] = (first_el, first, second, count + 1)
    for srs, (el, first, second, count) in suspects.items():
        code = refs[srs].horizontal
        more = f" {count} coordinate elements in EPSG:{code} look the same." if count > 1 else ""
        result.add(
            "WARNING",
            result.path(el),
            f"Coordinates ({_fmt(first)}, {_fmt(second)}) look like longitude, latitude,"
            f" but EPSG:{code} orders latitude first and {_fmt(first)} cannot be a"
            f" latitude. The axes are probably swapped.{more}",
            AXIS_ORDER_SUSPECT,
            node=el,
        )


def _axis_metres(
    pos_list: etree._Element, center_line: etree._Element, dim: int, ids: dict[str, etree._Element]
) -> list[float] | None:
    """Metres per unit of each axis, from the file's own statements, or None."""
    labels = _inherited(pos_list, "uomLabels", stop=center_line)
    if labels:
        units = labels.split()
    else:
        srs = _inherited(pos_list, "srsName")
        ref = parse_srs_name(srs) if srs else SrsRef("unknown")
        crs = ids.get(ref.local_id or "") if ref.kind == "local" else None
        if crs is None or not isinstance(crs.tag, str) or not crs.tag.endswith("CartesianCRS"):
            return None
        units = [a.get("uom", "") for a in crs.iter(f"{{{_GML}}}CoordinateSystemAxis")]
    if len(units) != dim:
        return None
    factors = [to_metres(u) for u in units]
    if any(f is None for f in factors):
        return None
    return [f for f in factors if f is not None]


def _check_depths(scan: _Scan, result: ContextValidationResult) -> None:
    for feature, center_line, depth_el in scan.features:
        reported = _numbers(depth_el.text)
        uom = depth_el.get("uom", "")
        per_unit = to_metres(uom) if uom else None
        if not reported or len(reported) != 1 or per_unit is None:
            continue
        pos_list = next(center_line.iter(_POS_LIST), None)
        if pos_list is None:
            continue
        srs = _inherited(pos_list, "srsName")
        dim = _dimension(pos_list, _resolve(parse_srs_name(srs), scan.ids) if srs else SrsRef(""))
        values = _numbers(pos_list.text)
        factors = _axis_metres(pos_list, center_line, dim, scan.ids)
        if not values or factors is None or len(values) % dim or len(values) < 2 * dim:
            continue
        points = [values[i : i + dim] for i in range(0, len(values), dim)]
        length = sum(
            math.sqrt(sum(((b - a) * f) ** 2 for a, b, f in zip(p, q, factors, strict=True)))
            for p, q in itertools.pairwise(points)
        )
        reported_m = reported[0] * per_unit
        tolerance = max(REL_TOLERANCE * max(abs(reported_m), length), ABS_TOLERANCE_M)
        if abs(reported_m - length) <= tolerance:
            continue
        name = etree.QName(feature).localname
        result.add(
            "WARNING",
            result.path(feature),
            f"{name} totalMeasuredDepth is {_fmt(reported[0])} {uom}, but its centerLine is"
            f" {_fmt(length / per_unit)} {uom} long ({_fmt(length)} m): they differ by more"
            " than 2 %.",
            DEPTH_MISMATCH,
            node=feature,
        )


def _check_remote(scan: _Scan, result: ContextValidationResult) -> None:
    for document, (el, count) in scan.remote_first.items():
        times = f" ({count} links)" if count > 1 else ""
        result.add(
            "INFO",
            result.path(el),
            f'xlink:href points into "{_short(document)}"{times}, outside this file.'
            " pyDIGGS does not fetch it, so what it references was not checked.",
            XLINK_REMOTE,
            node=el,
        )


def run_geo_checks(root: etree._Element, result: ContextValidationResult) -> None:
    """Run the four Inspector-derived checks on a parsed document, into ``result``."""
    wanted = result.checks
    if wanted is not None and wanted.isdisjoint(
        {DEPTH_MISMATCH, AXIS_ORDER_SUSPECT, CRS_UNRESOLVABLE, XLINK_REMOTE}
    ):
        return
    scan = _scan(root, result)
    _check_depths(scan, result)
    _check_axis_order(scan, result)
    _check_unresolvable(scan, result)
    _check_remote(scan, result)
