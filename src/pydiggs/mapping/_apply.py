"""Applying a mapping: source tables in, profile tables out, and every value accounted for.

The engine is small because its target is the tables profile, not DIGGS: it copies a
cell, writes a constant, fills a key template, renames a unit or translates a code.
:func:`pydiggs.tables.to_model` does the rest. No number is changed: a unit is renamed
through the mapping's unit table or, when the table has no entry for it, left out with
the values it belongs to and named by a finding.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydiggs.findings.model import Finding
from pydiggs.mapping._format import ColumnMap, ParamMap, TableMap
from pydiggs.mapping._format import Mapping as MappingFile
from pydiggs.tables import _report as rep
from pydiggs.tables._profile import RESULTS_PREFIX, TABLES
from pydiggs.tables._to_model import _text

__all__ = ["Applied", "apply", "code_space_column"]

Row = Mapping[str, Any]
#: The depths a row's ``depth_uom`` is the unit of (in every table but ``features``).
_DEPTHS: Final = ("top", "base", "depth", "position")


@dataclass(frozen=True, slots=True)
class Applied:
    """What a mapping made of source tables (**preview**).

    ``tables`` are tables of the profile, ready for :func:`pydiggs.tables.to_model`,
    :func:`pydiggs.tables.check` and :func:`pydiggs.tables.round_trip`. ``findings``
    names every source column the mapping does not use, every code with no entry in its
    code map, every code mapped by a match that is not exact, and every unit with no
    entry in the unit table.

    Examples:
        >>> from pydiggs import mapping
        >>> loaded = mapping.load(mapping.scaffold({"holes": ["hole_id", "hole_name"]}))
        >>> applied = loaded.apply({"holes": [{"hole_id": "BH1", "hole_name": "Boring 1"}]})
        >>> applied.tables, applied.lossless
        ({'features': [{'key': 'BH1', 'name': 'Boring 1'}]}, True)
    """

    tables: dict[str, list[dict[str, Any]]]
    findings: tuple[Finding, ...]

    @property
    def lossless(self) -> bool:
        """True when every source value was used: no finding above the level of a note."""
        return all(finding.severity.rank == 0 for finding in self.findings)


def code_space_column(table: str, column: str) -> str | None:
    """The column that holds the code space of ``column`` in profile table ``table``.

    Examples:
        >>> code_space_column("samples", "type"), code_space_column("params", "value")
        ('type_code_space', 'code_space')
        >>> code_space_column("features", "name") is None
        True
    """
    if table == "params":
        return "code_space" if column == "value" else None
    name = f"{column}_code_space"
    known = TABLES.get(table)
    return name if known is not None and name in known.names() else None


def _governed(table: str, column: str) -> tuple[str, ...]:
    """The quantity columns that ``column`` of ``table`` is the unit of."""
    if column == "depth_uom":
        return () if table == "features" else _DEPTHS
    known = TABLES.get(table)
    for field in () if known is None else known.fields:
        if field.name == column and field.uom_of is not None:
            return (field.uom_of,)
    return ()


class _Engine:
    def __init__(self, mapping: MappingFile) -> None:
        self.mapping = mapping
        self.notes = rep.Notes()
        self.out: dict[str, list[dict[str, Any]]] = {}
        self.params: list[dict[str, Any]] = []
        self.result_columns: dict[tuple[str, str], dict[str, Any]] = {}

    # --- one cell -------------------------------------------------------------------

    def unit(self, source: str, index: int, column: str, text: str) -> str | None:
        """The DIGGS unit a source unit is renamed to, or None (named) when it has no entry."""
        unit = self.mapping.units.get(text)
        if unit is None:
            self.notes.add(
                rep.MAPPING_UNKNOWN_UNIT,
                source,
                f"Units in {rep.shown(column)!r} of {rep.shown(source)!r} have no entry in the"
                f" mapping's [units] table, such as {rep.shown(text)!r}; the unit and the"
                " values it belongs to were left out ({times}).",
                row=index,
                column=column,
            )
        return unit

    def code(
        self, source: str, index: int, column: str, name: str, text: str
    ) -> tuple[str, str | None]:
        """The DIGGS code and code space of a source code; the code itself when unmapped."""
        codes = self.mapping.codes[name]
        entry = codes.entries.get(text)
        if entry is None:
            self.notes.add(
                rep.MAPPING_UNMAPPED_CODE,
                source,
                f"Codes in {rep.shown(column)!r} of {rep.shown(source)!r} have no entry in the"
                f" code map {rep.shown(name)!r}, such as {rep.shown(text)!r}; carried as text"
                " with no code list ({times}).",
                row=index,
                column=column,
            )
            return text, None
        if entry.match != "exact":
            self.notes.add(
                rep.MAPPING_CODE_NOT_EXACT,
                source,
                f"Code {rep.shown(text)!r} in {rep.shown(column)!r} of {rep.shown(source)!r} was"
                f" written as {rep.shown(entry.code)!r}, a {entry.match} match ({{times}}).",
                row=index,
                column=column,
            )
        return entry.code, codes.code_space

    def value(self, spec: ColumnMap | ParamMap, row: Row) -> tuple[Any, str | None]:
        """A spec's value for one row (None when blank), and the source column it came from."""
        if spec.template is not None:
            texts = {name: _text(row.get(name)) for name in spec.template.columns}
            return spec.template.render(texts), spec.template.columns[0]
        if spec.source is not None:
            value = row.get(spec.source)
            return (None if _text(value) is None else value), spec.source
        return spec.value, None

    # --- one row --------------------------------------------------------------------

    def row(self, table: TableMap, index: int, row: Row) -> dict[str, Any] | None:
        """The target row of one source row; None when the source gives it no value."""
        made: dict[str, Any] = {}
        from_source = False
        dropped: set[str] = set()
        spaces: dict[str, str] = {}
        for target, spec in table.columns.items():
            value, column = self.value(spec, row)
            if value is None:
                continue
            if column is not None:
                from_source = True
            if spec.units:
                value = self.unit(table.source, index, column or target, str(_text(value)))
                if value is None:
                    dropped.update(_governed(table.target, target))
                    continue
            elif spec.codes is not None:
                value, space = self.code(
                    table.source, index, column or target, spec.codes, str(_text(value))
                )
                holder = code_space_column(table.target, target)
                if space is not None and holder is not None:
                    spaces[holder] = space
            made[target] = value
        if not from_source:
            return None
        for holder, space in spaces.items():
            made.setdefault(holder, space)
        for name in dropped:
            made.pop(name, None)
        if table.target == "params" and "value" in dropped:
            return None  # a parameter with no value is no parameter; the unit is named
        return made

    def param(self, table: TableMap, index: int, row: Row, node: Any, spec: ParamMap) -> None:
        value, column = self.value(spec, row)
        if value is None:
            return
        made: dict[str, Any] = {"node": node, "name": spec.name, "value": value}
        if spec.scope is not None:
            made["scope"] = spec.scope
        if spec.codes is not None:
            code, space = self.code(
                table.source, index, column or spec.name, spec.codes, str(_text(value))
            )
            made["value"] = code
            if space is not None:
                made["code_space"] = space
        if spec.uom is not None:
            made["uom"] = spec.uom
        elif spec.uom_from is not None:
            given = _text(row.get(spec.uom_from))
            if given is not None:
                unit = self.unit(table.source, index, spec.uom_from, given)
                if unit is None:
                    return
                made["uom"] = unit
        self.params.append(made)

    # --- the tables -----------------------------------------------------------------

    def table(self, table: TableMap, rows: list[Row]) -> None:
        out = self.out.setdefault(table.target, [])
        before = len(out)
        for index, row in enumerate(rows):
            made = self.row(table, index, row)
            if made is None:
                continue
            out.append(made)
            node = made.get("key")
            if node is not None:
                for spec in table.params:
                    self.param(table, index, row, node, spec)
        if table.is_results and len(out) > before:  # describe only columns that hold values
            procedure = table.target[len(RESULTS_PREFIX) :]
            for target, column in table.columns.items():
                if column.property_key is None and column.uom is None:
                    continue
                described: dict[str, Any] = {"procedure": procedure, "column": target}
                if column.property_key is not None:
                    described["property"] = column.property_key
                if column.uom is not None:
                    described["uom"] = column.uom
                self.result_columns.setdefault((procedure, target), described)

    def unused(self, sources: Mapping[str, list[Row]]) -> None:
        """Name every source column that holds a value and that the mapping does not read."""
        reads = self.mapping.reads()
        for source, rows in sources.items():
            used = reads.get(source)
            if used is None:
                if any(_text(value) is not None for row in rows for value in row.values()):
                    self.notes.add(
                        rep.MAPPING_UNMAPPED_SOURCE,
                        source,
                        f"The mapping has no entry for source table {rep.shown(source)!r}; its"
                        f" {len(rows)} rows were not used.",
                    )
                continue
            for index, row in enumerate(rows):
                for column, value in row.items():
                    if column not in used and _text(value) is not None:
                        self.notes.add(
                            rep.MAPPING_UNMAPPED_SOURCE,
                            source,
                            f"Column {rep.shown(column)!r} of source table {rep.shown(source)!r}"
                            " is not used by the mapping; its values were left out"
                            " ({times}).",
                            row=index,
                            column=rep.shown(column),
                        )

    def run(self, source_tables: Mapping[str, Iterable[Row]]) -> Applied:
        sources = {str(name): list(rows) for name, rows in source_tables.items()}
        for table in self.mapping.tables:
            self.table(table, sources.get(table.source, []))
        if self.params:
            self.out.setdefault("params", []).extend(self.params)
        if self.result_columns:
            self.out.setdefault("result_columns", []).extend(self.result_columns.values())
        self.unused(sources)
        tables = {name: rows for name, rows in self.out.items() if rows}
        return Applied(tables=tables, findings=self.notes.findings())


def apply(
    mapping: MappingFile, source_tables: Mapping[str, Iterable[Mapping[str, Any]]]
) -> Applied:
    """Shape source tables into profile tables with ``mapping`` (**preview**).

    For each ``[[tables]]`` entry, each row of its source table gives one row of its
    target table: a column is copied, a constant written, a key template filled, a unit
    renamed through ``[units]``, a code translated through its code map. A source row
    that gives the entry no value makes no row. Values are carried as they are; no
    number is changed.

    Every source value is used or named, under the step ``tables``:

    - ``tables.mapping-unmapped-source``: a source table or column holds values the
      mapping does not read;
    - ``tables.mapping-unmapped-code``: a code has no entry in its code map, and was
      carried as text with no code list;
    - ``tables.mapping-code-not-exact``: a code was translated by a ``close``,
      ``broader`` or ``narrower`` match;
    - ``tables.mapping-unknown-unit``: a unit has no entry in ``[units]``; the unit and
      the values it is the unit of were left out.

    The ``path`` of a finding is the source table, the row (counted from 1) and the
    source column.

    Args:
        mapping: The loaded mapping.
        source_tables: Source table name to its rows, each a mapping of column name to
            value. A table the mapping names that is absent is empty.

    Returns:
        The profile tables and the findings.

    Examples:
        >>> from pydiggs import mapping as mapping_module
        >>> loaded = mapping_module.load('''
        ... mapping_format = "1.0"
        ... name = "demo"
        ... version = "1"
        ... method = "manual"
        ... profile_version = "1.0-beta.1"
        ... [units]
        ... feet = "ft"
        ... [[tables]]
        ... source = "hole"
        ... target = "features"
        ... [tables.columns]
        ... key = "id"
        ... total_depth = "depth"
        ... total_depth_uom = { from = "unit", units = true }
        ... ''')
        >>> rows = [
        ...     {"id": "B-7", "depth": 40, "unit": "feet"},
        ...     {"id": "B-8", "depth": 12, "unit": "fathoms"},
        ... ]
        >>> applied = apply(loaded, {"hole": rows})
        >>> applied.tables["features"]
        [{'key': 'B-7', 'total_depth': 40, 'total_depth_uom': 'ft'}, {'key': 'B-8'}]
        >>> [(finding.rule_id, finding.path) for finding in applied.findings]
        [('tables.mapping-unknown-unit', 'hole/2/unit')]
    """
    return _Engine(mapping).run(source_tables)
