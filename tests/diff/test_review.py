"""The findings of the review before diggs-diff 1.0 was first released, each with its scenario.

A comparison that could not look at something is incomplete, never identical; a report
is bounded whatever the file holds; a reference that names another element is never the
same reference; the report always reaches its reader; a document read back is one the
renderer can print.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import subprocess
import sys
import tracemalloc
from typing import Any

import pytest

from pydiggs import diff, read_bytes
from pydiggs._reader import read_with_places
from pydiggs.cli import main
from pydiggs.diff import _cli, _compare
from pydiggs.errors import DocumentError
from pydiggs.model import ResultTable

from ._helpers import (
    ANNOTATED,
    ROOT,
    column,
    make_feature,
    make_model,
    make_test,
    schema_errors,
    table,
)

TEXT = ANNOTATED.read_text("utf-8")
#: The root start tag of the fixture, up to its first comment.
HEAD = TEXT[: TEXT.index("<!--")]


def _member(start: str, end: str) -> str:
    """The first member of the fixture that starts with ``start``, to its ``end`` tag."""
    at = TEXT.index(start)
    return TEXT[at : TEXT.index(end, at) + len(end)] + "\n"


BOREHOLE = _member(
    '\t<samplingFeature>\n\t\t<Borehole gml:id="Location_B-01">', "</samplingFeature>"
)
ACTIVITY = _member("\t<samplingActivity>", "</samplingActivity>")
#: The borehole with nothing in it that refers to its own depth system.
BARE = BOREHOLE[: BOREHOLE.index("\t\t\t<waterStrike>")] + "\t\t</Borehole>\n\t</samplingFeature>\n"


def _doc(*members: str) -> bytes:
    return f"{HEAD}{''.join(members)}</Diggs>\n".encode()


def _paths(result: diff.Diff) -> list[str]:
    return [value.path for change in result.changes for value in change.values]


def _edit(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


# --- F2: result values that were not read ---------------------------------------------


def _one_cell_edited() -> bytes:
    at = TEXT.index("<dataValues")
    start = TEXT.index(">", at) + 1
    end = TEXT.index("</dataValues>", start)
    return (TEXT[:start] + "9" + TEXT[start:end] + TEXT[end:]).encode()


def test_models_read_without_results_are_never_identical_when_tables_have_columns():
    old, new = ANNOTATED.read_bytes(), _one_cell_edited()
    assert diff.compare(old, new).identical is False  # the files differ in one cell
    result = diff.compare(read_bytes(old, results=False), read_bytes(new, results=False))
    assert result.complete is False and result.identical is False
    assert result.limits_hit == ("old_results_not_read", "new_results_not_read")
    notes = [note for note in result.notes if note.rule_id == "diff.limit"]
    assert len(notes) == 2 and all("results=False" in note.message for note in notes)
    assert schema_errors(result.to_dict()) == []
    # The same file is not called identical either: nothing looked at its cells.
    same = diff.compare(read_bytes(old, results=False), read_bytes(old, results=False))
    assert (same.identical, same.complete, same.changes) == (False, False, ())
    assert "incomplete" in same.to_text()


def test_one_side_read_without_results_is_incomplete_and_names_that_side():
    data = ANNOTATED.read_bytes()
    result = diff.compare(read_bytes(data), read_bytes(data, results=False))
    assert result.limits_hit == ("new_results_not_read",)
    assert result.complete is False and result.identical is False
    other = diff.compare(read_bytes(data, results=False), data)
    assert other.limits_hit == ("old_results_not_read",)


def test_a_model_without_tables_or_with_empty_tables_is_still_complete():
    empty = table(column("n"))  # a column and no rows: there is nothing that was not read
    model = make_model(
        features=(make_feature("f1", "B-1"),),
        measurements=(make_test("t1", "T", "f1", results=(empty, ResultTable())),),
    )
    result = diff.compare(model, model)
    assert (result.identical, result.complete, result.limits_hit) == (True, True, ())


# --- F3: the length of a path ----------------------------------------------------------


def _nested(leaves: int, *, levels: int = 55, width: int = 190, mark: str = "") -> str:
    """``levels`` nested elements with ``width``-character names around ``leaves`` leaves."""
    names = [
        f"n{level:02d}{mark if level == levels // 2 else ''}".ljust(width, "x")
        for level in range(levels)
    ]
    body = "".join(f"<a>{i}</a>" for i in range(leaves))
    return (
        "".join(f"<{name}>" for name in names)
        + body
        + "".join(f"</{name}>" for name in reversed(names))
    )


def _with_extra(inside: str) -> bytes:
    return _doc(_edit(BOREHOLE, "\t\t</Borehole>", f"{inside}\t\t</Borehole>"))


@pytest.mark.parametrize("leaves", [150, 300])
def test_a_deep_extra_with_long_names_gives_a_bounded_report(leaves):
    result = diff.compare(_doc(BOREHOLE), _with_extra(_nested(leaves)))
    paths = _paths(result)
    assert result.complete and len(paths) >= 2 * leaves
    assert len(set(paths)) == len(paths)
    assert max(map(len, paths)) <= _compare.MAX_PATH
    assert schema_errors(result.to_dict()) == []
    # What one listed value costs does not grow with the depth of the element it is in.
    assert len(result.to_json()) / len(paths) < 2 * _compare.MAX_PATH
    assert len(result.to_text()) / len(paths) < 2 * _compare.MAX_PATH


def test_the_report_grows_with_the_changes_listed_and_not_with_their_depth():
    sizes = [
        len(diff.compare(_doc(BOREHOLE), _with_extra(_nested(n))).to_json()) for n in (150, 300)
    ]
    assert sizes[1] < 2.2 * sizes[0]
    shallow = len(diff.compare(_doc(BOREHOLE), _with_extra(_nested(300, levels=2))).to_json())
    assert sizes[1] < 4 * shallow


def test_long_paths_that_differ_only_in_the_middle_stay_distinct():
    both = _nested(1, mark="p") + _nested(1, mark="q")
    result = diff.compare(_doc(BOREHOLE), _with_extra(both))
    texts = [path for path in _paths(result) if path.endswith("#text")]
    assert len(texts) == 2 and len(set(texts)) == 2
    # The start and the end of a shortened path are those of the path itself.
    assert all(path.startswith("extra.n00") and ".a[1]#text" in path for path in texts)
    # An edit in one of the two is one change: the other one hides nothing of it.
    edited = both.replace("<a>0</a>", "<a>7</a>", 1)
    (change,) = diff.compare(_with_extra(both), _with_extra(edited)).changes
    (value,) = change.values
    assert (value.old, value.new) == ("0", "7") and value.path.endswith(".a[1]#text")
    assert len(value.path) <= _compare.MAX_PATH


def test_memory_does_not_grow_with_the_depth_of_leaves_that_are_compared():
    def peak(inside: str) -> int:
        old, new = _doc(BOREHOLE), _with_extra(inside)
        tracemalloc.start()
        try:
            diff.compare(old, new, max_changes=10)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    leaves = 1500
    deep, shallow = peak(_nested(leaves)), peak(_nested(leaves, levels=2))
    # 55 levels of 190 characters were 11,000 characters of path for every leaf.
    assert (deep - shallow) / leaves < 3 * _compare.MAX_PATH


def test_a_long_xpath_of_a_record_is_shortened_too():
    name = "w" * 5000
    member = f"\t<{name}><Thing><gml:name>one</gml:name><v>1</v></Thing></{name}>\n"
    result = diff.compare(_doc(BOREHOLE, member), _doc(BOREHOLE, member.replace("<v>1", "<v>2")))
    (change,) = result.changes
    (value,) = change.values
    assert value.at is not None and len(value.at) <= _compare.MAX_PATH
    assert schema_errors(result.to_dict()) == []


def test_two_long_names_that_start_alike_are_two_segments():
    one, two = "k" * 190 + "a" * 40, "k" * 190 + "b" * 40
    assert _compare.seg(one) != _compare.seg(two) and len(_compare.seg(one)) <= _compare.MAX_SHOWN
    assert len(_compare.seg(one).rpartition("#")[2]) == 32  # too long to find a twin for


# --- F4: a reference to an element the reader could not place ----------------------------

CENTRE_LINE = BOREHOLE[BOREHOLE.index("\t\t\t<centerLine>") : BOREHOLE.index("</centerLine>") + 14]


def _two_lines(
    first: str = "a", second: str = "b", target: str = "a", *, turned: bool = False
) -> bytes:
    """Borehole B-01 with two centre lines, and its depth system naming ``target``."""
    one = _edit(CENTRE_LINE, 'gml:id="cl-B-01"', f'gml:id="{first}"')
    two = _edit(CENTRE_LINE, 'gml:id="cl-B-01"', f'gml:id="{second}"').replace("-141", "-99")
    lines = two + one if turned else one + two
    text = _edit(BOREHOLE, CENTRE_LINE, lines)
    return _doc(_edit(text, 'xlink:href="#cl-B-01"', f'xlink:href="#{target}"'))


def test_swapping_the_ids_of_two_centre_lines_moves_the_reference_to_the_other_line():
    old, new = _two_lines("a", "b"), _two_lines("b", "a")
    _, places, _, marks, _, _ = read_with_places(old)
    assert "a" not in places and "b" not in places and {"a", "b"} <= marks.keys()
    result = diff.compare(old, new)
    assert result.identical is False
    assert {path.rpartition(".")[2] for path in _paths(result)} == {"linearElement"}
    assert diff.compare(old, old).identical


def test_new_ids_on_elements_placed_by_their_position_are_not_a_change():
    assert diff.compare(_two_lines("a", "b"), _two_lines("x9", "y9", "x9")).identical
    # The reference moved to the other line, under whatever ids.
    assert diff.compare(_two_lines("a", "b"), _two_lines("x9", "y9", "y9")).identical is False


def test_two_lines_reordered_with_the_reference_moved_to_the_other_one_is_a_difference():
    # The first centre line of each file has the id the reference names, so a place made
    # of positions alone would call the two references the same. They name lines with
    # different coordinates.
    old, new = _two_lines("a", "b", "a"), _two_lines("b", "a", "b", turned=True)
    assert diff.compare(old, new).identical is False
    # Reordered with the reference following its line: reported (a position is part of
    # such a place), never hidden.
    assert diff.compare(old, _two_lines("a", "b", "a", turned=True)).identical is False


@pytest.mark.parametrize(
    "inside",
    [
        # deeper below the record than an order-free place goes
        "<d>" * 20 + '<e gml:id="P"><v>1</v></e><e gml:id="Q"><v>2</v></e>' + "</d>" * 20,
        # a path longer than an order-free place keeps
        f'<{"n" * 200}><e gml:id="P"><v>1</v></e><f gml:id="Q"><v>2</v></f></{"n" * 200}>',
    ],
    ids=["deep", "long-name"],
)
def test_swapped_ids_below_what_the_reader_places_by_name_are_seen(inside):
    def text(first: str, second: str) -> bytes:
        line = _edit(CENTRE_LINE, "</LinearExtent>", f"{inside}</LinearExtent>")
        line = line.replace('"P"', f'"{first}"').replace('"Q"', f'"{second}"')
        borehole = _edit(BOREHOLE, CENTRE_LINE, line)
        return _doc(_edit(borehole, 'xlink:href="#cl-B-01"', 'xlink:href="#P"'))

    old = text("P", "Q")
    assert "P" not in read_with_places(old)[1]
    assert diff.compare(old, old).identical
    assert diff.compare(old, text("Q", "P")).identical is False


def test_swapped_ids_past_the_readers_index_limit_are_seen(monkeypatch):
    from pydiggs._reader import _read

    def text(first: str, second: str) -> bytes:
        many = "".join(f'<h{i} gml:id="h{i}"/>' for i in range(600))
        tail = f'<p gml:id="{first}"><v>1</v></p><q gml:id="{second}"><v>2</v></q>'
        line = _edit(CENTRE_LINE, "</LinearExtent>", f"{many}{tail}</LinearExtent>")
        borehole = _edit(BOREHOLE, CENTRE_LINE, line)
        return _doc(_edit(borehole, 'xlink:href="#cl-B-01"', 'xlink:href="#P"'))

    monkeypatch.setattr(_read, "LIMIT_BASE", 0)
    old = text("P", "Q")
    model, places, _, marks, _, _ = read_with_places(old)
    assert model.complete and "P" not in places and "P" in marks
    # So many element names for its size put the file past the same limit as a whole:
    # the comparison is incomplete, and what it lists is still right.
    assert diff.compare(old, old).changes == ()
    swapped = diff.compare(old, text("Q", "P"))
    assert swapped.complete is False
    assert {path.rpartition(".")[2] for path in _paths(swapped)} == {"linearElement"}


def test_an_edit_inside_an_element_placed_by_position_is_reported_with_its_reference():
    old = _two_lines("a", "b")
    edited = old.replace(b"-141", b"-142")
    assert edited != old
    assert diff.compare(old, edited).identical is False


@pytest.mark.parametrize("kept", [True, False], ids=["in-extra", "typed"])
def test_an_id_two_elements_have_names_both_of_them(kept):
    # Not valid DIGGS, and still two files: the reference names the elements that have
    # the id, wherever they are, never the text.
    def text(*ids: str) -> bytes:
        points = "".join(
            f'<x:p xmlns:x="urn:x" gml:id="{name}"><x:v>{n}</x:v></x:p>'
            for n, name in enumerate(ids)
        )
        use = '<x:use xmlns:x="urn:x" xlink:href="#t"/>'
        if kept:
            return _doc(_edit(BOREHOLE, "\t\t</Borehole>", f"{points}{use}\t\t</Borehole>"))
        line = _edit(CENTRE_LINE, "</LinearExtent>", f"{points}</LinearExtent>")
        return _doc(
            _edit(_edit(BOREHOLE, CENTRE_LINE, line), "\t\t</Borehole>", f"{use}\t\t</Borehole>")
        )

    old = text("t", "t", "u")
    assert diff.compare(old, old).identical
    assert diff.compare(old, text("t", "t", "w")).identical
    for moved in (
        text("u", "t", "t"),
        text("t", "u", "t"),
        text("t", "u", "u"),
        text("t", "t", "t"),
    ):
        assert diff.compare(old, moved).identical is False


def test_between_models_ids_the_model_does_not_keep_cannot_be_told_apart():
    # The documented limit of comparing models: neither id is in a model, so two models
    # of files whose reference point and centre line exchanged ids are equal. The files
    # themselves are not.
    old = _doc(BOREHOLE)
    text = BOREHOLE.replace('gml:id="pl-B-01"', 'gml:id="SWAP"')
    text = text.replace('gml:id="cl-B-01"', 'gml:id="pl-B-01"').replace(
        'gml:id="SWAP"', 'gml:id="cl-B-01"'
    )
    new = _doc(text)
    assert diff.compare(old, new).identical is False
    assert diff.compare(read_bytes(old), read_bytes(new)).identical is True


# --- F5: a depth system that only one file refers to -------------------------------------


def test_adding_the_first_record_that_uses_a_boreholes_depth_system_does_not_change_the_borehole():
    old, new = _doc(BARE), _doc(BARE, ACTIVITY)
    assert "#lsr-B-01" not in read_bytes(old).crs and "#lsr-B-01" in read_bytes(new).crs
    for a, b in ((old, new), (read_bytes(old), read_bytes(new)), (new, old)):
        result = diff.compare(a, b)
        assert result.identical is False
        assert result.summary["features"] == {
            "added": 0,
            "removed": 0,
            "changed": 0,
            "unchanged": 1,
        }
        assert _paths(result) == []
        assert [change.collection for change in result.changes] == ["sampling_activities"]


def test_a_depth_system_named_by_another_borehole_is_still_a_change():
    second = BOREHOLE.replace("B-01", "B-02")
    old = _doc(BOREHOLE, second, ACTIVITY)
    new = _doc(BOREHOLE, second, ACTIVITY.replace('srsName="#lsr-B-01"', 'srsName="#lsr-B-02"'))
    result = diff.compare(old, new)
    assert result.identical is False
    assert [change.collection for change in result.changes] == ["sampling_activities"]


# --- F6: a stdout that cannot encode the report ------------------------------------------


def _run_diff(*args: str, encoding: str = "ascii") -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    env.pop("PYTHONUTF8", None)
    command = [sys.executable, "-m", "pydiggs", "diff", *args]
    return subprocess.run(command, capture_output=True, cwd=ROOT, env=env, check=False)  # noqa: S603


@pytest.fixture(scope="module")
def greek(tmp_path_factory) -> tuple[str, str]:
    folder = tmp_path_factory.mktemp("greek")
    old, new = folder / "a.xml", folder / "b.xml"
    old.write_bytes(_doc(BOREHOLE))
    new.write_bytes(_doc(_edit(BOREHOLE, "<gml:name>B-01<", "<gml:name>B-01 φ≤<")))
    return str(old), str(new)


def test_a_text_report_stdout_cannot_encode_is_written_with_escapes(greek):
    done = _run_diff(*greek)
    assert done.returncode == 1 and done.stderr == b""
    out = done.stdout.decode("ascii")
    assert "The two files differ." in out
    assert "name: B-01 -> B-01 \\u03c6\\u2264" in out


def test_a_json_report_stdout_cannot_encode_is_the_same_document(greek):
    done = _run_diff(*greek, "--format", "json")
    assert done.returncode == 1 and done.stderr == b""
    document = json.loads(done.stdout.decode("ascii"))
    assert document == diff.compare(*greek).to_dict()
    assert schema_errors(document) == []


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_stdout_that_takes_the_characters_gets_them_as_they_are(greek, fmt):
    done = _run_diff(*greek, "--format", fmt, encoding="utf-8")
    assert done.returncode == 1 and "φ≤" in done.stdout.decode("utf-8")
    latin = _run_diff(*greek, "--format", fmt, encoding="cp1252")
    assert latin.returncode == 1 and latin.stderr == b""
    assert "\\u03c6\\u2264" in latin.stdout.decode("cp1252")


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_an_output_file_is_utf_8_whatever_stdout_is(greek, fmt, tmp_path):
    target = tmp_path / "report"
    done = _run_diff(*greek, "--format", fmt, "-o", str(target))
    assert (done.returncode, done.stdout, done.stderr) == (1, b"", b"")
    assert "B-01 φ≤" in target.read_text("utf-8")


def test_a_lone_surrogate_in_a_name_does_not_lose_the_json_report(greek, tmp_path, capsys):
    # A file name with a byte that is not UTF-8 reaches Python as a lone surrogate, which
    # UTF-8 itself cannot write.
    result = diff.compare(*greek)
    odd = dataclasses.replace(result, new={**result.new, "name": "b\udcff.xml"})
    target = tmp_path / "report.json"
    assert _cli._write(odd.to_json() + "\n", "json", str(target)) is None
    assert json.loads(target.read_bytes())["new"]["name"] == "b\udcff.xml"
    assert _cli._write(odd.to_json() + "\n", "json", None) is None
    assert json.loads(capsys.readouterr().out)["new"]["name"] == "b\udcff.xml"


class _Refusing:
    """A stdout that cannot be written to, for a reason that is not an ``OSError``."""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        raise ValueError("I/O operation on closed file.")

    def flush(self) -> None:
        return None


@pytest.mark.parametrize("stream", [_Refusing(), None], ids=["closed", "none"])
def test_a_stdout_that_refuses_the_report_exits_2_with_one_line(stream, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdout", stream)
    code = main(["diff", str(ANNOTATED), str(ANNOTATED)])
    monkeypatch.undo()
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("pydiggs diff: cannot write the report") and err.count("\n") == 1


# --- F7: a document read back -------------------------------------------------------------


def _document() -> dict[str, Any]:
    old = make_model(features=(make_feature("f1", "B-1", total_depth=None),))
    new = make_model(features=(make_feature("f1", "B-2"),))
    return diff.compare(old, new).to_dict()


def _set(document: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    holder: Any = document
    for step in path[:-1]:
        holder = holder[step]
    holder[path[-1]] = value
    return document


COUNTS = {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}
REFUSED = [
    (("summary",), {"features": {}}),
    (("summary",), {"features": {**COUNTS, "added": -1}}),
    (("summary",), {"features": {**COUNTS, "added": 1.5}}),
    (("summary",), {"features": {**COUNTS, "added": True}}),
    (("summary",), {"features": 3}),
    (("changes", 0, "op"), "bogus"),
    (("changes", 0, "matched_by"), "luck"),
    (("changes", 0, "name"), 7),
    (("changes", 0, "old_key"), ["f1"]),
    (("changes", 0, "values", 0, "op"), "bogus"),
    (("changes", 0, "values", 0, "at"), 7),
    (("changes", 0, "values", 0, "path"), "p" * 1001),
    (("changes", 0, "values", 0, "at"), "/" * 1001),
    (("identical",), True),  # with changes listed, and counted
    (("limits_hit",), [1, 2]),
    (("limits_hit",), ["max_changes"]),  # and complete
    (("complete",), False),  # and no limit named
    (("value_changes",), -1),
    (("reformatted",), -1),
    (("renamed",), True),
]
ACCEPTED = [
    (("summary",), {}),
    (("summary",), {"features": COUNTS, "samples": {**COUNTS, "changed": 0, "unchanged": 9}}),
    (("changes", 0, "matched_by"), None),
    (("changes", 0, "op"), "renamed"),
    (("changes", 0, "values", 0, "op"), "reformatted"),
    (("changes", 0, "values", 0, "at"), None),
    (("changes",), []),
    (("limits_hit",), []),
]


@pytest.mark.parametrize(("path", "value"), REFUSED)
def test_from_dict_and_the_schema_refuse_the_same_documents(path, value):
    document = _set(_document(), path, value)
    assert schema_errors(document) != []
    with pytest.raises(ValueError, match=r"^\$"):
        diff.Diff.from_dict(document)


@pytest.mark.parametrize(("path", "value"), ACCEPTED)
def test_from_dict_and_the_schema_accept_the_same_documents(path, value):
    document = _set(_document(), path, value)
    assert schema_errors(document) == []
    again = diff.Diff.from_dict(document)
    assert again.to_dict() == document
    assert again.to_text()


def test_an_incomplete_document_cannot_say_identical():
    document = _document()
    document.update(identical=True, complete=False, limits_hit=["time_budget"], changes=[])
    document.update(summary={}, value_changes=0)
    assert schema_errors(document) != []
    with pytest.raises(ValueError, match="identical"):
        diff.Diff.from_dict(document)
    document["identical"] = False
    assert schema_errors(document) == []
    assert "incomplete" in diff.Diff.from_dict(document).to_text()


def test_every_document_from_dict_accepts_can_be_printed():
    document = _document()
    assert diff.Diff.from_dict(document).to_text() == diff.Diff.from_dict(document).to_text()
    for path, value in REFUSED:
        try:
            read = diff.Diff.from_dict(_set(_document(), path, value))
        except ValueError:
            continue
        read.to_text()  # pragma: no cover - every one of them is refused


# --- the smaller points -------------------------------------------------------------------


def test_a_number_kept_as_text_is_compared_as_text_and_a_quantity_by_its_value():
    def borehole(depth: str, plain: str) -> bytes:
        text = _edit(
            BOREHOLE,
            '<totalMeasuredDepth uom="ft">160.00<',
            f'<totalMeasuredDepth uom="ft">{depth}<',
        )
        return _doc(
            _edit(text, "\t\t</Borehole>", f"<someDepth>{plain}</someDepth>\t\t</Borehole>")
        )

    quantity = diff.compare(borehole("160.00", "1.5"), borehole("160.0", "1.5"))
    assert quantity.identical and quantity.reformatted == 1
    plain = diff.compare(borehole("160.00", "1.50"), borehole("160.00", "1.5"))
    assert plain.identical is False and plain.reformatted == 0
    (change,) = plain.changes
    assert [(value.old, value.new) for value in change.values] == [("1.50", "1.5")]


def test_only_href_and_srs_name_are_references():
    def borehole(first: str, second: str, inside: str) -> bytes:
        points = f'<x:p xmlns:x="urn:x" gml:id="{first}"><x:v>1</x:v></x:p><x:p xmlns:x="urn:x" gml:id="{second}"><x:v>2</x:v></x:p>'
        return _doc(_edit(BOREHOLE, "\t\t</Borehole>", f"{points}{inside}\t\t</Borehole>"))

    # A reference follows its target under new ids ...
    href = '<x:use xmlns:x="urn:x" xlink:href="#{}"/>'
    assert diff.compare(
        borehole("p1", "p2", href.format("p1")), borehole("q1", "q2", href.format("q1"))
    ).identical
    # ... and an id written any other way is text: new ids are then a change.
    for other in (
        '<x:use xmlns:x="urn:x" ref="#{}"/>',
        '<x:use xmlns:x="urn:x">#{}</x:use>',
        '<x:use xmlns:x="urn:x" xlink:href="file.xml#{}"/>',
    ):
        old, new = (
            borehole("p1", "p2", other.format("p1")),
            borehole("q1", "q2", other.format("q1")),
        )
        assert diff.compare(old, old).identical
        assert diff.compare(old, new).identical is False


def test_index_columns_are_paired_by_key_and_not_by_position():
    def model(*index: Any) -> Any:
        results = (table(column("n", 5.0, 6.0), index=index),)
        return make_model(
            features=(make_feature("f1", "B-1"),),
            measurements=(make_test("t1", "T", "f1", results=results),),
        )

    top, base = column("top", 0.0, 1.0, key="top"), column("base", 1.0, 2.0, key="base")
    same = diff.compare(model(top, base), model(base, top))
    assert same.identical  # the order of the index columns is not data
    # Both the order and the values changed places: every interval is another one.
    swapped = diff.compare(
        model(top, base),
        model(column("base", 0.0, 1.0, key="base"), column("top", 1.0, 2.0, key="top")),
    )
    assert swapped.identical is False
    assert all("cells[" in path for path in _paths(swapped))


def test_index_columns_with_other_keys_fall_back_to_row_numbers():
    def model(key: str, *values: float) -> Any:
        index = (column(key, *values, key=key),)
        results = (table(column("n", 5.0, 6.0), index=index),)
        measurement = make_test("t1", "T", "f1", results=results)
        return make_model(features=(make_feature("f1", "B-1"),), measurements=(measurement,))

    result = diff.compare(model("depth", 0.0, 1.0), model("elevation", 0.0, 1.0))
    assert sorted(_paths(result)) == [
        "results[0].domain.index[depth]",
        "results[0].domain.index[elevation]",
    ]


@pytest.mark.parametrize("value", [1.0, 1.5, 1e9, math.inf, math.nan, -0.1, True, "0.1"])
def test_a_relative_tolerance_of_1_or_more_is_refused(value):
    model = make_model(features=(make_feature("f1", "B-1"),))
    with pytest.raises(ValueError, match="rel_tol"):
        diff.compare(model, model, rel_tol=value)


def test_a_relative_tolerance_below_1_is_accepted_and_does_not_equate_a_depth_with_0():
    def model(text: str) -> Any:
        from ._helpers import depth

        return make_model(features=(make_feature("f1", "B-1", total_depth=depth(text)),))

    assert diff.compare(model("160.00"), model("0"), rel_tol=0.999).identical is False
    assert diff.compare(model("160.00"), model("159.00"), rel_tol=0.01).identical


@pytest.mark.parametrize("value", ["1", "1.0", "2.5", "inf", "nan", "-1"])
def test_the_command_refuses_a_relative_tolerance_of_1_or_more(value, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["diff", str(ANNOTATED), str(ANNOTATED), f"--rel-tol={value}"])
    assert exit_.value.code == 2
    assert "--rel-tol" in capsys.readouterr().err


def test_naming_the_file_that_is_not_diggs_keeps_to_the_time_budget(tmp_path, monkeypatch, capsys):
    from pydiggs import _reader

    bad = tmp_path / "bad.xml"
    bad.write_text("<notDiggs/>", "utf-8")
    seen: list[dict[str, Any]] = []
    real = _reader.read

    def spy(path: Any, **options: Any) -> Any:
        seen.append(options)
        return real(path, **options)

    monkeypatch.setattr(_reader, "read", spy)
    assert main(["diff", str(ANNOTATED), str(bad), "--time-budget", "30"]) == 2
    assert f"{bad} is not a DIGGS file" in capsys.readouterr().err
    assert len(seen) == 1 and 0 < seen[0]["time_budget"] <= 30
    assert main(["diff", str(bad), str(ANNOTATED)]) == 2
    assert f"{bad} is not a DIGGS file" in capsys.readouterr().err


def test_a_file_that_cannot_be_named_in_time_is_an_input(tmp_path, monkeypatch, capsys):
    from pydiggs import _reader

    bad = tmp_path / "bad.xml"
    bad.write_text("<notDiggs/>", "utf-8")
    partly = dataclasses.replace(read_bytes(_doc(BOREHOLE)), complete=False)
    monkeypatch.setattr(_reader, "read", lambda *_, **__: partly)
    assert main(["diff", str(ANNOTATED), str(bad), "--time-budget", "30"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("pydiggs diff: an input is not a DIGGS file")


def test_the_fixture_members_are_what_the_tests_say():
    assert BOREHOLE.count("<Borehole ") == 1 and ACTIVITY.count("<SamplingActivity ") == 1
    model = read_bytes(_doc(BOREHOLE, ACTIVITY))
    assert model.complete and len(model.features) == 1 and len(model.sampling_activities) == 1
    assert read_bytes(_doc(BARE)).complete and "<waterStrike>" not in BARE
    with pytest.raises(DocumentError):
        read_bytes(b"<notDiggs/>")
