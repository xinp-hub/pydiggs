"""Tables on disk and from a database cursor: CSV files with a Data Package descriptor.

Nothing here imports a database driver or a data-frame library. A folder written by
:func:`write_package` holds one UTF-8 CSV file per table, ``datapackage.json`` (Frictionless
Data Package v2, one Table Schema per table) and ``plan.json`` (the load order).
"""

from __future__ import annotations

import csv
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
    with path.open(encoding="utf-8-sig", newline="") as handle:
        # Cells beyond the header get a column of their own, so they are reported, not lost.
        for row in csv.DictReader(handle, restkey=_BEYOND_HEADER):
            yield dict(row)


def _listed(folder: Path) -> dict[str, Path]:
    """Table name -> CSV file, from the descriptor when there is one, else from file names."""
    descriptor = folder / _DESCRIPTOR
    found: dict[str, Path] = {}
    if descriptor.is_file():
        if descriptor.stat().st_size > _MAX_DESCRIPTOR_BYTES:
            raise TablesError(f"{_DESCRIPTOR} is larger than {_MAX_DESCRIPTOR_BYTES} bytes")
        try:
            described = json.loads(descriptor.read_text(encoding="utf-8"))
            resources = described["resources"]
            pairs = [(str(r["name"]), str(r["path"])) for r in resources]
        except (ValueError, KeyError, TypeError) as exc:
            raise TablesError(f"{_DESCRIPTOR} is not a data package descriptor: {exc}") from exc
        root = folder.resolve()
        for name, relative in pairs:
            path = (folder / relative).resolve()
            if not path.is_relative_to(root):
                raise TablesError(f"{_DESCRIPTOR}: the path of {name!r:.80} leaves the folder")
            found[name] = path
    else:
        found = {path.stem: path for path in sorted(folder.glob("*.csv"))}
    return found


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
    for name, rows in tables.tables.items():
        path = root / f"{name}.csv"
        columns = _columns(name, list(rows))
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows([_cell(row.get(column)) for column in columns] for row in rows)
        written.append(path)
    for file_name, body in (
        (_DESCRIPTOR, tables.descriptor()),
        (_PLAN, tables.plan.to_dict()),
    ):
        path = root / file_name
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written
