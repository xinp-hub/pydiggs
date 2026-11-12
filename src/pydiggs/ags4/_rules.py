"""The AGS4 rules as findings: a :class:`~pydiggs.ags4.File` in, findings out.

Pure: no I/O and no clock (the caller passes ``should_stop``). Rule numbers and their
meaning follow section 4.1.1, "CSV File Rules", of the AGS4 specification, Edition 4.2
(December 2025), whose rules are unchanged from 4.1.1. The reader already reports what
it had to work around while reading (Rules 1, 2, 2a, 2b, 3, 4 and 5); this module checks
what can be seen in the tables.

Two tiers. Most rules need nothing but the file. Rules 7, 9, 10a, 10b and 10c, the
cross-group parts of 11c, 18 and 19b, and the REQUIRED headings of PROJ and TRAN
(Rules 13 and 14) need the AGS data dictionary: they run against the file's own DICT
group for user-defined groups and headings, and against a standard dictionary only when
the caller supplies one. pyDIGGS bundles none.

Messages are fixed templates. They hold counts, row and line numbers, and group and
heading names only when a name is made of letters, digits and underscores (any other
name is shown as an escaped, truncated literal). A value from the file is never put in
a message.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final

from pydiggs.ags4._dictionary import DICT, Dictionary, GroupDef, concatenator, from_file, overlay
from pydiggs.ags4._parse import (
    MAX_NOTES_PER_RULE,
    RULE_1,
    RULE_2,
    RULE_2B,
    RULE_4,
    _LimitError,
    _lines,
    _tokenize,
    shown,
)
from pydiggs.ags4._table import File, Group
from pydiggs.findings.model import Finding, Severity, Step

__all__ = [
    "CHECKER_RULES",
    "DICTIONARY_NOT_CHECKED",
    "DICTIONARY_RULES",
    "FORMULA_TEXT",
    "RULE_6",
    "RULE_7",
    "RULE_8",
    "RULE_9",
    "RULE_10A",
    "RULE_10B",
    "RULE_10C",
    "RULE_11",
    "RULE_11A",
    "RULE_11B",
    "RULE_11C",
    "RULE_13",
    "RULE_14",
    "RULE_15",
    "RULE_16",
    "RULE_16A",
    "RULE_17",
    "RULE_18",
    "RULE_18A",
    "RULE_19",
    "RULE_19A",
    "RULE_19B",
    "RULE_20",
    "GroupLines",
    "Stopped",
    "check_tables",
    "index_lines",
]

RULE_6: Final = "ags4.rule-6"
RULE_7: Final = "ags4.rule-7"
RULE_8: Final = "ags4.rule-8"
RULE_9: Final = "ags4.rule-9"
RULE_10A: Final = "ags4.rule-10a"
RULE_10B: Final = "ags4.rule-10b"
RULE_10C: Final = "ags4.rule-10c"
RULE_11: Final = "ags4.rule-11"
RULE_11A: Final = "ags4.rule-11a"
RULE_11B: Final = "ags4.rule-11b"
RULE_11C: Final = "ags4.rule-11c"
RULE_13: Final = "ags4.rule-13"
RULE_14: Final = "ags4.rule-14"
RULE_15: Final = "ags4.rule-15"
RULE_16: Final = "ags4.rule-16"
RULE_16A: Final = "ags4.rule-16a"
RULE_17: Final = "ags4.rule-17"
RULE_18: Final = "ags4.rule-18"
RULE_18A: Final = "ags4.rule-18a"
RULE_19: Final = "ags4.rule-19"
RULE_19A: Final = "ags4.rule-19a"
RULE_19B: Final = "ags4.rule-19b"
RULE_20: Final = "ags4.rule-20"
#: A text value a spreadsheet would read as a formula (warning).
FORMULA_TEXT: Final = "ags4.formula-text"
#: The rules that need the standard AGS4 dictionary were checked only for what the
#: file's own DICT group defines, because no dictionary was supplied (info).
DICTIONARY_NOT_CHECKED: Final = "ags4.dictionary-not-checked"

#: Every rule id the checker adds to the reader's notes.
CHECKER_RULES: Final = (
    RULE_6,
    RULE_7,
    RULE_8,
    RULE_9,
    RULE_10A,
    RULE_10B,
    RULE_10C,
    RULE_11,
    RULE_11A,
    RULE_11B,
    RULE_11C,
    RULE_13,
    RULE_14,
    RULE_15,
    RULE_16,
    RULE_16A,
    RULE_17,
    RULE_18,
    RULE_18A,
    RULE_19,
    RULE_19A,
    RULE_19B,
    RULE_20,
    FORMULA_TEXT,
    DICTIONARY_NOT_CHECKED,
)
#: Rules that need the standard dictionary in full (they also run, in part, against
#: the file's own DICT group).
DICTIONARY_RULES: Final = (RULE_7, RULE_9, RULE_10A, RULE_10B, RULE_10C, RULE_18)

_SEVERITY: Final = {FORMULA_TEXT: Severity.WARNING, DICTIONARY_NOT_CHECKED: Severity.INFO}

_PROJ: Final = "PROJ"
_TRAN: Final = "TRAN"
_UNIT: Final = "UNIT"
_TYPE: Final = "TYPE"
_ABBR: Final = "ABBR"
_FILE: Final = "FILE"
_FSET: Final = "FILE_FSET"
#: How many rows pass between two calls of ``should_stop``.
_CHECK_EVERY: Final = 1024
_GROUP_NAME: Final = re.compile(r"[A-Z0-9]{1,4}")
_HEADING_NAME: Final = re.compile(r"[A-Z0-9_]{1,9}")
_NUMBER: Final = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_DECIMAL: Final = re.compile(_NUMBER)
_U: Final = re.compile(rf"{_NUMBER}(?:[eE][+-]?\d+)?")
_DMS: Final = re.compile(r"[+-]?\d{1,3}:[0-5]?\d:[0-5]?\d(?:\.\d+)?")
_ELAPSED: Final = re.compile(r"\d+:[0-5]\d(?::[0-5]\d(?:\.\d+)?)?")
_PRECISION: Final = re.compile(r"(\d{1,2})(DP|SF|SCI)")
#: Most decimal places or significant figures a TYPE may ask for.
_MAX_PRECISION: Final = 30
_YN: Final = frozenset({"Y", "N", "y", "n"})
_FORMULA_START: Final = ("=", "+", "@", "-")
#: Headings whose value is a separator character, such as "+", never a formula.
_SEPARATORS: Final = frozenset({"TRAN_DLIM", "TRAN_RCON"})


class Stopped(Exception):  # noqa: N818  # a signal, not an error
    """``should_stop`` returned True: the check stops where it is."""


# --- where a row is -----------------------------------------------------------------


@dataclass(slots=True)
class GroupLines:
    """Lines of one group's rows in the file, 1-based; ``data`` is None when unknown."""

    group: int
    heading: int | None = None
    unit: int | None = None
    type: int | None = None
    data: list[int] | None = field(default_factory=list)


def index_lines(
    text: str, file: File, should_stop: Callable[[], bool] | None = None
) -> tuple[dict[str, GroupLines], list[int]]:
    """Where each group's rows are in ``text``, and lines that continue a broken value.

    The reader keeps only the GROUP row's line, so this pass finds the others, taking
    rows to groups as the reader does: the first group of a name, header rows before
    DATA rows. A group whose DATA row count differs from the table's (a limit stopped
    the read, or the text is not the one read) gets ``data=None``. The second list
    holds, for each value a line break splits (Rule 6), the line that opens it: a line
    with an odd number of quotes leaves a value open, lines with an even number keep it
    open, and the next line with an odd number closes it.

    Raises:
        Stopped: ``should_stop`` returned True, here or in the tokenizer.
    """
    index: dict[str, GroupLines] = {}
    broken: list[int] = []
    current: GroupLines | None = None
    opened = 0  # the line that left a quoted value open, or 0
    for number, (line, _crlf) in enumerate(_lines(text), start=1):
        if should_stop is not None and number % _CHECK_EVERY == 0 and should_stop():
            raise Stopped
        if line.count('"') % 2 == 1:
            if opened:
                broken.append(opened)
            opened = 0 if opened else number
        if not line or line.isspace():
            continue
        try:
            descriptor, name = _descriptor(line, should_stop)
        except _LimitError as stop:
            if stop.name == "time_budget":
                raise Stopped from None
            break
        if descriptor == "GROUP":
            current = None
            if name and name not in index:
                current = index[name] = GroupLines(number)
        elif current is not None:
            _place(current, descriptor, number)
    for name, lines in index.items():
        group = file.groups.get(name)
        if group is None or lines.data is None or len(lines.data) != len(group.rows):
            lines.data = None
    return index, broken


def _descriptor(line: str, should_stop: Callable[[], bool] | None) -> tuple[str, str]:
    """The first two fields of a row, read as the reader reads them."""
    if line.startswith('"DATA",') or line == '"DATA"':
        return "DATA", ""
    fields, _count, _clean = _tokenize(line, keep=2, should_stop=should_stop)
    return fields[0], fields[1] if len(fields) > 1 else ""


def _place(lines: GroupLines, descriptor: str, number: int) -> None:
    """Record line ``number`` of a row of the group being read."""
    if descriptor == "DATA":
        if lines.heading is not None and lines.data is not None:
            lines.data.append(number)
    elif descriptor in ("HEADING", "UNIT", "TYPE"):
        attribute = descriptor.lower()
        if getattr(lines, attribute) is None:
            setattr(lines, attribute, number)


# --- findings -----------------------------------------------------------------------


@dataclass(slots=True)
class _Out:
    """The findings of one check, at most ``MAX_NOTES_PER_RULE`` listed per rule."""

    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, rule: str, message: str, line: int | None, path: str | None) -> None:
        count = self.counts.get(rule, 0)
        self.counts[rule] = count + 1
        if count < MAX_NOTES_PER_RULE:
            self.findings.append(
                Finding(
                    step=Step.AGS4,
                    rule_id=rule,
                    severity=_SEVERITY.get(rule, Severity.ERROR),
                    message=message,
                    line=line,
                    path=path,
                )
            )

    def summarise(self) -> None:
        for rule, count in self.counts.items():
            if count > MAX_NOTES_PER_RULE:
                self.findings.append(
                    Finding(
                        step=Step.AGS4,
                        rule_id=rule,
                        severity=_SEVERITY.get(rule, Severity.ERROR),
                        message=(
                            f"{count - MAX_NOTES_PER_RULE} more findings of this rule are"
                            " not listed."
                        ),
                    )
                )


def _safe(name: str) -> bool:
    return shown(name) == name


def _path(group: str, row: int | None = None, heading: str | None = None) -> str | None:
    """``GROUP``, ``GROUP/row`` or ``GROUP/row/HEADING``; None for an unsafe group name."""
    if not _safe(group):
        return None
    if row is None:
        return group
    if heading is None or not _safe(heading):
        return f"{group}/{row}"
    return f"{group}/{row}/{heading}"


# --- values by declared TYPE (Rule 8) ----------------------------------------------


def _decimals(places: int) -> re.Pattern[str]:
    return re.compile(r"[+-]?\d+" + (rf"\.\d{{{places}}}" if places else ""))


def _scientific(places: int) -> re.Pattern[str]:
    return re.compile(r"[+-]?\d" + (rf"\.\d{{{places}}}" if places else "") + r"[eE][+-]?\d+")


def _figures(figures: int) -> Callable[[str], bool]:
    def valid(value: str) -> bool:
        if not _DECIMAL.fullmatch(value):
            return False
        digits = value.lstrip("+-")
        significant = digits.replace(".", "").lstrip("0")
        if not significant:  # zero: any number of places
            return True
        if "." in digits:
            return len(significant) == figures
        # Trailing zeros of a whole number may or may not be significant.
        return len(significant.rstrip("0")) <= figures <= len(significant)

    return valid


_DT_PARTS: Final = (
    ("yyyy", r"(?P<year>\d{4})"),
    ("sss", r"\d{1,9}"),
    ("dd", r"(?P<day>\d{2})"),
    ("hh", r"(?P<hour>\d{2})"),
    ("ss", r"(?P<second>\d{2})"),
)
_DT_LITERALS: Final = frozenset("-:T ./")
_TIME_ZONE: Final = r"(?:Z|[+-]\d{2}(?::?\d{2})?)?"
#: The forms Section 3.3 gives for DT, used when the UNIT row gives no date format.
_DT_FORMS: Final = (
    "yyyy-mm-ddThh:mm:ss.sss",
    "yyyy-mm-ddThh:mm:ss",
    "yyyy-mm-ddThh:mm",
    "yyyy-mm-dd",
    "yyyy-mm",
    "yyyy",
    "hh:mm:ss",
    "hh:mm",
)
_LIMITS: Final = {
    "month": (1, 12),
    "day": (1, 31),
    "hour": (0, 24),
    "minute": (0, 59),
    "second": (0, 59),
}
#: A year in which 29 February exists, for a date format that gives no year.
_LEAP_YEAR: Final = 2000


def _date_pattern(unit: str) -> re.Pattern[str] | None:
    """A pattern for DT values written in the format ``unit`` names, or None."""
    out: list[str] = []
    i, clock = 0, False
    while i < len(unit):
        for part, pattern in _DT_PARTS:
            if unit.startswith(part, i):
                out.append(pattern)
                clock = clock or part == "hh"
                i += len(part)
                break
        else:
            if unit.startswith("mm", i):
                out.append(r"(?P<minute>\d{2})" if clock else r"(?P<month>\d{2})")
                i += 2
            elif unit[i] in _DT_LITERALS:
                out.append(re.escape(unit[i]))
                i += 1
            elif unit.startswith(("Z(+hh:mm)", "(+hh:mm)", "Z"), i):
                break  # a time zone, allowed at the end of every form
            else:
                return None
    if not out or ("minute" in "".join(out) and "hour" not in "".join(out)):
        return None
    try:
        return re.compile("".join(out) + _TIME_ZONE)
    except re.error:  # a part used twice: not a date format
        return None


_GENERIC_DATES: Final = tuple(p for p in map(_date_pattern, _DT_FORMS) if p is not None)


def _dates(unit: str) -> Callable[[str], bool]:
    own = _date_pattern(unit.strip())
    patterns = (own,) if own is not None else _GENERIC_DATES

    def valid(value: str) -> bool:
        for pattern in patterns:
            match = pattern.fullmatch(value)
            if match is None:
                continue
            return _real(match.groupdict())
        return False

    return valid


def _real(parts: dict[str, str | None]) -> bool:
    """Every part in range, and the day one its month has (29 February in leap years)."""
    values = {name: int(value) for name, value in parts.items() if value is not None}
    if not all(
        low <= values[name] <= high for name, (low, high) in _LIMITS.items() if name in values
    ):
        return False
    if "day" in values and "month" in values:
        year = values.get("year") or _LEAP_YEAR  # no year, or year 0: allow 29 February
        return values["day"] <= calendar.monthrange(year, values["month"])[1]
    return True


def _matches(pattern: re.Pattern[str]) -> Callable[[str], bool]:
    return lambda value: pattern.fullmatch(value) is not None


def _precision(data_type: str) -> Callable[[str], bool] | None:
    """The check of an ``nDP``, ``nSF`` or ``nSCI`` type; None for any other type."""
    precision = _PRECISION.fullmatch(data_type)
    if precision is None or int(precision.group(1)) > _MAX_PRECISION:
        return None
    places, kind = int(precision.group(1)), precision.group(2)
    if kind == "SF":
        return _figures(places) if places else None
    return _matches(_decimals(places) if kind == "DP" else _scientific(places))


_SIMPLE: Final[dict[str, Callable[[str], bool]]] = {
    "U": _matches(_U),
    "DMS": _matches(_DMS),
    "T": _matches(_ELAPSED),
    "YN": _YN.__contains__,
}


def _value_check(data_type: str, unit: str) -> Callable[[str], bool] | None:
    """How a value of ``data_type`` is checked under Rule 8; None for text and unknown types."""
    if data_type == "DT":
        return _dates(unit)
    return _SIMPLE.get(data_type) or _precision(data_type)


def _formula_like(value: str) -> bool:
    if len(value) < 2 or not value.startswith(_FORMULA_START):  # noqa: PLR2004
        return False
    return not (value[0] in "+-" and _U.fullmatch(value) is not None)


# --- the check ----------------------------------------------------------------------


@dataclass(slots=True)
class _Context:
    file: File
    view: Dictionary
    own: Dictionary
    standard: bool
    lines: dict[str, GroupLines]
    out: _Out
    should_stop: Callable[[], bool] | None
    complete: bool
    rows_seen: int = 0
    _keys: dict[tuple[str, tuple[str, ...]], frozenset[tuple[str, ...]]] = field(
        default_factory=dict
    )

    def tick(self) -> None:
        """Count one DATA row; look at the clock every ``_CHECK_EVERY`` rows."""
        self.rows_seen += 1
        if self.rows_seen % _CHECK_EVERY == 0:
            self.poll()

    def poll(self) -> None:
        """Raise Stopped when ``should_stop`` says time is up."""
        if self.should_stop is not None and self.should_stop():
            raise Stopped

    def line(self, group: Group, row: int | None = None, header: str | None = None) -> int | None:
        """The line of a DATA row (1-based ``row``), a header row, or the GROUP row."""
        lines = self.lines.get(group.name)
        if lines is not None and row is not None:
            return lines.data[row - 1] if lines.data is not None else None
        if lines is not None and header is not None:
            found: int | None = getattr(lines, header)
            if found is not None:
                return found
        return group.line

    def add(
        self,
        rule: str,
        message: str,
        group: Group | None = None,
        *,
        row: int | None = None,
        heading: str | None = None,
        header: str | None = None,
    ) -> None:
        if group is None:
            self.out.add(rule, message, None, None)
            return
        line = self.line(group, row, header)
        self.out.add(rule, message, line, _path(group.name, row, heading))

    def key_set(self, group: Group, headings: tuple[str, ...]) -> frozenset[tuple[str, ...]]:
        """The tuples of ``group``'s values under ``headings`` (all present)."""
        cached = self._keys.get((group.name, headings))
        if cached is None:
            index = [group.headings.index(h) for h in headings]
            cached = frozenset(tuple(_cell(row, i) for i in index) for row in group.rows)
            self._keys[(group.name, headings)] = cached
        return cached


def _cell(row: tuple[str, ...], index: int) -> str:
    return row[index] if index < len(row) else ""


def _column(group: Group | None, heading: str) -> list[str]:
    if group is None or heading not in group.headings:
        return []
    index = group.headings.index(heading)
    return [_cell(row, index) for row in group.rows]


def _first(group: Group | None, heading: str) -> str | None:
    """The value under ``heading`` in the first row of ``group``; None when there is none."""
    values = _column(group, heading)
    return values[0] if values else None


@dataclass(frozen=True, slots=True)
class _Lookups:
    units: frozenset[str] | None
    types: frozenset[str] | None
    abbreviations: dict[str, frozenset[str]] | None
    file_sets: frozenset[str] | None
    delimiter: str
    concatenator: str


def _lookups(file: File) -> _Lookups:
    groups = file.groups
    unit, type_, abbr = groups.get(_UNIT), groups.get(_TYPE), groups.get(_ABBR)
    files = groups.get(_FILE)
    abbreviations: dict[str, frozenset[str]] | None = None
    if abbr is not None:
        found: dict[str, set[str]] = {}
        for heading, code in zip(
            _column(abbr, "ABBR_HDNG"), _column(abbr, "ABBR_CODE"), strict=False
        ):
            found.setdefault(heading, set()).add(code.upper())
        abbreviations = {k: frozenset(v) for k, v in found.items()}
    tran = groups.get(_TRAN)
    delimiter = _first(tran, "TRAN_DLIM")
    return _Lookups(
        units=None if unit is None else frozenset(_column(unit, "UNIT_UNIT")),
        types=None if type_ is None else frozenset(_column(type_, "TYPE_TYPE")),
        abbreviations=abbreviations,
        file_sets=None if files is None else frozenset(_column(files, _FSET)),
        delimiter=delimiter if delimiter is not None and len(delimiter) == 1 else "|",
        concatenator=concatenator(file),
    )


def _names(ctx: _Context, group: Group) -> None:
    """Rules 19, 19a and 19b, and a heading named twice (Rule 2b)."""
    name = shown(group.name)
    if not _GROUP_NAME.fullmatch(group.name):
        ctx.add(
            RULE_19,
            f"Group name {name} is not 1 to 4 uppercase letters and digits.",
            group,
        )
    seen: set[str] = set()
    for heading in group.headings:
        label = shown(heading)
        if heading in seen:
            ctx.add(
                RULE_2B,
                f"Heading {label} appears more than once in the HEADING row of group {name}.",
                group,
                header="heading",
            )
            continue
        seen.add(heading)
        if not _HEADING_NAME.fullmatch(heading):
            ctx.add(
                RULE_19A,
                f"Heading {label} of group {name} is not 1 to 9 uppercase letters, digits"
                " and underscores.",
                group,
                header="heading",
            )
        _rule_19b(ctx, group, heading)


def _rule_19b(ctx: _Context, group: Group, heading: str) -> None:
    prefix, underscore, rest = heading.partition("_")
    label, name = shown(heading), shown(group.name)
    if not underscore or not prefix or not rest:
        ctx.add(
            RULE_19B,
            f"Heading {label} of group {name} does not start with a group name and an underscore.",
            group,
            header="heading",
        )
        return
    if prefix == group.name:
        return
    other = ctx.file.groups.get(prefix)
    if other is not None:
        known = heading in other.headings
    else:
        definition = ctx.view.group(prefix)
        if definition is None or not (ctx.standard or definition.defined):
            return  # the group it refers to is neither in the file nor known
        known = heading in definition.headings
    if not known:
        ctx.add(
            RULE_19B,
            f"Heading {label} of group {name} starts with the name of another group, which"
            " has no heading of that name.",
            group,
            header="heading",
        )


def _header_rows(ctx: _Context, group: Group, look: _Lookups) -> None:
    """Rules 1, 15 and 17 on the UNIT and TYPE rows, and Rule 1 on the names."""
    name = shown(group.name)
    texts = (group.name, *group.headings, *group.units, *group.types)
    if not all(text.isascii() for text in texts):
        ctx.add(
            RULE_1,
            f"The GROUP or a header row of group {name} holds a character that is not ASCII.",
            group,
        )
    if look.units is not None:
        for heading, unit in zip(group.headings, group.units, strict=False):
            if unit and unit not in look.units:
                ctx.add(
                    RULE_15,
                    f"The unit of heading {shown(heading)} in group {name} is not listed in"
                    " the UNIT group.",
                    group,
                    header="unit",
                )
    if look.types is not None:
        for heading, data_type in zip(group.headings, group.types, strict=False):
            if data_type and data_type not in look.types:
                ctx.add(
                    RULE_17,
                    f"The data type of heading {shown(heading)} in group {name} is not listed"
                    " in the TYPE group.",
                    group,
                    header="type",
                )


def _in_order(present: list[str], listed: Iterable[str]) -> bool:
    rank = {heading: i for i, heading in enumerate(listed)}
    ranks = [rank[h] for h in present]
    return ranks == sorted(ranks)


def _order(ctx: _Context, group: Group) -> None:
    """Rules 7, 9 and 18a: heading order against the dictionary and the DICT group."""
    name = shown(group.name)
    own = ctx.own.group(group.name)
    user = {} if own is None else own.headings
    definition = ctx.view.group(group.name)
    if ctx.standard and definition is not None and definition.defined:
        standard = [h for h in definition.headings if h not in user]
        present = [h for h in group.headings if h in definition.headings and h not in user]
        if not _in_order(present, standard):
            ctx.add(
                RULE_7,
                f"The headings of group {name} are not in the order the dictionary gives.",
                group,
                header="heading",
            )
    if own is None:
        return
    present_user = [h for h in group.headings if h in user]
    if not _in_order(present_user, user):
        ctx.add(
            RULE_18A,
            f"The user-defined headings of group {name} are not in the order the DICT group gives.",
            group,
            header="heading",
        )
    if present_user and not own.defined:
        first = group.headings.index(present_user[0])
        if any(h not in user for h in group.headings[first:]):
            ctx.add(
                RULE_9,
                f"A user-defined heading of group {name} comes before a standard heading;"
                " user-defined headings go at the end of the HEADING row.",
                group,
                header="heading",
            )


@dataclass(slots=True)
class _Plan:
    """What to check in each column of one group's DATA rows."""

    values: list[tuple[int, str, Callable[[str], bool]]] = field(default_factory=list)
    abbreviations: list[tuple[int, str]] = field(default_factory=list)
    units: list[tuple[int, str]] = field(default_factory=list)
    types: list[tuple[int, str]] = field(default_factory=list)
    links: list[tuple[int, str]] = field(default_factory=list)
    text: list[tuple[int, str]] = field(default_factory=list)
    required: list[tuple[int, str]] = field(default_factory=list)
    keys: tuple[int, ...] = ()
    parent: tuple[Group, tuple[str, ...], tuple[int, ...]] | None = None
    files: int | None = None


def _plan(ctx: _Context, group: Group) -> _Plan:
    """What to check in each column, from the TYPE row and the dictionary."""
    plan = _Plan()
    headings = group.headings
    by_type = {
        "PA": plan.abbreviations,
        "PU": plan.units,
        "PT": plan.types,
        "RL": plan.links,
    }
    for index, heading in enumerate(headings):
        if heading in headings[:index]:
            continue  # a heading named twice is checked once (Rule 2b reports it)
        data_type = group.types[index].strip() if index < len(group.types) else ""
        unit = group.units[index] if index < len(group.units) else ""
        check = _value_check(data_type, unit)
        if check is not None:
            plan.values.append((index, heading, check))
        else:
            if heading not in _SEPARATORS:
                plan.text.append((index, heading))
            by_type.get(data_type, []).append((index, heading))
        if heading == _FSET and group.name != _FILE:
            plan.files = index
    definition = ctx.view.group(group.name)
    if definition is not None:
        _plan_dictionary(ctx, group, definition, plan)
    return plan


def _plan_dictionary(ctx: _Context, group: Group, definition: GroupDef, plan: _Plan) -> None:
    """Rules 10a, 10b and 10c: the headings the dictionary asks for, and what to check."""
    name, headings = shown(group.name), group.headings
    for heading in definition.required:
        if heading in headings:
            plan.required.append((headings.index(heading), heading))
        else:
            ctx.add(
                RULE_10B,
                f"REQUIRED heading {shown(heading)} is missing from group {name}.",
                group,
                header="heading",
            )
    missing = [h for h in definition.keys if h not in headings]
    for heading in missing:
        ctx.add(
            RULE_10A,
            f"KEY heading {shown(heading)} is missing from group {name}.",
            group,
            header="heading",
        )
    if not definition.defined:
        return  # only some headings are known: the KEY set and the parent are not
    if definition.keys and not missing:
        plan.keys = tuple(headings.index(h) for h in definition.keys)
    if definition.parent and ctx.complete:
        plan.parent = _parent(ctx, group, definition)


def _parent(
    ctx: _Context, group: Group, definition: GroupDef
) -> tuple[Group, tuple[str, ...], tuple[int, ...]] | None:
    """The parent group, its KEY headings and where they are in ``group`` (Rule 10c)."""
    parent_name = definition.parent or ""
    name = shown(group.name)
    parent = ctx.file.groups.get(parent_name)
    if parent is None:
        ctx.add(
            RULE_10C,
            f"Group {name} needs its parent group {shown(parent_name)}, which is not in the file.",
            group,
        )
        return None
    parent_def = ctx.view.group(parent_name)
    if parent_def is None or not parent_def.defined or not parent_def.keys:
        return None
    keys = parent_def.keys
    if any(h not in parent.headings for h in keys):
        return None  # Rule 10a reports the parent's missing KEY heading
    absent = [h for h in keys if h not in group.headings]
    if absent:
        ctx.add(
            RULE_10C,
            f"Group {name} lacks the KEY heading {shown(absent[0])} of its parent group"
            f" {shown(parent_name)}.",
            group,
            header="heading",
        )
        return None
    return parent, keys, tuple(group.headings.index(h) for h in keys)


def _rows(ctx: _Context, group: Group, plan: _Plan, look: _Lookups) -> bool:
    """Check every DATA row of ``group``; True when it holds a PA value."""
    name = shown(group.name)
    seen_keys: dict[tuple[str, ...], int] = {}
    parent_keys = None if plan.parent is None else ctx.key_set(plan.parent[0], plan.parent[1])
    used_abbreviation = False
    for number, row in enumerate(group.rows, start=1):
        ctx.tick()
        if not all(value.isascii() for value in row):
            ctx.add(
                RULE_1,
                f"DATA row {number} of group {name} holds a character that is not ASCII.",
                group,
                row=number,
            )
        if any("\r" in value or "\n" in value for value in row):
            ctx.add(
                RULE_6,
                f"A value in DATA row {number} of group {name} holds a line break.",
                group,
                row=number,
            )
        _row_values(ctx, group, number, row, plan)
        used_abbreviation = _row_lookups(ctx, group, number, row, plan, look) or used_abbreviation
        _row_dictionary(ctx, group, number, row, plan, seen_keys, parent_keys)
        if plan.files is not None and look.file_sets is not None:
            value = _cell(row, plan.files)
            if value and value not in look.file_sets:
                ctx.add(
                    RULE_20,
                    f"FILE_FSET in DATA row {number} of group {name} names a file set that"
                    " the FILE group does not list.",
                    group,
                    row=number,
                    heading=_FSET,
                )
    return used_abbreviation


def _row_values(
    ctx: _Context, group: Group, number: int, row: tuple[str, ...], plan: _Plan
) -> None:
    """Rule 8 and formula-like text in one row."""
    name = shown(group.name)
    for index, heading, valid in plan.values:
        value = _cell(row, index)
        if value and not valid(value):
            ctx.add(
                RULE_8,
                f"The value under {shown(heading)} in DATA row {number} of group {name}"
                " does not match the heading's TYPE (or the date format of its UNIT).",
                group,
                row=number,
                heading=heading,
            )
    for index, heading in plan.text:
        if _formula_like(_cell(row, index)):
            ctx.add(
                FORMULA_TEXT,
                f"The text under {shown(heading)} in DATA row {number} of group {name}"
                " starts like a spreadsheet formula (=, +, - or @); it is kept as written.",
                group,
                row=number,
                heading=heading,
            )


def _row_lookups(  # noqa: PLR0917  # one row's place, passed down
    ctx: _Context,
    group: Group,
    number: int,
    row: tuple[str, ...],
    plan: _Plan,
    look: _Lookups,
) -> bool:
    """Rules 11, 11c, 15, 16, 16a and 17 in one row; True when it holds a PA value."""
    name = shown(group.name)
    used = False
    for index, heading in plan.abbreviations:
        value = _cell(row, index)
        if value:
            used = True
            if look.abbreviations is not None:
                _abbreviation(ctx, group, number, heading, value, look)
    for column, rule, known, what, listed in (
        (plan.units, RULE_15, look.units, "unit", "UNIT"),
        (plan.types, RULE_17, look.types, "data type", "TYPE"),
    ):
        if known is None:
            continue
        for index, heading in column:
            value = _cell(row, index)
            if value and value not in known:
                ctx.add(
                    rule,
                    f"The {what} under {shown(heading)} in DATA row {number} of group {name}"
                    f" is not listed in the {listed} group.",
                    group,
                    row=number,
                    heading=heading,
                )
    for index, heading in plan.links:
        value = _cell(row, index)
        if value:
            _links(ctx, group, number, heading, value, look)
    return used


def _row_dictionary(  # noqa: PLR0917  # one row's place, passed down
    ctx: _Context,
    group: Group,
    number: int,
    row: tuple[str, ...],
    plan: _Plan,
    seen_keys: dict[tuple[str, ...], int],
    parent_keys: frozenset[tuple[str, ...]] | None,
) -> None:
    """Rules 10a, 10b and 10c in one row."""
    name = shown(group.name)
    for index, heading in plan.required:
        if not _cell(row, index).strip():
            ctx.add(
                RULE_10B,
                f"REQUIRED heading {shown(heading)} is empty in DATA row {number} of group {name}.",
                group,
                row=number,
                heading=heading,
            )
    if plan.keys:
        key = tuple(_cell(row, i) for i in plan.keys)
        first = seen_keys.setdefault(key, number)
        if first != number:
            ctx.add(
                RULE_10A,
                f"DATA row {number} of group {name} has the same KEY values as row {first}.",
                group,
                row=number,
            )
    if plan.parent is not None and parent_keys is not None:
        key = tuple(_cell(row, i) for i in plan.parent[2])
        if key not in parent_keys:
            ctx.add(
                RULE_10C,
                f"DATA row {number} of group {name} has no matching row in its parent group"
                f" {shown(plan.parent[0].name)}.",
                group,
                row=number,
            )


def _abbreviation(  # noqa: PLR0917  # one value's place, passed down
    ctx: _Context, group: Group, number: int, heading: str, value: str, look: _Lookups
) -> None:
    """Rules 16 and 16a for one PA value."""
    known = (look.abbreviations or {}).get(heading, frozenset())
    parts = value.split(look.concatenator) if look.concatenator in value else [value]
    combined = len(parts) > 1
    for part in parts:
        if part.upper() not in known:
            rule = RULE_16A if combined else RULE_16
            what = "A part of the combined abbreviation" if combined else "The abbreviation"
            ctx.add(
                rule,
                f"{what} under {shown(heading)} in DATA row {number} of group"
                f" {shown(group.name)} is not defined for that heading in the ABBR group.",
                group,
                row=number,
                heading=heading,
            )
            return


def _links(  # noqa: PLR0917  # one value's place, passed down
    ctx: _Context, group: Group, number: int, heading: str, value: str, look: _Lookups
) -> None:
    """Rules 11 and 11c for one Record Link value."""
    label, name = shown(heading), shown(group.name)
    for link in value.split(look.concatenator):
        target_name, *keys = link.split(look.delimiter)
        if not _GROUP_NAME.fullmatch(target_name) or not keys:
            ctx.add(
                RULE_11,
                f"The record link under {label} in DATA row {number} of group {name} is not a"
                " GROUP name followed by KEY values.",
                group,
                row=number,
                heading=heading,
            )
            return
        target = ctx.file.groups.get(target_name)
        if target is None:
            if ctx.complete:
                ctx.add(
                    RULE_11C,
                    f"The record link under {label} in DATA row {number} of group {name}"
                    " refers to a group that is not in the file.",
                    group,
                    row=number,
                    heading=heading,
                )
            return
        definition = ctx.view.group(target_name)
        if definition is None or not definition.defined or not definition.keys:
            continue  # its KEY headings are not known: the dictionary tier
        if any(h not in target.headings for h in definition.keys):
            continue
        if len(keys) != len(definition.keys) or tuple(keys) not in ctx.key_set(
            target, definition.keys
        ):
            ctx.add(
                RULE_11C,
                f"The record link under {label} in DATA row {number} of group {name} matches"
                f" no row of group {shown(target_name)} by its KEY values.",
                group,
                row=number,
                heading=heading,
            )
            return


def _single_row(ctx: _Context, name: str, rule: str) -> None:
    """Rules 13 and 14: the group is there and has exactly one DATA row."""
    group = ctx.file.groups.get(name)
    if group is None:
        if ctx.complete:
            ctx.add(rule, f"The file has no {name} group.")
        return
    for number in range(2, len(group.rows) + 1):
        ctx.add(
            rule,
            f"Group {name} has more than one DATA row; this is row {number}.",
            group,
            row=number,
        )


def _transmission(ctx: _Context) -> None:
    """Rules 11a and 11b: TRAN_DLIM and TRAN_RCON, when given, are one character."""
    tran = ctx.file.groups.get(_TRAN)
    for heading, rule, what in (
        ("TRAN_DLIM", RULE_11A, "delimiter"),
        ("TRAN_RCON", RULE_11B, "concatenator"),
    ):
        value = _first(tran, heading)
        if tran is not None and value is not None and len(value) != 1:
            ctx.add(
                rule,
                f"{heading} must define the record link {what} as one character.",
                tran,
                row=1,
                heading=heading,
            )


def _dict_rows(ctx: _Context) -> None:
    """Rule 18: each DICT row says what it defines."""
    group = ctx.file.groups.get(DICT)
    if group is None:
        return
    needed = {
        "GROUP": ("DICT_GRP", "DICT_DESC"),
        "HEADING": ("DICT_GRP", "DICT_HDNG", "DICT_STAT", "DICT_DTYP", "DICT_DESC"),
    }
    for number, record in enumerate(group.records(), start=1):
        kind = record.get("DICT_TYPE", "").strip().upper()
        if kind not in needed:
            ctx.add(
                RULE_18,
                f"DICT_TYPE in DATA row {number} of the DICT group is neither GROUP nor HEADING.",
                group,
                row=number,
                heading="DICT_TYPE",
            )
            continue
        for heading in needed[kind]:
            if not record.get(heading, "").strip():
                ctx.add(
                    RULE_18,
                    f"{heading} is empty in DATA row {number} of the DICT group, which"
                    f" defines a {kind}.",
                    group,
                    row=number,
                    heading=heading,
                )


def _standard_names(ctx: _Context) -> None:
    """Rules 9 and 18 with a standard dictionary: every name is standard or defined."""
    own = ctx.own
    undefined = False
    for group in ctx.file.groups.values():
        definition = ctx.view.group(group.name)
        name = shown(group.name)
        if definition is None or not definition.defined:
            ctx.add(
                RULE_9,
                f"Group {name} is neither in the dictionary nor defined in the DICT group.",
                group,
            )
            undefined = undefined or own.group(group.name) is None
            continue
        for heading in group.headings:
            if heading not in definition.headings and not _borrowed(ctx, heading):
                ctx.add(
                    RULE_9,
                    f"Heading {shown(heading)} of group {name} is neither in the dictionary"
                    " nor defined in the DICT group.",
                    group,
                    header="heading",
                )
                undefined = True
    if undefined and DICT not in ctx.file.groups:
        ctx.add(
            RULE_18,
            "The file uses group or heading names that are not in the dictionary, and has"
            " no DICT group to define them.",
        )


def _own_names(ctx: _Context) -> None:
    """Rule 9 without a dictionary: the headings of each group the DICT group defines.

    A heading that names another group (Rule 19b) is judged only when that group is the
    file's own too; a standard group's headings are unknown without the dictionary.
    """
    for group in ctx.file.groups.values():
        definition = ctx.own.group(group.name)
        if definition is None or not definition.defined:
            continue
        for heading in group.headings:
            if heading in definition.headings or _borrowed(ctx, heading):
                continue
            prefix = heading.partition("_")[0]
            other = ctx.own.group(prefix)
            if prefix != group.name and (other is None or not other.defined):
                continue
            ctx.add(
                RULE_9,
                f"Heading {shown(heading)} of group {shown(group.name)} is not defined in"
                " the DICT group, which defines the group.",
                group,
                header="heading",
            )


def _borrowed(ctx: _Context, heading: str) -> bool:
    """A heading of another group (Rule 19b), defined there."""
    prefix = heading.partition("_")[0]
    other = ctx.view.group(prefix)
    return other is not None and heading in other.headings


def _file_group(ctx: _Context) -> None:
    """Rule 20, for the FILE group itself: file names, never paths. Nothing is opened."""
    group = ctx.file.groups.get(_FILE)
    for number, name in enumerate(_column(group, "FILE_NAME"), start=1):
        if group is not None and (
            "/" in name or "\\" in name or name in (".", "..") or ":" in name
        ):
            ctx.add(
                RULE_20,
                f"FILE_NAME in DATA row {number} of the FILE group is a path, not a file name"
                " in the file set's folder.",
                group,
                row=number,
                heading="FILE_NAME",
            )


def check_tables(
    file: File,
    *,
    dictionary: Dictionary | None = None,
    lines: dict[str, GroupLines] | None = None,
    broken: Iterable[int] = (),
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[Finding], bool]:
    """Check ``file`` against the AGS4 rules; the findings, and whether the check finished.

    The reader's notes are not repeated here; :func:`pydiggs.ags4.check` reports both.

    Args:
        file: The file, as read or as built in code.
        dictionary: A standard dictionary (the view of its DICT group), or None. The
            file's own DICT group is laid over it.
        lines: Where each group's rows are (:func:`index_lines`); None when unknown.
        broken: Lines that end inside a quoted value (Rule 6).
        should_stop: Called every so often; True stops the check.
    """
    own = from_file(file)
    out = _Out()
    ctx = _Context(
        file=file,
        view=own if dictionary is None else overlay(dictionary, own),
        own=own,
        standard=dictionary is not None,
        lines=lines or {},
        out=out,
        should_stop=should_stop,
        complete=file.complete,
    )
    finished = True
    try:
        ctx.poll()
        _check(ctx, broken)
        ctx.poll()
    except Stopped:
        finished = False
    out.summarise()
    return out.findings, finished


def _check(ctx: _Context, broken: Iterable[int]) -> None:
    file = ctx.file
    for line in broken:
        ctx.out.add(
            RULE_6,
            "A line ends inside a quoted value that a later line closes: a value holds a"
            " line break.",
            line,
            None,
        )
    look = _lookups(file)
    if file.source is None:
        _built_in_code(ctx)
    used_abbreviation = False
    for group in file.groups.values():
        ctx.poll()
        if not group.rows:
            ctx.add(RULE_2, f"Group {shown(group.name)} has no DATA rows.", group)
        _names(ctx, group)
        _header_rows(ctx, group, look)
        _order(ctx, group)
        plan = _plan(ctx, group)
        used_abbreviation = _rows(ctx, group, plan, look) or used_abbreviation
    _single_row(ctx, _PROJ, RULE_13)
    _single_row(ctx, _TRAN, RULE_14)
    _transmission(ctx)
    if ctx.complete:
        if look.units is None:
            ctx.add(RULE_15, "The file has no UNIT group.")
        if look.types is None:
            ctx.add(RULE_17, "The file has no TYPE group.")
        if used_abbreviation and look.abbreviations is None:
            ctx.add(RULE_16, "The file uses abbreviations (PA values) and has no ABBR group.")
        if look.file_sets is None and _file_sets_used(file):
            ctx.add(RULE_20, "The file names file sets (FILE_FSET) and has no FILE group.")
    _dict_rows(ctx)
    if ctx.standard:
        _standard_names(ctx)
    else:
        _own_names(ctx)
    _file_group(ctx)


def _file_sets_used(file: File) -> bool:
    return any(
        any(_column(group, _FSET))
        for name, group in file.groups.items()
        if name != _FILE and _FSET in group.headings
    )


def _built_in_code(ctx: _Context) -> None:
    """What the reader would have noted, for a file built in code: Rules 2b and 4."""
    for group in ctx.file.groups.values():
        name = shown(group.name)
        width = len(group.headings)
        for descriptor, row in (("UNIT", group.units), ("TYPE", group.types)):
            if not row and width:
                ctx.add(RULE_2B, f"Group {name} has no {descriptor} row.", group)
            elif len(row) != width:
                ctx.add(
                    RULE_4,
                    f"The {descriptor} row of group {name} has {len(row)} values where the"
                    f" HEADING row has {width} headings.",
                    group,
                )
        for number, row in enumerate(group.rows, start=1):
            if len(row) != width:
                ctx.add(
                    RULE_4,
                    f"DATA row {number} of group {name} has {len(row)} values where the"
                    f" HEADING row has {width} headings.",
                    group,
                    row=number,
                )
