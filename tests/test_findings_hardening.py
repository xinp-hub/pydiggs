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
    """One file that is both the instance and its own local dictionary (audit B1)."""
    path = tmp_path / "self.xml"
    occurrences = "".join(
        "<diggs:Occurrence><diggs:sourceElementXpath>"
        f"//diggs:n{i}/diggs:{terminal}</diggs:sourceElementXpath></diggs:Occurrence>"
        for i in range(paths)
    )
    refs = '<diggs:a codeSpace="./self.xml#t">t</diggs:a>' * elements
    path.write_text(
        '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:diggs="http://diggsml.org/schemas/3" gml:id="x"><gml:dictionaryEntry>'
        '<diggs:Definition gml:id="t"><gml:name>t</gml:name>'
        f"<diggs:occurrences>{occurrences}</diggs:occurrences></diggs:Definition>"
        f"</gml:dictionaryEntry><gml:description>{refs}</gml:description></gml:Dictionary>"
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
    path = _self_dictionary(tmp_path, elements=4_000, paths=900, terminal="a")
    report = validate(path, checks=["dictionary"], max_findings=None)
    assert report.steps["dictionary"] == "failed"  # 3.6 million steps, within the budget


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
    assert not report.ok and report.steps["dictionary"] == "incomplete"
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
