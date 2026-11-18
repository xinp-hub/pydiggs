"""What a file holds and its model does not is compared too, when two files are compared.

A typed field reads part of an element (a position out of a point location, the cells
out of a result set) and the model keeps that part. The rest of the element (its unit
labels, the geometry of a layer, a grid, the files of a data package) is in no model.
Comparing two files compares it as part of the record, so that no edit to either file
goes unseen; comparing two models compares the models.
"""

from __future__ import annotations

import itertools
import os
import random
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from pydiggs import diff, read_bytes
from pydiggs._reader import _read, read_with_places
from pydiggs.cli import main
from pydiggs.diff import _compare, _engine, _load, _options
from pydiggs.errors import DocumentError

from ._helpers import OFFICIAL, corpus_paths, schema_errors

V3 = OFFICIAL / "3.0"
MWD = V3 / "MWDExample.xml"
ANNOTATED = V3 / "Borehole-CPT_Example_Annotated.xml"
TRENCH = V3 / "TrenchWallExample.xml"
SURVEY = V3 / "GeophysicalFieldSurveyExamples.xml"
AEROMAG = V3 / "Aeromag.xml"
GRADING = OFFICIAL / "2.6" / "LithoGrading-from25.xml"


def _values(result: diff.Diff) -> list[tuple[str, Any, Any]]:
    return [(value.path, value.old, value.new) for c in result.changes for value in c.values]


def _edited(path: Path, old: str, new: str) -> tuple[bytes, bytes]:
    data = path.read_bytes()
    changed = data.replace(old.encode(), new.encode())
    assert changed != data, old
    return data, changed


# --- the edits the review found invisible -------------------------------------------------

INVISIBLE = [
    (MWD, 'uomLabels="deg deg m"', 'uomLabels="deg deg ft"', "PointLocation[1]@uomLabels"),
    (ANNOTATED, 'uomLabels="dega dega ft"', 'uomLabels="dega dega m"', "@uomLabels"),
    (TRENCH, "0.2272 1.0762", "9.2272 5.0762", "posList[1]#text"),
    (SURVEY, "./res1-df.zip", "./other.zip", "GP_FieldDataFile[1].fileURL[1]#text"),
    (SURVEY, " 230 270 ", " 999 270 ", "MultiPointLocation[1].posList[1]#text"),
    (AEROMAG, "380300 3745100", "1 2", "RectifiedGrid[1].origin[1].PointLocation[1].pos[1]#text"),
    (MWD, ">Penetration rate<", ">Advance rate<", "propertyClass[1]#text"),
    (MWD, "<typeData>double</typeData>", "<typeData>float</typeData>", "typeData[1]#text"),
    (MWD, 'index="2"', 'index="12"', "]@index"),
    (MWD, 'reason="missing"', 'reason="not measured"', "nullValue[1]@reason"),
    (ANNOTATED, 'decimal="."', 'decimal=","', "dataValues[1]@decimal"),
    (ANNOTATED, 'srsDimension="3"', 'srsDimension="2"', "@srsDimension"),
]
IDS = [
    "mwd-unit-labels",
    "cpt-unit-labels",
    "layer-geometry",
    "data-package-file",
    "receiver-locations",
    "grid-origin",
    "property-class",
    "type-data",
    "property-index",
    "null-value-reason",
    "decimal-mark",
    "srs-dimension",
]


@pytest.mark.parametrize("case", INVISIBLE, ids=IDS)
def test_an_edit_to_content_the_model_does_not_keep_is_a_difference(case, tmp_path, capsys):
    path, old, new, named = case
    before, after = _edited(path, old, new)
    result = diff.compare(before, after)
    assert result.complete and result.identical is False and result.value_changes >= 1
    paths = [value[0] for value in _values(result)]
    assert any(p.startswith("xml.") and p.endswith(named) for p in paths), paths
    assert schema_errors(result.to_dict()) == []
    target = tmp_path / "edited.xml"
    target.write_bytes(after)
    assert main(["diff", str(path), str(target)]) == 1
    assert named.rpartition(".")[2] in capsys.readouterr().out


def test_the_report_names_what_changed_and_shows_both_values():
    result = diff.compare(*_edited(MWD, 'uomLabels="deg deg m"', 'uomLabels="deg deg ft"'))
    (change,) = result.changes
    assert (change.collection, change.name) == ("features", "TEST HOLE 1")
    assert _values(result) == [
        ("xml.Borehole[1].centerLine[1].LinearExtent[1]@uomLabels", "deg deg m", "deg deg ft"),
        ("xml.Borehole[1].referencePoint[1].PointLocation[1]@uomLabels", "deg deg m", "deg deg ft"),
    ]
    assert "@uomLabels: deg deg m -> deg deg ft" in result.to_text()


def test_the_character_of_a_colour_is_compared():
    marker = '<Color gml:id="DGS3125-1975-2EE8-E666-A1AF"'
    data = GRADING.read_bytes()
    assert data.count(marker.encode()) == 1
    old = data.replace(marker.encode(), f'{marker} character="primary"'.encode())
    new = data.replace(marker.encode(), f'{marker} character="mottle"'.encode())
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert result.identical is False
    assert [(p.rpartition(".")[2], a, b) for p, a, b in _values(result)] == [
        ("Color[1]@character", "primary", "mottle")
    ]
    # An attribute only one file has is one too.
    assert diff.compare(data, new).identical is False


# --- result values no column holds --------------------------------------------------------

ROWS = "0.0,0.5,false,0,0,0,0,0,0"


def _mwd_rows(*rows: str) -> bytes:
    """The MWD file with its nine-column result set holding ``rows``."""
    text = MWD.read_text("utf-8")
    start = text.index(">", text.index("<dataValues")) + 1
    end = text.index("</dataValues>")
    return (text[:start] + " ".join(rows) + text[end:]).encode()


def _cells(result: diff.Diff) -> list[tuple[str, Any, Any]]:
    return [(p.partition("dataValues[1].")[2], a, b) for p, a, b in _values(result)]


def test_two_different_values_that_are_not_numbers_are_not_the_same_null():
    old = _mwd_rows(ROWS, "1.0,abc,true,0,0,0,0,0,0")
    new = _mwd_rows(ROWS, "1.0,xyz,true,0,0,0,0,0,0")
    # The model reads both as null: two models are equal, the two files are not.
    assert read_bytes(old).measurements[0].results == read_bytes(new).measurements[0].results
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert result.identical is False
    assert _cells(result) == [("value[1]#text", "abc", "xyz")]
    # Where the value stands is part of it: the same text in another row is another value.
    moved = _mwd_rows("0.0,abc,false,0,0,0,0,0,0", "1.0,0.5,true,0,0,0,0,0,0")
    assert diff.compare(old, moved).identical is False


def test_a_boolean_column_keeps_what_it_could_not_read():
    old = _mwd_rows(ROWS.replace("false", "yes"))
    new = _mwd_rows(ROWS.replace("false", "no"))
    assert _cells(diff.compare(old, new)) == [("value[1]#text", "yes", "no")]


def test_values_beyond_the_declared_columns_count():
    old, new = _mwd_rows(f"{ROWS},7,8"), _mwd_rows(f"{ROWS},7,9")
    assert read_bytes(old).measurements[0].results == read_bytes(new).measurements[0].results
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert result.identical is False
    assert _cells(result) == [("value[2]#text", "8", "9")]
    assert diff.compare(_mwd_rows(ROWS), old).identical is False
    # They are numbers like any other: the tolerance holds for them too.
    assert diff.compare(old, _mwd_rows(f"{ROWS},7.0,8.000")).identical
    assert diff.compare(old, new, abs_tol=1.5).identical


def test_a_table_the_columns_hold_in_full_leaves_nothing_behind():
    data = _mwd_rows(ROWS, "1.0,0.5,true,0,0,0,0,0,0")
    unread = read_with_places(data)[4]
    texts = [node.qname for nodes in unread.values() for root in nodes for node in _walk(root)]
    assert "dataValues" in texts and "value" not in texts  # its separators, not its values


def _walk(node: Any) -> Iterator[Any]:
    yield node
    for child in node.children:
        yield from _walk(child)


# --- what a typed field holds is compared once, by its field ----------------------------


def test_a_value_a_typed_field_holds_is_listed_once_at_its_field():
    old, new = _edited(
        ANNOTATED,
        '<totalMeasuredDepth uom="ft">160.00<',
        '<totalMeasuredDepth uom="ft">161.00<',
    )
    assert _values(diff.compare(old, new)) == [("total_depth", "160.00 ft", "161.00 ft")]
    old, new = _edited(MWD, "<typeData>boolean</typeData>", "<typeData>string</typeData>")
    assert {p.rpartition(".")[2] for p, _, _ in _values(diff.compare(old, new))} >= {"type"}


def test_the_tolerance_and_number_formatting_still_hold_for_typed_values():
    # A coordinate of a centre line, a depth and a cell: each read by a typed field.
    for path, old, new in (
        (ANNOTATED, "19.00 -91.212861 30.429139 -141", "19.000 -91.212861 30.429139 -141.004"),
        (
            ANNOTATED,
            '<totalMeasuredDepth uom="ft">160.00<',
            '<totalMeasuredDepth uom="ft">160.004<',
        ),
        (MWD, "<gml:posList>0 1.83</gml:posList>", "<gml:posList>0.000 1.834</gml:posList>"),
    ):
        before, after = _edited(path, old, new)
        assert diff.compare(before, after).identical is False
        assert diff.compare(before, after, abs_tol=0.005).identical, old
    cells = (_mwd_rows(ROWS), _mwd_rows(ROWS.replace("0.5", "0.5004")))
    assert diff.compare(*cells).identical is False
    assert diff.compare(*cells, abs_tol=0.005).identical
    assert diff.compare(*cells, rel_tol=0.001).identical
    written = diff.compare(_mwd_rows(ROWS), _mwd_rows(ROWS.replace("0.5", "0.50")))
    assert written.identical


def test_numbers_in_content_the_model_does_not_keep_are_compared_as_numbers():
    before, moved = _edited(TRENCH, "0.2272 1.0762", "0.2274 1.0762")
    assert diff.compare(before, moved).identical is False
    assert diff.compare(before, moved, abs_tol=0.001).identical
    assert diff.compare(before, moved, rel_tol=0.01).identical
    # The same numbers written another way, and spaced another way.
    _, written = _edited(TRENCH, "0.2272 1.0762", "0.22720   1.0762000")
    result = diff.compare(before, written)
    assert result.identical and result.reformatted >= 1 and result.changes == ()
    listed = diff.compare(before, written, show_reformatted=True)
    assert {value.op for change in listed.changes for value in change.values} == {"reformatted"}
    # A word that is not a number is compared as it is, and one more word is a change.
    assert diff.compare(*_edited(TRENCH, "0.2272 1.0762", "0.2272 n/a")).identical is False
    assert diff.compare(*_edited(TRENCH, "0.2272 1.0762", "0.2272 1.0762 0")).identical is False


# --- what is deliberately not a difference ------------------------------------------------


def test_what_says_nothing_about_the_data_is_not_a_difference():
    text = ANNOTATED.read_text("utf-8")
    same = [
        text.replace("\n\t", "\n    "),  # the indentation
        text.replace("</Diggs>", "<!-- checked -->\n</Diggs>"),  # a comment
        text.replace("Diggs.xsd", "Other.xsd", 1),  # where the schema is
        text.replace("<gml:posList>", "<gml:posList>  ").replace(
            "</gml:posList>", "\n</gml:posList>"
        ),
        text.replace('uom="ft">160.00<', 'uom="ft">160.0<'),  # the same number
    ]
    for other in same:
        assert other != text
        result = diff.compare(text.encode(), other.encode())
        assert result.identical and result.complete, _values(result)[:3]


def test_a_namespace_prefix_is_not_a_difference_in_what_the_model_does_not_keep():
    text = MWD.read_text("utf-8")
    other = text.replace("gml:pos", "g:pos").replace("xmlns:gml=", "xmlns:g=").replace("gml:", "g:")
    assert other != text and "g:posList" in other
    assert diff.compare(text.encode(), other.encode()).identical


@pytest.mark.parametrize("path", corpus_paths(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_official_file_is_identical_to_itself(path):
    data = path.read_bytes()
    try:
        result = diff.compare(data, data)
    except DocumentError:
        pytest.skip("not readable DIGGS")
    assert result.identical and result.complete and result.changes == ()


# --- models -------------------------------------------------------------------------------


def test_two_models_are_compared_as_models_and_hold_none_of_it():
    before, after = _edited(MWD, 'uomLabels="deg deg m"', 'uomLabels="deg deg ft"')
    old, new = read_bytes(before), read_bytes(after)
    assert diff.compare(before, after).identical is False
    # The two models are equal, and that is what comparing them says.
    assert dataclass_equal(old, new)
    for a, b in ((old, new), (old, after), (before, new)):
        result = diff.compare(a, b)
        assert result.identical and result.complete and result.changes == ()


def dataclass_equal(old: Any, new: Any) -> bool:
    return old.features == new.features and old.measurements == new.measurements


# --- limits -------------------------------------------------------------------------------


def _many_unread(count: int) -> bytes:
    text = MWD.read_text("utf-8")
    inside = "".join(f'<gml:pos note="n{i}">1 2 3</gml:pos>' for i in range(count))
    head, mark, tail = text.partition("</PointLocation>")
    assert mark
    return f"{head}{inside}{mark}{tail}".encode()


def test_more_unread_content_than_the_work_limit_is_an_incomplete_comparison(monkeypatch):
    data = _many_unread(2000)
    assert diff.compare(data, data).identical
    monkeypatch.setattr(_read, "LIMIT_BASE", 0)
    model, _, _, _, unread, _ = read_with_places(data)
    assert model.complete and "" in unread  # the model is whole; what it lacks was cut
    kept = sum(1 for nodes in unread.values() for root in nodes for _ in _walk(root))
    assert kept <= len(data) // _read.LIMIT_BYTES_PER_ITEM
    result = diff.compare(data, data)
    assert (result.identical, result.complete) == (False, False)
    assert result.limits_hit == ("old_read_incomplete", "new_read_incomplete")
    assert all("outside the model" in note.message for note in result.notes)
    assert schema_errors(result.to_dict()) == []
    one = diff.compare(read_bytes(data), read_bytes(data))
    assert one.identical  # two models: nothing was cut, because nothing is there


def test_the_time_budget_bounds_reading_what_the_model_lacks():
    data = _many_unread(20000)
    result = diff.compare(data, data, time_budget=1e-6)
    assert (result.identical, result.complete) == (False, False)


def test_deep_and_long_unread_content_keeps_paths_and_memory_bounded():
    name = "n" * 190
    inside = f"<{name}>" * 70 + '<leaf v="1">7</leaf>' + f"</{name}>" * 70
    head, mark, tail = MWD.read_text("utf-8").partition("</PointLocation>")
    old = f"{head}{inside}{mark}{tail}".encode()
    new = old.replace(b'<leaf v="1">7<', b'<leaf v="2">8<')
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert result.identical is False and result.complete
    paths = [value[0] for value in _values(result)]
    assert paths and max(map(len, paths)) <= 1000
    assert schema_errors(result.to_dict()) == []


# --- a reference that reads the same and names another thing ----------------------------


def test_a_reference_that_kept_its_text_says_that_it_names_another_target():
    text = MWD.read_text("utf-8")
    swapped = text.replace('gml:id="pl_bh_TEST_HOLE_1"', 'gml:id="KEEP"')
    swapped = swapped.replace('gml:id="cl_bh_TEST_HOLE_1"', 'gml:id="pl_bh_TEST_HOLE_1"')
    swapped = swapped.replace('gml:id="KEEP"', 'gml:id="cl_bh_TEST_HOLE_1"')
    result = diff.compare(text.encode(), swapped.encode())
    shown = {value[0].rpartition(".")[2]: value[1:] for value in _values(result)}
    assert shown["linearElement[1]@xlink:href"] == (
        "#cl_bh_TEST_HOLE_1",
        "#cl_bh_TEST_HOLE_1 (another target)",
    )
    assert shown["linearElement"] == (
        ["TEST HOLE 1 #cl_bh_TEST_HOLE_1"],
        ["TEST HOLE 1 #cl_bh_TEST_HOLE_1 (another target)"],
    )
    assert "#cl_bh_TEST_HOLE_1 -> #cl_bh_TEST_HOLE_1 (another target)" in result.to_text()
    assert schema_errors(result.to_dict()) == []
    long = "p" * 400
    both = [t.replace("cl_bh_TEST_HOLE_1", long) for t in (text, swapped)]
    values = _values(diff.compare(both[0].encode(), both[1].encode()))
    assert all(
        len(v) <= 200 for _, _, new in values for v in ([new] if isinstance(new, str) else new)
    )
    assert any(str(new).endswith("(another target)") for _, _, new in values)


# --- no single edit goes unseen -----------------------------------------------------------
#
# One edit at a time is made to a file, and the edited file is compared with the file:
# no such edit may compare identical. The fast suite makes every edit of every kind to
# the three smallest files and a fixed sample of each kind to the larger ones. The tests
# marked ``slow`` make a larger fixed sample to the larger files (under two minutes on a
# CI runner). ``PYDIGGS_FUZZ_ALL=1`` makes them try every site of every file instead,
# which takes about half an hour: run it when the reader or the comparison changes.

GML_ID = "{http://www.opengis.net/gml/3.2}id"
#: Edits that say nothing about the data, by the attribute they are made to: where the
#: schema of the file is to be found. Nothing else is let through.
ALLOWED = frozenset({"{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"})
EVERY_SITE = os.environ.get("PYDIGGS_FUZZ_ALL") == "1"
SMALL = [MWD, AEROMAG, TRENCH]
LARGER = [SURVEY, ANNOTATED, V3 / "PorePressureDissipation_Example.xml", GRADING]
#: The kinds of edit: a text or an attribute value, the name of an element, text after
#: an element, an attribute exchanged between two like siblings, and one more separator
#: at the end of a result row.
KINDS = ("value", "name", "tail", "swap", "separator")
OPTIONS = _options(
    match="auto",
    rel_tol=1e-9,
    abs_tol=0.0,
    max_changes=10_000,
    time_budget=None,
    max_bytes=10**9,
    show_reformatted=False,
    show_renamed=False,
)

#: An edit: what it is made to (for the message), how to make it and how to take it back.
Edit = tuple[str, Callable[[], None], Callable[[], None]]


def _where(el: Any, name: str | None) -> str:
    steps = [etree.QName(e).localname for e in (el, *el.iterancestors())][:4]
    return "/".join(reversed(steps)) + ("" if name is None else f"/@{name}")


def _setter(el: Any, field: str, value: Any) -> Callable[[], None]:
    def apply() -> None:
        if field in ("text", "tail", "tag"):
            setattr(el, field, value)
        else:
            el.set(field, value)

    return apply


def _both(*steps: Callable[[], None]) -> Callable[[], None]:
    return lambda: [step() for step in steps] and None


def _value_edits(root: Any) -> Iterator[Edit]:
    """A ``9`` before every text and after every attribute value that is not an id."""
    for el in root.iter(etree.Element):
        if el.text and el.text.strip():
            text = el.text
            yield (
                _where(el, None),
                _setter(el, "text", f"9{text.strip()}"),
                _setter(el, "text", text),
            )
        for name, value in el.attrib.items():
            if name != GML_ID and name not in ALLOWED:
                yield _where(el, name), _setter(el, name, f"{value}9"), _setter(el, name, value)


def _name_edits(root: Any) -> Iterator[Edit]:
    """Another name (an ``X`` after it) for every element below the root."""
    for el in root.iter(etree.Element):
        if el is not root:
            yield (
                f"{_where(el, None)} renamed",
                _setter(el, "tag", f"{el.tag}X"),
                _setter(el, "tag", el.tag),
            )


def _tail_edits(root: Any) -> Iterator[Edit]:
    """Text after every element inside a member (after a member is outside every record)."""
    for el in root.iter(etree.Element):
        parent = el.getparent()
        if parent is not None and parent is not root:
            yield f"{_where(el, None)} tail", _setter(el, "tail", "9"), _setter(el, "tail", el.tail)


def _swap_edits(root: Any) -> Iterator[Edit]:
    """One attribute exchanged between two same-named siblings that have it differently."""
    for parent in root.iter(etree.Element):
        children = [c for c in parent if isinstance(c.tag, str)]
        for one, two in itertools.pairwise(children):
            if one.tag != two.tag:
                continue
            for name in one.attrib:
                a, b = one.get(name), two.get(name)
                if (
                    name != GML_ID
                    and b is not None
                    and a != b
                    and _shape(one, name) != _shape(two, name)
                ):
                    swap = _both(_setter(one, name, b), _setter(two, name, a))
                    back = _both(_setter(one, name, a), _setter(two, name, b))
                    yield f"{_where(one, name)} swapped", swap, back


def _shape(el: Any, but: str) -> tuple[Any, ...]:
    """What an element is apart from one attribute and its id.

    Two siblings alike in all of it that exchange the attribute have only changed places.
    """
    attrs = sorted((k, v) for k, v in el.attrib.items() if k not in (but, GML_ID))
    return ((el.text or "").strip(), attrs, [etree.tostring(child) for child in el])


def _separator_edits(root: Any) -> Iterator[Edit]:
    """One more separator at the end of the last row of every result set."""
    for el in root.iter(etree.Element):
        if etree.QName(el).localname == "dataValues" and el.text and el.text.strip():
            more = f"{el.text.rstrip()}{el.get('cs', ',')}"
            yield (
                f"{_where(el, None)} separator",
                _setter(el, "text", more),
                _setter(el, "text", el.text),
            )


EDITS = dict(
    zip(KINDS, (_value_edits, _name_edits, _tail_edits, _swap_edits, _separator_edits), strict=True)
)


def _unseen(path: Path, kind: str, sample: int | None = None) -> tuple[int, list[str]]:
    """Make the edits of one kind to ``path``, one at a time; those that compare identical."""
    data = path.read_bytes()
    old = _load(data, "old", 10**9, None)
    root = etree.fromstring(data)
    edits = list(EDITS[kind](root))
    if sample is not None and sample < len(edits):
        edits = random.Random(20261117).sample(edits, sample)  # noqa: S311 - a fixed sample
    missed = []
    for where, apply, undo in edits:
        apply()
        new = _load(etree.tostring(root), "new", 10**9, None)
        undo()
        seen = (old[1], new[1])
        assert seen[0] is not None and seen[1] is not None
        if _engine.compare_models(old[0], new[0], OPTIONS, seen=seen).diff.identical:
            missed.append(where)
    return len(edits), missed


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("path", SMALL, ids=lambda p: p.name)
def test_no_edit_of_a_small_file_compares_identical(path, kind):
    _, missed = _unseen(path, kind)
    assert missed == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("path", LARGER, ids=lambda p: p.name)
def test_no_sampled_edit_of_a_larger_file_compares_identical(path, kind):
    _, missed = _unseen(path, kind, sample=3)
    assert missed == []


@pytest.mark.slow
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("path", LARGER, ids=lambda p: p.name)
def test_no_edit_of_a_larger_file_compares_identical(path, kind):
    _, missed = _unseen(path, kind, sample=None if EVERY_SITE else 10)
    assert missed == []


def test_the_edits_cover_every_kind_in_the_small_files():
    counts = dict.fromkeys(KINDS, 0)
    for path in SMALL:
        root = etree.fromstring(path.read_bytes())
        for kind in KINDS:
            counts[kind] += sum(1 for _ in EDITS[kind](root))
    assert all(counts.values()), counts
    assert counts["value"] > 300 and counts["name"] > 300


def test_an_element_typed_fields_hold_in_full_is_still_an_element_of_the_file():
    text = MWD.read_text("utf-8")
    old = text.encode()
    # Renamed: the member property of the borehole, and the remark of a record.
    renamed = text.replace("<samplingFeature>", "<measurement>", 1)
    renamed = renamed.replace("</samplingFeature>", "</measurement>", 1)
    assert renamed != text
    assert diff.compare(old, renamed.encode()).identical is False
    # Added: a second name is a value; a second, empty description is only an element.
    added = text.replace(
        "<gml:name>TEST HOLE 1</gml:name>", "<gml:name>TEST HOLE 1</gml:name><gml:description/>"
    )
    assert added != text
    result = diff.compare(old, added.encode())
    assert result.identical is False and result.complete
    assert diff.compare(old, old).identical
    # The same elements in another order are the same elements.
    _, _, _, _, _, names = read_with_places(old)
    assert names and all(count > 0 for counts in names.values() for count in counts.values())


def test_the_one_edit_that_is_let_through_is_where_the_schema_is():
    root = etree.fromstring(MWD.read_bytes())
    assert sorted(ALLOWED) == [name for name in root.attrib if name in ALLOWED]


# --- what is left of an element stays with what a typed field holds of it ---------------


def _names(first: str, second: str) -> bytes:
    text = MWD.read_text("utf-8")
    one = "<gml:name>TEST HOLE 1</gml:name>"
    assert text.count(one) == 1
    two = f'<gml:name codeSpace="{first}">A</gml:name><gml:name codeSpace="{second}">B</gml:name>'
    return text.replace(one, f"{one}{two}").encode()


def test_two_names_that_exchange_their_code_space_are_a_difference():
    old, new = _names("urn:x", "urn:y"), _names("urn:y", "urn:x")
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert result.identical is False
    assert sorted((p.rpartition(".")[2], a, b) for p, a, b in _values(result)) == [
        ("name[1]@codeSpace", "urn:x", "urn:y"),
        ("name[2]@codeSpace", "urn:y", "urn:x"),
    ]
    # The names in another order, each with its own code space, are the same names.
    turned = old.replace(b'<gml:name codeSpace="urn:x">A</gml:name>', b"")
    turned = turned.replace(b"</gml:name><gml:name", b"</gml:name>\n<gml:name")
    turned = turned.replace(
        b'<gml:name codeSpace="urn:y">B</gml:name>',
        b'<gml:name codeSpace="urn:y">B</gml:name><gml:name codeSpace="urn:x">A</gml:name>',
    )
    assert turned != old
    assert [p for p, _, _ in _values(diff.compare(old, turned)) if p.startswith("xml.")] == []


def test_two_columns_that_exchange_their_declared_type_are_a_difference():
    text = MWD.read_text("utf-8").replace(' index="1"', "").replace(' index="2"', "")
    old = text.replace("<typeData>double</typeData>", "<typeData>float</typeData>", 1)
    new = text.replace("<typeData>double</typeData>", "<typeData>float</typeData>", 2)
    new = new.replace("<typeData>float</typeData>", "<typeData>double</typeData>", 1)
    assert old != new and old.count("float") == new.count("float") == 1
    assert diff.compare(old.encode(), old.encode()).identical
    result = diff.compare(old.encode(), new.encode())
    assert result.identical is False
    assert {p.rpartition(".")[2] for p, _, _ in _values(result)} == {"typeData[1]#text"}


def test_the_mark_of_what_a_field_holds_is_never_listed_and_is_the_readers():
    assert (_compare.HELD, _compare.TAIL) == (_read.HELD, _read.TAIL)
    result = diff.compare(*_edited(MWD, "<gml:name>LIM</gml:name>", "<gml:name>LIN</gml:name>"))
    assert [p for p, _, _ in _values(result)] == ["name", "names"]
    assert "held" not in result.to_json()


# --- text after a comment or a child element ------------------------------------------------


def test_text_after_a_comment_is_compared():
    one = "<gml:name>TEST HOLE 1</gml:name>"
    text = MWD.read_text("utf-8")
    old = text.replace(one, "<gml:name>B<!--note-->01</gml:name>").encode()
    new = text.replace(one, "<gml:name>B<!--note-->02</gml:name>").encode()
    assert read_bytes(old).features[0].name == read_bytes(new).features[0].name == "B"
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert result.identical is False
    assert _values(result) == [("xml.Borehole[1].name[1]#tail", "01", "02")]
    # The comment itself is not data.
    other = old.replace(b"<!--note-->", b"<!--another note-->")
    assert other != old and diff.compare(old, other).identical


def test_text_after_a_child_is_compared_in_an_element_extra_keeps_too():
    text = MWD.read_text("utf-8")
    inside = '<x:a xmlns:x="urn:x"><x:b>1</x:b>{}</x:a>\n    </Borehole>'
    old = text.replace("</Borehole>", inside.format("after"), 1).encode()
    new = text.replace("</Borehole>", inside.format("later"), 1).encode()
    assert read_bytes(old).features[0].extra == read_bytes(new).features[0].extra
    assert diff.compare(old, old).identical
    result = diff.compare(old, new)
    assert [(p.rpartition(".")[2], a, b) for p, a, b in _values(result)] == [
        ("a[1]#tail", "after", "later")
    ]


# --- one more separator ---------------------------------------------------------------------


def test_a_separator_after_the_last_value_of_a_row_is_one_more_value():
    old, new = _mwd_rows(ROWS), _mwd_rows(f"{ROWS},")
    assert read_bytes(old).measurements[0].results == read_bytes(new).measurements[0].results
    assert diff.compare(new, new).identical
    result = diff.compare(old, new)
    assert result.identical is False
    assert [
        (p.partition("dataValues[1].")[2], op)
        for (p, _, _), op in zip(_values(result), _ops(result), strict=True)
    ][-1][1] == "added"
    assert any("value[1]" in p for p, _, _ in _values(result))


def _ops(result: diff.Diff) -> list[str]:
    return [value.op for change in result.changes for value in change.values]


# --- a long text and the time budget ----------------------------------------------------------


def test_a_long_text_is_compared_within_the_time_budget(monkeypatch):
    words = 200_000
    old = _compare.Tokens(" ".join(f"{i}.0" for i in range(words)))
    new = _compare.Tokens(" ".join(f"{i}.00" for i in range(words)))
    ticks: list[int] = []
    assert _compare._same_leaf(old, new, _compare.Tol(), ticks.append) == "reformatted"
    assert sum(ticks) >= words - _compare.CHUNK and max(ticks) <= _compare.CHUNK

    class StopError(Exception):
        pass

    def stop(units: int) -> None:
        raise StopError

    with pytest.raises(StopError):
        _compare._same_leaf(old, new, _compare.Tol(), stop)
    # One more word, a word that is no number, and the same text.
    assert old.same(_compare.Tokens(f"{old.text} 1"), _compare.Tol()) == "changed"
    assert old.same(_compare.Tokens(old.text.replace("7.0", "x", 1)), _compare.Tol()) == "changed"
    assert old.same(_compare.Tokens(old.text), _compare.Tol()) is None
    # Through a whole comparison: a long list of coordinates stops at the budget.
    head, mark, tail = MWD.read_text("utf-8").partition("</PointLocation>")
    before = f"{head}<gml:posList>{old.text}</gml:posList>{mark}{tail}".encode()
    after = f"{head}<gml:posList>{new.text}</gml:posList>{mark}{tail}".encode()
    assert diff.compare(before, after).identical
    clock = iter(itertools.count())
    monkeypatch.setattr(_engine, "_now", lambda: float(next(clock)))
    cut = _engine.compare_models(
        _load(before, "old", 10**9, None)[0],
        _load(after, "new", 10**9, None)[0],
        OPTIONS,
        deadline=300.0,
        seen=(_load(before, "old", 10**9, None)[1], _load(after, "new", 10**9, None)[1]),  # type: ignore[arg-type]
    ).diff
    assert cut.complete is False and "time_budget" in cut.limits_hit
