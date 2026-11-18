"""Result tables: columns matched by key (else name), rows by the domain index (else number).

When a table's domain index (CPT depth, interval top and base) is unique on both sides,
rows are aligned by index value within the tolerance, in whatever order they are written;
otherwise by row number. A row only one side has is an addition or removal of each of its
cells. Index columns are paired by key like data columns, so their order is not data.

A table whose values were not read (a model read with ``results=False``) has columns and
a row count, and no cells to compare: the comparison says which side that was, and its
caller makes the result incomplete. Pure.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, Final

from pydiggs.diff._compare import Side, Tick, Tol, chunks, close, diff_maps, put, same, seg
from pydiggs.model import Column, ResultTable

__all__ = ["compare_results"]

Emit = Callable[[str, str, Any, Any], None]

_NOT_READ: Final = "<not read>"


def _describe_table(table: ResultTable) -> str:
    return f"table: {len(table.columns)} columns, {table.row_count} rows"


def _describe_column(column: Column) -> str:
    unit = f", {column.uom}" if column.uom else ""
    return f"column {column.name} ({column.type}{unit}, {len(column.values)} values)"


def _has_values(table: ResultTable) -> bool:
    columns = (*table.columns, *table.domain.index)
    return bool(columns) and all(len(c.values) == table.row_count for c in columns)


def _unread(table: ResultTable) -> bool:
    """Whether ``table`` has columns and not their values: cells nobody can compare."""
    return bool(table.columns or table.domain.index) and not _has_values(table)


def _paired(columns: Sequence[Column]) -> dict[str, list[Column]]:
    by_id: dict[str, list[Column]] = defaultdict(list)
    for column in columns:
        by_id[column.key or column.name].append(column)
    return by_id


def _label(base: str, ident: str, occurrence: int) -> str:
    return f"{base}[{seg(ident)}{'' if occurrence == 0 else f'#{occurrence + 1}'}]"


def _column_pairs(
    base: str, old: Sequence[Column], new: Sequence[Column]
) -> list[tuple[str, Column | None, Column | None]]:
    """Columns paired by key (else name); same-keyed columns pair in order of appearance."""
    old_by, new_by = _paired(old), _paired(new)
    pairs: list[tuple[str, Column | None, Column | None]] = []
    for ident in sorted(old_by.keys() | new_by.keys()):
        a, b = old_by.get(ident, []), new_by.get(ident, [])
        pairs.extend(
            (_label(base, ident, n), a[n] if n < len(a) else None, b[n] if n < len(b) else None)
            for n in range(max(len(a), len(b)))
        )
    return pairs


def _column_attrs(column: Column) -> dict[str, Any]:
    attrs: dict[str, Any] = {"name": column.name, "type": column.type}
    for name in ("definition", "uom", "null_value"):
        value = getattr(column, name)
        if value is not None:
            attrs[name] = value
    return attrs


def _ranks(
    old: Sequence[Any], new: Sequence[Any], tol: Tol, tick: Tick
) -> tuple[list[int], list[int]] | None:
    """Each value of one index column on both sides as the number of its cluster.

    The values of both sides are sorted together, and a value joins the cluster of the one
    before it when the two are equal within ``tol``. Values equal within ``tol`` are then
    always in one cluster, wherever float noise puts them in the order of their rows. None
    when a value is not a number.
    """
    distinct: set[float] = set()
    for values in (old, new):
        for part in chunks(values, tick):
            for value in part:
                if type(value) is not float:
                    return None
                if not math.isnan(value):
                    distinct.add(value)
    cluster: dict[float, int] = {}
    number, last = 0, None
    for part in chunks(sorted(distinct), tick):
        for value in part:
            if last is not None and not close(last, value, tol):
                number += 1
            cluster[value] = number
            last = value
    # NaN equals only NaN, and has no place in an order: a cluster of its own.
    return [cluster.get(value, -1) for value in old], [cluster.get(value, -1) for value in new]


def _index_pairs(
    old: Sequence[Column], new: Sequence[Column]
) -> tuple[list[Column], list[Column]] | None:
    """The index columns of both sides in one order, each old one with the new one of its key.

    The order is that of the old table, so a row is labelled as its file writes it (top,
    then base). Same-keyed columns pair in order of appearance, as data columns do. None
    when a column of one side has no partner on the other: the two tables are then not
    indexed by the same things.
    """
    if len(old) != len(new):
        return None
    new_by = _paired(new)
    taken: dict[str, int] = defaultdict(int)
    partners: list[Column] = []
    for column in old:
        ident = column.key or column.name
        twins = new_by.get(ident, [])
        if taken[ident] >= len(twins):
            return None
        partners.append(twins[taken[ident]])
        taken[ident] += 1
    return list(old), partners


def _align_by_index(
    old: Sequence[Column],
    new: Sequence[Column],
    rows_old: int,
    rows_new: int,
    *,
    tol: Tol,
    tick: Tick,
) -> tuple[list[tuple[int, int]], list[int], list[int]] | None:
    """Rows paired by index value within ``tol``, or None when the index cannot be used.

    ``old`` and ``new`` are the index columns as :func:`_index_pairs` gives them: the
    same number, each old one with the new one of its key.

    It cannot be used when a side has no index, a value is not a number, or two rows of
    one side could be the same row: the tolerance cannot tell them apart, and pairing
    either would be a guess. Rows are found by the clusters of their values, never by
    their order, so the work stays near linear and noise in one column cannot misalign
    the rest.
    """
    if not old or len(old) != len(new):
        return None
    ranked = []
    for a, b in zip(old, new, strict=True):
        ranks = _ranks(a.values, b.values, tol, tick)
        if ranks is None:
            return None
        ranked.append(ranks)
    keys_old = list(zip(*(ranks[0] for ranks in ranked), strict=True))
    keys_new = list(zip(*(ranks[1] for ranks in ranked), strict=True))
    if len(set(keys_old)) != rows_old:
        return None
    row_of = dict(zip(keys_new, range(rows_new), strict=True))
    if len(row_of) != rows_new:
        return None
    pairs: list[tuple[int, int]] = []
    removed: list[int] = []
    found = bytearray(rows_new)
    for part in chunks(range(rows_old), tick):
        for i in part:
            j = row_of.get(keys_old[i])
            # A cluster chains neighbours, so two of its values can be further apart than
            # the tolerance: the rows are a pair only if their values really are equal.
            if j is not None and all(
                same(a.values[i], b.values[j], tol) is None for a, b in zip(old, new, strict=True)
            ):
                pairs.append((i, j))
                found[j] = 1
            else:
                removed.append(i)
    added = [j for j in range(rows_new) if not found[j]]
    return pairs, removed, added


def _compare_cells(
    path: str,
    old: Column | None,
    new: Column | None,
    *,
    pairs: Sequence[tuple[int, int]],
    removed: Sequence[int],
    added: Sequence[int],
    labels: tuple[Sequence[str], Sequence[str]],
    tol: Tol,
    emit: Emit,
    tick: Tick,
) -> None:
    old_labels, new_labels = labels
    if old is not None and new is not None:
        for both in chunks(pairs, tick):
            for i, j in both:
                a, b = old.values[i], new.values[j]
                if a is not b and same(a, b, tol) is not None:
                    emit(f"{path}.cells[{old_labels[i]}]", "changed", a, b)
    if old is not None:
        for part in chunks(removed, tick):
            for row in part:
                emit(f"{path}.cells[{old_labels[row]}]", "removed", old.values[row], None)
    if new is not None:
        for part in chunks(added, tick):
            for row in part:
                emit(f"{path}.cells[{new_labels[row]}]", "added", None, new.values[row])


def _index_labels(columns: Sequence[Column], rows: int, tick: Tick) -> list[str]:
    labels: list[str] = []
    for part in chunks(range(rows), tick):
        labels.extend("@" + ",".join(repr(c.values[i]) for c in columns) for i in part)
    return labels


def _compare_table(
    base: str,
    old: ResultTable,
    new: ResultTable,
    *,
    sides: tuple[Side, Side],
    tol: Tol,
    emit: Emit,
    tick: Tick,
) -> tuple[bool, bool]:
    """Compare two tables; whether the values of the old, of the new one were not read."""
    head_old: dict[str, Any] = {"domain.shape": old.domain.shape}
    head_new: dict[str, Any] = {"domain.shape": new.domain.shape}
    for head, table, side in ((head_old, old, sides[0]), (head_new, new, sides[1])):
        put(head, "domain.crs", "crs", table.domain.crs, side)
        put(head, "location", "location", table.location, side)
        put(head, "extent", "extent", table.extent, side)

    def emit_head(path: str, op: str, a: Any, b: Any) -> None:
        emit(f"{base}.{path}", op, a, b)

    diff_maps(head_old, head_new, tol, emit_head, tick)

    index_pairs = _column_pairs(f"{base}.domain.index", old.domain.index, new.domain.index)
    data_pairs = _column_pairs(f"{base}.columns", old.columns, new.columns)
    for path, a, b in (*index_pairs, *data_pairs):
        if a is None and b is not None:
            emit(path, "added", None, _describe_column(b))
            continue
        if b is None and a is not None:
            emit(path, "removed", _describe_column(a), None)
            continue
        if a is None or b is None:  # pragma: no cover - a pair has at least one column
            continue
        attrs_a, attrs_b = _column_attrs(a), _column_attrs(b)

        def emit_attr(p: str, op: str, x: Any, y: Any, path: str = path) -> None:
            emit(f"{path}.{p}", op, x, y)

        diff_maps(attrs_a, attrs_b, tol, emit_attr, tick)

    # Only a table with no column of either kind has nothing but its row count to compare:
    # index columns alone (the depths of a table whose data was not kept) are values too.
    if not (old.columns or new.columns or old.domain.index or new.domain.index):
        if old.row_count != new.row_count:
            emit(f"{base}.row_count", "changed", old.row_count, new.row_count)
        return False, False
    unread = (_unread(old), _unread(new))
    if any(unread):
        _compare_unread(base, old, new, emit=emit, tick=tick)
    else:
        _compare_rows(base, old, new, tol=tol, emit=emit, tick=tick)
    return unread


def _compare_unread(
    base: str, old: ResultTable, new: ResultTable, *, emit: Emit, tick: Tick
) -> None:
    """At least one side was read without values: compare what is there.

    That is the row count when neither side has values, and otherwise a column with
    values against the same column without. Equal row counts say nothing about the
    cells, so the caller reports the comparison as incomplete either way.
    """
    if not _has_values(old) and not _has_values(new):
        if old.row_count != new.row_count:
            emit(f"{base}.row_count", "changed", old.row_count, new.row_count)
        return
    for prefix, a_cols, b_cols in (
        ("domain.index", old.domain.index, new.domain.index),
        ("columns", old.columns, new.columns),
    ):
        for path, a, b in _column_pairs(f"{base}.{prefix}", a_cols, b_cols):
            if a is None or b is None:
                continue
            tick(1)
            read_a, read_b = len(a.values) == old.row_count, len(b.values) == new.row_count
            if read_a is not read_b or (read_a and a.values != b.values):
                emit(
                    f"{path}.values",
                    "changed",
                    f"<{len(a.values)} values>" if read_a else _NOT_READ,
                    f"<{len(b.values)} values>" if read_b else _NOT_READ,
                )


def _compare_rows(
    base: str, old: ResultTable, new: ResultTable, *, tol: Tol, emit: Emit, tick: Tick
) -> None:
    rows_old, rows_new = old.row_count, new.row_count
    # The index columns in one order for both sides: paired by key, like data columns,
    # so that ``top, base`` and ``base, top`` index the same rows.
    indexed = _index_pairs(old.domain.index, new.domain.index)
    aligned = (
        None
        if indexed is None
        else _align_by_index(indexed[0], indexed[1], rows_old, rows_new, tol=tol, tick=tick)
    )
    by_index = aligned is not None
    if aligned is None or indexed is None:
        common = min(rows_old, rows_new)
        pairs = [(i, i) for i in range(common)]
        removed = list(range(common, rows_old))
        added = list(range(common, rows_new))
        labels_old = labels_new = [str(i + 1) for i in range(max(rows_old, rows_new))]
    else:
        pairs, removed, added = aligned
        labels_old = _index_labels(indexed[0], rows_old, tick)
        labels_new = _index_labels(indexed[1], rows_new, tick)
    label_pair = (labels_old, labels_new)
    index_pairs = [
        p
        for p in _column_pairs(f"{base}.domain.index", old.domain.index, new.domain.index)
        if p[1] is not None and p[2] is not None
    ]
    data_pairs = [
        p
        for p in _column_pairs(f"{base}.columns", old.columns, new.columns)
        if p[1] is not None and p[2] is not None
    ]
    for path, a, b in data_pairs:
        _compare_cells(
            path,
            a,
            b,
            pairs=pairs,
            removed=removed,
            added=added,
            labels=label_pair,
            tol=tol,
            emit=emit,
            tick=tick,
        )
    for path, a, b in index_pairs:
        # An index that aligned the rows identifies them, so only the cells of rows one side
        # lacks are reported; otherwise the index cells are compared like any others.
        matched = () if by_index else pairs
        _compare_cells(
            path,
            a,
            b,
            pairs=matched,
            removed=removed,
            added=added,
            labels=label_pair,
            tol=tol,
            emit=emit,
            tick=tick,
        )


def compare_results(
    old: Sequence[ResultTable],
    new: Sequence[ResultTable],
    *,
    sides: tuple[Side, Side],
    tol: Tol,
    emit: Emit,
    tick: Tick,
) -> tuple[bool, bool]:
    """Compare two nodes' result tables (``results[i]`` aligned by position).

    Returns whether a table of the old, of the new node that has a table to be compared
    with has columns whose values were not read: the cells of the two were not compared.
    """
    unread = [False, False]
    for i in range(max(len(old), len(new))):
        base = f"results[{i}]"
        if i >= len(old):
            emit(base, "added", None, _describe_table(new[i]))
        elif i >= len(new):
            emit(base, "removed", _describe_table(old[i]), None)
        else:
            one = _compare_table(base, old[i], new[i], sides=sides, tol=tol, emit=emit, tick=tick)
            unread = [was or now for was, now in zip(unread, one, strict=True)]
    return unread[0], unread[1]
