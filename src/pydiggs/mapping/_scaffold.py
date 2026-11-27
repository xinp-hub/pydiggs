"""Starting a mapping, and measuring one: the scaffold, the coverage and the source readers.

Nothing here imports a database driver. The source schema is plain data: a mapping of
source table name to its column names, read from a folder of CSV files, from a cursor,
or written by hand. A guess the scaffold makes comes from a column's name alone and is
always marked ``# guess`` in the text it writes.
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydiggs.errors import MappingError
from pydiggs.mapping._format import MAPPING_FORMAT_VERSION
from pydiggs.mapping._format import Mapping as MappingFile
from pydiggs.tables._io import _csv_rows
from pydiggs.tables._profile import PROFILE_VERSION, TABLES

__all__ = [
    "Coverage",
    "columns_from_cursor",
    "coverage",
    "read_source",
    "scaffold",
    "schema_from_csv",
]

#: Longest header line read from a CSV file.
_MAX_HEADER_BYTES: Final = 1024 * 1024
_MAX_TABLES: Final = 2000
_WORD: Final = re.compile(r"[^a-z0-9]+")

#: Source table names that suggest a profile table.
_TABLE_GUESSES: Final = {
    "features": ("hole", "borehole", "boring", "location", "point", "feature", "loca"),
    "projects": ("project", "proj"),
    "samples": ("sample", "samp"),
    "layers": ("stratum", "strata", "layer", "lithology", "geology", "geol"),
}
#: Source column names that suggest the feature a row belongs to.
_FEATURE_NAMES: Final = (
    "hole",
    "hole_id",
    "borehole",
    "borehole_id",
    "boring",
    "boring_id",
    "location",
    "location_id",
    "loca_id",
    "point_id",
    "feature",
    "feature_id",
)
_DEPTH_GUESSES: Final = {
    "top": ("top", "depth_top", "top_depth", "depth_from", "from_depth", "from"),
    "base": ("base", "depth_base", "base_depth", "bottom", "depth_to", "to_depth", "to"),
}
_COMMON_GUESSES: Final = {
    "name": ("name", "label", "title"),
    "description": ("description", "desc"),
}
#: Per profile table: target column -> the source column names that suggest it.
_COLUMN_GUESSES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "projects": {"key": ("id", "key", "project_id", "project_no", "project"), **_COMMON_GUESSES},
    "features": {
        "key": ("id", "key", *_FEATURE_NAMES),
        **_COMMON_GUESSES,
        "name": (*_COMMON_GUESSES["name"], "hole_name", "borehole_name", "boring_name"),
        "project": ("project", "project_id", "project_no"),
        "x": ("x", "east", "easting", "longitude", "lon"),
        "y": ("y", "north", "northing", "latitude", "lat"),
        "z": ("z", "elevation", "elev", "ground_level"),
        "total_depth": ("total_depth", "final_depth", "hole_depth", "depth"),
        "start": ("start", "start_date", "date_start"),
        "end": ("end", "end_date", "date_end"),
    },
    "samples": {
        "key": ("id", "key", "sample_id", "sample"),
        "feature": _FEATURE_NAMES,
        **_COMMON_GUESSES,
        **_DEPTH_GUESSES,
        "type": ("type", "sample_type"),
    },
    "layers": {
        "key": ("id", "key", "layer_id", "stratum_id"),
        "feature": _FEATURE_NAMES,
        **_DEPTH_GUESSES,
        "description": (*_COMMON_GUESSES["description"], "soil_description"),
        "legend_code": ("legend", "legend_code"),
        "geologic_unit": ("geologic_unit", "geology", "formation"),
    },
}


def _plain(name: str) -> str:
    """A name in lower case with every run of other characters as one underscore."""
    return _WORD.sub("_", name.lower()).strip("_")


def _quoted(text: str) -> str:
    """``text`` as a TOML basic string, with every control character escaped."""
    out = ['"']
    for ch in text:
        if ch in ('"', "\\"):
            out.append(f"\\{ch}")
        elif ch.isprintable() and ch != "\x7f":
            out.append(ch)
        elif ord(ch) <= 0xFFFF:  # noqa: PLR2004 - the end of the basic plane
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(f"\\U{ord(ch):08x}")
    out.append('"')
    return "".join(out)


def _loadable(text: str) -> bool:
    """True when :func:`~pydiggs.mapping.load` accepts ``text`` as a name: no control character."""
    return bool(text.strip()) and all(ch.isprintable() and ch != "\x7f" for ch in text)


def _clean(text: str, default: str) -> str:
    """``text`` with each run of control characters and white space as one space."""
    kept = "".join(ch if ch.isprintable() and ch != "\x7f" else " " for ch in text)
    return " ".join(kept.split()) or default


def _guess_table(source: str) -> str | None:
    plain = _plain(source)
    singular = plain.removesuffix("s")
    for target, names in _TABLE_GUESSES.items():
        if plain in names or singular in names:
            return target
    return None


def _guess_columns(target: str, columns: Iterable[str]) -> dict[str, str]:
    """Target column -> the first source column whose name suggests it."""
    by_plain: dict[str, str] = {}
    for column in columns:
        by_plain.setdefault(_plain(column), column)
    found: dict[str, str] = {}
    taken: set[str] = set()
    for column, names in _COLUMN_GUESSES[target].items():
        match = next((by_plain[n] for n in names if n in by_plain), None)
        if match is not None and match not in taken:
            found[column] = match
            taken.add(match)
    return found


def _schema(source_schema: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    if not isinstance(source_schema, Mapping):
        raise MappingError("the source schema is a mapping of table name to column names")
    if len(source_schema) > _MAX_TABLES:
        raise MappingError(f"the source schema has more than {_MAX_TABLES} tables")
    out: dict[str, list[str]] = {}
    for table, columns in source_schema.items():
        if isinstance(columns, str):
            raise MappingError(f"the columns of {str(table)!r:.80} are a list of names, not text")
        out[str(table)] = [str(column) for column in columns]
    return out


def scaffold(source_schema: Mapping[str, Iterable[str]], *, name: str = "my-mapping") -> str:
    """The TOML text of a mapping to fill in, from a description of a source schema (**preview**).

    Each source table gets a ``[[tables]]`` entry. Where a table's name suggests a
    profile table (``holes`` suggests ``features``) the entry is written out, and where a
    column's name suggests a profile column (``depth_top`` suggests ``top``) it is
    filled in. Every such line ends with ``# guess``: a guess comes from the name alone
    and is yours to check. Everything else is written as a comment that lists the
    table's columns, to complete by hand. The text loads with
    :func:`~pydiggs.mapping.load` as it is; its ``method`` is ``generated``.

    Args:
        source_schema: Source table name to its column names. See
            :func:`~pydiggs.mapping.schema_from_csv` and
            :func:`~pydiggs.mapping.columns_from_cursor`.
        name: The name the mapping is given; control characters become spaces.

    Returns:
        The TOML text.

    Raises:
        MappingError: The source schema is not a mapping of table name to column names.

    Examples:
        >>> text = scaffold({"holes": ["hole_id", "easting", "rig"], "spt": ["hole_id", "n"]})
        >>> print(text[text.index("[[tables]]") :].rstrip())
        [[tables]]
        source = "holes"
        target = "features"  # guess
        [tables.columns]
        key = "hole_id"  # guess
        x = "easting"  # guess
        # not mapped yet: "rig"
        <BLANKLINE>
        # [[tables]]
        # source = "spt"
        # target = "?"  (a table of the profile, or results_<procedure>)
        # [tables.columns]
        # not mapped yet: "hole_id", "n"
    """
    schema = _schema(source_schema)
    lines = [
        "# A diggs-mapping file, started by pydiggs.mapping.scaffold. Fill it in, then run",
        "#   pydiggs map check FOLDER --mapping THIS_FILE",
        "#   pydiggs map test FOLDER --mapping THIS_FILE",
        '# Lines marked "# guess" were filled in from a name alone: check each one.',
        f'mapping_format = "{MAPPING_FORMAT_VERSION}"',
        f"name = {_quoted(_clean(name, 'my-mapping'))}",
        'version = "0.1"',
        'method = "generated"  # manual, assisted or generated: change it when you have edited',
        '# creator = "who wrote this mapping"',
        '# licence = "the licence you share it under"',
        '# date = "YYYY-MM-DD"',
        f'profile_version = "{PROFILE_VERSION}"',
        "",
        "# [source]",
        '# schema = "the name of your database schema"',
        '# version = "its version"',
        "",
        "# Units as your data writes them = the DIGGS unit. A rename only: no number changes.",
        "[units]",
        '# "metres" = "m"',
        "",
        "# A code map: your code = the DIGGS code, and how well it matches",
        "# (exact, close, broader, narrower).",
        "# [codes.sampler]",
        '# code_space = "https://diggsml.org/def/codes/DIGGS/0.1/sampleType.xml"',
        "# [codes.sampler.map]",
        '# "U" = { code = "open_tube", match = "close" }',
    ]
    for source, columns in schema.items():
        lines.append("")
        # A name the loader would refuse is never written as live text, only as a comment.
        target = _guess_table(source) if _loadable(source) else None
        loadable = [column for column in columns if _loadable(column)]
        guessed = {} if target is None else _guess_columns(target, loadable)
        if target is None or not guessed:
            lines += [
                "# [[tables]]",
                f"# source = {_quoted(source)}",
                '# target = "?"  (a table of the profile, or results_<procedure>)',
                "# [tables.columns]",
            ]
            left = columns
        else:
            lines += [
                "[[tables]]",
                f"source = {_quoted(source)}",
                f'target = "{target}"  # guess',
                "[tables.columns]",
            ]
            lines += [f"{column} = {_quoted(given)}  # guess" for column, given in guessed.items()]
            left = [column for column in columns if column not in guessed.values()]
        if left:
            lines.append(f"# not mapped yet: {', '.join(_quoted(column) for column in left)}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of a source schema a mapping reads, and how much of the profile it fills.

    ``source_mapped`` and ``source_unmapped`` list, per source table, the columns the
    mapping reads and does not read; ``source_missing`` the columns it reads that the
    schema does not have. ``profile_filled`` and ``profile_empty`` list, per profile
    table the mapping fills, the columns it fills and leaves empty. The percentages are
    of all source columns, and of all columns of the profile tables the mapping fills
    (a result table has no fixed columns, so it is not counted).

    Examples:
        >>> from pydiggs import mapping
        >>> schema = {"holes": ["hole_id", "rig"]}
        >>> covered = mapping.coverage(mapping.load(mapping.scaffold(schema)), schema)
        >>> covered.source_unmapped, covered.source_percent
        ({'holes': ('rig',)}, 50.0)
    """

    source_mapped: dict[str, tuple[str, ...]]
    source_unmapped: dict[str, tuple[str, ...]]
    source_missing: dict[str, tuple[str, ...]]
    profile_filled: dict[str, tuple[str, ...]]
    profile_empty: dict[str, tuple[str, ...]]
    source_percent: float
    profile_percent: float

    def to_dict(self) -> dict[str, Any]:
        """The coverage as plain data, for JSON."""
        return {
            "source_percent": self.source_percent,
            "profile_percent": self.profile_percent,
            "source_mapped": {k: list(v) for k, v in self.source_mapped.items()},
            "source_unmapped": {k: list(v) for k, v in self.source_unmapped.items()},
            "source_missing": {k: list(v) for k, v in self.source_missing.items()},
            "profile_filled": {k: list(v) for k, v in self.profile_filled.items()},
            "profile_empty": {k: list(v) for k, v in self.profile_empty.items()},
        }


def _percent(part: int, whole: int) -> float:
    return 100.0 if whole == 0 else round(100.0 * part / whole, 1)


def coverage(mapping: MappingFile, source_schema: Mapping[str, Iterable[str]]) -> Coverage:
    """Which source columns ``mapping`` reads, and which profile columns it fills (**preview**).

    Args:
        mapping: The loaded mapping.
        source_schema: Source table name to its column names.

    Returns:
        The coverage. A column in ``source_unmapped`` is one whose values would be
        reported as ``tables.mapping-unmapped-source`` when the mapping is applied.

    Raises:
        MappingError: The source schema is not a mapping of table name to column names.

    Examples:
        >>> from pydiggs import mapping as mapping_module
        >>> schema = {"holes": ["hole_id", "easting", "northing", "rig"]}
        >>> loaded = mapping_module.load(mapping_module.scaffold(schema))
        >>> covered = coverage(loaded, schema)
        >>> covered.source_mapped, covered.source_unmapped, covered.source_percent
        ({'holes': ('hole_id', 'easting', 'northing')}, {'holes': ('rig',)}, 75.0)
        >>> covered.profile_filled, covered.profile_percent
        ({'features': ('key', 'x', 'y')}, 17.6)
    """
    schema = _schema(source_schema)
    reads = mapping.reads()
    mapped: dict[str, tuple[str, ...]] = {}
    unmapped: dict[str, tuple[str, ...]] = {}
    missing: dict[str, tuple[str, ...]] = {}
    for source, columns in schema.items():
        used = set(reads.get(source, ()))
        mapped[source] = tuple(column for column in columns if column in used)
        unmapped[source] = tuple(column for column in columns if column not in used)
    for source, used_columns in reads.items():
        known = set(schema.get(source, ()))
        absent = tuple(column for column in used_columns if column not in known)
        if absent:
            missing[source] = absent
    filled: dict[str, dict[str, None]] = {}
    for table in mapping.tables:
        filled.setdefault(table.target, {}).update(dict.fromkeys(table.columns))
        if table.params:
            filled.setdefault("params", {}).update(dict.fromkeys(("node", "name", "value")))
    empty = {
        target: tuple(name for name in TABLES[target].names() if name not in columns)
        for target, columns in filled.items()
        if target in TABLES
    }
    fixed_filled = sum(
        sum(1 for name in columns if name in TABLES[target].names())
        for target, columns in filled.items()
        if target in TABLES
    )
    fixed_all = sum(len(TABLES[target].names()) for target in filled if target in TABLES)
    return Coverage(
        source_mapped={k: v for k, v in mapped.items() if v},
        source_unmapped={k: v for k, v in unmapped.items() if v},
        source_missing=missing,
        profile_filled={target: tuple(columns) for target, columns in filled.items()},
        profile_empty={k: v for k, v in empty.items() if v},
        source_percent=_percent(
            sum(len(v) for v in mapped.values()), sum(len(v) for v in schema.values())
        ),
        profile_percent=_percent(fixed_filled, fixed_all),
    )


def columns_from_cursor(cursor: Any) -> list[str]:
    """The column names of an executed DB-API cursor, for a source schema (**preview**).

    pyDIGGS opens no connection and imports no driver: you run a query on each of your
    tables (``SELECT * FROM holes WHERE 1 = 0`` is enough) and collect the names.

    Raises:
        MappingError: The cursor has no result set (``description`` is None).

    Examples:
        >>> import sqlite3
        >>> db = sqlite3.connect(":memory:")
        >>> _ = db.execute("CREATE TABLE holes (hole_id TEXT, easting REAL)")
        >>> {"holes": columns_from_cursor(db.execute("SELECT * FROM holes WHERE 1 = 0"))}
        {'holes': ['hole_id', 'easting']}
    """
    described = getattr(cursor, "description", None)
    if described is None:
        raise MappingError("the cursor has no result set: execute a SELECT first")
    return [str(column[0]) for column in described]


def _csv_files(folder: str | os.PathLike[str]) -> dict[str, Path]:
    """Table name -> CSV file, for the files directly in ``folder`` and nowhere else."""
    root = Path(folder)
    if not root.is_dir():
        raise MappingError(f"{str(root)!r:.200} is not a folder")
    inside = root.resolve()
    found: dict[str, Path] = {}
    for path in sorted(root.glob("*.csv")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(inside):
            raise MappingError(f"{path.name!r:.120} is a link that leaves the folder")
        found[path.stem] = path
    return found


def schema_from_csv(folder: str | os.PathLike[str]) -> dict[str, list[str]]:
    r"""The source schema of a folder of CSV files: file name to header row (**preview**).

    Every ``<table>.csv`` directly in the folder is a table; only its first line is read.

    Raises:
        MappingError: The folder does not exist, or a file is a link out of it or
            cannot be read as UTF-8 CSV.

    Examples:
        >>> import pathlib, tempfile
        >>> folder = pathlib.Path(tempfile.mkdtemp())
        >>> _ = (folder / "holes.csv").write_text("hole_id,easting\nBH1,100\n")
        >>> schema_from_csv(folder)
        {'holes': ['hole_id', 'easting']}
    """
    schema: dict[str, list[str]] = {}
    for name, path in _csv_files(folder).items():
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                first = handle.readline(_MAX_HEADER_BYTES + 1)
        except (OSError, UnicodeDecodeError) as exc:
            raise MappingError(f"cannot read the header of {path.name!r:.120}: {exc}") from exc
        if len(first) > _MAX_HEADER_BYTES:
            raise MappingError(f"the header of {path.name!r:.120} is too long")
        try:
            schema[name] = next(csv.reader([first]), [])
        except csv.Error as exc:
            raise MappingError(f"the header of {path.name!r:.120} is not CSV: {exc}") from exc
    return schema


class _Lazy:
    """The rows of one CSV file, read each time it is iterated."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return _csv_rows(self._path)


def read_source(folder: str | os.PathLike[str]) -> dict[str, Iterable[dict[str, Any]]]:
    r"""The source tables of a folder of CSV files, ready for ``Mapping.apply`` (**preview**).

    Every ``<table>.csv`` directly in the folder is a source table, whatever its name
    (unlike :func:`pydiggs.tables.read_package`, which reads tables of the profile).
    Each is read when it is iterated, a row at a time. Cells are text; cells beyond the
    header get a column of their own, so they are reported, not lost.

    Raises:
        MappingError: The folder does not exist, or a file is a link out of it.

    Examples:
        >>> import pathlib, tempfile
        >>> folder = pathlib.Path(tempfile.mkdtemp())
        >>> _ = (folder / "holes.csv").write_text("hole_id,easting\nBH1,100\n")
        >>> {name: list(rows) for name, rows in read_source(folder).items()}
        {'holes': [{'hole_id': 'BH1', 'easting': '100'}]}
    """
    return {name: _Lazy(path) for name, path in _csv_files(folder).items()}
