"""Ids are not values: a re-export that gives every ``gml:id`` a new value is the same data.

The ``gml:id`` of an element inside ``extra`` is not compared, and a local reference
(``xlink:href="#x"``, ``srsName="#x"``) is compared by what it points to. A reference that
cannot be resolved for certain stays text, so a link that points at another thing is never
hidden.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pydiggs import diff
from pydiggs.cli import main
from pydiggs.model import CrsRef, ExtraNode, Interval, Link

from ._helpers import OFFICIAL, depth, make_feature, make_model, make_sample

AEROMAG = OFFICIAL / "3.0" / "Aeromag.xml"
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
def test_what_still_differs_after_reissuing_every_id_is_only_what_cannot_be_resolved(path: Path):
    """The two things a reissued file can still show, and nothing else.

    A reference to an element a typed field consumed (a borehole's centre line, named by
    its linear referencing system) has no place in the model, so it stays text. And nodes
    that only their ``gml:id`` told apart share a natural key, which is never guessed at.
    """
    text = path.read_text("utf-8")
    result = diff.compare(text.encode(), reissue_ids(text).encode())
    ambiguous = any(n.rule_id == "diff.ambiguous-match" for n in result.notes)
    for change in result.changes:
        if change.op == "changed":
            assert {v.path.rpartition(".")[2] for v in change.values} == {
                "linearElement[1]@xlink:href"
            }
        else:
            assert ambiguous and change.op in ("added", "removed")
    assert result.identical is (not result.changes)
    assert path.name != "Aeromag.xml" or result.identical


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
