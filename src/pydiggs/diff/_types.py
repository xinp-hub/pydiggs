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
OPS: Final = ("added", "removed", "changed", "reformatted", "renamed")
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
    differently, and ``renamed`` for the record's own ``gml:id`` (path ``gml_id``, with the
    old and the new id; None on a side that gives the record no id). Neither is a change.
    ``at`` is the XPath of the node in the new file: a value change belongs to a node
    both files hold (a node that was added or removed is listed without values).
    ``truncated`` is True when ``old`` or ``new`` was cut to fit 200 characters (or 200
    list items). ``path`` and ``at`` are at most 1,000 characters: a longer one keeps
    its start and its end, around a digest of the whole.
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
    differs, sorted by path. Two values of ``op`` are for a node that did not change and
    is listed only on request: ``renamed`` for one whose ``gml:id`` differs (listed with
    ``show_renamed=True``; its numbers written differently are listed with it when
    ``show_reformatted=True`` too), and ``reformatted`` for one that only has numbers
    written differently (listed with ``show_reformatted=True``). A changed node whose id
    also differs is ``changed``. ``truncated`` is True when a key or name was cut to 200
    characters.
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
    (``complete``). A number written differently and a record's own ``gml:id`` are not
    differences, so two exports of the same data with new ids are identical.
    ``limits_hit`` names what stopped it or what it could not look at (``max_changes``,
    ``time_budget``, ``old_read_incomplete``, ``new_read_incomplete``,
    ``old_results_not_read``, ``new_results_not_read``); it is empty exactly when
    ``complete`` is True. ``summary`` counts nodes per
    collection (``added``, ``removed``, ``changed``, ``unchanged``) and stays exact when
    ``max_changes`` stops the list, but is partial after a ``time_budget`` stop.
    ``value_changes`` counts every differing value, ``reformatted`` every value written
    differently and ``renamed`` every node matched by its natural key whose ``gml:id``
    differs; the last two are counted whether listed or not. ``changes`` is ordered by
    collection, then key, then path; ``notes`` are findings of step ``diff``.
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
    renamed: int
    changes: tuple[Change, ...]
    notes: tuple[Finding, ...]

    def __post_init__(self) -> None:
        for name in ("tool", "old", "new", "options"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))
        summary = {k: _frozen(v) for k, v in self.summary.items()}
        object.__setattr__(self, "summary", MappingProxyType(summary))

    def to_dict(self) -> dict[str, Any]:
        """The diff as a diggs-diff document (plain dicts and lists)."""
        from pydiggs.diff import _document  # noqa: PLC0415

        return _document.to_document(self)

    def to_json(self, indent: int | None = None) -> str:
        """The diff as diggs-diff JSON: keys sorted, no NaN, the same bytes every time."""
        from pydiggs.diff import _document  # noqa: PLC0415

        return _document.dumps(self, indent=indent)

    def to_text(self) -> str:
        """A human-readable report, safe to print through ``rich`` (file text is escaped).

        It lists what the diff holds: reformatted values appear only if the diff was made
        with ``show_reformatted=True``, and renamed ids only with ``show_renamed=True``.
        Both are always counted.
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
