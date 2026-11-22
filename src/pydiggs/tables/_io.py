"""Tables on disk and from a database cursor: CSV files with a Data Package descriptor.

Nothing here imports a database driver or a data-frame library. A folder written by
:func:`write_package` holds one UTF-8 CSV file per table, ``datapackage.json`` (Frictionless
Data Package v2, one Table Schema per table) and ``plan.json`` (the load order).
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final

from pydiggs.errors import TablesError
from pydiggs.tables._from_model import TableSet
from pydiggs.tables._profile import RESULTS_PREFIX, TABLES

__all__ = ["read_package", "rows_from_cursor", "write_package"]

_DESCRIPTOR: Final = "datapackage.json"
_PLAN: Final = "plan.json"
#: Largest descriptor read (it lists tables and columns, never data).
_MAX_DESCRIPTOR_BYTES: Final = 8 * 1024 * 1024
#: The column that holds the cells a CSV row has beyond its header.
_BEYOND_HEADER: Final = "(cells beyond the header)"
#: Longest CSV cell read (a JSON sub-tree in the extra table can be large).
_MAX_FIELD_CHARS: Final = 64 * 1024 * 1024


def rows_from_cursor(cursor: Any) -> Iterator[dict[str, Any]]:
    """The rows of an executed DB-API cursor as dicts, one at a time.

    The cursor is any object with ``description`` and iteration over rows (PEP 249):
    ``sqlite3``, ``pyodbc``, ``psycopg``, ``oracledb`` … . pyDIGGS opens no connection
    and imports no driver; you run the query, typically a view that shapes your schema
    into a profile table.

    Raises:
        TablesError: The cursor has no result set (``description`` is None).

    Examples:
        >>> import sqlite3
        >>> db = sqlite3.connect(":memory:")
        >>> cursor = db.execute("SELECT 'BH-1' AS key, 'BH-1' AS name, 12.5 AS total_depth")
        >>> list(rows_from_cursor(cursor))
        [{'key': 'BH-1', 'name': 'BH-1', 'total_depth': 12.5}]
    """
    described = getattr(cursor, "description", None)
    if described is None:
        raise TablesError("the cursor has no result set: execute a SELECT first")
    names = [str(column[0]) for column in described]
    for row in cursor:
        yield dict(zip(names, row, strict=True))


def _is_table(name: str) -> bool:
    return name in TABLES or (name.startswith(RESULTS_PREFIX) and len(name) > len(RESULTS_PREFIX))


def _csv_rows(path: Path) -> Iterator[dict[str, Any]]:
    """The rows of one CSV file; a file that is not UTF-8 CSV is a :class:`TablesError`."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield from _parsed(handle, path.name)
    except UnicodeDecodeError as exc:
        raise _not_utf8(path.name, exc) from exc


def _not_utf8(name: str, exc: UnicodeDecodeError) -> TablesError:
    return TablesError(f"{name!r:.120} is not UTF-8 text (byte {exc.start}): save it as UTF-8 CSV")


def _parsed(handle: Any, name: str) -> Iterator[dict[str, Any]]:
    """The rows of an open CSV text stream, read strictly; bad CSV is a TablesError."""
    reader = csv.DictReader(handle, restkey=_BEYOND_HEADER, strict=True)
    try:
        # Cells beyond the header get a column of their own, so they are reported.
        while (row := _next_row(reader)) is not None:
            yield row
    except csv.Error as exc:
        raise TablesError(
            f"{name!r:.120} is not a readable CSV file at line {reader.line_num}: {exc}"
        ) from exc


def _next_row(reader: csv.DictReader[str]) -> dict[str, Any] | None:
    """The next row, read with a cell limit of 64 MiB, or None at the end.

    A cell of the extra table can hold a whole DIGGS sub-tree as JSON, far over the csv
    module's 128 KiB default. That limit is process-wide, so it is raised only while this
    one row is parsed and put back before the row is returned: other code never sees it.
    """
    before = csv.field_size_limit(max(csv.field_size_limit(), _MAX_FIELD_CHARS))
    try:
        return dict(next(reader))
    except StopIteration:
        return None
    finally:
        csv.field_size_limit(before)


def _resources(text: str) -> list[tuple[str, str]]:
    """``(name, path)`` of each resource a descriptor lists, each name once."""
    try:
        described = json.loads(text)
        resources = described["resources"]
        pairs = [(str(r["name"]), r["path"]) for r in resources]
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        reason = "nested too deeply" if isinstance(exc, RecursionError) else str(exc)
        raise TablesError(f"{_DESCRIPTOR} is not a data package descriptor: {reason}") from exc
    seen: set[str] = set()
    for name, path in pairs:
        if not isinstance(path, str):  # Data Package v2 allows a list of chunks
            raise TablesError(
                f"{_DESCRIPTOR}: the path of {name!r:.80} must be one file name; a table"
                " split over several files is not read"
            )
        if name in seen:
            raise TablesError(f"{_DESCRIPTOR}: the table {name!r:.80} is listed twice")
        seen.add(name)
    return [(name, str(path)) for name, path in pairs]


def _listed(folder: Path) -> dict[str, Path]:
    """Table name -> CSV file, from the descriptor when there is one, else from file names."""
    descriptor = folder / _DESCRIPTOR
    found: dict[str, Path] = {}
    if descriptor.is_file():
        if descriptor.stat().st_size > _MAX_DESCRIPTOR_BYTES:
            raise TablesError(f"{_DESCRIPTOR} is larger than {_MAX_DESCRIPTOR_BYTES} bytes")
        pairs = _resources(descriptor.read_text(encoding="utf-8"))
        for name, relative in pairs:
            found[name] = _inside(folder, folder / relative, name)
    else:
        found = {
            path.stem: _inside(folder, path, path.stem) for path in sorted(folder.glob("*.csv"))
        }
    return found


def _inside(folder: Path, path: Path, name: str) -> Path:
    """``path`` resolved, refused when it (or a link it is) leaves ``folder``."""
    resolved = path.resolve()
    if not resolved.is_relative_to(folder.resolve()):
        raise TablesError(f"the file of table {name!r:.80} leaves the folder (a path or a link)")
    return resolved


def read_package(folder: str | os.PathLike[str]) -> dict[str, Iterable[dict[str, Any]]]:
    r"""The tables of a folder of CSV files, ready for :func:`~pydiggs.tables.to_model`.

    With a ``datapackage.json`` the tables are the resources it lists; without one,
    every ``<table>.csv`` in the folder. Each table is read when it is first iterated, a
    row at a time. Cells are text; an empty cell is a missing value.

    Raises:
        TablesError: The folder does not exist, the descriptor cannot be read or names a
            file outside the folder, or a file is not a table of the profile.

    Examples:
        >>> import tempfile, pathlib
        >>> from pydiggs import tables
        >>> folder = pathlib.Path(tempfile.mkdtemp())
        >>> _ = (folder / "features.csv").write_text("key,name,x,y,depth_uom\nBH-1,BH-1,1,2,m\n")
        >>> result = tables.to_model(read_package(folder))
        >>> result.model.features[0].location.coordinates
        (1.0, 2.0)
    """
    root = Path(folder)
    if not root.is_dir():
        raise TablesError(f"{str(root)!r:.200} is not a folder")
    out: dict[str, Iterable[dict[str, Any]]] = {}
    for name, path in _listed(root).items():
        if not _is_table(name):
            raise TablesError(
                f"{name!r:.80} is not a table of the profile ({', '.join(TABLES)}, or"
                f" {RESULTS_PREFIX}<procedure>)"
            )
        if not path.is_file():
            raise TablesError(f"table {name!r:.80} has no file {path.name!r:.120}")
        out[name] = _Lazy(path)
    return out


class _Lazy:
    """The rows of one CSV file, read each time it is iterated."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return _csv_rows(self._path)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:  # noqa: PLR2004 - exact as an integer
            return str(int(value))
        return repr(value)
    return str(value)


def _columns(name: str, rows: list[Mapping[str, Any]]) -> list[str]:
    names: dict[str, None] = dict.fromkeys(TABLES[name].names()) if name in TABLES else {}
    for row in rows:
        names.update(dict.fromkeys(row))
    return list(names)


def package_files(tables: TableSet) -> list[tuple[str, bytes]]:
    """The files :func:`write_package` writes, as ``(name, bytes)``, in the same order.

    Examples:
        >>> from pydiggs import tables
        >>> made = tables.to_model({"features": [{"key": "BH-1", "depth_uom": "m"}]})
        >>> files = package_files(tables.from_model(made.model))
        >>> [name for name, _ in files][-3:]
        ['features.csv', 'datapackage.json', 'plan.json']
    """
    out: list[tuple[str, bytes]] = []
    for name, rows in tables.tables.items():
        columns = _columns(name, list(rows))
        handle = io.StringIO(newline="")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([_cell(row.get(column)) for column in columns] for row in rows)
        out.append((f"{name}.csv", handle.getvalue().encode("utf-8")))
    for file_name, body in (
        (_DESCRIPTOR, tables.descriptor()),
        (_PLAN, tables.plan.to_dict()),
    ):
        text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        out.append((file_name, text.encode("utf-8")))
    return out


def tables_from_files(files: Mapping[str, bytes]) -> dict[str, Iterable[dict[str, Any]]]:
    r"""The tables of a data package held in memory, as :func:`read_package` reads a folder.

    ``files`` maps a file name (a plain name, no folder) to its bytes. With a
    ``datapackage.json`` the tables are the resources it lists, each of which must be
    one of ``files``; without one, every ``<table>.csv``. Each table is decoded and
    parsed when it is first iterated.

    Raises:
        TablesError: The descriptor cannot be read, a resource has no file, or a file is
            not a table of the profile.

    Examples:
        >>> found = tables_from_files({"features.csv": b"key,depth_uom\nBH-1,m\n"})
        >>> [list(rows) for rows in found.values()]
        [[{'key': 'BH-1', 'depth_uom': 'm'}]]
    """
    descriptor = files.get(_DESCRIPTOR)
    if descriptor is not None:
        if len(descriptor) > _MAX_DESCRIPTOR_BYTES:
            raise TablesError(f"{_DESCRIPTOR} is larger than {_MAX_DESCRIPTOR_BYTES} bytes")
        try:
            pairs = _resources(descriptor.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise _not_utf8(_DESCRIPTOR, exc) from exc
    else:
        pairs = [(name[: -len(".csv")], name) for name in sorted(files) if name.endswith(".csv")]
    out: dict[str, Iterable[dict[str, Any]]] = {}
    for name, relative in pairs:
        if not _is_table(name):
            raise TablesError(
                f"{name!r:.80} is not a table of the profile ({', '.join(TABLES)}, or"
                f" {RESULTS_PREFIX}<procedure>)"
            )
        data = files.get(relative.removeprefix("./"))
        if data is None:
            raise TablesError(f"table {name!r:.80} has no file {relative!r:.120}")
        out[name] = _InMemory(data, relative)
    return out


class _InMemory:
    """The rows of one CSV file held as bytes, parsed each time it is iterated."""

    def __init__(self, data: bytes, name: str = "a table of the package") -> None:
        self._data = data
        self._name = name

    def __iter__(self) -> Iterator[dict[str, Any]]:
        try:
            text = self._data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise _not_utf8(self._name, exc) from exc
        yield from _parsed(io.StringIO(text, newline=""), self._name)


def write_package(tables: TableSet, folder: str | os.PathLike[str]) -> list[Path]:
    """Write ``tables`` to ``folder``: a CSV per table, ``datapackage.json`` and ``plan.json``.

    The folder is created when it is missing; files of the same names are replaced.
    CSV files are UTF-8 with a header row; a missing value is an empty cell. Cells hold
    the values of the source file as they are: open them as data, not in a spreadsheet
    that runs formulas.

    Returns:
        The files written, in load order, then the descriptor and the plan.

    Examples:
        >>> import tempfile
        >>> from pydiggs import tables
        >>> made = tables.to_model(
        ...     {"features": [{"key": "BH-1", "name": "BH-1", "depth_uom": "m"}]}
        ... )
        >>> written = write_package(tables.from_model(made.model), tempfile.mkdtemp())
        >>> [path.name for path in written]
        ['document.csv', 'projects.csv', 'features.csv', 'datapackage.json', 'plan.json']
    """
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name, data in package_files(tables):
        path = root / file_name
        path.write_bytes(data)
        written.append(path)
    return written
