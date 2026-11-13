"""The AGS4 data model: a :class:`File` of :class:`Group` tables, every value a string.

Pure: no I/O. Nothing is converted here: a number, a date or a yes/no stays the text
the file holds, and reading by the declared TYPE is left to the checker and the
converter.
"""

from __future__ import annotations

import types
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pydiggs.findings.model import Finding

__all__ = ["AGS_VERSIONS", "File", "Group", "SourceInfo", "declared_version"]

#: The AGS4 versions pyDIGGS reads and writes, oldest first.
AGS_VERSIONS: Final[tuple[str, ...]] = ("4.0.3", "4.0.4", "4.1", "4.1.1", "4.2")

_TRAN: Final = "TRAN"
_TRAN_AGS: Final = "TRAN_AGS"


def _strings(name: str, values: object) -> tuple[str, ...]:
    """``values`` as a tuple of str; a bare str is refused (it is not a row)."""
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise TypeError(f"{name} must be a sequence of str, got {type(values).__name__}")
    out = tuple(values)
    for value in out:
        if not isinstance(value, str):
            raise TypeError(f"{name} must hold only str, got {type(value).__name__}")
    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class Group:
    """One AGS4 group: a table with a name, three header rows and its DATA rows.

    ``headings``, ``units`` and ``types`` are the HEADING, UNIT and TYPE rows; each of
    ``rows`` is one DATA row. Every value is the string as written (an empty string for
    a null). ``units`` or ``types`` is an empty tuple when the group has no such row:
    :func:`pydiggs.ags4.read` keeps a missing row missing (and notes it under
    ``ags4.rule-2b``) rather than inventing one, and :func:`pydiggs.ags4.dumps` writes
    none for it. A row that is there but empty holds one ``""`` per heading.

    ``line`` is the 1-based line of the GROUP row in the file the group was
    read from, None for a group built in code; it takes no part in equality.

    A group built in code may be ragged (a row shorter than ``headings``): it is
    :func:`pydiggs.ags4.write` that refuses it. A group that :func:`pydiggs.ags4.read`
    returns is always rectangular: every row is as long as ``headings``, and ``units``
    and ``types`` are too unless they are empty for a row the file lacks.

    Examples:
        >>> from pydiggs import ags4
        >>> loca = ags4.Group(
        ...     name="LOCA",
        ...     headings=("LOCA_ID", "LOCA_GL"),
        ...     units=("", "m"),
        ...     types=("ID", "2DP"),
        ...     rows=[("BH01", "12.50"), ("BH02", "")],
        ... )
        >>> loca.rows[0], loca.column("LOCA_GL")
        (('BH01', '12.50'), ('12.50', ''))
        >>> loca.records()[1]
        {'LOCA_ID': 'BH02', 'LOCA_GL': ''}
    """

    name: str
    headings: tuple[str, ...]
    units: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    line: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError(f"name must be str, got {type(self.name).__name__}")
        object.__setattr__(self, "headings", _strings("headings", self.headings))
        object.__setattr__(self, "units", _strings("units", self.units))
        object.__setattr__(self, "types", _strings("types", self.types))
        rows: object = self.rows
        if isinstance(rows, str) or not isinstance(rows, Iterable):
            raise TypeError(f"rows must be a sequence of rows, got {type(rows).__name__}")
        object.__setattr__(self, "rows", tuple(_strings("a row", row) for row in rows))
        if self.line is not None and (type(self.line) is not int or self.line < 1):
            raise ValueError(f"line must be a positive int or None, got {self.line!r}")

    @classmethod
    def _trusted(
        cls,
        *,
        name: str,
        headings: tuple[str, ...],
        units: tuple[str, ...],
        types: tuple[str, ...],
        rows: tuple[tuple[str, ...], ...],
        line: int | None,
    ) -> Group:
        """Build from tuples of str the reader made itself, without re-checking each cell."""
        group = object.__new__(cls)
        object.__setattr__(group, "name", name)
        object.__setattr__(group, "headings", headings)
        object.__setattr__(group, "units", units)
        object.__setattr__(group, "types", types)
        object.__setattr__(group, "rows", rows)
        object.__setattr__(group, "line", line)
        return group

    def records(self) -> list[dict[str, str]]:
        """The DATA rows as dicts keyed by heading, in file order.

        A row shorter than ``headings`` gives a shorter dict; values past the last
        heading are left out. When a heading is repeated, the last value wins.
        """
        headings = self.headings
        return [dict(zip(headings, row, strict=False)) for row in self.rows]

    def column(self, heading: str) -> tuple[str, ...]:
        """Every row's value under ``heading`` (the first heading of that name).

        Raises:
            KeyError: The group has no such heading.
            ValueError: A row is too short to have the value (a ragged group).
        """
        try:
            index = self.headings.index(heading)
        except ValueError:
            raise KeyError(heading) from None
        try:
            return tuple(row[index] for row in self.rows)
        except IndexError:
            raise ValueError(f"a row of {self.name!r} has no value under {heading!r}") from None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceInfo:
    """The bytes an AGS4 :class:`File` was read from.

    ``encoding`` is how they were decoded: ``"ascii"``, ``"utf-8"``, ``"utf-8-sig"``
    (UTF-8 with a byte order mark) or ``"cp1252"``, or the encoding asked for.
    """

    name: str | None
    sha256: str
    size: int
    encoding: str


def _groups(groups: object) -> Mapping[str, Group]:
    """``groups`` (a mapping or an iterable of groups) as a read-only mapping by name."""
    if isinstance(groups, Mapping):
        items = list(groups.items())
    elif isinstance(groups, Iterable) and not isinstance(groups, str | bytes):
        items = [(getattr(group, "name", None), group) for group in groups]
    else:
        raise TypeError(f"groups must be a mapping or a sequence of Group, got {type(groups)!r}")
    out: dict[str, Group] = {}
    for name, group in items:
        if not isinstance(group, Group):
            raise TypeError(f"groups must hold only Group, got {type(group).__name__}")
        if name != group.name:
            raise ValueError("a groups key is not the name of its group")
        if name in out:
            raise ValueError("two groups have the same name")
        out[group.name] = group
    return types.MappingProxyType(out)


@dataclass(frozen=True, slots=True, init=False)
class File:
    """An AGS4 file as tables of strings: its groups, in file order.

    ``groups`` maps each group name to its :class:`Group`; the constructor also takes a
    plain sequence of groups. The rest describes a file that was read: ``source`` (None
    for a file built in code), ``notes`` (what reading had to work around, as findings:
    not a validity verdict), ``complete`` (False when a limit or the time budget stopped
    the read) and ``limits_hit`` (which ones).

    Two files are equal when their groups are: ``source``, ``notes``, ``complete`` and
    ``limits_hit`` take no part, so ``read_bytes(dumps(f)) == f``.

    Examples:
        >>> from pydiggs import ags4
        >>> tran = ags4.Group(
        ...     name="TRAN",
        ...     headings=("TRAN_ISNO", "TRAN_AGS"),
        ...     units=("", ""),
        ...     types=("X", "X"),
        ...     rows=[("1", "4.1.1")],
        ... )
        >>> f = ags4.File(groups=[tran])
        >>> list(f.groups), f.version, f.complete
        (['TRAN'], '4.1.1', True)
        >>> ags4.read_bytes(ags4.dumps(f)) == f
        True
    """

    groups: Mapping[str, Group]
    source: SourceInfo | None = field(default=None, compare=False)
    notes: tuple[Finding, ...] = field(default=(), compare=False)
    complete: bool = field(default=True, compare=False)
    limits_hit: tuple[str, ...] = field(default=(), compare=False)

    def __init__(
        self,
        *,
        groups: Mapping[str, Group] | Iterable[Group],
        source: SourceInfo | None = None,
        notes: Iterable[Finding] = (),
        complete: bool = True,
        limits_hit: Iterable[str] = (),
    ) -> None:
        """Take the groups as a mapping by name or as a sequence, and freeze them.

        Raises:
            TypeError: ``groups`` holds something that is not a :class:`Group`.
            ValueError: Two groups have the same name, or a key is not its group's name.
        """
        object.__setattr__(self, "groups", _groups(groups))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "notes", tuple(notes))
        object.__setattr__(self, "complete", complete)
        object.__setattr__(self, "limits_hit", tuple(limits_hit))

    def __reduce__(self) -> tuple[object, ...]:
        """Pickle and deep-copy through plain containers; a mappingproxy cannot be pickled."""
        state = (tuple(self.groups.values()), self.source, self.notes, self.complete)
        return (_rebuild, (*state, self.limits_hit))

    @property
    def version(self) -> str | None:
        """The AGS4 version the file declares, when it is one of :data:`AGS_VERSIONS`.

        It is ``TRAN_AGS`` of the first TRAN row. None when the file has no such value
        or declares a version pyDIGGS does not know (the text is still in the table).
        """
        declared = declared_version(self.groups)
        return declared if declared in AGS_VERSIONS else None


def declared_version(groups: Mapping[str, Group]) -> str | None:
    """``TRAN_AGS`` of the first TRAN row, exactly as written; None when there is none."""
    tran = groups.get(_TRAN)
    if tran is None or not tran.rows or _TRAN_AGS not in tran.headings:
        return None
    index = tran.headings.index(_TRAN_AGS)
    row = tran.rows[0]
    return row[index] if index < len(row) else None


def _rebuild(
    groups: tuple[Group, ...],
    source: SourceInfo | None,
    notes: tuple[Finding, ...],
    complete: bool,
    limits_hit: tuple[str, ...],
) -> File:
    """Inverse of :meth:`File.__reduce__`."""
    return File(groups=groups, source=source, notes=notes, complete=complete, limits_hit=limits_hit)
