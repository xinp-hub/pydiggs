"""validate(): the native layer as findings, checked against the legacy methods."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import (
    Finding,
    InstanceReadError,
    PydiggsError,
    Report,
    ResourceError,
    Severity,
    Step,
    validate,
    validator,
)
from pydiggs.findings import _json, _sarif, rules
from pydiggs.findings.locate import SNIPPET_LIMIT, Locator
from pydiggs.findings.run import NATIVE_STEPS, display_paths, reference_data

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CORPUS = sorted(
    p.relative_to(ROOT)
    for d in ("tests/fixtures/official", "tests/test_files")
    for p in (ROOT / d).rglob("*.xml")
)
SCHEMA = jsonschema.Draft202012Validator(
    json.loads(
        resources.files("pydiggs.findings").joinpath(_json.SCHEMA_RESOURCE).read_text("utf-8")
    )
)
SARIF = jsonschema.Draft4Validator(
    json.loads((FIXTURES / "sarif" / "sarif-schema-2.1.0.json").read_text("utf-8"))
)
NO_ERROR = "tests/test_files/2.6/No_Error_2_6.xml"
SCHEMA_ERROR = "tests/test_files/2.6/Schema_Error_1-2_6.xml"


@pytest.fixture(autouse=True)
def _at_repo_root(monkeypatch):
    monkeypatch.chdir(ROOT)


def _errors(report: Report, *steps: Step, exclude: frozenset[str] = frozenset()) -> list[Finding]:
    return [
        f
        for f in report.findings
        if f.step in steps and f.severity is Severity.ERROR and f.rule_id not in exclude
    ]


@pytest.mark.slow
@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.as_posix())
def test_validate_agrees_with_the_legacy_methods(path):
    report = validate(path)
    legacy = validator(str(path), output_log=False)
    wp04 = frozenset({rules.DICTIONARY_13, rules.DEPRECATED, rules.REMOVED})
    if report.steps[Step.STRUCTURE] == "failed" and _errors(report, Step.STRUCTURE)[0].rule_id == (
        rules.WELL_FORMED
    ):
        assert not legacy.schema_check()
        assert not legacy.dictionary_check()
        assert not legacy.schematron_check()
        assert not legacy.context_check()
        return
    assert legacy.schema_check() is (not _errors(report, Step.SCHEMA))
    assert legacy.dictionary_check() is (not _errors(report, Step.DICTIONARY, exclude=wp04))
    assert legacy.schematron_check() is (not _errors(report, Step.SCHEMATRON))
    context = _errors(report, Step.STRUCTURE, Step.GEOMETRY, Step.CONTEXT)
    assert legacy.context_check() is (not context)
    assert report.diggs_version == legacy.diggs_version
    SCHEMA.validate(json.loads(report.to_json()))
    SARIF.validate(json.loads(json.dumps(report.to_sarif())))


def test_counts_match_the_legacy_dictionary_messages():
    from pydiggs.dictionary import DictionarySemanticValidator

    path = FIXTURES / "official" / "3.0" / "PorePressureDissipation_Example.xml"
    legacy = DictionarySemanticValidator().validate_instance(path)
    report = validate(path, checks=["dictionary"])
    native = [
        "dictionary.3" if f.rule_id == rules.DICTIONARY_OFFLINE else f.rule_id
        for f in report.findings
        if f.rule_id.split(".")[1].isdigit() or f.rule_id == rules.DICTIONARY_OFFLINE
    ]
    assert sorted(native) == sorted(f"dictionary.{m.check}" for m in legacy.messages)
    assert {f.message for f in report.findings if f.rule_id == "dictionary.5"} == {
        m.text.split("\n", 1)[1] for m in legacy.messages if m.check == 5
    }


def test_schema_findings_carry_rule_line_xpath_and_gml_id():
    report = validate(SCHEMA_ERROR)
    assert report.ok is False
    assert report.diggs_version == "2.6"
    assert report.steps[Step.SCHEMA] == "failed"
    first, second = _errors(report, Step.SCHEMA)
    assert first.rule_id == second.rule_id == "schema.cvc-complex-type.2.4"
    assert (first.line, second.line) == (12, 17)
    assert first.column is None
    assert first.xpath == (
        "/Diggs[1]/documentInformation[1]/DocumentInformation[1]/author[1]/businessAssociate[1]"
    )
    assert first.gml_id == "BA_LADOTD"
    assert first.message.startswith("Element '{http://diggsml.org/schemas/2.6}businessAssociate'")
    assert first.source_snippet is None


def test_not_well_formed_is_a_finding_and_other_steps_skip():
    report = validate("tests/test_files/Syntax_Error_1.xml")
    (finding,) = report.findings
    assert finding.rule_id == "structure.well-formed"
    assert (finding.line, finding.column) == (4, 52)
    assert finding.message == (
        "Opening and ending tag mismatch: creationDate line 4 and CreationDate"
    )
    assert report.diggs_version is None
    assert report.steps == {
        Step.STRUCTURE: "failed",
        **{s: "skipped" for s in NATIVE_STEPS if s is not Step.STRUCTURE},
    }


def test_clean_file_passes_every_step():
    report = validate(NO_ERROR)
    assert report.ok
    assert set(report.steps.values()) == {"passed"}
    assert list(report.steps) == list(NATIVE_STEPS)
    assert report.path == NO_ERROR
    assert report.tool_version == pydiggs.__version__
    assert report.reference_data == reference_data()
    assert set(report.reference_data) == {"schemas", "def"}


def test_checks_select_steps():
    report = validate(SCHEMA_ERROR, checks=["dictionary", Step.GEOMETRY])
    assert report.steps[Step.SCHEMA] == "skipped"
    assert report.steps[Step.DICTIONARY] == "passed"
    assert report.steps[Step.STRUCTURE] == "skipped"
    assert {f.step for f in report.findings} <= {Step.DICTIONARY, Step.GEOMETRY}
    broken = validate("tests/test_files/Syntax_Error_1.xml", checks=["schema"])
    assert broken.steps[Step.STRUCTURE] == "failed"  # well-formedness always runs


def test_schematron_locations_two_failing_boreholes_and_namespace_fallback():
    report = validate(FIXTURES / "findings" / "two_failing_boreholes.xml", checks=["schematron"])
    depth = [f for f in report.findings if f.rule_id == "schematron.Borehole.totalMeasuredDepth"]
    assert [(f.line, f.gml_id) for f in depth] == [(6, "BH-1"), (16, "BH-3")]
    assert depth[1].xpath == "/Diggs[1]/samplingFeature[3]/Borehole[1]/totalMeasuredDepth[1]"
    casing = {f.rule_id for f in report.findings if f.gml_id == "C-1"}
    assert casing == {"schematron.Casing.outside-gt-inside", rules.SCHEMATRON_CASING_UNITS}
    collision = validate(
        FIXTURES / "findings" / "svrl_namespace_collision.xml", checks=["schematron"]
    )
    (finding,) = collision.findings
    assert (finding.line, finding.gml_id) == (8, "BH-X")


def test_user_schematron_rules_get_hashed_ids():
    report = validate(
        "tests/test_files/Schematron_Error_1.xml",
        checks=["schematron"],
        schematron_path="tests/test_schematron_schema/test_schematron_2.sch",
    )
    legacy = validator(
        "tests/test_files/Schematron_Error_1.xml",
        schematron_path="tests/test_schematron_schema/test_schematron_2.sch",
        output_log=False,
    ).schematron_check()
    assert legacy is (not _errors(report, Step.SCHEMATRON))
    for finding in report.findings:
        assert finding.rule_id.startswith("schematron.")


def test_snippets_are_opt_in_and_capped():
    assert all(f.source_snippet is None for f in validate(SCHEMA_ERROR).findings)
    with_snippets = validate(SCHEMA_ERROR, include_snippets=True)
    snippets = [f.source_snippet for f in with_snippets.findings]
    assert all(s is not None for s in snippets)
    assert all(len(s.encode()) <= SNIPPET_LIMIT for s in snippets if s is not None)
    assert snippets[0].startswith("<businessAssociate")


def test_snippet_cap_cuts_long_elements(tmp_path):
    from lxml import etree

    root = etree.fromstring(("<a>" + "é" * 2000 + "</a>").encode())
    snippet = Locator(etree.ElementTree(root)).snippet(root)
    assert len(snippet.encode()) <= SNIPPET_LIMIT
    assert snippet.endswith("...")


@pytest.mark.parametrize(
    ("kwargs", "exc", "match"),
    [
        ({"diggs_version": "9.9"}, ValueError, "unknown DIGGS version '9.9'"),
        ({"checks": ["nope"]}, ValueError, "unknown check 'nope'"),
        ({"checks": []}, ValueError, "at least one step"),
        ({"checks": "schema"}, ValueError, "not the string 'schema'"),
        ({"checks": ["crs"]}, ValueError, "unknown check 'crs'"),
        ({"schema_path": "missing.xsd"}, ResourceError, "cannot read schema 'missing.xsd'"),
        (
            {"schema_path": "tests/test_schematron_schema/test_schematron_1.sch"},
            ResourceError,
            "cannot use schema",
        ),
        ({"dictionary_path": "missing.xml"}, ResourceError, "dictionary 'missing.xml'"),
        (
            {"dictionary_path": "tests/test_files/Syntax_Error_1.xml"},
            ResourceError,
            "cannot read dictionary data",
        ),
        ({"schematron_path": "missing.sch"}, ResourceError, "cannot read Schematron rules"),
        ({"schematron_path": NO_ERROR}, ResourceError, "cannot use Schematron rules"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_bad_arguments_and_trusted_inputs_raise(kwargs, exc, match):
    with pytest.raises(exc, match=match):
        validate(NO_ERROR, **kwargs)


def test_unreadable_instances_raise_instance_read_error(tmp_path):
    with pytest.raises(InstanceReadError, match="No such file") as missing:
        validate(tmp_path / "missing.xml")
    assert isinstance(missing.value, OSError)
    assert isinstance(missing.value, PydiggsError)
    with pytest.raises(InstanceReadError):
        validate(tmp_path)
    packed = tmp_path / "packed.xml"
    packed.write_bytes(gzip.compress(b"<Diggs/>"))
    with pytest.raises(InstanceReadError, match="compressed XML"):
        validate(packed)


def test_paths_are_relative_or_bare_and_absolute_only_on_request(tmp_path, monkeypatch):
    outside = tmp_path / "site" / "BH 01.xml"
    outside.parent.mkdir()
    outside.write_bytes((ROOT / NO_ERROR).read_bytes())
    report = validate(outside)
    assert report.path == "BH 01.xml"
    assert report.absolute_path == outside.resolve().as_posix()
    text = report.to_json() + json.dumps(report.to_sarif())
    assert str(tmp_path) not in text
    assert str(Path.home()) not in text
    assert outside.resolve().as_posix() in report.to_json(absolute_paths=True)
    monkeypatch.chdir(tmp_path)
    assert display_paths(outside) == ("site/BH 01.xml", outside.resolve().as_posix())


def test_messages_never_carry_the_absolute_instance_path(tmp_path):
    broken = tmp_path / "broken.xml"
    broken.write_bytes(b'<!DOCTYPE d [<!ENTITY e SYSTEM "x.ent">]><d>&e;</d>')
    report = validate(broken)
    assert report.findings
    assert str(tmp_path) not in report.to_json()


def test_offline_remote_dictionaries_are_info_not_warnings(monkeypatch, capsys):
    from pydiggs import _xml
    from pydiggs.cli import main

    path = FIXTURES / "official" / "3.0" / "PorePressureDissipation_Example.xml"
    report = validate(path, checks=["dictionary"])
    offline = [f for f in report.findings if f.rule_id == rules.DICTIONARY_OFFLINE]
    assert len(offline) == 12
    assert all(f.severity is Severity.INFO and f.line for f in offline)
    assert "allow_network=False" in offline[0].message
    assert not [f for f in report.findings if f.rule_id == "dictionary.3"]
    water = FIXTURES / "official" / "2.6" / "water_content-from25.xml"
    assert main(["validate", "--checks", "dictionary", "--fail-on", "warning", str(water)]) == 0
    capsys.readouterr()

    def unreachable(url, patterns):
        raise OSError("network down")

    monkeypatch.setattr(_xml, "fetch_and_parse", unreachable)
    online = validate(path, checks=["dictionary"], allow_network=True)
    assert not [f for f in online.findings if f.rule_id == rules.DICTIONARY_OFFLINE]
    third = [f for f in online.findings if f.rule_id == "dictionary.3"]
    assert len(third) == 12
    assert all(f.severity is Severity.WARNING for f in third)


_DETERMINISM = """
import json, sys
from pydiggs import validate
paths = sys.argv[1:]
print(json.dumps([validate(p, include_snippets=True).to_json() for p in paths]))
"""


def _run_seeded(seed: str, paths: list[str]) -> list[str]:
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _DETERMINISM, *paths],
        env={**os.environ, "PYTHONHASHSEED": seed},
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    docs: list[str] = json.loads(out)
    return docs


def test_json_is_deterministic_across_hash_seeds_and_path_spellings():
    spellings = [SCHEMA_ERROR, "./" + SCHEMA_ERROR, str(ROOT / SCHEMA_ERROR)]
    outputs = set()
    for seed in ("0", "1", "12345"):
        docs = _run_seeded(seed, spellings)
        assert len(set(docs)) == 1
        outputs.add(docs[0])
    assert len(outputs) == 1


@pytest.mark.slow
def test_json_is_deterministic_across_hash_seeds_on_several_files():
    paths = [
        "tests/fixtures/findings/multi_name_property.xml",
        "tests/fixtures/official/3.0/PorePressureDissipation_Example.xml",
        "tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml",
        "tests/fixtures/official/2.5.a/StrengthExample.xml",
        "tests/test_files/Dictionary_Check_MWD_1.xml",
    ]
    runs = [_run_seeded(seed, paths) for seed in ("0", "1", "2", "3", "4242")]
    assert all(run == runs[0] for run in runs)


_LEGACY_NAME = """
from pydiggs.dictionary import DictionarySemanticValidator
result = DictionarySemanticValidator().validate_instance(
    "tests/fixtures/findings/multi_name_property.xml"
)
print("|".join(m.text for m in result.messages))
"""


def test_multi_name_definition_text_does_not_depend_on_hash_seed():
    texts = {
        subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
            [sys.executable, "-c", _LEGACY_NAME],
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for seed in ("0", "1", "2", "3", "4", "5")
    }
    assert len(texts) == 1
    assert '"pull speed"' in texts.pop()


_BASE = (ROOT / NO_ERROR).read_bytes()


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(st.integers(0, len(_BASE) - 1), st.binary(min_size=0, max_size=8)),
        min_size=1,
        max_size=4,
    )
)
def test_mutated_files_give_findings_never_other_exceptions(tmp_path_factory, edits):
    data = bytearray(_BASE)
    for offset, replacement in edits:
        data[offset : offset + len(replacement)] = replacement
    path = tmp_path_factory.mktemp("mut") / "m.xml"
    path.write_bytes(bytes(data))
    try:
        report = validate(path)
    except InstanceReadError:
        return
    SCHEMA.validate(json.loads(report.to_json()))


def test_doctests_of_public_modules():
    import doctest

    from pydiggs import errors
    from pydiggs.findings import keys, locate, model, run

    for module in (errors, keys, locate, model, rules, run, _json, _sarif):
        failed, _ = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
        assert failed == 0, module.__name__
