"""validate(local_dictionaries=...): local codeSpace dictionaries stay in the instance folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydiggs import validate, validator
from pydiggs.cli import main

_DICT = """<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:diggs="http://diggsml.org/schemas/3" gml:id="d">
  <gml:dictionaryEntry><diggs:Definition gml:id="surveyor"><gml:name>surveyor</gml:name>
  </diggs:Definition></gml:dictionaryEntry>
</gml:Dictionary>"""


def _instance(folder: Path, code_space: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "site.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        f'<role codeSpace="{code_space}#surveyor">surveyor</role></Diggs>',
        encoding="utf-8",
    )
    return path


def _dictionary_findings(path: Path, **kwargs) -> list[tuple[str, str, str]]:
    report = validate(path, checks=["dictionary"], **kwargs)
    return [(f.rule_id, f.severity.value, f.message) for f in report.findings]


def test_dictionary_next_to_the_instance_is_read(tmp_path):
    site = _instance(tmp_path / "survey", "./local.xml")
    (tmp_path / "survey" / "local.xml").write_text(_DICT)
    assert _dictionary_findings(site) == []
    sub = _instance(tmp_path / "survey2", "./dicts/local.xml")
    (tmp_path / "survey2" / "dicts").mkdir()
    (tmp_path / "survey2" / "dicts" / "local.xml").write_text(_DICT)
    assert _dictionary_findings(sub) == []
    inside_uri = _instance(tmp_path / "survey3", (tmp_path / "survey3" / "d.xml").as_uri())
    (tmp_path / "survey3" / "d.xml").write_text(_DICT)
    assert _dictionary_findings(inside_uri) == []


def test_parent_folder_is_refused_and_existence_does_not_show(tmp_path):
    site = _instance(tmp_path / "survey", "../secret.xml")
    missing = _dictionary_findings(site)
    (tmp_path / "secret.xml").write_text(_DICT)
    present = _dictionary_findings(site)
    assert missing == present  # no existence oracle
    ((rule, severity, message),) = present
    assert (rule, severity) == ("dictionary.3", "warning")
    assert "only from inside the instance's folder" in message
    # The legacy check keeps 1.1 behaviour and reads it.
    assert validator(str(site), output_log=False).dictionary_check() is True


def test_no_working_directory_fallback(tmp_path, monkeypatch):
    site = _instance(tmp_path / "survey", "./local.xml")
    (tmp_path / "local.xml").write_text(_DICT)  # only in the working directory
    monkeypatch.chdir(tmp_path)
    assert [r for r, _, _ in _dictionary_findings(site)] == ["dictionary.3"]
    assert validator(str(site), output_log=False).dictionary_check() is True


def test_file_uris_and_symlinks_out_of_the_folder_are_refused(tmp_path):
    (tmp_path / "outside.xml").write_text(_DICT)
    uri = _instance(tmp_path / "a", (tmp_path / "outside.xml").as_uri())
    assert [r for r, _, _ in _dictionary_findings(uri)] == ["dictionary.3"]
    link = _instance(tmp_path / "b", "./link.xml")
    (tmp_path / "b" / "link.xml").symlink_to(tmp_path / "outside.xml")
    assert [r for r, _, _ in _dictionary_findings(link)] == ["dictionary.3"]


def test_off_reads_no_local_dictionary(tmp_path, capsys):
    site = _instance(tmp_path / "survey", "./local.xml")
    (tmp_path / "survey" / "local.xml").write_text(_DICT)
    ((rule, _, message),) = _dictionary_findings(site, local_dictionaries="off")
    assert rule == "dictionary.3"
    assert "local_dictionaries='off'" in message
    with pytest.raises(ValueError, match="local_dictionaries"):
        validate(site, local_dictionaries="anywhere")  # type: ignore[arg-type]
    code = main(
        ["validate", "--format", "json", "--checks", "dictionary", "--local-dictionaries",
         "off", str(site)]
    )  # fmt: skip
    assert code == 0  # a warning only
    findings = json.loads(capsys.readouterr().out)["files"][0]["findings"]
    assert [f["rule_id"] for f in findings] == ["dictionary.3"]
