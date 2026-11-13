"""pydiggs.ags4: the deterministic writer and the AGS4 round trip (property 2).

``dumps(read(b)) == b`` for every conforming file, and ``read(dumps(f)) == f`` for
every :class:`~pydiggs.ags4.File` that can be written. A UNIT or TYPE row a file lacks
is an empty tuple, and the writer writes no row for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pydiggs import DocumentError, ags4
from pydiggs.ags4 import _emit, _parse

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
    # Only what the tables themselves say is left: an unknown version, and a UNIT or
    # TYPE row the file lacked, which the writer does not invent.
    missing = [
        (group.name, row)
        for group in f.groups.values()
        for row, values in (("UNIT", group.units), ("TYPE", group.types))
        if group.headings and not values
    ]
    assert [n.message for n in again.notes if n.rule_id == "ags4.rule-2b"] == [
        f"Group {name} has no {row} row." for name, row in missing
    ]
    assert {n.rule_id for n in again.notes} <= {"ags4.version-unknown", "ags4.rule-2b"}
    assert ags4.dumps(again) == data


@pytest.mark.parametrize("row", ["UNIT", "TYPE", "both"])
def test_a_missing_unit_or_type_row_stays_missing(row):
    lines = [
        b'"GROUP","LOCA"',
        b'"HEADING","LOCA_ID","LOCA_GL"',
        b'"UNIT","","m"',
        b'"TYPE","ID","2DP"',
        b'"DATA","BH01","12.50"',
    ]
    gone = {"UNIT": [2], "TYPE": [3], "both": [2, 3]}[row]
    data = b"".join(line + b"\r\n" for i, line in enumerate(lines) if i not in gone)
    f = ags4.read_bytes(data)
    loca = f.groups["LOCA"]
    assert loca.units == (() if row in {"UNIT", "both"} else ("", "m"))
    assert loca.types == (() if row in {"TYPE", "both"} else ("ID", "2DP"))
    assert {n.rule_id for n in f.notes} == {"ags4.rule-2b"}
    assert ags4.dumps(f) == data  # no row is invented
    assert ags4.read_bytes(ags4.dumps(f)) == f
    assert f != ags4.read_bytes(b"".join(line + b"\r\n" for line in lines))


def test_an_empty_unit_or_type_is_no_row_but_a_short_one_is_refused():
    assert b'"UNIT"' not in ags4.dumps(ags4.File(groups=[_group(units=())]))
    assert b'"TYPE"' not in ags4.dumps(ags4.File(groups=[_group(types=())]))
    with pytest.raises(ValueError, match="or empty for no row"):
        ags4.dumps(ags4.File(groups=[_group(types=("ID",))]))


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
        (_group(types=("ID",)), "UNIT and TYPE rows"),
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


# --- the writer refuses what the reader would not read back whole ------------------------

MAX = _parse.Limits()


def _round_trips(f: ags4.File) -> None:
    again = ags4.read_bytes(ags4.dumps(f))
    assert again.complete
    assert again == f


def _refused(f: ags4.File, message: str) -> None:
    with pytest.raises(ValueError, match=message) as caught:
        ags4.dumps(f)
    assert len(str(caught.value)) < 200  # a fixed template: no value is echoed


def test_the_writer_uses_the_readers_limits():
    assert _emit._LIMITS == MAX


def test_headings_up_to_the_readers_limit_are_written_and_no_more():
    def wide(width: int) -> ags4.File:
        empty = ("",) * width
        names = tuple(f"H{i}" for i in range(width))
        return ags4.File(
            groups=[ags4.Group(name="A", headings=names, units=empty, types=empty, rows=[empty])]
        )

    _round_trips(wide(MAX.max_headings))
    _refused(wide(MAX.max_headings + 1), f"group A cannot be written: .* {MAX.max_headings} ")


@pytest.mark.parametrize("where", ["name", "headings", "units", "types", "rows"])
def test_values_up_to_the_readers_limit_are_written_and_no_more(where):
    def holding(value: str) -> ags4.File:
        if where == "name":
            return ags4.File(groups=[_group(name=value)])
        if where == "rows":
            return ags4.File(groups=[_group(rows=(("P1", value),))])
        return ags4.File(groups=[_group(**{where: (value, "")})])

    _round_trips(holding("x" * MAX.max_field_chars))
    # A doubled quote is one character of the value, so it counts once.
    _round_trips(holding('"' * MAX.max_field_chars))
    _refused(holding("x" * (MAX.max_field_chars + 1)), "a value longer than 1000000 characters")


def test_groups_and_rows_over_the_readers_limits_are_refused(monkeypatch):
    # The real limits are 10,000 groups and 5,000,000 rows: the writer reads them from
    # the same Limits, so smaller ones show the boundary without the memory.
    monkeypatch.setattr(_emit, "_LIMITS", _parse.Limits(max_groups=3, max_rows=4))
    three = ags4.File(groups=[_group(name=f"G{i}") for i in range(3)])
    assert ags4.dumps(three)
    _refused(ags4.File(groups=[_group(name=f"G{i}") for i in range(4)]), "more than 3 groups")
    four = ags4.File(
        groups=[_group(rows=(("P1", "a"),) * 2), _group(name="B", rows=[("P", "b")] * 2)]
    )
    assert ags4.dumps(four)
    five = ags4.File(
        groups=[_group(rows=(("P1", "a"),) * 2), _group(name="B", rows=[("P", "b")] * 3)]
    )
    _refused(five, "more than 4 DATA rows")


def test_error_messages_do_not_echo_a_name_that_is_not_plain():
    with pytest.raises(ValueError, match="cannot be written") as caught:
        ags4.dumps(ags4.File(groups=[_group(name="\x1b[31m" + "x" * 80, units=("",))]))
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
