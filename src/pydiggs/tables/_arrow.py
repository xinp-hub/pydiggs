"""Rows of a table, whatever holds them: mappings, or an Arrow stream read in batches.

A table's rows reach :func:`~pydiggs.tables.to_model` as an iterable of mappings (a list,
a :class:`csv.DictReader`, :func:`~pydiggs.tables.rows_from_cursor`), or as any object
that exports the Arrow PyCapsule stream interface (``__arrow_c_stream__``): a pyarrow
``Table`` or ``RecordBatchReader``, a polars or pandas data frame, a DuckDB or ADBC
result. An Arrow stream is read one record batch at a time through pyarrow, which is
optional (``pip install 'pydiggs[gis]'`` installs it); pyDIGGS adds no dependency for it.
"""

from __future__ import annotations

import datetime as dt
import decimal
import importlib
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Final, Protocol, cast

from pydiggs.errors import TablesError

__all__ = ["ArrowStream", "Rows", "is_arrow", "rows_of"]


class ArrowStream(Protocol):
    """Any object that exports the Arrow PyCapsule stream interface."""

    def __arrow_c_stream__(self, requested_schema: Any = None) -> Any:
        """The stream, as an Arrow C stream capsule."""
        ...


#: The rows of one table: mappings of column name to value, or an Arrow stream.
Rows = Iterable[Mapping[str, Any]] | ArrowStream

#: The extra that installs pyarrow.
_EXTRA: Final = "gis"


def is_arrow(rows: object) -> bool:
    """Whether ``rows`` exports an Arrow stream (and is read as one).

    Examples:
        >>> is_arrow([{"key": "BH-1"}])
        False
    """
    return callable(getattr(rows, "__arrow_c_stream__", None))


def _cell(value: Any) -> Any:
    """An Arrow value as the profile reads it: dates and times as ISO 8601 text."""
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value


def _arrow_rows(stream: object, table: str) -> Iterator[dict[str, Any]]:
    """The rows of an Arrow stream as dicts, read one record batch at a time."""
    try:  # optional, imported only for Arrow input; untyped, so as a module of Any
        pa: Any = importlib.import_module("pyarrow")
    except ImportError:
        raise TablesError(
            f"table {table!r:.80} is an Arrow stream; reading it needs pyarrow:"
            f" pip install 'pydiggs[{_EXTRA}]'"
        ) from None
    try:
        reader = pa.RecordBatchReader.from_stream(stream)
    except (TypeError, ValueError, pa.ArrowException) as exc:
        raise TablesError(f"table {table!r:.80} is not a readable Arrow stream: {exc}") from exc
    for batch in reader:
        names = batch.schema.names
        columns = [column.to_pylist() for column in batch.columns]
        for values in zip(*columns, strict=True):
            yield {name: _cell(value) for name, value in zip(names, values, strict=True)}


def rows_of(rows: Rows, table: str) -> Iterable[Mapping[str, Any]]:
    """The rows of one table: as given, or read from an Arrow stream batch by batch.

    Args:
        rows: An iterable of mappings, or an object with ``__arrow_c_stream__``.
        table: The table's name, for an error message.

    Returns:
        An iterable of mappings. An Arrow stream is read lazily, one record batch at a
        time; dates and times become ISO 8601 text and decimals text.

    Raises:
        TablesError: ``rows`` is an Arrow stream and pyarrow is not installed, or the
            stream cannot be read.

    Examples:
        >>> rows_of([{"key": "BH-1"}], "features")
        [{'key': 'BH-1'}]
    """
    if is_arrow(rows):
        return _arrow_rows(rows, table)
    return cast("Iterable[Mapping[str, Any]]", rows)  # not Arrow: an iterable of mappings
