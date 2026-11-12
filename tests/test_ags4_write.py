"""pydiggs.ags4: the deterministic writer and the AGS4 round trip (property 2).

``dumps(read(b)) == b`` for every conforming file, and ``read(dumps(f)) == f`` for
every :class:`~pydiggs.ags4.File` that can be written.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pydiggs import DocumentError, ags4
from pydiggs.ags4 import _emit

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
CONFORMING = [
    *sorted(FIXTURES.glob("clean_*.ags")),
    FIXTURES / "quoting.ags",
    FIXTURES / "utf8.ags",
]

# Any text a value may hold: everything but a line break, a NUL and a lone surrogate.
_VALUES = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n\x00"),
    max_size=12,
) | st.sampled_from(["", '"', '""', ",", '","', " ", "=1+1", "\t", "\u2028", "\x85", "\x0b\x0c"])
_NAMES = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n\x00"),
    min_size=1,
    max_size=6,
) | st.sampled_from(["PROJ", "TRAN", "LOCA", "GEOL", '"', ",", " ", "**X"])


@st.composite
def groups(draw: st.DrawFn, name: str) -> ags4.Group:
    width = draw(st.integers(min_value=0, max_value=5))
    row = st.lists(_VALUES, min_size=width, max_size=width).map(tuple)
    return ags4.Group(
        name=name,
        headings=draw(row),
        units=draw(row),
        types=draw(row),
        rows=draw(st.lists(row, max_size=5)),
    )


@st.composite
def files(draw: st.DrawFn) -> ags4.File:
    names = draw(st.lists(_NAMES, min_size=1, max_size=4, unique=True))
    return ags4.File(groups=[draw(groups(name)) for name in names])


def _group(**changes) -> ags4.Group:
    fields = {
        "name": "PROJ",
        "headings": ("PROJ_ID", "PROJ_NAME"),
        "units": ("", ""),
        "types": ("ID", "X"),
        "rows": (("P1", "One"),),
    }
    return ags4.Group(**{**fields, **changes})


# --- property 2 -------------------------------------------------------------------------


@pytest.mark.parametrize("path", CONFORMING, ids=lambda p: p.name)
def test_a_conforming_file_is_written_back_byte_for_byte(path):
    data = path.read_bytes()
    f = ags4.read_bytes(data)
    assert f.notes == ()
    assert ags4.dumps(f) == data


@settings(deadline=None, max_examples=300)
@given(files())
def test_any_file_that_can_be_written_reads_back_equal(f):
    data = ags4.dumps(f)
    again = ags4.read_bytes(data)
    assert again == f
    assert list(again.groups) == list(f.groups)  # and in the same order
    assert again.complete
    assert ags4.dumps(again) == data  # a fixed point
    # What the writer makes breaks none of the rules the reader notes.
    assert [n.rule_id for n in again.notes if n.rule_id != "ags4.version-unknown"] == []


def test_a_file_with_no_group_has_no_ags4_form():
    with pytest.raises(ValueError, match="no group"):
        ags4.dumps(ags4.File(groups=[]))
    with pytest.raises(DocumentError):
        ags4.read_bytes(b"")


@pytest.mark.parametrize(
    "path", [FIXTURES / "messy.ags", FIXTURES / "lf_only.ags", FIXTURES / "cp1252.ags"]
)
def test_a_file_that_broke_rules_is_written_conforming(path):
    f = ags4.read(path)
    assert f.notes
    data = ags4.dumps(f)
    again = ags4.read_bytes(data)
    assert again == f
    # Only what the tables themselves say is left: an unknown version, absent header rows.
    assert {n.rule_id for n in again.notes} <= {"ags4.version-unknown"}
    assert ags4.dumps(again) == data


# --- the layout -------------------------------------------------------------------------


def test_the_layout_is_fixed():
    f = ags4.File(
        groups=[
            _group(rows=(("P1", 'The "Old" Quay'), ("P2", ""))),
            ags4.Group(name="TRAN", headings=("TRAN_AGS",), units=("",), types=("X",)),
            ags4.Group(name="NONE", headings=()),
        ]
    )
    assert ags4.dumps(f) == (
        b'"GROUP","PROJ"\r\n'
        b'"HEADING","PROJ_ID","PROJ_NAME"\r\n'
        b'"UNIT","",""\r\n'
        b'"TYPE","ID","X"\r\n'
        b'"DATA","P1","The ""Old"" Quay"\r\n'
        b'"DATA","P2",""\r\n'
        b"\r\n"
        b'"GROUP","TRAN"\r\n'
        b'"HEADING","TRAN_AGS"\r\n'
        b'"UNIT",""\r\n'
        b'"TYPE","X"\r\n'
        b"\r\n"
        b'"GROUP","NONE"\r\n'
        b'"HEADING"\r\n'
        b'"UNIT"\r\n'
        b'"TYPE"\r\n'
    )


def test_writing_is_deterministic_and_keeps_the_order_given():
    f = ags4.read(FIXTURES / "clean_4_1_1.ags")
    assert ags4.dumps(f) == ags4.dumps(f)
    backwards = ags4.File(groups=list(f.groups.values())[::-1])
    assert list(ags4.read_bytes(ags4.dumps(backwards)).groups) == list(f.groups)[::-1]
    assert ags4.dumps(backwards) != ags4.dumps(f)


def test_formula_like_text_is_written_unchanged():
    values = ("=SUM(A1:A2)", "+44 20", "-1.5", "@cmd", "'quoted")
    f = ags4.File(
        groups=[ags4.Group(name="X", headings=values, units=values, types=values, rows=[values])]
    )
    assert ags4.read_bytes(ags4.dumps(f)).groups["X"].rows == (values,)
    assert b'"=SUM(A1:A2)","+44 20","-1.5","@cmd","\'quoted"' in ags4.dumps(f)


def test_non_ascii_text_is_written_as_utf8():
    f = ags4.File(groups=[_group(rows=(("P1", "12° café"),))])
    data = ags4.dumps(f)
    assert "12° café".encode() in data
    assert not data.startswith(b"\xef\xbb\xbf")
    assert ags4.read_bytes(data).source.encoding == "utf-8"


# --- what cannot be written -------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "message"),
    [
        (_group(name=""), "empty name"),
        (_group(units=("",)), "UNIT and TYPE rows"),
        (_group(types=()), "UNIT and TYPE rows"),
        (_group(rows=(("P1",),)), "DATA row 1 has 1 values for 2 headings"),
        (_group(rows=(("P1", "a"), ("P2", "b", "c"))), "DATA row 2 has 3 values for 2 headings"),
        (_group(rows=(("P1", "two\nlines"),)), "a DATA row holds a line break"),
        (_group(rows=(("P1", "cr\r"),)), "a DATA row holds a line break"),
        (_group(rows=(("P1", "nul\x00"),)), "a DATA row holds a line break or a NUL"),
        (_group(name="PR\nOJ"), "a GROUP row holds a line break"),
        (_group(headings=("A\n", "B")), "a HEADING row holds a line break"),
        (_group(units=("\r", "")), "a UNIT row holds a line break"),
        (_group(types=("ID", "\x00")), "a TYPE row holds a line break"),
        (_group(rows=(("P1", "\ud800"),)), "cannot be written as UTF-8"),
    ],
)
def test_a_file_that_cannot_be_written_is_a_value_error(group, message, tmp_path):
    f = ags4.File(groups=[group])
    with pytest.raises(ValueError, match=message):
        ags4.dumps(f)
    target = tmp_path / "out.ags"
    with pytest.raises(ValueError, match=message):
        ags4.write(f, target)
    assert not target.exists()  # nothing is written


def test_error_messages_do_not_echo_a_name_that_is_not_plain():
    with pytest.raises(ValueError, match="cannot be written") as caught:
        ags4.dumps(ags4.File(groups=[_group(name="\x1b[31m" + "x" * 80, units=())]))
    assert "\x1b" not in str(caught.value)
    assert "x" * 40 not in str(caught.value)


@pytest.mark.parametrize("bad", [None, b"", "text", {"PROJ": 1}, _group()])
def test_only_a_file_is_written(bad, tmp_path):
    with pytest.raises(TypeError, match=r"ags4\.File"):
        ags4.dumps(bad)
    with pytest.raises(TypeError, match=r"ags4\.File"):
        ags4.write(bad, tmp_path / "out.ags")


# --- write() ----------------------------------------------------------------------------


def test_write_puts_the_bytes_of_dumps_at_the_path(tmp_path):
    f = ags4.read(FIXTURES / "clean_4_2.ags")
    target = tmp_path / "out.ags"
    target.write_bytes(b"old content that is longer than nothing")
    assert ags4.write(f, target) is None
    assert target.read_bytes() == ags4.dumps(f) == (FIXTURES / "clean_4_2.ags").read_bytes()
    ags4.write(f, str(target), line_ending="\r\n")
    assert ags4.read(target) == f


@pytest.mark.parametrize("line_ending", ["\n", "\r", "", "crlf", None])
def test_crlf_is_the_only_line_ending(line_ending, tmp_path):
    f = ags4.File(groups=[_group()])
    target = tmp_path / "out.ags"
    with pytest.raises(ValueError, match="line_ending"):
        ags4.write(f, target, line_ending=line_ending)
    assert not target.exists()


def test_a_path_that_cannot_be_written_is_an_os_error(tmp_path):
    with pytest.raises(OSError, match=r"out\.ags"):
        ags4.write(ags4.File(groups=[_group()]), tmp_path / "missing" / "out.ags")


def test_the_emitter_is_pure_text():
    text = _emit.emit(ags4.File(groups=[_group()]))
    assert isinstance(text, str)
    assert text.count(_emit.CRLF) == 5
    assert "\n" not in text.replace(_emit.CRLF, "")
