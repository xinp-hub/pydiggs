"""The AGS4 writer: a :class:`~pydiggs.ags4.File` to text, the same way every time.

Pure: no I/O. Every field is quoted, a quote inside a value is doubled, each group is
its GROUP, HEADING, UNIT and TYPE rows and then its DATA rows, groups come in the order
given with one blank line between them, and every line ends with CR LF. A value is
written exactly as it is held: nothing is trimmed, converted or guarded (a value that a
spreadsheet would take for a formula is written unchanged).
"""

from __future__ import annotations

from typing import Final

from pydiggs.ags4._parse import shown
from pydiggs.ags4._table import File, Group

__all__ = ["CRLF", "emit"]

CRLF: Final = "\r\n"
#: Characters no AGS4 value can hold: a line break ends the row (Rule 6), and a file
#: with a NUL is read as binary.
_FORBIDDEN: Final = ("\r", "\n", "\x00")


def _row(descriptor: str, values: tuple[str, ...]) -> str:
    return '"' + '","'.join([descriptor, *(value.replace('"', '""') for value in values)]) + '"'


def _lines(group: Group) -> list[str]:
    """The rows of one group; ValueError when it cannot be written."""
    name = shown(group.name)
    if not group.name:
        raise ValueError("a group with an empty name cannot be written")
    width = len(group.headings)
    if len(group.units) != width or len(group.types) != width:
        raise ValueError(
            f"group {name} cannot be written: its UNIT and TYPE rows must be as long as its"
            " HEADING row"
        )
    lines = [
        _row("GROUP", (group.name,)),
        _row("HEADING", group.headings),
        _row("UNIT", group.units),
        _row("TYPE", group.types),
    ]
    for number, row in enumerate(group.rows, start=1):
        if len(row) != width:
            raise ValueError(
                f"group {name} cannot be written: DATA row {number} has {len(row)} values for"
                f" {width} headings"
            )
        lines.append(_row("DATA", row))
    for number, line in enumerate(lines):
        if any(character in line for character in _FORBIDDEN):
            where = ("GROUP", "HEADING", "UNIT", "TYPE")[number] if number < 4 else "DATA"  # noqa: PLR2004
            raise ValueError(
                f"group {name} cannot be written: a {where} row holds a line break or a NUL"
                " character"
            )
    return lines


def emit(file: File) -> str:
    """Return ``file`` as AGS4 text.

    Raises:
        TypeError: ``file`` is not a :class:`~pydiggs.ags4.File`.
        ValueError: The file has no group (an AGS4 file is made of groups), or a group
            cannot be written: an empty name, UNIT, TYPE or DATA rows that are not as
            long as the HEADING row, or a line break or NUL character in a value.
    """
    if not isinstance(file, File):
        raise TypeError(f"file must be an ags4.File, got {type(file).__name__}")
    if not file.groups:
        raise ValueError("a file with no group cannot be written: AGS4 has no empty file")
    blocks = [CRLF.join(_lines(group)) + CRLF for group in file.groups.values()]
    return CRLF.join(blocks)
