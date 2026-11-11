"""GIS exports of a model (the ``gis`` extra): transform, GeoDataFrame, GeoPackage, GeoParquet.

The core of pyDIGGS never transforms a coordinate: :func:`pydiggs.read` keeps positions
as written and fills ``geographic`` (longitude, latitude) only for a geographic CRS,
on that CRS's own datum. This module does the rest with pyproj, geopandas and pyogrio,
which ``import pydiggs`` never imports.

* :func:`transform` gives every position it can a longitude and latitude on one datum.
* :func:`to_geodataframe` gives the features as points.
* :func:`write` saves them as GeoPackage, GeoParquet or GeoJSON.

Only positions whose ``srsName`` names an EPSG or OGC CRS are transformed. A position in
a CRS defined in the file (a local or linear referencing system) or in an unknown one
stays as it is, with no ``geographic``. pyproj chooses the transformation; without its
datum grids a shift between datums (NAD27 to WGS 84, say) can be off by metres.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from pydiggs import _views, crs
from pydiggs.model import Model

if TYPE_CHECKING:
    import os

__all__ = ["DRIVERS", "to_geodataframe", "transform", "write"]

_OWNER: Final = "pydiggs.gis"
#: File suffix -> the format :func:`write` saves.
DRIVERS: Final[dict[str, str]] = {".gpkg": "GPKG", ".parquet": "Parquet", ".geojson": "GeoJSON"}
_POSITION_KEYS: Final = frozenset({"coordinates", "dim", "crs", "geographic", "transformed_by"})
_OGC: Final = re.compile(r"CRS(84|83|27)\s*$", re.IGNORECASE)
_GEOCENTRIC_DIM: Final = 3


def _source(pyproj: Any, srs_name: str) -> Any | None:
    """The pyproj CRS a position's ``srsName`` names, or None when it names none."""
    ref = crs.parse(srs_name)
    try:
        if ref.form == "ogc":
            match = _OGC.search(srs_name)
            return pyproj.CRS.from_user_input(f"OGC:CRS{match.group(1) if match else '84'}")
        if ref.form in ("epsg", "diggs") and ref.horizontal is not None and ref.horizontal.epsg:
            return pyproj.CRS.from_epsg(ref.horizontal.epsg)
    except pyproj.exceptions.CRSError:
        return None
    return None


class _Transformer:
    """Transforms the positions of one model to one geographic CRS, caching per srsName."""

    def __init__(self, to: str) -> None:
        self.pyproj = _views.require("pyproj", "gis", _OWNER)
        try:
            target = self.pyproj.CRS.from_user_input(to)
        except self.pyproj.exceptions.CRSError as exc:
            raise ValueError(f"to must name a geographic CRS, got {to!r}") from exc
        if not target.is_geographic:
            raise ValueError(f"to must name a geographic CRS, got {to!r}")
        self.target = target.to_2d()
        self.target_3d = target.to_3d()
        self.datum = target.datum.name
        self.stamp = f"pydiggs[gis] pyproj {self.pyproj.__version__}"
        self._sources: dict[str, Any | None] = {}
        self._operations: dict[tuple[str, str], Any] = {}

    def _operation(self, srs_name: str, source: Any, kind: str) -> Any:
        """A transformer from ``source``: ``xy`` (lon, lat in), ``3d`` or ``native`` order."""
        key = (srs_name, kind)
        if key not in self._operations:
            make = self.pyproj.Transformer.from_crs
            if kind == "xy":
                operation = make(source, self.target, always_xy=True)
            elif kind == "3d":
                operation = make(source, self.target_3d, always_xy=True)
            else:
                operation = make(source, self.target)
            self._operations[key] = operation
        return self._operations[key]

    @staticmethod
    def _written(source: Any, coordinates: Any) -> tuple[float, float, Any] | None:
        """Longitude, latitude and height as written in a geographic CRS.

        The axes are read in the CRS's own order; a first value that cannot be a
        latitude (beyond 90 degrees) means the file wrote longitude first, as the reader
        itself decides.
        """
        if len(coordinates) < 2:  # noqa: PLR2004 - a horizontal position has two axes
            return None
        first, second = coordinates[0], coordinates[1]
        lat_first = source.axis_info[0].direction in ("north", "south")
        lat, lon = (first, second) if lat_first else (second, first)
        if abs(lat) > 90 >= abs(lon):  # noqa: PLR2004 - the reader's axis-swap rule
            lat, lon = lon, lat
        height = coordinates[2] if len(coordinates) > 2 else None  # noqa: PLR2004
        return lon, lat, height

    def _in_source(self, position: dict[str, Any], source: Any) -> tuple[float, float, Any] | None:
        """A geographic position's longitude and latitude on its own CRS, before any transform."""
        known = position["geographic"]
        if known is not None and position["transformed_by"] is None:
            return known["lon"], known["lat"], known["h"]  # filled by the reader
        return self._written(source, position["coordinates"])  # never trust an earlier transform

    def _lon_lat(self, position: dict[str, Any], source: Any) -> tuple[float, float, Any] | None:
        srs_name: str = position["crs"]
        coordinates = position["coordinates"]
        if source.is_geographic:
            start = self._in_source(position, source)
            if start is None:
                return None
            lon, lat = self._operation(srs_name, source, "xy").transform(start[0], start[1])
            return lon, lat, start[2]
        if source.is_geocentric and len(coordinates) >= _GEOCENTRIC_DIM:
            lon, lat, h = self._operation(srs_name, source, "3d").transform(*coordinates[:3])
            return lon, lat, h
        if len(coordinates) < 2:  # noqa: PLR2004 - a horizontal position has two axes
            return None
        first, second = self._operation(srs_name, source, "native").transform(*coordinates[:2])
        north_first = self.target.axis_info[0].direction == "north"
        return (second, first, None) if north_first else (first, second, None)

    def _restore(self, position: dict[str, Any], source: Any) -> None:
        """Undo an earlier transform of a position whose own CRS is the target."""
        start = self._written(source, position["coordinates"])
        if start is None:
            return
        ref = crs.parse(position["crs"])  # the datum name the reader itself would give
        datum = ref.horizontal.datum if ref.horizontal is not None else None
        position["geographic"] = {
            "lon": start[0],
            "lat": start[1],
            "h": start[2],
            "datum": datum or source.datum.name,
        }
        position["transformed_by"] = None

    def apply(self, position: dict[str, Any]) -> None:
        """Fill ``position``'s ``geographic`` in the target CRS, when its CRS allows.

        Applying it again, or to a model transformed to another CRS before, gives the
        same result as applying it once to the model as read: everything is computed
        from ``coordinates`` (or from what the reader itself filled), never from an
        earlier transform's output.
        """
        srs_name = position["crs"]
        if not isinstance(srs_name, str):
            return
        if srs_name not in self._sources:
            self._sources[srs_name] = _source(self.pyproj, srs_name)
        source = self._sources[srs_name]
        if source is None:
            return
        if source.to_2d().equals(self.target):
            if position["transformed_by"] is not None:
                self._restore(position, source)
            return
        try:
            result = self._lon_lat(position, source)
        except self.pyproj.exceptions.ProjError:
            return
        if result is None or not (math.isfinite(result[0]) and math.isfinite(result[1])):
            return
        lon, lat, h = result
        position["geographic"] = {"lon": lon, "lat": lat, "h": h, "datum": self.datum}
        position["transformed_by"] = self.stamp


def _positions(node: Any) -> Any:
    """Every position object in a diggs-model document, in place."""
    if isinstance(node, dict):
        if node.keys() >= _POSITION_KEYS:  # >=: a later minor may add a member
            yield node
            return
        for value in node.values():
            yield from _positions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _positions(value)


def transform(model: Model, to: str = "EPSG:4326") -> Model:
    """A copy of ``model`` with longitude and latitude in the CRS ``to``, where possible.

    Every position whose ``srsName`` names an EPSG or OGC CRS gets ``geographic``
    (``lon``, ``lat``, and ``h`` when the source is three-dimensional) in ``to``, and
    ``transformed_by`` says so. ``coordinates`` and ``crs`` are never changed. A position
    already in ``to``, or in a CRS that cannot be resolved, is left as it is. The height
    ``h`` of a geographic position is copied as written, not shifted to the new datum.

    Transforming a model that was transformed before is safe: each position is computed
    again from its coordinates as written, so ``transform(transform(m))`` equals
    ``transform(m)``, and a later ``to`` replaces an earlier one.

    Args:
        model: From :func:`pydiggs.read`.
        to: A geographic CRS pyproj understands, such as ``"EPSG:4326"`` (WGS 84) or
            ``"EPSG:4269"`` (NAD83).

    Returns:
        A new model; ``model`` is not changed.

    Raises:
        ImportError: pyproj is not installed (``pip install 'pydiggs[gis]'``).
        ValueError: ``to`` is not a geographic CRS.

    Examples:
        >>> import pydiggs
        >>> from pydiggs import gis
        >>> m = pydiggs.read("tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml")
        >>> moved = gis.transform(m)
        >>> moved.features[0].location.coordinates == m.features[0].location.coordinates
        True
    """
    transformer = _Transformer(to)
    document = model.to_dict()
    for position in _positions(document):
        transformer.apply(position)
    return Model.from_dict(document)


def to_geodataframe(model: Model, *, to: str = "EPSG:4326", kind: str | None = None) -> Any:
    """The features of ``model`` as a ``geopandas.GeoDataFrame`` of points in ``to``.

    The columns are those of the ``features`` view of :mod:`pydiggs.frames`, plus
    ``geometry``. A feature whose position cannot be brought to ``to`` (no location, or
    a local or unknown CRS) has an empty geometry and still has its row.

    Args:
        model: From :func:`pydiggs.read`.
        to: The geographic CRS of the points (see :func:`transform`).
        kind: Only features of this kind, such as ``"borehole"``.

    Raises:
        ImportError: the ``gis`` extra is not installed.
        ValueError: ``to`` is not a geographic CRS.
    """
    geopandas = _views.require("geopandas", "gis", _OWNER)
    shapely = _views.require("shapely", "gis", _OWNER)
    data = _views.columns(transform(model, to), "features", kind=kind)
    geometry = [
        None if lon is None or lat is None else shapely.Point(lon, lat)
        for lon, lat in zip(data["lon"], data["lat"], strict=True)
    ]
    return geopandas.GeoDataFrame(data, geometry=geometry, crs=to)


def write(
    model: Model,
    path: str | os.PathLike[str],
    *,
    driver: str | None = None,
    to: str = "EPSG:4326",
    kind: str | None = None,
) -> Path:
    """Save the features of ``model`` as GeoPackage, GeoParquet or GeoJSON.

    The format follows the file suffix (``.gpkg``, ``.parquet``, ``.geojson``) unless
    ``driver`` names it (``"GPKG"``, ``"Parquet"`` or ``"GeoJSON"``). An existing file is
    replaced. The rows and columns are those of :func:`to_geodataframe`; a GeoPackage's
    layer is named ``features``.

    Args:
        model: From :func:`pydiggs.read`.
        path: The file to write.
        driver: The format, when the suffix does not say.
        to: The geographic CRS of the points (see :func:`transform`).
        kind: Only features of this kind.

    Returns:
        ``path`` as a :class:`~pathlib.Path`.

    Raises:
        ImportError: the ``gis`` extra is not installed.
        ValueError: the format is not one of the three, or ``to`` is not geographic.
    """
    target = Path(path)
    chosen = driver or DRIVERS.get(target.suffix.lower())
    if chosen not in DRIVERS.values():
        raise ValueError(
            f"write() saves {', '.join(DRIVERS.values())} ({', '.join(DRIVERS)}); "
            f"got driver {driver!r} for {target.name!r}"
        )
    frame = to_geodataframe(model, to=to, kind=kind)
    if chosen == "Parquet":
        _views.require("pyarrow", "gis", _OWNER)
        frame.to_parquet(target)
    else:
        _views.require("pyogrio", "gis", _OWNER)
        options = {"layer": "features"} if chosen == "GPKG" else {}
        frame.to_file(target, driver=chosen, engine="pyogrio", **options)
    return target
