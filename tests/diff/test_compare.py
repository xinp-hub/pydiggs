"""The contract of ``pydiggs.diff.compare``: identity, values, tables, options, inputs."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

import pydiggs
from pydiggs import diff
from pydiggs.errors import DocumentError, InstanceReadError
from pydiggs.model import Code, ExtraNode, Interval, Link, Position, Procedure, Provenance

from ._helpers import (
    ANNOTATED,
    ROOT,
    column,
    depth,
    make_component,
    make_feature,
    make_model,
    make_sample,
    make_test,
    table,
)


def values(result: diff.Diff) -> list[tuple[str, str, str]]:
    """``(collection, op, path)`` of every listed value change, in listed order."""
    return [(c.collection, v.op, v.path) for c in result.changes for v in c.values]


def ops(result: diff.Diff) -> list[tuple[str, str, str | None, str | None]]:
    return [(c.collection, c.op, c.old_key, c.new_key) for c in result.changes]


# --- identical -----------------------------------------------------------------------


def test_a_model_is_identical_to_itself_and_the_summary_says_so():
    a = make_model(features=(make_feature("f1", "B-1"),), samples=(make_sample("s1", "S", "f1"),))
    result = diff.compare(a, a)
    assert result.identical is True
    assert result.complete is True
    assert result.limits_hit == ()
    assert result.changes == ()
    assert result.notes == ()
    assert result.value_changes == 0
    assert result.summary == {
        "features": {"added": 0, "removed": 0, "changed": 0, "unchanged": 1},
        "samples": {"added": 0, "removed": 0, "changed": 0, "unchanged": 1},
    }


def test_the_documented_example_runs():
    result = diff.compare(ANNOTATED, ANNOTATED)
    assert (result.identical, result.complete, result.changes) == (True, True, ())
    assert result.old["diggs_version"] == "3.0.0"


# --- identity (Q8) -------------------------------------------------------------------


def test_same_gml_id_and_element_is_matched_by_gml_id_even_if_the_name_changed():
    old = make_model(features=(make_feature("f1", "B-1"),))
    new = make_model(features=(make_feature("f1", "B-1A"),))
    result = diff.compare(old, new)
    (change,) = result.changes
    assert (change.op, change.matched_by, change.old_key, change.new_key) == (
        "changed",
        "gml_id",
        "f1",
        "f1",
    )
    assert [(v.path, v.old, v.new) for v in change.values] == [
        ("name", "B-1", "B-1A"),
        ("names", ["B-1"], ["B-1A"]),
    ]
    assert result.summary["features"] == {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}


def test_a_different_gml_id_falls_back_to_the_natural_key_and_is_not_a_difference():
    old = make_model(features=(make_feature("f1", "B-1"),))
    new = make_model(features=(make_feature("g9", "B-1"),))
    result = diff.compare(old, new)
    assert result.identical is True and result.complete is True
    assert result.changes == ()
    assert (result.renamed, result.value_changes, result.reformatted) == (1, 0, 0)
    assert result.summary["features"] == {"added": 0, "removed": 0, "changed": 0, "unchanged": 1}


def test_renamed_ids_are_listed_when_asked():
    old = make_model(features=(make_feature("f1", "B-1"),))
    new = make_model(features=(make_feature("g9", "B-1"),))
    result = diff.compare(old, new, show_renamed=True)
    (change,) = result.changes
    assert (change.op, change.matched_by, change.old_key, change.new_key) == (
        "renamed",
        "natural",
        "f1",
        "g9",
    )
    (value,) = change.values
    assert (value.op, value.path, value.old, value.new) == ("renamed", "gml_id", "f1", "g9")
    assert result.identical is True and result.renamed == 1
    assert result.summary["features"]["unchanged"] == 1
    # Listing reformatted values is another request: it lists no renamed id.
    assert diff.compare(old, new, show_reformatted=True).changes == ()


def test_a_real_change_with_a_renamed_id_is_changed_and_the_id_is_listed_only_when_asked():
    old = make_model(features=(make_feature("f1", "B-1", params={"a": "1"}),))
    new = make_model(features=(make_feature("g9", "B-1", params={"a": "2"}),))
    result = diff.compare(old, new)
    assert result.identical is False
    assert (result.renamed, result.value_changes) == (1, 1)
    assert result.summary["features"] == {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}
    (change,) = result.changes
    assert (change.op, change.matched_by, change.old_key, change.new_key) == (
        "changed",
        "natural",
        "f1",
        "g9",
    )
    assert [(v.op, v.path) for v in change.values] == [("changed", "params.a")]
    (shown,) = diff.compare(old, new, show_renamed=True).changes
    assert shown.op == "changed"
    assert [(v.op, v.path) for v in shown.values] == [
        ("renamed", "gml_id"),
        ("changed", "params.a"),
    ]


def test_a_renamed_record_with_a_reformatted_number_is_listed_by_what_was_asked_for():
    old = _with_total("1.50")
    new_feature = dataclasses.replace(_with_total("1.5").features[0], key="g9", gml_id="g9")
    new = make_model(features=(new_feature,))
    quiet = diff.compare(old, new)
    assert quiet.identical is True and quiet.changes == ()
    assert (quiet.renamed, quiet.reformatted) == (1, 1)
    (only_numbers,) = diff.compare(old, new, show_reformatted=True).changes
    assert only_numbers.op == "reformatted"
    assert [v.op for v in only_numbers.values] == ["reformatted"]
    (only_id,) = diff.compare(old, new, show_renamed=True).changes
    assert only_id.op == "renamed"
    assert [v.op for v in only_id.values] == ["renamed"]
    (both,) = diff.compare(old, new, show_reformatted=True, show_renamed=True).changes
    assert both.op == "renamed"
    assert [(v.op, v.path) for v in both.values] == [
        ("renamed", "gml_id"),
        ("reformatted", "total_depth"),
    ]


def test_an_id_that_only_one_file_gives_a_record_is_a_rename_too():
    old = make_model(features=(make_feature(None, "B-1"),))
    new = make_model(features=(make_feature("g9", "B-1"),))
    result = diff.compare(old, new, show_renamed=True)
    assert result.identical is True and result.renamed == 1
    (value,) = result.changes[0].values
    assert (value.op, value.path, value.old, value.new) == ("renamed", "gml_id", None, "g9")


def test_a_listed_rename_counts_toward_max_changes_like_a_reformatted_value():
    old = make_model(features=(make_feature("f1", "B-1"), make_feature("f2", "B-2", at=2)))
    new = make_model(features=(make_feature("g1", "B-1"), make_feature("g2", "B-2", at=2)))
    # Not listed: nothing to cut, whatever the limit.
    assert diff.compare(old, new, max_changes=0).identical is True
    cut = diff.compare(old, new, max_changes=2, show_renamed=True)
    assert len(cut.changes) == 1 and cut.limits_hit == ("max_changes",)
    assert (cut.complete, cut.identical, cut.renamed) == (False, False, 2)
    whole = diff.compare(old, new, max_changes=4, show_renamed=True)
    assert len(whole.changes) == 2 and whole.identical is True


def test_match_gml_id_never_uses_natural_keys():
    old = make_model(features=(make_feature("f1", "B-1"),))
    new = make_model(features=(make_feature("g9", "B-1"),))
    for listed in (False, True):
        result = diff.compare(old, new, match="gml-id", show_renamed=listed)
        assert ops(result) == [
            ("features", "removed", "f1", None),
            ("features", "added", None, "g9"),
        ]
        assert result.summary["features"] == {
            "added": 1,
            "removed": 1,
            "changed": 0,
            "unchanged": 0,
        }
        assert result.identical is False and result.renamed == 0


def test_match_natural_ignores_equal_gml_ids():
    old = make_model(features=(make_feature("f1", "B-1"), make_feature("f2", "B-2", at=2)))
    new = make_model(features=(make_feature("f1", "B-2", at=1), make_feature("f2", "B-1", at=2)))
    by_id = diff.compare(old, new)
    assert {c.op for c in by_id.changes} == {"changed"}
    assert all(c.matched_by == "gml_id" for c in by_id.changes)
    # By name, each borehole is the other file's borehole under another id: no change.
    assert diff.compare(old, new, match="natural").identical is True
    natural = diff.compare(old, new, match="natural", show_renamed=True)
    assert {c.op for c in natural.changes} == {"renamed"}
    assert all(c.matched_by == "natural" for c in natural.changes)
    assert {(c.old_key, c.new_key) for c in natural.changes} == {("f1", "f2"), ("f2", "f1")}


def test_the_same_gml_id_on_another_element_is_not_a_pass_one_match():
    other = dataclasses.replace(make_feature("f1", "B-1"), element="Well", kind="well")
    result = diff.compare(
        make_model(features=(make_feature("f1", "B-1"),)), make_model(features=(other,))
    )
    assert ops(result) == [("features", "removed", "f1", None), ("features", "added", None, "f1")]


def test_nodes_without_a_gml_id_are_matched_naturally():
    old = make_model(features=(make_feature(None, "B-1", at=4),))
    new = make_model(features=(make_feature(None, "B-1", at=9),))
    assert diff.compare(old, new).identical


def test_an_ambiguous_natural_key_is_never_matched_and_is_reported():
    old = make_model(features=(make_feature("a", "B-1", at=1), make_feature("b", "B-1", at=2)))
    new = make_model(features=(make_feature("c", "B-1", at=1), make_feature("d", "B-1", at=2)))
    result = diff.compare(old, new)
    assert [(c.op, c.matched_by) for c in result.changes] == [
        ("removed", None),
        ("removed", None),
        ("added", None),
        ("added", None),
    ]
    assert result.summary["features"] == {"added": 2, "removed": 2, "changed": 0, "unchanged": 0}
    (note,) = result.notes
    assert (note.step.value, note.rule_id, note.severity) == (
        "diff",
        "diff.ambiguous-match",
        "info",
    )
    assert "features" in note.message and "2" in note.message
    assert result.complete is True
    assert result.identical is False


def test_a_key_that_repeats_on_one_side_only_is_not_ambiguous_when_the_other_has_none():
    old = make_model(features=(make_feature("a", "B-1", at=1), make_feature("b", "B-1", at=2)))
    result = diff.compare(old, make_model())
    assert result.notes == ()
    assert result.summary["features"]["removed"] == 2


def test_references_follow_the_matching_so_an_id_rename_changes_nothing_else():
    old = make_model(
        features=(make_feature("f1", "B-1"),),
        samples=(make_sample("s1", "S-1", "f1", interval=Interval(top=depth("1.0"), lrs="#l")),),
    )
    new = make_model(
        features=(make_feature("F", "B-1"),),
        samples=(make_sample("S", "S-1", "F", interval=Interval(top=depth("1.0"), lrs="#l")),),
    )
    result = diff.compare(old, new)
    assert result.identical is True and result.changes == ()
    assert result.renamed == 2
    assert values(diff.compare(old, new, show_renamed=True)) == [
        ("features", "renamed", "gml_id"),
        ("samples", "renamed", "gml_id"),
    ]


def test_a_reference_to_another_node_is_a_change():
    f = (make_feature("f1", "B-1"), make_feature("f2", "B-2", at=2))
    old = make_model(features=f, samples=(make_sample("s1", "S", "f1"),))
    new = make_model(features=f, samples=(make_sample("s1", "S", "f2"),))
    (change,) = diff.compare(old, new).changes
    ((value),) = change.values
    assert (value.path, value.old, value.new) == ("feature", "B-1", "B-2")


def test_links_compare_by_relation_and_target_not_by_href():
    f = make_feature("f1", "B-1")
    p = make_feature("p1", "P", at=2)
    a = Link(source="f1", relation="project", target="p1", href="#p1", resolved=True)
    b = dataclasses.replace(a, href="#p1 ")
    same = diff.compare(
        make_model(features=(f, p), links=(a,)), make_model(features=(f, p), links=(b,))
    )
    assert same.identical
    moved = Link(source="f1", relation="project", target=None, href="http://x/y", resolved=False)
    result = diff.compare(
        make_model(features=(f, p), links=(a,)), make_model(features=(f, p), links=(moved,))
    )
    assert {(c.collection, c.op) for c in result.changes} == {("features", "changed")}
    assert [v.path for c in result.changes for v in c.values] == ["links.project"]


# --- values (Q9) ---------------------------------------------------------------------


def _with_total(text: str, uom: str | None = "ft"):
    return make_model(features=(make_feature("f1", "B", total_depth=depth(text, uom)),))


def test_a_unit_change_is_a_change_and_nothing_is_converted():
    result = diff.compare(_with_total("10.0", "ft"), _with_total("3.048", "m"))
    (change,) = result.changes
    (value,) = change.values
    assert (value.op, value.path, value.old, value.new) == (
        "changed",
        "total_depth",
        "10.0 ft",
        "3.048 m",
    )
    same_number = diff.compare(_with_total("10.0", "ft"), _with_total("10.0", "m"))
    assert [v.path for c in same_number.changes for v in c.values] == ["total_depth"]


def test_numbers_are_equal_within_float_noise_by_default():
    assert diff.compare(_with_total("10.0"), _with_total("10.000000000000002")).identical
    assert not diff.compare(_with_total("10.0"), _with_total("10.0001")).identical


@pytest.mark.parametrize(
    ("options", "equal"),
    [
        ({"abs_tol": 0.01}, True),
        ({"abs_tol": 0.0001}, False),
        ({"rel_tol": 1e-3}, True),
        ({"rel_tol": 1e-6}, False),
    ],
    ids=["abs-wide", "abs-narrow", "rel-wide", "rel-narrow"],
)
def test_tolerance_is_the_larger_of_the_absolute_and_relative_bound(options, equal):
    assert diff.compare(_with_total("10.0"), _with_total("10.005"), **options).identical is equal


def test_the_same_number_written_differently_is_reformatted_counted_and_not_listed():
    result = diff.compare(_with_total("1.50"), _with_total("1.5"))
    assert result.identical is True
    assert result.changes == ()
    assert result.reformatted == 1
    assert result.value_changes == 0
    assert result.summary["features"]["unchanged"] == 1


def test_reformatted_values_are_listed_when_asked():
    result = diff.compare(_with_total("1.50"), _with_total("1.5"), show_reformatted=True)
    (change,) = result.changes
    assert change.op == "reformatted"
    (value,) = change.values
    assert (value.op, value.path, value.old, value.new) == (
        "reformatted",
        "total_depth",
        "1.50 ft",
        "1.5 ft",
    )
    assert result.identical is True
    assert result.summary["features"]["unchanged"] == 1


def test_codes_compare_by_value_and_code_space_and_text_exactly():
    def model(**params):
        return make_model(features=(make_feature("f1", "B", params=params),))

    base = model(kind=Code(value="A", code_space="cs1"), status="FINAL")
    assert diff.compare(
        base, model(kind=Code(value="A", code_space="cs1"), status="FINAL")
    ).identical
    space = diff.compare(base, model(kind=Code(value="A", code_space="cs2"), status="FINAL"))
    assert values(space) == [("features", "changed", "params.kind")]
    text = diff.compare(base, model(kind=Code(value="A", code_space="cs1"), status="final"))
    assert values(text) == [("features", "changed", "params.status")]


def test_a_parameter_that_appears_or_disappears_is_an_added_or_removed_value():
    base = make_model(features=(make_feature("f1", "B", params={"a": "1"}),))
    more = make_model(features=(make_feature("f1", "B", params={"a": "1", "b": "2"}),))
    added = diff.compare(base, more)
    assert [(v.op, v.path, v.old, v.new) for c in added.changes for v in c.values] == [
        ("added", "params.b", None, "2")
    ]
    removed = diff.compare(more, base)
    assert [(v.op, v.path, v.old, v.new) for c in removed.changes for v in c.values] == [
        ("removed", "params.b", "2", None)
    ]


def test_provenance_source_tool_notes_and_document_order_are_ignored():
    f1, f2 = make_feature("f1", "B-1"), make_feature("f2", "B-2", at=2)
    moved = dataclasses.replace(f1, provenance=Provenance(xpath="/Diggs[1]/other[7]", line=99))
    a = make_model(features=(f1, f2))
    b = dataclasses.replace(
        make_model(features=(f2, moved)),
        tool={"name": "other", "version": "9"},
        source=dataclasses.replace(a.source, name="y.xml", sha256="f" * 64, size=999),
        notes=(
            pydiggs.Finding(step="structure", rule_id="structure.x", severity="info", message="m"),
        ),
    )
    assert diff.compare(a, b).identical


def test_extra_is_compared_structurally_and_in_any_element_order():
    def leaf(name, text):
        return ExtraNode(qname=name, ns="urn:x", text=text)

    parent = ExtraNode(qname="x:wrap", ns="urn:x", children=(leaf("x:a", "1"), leaf("x:b", "2")))
    base = make_model(features=(make_feature("f1", "B", extra=(parent,)),))
    reordered = dataclasses.replace(parent, children=(leaf("x:b", "2"), leaf("x:a", "1")))
    assert diff.compare(
        base, make_model(features=(make_feature("f1", "B", extra=(reordered,)),))
    ).identical
    edited = dataclasses.replace(parent, children=(leaf("x:a", "1"), leaf("x:b", "3")))
    result = diff.compare(base, make_model(features=(make_feature("f1", "B", extra=(edited,)),)))
    (value,) = [v for c in result.changes for v in c.values]
    assert value.path == "extra.wrap[1].b[1]#text"
    assert (value.old, value.new) == ("2", "3")
    attr = dataclasses.replace(parent, attrs={"k": "v"})
    result = diff.compare(base, make_model(features=(make_feature("f1", "B", extra=(attr,)),)))
    assert values(result) == [("features", "added", "extra.wrap[1]@k")]


def test_positions_compare_coordinates_with_tolerance_and_the_crs_reference():
    def model(coords, crs="urn:ogc:def:crs:EPSG::4326"):
        pos = Position(coordinates=coords, dim=len(coords), crs=crs)
        return make_model(features=(make_feature("f1", "B", location=pos),))

    base = model((1.0, 2.0))
    assert diff.compare(base, model((1.0, 2.0 + 1e-12))).identical
    moved = diff.compare(base, model((1.0, 2.5)))
    assert values(moved) == [("features", "changed", "location.coordinates")]
    (value,) = [v for c in moved.changes for v in c.values]
    assert (value.old, value.new) == ([1.0, 2.0], [1.0, 2.5])
    other = diff.compare(base, model((1.0, 2.0), crs="urn:ogc:def:crs:EPSG::4269"))
    assert values(other) == [("features", "changed", "location.crs")]


def test_a_text_that_is_not_a_number_compares_exactly():
    a = make_model(
        features=(make_feature("f1", "B", params={"d": pydiggs.model.Quantity(text="n/a")}),)
    )
    b = make_model(
        features=(make_feature("f1", "B", params={"d": pydiggs.model.Quantity(text="N/A")}),)
    )
    assert diff.compare(a, a).identical
    assert values(diff.compare(a, b)) == [("features", "changed", "params.d")]


def test_a_nan_equals_a_nan_and_the_output_stays_json():
    nan = float("nan")
    a = make_model(
        features=(
            make_feature(
                "f1", "B", total_depth=pydiggs.model.Quantity(text="x", value=nan, uom="m")
            ),
        )
    )
    b = make_model(
        features=(
            make_feature(
                "f1", "B", total_depth=pydiggs.model.Quantity(text="x", value=1.0, uom="m")
            ),
        )
    )
    assert diff.compare(a, a).identical
    assert diff.compare(a, b).to_json()  # allow_nan=False would raise on a raw nan


# --- tables --------------------------------------------------------------------------


def _test_with(*tables):
    return make_model(
        features=(make_feature("f1", "B"),),
        measurements=(make_test("t1", "T", "f1", results=tuple(tables)),),
    )


def _cpt(depths, qc, *, qc_name="qc", qc_key="tip_resistance", dup=False):
    index = (column("position", *depths, key="position", uom="ft"),)
    return table(column(qc_name, *qc, key=qc_key, uom="MPa"), index=index)


def test_columns_match_by_key_and_rows_by_the_domain_index():
    old = _test_with(_cpt([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]))
    new = _test_with(_cpt([0.5, 1.0, 2.0, 3.0], [5.0, 10.0, 21.0, 30.0]))
    result = diff.compare(old, new)
    assert [(v.op, v.path, v.old, v.new) for c in result.changes for v in c.values] == [
        ("added", "results[0].columns[tip_resistance].cells[@0.5]", None, 5.0),
        ("changed", "results[0].columns[tip_resistance].cells[@2.0]", 20.0, 21.0),
        ("added", "results[0].domain.index[position].cells[@0.5]", None, 0.5),
    ]


def test_a_renamed_column_with_the_same_key_is_still_the_same_column():
    old = _test_with(_cpt([1.0, 2.0], [10.0, 20.0], qc_name="qc"))
    new = _test_with(_cpt([1.0, 2.0], [10.0, 20.0], qc_name="Qc (MPa)"))
    result = diff.compare(old, new)
    assert values(result) == [
        ("measurements", "changed", "results[0].columns[tip_resistance].name")
    ]


def test_rows_align_by_index_within_the_tolerance():
    old = _test_with(_cpt([1.0, 2.0], [10.0, 20.0]))
    new = _test_with(_cpt([1.0 + 1e-12, 2.0], [10.0, 20.0]))
    assert diff.compare(old, new).identical
    moved = _test_with(_cpt([1.01, 2.0], [10.0, 20.0]))
    assert not diff.compare(old, moved, abs_tol=0.001).identical
    assert diff.compare(old, moved, abs_tol=0.1).identical


def test_a_duplicate_index_falls_back_to_row_numbers():
    old = _test_with(_cpt([1.0, 1.0, 2.0], [10.0, 11.0, 20.0]))
    new = _test_with(_cpt([1.0, 1.0, 2.0], [10.0, 12.0, 20.0]))
    result = diff.compare(old, new)
    assert values(result) == [
        ("measurements", "changed", "results[0].columns[tip_resistance].cells[2]")
    ]


def test_a_table_without_an_index_aligns_by_row_number_and_adds_trailing_rows():
    old = _test_with(table(column("n", 1.0, 2.0, key="n")))
    new = _test_with(table(column("n", 1.0, 3.0, 4.0, key="n")))
    result = diff.compare(old, new)
    assert [(v.op, v.path, v.old, v.new) for c in result.changes for v in c.values] == [
        ("changed", "results[0].columns[n].cells[2]", 2.0, 3.0),
        ("added", "results[0].columns[n].cells[3]", None, 4.0),
    ]


def test_a_column_that_appears_or_disappears_is_one_value_change():
    old = _test_with(table(column("a", 1.0, 2.0, key="a")))
    new = _test_with(table(column("a", 1.0, 2.0, key="a"), column("b", 5.0, 6.0, key="b")))
    added = diff.compare(old, new)
    assert [(v.op, v.path) for c in added.changes for v in c.values] == [
        ("added", "results[0].columns[b]")
    ]
    assert diff.compare(new, old).value_changes == 1


def test_a_whole_table_that_appears_or_disappears_is_one_value_change():
    one = _test_with(table(column("a", 1.0, key="a")))
    two = _test_with(table(column("a", 1.0, key="a")), table(column("b", 2.0, key="b")))
    result = diff.compare(one, two)
    assert [(v.op, v.path) for c in result.changes for v in c.values] == [("added", "results[1]")]


def test_a_column_unit_change_is_reported_once_and_cells_are_still_compared():
    a = _test_with(table(column("a", 1.0, key="a", uom="m")))
    b = _test_with(table(column("a", 1.0, key="a", uom="ft")))
    assert values(diff.compare(a, b)) == [("measurements", "changed", "results[0].columns[a].uom")]


def test_cells_that_differ_in_kind_are_changes_and_text_cells_compare_exactly():
    a = _test_with(table(column("a", 1.0, "x", None, key="a")))
    b = _test_with(table(column("a", 1.0, "X", 2.0, key="a")))
    assert [(v.path, v.old, v.new) for c in diff.compare(a, b).changes for v in c.values] == [
        ("results[0].columns[a].cells[2]", "x", "X"),
        ("results[0].columns[a].cells[3]", None, 2.0),
    ]


def test_an_inventory_read_without_values_compares_row_counts_and_never_claims_equal_cells():
    full = pydiggs.read(ANNOTATED)
    inventory = pydiggs.read(ANNOTATED, results=False)
    assert diff.compare(inventory, inventory).identical
    result = diff.compare(full, inventory)
    assert not result.identical
    paths = [v.path for c in result.changes for v in c.values]
    assert paths
    assert all(p.endswith(".values") for p in paths), paths
    short = dataclasses.replace(
        inventory,
        measurements=tuple(
            dataclasses.replace(
                m,
                results=tuple(
                    dataclasses.replace(t, row_count=t.row_count + 1) if t.row_count else t
                    for t in m.results
                ),
            )
            for m in inventory.measurements
        ),
    )
    counts = [v.path for c in diff.compare(inventory, short).changes for v in c.values]
    assert counts and all(p.endswith(".row_count") for p in counts)


# --- procedure and nested components -------------------------------------------------


def test_procedure_parameters_and_nested_components_are_compared():
    def proc(blows):
        comp = make_component("d1", "driveSet", params={"index": "1", "blowCount": blows})
        return Procedure(
            type="DrivenPenetrationTest", params={"hammer": "auto"}, components=(comp,)
        )

    old = make_model(
        features=(make_feature("f1", "B"),),
        measurements=(make_test("t1", "T", "f1", procedure=proc("8")),),
    )
    new = make_model(
        features=(make_feature("f1", "B"),),
        measurements=(make_test("t1", "T", "f1", procedure=proc("9")),),
    )
    result = diff.compare(old, new)
    assert [(c.collection, c.old_key, v.path) for c in result.changes for v in c.values] == [
        ("components", "d1", "params.blowCount")
    ]
    assert result.summary["measurements"]["unchanged"] == 1
    assert result.summary["components"]["changed"] == 1


# --- ordering ------------------------------------------------------------------------


def test_changes_are_ordered_by_collection_then_key_then_path():
    old = make_model(
        features=(
            make_feature("f2", "B", params={"z": "1", "a": "1"}),
            make_feature("f1", "A", at=2),
        ),
        samples=(make_sample("s1", "S", "f1", at=5),),
    )
    new = make_model(
        features=(
            make_feature("f2", "B", params={"z": "2", "a": "2"}),
            make_feature("f1", "A2", at=2),
        ),
        samples=(make_sample("s1", "S2", "f1", at=5),),
    )
    result = diff.compare(old, new)
    assert [(c.collection, c.new_key) for c in result.changes] == [
        ("features", "f1"),
        ("features", "f2"),
        ("samples", "s1"),
    ]
    assert [v.path for v in result.changes[1].values] == ["params.a", "params.z"]
    assert diff.compare(old, new) == result


# --- inputs, options and errors ------------------------------------------------------


def test_paths_bytes_and_models_are_interchangeable(tmp_path):
    path = ROOT / "tests/fixtures/official/3.0/MWDExample.xml"
    data = path.read_bytes()
    model = pydiggs.read(path)
    for old, new in [(path, data), (str(path), model), (data, model), (bytearray(data), path)]:
        assert diff.compare(old, new).identical
    assert diff.compare(Path(path), path.with_name("TrenchWallExample.xml")).changes


def test_the_two_files_may_be_different_diggs_versions_and_both_are_recorded():
    old = ROOT / "tests/fixtures/official/2.6/Aeromag.xml"
    new = ROOT / "tests/fixtures/official/3.0/Aeromag.xml"
    result = diff.compare(old, new)
    assert (result.old["diggs_version"], result.new["diggs_version"]) == ("2.6", "3.0.0")
    assert result.old["name"] == "Aeromag.xml"
    assert len(result.new["sha256"]) == 64


@pytest.mark.parametrize(
    ("option", "bad", "message"),
    [
        ("match", "fuzzy", "match must be"),
        ("rel_tol", -1.0, "rel_tol"),
        ("rel_tol", float("nan"), "rel_tol"),
        ("rel_tol", True, "rel_tol"),
        ("abs_tol", -0.5, "abs_tol"),
        ("abs_tol", float("inf"), "abs_tol"),
        ("abs_tol", "1", "abs_tol"),
        ("max_changes", -1, "max_changes"),
        ("max_changes", 1.5, "max_changes"),
        ("max_changes", True, "max_changes"),
        ("time_budget", 0, "time_budget"),
        ("time_budget", float("nan"), "time_budget"),
        ("time_budget", "5", "time_budget"),
        ("max_bytes", 0, "max_bytes"),
    ],
    ids=lambda v: str(v)[:12],
)
def test_bad_options_are_a_value_error_naming_the_option(option, bad, message):
    model = make_model()
    with pytest.raises(ValueError, match=message):
        diff.compare(model, model, **{option: bad})


@pytest.mark.parametrize(
    "bad", [None, 3, ["a.xml"], object()], ids=["none", "int", "list", "object"]
)
def test_an_input_of_the_wrong_type_is_a_type_error(bad):
    with pytest.raises(TypeError, match="old must be"):
        diff.compare(bad, make_model())
    with pytest.raises(TypeError, match="new must be"):
        diff.compare(make_model(), bad)


def test_everything_pydiggs_read_raises_comes_through(tmp_path):
    ok = ROOT / "tests/fixtures/official/3.0/MWDExample.xml"
    with pytest.raises(InstanceReadError):
        diff.compare(tmp_path / "missing.xml", ok)
    with pytest.raises(DocumentError) as info:
        diff.compare(ok, b"<html/>")
    assert info.value.kind == "not-diggs"
    with pytest.raises(DocumentError):
        diff.compare(b"<Diggs", ok)
    with pytest.raises(InstanceReadError):
        diff.compare(ok, ok, max_bytes=100)


def test_the_options_are_recorded():
    result = diff.compare(make_model(), make_model(), rel_tol=1e-6, max_changes=5)
    assert result.options == {
        "match": "auto",
        "rel_tol": 1e-6,
        "abs_tol": 0.0,
        "max_changes": 5,
        "time_budget": None,
    }


# --- the module ----------------------------------------------------------------------


def test_the_public_names_are_sorted_and_documented():
    assert diff.__all__ == sorted(
        diff.__all__, key=lambda n: (0 if n.isupper() else 1 if n[:1].isupper() else 2, n)
    )
    assert set(diff.__all__) == {
        "Change",
        "DIFF_SCHEMA_URL",
        "DIFF_SCHEMA_VERSION",
        "Diff",
        "ValueChange",
        "compare",
        "diff_schema",
    }
    assert diff.DIFF_SCHEMA_VERSION == "1.0"
    assert (
        diff.DIFF_SCHEMA_URL
        == "https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json"
    )
    assert diff.diff_schema()["$id"] == diff.DIFF_SCHEMA_URL


@pytest.mark.parametrize("cls", ["Diff", "Change", "ValueChange"])
def test_the_result_classes_are_frozen_and_slotted(cls):
    obj = getattr(diff, cls)
    assert dataclasses.is_dataclass(obj)
    assert hasattr(obj, "__slots__")
    result = diff.compare(make_model(), make_model())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.identical = False  # type: ignore[misc]


def test_import_pydiggs_does_not_import_the_diff_module():
    import subprocess

    code = (
        "import sys, pydiggs\n"
        "before = 'pydiggs.diff' in sys.modules\n"
        "from pydiggs import diff\n"
        "print(before, 'pydiggs.diff' in sys.modules, 'rich' in sys.modules)\n"
        "print(hasattr(pydiggs, 'diff') and 'diff' in pydiggs.__all__)\n"
    )
    out = subprocess.run(  # noqa: S603  # our own interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout.split()
    assert out[:2] == ["False", "True"]
    assert out[-1] == "False"
    assert "diff" not in pydiggs.__all__
