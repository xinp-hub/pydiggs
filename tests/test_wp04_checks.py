"""WP-0.4: dictionary Check 13 and the deprecation check, in validate() only."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from pydiggs import Severity, Step, deprecations, validate, validator
from pydiggs.findings import rules

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "findings"
CHECK13 = FIXTURES / "check13_value_code_space.xml"
DEPRECATION = FIXTURES / "deprecation_3_0.xml"


def test_check13_reports_values_outside_the_code_list():
    report = validate(CHECK13, checks=["dictionary"])
    (finding,) = [f for f in report.findings if f.rule_id == rules.DICTIONARY_13]
    assert finding.severity is Severity.ERROR
    assert finding.message == (
        '2 value(s) of "Loading Phase" are not terms of the code list '
        '"https://diggsml.org/def/codes/DIGGS/0.1/loadTestIncrementPhase.xml": "bogus", "oops". '
        "Valid terms: loading, unloading, reload, seating."
    )
    assert finding.gml_id == "P1"
    assert finding.xpath.endswith("/Property[1]/propertyClass[1]")
    assert report.steps[Step.DICTIONARY] == "failed"


def test_check13_is_not_part_of_the_legacy_dictionary_check():
    legacy = validator(str(CHECK13), output_log=False)
    assert legacy.dictionary_check() is True
    assert not any("Check 13" in line for line in legacy.dictionary_validation_log or [])


def _check13_variant(tmp_path, old, new):
    path = tmp_path / "v.xml"
    path.write_text(CHECK13.read_text("utf-8").replace(old, new), "utf-8")
    report = validate(path, checks=["dictionary"])
    return [f for f in report.findings if f.rule_id == rules.DICTIONARY_13]


def test_check13_passes_valid_values_and_handles_missing_index(tmp_path):
    assert _check13_variant(tmp_path, "bogus", "loading") != []  # "oops" remains
    assert _check13_variant(tmp_path, 'index="1"', 'index="x"') == []
    assert _check13_variant(tmp_path, 'index="1"', 'index="2"') == []


def test_check13_custom_separators(tmp_path):
    findings = _check13_variant(
        tmp_path,
        'cs="," ts=" ">loading unloading bogus NA\n                reload',
        'cs=";" ts="|">x;loading|y;bad',
    )
    assert len(findings) == 1
    assert '"loading", "bad"' not in findings[0].message
    assert '"bogus", "oops"' not in findings[0].message


def test_check13_unwhitelisted_and_inaccessible_code_lists(tmp_path, monkeypatch):
    from pydiggs.dictionary import DictionarySemanticValidator

    original = DictionarySemanticValidator._is_whitelisted
    monkeypatch.setattr(
        DictionarySemanticValidator,
        "_is_whitelisted",
        lambda self, url: "loadTest" not in url and original(self, url),
    )
    report = validate(CHECK13, checks=["dictionary"])
    warnings = [f for f in report.findings if f.rule_id == rules.DICTIONARY_13]
    assert any("not on the white list" in f.message for f in warnings)
    monkeypatch.undo()
    monkeypatch.setattr(
        DictionarySemanticValidator,
        "_resolve_dictionary",
        lambda self, url: None if "loadTest" in url else self._dict_cache.get(url),
    )
    report = validate(CHECK13, checks=["dictionary"])
    (finding,) = [f for f in report.findings if f.rule_id == rules.DICTIONARY_13]
    assert finding.severity is Severity.WARNING
    assert "could not be accessed" in finding.message


def test_deprecation_since_gating_on_a_3_0_file():
    report = validate(DEPRECATION, checks=["deprecation"])
    found = [(f.rule_id, f.severity, f.xpath, f.gml_id) for f in report.findings]
    assert found == [
        (
            rules.DEPRECATED,
            Severity.WARNING,
            "/Diggs[1]/samplingFeature[1]/Borehole[1]/easting[1]",
            "BH-1",
        ),
        (
            rules.DEPRECATED,
            Severity.WARNING,
            "/Diggs[1]/samplingFeature[1]/Borehole[1]/lithDescription[1]/@legendCode",
            "BH-1",
        ),
    ]
    assert report.steps[Step.DEPRECATION] == "passed"
    messages = [f.message for f in report.findings]
    assert messages[0] == (
        "Element diggs:easting is deprecated since 3.0.0; use a geometry object in the "
        "location property."
    )


def test_registry_3_1_0_entries_never_fire_on_older_versions():
    _, entries = deprecations.load_registry()
    later = [e for e in entries if e.applies_from == "3.1.0"]
    assert later, "the bundled registry has 3.1.0 entries"
    for version in ("3.0.0", "2.6", "2.5.a"):
        assert not any(deprecations.applies(e, version) for e in later)
    doc = etree.parse(str(DEPRECATION))
    at_31 = {m.entry.key for m in deprecations.matches(doc, "3.1.0")}
    assert "element:diggs:MonitorResult" in at_31
    assert "element:diggs:originalProjectRef" in at_31


def test_removed_entries_are_errors_with_the_official_text(monkeypatch):
    doc = etree.parse(str(DEPRECATION))
    removed = next(m for m in deprecations.matches(doc, "3.1.0") if m.entry.status == "removed")
    assert removed.entry.text() == (
        "Element diggs:MonitorResult was removed in 3.1.0; use diggs:TemporalResult."
    )
    monkeypatch.setattr(deprecations, "matches", lambda doc, version: [removed])
    report = validate(DEPRECATION, checks=["deprecation"])
    (finding,) = report.findings
    assert (finding.rule_id, finding.severity) == (rules.REMOVED, Severity.ERROR)
    assert report.steps[Step.DEPRECATION] == "failed"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"status": "deprecated", "replaced_by": "", "note": ""},
            "Type t is deprecated since 3.1.0.",
        ),
        (
            {"status": "deprecated", "replaced_by": "r", "note": "n", "exact": False},
            "Type t is deprecated since 3.1.0; use r (n). This match is by name only and may "
            "be a valid use of the same name; check its context.",
        ),
        (
            {"status": "removed", "replaced_by": "", "note": "Gone."},
            "Type t was removed in 3.1.0. Gone.",
        ),
    ],
)
def test_deprecation_text_variants(kwargs, expected):
    base = {
        "key": "k",
        "kind": "type",
        "name": "t",
        "host": "",
        "since": "3.1.0",
        "removed_in": "3.1.0",
        "instance_xpath": "//x",
        "exact": True,
    }
    assert deprecations.DeprecationEntry(**{**base, **kwargs}).text() == expected


def test_bad_registry_xpath_is_skipped(monkeypatch):
    namespaces, _ = deprecations.load_registry()
    broken = deprecations.DeprecationEntry(
        "k", "deprecated", "element", "x", "", "", "", "", "", "//[", True
    )
    monkeypatch.setattr(deprecations, "load_registry", lambda: (namespaces, (broken,)))
    assert deprecations.matches(etree.parse(str(DEPRECATION)), "3.0.0") == []
