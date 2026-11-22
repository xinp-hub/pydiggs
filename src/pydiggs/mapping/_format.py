"""The ``diggs-mapping`` 1.0 document: its typed form, and loading it strictly.

A mapping file is TOML (read with :mod:`tomllib`); its format is
``diggs-mapping-1.schema.json``. Loading checks the shape the engine relies on: closed
sets of keys, one source per column, templates that only substitute columns, code maps
that exist. Every error names the place in the file. Whether the targets are tables and
columns of the profile, and the units and codes DIGGS ones, is
:func:`pydiggs.mapping.check`: a file can be loaded and still be wrong.

A mapping file comes from anywhere: it is bounded in size, and nothing in it is ever
evaluated. A template is plain ``{column}`` substitution done here, not ``str.format``.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import tomllib
from collections.abc import Iterable
from collections.abc import Mapping as Map
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from pydiggs.errors import MappingError
from pydiggs.tables._profile import PROFILE_VERSION, RESULTS_PREFIX

if TYPE_CHECKING:
    from pydiggs.mapping._apply import Applied

__all__ = [
    "MAPPING_FORMAT_VERSION",
    "MATCHES",
    "METHODS",
    "CodeEntry",
    "CodeMap",
    "ColumnMap",
    "Mapping",
    "ParamMap",
    "TableMap",
    "Template",
    "load",
    "mapping_schema",
]

#: Version of the ``diggs-mapping`` format this release reads.
MAPPING_FORMAT_VERSION: Final = "1.0"
#: How a mapping was made.
METHODS: Final = ("manual", "assisted", "generated")
#: How well a source code matches the DIGGS code it is mapped to (the SKOS words).
MATCHES: Final = ("exact", "close", "broader", "narrower")
#: Largest mapping file read.
MAX_MAPPING_BYTES: Final = 1024 * 1024
_MAX_TEXT: Final = 500
_SCHEMA_FILE: Final = "diggs-mapping-1.schema.json"
_TARGET_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,119}")
_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
#: What a template field may not hold: these would be attribute access, indexing,
#: a conversion or a format specification in ``str.format``.
_NOT_IN_FIELD: Final = frozenset(".[]!:")

_TOP_KEYS: Final = frozenset(
    {
        "mapping_format",
        "name",
        "version",
        "creator",
        "method",
        "licence",
        "date",
        "profile_version",
        "source",
        "units",
        "codes",
        "tables",
    }
)
_REQUIRED: Final = ("mapping_format", "name", "version", "method", "profile_version")
_VALUE_KEYS: Final = ("from", "value", "template")
_COLUMN_KEYS: Final = frozenset({*_VALUE_KEYS, "units", "codes", "property", "uom"})
_PARAM_KEYS: Final = frozenset({*_VALUE_KEYS, "name", "scope", "uom", "uom_from", "codes"})


@dataclass(frozen=True, slots=True)
class Template:
    """A text built from source columns: literal pieces and the columns between them.

    ``parts`` alternates literal text and column names, starting and ending with a
    literal: ``"{hole}-SPT-{seq}"`` is ``("", "hole", "-SPT-", "seq", "")``.
    """

    text: str
    parts: tuple[str, ...]

    @property
    def columns(self) -> tuple[str, ...]:
        """The source columns the template reads, in order."""
        return self.parts[1::2]

    def render(self, values: Map[str, str | None]) -> str | None:
        """The text for one row, or None when a column it reads is missing or blank."""
        out: list[str] = []
        for index, part in enumerate(self.parts):
            if index % 2 == 0:
                out.append(part)
                continue
            value = values.get(part)
            if value is None:
                return None
            out.append(value)
        return "".join(out)


def parse_template(text: str, where: str) -> Template:
    """Parse a key template: ``{column}`` is a source column, ``{{`` and ``}}`` are braces.

    Raises:
        MappingError: A brace is not closed, a field is empty, or a field holds ``.``,
            ``[``, ``]``, ``!`` or ``:``.

    Examples:
        >>> parse_template("{hole}-SPT-{seq}", "tables[1].columns.key").columns
        ('hole', 'seq')
    """
    parts: list[str] = []
    literal: list[str] = []
    at = 0
    while at < len(text):
        ch = text[at]
        if text.startswith(("{{", "}}"), at):
            literal.append(ch)
            at += 2
        elif ch == "{":
            end = text.find("}", at)
            name = text[at + 1 : end] if end > 0 else ""
            if end < 0 or not name.strip() or "{" in name:
                raise MappingError(f"{where}: the template has a brace that names no column")
            if _NOT_IN_FIELD & set(name):
                raise MappingError(
                    f"{where}: a template field is a plain column name; {name!r:.80} holds"
                    " one of . [ ] ! :"
                )
            parts += ["".join(literal), name]
            literal = []
            at = end + 1
        elif ch == "}":
            raise MappingError(f"{where}: the template has a closing brace with no opening one")
        else:
            literal.append(ch)
            at += 1
    if not parts:
        raise MappingError(f"{where}: the template names no column; use `value` for a constant")
    return Template(text=text, parts=(*parts, "".join(literal)))


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeEntry:
    """One source code: the DIGGS code it stands for, and how well it matches."""

    code: str
    match: str = "exact"


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeMap:
    """A named code map: source code to DIGGS code, and the code list of the DIGGS codes."""

    name: str
    entries: Map[str, CodeEntry]
    code_space: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ColumnMap:
    """Where one target column's value comes from.

    Exactly one of ``source`` (a source column, ``from`` in the file), ``value`` (a
    constant) and ``template`` is set. ``units`` renames the value through the unit
    table; ``codes`` names the code map it is translated by. ``property_key`` (``property``
    in the file) and ``uom``
    describe a result column and are only allowed in a ``results_<procedure>`` table.
    """

    target: str
    source: str | None = None
    value: str | int | float | bool | None = None
    template: Template | None = None
    units: bool = False
    codes: str | None = None
    property_key: str | None = None
    uom: str | None = None

    @property
    def reads(self) -> tuple[str, ...]:
        """The source columns this column reads."""
        if self.source is not None:
            return (self.source,)
        return () if self.template is None else self.template.columns


@dataclass(frozen=True, slots=True, kw_only=True)
class ParamMap:
    """A ``params`` row made for each row of the table: a value under its DIGGS name.

    The row's ``node`` is the key of the table's row. ``uom`` is a DIGGS unit;
    ``uom_from`` is a source column whose text is renamed through the unit table.
    """

    name: str
    scope: str | None = None
    source: str | None = None
    value: str | int | float | bool | None = None
    template: Template | None = None
    uom: str | None = None
    uom_from: str | None = None
    codes: str | None = None

    @property
    def reads(self) -> tuple[str, ...]:
        """The source columns this parameter reads."""
        own = (
            (self.source,)
            if self.source is not None
            else (() if self.template is None else self.template.columns)
        )
        return own if self.uom_from is None else (*own, self.uom_from)


@dataclass(frozen=True, slots=True, kw_only=True)
class TableMap:
    """One ``[[tables]]`` entry: a source table sent to one table of the profile."""

    source: str
    target: str
    columns: Map[str, ColumnMap]
    params: tuple[ParamMap, ...] = ()

    @property
    def is_results(self) -> bool:
        """True when the target is a ``results_<procedure>`` table."""
        return self.target.startswith(RESULTS_PREFIX)

    @property
    def reads(self) -> tuple[str, ...]:
        """The source columns this entry reads, each once, in order."""
        found: dict[str, None] = {}
        for column in self.columns.values():
            found.update(dict.fromkeys(column.reads))
        for param in self.params:
            found.update(dict.fromkeys(param.reads))
        return tuple(found)


@dataclass(frozen=True, slots=True, kw_only=True)
class Mapping:
    """A loaded ``diggs-mapping`` 1.0 file (**preview**).

    ``units`` maps a unit as the source writes it to the DIGGS unit: a rename only, no
    number is ever changed. ``codes`` holds the named code maps; ``tables`` the entries
    that send a source table to a table of the profile, in file order. The header
    fields say who made the mapping, how, and for which source schema.

    Examples:
        >>> from pydiggs import mapping
        >>> loaded = mapping.load('''
        ... mapping_format = "1.0"
        ... name = "holes-only"
        ... version = "1"
        ... method = "manual"
        ... profile_version = "1.0-beta.1"
        ... [[tables]]
        ... source = "hole"
        ... target = "features"
        ... columns = { key = "id", depth_uom = { value = "ft" } }
        ... ''')
        >>> loaded.name, loaded.tables[0].target, loaded.tables[0].reads
        ('holes-only', 'features', ('id',))
        >>> loaded.apply({"hole": [{"id": "B-7"}]}).tables
        {'features': [{'key': 'B-7', 'depth_uom': 'ft'}]}
    """

    mapping_format: str
    name: str
    version: str
    method: str
    profile_version: str
    creator: str | None = None
    licence: str | None = None
    date: str | None = None
    source_schema: str | None = None
    source_version: str | None = None
    units: Map[str, str] = field(default_factory=lambda: MappingProxyType({}))
    codes: Map[str, CodeMap] = field(default_factory=lambda: MappingProxyType({}))
    tables: tuple[TableMap, ...] = ()

    def reads(self) -> dict[str, tuple[str, ...]]:
        """Source table -> the columns of it the mapping reads.

        Examples:
            >>> from pydiggs import mapping
            >>> mapping.load(mapping.scaffold({"holes": ["hole_id", "depth_top"]})).reads()
            {'holes': ('hole_id',)}
        """
        found: dict[str, dict[str, None]] = {}
        for table in self.tables:
            found.setdefault(table.source, {}).update(dict.fromkeys(table.reads))
        return {name: tuple(columns) for name, columns in found.items()}

    def apply(self, source_tables: Map[str, Iterable[Map[str, Any]]]) -> Applied:
        """Shape source tables into profile tables. See :func:`pydiggs.mapping.apply`.

        Args:
            source_tables: Source table name to its rows, each a mapping of column name
                to value.

        Returns:
            The profile tables, ready for :func:`pydiggs.tables.to_model`, and a
            finding for every source value that was not used.

        Examples:
            >>> from pydiggs import mapping
            >>> loaded = mapping.load(mapping.scaffold({"holes": ["hole_id", "rig"]}))
            >>> applied = loaded.apply({"holes": [{"hole_id": "BH1", "rig": "CME-55"}]})
            >>> applied.tables
            {'features': [{'key': 'BH1'}]}
            >>> [(finding.rule_id, finding.path) for finding in applied.findings]
            [('tables.mapping-unmapped-source', 'holes/1/rig')]
        """
        from pydiggs.mapping._apply import apply  # noqa: PLC0415 - the engine reads this module

        return apply(self, source_tables)


class _Reader:
    """Typed reads of one TOML table, each error naming the place."""

    def __init__(self, entry: object, where: str, allowed: frozenset[str]) -> None:
        if not isinstance(entry, dict):
            raise MappingError(f"{where}: must be a table")
        self.entry: dict[str, Any] = entry
        self.where = where
        unknown = sorted(set(entry) - allowed)
        if unknown:
            raise MappingError(
                f"{where}: unknown key {unknown[0]!r:.80} (allowed: {', '.join(sorted(allowed))})"
            )

    def at(self, key: str) -> str:
        return f"{self.where}.{key}" if self.where else key

    def text(self, key: str, *, required: bool = False) -> str | None:
        value = self.entry.get(key)
        if value is None and not required:
            return None
        return _string(value, self.at(key))

    def constant(self, key: str) -> str | int | float | bool:
        value = self.entry[key]
        if isinstance(value, str):
            return _string(value, self.at(key))
        if isinstance(value, bool | int | float):
            return value
        raise MappingError(f"{self.at(key)}: a constant is a string, a number or a boolean")


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{where}: must be a non-empty string")
    if len(value) > _MAX_TEXT:
        raise MappingError(f"{where}: is longer than {_MAX_TEXT} characters")
    if any(not ch.isprintable() for ch in value):
        raise MappingError(f"{where}: holds a control character")
    return value


def _value_source(
    reader: _Reader,
) -> tuple[str | None, str | int | float | bool | None, Template | None]:
    """The one of ``from``, ``value`` and ``template`` an entry gives."""
    given = [key for key in _VALUE_KEYS if key in reader.entry]
    if len(given) != 1:
        raise MappingError(f"{reader.where}: give exactly one of {', '.join(_VALUE_KEYS)}")
    if given[0] == "from":
        return reader.text("from", required=True), None, None
    if given[0] == "value":
        return None, reader.constant("value"), None
    text = str(reader.text("template", required=True))
    return None, None, parse_template(text, reader.at("template"))


def _codes_name(reader: _Reader, codes: Map[str, CodeMap]) -> str | None:
    name = reader.text("codes")
    if name is not None and name not in codes:
        raise MappingError(f"{reader.at('codes')}: there is no code map [codes.{name:.80}]")
    return name


def _column(
    target: str, entry: object, where: str, codes: Map[str, CodeMap], *, results: bool
) -> ColumnMap:
    if not _TARGET_NAME.fullmatch(target):
        raise MappingError(f"{where}: {target!r:.80} is not a column name of the profile")
    if isinstance(entry, str):
        return ColumnMap(target=target, source=_string(entry, where))
    reader = _Reader(entry, where, _COLUMN_KEYS)
    source, value, template = _value_source(reader)
    units = reader.entry.get("units", False)
    if not isinstance(units, bool):
        raise MappingError(f"{reader.at('units')}: must be true or false")
    described = [key for key in ("property", "uom") if key in reader.entry]
    if described and not results:
        raise MappingError(
            f"{reader.at(described[0])}: only a column of a {RESULTS_PREFIX}<procedure> table"
            " has a property and a uom"
        )
    return ColumnMap(
        target=target,
        source=source,
        value=value,
        template=template,
        units=units,
        codes=_codes_name(reader, codes),
        property_key=reader.text("property"),
        uom=reader.text("uom"),
    )


def _param(entry: object, where: str, codes: Map[str, CodeMap]) -> ParamMap:
    reader = _Reader(entry, where, _PARAM_KEYS)
    source, value, template = _value_source(reader)
    if "uom" in reader.entry and "uom_from" in reader.entry:
        raise MappingError(f"{where}: give uom (a DIGGS unit) or uom_from (a column), not both")
    return ParamMap(
        name=str(reader.text("name", required=True)),
        scope=reader.text("scope"),
        source=source,
        value=value,
        template=template,
        uom=reader.text("uom"),
        uom_from=reader.text("uom_from"),
        codes=_codes_name(reader, codes),
    )


def _table(entry: object, where: str, codes: Map[str, CodeMap]) -> TableMap:
    reader = _Reader(entry, where, frozenset({"source", "target", "columns", "params"}))
    source = str(reader.text("source", required=True))
    target = str(reader.text("target", required=True))
    if not _TARGET_NAME.fullmatch(target):
        raise MappingError(f"{reader.at('target')}: {target!r:.80} is not a table name")
    listed = reader.entry.get("columns")
    if not isinstance(listed, dict) or not listed:
        raise MappingError(f"{reader.at('columns')}: must be a table with at least one column")
    results = target.startswith(RESULTS_PREFIX)
    columns = {
        str(name): _column(str(name), spec, f"{where}.columns.{name}", codes, results=results)
        for name, spec in listed.items()
    }
    params = reader.entry.get("params", [])
    if not isinstance(params, list):
        raise MappingError(f"{reader.at('params')}: must be an array of tables ([[tables.params]])")
    return TableMap(
        source=source,
        target=target,
        columns=MappingProxyType(columns),
        params=tuple(
            _param(spec, f"{where}.params[{index}]", codes)
            for index, spec in enumerate(params, start=1)
        ),
    )


def _code_map(name: str, entry: object) -> CodeMap:
    where = f"codes.{name}"
    reader = _Reader(entry, where, frozenset({"code_space", "map"}))
    listed = reader.entry.get("map")
    if not isinstance(listed, dict) or not listed:
        raise MappingError(f"{where}.map: must be a table with at least one code")
    entries: dict[str, CodeEntry] = {}
    for source, spec in listed.items():
        place = f"{where}.map.{source}"
        _string(source, place)
        if isinstance(spec, str):
            entries[source] = CodeEntry(code=_string(spec, place))
            continue
        row = _Reader(spec, place, frozenset({"code", "match"}))
        match = row.text("match") or "exact"
        if match not in MATCHES:
            raise MappingError(f"{place}.match: {match!r:.40} is not one of {', '.join(MATCHES)}")
        entries[source] = CodeEntry(code=str(row.text("code", required=True)), match=match)
    return CodeMap(
        name=name, entries=MappingProxyType(entries), code_space=reader.text("code_space")
    )


def _date(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, str) and _DATE.fullmatch(value):
        return value
    raise MappingError("date: must be a date, YYYY-MM-DD")


def _build(document: dict[str, Any]) -> Mapping:
    top = _Reader(document, "", _TOP_KEYS)
    if top.text("mapping_format", required=True) != MAPPING_FORMAT_VERSION:
        raise MappingError(
            f"mapping_format: this release reads {MAPPING_FORMAT_VERSION!r}, the file says"
            f" {top.text('mapping_format')!r:.40}"
        )
    for key in _REQUIRED:
        top.text(key, required=True)
    if top.text("method") not in METHODS:
        raise MappingError(f"method: must be one of {', '.join(METHODS)}")
    if top.text("profile_version") != PROFILE_VERSION:
        raise MappingError(
            f"profile_version: this release has the tables profile {PROFILE_VERSION!r}, the"
            f" file is written for {top.text('profile_version')!r:.40}"
        )
    source = _Reader(document.get("source", {}), "source", frozenset({"schema", "version"}))
    units_given = document.get("units", {})
    if not isinstance(units_given, dict):
        raise MappingError("units: must be a table of source unit = DIGGS unit")
    units = {
        _string(given, f"units.{given}"): _string(unit, f"units.{given}")
        for given, unit in units_given.items()
    }
    codes_given = document.get("codes", {})
    if not isinstance(codes_given, dict):
        raise MappingError("codes: must be a table of code maps ([codes.<name>])")
    codes = {str(name): _code_map(str(name), entry) for name, entry in codes_given.items()}
    listed = document.get("tables", [])
    if not isinstance(listed, list):
        raise MappingError("tables: must be an array of tables ([[tables]])")
    return Mapping(
        mapping_format=MAPPING_FORMAT_VERSION,
        name=str(top.text("name")),
        version=str(top.text("version")),
        method=str(top.text("method")),
        profile_version=PROFILE_VERSION,
        creator=top.text("creator"),
        licence=top.text("licence"),
        date=_date(document.get("date")),
        source_schema=source.text("schema"),
        source_version=source.text("version"),
        units=MappingProxyType(units),
        codes=MappingProxyType(codes),
        tables=tuple(
            _table(entry, f"tables[{index}]", codes) for index, entry in enumerate(listed, start=1)
        ),
    )


def _read(path: Path) -> str:
    try:
        if not path.is_file():
            raise MappingError(f"{str(path)!r:.200} is not a file")
        with path.open("rb") as handle:
            data = handle.read(MAX_MAPPING_BYTES + 1)
    except OSError as exc:
        raise MappingError(f"cannot read the mapping file: {exc.strerror or exc}") from exc
    if len(data) > MAX_MAPPING_BYTES:
        raise MappingError(f"the mapping file is larger than {MAX_MAPPING_BYTES} bytes")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MappingError("the mapping file is not UTF-8 text") from exc


def load(source: str | os.PathLike[str]) -> Mapping:
    r"""Load a ``diggs-mapping`` 1.0 file, strictly (**preview**).

    Args:
        source: The path of the file, or its TOML text. A :class:`str` with a line
            break in it is the text itself; anything else is a path.

    Returns:
        The mapping. Loading checks its shape only; run :func:`~pydiggs.mapping.check`
        to check its targets, units and codes against the profile and DIGGS.

    Raises:
        MappingError: The file cannot be read, is larger than 1 MiB, is not TOML, or
            has a key, a value or a template the format does not allow. The message
            names the place, such as ``tables[2].columns.depth``: the ``depth`` column
            of the second ``[[tables]]`` entry.

    Examples:
        >>> text = '''
        ... mapping_format = "1.0"
        ... name = "demo"
        ... version = "1"
        ... method = "manual"
        ... profile_version = "1.0-beta.1"
        ... [[tables]]
        ... source = "spt"
        ... target = "measurements"
        ... [tables.columns]
        ... key = { template = "{hole}-SPT-{seq}" }
        ... feature = "hole"
        ... procedure = { value = "DrivenPenetrationTest" }
        ... '''
        >>> load(text).tables[0].columns["key"].template.columns
        ('hole', 'seq')
        >>> try:
        ...     load(text.replace("{hole}", "{hole.__class__}"))
        ... except MappingError as exc:
        ...     print(str(exc)[:71])
        tables[1].columns.key.template: a template field is a plain column name
    """
    if isinstance(source, str) and ("\n" in source or "\r" in source):
        text = source
        if len(text.encode("utf-8", "replace")) > MAX_MAPPING_BYTES:
            raise MappingError(f"the mapping text is larger than {MAX_MAPPING_BYTES} bytes")
    else:
        text = _read(Path(source))
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MappingError(f"the mapping is not TOML: {exc}") from exc
    except RecursionError as exc:
        raise MappingError("the mapping is nested too deeply") from exc
    return _build(document)


def mapping_schema() -> dict[str, Any]:
    """The JSON Schema (2020-12) of a ``diggs-mapping`` 1.0 file, read as TOML (**preview**).

    The schema describes the shape; :func:`load` also checks what a schema cannot say
    (a template's fields, a code map that exists).

    Examples:
        >>> schema = mapping_schema()
        >>> schema["title"], schema["properties"]["mapping_format"]
        ('pyDIGGS mapping file, format 1', {'const': '1.0'})
    """
    text = resources.files("pydiggs.mapping").joinpath(_SCHEMA_FILE).read_text("utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded
