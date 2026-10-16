"""The pure findings model: Finding, Report, Severity, Step, rule ids and keys."""

from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pydiggs.findings import keys, rules
from pydiggs.findings.model import (
    RULE_ID_PATTERN,
    Finding,
    Report,
    Severity,
    Step,
    is_valid_rule_id,
)

from .strategies import findings, reports, rule_ids


def _finding(**kw):
    base = {
        "step": Step.SCHEMA,
        "rule_id": "schema.cvc-elt.1",
        "severity": Severity.ERROR,
        "message": "m",
    }
    return Finding(**{**base, **kw})


def test_severity_rank_orders_error_warning_info():
    assert [s.rank for s in (Severity.INFO, Severity.WARNING, Severity.ERROR)] == [0, 1, 2]
    assert Severity("error") is Severity.ERROR


def test_step_values_and_order_follow_the_official_pipeline():
    assert [s.value for s in Step] == [
        "structure",
        "schema",
        "codeType",
        "dictionary",
        "schematron",
        "geometry",
        "crs",
        "coordinate",
        "deprecation",
        "context",
    ]
    assert [s.order for s in Step] == list(range(len(Step)))


def test_finding_coerces_strings_to_enums():
    f = Finding(step="dictionary", rule_id="dictionary.5", severity="warning", message="")
    assert f.step is Step.DICTIONARY
    assert f.severity is Severity.WARNING
    assert f.layer == "native"


def test_finding_is_frozen_slotted_kw_only():
    f = _finding()
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.message = "x"  # type: ignore[misc]
    assert not hasattr(f, "__dict__")
    with pytest.raises(TypeError):
        Finding("native", Step.SCHEMA, "schema.x", Severity.ERROR, "m")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "exc", "match"),
    [
        ({"rule_id": "dictionary.5"}, ValueError, r"not a valid rule id for step 'schema'"),
        ({"rule_id": "schema."}, ValueError, "not a valid rule id"),
        ({"rule_id": 5}, ValueError, "not a valid rule id"),
        ({"layer": "other"}, ValueError, "layer must be one of"),
        ({"step": "nope"}, ValueError, "'nope' is not a valid Step"),
        ({"severity": "fatal"}, ValueError, "'fatal' is not a valid Severity"),
        ({"line": 0}, ValueError, "line must be a positive int"),
        ({"line": True}, ValueError, "line must be a positive int"),
        ({"column": -1}, ValueError, "column must be a positive int"),
        ({"message": None}, TypeError, "message must be str"),
        ({"xpath": 1}, TypeError, "xpath must be str or None"),
    ],
    ids=[
        "wrong-step",
        "empty-name",
        "not-str",
        "layer",
        "step",
        "severity",
        "line-zero",
        "line-bool",
        "column",
        "message",
        "xpath",
    ],
)
def test_finding_rejects_invalid_fields(kwargs, exc, match):
    with pytest.raises(exc, match=match):
        _finding(**kwargs)


@pytest.mark.parametrize(
    ("xpath", "line", "expected"),
    [
        ("/diggs:Diggs[1]/diggs:Borehole[2]", 4, "xpath:/Diggs[1]/Borehole[2]"),
        (None, 4, "line:4"),
        (None, None, "file"),
        ("", 7, "line:7"),
    ],
    ids=["xpath", "line", "file", "empty-xpath"],
)
def test_conformance_key(xpath, line, expected):
    f = _finding(xpath=xpath, line=line)
    assert f.conformance_key() == ("schema", "error", expected, "schema.cvc-elt.1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/diggs:Diggs[1]/diggs:a[3]", "/Diggs[1]/a[3]"),
        ("/Diggs/a", "/Diggs[1]/a[1]"),
        ("/Diggs[1]/a[1]/@gml:id", "/Diggs[1]/a[1]/@id"),
        ("  /Diggs[1]  ", "/Diggs[1]"),
        ("//Borehole", "//Borehole"),
        ("Diggs[1]", "Diggs[1]"),
        ("/Diggs[last()]", "/Diggs[last()]"),
    ],
    ids=["prefixes", "positions", "attribute", "whitespace", "descendant", "relative", "predicate"],
)
def test_normalise_xpath(raw, expected):
    assert keys.normalise_xpath(raw) == expected


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(st.text())
def test_normalise_xpath_is_idempotent(raw):
    once = keys.normalise_xpath(raw)
    assert keys.normalise_xpath(once) == once


def test_compare_is_a_multiset_difference_over_shared_steps():
    a = [
        ("schema", "error", "line:1", "schema.x"),
        ("schema", "error", "line:1", "schema.x"),
        ("crs", "error", "file", "crs.x"),
    ]
    b = [("schema", "error", "line:1", "schema.x"), ("dictionary", "info", "file", "dictionary.1")]
    only_a, only_b = keys.compare(a, b, ["schema", "dictionary"])
    assert only_a == Counter({("schema", "error", "line:1", "schema.x"): 1})
    assert only_b == Counter({("dictionary", "info", "file", "dictionary.1"): 1})


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(rule_ids())
def test_generated_rule_ids_match_the_grammar(rule_id):
    assert RULE_ID_PATTERN.match(rule_id)
    assert is_valid_rule_id(rule_id, rule_id.split(".", 1)[0])


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(st.text())
def test_rule_id_grammar_never_accepts_a_bad_step(text):
    if is_valid_rule_id(text):
        assert text.split(".", 1)[0] in {s.value for s in Step}
        assert re.fullmatch(r"[A-Za-z]+\.[A-Za-z0-9][A-Za-z0-9._-]*", text)


def test_every_named_rule_id_is_valid():
    named = [
        rules.WELL_FORMED,
        rules.SCHEMA_INCOMPLETE,
        rules.DICTIONARY_13,
        rules.DICTIONARY_OFFLINE,
        rules.DEPRECATED,
        rules.REMOVED,
        rules.SCHEMATRON_CASING_UNITS,
        rules.SCHEMATRON_CASING_NUMERIC,
        *rules.SCHEMATRON_BUNDLED.values(),
    ]
    named += [rule for _, rule in rules.CONTEXT_RULES.values()]
    named += [rules.dictionary_rule(n) for n in range(1, 14)]
    for rule_id in named:
        assert is_valid_rule_id(rule_id), rule_id
    for step, rule_id in rules.CONTEXT_RULES.values():
        assert is_valid_rule_id(rule_id, step)
    assert len(set(rules.SCHEMATRON_BUNDLED.values())) == len(rules.SCHEMATRON_BUNDLED)


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("SCHEMAV_CVC_COMPLEX_TYPE_2_4", "schema.cvc-complex-type.2.4"),
        ("SCHEMAV_CVC_DATATYPE_VALID_1_2_1", "schema.cvc-datatype-valid.1.2.1"),
        ("SCHEMAV_CVC_ELT_5_2_2_2_1", "schema.cvc-elt.5.2.2.2.1"),
        ("SCHEMAV_CVC_MAXLENGTH_VALID", "schema.cvc-maxLength-valid"),
        ("SCHEMAV_CVC_IDC", "schema.cvc-identity-constraint"),
        ("SCHEMAV_CVC_AU", "schema.cvc-au"),
        ("SCHEMAV_CVC_TYPE_3_1_1", "schema.cvc-type.3.1.1"),
        ("SCHEMAV_ELEMENT_CONTENT", "schema.cvc-complex-type.2.4"),
        ("SCHEMAV_DOCUMENT_ELEMENT_MISSING", "schema.libxml2.SCHEMAV_DOCUMENT_ELEMENT_MISSING"),
        ("SCHEMAV_CVC_NEWTHING_1", "schema.libxml2.SCHEMAV_CVC_NEWTHING_1"),
        (None, "schema.libxml2.UNKNOWN"),
        ("weird name!", "schema.libxml2.weird_name_"),
    ],
)
def test_schema_rule(type_name, expected):
    assert rules.schema_rule(type_name) == expected
    assert is_valid_rule_id(expected, "schema")


def test_schema_rule_covers_every_libxml2_cvc_code():
    from lxml import etree

    for name in dir(etree.ErrorTypes):
        if name.startswith("SCHEMAV_CVC_"):
            assert rules.schema_rule(name).startswith("schema.cvc-"), name


def test_schematron_user_rule_uses_id_or_a_stable_hash():
    assert rules.schematron_user_rule("depth-ok", "c", "t") == "schematron.depth-ok"
    hashed = rules.schematron_user_rule("bad id!", "c", "t")
    assert hashed == rules.schematron_user_rule(None, "c", "  t ")
    assert re.fullmatch(r"schematron\.user-[0-9a-f]{12}", hashed)
    assert hashed != rules.schematron_user_rule(None, "c", "u")


@pytest.mark.parametrize(
    ("rule_id", "expected"),
    [
        ("dictionary.13", "Result values are not terms of the property's value code list"),
        ("dictionary.99", "dictionary.99"),
        ("schema.libxml2.SCHEMAV_MISC", "XML Schema validation error SCHEMAV_MISC"),
        ("schematron.Borehole.totalMeasuredDepth", "Schematron rule Borehole.totalMeasuredDepth"),
        ("context.datablock-arity", "dataBlock tuple size does not match the Property count"),
        ("crs.whatever", "crs.whatever"),
    ],
)
def test_describe(rule_id, expected):
    assert rules.describe(rule_id) == expected


def test_report_sorts_findings_and_steps_and_counts():
    late = _finding(line=9)
    early = _finding(line=2, severity=Severity.WARNING, rule_id="schema.b")
    no_line = _finding(rule_id="schema.z")
    dictionary = Finding(step="dictionary", rule_id="dictionary.1", severity="info", message="")
    report = Report(
        path="a.xml",
        sha256="0" * 64,
        diggs_version="2.6",
        steps={Step.DICTIONARY: "passed", Step.SCHEMA: "failed"},
        findings=(dictionary, late, early, no_line),
        tool_version="1",
        reference_data={"schemas": "abc1234"},
    )
    assert report.findings == (no_line, early, late, dictionary)
    assert list(report.steps) == [Step.SCHEMA, Step.DICTIONARY]
    assert report.counts == {"error": 2, "warning": 1, "info": 1}
    assert report.ok is False
    with pytest.raises(TypeError):
        report.steps[Step.CRS] = "passed"  # type: ignore[index]


def test_report_rejects_unknown_step_status():
    with pytest.raises(ValueError, match="unknown status 'done'"):
        Report(path="a", sha256="", diggs_version=None, steps={"schema": "done"}, tool_version="1")  # type: ignore[dict-item]


def test_report_is_hashable_and_ok_when_only_warnings():
    report = Report(
        path="a",
        sha256="",
        diggs_version=None,
        steps={},
        findings=(_finding(severity="warning"),),
        tool_version="1",
    )
    assert report.ok
    assert isinstance(hash(report), int)


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(findings())
def test_sort_key_is_total_and_deterministic(finding):
    assert finding.sort_key() == dataclasses.replace(finding).sort_key()


@settings(deadline=None)  # no per-example deadline: timing varies under CI load
@given(reports())
def test_report_order_does_not_depend_on_input_order(report):
    shuffled = dataclasses.replace(report, findings=tuple(reversed(report.findings)))
    assert shuffled.findings == report.findings
