"""The AGS4 writer: a :class:`~pydiggs.ags4.File` to text, the same way every time.

Pure: no I/O. Every field is quoted, a quote inside a value is doubled, each group is
its GROUP, HEADING, UNIT and TYPE rows and then its DATA rows, groups come in the order
given with one blank line between them, and every line ends with CR LF. A group whose
``units`` (or ``types``) is empty while it has headings is written without that row, as
the reader returns a group whose file had none: no row is invented. A value is
written exactly as it is held: nothing is trimmed, converted or guarded (a value that a
spreadsheet would take for a formula is written unchanged).

What the reader would not read back whole is refused: the writer checks the reader's own
work limits (:class:`~pydiggs.ags4._parse.Limits`), so ``read_bytes(dumps(f)) == f``.
"""

from __future__ import annotations

from typing import Final

from pydiggs.ags4._parse import Limits, shown
from pydiggs.ags4._table import File, Group

__all__ = ["CRLF", "emit"]

CRLF: Final = "\r\n"
#: Characters no AGS4 value can hold: a line break ends the row (Rule 6), and a file
#: with a NUL is read as binary.
_FORBIDDEN: Final = ("\r", "\n", "\x00")
#: The reader's default limits: a file over them would read back cut short.
_LIMITS: Final = Limits()


def _row(descriptor: str, values: tuple[str, ...]) -> str:
    return '"' + '","'.join([descriptor, *(value.replace('"', '""') for value in values)]) + '"'


def _lines(group: Group) -> list[str]:
    """The rows of one group; ValueError when it cannot be written."""
    name = shown(group.name)
    if not group.name:
        raise ValueError("a group with an empty name cannot be written")
    width = len(group.headings)
    if len(group.units) not in (0, width) or len(group.types) not in (0, width):
        raise ValueError(
            f"group {name} cannot be written: its UNIT and TYPE rows must be as long as its"
            " HEADING row, or empty for no row"
        )
    if width > _LIMITS.max_headings:
        raise ValueError(
            f"group {name} cannot be written: it has more than {_LIMITS.max_headings} headings,"
            " which pyDIGGS does not read back"
        )
    rows: list[tuple[str, tuple[str, ...]]] = [
        ("GROUP", (group.name,)),
        ("HEADING", group.headings),
    ]
    # An empty UNIT or TYPE row of a group with headings stands for a row the file lacks.
    rows.extend(
        (where, values)
        for where, values in (("UNIT", group.units), ("TYPE", group.types))
        if values or not width
    )
    for number, row in enumerate(group.rows, start=1):
        if len(row) != width:
            raise ValueError(
                f"group {name} cannot be written: DATA row {number} has {len(row)} values for"
                f" {width} headings"
            )
        rows.append(("DATA", row))
    lines = []
    for where, values in rows:
        line = _row(where, values)
        if any(character in line for character in _FORBIDDEN):
            raise ValueError(
                f"group {name} cannot be written: a {where} row holds a line break or a NUL"
                " character"
            )
        # A value is never longer than its line, so only a long line needs a closer look.
        if len(line) > _LIMITS.max_field_chars and max(map(len, values), default=0) > (
            _LIMITS.max_field_chars
        ):
            raise ValueError(
                f"group {name} cannot be written: a {where} row holds a value longer than"
                f" {_LIMITS.max_field_chars} characters, which pyDIGGS does not read back"
            )
        lines.append(line)
    return lines


def emit(file: File) -> str:
    """Return ``file`` as AGS4 text.

    Raises:
        TypeError: ``file`` is not a :class:`~pydiggs.ags4.File`.
        ValueError: The file has no group (an AGS4 file is made of groups), or a group
            cannot be written: an empty name, UNIT, TYPE or DATA rows that are not as
            long as the HEADING row (an empty UNIT or TYPE is no row and is allowed), or
            a line break or NUL character in a value. Also a file over the reader's
            limits, which would not read back whole: more groups, headings in a group,
            DATA rows or characters in a value than :class:`~pydiggs.ags4._parse.Limits`
            allows.
    """
    if not isinstance(file, File):
        raise TypeError(f"file must be an ags4.File, got {type(file).__name__}")
    if not file.groups:
        raise ValueError("a file with no group cannot be written: AGS4 has no empty file")
    if len(file.groups) > _LIMITS.max_groups:
        raise ValueError(
            f"the file cannot be written: it has more than {_LIMITS.max_groups} groups, which"
            " pyDIGGS does not read back"
        )
    if sum(len(group.rows) for group in file.groups.values()) > _LIMITS.max_rows:
        raise ValueError(
            f"the file cannot be written: it has more than {_LIMITS.max_rows} DATA rows, which"
            " pyDIGGS does not read back"
        )
    blocks = [CRLF.join(_lines(group)) + CRLF for group in file.groups.values()]
    return CRLF.join(blocks)
