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


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "utf-32-le", "utf-32-be"])
def test_markup_entities_are_found_in_every_unicode_encoding(tmp_path, encoding):
    """utf-16 and utf-32 write a BOM; lxml's push parser cannot read UTF-32 with one."""
    declared = encoding if encoding in ("utf-16", "utf-32") else "utf-8"
    path = _entity_file(tmp_path, "<b/>", encoding=declared)
    if encoding in ("utf-32-le", "utf-32-be"):  # BOM written by hand, no declaration
        bom = b"\xff\xfe\x00\x00" if encoding.endswith("le") else b"\x00\x00\xfe\xff"
        text = f'<!DOCTYPE Diggs [<!ENTITY e "<b/>">]><Diggs xmlns="{NS}">&e;</Diggs>'
        path.write_bytes(bom + text.encode(encoding))
    assert run._markup_entity(path.read_bytes()) == "e"  # the cheap first line
    report = validate(path)
    assert [f.rule_id for f in report.findings] == ["structure.markup-entity"]
    assert not report.ok


def test_the_main_parse_is_checked_too(tmp_path, monkeypatch):
    """If the cheap first check ever misses one, the main parser's own DTD still refuses."""
    monkeypatch.setattr(run, "_markup_entity", lambda data: None)
    report = validate(_entity_file(tmp_path, "<!--x-->", uses=1))
    assert [f.rule_id for f in report.findings] == ["structure.markup-entity"]
    assert {s for s, v in report.steps.items() if v != "skipped"} == {Step.STRUCTURE}


def test_parameter_entities_reach_the_check():
    """A parameter entity's own value counts. (The full parse would also reject these
    files, because pydiggs does not load DTDs, so the check is tested directly.)"""
    declaring = (
        f'<!DOCTYPE r [<!ENTITY % p "&#60;!ENTITY e \'<b/>\'>"> %p;]><r xmlns="{NS}">&e;</r>'
    ).encode()
    valued = f'<!DOCTYPE r [<!ENTITY % p "<b/>"><!ENTITY e "x">]><r xmlns="{NS}">&e;</r>'
    assert run._markup_entity(declaring) == "p"
    assert run._markup_entity(valued.encode()) == "p"
    assert run._markup_entity(b'<!DOCTYPE r [<!ENTITY % p "text">]><r/>') is None


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
    doc.write_text('<Diggs><a n="1"/><b n="2"/><c n="3"/></Diggs>')
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
    assert _located(tmp_path, sch) == [("B", "/Diggs[1]/b[1]")]


def test_abstract_patterns_do_not_shift_the_numbering(tmp_path):
    abstract = (
        '<sch:pattern abstract="true" id="abs"><sch:rule context="$x">'
        '<sch:assert test="false()">ABS</sch:assert></sch:rule></sch:pattern>'
    )
    instance = '<sch:pattern is-a="abs" id="inst"><sch:param name="x" value="c"/></sch:pattern>'
    sch = _sch(tmp_path, "abstract", abstract + _pattern("P1", "a", "A") + instance)
    assert _located(tmp_path, sch) == [("A", "/Diggs[1]/a[1]"), ("ABS", "/Diggs[1]/c[1]")]


def test_included_patterns_are_located(tmp_path):
    (tmp_path / "inc.sch").write_text(f"<sch:pattern {SCH} " + _pattern("P1", "a", "A")[13:])
    sch = _sch(tmp_path, "include", '<sch:include href="inc.sch"/>' + _pattern("P2", "b", "B"))
    assert _located(tmp_path, sch) == [("A", "/Diggs[1]/a[1]"), ("B", "/Diggs[1]/b[1]")]


def test_first_matching_rule_wins_and_unlocatable_contexts_stay_unlocated(tmp_path):
    first = (
        '<sch:pattern id="P"><sch:rule context="*[@n=\'2\']">'
        '<sch:assert test="false()">N2</sch:assert></sch:rule>'
        '<sch:rule context="b|c"><sch:assert test="false()">BC</sch:assert></sch:rule>'
        "</sch:pattern>"
    )
    assert _located(tmp_path, _sch(tmp_path, "first", first)) == [
        ("BC", "/Diggs[1]/c[1]"),
        ("N2", "/Diggs[1]/b[1]"),
    ]
    attr = _pattern("P", "@n", "AT") + _pattern("Q", "c", "C")
    assert _located(tmp_path, _sch(tmp_path, "attr", attr)) == [
        ("AT", None),
        ("AT", None),
        ("AT", None),
        ("C", "/Diggs[1]/c[1]"),
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


# --- Schematron sibling cap -----------------------------------------------------------


def _wide(tmp_path: Path, children: int) -> Path:
    path = tmp_path / f"wide_{children}.xml"
    body = "<percentPassing>-1</percentPassing>" * children
    path.write_text(f'<Diggs xmlns="{NS}"><Grading>{body}<!--c--></Grading></Diggs>')
    return path


@pytest.mark.parametrize("user_rules", [False, True])
def test_schematron_is_refused_above_the_sibling_cap(tmp_path, monkeypatch, user_rules):
    monkeypatch.setattr(run, "MAX_SCHEMATRON_SIBLINGS", 100)
    kwargs = {}
    if user_rules:
        kwargs["schematron_path"] = _sch(
            tmp_path, "user", _pattern("P", "*[local-name()='percentPassing']", "neg")
        )
    under = validate(_wide(tmp_path, 100), checks=["schematron"], **kwargs)
    assert under.steps["schematron"] == "failed"
    assert len(under.findings) == 100
    assert "schematron.incomplete" not in {f.rule_id for f in under.findings}
    over = validate(_wide(tmp_path, 101), checks=["schematron"], **kwargs)
    assert not over.ok
    assert over.steps["schematron"] == "incomplete"
    (finding,) = over.findings
    assert (finding.rule_id, finding.severity.value) == ("schematron.incomplete", "error")
    assert "101 child elements, more than the 100" in finding.message


def test_the_casing_rule_still_runs_above_the_sibling_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MAX_SCHEMATRON_SIBLINGS", 3)
    path = tmp_path / "casing.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}"><a/><a/><a/><a/><Casing>'
        '<casingOutsideDiameter uom="cm">1</casingOutsideDiameter>'
        '<casingInsideDiameter uom="m">1</casingInsideDiameter></Casing></Diggs>'
    )
    rules = {f.rule_id for f in validate(path, checks=["schematron"]).findings}
    assert rules == {"schematron.incomplete", "schematron.Casing.outside-gt-inside-converted"}


# --- Schematron fired-element cap -----------------------------------------------------


def _cement(tmp_path: Path, parents: int, per_parent: int, value: str = "-1") -> Path:
    path = tmp_path / f"cement_{parents}_{per_parent}.xml"
    group = "<Cement>" + f"<weight>{value}</weight>" * per_parent + "</Cement>"
    path.write_text(f'<Diggs xmlns="{NS}">{group * parents}</Diggs>')
    return path


@pytest.mark.parametrize("user_rules", [False, True])
def test_schematron_is_refused_above_the_fired_element_cap(tmp_path, monkeypatch, user_rules):
    monkeypatch.setattr(run, "MAX_SCHEMATRON_FIRED", 50)
    kwargs = {}
    if user_rules:
        kwargs["schematron_path"] = _sch(
            tmp_path, "user", _pattern("P", "*[local-name()='weight']", "neg")
        )
    under = validate(_cement(tmp_path, 5, 10), checks=["schematron"], **kwargs)
    assert under.steps["schematron"] == "failed"
    assert len(under.findings) == 50
    over = validate(_cement(tmp_path, 3, 17), checks=["schematron"], **kwargs)
    assert not over.ok and over.steps["schematron"] == "incomplete"
    (finding,) = over.findings
    assert finding.rule_id == "schematron.incomplete"
    assert "more than 50 elements" in finding.message


def test_the_fired_count_agrees_with_xpath(tmp_path):
    """The C-level count of the bundled contexts equals an XPath count of the same."""
    _, contexts = run._schematron(None)
    xml = (
        f'<Diggs xmlns="{NS}" xmlns:o="urn:other"><Cement><weight>1</weight>'
        "<o:weight>1</o:weight><x><weight/></x></Cement><o:Cement><weight/></o:Cement>"
        "<weight/><Casing><casingOutsideDiameter/></Casing><Grading><percentPassing/>"
        "</Grading></Diggs>"
    )
    doc = etree.ElementTree(etree.fromstring(xml))
    expected = (
        sum(int(doc.xpath(f"count(//{c})")) for c in contexts.patterns[0]) + 1
    )  # the Casing context is counted by name (an upper bound): one Casing
    assert contexts.fired_count(doc, 1_000) == expected
    assert contexts.fired_count(doc, 2) == 3  # stops just past the limit


# --- dictionary Check 6/7/8 bounds ----------------------------------------------------


def _self_dictionary(tmp_path: Path, elements: int, paths: int, terminal: str = "m") -> Path:
    """An instance and a local dictionary with many source paths (audit B1).

    The audit's file was its own dictionary; since round 10 a file whose root is not
    Diggs is refused as not-diggs, so the dictionary is a separate file here.
    """
    occurrences = "".join(
        "<diggs:Occurrence><diggs:sourceElementXpath>"
        f"//diggs:n{i}/diggs:{terminal}</diggs:sourceElementXpath></diggs:Occurrence>"
        for i in range(paths)
    )
    (tmp_path / "d.xml").write_text(
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/3" gml:id="x"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="t"><gml:name>t</gml:name>'
        f"<diggs:occurrences>{occurrences}</diggs:occurrences></diggs:Definition>"
        "</gml:dictionaryEntry></gml:Dictionary>"
    )
    refs = '<diggs:a codeSpace="./d.xml#t">t</diggs:a>' * elements
    path = tmp_path / "self.xml"
    path.write_text(
        '<diggs:Diggs xmlns:gml="http://www.opengis.net/gml/3.2"'
        f' xmlns:diggs="http://diggsml.org/schemas/3"><gml:description>{refs}'
        "</gml:description></diggs:Diggs>"
    )
    return path


def test_definition_paths_are_matched_by_their_last_step(tmp_path):
    """Paths whose last step cannot name the element are never tried: linear time."""
    path = _self_dictionary(tmp_path, elements=10_000, paths=900)
    start = time.process_time()
    report = validate(path, checks=["dictionary"], max_findings=None)
    elapsed = time.process_time() - start
    assert report.steps["dictionary"] == "failed"
    assert {f.rule_id for f in report.findings} == {"dictionary.6"}
    message = report.findings[0].message
    assert message.count("//diggs:n") == 20 and "... and 880 more" in message
    assert elapsed < 4, elapsed  # 9 million path tests: minutes before, ~10 s unfiltered


def test_too_many_paths_or_too_much_work_fail_closed(tmp_path, monkeypatch):
    from pydiggs import dictionary

    report = validate(_self_dictionary(tmp_path, 10, 1_001), checks=["dictionary"])
    assert not report.ok and report.steps["dictionary"] == "incomplete"
    (finding,) = report.findings
    assert "more than 1,000 paths or names" in finding.message
    monkeypatch.setattr(dictionary, "MAX_PATH_MATCH_WORK", 500)
    worked = validate(_self_dictionary(tmp_path, 400, 5, terminal="a"), checks=["dictionary"])
    assert worked.steps["dictionary"] == "incomplete"
    assert any("more than 500 steps" in f.message for f in worked.findings)
    monkeypatch.setattr(dictionary, "MAX_PATH_MATCH_WORK", 10**9)
    assert (
        validate(_self_dictionary(tmp_path, 400, 5, terminal="a"), checks=["dictionary"]).steps[
            "dictionary"
        ]
        == "failed"
    )


@pytest.mark.parametrize(
    "name",
    [
        "tests/test_files/AtterbergExample.xml",
        "tests/fixtures/official/2.6/AtterbergExample-from25.xml",
    ],
)
def test_check8_reads_descendant_paths_in_validate(name):
    """ancestor::diggs:measurement//diggs:procedure means any descendant (Check 8).

    dictionary_check() keeps reading it as a direct child, which skips Check 8 for these
    files; validate() reports the two errors.
    """
    report = validate(ROOT / name, checks=["dictionary"])
    check8 = sorted(
        (f.line, f.message.split('"')[1]) for f in report.findings if f.rule_id == "dictionary.8"
    )
    assert check8 == [(178, "uscs group symbol"), (188, "natural water content")]
    assert all(f.severity.value == "error" for f in report.findings if f.rule_id == "dictionary.8")


def test_paths_naming_absent_ancestors_are_rejected_cheaply(tmp_path):
    """Each path costs one step when one of its ancestors is missing, not a walk."""
    path = _self_dictionary(tmp_path, elements=1_500, paths=900, terminal="a")
    report = validate(path, checks=["dictionary"], max_findings=None)
    # About 3 units per path tried (4 million), within the 5 million budget; walking
    # the ancestors instead would pass it.
    assert report.steps["dictionary"] == "failed"


# --- local dictionaries refused for safety --------------------------------------------


def _local_site(tmp_path: Path, dictionary: bytes) -> Path:
    (tmp_path / "dict.xml").write_bytes(dictionary)
    site = tmp_path / "site.xml"
    site.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        '<role codeSpace="./dict.xml#t">t</role></Diggs>'
    )
    return site


@pytest.mark.parametrize(
    ("dictionary", "why"),
    [
        (
            b'<!DOCTYPE d [<!ENTITY e "<x/>">]><gml:Dictionary'
            b' xmlns:gml="http://www.opengis.net/gml/3.2">&e;</gml:Dictionary>',
            "markup entity 'e'",
        ),
        (b"<a>" * 300 + b"</a>" * 300, "libxml2 refused it"),
    ],
)
def test_unsafe_local_dictionaries_fail_closed(tmp_path, dictionary, why):
    report = validate(_local_site(tmp_path, dictionary), checks=["dictionary"])
    try:  # what this platform's libxml2 reports, shown if the assertion fails
        _xml.parse_bytes(dictionary, None)
        errors = ["parsed"]
    except etree.XMLSyntaxError as exc:
        errors = [(e.type_name, e.domain_name, e.message) for e in exc.parser_errors]
    assert not report.ok and report.steps["dictionary"] == "incomplete", (errors, report.findings)
    by_rule = {f.rule_id: f for f in report.findings}
    assert set(by_rule) == {"dictionary.incomplete", "dictionary.3"}
    assert why in by_rule["dictionary.incomplete"].message
    assert by_rule["dictionary.3"].severity.value == "warning"


# --- reports, worker crashes, scrubbing -----------------------------------------------


def test_reports_pickle_with_every_field():
    import dataclasses

    report = validate(NO_ERROR, checks=["schema"], base_dir=ROOT)
    report = dataclasses.replace(report, absolute_path="/x/y.xml")
    again = pickle.loads(pickle.dumps(report))  # noqa: S301 - our own bytes
    assert again == report
    for item in dataclasses.fields(report):
        assert getattr(again, item.name) == getattr(report, item.name), item.name
    assert again.absolute_path == "/x/y.xml"


def test_the_real_worker_reports_a_crash(work, monkeypatch):
    sent = []

    class Conn:
        def send(self, value):
            sent.append(value)

        def close(self):
            pass

    def boom(path, **kwargs):
        raise RuntimeError(f"boom in {Path(path).resolve()}")

    monkeypatch.setattr(_cli, "validate", boom)
    _cli._worker(Conn(), str(work / "ok.xml"), {})
    ((kind, text),) = sent
    assert kind == "crash" and text.startswith("RuntimeError: boom in ")
    monkeypatch.setattr(_cli.multiprocessing, "get_context", lambda method: _Context(sent[0]))
    report = _cli._validate_isolated(work / "ok.xml", {"max_bytes": 10**8}, 5.0)
    (finding,) = report.findings
    assert finding.rule_id == "structure.internal-error"
    assert str(work) not in finding.message  # scrubbed to the shown path
    assert "boom in ok.xml" in finding.message


def test_internal_error_text_is_cut_short(work, monkeypatch):
    def boom(path, **kwargs):
        raise ValueError("x" * 5_000)

    monkeypatch.setattr(_cli, "validate", boom)
    reports, _ = _cli._run(_cli._options(_cli.build_parser(), ["ok.xml"]), [work / "ok.xml"])
    (finding,) = reports[0].findings
    assert len(finding.message) < 400


def test_old_bundled_rule_contexts_keep_their_rule_ids():
    from pydiggs.findings import rules

    old = "//*[local-name()='Grading']/*[local-name()='percentPassing']"
    assert (
        rules.schematron_bundled_rule(old, "number(.) >= 0") == "schematron.Grading.percentPassing"
    )


# --- round 7: per-element costs in dictionary Checks 8 to 13 --------------------------

_LOCAL_DEF = (
    '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
    ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="x"><gml:dictionaryEntry>'
    '<diggs:Definition gml:id="t"><gml:name>t</gml:name><diggs:occurrences>'
    "<diggs:Occurrence><diggs:sourceElementXpath>//diggs:propertyClass"
    "</diggs:sourceElementXpath>{conditionals}</diggs:Occurrence></diggs:occurrences>"
    "{extra}</diggs:Definition></gml:dictionaryEntry></gml:Dictionary>"
)


def _local_definition(tmp_path: Path, body: str, conditionals: str = "", extra: str = "") -> Path:
    (tmp_path / "d.xml").write_text(_LOCAL_DEF.format(conditionals=conditionals, extra=extra))
    site = tmp_path / "i.xml"
    site.write_text(f'<Diggs xmlns="{NS}">{body}</Diggs>')
    return site


def test_property_class_siblings_are_read_once(tmp_path):
    """Audit: 8,000 coded siblings under one parent took 59 s (each rescanned the rest)."""
    el = (
        '<propertyClass codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml'
        '#aashto_group_index">aashto_group_index</propertyClass>'
    )
    path = tmp_path / "bsib.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}"><characteristicProperty>{el * 4_000}</characteristicProperty></Diggs>'
    )
    start = time.process_time()
    report = validate(path, checks=["dictionary"])
    assert time.process_time() - start < 3
    assert report.steps["dictionary"] == "passed"


def test_definitions_are_read_once_and_huge_ones_refused(tmp_path):
    body = '<p><propertyClass codeSpace="./d.xml#t">t</propertyClass></p>' * 500
    site = _local_definition(tmp_path, body, extra="<x/>" * 9_000)
    start = time.process_time()
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"
    assert time.process_time() - start < 3  # 500 x 4 whole-Definition XPaths before
    huge = _local_definition(tmp_path, body, extra="<x/>" * 10_001)
    report = validate(huge, checks=["dictionary"])
    assert report.steps["dictionary"] == "incomplete"
    assert any("more than 10,000 nodes" in f.message for f in report.findings)


def test_long_conditional_paths_are_bounded(tmp_path):
    def conditionals(steps: int) -> str:
        return "".join(
            "<diggs:conditionalElementXpath>ancestor::diggs:a/"
            f"{'diggs:b/' * steps}diggs:z{i}</diggs:conditionalElementXpath>"
            for i in range(4)
        )

    body = "<a>" + "<b/>" * 3_000 + '<propertyClass codeSpace="./d.xml#t">t</propertyClass></a>'
    start = time.process_time()
    report = validate(_local_definition(tmp_path, body, conditionals(60)), checks=["dictionary"])
    assert time.process_time() - start < 3
    assert report.steps["dictionary"] == "failed"  # Check 8: no z child
    too_long = validate(_local_definition(tmp_path, body, conditionals(64)), checks=["dictionary"])
    assert too_long.steps["dictionary"] == "incomplete"
    assert any("more than 64 steps" in f.message for f in too_long.findings)


def test_a_slash_inside_a_predicate_is_not_a_step(tmp_path):
    cond = (
        "<diggs:conditionalElementXpath>ancestor::diggs:measurement//diggs:procedure/"
        'diggs:X/diggs:method[text()="ERI/ERT"]</diggs:conditionalElementXpath>'
    )
    inside = (
        '<measurement><Test><outcome><propertyClass codeSpace="./d.xml#t">t</propertyClass>'
        "</outcome><procedure><X><method>ERI/ERT</method></X></procedure></Test></measurement>"
    )
    assert validate(_local_definition(tmp_path, inside, cond), checks=["dictionary"]).ok
    other = inside.replace("<X>", "<Y>").replace("</X>", "</Y>")
    report = validate(_local_definition(tmp_path, other, cond), checks=["dictionary"])
    (finding,) = [f for f in report.findings if f.rule_id == "dictionary.8"]
    assert finding.message.endswith("measurements: \n     method.")


def test_overlong_names_in_a_local_dictionary_fail_closed(tmp_path):
    name = "n" * 60_000
    site = _local_site(tmp_path, f"<{name}/>".encode())
    report = validate(site, checks=["dictionary"])
    assert report.steps["dictionary"] == "incomplete"
    assert any("Name too long" in f.message for f in report.findings)


def test_internal_error_text_is_scrubbed_before_it_is_cut(work, monkeypatch, tmp_path):
    import urllib.parse

    home = work.parent
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    target = work / "ok.xml"
    spellings = [
        str(target.resolve()),
        target.resolve().as_uri(),
        urllib.parse.quote(home.as_posix()),
    ]

    def boom(path, **kwargs):
        raise RuntimeError("x" * 280 + " ".join(spellings) + f" {home}/elsewhere.txt")

    monkeypatch.setattr(_cli, "validate", boom)
    reports, _ = _cli._run(_cli._options(_cli.build_parser(), ["ok.xml"]), [target])
    (finding,) = reports[0].findings
    assert str(home) not in finding.message
    assert str(home)[:12] not in finding.message  # not even a cut-off prefix
    assert "ok.xml" in finding.message


# --- round 7: Schematron count branches ---------------------------------------------


def test_fired_count_falls_back_to_xpath_count(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MAX_SCHEMATRON_FIRED", 5)
    path = _cement(tmp_path, 1, 6)
    # A prefixed name test and a descendant step are not "local name plus predicates".
    for context in ("d:weight", "*[local-name()='Cement']//*[local-name()='weight']"):
        sch = tmp_path / "x.sch"
        sch.write_text(
            f'<sch:schema {SCH}><sch:ns prefix="d" uri="{NS}"/>'
            + _pattern("P", context, "neg")
            + "</sch:schema>"
        )
        report = validate(path, checks=["schematron"], schematron_path=sch)
        assert report.steps["schematron"] == "incomplete", context
    _, contexts = run._schematron(None)
    doc = etree.ElementTree(
        etree.fromstring(f'<Diggs xmlns="{NS}"><Cement><a><weight/></a></Cement></Diggs>')
    )
    assert contexts._count(doc, "*[local-name()='Cement']//*[local-name()='weight']", 10) == 1


def test_an_unexpanded_abstract_pattern_is_never_located():
    schema = etree.ElementTree(
        etree.fromstring(
            f'<sch:schema {SCH}><sch:pattern abstract="true" id="a">'
            '<sch:rule context="$x"><sch:assert test="1"/></sch:rule></sch:pattern>'
            '<sch:pattern id="b"><sch:rule context="b"><sch:assert test="1"/></sch:rule>'
            "</sch:pattern></sch:schema>"
        )
    )
    contexts = RuleContexts(schema)
    assert contexts.patterns == [[], ["b"]]
    doc = etree.ElementTree(etree.fromstring("<r><b/></r>"))
    assert contexts.matched(doc, 0, FiredPattern("a", ["$x"])) is None


def test_parser_limits_are_read_from_this_parse_only(tmp_path):
    """lxml's exception log can hold earlier parses' errors; only this parse counts."""
    for _ in range(150):  # fill the shared log with limit errors from other parses
        with pytest.raises(etree.XMLSyntaxError):
            _xml.parse_bytes(b"<" + b"n" * 60_000 + b"/>", None)
    broken = validate(_local_site(tmp_path, b"<a><b></a>"), checks=["dictionary"])
    assert broken.steps["dictionary"] == "passed"  # malformed, not a limit: warning only
    assert [f.rule_id for f in broken.findings] == ["dictionary.3"]
    limited = validate(_local_site(tmp_path, b"<" + b"m" * 60_000 + b"/>"), checks=["dictionary"])
    assert limited.steps["dictionary"] == "incomplete"


# --- round 8 -------------------------------------------------------------------------

_DEF_HEAD = (
    '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
    ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="x"><gml:dictionaryEntry>'
    '<diggs:Definition gml:id="t">'
)
_DEF_TAIL = "</diggs:Definition></gml:dictionaryEntry></gml:Dictionary>"


def _definition(
    tmp_path: Path, inner: str, body: str, extra_files: dict[str, str] | None = None
) -> Path:
    """A local Definition ``t`` (``inner`` is its content) and an instance ``body``."""
    (tmp_path / "d.xml").write_text(_DEF_HEAD + inner + _DEF_TAIL)
    for name, text in (extra_files or {}).items():
        (tmp_path / name).write_text(text)
    site = tmp_path / "i.xml"
    site.write_text(
        f'<Diggs xmlns="{NS}" xmlns:gml="http://www.opengis.net/gml/3.2">{body}</Diggs>'
    )
    return site


def _sources(*paths: str) -> str:
    return (
        "<diggs:occurrences><diggs:Occurrence>"
        + "".join(f"<diggs:sourceElementXpath>{p}</diggs:sourceElementXpath>" for p in paths)
        + "</diggs:Occurrence></diggs:occurrences>"
    )


_PC = '<propertyClass codeSpace="./d.xml#t">t</propertyClass>'


def _rules(report) -> list[str]:
    return sorted(f.rule_id for f in report.findings)


def test_huge_column_indexes_never_raise(tmp_path):
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = (
        "<gml:name>t</gml:name>"
        + _sources("//diggs:propertyClass")
        + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    )
    for index, rules_expected in (("0" * 5_000 + "1", ["dictionary.13"]), ("9" * 5_001, [])):
        body = (
            '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
            f'<Property index="{index}">{_PC}</Property></properties></PropertyParameters>'
            "</parameters><dataValues>bad</dataValues></ResultSet>"
        )
        report = validate(
            _definition(tmp_path, inner, body, {"c.xml": codes}), checks=["dictionary"]
        )
        assert _rules(report) == rules_expected, index[:3]


@pytest.mark.parametrize(
    ("target", "step"),
    [
        ("_run_schema", "schema"),
        ("_run_dictionary", "dictionary"),
        ("_run_schematron", "schematron"),
        ("_run_deprecation", "deprecation"),
        ("_run_context", "context"),
        ("_casing_diameter_problems", "schematron"),
    ],
)
def test_an_unexpected_error_in_any_step_fails_closed(monkeypatch, target, step):
    def boom(*args, **kwargs):
        raise RuntimeError(f"bug in {NO_ERROR.resolve()}")

    monkeypatch.setattr(run, target, boom)
    report = validate(NO_ERROR, checks=[step])
    assert not report.ok and report.steps[step] == "incomplete"
    (finding,) = [f for f in report.findings if f.rule_id == f"{step}.incomplete"]
    assert "RuntimeError: bug in" in finding.message
    assert str(NO_ERROR.resolve()) not in finding.message  # scrubbed by the builder


def test_every_candidate_path_is_charged(tmp_path, monkeypatch):
    """Audit: paths rejected on their prefix cost nothing, so the budget never stopped them."""
    from pydiggs import dictionary

    monkeypatch.setattr(dictionary, "MAX_PATH_MATCH_WORK", 50_000)
    inner = "<gml:name>t</gml:name>" + _sources(
        *(f"//gml:propertyClass[{i}]" for i in range(500)), "//diggs:propertyClass"
    )
    report = validate(_definition(tmp_path, inner, _PC * 200), checks=["dictionary"])
    assert report.steps["dictionary"] == "incomplete"


def test_check13_columns_are_read_once_per_result_set(tmp_path, monkeypatch):
    from pydiggs import dictionary

    calls = []
    real = dictionary._Check13Index.column
    monkeypatch.setattr(
        dictionary._Check13Index,
        "column",
        lambda self, rs, col, *rest: calls.append(col) or real(self, rs, col, *rest),
    )
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = (
        "<gml:name>t</gml:name>"
        + _sources("//diggs:propertyClass")
        + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    )
    props = f'<Property index="1">{_PC}</Property>' * 300
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f"{props}</properties></PropertyParameters></parameters>"
        "<dataValues>ok bad other</dataValues></ResultSet>"
    )
    report = validate(_definition(tmp_path, inner, body, {"c.xml": codes}), checks=["dictionary"])
    assert _rules(report) == ["dictionary.13"] * 300
    assert len(calls) == 1


def test_code_list_terms_are_not_rebuilt_per_property(tmp_path):
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c">'
        + "".join(f'<diggs:Definition gml:id="v{i}"/>' for i in range(50_000))
        + "</gml:Dictionary>"
    )
    inner = (
        "<gml:name>t</gml:name>"
        + _sources("//diggs:propertyClass")
        + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    )
    site = _definition(tmp_path, inner, _PC * 5_000, {"c.xml": codes})
    start = time.process_time()
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"
    assert time.process_time() - start < 4  # 5,000 x a 50,000-term frozenset before


def test_ancestor_name_sets_are_shared():
    from pydiggs.dictionary import DictionarySemanticValidator

    chain = (
        "".join(f"<c{i}>" for i in range(50))
        + "<p><x/></p>" * 3
        + "".join(f"</c{i}>" for i in reversed(range(50)))
    )
    root = etree.fromstring(f"<r>{chain}</r>")
    xs = list(root.iter("x"))
    semantic = DictionarySemanticValidator(bounded=True)
    sets = [semantic._ancestor_names(x) for x in xs]
    assert sets[0] is sets[1] is sets[2]  # one set for the three <p> parents
    assert "p" in sets[0] and "c49" in sets[0]


def test_child_path_memo_entries_are_charged():
    from pydiggs import dictionary
    from pydiggs.dictionary import DictionarySemanticValidator

    semantic = DictionarySemanticValidator(bounded=True)
    root = etree.fromstring("<a>" + "<b/>" * 10 + "</a>")
    segments = semantic._segments("b/z")
    before = semantic._work
    for child in [root, *root]:
        semantic._match_child_path(child, segments, 0)
    assert semantic._work - before >= dictionary._MEMO_ENTRY_COST * 11


def test_datablocks_in_nested_measurements_are_counted_in_linear_time(tmp_path):
    path = tmp_path / "nest.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}"><measurement>'
        + "<result>" * 150
        + "<Property/>" * 8_000
        + "</result>" * 150
        + "<dataBlock>1,2 3</dataBlock></measurement></Diggs>"
    )
    start = time.process_time()
    report = validate(path, checks=["context"])
    assert time.process_time() - start < 3
    (finding,) = report.findings
    assert "tuple 1 has 2 value(s) but 8000 Property declaration(s)" in finding.message


def test_linear_datablock_check_matches_the_legacy_one():
    import random

    from pydiggs import semantic

    names = ["measurement", "result", "Result", "Property", "dataBlock", "x"]
    compared = 0
    for seed in range(600):
        rng = random.Random(seed)  # noqa: S311 - test data

        def element(depth: int, rng: random.Random = rng) -> etree._Element:
            el = etree.Element(rng.choice(names) if depth else "Diggs")
            if el.tag == "dataBlock":
                el.text = " ".join(
                    ",".join("1" * rng.randint(0, 3)) for _ in range(rng.randint(0, 3))
                )
                return el
            if depth < 6:
                for _ in range(rng.randint(0, 3)):
                    el.append(element(depth + 1, rng))
            return el

        root = element(0)
        legacy = semantic.ContextValidationResult()
        semantic._check_datablocks(root, legacy)
        linear = semantic.ContextValidationResult(legacy_paths=False)
        semantic._check_datablocks_linear(root, linear)
        assert [(m.text, m.node) for m in linear.messages] == [
            (m.text, m.node) for m in legacy.messages
        ], seed
        compared += len(legacy.messages)
    assert compared > 100


def test_datablock_pairs_are_bounded(monkeypatch):
    from pydiggs import semantic

    monkeypatch.setattr(semantic, "MAX_DATABLOCK_PAIRS", 10)
    root = etree.fromstring(
        "<Diggs>"
        + "<measurement>" * 5
        + "<result><Property/></result>"
        + "<dataBlock>1</dataBlock>" * 3
        + "</measurement>" * 5
        + "</Diggs>"
    )
    result = semantic.validate_context_tree(root, legacy_paths=False)
    assert result.limits_hit
    assert not [m for m in result.messages if m.check == 103]


def test_a_predicate_only_conditional_matches_nothing(tmp_path):
    inner = (
        "<gml:name>v</gml:name><diggs:occurrences><diggs:Occurrence>"
        "<diggs:conditionalElementXpath>[@x='only a predicate']</diggs:conditionalElementXpath>"
        "</diggs:Occurrence></diggs:occurrences>"
    )
    body = '<Property><propertyClass codeSpace="./d.xml#t">v</propertyClass></Property>'
    assert _rules(validate(_definition(tmp_path, inner, body), checks=["dictionary"])) == [
        "dictionary.8"
    ]


@pytest.mark.parametrize("home", ["/", "a b@c"])
def test_scrubbing_handles_odd_home_folders(tmp_path, monkeypatch, home):
    import urllib.parse

    home_path = Path(Path.cwd().anchor) if home == "/" else tmp_path / home
    # Path.home() reads HOME on POSIX but USERPROFILE on Windows: patch it directly.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_path))
    target = tmp_path / "site.xml"
    if home == "/":
        text = _cli._scrub("RuntimeError: /etc/passwd and file:///x", target, "site.xml")
        assert text == "RuntimeError: /etc/passwd and file:///x"  # nothing becomes "~"
        return
    raw = f"at {urllib.parse.quote(home_path.as_posix())}/q and {home_path.as_uri()}/r"
    text = _cli._scrub(raw, target, "site.xml")
    assert text == "at ~/q and ~/r"


def test_scrubbing_survives_an_unknown_home(monkeypatch, tmp_path):
    def nohome():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", staticmethod(nohome))
    assert _cli._scrub("x", tmp_path / "a.xml", "a.xml") == "x"


# --- round 8: pinned behaviour of the refactored dictionary reads ---------------------


def _check(tmp_path: Path, inner: str, body: str, files: dict[str, str] | None = None):
    report = validate(_definition(tmp_path, inner, body, files), checks=["dictionary"])
    return [(f.rule_id, f.message) for f in report.findings]


_SRC = _sources("//diggs:propertyClass")


@pytest.mark.parametrize(
    ("siblings", "expected"),
    [
        ("<typeData>string</typeData><typeData>double</typeData>", []),  # the last one counts
        ("<typeData>double</typeData><typeData>string</typeData>", ["dictionary.9"]),
    ],
)
def test_the_last_type_data_sibling_counts(tmp_path, siblings, expected):
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:dataType>double</diggs:dataType>"
    found = _check(tmp_path, inner, f"<Property>{_PC}{siblings}</Property>")
    assert [r for r, _ in found] == expected


@pytest.mark.parametrize(
    ("siblings", "expected"),
    [
        ("<uom>kg</uom><uom>m</uom>", []),  # the last one counts
        ("<uom>m</uom><uom>kg</uom>", ["dictionary.12"]),
    ],
)
def test_the_last_uom_sibling_counts(tmp_path, siblings, expected):
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:quantityClass>length</diggs:quantityClass>"
    found = _check(tmp_path, inner, f"<Property>{_PC}{siblings}</Property>")
    assert [r for r, _ in found] == expected


@pytest.mark.parametrize(("value", "expected"), [("x", []), ("y", ["dictionary.13"])])
def test_the_first_null_value_sibling_counts(tmp_path, value, expected):
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{_PC}<nullValue>x</nullValue><nullValue>y</nullValue></Property>'
        f"</properties></PropertyParameters></parameters><dataValues>{value}</dataValues>"
        "</ResultSet>"
    )
    assert [r for r, _ in _check(tmp_path, inner, body, {"c.xml": codes})] == expected


def test_definition_fields_are_the_first_in_document_order(tmp_path):
    """The first non-empty name; the first dataType, even inside a nested element."""
    inner = (
        "<gml:name></gml:name><diggs:x><gml:name>First</gml:name>"
        "<diggs:dataType>integer</diggs:dataType></diggs:x><gml:name>Second</gml:name>"
        + _SRC
        + "<diggs:dataType>string</diggs:dataType>"
    )
    ((rule, message),) = _check(
        tmp_path, inner, f"<Property>{_PC}<typeData>string</typeData></Property>"
    )
    assert rule == "dictionary.9"
    assert 'for "first" should be "integer"' in message


def test_check13_names_the_definition_by_its_direct_name_child(tmp_path):
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = (
        "<diggs:x><gml:name>inner</gml:name></diggs:x><gml:name>outer</gml:name>"
        + _SRC
        + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    )
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{_PC}</Property></properties></PropertyParameters>'
        "</parameters><dataValues>bad</dataValues></ResultSet>"
    )
    ((rule, message),) = _check(tmp_path, inner, body, {"c.xml": codes})
    assert rule == "dictionary.13" and 'of "outer" are not terms' in message


@pytest.mark.parametrize(
    "source",
    [
        '//diggs:a/diggs:propertyClass[@k="x/y"]',  # a "/" inside the last step's predicate
        '//diggs:a/diggs:propertyClass[text()="a]b"]',  # a "]" inside a quoted string
    ],
)
def test_predicates_do_not_change_a_source_path_steps(tmp_path, source):
    inner = "<gml:name>t</gml:name>" + _sources(source)
    assert _check(tmp_path, inner, f"<a>{_PC}</a>") == []
    assert [r for r, _ in _check(tmp_path, inner, f"<b>{_PC}</b>")] == ["dictionary.6"]


@pytest.mark.parametrize(("steps", "incomplete"), [(64, False), (65, True)])
def test_path_length_limit_boundary(tmp_path, steps, incomplete):
    absolute = "//" + "diggs:a/" * (steps - 1) + "diggs:propertyClass"
    report = validate(
        _definition(tmp_path, "<gml:name>t</gml:name>" + _sources(absolute), _PC),
        checks=["dictionary"],
    )
    assert (report.steps["dictionary"] == "incomplete") is incomplete
    # The steps below the ancestor step count (the remainder is matched as a path).
    conditional = "ancestor::diggs:a/" + "diggs:b/" * (steps - 1) + "diggs:z"
    inner = (
        "<gml:name>t</gml:name><diggs:occurrences><diggs:Occurrence><diggs:sourceElementXpath>"
        "//diggs:propertyClass</diggs:sourceElementXpath><diggs:conditionalElementXpath>"
        f"{conditional}</diggs:conditionalElementXpath></diggs:Occurrence></diggs:occurrences>"
    )
    report = validate(_definition(tmp_path, inner, f"<a>{_PC}</a>"), checks=["dictionary"])
    assert (report.steps["dictionary"] == "incomplete") is incomplete


def test_home_scrubbing_prefers_the_longest_spelling(tmp_path, monkeypatch):
    home = tmp_path / "h"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    assert _cli._scrub(f"at {home.as_uri()}/x", tmp_path / "a.xml", "a.xml") == "at ~/x"
    assert _cli._scrub(f"at {home}{os.sep}x", tmp_path / "a.xml", "a.xml") == f"at ~{os.sep}x"


def test_the_strip_cache_is_per_instance():
    from pydiggs.dictionary import DictionarySemanticValidator

    semantic = DictionarySemanticValidator(bounded=True)
    assert semantic._stripped("a[x/y]/b") == "a/b"
    semantic.release()
    assert semantic._strip_cache == {}


def test_work_is_charged_where_it_is_done():
    """Each kind of dictionary work counts towards the budget (unit level)."""
    from pydiggs.dictionary import DictionarySemanticValidator

    semantic = DictionarySemanticValidator(bounded=True)
    root = etree.fromstring("<a><b><p/>" + "<x/>" * 40 + "</b></a>")
    el = root[0][0]
    semantic._evaluate_xpath_match(el, "//nothing", relative=False)  # parse it once
    before = semantic._work
    semantic._evaluate_xpath_match(el, "//nothing", relative=False)
    assert semantic._work > before  # every path tried costs at least one unit
    before = semantic._work
    semantic._siblings(root[0])
    assert semantic._work - before >= 41  # reading a parent's children


def test_check13_result_sets_are_charged(tmp_path, monkeypatch):
    from pydiggs import dictionary

    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    props = "".join(f'<Property index="{i + 1}">{_PC}</Property>' for i in range(30))
    citing = (
        '<ResultSet><parameters xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#PP"/>'
        "<dataValues>ok</dataValues></ResultSet>"
    ) * 300
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f"{props}</properties></PropertyParameters></parameters></ResultSet>{citing}"
    )
    site = _definition(tmp_path, inner, body, {"c.xml": codes})
    work = _check13_work(monkeypatch, site)
    assert work >= 2 * 301 * dictionary._CHECK13_STEP  # each ResultSet read and merged
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work - 1)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "incomplete"
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"


def test_a_context_limit_makes_the_step_incomplete(tmp_path, monkeypatch):
    from pydiggs import semantic

    monkeypatch.setattr(semantic, "MAX_DATABLOCK_PAIRS", 2)
    path = tmp_path / "m.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}">'
        + "<measurement>" * 3
        + "<dataBlock>1</dataBlock>"
        + "</measurement>" * 3
        + "</Diggs>"
    )
    report = validate(path, checks=["context"])
    assert report.steps["context"] == "incomplete"
    assert "context.incomplete" in {f.rule_id for f in report.findings}


# --- round 9 -------------------------------------------------------------------------


def _entities(tmp_path: Path, value: str, uses: int) -> Path:
    path = tmp_path / "ent.xml"
    path.write_text(
        f'<!DOCTYPE Diggs [<!ENTITY e "{value}">]><Diggs xmlns="{NS}">' + "&e;" * uses + "</Diggs>"
    )
    return path


def test_entities_that_grow_the_file_are_refused(tmp_path):
    report = validate(_entities(tmp_path, "x" * 1_000, 50))
    (finding,) = report.findings
    assert (finding.rule_id, finding.severity.value) == ("structure.entity-expansion", "error")
    assert {s for s, v in report.steps.items() if v != "skipped"} == {Step.STRUCTURE}
    assert not report.ok


def test_small_text_entities_still_work(tmp_path):
    path = tmp_path / "deg.xml"
    path.write_text(
        '<!DOCTYPE Diggs [<!ENTITY deg "&#176;">]>'
        f'<Diggs xmlns="{NS}"><a>12&deg; north</a><b>3&deg;</b></Diggs>'
    )
    report = validate(path, checks=["schema", "context"])
    assert "structure.entity-expansion" not in {f.rule_id for f in report.findings}
    assert report.steps["schema"] == "failed"  # it ran


def test_entity_expansion_boundary(tmp_path, monkeypatch):
    path = _entities(tmp_path, "x" * 100, 40)  # 4,000 characters of text
    raw = path.stat().st_size
    doc = _xml.parse(str(path))
    namespaces = run._namespaces(doc)[1]  # the namespace URI counts too
    size = run._expanded_size(doc, 10**9, namespaces)
    assert size == 4_000 + namespaces
    monkeypatch.setattr(run, "MAX_ENTITY_EXPANSION", size / raw + 0.01)  # just under: runs
    assert validate(path, checks=["context"]).steps["context"] != "skipped"
    monkeypatch.setattr(run, "MAX_ENTITY_EXPANSION", size / raw - 0.01)  # just over: refused
    assert [f.rule_id for f in validate(path).findings] == ["structure.entity-expansion"]


def test_no_entities_means_nothing_is_measured(tmp_path):
    doc = _xml.parse(str(NO_ERROR))
    assert run._expanded_size(doc, 1) == 0  # no internal DTD: never refused


def _check13_work(monkeypatch, site) -> int:
    """Check 13 work units validate() spends on ``site`` with an unbounded budget."""
    from pydiggs import dictionary

    seen = []
    real = dictionary._Check13Index.__init__

    def init(self, *args, **kwargs):
        real(self, *args, **kwargs)
        seen.append(self)

    monkeypatch.setattr(dictionary._Check13Index, "__init__", init)
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", 10**12)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"
    monkeypatch.setattr(dictionary._Check13Index, "__init__", real)
    return seen[-1]._work


def test_check13_keeps_no_rows_and_charges_fields(tmp_path, monkeypatch):
    from pydiggs import dictionary

    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{_PC}</Property></properties></PropertyParameters>'
        "</parameters><dataValues>" + "ok,x " * 3_000 + "</dataValues></ResultSet>"
    )
    site = _definition(tmp_path, inner, body, {"c.xml": codes})
    assert not hasattr(dictionary._Check13Index, "rows")
    work = _check13_work(monkeypatch, site)
    # 3,000 rows: each token and each of its two fields (split at most once) is charged.
    assert work >= 3_000 * 3 * dictionary._CHECK13_STEP
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work - 3_000)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "incomplete"
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"


def test_each_datablock_pair_is_constant_time(tmp_path):
    path = tmp_path / "scan.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}">'
        + "<measurement>" * 100
        + "<result><Property/></result>"
        + "<dataBlock>"
        + "1 " * 200_000
        + "2,2</dataBlock>"
        + "</measurement>" * 100
        + "</Diggs>"
    )
    start = time.process_time()
    report = validate(path, checks=["context"])
    assert time.process_time() - start < 3  # 100 x 200,000 tuple rescans before
    assert len(report.findings) == 100
    assert all("tuple 200001 has 2 value(s)" in f.message for f in report.findings)


def test_a_non_utf8_file_name_is_passed_as_a_uri(tmp_path, monkeypatch):
    import pathlib

    weird = Path(str(tmp_path / "a\udcff.xml"))
    assert run._base_url(weird).startswith("file://")
    target = NO_ERROR.resolve()
    real = pathlib.Path.resolve

    def resolve(self, strict=False):
        resolved = real(self, strict)
        return (
            Path(str(resolved).replace("No_Error", "No\udcffError"))
            if resolved == target
            else resolved
        )

    monkeypatch.setattr(pathlib.Path, "resolve", resolve)
    report = validate(NO_ERROR)  # no UnicodeEncodeError from lxml
    assert report.ok
    report.to_json()


@pytest.mark.parametrize("use", ["<a/>&e;", '<a v="&e;"/>'])
def test_expansion_in_tails_and_attributes_counts(tmp_path, use):
    path = tmp_path / "t.xml"
    path.write_text(
        f'<!DOCTYPE Diggs [<!ENTITY e "{"x" * 1_000}">]><Diggs xmlns="{NS}">'
        + use * 50
        + "</Diggs>"
    )
    assert [f.rule_id for f in validate(path).findings] == ["structure.entity-expansion"]


# --- round 10 ------------------------------------------------------------------------


def test_an_overlong_namespace_uri_is_refused(tmp_path):
    path = tmp_path / "ns.xml"
    path.write_text(f'<Diggs xmlns="urn:{"a" * 2_000}"><a/></Diggs>')
    report = validate(path)
    (finding,) = report.findings
    assert (finding.rule_id, finding.severity.value) == ("structure.namespace-too-long", "error")
    assert {s for s, v in report.steps.items() if v != "skipped"} == {Step.STRUCTURE}


def test_attlist_namespace_defaults_count_towards_expansion(tmp_path):
    """An ATTLIST default xmlns:p is declared on every <a> without growing the file."""
    uri = "urn:" + "u" * 1_000  # under the 1,024 limit
    path = tmp_path / "attlist.xml"
    path.write_text(
        f'<!DOCTYPE Diggs [<!ATTLIST a xmlns:p CDATA "{uri}">]><Diggs xmlns="{NS}">'
        + "<a/>" * 200
        + "</Diggs>"
    )
    assert [f.rule_id for f in validate(path).findings] == ["structure.entity-expansion"]
    too_long = tmp_path / "attlist_long.xml"
    too_long.write_text(
        f'<!DOCTYPE Diggs [<!ATTLIST a xmlns:p CDATA "urn:{"u" * 1_100}">]>'
        f'<Diggs xmlns="{NS}"><a/></Diggs>'
    )
    assert [f.rule_id for f in validate(too_long).findings] == ["structure.namespace-too-long"]


def test_attlist_attribute_defaults_count_towards_expansion(tmp_path):
    """libxml2 applies an ATTLIST default to every matching element: it is counted."""
    path = tmp_path / "defaults.xml"
    path.write_text(
        f'<!DOCTYPE Diggs [<!ATTLIST a x CDATA "{"v" * 500}">]><Diggs>'
        + '<a xmlns=""/>' * 100
        + "</Diggs>"
    )
    doc = _xml.parse(str(path))
    assert all(len(a.get("x", "")) == 500 for a in doc.getroot())  # applied, no entity
    assert [f.rule_id for f in validate(path).findings] == ["structure.entity-expansion"]


def test_expansion_is_measured_in_bytes(tmp_path):
    """Text is counted as UTF-8 bytes, like the raw file size it is compared with."""
    doc = _xml.parse_bytes('<!DOCTYPE r [<!ENTITY e "x">]><r a="€">ééé</r>'.encode(), None)
    assert run._expanded_size(doc, 10**9) == 3 + 6  # "€" is 3 bytes, "ééé" 6


@pytest.mark.parametrize(
    "dictionary",
    [
        b"<a><!--" + b"x" * 10_500_000 + b"--></a>",
        b"<a><?p " + b"x" * 10_500_000 + b"?></a>",
        b"<a><![CDATA[" + b"x" * 10_500_000 + b"]]></a>",
    ],
    ids=["comment", "pi", "cdata"],
)
def test_oversized_comment_pi_and_cdata_in_a_local_dictionary_fail_closed(tmp_path, dictionary):
    report = validate(_local_site(tmp_path, dictionary), checks=["dictionary"])
    assert report.steps["dictionary"] == "incomplete", report.findings
    assert any("too big" in f.message for f in report.findings)


@pytest.mark.parametrize(
    "dictionary",
    [
        b"<maximumDepth><b></maximumDepth>",  # a tag mismatch quoting a limit-like name
        b"<XML_PARSE_HUGE><b></XML_PARSE_HUGE>",  # a quoted name equal to a limit phrase
        b"<a>&depth;</a>",  # an undeclared entity called "depth"
        b"<a><q:depth/></a>",  # an undeclared namespace prefix
        # A PI that never ends quotes its target in a *_NOT_FINISHED error, the code
        # libxml2 also uses for its size limits, so only multi-word phrases may match.
        b"<a><?depth_XML_PARSE_HUGE ",
    ],
)
def test_ordinary_errors_quoting_limit_words_stay_warnings(tmp_path, dictionary):
    report = validate(_local_site(tmp_path, dictionary), checks=["dictionary"])
    assert [f.rule_id for f in report.findings] == ["dictionary.3"]
    assert report.steps["dictionary"] == "passed"


def test_a_context_step_error_fails_both_context_steps(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(run, "validate_context_tree", boom)
    report = validate(NO_ERROR, checks=["geometry", "context"])
    assert report.steps["geometry"] == report.steps["context"] == "incomplete"
    assert {f.rule_id for f in report.findings} == {"geometry.incomplete", "context.incomplete"}


def test_invalid_values_are_cached_per_null_value(tmp_path):
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{_PC}<nullValue>x</nullValue></Property>'
        f'<Property index="1">{_PC}<nullValue>y</nullValue></Property>'
        "</properties></PropertyParameters></parameters><dataValues>x y</dataValues></ResultSet>"
    )
    messages = [
        m for r, m in _check(tmp_path, inner, body, {"c.xml": codes}) if r == "dictionary.13"
    ]
    assert len(messages) == 2
    assert '"y"' in messages[0] and '"x"' not in messages[0].split(":")[1]
    assert '"x"' in messages[1]


def test_entity_expansion_at_exactly_twice_the_raw_size(tmp_path):
    """Exactly 2x is allowed; one byte more is refused (MAX_ENTITY_EXPANSION = 2)."""

    def build(pad: int, length: int) -> tuple[Path, int, int]:
        path = tmp_path / "x.xml"
        path.write_text(
            f'<!DOCTYPE Diggs [<!ENTITY e "{"x" * length}">]>'
            f"<Diggs{' ' * pad}>" + "<a>&e;</a>" * 20 + "</Diggs>"
        )
        doc = _xml.parse(str(path))
        size = run._expanded_size(doc, 10**9, run._namespaces(doc)[1], path.read_bytes())
        return path, size, path.stat().st_size

    for length in (100, 101):  # one of the two parities can hit 2x exactly
        pad = 0
        path, size, raw = build(pad, length)
        while 2 * raw < size:
            pad += 1
            path, size, raw = build(pad, length)
        if 2 * raw == size:
            break
    assert 2 * raw == size
    assert "structure.entity-expansion" not in {f.rule_id for f in validate(path).findings}
    path, size, raw = build(pad - 1, length)  # one raw byte fewer: now over 2x
    assert [f.rule_id for f in validate(path).findings] == ["structure.entity-expansion"]


def test_limit_comparisons_allow_the_limit_itself(tmp_path, monkeypatch):
    from pydiggs import dictionary, semantic
    from pydiggs.dictionary import _xs_integer

    assert _xs_integer("9" * 18) == int("9" * 18)
    assert _xs_integer("9" * 19) is None
    monkeypatch.setattr(semantic, "MAX_DATABLOCK_PAIRS", 3)
    root = etree.fromstring(
        "<Diggs>"
        + "<measurement>" * 3
        + "<dataBlock>1</dataBlock>"
        + "</measurement>" * 3
        + "</Diggs>"
    )
    assert not semantic.validate_context_tree(root, legacy_paths=False).limits_hit  # 3 pairs
    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{_PC}</Property></properties></PropertyParameters>'
        "</parameters><dataValues>" + "ok " * 10 + "</dataValues></ResultSet>"
    )
    site = _definition(tmp_path, inner, body, {"c.xml": codes})
    work = _check13_work(monkeypatch, site)
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work)  # exactly the work needed
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work - 1)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "incomplete"


def test_strip_cache_charges_and_ancestor_sets_reuse_their_parents():
    from pydiggs.dictionary import DictionarySemanticValidator

    semantic = DictionarySemanticValidator(bounded=True)
    before = semantic._work
    semantic._stripped("a[x]/b")
    assert semantic._work > before
    root = etree.fromstring("<r><a><b><a><x/></a></b></a></r>")
    inner_a = root[0][0][0]
    x = inner_a[0]
    # x's ancestors are r, a, b, a: the inner a adds nothing new, so its set is b's.
    assert semantic._ancestor_names(x) is semantic._ancestor_names(inner_a)


# --- round 10: UX findings (U-01, U-03, U-04, U-07) ------------------------------------


def test_a_non_diggs_file_is_reported_as_not_diggs(tmp_path, capsys):
    import json

    path = tmp_path / "notes.xml"
    path.write_text("<notes><item>meeting</item></notes>")
    report = validate(path)
    assert (report.status, report.verdict, report.diggs_version) == ("not-diggs", "not-diggs", None)
    assert report.steps["structure"] == "failed"
    assert {v for s, v in report.steps.items() if s != Step.STRUCTURE} == {"skipped"}
    (finding,) = report.findings
    assert finding.rule_id == "structure.diggs-root"
    assert "<notes>, not <Diggs>" in finding.message
    entry = json.loads(report.to_json())["files"][0]
    assert (entry["status"], entry["verdict"], entry["diggs_version"]) == (
        "not-diggs",
        "not-diggs",
        None,
    )
    artifact = report.to_sarif()["runs"][0]["artifacts"][0]
    assert artifact["properties"] == {"status": "not-diggs", "verdict": "not-diggs"}
    assert _cli.main([str(path)]) == 1
    assert ": not-diggs: 1 error(s)" in capsys.readouterr().out


def test_an_offline_only_dictionary_step_is_partial(tmp_path):
    """U-04: a code list that could not be fetched offline leaves the step partial."""
    path = tmp_path / "offline.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3"><role codeSpace='
        '"https://diggsml.org/def/codes/DIGGS/0.1/status.xml#active">active</role></Diggs>'
    )
    report = validate(path, checks=["dictionary"])
    assert [f.rule_id for f in report.findings] == ["dictionary.offline"]
    assert report.steps["dictionary"] == "partial"
    assert report.ok and report.verdict == "partly-checked"


def test_partial_never_hides_a_failure(tmp_path):
    """precedence: incomplete > failed > partial > passed."""
    from pydiggs.findings.model import Finding, Report, Severity

    def make(status: str, severity: Severity | None) -> Report:
        findings = (
            (Finding(step=Step.DICTIONARY, rule_id="dictionary.5", severity=severity, message="m"),)
            if severity
            else ()
        )
        return Report(
            path="a.xml",
            sha256="0" * 64,
            diggs_version="2.6",
            steps={Step.DICTIONARY: status},
            findings=findings,
            tool_version="x",
        )

    assert make("passed", None).verdict == "valid"
    assert make("passed", Severity.WARNING).verdict == "valid-with-warnings"
    assert make("partial", Severity.WARNING).verdict == "partly-checked"
    assert make("partial", None).verdict == "partly-checked"
    assert make("failed", Severity.ERROR).verdict == "not-valid"
    assert make("incomplete", Severity.ERROR).verdict == "not-valid"
    # Warnings cut by max_findings: incomplete with no error is partly checked.
    assert make("incomplete", Severity.WARNING).verdict == "partly-checked"
    assert make("incomplete", None).verdict == "partly-checked"


def test_a_not_diggs_report_has_no_version():
    from pydiggs.findings.model import Report

    with pytest.raises(ValueError, match="diggs_version"):
        Report(
            path="a.xml",
            sha256="0" * 64,
            diggs_version="2.6",
            steps={},
            tool_version="x",
            status="not-diggs",
        )


@pytest.mark.parametrize("status", ["unreadable", "Checked", "", "valid"])
def test_a_report_refuses_an_unknown_file_status(status):
    from pydiggs.findings.model import Report

    with pytest.raises(ValueError, match=repr(status)):
        Report(
            path="a.xml",
            sha256="0" * 64,
            diggs_version="2.6",
            steps={},
            tool_version="x",
            status=status,  # type: ignore[arg-type]
        )


def test_unreadable_entries_carry_their_verdict():
    from pydiggs.findings import _json
    from pydiggs.findings.model import UnreadableFile

    entry = _json.unreadable_entry(UnreadableFile(path="x.xml", error="gone"))
    assert (entry["status"], entry["verdict"]) == ("unreadable", "unreadable")


def test_srs_on_the_child_position_is_one_best_practice_warning():
    """U-03: GML 3.2 allows srsName/srsDimension on gml:pos; the official module warns once."""
    path = ROOT / "tests" / "fixtures" / "official" / "2.6" / "DocumentationExample.xml"
    report = validate(path, checks=["geometry"])
    srs = [f for f in report.findings if f.rule_id == "geometry.srs-attributes"]
    assert len(srs) == 31  # one per geometry object; 62 (two per object) before
    assert all("on a child element" in f.message for f in srs)
    from pydiggs.semantic import validate_context

    legacy = [m for m in validate_context(str(path)).messages if m.check == 101]
    assert len(legacy) == 62  # context_check() keeps its per-attribute warnings


def test_srs_on_an_ancestor_passes():
    from pydiggs import semantic

    root = etree.fromstring(
        f'<Diggs xmlns="{NS}" xmlns:gml="http://www.opengis.net/gml/3.2">'
        '<x srsName="urn:ogc:def:crs:EPSG::4326" srsDimension="2"><PointLocation>'
        "<gml:pos>1 2</gml:pos></PointLocation></x><PointLocation><gml:pos>1 2</gml:pos>"
        "</PointLocation></Diggs>"
    )
    result = semantic.validate_context_tree(root, legacy_paths=False)
    srs = [m.text for m in result.messages if m.check == 101]
    assert srs == ["Geometry: <PointLocation> should declare srsName and srsDimension."]


# --- round 11 ------------------------------------------------------------------------


@pytest.mark.timeout(30)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_external_dtd_is_never_opened(tmp_path, monkeypatch):
    """The ATTLIST-defaults parse must not read the external subset (it would block here)."""
    os.mkfifo(tmp_path / "pipe.dtd")
    monkeypatch.chdir(tmp_path)  # the second parse has no base URL: relative ids meet cwd
    path = tmp_path / "fifo.xml"
    path.write_text(
        f'<!DOCTYPE Diggs SYSTEM "pipe.dtd" [<!ATTLIST a x CDATA "v">]><Diggs xmlns="{NS}"><a/></Diggs>'
    )
    report = validate(path, checks=["structure"])
    assert "structure.entity-expansion" not in {f.rule_id for f in report.findings}


def test_an_external_dtd_contributes_nothing(tmp_path):
    """Same result whether the external subset exists or not: no existence oracle."""
    path = tmp_path / "ext.xml"
    path.write_text(
        '<!DOCTYPE Diggs SYSTEM "ext.dtd" [<!ATTLIST b y CDATA "z">]><Diggs>'
        + '<a xmlns=""/>' * 20
        + "</Diggs>"
    )
    without = validate(path, checks=["structure"])
    (tmp_path / "ext.dtd").write_text(f'<!ATTLIST a x CDATA "{"v" * 5_000}">')
    with_file = validate(path, checks=["structure"])
    assert [f.rule_id for f in with_file.findings] == [f.rule_id for f in without.findings]
    assert "structure.entity-expansion" not in {f.rule_id for f in with_file.findings}
    measured = _xml.parse_bytes(path.read_bytes(), str(path), attribute_defaults=True)
    assert all("x" not in a.attrib for a in measured.getroot())  # never filled from outside


def _srs_reference(root: etree._Element) -> list[tuple[etree._Element, str]]:
    """The official module's rule written as it reads (per-object ancestor and subtree
    walks), kept to compare the one-pass walk with."""
    from pydiggs import semantic

    out = []
    for el in root.iter(*(f"{{*}}{n}" for n in sorted(semantic._GEOMETRY_NAMES))):
        if semantic._has_both(el) or any(semantic._has_both(a) for a in el.iterancestors()):
            continue
        child = any(semantic._has_both(d) for d in el.iterdescendants())
        out.append((el, semantic._srs_text(el, etree.QName(el).localname, child)))
    return out


def test_one_pass_srs_rule_matches_the_per_object_rule():
    import random

    from pydiggs import semantic

    def compare(root: etree._Element) -> int:
        result = semantic.ContextValidationResult(legacy_paths=False)
        semantic._check_geometry_srs_official(root, result)
        got = [(m.node, m.text) for m in result.messages]
        assert got == _srs_reference(root)
        return len(got)

    compared = 0
    for name in sorted((ROOT / "tests").rglob("*.xml")):
        try:
            compared += compare(_xml.parse(str(name)).getroot())
        except etree.XMLSyntaxError:
            continue
    names = ["Point", "PointLocation", "LinearExtent", "MultiCurve", "Solid", "pos", "posList", "x"]
    for seed in range(400):
        rng = random.Random(seed)  # noqa: S311 - test data

        def element(depth: int, rng: random.Random = rng) -> etree._Element:
            el = etree.Element(f"{{urn:g}}{rng.choice(names)}" if depth else "Diggs")
            for attr in ("srsName", "srsDimension"):
                if rng.random() < 0.3:
                    el.set(attr, "1")
            if depth < 6:
                for _ in range(rng.randint(0, 3)):
                    el.append(element(depth + 1, rng))
            return el

        compared += compare(element(0))
    assert compared > 100


def test_check13_characters_scanned_are_capped(tmp_path, monkeypatch):
    from pydiggs import dictionary

    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{_PC}</Property></properties></PropertyParameters>'
        "</parameters><dataValues>" + "ok " * 100 + "</dataValues></ResultSet>"
    )
    site = _definition(tmp_path, inner, body, {"c.xml": codes})
    work = _check13_work(monkeypatch, site)
    (tmp_path / "pad").mkdir()
    (tmp_path / "commas").mkdir()
    # Separators count: the same text with 10,000 more spaces costs 10,000 more units.
    padded = _definition(
        tmp_path / "pad",
        inner,
        body.replace("</dataValues>", " " * 10_000 + "</dataValues>"),
        {"c.xml": codes},
    )
    assert _check13_work(monkeypatch, padded) == work + 10_000
    commas = _definition(
        tmp_path / "commas",
        inner,
        body.replace("<dataValues>", '<dataValues ts=",">').replace("ok " * 100, "," * 10_000),
        {"c.xml": codes},
    )
    assert _check13_work(monkeypatch, commas) >= 10_000  # separator-only text is charged
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "passed"
    monkeypatch.setattr(dictionary, "MAX_CHECK13_WORK", work - 1)
    assert validate(site, checks=["dictionary"]).steps["dictionary"] == "incomplete"


# --- geometry: the official element list (human decision) ----------------------------

_GML = "http://www.opengis.net/gml/3.2"


def _srs(body: str) -> list[tuple[str, str]]:
    from pydiggs import semantic

    root = etree.fromstring(f'<Diggs xmlns="{NS}" xmlns:gml="{_GML}">{body}</Diggs>')
    result = semantic.validate_context_tree(root, legacy_paths=False)
    return [(m.severity, m.text) for m in result.messages if m.check == 101]


def test_geometry_list_is_the_official_modules():
    from pydiggs import semantic

    assert semantic._GEOMETRY_NAMES == (
        "LinearExtent",
        "MultiPointLocation",
        "MultiCurve",
        "Solid",
        "PointLocation",
        "PlanarObservationRepresentation",
        "PlanarSurface",
        "MultiPlanarSurface",
        "RectifiedGrid",
    )


def test_a_point_without_srs_is_not_a_checked_object():
    assert _srs("<gml:Point><gml:pos>1 2</gml:pos></gml:Point>") == []


@pytest.mark.parametrize("name", ["Solid", "PlanarSurface"])
def test_an_object_with_coordinates_and_no_srs_warns(name):
    assert _srs(f"<{name}><gml:posList>1 2 3 4</gml:posList></{name}>") == [
        ("WARNING", f"Geometry: <{name}> should declare srsName and srsDimension.")
    ]


def test_an_empty_linear_extent_warns():
    """No coordinates pre-filter any more: the official module checks it too."""
    assert _srs('<LinearExtent gml:id="l"/>') == [
        ("WARNING", "Geometry: <LinearExtent> should declare srsName and srsDimension.")
    ]


def test_a_point_location_with_only_srs_dimension_names_srs_name():
    assert _srs('<PointLocation srsDimension="2"/>') == [
        ("WARNING", "Geometry: <PointLocation> should declare srsName.")
    ]


def test_an_empty_srs_name_counts_as_present():
    """The module tests exists(@srsName): an empty value is not missing."""
    assert _srs('<PointLocation srsName="" srsDimension="2"/>') == []
    assert _srs('<PointLocation srsName=""/>') == [
        ("WARNING", "Geometry: <PointLocation> should declare srsDimension.")
    ]


def test_srs_on_a_child_names_only_what_the_object_lacks():
    got = _srs(
        '<MultiCurve srsName="urn:x"><gml:pos srsName="urn:x" srsDimension="2">1 2</gml:pos>'
        "</MultiCurve>"
    )
    assert got == [
        (
            "WARNING",
            "Geometry: <MultiCurve> has no srsDimension itself; srsName and srsDimension are"
            " on a child element (allowed by GML 3.2), but DIGGS best practice is to put"
            " them on the geometry object.",
        )
    ]


def test_legacy_context_check_keeps_its_own_list():
    from pydiggs import semantic

    root = etree.fromstring(
        f'<Diggs xmlns="{NS}" xmlns:gml="{_GML}"><gml:Point><gml:pos>1 2</gml:pos></gml:Point>'
        '<LinearExtent gml:id="l"/></Diggs>'
    )
    legacy = semantic.validate_context_tree(root)
    assert [m.text for m in legacy.messages if m.check == 101] == [
        "Geometry: <Point> should declare srsName.",
        "Geometry: <Point> should declare srsDimension.",
    ]


# --- round 11: partial precedence end to end ------------------------------------------

_OFFLINE_ROLE = (
    '<role xmlns="{ns}" codeSpace='
    '"https://diggsml.org/def/codes/DIGGS/0.1/status.xml#active">active</role>'
)


def _with_offline_role(tmp_path, source: Path) -> Path:
    text = source.read_text(encoding="utf-8")
    ns = etree.QName(etree.fromstring(text.encode()).tag).namespace
    end = text.rindex("</")
    path = tmp_path / source.name
    path.write_text(text[:end] + _OFFLINE_ROLE.format(ns=ns) + text[end:], encoding="utf-8")
    return path


def test_offline_with_a_dictionary_error_is_failed_not_partial(tmp_path):
    path = _with_offline_role(tmp_path, ROOT / "tests" / "test_files" / "AtterbergExample.xml")
    report = validate(path, checks=["dictionary"])
    rules_found = {f.rule_id for f in report.findings}
    assert {"dictionary.offline", "dictionary.8"} <= rules_found
    assert report.steps["dictionary"] == "failed"
    assert report.verdict == "not-valid"


def test_offline_with_truncation_is_incomplete_not_partial(tmp_path):
    path = tmp_path / "offline.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        + "".join(
            f'<role codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/status{i}.xml#a">a</role>'
            for i in range(3)
        )
        + "</Diggs>"
    )
    report = validate(path, checks=["dictionary"], max_findings=1)
    rules_found = [f.rule_id for f in report.findings]
    assert "structure.findings-truncated" in rules_found
    assert report.steps["dictionary"] == "incomplete"
    assert report.verdict == "partly-checked"


def test_cli_text_says_why_a_file_is_partly_checked(tmp_path, capsys):
    from pydiggs.findings import _cli

    path = tmp_path / "offline.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3"><role codeSpace='
        '"https://diggsml.org/def/codes/DIGGS/0.1/status.xml#active">active</role></Diggs>'
    )
    assert _cli.main([str(path), "--checks", "dictionary"]) == 0
    out = capsys.readouterr().out
    assert ": partly-checked: 0 error(s), 0 warning(s), 1 info" in out
    assert ": not fully checked: dictionary (partial)\n" in out
    assert " info dictionary.offline: " in out  # shown though INFO is otherwise hidden


def test_cli_text_hides_info_on_a_fully_checked_file(tmp_path, capsys):
    from pydiggs.findings import _cli

    path = tmp_path / "ok.xml"
    path.write_text('<Diggs xmlns="http://diggsml.org/schemas/3"/>')
    _cli.main([str(path), "--checks", "structure"])
    out = capsys.readouterr().out
    assert "not fully checked" not in out


@pytest.mark.timeout(30)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_external_dtd_by_absolute_path_is_never_opened(tmp_path):
    fifo = tmp_path / "abs.dtd"
    os.mkfifo(fifo)
    path = tmp_path / "sub" / "fifo.xml"
    path.parent.mkdir()
    path.write_text(
        f'<!DOCTYPE Diggs SYSTEM "{fifo.as_posix()}" [<!ATTLIST a x CDATA "v">]>'
        f'<Diggs xmlns="{NS}"><a/></Diggs>'
    )
    report = validate(path, checks=["structure"])
    assert "structure.entity-expansion" not in {f.rule_id for f in report.findings}


def test_a_file_uri_external_dtd_contributes_nothing(tmp_path):
    """Neither its ATTLIST defaults nor its entities are read, by file: URI either."""
    dtd = tmp_path / "far" / "ext.dtd"
    dtd.parent.mkdir()
    dtd.write_text(f'<!ATTLIST a x CDATA "{"v" * 5_000}"><!ENTITY big "{"w" * 5_000}">')
    path = tmp_path / "ext.xml"
    path.write_text(
        f'<!DOCTYPE Diggs SYSTEM "{dtd.as_uri()}" [<!ATTLIST b y CDATA "z">]><Diggs>'
        + '<a xmlns=""/>' * 20
        + "</Diggs>"
    )
    report = validate(path, checks=["structure"])
    assert "structure.entity-expansion" not in {f.rule_id for f in report.findings}
    measured = _xml.parse_bytes(path.read_bytes(), str(path), attribute_defaults=True)
    assert all("x" not in a.attrib for a in measured.getroot())
    uses = path.read_text().replace('<a xmlns=""/>', '<a xmlns="">&big;</a>')
    with pytest.raises(etree.XMLSyntaxError, match="'big' not defined"):  # never loaded
        _xml.parse_bytes(uses.encode(), str(path), attribute_defaults=True)


def test_a_relative_external_dtd_is_not_read_from_the_working_directory(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "rel.dtd").write_text(f'<!ATTLIST a x CDATA "{"v" * 5_000}">')
    monkeypatch.chdir(cwd)
    data = (
        b'<!DOCTYPE Diggs SYSTEM "rel.dtd" [<!ATTLIST b y CDATA "z">]><Diggs>'
        + b'<a xmlns=""/>' * 20
        + b"</Diggs>"
    )
    for base_url in (None, str(tmp_path / "elsewhere" / "i.xml")):
        measured = _xml.parse_bytes(data, base_url, attribute_defaults=True)
        assert all("x" not in a.attrib for a in measured.getroot())


def test_check13_index_charges_every_loop():
    """The invariant at _Check13Index: each iteration is charged to the one counter."""
    from pydiggs import dictionary

    step = dictionary._CHECK13_STEP
    root = etree.fromstring(
        f'<Diggs xmlns="{NS}" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<ResultSet><parameters xlink:href="#B" other="x"/><x><y>'
        '<PropertyParameters gml:id="A" xmlns:gml="http://www.opengis.net/gml/3.2">'
        '<Property index="1"><c/></Property><Property index="no"/><Property index="3"/>'
        "</PropertyParameters></y></x></ResultSet></Diggs>"
    )
    index = dictionary._Check13Index(root.getroottree(), bounded=True)
    layout = index.layout()
    # 3 elements x (start + end) and the two attributes of ResultSet/parameters.
    assert index._work == step * (3 * 2 + 2)
    params = root.find(".//{*}PropertyParameters")
    assert layout.nearest_rs[params] is root[0]  # at any depth, as the driver reads it
    assert layout.refs[root[0]] == ["#B"]
    before = index._work
    assert index.group_columns(params) == frozenset({1, 3})
    assert index._work - before == 3 * step  # each Property examined
    before = index._work
    leaf = root.find(".//{*}c")
    assert index.nearest(leaf, "PropertyParameters") is params
    assert index._work - before == 2 * step  # Property, then PropertyParameters
    before = index._work
    assert index.nearest(leaf, "nothing") is None
    assert index._work - before == 6 * step  # every ancestor up to the root


def test_check13_union_work_is_charged_and_shared():
    from pydiggs import dictionary

    step = dictionary._CHECK13_STEP
    props = "".join(f'<Property index="{i}"/>' for i in range(1, 101))
    root = etree.fromstring(
        f'<Diggs xmlns="{NS}" xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:xlink="http://www.w3.org/1999/xlink">'
        f'<ResultSet><parameters><PropertyParameters gml:id="B">{props}'
        "</PropertyParameters></parameters></ResultSet>"
        + '<ResultSet><parameters xlink:href="#B"><PropertyParameters/></parameters></ResultSet>'
        * 3
        + "</Diggs>"
    )
    index = dictionary._Check13Index(root.getroottree(), bounded=True)
    sets = root.findall("{*}ResultSet")
    index.layout()
    first = index._columns_for(sets[1])
    before = index._work
    second = index._columns_for(sets[2])
    assert second is first  # one union for every ResultSet citing the same groups
    # href, cited group, groups (own empty one + B, +1): no union rebuilt.
    assert index._work - before == step * (1 + 1 + 3)
    assert index._columns_for(sets[0]) is first  # B's own ResultSet shares it too


def test_check13_properties_and_result_sets_add_not_multiply(tmp_path, monkeypatch):
    """Each property's values are merged once over its group's ResultSets, so doubling
    the properties or the citing ResultSets adds work; it does not multiply it (the
    security review's properties x ResultSets shape took 60 s on CI before)."""
    from pydiggs import dictionary

    codes = (
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/2.6" gml:id="c"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="ok"/></gml:dictionaryEntry></gml:Dictionary>'
    )
    inner = "<gml:name>t</gml:name>" + _SRC + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"

    def work(properties: int, result_sets: int, name: str) -> int:
        props = "".join(f'<Property index="{i + 1}">{_PC}</Property>' for i in range(properties))
        citing = (
            '<ResultSet><parameters xmlns:xlink="http://www.w3.org/1999/xlink"'
            ' xlink:href="#PP"/><dataValues>ok</dataValues></ResultSet>'
        ) * result_sets
        body = (
            '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
            f"{props}</properties></PropertyParameters></parameters></ResultSet>{citing}"
        )
        (tmp_path / name).mkdir()
        return _check13_work(
            monkeypatch, _definition(tmp_path / name, inner, body, {"c.xml": codes})
        )

    base = work(300, 300, "a")
    more_properties = work(600, 300, "b") - base
    more_result_sets = work(300, 600, "c") - base
    step = dictionary._CHECK13_STEP
    # A product would add at least 300 x 300 steps; a few dozen per property is linear.
    assert more_properties <= 300 * 40 * step, more_properties
    assert more_result_sets <= 300 * 40 * step, more_result_sets
