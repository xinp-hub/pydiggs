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


def test_an_edited_dictionary_next_to_the_instance_is_reread(tmp_path):
    site = _instance(tmp_path / "survey", "./local.xml")
    local = tmp_path / "survey" / "local.xml"
    local.write_text(_DICT)
    assert _dictionary_findings(site) == []
    local.write_text(_DICT.replace("surveyor", "geologist"))
    assert [r for r, _, _ in _dictionary_findings(site)] == ["dictionary.5"]


def test_a_failed_fetch_is_retried_on_the_next_file(tmp_path, monkeypatch):
    from lxml import etree

    from pydiggs import _xml

    url = "https://diggsml.org/def/codes/DIGGS/0.1/not-bundled.xml"
    site = _instance(tmp_path / "survey", url)
    calls = []

    def flaky(requested, patterns):
        calls.append(requested)
        if len(calls) == 1:
            raise OSError("temporary failure")
        return etree.ElementTree(etree.fromstring(_DICT.encode()))

    monkeypatch.setattr(_xml, "fetch_and_parse", flaky)
    first = _dictionary_findings(site, allow_network=True)
    assert [r for r, _, _ in first] == ["dictionary.3"]
    assert _dictionary_findings(site, allow_network=True) == []
    assert calls == [url, url]


@pytest.mark.parametrize(
    "spelling",
    ["nul", "loop", "long"],
)
def test_paths_the_os_rejects_are_refused_without_a_traceback(tmp_path, capsys, spelling):
    folder = tmp_path / "survey"
    folder.mkdir()
    if spelling == "nul":
        code_space = (folder / "a").as_uri() + "%00b.xml"
    elif spelling == "loop":
        (folder / "loop.xml").symlink_to(folder / "loop.xml")
        code_space = "./loop.xml"
    else:
        code_space = "./" + "n" * 300 + ".xml"  # longer than NAME_MAX
    site = _instance(folder, code_space)
    ((rule, severity, message),) = _dictionary_findings(site)
    assert (rule, severity) == ("dictionary.3", "warning")
    assert str(tmp_path) not in message or spelling == "nul"
    other = _instance(tmp_path / "next", "./local.xml")
    (tmp_path / "next" / "local.xml").write_text(_DICT)
    code = main(["validate", "--format", "json", "--checks", "dictionary", str(site), str(other)])
    out = capsys.readouterr()
    assert code == 0  # warnings only; the batch went on
    assert "Traceback" not in out.err
    assert len(json.loads(out.out)["files"]) == 2


def test_one_file_is_parsed_once_however_it_is_spelled(tmp_path, monkeypatch):
    from pydiggs import _xml

    folder = tmp_path / "survey"
    (folder / "d").mkdir(parents=True)
    (folder / "local.xml").write_text(_DICT)
    spellings = [f"./{'d/../' * i}local.xml" for i in range(200)]
    spellings += [f"{'./' * (i + 1)}local.xml" for i in range(200)]
    folder.joinpath("site.xml").write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        + "".join(f'<role codeSpace="{s}#surveyor">surveyor</role>' for s in spellings)
        + "</Diggs>",
        encoding="utf-8",
    )
    parsed: list[str | None] = []
    real = _xml.parse_bytes

    def spy(data, base_url):
        parsed.append(base_url)
        return real(data, base_url)

    monkeypatch.setattr(_xml, "parse_bytes", spy)
    assert _dictionary_findings(folder / "site.xml") == []
    assert sum(url is not None and url.endswith("local.xml") for url in parsed) == 1


def test_local_dictionaries_obey_max_bytes_regular_files_and_the_cap(tmp_path):
    import os

    folder = tmp_path / "survey"
    folder.mkdir()
    (folder / "big.xml").write_text(_DICT + " " * 10_000)
    site = _instance(folder, "./big.xml")
    assert _dictionary_findings(site, max_bytes=10_000_000) == []
    assert [r for r, _, _ in _dictionary_findings(site, max_bytes=5_000)] == ["dictionary.3"]
    if hasattr(os, "mkfifo"):
        os.mkfifo(folder / "pipe.xml")
        fifo = _instance(tmp_path / "f", "../survey/pipe.xml")  # refused: outside
        assert [r for r, _, _ in _dictionary_findings(fifo)] == ["dictionary.3"]
        inside = _instance(folder / "sub", "../pipe.xml")
        assert [r for r, _, _ in _dictionary_findings(inside)] == ["dictionary.3"]
    many = tmp_path / "many"
    many.mkdir()
    for i in range(17):
        (many / f"d{i}.xml").write_text(_DICT)
    (many / "site.xml").write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        + "".join(f'<role codeSpace="./d{i}.xml#surveyor">surveyor</role>' for i in range(17))
        + "</Diggs>"
    )
    ((rule, _, message),) = _dictionary_findings(many / "site.xml")
    assert rule == "dictionary.3"
    assert "more than 16 different local dictionaries" in message


def test_file_uri_inside_the_folder_is_percent_decoded(tmp_path):
    folder = tmp_path / "survey"
    folder.mkdir()
    (folder / "a b.xml").write_text(_DICT)
    site = _instance(folder, (folder / "a b.xml").as_uri())
    assert "%20" in site.read_text()
    assert _dictionary_findings(site) == []


def _elements_reachable(obj, seen=None):
    from lxml import etree

    seen = set() if seen is None else seen
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, etree._Element):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _elements_reachable(k, seen)
            yield from _elements_reachable(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            yield from _elements_reachable(v, seen)


def test_the_cached_validator_keeps_no_instance_tree(tmp_path):
    from pydiggs.findings import run

    site = _instance(tmp_path / "survey", "./local.xml")
    (tmp_path / "survey" / "local.xml").write_text(_DICT)
    validate(site, checks=["dictionary"])
    cached = run._dictionary_validator(None, allow_network=False)
    leftovers = [
        el
        for name, value in vars(cached).items()
        if name not in ("_baseline_cache", "_definition_index", "_value_index")
        for el in _elements_reachable(value)
    ]
    assert leftovers == []
    assert cached._result is None
    assert cached._instance_dir is None


def test_local_dictionaries_share_the_instance_byte_budget(tmp_path):
    folder = tmp_path / "survey"
    folder.mkdir()
    for name in ("a", "b"):
        (folder / f"{name}.xml").write_text(_DICT.replace("surveyor", f"surveyor-{name}"))
    site = folder / "site.xml"
    site.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3">'
        '<role codeSpace="./a.xml#surveyor-a">surveyor-a</role>'
        '<role codeSpace="./b.xml#surveyor-b">surveyor-b</role>'
        "</Diggs>"
    )
    one = (folder / "a.xml").stat().st_size
    roomy = site.stat().st_size + 2 * one + 10
    assert [r for r, _, _ in _dictionary_findings(site, max_bytes=roomy)] == []
    tight = site.stat().st_size + one + 10  # the instance and one dictionary fit
    assert [r for r, _, _ in _dictionary_findings(site, max_bytes=tight)] == ["dictionary.3"]
