"""The python-ags4 oracle recorder (scripts/ags4_oracle.py) and its record.

python-ags4 is never installed for the tests: the script is loaded from its file and
driven with a fake oracle. The committed record, conformance/ags4-expected.toml, is
checked for the shape the pydiggs.ags4 parity tests read, and for agreement with an
independent stdlib ``csv`` reading of the synthetic fixtures.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "ags4_oracle.py"
EXPECTED = ROOT / "conformance" / "ags4-expected.toml"
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")

CLEAN = (
    b'"GROUP","PROJ"\r\n"HEADING","PROJ_ID","PROJ_NAME"\r\n"UNIT","",""\r\n'
    b'"TYPE","ID","X"\r\n"DATA","P1","Quay ""A"""\r\n\r\n'
    b'"GROUP","LOCA"\r\n"HEADING","LOCA_ID","LOCA_GL"\r\n"DATA","BH01","1.20"\r\n'
)


@pytest.fixture(scope="module")
def oracle_script():
    spec = importlib.util.spec_from_file_location("ags4_oracle", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[spec.name]
        sys.modules.pop("conformance", None)  # loaded by the script, by file path


def _csv_tables(data: bytes) -> tuple[dict[str, dict[str, list[Any]]], dict[str, list[str]]]:
    """A stand-in for python-ags4's ``AGS4_to_dict``: the same two dicts, from csv."""
    text = data.decode("utf-8-sig")
    tables: dict[str, dict[str, list[Any]]] = {}
    headings: dict[str, list[str]] = {}
    group = None
    for row in csv.reader(io.StringIO(text, newline="")):
        if not row:
            continue
        if row[0] == "GROUP":
            group = row[1]
            tables[group] = {}
        elif row[0] == "HEADING" and group is not None:
            headings[group] = row
            tables[group] = {h: [] for h in row}
        elif row[0] in {"UNIT", "TYPE", "DATA"} and group is not None:
            for heading, value in zip(headings[group], row, strict=True):
                tables[group][heading].append(value)
    return tables, headings


def _fake(oracle_script, *, check=None, fail_on=()):
    reports = {
        "AGS Format Rule 2a": [{"line": 1, "group": "", "desc": "no CRLF"}],
        "AGS Format Rule 10a": [
            {"line": 9, "group": "LOCA", "desc": "x"},
            {"line": "-", "group": "LOCA", "desc": "y"},
            {"line": "7", "group": "LOCA", "desc": "z"},
        ],
        "FYI (Related to Rule 16)": [{"line": 3, "group": "ABBR", "desc": "fyi"}],
        "General": [{"line": "", "group": "", "desc": "about rule 1"}],
        "Summary of data": [{"line": "", "group": "", "desc": "2 groups"}],
        "Metadata": [
            {"line": "Dictionary", "group": "", "desc": "Standard_dictionary_v4_1_1.ags"},
            {"line": "Time (UTC)", "group": "", "desc": "2026-11-11 10:00:00"},
        ],
    }

    def read(path: Path, encoding: str):
        if path.name in fail_on:
            raise ValueError("Line 4 does not have the same\nnumber of entries")
        return _csv_tables(path.read_bytes())

    return oracle_script.Oracle(
        version="1.2.0",
        pandas="2.3.3",
        read=read,
        check=lambda path, encoding: reports if check is None else check,
    )


# --- pieces --------------------------------------------------------------------------


def test_groups_of_splits_header_rows_from_data_rows(oracle_script):
    data, headings = _csv_tables(CLEAN)
    proj, loca = oracle_script.groups_of(data, headings, with_rows=True)
    assert proj == {
        "name": "PROJ",
        "headings": ["PROJ_ID", "PROJ_NAME"],
        "units": ["", ""],
        "types": ["ID", "X"],
        "row_count": 1,
        "rows_sha256": oracle_script.rows_digest([["P1", 'Quay "A"']]),
        "rows": [["P1", 'Quay "A"']],
    }
    assert (loca["units"], loca["types"], loca["row_count"]) == ([], [], 1)
    (bare, _) = oracle_script.groups_of(data, headings, with_rows=False)
    assert "rows" not in bare
    assert bare["rows_sha256"] == proj["rows_sha256"]


def test_groups_of_a_group_without_a_heading_row(oracle_script):
    (group,) = oracle_script.groups_of({"PROJ": {}}, {}, with_rows=True)
    assert group["headings"] == [] and group["rows"] == [] and group["row_count"] == 0


def test_rows_digest_is_the_documented_json(oracle_script):
    rows = [["BH01", "é", ""]]
    text = '[["BH01","é",""]]'
    assert oracle_script.rows_digest(rows) == hashlib.sha256(text.encode()).hexdigest()
    assert oracle_script.rows_digest([]) == hashlib.sha256(b"[]").hexdigest()


def test_summarise_check_keeps_rules_and_drops_the_clock(oracle_script):
    summary = oracle_script.summarise_check(_fake(oracle_script).check(Path("x"), "utf-8"))
    assert summary == {
        "dictionary": "Standard_dictionary_v4_1_1.ags",
        "errors": 4,
        "fyi": 1,
        "rules": {"ags4.rule-2a": 1, "ags4.rule-10a": 3},
        "lines": {"ags4.rule-2a": [1], "ags4.rule-10a": [7, 9]},
    }


def test_summarise_check_records_a_process_error(oracle_script):
    errors = {
        "AGS Format Rule 4": [{"line": 5, "group": "PROJ", "desc": "fields"}],
        "Validator Process Error": [{"line": "-", "group": "", "desc": "x" * 400}],
    }
    summary = oracle_script.summarise_check(errors)
    assert summary["errors"] == 2
    assert summary["process_error"] == "x" * 300
    assert summary["dictionary"] == ""


def test_rule_order_is_numeric(oracle_script):
    rules = ["ags4.rule-19b", "ags4.rule-2a", "ags4.rule-10a", "ags4.rule-2", "ags4.rule-19"]
    assert sorted(rules, key=oracle_script._rule_order) == [
        "ags4.rule-2",
        "ags4.rule-2a",
        "ags4.rule-10a",
        "ags4.rule-19",
        "ags4.rule-19b",
    ]


def test_measure_reads_and_checks(oracle_script, tmp_path):
    path = tmp_path / "site.ags"
    path.write_bytes(b"\xef\xbb\xbf" + CLEAN)
    record = oracle_script.measure(
        "fixtures/site.ags", path, _fake(oracle_script), in_repo=True, rel="x/site.ags"
    )
    assert record["encoding"] == "utf-8-sig"
    assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record["size"] == len(CLEAN) + 3
    assert record["readable"] is True
    assert record["ags_version"] == ""
    assert [g["name"] for g in record["group"]] == ["PROJ", "LOCA"]
    assert record["check"]["rules"] == {"ags4.rule-2a": 1, "ags4.rule-10a": 3}


def test_measure_records_a_refusal(oracle_script, tmp_path):
    path = tmp_path / "bad.ags"
    path.write_bytes("é".encode("cp1252"))
    record = oracle_script.measure(
        "bgs/bad.ags", path, _fake(oracle_script, fail_on={"bad.ags"}), in_repo=False, rel="b"
    )
    assert record["encoding"] == "cp1252"
    assert record["readable"] is False
    assert record["error"] == "Line 4 does not have the same number of entries"
    assert record["group"] == []
    assert "rules" in record["check"]  # the rule check still runs


def test_ags_version_is_the_first_tran_row(oracle_script, tmp_path):
    path = tmp_path / "tran.ags"
    path.write_bytes(
        b'"GROUP","TRAN"\r\n"HEADING","TRAN_ISNO","TRAN_AGS"\r\n"UNIT","",""\r\n'
        b'"TYPE","X","X"\r\n"DATA","1","4.1.1"\r\n"DATA","2","4.2"\r\n'
    )
    record = oracle_script.measure("t", path, _fake(oracle_script), in_repo=True, rel="t")
    assert record["ags_version"] == "4.1.1"


@pytest.mark.parametrize(
    "text",
    [
        "plain",
        'a "quoted" \\ back',
        "tab\there",
        "line\r\nbreak",
        "\x00\x1f\x7f\x85",
        "\u00e9 \u2013 \u4e2d",
    ],
)
def test_toml_string_round_trips(oracle_script, text):
    assert tomllib.loads(f"v = {oracle_script.toml_string(text)}")["v"] == text


def test_render_round_trips_through_tomllib(oracle_script, tmp_path):
    fake = _fake(oracle_script, fail_on={"bad.ags"})
    good = tmp_path / "good.ags"
    good.write_bytes(CLEAN.replace(b"Quay", b"Quay\x01\t"))
    bad = tmp_path / "bad.ags"
    bad.write_bytes(b"x")
    records = [
        oracle_script.measure("fixtures/z.ags", good, fake, in_repo=True, rel="z"),
        oracle_script.measure("bgs/a.ags", bad, fake, in_repo=False, rel="a"),
    ]
    text = oracle_script.render(fake, records)
    data = tomllib.loads(text)
    assert data["oracle"] == {"name": "python-ags4", "version": "1.2.0", "pandas": "2.3.3"}
    assert [f["key"] for f in data["file"]] == ["bgs/a.ags", "fixtures/z.ags"]
    refused, read = data["file"]
    assert refused["group"] == [] and refused["readable"] is False
    assert read["group"][0]["rows"] == [["P1", 'Quay\x01\t "A"']]
    assert read["check"]["lines"]["ags4.rule-10a"] == [7, 9]
    assert text == oracle_script.render(fake, list(reversed(records)))  # stable


def test_render_refuses_a_value_it_cannot_write(oracle_script):
    with pytest.raises(TypeError, match="float"):
        oracle_script._value(1.5)


# --- record and the command line -------------------------------------------------------


def _small_set(oracle_script, tmp_path, monkeypatch):
    conf = oracle_script._conformance()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "one.ags").write_bytes(CLEAN)
    corpus = tmp_path / "ags4-corpus.toml"
    corpus.write_text("", encoding="utf-8")
    monkeypatch.setattr(conf, "AGS4_CORPUS", corpus)
    monkeypatch.setattr(conf, "AGS4_CACHE", tmp_path / "cache")
    monkeypatch.setattr(conf, "AGS4_FIXTURES", fixtures)
    monkeypatch.setattr(conf, "AGS4_EXPECTED", tmp_path / "ags4-expected.toml")
    return conf


def test_record_writes_what_check_ags4_accepts(oracle_script, tmp_path, monkeypatch):
    conf = _small_set(oracle_script, tmp_path, monkeypatch)
    assert oracle_script.record(_fake(oracle_script)) == 1
    data = tomllib.loads(conf.AGS4_EXPECTED.read_text(encoding="utf-8"))
    (record,) = data["file"]
    assert record["key"] == "fixtures/one.ags"
    assert record["group"][0]["rows"] == [["P1", 'Quay "A"']]
    entries = conf.ags4_fixtures(conf.AGS4_FIXTURES)
    assert conf.check_ags4(entries, conf.AGS4_EXPECTED) == []


def test_main_records_with_an_injected_oracle(oracle_script, tmp_path, monkeypatch, capsys):
    conf = _small_set(oracle_script, tmp_path, monkeypatch)
    assert oracle_script.main(["record"], oracle=_fake(oracle_script)) == 0
    assert "recorded 1 file(s)" in capsys.readouterr().out
    assert conf.AGS4_EXPECTED.is_file()


def test_main_without_python_ags4_exits_2(oracle_script, tmp_path, monkeypatch, capsys):
    _small_set(oracle_script, tmp_path, monkeypatch)

    def missing(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(oracle_script.importlib, "import_module", missing)
    assert oracle_script.main(["record"]) == 2
    assert "uv run --isolated --with python-ags4==1.2.0" in capsys.readouterr().err


def test_another_python_ags4_version_is_refused(oracle_script, monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.1.0")
    monkeypatch.setattr(oracle_script.importlib, "import_module", lambda name: object())
    with pytest.raises(oracle_script.OracleError, match=r"1\.1\.0 is installed"):
        oracle_script.python_ags4_oracle()


def test_main_missing_corpus_file_exits_1(oracle_script, tmp_path, monkeypatch, capsys):
    conf = _small_set(oracle_script, tmp_path, monkeypatch)
    sha = hashlib.sha256(b"x").hexdigest()
    conf.AGS4_CORPUS.write_text(
        '[sources.demo]\nrepo = "https://github.com/example/demo"\n'
        f'commit = "{"c" * 40}"\nlicense = "MIT"\n\n'
        f'[files."demo/a.ags"]\npath = "a.ags"\nsha256 = "{sha}"\nsize = 1\n',
        encoding="utf-8",
    )
    assert oracle_script.main(["record"], oracle=_fake(oracle_script)) == 1
    assert "conformance.py fetch" in capsys.readouterr().err
    assert oracle_script.main(["record", "--allow-missing"], oracle=_fake(oracle_script)) == 0


# --- python-ags4 stays out of pyDIGGS (Q15) ----------------------------------------------


def test_python_ags4_is_never_a_dependency_or_an_import():
    for name in ("pyproject.toml", "uv.lock"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "python-ags4" not in text and "python_ags4" not in text, name
    for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]:
        if path.name == Path(__file__).name:
            continue
        assert "python_ags4" not in path.read_text(encoding="utf-8"), path


# --- the committed record --------------------------------------------------------------


@pytest.fixture(scope="module")
def recorded():
    if not EXPECTED.is_file():
        pytest.skip("conformance/ags4-expected.toml is not in this tree")
    return tomllib.loads(EXPECTED.read_text(encoding="utf-8"))


def test_record_has_the_shape_the_parity_tests_read(oracle_script, recorded):
    assert recorded["oracle"]["name"] == "python-ags4"
    assert recorded["oracle"]["version"] == oracle_script.ORACLE_VERSION
    files = recorded["file"]
    assert files, "the record is empty"
    for entry in files:
        assert {"key", "path", "sha256", "size", "encoding", "readable", "group", "check"} <= set(
            entry
        ), entry["key"]
        assert set(entry["check"]) >= {"dictionary", "errors", "fyi", "rules", "lines"}
        in_repo = entry["key"].startswith("fixtures/")
        assert entry["readable"] or entry["group"] == []
        for group in entry["group"]:
            assert len(group["units"]) in {0, len(group["headings"])}
            assert len(group["types"]) in {0, len(group["headings"])}
            # Cell values are recorded for fixtures only (Q16: no republishing of OGL data).
            assert ("rows" in group) is in_repo, entry["key"]
            if in_repo:
                assert group["row_count"] == len(group["rows"])
                assert group["rows_sha256"] == oracle_script.rows_digest(group["rows"])


def test_record_agrees_with_csv_on_the_clean_fixtures(oracle_script, recorded):
    """An independent reading (stdlib csv) gives the same tables as the oracle record."""
    by_key = {f["key"]: f for f in recorded["file"]}
    clean = sorted(FIXTURES.glob("site_*.ags"))
    assert clean, "no site_*.ags fixtures"
    for path in clean:
        entry = by_key[f"fixtures/{path.name}"]
        data, headings = _csv_tables(path.read_bytes())
        assert entry["group"] == oracle_script.groups_of(data, headings, with_rows=True)


def test_every_rule_fixture_reports_its_rule(recorded):
    """Each tests/fixtures/ags4/rule_<id>.ags makes python-ags4 report ags4.rule-<id>."""
    by_key = {f["key"]: f for f in recorded["file"]}
    rule_files = sorted(FIXTURES.glob("rule_*.ags"))
    assert len(rule_files) >= 20
    for path in rule_files:
        rule = "ags4.rule-" + path.stem.removeprefix("rule_")
        assert rule in by_key[f"fixtures/{path.name}"]["check"]["rules"], path.name


def test_clean_site_fixtures_have_no_errors(recorded):
    for entry in recorded["file"]:
        if entry["key"].startswith("fixtures/site_"):
            assert entry["check"]["errors"] == 0, entry["key"]
            assert entry["readable"], entry["key"]
