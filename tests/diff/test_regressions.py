"""Regressions from the review of ``pydiggs.diff``: one section per finding.

Each test failed before its fix. Together they pin the two promises a reader relies on: a
real difference is never reported as ``identical``, and document order and ``gml:id``
renames are never reported as differences.
"""

from __future__ import annotations

import copy
import dataclasses
from typing import Any

import pytest

from pydiggs import diff
from pydiggs.diff import _compare, _engine
from pydiggs.model import (
    Column,
    CrsRef,
    Domain,
    ExtraNode,
    Interval,
    LithologyLayer,
    Observation,
    Position,
    Procedure,
    ResultTable,
)

from ._helpers import (
    _header,
    column,
    depth,
    make_component,
    make_feature,
    make_model,
    make_sample,
    make_test,
    schema_errors,
    table,
)
from .test_limits import FakeClock


def paths(result: diff.Diff) -> list[tuple[str, str, str]]:
    return [(c.collection, v.op, v.path) for c in result.changes for v in c.values]


def only_ids_differ(result: diff.Diff) -> bool:
    """Whether a rename shows as the ``gml_id`` of the renamed nodes and nothing else."""
    return {v.path for c in result.changes for v in c.values} == {"gml_id"}


# --- 1. local coordinate systems are compared by whose they are ------------------------


def _local(name: str) -> CrsRef:
    return CrsRef(srs_name=name, form="local", unit="ft", known=True, local=name[1:])


def _two_boreholes(
    *, sample_lrs: str, table_crs: str, ids: tuple[str, str] = ("f1", "f2"), lrs=("#l1", "#l2")
):
    by = dict(zip(("#l1", "#l2"), lrs, strict=True))
    index = (column("position", 1.0, 2.0, key="position", uom="ft"),)
    tab = ResultTable(
        domain=Domain(shape="multipoint", index=index, crs=by[table_crs]),
        columns=(column("q", 10.0, 20.0, key="q"),),
        row_count=2,
    )
    interval = Interval(top=depth("1"), base=depth("2"), lrs=by[sample_lrs])
    return make_model(
        features=(
            make_feature(ids[0], "B-1", lrs=lrs[0]),
            make_feature(ids[1], "B-2", at=2, lrs=lrs[1]),
        ),
        samples=(make_sample("s1", "S", ids[0], interval=interval),),
        measurements=(make_test("t1", "T", ids[0], results=(tab,)),),
        crs={name: _local(name) for name in lrs},
    )


def test_an_interval_moved_to_another_boreholes_depth_system_is_a_change():
    base = _two_boreholes(sample_lrs="#l1", table_crs="#l1")
    result = diff.compare(base, _two_boreholes(sample_lrs="#l2", table_crs="#l1"))
    assert result.identical is False
    assert paths(result) == [("samples", "changed", "interval.lrs")]
    (value,) = [v for c in result.changes for v in c.values]
    assert (value.old, value.new) == ("#l1", "#l2")


def test_a_table_moved_to_another_boreholes_depth_system_is_a_change():
    base = _two_boreholes(sample_lrs="#l1", table_crs="#l1")
    result = diff.compare(base, _two_boreholes(sample_lrs="#l1", table_crs="#l2"))
    assert paths(result) == [("measurements", "changed", "results[0].domain.crs")]


def test_renaming_a_depth_system_or_its_borehole_is_not_a_change():
    base = _two_boreholes(sample_lrs="#l1", table_crs="#l1")
    systems = _two_boreholes(sample_lrs="#l1", table_crs="#l1", lrs=("#m1", "#m2"))
    assert diff.compare(base, systems).identical
    both = _two_boreholes(sample_lrs="#l1", table_crs="#l1", lrs=("#m1", "#m2"), ids=("g1", "g2"))
    assert only_ids_differ(diff.compare(base, both))


def test_a_depth_system_is_owned_by_the_node_whose_extra_defines_it():
    def model(lrs: str):
        def system(gml_id: str) -> tuple[ExtraNode, ...]:
            inner = ExtraNode(qname="LinearSpatialReferenceSystem", attrs={"gml:id": gml_id})
            return (ExtraNode(qname="linearReferencing", children=(inner,)),)

        return make_model(
            features=(
                make_feature("f1", "B-1", extra=system("l1")),
                make_feature("f2", "B-2", at=2, extra=system("l2")),
            ),
            samples=(make_sample("s1", "S", "f1", interval=Interval(top=depth("1"), lrs=lrs)),),
            crs={name: _local(name) for name in ("#l1", "#l2")},
        )

    assert diff.compare(model("#l1"), model("#l1")).identical
    assert paths(diff.compare(model("#l1"), model("#l2"))) == [
        ("samples", "changed", "interval.lrs")
    ]


def test_a_local_system_nothing_defines_is_compared_by_its_name():
    def model(lrs: str):
        ref = CrsRef(srs_name=lrs, form="local", known=False, local=lrs[1:])
        interval = Interval(top=depth("1"), lrs=lrs)
        return make_model(
            features=(make_feature("f1", "B-1"),),
            samples=(make_sample("s1", "S", "f1", interval=interval),),
            crs={lrs: ref},
        )

    assert diff.compare(model("#gone"), model("#gone")).identical
    assert paths(diff.compare(model("#gone"), model("#lost"))) == [
        ("samples", "changed", "interval.lrs")
    ]


# --- 2. an element with nothing in it is still there ------------------------------------


def _wrapped(*children: ExtraNode):
    wrap = ExtraNode(qname="x:wrap", ns="urn:x", children=children)
    return make_model(features=(make_feature("f1", "B", extra=(wrap,)),))


def test_an_empty_element_without_a_namespace_is_seen_when_added_or_removed():
    flag = ExtraNode(qname="flag")
    added = diff.compare(_wrapped(), _wrapped(flag))
    assert added.identical is False
    assert paths(added) == [("features", "added", "extra.wrap[1].flag[1]#ns")]
    removed = diff.compare(_wrapped(flag), _wrapped())
    assert paths(removed) == [("features", "removed", "extra.wrap[1].flag[1]#ns")]
    assert diff.compare(_wrapped(flag), _wrapped(flag)).identical
    assert schema_errors(added.to_dict()) == []


def test_a_second_empty_element_of_the_same_name_is_seen():
    flag = ExtraNode(qname="flag")
    result = diff.compare(_wrapped(flag), _wrapped(flag, flag))
    assert paths(result) == [("features", "added", "extra.wrap[1].flag[2]#ns")]


# --- 3. the order of same-named extra siblings is not a difference -----------------------


def _item(text: str, *children: ExtraNode) -> ExtraNode:
    return ExtraNode(qname="x:item", ns="urn:x", text=text, children=children)


def test_swapping_same_named_extra_siblings_is_not_a_change():
    assert diff.compare(
        _wrapped(_item("A"), _item("B")), _wrapped(_item("B"), _item("A"))
    ).identical


def test_swapping_same_named_extra_subtrees_is_not_a_change():
    def leaf(name: str, text: str) -> ExtraNode:
        return ExtraNode(qname=name, ns="urn:x", text=text)

    one = _item("A", leaf("x:p", "1"), leaf("x:q", "2"))
    two = _item("B", leaf("x:p", "3"))
    swapped_inside = _item("A", leaf("x:q", "2"), leaf("x:p", "1"))
    assert diff.compare(_wrapped(one, two), _wrapped(two, swapped_inside)).identical


def test_one_edit_among_same_named_siblings_is_one_change_whatever_the_order():
    old = _wrapped(_item("A"), _item("B"), _item("C"))
    for new in (
        _wrapped(_item("A"), _item("B"), _item("Z")),
        _wrapped(_item("Z"), _item("B"), _item("A")),
    ):
        result = diff.compare(old, new)
        (value,) = [v for c in result.changes for v in c.values]
        assert (value.op, value.old, value.new) == ("changed", "C", "Z")
        assert value.path.endswith("#text")
    grown = diff.compare(old, _wrapped(_item("C"), _item("D"), _item("B"), _item("A")))
    assert [(v.op, v.new) for c in grown.changes for v in c.values] == [
        ("added", "urn:x"),
        ("added", "D"),
    ]


def test_elements_below_the_depth_limit_compare_in_any_order_too():
    def nest(leaves: tuple[ExtraNode, ...]) -> ExtraNode:
        node = ExtraNode(qname="deep", children=leaves)
        for _ in range(70):
            node = ExtraNode(qname="deep", children=(node,))
        return node

    a, b = ExtraNode(qname="leaf", text="A"), ExtraNode(qname="leaf", text="B")
    old = make_model(features=(make_feature("f1", "B", extra=(nest((a, b)),)),))
    new = make_model(features=(make_feature("f1", "B", extra=(nest((b, a)),)),))
    assert diff.compare(old, new).identical
    other = make_model(features=(make_feature("f1", "B", extra=(nest((a, a)),)),))
    assert diff.compare(old, other).identical is False


# --- 4. where a nested node sits is part of what it is ----------------------------------


def _drive_sets(holder_of_d1: str, ids: tuple[str, str] = ("t1", "t2")):
    def proc(kind: str, *components):
        return Procedure(type=kind, components=tuple(components))

    d1 = make_component("d1", "driveSet", params={"index": "1"})
    first = (d1,) if holder_of_d1 == "t1" else ()
    second = (d1,) if holder_of_d1 == "t2" else ()
    return make_model(
        features=(make_feature("f1", "B"),),
        measurements=(
            make_test(ids[0], "T-1", "f1", procedure=proc("DrivenPenetrationTest", *first)),
            make_test(ids[1], "T-2", "f1", at=4, procedure=proc("FlatDilatometerTest", *second)),
        ),
    )


def test_a_component_moved_to_another_holder_is_a_change():
    result = diff.compare(_drive_sets("t1"), _drive_sets("t2"))
    assert result.identical is False
    assert paths(result) == [("components", "changed", "holder")]
    (value,) = [v for c in result.changes for v in c.values]
    assert (value.old, value.new) == ("T-1", "T-2")
    assert schema_errors(result.to_dict()) == []


def test_renaming_a_components_holder_is_not_a_change():
    assert only_ids_differ(diff.compare(_drive_sets("t1"), _drive_sets("t1", ids=("x1", "x2"))))


def _layers(holder_of_layer: str, ids: tuple[str, str] = ("o1", "o2")):
    layer = LithologyLayer(
        **_header("l1", None, "Lithology", "lithology", 9),
        interval=Interval(top=depth("0"), base=depth("1")),
    )

    def system(key: str, name: str, at: int, *layers: LithologyLayer) -> Observation:
        header = _header(key, name, "LithologySystem", "lithology_system", at)
        return Observation(**header, system="lithology", feature="f1", layers=layers)

    first = (layer,) if holder_of_layer == "o1" else ()
    second = (layer,) if holder_of_layer == "o2" else ()
    return make_model(
        features=(make_feature("f1", "B"),),
        observations=(system(ids[0], "USCS", 5, *first), system(ids[1], "AASHTO", 6, *second)),
    )


def test_a_layer_moved_to_another_observation_is_a_change():
    result = diff.compare(_layers("o1"), _layers("o2"))
    assert paths(result) == [("layers", "changed", "holder")]
    assert only_ids_differ(diff.compare(_layers("o1"), _layers("o1", ids=("p1", "p2"))))


# --- 5. the time budget is checked however small or large the pieces are -----------------


def test_a_small_comparison_started_after_the_deadline_is_incomplete(monkeypatch):
    monkeypatch.setattr(_engine, "_now", FakeClock())
    model = make_model(features=(make_feature("f1", "B"),))
    result = diff.compare(model, model, time_budget=0.5)
    assert result.complete is False and result.identical is False
    assert result.limits_hit == ("time_budget",)


def _timed_units(monkeypatch, old, new) -> tuple[diff.Diff, int]:
    """A run stopped at the clock's twentieth reading, and the work it had done by then."""
    monkeypatch.setattr(_engine, "_now", FakeClock())
    run = _engine.compare_models(old, new, _engine.Options(time_budget=20.0), deadline=20.0)
    return run.diff, run.units


def test_one_node_with_very_many_values_cannot_overrun_the_budget(monkeypatch):
    def model(suffix: str):
        params = {f"p{i}": f"{i}{suffix}" for i in range(20_000)}
        extra = tuple(ExtraNode(qname=f"e{i}", text=suffix) for i in range(20_000))
        return make_model(features=(make_feature("f1", "B", params=params, extra=extra),))

    result, units = _timed_units(monkeypatch, model(""), model("x"))
    assert result.complete is False and "time_budget" in result.limits_hit
    assert units < 20 * 2 * _compare.CHUNK


def test_one_large_table_cannot_overrun_the_budget(monkeypatch):
    def model(shift: float):
        rows = 20_000
        index = (column("position", *[float(i) for i in range(rows)], key="position"),)
        data = tuple(
            column(f"c{k}", *[i + shift for i in range(rows)], key=f"c{k}") for k in range(4)
        )
        tab = ResultTable(
            domain=Domain(shape="multipoint", index=index), columns=data, row_count=rows
        )
        return make_model(
            features=(make_feature("f1", "B"),),
            measurements=(make_test("t1", "T", "f1", results=(tab,)),),
        )

    result, units = _timed_units(monkeypatch, model(0.0), model(0.5))
    assert result.complete is False and "time_budget" in result.limits_hit
    assert units < 20 * 2 * _compare.CHUNK


def test_a_very_long_tuple_is_compared_in_slices_with_the_same_verdict(monkeypatch):
    def model(last: float):
        coords = (*[float(i) for i in range(29_999)], last)
        return make_model(
            features=(make_feature("f1", "B", location=Position(coordinates=coords, dim=3)),)
        )

    assert diff.compare(model(7.0), model(7.0 + 1e-13)).identical
    assert paths(diff.compare(model(7.0), model(8.0))) == [
        ("features", "changed", "location.coordinates")
    ]
    result, units = _timed_units(monkeypatch, model(7.0), model(8.0))
    assert result.complete is False and units < 20 * 2 * _compare.CHUNK


# --- 6. a cut list does not promise complete counts after a timeout ----------------------


def test_the_limit_note_does_not_call_partial_counts_complete(monkeypatch):
    def features(suffix: str):
        return tuple(
            make_feature(f"f{i}", f"B-{i}", at=i + 1, params={"v": f"{i}{suffix}"})
            for i in range(2_000)
        )

    old, new = make_model(features=features("")), make_model(features=features("x"))
    monkeypatch.setattr(_engine, "_now", FakeClock())
    result = diff.compare(old, new, max_changes=1, time_budget=8)
    assert result.limits_hit == ("max_changes", "time_budget")
    limit, timeout = result.notes
    assert (limit.rule_id, timeout.rule_id) == ("diff.limit", "diff.timeout")
    assert "complete" not in limit.message
    assert "partial" in limit.message and "partial" in timeout.message
    monkeypatch.undo()
    whole = diff.compare(old, new, max_changes=1)
    (note,) = whole.notes
    assert "the counts are complete" in note.message


# --- 7. a table of index columns only is still compared ----------------------------------


def _index_only(*depths: float) -> ResultTable:
    index = (column("position", *depths, key="position", uom="ft"),)
    return ResultTable(domain=Domain(shape="multipoint", index=index), row_count=len(depths))


def _holding(tab: ResultTable):
    return make_model(
        features=(make_feature("f1", "B"),),
        measurements=(make_test("t1", "T", "f1", results=(tab,)),),
    )


def test_a_changed_depth_in_a_table_without_data_columns_is_a_change():
    old = _holding(_index_only(1.0, 2.0))
    result = diff.compare(old, _holding(_index_only(1.0, 2.5)))
    assert result.identical is False
    assert [(v.op, v.path) for c in result.changes for v in c.values] == [
        ("removed", "results[0].domain.index[position].cells[@2.0]"),
        ("added", "results[0].domain.index[position].cells[@2.5]"),
    ]
    assert diff.compare(old, _holding(_index_only(1.0, 2.0))).identical


def test_tables_with_no_columns_at_all_still_compare_their_row_counts():
    result = diff.compare(_holding(ResultTable(row_count=2)), _holding(ResultTable(row_count=3)))
    assert paths(result) == [("measurements", "changed", "results[0].row_count")]
    assert diff.compare(_holding(ResultTable(row_count=2)), _holding(ResultTable(row_count=2)))


# --- 8. rows align within the tolerance whatever order that leaves them in ----------------


def _intervals(rows: list[tuple[float, float]], values: list[float]):
    index = (
        column("top", *[r[0] for r in rows], key="top", uom="ft"),
        column("base", *[r[1] for r in rows], key="base", uom="ft"),
    )
    return _holding(table(column("q", *values, key="q"), index=index))


def test_float_noise_in_the_first_index_column_does_not_misalign_the_rows():
    old = _intervals([(1.0, 2.0), (1.0, 3.0)], [10.0, 20.0])
    noisy = [(1.0 + 1e-12, 2.0), (1.0 - 1e-12, 3.0)]
    assert diff.compare(old, _intervals(noisy, [10.0, 20.0])).identical
    result = diff.compare(old, _intervals(noisy, [10.0, 21.0]))
    assert [(v.op, v.path, v.old, v.new) for c in result.changes for v in c.values] == [
        ("changed", "results[0].columns[q].cells[@1.0,3.0]", 20.0, 21.0)
    ]


def test_rows_in_another_order_align_by_their_index():
    old = _intervals([(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)], [1.0, 2.0, 3.0])
    new = _intervals([(2.0, 3.0), (0.0, 1.0), (1.0, 2.5)], [3.0, 1.0, 2.0])
    result = diff.compare(old, new)
    assert sorted((v.op, v.path) for c in result.changes for v in c.values) == [
        ("added", "results[0].columns[q].cells[@1.0,2.5]"),
        ("added", "results[0].domain.index[base].cells[@1.0,2.5]"),
        ("added", "results[0].domain.index[top].cells[@1.0,2.5]"),
        ("removed", "results[0].columns[q].cells[@1.0,2.0]"),
        ("removed", "results[0].domain.index[base].cells[@1.0,2.0]"),
        ("removed", "results[0].domain.index[top].cells[@1.0,2.0]"),
    ]


def test_rows_the_tolerance_cannot_tell_apart_fall_back_to_row_numbers():
    old = _intervals([(1.0, 2.0), (1.05, 2.0)], [10.0, 20.0])
    new = _intervals([(1.0, 2.0), (1.05, 2.0)], [10.0, 21.0])
    result = diff.compare(old, new, abs_tol=0.1)
    assert paths(result) == [("measurements", "changed", "results[0].columns[q].cells[2]")]
    # Each new row is within the tolerance of both old rows: pairing either way is a guess.
    shifted = _intervals([(1.04, 2.0), (1.09, 2.0)], [10.0, 20.0])
    assert diff.compare(old, shifted, abs_tol=0.1).identical
    # The same when only the new file has two such rows.
    apart = _intervals([(1.0, 2.0), (5.0, 2.0)], [10.0, 20.0])
    near = _intervals([(1.0, 2.0), (1.05, 2.0)], [10.0, 20.0])
    assert paths(diff.compare(apart, near, abs_tol=0.1)) == [
        ("measurements", "changed", "results[0].domain.index[top].cells[2]")
    ]


def test_rows_that_only_share_a_chain_of_near_values_are_not_paired():
    # With abs_tol 0.05 the tops 1.00, 1.04, 1.08 and 1.12 chain into one run of near
    # values, but 1.00 and 1.12 are not equal: those two rows are different rows.
    old = _intervals([(1.00, 10.0), (1.08, 20.0)], [1.0, 2.0])
    new = _intervals([(1.04, 20.0), (1.12, 10.0)], [2.0, 1.0])
    result = diff.compare(old, new, abs_tol=0.05)
    assert sorted((v.op, v.path) for c in result.changes for v in c.values) == [
        ("added", "results[0].columns[q].cells[@1.12,10.0]"),
        ("added", "results[0].domain.index[base].cells[@1.12,10.0]"),
        ("added", "results[0].domain.index[top].cells[@1.12,10.0]"),
        ("removed", "results[0].columns[q].cells[@1.0,10.0]"),
        ("removed", "results[0].domain.index[base].cells[@1.0,10.0]"),
        ("removed", "results[0].domain.index[top].cells[@1.0,10.0]"),
    ]


def test_an_index_value_that_is_not_a_number_falls_back_to_row_numbers():
    def model(q: float):
        index = (column("top", 1.0, None, key="top"),)
        return _holding(table(column("q", 10.0, q, key="q"), index=index))

    result = diff.compare(model(20.0), model(21.0))
    assert paths(result) == [("measurements", "changed", "results[0].columns[q].cells[2]")]


def test_not_a_number_and_infinite_index_values_align_with_themselves():
    nan, inf = float("nan"), float("inf")
    old = _intervals([(nan, 1.0), (inf, 1.0), (-inf, 1.0), (0.0, 1.0)], [1.0, 2.0, 3.0, 4.0])
    new = _intervals([(0.0, 1.0), (-inf, 1.0), (inf, 1.0), (nan, 1.0)], [4.0, 3.0, 2.0, 1.0])
    assert diff.compare(old, new).identical


@pytest.mark.scaling
@pytest.mark.parametrize("n", [500, 4000], ids=["small", "large"])
def test_table_work_is_linear_with_a_two_column_index(n):
    def world(m: int, shift: float):
        rows = [(float(i), i + 1.0 + shift) for i in range(m)]
        return _intervals(rows, [float(i) for i in range(m)])

    def units(m: int) -> int:
        return _engine.compare_models(world(m, 0.0), world(m, 0.5), _engine.Options()).units

    small, large = units(n), units(2 * n)
    assert large <= 2.5 * small, f"{n} -> {2 * n}: work grew {large / small:.2f}x"


# --- 9. an exported document shares nothing with the diff --------------------------------


def _moved() -> diff.Diff:
    def model(coords: tuple[float, ...]):
        pos = Position(coordinates=coords, dim=len(coords), crs="urn:ogc:def:crs:EPSG::4326")
        return make_model(features=(make_feature("f1", "B", location=pos),))

    return diff.compare(model((1.0, 2.0)), model((1.0, 2.5)))


def test_changing_an_exported_document_does_not_change_the_diff():
    result = _moved()
    before = copy.deepcopy(result.to_dict())
    document = result.to_dict()
    value = document["changes"][0]["values"][0]
    assert value["old"] == [1.0, 2.0]
    value["old"].append(99.0)
    value["new"].clear()
    document["limits_hit"].append("x")
    document["summary"]["features"]["changed"] = 7
    assert result.to_dict() == before


def test_a_diff_built_from_a_document_does_not_share_its_lists():
    document = _moved().to_dict()
    before = copy.deepcopy(document)
    rebuilt = diff.Diff.from_dict(document)
    document["changes"][0]["values"][0]["old"].append(99.0)
    assert rebuilt.to_dict() == before


# --- 10. only a diggs-diff 1.x document is read ------------------------------------------


def _document(**changes: Any) -> dict[str, Any]:
    document = _moved().to_dict()
    for key, value in changes.items():
        if value is None:
            del document[key.replace("schema_url", "$schema")]
        else:
            document[key.replace("schema_url", "$schema")] = value
    return document


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"format": "diggs-findings"}, "format"),
        ({"format": None}, "format"),
        ({"schema_url": None}, r"\$schema"),
        ({"schema_url": 7}, r"\$schema"),
        ({"schema_version": None}, "schema_version"),
        ({"schema_version": "1.not-a-version"}, "schema_version"),
        ({"schema_version": "1."}, "schema_version"),
        ({"schema_version": "1.0.0"}, "schema_version"),
        ({"schema_version": "1.-1"}, "schema_version"),
        ({"schema_version": "1.\u0663"}, "schema_version"),
        ({"schema_version": "1.0\n"}, "schema_version"),
        ({"schema_version": 1.0}, "schema_version"),
    ],
)
def test_from_dict_refuses_a_document_that_is_not_diggs_diff_1x(changes, message):
    document = _document(**changes)
    if changes != {"schema_version": "1.0\n"}:  # a pattern's "$" lets a last newline through
        assert schema_errors(document) != []
    with pytest.raises(ValueError, match=message):
        diff.Diff.from_dict(document)


def test_from_dict_reads_any_1x_minor_version():
    assert diff.Diff.from_dict(_document()) == _moved()
    later = diff.Diff.from_dict(_document(schema_version="1.12"))
    assert later.changes == _moved().changes


# --- the text renderer -------------------------------------------------------------------


def test_text_spells_out_hidden_characters_of_every_width():
    a = make_model(features=(make_feature("f1", "B", params={"p": "a"}),))
    hidden = "x\x07\u202e\U000e0001"
    b = make_model(features=(make_feature("f1", "B", params={"p": hidden}),))
    text = diff.compare(a, b).to_text()
    assert "x\\x07\\u202e\\U000e0001" in text


def test_text_shows_lists_booleans_missing_values_and_shortened_ones():
    old = make_model(
        features=(
            make_feature(
                "f1",
                "B",
                params={"flag": True, "long": "y" * 300, "gone": "1"},
                location=Position(coordinates=(1.0, 2.0), dim=2),
            ),
        )
    )
    new = make_model(
        features=(
            make_feature(
                "f1",
                "B",
                params={"flag": False, "long": "z" * 300, "here": "2"},
                location=Position(coordinates=(1.0, 2.5), dim=2),
            ),
        )
    )
    text = diff.compare(old, new).to_text()
    assert "    params.flag: true -> false" in text
    assert "    location.coordinates: [1.0, 2.0] -> [1.0, 2.5]" in text
    assert "    params.gone: removed 1" in text
    assert "    params.here: added 2" in text
    assert " (shortened)" in text


def test_text_names_how_a_changed_node_was_matched():
    old = make_model(features=(make_feature("f1", "B-1", params={"a": "1"}),))
    by_name = make_model(features=(make_feature("g1", "B-1", params={"a": "2"}),))
    text = diff.compare(old, by_name).to_text()
    assert "changed features g1 (B-1), matched by natural key (f1 -> g1)" in text
    by_id = make_model(features=(make_feature("f1", "B-1", params={"a": "2"}),))
    assert "changed features f1 (B-1), matched by gml:id\n" in diff.compare(old, by_id).to_text()


def test_text_of_unnamed_nodes_and_sources_and_of_an_incomplete_diff():
    old = make_model(features=(make_feature(None, None), make_feature("f2", "B-2", at=2)))
    new = make_model(features=(make_feature("f2", "B-2", at=2),))
    result = diff.compare(old, new, max_changes=0)
    result = dataclasses.replace(result, old={**result.old, "name": None})
    text = result.to_text()
    assert text.startswith("Compared (unnamed) (DIGGS 3.0.0) with x.xml (DIGGS 3.0.0)\n")
    assert "The two files differ." in text
    assert "The comparison is incomplete (stopped by max_changes)." in text
    assert "note diff.limit: " in text
    listed = diff.compare(old, new).to_text()
    assert "removed features @/Diggs[1]/x[1]\n" in listed


def test_text_of_a_diff_that_only_stopped_early_does_not_say_the_files_differ(monkeypatch):
    monkeypatch.setattr(_engine, "_now", FakeClock())
    model = make_model(features=(make_feature("f1", "B"),))
    text = diff.compare(model, model, time_budget=0.5).to_text()
    assert "identical" not in text and "differ" not in text
    assert "The comparison is incomplete (stopped by time_budget)." in text


def test_a_column_helper_keeps_its_type():
    assert isinstance(column("n", 1.0), Column)
