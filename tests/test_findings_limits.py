"""HG1 amendment: findings cap and instance size limit for validate()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydiggs import InstanceReadError, Severity, Step, validate, validator
from pydiggs.cli import main
from pydiggs.findings import rules
from pydiggs.findings.run import DEFAULT_MAX_BYTES, DEFAULT_MAX_FINDINGS

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"


def test_defaults():
    assert DEFAULT_MAX_FINDINGS == 10_000
    assert DEFAULT_MAX_BYTES == 100 * 1024 * 1024


def test_truncation_keeps_n_findings_plus_one_marker():
    full = validate(NO_ERROR)
    assert len(full.findings) == 51
    report = validate(NO_ERROR, max_findings=5)
    marker = [f for f in report.findings if f.rule_id == rules.FINDINGS_TRUNCATED]
    assert len(report.findings) == 6
    (finding,) = marker
    assert finding.step is Step.STRUCTURE
    assert finding.severity is Severity.WARNING  # the worst finding left out
    assert "Stopped after 5 findings; 46 more were not reported" in finding.message
    assert report.steps[Step.DICTIONARY] == "incomplete"
    assert report.steps[Step.SCHEMA] == "passed"


def test_truncated_errors_keep_the_report_failing():
    report = validate(SCHEMA_ERROR, checks=["schema"], max_findings=1)
    (_, marker) = sorted(report.findings, key=lambda f: f.rule_id)
    assert marker.rule_id == rules.FINDINGS_TRUNCATED
    assert marker.severity is Severity.ERROR
    assert report.steps[Step.SCHEMA] == "incomplete"
    assert report.ok is False


def test_no_limit_and_bad_limits():
    assert len(validate(NO_ERROR, max_findings=None).findings) == 51
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="max_findings"):
            validate(NO_ERROR, max_findings=bad)  # type: ignore[arg-type]
    for bad_size in (0, None):
        with pytest.raises(ValueError, match="max_bytes"):
            validate(NO_ERROR, max_bytes=bad_size)  # type: ignore[arg-type]


def test_size_limit_is_configurable_and_legacy_keeps_its_own(tmp_path, monkeypatch):
    size = NO_ERROR.stat().st_size
    with pytest.raises(InstanceReadError, match=f"exceeds {size - 1} bytes"):
        validate(NO_ERROR, max_bytes=size - 1)
    assert validate(NO_ERROR, max_bytes=size).ok
    assert main(["validate", "--max-bytes", str(size - 1), str(NO_ERROR)]) == 2
    with pytest.raises(SystemExit):
        main(["validate", "--max-bytes", "0", str(NO_ERROR)])
    assert validator(str(NO_ERROR), output_log=False).schema_check() is True


def test_cli_max_findings(capsys):
    assert main(["validate", "--format", "json", "--max-findings", "3", str(NO_ERROR)]) == 0
    doc = json.loads(capsys.readouterr().out)
    findings = doc["files"][0]["findings"]
    assert len(findings) == 4
    assert findings[0]["rule_id"] == "structure.findings-truncated"
    assert main(["validate", "--format", "json", "--max-findings", "0", str(NO_ERROR)]) == 0
    assert len(json.loads(capsys.readouterr().out)["files"][0]["findings"]) == 51
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--max-findings", "-1", str(NO_ERROR)])
    assert exc.value.code == 2


def _copy(tmp_path: Path, rel: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(NO_ERROR.read_bytes())
    return target


def test_paths_are_bare_names_when_cwd_is_root_or_above_home(tmp_path, monkeypatch):
    from pydiggs.findings.run import display_paths

    home = tmp_path / "Users" / "me"
    target = _copy(home, "projects/site/BH-1.xml")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    monkeypatch.chdir("/")
    assert display_paths(target)[0] == "BH-1.xml"
    monkeypatch.chdir(tmp_path / "Users")  # an ancestor of home
    assert display_paths(target)[0] == "BH-1.xml"
    monkeypatch.chdir(home)  # home itself is fine
    assert display_paths(target)[0] == "projects/site/BH-1.xml"
    monkeypatch.chdir(home / "projects")
    assert validate(target, checks=["schema"]).path == "site/BH-1.xml"


def test_base_dir_gives_paths_relative_to_a_chosen_folder(tmp_path, monkeypatch, capsys):
    target = _copy(tmp_path, "survey/site/BH-1.xml")
    other = _copy(tmp_path, "elsewhere/BH-2.xml")
    monkeypatch.chdir("/")
    base = tmp_path / "survey"
    assert validate(target, checks=["schema"], base_dir=base).path == "site/BH-1.xml"
    assert validate(other, checks=["schema"], base_dir=base).path == "BH-2.xml"
    code = main(
        ["validate", "--format", "json", "--checks", "schema", "--base-dir", str(base),
         str(target), str(other)]
    )  # fmt: skip
    assert code == 0
    paths = [f["path"] for f in json.loads(capsys.readouterr().out)["files"]]
    assert paths == ["BH-2.xml", "site/BH-1.xml"]


_LOCAL_DICT = """<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:diggs="http://diggsml.org/schemas/3" gml:id="local">
  {entries}
</gml:Dictionary>"""
_LOCAL_INSTANCE = """<Diggs xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2">
  <role codeSpace="./local.xml#surveyor">surveyor</role>
</Diggs>"""


def test_cached_dictionary_validator_does_not_leak_between_files(tmp_path):
    from pydiggs.findings import run

    for name, entries in (
        ("a", '<gml:dictionaryEntry><diggs:Definition gml:id="surveyor"><gml:name>surveyor'
              "</gml:name></diggs:Definition></gml:dictionaryEntry>"),
        ("b", ""),
    ):  # fmt: skip
        (tmp_path / name).mkdir()
        (tmp_path / name / "local.xml").write_text(_LOCAL_DICT.format(entries=entries))
        (tmp_path / name / "site.xml").write_text(_LOCAL_INSTANCE)

    first = run._dictionary_validator(None, allow_network=False)
    a = validate(tmp_path / "a" / "site.xml", checks=["dictionary"])
    b = validate(tmp_path / "b" / "site.xml", checks=["dictionary"])
    a_again = validate(tmp_path / "a" / "site.xml", checks=["dictionary"])
    assert run._dictionary_validator(None, allow_network=False) is first
    assert run._dictionary_validator(None, allow_network=True) is not first
    assert not [f for f in a.findings if f.severity is Severity.ERROR]
    assert [f.rule_id for f in b.findings if f.severity is Severity.ERROR] == ["dictionary.5"]
    assert a_again.findings == a.findings


def test_truncation_marker_is_not_compared_and_always_shown(tmp_path, capsys):
    from pydiggs.findings import keys

    marker = ("structure", "warning", "file", rules.FINDINGS_TRUNCATED)
    finding = ("schema", "error", "line:1", "schema.x")
    assert keys.compare([marker, finding], [finding], ["structure", "schema"]) == ({}, {})
    # Only info findings past the cap: the marker is info, and text output still shows it.
    infos = tmp_path / "infos.xml"
    infos.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/2.6">'
        + '<q codeSpace="urn:x">v</q>' * 3
        + "</Diggs>"
    )
    report = validate(infos, checks=["dictionary"], max_findings=1)
    (shown,) = [f for f in report.findings if f.rule_id == rules.FINDINGS_TRUNCATED]
    assert shown.severity is Severity.INFO
    assert shown.message.startswith("Stopped after 1 finding; 2 more were not reported")
    assert report.counts["info"] == 2  # counts include the marker
    main(["validate", "--checks", "dictionary", "--max-findings", "1", str(infos)])
    out = capsys.readouterr().out
    assert "info structure.findings-truncated: Stopped after 1 finding" in out


def test_context_budget_counts_only_requested_steps(tmp_path):
    # Geometry warnings only; the unrequested structure and context checks must not
    # use up max_findings (code review, round 3).
    path = tmp_path / "geom.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2"'
        ' xmlns:xlink="http://www.w3.org/1999/xlink"><r xlink:href="#no"/>'
        "<gml:Point><gml:pos>1 2</gml:pos></gml:Point></Diggs>"
    )
    report = validate(path, checks=["geometry"], max_findings=2)
    assert [f.rule_id for f in report.findings] == ["geometry.srs-attributes"] * 2
    assert report.steps[Step.GEOMETRY] == "passed"


@pytest.mark.parametrize("cap", ["0", "1"])
def test_offline_findings_past_the_cap_stay_info(tmp_path, capsys, cap):
    path = tmp_path / "offline.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        + "".join(
            f'<role codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/missing{i}.xml#x">x</role>'
            for i in range(3)
        )
        + "</Diggs>"
    )
    code = main(
        ["validate", "--checks", "dictionary", "--fail-on", "warning", "--max-findings", cap,
         str(path)]
    )  # fmt: skip
    capsys.readouterr()
    assert code == 0
    if cap == "1":
        report = validate(path, checks=["dictionary"], max_findings=1)
        marker = [f for f in report.findings if f.rule_id == rules.FINDINGS_TRUNCATED]
        assert [f.severity for f in marker] == [Severity.INFO]


FIXTURES = ROOT / "tests" / "fixtures"


def test_nothing_is_located_once_the_report_is_full(monkeypatch):
    from pydiggs.findings._schematron import RuleContexts
    from pydiggs.findings.locate import Locator

    calls = {"matched": 0, "element_at": 0, "xpath": 0}
    for owner, name in ((RuleContexts, "matched"), (Locator, "element_at"), (Locator, "xpath")):
        real = getattr(owner, name)

        def counted(self, *args, _real=real, _name=name):
            calls[_name] += 1
            return _real(self, *args)

        monkeypatch.setattr(owner, name, counted)
    boreholes = FIXTURES / "findings" / "two_failing_boreholes.xml"
    report = validate(boreholes, checks=["schematron"], max_findings=1)
    assert calls["xpath"] == 1  # four findings, only the kept one located
    assert calls["matched"] == 1  # the pattern's contexts evaluated once
    assert report.steps[Step.SCHEMATRON] == "incomplete"
    calls["xpath"] = 0
    validate(SCHEMA_ERROR, checks=["schema"], max_findings=1)
    assert calls["element_at"] == 1  # two schema errors
    assert calls["xpath"] == 1


def test_an_edited_dictionary_path_is_reloaded(tmp_path):
    import os

    from pydiggs.findings import run
    from pydiggs.pydiggs import _DEFAULT_DICTIONARY

    dictionary = tmp_path / "properties.xml"
    dictionary.write_bytes(_DEFAULT_DICTIONARY.read_bytes())
    first = run._dictionary_validator(dictionary, allow_network=False)
    assert run._dictionary_validator(dictionary, allow_network=False) is first
    later = dictionary.stat().st_mtime_ns + 10_000_000_000
    os.utime(dictionary, ns=(later, later))
    second = run._dictionary_validator(dictionary, allow_network=False)
    assert second is not first
    assert run._dictionary_validator(dictionary, allow_network=False) is second


def test_long_user_schematron_ids_are_hashed():
    long_id = "a" * (rules.MAX_USER_ID_CHARS + 1)
    assert rules.schematron_user_rule(long_id, "c", "t") == rules.schematron_user_rule(
        None, "c", "t"
    )
    ok_id = "a" * rules.MAX_USER_ID_CHARS
    assert rules.schematron_user_rule(ok_id, "c", "t") == f"schematron.{ok_id}"


def test_dictionary_validator_cache_is_a_small_lru(tmp_path, monkeypatch):
    from pydiggs.findings import run

    monkeypatch.setattr(run, "DictionarySemanticValidator", lambda **kwargs: object())
    run._CACHE.dictionaries.clear()
    paths = []
    for i in range(run.MAX_CACHED_VALIDATORS + 3):
        p = tmp_path / f"d{i}.xml"
        p.write_text("<d/>")
        paths.append(p)
        run._dictionary_validator(p, allow_network=False)
    assert len(run._CACHE.dictionaries) == run.MAX_CACHED_VALIDATORS
    newest = run._dictionary_validator(paths[-1], allow_network=False)
    assert run._dictionary_validator(paths[-1], allow_network=False) is newest
    run._CACHE.dictionaries.clear()
