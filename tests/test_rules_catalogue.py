"""rule_catalogue() and explain_rule(): every rule id, its step, severities and meaning."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pydiggs import RuleInfo, Severity, Step, explain_rule, rule_catalogue, validate
from pydiggs.findings import rules
from pydiggs.findings.model import is_valid_rule_id

ROOT = Path(__file__).resolve().parent.parent
CORPUS = sorted(
    p for d in ("tests/fixtures/official", "tests/test_files") for p in (ROOT / d).rglob("*.xml")
)
SEVERITY_WORDS = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}


def test_catalogue_is_stable_ordered_and_serialisable():
    catalogue = rule_catalogue()
    assert catalogue is rule_catalogue()
    fixed = [r for r in catalogue if r.pattern is None]
    families = [r for r in catalogue if r.pattern is not None]
    assert catalogue == (*fixed, *families)
    assert fixed == sorted(fixed, key=lambda r: (r.step.order, r.id))
    assert len({r.id for r in catalogue}) == len(catalogue)
    for info in catalogue:
        assert info.known
        assert info.help_uri == rules.HELP_URI
        assert info.severities
        assert list(info.severities) == sorted(info.severities, key=lambda s: -s.rank)
        data = info.to_dict()
        assert json.loads(json.dumps(data)) == data
        assert list(data) == [
            "id",
            "pattern",
            "step",
            "description",
            "severities",
            "help_uri",
            "known",
        ]
        if info.pattern is None:
            assert is_valid_rule_id(info.id, info.step)
            assert explain_rule(info.id) == info


def test_the_open_families_are_patterns():
    families = {r.id: r.pattern for r in rule_catalogue() if r.pattern}
    assert list(families) == [
        "schema.cvc-<rule>",
        "schema.libxml2.<TYPE>",
        "<step>.incomplete",
        "schematron.user-<hash>",
        "schematron.<id>",
    ]
    for template, pattern in families.items():
        assert pattern.startswith("^")
        assert pattern.endswith("$")
        assert re.compile(pattern)
        del template


def test_every_named_rule_constant_is_in_the_catalogue():
    ids = {r.id for r in rule_catalogue()}
    named = [
        rules.WELL_FORMED,
        rules.DIGGS_ROOT,
        rules.MARKUP_ENTITY,
        rules.ENTITY_EXPANSION,
        rules.NAMESPACE_TOO_LONG,
        rules.FINDINGS_TRUNCATED,
        rules.TIMEOUT,
        rules.INTERNAL_ERROR,
        rules.DICTIONARY_13,
        rules.DICTIONARY_OFFLINE,
        rules.DEPRECATED,
        rules.REMOVED,
        rules.SCHEMATRON_CASING_UNITS,
        rules.SCHEMATRON_CASING_NUMERIC,
        *rules.SCHEMATRON_BUNDLED.values(),
        *(rule_id for _, rule_id in rules.CONTEXT_RULES.values()),
        *(rules.dictionary_rule(n) for n in range(1, 14)),
    ]
    assert set(named) <= ids
    assert explain_rule(rules.SCHEMA_INCOMPLETE).pattern is not None


def test_every_finding_on_the_corpus_is_explained_with_its_severity():
    seen = set()
    for path in CORPUS:
        for finding in validate(path).findings:
            if finding.rule_id in seen:
                continue
            seen.add(finding.rule_id)
            info = explain_rule(finding.rule_id)
            assert info.known, finding.rule_id
            assert finding.severity in info.severities, finding.rule_id
            assert info.step is finding.step
    assert len(seen) > 10


@pytest.mark.parametrize(
    ("rule_id", "pattern", "description"),
    [
        ("schema.cvc-elt.1", "schema.cvc-<rule>", "XML Schema validation rule cvc-elt.1"),
        (
            "schema.libxml2.SCHEMAV_MISC",
            "schema.libxml2.<TYPE>",
            "XML Schema validation error SCHEMAV_MISC",
        ),
        ("dictionary.incomplete", "<step>.incomplete", "The step could not finish"),
        ("schema.incomplete", "<step>.incomplete", "Schema validation could not complete"),
        ("crs.incomplete", "<step>.incomplete", "The step could not finish"),
        ("schematron.user-45b6d8b8510e", "schematron.user-<hash>", "Schematron rule user-"),
        ("schematron.borehole-depth", "schematron.<id>", "Schematron rule borehole-depth"),
    ],
)
def test_family_members_get_the_family_pattern(rule_id, pattern, description):
    family = next(r for r in rule_catalogue() if r.id == pattern)
    info = explain_rule(rule_id)
    assert info.id == rule_id
    assert info.pattern == family.pattern
    assert re.fullmatch(info.pattern or "", rule_id)
    assert info.severities == family.severities
    assert info.description.startswith(description)
    assert info.known
    assert info.step is Step(rule_id.partition(".")[0])


def test_a_valid_unknown_id_is_known_false():
    info = explain_rule("crs.whatever")
    assert info == RuleInfo(
        id="crs.whatever",
        pattern=None,
        step=Step.CRS,
        description="Not a rule id this version of pydiggs emits",
        severities=(),
        help_uri=rules.HELP_URI,
        known=False,
    )
    assert not explain_rule("dictionary.99").known


@pytest.mark.parametrize(
    "rule_id",
    ["", "schema", "bogus.1", "dictionary..5", "schema.-x", "x" * 300, "schema." + "a" * 250],
)
def test_bad_grammar_or_length_is_a_value_error(rule_id):
    with pytest.raises(ValueError, match="rule id"):
        explain_rule(rule_id)


def test_the_longest_accepted_id_is_256_characters():
    assert explain_rule("schema.cvc-" + "a" * 245).known
    with pytest.raises(ValueError, match="256"):
        explain_rule("schema.cvc-" + "a" * 246)


def test_a_non_string_is_a_type_error():
    with pytest.raises(TypeError):
        explain_rule(5)  # type: ignore[arg-type]


@given(st.text(max_size=300))
def test_explain_never_crashes(text):
    try:
        info = explain_rule(text)
    except ValueError:
        assert len(text) > rules.MAX_RULE_ID_CHARS or not is_valid_rule_id(text)
    else:
        assert info.id == text


def test_the_timeout_description_is_generic():
    assert rules.describe(rules.TIMEOUT) == "Validation did not finish within its time limit"


# --- docs/findings.md and the catalogue agree --------------------------------------


def _doc_rows() -> list[tuple[list[str], str]]:
    text = (ROOT / "docs" / "findings.md").read_text("utf-8")
    section = text.split("\n## Rule ids\n", 1)[1].split("\n### Differences", 1)[0]
    rows, in_table = [], False
    for line in section.splitlines():
        if line.startswith("| Rule id | Severity"):
            in_table = True
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        if in_table and not line.startswith("|---"):
            first, severity = (cell.strip() for cell in line.strip("|").split("|")[:2])
            rows.append((re.findall(r"`([^`]+)`", first), severity))
    return rows


def _severities(cell: str) -> tuple[Severity, ...] | None:
    if "see [below]" in cell:
        return None
    if "worst dropped finding" in cell:
        return (Severity.ERROR, Severity.WARNING, Severity.INFO)
    words = re.findall(r"\b(error|warning|info)\b", cell.split("(", maxsplit=1)[0])
    return tuple(SEVERITY_WORDS[w] for w in words)


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(<[^>]+>)", template)
    return re.compile(
        "".join("[A-Za-z0-9_-]+" if p.startswith("<") else re.escape(p) for p in parts)
    )


def test_docs_table_and_catalogue_agree():
    catalogue = {r.id: r for r in rule_catalogue()}
    covered: set[str] = set()
    for spans, cell in _doc_rows():
        expected = _severities(cell)
        if expected is None:
            continue
        for span in spans:
            if span in catalogue:
                assert catalogue[span].severities == expected, span
                covered.add(span)
            elif "<" in span:  # a doc-only grouping of fixed ids, such as the bundled rules
                members = [
                    r
                    for r in catalogue.values()
                    if r.pattern is None and _template_regex(span).fullmatch(r.id)
                ]
                assert members, span
                for member in members:
                    assert member.severities == expected, member.id
                    covered.add(member.id)
            else:
                pytest.fail(f"docs/findings.md names {span!r}, which the catalogue lacks")
    assert covered == set(catalogue), sorted(set(catalogue) - covered)
