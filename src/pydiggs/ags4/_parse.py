"""The AGS4 tokenizer and group assembler: text in, tables and notes out.

Pure: no I/O and no clock (the caller passes ``should_stop``). The tokenizer is written
by hand and not built on the stdlib ``csv`` module: ``csv.field_size_limit`` is state
shared by the whole process, the rules need exact line numbers and a record of every
field that was not quoted, and the limits must apply while scanning.

AGS4 has one row per line (Rule 6 allows no line break inside a value), so the text
is cut into lines first and each line is tokenized on its own. A quote left open
therefore ends at its line and can never swallow the rest of the file.

Reading is lenient. A row that breaks a rule is read as far as it can be told, and what
was done about it is a note: a finding of step ``ags4`` with a fixed-template message
that holds counts and line numbers, and a group name only when the name is made of
letters, digits and underscores.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Final

from pydiggs.ags4._table import AGS_VERSIONS, Group, declared_version
from pydiggs.errors import DocumentError
from pydiggs.findings.model import Finding, Severity, Step

__all__ = [
    "DUPLICATE_GROUP",
    "ENCODING_ASSUMED",
    "LIMIT",
    "RULE_1",
    "RULE_2",
    "RULE_2A",
    "RULE_2B",
    "RULE_3",
    "RULE_4",
    "RULE_5",
    "TIMEOUT",
    "UNSUPPORTED_VERSION",
    "VERSION_UNKNOWN",
    "Limits",
    "Parsed",
    "note",
    "parse",
    "shown",
]

RULE_1: Final = "ags4.rule-1"
RULE_2: Final = "ags4.rule-2"
RULE_2A: Final = "ags4.rule-2a"
RULE_2B: Final = "ags4.rule-2b"
RULE_3: Final = "ags4.rule-3"
RULE_4: Final = "ags4.rule-4"
RULE_5: Final = "ags4.rule-5"
#: A group name is used by more than one GROUP row; only the first group is read.
DUPLICATE_GROUP: Final = "ags4.duplicate-group"
#: The bytes are not UTF-8 and were decoded as Windows-1252.
ENCODING_ASSUMED: Final = "ags4.encoding-assumed"
#: ``TRAN_AGS`` names a version that is not in ``AGS_VERSIONS``.
VERSION_UNKNOWN: Final = "ags4.version-unknown"
#: An AGS 3 (or older) file: refused, never read.
UNSUPPORTED_VERSION: Final = "ags4.unsupported-version"
#: Reading stopped at a work limit; the file is incomplete.
LIMIT: Final = "ags4.limit"
#: Reading stopped at its time budget; the file is incomplete.
TIMEOUT: Final = "ags4.timeout"

_SEVERITY: Final = {ENCODING_ASSUMED: Severity.WARNING, VERSION_UNKNOWN: Severity.WARNING}

_GROUP: Final = "GROUP"
_HEADING: Final = "HEADING"
_UNIT: Final = "UNIT"
_TYPE: Final = "TYPE"
_DATA: Final = "DATA"
#: Position of each header row in a group: GROUP, HEADING, UNIT, TYPE, then DATA.
_STAGE: Final = {_HEADING: 1, _UNIT: 2, _TYPE: 3, _DATA: 4}

_EOL: Final = re.compile(r"\r\n|\r|\n")
_CRLF: Final = "\r\n"
#: A name that may be shown in a message or a ``Finding.path``.
_SAFE_NAME: Final = re.compile(r"[A-Za-z0-9_]{1,32}")
#: Notes listed one by one for each rule before the rest are counted.
MAX_NOTES_PER_RULE: Final = 100
#: How many lines pass between two calls of ``should_stop``.
_CHECK_EVERY: Final = 1024
#: Cells a file may hold beyond one per two characters of its text. An honest cell
#: takes at least three characters (``"",``), so only rows padded out to a long
#: HEADING row can reach this.
_CELL_BASE: Final = 100_000


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    """Work limits of one read. Reading stops at the first one reached.

    The defaults are far above real files: AGS 4.2 defines about 170 groups, the
    widest with fewer than 150 headings.
    """

    max_groups: int = 10_000
    max_headings: int = 2_000
    max_rows: int = 5_000_000
    max_field_chars: int = 1_000_000


_LIMIT_TEXT: Final = {
    "max_groups": "the file has more groups than pyDIGGS reads",
    "max_headings": "a HEADING row has more headings than pyDIGGS reads",
    "max_rows": "the file has more DATA rows than pyDIGGS reads",
    "max_field_chars": "a value is longer than pyDIGGS reads",
    "max_cells": "short DATA rows under a long HEADING row make more cells than a file of"
    " this size may hold",
}


@dataclass(slots=True)
class Parsed:
    """What :func:`parse` made of a text."""

    groups: dict[str, Group]
    notes: list[Finding]
    complete: bool = True
    limits_hit: tuple[str, ...] = ()


def note(rule: str, message: str, *, line: int | None = None, path: str | None = None) -> Finding:
    """One reader note: a finding of step ``ags4`` at the severity the rule always has."""
    return Finding(
        step=Step.AGS4,
        rule_id=rule,
        severity=_SEVERITY.get(rule, Severity.ERROR),
        message=message,
        line=line,
        path=path,
    )


def _lines(text: str) -> Iterator[tuple[str, bool]]:
    """Each line of ``text`` without its line end, and whether that end was CR LF."""
    position = 0
    for match in _EOL.finditer(text):
        yield text[position : match.start()], match.group() == _CRLF
        position = match.end()
    if position < len(text):
        yield text[position:], False


def _tokenize(line: str) -> tuple[list[str], bool]:
    """Split one line into its fields; False when a field breaks the quoting rule.

    The general path, for a line the fast path in :func:`parse` cannot take. A field
    is read leniently: an unquoted field runs to the next comma, a quote left open runs
    to the end of the line, text after a closing quote joins the field, and spaces or
    tabs around a quoted field are dropped. Work is linear in the length of the line.
    """
    fields: list[str] = []
    clean = True
    i, n = 0, len(line)
    while True:
        start = i
        while i < n and line[i] in " \t":
            i += 1
        if i < n and line[i] == '"':
            clean = clean and i == start
            parts: list[str] = []
            j = i + 1
            while True:
                k = line.find('"', j)
                if k == -1:  # the quote is never closed: the field ends with the line
                    parts.append(line[j:])
                    i, clean = n, False
                    break
                if line.startswith('"', k + 1):  # a doubled quote is one quote
                    parts.append(line[j : k + 1])
                    j = k + 2
                    continue
                parts.append(line[j:k])
                i = k + 1
                break
            after = i
            while i < n and line[i] in " \t":
                i += 1
            if i < n and line[i] != ",":  # text after the closing quote
                k = line.find(",", i)
                k = n if k == -1 else k
                parts.append(line[after:k])
                i = k
            clean = clean and i == after
            fields.append("".join(parts))
        else:
            k = line.find(",", start)
            k = n if k == -1 else k
            fields.append(line[start:k])
            i, clean = k, False
        if i >= n:
            return fields, clean
        i += 1  # the comma


def shown(name: str) -> str:
    """``name`` for a message: itself when plain, else an escaped, truncated literal."""
    return name if _SAFE_NAME.fullmatch(name) else ascii(name[:32])


@dataclass(slots=True)
class _Open:
    """The group being read."""

    name: str
    line: int
    path: str | None
    headings: tuple[str, ...] | None = None
    units: tuple[str, ...] | None = None
    units_line: int = 0
    types: tuple[str, ...] | None = None
    types_line: int = 0
    rows: list[tuple[str, ...]] = field(default_factory=list)
    stage: int = 0


class _LimitError(Exception):
    """A limit stopped the read; ``name`` goes into ``limits_hit``."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class _Assembler:
    """Builds groups from tokenized rows, keeping the notes."""

    def __init__(self, limits: Limits, max_cells: int) -> None:
        self.limits = limits
        self.max_cells = max_cells
        self.groups: dict[str, Group] = {}
        self.notes: list[Finding] = []
        self._counts: dict[str, int] = {}
        self.current: _Open | None = None
        self.skipping = False
        self.group_rows = 0
        self.rows = 0
        self.cells = 0
        self.ags3 = False
        self.line_no = 0
        self.not_crlf = 0
        self.first_not_crlf = 0

    def note(self, rule: str, message: str, line: int | None, path: str | None = None) -> None:
        count = self._counts.get(rule, 0)
        self._counts[rule] = count + 1
        if count < MAX_NOTES_PER_RULE:
            self.notes.append(note(rule, message, line=line, path=path))

    def summarise(self) -> None:
        """One closing note for each rule that had more notes than were listed."""
        for rule, count in self._counts.items():
            if count > MAX_NOTES_PER_RULE:
                self.notes.append(
                    note(
                        rule,
                        f"{count - MAX_NOTES_PER_RULE} more notes of this rule are not listed.",
                    )
                )

    # --- rows ---------------------------------------------------------------------------

    def row(self, fields: list[str], line: int) -> None:
        descriptor = fields[0]
        if descriptor == _DATA:
            self._data(fields, line)
        elif descriptor == _GROUP:
            self._group(fields, line)
        elif descriptor in _STAGE:
            self._header(descriptor, fields, line)
        else:
            if descriptor.startswith("**"):
                self.ags3 = True
            self.note(
                RULE_3,
                "A row does not start with GROUP, HEADING, UNIT, TYPE or DATA; it was not read.",
                line,
            )

    def _group(self, fields: list[str], line: int) -> None:
        self.close()
        self.group_rows += 1
        name = fields[1] if len(fields) > 1 else ""
        self.skipping = True
        if not name:
            self.note(RULE_4, "A GROUP row has no group name; its rows were not read.", line)
            return
        if len(fields) > 2:  # noqa: PLR2004  # the descriptor and the name
            self.note(
                RULE_4,
                "A GROUP row has more than a group name; the other fields were not read.",
                line,
            )
        if name in self.groups:
            self.note(
                DUPLICATE_GROUP,
                f"Group {shown(name)} has more than one GROUP row; only the first group was read.",
                line,
            )
            return
        if len(self.groups) >= self.limits.max_groups:
            raise _LimitError("max_groups")
        self.skipping = False
        self.current = _Open(name, line, name if _SAFE_NAME.fullmatch(name) else None)

    def _orphan(self, line: int) -> bool:
        """True when the row belongs to no group that is being read."""
        if self.current is not None:
            return False
        if not self.skipping:
            self.note(RULE_2, "A row comes before the first GROUP row; it was not read.", line)
        return True

    def _header(self, descriptor: str, fields: list[str], line: int) -> None:
        if self._orphan(line):
            return
        group = self.current
        assert group is not None  # noqa: S101  # _orphan() returned False
        values = tuple(fields[1:])
        taken = {_HEADING: group.headings, _UNIT: group.units, _TYPE: group.types}[descriptor]
        if taken is not None:
            self.note(
                RULE_2B,
                f"Group {shown(group.name)} has more than one {descriptor} row; only the first"
                " was read.",
                line,
                group.path,
            )
            return
        if descriptor == _HEADING:
            if len(values) > self.limits.max_headings:
                raise _LimitError("max_headings")
            group.headings = values
        elif descriptor == _UNIT:
            group.units, group.units_line = values, line
        else:
            group.types, group.types_line = values, line
        if group.stage != _STAGE[descriptor] - 1:
            self.note(
                RULE_2B,
                f"The {descriptor} row of group {shown(group.name)} is out of order (the order"
                " is GROUP, HEADING, UNIT, TYPE, DATA); it was read all the same.",
                line,
                group.path,
            )
        group.stage = max(group.stage, _STAGE[descriptor])

    def _data(self, fields: list[str], line: int) -> None:
        if self._orphan(line):
            return
        group = self.current
        assert group is not None  # noqa: S101  # _orphan() returned False
        headings = group.headings
        if headings is None:
            self.note(
                RULE_2B,
                f"A DATA row of group {shown(group.name)} comes before its HEADING row; it was"
                " not read.",
                line,
                group.path,
            )
            return
        if self.rows >= self.limits.max_rows:
            raise _LimitError("max_rows")
        width = len(headings)
        self.cells += width
        if self.cells > self.max_cells:
            raise _LimitError("max_cells")
        self.rows += 1
        group.stage = _STAGE[_DATA]
        values = tuple(fields[1:])
        if len(values) != width:
            values = self._fit(
                values, width, descriptor=_DATA, line=line, group=group, row=len(group.rows) + 1
            )
        group.rows.append(values)

    def _fit(
        self,
        values: tuple[str, ...],
        width: int,
        *,
        descriptor: str,
        line: int,
        group: _Open,
        row: int | None = None,
    ) -> tuple[str, ...]:
        """``values`` cut or padded to ``width``, with the Rule 4 note."""
        done = (
            "the missing values were read as empty"
            if len(values) < width
            else "the values past the last heading were not read"
        )
        path = group.path if row is None or group.path is None else f"{group.path}/{row}"
        self.note(
            RULE_4,
            f"A {descriptor} row has {len(values)} values where the HEADING row has {width}"
            f" headings; {done}.",
            line,
            path,
        )
        return values[:width] + ("",) * (width - len(values))

    # --- groups -------------------------------------------------------------------------

    def close(self) -> None:
        """Finish the group being read and keep it."""
        group, self.current = self.current, None
        if group is None:
            return
        name = shown(group.name)
        if group.headings is None:
            self.note(RULE_2B, f"Group {name} has no HEADING row.", group.line, group.path)
        headings = group.headings or ()
        width = len(headings)
        header: list[tuple[str, ...]] = []
        for descriptor, values, line in (
            (_UNIT, group.units, group.units_line),
            (_TYPE, group.types, group.types_line),
        ):
            if values is None:
                self.note(
                    RULE_2B,
                    f"Group {name} has no {descriptor} row; it was read as empty.",
                    group.line,
                    group.path,
                )
                header.append(("",) * width)
            elif len(values) != width and group.headings is not None:
                header.append(
                    self._fit(values, width, descriptor=descriptor, line=line, group=group)
                )
            else:
                header.append(values[:width])
        self.groups[group.name] = Group._trusted(
            name=group.name,
            headings=headings,
            units=header[0],
            types=header[1],
            rows=tuple(group.rows),
            line=group.line,
        )


def _scan(text: str, build: _Assembler, should_stop: Callable[[], bool] | None) -> str | None:
    """Feed every row of ``text`` to ``build``; the name of the limit that stopped it, if any."""
    max_field = build.limits.max_field_chars
    line_no = 0
    try:
        for line, crlf in _lines(text):
            build.line_no = line_no = line_no + 1
            if not crlf:
                build.not_crlf += 1
                build.first_not_crlf = build.first_not_crlf or line_no
            if should_stop is not None and line_no % _CHECK_EVERY == 0 and should_stop():
                return "time_budget"
            if not line or line.isspace():
                continue
            if line[0] == '"' and line[-1] == '"' and len(line) > 1:
                fields = line[1:-1].split('","')
                clean = line.count('"') == 2 * len(fields)
                if not clean:
                    fields, clean = _tokenize(line)
            else:
                fields, clean = _tokenize(line)
            if len(line) > max_field and max(map(len, fields)) > max_field:
                return "max_field_chars"
            if not clean:
                build.note(
                    RULE_5,
                    "A field is not enclosed in double quotes, or has text outside its quotes;"
                    " the row was read as far as it could be told.",
                    line_no,
                )
            build.row(fields, line_no)
    except _LimitError as stop:
        return stop.name
    return None


def parse(
    text: str,
    *,
    limits: Limits | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Parsed:
    """Read AGS4 ``text`` (already decoded, without a byte order mark) into groups.

    Args:
        text: The whole file.
        limits: Work limits (the defaults when None).
        should_stop: Called every so often; when it returns True the read stops as at
            a time budget.

    Returns:
        The groups in file order, the notes in the order they arose, and whether the
        whole text was read.

    Raises:
        DocumentError: The whole text was read and no table can be built: it has no
            GROUP row (``not-ags4``), or it is an AGS 3 file (``unsupported-version``).
    """
    build = _Assembler(limits or Limits(), _CELL_BASE + len(text) // 2)
    stopped = _scan(text, build, should_stop)
    build.close()
    if stopped is None and not build.group_rows:
        raise _no_table(build.ags3)
    if build.not_crlf:
        build.note(
            RULE_2A,
            f"Lines that do not end with CR LF: {build.not_crlf}; the first is line"
            f" {build.first_not_crlf}.",
            build.first_not_crlf,
        )
    declared = declared_version(build.groups)
    if declared and declared not in AGS_VERSIONS:
        build.note(
            VERSION_UNKNOWN,
            "TRAN_AGS names a version pyDIGGS does not know; the file is read as AGS"
            f" {AGS_VERSIONS[-1]}.",
            build.groups["TRAN"].line,
            "TRAN",
        )
    build.summarise()
    if stopped == "time_budget":
        build.note(
            TIMEOUT,
            "Reading stopped at its time budget; rows after this point were not read.",
            build.line_no,
        )
    elif stopped is not None:
        build.note(
            LIMIT,
            f"Reading stopped: {_LIMIT_TEXT[stopped]}. The row at this point and those after"
            " it were not read.",
            build.line_no,
        )
    return Parsed(
        groups=build.groups,
        notes=build.notes,
        complete=stopped is None,
        limits_hit=() if stopped is None else (stopped,),
    )


def _no_table(ags3: bool) -> DocumentError:
    if ags3:
        finding = note(
            UNSUPPORTED_VERSION,
            "The file is in AGS 3 or an older format (its group names start with **);"
            " pyDIGGS reads AGS4 only.",
        )
        return DocumentError("unsupported-version", finding.message, finding)
    finding = note(RULE_2, "The file has no GROUP row: it is not an AGS4 file.")
    return DocumentError("not-ags4", finding.message, finding)
