"""The conformance corpus manifest, fetcher and expected outcomes (scripts/conformance.py)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "conformance.py"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")

SHA_A = hashlib.sha256(b"<a/>\n").hexdigest()
COMMIT = "c9b1d08580a951707fc8e3ac4397d64cedba59fc"


@pytest.fixture(scope="module")
def conf():
    spec = importlib.util.spec_from_file_location("conformance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules, so register before running.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def _manifest(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "corpus.toml"
    path.write_text(body, encoding="utf-8")
    return path


GOOD = f"""
[sources.demo]
repo = "https://github.com/example/demo"
commit = "{COMMIT}"
license = "MIT"

[files."demo/a.xml"]
path = "dir/a b.xml"
sha256 = "{SHA_A}"
size = 5
"""


class FakeResponse:
    """An HTTP response that returns at most 2 bytes per read, like a slow socket."""

    def __init__(self, data: bytes, url: str) -> None:
        self._data = data
        self._url = url

    def read(self, amt: int = -1) -> bytes:
        n = min(2, len(self._data) if amt < 0 else amt)
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _opener(data: bytes, final_url: str | None = None):
    calls: list[str] = []

    def open_(url: str, timeout: float) -> FakeResponse:
        calls.append(url)
        return FakeResponse(data, final_url or url)

    open_.calls = calls  # type: ignore[attr-defined]
    return open_


# --- the committed manifest -------------------------------------------------------------


def test_committed_manifest_is_valid(conf):
    external = conf.load_external()
    vendored = conf.load_vendored()
    keys = [e.key for e in [*vendored, *external]]
    assert len(keys) == len(set(keys))
    assert external, "corpus.toml lists no external files"
    for entry in external:
        assert entry.url.startswith("https://raw.githubusercontent.com/")
        assert ".cache" in entry.path.parts


def test_expected_covers_exactly_the_corpus(conf):
    keys = {e.key for e in conf.with_profiles([*conf.load_vendored(), *conf.load_external()])}
    expected = tomllib.loads(conf.EXPECTED.read_text(encoding="utf-8"))
    assert set(expected) == keys


def test_vendored_outcomes_match_expected(conf):
    """Every corpus file on disk gives its expected outcome.

    External files are checked only if fetched; the CI conformance job fetches them.
    """
    entries = conf.with_profiles([*conf.load_vendored(), *conf.load_external()])
    problems = conf.check(entries, conf.EXPECTED, allow_missing=True)
    assert problems == [], "\n".join(problems)


def test_committed_profiles_name_corpus_files(conf):
    base = [*conf.load_vendored(), *conf.load_external()]
    extra = conf.with_profiles(base)[len(base) :]
    assert extra, "corpus.toml [profiles] is empty"
    for entry in extra:
        key, _, profile = entry.key.rpartition("@")
        assert entry.diggs_version == profile
        assert entry.path == next(e.path for e in base if e.key == key)


# --- manifest validation ----------------------------------------------------------------


def test_with_profiles_adds_one_entry_per_profile(conf, tmp_path):
    base = _small_corpus(conf)
    key = base[0].key
    corpus = _manifest(tmp_path, f'[profiles]\n"{key}" = ["3.1-dev", "2.6"]\n')
    entries = conf.with_profiles(base, corpus)
    assert entries[: len(base)] == base
    extra = entries[len(base) :]
    assert [(e.key, e.diggs_version) for e in extra] == [
        (f"{key}@3.1-dev", "3.1-dev"),
        (f"{key}@2.6", "2.6"),
    ]
    assert {e.path for e in extra} == {base[0].path}
    assert conf.with_profiles(base, _manifest(tmp_path, "")) == base


@pytest.mark.parametrize(
    "body",
    [
        '[profiles]\n"official/nope.xml" = ["3.1-dev"]\n',  # not a corpus file
        '[profiles]\n"{key}" = []\n',
        '[profiles]\n"{key}" = "3.1-dev"\n',
        '[profiles]\n"{key}" = ["../x"]\n',
        '[profiles]\n"{key}" = [3]\n',
        'profiles = "3.1-dev"\n',
    ],
)
def test_with_profiles_rejects_bad_tables(conf, tmp_path, body):
    base = _small_corpus(conf)
    corpus = _manifest(tmp_path, body.replace("{key}", base[0].key))
    with pytest.raises(conf.ManifestError):
        conf.with_profiles(base, corpus)


def test_load_external_builds_pinned_url(conf, tmp_path):
    (entry,) = conf.load_external(_manifest(tmp_path, GOOD), tmp_path / "cache")
    assert entry.url == f"https://raw.githubusercontent.com/example/demo/{COMMIT}/dir/a%20b.xml"
    assert entry.path == tmp_path / "cache" / "demo" / "a.xml"
    assert entry.size == 5


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (COMMIT, "main"),
        ("https://github.com/example/demo", "https://bitbucket.org/example/demo"),
        ("https://github.com/example/demo", "https://gitlab.com/example/../demo"),
        ("https://github.com/example/demo", "https://gitlab.com/example/-/demo"),
        ("https://github.com/example/demo", "https://gitlab.com/demo"),
        ('path = "dir/a b.xml"', 'path = "../a.xml"'),
        ('[files."demo/a.xml"]', '[files."other/a.xml"]'),
        ("size = 5", "size = 999999999"),
        ("size = 5", 'size = "5"'),
        (SHA_A, "ABC"),
        ('license = "MIT"\n', ""),
    ],
    ids=[
        "branch-not-commit",
        "not-github-or-gitlab",
        "gitlab-dotdot",
        "gitlab-dash-segment",
        "gitlab-no-namespace",
        "dotdot",
        "unknown-source",
        "too-big",
        "size-str",
        "bad-sha",
        "no-license",
    ],
)
def test_load_external_rejects_bad_manifests(conf, tmp_path, old, new):
    assert old in GOOD
    with pytest.raises(conf.ManifestError):
        conf.load_external(_manifest(tmp_path, GOOD.replace(old, new)), tmp_path)


def test_load_external_builds_pinned_gitlab_url(conf, tmp_path):
    body = GOOD.replace(
        "https://github.com/example/demo", "https://gitlab.com/group/sub-group/demo"
    )
    (entry,) = conf.load_external(_manifest(tmp_path, body), tmp_path / "cache")
    assert entry.url == f"https://gitlab.com/group/sub-group/demo/-/raw/{COMMIT}/dir/a%20b.xml"
    conf._check_url(entry.url)  # an allowed host


def test_digest_ignores_crlf(conf):
    assert conf.digest(b"<a/>\r\n") == conf.digest(b"<a/>\n") == SHA_A


# --- fetch ------------------------------------------------------------------------------


def _entry(conf, tmp_path, **kw):
    fields = {
        "key": "demo/a.xml",
        "path": tmp_path / "cache" / "a.xml",
        "sha256": SHA_A,
        "url": "https://raw.githubusercontent.com/example/demo/x/a.xml",
        "size": 5,
    } | kw
    return conf.Entry(**fields)


def test_fetch_downloads_in_short_reads_then_skips(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    opener = _opener(b"<a/>\n")
    assert conf.fetch([entry], opener) == ["demo/a.xml"]
    assert entry.path.read_bytes() == b"<a/>\n"
    assert conf.fetch([entry], opener) == []
    assert len(opener.calls) == 1


def test_fetch_refuses_oversize(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    with pytest.raises(conf.CorpusError, match="more than 5 bytes"):
        conf.fetch([entry], _opener(b"<a/>\n" * 10))
    assert not entry.path.exists()


def test_fetch_refuses_digest_mismatch(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    with pytest.raises(conf.CorpusError, match="sha256"):
        conf.fetch([entry], _opener(b"<b/>\n"))
    assert not entry.path.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://raw.githubusercontent.com/a",
        "http://gitlab.com/a",
        "https://example.com/a",
        "https://gitlab.com.example.org/a",
        "file:///etc/passwd",
    ],
)
def test_fetch_refuses_disallowed_urls(conf, tmp_path, url):
    opener = _opener(b"<a/>\n")
    with pytest.raises(conf.ManifestError):
        conf.fetch([_entry(conf, tmp_path, url=url)], opener)
    assert opener.calls == []


def test_fetch_refuses_redirect_off_the_allowed_host(conf, tmp_path):
    with pytest.raises(conf.ManifestError):
        conf.fetch([_entry(conf, tmp_path)], _opener(b"<a/>\n", "https://evil.example/a"))


def test_fetch_refuses_short_download_even_if_digest_matches(conf, tmp_path):
    # b"<a/>\n" has the normalised digest of b"<a/>\r\n" but one byte fewer.
    entry = _entry(conf, tmp_path, size=6)
    opener = _opener(b"<a/>\n")
    sleeps: list[float] = []
    with pytest.raises(conf.CorpusError, match=r"is 5 bytes, .*\(after 3 attempts\)"):
        conf.fetch([entry], opener, sleep=sleeps.append)
    assert not entry.path.exists()
    assert len(opener.calls) == 3
    assert sleeps == [2.0, 4.0]


def test_fetch_retries_a_truncated_download_then_succeeds(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    bodies = [b"<a", b"<a/>\n"]

    def opener(url, timeout):
        return FakeResponse(bodies.pop(0), url)

    assert conf.fetch([entry], opener, sleep=lambda s: None) == ["demo/a.xml"]
    assert entry.path.read_bytes() == b"<a/>\n"


def test_fetch_retries_network_errors(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise TimeoutError("timed out")
        return FakeResponse(b"<a/>\n", url)

    assert conf.fetch([entry], opener, sleep=lambda s: None) == ["demo/a.xml"]
    assert len(calls) == 3


def test_fetch_does_not_retry_a_wrong_digest(conf, tmp_path):
    opener = _opener(b"<b/>\n")
    with pytest.raises(conf.CorpusError, match="sha256"):
        conf.fetch([_entry(conf, tmp_path)], opener, sleep=lambda s: None)
    assert len(opener.calls) == 1


@pytest.mark.parametrize("target", ["https://evil.example/a", "http://raw.githubusercontent.com/a"])
def test_redirects_are_checked_before_they_are_followed(conf, target):
    import urllib.request

    handler = conf._CheckedRedirectHandler()
    req = urllib.request.Request("https://raw.githubusercontent.com/example/demo/x/a.xml")
    with pytest.raises(conf.ManifestError):
        handler.redirect_request(req, None, 302, "Found", {}, target)


def test_redirect_within_the_allowed_host_is_followed(conf):
    import urllib.request

    handler = conf._CheckedRedirectHandler()
    req = urllib.request.Request("https://raw.githubusercontent.com/example/demo/x/a.xml")
    new = handler.redirect_request(
        req, None, 302, "Found", {}, "https://raw.githubusercontent.com/example/demo/y/a.xml"
    )
    assert new.full_url.endswith("/y/a.xml")


# --- record and check -------------------------------------------------------------------


def _small_corpus(conf):
    return [e for e in conf.load_vendored() if e.key.startswith("official/3.0/")][:2]


def test_record_then_check_round_trips(conf, tmp_path):
    entries = _small_corpus(conf)
    expected = tmp_path / "expected.toml"
    assert conf.record(entries, expected) == 2
    assert conf.check(entries, expected) == []
    text = expected.read_text(encoding="utf-8")
    conf.record(entries, expected)
    assert expected.read_text(encoding="utf-8") == text  # stable output


def test_check_reports_changed_missing_and_stale_entries(conf, tmp_path):
    entries = _small_corpus(conf)
    expected = tmp_path / "expected.toml"
    conf.record(entries, expected)
    data = tomllib.loads(expected.read_text(encoding="utf-8"))
    first, second = (e.key for e in entries)
    data[first]["warnings"] += 1
    del data[second]
    data["official/gone.xml"] = {"ok": True}
    expected.write_text(conf.render_expected(data), encoding="utf-8")
    problems = conf.check(entries, expected)
    assert any(p.startswith(f"{first}: warnings expected") for p in problems)
    assert f"{second}: no expected outcome (run record and review)" in problems
    assert "official/gone.xml: in expected.toml but not in the corpus" in problems


def test_check_missing_external_file(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    expected = tmp_path / "expected.toml"
    expected.write_text("", encoding="utf-8")
    with pytest.raises(conf.CorpusError, match="missing"):
        conf.check([entry], expected)
    assert conf.check([entry], expected, allow_missing=True) == []


def test_allow_missing_does_not_hide_a_corrupt_external_file(conf, tmp_path):
    entry = _entry(conf, tmp_path)
    entry.path.parent.mkdir(parents=True)
    entry.path.write_bytes(b"<tampered/>\n")
    expected = tmp_path / "expected.toml"
    expected.write_text("", encoding="utf-8")
    with pytest.raises(conf.CorpusError, match="missing or corrupt"):
        conf.check([entry], expected, allow_missing=True)


def test_allow_missing_does_not_hide_a_missing_vendored_file(conf, tmp_path):
    entry = conf.Entry("official/gone.xml", tmp_path / "gone.xml", SHA_A)
    expected = tmp_path / "expected.toml"
    expected.write_text("", encoding="utf-8")
    with pytest.raises(conf.CorpusError, match="missing or corrupt"):
        conf.check([entry], expected, allow_missing=True)


# --- command line -----------------------------------------------------------------------


def test_main_exit_codes(conf, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(conf, "CORPUS", _manifest(tmp_path, GOOD.replace(COMMIT, "main")))
    assert conf.main(["check"]) == 2
    assert "commit" in capsys.readouterr().err

    monkeypatch.setattr(conf, "CORPUS", _manifest(tmp_path, GOOD))
    monkeypatch.setattr(conf, "CACHE", tmp_path / "cache")
    assert conf.main(["check"]) == 1  # demo/a.xml has not been fetched
    assert "missing or corrupt: demo/a.xml" in capsys.readouterr().err


@pytest.mark.parametrize("which", ["CORPUS", "EXPECTED"])
def test_main_malformed_toml_exits_2(conf, tmp_path, monkeypatch, capsys, which):
    broken = tmp_path / "broken.toml"
    broken.write_text("[sources.demo\n", encoding="utf-8")
    monkeypatch.setattr(conf, which, broken)
    monkeypatch.setattr(conf, "CACHE", tmp_path / "cache")
    if which == "EXPECTED":
        monkeypatch.setattr(conf, "CORPUS", _manifest(tmp_path, ""))
    assert conf.main(["check"]) == 2
    assert "broken.toml" in capsys.readouterr().err


# --- the reader census (record/check --model) -------------------------------------------

_CENSUS_DOC = """<Diggs xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2"
 xmlns:xlink="http://www.w3.org/1999/xlink" gml:id="d">
  <project><Project gml:id="p1"/></project>
  <project xlink:href="other.xml#p9"/>
  <samplingFeature><Borehole gml:id="b1"><projectRef xlink:href="#p1"/></Borehole></samplingFeature>
  <samplingFeature><Borehole gml:id="b1"/></samplingFeature>
  <!-- a comment is not an element -->
  <measurement><Test gml:id="t1">
    <samplingFeatureRef xlink:href="#missing"/>
    <procedure><AtterbergLimitsTest gml:id="a1"/></procedure>
    <outcome><TestResult gml:id="r1"><results><ResultSet>
      <parameters/>
      <dataValues cs=";" ts="|">1;2;3|4;;6</dataValues>
    </ResultSet></results></TestResult></outcome></Test>
  </measurement>
</Diggs>
"""


def _census_of(conf, tmp_path, text: str) -> dict:
    path = tmp_path / "doc.xml"
    path.write_text(text, encoding="utf-8")
    return conf.census(conf.Entry("demo/doc.xml", path, SHA_A))


def test_census_counts_members_links_and_tables(conf, tmp_path):
    got = _census_of(conf, tmp_path, _CENSUS_DOC)
    assert got["readable"] is True
    assert got["diggs_version"] == "3.0.0"
    assert got["members"] == {
        "measurement/Test": 1,
        "project/-": 1,
        "project/Project": 1,
        "samplingFeature/Borehole": 2,
    }
    assert got["procedures"] == {"AtterbergLimitsTest": 1}
    assert got["links"] == {"project": 2, "samplingFeature": 1}
    assert (got["links_unresolved"], got["links_remote"]) == (1, 1)
    assert (got["gml_ids"], got["duplicate_gml_ids"]) == (6, 1)
    assert (got["result_tables"], got["result_rows"], got["result_cells"]) == (1, 2, 6)
    assert got["elements"] == 20  # the comment is not counted


@pytest.mark.parametrize(
    ("text", "kind"),
    [("not xml", "malformed"), ("<html/>", "not-diggs")],
)
def test_census_records_what_a_reader_must_refuse(conf, tmp_path, text, kind):
    assert _census_of(conf, tmp_path, text) == {"readable": False, "document_error": kind}


@pytest.mark.parametrize(
    ("text", "ts", "cs", "shape"),
    [
        ("1,2,3 4,5,6", " ", ",", (2, 6)),
        ("\n  1,,3\n\t4,5\n", " ", ",", (2, 5)),
        ("a;b|c;d|", "|", ";", (2, 4)),
        (None, " ", ",", (0, 0)),
        ("  ", " ", ",", (0, 0)),
    ],
)
def test_table_shape(conf, text, ts, cs, shape):
    assert conf.table_shape(text, ts, cs) == shape


def test_model_covers_exactly_the_corpus_without_profiles(conf):
    keys = {e.key for e in [*conf.load_vendored(), *conf.load_external()]}
    model = tomllib.loads(conf.MODEL.read_text(encoding="utf-8"))
    assert set(model) == keys


def test_vendored_census_matches_model(conf):
    entries = [*conf.load_vendored(), *conf.load_external()]
    problems = conf.check(entries, conf.MODEL, allow_missing=True, measure=conf.census)
    assert problems == [], "\n".join(problems)


def test_census_record_then_check_round_trips(conf, tmp_path):
    entries = _small_corpus(conf)
    model = tmp_path / "model.toml"
    assert conf.record(entries, model, conf.census) == 2
    text = model.read_text(encoding="utf-8")
    assert text.startswith("# What a reader must produce")
    assert conf.check(entries, model, measure=conf.census) == []
    data = tomllib.loads(text)
    first = entries[0].key
    data[first]["members"]["samplingFeature/Borehole"] = 99
    model.write_text(conf.render_expected(data), encoding="utf-8")
    problems = conf.check(entries, model, measure=conf.census)
    assert [p.split(":")[0] for p in problems] == [first]
    assert "members expected" in problems[0]


def test_main_model_flag_uses_model_toml(conf, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(conf, "CORPUS", _manifest(tmp_path, ""))
    monkeypatch.setattr(conf, "MODEL", tmp_path / "model.toml")
    assert conf.main(["record", "--model"]) == 0
    assert "model.toml" in capsys.readouterr().out
    assert conf.main(["check", "--model"]) == 0
    assert "0 difference(s)" in capsys.readouterr().out


# --- the AGS4 set (--set ags4) ----------------------------------------------------------

AGS4_BODY = b'"GROUP","PROJ"\r\n"HEADING","PROJ_ID"\r\n"UNIT",""\r\n"TYPE","ID"\r\n"DATA","P1"\r\n'


def test_committed_ags4_manifest_is_valid(conf):
    external = conf.load_external(conf.AGS4_CORPUS, conf.AGS4_CACHE)
    assert external, "ags4-corpus.toml lists no external files"
    keys = [e.key for e in external]
    assert len(keys) == len(set(keys))
    data = tomllib.loads(conf.AGS4_CORPUS.read_text(encoding="utf-8"))
    for name, source in data["sources"].items():
        # Q1: every source records its licence, where it was read, and the attribution.
        for field in ("license", "license_url", "license_text", "license_source", "attribution"):
            assert source.get(field), f"{name}: {field}"
        assert source["commit"] in source["license_source"], "licence read at the pinned commit"
    for entry in external:
        assert entry.path.is_relative_to(conf.AGS4_CACHE)
        assert entry.url.startswith("https://raw.githubusercontent.com/")
        # Q1: no AGS data dictionary and no AGS example file until AGS agrees in writing.
        lowered = entry.url.lower()
        assert "dictionary" not in lowered
        assert "example" not in lowered
        assert "ags.org.uk" not in lowered


def test_ags4_sets_do_not_overlap_the_diggs_corpus(conf):
    diggs = {e.key for e in conf.load_external()}
    ags4 = {e.key for e in conf.load_external(conf.AGS4_CORPUS, conf.AGS4_CACHE)}
    assert not diggs & ags4
    assert conf.AGS4_CACHE != conf.CACHE  # an AGS4 source name cannot shadow a DIGGS file


def test_committed_ags4_oracle_covers_exactly_the_set(conf):
    """ags4-expected.toml has one record per AGS4 file, at the bytes in the repository.

    External files are compared only if fetched; the oracle is re-recorded when the set
    or a fixture changes (python scripts/ags4_oracle.py, see conformance/README.md).
    """
    entries = [
        *conf.load_external(conf.AGS4_CORPUS, conf.AGS4_CACHE),
        *conf.ags4_fixtures(conf.AGS4_FIXTURES),
    ]
    problems = conf.check_ags4(entries, conf.AGS4_EXPECTED, allow_missing=True)
    assert problems == [], "\n".join(problems)


def test_ags4_fixtures_are_keyed_by_name(conf, tmp_path):
    (tmp_path / "b.ags").write_bytes(AGS4_BODY)
    (tmp_path / "a.ags").write_bytes(AGS4_BODY)
    (tmp_path / "notes.txt").write_text("not a fixture", encoding="utf-8")
    entries = conf.ags4_fixtures(tmp_path)
    assert [e.key for e in entries] == ["fixtures/a.ags", "fixtures/b.ags"]
    assert all(e.url is None and conf.is_present(e) for e in entries)
    assert conf.ags4_fixtures(tmp_path / "absent") == []


def _ags4_record(conf, entry, **changes) -> str:
    fields = {
        "key": entry.key,
        "path": conf._rel(entry.path),
        "sha256": conf.raw_sha256(entry.path),
    } | changes
    return "[[file]]\n" + "".join(f'{k} = "{v}"\n' for k, v in fields.items())


def test_check_ags4_round_trip_and_differences(conf, tmp_path):
    (tmp_path / "a.ags").write_bytes(AGS4_BODY)
    (tmp_path / "b.ags").write_bytes(AGS4_BODY.replace(b"P1", b"P2"))
    a, b = conf.ags4_fixtures(tmp_path)
    expected = tmp_path / "ags4-expected.toml"
    expected.write_text(_ags4_record(conf, a) + _ags4_record(conf, b), encoding="utf-8")
    assert conf.check_ags4([a, b], expected) == []

    # A CRLF -> LF change keeps the manifest digest but changes what the oracle read.
    b.path.write_bytes(AGS4_BODY.replace(b"P1", b"P2").replace(b"\r\n", b"\n"))
    stale = _ags4_record(conf, a, key="fixtures/gone.ags")
    expected.write_text(_ags4_record(conf, b) + stale, encoding="utf-8")
    problems = conf.check_ags4([a, b], expected)
    assert problems[0] == "fixtures/gone.ags: in ags4-expected.toml but not in the AGS4 set"
    assert problems[1].startswith("fixtures/a.ags: no oracle record (run: uv run --isolated")
    assert len(problems) == 2  # b's record was written from its current bytes

    expected.write_text(_ags4_record(conf, a, sha256="0" * 64), encoding="utf-8")
    (problem,) = conf.check_ags4([a], expected)
    assert problem.startswith("fixtures/a.ags: changed since the oracle was recorded")

    expected.write_text(_ags4_record(conf, a, path="elsewhere/a.ags"), encoding="utf-8")
    (problem,) = conf.check_ags4([a], expected)
    assert "path expected" in problem


@pytest.mark.parametrize("body", ['file = "x"\n', "[[file]]\npath = 'a'\n", "[file]\nkey = 'a'\n"])
def test_check_ags4_rejects_a_malformed_record(conf, tmp_path, body):
    expected = tmp_path / "ags4-expected.toml"
    expected.write_text(body, encoding="utf-8")
    with pytest.raises(conf.ManifestError, match=r"\[\[file\]\]"):
        conf.check_ags4([], expected)


def test_check_ags4_missing_external_file(conf, tmp_path):
    entry = _entry(conf, tmp_path, key="bgs/a.ags")
    expected = tmp_path / "ags4-expected.toml"
    expected.write_text("", encoding="utf-8")
    with pytest.raises(conf.CorpusError, match="missing"):
        conf.check_ags4([entry], expected)
    assert conf.check_ags4([entry], expected, allow_missing=True) == []


def _ags4_paths(conf, tmp_path, monkeypatch, corpus_body: str = ""):
    monkeypatch.setattr(conf, "AGS4_CORPUS", _manifest(tmp_path, corpus_body))
    monkeypatch.setattr(conf, "AGS4_CACHE", tmp_path / "cache")
    monkeypatch.setattr(conf, "AGS4_FIXTURES", tmp_path / "fixtures")
    monkeypatch.setattr(conf, "AGS4_EXPECTED", tmp_path / "ags4-expected.toml")
    # The DIGGS set must not be read at all for --set ags4.
    monkeypatch.setattr(conf, "CORPUS", tmp_path / "missing-corpus.toml")


def test_main_set_ags4(conf, tmp_path, monkeypatch, capsys):
    _ags4_paths(conf, tmp_path, monkeypatch)
    assert conf.main(["fetch", "--set", "ags4"]) == 0
    assert "0 external AGS4 file(s) present" in capsys.readouterr().out

    assert conf.main(["record", "--set", "ags4"]) == 2
    assert "scripts/ags4_oracle.py record" in capsys.readouterr().err

    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "a.ags").write_bytes(AGS4_BODY)
    conf.AGS4_EXPECTED.write_text("", encoding="utf-8")
    assert conf.main(["check", "--set", "ags4"]) == 1
    assert "fixtures/a.ags: no oracle record" in capsys.readouterr().out
    (entry,) = conf.ags4_fixtures(tmp_path / "fixtures")
    conf.AGS4_EXPECTED.write_text(_ags4_record(conf, entry), encoding="utf-8")
    assert conf.main(["check", "--set", "ags4"]) == 0
    assert "1 AGS4 file(s), 0 difference(s)" in capsys.readouterr().out


def test_main_set_ags4_fetches_its_own_manifest(conf, tmp_path, monkeypatch, capsys):
    body = GOOD.replace('"demo/a.xml"', '"demo/a.ags"')
    _ags4_paths(conf, tmp_path, monkeypatch, body)
    calls = []

    def fake_fetch(entries, *args, **kwargs):
        calls.extend(entries)
        return [e.key for e in entries]

    monkeypatch.setattr(conf, "fetch", fake_fetch)
    assert conf.main(["fetch", "--set", "ags4"]) == 0
    assert [e.path for e in calls] == [tmp_path / "cache" / "demo" / "a.ags"]
    assert "fetched demo/a.ags" in capsys.readouterr().out


def test_main_set_ags4_bad_manifest_exits_2(conf, tmp_path, monkeypatch, capsys):
    _ags4_paths(conf, tmp_path, monkeypatch, GOOD.replace(COMMIT, "main"))
    assert conf.main(["fetch", "--set", "ags4"]) == 2
    assert "commit" in capsys.readouterr().err


def test_main_model_is_not_for_the_ags4_set(conf, capsys):
    with pytest.raises(SystemExit) as exc:
        conf.main(["check", "--set", "ags4", "--model"])
    assert exc.value.code == 2
    assert "--model is for the DIGGS corpus" in capsys.readouterr().err
