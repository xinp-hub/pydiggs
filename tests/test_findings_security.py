"""Security regressions for validate() and pydiggs validate (verify-stage audit)."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
from lxml import etree

from pydiggs import InstanceReadError, ResourceError, validate
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


def _leak_inputs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    import gzip

    folder = tmp_path / "secret-folder"
    folder.mkdir()
    packed = folder / "packed.xml"
    packed.write_bytes(gzip.compress(NO_ERROR.read_bytes()))
    big = folder / "big.xml"
    big.write_bytes(NO_ERROR.read_bytes())
    assert big.stat().st_size > packed.stat().st_size + 1
    return packed, big


def _limit(packed: Path) -> int:
    return packed.stat().st_size + 1


@pytest.mark.parametrize("fmt", ["json", "sarif", "text"])
def test_unreadable_reasons_carry_no_absolute_path(tmp_path, monkeypatch, capsys, fmt):
    packed, big = _leak_inputs(tmp_path, monkeypatch)
    monkeypatch.chdir(ROOT)
    code = main(
        ["validate", "--format", fmt, "--max-bytes", str(_limit(packed)), str(packed), str(big)]
    )
    out, err = capsys.readouterr()
    assert code == 2
    assert "secret-folder" not in out + err
    assert str(tmp_path) not in out + err
    assert "compressed XML is not accepted" in out
    assert "XML file exceeds" in out
    assert "could not read packed.xml: cannot read" not in out


def test_instance_read_error_names_the_shown_path_only(tmp_path, monkeypatch):
    packed, _ = _leak_inputs(tmp_path, monkeypatch)
    monkeypatch.chdir(ROOT)
    with pytest.raises(InstanceReadError) as exc:
        validate(packed, max_bytes=_limit(packed))
    assert exc.value.strerror == "compressed XML is not accepted"
    assert exc.value.filename == "packed.xml"
    assert "secret-folder" not in str(exc.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifos_are_skipped_and_refused_without_blocking(tmp_path, capsys):
    from pydiggs.findings._cli import expand_inputs

    fifo = tmp_path / "pipe.xml"
    os.mkfifo(fifo)
    (tmp_path / "real.xml").write_bytes(NO_ERROR.read_bytes())
    assert [p.name for p in expand_inputs([str(tmp_path)])] == ["real.xml"]
    with pytest.raises(InstanceReadError, match="not a regular file"):
        validate(fifo)
    assert main(["validate", "--format", "json", str(fifo)]) == 2
    assert '"not a regular file"' in capsys.readouterr().out


def test_output_file_is_private(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["validate", "--format", "json", "--output", "out.json", str(NO_ERROR)]) == 0
    assert stat.S_IMODE((tmp_path / "out.json").stat().st_mode) == 0o600


def test_text_output_escapes_control_and_bidi_characters(tmp_path, monkeypatch, capsys):
    from pydiggs.findings._cli import _safe

    assert _safe("a\x1b[31mb\u202ec\td\x85") == "a\\x1b[31mb\\u202ec\td\\x85"
    nasty = tmp_path / "evil\u202etxt.xml"
    nasty.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/2.6"><x codeSpace="https://example.org/\x1b[2J">'
        "v</x></Diggs>",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    main(["validate", "--checks", "dictionary", nasty.name])
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\u202e" not in out
    assert "evil\\u202etxt.xml" in out


def test_non_utf8_file_names_are_reported_without_traceback(tmp_path, monkeypatch, capsys):
    from pydiggs.findings.run import display_paths

    raw = os.fsdecode(b"bad\xff.xml")
    monkeypatch.chdir(tmp_path)
    assert display_paths(raw)[0] == "bad\\xff.xml"
    for fmt in ("json", "sarif", "text"):
        assert main(["validate", "--format", fmt, raw]) == 2
        out = capsys.readouterr().out
        assert "bad\\\\xff.xml" in out or "bad\\xff.xml" in out or "bad%5Cxff.xml" in out


@pytest.mark.parametrize(
    ("path", "uri"),
    [
        ("C:/Users/me/site/BH 01.xml", "file:///C:/Users/me/site/BH%2001.xml"),
        ("d:/x.xml", "file:///d:/x.xml"),
        ("//server/share/a.xml", "file://server/share/a.xml"),
        ("/home/me/a.xml", "file:///home/me/a.xml"),
    ],
)
def test_sarif_file_uris_for_posix_and_windows_paths(path, uri):
    from pydiggs.findings import _sarif
    from pydiggs.findings.model import Report

    assert _sarif.file_uri(path) == uri
    report = Report(
        path="a.xml", absolute_path=path, sha256="a" * 64, diggs_version=None, steps={},
        tool_version="1",
    )  # fmt: skip
    log = report.to_sarif(absolute_paths=True)
    assert log["runs"][0]["artifacts"][0]["location"] == {"uri": uri}


def test_snippet_cost_does_not_scale_with_the_subtree():
    from pydiggs.findings.locate import SNIPPET_CHILDREN, SNIPPET_LIMIT

    root = etree.fromstring(
        b"<a xmlns:unused='urn:u' k='v'>" + b"<b><c>deep</c></b>" * 20_000 + b"</a>"
    )
    start = time.perf_counter()
    snippet = Locator(etree.ElementTree(root)).snippet(root)
    assert time.perf_counter() - start < 0.05
    assert len(snippet.encode()) <= SNIPPET_LIMIT
    assert snippet.count("<b>") == SNIPPET_CHILDREN
    assert "<b>...</b>" in snippet
    assert f"<!-- {20_000 - SNIPPET_CHILDREN} more -->" in snippet
    assert "urn:u" not in snippet


def test_existing_output_file_keeps_its_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out.json"
    out.write_text("old")
    out.chmod(0o644)
    assert main(["validate", "--format", "json", "--output", "out.json", str(NO_ERROR)]) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o644
    assert out.read_text().startswith("{")


def test_line_and_paragraph_separators_are_escaped():
    from pydiggs.findings._cli import _safe

    assert _safe("a\u2028b\u2029c") == "a\\u2028b\\u2029c"


@pytest.mark.parametrize(
    ("path", "uri"),
    [
        ("//?/C:/Users/me/a b.xml", "file:///C:/Users/me/a%20b.xml"),
        ("//?/UNC/server/share/a.xml", "file://server/share/a.xml"),
    ],
)
def test_sarif_file_uris_for_extended_length_windows_paths(path, uri):
    from pydiggs.findings import _sarif

    assert _sarif.file_uri(path) == uri
