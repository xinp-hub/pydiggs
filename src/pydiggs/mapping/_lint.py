"""The static check of a mapping file: its targets, units and codes, with no data.

:func:`check` answers "can this mapping work at all?" before any row is read: every
target is a table and column of the profile, the units and codes on the DIGGS side are
DIGGS ones (the same lookups as :func:`pydiggs.tables.check`), and what every data set
needs is mapped.
"""

from __future__ import annotations

from typing import Final

from pydiggs._writer._layout import load_layout
from pydiggs.convert._mapping import measure_facts
from pydiggs.findings.model import Finding
from pydiggs.mapping._apply import code_space_column
from pydiggs.mapping._format import ColumnMap, Mapping, TableMap
from pydiggs.tables import _report as rep
from pydiggs.tables._check import (
    CODE_COLUMNS,
    code_problem,
    property_problem,
    property_rule,
    unit_known,
    unit_problem,
)
from pydiggs.tables._profile import (
    RESULT_KEYS,
    RESULT_PLACES,
    RESULT_TIMES,
    RESULTS_PREFIX,
    TABLES,
)
from pydiggs.tables._to_model import _DEPTH_UNITS

__all__ = ["check"]

#: Columns of a result table that are not results.
_NOT_RESULTS: Final = frozenset({*RESULT_KEYS, *RESULT_PLACES, *RESULT_TIMES, "depth_uom"})
#: Columns that hold a length's unit.
_LENGTH_UNITS: Final = frozenset({"total_depth_uom", "recovery_uom"})
#: Constant columns that must name a DIGGS 3.0.0 element.
_ELEMENTS: Final = frozenset(
    {
        ("features", "element"),
        ("observations", "element"),
        ("measurements", "element"),
        ("measurements", "procedure"),
    }
)


#: (table, column) -> the DIGGS name a code in that column is written under.
_WRITTEN_AS: Final = {
    (table, column): written for table, column, _space, written in CODE_COLUMNS if written
}


class _Lint:
    def __init__(self, mapping: Mapping) -> None:
        self.mapping = mapping
        self.notes = rep.Notes()
        self.elements = load_layout("3.0.0").elements

    def target(self, place: str, message: str) -> None:
        self.notes.add(rep.MAPPING_UNKNOWN_TARGET, place, message)

    def unit(self, place: str, column: str, unit: str) -> None:
        """Check a DIGGS unit written as a constant into a unit column."""
        if column == "depth_uom":
            known = unit in _DEPTH_UNITS
            wanted = f"a depth unit of DIGGS 3.0.0 ({', '.join(_DEPTH_UNITS)})"
        elif column in _LENGTH_UNITS:
            known = unit in measure_facts("Length").uoms
            wanted = "a unit of length DIGGS 3.0.0 has"
        else:
            known = unit_known(unit)
            wanted = "a unit of the DIGGS units dictionary"
        if not known:
            self.notes.add(rep.UNKNOWN_UNIT, place, f"{rep.shown(unit)!r} is not {wanted}.")

    def constant_code(self, place: str, table: TableMap, target: str, spec: ColumnMap) -> None:
        """Check a constant code against a constant code space beside it."""
        holder = code_space_column(table.target, target)
        space = None if holder is None else table.columns.get(holder)
        if space is None or not isinstance(space.value, str) or not isinstance(spec.value, str):
            return
        problem = code_problem(spec.value, space.value, _WRITTEN_AS.get((table.target, target)))
        if problem is not None:
            self.notes.add(rep.UNKNOWN_CODE, place, f"{problem}.")

    def code_map(self, name: str, element: str | None) -> None:
        """Check the DIGGS codes of a code map, as codes written under ``element``."""
        codes = self.mapping.codes[name]
        if codes.code_space is None:
            return
        for given, entry in codes.entries.items():
            problem = code_problem(entry.code, codes.code_space, element)
            if problem is not None:
                self.notes.add(
                    rep.UNKNOWN_CODE, f"codes.{name}.map.{rep.shown(given)}", f"{problem}."
                )

    def fixed(self, place: str, table: TableMap) -> None:
        known = TABLES[table.target]
        names = set(known.names())
        for target, spec in table.columns.items():
            at = f"{place}.columns.{target}"
            if target not in names:
                self.target(at, f"{target!r} is not a column of the profile table {known.name}.")
                continue
            if isinstance(spec.value, str):
                if target.endswith("uom"):
                    self.unit(at, target, spec.value)
                if (table.target, target) in _ELEMENTS and spec.value not in self.elements:
                    self.target(at, f"{rep.shown(spec.value)!r} is not a DIGGS 3.0.0 element.")
                self.constant_code(at, table, target, spec)
        for field in known.fields:
            if field.required and field.name not in table.columns:
                self.target(
                    f"{place}.columns.{field.name}",
                    f"The profile table {known.name} requires `{field.name}`, and the mapping"
                    " does not fill it.",
                )
        if table.params and "key" not in table.columns:
            self.target(
                f"{place}.params",
                f"A parameter belongs to the row's key, and {known.name} rows get no `key`"
                " from this entry.",
            )

    def results(self, place: str, table: TableMap) -> None:
        procedure = table.target[len(RESULTS_PREFIX) :]
        if procedure not in self.elements:
            self.target(
                f"{place}.target",
                f"{rep.shown(procedure)!r} is not a DIGGS 3.0.0 procedure element, so"
                f" {rep.shown(table.target)!r} is not a result table.",
            )
        if "measurement" not in table.columns:
            self.target(
                f"{place}.columns.measurement",
                "A result table requires `measurement`, the key of the test, and the mapping"
                " does not fill it.",
            )
        if table.params:
            self.target(
                f"{place}.params",
                "A parameter belongs to a row with a key; put it on the entry that fills"
                " `measurements`.",
            )
        for target, spec in table.columns.items():
            at = f"{place}.columns.{target}"
            if target in _NOT_RESULTS:
                if target == "depth_uom" and isinstance(spec.value, str):
                    self.unit(at, target, spec.value)
                continue
            key = spec.property_key or target
            problem = property_problem(key, spec.uom)
            if problem is not None:
                self.notes.add(property_rule(key), at, f"Result column: {problem}.")
            wrong = None if spec.uom is None else unit_problem(key, spec.uom)
            if wrong is not None:
                self.notes.add(rep.UNKNOWN_UNIT, at, f"Result column {rep.shown(key)!r}: {wrong}.")

    def vocabulary(self) -> None:
        """Check the DIGGS side of the unit table and of every code map."""
        for given, unit in self.mapping.units.items():
            if not unit_known(unit):
                self.notes.add(
                    rep.UNKNOWN_UNIT,
                    f"units.{rep.shown(given)}",
                    f"{rep.shown(unit)!r} is not a unit of the DIGGS units dictionary.",
                )
        for table in self.mapping.tables:
            for target, column in table.columns.items():
                if column.codes is not None:
                    self.code_map(column.codes, _WRITTEN_AS.get((table.target, target)))
            for param in table.params:
                if param.codes is not None:
                    self.code_map(param.codes, param.name)
        for name in self.mapping.codes:
            self.code_map(name, None)

    def run(self) -> tuple[Finding, ...]:
        if not any(table.target == "features" for table in self.mapping.tables):
            self.target(
                "tables",
                "No entry fills the profile table features, which every data set needs"
                " (each borehole, sounding or pit, with its key).",
            )
        for index, table in enumerate(self.mapping.tables, start=1):
            place = f"tables[{index}]"
            if table.target in TABLES:
                self.fixed(place, table)
            elif table.is_results and len(table.target) > len(RESULTS_PREFIX):
                self.results(place, table)
            else:
                self.target(
                    f"{place}.target",
                    f"{rep.shown(table.target)!r} is not a table of the profile"
                    f" ({', '.join(TABLES)}, or {RESULTS_PREFIX}<procedure>).",
                )
            for at, param in enumerate(table.params, start=1):
                if param.uom is not None and not unit_known(param.uom):
                    self.notes.add(
                        rep.UNKNOWN_UNIT,
                        f"{place}.params[{at}].uom",
                        f"{rep.shown(param.uom)!r} is not a unit of the DIGGS units dictionary.",
                    )
        self.vocabulary()
        return self.notes.findings()


def check(mapping: Mapping) -> tuple[Finding, ...]:
    """Check a mapping against the profile and the DIGGS vocabularies, with no data (**preview**).

    Findings, under the step ``tables``:

    - ``tables.mapping-unknown-target`` (error): a target is not a table or column of
      the profile; a ``results_<procedure>`` names no DIGGS 3.0.0 procedure; a constant
      ``procedure`` or ``element`` is not a DIGGS element; a column the profile requires
      (such as ``features.key``) is not filled; no entry fills ``features``; an entry
      has parameters and no ``key``;
    - ``tables.unknown-unit``: a DIGGS unit on the right-hand side (in ``[units]``, a
      constant unit column, a result column's or a parameter's ``uom``) is not one;
    - ``tables.unknown-property``: a result column is not a property of the DIGGS
      property dictionary, or its ``uom`` disagrees with it;
    - ``tables.unknown-code``: a DIGGS code in a code map is not in the map's code list
      (only for code lists bundled with pyDIGGS).

    The ``path`` of a finding is the place in the file: ``tables[2].columns.depth`` is
    the ``depth`` column of the second ``[[tables]]`` entry.

    Args:
        mapping: The loaded mapping.

    Returns:
        The findings; empty when the mapping can work.

    Examples:
        >>> from pydiggs import mapping as mapping_module
        >>> loaded = mapping_module.load('''
        ... mapping_format = "1.0"
        ... name = "demo"
        ... version = "1"
        ... method = "manual"
        ... profile_version = "1.0-beta.1"
        ... [[tables]]
        ... source = "hole"
        ... target = "features"
        ... columns = { key = "id", depth_unit = { value = "feet" } }
        ... ''')
        >>> [(finding.rule_id, finding.path) for finding in check(loaded)]
        [('tables.mapping-unknown-target', 'tables[1].columns.depth_unit')]
    """
    return _Lint(mapping).run()
