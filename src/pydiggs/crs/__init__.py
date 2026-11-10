"""Coordinate reference systems: what an ``srsName`` refers to.

pyDIGGS reads coordinates without pyproj. To read a position correctly it still needs a
few facts about the CRS an ``srsName`` names: is it geographic (and so which axis is
latitude), projected, vertical; in what unit; on which datum. :func:`parse` answers
from the ``srsName`` alone, and :func:`epsg` from a packaged subset of the EPSG Dataset
(see :func:`epsg_source`). Nothing is fetched and nothing is transformed: for
transformations, use pyproj.

Stable from pyDIGGS 1.7.0, with the rest of the read API.

Examples:
    >>> from pydiggs import crs
    >>> crs.epsg(2272).name
    'NAD83 / Pennsylvania South (ftUS)'
    >>> crs.epsg(2272).unit
    'US survey foot'
    >>> ref = crs.parse("urn:ogc:def:crs:EPSG::4326")
    >>> ref.horizontal.type, ref.axis_order
    ('geographic_2d', 'lat,lon')
    >>> crs.parse("#lsr-B-01").form
    'local'

EPSG data: facts extracted unmodified from the EPSG Geodetic Parameter Dataset, owned by
the International Association of Oil & Gas Producers (IOGP) and used under its terms of
use, https://epsg.org/terms-of-use.html.
"""

from __future__ import annotations

import json
import re
import types
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any, Final

from pydiggs._geo_checks import parse_srs_name
from pydiggs.errors import ResourceError
from pydiggs.model._values import CrsComponent, CrsRef

__all__ = ["SUPPORTED_SRS_FORMS", "epsg", "epsg_source", "parse"]

#: The ``form`` values :func:`parse` recognises (anything else is ``unknown``).
SUPPORTED_SRS_FORMS: Final[tuple[str, ...]] = ("epsg", "diggs", "ogc", "local")
_RESOURCE: Final = "epsg-subset.json"
#: OGC's longitude-first geographic CRSs and the EPSG datum each is on.
_OGC: Final = {
    "CRS84": (4326, "WGS 84 (CRS84)"),
    "CRS83": (4269, "NAD83 (CRS83)"),
    "CRS27": (4267, "NAD27 (CRS27)"),
}
_OGC_NAME: Final = re.compile(r"CRS(84|83|27)\s*$", re.IGNORECASE)
_LRM_RESOURCE: Final = "lrm.xml"
#: A linear referencing method in the DIGGS registry (DIGGSml/def, vendored as lrm.xml).
_REGISTRY_LRM: Final = re.compile(
    r"^https?://(?:www\.)?diggsml\.org/def/crs/DIGGS/0\.1/lrm\.xml#([A-Za-z_][\w.-]{0,127})$"
)


@cache
def _subset() -> tuple[Mapping[str, Any], Mapping[str, list[Any]]]:
    try:
        text = resources.files("pydiggs.crs").joinpath(_RESOURCE).read_text("utf-8")
        document = json.loads(text)
        return types.MappingProxyType(document["source"]), document["crs"]
    except (OSError, ValueError, KeyError) as exc:  # pragma: no cover - a broken install
        raise ResourceError(f"pyDIGGS's EPSG subset ({_RESOURCE}) cannot be read: {exc}") from exc


@cache
def _registry_lrm() -> Mapping[str, str]:
    """The DIGGS registry's linear referencing methods: gml:id -> units."""
    from lxml import etree  # noqa: PLC0415 - only reading files needs lxml here

    from pydiggs import _xml  # noqa: PLC0415

    try:
        data = resources.files("pydiggs.crs").joinpath(_LRM_RESOURCE).read_bytes()
        root = _xml.parse_bytes(data, None).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:  # pragma: no cover - a broken install
        raise ResourceError(f"pyDIGGS's DIGGS lrm.xml cannot be read: {exc}") from exc
    units: dict[str, str] = {}
    for method in root.iter("{*}LinearReferencingMethod"):
        gml_id = method.get("{http://www.opengis.net/gml/3.2}id")
        unit = method.find("{*}units")
        if gml_id and unit is not None and unit.text and unit.text.strip():
            units[gml_id] = unit.text.strip()
    return types.MappingProxyType(units)


def _lrm_units(href: str) -> str | None:
    """The unit of a DIGGS registry linear referencing method, from its ``xlink:href``."""
    match = _REGISTRY_LRM.match(href.strip())
    return None if match is None else _registry_lrm().get(match.group(1))


def epsg_source() -> Mapping[str, Any]:
    """Where the EPSG facts come from.

    The dataset, its ``version`` and ``date``, the tool that extracted them, the owner
    and the terms of use.

    Raises:
        ResourceError: the packaged subset cannot be read (a broken install).
    """
    return _subset()[0]


def epsg(code: int) -> CrsComponent | None:
    """Facts about one EPSG CRS, or None when it is not in pyDIGGS's subset.

    ``type`` is ``geographic_2d``, ``geographic_3d``, ``geocentric``, ``projected``,
    ``vertical`` or ``compound``; ``unit`` is the axes' unit when they share one;
    ``axis_order`` names the axes in order (``lat,lon`` for most geographic CRSs,
    ``east,north`` for most projected ones, ``up`` for heights).

    Raises:
        ResourceError: the packaged subset cannot be read (a broken install).
    """
    entry = _subset()[1].get(str(code))
    if entry is None:
        return None
    name, kind, axes, unit, datum, components = entry
    return CrsComponent(
        epsg=code,
        name=name,
        type=kind,
        unit=unit,
        axis_order=axes or None,
        datum=datum,
        components=tuple(components),
    )


def parse(srs_name: str, *, local_ids: frozenset[str] | None = None) -> CrsRef:
    """What ``srs_name`` refers to, from the name and the EPSG subset alone.

    ``form`` is ``epsg`` (one EPSG code or a compound of two), ``diggs`` (a DIGGS CRS
    registry entry), ``ogc`` (CRS84, CRS83 or CRS27: longitude first), ``local`` (the
    ``gml:id`` of an element in the file) or ``unknown``. ``known`` says whether every
    part was found: the EPSG codes in the subset, or, given ``local_ids``, the id in
    the file. A compound EPSG code is split into its ``horizontal`` and ``vertical``
    parts.
    """
    ref = parse_srs_name(srs_name)
    if ref.kind == "local" and ref.local_id and ref.local_id.isdecimal():
        ref = type(ref)("epsg", int(ref.local_id))  # a bare EPSG code, as some files write
    if ref.kind == "ogc":
        return _ogc(srs_name)
    if ref.kind == "local":
        known = local_ids is not None and ref.local_id in local_ids
        return CrsRef(srs_name=srs_name, form="local", known=known, local=ref.local_id)
    if ref.kind not in ("epsg", "diggs") or ref.horizontal is None:
        return CrsRef(srs_name=srs_name, form=ref.kind)
    horizontal = epsg(ref.horizontal)
    vertical = None if ref.vertical is None else epsg(ref.vertical)
    known = horizontal is not None and (ref.vertical is None or vertical is not None)
    if horizontal is not None and horizontal.type == "compound" and vertical is None:
        parts = [epsg(c) for c in horizontal.components]
        if len(parts) == 2 and all(p is not None for p in parts):  # noqa: PLR2004
            name = horizontal.name
            horizontal, vertical = parts
            return _ref(srs_name, ref.kind, horizontal, vertical, known=known, name=name)
    if horizontal is None:
        horizontal = CrsComponent(epsg=ref.horizontal)
    if ref.vertical is not None and vertical is None:
        vertical = CrsComponent(epsg=ref.vertical)
    return _ref(srs_name, ref.kind, horizontal, vertical, known=known)


def _ref(
    srs_name: str,
    form: str,
    horizontal: CrsComponent | None,
    vertical: CrsComponent | None,
    *,
    known: bool,
    name: str | None = None,
) -> CrsRef:
    axes = [
        a for a in (horizontal and horizontal.axis_order, vertical and vertical.axis_order) if a
    ]
    return CrsRef(
        srs_name=srs_name,
        form=form,
        name=name or (horizontal.name if horizontal is not None else None),
        horizontal=horizontal,
        vertical=vertical,
        axis_order=",".join(axes)
        if len(axes) == len([c for c in (horizontal, vertical) if c])
        else None,
        unit=horizontal.unit if horizontal is not None else None,
        known=known,
    )


def _ogc(srs_name: str) -> CrsRef:
    match = _OGC_NAME.search(srs_name)
    code, name = _OGC[f"CRS{match.group(1)}"] if match else _OGC["CRS84"]
    on = epsg(code)
    horizontal = CrsComponent(
        name=name,
        type="geographic_2d",
        unit="degree",
        axis_order="lon,lat",
        datum=None if on is None else on.datum,
    )
    return CrsRef(
        srs_name=srs_name,
        form="ogc",
        name=name,
        horizontal=horizontal,
        axis_order="lon,lat",
        unit="degree",
        known=True,
    )
