"""Fail-closed gates, Schematron pairing and --timeout robustness (review round 5)."""

from __future__ import annotations

import os
import pickle
import shutil
import sys
import time
from pathlib import Path

import pytest
from lxml import etree

from pydiggs import _xml, validate
from pydiggs.dictionary import DictionarySemanticValidator
from pydiggs.findings import _cli, run
from pydiggs.findings._schematron import FiredPattern, RuleContexts, fired_patterns
from pydiggs.findings.locate import Locator
from pydiggs.findings.model import Step

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
NS = "http://diggsml.org/schemas/2.6"
SCH = 'xmlns:sch="http://purl.oclc.org/dsdl/schematron"'
_XPATH_STEPS = {"dictionary", "schematron", "deprecation", "structure", "geometry", "context"}


# --- markup entities and the element gate --------------------------------------------


def _entity_file(tmp_path: Path, value: str, uses: int = 3, encoding: str = "utf-8") -> Path:
    text = (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        f'<!DOCTYPE Diggs [<!ENTITY e "{value}">]>\n'
        f'<Diggs xmlns="{NS}">' + "&e;" * uses + "</Diggs>"
    )
    path = tmp_path / "ent.xml"
    path.write_bytes(text.encode(encoding))
    return path


@pytest.mark.parametrize("value", ["<b/>" * 1000, "&#60;b/>", "&#38;#60;b/>x"])
def test_a_markup_entity_is_one_error_and_nothing_else_runs(tmp_path, value):
    report = validate(_entity_file(tmp_path, value))
    assert not report.ok
    if value.startswith("&#38;"):  # replacement text '&#60;b/>x': character data, no markup
        assert all(f.rule_id != "structure.markup-entity" for f in report.findings)
        return
    (finding,) = report.findings
    assert (finding.rule_id, finding.severity.value) == ("structure.markup-entity", "error")
    assert "'e'" in finding.message
    assert report.steps["structure"] == "failed"
    assert {s for s, v in report.steps.items() if v != "skipped"} == {Step.STRUCTURE}


def test_markup_entities_are_found_in_utf16_and_through_parameter_entities(tmp_path):
    utf16 = _entity_file(tmp_path, "<b/>", encoding="utf-16")
    assert [f.rule_id for f in validate(utf16).findings] == ["structure.markup-entity"]
    nested = tmp_path / "pe.xml"
    nested.write_text(
        "<!DOCTYPE Diggs [<!ENTITY % p \"<!ENTITY e '<b/>'>\"> %p;]>"
        f'<Diggs xmlns="{NS}">&e;</Diggs>'
    )
    assert [f.rule_id for f in validate(nested).findings] == ["structure.markup-entity"]


def test_plain_text_entities_still_work(tmp_path):
    report = validate(_entity_file(tmp_path, "plain text"), checks=["schema", "context"])
    assert "structure.markup-entity" not in {f.rule_id for f in report.findings}
    assert report.steps["schema"] == "failed"  # it ran
    broken = tmp_path / "broken.xml"
    broken.write_text('<!DOCTYPE r [<!ENTITY e "<b/>"')  # cut short: the parse reports it
    assert [f.rule_id for f in validate(broken).findings] == ["structure.well-formed"]


def test_the_element_gate_counts_the_tree_not_the_bytes(tmp_path, monkeypatch):
    """UTF-7 spells '<' as '+ADw-': a byte count sees no elements at all."""
    monkeypatch.setattr(run, "MAX_ELEMENTS", 50)
    lt = b"+ADw-"
    data = (
        b'<?xml version="1.0" encoding="UTF-7"?>'
        + lt
        + b'Diggs xmlns="http://diggsml.org/schemas/2.6">'
        + (lt + b"a/>") * 60
        + lt
        + b"/Diggs>"
    )
    assert data.count(b"<") == 1  # only the XML declaration
    path = tmp_path / "u7.xml"
    path.write_bytes(data)
    report = validate(path)
    assert not report.ok
    assert {s.value for s, v in report.steps.items() if v == "incomplete"} == _XPATH_STEPS
    monkeypatch.setattr(run, "MAX_ELEMENTS", 61)
    assert validate(path, checks=["context"]).steps["context"] != "incomplete"


def test_casing_xpath_errors_fail_closed(tmp_path, monkeypatch):
    def broken(doc):
        raise etree.XPathEvalError("limit")

    monkeypatch.setattr(run, "_casing_diameter_problems", broken)
    report = validate(NO_ERROR, checks=["schematron"])
    assert report.steps["schematron"] == "incomplete"
    assert "schematron.incomplete" in {f.rule_id for f in report.findings}


# --- CLI: unexpected errors and --timeout ---------------------------------------------


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copy(NO_ERROR, tmp_path / "ok.xml")
    shutil.copy(NO_ERROR, tmp_path / "bad.xml")
    return tmp_path


def test_an_unexpected_error_is_an_error_report_and_the_batch_goes_on(work, monkeypatch, capsys):
    import json

    real = _cli.validate

    def flaky(path, **kwargs):
        if str(path).endswith("bad.xml"):
            raise RuntimeError("boom")
        return real(path, **kwargs)

    monkeypatch.setattr(_cli, "validate", flaky)
    code = _cli.main(["--format", "json", "--checks", "schema", "ok.xml", "bad.xml"])
    files = json.loads(capsys.readouterr().out)["files"]
    assert code == 1
    bad, ok = files
    assert [(f["rule_id"], f["severity"]) for f in bad["findings"]] == [
        ("structure.internal-error", "error")
    ]
    assert "RuntimeError: boom" in bad["findings"][0]["message"]
    steps = {s["step"]: s["status"] for s in bad["steps"]}
    assert steps["schema"] == "incomplete"
    assert {v for k, v in steps.items() if k != "schema"} == {"skipped"}
    assert ok["findings"] == []


@pytest.mark.parametrize("value", ["inf", "1e300", "nan", "86401", "-1"])
def test_timeout_must_be_finite_and_at_most_a_day(work, value):
    with pytest.raises(SystemExit):
        _cli.main(["--timeout", value, "ok.xml"])


class _Conn:
    def __init__(self, answer):
        self.answer = answer

    def poll(self, timeout):
        return True

    def recv(self):
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    def close(self):
        pass


class _Process:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


class _Context:
    def __init__(self, answer):
        self.answer = answer

    def Pipe(self, duplex):  # noqa: N802 - multiprocessing's name
        return _Conn(self.answer), _Conn(None)

    def Process(self, *args, **kwargs):  # noqa: N802 - multiprocessing's name
        return _Process()


@pytest.mark.parametrize(
    ("answer", "rule", "text"),
    [
        (EOFError(), "structure.timeout", "stopped unexpectedly"),
        (OSError("reset"), "structure.timeout", "stopped unexpectedly"),
        (pickle.UnpicklingError("bad"), "structure.timeout", "stopped unexpectedly"),
        (("crash", "MemoryError: "), "structure.internal-error", "MemoryError"),
    ],
)
def test_a_worker_that_dies_fails_closed(work, monkeypatch, answer, rule, text):
    monkeypatch.setattr(_cli.multiprocessing, "get_context", lambda method: _Context(answer))
    kwargs = {"checks": ["schema", "dictionary"], "max_bytes": 10**8, "base_dir": None}
    report = _cli._validate_isolated(work / "ok.xml", kwargs, 5.0)
    (finding,) = report.findings
    assert (finding.rule_id, finding.severity.value) == (rule, "error")
    assert text in finding.message
    assert not report.ok
    assert {s.value for s, v in report.steps.items() if v == "incomplete"} == {
        "schema",
        "dictionary",
    }
    assert {v for s, v in report.steps.items() if s.value not in ("schema", "dictionary")} == {
        "skipped"
    }


def test_worker_errors_still_propagate(work, monkeypatch):
    error = _cli.InstanceReadError(2, "gone", "ok.xml")
    monkeypatch.setattr(
        _cli.multiprocessing, "get_context", lambda method: _Context(("error", error))
    )
    with pytest.raises(_cli.InstanceReadError):
        _cli._validate_isolated(work / "ok.xml", {"max_bytes": 10**8}, 5.0)


@pytest.mark.timeout(30)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_the_timeout_report_reads_only_regular_files(work):
    os.mkfifo(work / "pipe.xml")
    with pytest.raises(_cli.InstanceReadError):
        _cli._failed(work / "pipe.xml", {"max_bytes": 10**6}, "structure.timeout", "x")


# --- Schematron pairing ---------------------------------------------------------------


def _sch(tmp_path: Path, name: str, body: str, attrs: str = "") -> Path:
    path = tmp_path / f"{name}.sch"
    path.write_text(f"<sch:schema {SCH} {attrs}>{body}</sch:schema>")
    return path


def _located(tmp_path: Path, sch: Path) -> list[tuple[str, str | None]]:
    doc = tmp_path / "doc.xml"
    doc.write_text('<r><a n="1"/><b n="2"/><c n="3"/></r>')
    report = validate(doc, checks=["schematron"], schematron_path=sch)
    return sorted((f.message, f.xpath) for f in report.findings)


def _pattern(pid: str, context: str, message: str, extra: str = "") -> str:
    return (
        f'<sch:pattern id="{pid}" {extra}><sch:rule context="{context}">'
        f'<sch:assert test="false()">{message}</sch:assert></sch:rule></sch:pattern>'
    )


def test_default_phase_patterns_are_numbered_as_lxml_runs_them(tmp_path):
    sch = _sch(
        tmp_path,
        "phase",
        '<sch:phase id="p2"><sch:active pattern="P2"/></sch:phase>'
        + _pattern("P1", "a", "A")
        + _pattern("P2", "b", "B"),
        'defaultPhase="p2"',
    )
    assert _located(tmp_path, sch) == [("B", "/r[1]/b[1]")]


def test_abstract_patterns_do_not_shift_the_numbering(tmp_path):
    abstract = (
        '<sch:pattern abstract="true" id="abs"><sch:rule context="$x">'
        '<sch:assert test="false()">ABS</sch:assert></sch:rule></sch:pattern>'
    )
    instance = '<sch:pattern is-a="abs" id="inst"><sch:param name="x" value="c"/></sch:pattern>'
    sch = _sch(tmp_path, "abstract", abstract + _pattern("P1", "a", "A") + instance)
    assert _located(tmp_path, sch) == [("A", "/r[1]/a[1]"), ("ABS", "/r[1]/c[1]")]


def test_included_patterns_are_located(tmp_path):
    (tmp_path / "inc.sch").write_text(f"<sch:pattern {SCH} " + _pattern("P1", "a", "A")[13:])
    sch = _sch(tmp_path, "include", '<sch:include href="inc.sch"/>' + _pattern("P2", "b", "B"))
    assert _located(tmp_path, sch) == [("A", "/r[1]/a[1]"), ("B", "/r[1]/b[1]")]


def test_first_matching_rule_wins_and_unlocatable_contexts_stay_unlocated(tmp_path):
    first = (
        '<sch:pattern id="P"><sch:rule context="*[@n=\'2\']">'
        '<sch:assert test="false()">N2</sch:assert></sch:rule>'
        '<sch:rule context="b|c"><sch:assert test="false()">BC</sch:assert></sch:rule>'
        "</sch:pattern>"
    )
    assert _located(tmp_path, _sch(tmp_path, "first", first)) == [
        ("BC", "/r[1]/c[1]"),
        ("N2", "/r[1]/b[1]"),
    ]
    attr = _pattern("P", "@n", "AT") + _pattern("Q", "c", "C")
    assert _located(tmp_path, _sch(tmp_path, "attr", attr)) == [
        ("AT", None),
        ("AT", None),
        ("AT", None),
        ("C", "/r[1]/c[1]"),
    ]


def _contexts(body: str) -> tuple[RuleContexts, etree._ElementTree]:
    schema = etree.ElementTree(etree.fromstring(f"<sch:schema {SCH}>{body}</sch:schema>"))
    doc = etree.ElementTree(etree.fromstring("<r><a/><b/><a/></r>"))
    return RuleContexts(schema), doc


def test_matched_refuses_any_disagreement_with_the_report():
    contexts, doc = _contexts(
        '<sch:pattern id="P"><sch:rule context="a"><sch:assert test="1"/></sch:rule>'
        '<sch:rule context="b"><sch:assert test="1"/></sch:rule></sch:pattern>'
    )
    good = FiredPattern("P", ["a", "b", "a"])
    assert [el.tag for el in contexts.matched(doc, 0, good) or []] == ["a", "b", "a"]
    assert contexts.matched(doc, 0, FiredPattern("P", ["a", "b"])) is None  # fewer fired
    assert contexts.matched(doc, 0, FiredPattern("P", ["a", "b", "a", "a"])) is None  # more
    assert contexts.matched(doc, 0, FiredPattern("P", ["a", "a", "b"])) is None  # other rule
    assert contexts.matched(doc, 0, FiredPattern("Q", ["a", "b", "a"])) is None  # other id
    assert contexts.matched(doc, 1, good) is None  # no such pattern


def test_fired_patterns_reads_ids_and_contexts():
    svrl = "http://purl.oclc.org/dsdl/svrl"
    report = etree.ElementTree(
        etree.fromstring(
            f'<s:schematron-output xmlns:s="{svrl}"><s:active-pattern id="P"/>'
            '<s:fired-rule context="a"/><s:failed-assert test="1"/><s:fired-rule context="b"/>'
            "<s:active-pattern/></s:schematron-output>"
        )
    )
    assert fired_patterns(report) == {
        0: FiredPattern("P", ["a", "b"]),
        1: FiredPattern(None, []),
    }


def test_many_contexts_over_many_elements_are_located_in_linear_time():
    """18 contexts, ~100,000 matched elements: no quadratic XPath union (audit H1)."""
    _, contexts = run._schematron(None)
    rules = contexts.patterns[0][:-1]  # the parent/child rules
    pairs = []
    for rule in rules:
        child = rule.split("'")[1]
        parent = rule.split("'")[3]
        pairs.append((parent, child))
    m = 6000
    parts = [f'<Diggs xmlns="{NS}">']
    expected = []
    for parent, child in pairs:
        parts.append(f"<{parent}>" + f"<{child}>1</{child}>" * m + f"</{parent}>")
        expected += [f"*[local-name()='{child}'][parent::*[local-name()='{parent}']]"] * m
    parts.append("</Diggs>")
    doc = etree.ElementTree(etree.fromstring("".join(parts)))
    assert contexts.patterns[0][: len(pairs)] == expected[::m]
    start = time.perf_counter()
    nodes = contexts.matched(doc, 0, FiredPattern("DIGGS-validation-rules", expected))
    elapsed = time.perf_counter() - start
    assert nodes is not None and len(nodes) == len(expected)
    assert elapsed < 10, elapsed  # the union XPath took ~25 s here


def test_siblings_with_their_own_prefixes_are_located_in_linear_time():
    """Audit H2: 20,000 siblings, one local name, each with its own prefix."""
    n = 20_000
    xml = f'<r xmlns="{NS}">' + "".join(f'<p{k}:c xmlns:p{k}="{NS}"/>' for k in range(n)) + "</r>"
    doc = etree.ElementTree(etree.fromstring(xml))
    locator = Locator(doc)
    start = time.perf_counter()
    found = [locator.element_at(f"/*/p{k}:c") for k in range(0, n, 7)]
    paths = [locator.xpath(el) for el in doc.getroot()[::7]]
    elapsed = time.perf_counter() - start
    assert found == list(doc.getroot()[::7])
    assert paths[1] == "/r[1]/p7:c[1]"
    assert elapsed < 5, elapsed  # quadratic grouping took over a minute


# --- reading and memo keys ------------------------------------------------------------


def test_a_file_that_grew_since_fstat_is_read_on(tmp_path):
    path = tmp_path / "grow.xml"
    path.write_bytes(b"<r>")
    with path.open("rb") as fh:
        stale = os.fstat(fh.fileno())
        path.write_bytes(b"<r>" + b" " * 50 + b"</r>")
        assert _xml._read(fh, stale, 1000) == b"<r>" + b" " * 50 + b"</r>"
    with path.open("rb") as fh:
        stale = os.fstat(fh.fileno())
        path.write_bytes(b"x" * 5000)
        assert len(_xml._read(fh, stale, 1000)) == 1001  # one past the limit: refused


@pytest.mark.timeout(30)
@pytest.mark.skipif(sys.platform == "win32", reason="no pipes as files on Windows")
def test_a_pipe_is_read_whole_by_the_legacy_reader():
    read_end, write_end = os.pipe()
    with os.fdopen(write_end, "wb") as out:
        out.write(b"<r>" + b"a" * 8_000 + b"</r>")  # fits in the pipe buffer
    with os.fdopen(read_end, "rb") as fh:
        info = os.fstat(fh.fileno())
        assert len(_xml._read(fh, info, 10**6)) == 8_007


def test_subtree_memo_is_keyed_by_the_remainder_path():
    semantic = DictionarySemanticValidator()
    root = etree.fromstring("<r><a><b/></a></r>")
    assert semantic._match_within_subtree(root, "a/b") is True
    assert semantic._match_within_subtree(root, "a/c") is False
    assert semantic._match_within_subtree(root, "a") is True
    assert semantic._match_within_subtree(root, "c") is False
