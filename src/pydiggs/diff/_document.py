"""The diggs-diff 1.0 JSON codec: a :class:`~pydiggs.diff.Diff` to a document and back.

Pure: no I/O. Keys are written sorted and NaN is refused, so one diff always gives the
same bytes. The JSON Schema ships next to this package as ``diggs-diff-1.0.schema.json``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final

from pydiggs.diff._types import Change, Diff, ValueChange
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
DIFF_SCHEMA_VERSION: Final = "1.0"
DIFF_SCHEMA_URL: Final = "https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json"
SCHEMA_RESOURCE: Final = "diggs-diff-1.0.schema.json"

_VERSION: Final = re.compile(r"1\.[0-9]+")
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
    """The diff as a diggs-diff 1.0 document."""
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


def _decode_value(data: Any, where: str) -> ValueChange:
    if not isinstance(data, Mapping):
        raise ValueError(f"{where}: a value change is an object")
    return ValueChange(
        op=_need(data, "op", str, where),
        path=_need(data, "path", str, where),
        old=_copy(data.get("old")),
        new=_copy(data.get("new")),
        at=data.get("at"),
        truncated=bool(data.get("truncated", False)),
    )


def _decode_change(data: Any, where: str) -> Change:
    if not isinstance(data, Mapping):
        raise ValueError(f"{where}: a change is an object")
    values = _need(data, "values", list, where)
    return Change(
        op=_need(data, "op", str, where),
        collection=_need(data, "collection", str, where),
        element=_need(data, "element", str, where),
        name=data.get("name"),
        old_key=data.get("old_key"),
        new_key=data.get("new_key"),
        matched_by=data.get("matched_by"),
        values=tuple(_decode_value(v, f"{where}.values[{i}]") for i, v in enumerate(values)),
        truncated=bool(data.get("truncated", False)),
    )


def from_document(data: Mapping[str, Any]) -> Diff:
    """Build a :class:`~pydiggs.diff.Diff` from a diggs-diff document.

    Raises:
        ValueError: ``data`` is not a diggs-diff 1.x document.
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
    changes = _need(data, "changes", list, "$")
    notes = _need(data, "notes", list, "$")
    return Diff(
        tool=_copy(_need(data, "tool", Mapping, "$")),
        identical=_need(data, "identical", bool, "$"),
        complete=_need(data, "complete", bool, "$"),
        limits_hit=tuple(_copy(_need(data, "limits_hit", list, "$"))),
        old=_copy(_need(data, "old", Mapping, "$")),
        new=_copy(_need(data, "new", Mapping, "$")),
        options=_copy(_need(data, "options", Mapping, "$")),
        summary=_copy(_need(data, "summary", Mapping, "$")),
        value_changes=_need(data, "value_changes", int, "$"),
        reformatted=_need(data, "reformatted", int, "$"),
        renamed=_need(data, "renamed", int, "$"),
        changes=tuple(_decode_change(c, f"$.changes[{i}]") for i, c in enumerate(changes)),
        notes=tuple(_finding(n, f"$.notes[{i}]") for i, n in enumerate(notes)),
    )
