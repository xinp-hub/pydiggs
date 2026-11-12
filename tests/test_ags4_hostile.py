"""pydiggs.ags4 on hostile input (property 6).

Any bytes give a :class:`~pydiggs.ags4.File`, a ``DocumentError`` or an
``InstanceReadError``; no other exception escapes. Every limit stops the read with a
note and ``complete=False``, and the work is linear in the size of the input.

The hypothesis tests here are the reader's fuzz target. They run with the rest of the
suite on a small budget; CI's ``ags4 fuzz`` job runs this file alone with
``PYDIGGS_FUZZ_EXAMPLES`` set to a large one.
"""

from __future__ import annotations

import contextlib
import errno
import gzip
import io
import os
import sys
import threading
import tracemalloc
import zipfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pydiggs import DocumentError, InstanceReadError, Severity, ags4
from pydiggs.ags4 import _io, _parse
from pydiggs.ags4._parse import Limits, parse

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
SEEDS = [path.read_bytes() for path in sorted(FIXTURES.glob("*.ags"))]
EXAMPLES = int(os.environ.get("PYDIGGS_FUZZ_EXAMPLES", "200"))
FUZZ = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
LINEAR = 2.5

HEADER = '"GROUP","PROJ"\r\n"HEADING","PROJ_ID","PROJ_NAME"\r\n"UNIT","",""\r\n"TYPE","ID","X"\r\n'
ROW = '"DATA","P1","One"\r\n'

# Pieces an AGS4 file is made of, and pieces that break one.
_TOKENS = st.sampled_from(
    [
        b'"',
        b'""',
        b",",
        b'","',
        b"\r\n",
        b"\n",
        b"\r",
        b" ",
        b"\t",
        b'"GROUP"',
        b'"HEADING"',
        b'"UNIT"',
        b'"TYPE"',
        b'"DATA"',
        b"GROUP",
        b"DATA",
        b'"PROJ"',
        b'"TRAN"',
        b'"TRAN_AGS"',
        b'"4.1.1"',
        b'"**PROJ"',
        b"\xef\xbb\xbf",
        b"\xe9",
        b"\x81",
        b"\x00",
        b"\x1f\x8b",
        b"PK\x03\x04",
        b"x" * 50,
    ]
)
_SOUP = st.lists(_TOKENS | st.binary(max_size=8), max_size=60).map(b"".join)


def _check(file: ags4.File, data: bytes) -> None:
    """The invariants of every file the reader returns."""
    assert file.source is not None
    assert file.source.size == len(data)
    assert file.complete is (file.limits_hit == ())
    for name, group in file.groups.items():
        assert name == group.name
        assert name != ""
        width = len(group.headings)
        assert len(group.units) == len(group.types) == width
        assert all(len(row) == width for row in group.rows)
        assert group.line is not None
        assert group.line >= 1
    for note in file.notes:
        assert note.rule_id.startswith("ags4.")
        assert len(note.message) < 300
        assert note.path is None or len(note.path) <= 64
    if not file.complete:
        assert any(n.rule_id in ("ags4.limit", "ags4.timeout") for n in file.notes)
    # What was read can always be written, and reads back equal.
    again = ags4.read_bytes(ags4.dumps(file)) if file.groups else file
    assert again == file


def _read(data: bytes, **kwargs) -> ags4.File | None:
    """Read ``data``; None for the two refusals. Any other exception fails the test."""
    try:
        file = ags4.read_bytes(data, **kwargs)
    except DocumentError as exc:
        assert exc.kind in ("not-ags4", "unsupported-version")
        assert exc.finding is not None
        assert exc.finding.severity is Severity.ERROR
        return None
    except InstanceReadError as exc:
        assert exc.errno in (errno.EFBIG, errno.EINVAL)
        return None
    _check(file, data)
    return file


# --- the fuzz target ----------------------------------------------------------------------


@FUZZ
@given(st.binary(max_size=400))
def test_any_bytes_give_a_file_or_one_of_the_two_errors(data):
    _read(data)


@FUZZ
@given(_SOUP)
def test_any_mix_of_ags4_pieces_gives_a_file_or_one_of_the_two_errors(data):
    _read(data)


@st.composite
def mutations(draw: st.DrawFn) -> bytes:
    """A fixture with a few edits: cut, insert, repeat, swap, or flip a byte."""
    data = bytearray(draw(st.sampled_from(SEEDS)))
    for _ in range(draw(st.integers(min_value=1, max_value=6))):
        if not data:
            break
        at = draw(st.integers(min_value=0, max_value=len(data) - 1))
        span = draw(st.integers(min_value=1, max_value=40))
        kind = draw(st.sampled_from(["cut", "insert", "repeat", "quote", "newline", "flip"]))
        if kind == "cut":
            del data[at : at + span]
        elif kind == "insert":
            data[at:at] = draw(_SOUP)
        elif kind == "repeat":
            data[at:at] = bytes(data[at : at + span]) * draw(st.integers(1, 20))
        elif kind == "quote":
            data[at : at + 1] = b'"'
        elif kind == "newline":
            data[at : at + 1] = draw(st.sampled_from([b"\r\n", b"\n", b"\r", b""]))
        else:
            data[at] ^= 1 << draw(st.integers(0, 7))
    return bytes(data)


@FUZZ
@given(mutations())
def test_any_mutation_of_a_fixture_gives_a_file_or_one_of_the_two_errors(data):
    _read(data)


@FUZZ
@given(_SOUP, st.sampled_from(["ascii", "utf-8", "utf-8-sig", "cp1252", "latin-1"]))
def test_an_explicit_encoding_adds_only_a_value_error(data, encoding):
    try:
        _read(data, encoding=encoding)
    except ValueError as exc:
        assert not isinstance(exc, DocumentError)
        assert "encoding asked for" in str(exc)


@FUZZ
@given(st.text(max_size=300), st.integers(1, 5), st.integers(0, 5), st.integers(1, 30))
def test_the_pure_parser_takes_any_text_under_any_limits(text, groups, headings, chars):
    limits = Limits(max_groups=groups, max_headings=headings, max_rows=3, max_field_chars=chars)
    try:
        parsed = parse(text, limits=limits)
    except DocumentError:
        return
    assert parsed.complete is (parsed.limits_hit == ())
    assert len(parsed.groups) <= groups
    for group in parsed.groups.values():
        assert len(group.headings) <= headings
        assert all(len(value) <= chars for row in group.rows for value in row)
    assert sum(len(group.rows) for group in parsed.groups.values()) <= 3


@FUZZ
@given(st.text(alphabet='",ab \t', max_size=40))
def test_the_tokenizer_agrees_with_itself_on_any_line(line):
    fields, count, clean = _parse._tokenize(line)
    assert fields  # every line has at least one field
    assert count == len(fields)  # nothing is dropped under the default bound
    if clean:  # a clean line is exactly what the writer would make of its fields
        assert '"' + '","'.join(f.replace('"', '""') for f in fields) + '"' == line


# --- shapes worth naming --------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b'"' * 10_000,
        b'"GROUP","A"\r\n' + b'"' * 9_999,
        b'"GROUP","A"\r\n"HEADING",' + b'"",' * 5_000,
        b'"GROUP","A"\r\n"DATA"' + b"," * 10_000,
        b"," * 10_000,
        b'"GROUP","A"\r\n' * 2_000,
        b"\r" * 10_000,
        b'"GROUP"\r\n"GROUP",""\r\n"GROUP","","",""\r\n',
        b'"GROUP","A"\r\n"HEADING"\r\n"DATA"\r\n"DATA","x","y"\r\n',
        b'"GROUP","A"\r\n"UNIT","u"\r\n"TYPE","t"\r\n"HEADING","H"\r\n"DATA","1"\r\n',
        b'"GROUP","A"\r\n"HEADING","H","H","H"\r\n"DATA","1","2","3"\r\n',
        b'"GROUP","TRAN"\r\n"HEADING","TRAN_AGS"\r\n"DATA"\r\n',
        b'"GROUP","TRAN"\r\n"HEADING","X"\r\n"DATA","1"\r\n',
        b'"**PROJ"\r\n"GROUP","A"\r\n',
        b"\xef\xbb\xbf",
        b"\xef\xbb\xbf\xff",
        b'\xef\xbb\xbf"GROUP","A"\xff\r\n',
    ],
)
def test_awkward_shapes_do_not_crash(data):
    _read(data)


def test_duplicate_headings_are_kept_as_written():
    f = _read(
        b'"GROUP","A"\r\n"HEADING","H","H"\r\n"UNIT","",""\r\n"TYPE","X","X"\r\n"DATA","1","2"\r\n'
    )
    assert f is not None
    assert f.groups["A"].headings == ("H", "H")
    assert f.groups["A"].records() == [{"H": "2"}]


def test_a_second_group_of_the_same_name_is_left_out_whole():
    data = (HEADER + ROW + HEADER + '"DATA","P2","Two"\r\n').encode()
    f = _read(data)
    assert f is not None
    assert f.groups["PROJ"].rows == (("P1", "One"),)
    assert [n.rule_id for n in f.notes] == ["ags4.duplicate-group"]


# --- limits: each stops the read, with a note and complete=False ----------------------------


def _limited(text: str, **limits: int) -> _parse.Parsed:
    return parse(text, limits=Limits(**limits))


def _stopped(parsed: _parse.Parsed, limit: str, line: int) -> None:
    assert not parsed.complete
    assert parsed.limits_hit == (limit,)
    last = parsed.notes[-1]
    assert (last.rule_id, last.severity, last.line) == ("ags4.limit", Severity.ERROR, line)
    assert last.message.startswith("Reading stopped: ")
    assert last.message.endswith("The row at this point and those after it were not read.")


def test_max_groups():
    text = "".join(f'"GROUP","G{i}"\r\n"HEADING","H"\r\n' for i in range(5))
    parsed = _limited(text, max_groups=3)
    assert list(parsed.groups) == ["G0", "G1", "G2"]
    _stopped(parsed, "max_groups", 7)
    assert _limited(text, max_groups=5).complete


def test_max_headings():
    text = '"GROUP","A"\r\n"HEADING","H1","H2","H3"\r\n"DATA","1","2","3"\r\n'
    parsed = _limited(text, max_headings=2)
    assert parsed.groups["A"].headings == ()
    assert parsed.groups["A"].rows == ()
    _stopped(parsed, "max_headings", 2)
    assert _limited(text, max_headings=3).complete


def test_max_rows_counts_the_whole_file():
    text = HEADER + ROW * 2 + '"GROUP","B"\r\n"HEADING","H"\r\n' + '"DATA","x"\r\n' * 3
    parsed = _limited(text, max_rows=3)
    assert len(parsed.groups["PROJ"].rows) == 2
    assert len(parsed.groups["B"].rows) == 1
    _stopped(parsed, "max_rows", 10)
    assert _limited(text, max_rows=5).complete


def test_max_field_chars():
    text = HEADER + ROW + '"DATA","P2","' + "x" * 50 + '"\r\n' + ROW
    parsed = _limited(text, max_field_chars=49)
    assert parsed.groups["PROJ"].rows == (("P1", "One"),)
    _stopped(parsed, "max_field_chars", 6)
    assert _limited(text, max_field_chars=50).complete
    assert "x" * 20 not in parsed.notes[-1].message


def test_padding_cannot_multiply_the_file():
    width = 1_000
    head = '"GROUP","A"\r\n"HEADING",' + ",".join(['"H"'] * width) + "\r\n"
    text = head + '"DATA"\r\n' * 5_000  # 5,000 rows of 1,000 cells from 40 kB of rows
    parsed = parse(text)
    cells = sum(len(row) for row in parsed.groups["A"].rows)
    assert cells <= _parse._CELL_BASE + len(text) // 2
    _stopped(parsed, "max_cells", parsed.notes[-1].line or 0)
    assert len(parsed.notes) <= _parse.MAX_NOTES_PER_RULE + 5
    honest = head + ('"DATA",' + ",".join(['""'] * width) + "\r\n") * 200
    assert parse(honest).complete


def _peak(text: str, **limits: int) -> tuple[_parse.Parsed, int]:
    """Parse ``text``; the result and the peak of memory allocated while parsing."""
    tracemalloc.start()
    try:
        parsed = parse(text, limits=Limits(**limits))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return parsed, peak


# A row may cost a copy of its line and a bounded number of fields, never one object (or
# one list slot) per comma: the bound is the size of the text plus 1 MiB, against tens of
# MiB for one list slot per field.
@pytest.mark.parametrize(
    ("tail", "path"),
    [
        ("," * 2_000_000, "general: unquoted commas"),
        (',""' * 300_000, "fast: quoted empty fields, a line under max_field_chars"),
        (',"' + '""' * 1_500_000 + '"', "general: one value of doubled quotes, over the limit"),
        (',"' + "x" * 3_000_000 + '"', "general: one value over the limit"),
    ],
    ids=["commas", "quoted-empties", "doubled-quotes", "long-value"],
)
def test_a_pathological_row_is_read_in_bounded_memory(tail, path):
    text = HEADER + '"DATA"' + tail + "\r\n" + ROW
    parsed, peak = _peak(text)
    assert peak < len(text) + 2**20, (path, peak)
    if "over the limit" in path:
        _stopped(parsed, "max_field_chars", 5)
    else:
        rows = parsed.groups["PROJ"].rows
        assert rows == (("", ""), ("P1", "One"))
        rule_4 = next(n for n in parsed.notes if n.rule_id == "ags4.rule-4")
        count = tail.count(",")
        assert rule_4.message.startswith(f"A DATA row has {count} values where the HEADING")


def test_a_long_value_is_refused_while_scanning_in_both_paths():
    for row in ('"DATA","P1","' + "x" * 60 + '"', 'DATA,P1,"' + "x" * 60 + '"'):
        parsed = _limited(HEADER + row + "\r\n", max_field_chars=59)
        _stopped(parsed, "max_field_chars", 5)
        assert _limited(HEADER + row + "\r\n", max_field_chars=60).complete


def test_fields_past_the_widest_heading_row_are_counted_not_kept():
    fields, count, clean = _parse._tokenize('"DATA"' + ',"x"' * 50, keep=4)
    assert (fields, count, clean) == (["DATA", "x", "x", "x"], 51, True)
    parsed = _limited(HEADER + '"DATA","a","b","c","d"\r\n', max_headings=2)
    assert parsed.groups["PROJ"].rows == (("a", "b"),)
    assert "has 4 values where the HEADING row has 2" in parsed.notes[0].message
    wide = _limited('"GROUP","A"\r\n"HEADING","H1","H2","H3"\r\n', max_headings=2)
    _stopped(wide, "max_headings", 2)
    group = _limited('"GROUP","A","B","C"\r\n"HEADING","H"\r\n', max_headings=0)
    assert group.notes[0].message.startswith("A GROUP row has more than a group name")


def test_a_group_row_with_no_name_alone_is_not_ags4():
    for data in (b'"GROUP",""\r\n', b'"GROUP"\r\n"GROUP",""\r\n"GROUP","","",""\r\n'):
        with pytest.raises(DocumentError) as caught:
            ags4.read_bytes(data)
        assert caught.value.kind == "not-ags4"
    with pytest.raises(DocumentError) as caught:
        ags4.read_bytes(b'"**PROJ"\r\n"GROUP",""\r\n')
    assert caught.value.kind == "unsupported-version"
    # A read stopped by a limit before any group is assembled still gives a partial file.
    stopped = parse(
        '"GROUP",""\r\n' + '"GROUP","' + "x" * 40 + '"\r\n', limits=Limits(max_field_chars=10)
    )
    assert stopped.groups == {}
    assert stopped.limits_hit == ("max_field_chars",)


def test_the_default_limits_are_far_above_real_files():
    assert Limits() == Limits(
        max_groups=10_000, max_headings=2_000, max_rows=5_000_000, max_field_chars=1_000_000
    )
    f = ags4.read(FIXTURES / "clean_4_2.ags")
    assert f.complete


def test_a_limit_reached_in_the_adapter_gives_an_incomplete_file():
    file = _io.load(
        (HEADER + ROW * 3).encode(),
        name="x.ags",
        codec=None,
        time_budget=None,
        limits=Limits(max_rows=2),
    )
    assert not file.complete
    assert file.limits_hit == ("max_rows",)
    assert len(file.groups["PROJ"].rows) == 2
    assert file.notes[-1].rule_id == "ags4.limit"
    _check(file, (HEADER + ROW * 3).encode())


# --- the time budget ------------------------------------------------------------------------


def test_should_stop_is_cooperative_and_gives_a_partial_file():
    calls = []

    def stop_at_third() -> bool:
        calls.append(1)
        return len(calls) >= 3

    text = HEADER + ROW * 10_000
    parsed = parse(text, should_stop=stop_at_third)
    assert not parsed.complete
    assert parsed.limits_hit == ("time_budget",)
    assert len(calls) == 3
    assert len(parsed.groups["PROJ"].rows) == 3 * _parse._CHECK_EVERY - 5
    last = parsed.notes[-1]
    assert (last.rule_id, last.severity, last.line) == (
        "ags4.timeout",
        Severity.ERROR,
        3 * _parse._CHECK_EVERY,
    )


def test_an_exhausted_time_budget_is_not_an_exception(monkeypatch):
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(_io.time, "monotonic", lambda: next(clock))
    data = (HEADER + ROW * 5_000).encode()
    f = ags4.read_bytes(data, time_budget=5)
    assert not f.complete
    assert f.limits_hit == ("time_budget",)
    assert 0 < len(f.groups["PROJ"].rows) < 5_000
    assert f.notes[-1].rule_id == "ags4.timeout"
    _check(f, data)


def _clock(monkeypatch: pytest.MonkeyPatch, *times: float) -> list[float]:
    """Make ``time.monotonic`` in the adapter return ``times``, then 1,000 for ever.

    The first value is read when the deadline is set; the list records each read.
    """
    reads: list[float] = []
    ticks = iter(times)

    def monotonic() -> float:
        reads.append(next(ticks, 1_000.0))
        return reads[-1]

    monkeypatch.setattr(_io.time, "monotonic", monotonic)
    return reads


def test_a_short_file_of_long_rows_checks_its_time_budget(monkeypatch):
    reads = _clock(monkeypatch, 0.0)
    data = (HEADER + ('"DATA","P1","' + "x" * 2_000 + '"\r\n') * 200).encode()
    assert data.count(b"\r\n") < _parse._CHECK_EVERY
    f = ags4.read_bytes(data, time_budget=5)
    assert not f.complete
    assert f.limits_hit == ("time_budget",)
    assert f.notes[-1].rule_id == "ags4.timeout"
    assert len(f.groups["PROJ"].rows) < 200
    assert len(reads) >= 2
    _check(f, data)


def test_one_long_row_checks_its_time_budget_while_it_is_tokenized(monkeypatch):
    for tail in ("," * 1_000_000, ',"x"' * 300_000, ',"' + '""' * 400_000 + '"'):
        # Set the deadline, pass the check before the long line, then run out inside it.
        reads = _clock(monkeypatch, 0.0, 0.0)
        data = (HEADER + '"DATA"' + tail + "\r\n" + ROW).encode()
        f = ags4.read_bytes(data, time_budget=5)
        assert not f.complete
        assert f.limits_hit == ("time_budget",)
        assert (f.notes[-1].rule_id, f.notes[-1].line) == ("ags4.timeout", 5)
        assert f.groups["PROJ"].rows == ()
        assert len(reads) == 3
        _check(f, data)


def test_a_file_that_fits_its_time_budget_checks_it_without_stopping(monkeypatch):
    reads = _clock(monkeypatch, *([0.0] * 10_000))
    data = (HEADER + ('"DATA","P1","' + "x" * 2_000 + '"\r\n') * 200).encode()
    f = ags4.read_bytes(data, time_budget=5)
    assert f.complete
    assert len(reads) > 2


def test_out_of_time_before_any_group_is_a_partial_file_not_a_refusal():
    parsed = parse("\r\n" * 5_000 + HEADER, should_stop=lambda: True)
    assert parsed.groups == {}
    assert not parsed.complete
    assert parsed.notes[-1].rule_id == "ags4.timeout"


def test_a_generous_time_budget_changes_nothing():
    path = FIXTURES / "clean_4_1_1.ags"
    assert ags4.read(path, time_budget=60) == ags4.read(path)
    assert ags4.read(path, time_budget=60).complete


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf"), True, "1"])
def test_a_bad_time_budget_is_a_value_error(budget):
    with pytest.raises(ValueError, match="time_budget"):
        ags4.read_bytes(HEADER.encode(), time_budget=budget)
    with pytest.raises(ValueError, match="time_budget"):
        ags4.read(FIXTURES / "clean_4_1_1.ags", time_budget=budget)


# --- size, compression and what a path may be -----------------------------------------------


def test_max_bytes_is_checked_before_anything_is_parsed(tmp_path):
    data = (HEADER + ROW).encode()
    assert ags4.read_bytes(data, max_bytes=len(data)).complete
    for source in (data, bytearray(data), memoryview(data), io.BytesIO(data)):
        with pytest.raises(InstanceReadError) as caught:
            ags4.read_bytes(source, max_bytes=len(data) - 1, name="big.ags")
        assert caught.value.errno == errno.EFBIG
        assert caught.value.filename == "big.ags"
        assert isinstance(caught.value, OSError)
    path = tmp_path / "big.ags"
    path.write_bytes(data)
    assert ags4.read(path, max_bytes=len(data)).complete
    with pytest.raises(InstanceReadError) as caught:
        ags4.read(path, max_bytes=len(data) - 1)
    assert caught.value.errno == errno.EFBIG
    assert caught.value.strerror == f"AGS4 file exceeds {len(data) - 1} bytes"


@pytest.mark.parametrize("limit", [0, -1, 1.5, True, None, "10"])
def test_a_bad_max_bytes_is_a_value_error(limit):
    with pytest.raises(ValueError, match="max_bytes"):
        ags4.read_bytes(HEADER.encode(), max_bytes=limit)


def test_a_stream_is_read_only_one_byte_past_the_limit():
    class Endless(io.RawIOBase):
        read_total = 0

        def readable(self) -> bool:
            return True

        def readinto(self, buffer) -> int:
            self.read_total += len(buffer)
            buffer[:] = b"x" * len(buffer)
            return len(buffer)

    stream = Endless()
    with pytest.raises(InstanceReadError):
        ags4.read_bytes(stream, max_bytes=1_000)
    assert stream.read_total == 1_001
    assert not stream.closed


def test_compressed_input_is_refused(tmp_path):
    data = (HEADER + ROW).encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("site.ags", data)
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    for packed in (gzip.compress(data), archive.getvalue(), empty.getvalue()):
        with pytest.raises(InstanceReadError) as caught:
            ags4.read_bytes(packed)
        assert caught.value.errno == errno.EINVAL
        assert caught.value.strerror == "compressed input is not accepted"
        path = tmp_path / "packed.ags"
        path.write_bytes(packed)
        with pytest.raises(InstanceReadError):
            ags4.read(path)


def test_a_missing_file_a_folder_and_a_fifo_are_read_errors(tmp_path):
    with pytest.raises(InstanceReadError) as caught:
        ags4.read(tmp_path / "missing.ags")
    assert caught.value.errno == errno.ENOENT
    assert caught.value.filename == str(tmp_path / "missing.ags")
    with pytest.raises(InstanceReadError):
        ags4.read(tmp_path)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "pipe.ags"
        os.mkfifo(fifo)
        result: list[BaseException | None] = []

        def attempt() -> None:
            try:
                ags4.read(fifo)
            except BaseException as exc:  # reported to the test below
                result.append(exc)
            else:
                result.append(None)

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(10)
        assert not thread.is_alive(), "reading a FIFO must not block"
        assert isinstance(result[0], InstanceReadError)
        assert result[0].strerror == "not a regular file"


@pytest.mark.parametrize(
    "data",
    ["text", io.StringIO("text"), 5, None, [b"x"], memoryview(bytes(8))[::2]],
)
def test_what_is_not_bytes_is_a_type_error(data):
    with pytest.raises(TypeError):
        ags4.read_bytes(data)


def test_a_closed_stream_is_a_read_error():
    stream = io.BytesIO(b"x")
    stream.close()
    with pytest.raises(InstanceReadError):
        ags4.read_bytes(stream)


def test_nothing_named_in_a_file_is_opened(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    data = (
        '"GROUP","FILE"\r\n"HEADING","FILE_FSET","FILE_NAME"\r\n"UNIT","",""\r\n"TYPE","X","X"\r\n'
        f'"DATA","FS1","{secret}"\r\n"DATA","FS2","http://example.invalid/x.ags"\r\n'
    ).encode()
    opened: list[object] = []
    monkeypatch.setattr(
        os, "open", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError)
    )
    monkeypatch.setattr(
        Path, "open", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError)
    )
    f = ags4.read_bytes(data)
    assert f.groups["FILE"].rows[0] == ("FS1", str(secret))
    assert opened == []
    assert "socket" not in {name for name in sys.modules if name.startswith("pydiggs.ags4")}


# --- linear work ------------------------------------------------------------------------------


def _work(text: str) -> int:
    """Lines of pydiggs.ags4 code executed to parse ``text``: a deterministic work count."""
    count = 0
    folder = str(Path(_parse.__file__).parent)

    def tracer(frame, event, arg):
        nonlocal count
        if not frame.f_code.co_filename.startswith(folder):
            return None
        if event == "line":
            count += 1
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        with contextlib.suppress(DocumentError):
            parse(text)
    finally:
        sys.settrace(previous)
    return count


SHAPES = {
    "rows": lambda n: HEADER + ROW * n,
    "one long line of quotes": lambda n: HEADER + '"DATA",' + '"' * n,
    "one long line of doubled quotes": lambda n: HEADER + '"DATA","' + '""' * n + '"',
    "one long line of commas": lambda n: HEADER + '"DATA"' + "," * n,
    "unquoted fields": lambda n: HEADER + "DATA,a,b\r\n" * n,
    "open quotes": lambda n: HEADER + '"DATA","a\r\n' * n,
    "text after quotes": lambda n: HEADER + '"DATA","a"b,"c"d\r\n' * n,
    "short rows": lambda n: HEADER + '"DATA"\r\n' * n,
    "long rows": lambda n: HEADER + '"DATA","1","2","3","4","5","6"\r\n' * n,
    "duplicate groups": lambda n: (HEADER + ROW) * n,
    "many groups": lambda n: "".join(f'"GROUP","G{i}"\r\n"HEADING","H"\r\n' for i in range(n)),
    "unknown descriptors": lambda n: HEADER + '"NOPE","x"\r\n' * n,
    "rows before any group": lambda n: ROW * n + HEADER,
    "repeated header rows": lambda n: HEADER + '"HEADING","A"\r\n"UNIT","b"\r\n' * n,
    "blank lines": lambda n: HEADER + "\r\n \r\n" * n,
    "bare carriage returns": lambda n: HEADER + "\r" * n,
    "wide heading": lambda n: '"GROUP","A"\r\n"HEADING"' + ',"H"' * min(n, 2_000) + "\r\n",
}


@pytest.mark.scaling
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_work_is_linear_in_the_size_of_the_input(shape):
    make = SHAPES[shape]
    small, large = _work(make(500)), _work(make(1_000))
    assert large <= LINEAR * small, (shape, small, large)


@pytest.mark.scaling
def test_one_megabyte_reads_in_bounded_work_per_byte():
    text = HEADER + ROW * 50_000
    assert len(text) > 900_000
    assert _work(text) < 40 * 50_000  # a fixed number of lines of code for each row
