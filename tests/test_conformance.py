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
    keys = {e.key for e in [*conf.load_vendored(), *conf.load_external()]}
    expected = tomllib.loads(conf.EXPECTED.read_text(encoding="utf-8"))
    assert set(expected) == keys


def test_vendored_outcomes_match_expected(conf):
    """Every corpus file on disk gives its expected outcome.

    External files are checked only if fetched; the CI conformance job fetches them.
    """
    entries = [*conf.load_vendored(), *conf.load_external()]
    problems = conf.check(entries, conf.EXPECTED, allow_missing=True)
    assert problems == [], "\n".join(problems)


# --- manifest validation ----------------------------------------------------------------


def test_load_external_builds_pinned_url(conf, tmp_path):
    (entry,) = conf.load_external(_manifest(tmp_path, GOOD), tmp_path / "cache")
    assert entry.url == f"https://raw.githubusercontent.com/example/demo/{COMMIT}/dir/a%20b.xml"
    assert entry.path == tmp_path / "cache" / "demo" / "a.xml"
    assert entry.size == 5


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (COMMIT, "main"),
        ("https://github.com/example/demo", "https://gitlab.com/example/demo"),
        ('path = "dir/a b.xml"', 'path = "../a.xml"'),
        ('[files."demo/a.xml"]', '[files."other/a.xml"]'),
        ("size = 5", "size = 999999999"),
        ("size = 5", 'size = "5"'),
        (SHA_A, "ABC"),
        ('license = "MIT"\n', ""),
    ],
    ids=[
        "branch-not-commit",
        "not-github",
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
    ["http://raw.githubusercontent.com/a", "https://example.com/a", "file:///etc/passwd"],
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
