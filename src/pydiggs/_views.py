"""Flat, columnar views of a model (stdlib only): what ``pydiggs.frames`` and ``pydiggs.gis`` share.

A view is a mapping of column name to a list of cells, every list the same length: the
shape pandas, polars and geopandas all build a frame from. Nothing here imports them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from pydiggs.model import Column, Interval, Model, Position, Quantity

__all__ = ["VIEWS", "columns", "require"]

#: The views, and the filters each one takes.
VIEWS: Final[dict[str, frozenset[str]]] = {
    "features": frozenset({"kind"}),
    "samples": frozenset({"feature"}),
    "layers": frozenset({"feature"}),
    "measurements": frozenset({"feature", "kind", "procedure"}),
    "results": frozenset({"feature", "procedure", "measurement", "si"}),
}
#: The columns every ``results`` row starts with; a table column of the same name is suffixed.
_RESULT_KEYS: Final = ("measurement", "feature", "procedure", "table", "row")
#: The names index columns have (where a row is). In the ``results`` view a result
#: column with one of these names is always ``<name>_value``.
_PLACE_NAMES: Final = frozenset({"position", "top", "base", "time", "elapsed_time"})

Row = dict[str, Any]


def require(module: str, extra: str, owner: str) -> Any:
    """Import ``module`` for ``owner``, or say which extra provides it.

    Raises:
        ImportError: ``module`` is not installed (another missing module propagates).
    """
    import importlib  # noqa: PLC0415 - only the exports need it

    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != module.partition(".")[0]:
            raise
        raise ImportError(
            f"{owner} needs {module}: pip install 'pydiggs[{extra}]'", name=exc.name
        ) from exc


def columns(model: Model, view: str, **filters: Any) -> dict[str, list[Any]]:
    """The ``view`` of ``model`` as columns (see :func:`pydiggs.frames.to_pandas`).

    Raises:
        ValueError: ``view`` is not one of :data:`VIEWS`, or it does not take a filter.

    Examples:
        >>> import pydiggs
        >>> m = pydiggs.read("tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml")
        >>> list(columns(m, "features"))[:5]
        ['key', 'gml_id', 'element', 'kind', 'name']
    """
    if view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}, got {view!r}")
    given = {name: value for name, value in filters.items() if value is not None}
    unknown = sorted(set(given) - VIEWS[view])
    if unknown:
        raise ValueError(f"the {view!r} view takes no {unknown[0]!r} filter")
    rows = list(_BUILDERS[view](model, given))
    names: dict[str, None] = {}
    for row in rows:
        names.update(dict.fromkeys(row))
    if not rows:
        names = dict.fromkeys(_EMPTY[view])
    return {name: [row.get(name) for row in rows] for name in names}


def _value(quantity: Quantity | None) -> float | None:
    return None if quantity is None else quantity.value


def _uom(*quantities: Quantity | None) -> str | None:
    return next((q.uom for q in quantities if q is not None and q.uom), None)


def _interval(interval: Interval | None) -> Row:
    top = None if interval is None else interval.top
    base = None if interval is None else interval.base
    return {"top": _value(top), "base": _value(base), "depth_uom": _uom(top, base)}


def _place(position: Position | None) -> Row:
    """Coordinates as written (``x``, ``y``, ``z`` in the CRS's axis order) and lon/lat."""
    coordinates = () if position is None else position.coordinates
    geographic = None if position is None else position.geographic
    return {
        "x": coordinates[0] if coordinates else None,
        "y": coordinates[1] if len(coordinates) > 1 else None,
        "z": coordinates[2] if len(coordinates) > 2 else None,  # noqa: PLR2004 - the third axis
        "crs": None if position is None else position.crs,
        "lon": None if geographic is None else geographic.lon,
        "lat": None if geographic is None else geographic.lat,
    }


def _features(model: Model, filters: Row) -> Iterator[Row]:
    for feature in model.features:
        if filters.get("kind") not in (None, feature.kind):
            continue
        depth = feature.total_depth
        yield {
            "key": feature.key,
            "gml_id": feature.gml_id,
            "element": feature.element,
            "kind": feature.kind,
            "name": feature.name,
            **_place(feature.location),
            "total_depth": _value(depth),
            "total_depth_uom": _uom(depth),
            "parent": feature.parent,
        }


def _samples(model: Model, filters: Row) -> Iterator[Row]:
    for sample in model.samples:
        if filters.get("feature") not in (None, sample.feature):
            continue
        yield {
            "key": sample.key,
            "gml_id": sample.gml_id,
            "name": sample.name,
            "feature": sample.feature,
            **_interval(sample.interval),
            "type": None if sample.type is None else sample.type.value,
            "method": None if sample.method is None else sample.method.value,
            "recovery": _value(sample.recovery),
            "recovery_uom": _uom(sample.recovery),
        }


def _layers(model: Model, filters: Row) -> Iterator[Row]:
    for observation in model.observations:
        if filters.get("feature") not in (None, observation.feature):
            continue
        for layer in observation.layers:
            yield {
                "key": layer.key,
                "observation": observation.key,
                "feature": observation.feature,
                **_interval(layer.interval),
                "description": layer.description,
                "classification": layer.classification[0].value if layer.classification else None,
                "colour": None if layer.colour is None else layer.colour.value,
                "geologic_unit": layer.geologic_unit,
            }


def _measurements(model: Model, filters: Row) -> Iterator[Row]:
    for measurement in model.measurements:
        procedure = None if measurement.procedure is None else measurement.procedure.type
        if (
            filters.get("feature") not in (None, measurement.feature)
            or filters.get("kind") not in (None, measurement.kind)
            or filters.get("procedure") not in (None, procedure)
        ):
            continue
        time = measurement.result_time
        yield {
            "key": measurement.key,
            "gml_id": measurement.gml_id,
            "element": measurement.element,
            "kind": measurement.kind,
            "name": measurement.name,
            "feature": measurement.feature,
            "procedure": procedure,
            "samples": len(measurement.samples),
            "result_time": None if time is None else time.start,
            "tables": len(measurement.results),
            "rows": sum(table.row_count for table in measurement.results),
        }


def _cells(column: Column, *, si: bool) -> tuple[Any, ...]:
    """A column's values, in SI when asked and the unit converts by a factor and an offset."""
    factor, offset = column.si_factor, column.si_offset or 0.0
    if not si or factor is None:
        return column.values
    return tuple(
        v * factor + offset if isinstance(v, int | float) and not isinstance(v, bool) else v
        for v in column.values
    )


def _results(model: Model, filters: Row) -> Iterator[Row]:
    """One row per row of every result table: its place (index columns), then its values.

    Index columns (``position``, ``top``, ``base``, ``time``, ``elapsed_time``) keep
    their names; a result column named like one of them is always ``<name>_value``,
    whatever else is in the frame, so a place and a measured value never share a frame
    column and a column's name does not depend on the filters.
    """
    si = bool(filters.get("si"))
    chosen = [
        measurement
        for measurement in model.measurements
        if filters.get("feature") in (None, measurement.feature)
        and filters.get("procedure")
        in (None, None if measurement.procedure is None else measurement.procedure.type)
        and filters.get("measurement") in (None, measurement.key)
    ]
    for measurement in chosen:
        procedure = None if measurement.procedure is None else measurement.procedure.type
        for number, table in enumerate(measurement.results):
            named: dict[str, tuple[Any, ...]] = {}
            for column in table.domain.index:
                name = column.key or column.name
                while name in named or name in _RESULT_KEYS:
                    name += "_value"
                named[name] = _cells(column, si=si)
            for column in table.columns:
                name = column.key or column.name
                while name in named or name in _RESULT_KEYS or name in _PLACE_NAMES:
                    name += "_value"
                named[name] = _cells(column, si=si)
            count = min((len(cells) for cells in named.values()), default=0)
            for row in range(count):
                yield {
                    "measurement": measurement.key,
                    "feature": measurement.feature,
                    "procedure": procedure,
                    "table": number,
                    "row": row,
                    **{name: cells[row] for name, cells in named.items()},
                }


_BUILDERS: Final[dict[str, Callable[[Model, Row], Iterator[Row]]]] = {
    "features": _features,
    "samples": _samples,
    "layers": _layers,
    "measurements": _measurements,
    "results": _results,
}
#: The columns of a view with no rows, so that an empty frame still has its shape.
_EMPTY: Final[dict[str, tuple[str, ...]]] = {
    "features": (
        "key",
        "gml_id",
        "element",
        "kind",
        "name",
        "x",
        "y",
        "z",
        "crs",
        "lon",
        "lat",
        "total_depth",
        "total_depth_uom",
        "parent",
    ),
    "samples": (
        "key",
        "gml_id",
        "name",
        "feature",
        "top",
        "base",
        "depth_uom",
        "type",
        "method",
        "recovery",
        "recovery_uom",
    ),
    "layers": (
        "key",
        "observation",
        "feature",
        "top",
        "base",
        "depth_uom",
        "description",
        "classification",
        "colour",
        "geologic_unit",
    ),
    "measurements": (
        "key",
        "gml_id",
        "element",
        "kind",
        "name",
        "feature",
        "procedure",
        "samples",
        "result_time",
        "tables",
        "rows",
    ),
    "results": _RESULT_KEYS,
}
