"""The source-cell ledger: every non-null AGS4 value is converted or named by a loss. Pure.

A *cell* is one value of one DATA row: ``(group, row, column)``, rows counted from 0. The
converter marks each cell it carries into DIGGS as *used*, by the node that holds it, and
names each one it cannot carry in an *issue*. An issue becomes one finding, however many
cells it names: findings are counted, never repeated per cell. :meth:`Ledger.finish`
names whatever is left (unmapped groups and headings, user-defined content), so after it
every non-null cell is used or named. That is the property the tests check.

Messages are fixed templates. Group and heading names appear as written only when they
are made of letters, digits and ``_`` (the AGS4 Rule 19 alphabet); anything else, and
every code value, appears as an escaped, truncated literal.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final

from pydiggs.ags4 import File, Group
from pydiggs.findings.model import Finding, Severity, Step

__all__ = [
    "ASSUMED",
    "INCOMPLETE",
    "ORPHAN_ROW",
    "REFORMATTED",
    "RULES",
    "UNMAPPED_CODE",
    "UNMAPPED_GROUP",
    "UNMAPPED_HEADING",
    "UNMAPPED_UNIT",
    "UNPARSED",
    "USER_DEFINED",
    "Cell",
    "Ledger",
    "literal",
    "name",
]

UNMAPPED_GROUP: Final = "convert.unmapped-group"
UNMAPPED_HEADING: Final = "convert.unmapped-heading"
USER_DEFINED: Final = "convert.user-defined"
UNMAPPED_CODE: Final = "convert.unmapped-code"
UNMAPPED_UNIT: Final = "convert.unmapped-unit"
ORPHAN_ROW: Final = "convert.orphan-row"
UNPARSED: Final = "convert.value-unparsed"
ASSUMED: Final = "convert.assumed"
REFORMATTED: Final = "convert.value-reformatted"
INCOMPLETE: Final = "convert.incomplete"

#: Every rule id the converter reports, with its severity.
RULES: Final[Mapping[str, Severity]] = {
    UNMAPPED_GROUP: Severity.WARNING,
    UNMAPPED_HEADING: Severity.WARNING,
    USER_DEFINED: Severity.WARNING,
    UNMAPPED_CODE: Severity.WARNING,
    UNMAPPED_UNIT: Severity.WARNING,
    ORPHAN_ROW: Severity.ERROR,
    UNPARSED: Severity.WARNING,
    ASSUMED: Severity.WARNING,
    REFORMATTED: Severity.INFO,
    INCOMPLETE: Severity.ERROR,
}

#: ``(group, row, column)``: one DATA value, rows and columns counted from 0.
Cell = tuple[str, int, int]

_SAFE: Final = re.compile(r"[A-Za-z0-9_]{1,32}")
_LITERAL_CHARS: Final = 40


def name(text: str) -> str:
    """A group or heading name for a message: as written when safe, else an escaped literal.

    Examples:
        >>> name("LOCA_ID"), name("Bad<name>")
        ('LOCA_ID', "'Bad<name>'")
    """
    return text if _SAFE.fullmatch(text) else literal(text)


def literal(text: str) -> str:
    """``text`` as an escaped, quoted literal of at most 40 characters (and an ellipsis).

    Examples:
        >>> literal("CP"), literal("x" * 50)[-5:]
        ("'CP'", "x'...")
    """
    shown = ascii(text[:_LITERAL_CHARS])
    return shown + ("..." if len(text) > _LITERAL_CHARS else "")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


@dataclass(slots=True)
class _Issue:
    rule: str
    group: str
    heading: str | None
    row: int | None
    template: str
    rows: set[int] = field(default_factory=set)
    cells: int = 0
    times: int = 0


class Ledger:
    """Which source cells were used, by which node, and which were named by which issue."""

    def __init__(self, file: File) -> None:
        self.file = file
        self._columns: dict[str, dict[str, int]] = {}
        for group in file.groups.values():
            columns: dict[str, int] = {}
            for index, heading in enumerate(group.headings):
                columns.setdefault(heading, index)
            self._columns[group.name] = columns
        #: Cell -> keys of the nodes that hold it ("" for a cell read as format information).
        self.used: dict[Cell, set[str]] = {}
        #: Cell -> index of the issue that names it.
        self.named: dict[Cell, int] = {}
        self._issues: list[_Issue] = []
        self._index: dict[tuple[str, str, str | None, str], int] = {}

    # --- cells ------------------------------------------------------------------------

    def column(self, group: str, heading: str) -> int | None:
        """The column of ``heading`` in ``group`` (its first, when it is repeated)."""
        return self._columns.get(group, {}).get(heading)

    def value(self, group: str, row: int, heading: str) -> str:
        """The value under ``heading`` in DATA row ``row`` of ``group``; "" when it has none."""
        column = self.column(group, heading)
        values = self.file.groups[group].rows[row]
        return values[column] if column is not None and column < len(values) else ""

    def cells(self) -> Iterator[Cell]:
        """Every non-null cell of the file, in file order."""
        for group in self.file.groups.values():
            for row, values in enumerate(group.rows):
                for column, value in enumerate(values[: len(group.headings)]):
                    if value != "":
                        yield group.name, row, column

    def _row_cells(self, group: str, row: int) -> Iterator[Cell]:
        found = self.file.groups[group]
        values = found.rows[row]
        for column, value in enumerate(values[: len(found.headings)]):
            if value != "":
                yield group, row, column

    def use(self, group: str, row: int, heading: str, owner: str = "") -> None:
        """Mark the value under ``heading`` as carried into DIGGS by node ``owner``."""
        column = self.column(group, heading)
        if column is not None and self.value(group, row, heading) != "":
            self.used.setdefault((group, row, column), set()).add(owner)

    def is_done(self, cell: Cell) -> bool:
        """True when ``cell`` is used by a node or named by an issue."""
        return bool(self.used.get(cell)) or cell in self.named

    # --- issues -----------------------------------------------------------------------

    def _issue(
        self, rule: str, group: str, heading: str | None, row: int | None, template: str
    ) -> int:
        key = (rule, group, heading, template)
        index = self._index.get(key)
        if index is None:
            index = len(self._issues)
            self._issues.append(_Issue(rule, group, heading, row, template))
            self._index[key] = index
        return index

    def note(self, rule: str, group: str, template: str, row: int | None = None) -> None:
        """Report something that names no source cell (a default written, a reformatted value).

        ``template`` may use ``{group}``, ``{times}`` and ``{rows}``; issues with the same
        rule, group and template are one finding.
        """
        issue = self._issues[self._issue(rule, group, None, row, template)]
        issue.times += 1
        if row is not None:
            issue.rows.add(row)

    def lose(self, rule: str, group: str, row: int, heading: str | None, template: str) -> None:
        """Name one value (or, with ``heading`` None, one whole row) as not converted.

        ``template`` may use ``{group}``, ``{heading}``, ``{values}`` and ``{rows}``.
        """
        index = self._issue(rule, group, heading, row, template)
        issue = self._issues[index]
        issue.rows.add(row)
        issue.times += 1
        if heading is None:
            cells = list(self._row_cells(group, row))
        else:
            column = self.column(group, heading)
            cells = [] if column is None else [(group, row, column)]
            cells = [cell for cell in cells if self.value(group, row, heading) != ""]
        for cell in cells:
            if cell not in self.named:
                self.named[cell] = index
                issue.cells += 1

    def lose_group(self, rule: str, group: str, template: str) -> None:
        """Name every value of ``group`` as not converted (one finding for the whole group)."""
        for row in range(len(self.file.groups[group].rows)):
            self.lose(rule, group, row, None, template)

    def forget(self, owner: str) -> None:
        """Node ``owner`` will not be written: it no longer holds any cell."""
        for owners in self.used.values():
            owners.discard(owner)

    def release(self, owner: str, rule: str, template: str) -> None:
        """Node ``owner`` was not written: name each cell it alone held, row by row.

        ``template`` may use ``{group}``, ``{values}`` and ``{rows}``.
        """
        orphaned: list[Cell] = []
        for cell, owners in self.used.items():
            if owner in owners:
                owners.discard(owner)
                if not owners and cell not in self.named:
                    orphaned.append(cell)
        for group, row, column in orphaned:
            index = self._issue(rule, group, None, row, template)
            issue = self._issues[index]
            issue.rows.add(row)
            issue.times += 1
            self.named[(group, row, column)] = index
            issue.cells += 1

    # --- findings ---------------------------------------------------------------------

    def findings(self) -> list[Finding]:
        """One finding per issue, in the order the issues were first met."""
        out: list[Finding] = []
        for issue in self._issues:
            group = self.file.groups[issue.group]
            row = min(issue.rows) if issue.rows else issue.row
            message = issue.template.format(
                group=name(issue.group),
                heading=name(issue.heading or ""),
                values=_count(issue.cells, "value"),
                rows=_count(len(issue.rows), "row"),
                times=_count(issue.times, "time"),
            )
            out.append(
                Finding(
                    step=Step.CONVERT,
                    rule_id=issue.rule,
                    severity=RULES[issue.rule],
                    message=message,
                    line=row_line(group, row),
                    path=_path(issue.group, row, issue.heading),
                )
            )
        return out


def _path(group: str, row: int | None, heading: str | None) -> str:
    parts = [name(group)]
    if row is not None:
        parts.append(str(row + 1))
        if heading is not None:
            parts.append(name(heading))
    return "/".join(parts)[:1024]


def row_line(group: Group, row: int | None) -> int | None:
    """The source line of DATA row ``row`` (from 0) of ``group``; its GROUP line for None.

    AGS4 puts a group's GROUP, HEADING, UNIT, TYPE and DATA rows on consecutive lines, so
    a DATA row's line follows from the GROUP row's. None for a group built in code.
    """
    if group.line is None:
        return None
    if row is None:
        return group.line
    header = 1 + (1 if group.units else 0) + (1 if group.types else 0)
    return group.line + header + 1 + row
