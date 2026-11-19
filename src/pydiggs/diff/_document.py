"""The diggs-diff JSON codec: a :class:`~pydiggs.diff.Diff` to a document and back.

The format is diggs-diff 1.0, frozen in pyDIGGS 1.13: a 1.0 reader accepts any 1.x
document, a new optional field is a minor version, and anything else is a new major.
:func:`from_document` also reads the 1.0-beta.1 documents pyDIGGS 1.12 wrote.

Pure: no I/O. Keys are written sorted and NaN is refused, so one diff always gives the
same bytes. The JSON Schema ships next to this package as ``diggs-diff-1.0.schema.json``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final

from pydiggs.diff._compare import MAX_PATH
from pydiggs.diff._types import MATCHED_BY, OPS, Change, Diff, ValueChange
from pydiggs.findings._json import finding_entry
from pydiggs.model._model import _finding

__all__ = [
    "DIFF_SCHEMA_URL",
    "DIFF_SCHEMA_VERSION",
    "FORMAT",
    "SCHEMA_RESOURCE",
    "dumps",
    "from_document",
    "to_document",
]

FORMAT: Final = "diggs-diff"
#: Frozen in pyDIGGS 1.13 (pyDIGGS 1.12 wrote ``1.0-beta.1``, which is still read).
DIFF_SCHEMA_VERSION: Final = "1.0"
DIFF_SCHEMA_URL: Final = "https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json"
SCHEMA_RESOURCE: Final = "diggs-diff-1.0.schema.json"

_VERSION: Final = re.compile(r"1\.[0-9]+(-beta\.[0-9]+)?")
_REQUIRED: Final = (
    "$schema",
    "format",
    "schema_version",
    "tool",
    "identical",
    "complete",
    "limits_hit",
    "old",
    "new",
    "options",
    "summary",
    "value_changes",
    "reformatted",
    "renamed",
    "changes",
    "notes",
)


def _copy(value: Any) -> Any:
    """A JSON value sharing no list or object with ``value``, copied without recursion.

    A diff is frozen, and a document is the caller's to change: neither may reach the
    other through a list they both hold.
    """
    root: list[Any] = [None]
    stack: list[tuple[Any, Any, Any]] = [(value, root, 0)]
    while stack:
        item, holder, key = stack.pop()
        if isinstance(item, Mapping):
            made: Any = dict.fromkeys(item)
            stack.extend((child, made, name) for name, child in item.items())
        elif isinstance(item, list | tuple):
            made = [None] * len(item)
            stack.extend((child, made, i) for i, child in enumerate(item))
        else:
            made = item
        holder[key] = made
    return root[0]


def _value(value: ValueChange) -> dict[str, Any]:
    return {
        "op": value.op,
        "path": value.path,
        "old": _copy(value.old),
        "new": _copy(value.new),
        "at": value.at,
        "truncated": value.truncated,
    }


def _change(change: Change) -> dict[str, Any]:
    return {
        "op": change.op,
        "collection": change.collection,
        "element": change.element,
        "name": change.name,
        "old_key": change.old_key,
        "new_key": change.new_key,
        "matched_by": change.matched_by,
        "truncated": change.truncated,
        "values": [_value(v) for v in change.values],
    }


def to_document(diff: Diff) -> dict[str, Any]:
    """The diff as a diggs-diff document."""
    return {
        "$schema": DIFF_SCHEMA_URL,
        "format": FORMAT,
        "schema_version": DIFF_SCHEMA_VERSION,
        "tool": _copy(diff.tool),
        "identical": diff.identical,
        "complete": diff.complete,
        "limits_hit": _copy(diff.limits_hit),
        "old": _copy(diff.old),
        "new": _copy(diff.new),
        "options": _copy(diff.options),
        "summary": _copy(diff.summary),
        "value_changes": diff.value_changes,
        "reformatted": diff.reformatted,
        "renamed": diff.renamed,
        "changes": [_change(c) for c in diff.changes],
        "notes": [finding_entry(n) for n in diff.notes],
    }


def dumps(diff: Diff, *, indent: int | None = None) -> str:
    """The document as JSON text: sorted keys, non-ASCII kept, NaN refused."""
    return json.dumps(
        to_document(diff), indent=indent, ensure_ascii=False, allow_nan=False, sort_keys=True
    )


def _need(data: Mapping[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ValueError(f"{where}.{key}: expected {getattr(kind, '__name__', 'a value')}")
    return value


def _count(data: Mapping[str, Any], key: str, where: str) -> int:
    """A count: an integer, 0 or more (``True`` is not one)."""
    value = _need(data, key, int, where)
    if value < 0:
        raise ValueError(f"{where}.{key}: expected 0 or more, got {value}")
    return int(value)


def _one_of(data: Mapping[str, Any], key: str, allowed: tuple[Any, ...], where: str) -> Any:
    value = data.get(key)
    # ``True == 1`` and unhashable values: a member is the same value of the same type.
    if not any(type(value) is type(item) and value == item for item in allowed):
        names = ", ".join("null" if item is None else str(item) for item in allowed)
        raise ValueError(f"{where}.{key}: expected one of {names}, got {value!r}")
    return value


def _text(data: Mapping[str, Any], key: str, where: str, *, longest: int | None = None) -> Any:
    """A field that is text or null, at most ``longest`` characters."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{where}.{key}: expected text or null")
    if value is not None and longest is not None and len(value) > longest:
        raise ValueError(f"{where}.{key}: longer than {longest} characters")
    return value


def _decode_value(data: Any, where: str) -> ValueChange:
    if not isinstance(data, Mapping):
        raise ValueError(f"{where}: a value change is an object")
    path = _need(data, "path", str, where)
    if not 0 < len(path) <= MAX_PATH:
        raise ValueError(f"{where}.path: expected 1 to {MAX_PATH} characters")
    return ValueChange(
        op=_one_of(data, "op", OPS, where),
        path=path,
        old=_copy(data.get("old")),
        new=_copy(data.get("new")),
        at=_text(data, "at", where, longest=MAX_PATH),
        truncated=bool(data.get("truncated", False)),
    )


def _decode_change(data: Any, where: str) -> Change:
    if not isinstance(data, Mapping):
        raise ValueError(f"{where}: a change is an object")
    values = _need(data, "values", list, where)
    return Change(
        op=_one_of(data, "op", OPS, where),
        collection=_need(data, "collection", str, where),
        element=_need(data, "element", str, where),
        name=_text(data, "name", where),
        old_key=_text(data, "old_key", where),
        new_key=_text(data, "new_key", where),
        matched_by=_one_of(data, "matched_by", (*MATCHED_BY, None), where),
        values=tuple(_decode_value(v, f"{where}.values[{i}]") for i, v in enumerate(values)),
        truncated=bool(data.get("truncated", False)),
    )


_COUNTS: Final = ("added", "removed", "changed", "unchanged")


def _decode_summary(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The counts per collection: each entry has the four counts, whatever else it has."""
    summary: dict[str, dict[str, Any]] = {}
    for name, counts in data.items():
        where = f"$.summary.{name}"
        if not isinstance(name, str) or not isinstance(counts, Mapping):
            raise ValueError(f"{where}: expected an object of counts")
        for key in _COUNTS:
            _count(counts, key, where)
        summary[name] = _copy(counts)
    return summary


def _decode_limits(data: Mapping[str, Any], *, identical: bool, complete: bool) -> tuple[str, ...]:
    """``limits_hit``, checked against what ``identical`` and ``complete`` say."""
    limits = _need(data, "limits_hit", list, "$")
    if not all(isinstance(item, str) for item in limits):
        raise ValueError("$.limits_hit: expected a list of text")
    if complete is bool(limits):
        raise ValueError(
            "$.limits_hit: a complete comparison names no limit, and an incomplete one"
            " names what stopped it"
        )
    if identical and not complete:
        raise ValueError("$.identical: an incomplete comparison cannot say identical")
    return tuple(limits)


def _differs(summary: Mapping[str, Mapping[str, Any]], changes: tuple[Change, ...]) -> bool:
    """Whether a document counts or lists a difference."""
    counted = any(counts[key] for counts in summary.values() for key in _COUNTS[:3])
    return counted or any(change.op in OPS[:3] for change in changes)


def from_document(data: Mapping[str, Any]) -> Diff:
    """Build a :class:`~pydiggs.diff.Diff` from a diggs-diff document.

    A document is checked for what the report needs and for what it says about itself:
    each summary entry has its four counts, every count is an integer of 0 or more,
    ``op`` and ``matched_by`` are values the format defines, ``limits_hit`` is text and
    is empty exactly when ``complete`` is true, and ``identical`` is not true for an
    incomplete comparison or one that counts or lists a difference.

    Raises:
        ValueError: ``data`` is not a diggs-diff 1.x document, or contradicts itself.
    """
    if not isinstance(data, Mapping):
        raise ValueError("a diggs-diff document is a JSON object")
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise ValueError(f"not a diggs-diff document: missing {', '.join(missing)}")
    if data["format"] != FORMAT:
        raise ValueError(f"not a diggs-diff document: format is {data['format']!r}")
    _need(data, "$schema", str, "$")
    version = data["schema_version"]
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ValueError(f"unsupported diggs-diff schema_version {version!r}")
    listed = _need(data, "changes", list, "$")
    notes = _need(data, "notes", list, "$")
    identical = _need(data, "identical", bool, "$")
    complete = _need(data, "complete", bool, "$")
    limits = _decode_limits(data, identical=identical, complete=complete)
    summary = _decode_summary(_need(data, "summary", Mapping, "$"))
    changes = tuple(_decode_change(c, f"$.changes[{i}]") for i, c in enumerate(listed))
    value_changes = _count(data, "value_changes", "$")
    if identical and (value_changes or _differs(summary, changes)):
        raise ValueError("$.identical: a comparison that counts or lists a difference is not")
    return Diff(
        tool=_copy(_need(data, "tool", Mapping, "$")),
        identical=identical,
        complete=complete,
        limits_hit=limits,
        old=_copy(_need(data, "old", Mapping, "$")),
        new=_copy(_need(data, "new", Mapping, "$")),
        options=_copy(_need(data, "options", Mapping, "$")),
        summary=summary,
        value_changes=value_changes,
        reformatted=_count(data, "reformatted", "$"),
        renamed=_count(data, "renamed", "$"),
        changes=changes,
        notes=tuple(_finding(n, f"$.notes[{i}]") for i, n in enumerate(notes)),
    )
