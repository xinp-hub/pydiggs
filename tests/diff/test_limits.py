"""Property 6: bounded and linear. Limits, hostile models and the scaling counter."""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from pydiggs import diff
from pydiggs.diff import _engine
from pydiggs.model import ExtraNode, Position

from ._helpers import (
    column,
    make_feature,
    make_model,
    make_sample,
    make_test,
    schema_errors,
    table,
)


def _features(n: int, *, tag: str = "B", suffix: str = "", **fields):
    return tuple(
        make_feature(f"f{i}", f"{tag}-{i}", at=i + 1, params={"v": f"{i}{suffix}"}, **fields)
        for i in range(n)
    )


# --- max_changes ---------------------------------------------------------------------


def test_max_changes_bounds_the_list_keeps_exact_counts_and_says_so():
    old = make_model(features=_features(50))
    new = make_model(features=_features(50, suffix="x"))
    result = diff.compare(old, new, max_changes=10)
    listed = len(result.changes) + sum(len(c.values) for c in result.changes)
    assert listed <= 10
    assert result.complete is False
    assert result.limits_hit == ("max_changes",)
    assert result.identical is False
    assert result.summary["features"] == {"added": 0, "removed": 0, "changed": 50, "unchanged": 0}
    assert result.value_changes == 50
    (note,) = result.notes
    assert (note.step.value, note.rule_id) == ("diff", "diff.limit")
    assert "max_changes" in note.message and "10" in note.message
    assert schema_errors(result.to_dict()) == []


def test_the_list_that_max_changes_keeps_is_the_first_in_collection_key_order():
    old = make_model(features=_features(20))
    new = make_model(features=_features(20, suffix="x"))
    full = diff.compare(old, new)
    cut = diff.compare(old, new, max_changes=7)
    kept = [(c.new_key, [v.path for v in c.values]) for c in cut.changes]
    assert kept == [(c.new_key, [v.path for v in c.values]) for c in full.changes][: len(kept)]


def test_max_changes_zero_lists_nothing_but_counts_everything():
    old = make_model(features=_features(5))
    new = make_model(features=_features(5, suffix="x"))
    result = diff.compare(old, new, max_changes=0)
    assert result.changes == ()
    assert result.summary["features"]["changed"] == 5
    assert result.complete is False and result.identical is False


def test_exactly_max_changes_is_not_a_limit():
    old = make_model(features=_features(3))
    new = make_model(features=_features(3, suffix="x"))
    full = diff.compare(old, new)
    listed = len(full.changes) + sum(len(c.values) for c in full.changes)
    exact = diff.compare(old, new, max_changes=listed)
    assert exact.complete is True and exact.notes == ()
    assert (exact.changes, exact.summary) == (full.changes, full.summary)
    assert diff.compare(old, new, max_changes=listed - 1).complete is False


def test_identical_models_with_max_changes_zero_stay_identical():
    model = make_model(features=_features(3))
    result = diff.compare(model, model, max_changes=0)
    assert result.identical and result.complete and result.notes == ()


# --- time_budget ---------------------------------------------------------------------


class FakeClock:
    """A clock that advances one second each time it is read."""

    def __init__(self) -> None:
        self.ticks = itertools.count()

    def __call__(self) -> float:
        return float(next(self.ticks))


def test_time_budget_stops_the_comparison_and_says_it_is_incomplete(monkeypatch):
    monkeypatch.setattr(_engine, "_now", FakeClock())
    old = make_model(features=_features(400))
    new = make_model(features=_features(400, suffix="x"))
    result = diff.compare(old, new, time_budget=5)
    assert result.complete is False
    assert result.limits_hit == ("time_budget",)
    assert result.identical is False
    (note,) = result.notes
    assert (note.step.value, note.rule_id) == ("diff", "diff.timeout")
    assert result.summary["features"]["changed"] < 400
    assert result.options["time_budget"] == 5
    assert schema_errors(result.to_dict()) == []


def test_an_ample_time_budget_changes_nothing():
    old = make_model(features=_features(5))
    new = make_model(features=_features(5, suffix="x"))
    full, timed = diff.compare(old, new), diff.compare(old, new, time_budget=3600)
    assert timed.complete is True and timed.notes == ()
    assert (timed.changes, timed.summary) == (full.changes, full.summary)


def test_a_timed_out_comparison_of_identical_models_cannot_claim_identity(monkeypatch):
    monkeypatch.setattr(_engine, "_now", FakeClock())
    model = make_model(features=_features(400))
    result = diff.compare(model, model, time_budget=5)
    assert result.identical is False and result.complete is False


def test_a_file_that_was_read_in_part_makes_the_diff_incomplete():
    model = make_model(features=_features(3))
    partial = dataclasses.replace(model, complete=False, limits_hit=("read_limit",))
    result = diff.compare(model, partial)
    assert result.complete is False
    assert result.identical is False
    assert result.limits_hit == ("new_read_incomplete",)
    (note,) = result.notes
    assert note.rule_id == "diff.limit" and "new" in note.message
    timed = dataclasses.replace(model, complete=False, limits_hit=("time_budget",))
    assert diff.compare(timed, model).limits_hit == ("old_read_incomplete",)


# --- hostile models stay bounded -----------------------------------------------------


def test_many_duplicate_natural_keys_are_not_matched_and_the_notes_stay_few():
    n = 5000
    old = make_model(features=tuple(make_feature(f"a{i}", "same", at=i + 1) for i in range(n)))
    new = make_model(features=tuple(make_feature(f"b{i}", "same", at=i + 1) for i in range(n)))
    result = diff.compare(old, new, max_changes=100)
    assert result.summary["features"] == {"added": n, "removed": n, "changed": 0, "unchanged": 0}
    assert 1 <= len(result.notes) <= 51
    assert {x.rule_id for x in result.notes} <= {"diff.ambiguous-match", "diff.limit"}
    assert len(result.to_json()) < 200_000
    assert schema_errors(result.to_dict()) == []


def test_distinct_ambiguous_groups_are_capped_at_fifty_notes_and_one_summary():
    n = 400
    old = make_model(
        features=tuple(
            make_feature(f"a{i}_{j}", f"k{i}", at=1 + 2 * i + j) for i in range(n) for j in range(2)
        )
    )
    new = make_model(
        features=tuple(
            make_feature(f"b{i}_{j}", f"k{i}", at=1 + 2 * i + j) for i in range(n) for j in range(2)
        )
    )
    notes = [
        x
        for x in diff.compare(old, new, max_changes=0).notes
        if x.rule_id == "diff.ambiguous-match"
    ]
    assert len(notes) == 51
    assert "350" in notes[-1].message  # "and 350 more"


def test_huge_names_and_deep_extra_cost_nothing_extra():
    name = "x" * 1_000_000
    deep = ExtraNode(qname="leaf", text="t")
    for _ in range(5000):
        deep = ExtraNode(qname="n", children=(deep,))
    a = make_model(features=(make_feature("f1", name, extra=(deep,)),))
    other = ExtraNode(qname="leaf", text="u")
    for _ in range(5000):
        other = ExtraNode(qname="n", children=(other,))
    b = make_model(features=(make_feature("f1", name, extra=(other,)),))
    assert diff.compare(a, a).identical
    result = diff.compare(a, b)
    assert not result.identical
    paths = [v.path for c in result.changes for v in c.values]
    assert paths and all(len(p) < 20_000 for p in paths)
    assert len(result.to_json()) < 50_000
    assert schema_errors(result.to_dict()) == []


def test_a_column_name_a_million_characters_long_gives_a_short_distinct_path():
    long_a, long_b = "c" * 1_000_000, "c" * 999_999 + "d"
    old = make_model(
        features=(make_feature("f1", "B"),),
        measurements=(
            make_test("t1", "T", "f1", results=(table(column(long_a, 1.0), column(long_b, 1.0)),)),
        ),
    )
    new = make_model(
        features=(make_feature("f1", "B"),),
        measurements=(
            make_test("t1", "T", "f1", results=(table(column(long_a, 1.0), column(long_b, 2.0)),)),
        ),
    )
    result = diff.compare(old, new)
    (value,) = [v for c in result.changes for v in c.values]
    assert len(value.path) < 400
    clash = diff.compare(
        old,
        make_model(
            features=(make_feature("f1", "B"),),
            measurements=(
                make_test(
                    "t1", "T", "f1", results=(table(column(long_a, 2.0), column(long_b, 1.0)),)
                ),
            ),
        ),
    )
    (other,) = [v for c in clash.changes for v in c.values]
    assert other.path != value.path


def test_a_hostile_position_with_a_huge_tuple_is_still_one_bounded_value():
    big = tuple(float(i) for i in range(50_000))
    a = make_model(features=(make_feature("f1", "B", location=Position(coordinates=big, dim=3)),))
    b = make_model(
        features=(make_feature("f1", "B", location=Position(coordinates=(*big[:-1], -1.0), dim=3)),)
    )
    result = diff.compare(a, b)
    (value,) = [v for c in result.changes for v in c.values]
    assert len(value.old) <= 200 and len(value.new) <= 200 and value.truncated
    assert len(result.to_json()) < 10_000


# --- linear work ---------------------------------------------------------------------

LINEAR = 2.5


def _units(old, new) -> int:
    return _engine.compare_models(old, new, _engine.Options()).units


def _world(n: int, *, edit: bool):
    suffix = "x" if edit else ""
    return make_model(
        features=_features(n, suffix=suffix),
        samples=tuple(make_sample(f"s{i}", f"S{i}", f"f{i}", at=n + i + 1) for i in range(n)),
        measurements=tuple(
            make_test(
                f"t{i}",
                f"T{i}",
                f"f{i}",
                at=2 * n + i + 1,
                results=(
                    table(
                        column("a", *[float(j) + (0.5 if edit else 0) for j in range(10)], key="a")
                    ),
                ),
            )
            for i in range(n)
        ),
    )


@pytest.mark.scaling
@pytest.mark.parametrize("n", [200, 1000], ids=["small", "large"])
def test_work_is_linear_in_nodes_plus_cells(n):
    small = _units(_world(n, edit=False), _world(n, edit=True))
    large = _units(_world(2 * n, edit=False), _world(2 * n, edit=True))
    assert small > 0
    assert large <= LINEAR * small, f"{n} -> {2 * n}: work grew {large / small:.2f}x"


@pytest.mark.scaling
@pytest.mark.parametrize("n", [200, 1000], ids=["small", "large"])
def test_work_is_linear_when_every_natural_key_is_ambiguous(n):
    def world(m, prefix):
        return make_model(
            features=tuple(make_feature(f"{prefix}{i}", "same", at=i + 1) for i in range(m))
        )

    small = _units(world(n, "a"), world(n, "b"))
    large = _units(world(2 * n, "a"), world(2 * n, "b"))
    assert large <= LINEAR * small, f"{n} -> {2 * n}: work grew {large / small:.2f}x"


@pytest.mark.scaling
@pytest.mark.parametrize("n", [500, 4000], ids=["small", "large"])
def test_table_work_is_linear_when_rows_are_aligned_by_index(n):
    def world(m, shift):
        index = (column("position", *[i + shift for i in range(m)], key="position"),)
        return make_model(
            features=(make_feature("f1", "B"),),
            measurements=(
                make_test(
                    "t1",
                    "T",
                    "f1",
                    results=(
                        table(column("q", *[float(i) for i in range(m)], key="q"), index=index),
                    ),
                ),
            ),
        )

    small = _units(world(n, 0.0), world(n, 0.5))
    large = _units(world(2 * n, 0.0), world(2 * n, 0.5))
    assert large <= LINEAR * small, f"{n} -> {2 * n}: work grew {large / small:.2f}x"


def test_memory_for_the_changes_is_bounded_by_the_listing():
    n = 3000
    old = make_model(features=_features(n))
    new = make_model(features=_features(n, suffix="x"))
    result = diff.compare(old, new, max_changes=20)
    assert len(result.changes) <= 20
    assert result.value_changes == n
