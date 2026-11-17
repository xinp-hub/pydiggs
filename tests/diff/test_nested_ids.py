"""Ids are not values: a re-export that gives every ``gml:id`` a new value is the same data.

The ``gml:id`` of an element inside ``extra`` is not compared, and a local reference
(``xlink:href="#x"``, ``srsName="#x"``) is compared by what it points to. A reference that
cannot be resolved for certain stays text, so a link that points at another thing is never
hidden.

An element a typed field read (a borehole's centre line) is not in the model; a reference
to one is resolved from the two files, so only when both are given as paths or bytes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pydiggs import diff, read_bytes
from pydiggs._reader import _read, read_with_places
from pydiggs.cli import main
from pydiggs.model import CrsRef, ExtraNode, Interval, Link

from ._helpers import OFFICIAL, depth, make_feature, make_model, make_sample, schema_errors

AEROMAG = OFFICIAL / "3.0" / "Aeromag.xml"
BOREHOLES = OFFICIAL / "3.0" / "Borehole-CPT_Example_Annotated.xml"
MWD = OFFICIAL / "3.0" / "MWDExample.xml"
#: What stays after every id is reissued: records that only their ``gml:id`` told apart.
AMBIGUOUS = {"GeophysicalFieldSurveyExamples": 2, "TrenchWallExample": 6}
LINEAR_ELEMENT = "extra.linearReferencing[1].LinearSpatialReferenceSystem[1].linearElement[1]"
OFFICIAL_3_0 = sorted((OFFICIAL / "3.0").glob("*.xml"))


def reissue_ids(text: str) -> str:
    """``text`` with every ``gml:id`` given a new value and every local reference following."""
    ids = list(dict.fromkeys(re.findall(r'\bgml:id="([^"]+)"', text)))
    new = {old: f"x{len(ids) - i:04d}" for i, old in enumerate(ids)}
    text = re.sub(r'\bgml:id="([^"]+)"', lambda m: f'gml:id="{new[m[1]]}"', text)
    return re.sub(r'="#([^"]+)"', lambda m: f'="#{new.get(m[1], m[1])}"', text)


def paths(result: diff.Diff) -> list[tuple[str, str, str]]:
    return [(c.collection, v.op, v.path) for c in result.changes for v in c.values]


def _geom(gml_id: str | None, text: str, *, id_name: str = "gml:id", **attrs: str) -> ExtraNode:
    own = {} if gml_id is None else {id_name: gml_id}
    pos = ExtraNode(qname="gml:pos", ns="urn:gml", text=text)
    return ExtraNode(qname="gml:Point", ns="urn:gml", attrs={**own, **attrs}, children=(pos,))


def _ref(href: str, name: str = "xlink:href") -> ExtraNode:
    return ExtraNode(qname="x:usedPoint", ns="urn:x", attrs={name: href})


def _holder(*extra: ExtraNode, key: str = "f1", links: tuple[Link, ...] = ()):
    return make_model(features=(make_feature(key, "B-1", extra=extra),), links=links)


# --- a whole file ------------------------------------------------------------------------


def test_an_official_file_with_every_gml_id_reissued_is_identical(tmp_path, capsys):
    # Aeromag: 15 gml:ids, among them a geometry kept in ``extra`` (PlanarSurface), a time
    # interval and a point that typed fields read, and a link to that point (#opl).
    text = AEROMAG.read_text("utf-8")
    again = reissue_ids(text)
    assert 'gml:id="ps1"' in text and 'xlink:href="#opl"' in text
    assert not set(re.findall(r'gml:id="([^"]+)"', text)) & set(
        re.findall(r'gml:id="([^"]+)"', again)
    )
    result = diff.compare(text.encode(), again.encode())
    assert result.identical is True and result.changes == ()
    assert result.renamed == 5  # the records with a gml:id; nested ids are not counted
    assert all(c["changed"] == c["added"] == c["removed"] == 0 for c in result.summary.values())
    by_id = diff.compare(text.encode(), again.encode(), match="gml-id")
    assert by_id.identical is False

    reissued = tmp_path / "reissued.xml"
    reissued.write_text(again, "utf-8")
    assert main(["diff", str(AEROMAG), str(reissued)]) == 0
    assert "The two files are identical." in capsys.readouterr().out


def test_a_link_pointed_at_another_element_is_a_difference_whatever_the_ids(tmp_path, capsys):
    # The survey's reference point (#opl, a point inside the test) now names the survey's
    # own surface (#ps1): another element of another record.
    text = AEROMAG.read_text("utf-8")
    moved = text.replace(
        '<referencePoint xlink:href="#opl"/>', '<referencePoint xlink:href="#ps1"/>'
    )
    assert moved != text
    for new in (moved, reissue_ids(moved)):
        result = diff.compare(text.encode(), new.encode())
        assert result.identical is False
        assert paths(result) == [("features", "changed", "links.referencePoint")]
    target = tmp_path / "moved.xml"
    target.write_text(reissue_ids(moved), "utf-8")
    assert main(["diff", str(AEROMAG), str(target)]) == 1
    assert "links.referencePoint" in capsys.readouterr().out


@pytest.mark.parametrize("path", OFFICIAL_3_0, ids=lambda p: p.stem)
def test_what_still_differs_after_reissuing_every_id_is_only_what_cannot_be_matched(
    path: Path, tmp_path, capsys
):
    """A reissued file is identical, but for records that only their ``gml:id`` told apart.

    Such records share a natural key, which is never guessed at: they are removed and
    added. No value is changed in any file: the reference of a linear referencing system
    to its centre line, which the model does not keep, follows the centre line.
    """
    text = path.read_text("utf-8")
    assert re.findall(r'gml:id="', text)
    reissued = tmp_path / "reissued.xml"
    reissued.write_text(reissue_ids(text), "utf-8")
    result = diff.compare(path, reissued)
    assert result.value_changes == 0
    assert all(counts["changed"] == 0 for counts in result.summary.values())
    ops = [change.op for change in result.changes]
    unmatched = AMBIGUOUS.get(path.stem, 0)
    assert sorted(ops) == ["added"] * unmatched + ["removed"] * unmatched
    assert any(n.rule_id == "diff.ambiguous-match" for n in result.notes) is bool(unmatched)
    assert result.identical is (unmatched == 0)
    assert main(["diff", str(path), str(reissued)]) == (1 if unmatched else 0)
    assert ("The two files are identical." in capsys.readouterr().out) is (unmatched == 0)


# --- a reference to an element a typed field read (not in the model) -------------------


def _last(result: diff.Diff) -> set[str]:
    return {v.path for c in result.changes for v in c.values}


def test_the_reader_places_the_elements_the_model_does_not_keep():
    model, places = read_with_places(BOREHOLES)
    assert model == read_bytes(BOREHOLES.read_bytes(), name=BOREHOLES.name)
    assert places["cl-B-01"] == ("Location_B-01", "centerLine/LinearExtent")
    assert places["pl-B-01"] == ("Location_B-01", "referencePoint/PointLocation")
    assert places["cl-BENT_9_MIDDLE_A"][1] == "centerLine/LinearExtent"
    # Not a record's own id, and not an id the model keeps in ``extra``.
    assert "Location_B-01" not in places
    kept = {
        value
        for node in model.nodes()
        for item in _walk(node.extra)
        for name, value in item.attrs.items()
        if name == "gml:id"
    }
    assert kept and not kept & places.keys()


def _walk(items):
    for item in items:
        yield item
        yield from _walk(item.children)


def test_a_linear_element_pointed_at_another_element_of_its_borehole_is_a_difference(
    tmp_path, capsys
):
    # The depth system of borehole B-01 names the borehole's reference point instead of
    # its centre line. Both are read into typed fields, so the model has neither id.
    text = BOREHOLES.read_text("utf-8")
    moved = text.replace(
        'linearElement xlink:href="#cl-B-01"', 'linearElement xlink:href="#pl-B-01"'
    )
    assert moved != text
    for new in (moved, reissue_ids(moved)):
        result = diff.compare(text.encode(), new.encode())
        assert result.identical is False
        assert _last(result) == {f"{LINEAR_ELEMENT}@xlink:href", "links.linearElement"}
        (change,) = result.changes
        assert (change.collection, change.name) == ("features", "B-01")
        target = tmp_path / "moved.xml"
        target.write_text(new, "utf-8")
        assert main(["diff", str(BOREHOLES), str(target)]) == 1
        assert "linearElement" in capsys.readouterr().out


def test_a_linear_element_pointed_at_another_boreholes_centre_line_is_a_difference():
    text = BOREHOLES.read_text("utf-8")
    moved = text.replace(
        'linearElement xlink:href="#cl-B-01"', 'linearElement xlink:href="#cl-BENT_9_MIDDLE_A"'
    )
    assert moved != text
    for new in (moved, reissue_ids(moved)):
        result = diff.compare(text.encode(), new.encode())
        assert result.identical is False
        assert _last(result) == {f"{LINEAR_ELEMENT}@xlink:href", "links.linearElement"}
    # Each borehole's system naming the other's centre line: the same two ids, crossed.
    crossed = moved.replace(
        'linearElement xlink:href="#cl-BENT_9_MIDDLE_A"/>\n\t\t\t\t\t<lrm>\n\t\t\t\t\t\t<LinearReferencingMethod gml:id="lrm-BENT',
        "",
    )
    assert crossed == moved  # the fixture is not edited by accident
    both = text.replace("#cl-B-01", "#SWAP").replace("#cl-BENT_9_MIDDLE_A", "#cl-B-01")
    both = both.replace("#SWAP", "#cl-BENT_9_MIDDLE_A")
    for new in (both, reissue_ids(both)):
        result = diff.compare(text.encode(), new.encode())
        assert result.identical is False
        assert len(result.changes) == 2


def test_between_models_such_a_reference_is_still_compared_as_text():
    # A model has no file to place the centre line by: the behaviour of two models, and
    # of a model against a file, is what it was.
    data = BOREHOLES.read_bytes()
    again = reissue_ids(data.decode()).encode()
    old, new = read_bytes(data), read_bytes(again)
    for a, b in ((old, new), (old, again), (data, new)):
        result = diff.compare(a, b)
        assert result.identical is False
        assert _last(result) == {f"{LINEAR_ELEMENT}@xlink:href"}
        assert len(result.changes) == 2  # one value per borehole
    assert diff.compare(old, old).identical
    assert diff.compare(old, data).identical and diff.compare(data, old).identical
    assert diff.compare(data, again).identical


def _centre_line(text: str, inside: str = "", after: str = "") -> str:
    """The MWD borehole with ``inside`` added to its centre line and ``after`` behind it."""
    head, mark, tail = text.partition("</LinearExtent>\n      </centerLine>")
    assert mark
    return f"{head}{inside}{mark}{after}{tail}"


def _point_at(text: str, target: str) -> str:
    out = text.replace(
        'linearElement xlink:href="#cl_bh_TEST_HOLE_1"', f'linearElement xlink:href="#{target}"'
    )
    assert out != text
    return out


@pytest.mark.parametrize(
    "inside, after, target",
    [
        # a second centre line: which of the two is meant would rest on their order
        ("", '<centerLine><LinearExtent gml:id="cl2"/></centerLine>', "cl_bh_TEST_HOLE_1"),
        # two elements of the file have the id
        ('<a gml:id="twice"/><b gml:id="twice"/>', "", "twice"),
        # deeper below the record than a place goes
        ("<d>" * 20 + '<e gml:id="deep"/>' + "</d>" * 20, "", "deep"),
        # a path longer than a place keeps
        (f'<{"n" * 200} gml:id="long"/>', "", "long"),
    ],
    ids=["same-named-sibling", "duplicate-id", "deep", "long-name"],
)
def test_an_element_that_cannot_be_placed_for_certain_is_compared_as_text(inside, after, target):
    text = _centre_line(MWD.read_text("utf-8"), inside, after)
    assert text != MWD.read_text("utf-8")
    if target != "cl_bh_TEST_HOLE_1":
        text = _point_at(text, target)
    _, places = read_with_places(text.encode())
    assert target not in places
    assert diff.compare(text.encode(), text.encode()).identical
    result = diff.compare(text.encode(), reissue_ids(text).encode())
    assert result.identical is False
    assert _last(result) == {f"{LINEAR_ELEMENT}@xlink:href"}


def test_a_place_does_not_depend_on_the_order_or_the_prefix_of_the_elements():
    text = MWD.read_text("utf-8")
    head, mark, rest = text.partition("      <referencePoint>")
    point, close, tail = rest.partition("</referencePoint>\n")
    line, end, after = tail.partition("</centerLine>\n")
    assert mark and close and end
    # The centre line now stands before the reference point.
    turned = f"{head}{line}{end}{mark}{point}{close}{after}"
    assert turned != text and len(turned) == len(text)
    assert diff.compare(text.encode(), reissue_ids(turned).encode()).identical
    moved = _point_at(turned, "pl_bh_TEST_HOLE_1")
    assert diff.compare(text.encode(), reissue_ids(moved).encode()).identical is False


# --- the index of places is bounded ----------------------------------------------------


def _many_ids(n: int) -> str:
    inside = "".join(f'<h{i} gml:id="h{i}"/>' for i in range(n))
    return _point_at(_centre_line(MWD.read_text("utf-8"), inside), f"h{n - 1}")


def test_placing_many_ids_counts_the_children_of_a_parent_once(monkeypatch):
    made = []

    class Counting(_read.Counter):
        def __init__(self, *args, **kwargs):
            made.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(_read, "Counter", Counting)

    def counters(n: int) -> int:
        made.clear()
        _, places = read_with_places(_many_ids(n).encode())
        assert sum(1 for name in places if name.startswith("h")) == n
        return len(made)

    assert counters(4000) == counters(1000)


def test_the_index_of_places_stops_at_the_readers_work_limit(monkeypatch):
    text = _many_ids(3000)
    data = text.encode()
    monkeypatch.setattr(_read, "LIMIT_BASE", 0)
    limit = len(data) // _read.LIMIT_BYTES_PER_ITEM
    model, places = read_with_places(data)
    assert model.complete and limit - 5 < len(places) <= limit < 3000
    # The last id is past the limit, so the reference to it is text: never a guess.
    assert "h2999" not in places
    assert diff.compare(data, data).identical
    result = diff.compare(data, reissue_ids(text).encode())
    assert result.identical is False and result.complete
    assert _last(result) == {f"{LINEAR_ELEMENT}@xlink:href"}


def test_ids_nested_as_deep_as_the_parser_allows_are_read_and_not_placed():
    depth_ = 200
    inside = "".join(f'<d gml:id="d{i}">' for i in range(depth_)) + "</d>" * depth_
    text = _point_at(_centre_line(MWD.read_text("utf-8"), inside), f"d{depth_ - 1}")
    model, places = read_with_places(text.encode())
    assert model.complete
    assert [name for name in places if name.startswith("d")] == [
        f"d{i}" for i in range(_read.MAX_PLACE_STEPS - 2)
    ]
    assert diff.compare(text.encode(), text.encode(), time_budget=30).identical
    assert diff.compare(text.encode(), reissue_ids(text).encode()).identical is False


def test_a_time_budget_still_bounds_a_comparison_of_two_files():
    data = BOREHOLES.read_bytes()
    result = diff.compare(data, data, time_budget=1e-9)
    assert result.complete is False and result.identical is False


# --- the id of an element inside extra ------------------------------------------------


def test_the_gml_id_of_an_element_inside_extra_is_not_compared():
    old = _holder(_geom("p1", "1 2"))
    assert diff.compare(old, _holder(_geom("q7", "1 2"))).identical
    # An id only one file gives the element is no change either, like a record's.
    assert diff.compare(old, _holder(_geom(None, "1 2"))).identical
    # The attribute is told by its local name in a namespace, not by its prefix.
    assert diff.compare(old, _holder(_geom("q7", "1 2", id_name="g:id"))).identical
    assert diff.compare(old, _holder(_geom("q7", "1 2", id_name="{urn:gml}id"))).identical
    # Everything else about the element still counts, a plain ``id`` attribute included.
    assert paths(diff.compare(old, _holder(_geom("p1", "1 3")))) == [
        ("features", "changed", "extra.Point[1].pos[1]#text")
    ]
    plain = diff.compare(_holder(_geom("p1", "1 2", id="a")), _holder(_geom("p1", "1 2", id="b")))
    assert paths(plain) == [("features", "changed", "extra.Point[1]@id")]
    listed = diff.compare(old, _holder(_geom("q7", "1 2")), show_renamed=True)
    assert listed.renamed == 0 and listed.changes == ()  # ``renamed`` counts records


def test_siblings_that_differ_only_by_their_gml_id_still_cancel_in_any_order():
    old = _holder(_geom("p1", "1 2"), _geom("p2", "3 4"), _geom("p3", "5 6"))
    new = _holder(_geom("q3", "5 6"), _geom("q1", "1 2"), _geom("q2", "3 4"))
    assert diff.compare(old, new).identical
    edited = _holder(_geom("q3", "5 6"), _geom("q1", "1 2"), _geom("q2", "3 5"))
    assert paths(diff.compare(old, edited)) == [
        ("features", "changed", "extra.Point[3].pos[1]#text")
    ]


# --- a reference to an element inside extra -------------------------------------------


def test_a_reference_to_an_element_inside_extra_follows_the_element_not_the_id():
    old = _holder(_geom("p1", "1 2"), _geom("p2", "3 4"), _ref("#p1"))
    same = _holder(_geom("q2", "3 4"), _geom("q1", "1 2"), _ref("#q1"))
    assert diff.compare(old, same).identical
    # Same ids as before, but the reference now names the other point.
    moved = _holder(_geom("p1", "1 2"), _geom("p2", "3 4"), _ref("#p2"))
    result = diff.compare(old, moved)
    assert result.identical is False
    assert paths(result) == [("features", "changed", "extra.usedPoint[1]@xlink:href")]
    (value,) = result.changes[0].values
    assert (value.old, value.new) == ("#p1", "#p2")
    # New ids, and the reference names the other point: still a change.
    assert (
        diff.compare(old, _holder(_geom("q1", "1 2"), _geom("q2", "3 4"), _ref("#q2"))).identical
        is False
    )
    # The ids swapped places and the reference kept its text: it names another point now.
    swapped = _holder(_geom("p2", "1 2"), _geom("p1", "3 4"), _ref("#p1"))
    assert diff.compare(old, swapped).identical is False


def test_a_reference_follows_an_edited_element_and_reports_only_the_edit():
    old = _holder(_geom("p1", "1 2"), _geom("p2", "3 4"), _ref("#p1"))
    new = _holder(_geom("q1", "1 9"), _geom("q2", "3 4"), _ref("#q1"))
    assert paths(diff.compare(old, new)) == [("features", "changed", "extra.Point[2].pos[1]#text")]


def test_a_reference_into_another_record_follows_the_pairing():
    def model(feature: str, point: str, target: str):
        return make_model(
            features=(make_feature(feature, "B-1", extra=(_geom(point, "1 2"),)),),
            samples=(make_sample(f"s-{feature}", "S-1", feature, extra=(_ref(f"#{target}"),)),),
        )

    old = model("f1", "p1", "p1")
    assert diff.compare(old, model("g1", "q1", "q1")).identical
    # A reference to the record itself is another thing than one to the point inside it.
    assert paths(diff.compare(old, model("g1", "q1", "g1"))) == [
        ("samples", "changed", "extra.usedPoint[1]@xlink:href")
    ]
    assert diff.compare(model("f1", "p1", "f1"), model("g1", "q1", "g1")).identical


@pytest.mark.parametrize(
    "new_extra",
    [
        # two elements of the file have the id
        (_geom("q1", "1 2"), _geom("q1", "3 4"), _ref("#q1")),
        # nothing in the file has it
        (_geom("q1", "1 2"), _geom("q2", "3 4"), _ref("#gone")),
        # another document
        (_geom("q1", "1 2"), _geom("q2", "3 4"), _ref("http://example.com/x#q1")),
    ],
    ids=["duplicate-id", "dangling", "external"],
)
def test_a_reference_that_cannot_be_resolved_is_compared_as_text(new_extra):
    old = _holder(_geom("p1", "1 2"), _geom("p2", "3 4"), _ref("#p1"))
    result = diff.compare(old, _holder(*new_extra))
    assert result.identical is False
    assert ("features", "changed", "extra.usedPoint[1]@xlink:href") in paths(result)
    # The same text on both sides is the same reference, resolved or not.
    assert diff.compare(_holder(*new_extra), _holder(*new_extra)).identical


def test_two_equal_elements_cannot_be_told_apart_so_a_reference_to_one_stays_text():
    old = _holder(_geom("p1", "1 2"), _geom("p2", "1 2"), _ref("#p1"))
    assert diff.compare(old, old).identical
    new = _holder(_geom("q1", "1 2"), _geom("q2", "1 2"), _ref("#q1"))
    # Never a guess: a difference is reported rather than "identical" on a coin toss.
    assert paths(diff.compare(old, new)) == [
        ("features", "changed", "extra.usedPoint[1]@xlink:href")
    ]


def test_a_link_to_an_element_inside_extra_is_compared_by_that_element():
    def model(first: str, second: str, href: str):
        link = Link(source="f1", relation="usedPoint", target="f1", href=href, resolved=True)
        return _holder(_geom(first, "1 2"), _geom(second, "3 4"), links=(link,))

    old = model("p1", "p2", "#p1")
    assert diff.compare(old, model("q1", "q2", "#q1")).identical
    for moved in (model("p1", "p2", "#p2"), model("q1", "q2", "#q2")):
        assert paths(diff.compare(old, moved)) == [("features", "changed", "links.usedPoint")]


# --- references to coordinate systems ------------------------------------------------


def test_a_features_own_depth_system_is_told_by_the_feature_when_the_model_has_no_more():
    def model(lrs: str):
        interval = Interval(top=depth("1"), lrs=lrs)
        extent = ExtraNode(qname="LinearExtent", ns="urn:x", attrs={"srsName": lrs})
        return make_model(
            features=(make_feature("f1", "B-1", lrs=lrs), make_feature("f2", "B-2", at=2)),
            samples=(make_sample("s1", "S", "f1", interval=interval, extra=(extent,)),),
        )

    assert diff.compare(model("#l1"), model("#m1")).identical


def _site_grid(*systems: tuple[str, str], used: str):
    crs = {
        name: CrsRef(srs_name=name, form="local", name=title, known=True, local=name[1:])
        for name, title in systems
    }
    surface = ExtraNode(qname="PlanarSurface", ns="urn:x", attrs={"srsName": used})
    return make_model(features=(make_feature("f1", "B-1", extra=(surface,)),), crs=crs)


def test_a_coordinate_system_outside_every_record_is_told_by_what_the_model_knows_of_it():
    old = _site_grid(("#grid", "Site grid"), used="#grid")
    assert diff.compare(old, _site_grid(("#g9", "Site grid"), used="#g9")).identical
    other = diff.compare(old, _site_grid(("#g9", "Wall grid"), used="#g9"))
    assert paths(other) == [("features", "changed", "extra.PlanarSurface[1]@srsName")]
    # Two systems that read the same cannot be told apart: the name in the file decides.
    twins = (("#a", "Site grid"), ("#b", "Site grid"))
    assert diff.compare(_site_grid(*twins, used="#a"), _site_grid(*twins, used="#a")).identical
    assert (
        diff.compare(_site_grid(*twins, used="#a"), _site_grid(*twins, used="#b")).identical
        is False
    )


# --- review findings on references to elements inside extra -----------------------------


def _used(role: str, href: str) -> ExtraNode:
    return ExtraNode(qname="x:usedPoint", ns="urn:x", attrs={"role": role, "xlink:href": href})


def test_repeated_references_whose_targets_swapped_ids_are_not_cancelled_as_equal():
    # Two references with distinct roles keep their text, but the ids of the two points
    # changed places: each role now names the other point. The href text and the set of
    # links are what they were; what is pointed at is not.
    def model(first: str, second: str):
        links = tuple(
            Link(source="f1", relation="usedPoint", target="f1", href=h, resolved=True)
            for h in ("#p1", "#p2")
        )
        return _holder(
            _geom(first, "1 2"),
            _geom(second, "3 4"),
            _used("top", "#p1"),
            _used("base", "#p2"),
            links=links,
        )

    old = model("p1", "p2")
    assert diff.compare(old, old).identical
    result = diff.compare(old, model("p2", "p1"))
    assert result.identical is False
    assert paths(result) == [
        ("features", "changed", "extra.usedPoint[1]@xlink:href"),
        ("features", "changed", "extra.usedPoint[2]@xlink:href"),
    ]
    # New ids, the points in another order, each role still naming its point: the same.
    same = _holder(
        _geom("b", "3 4"),
        _used("top", "#a"),
        _geom("a", "1 2"),
        _used("base", "#b"),
        links=tuple(
            Link(source="f1", relation="usedPoint", target="f1", href=h, resolved=True)
            for h in ("#a", "#b")
        ),
    )
    assert diff.compare(old, same).identical

    # The same below another element: the cancelled subtrees are checked at any depth.
    def wrapped(first: str, second: str):
        group = ExtraNode(
            qname="x:group", ns="urn:x", children=(_used("top", "#p1"), _used("base", "#p2"))
        )
        return _holder(_geom(first, "1 2"), _geom(second, "3 4"), group)

    assert diff.compare(wrapped("p1", "p2"), wrapped("p1", "p2")).identical
    assert diff.compare(wrapped("p1", "p2"), wrapped("p2", "p1")).identical is False


def test_a_link_to_one_of_two_equal_elements_is_compared_by_its_text():
    def model(href: str, ids: tuple[str, str] = ("p1", "p2")):
        link = Link(source="f1", relation="usedPoint", target="f1", href=href, resolved=True)
        return _holder(_geom(ids[0], "1 2"), _geom(ids[1], "1 2"), links=(link,))

    old = model("#p1")
    assert diff.compare(old, old).identical
    result = diff.compare(old, model("#p2"))
    assert result.identical is False
    assert paths(result) == [("features", "changed", "links.usedPoint")]
    # Never a guess: with new ids the two points cannot be told apart, so it is reported.
    assert diff.compare(old, model("#q1", ("q1", "q2"))).identical is False


def test_a_link_to_an_element_the_model_does_not_keep_is_still_told_by_its_record():
    # Between models an element a typed field read has no place: the record that holds
    # it is all there is to compare, whatever the id (unchanged behaviour).
    def model(href: str):
        link = Link(source="f1", relation="referencePoint", target="f1", href=href, resolved=True)
        return _holder(_geom("p1", "1 2"), links=(link,))

    assert diff.compare(model("#opl"), model("#x9")).identical


def test_a_retargeted_link_inside_one_record_shows_what_it_named():
    def model(href: str, second: str = "p2"):
        link = Link(source="f1", relation="usedPoint", target="f1", href=href, resolved=True)
        return _holder(_geom("p1", "1 2"), _geom(second, "3 4"), links=(link,))

    result = diff.compare(model("#p1"), model("#p2"))
    (value,) = result.changes[0].values
    assert (value.path, value.old, value.new) == ("links.usedPoint", ["B-1 #p1"], ["B-1 #p2"])
    assert "B-1 #p1" in result.to_text() and "B-1 #p2" in result.to_text()
    # An id is file text like any other: cut at 200 characters, and no markup gets through.
    hostile = "p2[bold]" + "x" * 500
    long = diff.compare(model("#p1"), model(f"#{hostile}", hostile))
    (value,) = long.changes[0].values
    assert value.new[0] == f"B-1 #{hostile}"[:200] and value.truncated
    assert "p2\\[bold]" in long.to_text() and "p2[bold]" not in long.to_text()
    assert schema_errors(long.to_dict()) == []
