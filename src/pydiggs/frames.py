"""Data frames from a model: ``to_pandas`` (the ``frames`` extra) and ``to_polars`` (``polars``).

``import pydiggs`` never imports pandas or polars; each function imports its library
when it is called.

Every function takes a :class:`pydiggs.Model` and a **view**:

``features``
    One row per sampling feature: ``key``, ``gml_id``, ``element``, ``kind``, ``name``;
    the location as written (``x``, ``y``, ``z`` in the CRS's axis order, and ``crs``);
    ``lon`` and ``lat`` when the model has them (a geographic CRS, or after
    :func:`pydiggs.gis.transform`); ``total_depth`` with its unit; ``parent``.
    Filter: ``kind``.
``samples``
    One row per sample: ``key``, ``name``, ``feature``, ``top``, ``base``,
    ``depth_uom``, ``type``, ``method``, ``recovery`` with its unit. Filter: ``feature``.
``layers``
    One row per lithology layer: ``key``, ``observation``, ``feature``, ``top``,
    ``base``, ``depth_uom``, ``description``, ``classification`` (the first code),
    ``colour``, ``geologic_unit``. Filter: ``feature``.
``measurements``
    One row per test, monitor or survey: ``key``, ``element``, ``kind``, ``name``,
    ``feature``, ``procedure``, the number of ``samples``, ``result_time``, and how many
    ``tables`` and ``rows`` of results it has. Filters: ``feature``, ``kind``,
    ``procedure``.
``results`` (the default)
    One row per row of every result table: ``measurement``, ``feature``, ``procedure``,
    ``table`` (its number within the measurement) and ``row``; then where the row is
    (``position``, ``top`` and ``base``, ``time`` or ``elapsed_time``, when the table
    says) and one column per result, named by its dictionary key (``tip_resistance``)
    or, without one, its name. A result column named like a place (``position``,
    ``top``, ``base``, ``time``, ``elapsed_time``) is always ``<name>_value``, so a place
    and a measured value never share a column. Tables with different columns share
    the frame; a cell a table does not have is null. Filters: ``feature``,
    ``procedure``, ``measurement``; ``si=True`` gives values in SI units where the unit
    converts. Filter by ``procedure`` for one tidy table per test type.

A filter value is a node key (``feature``, ``measurement``), a ``kind`` such as
``"borehole"``, or a procedure type such as ``"StaticConePenetrationTest"``. Units are
in the model's columns (``table.column(key).uom``), not in the frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydiggs import _views

if TYPE_CHECKING:
    from pydiggs.model import Model

__all__ = ["VIEWS", "to_pandas", "to_polars"]

#: The names a ``view`` argument takes.
VIEWS: tuple[str, ...] = tuple(_views.VIEWS)


def to_pandas(
    model: Model,
    view: str = "results",
    *,
    feature: str | None = None,
    kind: str | None = None,
    procedure: str | None = None,
    measurement: str | None = None,
    si: bool = False,
) -> Any:
    """A view of ``model`` as a ``pandas.DataFrame`` (needs the ``frames`` extra).

    Args:
        model: From :func:`pydiggs.read`.
        view: ``features``, ``samples``, ``layers``, ``measurements`` or ``results``
            (see the module documentation for their columns).
        feature: Only rows of the feature with this key.
        kind: Only features or measurements of this kind.
        procedure: Only measurements, or results, of this procedure type.
        measurement: Only results of the measurement with this key.
        si: For ``results``: values in SI units where the unit converts.

    Raises:
        ImportError: pandas is not installed.
        ValueError: ``view`` is unknown, or it does not take one of the filters given.

    Examples:
        >>> import pydiggs
        >>> from pydiggs.frames import to_pandas
        >>> m = pydiggs.read("tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml")
        >>> frame = to_pandas(m, "results", procedure="StaticConePenetrationTest")
        >>> list(frame.columns[:5])
        ['measurement', 'feature', 'procedure', 'table', 'row']
    """
    data = _views.columns(
        model,
        view,
        feature=feature,
        kind=kind,
        procedure=procedure,
        measurement=measurement,
        si=si or None,
    )
    return _views.require("pandas", "frames", "pydiggs.frames").DataFrame(data)


def to_polars(
    model: Model,
    view: str = "results",
    *,
    feature: str | None = None,
    kind: str | None = None,
    procedure: str | None = None,
    measurement: str | None = None,
    si: bool = False,
) -> Any:
    """A view of ``model`` as a ``polars.DataFrame`` (needs the ``polars`` extra).

    The same views, filters and columns as :func:`to_pandas`.

    Raises:
        ImportError: polars is not installed.
        ValueError: ``view`` is unknown, or it does not take one of the filters given.
    """
    data = _views.columns(
        model,
        view,
        feature=feature,
        kind=kind,
        procedure=procedure,
        measurement=measurement,
        si=si or None,
    )
    return _views.require("polars", "polars", "pydiggs.frames").DataFrame(data, strict=False)
