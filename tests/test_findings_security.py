"""Security regressions for validate() and pydiggs validate (verify-stage audit)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from lxml import etree

from pydiggs import ResourceError, validate
from pydiggs.cli import main
from pydiggs.findings.locate import Locator

ROOT = Path(__file__).resolve().parent.parent
# A namespace URI is instance data. It must never reach an XPath evaluator: lxml's
# Schematron skeleton copies it verbatim into SVRL @location.
INJECTED_NS = "urn:a'or(count(//*/following::*/following::*)=0)or'a'='b"


def _injected(tmp_path: Path, pad: int = 900) -> Path:
    path = tmp_path / "inj.xml"
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<Diggs xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2">\n'
        f' <x:w xmlns:x="{INJECTED_NS}">{"<p/>" * pad}\n'
        '  <samplingFeature><Borehole gml:id="BH-1">'
        '<totalMeasuredDepth uom="ft">-1</totalMeasuredDepth></Borehole></samplingFeature>\n'
        " </x:w>\n</Diggs>\n",
        encoding="utf-8",
    )
    return path


def test_svrl_location_with_injected_namespace_is_matched_not_evaluated(tmp_path):
    path = _injected(tmp_path)
    assert 3_000 < path.stat().st_size < 5_000
    validate(path, checks=["schematron"])  # compile the bundled rules once
    start = time.perf_counter()
    report = validate(path, checks=["schematron"])
    assert time.perf_counter() - start < 1.0
    (finding,) = report.findings
    assert finding.gml_id == "BH-1"
    assert finding.xpath == "/Diggs[1]/x:w[1]/samplingFeature[1]/Borehole[1]/totalMeasuredDepth[1]"


def test_svrl_location_grammar_rejects_anything_else():
    doc = etree.ElementTree(etree.fromstring(b'<a xmlns="urn:x"><b/><b/></a>'))
    loc = Locator(doc)
    good = "/*[local-name()='a' and namespace-uri()='urn:x']/*[local-name()='b' and namespace-uri()='urn:x'][2]"
    assert loc.svrl_element(good) is doc.getroot()[1]
    assert loc.svrl_element(good + "/@id") is doc.getroot()[1]
    for bad in (
        "//b",
        "/*[local-name()='a' and namespace-uri()='urn:x']/*[1]",
        good + "[1]",
        good + " | //*",
        "/*[local-name()='a' and namespace-uri()='urn:x']/*[local-name()='b' and namespace-uri()='urn:x']",
        "",
        None,
    ):
        assert loc.svrl_element(bad) is None, bad


def test_log_entry_paths_are_grammar_checked_before_evaluation():
    doc = etree.ElementTree(etree.fromstring(b"<a><b/><c:d xmlns:c='u'/></a>"))
    loc = Locator(doc)
    assert loc.element_at("/*/*[1]") is doc.getroot()[0]
    assert loc.element_at("/a/b") is doc.getroot()[0]
    for bad in ("/a/b[count(//*)>0]", "//b", "/a/b | /a", "/a/b[0]", "", None):
        assert loc.element_at(bad) is None, bad


BAD_SCH = ROOT / "tests" / "fixtures" / "findings" / "bad_schematron"
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"


@pytest.mark.parametrize(
    ("name", "match"),
    [
        ("unparseable_test.sch", "cannot use Schematron rules"),
        ("unknown_function.sch", "cannot apply Schematron rules"),
    ],
)
def test_broken_user_schematron_is_a_resource_error(name, match, capsys):
    with pytest.raises(ResourceError, match=match):
        validate(NO_ERROR, checks=["schematron"], schematron_path=BAD_SCH / name)
    code = main(["validate", str(NO_ERROR), "--schematron-path", str(BAD_SCH / name)])
    assert code == 2
    assert "Schematron rules" in capsys.readouterr().err
