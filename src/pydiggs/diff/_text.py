r"""The human renderer of a diff. File text is untrusted, so it is escaped.

Control characters and format characters (the escape that moves a terminal cursor, the
bidirectional overrides that reorder text) are shown as ``\xNN`` or ``\uNNNN``, and
``rich`` markup in a value is escaped, so the text is safe to print through a ``rich``
console. Pure apart from a lazy import of ``rich.markup``.

``render(diff, markup=False)`` is the same report for a stream that reads no markup (a
terminal written to directly, a file): control and format characters are spelled out in
the same way, and brackets are left as they are, since nothing will read them as markup.
``rich`` is then not imported.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from typing import Any

from pydiggs.diff._types import Change, Diff, ValueChange

__all__ = ["render"]

_HIDDEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


_LATIN1_END = 0x100
_BMP_END = 0x10000


def _spell(code: int) -> str:
    if code < _LATIN1_END:
        return f"\\x{code:02x}"
    if code < _BMP_END:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def _plain(text: str) -> str:
    """``text`` with every control or format character spelled out."""
    out = []
    for ch in text:
        if unicodedata.category(ch) in _HIDDEN:
            out.append(_spell(ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def _esc(text: object) -> str:
    from rich.markup import escape  # noqa: PLC0415  # rich is lazy: pydiggs does not import it

    return escape(_plain(str(text)))


def _esc_plain(text: object) -> str:
    return _plain(str(text))


Esc = Callable[[object], str]


def _shown(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, list):
        return "[" + ", ".join(_shown(v) for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _value_line(value: ValueChange, esc: Esc) -> str:
    cut = " (shortened)" if value.truncated else ""
    if value.op == "added":
        body = f"added {_shown(value.new)}"
    elif value.op == "removed":
        body = f"removed {_shown(value.old)}"
    else:
        what = "reformatted: " if value.op == "reformatted" else ""
        body = f"{what}{_shown(value.old)} -> {_shown(value.new)}"
    return f"    {esc(value.path)}: {esc(body)}{cut}"


def _change_lines(change: Change, esc: Esc) -> list[str]:
    key = change.new_key if change.new_key is not None else change.old_key
    label = esc(change.name) if change.name else ""
    head = f"{change.op} {change.collection} {esc(key)}"
    if label:
        head += f" ({label})"
    if change.op in ("changed", "reformatted") and change.matched_by:
        head += f", matched by {'natural key' if change.matched_by == 'natural' else 'gml:id'}"
        if change.old_key != change.new_key:
            head += f" ({esc(change.old_key)} -> {esc(change.new_key)})"
    return [head, *(_value_line(v, esc) for v in change.values)]


def _source(info: Any, esc: Esc) -> str:
    name = esc(info.get("name") or "(unnamed)")
    return f"{name} (DIGGS {esc(info.get('diggs_version'))})"


def render(diff: Diff, *, markup: bool = True) -> str:
    """The diff as text, one line per change and one per value, ending with a newline.

    ``markup=True`` (what :meth:`Diff.to_text` gives) also escapes ``rich`` markup;
    ``markup=False`` is for a stream that reads none.
    """
    esc: Esc = _esc if markup else _esc_plain
    lines = [f"Compared {_source(diff.old, esc)} with {_source(diff.new, esc)}"]
    if diff.identical:
        lines.append("The two files are identical.")
    elif (
        diff.changes
        or diff.value_changes
        or any(s["added"] or s["removed"] or s["changed"] for s in diff.summary.values())
    ):
        lines.append("The two files differ.")
    if not diff.complete:
        lines.append(f"The comparison is incomplete (stopped by {', '.join(diff.limits_hit)}).")
    for name, counts in diff.summary.items():
        lines.append(
            f"{name}: {counts['added']} added, {counts['removed']} removed,"
            f" {counts['changed']} changed, {counts['unchanged']} unchanged"
        )
    if diff.reformatted:
        shown = any(
            c.op == "reformatted" or any(v.op == "reformatted" for v in c.values)
            for c in diff.changes
        )
        lines.append(
            f"{diff.reformatted} value(s) were only reformatted (written differently, same"
            f" value){'' if shown else '; not listed'}."
        )
    for change in diff.changes:
        lines.extend(_change_lines(change, esc))
    lines.extend(f"note {esc(note.rule_id)}: {esc(note.message)}" for note in diff.notes)
    return "\n".join(lines) + "\n"
