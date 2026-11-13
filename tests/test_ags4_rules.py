"""pydiggs.ags4.check: the AGS4 rules as findings, both tiers, and ``pydiggs ags4 check``.

Rule numbers are those of section 4.1.1 of the AGS4 specification, Edition 4.2
(December 2025). Every ``tests/fixtures/ags4/c2_*.ags`` file was written for pyDIGGS
with invented data; ``c2_dictionary.ags`` is a small invented dictionary in the AGS4
DICT layout for the dictionary tier, not the AGS data dictionary (none is bundled).

The rule parity test at the end compares with python-ags4's recorded results
(``conformance/ags4-expected.toml``, work package C0): every fixture, and every BGS file
of the AGS4 corpus once ``scripts/conformance.py fetch --set ags4`` has fetched it (with
``PYDIGGS_AGS4_CORPUS=required``, as in CI's conformance job, a file not fetched fails).
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import hashlib
import inspect
import json
import os
import sys
import tomllib
import types
from pathlib import Path

import pytest

from pydiggs import DocumentError, ResourceError, Severity, Step, ags4, explain_rule
from pydiggs.ags4 import _check, _dictionary, _io, _parse, _rules
from pydiggs.cli import main
from pydiggs.findings.rules import rule_catalogue

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
CLEAN = FIXTURES / "c2_clean.ags"
RULE_10A = FIXTURES / "c2_rule_10a.ags"
USER_GROUP = FIXTURES / "c2_user_group.ags"
BROKEN = FIXTURES / "c2_broken.ags"
DICTIONARY = FIXTURES / "c2_dictionary.ags"
EXPECTED = ROOT / "conformance" / "ags4-expected.toml"
NOT_CHECKED = "ags4.dictionary-not-checked"
SECRET = "Q7SECRETQ7"  # noqa: S105  # a marker value, not a secret


def errors(report):
    return sorted((f.rule_id, f.line, f.path) for f in report.findings if f.severity == "error")


def ids(report):
    return {f.rule_id for f in report.findings}


def replace_group(file, name, **changes):
    """``file`` with group ``name`` changed (or removed with ``changes=None``)."""
    groups = []
    for old in file.groups.values():
        if old.name == name and changes.get("drop"):
            continue
        changed = {**changes, "line": None} if old.name == name else {"line": None}
        groups.append(dataclasses.replace(old, **changed))  # built in code: no lines
    return ags4.File(groups=groups)


def add_group(file, group):
    plain = [dataclasses.replace(g, line=None) for g in file.groups.values()]
    return ags4.File(groups=[*plain, group])


def group(name, headings, types, *rows, units=None):
    return ags4.Group(
        name=name,
        headings=tuple(headings),
        units=tuple(units or ("",) * len(headings)),
        types=tuple(types),
        rows=[tuple(r) for r in rows],
    )


@pytest.fixture(scope="module")
def clean():
    return ags4.read(CLEAN)


# --- the API --------------------------------------------------------------------------


def test_the_signature_is_that_of_the_design():
    params = inspect.signature(ags4.check).parameters
    assert [(p.name, p.kind.name, p.default) for p in params.values()] == [
        ("source", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("dictionary", "KEYWORD_ONLY", None),
        ("encoding", "KEYWORD_ONLY", None),
        ("max_bytes", "KEYWORD_ONLY", 100 * 1024 * 1024),
        ("max_findings", "KEYWORD_ONLY", 10_000),
        ("time_budget", "KEYWORD_ONLY", None),
    ]
    assert "Examples:" in (ags4.check.__doc__ or "")


def test_a_clean_file_passes_and_says_the_dictionary_rules_were_partly_checked():
    report = ags4.check(CLEAN)
    assert report.ok
    assert report.verdict == "valid"
    assert report.format == "ags4"
    assert report.diggs_version is None
    assert dict(report.steps) == {Step.AGS4: "passed"}
    assert [(f.rule_id, f.severity) for f in report.findings] == [(NOT_CHECKED, Severity.INFO)]
    assert report.path == "tests/fixtures/ags4/c2_clean.ags" or report.path.endswith("c2_clean.ags")
    assert len(report.sha256) == 64
    json.loads(report.to_json())


def test_rule_10a_from_the_files_own_dict_group():
    report = ags4.check(RULE_10A)
    assert errors(report) == [("ags4.rule-10a", 77, "XTST/2")]
    assert dict(report.steps) == {Step.AGS4: "failed"}
    assert ags4.check(USER_GROUP).ok


def test_a_file_breaking_several_rules_reports_each_where_it_is():
    report = ags4.check(BROKEN)
    assert errors(report) == [
        ("ags4.rule-15", 42, "LOCA"),
        ("ags4.rule-16", 46, "LOCA/3/LOCA_TYPE"),
        ("ags4.rule-16a", 45, "LOCA/2/LOCA_TYPE"),
        ("ags4.rule-17", 43, "LOCA"),
        ("ags4.rule-20", None, None),
        ("ags4.rule-8", 44, "LOCA/1/LOCA_GL"),
    ]
    warnings = [(f.rule_id, f.line, f.path) for f in report.findings if f.severity == "warning"]
    assert warnings == [("ags4.formula-text", 44, "LOCA/1/LOCA_REM")]


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("clean_*.ags")), ids=lambda p: p.name)
def test_every_clean_fixture_passes_with_only_the_dictionary_note(path):
    # Each lists its abbreviations (LOCA_TYPE, GEOL_LEG, SAMP_TYPE) in an ABBR group, so
    # Rule 16 holds; python-ags4's recorded check of them reports no error either.
    report = ags4.check(path)
    assert report.ok
    assert [f.rule_id for f in report.findings] == [NOT_CHECKED]


def test_the_reader_notes_are_part_of_the_report():
    report = ags4.check(FIXTURES / "messy.ags")
    reader = {f.rule_id for f in ags4.read(FIXTURES / "messy.ags").notes}
    assert reader <= ids(report)
    assert {"ags4.rule-15", "ags4.rule-17"} <= ids(report)


def test_a_file_already_read_or_built_in_code_can_be_checked(clean):
    report = ags4.check(clean)
    assert report.ok
    assert report.path == "c2_clean.ags"
    built = ags4.check(ags4.File(groups=list(clean.groups.values())))
    assert built.ok
    assert built.path == "input.ags"
    assert built.sha256 == report.sha256  # the bytes the writer gives back are the file's


def test_a_not_ags4_file_raises_as_read_does(tmp_path):
    path = tmp_path / "x.ags"
    path.write_bytes(b"no groups here\r\n")
    with pytest.raises(DocumentError) as caught:
        ags4.check(path)
    assert caught.value.kind == "not-ags4"


def test_bad_arguments_are_refused(clean, tmp_path):
    with pytest.raises(TypeError):
        ags4.check(b"bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ags4.check(clean, dictionary=42)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_findings"):
        ags4.check(clean, max_findings=0)
    with pytest.raises(ResourceError, match="could not be read"):
        ags4.check(clean, dictionary=tmp_path / "missing.ags")
    with pytest.raises(ResourceError, match="no DICT group"):
        ags4.check(clean, dictionary=FIXTURES / "clean_4_1_1.ags")
    (tmp_path / "bin.ags").write_bytes(b"\x00\x01")
    with pytest.raises(ResourceError, match="not an AGS4 file"):
        ags4.check(clean, dictionary=tmp_path / "bin.ags")


# --- the dictionary tier --------------------------------------------------------------


def test_with_a_dictionary_the_standard_groups_are_checked(clean):
    dictionary = ags4.read(DICTIONARY)
    report = ags4.check(CLEAN, dictionary=DICTIONARY)
    assert report.ok
    assert NOT_CHECKED not in ids(report)
    assert ags4.check(USER_GROUP, dictionary=dictionary).ok

    loca = clean.groups["LOCA"]
    swapped = replace_group(
        clean,
        "LOCA",
        headings=(loca.headings[1], loca.headings[0], *loca.headings[2:]),
        units=(loca.units[1], loca.units[0], *loca.units[2:]),
        types=(loca.types[1], loca.types[0], *loca.types[2:]),
        rows=[(r[1], r[0], *r[2:]) for r in loca.rows],
    )
    assert ("ags4.rule-7", None, "LOCA") in errors(ags4.check(swapped, dictionary=dictionary))

    no_fdep = replace_group(
        clean,
        "LOCA",
        headings=loca.headings[:3],
        units=loca.units[:3],
        types=loca.types[:3],
        rows=[r[:3] for r in loca.rows],
    )
    assert ("ags4.rule-10b", None, "LOCA") in errors(ags4.check(no_fdep, dictionary=dictionary))
    empty_fdep = replace_group(clean, "LOCA", rows=[(*r[:3], "") for r in loca.rows])
    found = errors(ags4.check(empty_fdep, dictionary=dictionary))
    assert ("ags4.rule-10b", None, "LOCA/1/LOCA_FDEP") in found

    geol = clean.groups["GEOL"]
    orphan = replace_group(clean, "GEOL", rows=[*geol.rows, ("ZZ99", "0.00", "1.00", "x", "201")])
    assert ("ags4.rule-10c", None, "GEOL/4") in errors(ags4.check(orphan, dictionary=dictionary))
    no_parent = replace_group(clean, "LOCA", drop=True)
    assert ("ags4.rule-10c", None, "GEOL") in errors(ags4.check(no_parent, dictionary=dictionary))
    duplicate = replace_group(clean, "GEOL", rows=[*geol.rows, geol.rows[0]])
    assert ("ags4.rule-10a", None, "GEOL/4") in errors(ags4.check(duplicate, dictionary=dictionary))

    extra = add_group(clean, group("ZZZZ", ["ZZZZ_ID"], ["X"], ["1"]))
    found = errors(ags4.check(extra, dictionary=dictionary))
    assert ("ags4.rule-9", None, "ZZZZ") in found
    assert ("ags4.rule-18", None, None) in found
    # Without the dictionary, the same file is not judged on names it cannot know.
    assert {"ags4.rule-9", "ags4.rule-18"}.isdisjoint(ids(ags4.check(extra)))


def test_the_dictionary_view_reads_statuses_and_lays_the_files_dict_over_it():
    view = _dictionary.from_file(ags4.read(DICTIONARY))
    geol = view.group("GEOL")
    assert geol is not None
    assert geol.defined
    assert geol.parent == "LOCA"
    assert geol.keys == ("LOCA_ID", "GEOL_TOP", "GEOL_BASE")
    assert geol.required == ("LOCA_ID", "GEOL_TOP", "GEOL_BASE", "GEOL_DESC")
    own = _dictionary.from_file(ags4.read(USER_GROUP))
    merged = _dictionary.overlay(view, own)
    assert merged.group("XTST") is not None
    assert merged.group("XTST").defined
    assert list(merged.group("GEOL").headings) == list(geol.headings)
    assert not _dictionary.build(None)


def with_concatenator(file, concatenator):
    """``file`` with TRAN_RCON ``concatenator`` and each combined DICT_STAT joined by it."""
    tran = file.groups["TRAN"]
    file = replace_group(file, "TRAN", rows=[(*tran.rows[0][:-1], concatenator)])
    dict_group = file.groups["DICT"]
    at = dict_group.headings.index("DICT_STAT")
    rows = [
        tuple(v.replace("+", concatenator) if i == at else v for i, v in enumerate(row))
        for row in dict_group.rows
    ]
    return replace_group(file, "DICT", rows=rows)


def test_combined_statuses_are_split_on_the_files_own_concatenator(clean):
    # A supplied dictionary whose TRAN_RCON is ";" writes KEY;REQUIRED.
    dictionary = with_concatenator(ags4.read(DICTIONARY), ";")
    geol = _dictionary.from_file(dictionary).group("GEOL")
    assert geol is not None
    assert geol.keys == ("LOCA_ID", "GEOL_TOP", "GEOL_BASE")
    assert geol.required == ("LOCA_ID", "GEOL_TOP", "GEOL_BASE", "GEOL_DESC")
    rows = clean.groups["GEOL"].rows
    duplicate = replace_group(clean, "GEOL", rows=[*rows, rows[0]])
    assert ("ags4.rule-10a", None, "GEOL/4") in errors_of(duplicate, dictionary=dictionary)
    # The file's own DICT group, read with the file's TRAN_RCON.
    user = ags4.read(USER_GROUP)
    dict_rows = user.groups["DICT"].rows
    at = user.groups["DICT"].headings.index("DICT_STAT")
    combined = [
        tuple("KEY+REQUIRED" if i == at and row[2] == "XTST_REF" else v for i, v in enumerate(row))
        for row in dict_rows
    ]
    user = with_concatenator(replace_group(user, "DICT", rows=combined), ";")
    xtst = user.groups["XTST"]
    empty = replace_group(user, "XTST", rows=[("XB01", "", "1.25"), *xtst.rows[1:]])
    assert ("ags4.rule-10b", None, "XTST/1/XTST_REF") in errors_of(empty)


def test_a_dictionary_read_only_in_part_is_refused(clean, monkeypatch, capsys):
    partial = ags4.File(groups=list(ags4.read(DICTIONARY).groups.values()), complete=False)
    with pytest.raises(ResourceError, match="read only in part"):
        ags4.check(clean, dictionary=partial)
    # A limit stops the read of the dictionary file: refused, not taken as whole.
    monkeypatch.setattr(_parse, "Limits", functools.partial(_parse.Limits, max_rows=5))
    with pytest.raises(ResourceError, match="read only in part"):
        ags4.check(clean, dictionary=DICTIONARY)
    code, _, err = run_cli(capsys, CLEAN, "--dictionary", DICTIONARY)
    assert code == 2
    assert "read only in part" in err


# --- each rule, on files built in code -----------------------------------------------


def errors_of(file, **kwargs):
    return errors(ags4.check(file, **kwargs))


def test_rule_1_and_rule_6_values(clean):
    loca = clean.groups["LOCA"]
    rows = [(*loca.rows[0][:2], loca.rows[0][2], loca.rows[0][3]), *loca.rows[1:]]
    accented = replace_group(clean, "PROJ", rows=[("C2-001", "Caf\u00e9", "x", "y")])
    assert ("ags4.rule-1", None, "PROJ/1") in errors_of(accented)
    broken = replace_group(clean, "PROJ", rows=[("C2-001", "two\nlines", "x", "y")])
    assert ("ags4.rule-6", None, "PROJ/1") in errors_of(broken)
    assert ags4.check(replace_group(clean, "LOCA", rows=rows)).ok


def test_rule_6_a_line_break_inside_a_quoted_value(tmp_path):
    text = CLEAN.read_bytes().replace(b'"Invented dense sand"', b'"Invented dense\r\nsand"')
    path = tmp_path / "broken.ags"
    path.write_bytes(text)
    report = ags4.check(path)
    rule_6 = [f for f in report.findings if f.rule_id == "ags4.rule-6"]
    assert [f.line for f in rule_6] == [
        text.split(b"\r\n").index(b'"DATA","XB01","3.50","12.00","Invented dense') + 1
    ]


def test_rule_6_a_quoted_value_over_three_lines(tmp_path):
    # The middle line has no quote; the value stays open across it.
    old = b'"Invented dense sand"'
    text = CLEAN.read_bytes().replace(old, b'"Invented\r\ndense\r\nsand"')
    path = tmp_path / "three.ags"
    path.write_bytes(text)
    rule_6 = [f.line for f in ags4.check(path).findings if f.rule_id == "ags4.rule-6"]
    assert rule_6 == [text.split(b"\r\n").index(b'"DATA","XB01","3.50","12.00","Invented') + 1]


def test_rules_2_2b_4_19_19a_19b(clean):
    no_rows = add_group(clean, group("XEMP", ["XEMP_ID"], ["X"]))
    assert ("ags4.rule-2", None, "XEMP") in errors_of(no_rows)
    twice = add_group(clean, group("XTWO", ["XTWO_ID", "XTWO_ID"], ["X", "X"], ["a", "b"]))
    assert ("ags4.rule-2b", None, "XTWO") in errors_of(twice)
    ragged = add_group(clean, group("XRAG", ["XRAG_ID", "XRAG_B"], ["X", "X"], ["a"]))
    assert ("ags4.rule-4", None, "XRAG/1") in errors_of(ragged)
    no_unit = add_group(
        clean, ags4.Group(name="XNOU", headings=("XNOU_ID",), types=("X",), rows=[("a",)])
    )
    assert ("ags4.rule-2b", None, "XNOU") in errors_of(no_unit)
    lower = add_group(clean, group("Xlow", ["XLOW_ID"], ["X"], ["a"]))
    assert ("ags4.rule-19", None, "Xlow") in errors_of(lower)
    long = add_group(clean, group("XLNG", ["XLNG_TOOLONG"], ["X"], ["a"]))
    assert ("ags4.rule-19a", None, "XLNG") in errors_of(long)
    no_prefix = add_group(clean, group("XPRE", ["XPREID", "GEOL_ZZZ", "SPEC_REF"], ["X"] * 3))
    found = [e for e in errors_of(no_prefix) if e[0] == "ags4.rule-19b"]
    # XPREID has no underscore; GEOL_ZZZ names GEOL, which has no such heading; SPEC is
    # neither in the file nor known without a dictionary, so SPEC_REF is not judged.
    assert found == [("ags4.rule-19b", None, "XPRE"), ("ags4.rule-19b", None, "XPRE")]


@pytest.mark.parametrize(
    ("data_type", "unit", "good", "bad"),
    [
        ("2DP", "m", ["1.25", "-0.50", "+3.00"], ["1.2", "1.250", "1", "1e2", "x"]),
        ("0DP", "", ["12", "-3"], ["12.0", "1.", "x"]),
        ("2SF", "", ["1.2", "0.050", "10", "1200", "0"], ["1.23", "1234", "1.2.3"]),
        ("2SCI", "", ["7.31E4", "7.31e-04"], ["7.3E4", "73100", "7.31"]),
        ("0SCI", "", ["7E4"], ["7.3E4"]),
        ("U", "", ["1", "-1.5", ".5", "1e-3"], ["1,5", "abc", ""[:0] + "1x"]),
        ("DT", "yyyy-mm-dd", ["2026-11-12"], ["12/11/2026", "2026-13-01", "2026-11-12T10:00"]),
        ("DT", "yyyy-mm-ddThh:mm", ["2026-11-12T10:30", "2026-11-12T10:30Z"], ["2026-11-12"]),
        ("DT", "hh:mm", ["10:30"], ["10:61", "25:00"]),
        (
            "DT",
            "yyyy-mm-dd",
            ["2024-02-29", "2000-02-29", "2026-02-28", "2026-04-30"],
            ["2026-02-31", "2026-02-29", "1900-02-29", "2026-04-31", "2026-11-00"],
        ),
        ("DT", "dd/mm/yyyy", ["29/02/2024"], ["31/02/2026", "29/02/2026"]),
        ("DT", "hh:mm:ss", ["10:30:00", "23:59:59"], ["10:30:99", "10:30:60"]),
        (
            "DT",
            "",
            ["2026", "2026-11-12", "2026-11-12T10:30:00.123+01:00", "10:30"],
            ["Nov", "2026-02-31", "2026-11-12T10:30:99", "10:30:99"],
        ),
        ("T", "hh:mm:ss", ["12:30:00", "100:05"], ["12h30", "12:61"]),
        ("DMS", "", ["51:28:52.498", "-0:7:39"], ["51.48"]),
        ("YN", "", ["Y", "N", "y", "n"], ["YES", "1"]),
    ],
)
def test_rule_8_values_by_type(clean, data_type, unit, good, bad):
    names = [f"XVAL_{i}" for i in range(len(good) + len(bad))]
    row = tuple(good + bad)
    test = group("XVAL", names, [data_type] * len(row), row, units=[unit] * len(row))
    types = clean.groups["TYPE"]
    units = clean.groups["UNIT"]
    file = replace_group(clean, "TYPE", rows=[*types.rows, (data_type, "Invented")])
    if unit:
        file = replace_group(file, "UNIT", rows=[*units.rows, (unit, "Invented")])
    found = [e for e in errors_of(add_group(file, test)) if e[0] == "ags4.rule-8"]
    assert [e[2] for e in found] == [f"XVAL/1/XVAL_{i}" for i in range(len(good), len(row))]


def test_text_and_unknown_types_are_not_judged_by_rule_8(clean):
    test = group("XTXT", ["XTXT_A", "XTXT_B", "XTXT_C"], ["X", "XN", "9DPX"], ["1.2.3", "NP", "?"])
    found = {e[0] for e in errors_of(add_group(clean, test))}
    assert "ags4.rule-8" not in found
    assert "ags4.rule-17" in found  # XN and 9DPX are not in the TYPE group


def test_rules_13_14_and_the_lookup_groups(clean):
    proj = clean.groups["PROJ"]
    two = replace_group(clean, "PROJ", rows=[*proj.rows, proj.rows[0]])
    assert ("ags4.rule-13", None, "PROJ/2") in errors_of(two)
    assert ("ags4.rule-14", None, None) in errors_of(replace_group(clean, "TRAN", drop=True))
    assert ("ags4.rule-13", None, None) in errors_of(replace_group(clean, "PROJ", drop=True))
    assert ("ags4.rule-15", None, None) in errors_of(replace_group(clean, "UNIT", drop=True))
    assert ("ags4.rule-17", None, None) in errors_of(replace_group(clean, "TYPE", drop=True))
    assert ("ags4.rule-16", None, None) in errors_of(replace_group(clean, "ABBR", drop=True))


def test_pu_and_pt_values_are_looked_up(clean):
    test = group("XLKP", ["XLKP_U", "XLKP_T"], ["PU", "PT"], ["m", "2DP"], ["kg", "9XX"])
    file = replace_group(clean, "TYPE", rows=[*clean.groups["TYPE"].rows, ("PU", "u"), ("PT", "t")])
    assert [
        e for e in errors_of(add_group(file, test)) if e[0] in ("ags4.rule-15", "ags4.rule-17")
    ] == [
        ("ags4.rule-15", None, "XLKP/2/XLKP_U"),
        ("ags4.rule-17", None, "XLKP/2/XLKP_T"),
    ]


def test_abbreviations_are_case_insensitive_and_use_tran_rcon(clean):
    loca = clean.groups["LOCA"]
    lower = replace_group(clean, "LOCA", rows=[("XB01", "cp", "4.20", "12.00"), *loca.rows[1:]])
    assert ags4.check(lower).ok
    tran = clean.groups["TRAN"]
    semi = replace_group(clean, "TRAN", rows=[(*tran.rows[0][:-1], ";")])
    combined = replace_group(semi, "LOCA", rows=[("XB01", "CP;RC", "4.20", "12.00")])
    assert ags4.check(combined).ok


def test_rules_11_11a_11b_11c(clean):
    tran = clean.groups["TRAN"]
    no_dlim = replace_group(clean, "TRAN", rows=[(*tran.rows[0][:-2], "", "+")])
    assert ("ags4.rule-11a", None, "TRAN/1/TRAN_DLIM") in errors_of(no_dlim)
    no_rcon = replace_group(clean, "TRAN", rows=[(*tran.rows[0][:-1], "++")])
    assert ("ags4.rule-11b", None, "TRAN/1/TRAN_RCON") in errors_of(no_rcon)
    types = replace_group(clean, "TYPE", rows=[*clean.groups["TYPE"].rows, ("RL", "link")])
    links = group(
        "XLNK",
        ["XLNK_ID", "XLNK_REF"],
        ["X", "RL"],
        ["1", "LOCA|XB01"],
        ["2", "LOCA|XB01+LOCA|XB02"],
        ["3", "nothing"],
        ["4", "SPEC|1"],
        ["5", "LOCA|ZZ99"],
    )
    found = [e for e in errors_of(add_group(types, links)) if e[0].startswith("ags4.rule-11")]
    # Without a dictionary LOCA's KEY headings are unknown, so row 5 is not judged.
    assert found == [
        ("ags4.rule-11", None, "XLNK/3/XLNK_REF"),
        ("ags4.rule-11c", None, "XLNK/4/XLNK_REF"),
    ]
    found = errors_of(add_group(types, links), dictionary=DICTIONARY)
    assert ("ags4.rule-11c", None, "XLNK/5/XLNK_REF") in found
    assert ("ags4.rule-11c", None, "XLNK/1/XLNK_REF") not in found


def test_rule_18_dict_rows_and_18a_9_order():
    user = ags4.read(USER_GROUP)
    rows = user.groups["DICT"].rows
    bad_dict = replace_group(
        user,
        "DICT",
        rows=[*rows, ("NEITHER", "XTST", "", "", "", "x", "", ""), (*rows[1][:5], "", "", "")],
    )
    found = [e for e in errors_of(bad_dict) if e[0] == "ags4.rule-18"]
    assert found == [
        ("ags4.rule-18", None, "DICT/5/DICT_TYPE"),
        ("ags4.rule-18", None, "DICT/6/DICT_DESC"),
    ]
    xtst = user.groups["XTST"]
    swapped = replace_group(
        user,
        "XTST",
        headings=(xtst.headings[1], xtst.headings[0], xtst.headings[2]),
        rows=[(r[1], r[0], r[2]) for r in xtst.rows],
    )
    assert ("ags4.rule-18a", None, "XTST") in errors_of(swapped)
    # A user-defined heading in a standard group goes at the end of the HEADING row.
    dict_rows = (*rows, ("HEADING", "LOCA", "LOCA_XUSR", "OTHER", "X", "Invented", "", ""))
    with_user = replace_group(user, "DICT", rows=dict_rows)
    loca = with_user.groups["LOCA"]
    first = replace_group(
        with_user,
        "LOCA",
        headings=("LOCA_XUSR", *loca.headings),
        units=("", *loca.units),
        types=("X", *loca.types),
        rows=[("u", *r) for r in loca.rows],
    )
    assert ("ags4.rule-9", None, "LOCA") in errors_of(first)
    last = replace_group(
        with_user,
        "LOCA",
        headings=(*loca.headings, "LOCA_XUSR"),
        units=(*loca.units, ""),
        types=(*loca.types, "X"),
        rows=[(*r, "u") for r in loca.rows],
    )
    assert ags4.check(last).ok


def test_rule_9_a_heading_the_files_own_dict_group_does_not_define():
    user = ags4.read(USER_GROUP)
    xtst = user.groups["XTST"]
    extra = replace_group(
        user,
        "XTST",
        headings=(*xtst.headings, "XTST_BAD"),
        units=(*xtst.units, ""),
        types=(*xtst.types, "X"),
        rows=[(*r, "x") for r in xtst.rows],
    )
    assert ("ags4.rule-9", None, "XTST") in errors_of(extra)
    # A standard group is not judged without a dictionary: none is bundled.
    loca = user.groups["LOCA"]
    unknown = replace_group(
        user,
        "LOCA",
        headings=(*loca.headings, "LOCA_ZZZ"),
        units=(*loca.units, ""),
        types=(*loca.types, "X"),
        rows=[(*r, "x") for r in loca.rows],
    )
    assert ags4.check(unknown).ok


def test_rule_20_the_file_group_is_never_opened(clean):
    loca = clean.groups["LOCA"]
    with_fset = replace_group(
        clean,
        "LOCA",
        headings=(*loca.headings, "FILE_FSET"),
        units=(*loca.units, ""),
        types=(*loca.types, "X"),
        rows=[(*loca.rows[0], "FS1"), *((*r, "") for r in loca.rows[1:])],
    )
    assert ("ags4.rule-20", None, None) in errors_of(with_fset)
    files = group(
        "FILE",
        ["FILE_FSET", "FILE_NAME"],
        ["X", "X"],
        ["FS1", "photo.jpg"],
        ["FS1", "../../etc/passwd"],
        ["FS1", "C:\\x.jpg"],
    )
    found = [e for e in errors_of(add_group(with_fset, files)) if e[0] == "ags4.rule-20"]
    assert found == [
        ("ags4.rule-20", None, "FILE/2/FILE_NAME"),
        ("ags4.rule-20", None, "FILE/3/FILE_NAME"),
    ]
    other = replace_group(add_group(with_fset, files), "LOCA", rows=[(*loca.rows[0], "FS2")])
    assert ("ags4.rule-20", None, "LOCA/1/FILE_FSET") in errors_of(other)


def test_formula_like_text_is_a_warning_and_numbers_are_not(clean):
    proj = clean.groups["PROJ"]
    rows = [("C2-001", "=HYPERLINK(1)", "-5", "@x")]
    report = ags4.check(replace_group(clean, "PROJ", rows=rows))
    warned = [f.path for f in report.findings if f.rule_id == "ags4.formula-text"]
    assert sorted(warned) == ["PROJ/1/PROJ_CLNT", "PROJ/1/PROJ_NAME"]
    assert report.ok
    assert proj.rows  # the original is untouched


# --- what a finding may say -----------------------------------------------------------


def test_messages_never_hold_a_value_from_the_file(clean):
    # Group and heading names may be shown (when made of letters, digits and
    # underscores); values, units and types never are.
    loca = clean.groups["LOCA"]
    hostile = group(
        "XSEC",
        ["XSEC_A", "XSEC_B", "XSEC_DT", "XSEC_C"],
        ["PA", "RL", "DT", SECRET],
        [f"={SECRET}", f"{SECRET}|1", SECRET, SECRET],
        units=[SECRET, "", "yyyy", ""],
    )
    tran = clean.groups["TRAN"]
    file = replace_group(clean, "TRAN", rows=[(*tran.rows[0][:-2], SECRET, SECRET)])
    file = add_group(file, hostile)
    file = replace_group(file, "LOCA", rows=[(f"{SECRET}\u202e", *loca.rows[0][1:])])
    report = ags4.check(file, dictionary=DICTIONARY)
    assert len(report.findings) > 5
    for finding in report.findings:
        assert SECRET not in finding.message, finding
        assert "\u202e" not in finding.message
        assert finding.path is None or SECRET not in finding.path


def test_every_checker_rule_is_catalogued_and_explained():
    catalogue = {r.id: r for r in rule_catalogue()}
    for rule_id in _rules.CHECKER_RULES:
        info = explain_rule(rule_id)
        assert info.known, rule_id
        assert info.step is Step.AGS4
        assert info.description, rule_id
        assert rule_id in catalogue
    assert explain_rule("ags4.rule-10a").description.startswith("AGS4 Rule 10a:")
    assert explain_rule("ags4.formula-text").severities == (Severity.WARNING,)
    assert explain_rule(NOT_CHECKED).severities == (Severity.INFO,)
    numbered = sorted(r for r in catalogue if r.startswith("ags4.rule-"))
    # Every rule of section 4.1.1 except 10 and 12, which define terms (KEY, REQUIRED,
    # OTHER; null) that 10a to 10c check.
    assert numbered == sorted(
        f"ags4.rule-{n}"
        for n in [
            "1",
            "2",
            "2a",
            "2b",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10a",
            "10b",
            "10c",
            "11",
            "11a",
            "11b",
            "11c",
            "13",
            "14",
            "15",
            "16",
            "16a",
            "17",
            "18",
            "18a",
            "19",
            "19a",
            "19b",
            "20",
        ]
    )


def test_the_rule_modules_import_only_the_stdlib_and_the_pure_parts_of_pydiggs():
    allowed = {"pydiggs.ags4", "pydiggs.errors", "pydiggs.findings.model"}
    for module, extra in (
        (_rules, set()),
        (_dictionary, set()),
        (_check, {"pydiggs", "pydiggs.findings.run"}),
    ):
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
                    assert name in allowed | extra or name.startswith("pydiggs.ags4."), name
                else:
                    assert top in sys.stdlib_module_names, (module.__name__, name)


# --- limits ---------------------------------------------------------------------------


def test_max_findings_caps_the_report_and_counts_the_rest(clean):
    rows = [(f"r{i}", f"Caf\u00e9 {i}", "", "") for i in range(30)]
    file = replace_group(clean, "PROJ", rows=rows)
    report = ags4.check(file, max_findings=5)
    assert len(report.findings) == 6
    assert report.findings[-1].rule_id == "structure.findings-truncated" or any(
        f.rule_id == "structure.findings-truncated" for f in report.findings
    )
    assert dict(report.steps) == {Step.AGS4: "incomplete"}
    assert len(ags4.check(file, max_findings=None).findings) > 30


def test_a_rule_lists_at_most_100_findings_then_counts(clean):
    rows = [(f"r{i}", f"Caf\u00e9 {i}", "", "") for i in range(150)]
    report = ags4.check(replace_group(clean, "PROJ", rows=rows), max_findings=None)
    rule_1 = [f for f in report.findings if f.rule_id == "ags4.rule-1"]
    assert len(rule_1) == 101
    assert any("50 more findings" in f.message for f in rule_1)


def test_the_time_budget_stops_the_check(clean, monkeypatch):
    rows = [(f"x{i}", "CP", "1.00", "1.00") for i in range(3000)]
    file = replace_group(clean, "LOCA", rows=rows)
    monkeypatch.setattr(_check, "deadline", lambda budget: lambda: True)
    report = ags4.check(file, time_budget=1.0)
    assert "ags4.timeout" in ids(report)
    assert dict(report.steps) == {Step.AGS4: "incomplete"}
    assert not report.ok


def test_the_time_budget_holds_for_a_short_file_already_read(clean, monkeypatch):
    monkeypatch.setattr(_check, "deadline", lambda budget: lambda: True)
    report = ags4.check(clean, time_budget=1.0)
    assert "ags4.timeout" in ids(report)
    assert dict(report.steps) == {Step.AGS4: "incomplete"}


def test_a_clock_that_runs_out_partway_stops_a_short_check(clean, monkeypatch):
    now = [0.0]

    def monotonic():
        now[0] += 1.0  # each look at the clock costs a second
        return now[0]

    monkeypatch.setattr(_io, "time", types.SimpleNamespace(monotonic=monotonic))
    report = ags4.check(clean, time_budget=3.0)
    assert "ags4.timeout" in ids(report)
    assert dict(report.steps) == {Step.AGS4: "incomplete"}
    now[0] = 0.0
    assert ags4.check(clean, time_budget=10_000.0).ok


def test_line_index_stops_on_time_inside_a_long_header_row(clean):
    line = '"HEADING","' + '""' * (3 * 4096) + '"'
    text = f'"GROUP","XQUO"\r\n{line}\r\n'
    with pytest.raises(_rules.Stopped):
        _rules.index_lines(text, clean, lambda: True)


def test_line_index_stops_on_time_and_tolerates_a_mismatch(clean):
    text = CLEAN.read_text("ascii")
    with pytest.raises(_rules.Stopped):
        _rules.index_lines(text * 200, clean, lambda: True)
    other = replace_group(clean, "GEOL", rows=clean.groups["GEOL"].rows[:1])
    lines, broken = _rules.index_lines(text, other)
    assert lines["GEOL"].data is None
    assert lines["LOCA"].data == [44, 45, 46]
    assert broken == []


# --- the command line -----------------------------------------------------------------


def run_cli(capsys, *args):
    code = main(["ags4", "check", *map(str, args)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_clean_file_exits_0(capsys):
    code, out, _ = run_cli(capsys, CLEAN)
    assert code == 0
    assert "c2_clean.ags: valid: 0 error(s)" in out
    assert NOT_CHECKED in out


def test_cli_rule_10a_exits_1_and_names_the_rule(capsys):
    code, out, _ = run_cli(capsys, RULE_10A)
    assert code == 1
    assert "c2_rule_10a.ags:77: error ags4.rule-10a [XTST/2]:" in out


def test_cli_fail_on_warning_and_dictionary(capsys):
    code, out, _ = run_cli(capsys, BROKEN, "--fail-on", "warning", "--dictionary", DICTIONARY)
    assert code == 1
    assert NOT_CHECKED not in out
    code, _, err = run_cli(capsys, CLEAN, "--dictionary", FIXTURES / "clean_4_1_1.ags")
    assert code == 2
    assert "no DICT group" in err
    code, _, err = run_cli(capsys, CLEAN, "--dictionary", FIXTURES / "missing.ags")
    assert code == 2


def test_cli_json_and_sarif(capsys, tmp_path):
    code, out, _ = run_cli(capsys, CLEAN, RULE_10A, "--format", "json")
    assert code == 1
    document = json.loads(out)
    assert document["schema_version"] == "1.1"
    assert [f["format"] for f in document["files"]] == ["ags4", "ags4"]
    located = [
        f["location"]
        for entry in document["files"]
        for f in entry["findings"]
        if f["location"].get("path")
    ]
    assert {"line": 77, "column": None, "xpath": None, "gml_id": None, "path": "XTST/2"} in located
    target = tmp_path / "out.sarif"
    code, out, _ = run_cli(capsys, RULE_10A, "--format", "sarif", "--output", target)
    assert code == 1
    assert out == ""
    assert json.loads(target.read_text("utf-8"))["version"] == "2.1.0"


def test_cli_unreadable_and_not_ags4_inputs(capsys, tmp_path):
    code, out, _ = run_cli(capsys, tmp_path / "missing.ags")
    assert code == 2
    assert "unreadable" in out
    text = tmp_path / "notes.ags"
    text.write_bytes(b"just some text\r\n")
    code, out, _ = run_cli(capsys, text)
    assert code == 1
    assert "ags4.rule-2" in out


def test_cli_usage_errors(capsys):
    for args in (["--max-findings", "-1"], ["--max-bytes", "0"], ["--time-budget", "0"]):
        with pytest.raises(SystemExit) as caught:
            main(["ags4", "check", str(CLEAN), *args])
        assert caught.value.code == 2
    with pytest.raises(SystemExit):
        main(["ags4"])
    with pytest.raises(SystemExit):
        main(["ags4", "check", "--help"])
    assert "--dictionary" in capsys.readouterr().out


# --- rule parity with python-ags4 (the recorded oracle of work package C0) --------------

#: Rules that need nothing but the file; python-ags4 ran its check with the standard
#: dictionary, so only these are compared.
FILE_ONLY = frozenset(
    f"ags4.rule-{n}"
    for n in [
        "1",
        "2",
        "2a",
        "2b",
        "3",
        "4",
        "5",
        "6",
        "8",
        "11",
        "11a",
        "11b",
        "15",
        "16",
        "16a",
        "17",
        "19",
        "19a",
        "20",
    ]
)
#: Known, reasoned differences. The reader notes a UTF-8 byte order mark under Rule 1
#: (python-ags4 drops it silently). python-ags4 has no Rule 16a: it reports a part of a
#: combined abbreviation missing from ABBR under Rule 16, so pyDIGGS's 16a is compared
#: as 16 (c2_broken.ags).
BOM_NOTE = "The file starts with a UTF-8 byte order mark"
AS_PYTHON_AGS4 = {"ags4.rule-16a": "ags4.rule-16"}


def _oracle_records() -> list[dict]:
    records = tomllib.loads(EXPECTED.read_text("utf-8"))["file"]
    # Files python-ags4 could not read, or whose check stopped before it loaded a
    # dictionary (an AGS 3 file, a file not in UTF-8, a GROUP row with no name), are
    # compared by the reader tests only: python-ags4 reports just the rules it reached
    # before stopping, so its list is not the whole set to compare with. (On messy.ags it
    # reports an unnamed GROUP row under Rule 19, where the reader notes it under Rule 4.)
    return [r for r in records if r["readable"] and r["check"].get("dictionary")]


def test_the_rule_parity_covers_every_fixture_the_oracle_checked_in_full():
    keys = {r["key"] for r in _oracle_records()}
    assert {f"fixtures/{p.name}" for p in FIXTURES.glob("c2_*.ags")} <= keys
    assert {f"fixtures/{p.name}" for p in FIXTURES.glob("clean_*.ags")} <= keys
    assert sum(key.startswith("bgs/") for key in keys) >= 10


@pytest.mark.parametrize("record", _oracle_records(), ids=lambda record: record["key"])
def test_rule_parity_with_the_recorded_oracle(record):
    path = ROOT / record["path"]
    if not path.is_file():
        if os.environ.get("PYDIGGS_AGS4_CORPUS") == "required":
            pytest.fail(f"{record['key']} is not fetched: run conformance.py fetch --set ags4")
        pytest.skip(f"{record['key']} is not fetched (scripts/conformance.py fetch --set ags4)")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"], "re-record"
    report = ags4.check(path, max_findings=None)
    ours = {
        AS_PYTHON_AGS4.get(f.rule_id, f.rule_id)
        for f in report.findings
        if f.rule_id in FILE_ONLY and not f.message.startswith(BOM_NOTE)
    }
    expected = set(record["check"].get("rules", {})) & FILE_ONLY
    assert ours == expected
    for rule, lines in record["check"].get("lines", {}).items():
        if rule in FILE_ONLY and rule != "ags4.rule-2a":  # pyDIGGS notes 2a once per file
            found = sorted({f.line for f in report.findings if f.rule_id == rule and f.line})
            assert found[: len(lines)] == lines, rule
