"""Properties 4 and 5 of the 1.12.0 brief: identity, symmetry, and exactly the edit.

Models come from the corpus files; each hypothesis example rebuilds one model with one
edit, so the expected change is known without running the code under test.
"""

from __future__ import annotations

import dataclasses
import math
from collections import Counter
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from pydiggs import convert, diff
from pydiggs.errors import PydiggsError
from pydiggs.model import Feature, Measurement, Model, Other, Project, Quantity, Sample

from ._helpers import (
    AGS4,
    bump,
    collection_of,
    corpus_paths,
    drop_node,
    other_unit,
    read_corpus,
    referenced_keys,
    renamed,
    replace_in,
    small_models,
    subtree,
    with_param,
    with_row,
)

SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

_PATHS = [p for p in corpus_paths() if read_corpus(p) is not None]
_IDS = [str(p.relative_to(p.parents[2])) for p in _PATHS]


# --- 4. identity and symmetry --------------------------------------------------------


@pytest.mark.parametrize("path", _PATHS, ids=_IDS)
def test_every_corpus_file_is_identical_to_itself(path):
    model = read_corpus(path)
    assert model is not None
    for result in (diff.compare(model, model), diff.compare(path, path)):
        assert result.identical, [(c.collection, c.old_key) for c in result.changes[:3]]
        assert result.complete
        assert result.changes == ()
        assert result.notes == ()
        assert result.value_changes == 0


def _convertible() -> list[Any]:
    out = []
    for path in sorted(AGS4.glob("*.ags")):
        try:
            xml = convert.ags4_to_diggs(path, validate=False).xml
        except (PydiggsError, ValueError):
            continue
        if xml:
            out.append(pytest.param(xml, id=path.stem))
    return out


@pytest.mark.parametrize("xml", _convertible())
def test_every_converter_output_is_identical_to_itself(xml):
    result = diff.compare(xml, xml)
    assert result.identical, [(c.collection, c.old_key) for c in result.changes[:3]]
    assert result.notes == ()


def test_the_corpus_and_converter_cases_are_not_empty():
    assert len(_PATHS) >= 30
    assert len(_convertible()) >= 3


_SWAP = {"added": "removed", "removed": "added"}


def canon(result: diff.Diff, *, inverse: bool = False) -> tuple[Any, ...]:
    """Everything a diff says, in an order-free form; ``inverse`` reads it the other way round."""
    rows = []
    for c in result.changes:
        old_key, new_key, op = c.old_key, c.new_key, c.op
        if inverse:
            old_key, new_key, op = new_key, old_key, _SWAP.get(op, op)
        vals = []
        for v in c.values:
            vop, old, new = v.op, v.old, v.new
            if inverse:
                vop, old, new = _SWAP.get(vop, vop), new, old
            vals.append((vop, v.path, repr(old), repr(new)))
        rows.append((c.collection, op, old_key, new_key, c.matched_by, tuple(sorted(vals))))
    summary = {
        name: ({**s, "added": s["removed"], "removed": s["added"]} if inverse else dict(s))
        for name, s in result.summary.items()
    }
    return (
        sorted(rows, key=repr),
        summary,
        result.identical,
        result.complete,
        result.value_changes,
        result.reformatted,
        sorted(n.rule_id for n in result.notes),
    )


def _edit(data: st.DataObject, model: Model) -> tuple[Model, str]:
    """A copy of ``model`` with one edit of a random kind (the kind's name is returned)."""
    kind = data.draw(
        st.sampled_from(["value", "unit", "drop", "cell", "row", "column", "rename"]), label="kind"
    )
    tops = [n for n in model.nodes() if n.gml_id is not None and n.key == n.gml_id]
    if kind == "rename":
        return renamed(model), kind
    if kind in ("value", "unit"):
        want = (lambda v: isinstance(v, Quantity) and v.uom) if kind == "unit" else (lambda _: True)
        found = [
            (n, name)
            for n in model.nodes()
            if isinstance(n, Feature | Sample | Measurement | Project)
            for name, v in n.params.items()
            if want(v)
        ]
        assume(found)
        node, name = data.draw(st.sampled_from(found), label="param")
        old = node.params[name]
        new = other_unit(old) if kind == "unit" else bump(old)
        return replace_in(model, node, with_param(node, name, new)), kind
    if kind == "drop":
        loose = [
            n
            for n in tops
            if collection_of(n) not in ("components", "layers", "document")
            and n.key not in referenced_keys(model)
            and all(s.key not in referenced_keys(model) for s in subtree(n)[1:])
        ]
        assume(loose)
        return drop_node(model, data.draw(st.sampled_from(loose), label="node")), kind
    holders = [
        (m, i)
        for m in (*model.measurements, *model.others)
        for i, t in enumerate(m.results)
        if t.columns and t.row_count
    ]
    assume(holders)
    holder, i = data.draw(st.sampled_from(holders), label="table")
    tab = holder.results[i]
    if kind == "column":
        ids = Counter(c.key or c.name for c in tab.columns)
        k = data.draw(st.integers(0, len(tab.columns) - 1), label="column")
        assume(ids[tab.columns[k].key or tab.columns[k].name] == 1)
        tab = dataclasses.replace(tab, columns=tab.columns[:k] + tab.columns[k + 1 :])
    elif kind == "cell":
        numeric = [
            (j, r)
            for j, c in enumerate(tab.columns)
            for r, v in enumerate(c.values)
            if isinstance(v, float) and not isinstance(v, bool) and math.isfinite(v)
        ]
        assume(numeric)
        j, r = data.draw(st.sampled_from(numeric), label="cell")
        col = tab.columns[j]
        values = list(col.values)
        values[r] = values[r] + 1.0 + abs(values[r])
        cols = list(tab.columns)
        cols[j] = dataclasses.replace(col, values=tuple(values))
        tab = dataclasses.replace(tab, columns=tuple(cols))
    else:  # row
        index_row: tuple[Any, ...] = ()
        if tab.domain.index:
            tops_ = [c.values for c in tab.domain.index]
            assume(all(v and all(type(x) is float for x in v) for v in tops_))
            index_row = tuple(max(v) + 1000.0 for v in tops_)
        tab = with_row(tab, tuple(7.5 for _ in tab.columns), index_row)
    return _put_table(model, holder, i, tab), kind


def _put_table(model: Model, holder: Measurement | Other, i: int, tab: Any) -> Model:
    results = tuple(tab if j == i else t for j, t in enumerate(holder.results))
    return replace_in(model, holder, dataclasses.replace(holder, results=results))


@SETTINGS
@given(st.data())
def test_compare_a_to_b_is_the_inverse_of_b_to_a(data):
    model = data.draw(st.sampled_from(small_models()), label="model")
    edited, _ = _edit(data, model)
    forward = diff.compare(model, edited)
    backward = diff.compare(edited, model)
    assert canon(forward) == canon(backward, inverse=True)
    assert forward.identical is backward.identical


# --- 5. exactly the edit -------------------------------------------------------------


def _check_rename(model: Model, edited: Model, result: diff.Diff) -> None:
    """A renamed id is not a difference: nothing is listed unless a key was ambiguous."""
    ambiguous = any(n.rule_id == "diff.ambiguous-match" for n in result.notes)
    renamed_nodes = [n for n in model.nodes() if n.gml_id is not None and n.key == n.gml_id]
    assert {c.op for c in result.changes} <= {"added", "removed"}
    assert result.value_changes == 0
    assert all(r["changed"] == 0 for r in result.summary.values())
    assert result.identical is (not result.changes)
    if result.changes:
        assert ambiguous, "an unmatched node needs an ambiguity note"
    if not ambiguous:
        assert result.identical is True
        assert result.renamed == len(renamed_nodes)
    shown = diff.compare(model, edited, show_renamed=True)
    assert (shown.identical, shown.renamed, shown.summary) == (
        result.identical,
        result.renamed,
        result.summary,
    )
    listed_renames = [c for c in shown.changes if c.op == "renamed"]
    assert len(listed_renames) == result.renamed
    for c in listed_renames:
        assert [(v.op, v.path) for v in c.values] == [("renamed", "gml_id")]
        assert c.matched_by == "natural" and c.old_key != c.new_key
    by_id = diff.compare(model, edited, match="gml-id")
    assert {c.op for c in by_id.changes} == {"added", "removed"} or not renamed_nodes
    assert by_id.renamed == 0


@SETTINGS
@given(st.data())
def test_a_single_edit_gives_exactly_that_change(data):
    model = data.draw(st.sampled_from(small_models()), label="model")
    edited, kind = _edit(data, model)
    result = diff.compare(model, edited)
    listed = [(c.collection, c.op, c.old_key, c.new_key, c.matched_by) for c in result.changes]
    paths = [v.path for c in result.changes for v in c.values]
    if kind in ("value", "unit"):
        assert len(result.changes) == 1 and result.changes[0].op == "changed"
        assert len(paths) == 1 and paths[0].startswith("params.")
        if kind == "unit":
            old, new = result.changes[0].values[0].old, result.changes[0].values[0].new
            assert old != new and old.split()[0] == new.split()[0]
    elif kind == "drop":
        before = {n.key: collection_of(n) for n in model.nodes()}
        after = {n.key for n in edited.nodes()}
        gone = {(coll, "removed", key) for key, coll in before.items() if key not in after}
        assert {(c, o, ok) for c, o, ok, _, _ in listed} == gone
        assert paths == []
        # and the other way round it is an addition of exactly the same nodes
        again = diff.compare(edited, model)
        assert {(c.collection, c.op, c.new_key) for c in again.changes} == {
            (c, "added", k) for c, _, k in gone
        }
    elif kind == "rename":
        _check_rename(model, edited, result)
    elif kind == "cell":
        assert len(result.changes) == 1 and len(paths) == 1
        (value,) = result.changes[0].values
        assert value.op == "changed" and ".cells[" in value.path
        assert isinstance(value.old, float) and isinstance(value.new, float)
    elif kind == "row":
        (change,) = result.changes
        assert {v.op for v in change.values} == {"added"}
        assert all(".cells[" in p for p in paths)
        # one added cell per column of that table, index columns included
        per_column = Counter(p.rsplit(".cells[", 1)[0] for p in paths)
        assert set(per_column.values()) == {1}
    else:  # column
        (change,) = result.changes
        (value,) = change.values
        assert value.op == "removed" and value.path.count(".columns[") == 1
        assert not value.path.endswith("]]")
    if kind != "rename":
        assert result.identical is False
