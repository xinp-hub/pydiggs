"""pydiggs.ags4: the data model and the lenient reader (property 1, reader parity).

Every file under ``tests/fixtures/ags4/`` was written for pyDIGGS with invented data
(Apache-2.0, like the rest of the repository): none is an AGS example file or taken
from another project, and no AGS data dictionary is bundled.

Parity is checked here against the stdlib ``csv`` module on those fixtures. The
python-ags4 oracle record (``conformance/ags4-expected.toml``, work package C0) is
compared with at the end of this file: every fixture, and every external file of the
AGS4 set that ``scripts/conformance.py fetch --set ags4`` has cached.
"""

from __future__ import annotations

import ast
import csv
import doctest
import hashlib
import inspect
import io
import json
import os
import pickle
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import pydiggs
from pydiggs import DocumentError, Finding, Severity, Step, ags4, explain_rule
from pydiggs.ags4 import _emit, _io, _parse, _table
from pydiggs.findings import run

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
CLEAN = sorted(FIXTURES.glob("clean_*.ags"))
CONFORMING = [*CLEAN, FIXTURES / "quoting.ags"]
SITE_GROUPS = ["PROJ", "TRAN", "TYPE", "UNIT", "LOCA", "GEOL", "SAMP", "ISPT"]
EXPECTED = ROOT / "conformance" / "ags4-expected.toml"

HEADER = b'"GROUP","PROJ"\r\n"HEADING","PROJ_ID","PROJ_NAME"\r\n"UNIT","",""\r\n"TYPE","ID","X"\r\n'


def _rules(file: ags4.File) -> list[str]:
    return [note.rule_id for note in file.notes]


def _csv_tables(data: bytes) -> dict[str, dict[str, list[list[str]]]]:
    """The tables of a conforming file as the stdlib csv module reads them."""
    tables: dict[str, dict[str, list[list[str]]]] = {}
    current: dict[str, list[list[str]]] = {}
    for row in csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True):
        if not row:
            continue
        if row[0] == "GROUP":
            current = tables.setdefault(row[1], {})
        else:
            current.setdefault(row[0], []).append(row[1:])
    return tables


# --- the public surface ---------------------------------------------------------------


def test_the_public_names_are_the_seven_of_work_package_c1():
    assert ags4.__all__ == ["AGS_VERSIONS", "File", "Group", "dumps", "read", "read_bytes", "write"]
    assert ags4.AGS_VERSIONS == ("4.0.3", "4.0.4", "4.1", "4.1.1", "4.2")
    for name in ags4.__all__:
        assert getattr(ags4, name) is not None
    assert not hasattr(pydiggs, "ags4_to_model")
    assert "ags4" not in pydiggs.__all__


def test_the_signatures_are_those_of_the_design():
    def shape(function):
        return [
            (p.name, p.kind.name, p.default)
            for p in inspect.signature(function).parameters.values()
        ]

    keyword, positional, empty = "KEYWORD_ONLY", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty
    limits = [
        ("encoding", keyword, None),
        ("max_bytes", keyword, 100 * 1024 * 1024),
        ("time_budget", keyword, None),
    ]
    assert shape(ags4.read) == [("path", positional, empty), *limits]
    assert shape(ags4.read_bytes) == [("data", positional, empty), ("name", keyword, None), *limits]
    assert shape(ags4.dumps) == [("file", positional, empty)]
    assert shape(ags4.write) == [
        ("file", positional, empty),
        ("path", positional, empty),
        ("line_ending", keyword, "\r\n"),
    ]
    assert _io.DEFAULT_MAX_BYTES == run.DEFAULT_MAX_BYTES


def test_import_pydiggs_does_not_import_ags4():
    code = "import sys, pydiggs; print('pydiggs.ags4' in sys.modules)"
    result = subprocess.run(  # noqa: S603  # our own interpreter, fixed code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert result.stdout.strip() == "False"


def test_the_ags4_modules_import_only_the_stdlib_and_the_pure_parts_of_pydiggs():
    allowed = {"pydiggs.ags4", "pydiggs.errors", "pydiggs.findings.model"}
    for module in (ags4, _table, _parse, _emit, _io):
        tree = ast.parse(Path(module.__file__).read_text("utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                if top == "pydiggs":
                    assert name in allowed or name.startswith("pydiggs.ags4."), name
                else:
                    assert top in sys.stdlib_module_names, (module.__name__, name)
                assert top != "csv"  # the tokenizer is hand-written (Q12)


@pytest.mark.parametrize("module", [ags4, _table], ids=lambda m: m.__name__)
def test_docstring_examples_run(module, monkeypatch):
    monkeypatch.chdir(ROOT)
    failed, attempted = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert attempted > 0
    assert failed == 0


def test_every_public_function_and_class_has_a_runnable_example():
    for name in ("read", "read_bytes", "dumps", "write", "File", "Group"):
        doc = inspect.getdoc(getattr(ags4, name)) or ""
        assert "Examples:" in doc, name
        assert ">>>" in doc, name


# --- conforming files -----------------------------------------------------------------


@pytest.mark.parametrize("path", CLEAN, ids=lambda p: p.name)
def test_a_clean_file_reads_without_notes(path):
    f = ags4.read(path)
    version = path.stem.removeprefix("clean_").replace("_", ".")
    assert version in ags4.AGS_VERSIONS
    assert f.version == version
    assert list(f.groups) == SITE_GROUPS
    assert f.notes == ()
    assert f.complete
    assert f.limits_hit == ()
    data = path.read_bytes()
    assert f.source == _table.SourceInfo(
        name=path.name, sha256=hashlib.sha256(data).hexdigest(), size=len(data), encoding="ascii"
    )


def test_there_is_one_clean_fixture_per_version():
    assert len(CLEAN) == len(ags4.AGS_VERSIONS)


def test_the_tables_are_strings_as_written():
    f = ags4.read(FIXTURES / "clean_4_1_1.ags")
    loca = f.groups["LOCA"]
    assert loca.name == "LOCA"
    assert loca.headings == (
        "LOCA_ID",
        "LOCA_TYPE",
        "LOCA_NATE",
        "LOCA_NATN",
        "LOCA_GL",
        "LOCA_FDEP",
        "LOCA_REM",
    )
    assert loca.units == ("", "", "m", "m", "m", "m", "")
    assert loca.types == ("ID", "PA", "2DP", "2DP", "2DP", "2DP", "X")
    assert loca.rows[0] == ("BH01", "CP", "451200.00", "206500.00", "12.50", "15.00", "")
    assert loca.rows[1][-1] == "Stopped on an obstruction"
    assert len(loca.rows) == 3
    assert loca.line == 31
    assert loca.records()[2]["LOCA_FDEP"] == "3.20"  # the trailing zero is kept: no conversion
    assert loca.column("LOCA_ID") == ("BH01", "BH02", "TP01")
    assert all(type(value) is str for row in loca.rows for value in row)
    with pytest.raises(KeyError):
        loca.column("LOCA_XXXX")


def test_quotes_commas_and_spaces_inside_values_are_kept():
    rows = ags4.read(FIXTURES / "quoting.ags").groups["PROJ"].rows
    assert rows == (
        ("PD-002", 'The "Old" Quay, east side', '""'),
        ("PD-003", " padded ", "a,b,c"),
        ("PD-004", '","', '"'),
        ("PD-005", "=SUM(A1:A2)", "+44 20 0000 0000"),
        ("PD-006", "", "tab\there"),
    )


@pytest.mark.parametrize("path", [*CONFORMING, FIXTURES / "utf8.ags"], ids=lambda p: p.name)
def test_reader_parity_with_the_stdlib_csv_module(path):
    f = ags4.read(path)
    expected = _csv_tables(path.read_bytes())
    assert list(f.groups) == list(expected)
    for name, group in f.groups.items():
        table = expected[name]
        assert [list(group.headings)] == table["HEADING"]
        assert [list(group.units)] == table["UNIT"]
        assert [list(group.types)] == table["TYPE"]
        assert [list(row) for row in group.rows] == table.get("DATA", [])


def test_path_and_bytes_and_stream_give_the_same_file():
    path = FIXTURES / "clean_4_2.ags"
    data = path.read_bytes()
    from_path = ags4.read(path)
    for source in (data, bytearray(data), memoryview(data), io.BytesIO(data)):
        f = ags4.read_bytes(source, name="any/folder/" + path.name)
        assert f == from_path
        assert f.source == from_path.source
    assert ags4.read(str(path)) == from_path
    assert ags4.read_bytes(data).source.name is None


# --- encoding -------------------------------------------------------------------------


def test_utf8_reads_as_utf8_without_a_note():
    f = ags4.read(FIXTURES / "utf8.ags")
    assert f.source.encoding == "utf-8"
    assert f.groups["PROJ"].rows[0][1] == "Slope of 12\u00b0 \u2013 caf\u00e9 site"
    assert f.notes == ()


def test_a_byte_order_mark_is_dropped_and_noted_under_rule_1():
    f = ags4.read(FIXTURES / "utf8_bom.ags")
    assert f.source.encoding == "utf-8-sig"
    assert f == ags4.read(FIXTURES / "clean_4_1_1.ags")
    assert [(n.rule_id, n.severity, n.line) for n in f.notes] == [
        ("ags4.rule-1", Severity.ERROR, 1)
    ]


def test_windows_1252_is_assumed_with_a_warning():
    f = ags4.read(FIXTURES / "cp1252.ags")
    assert f.source.encoding == "cp1252"
    assert f == ags4.read(FIXTURES / "utf8.ags")
    (note,) = f.notes
    assert (note.step, note.rule_id, note.severity) == (
        Step.AGS4,
        "ags4.encoding-assumed",
        Severity.WARNING,
    )
    assert "Windows-1252" in note.message


def test_bytes_windows_1252_does_not_define_are_replaced_and_said_so():
    f = ags4.read_bytes(HEADER + b'"DATA","P1","caf\xe9 \x81"\r\n')
    assert f.groups["PROJ"].rows == (("P1", "caf\u00e9 \ufffd"),)
    assert "U+FFFD" in f.notes[0].message


@pytest.mark.parametrize(
    ("encoding", "reported"),
    [
        ("cp1252", "cp1252"),
        ("windows-1252", "cp1252"),
        ("latin-1", "iso8859-1"),
        ("UTF-8", "utf-8"),
        ("utf-8-sig", "utf-8-sig"),
    ],
)
def test_an_explicit_encoding_is_read_strictly_and_without_a_note(encoding, reported):
    f = ags4.read_bytes(HEADER + b'"DATA","P1","plain"\r\n', encoding=encoding)
    assert f.source.encoding == reported
    assert f.notes == ()


def test_a_byte_order_mark_is_dropped_under_an_explicit_utf8_too():
    data = (FIXTURES / "utf8_bom.ags").read_bytes()
    for encoding in ("utf-8", "utf-8-sig"):
        f = ags4.read_bytes(data, encoding=encoding)
        assert f == ags4.read(FIXTURES / "clean_4_1_1.ags")
        assert _rules(f) == ["ags4.rule-1"]
    assert "PROJ" not in ags4.read_bytes(data, encoding="cp1252").groups  # read as asked


def test_an_explicit_encoding_the_bytes_do_not_fit_is_a_value_error():
    data = (FIXTURES / "cp1252.ags").read_bytes()
    assert ags4.read_bytes(data, encoding="cp1252").notes == ()
    with pytest.raises(ValueError, match="not valid text in the encoding asked for"):
        ags4.read_bytes(data, encoding="utf-8")
    with pytest.raises(ValueError, match="not valid text"):
        ags4.read_bytes(data, encoding="ascii")


@pytest.mark.parametrize(
    "encoding", ["utf-16", "rot13", "zlib", "punycode", "idna", "nope", "", "a\x00b", 5]
)
def test_only_the_listed_encodings_are_accepted(encoding):
    with pytest.raises(ValueError, match="encoding must be"):
        ags4.read_bytes(HEADER, encoding=encoding)


# --- versions -------------------------------------------------------------------------


def test_an_unknown_version_is_noted_and_version_is_none():
    data = (FIXTURES / "clean_4_1_1.ags").read_bytes().replace(b'"4.1.1"', b'"5.0"')
    f = ags4.read_bytes(data)
    assert f.version is None
    assert f.groups["TRAN"].records()[0]["TRAN_AGS"] == "5.0"
    (note,) = f.notes
    assert (note.rule_id, note.severity, note.path) == (
        "ags4.version-unknown",
        Severity.WARNING,
        "TRAN",
    )
    assert note.line == f.groups["TRAN"].line
    assert "5.0" not in note.message  # file text is not echoed
    assert "4.2" in note.message


def test_no_tran_group_means_no_version_and_no_note():
    f = ags4.read_bytes(HEADER + b'"DATA","P1","x"\r\n')
    assert f.version is None
    assert f.notes == ()


# --- leniency: every workaround is a note ---------------------------------------------


def test_a_file_that_breaks_many_rules_still_reads_and_each_workaround_is_a_note():
    f = ags4.read(FIXTURES / "messy.ags")
    assert f.complete
    assert list(f.groups) == ["PROJ", "LOCA", "TRAN", "SAMP"]
    proj = f.groups["PROJ"]
    assert (proj.headings, proj.units, proj.types) == (
        ("PROJ_ID", "PROJ_NAME"),
        ("", ""),
        ("ID", "X"),
    )
    assert proj.rows == (("PD-008", ""), ("PD-009", "Two"), ("PD-010", "unquoted"))
    loca = f.groups["LOCA"]
    assert (loca.headings, loca.units, loca.types, loca.rows) == (
        ("LOCA_ID",),
        (),  # no UNIT row: kept missing, not invented
        (),  # no TYPE row
        (("BH01",),),
    )
    assert f.groups["SAMP"] == ags4.Group(name="SAMP", headings=())
    assert f.version is None
    assert [(n.rule_id, n.line, n.path) for n in f.notes] == [
        ("ags4.rule-2", 1, None),  # a row before the first GROUP row
        ("ags4.rule-4", 2, None),  # GROUP row with an extra field
        ("ags4.rule-2b", 4, "PROJ"),  # TYPE before UNIT
        ("ags4.rule-2b", 5, "PROJ"),  # UNIT after TYPE
        ("ags4.rule-4", 6, "PROJ/1"),  # short DATA row, padded
        ("ags4.rule-4", 7, "PROJ/2"),  # long DATA row, cut
        ("ags4.rule-5", 8, None),  # unquoted fields
        ("ags4.rule-3", 9, None),  # unknown descriptor
        ("ags4.rule-2b", 12, "LOCA"),  # DATA before HEADING
        ("ags4.rule-2b", 14, "LOCA"),  # a second HEADING row
        ("ags4.rule-2b", 11, "LOCA"),  # no UNIT row
        ("ags4.rule-2b", 11, "LOCA"),  # no TYPE row
        ("ags4.duplicate-group", 17, None),  # PROJ again
        ("ags4.rule-4", 27, None),  # GROUP row without a name
        ("ags4.rule-2b", 30, "SAMP"),  # no HEADING row
        ("ags4.rule-2b", 30, "SAMP"),
        ("ags4.rule-2b", 30, "SAMP"),
        ("ags4.rule-2a", 30, None),  # the last line has no CR LF
        ("ags4.version-unknown", 21, "TRAN"),
    ]
    for note in f.notes:
        assert isinstance(note, Finding)
        assert note.step is Step.AGS4
        assert note.layer == "native"
        assert note.xpath is None
        assert note.gml_id is None


def test_line_endings_other_than_crlf_are_read_and_counted_once():
    f = ags4.read(FIXTURES / "lf_only.ags")
    assert f == ags4.read(FIXTURES / "clean_4_1_1.ags")
    (note,) = f.notes
    assert (note.rule_id, note.line) == ("ags4.rule-2a", 1)
    assert note.message == "Lines that do not end with CR LF: 60; the first is line 1."
    mac = (FIXTURES / "clean_4_1_1.ags").read_bytes().replace(b"\r\n", b"\r")
    assert ags4.read_bytes(mac) == f
    assert _rules(ags4.read_bytes(mac)) == ["ags4.rule-2a"]


@pytest.mark.parametrize(
    ("line", "row"),
    [
        (b'"DATA","P1",unquoted', ("P1", "unquoted")),
        (b'DATA,"P1","x"', ("P1", "x")),
        (b'"DATA", "P1" , "x"', ("P1", "x")),
        (b'"DATA","P1","open', ("P1", "open")),
        (b'"DATA","P1","a"b', ("P1", "ab")),
        (b'"DATA","P1","a" b,', ("P1", "a b")),
        (b'"DATA","P1",', ("P1", "")),
        (b'"DATA","P1","x"\t', ("P1", "x")),
        (b'"DATA","P1","say ""hi"" "', ("P1", 'say "hi" ')),
    ],
)
def test_a_badly_quoted_row_is_read_as_far_as_it_can_be_told(line, row):
    f = ags4.read_bytes(HEADER + line + b"\r\n")
    assert f.groups["PROJ"].rows == (row,)
    expected = [] if line.endswith(b'"" "') else ["ags4.rule-5"]
    if line.endswith(b" b,"):
        expected.append("ags4.rule-4")  # the trailing comma makes a third, empty value
    assert _rules(f) == expected


def test_an_open_quote_ends_with_its_line_and_never_swallows_the_next_row():
    f = ags4.read_bytes(HEADER + b'"DATA","P1","open\r\n"DATA","P2","next"\r\n')
    assert f.groups["PROJ"].rows == (("P1", "open"), ("P2", "next"))


def test_blank_and_whitespace_lines_are_skipped_anywhere():
    data = (
        b"\r\n \r\n" + HEADER.replace(b"\r\n", b"\r\n\r\n") + b'\t\r\n"DATA","P1","x"\r\n\r\n\r\n'
    )
    f = ags4.read_bytes(data)
    assert f.groups["PROJ"].rows == (("P1", "x"),)
    assert f.notes == ()


def test_notes_are_listed_up_to_a_hundred_per_rule_and_then_counted():
    data = HEADER + b'"DATA","only one"\r\n' * 250 + b'"NOPE"\r\n' * 3
    f = ags4.read_bytes(data)
    assert len(f.groups["PROJ"].rows) == 250
    rule_4 = [n for n in f.notes if n.rule_id == "ags4.rule-4"]
    assert len(rule_4) == _parse.MAX_NOTES_PER_RULE + 1
    assert rule_4[-1].message == "150 more notes of this rule are not listed."
    assert rule_4[-1].line is None
    assert rule_4[99].path == "PROJ/100"
    assert _rules(f).count("ags4.rule-3") == 3


def test_messages_never_echo_names_that_are_not_plain():
    bad = 'PR"OJ\x1b[31m<script>' + "x" * 100
    data = f'"GROUP","{bad.replace(chr(34), chr(34) * 2)}"\r\n"DATA","x"\r\n'.encode()
    f = ags4.read_bytes(data + data)
    assert list(f.groups) == [bad]
    for note in f.notes:
        assert note.path is None
        assert "\x1b" not in note.message
        assert "x" * 40 not in note.message
        assert len(note.message) < 200
    assert "ags4.duplicate-group" in _rules(f)
    plain = ags4.read_bytes(b'"GROUP","LOCA"\r\n"DATA","x"\r\n')
    assert "Group LOCA " in plain.notes[-1].message


# --- what is refused ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "kind", "rule"),
    [
        (b"", "not-ags4", "ags4.rule-2"),
        (b"\r\n\r\n", "not-ags4", "ags4.rule-2"),
        (b"just some text\r\nwith, commas\r\n", "not-ags4", "ags4.rule-2"),
        (b'"HEADING","A"\r\n"DATA","1"\r\n', "not-ags4", "ags4.rule-2"),
        (b"<Diggs/>", "not-ags4", "ags4.rule-2"),
        (b'"GROUP","PROJ"\r\n\x00', "not-ags4", "ags4.rule-1"),
        ('"GROUP","PROJ"\r\n'.encode("utf-16"), "not-ags4", "ags4.rule-1"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "not-ags4", "ags4.rule-1"),
        ((FIXTURES / "ags3.ags").read_bytes(), "unsupported-version", "ags4.unsupported-version"),
    ],
)
def test_no_table_at_all_is_a_document_error_with_its_finding(data, kind, rule):
    with pytest.raises(DocumentError) as caught:
        ags4.read_bytes(data, name="x.ags")
    error = caught.value
    assert error.kind == kind
    assert isinstance(error, ValueError)
    assert error.finding is not None
    assert (error.finding.step, error.finding.rule_id, error.finding.severity) == (
        Step.AGS4,
        rule,
        Severity.ERROR,
    )
    assert str(error) == error.finding.message


def test_a_group_row_is_enough_for_a_file():
    f = ags4.read_bytes(b'"GROUP","PROJ"\r\n')
    assert f.groups["PROJ"] == ags4.Group(name="PROJ", headings=())
    assert _rules(f) == ["ags4.rule-2b"] * 3


# --- the data model -------------------------------------------------------------------


def test_group_and_file_are_frozen_and_checked():
    group = ags4.Group(name="PROJ", headings=["PROJ_ID"], units=[""], types=["ID"], rows=[["P1"]])
    assert (group.headings, group.rows) == (("PROJ_ID",), (("P1",),))
    assert group.line is None
    with pytest.raises(AttributeError):
        group.name = "X"  # type: ignore[misc]
    f = ags4.File(groups=[group])
    assert f.groups["PROJ"] is group
    assert (f.source, f.notes, f.complete, f.limits_hit, f.version) == (None, (), True, (), None)
    with pytest.raises(TypeError):
        f.groups["X"] = group  # type: ignore[index]
    with pytest.raises(AttributeError):
        f.complete = False  # type: ignore[misc]
    assert ags4.File(groups={"PROJ": group}) == f
    assert ags4.File(groups=()) == ags4.File(groups={})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": 3, "headings": ()},
        {"name": "A", "headings": "PROJ_ID"},
        {"name": "A", "headings": (1,)},
        {"name": "A", "headings": (), "units": None},
        {"name": "A", "headings": (), "rows": "x"},
        {"name": "A", "headings": (), "rows": ["x"]},
        {"name": "A", "headings": (), "rows": [(1.5,)]},
        {"name": "A", "headings": (), "rows": 5},
    ],
)
def test_a_group_holds_only_strings(kwargs):
    with pytest.raises(TypeError):
        ags4.Group(**kwargs)


def test_a_file_refuses_groups_it_cannot_key():
    group = ags4.Group(name="PROJ", headings=())
    with pytest.raises(ValueError, match="same name"):
        ags4.File(groups=[group, group])
    with pytest.raises(ValueError, match="not the name"):
        ags4.File(groups={"LOCA": group})
    for bad in ("PROJ", 5, [5], [("PROJ", group)]):
        with pytest.raises(TypeError):
            ags4.File(groups=bad)
    with pytest.raises(ValueError, match="line"):
        ags4.Group(name="A", headings=(), line=0)


def test_equality_is_about_the_tables_only():
    a = ags4.read(FIXTURES / "clean_4_1_1.ags")
    b = ags4.read(FIXTURES / "lf_only.ags")
    assert a == b
    assert a.notes != b.notes
    assert a.source != b.source
    assert a != ags4.read(FIXTURES / "clean_4_2.ags")
    built = ags4.Group(name="A", headings=("H",), units=("",), types=("X",), line=7)
    assert built == ags4.Group(name="A", headings=("H",), units=("",), types=("X",))
    assert hash(built) == hash(ags4.Group(name="A", headings=("H",), units=("",), types=("X",)))


def test_a_file_pickles_and_copies():
    import copy

    f = ags4.read(FIXTURES / "messy.ags")
    for again in (pickle.loads(pickle.dumps(f)), copy.deepcopy(f)):  # noqa: S301  # our own bytes
        assert again == f
        assert (again.source, again.notes, again.complete, again.limits_hit) == (
            f.source,
            f.notes,
            f.complete,
            f.limits_hit,
        )


def test_records_of_a_ragged_group_built_in_code():
    ragged = ags4.Group(name="A", headings=("H1", "H2"), rows=[("1",), ("1", "2", "3")])
    assert ragged.records() == [{"H1": "1"}, {"H1": "1", "H2": "2"}]
    with pytest.raises(ValueError, match="no value"):
        ragged.column("H2")


# --- the reported name ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("site.ags", "site.ags"),
        ("/home/me/secret/site.ags", "site.ags"),
        ("C:\\Users\\me\\site.ags", "site.ags"),
        ("..", None),
        ("folder/", None),
        ("", None),
        ("a\x1b[31mb\u202e.ags", "a\\x1b[31mb\\u202e.ags"),
        ("x" * 300, "x" * 255),
    ],
)
def test_the_reported_name_is_a_bare_escaped_file_name(name, shown):
    assert ags4.read_bytes(HEADER, name=name).source.name == shown
    if shown is not None and "\\" not in shown:
        assert shown == run._input_name(name)  # the rule validate_bytes() applies


def test_a_name_must_be_text():
    with pytest.raises(TypeError, match="name must be str"):
        ags4.read_bytes(HEADER, name=b"site.ags")  # type: ignore[arg-type]


# --- the notes are catalogued findings ------------------------------------------------


def test_every_reader_rule_id_is_in_the_catalogue_at_its_severity():
    ids = [getattr(_parse, name) for name in _parse.__all__ if name.isupper()]
    assert len(ids) == 13
    for rule_id in ids:
        info = explain_rule(rule_id)
        assert info.known, rule_id
        assert info.step is Step.AGS4
        assert info.severities == (_parse.note(rule_id, "x").severity,), rule_id
    catalogued = {r.id for r in pydiggs.rule_catalogue() if r.step is Step.AGS4}
    assert catalogued == set(ids)


# --- the recorded oracle (work package C0) ---------------------------------------------


def _oracle_records() -> list[dict]:
    """The ``[[file]]`` records of ``conformance/ags4-expected.toml``; none without it."""
    if not EXPECTED.is_file():
        return []
    return tomllib.loads(EXPECTED.read_text("utf-8")).get("file", [])


def _rows_sha256(rows: tuple[tuple[str, ...], ...]) -> str:
    """The oracle's ``rows_sha256`` (scripts/ags4_oracle.py ``rows_digest``) of ``rows``."""
    text = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("record", _oracle_records(), ids=lambda record: record["key"])
def test_reader_parity_with_the_recorded_oracle(record):
    """pyDIGGS reads every file of the AGS4 set as python-ags4 did when it was recorded.

    Fixtures are always there. An external file is there only after
    ``scripts/conformance.py fetch --set ags4`` (CI's conformance job runs it, and sets
    ``PYDIGGS_AGS4_CORPUS=required`` so that a missing file fails there); elsewhere a
    file not fetched is skipped.
    """
    path = ROOT / record["path"]
    if not path.is_file():
        if os.environ.get("PYDIGGS_AGS4_CORPUS") == "required":
            pytest.fail(f"{record['key']} is not fetched: run conformance.py fetch --set ags4")
        pytest.skip(f"{record['key']} is not fetched (conformance.py fetch --set ags4)")
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == record["sha256"], "re-record the oracle"
    if not record["readable"]:
        # python-ags4 refused the file: pyDIGGS reads it leniently, saying what it
        # worked around, or refuses it too.
        try:
            f = ags4.read_bytes(data)
        except DocumentError:
            return
        assert f.notes
        return
    if not record["group"]:
        # python-ags4 found no table (an AGS3 file): pyDIGGS has none to build either.
        with pytest.raises(DocumentError):
            ags4.read_bytes(data)
        return
    f = ags4.read_bytes(data)
    assert list(f.groups) == [table["name"] for table in record["group"]]
    for table in record["group"]:
        group = f.groups[table["name"]]
        where = table["name"]
        assert list(group.headings) == table["headings"], where
        assert list(group.units) == table["units"], where
        assert list(group.types) == table["types"], where
        if "rows" in table:
            assert [list(row) for row in group.rows] == table["rows"], where
        assert len(group.rows) == table["row_count"], where
        assert _rows_sha256(group.rows) == table["rows_sha256"], where


def test_the_oracle_record_covers_every_fixture():
    if not EXPECTED.is_file():
        pytest.skip("conformance/ags4-expected.toml is not in this tree")
    recorded = {record["key"] for record in _oracle_records()}
    assert {f"fixtures/{path.name}" for path in FIXTURES.glob("*.ags")} <= recorded


def test_a_group_without_a_unit_row_has_empty_units_as_the_oracle_records():
    f = ags4.read(FIXTURES / "rule_2b.ags")
    loca = f.groups["LOCA"]
    assert loca.units == ()  # no row: an empty tuple, not one invented of empty strings
    assert loca.types == ("ID", "PA", "2DP", "2DP", "2DP", "2DP")
    assert [(n.rule_id, n.line, n.path, n.message) for n in f.notes] == [
        (
            "ags4.rule-2b",
            40,
            "LOCA",
            "The TYPE row of group LOCA is out of order (the order is GROUP, HEADING, UNIT,"
            " TYPE, DATA); it was read all the same.",
        ),
        ("ags4.rule-2b", 38, "LOCA", "Group LOCA has no UNIT row."),
    ]
    assert ags4.dumps(f) == (FIXTURES / "rule_2b.ags").read_bytes()
