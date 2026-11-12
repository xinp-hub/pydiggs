"""A dictionary view built from an AGS4 DICT group: groups, headings, keys and parents.

Pure: no I/O. An AGS4 data dictionary is itself an AGS4 file whose DICT group lists
every group (``DICT_TYPE`` ``GROUP``, with its parent in ``DICT_PGRP``) and every
heading (``DICT_TYPE`` ``HEADING``, with its status in ``DICT_STAT``), in order. A
data file's own DICT group has the same shape and defines its user-defined groups and
headings (AGS4 Rule 18). The checker builds one view from each and lays the file's
over the standard one.

pyDIGGS bundles no AGS dictionary. The standard one is used only when the caller passes
it, so most rules that need it run only for what the file's own DICT group defines.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from pydiggs.ags4._table import File, Group

__all__ = ["DICT", "Dictionary", "GroupDef", "HeadingDef", "build", "from_file", "overlay"]

DICT: Final = "DICT"
_TYPE: Final = "DICT_TYPE"
_GROUP: Final = "DICT_GRP"
_HEADING: Final = "DICT_HDNG"
_STATUS: Final = "DICT_STAT"
_PARENT: Final = "DICT_PGRP"
_KEY: Final = "KEY"
_REQUIRED: Final = "REQUIRED"


@dataclass(frozen=True, slots=True)
class HeadingDef:
    """One heading of a group, as a dictionary defines it."""

    name: str
    key: bool
    required: bool


@dataclass(slots=True)
class GroupDef:
    """One group, as a dictionary defines it.

    ``defined`` is True when the dictionary has a GROUP row for it (so its headings,
    keys and parent are the whole story); False when it only lists headings that a
    file appends to the group (user-defined headings in a standard group).
    """

    name: str
    defined: bool = False
    parent: str | None = None
    headings: dict[str, HeadingDef] = field(default_factory=dict)

    @property
    def keys(self) -> tuple[str, ...]:
        """The KEY headings, in dictionary order."""
        return tuple(h.name for h in self.headings.values() if h.key)

    @property
    def required(self) -> tuple[str, ...]:
        """The REQUIRED headings, in dictionary order."""
        return tuple(h.name for h in self.headings.values() if h.required)


@dataclass(slots=True)
class Dictionary:
    """Groups by name, each with its headings in dictionary order."""

    groups: dict[str, GroupDef] = field(default_factory=dict)

    def group(self, name: str) -> GroupDef | None:
        """The definition of ``name``, or None."""
        return self.groups.get(name)

    def heading(self, group: str, heading: str) -> HeadingDef | None:
        """The definition of ``heading`` in ``group``, or None."""
        found = self.groups.get(group)
        return None if found is None else found.headings.get(heading)

    def __bool__(self) -> bool:
        return bool(self.groups)


def _status(text: str) -> tuple[bool, bool]:
    """(key, required) from a DICT_STAT value such as ``KEY+REQUIRED`` or ``*R``."""
    words = {w for w in text.upper().replace("+", " ").replace(",", " ").split() if w}
    key = _KEY in words or "*" in text
    required = _REQUIRED in words or text.strip().upper() in ("R", "*R")
    return key, required


def _value(index: int | None, row: tuple[str, ...]) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def build(dict_group: Group | None) -> Dictionary:
    """The view a DICT group defines; an empty view for None.

    A row that names no group, or a HEADING row that names no heading, is skipped: the
    checker reports such rows under Rule 18 itself.
    """
    view = Dictionary()
    if dict_group is None:
        return view
    headings = dict_group.headings

    def where(name: str) -> int | None:
        return headings.index(name) if name in headings else None

    kind_at, group_at, heading_at = where(_TYPE), where(_GROUP), where(_HEADING)
    status_at, parent_at = where(_STATUS), where(_PARENT)
    for row in dict_group.rows:
        kind = _value(kind_at, row).upper()
        name = _value(group_at, row)
        if not name:
            continue
        definition = view.groups.setdefault(name, GroupDef(name))
        if kind == "GROUP":
            definition.defined = True
            definition.parent = _value(parent_at, row) or None
        elif kind == "HEADING":
            heading = _value(heading_at, row)
            if heading and heading not in definition.headings:
                key, required = _status(_value(status_at, row))
                definition.headings[heading] = HeadingDef(heading, key, required)
    return view


def overlay(standard: Dictionary, own: Dictionary) -> Dictionary:
    """``standard`` with ``own`` laid over it: own groups added, own headings appended.

    A heading both define keeps the standard definition (the file cannot redefine a
    standard heading); a heading only the file defines goes after the standard ones,
    which is where AGS4 Rule 9 puts user-defined headings.
    """
    out = Dictionary()
    for name, group in standard.groups.items():
        out.groups[name] = GroupDef(name, group.defined, group.parent, dict(group.headings))
    for name, group in own.groups.items():
        merged = out.groups.setdefault(name, GroupDef(name))
        if group.defined and not merged.defined:
            merged.defined, merged.parent = True, group.parent
        for heading, definition in group.headings.items():
            merged.headings.setdefault(heading, definition)
    return out


def from_file(file: File) -> Dictionary:
    """The view of a whole dictionary file (its DICT group)."""
    groups: Mapping[str, Group] = file.groups
    return build(groups.get(DICT))
