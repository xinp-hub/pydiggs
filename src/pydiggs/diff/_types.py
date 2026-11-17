"""The result classes of :mod:`pydiggs.diff`: :class:`Diff`, :class:`Change`, :class:`ValueChange`.

Every class is a frozen, slotted, keyword-only dataclass, like the model classes, so a
result can be shared between threads. Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from pydiggs.findings.model import Finding

__all__ = ["Change", "Diff", "ValueChange"]

#: ``op`` of a :class:`Change` and of a :class:`ValueChange`.
OPS: Final = ("added", "removed", "changed", "reformatted")
#: How two nodes were matched.
MATCHED_BY: Final = ("gml_id", "natural")


def _frozen(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return value if isinstance(value, MappingProxyType) else MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class ValueChange:
    """One value that differs between two matched nodes.

    ``path`` says which value (``name``, ``params.totalPenetration``,
    ``interval.top``, ``results[0].columns[tip_resistance].cells[@1.5]``). ``old`` and
    ``new`` are what each file holds, as JSON values: text, a number, ``"1.50 ft"`` for a
    quantity, a list for a tuple, ``None`` when that side has no such value (``op`` is then
    ``added`` or ``removed``). ``op`` is ``reformatted`` when the same number is written
    differently. ``at`` is the XPath of the node in the new file (the old file for a
    removed node). ``truncated`` is True when ``old`` or ``new`` was cut to fit 200
    characters (or 200 list items).
    """

    op: str
    path: str
    old: Any
    new: Any
    at: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Change:
    """One node that was added, removed or changed.

    ``collection`` is where the node lives (``features``, ``samples``, ``measurements``,
    ``layers``, ``components`` …). ``old_key`` and ``new_key`` are its keys in the two
    files (None on the side that has no such node); ``matched_by`` says how a changed node
    was paired (``gml_id``, or ``natural`` for the natural key). ``values`` lists what
    differs, sorted by path. ``op`` is ``reformatted`` only for a node whose values were
    all written differently (listed with ``show_reformatted=True``). ``truncated`` is True
    when a key or name was cut to 200 characters.
    """

    op: str
    collection: str
    element: str
    name: str | None
    old_key: str | None
    new_key: str | None
    matched_by: str | None = None
    values: tuple[ValueChange, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Diff:
    """The difference between two DIGGS files, at model level.

    ``identical`` is True only when nothing differs and the comparison finished
    (``complete``). ``limits_hit`` names what stopped it (``max_changes``,
    ``time_budget``, ``old_read_incomplete``, ``new_read_incomplete``). ``summary`` counts
    nodes per collection (``added``, ``removed``, ``changed``, ``unchanged``) and stays
    exact when ``max_changes`` stops the list, but is partial after a ``time_budget``
    stop. ``value_changes`` counts every differing value and ``reformatted`` every value
    written differently. ``changes`` is ordered by collection, then key, then path;
    ``notes`` are findings of step ``diff``.
    """

    tool: Mapping[str, str]
    identical: bool
    complete: bool
    limits_hit: tuple[str, ...]
    old: Mapping[str, Any]
    new: Mapping[str, Any]
    options: Mapping[str, Any]
    summary: Mapping[str, Mapping[str, int]]
    value_changes: int
    reformatted: int
    changes: tuple[Change, ...]
    notes: tuple[Finding, ...]

    def __post_init__(self) -> None:
        for name in ("tool", "old", "new", "options"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))
        summary = {k: _frozen(v) for k, v in self.summary.items()}
        object.__setattr__(self, "summary", MappingProxyType(summary))

    def to_dict(self) -> dict[str, Any]:
        """The diff as a diggs-diff 1.0 document (plain dicts and lists)."""
        from pydiggs.diff import _document  # noqa: PLC0415

        return _document.to_document(self)

    def to_json(self, indent: int | None = None) -> str:
        """The diff as diggs-diff 1.0 JSON: keys sorted, no NaN, the same bytes every time."""
        from pydiggs.diff import _document  # noqa: PLC0415

        return _document.dumps(self, indent=indent)

    def to_text(self) -> str:
        """A human-readable report, safe to print through ``rich`` (file text is escaped).

        It lists what the diff holds: reformatted values appear only if the diff was made
        with ``show_reformatted=True``.
        """
        from pydiggs.diff import _text  # noqa: PLC0415

        return _text.render(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Diff:
        """Build a diff from a diggs-diff document.

        Raises:
            ValueError: ``data`` is not a diggs-diff 1.x document.
        """
        from pydiggs.diff import _document  # noqa: PLC0415

        return _document.from_document(data)
