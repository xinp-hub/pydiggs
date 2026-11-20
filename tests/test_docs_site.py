"""The assembled documentation site serves every schema at the URL its ``$id`` names,
and every page at its unversioned address too."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("docs_site", ROOT / "scripts" / "docs_site.py")
assert _SPEC is not None and _SPEC.loader is not None
docs_site = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(docs_site)
_HOOKS_SPEC = importlib.util.spec_from_file_location(
    "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
)
assert _HOOKS_SPEC is not None and _HOOKS_SPEC.loader is not None
mkdocs_hooks = importlib.util.module_from_spec(_HOOKS_SPEC)
_HOOKS_SPEC.loader.exec_module(mkdocs_hooks)

SITE_ROOT = "https://xinp-hub.github.io/pydiggs/"


def _tree(site: Path, name: str, schemas: dict[str, str]) -> None:
    (site / name).mkdir(parents=True)
    (site / name / "index.html").write_text("<html></html>", encoding="utf-8")
    for rel, text in schemas.items():
        path = site / name / "schemas" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_schemas_are_served_at_the_root_released_first_then_unreleased(tmp_path: Path) -> None:
    _tree(tmp_path, "latest", {"a.schema.json": "released", "old.schema.json": "old"})
    _tree(
        tmp_path,
        "dev",
        {"a.schema.json": "changed on master", "preview/new.schema.json": "new"},
    )
    docs_site.assemble(tmp_path, "1.12.0")
    root = tmp_path / "schemas"
    assert (root / "a.schema.json").read_text(encoding="utf-8") == "released"
    assert (root / "old.schema.json").read_text(encoding="utf-8") == "old"
    assert (root / "preview" / "new.schema.json").read_text(encoding="utf-8") == "new"


def test_before_any_release_the_root_serves_the_schemas_of_dev(tmp_path: Path) -> None:
    _tree(tmp_path, "dev", {"a.schema.json": "dev"})
    docs_site.assemble(tmp_path)
    assert (tmp_path / "schemas" / "a.schema.json").read_text(encoding="utf-8") == "dev"


@pytest.mark.parametrize(
    "schema",
    sorted((ROOT / "src" / "pydiggs").rglob("*.schema.json")),
    ids=lambda p: p.name,
)
def test_every_packaged_schema_names_a_url_at_the_site_root(schema: Path) -> None:
    schema_id = json.loads(schema.read_text(encoding="utf-8")).get("$id", "")
    if not schema_id.startswith(SITE_ROOT):
        pytest.skip("not published on the documentation site")
    rel = schema_id.removeprefix(SITE_ROOT)
    assert rel.startswith("schemas/"), schema_id
    assert not rel.startswith(("latest/", "dev/")), schema_id
    # The docs build publishes it at exactly that path (and the site root keeps it there).
    published = {
        source.resolve(): path.as_posix() for source, path in mkdocs_hooks.DOCUMENTS.items()
    }
    assert published.get(schema.resolve()) == rel, f"{schema.name} is not published at {rel}"


# --- Unversioned page addresses -----------------------------------------------------------

#: Every address under the site that pyDIGGS writes: problem types, the SARIF helpUri,
#: schema ids, docstrings and help texts. A trailing "." is the end of a sentence.
EMITTED = re.compile(r"https://xinp-hub\.github\.io/pydiggs/[A-Za-z0-9._~/#?=&%-]*")


def _pages(site: Path, tree: str, pages: list[str]) -> None:
    """Build ``site/tree`` with a directory-URL page for each of ``pages`` ("" is home)."""
    for page in ["", *pages]:
        folder = site / tree / page
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(f"<html>{tree}/{page}</html>", encoding="utf-8")
    for name in ("404.html", "sitemap.xml", "search/search_index.json", "assets/x/index.html"):
        (site / tree / name).parent.mkdir(parents=True, exist_ok=True)
        (site / tree / name).write_text("x", encoding="utf-8")


def _target(stub: Path) -> str:
    text = stub.read_text(encoding="utf-8")
    found = re.search(r'<link rel="canonical" href="([^"]+)">', text)
    assert found, text
    return found.group(1)


def _resolves(site: Path, stub_dir: Path) -> Path:
    """The file the stub at ``stub_dir`` leads to, following its relative target."""
    return (stub_dir / _target(stub_dir / "index.html") / "index.html").resolve()


def test_every_page_gets_a_stub_that_keeps_the_query_and_fragment(tmp_path: Path) -> None:
    for tree in ("dev", "latest"):
        _pages(tmp_path, tree, ["http-api", "findings", "reference/python/api"])
    docs_site.assemble(tmp_path, "1.14.0")
    assert _target(tmp_path / "http-api" / "index.html") == "../latest/http-api/"
    assert (
        _target(tmp_path / "reference" / "python" / "api" / "index.html")
        == "../../../latest/reference/python/api/"
    )
    # reference/ itself is no page (no index.html in latest/), so it gets no stub.
    assert not (tmp_path / "reference" / "index.html").exists()
    stub = (tmp_path / "http-api" / "index.html").read_text(encoding="utf-8")
    assert (
        '<script>location.replace("../latest/http-api/" + location.search + location.hash);' in stub
    )
    assert '<meta name="robots" content="noindex">' in stub
    assert '<meta http-equiv="refresh" content="0; url=../latest/http-api/">' in stub
    assert '<a href="../latest/http-api/">' in stub
    for page in ("http-api", "reference/python/api"):
        assert (
            _resolves(tmp_path, tmp_path / page)
            == (tmp_path / "latest" / page / "index.html").resolve()
        )
    # Assets, search and the sitemap are not pages.
    assert not (tmp_path / "assets").exists()
    assert not (tmp_path / "search").exists()
    assert not (tmp_path / "sitemap.xml").exists()


def test_before_any_release_the_stubs_lead_to_dev(tmp_path: Path) -> None:
    _pages(tmp_path, "dev", ["http-api", "a/b"])
    docs_site.assemble(tmp_path)
    assert _target(tmp_path / "http-api" / "index.html") == "../dev/http-api/"
    assert _target(tmp_path / "a" / "b" / "index.html") == "../../dev/a/b/"
    assert '"dev"' in (tmp_path / "404.html").read_text(encoding="utf-8")


def test_a_page_named_like_a_root_entry_is_skipped_not_written_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    taken = ["dev", "latest", "1.14.0", "schemas", "versions.json", "llms-full.txt"]
    _pages(tmp_path, "dev", [])
    _pages(tmp_path, "latest", [*taken, "dev/inner", "ok"])
    (tmp_path / "latest" / "schemas" / "s.schema.json").write_text("{}", encoding="utf-8")
    (tmp_path / "latest" / "llms.txt").write_text("llms", encoding="utf-8")
    (tmp_path / "404.html").write_text("mine", encoding="utf-8")
    (tmp_path / "keep-me").mkdir()
    docs_site.assemble(tmp_path, "1.14.0")
    assert (tmp_path / "dev" / "index.html").read_text(encoding="utf-8") == "<html>dev/</html>"
    assert not (tmp_path / "dev" / "inner").exists()
    assert "url=../latest/" in (tmp_path / "1.14.0" / "index.html").read_text(encoding="utf-8")
    # schemas/ at the root is the copy of latest/schemas/, not a stub.
    assert "Redirecting" not in (tmp_path / "schemas" / "index.html").read_text("utf-8")
    assert (tmp_path / "versions.json").is_file()
    assert (tmp_path / "llms.txt").read_text(encoding="utf-8") == "llms"
    assert (tmp_path / "404.html").read_text(encoding="utf-8") == "mine"
    assert "url=latest/" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert _target(tmp_path / "ok" / "index.html") == "../latest/ok/"
    assert not (tmp_path / "llms-full.txt").exists()
    err = capsys.readouterr().err
    for name in taken:
        assert f"no redirect for {name}/" in err


_KEEP = ["404.html", "assets", "dev", "latest", "1.14.0", "schemas", "search"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/pydiggs/http-api/", "/pydiggs/latest/http-api/"),
        ("/pydiggs/new/nested/page/", "/pydiggs/latest/new/nested/page/"),
        ("/pydiggs/latest/gone/", None),  # missing in latest/ too: no loop
        ("/pydiggs/dev/gone/", None),
        ("/pydiggs/1.14.0/gone/", None),
        ("/pydiggs//evil.example/", None),
        ("/pydiggs/\\evil.example/", None),
        ("/pydiggs/%2e%2e/x/", None),
        ("/pydiggs/a/../../x/", None),
        ("/pydiggs/x\n", None),
        ("/other/http-api/", None),
        ("//evil.example/pydiggs/", None),
    ],
)
def test_the_404_fallback_stays_under_the_site_base(path: str, expected: str | None) -> None:
    got = docs_site.fallback(path, "/pydiggs/", "latest", _KEEP)
    assert got == expected
    if got is not None:
        assert got.startswith("/pydiggs/latest/")
        assert not got.startswith("//")


def _run_404(page: str, pathname: str) -> str | None:
    """Run the 404 page's script in Node with ``location`` stubbed; where it went."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = re.search(r"<script>(.*?)</script>", page, re.DOTALL)
    assert script is not None
    harness = "\n".join(
        [
            f"var went = null, location = {{pathname: {json.dumps(pathname)},",
            "  search: '?a=1', hash: '#h', replace: function (u) { went = u; }};",
            script.group(1),
            "console.log(JSON.stringify(went));",
        ]
    )
    out = subprocess.run(  # noqa: S603 - node from PATH, running the page we just wrote
        [node, "-e", harness], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)  # type: ignore[no-any-return]


def test_the_404_page_script_follows_the_same_rule(tmp_path: Path) -> None:
    _pages(tmp_path, "dev", [])
    _pages(tmp_path, "latest", [])
    docs_site.assemble(tmp_path, "1.14.0", "https://example.test/pydiggs")
    page = (tmp_path / "404.html").read_text(encoding="utf-8")
    assert json.dumps(docs_site.SAFE_REST) in page
    assert '<a href="/pydiggs/latest/">' in page
    for path, expected in [
        ("/pydiggs/http-api/", "/pydiggs/latest/http-api/?a=1#h"),
        ("/pydiggs/latest/http-api/", None),
        ("/pydiggs/1.14.0/x/", None),
        ("/pydiggs//evil.example/", None),
        ("/pydiggs/\\evil.example/", None),
        ("/pydiggs/%2F%2Fevil/", None),
        ("/elsewhere/", None),
    ]:
        assert _run_404(page, path) == expected, path


@pytest.mark.parametrize("base", ["https://h.test/a b/", "https://h.test/<x>/", "https://h/%2F/"])
def test_an_unusual_base_url_is_refused(tmp_path: Path, base: str) -> None:
    _pages(tmp_path, "dev", [])
    with pytest.raises(ValueError, match="base path"):
        docs_site.assemble(tmp_path, base_url=base)


def _emitted() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "src").rglob("*"):
        if path.suffix in {".py", ".json", ".md", ".txt", ".html"} and path.is_file():
            found.update(m.rstrip(".") for m in EMITTED.findall(path.read_text("utf-8")))
    return found


def test_pydiggs_emits_unversioned_addresses() -> None:
    emitted = _emitted()
    assert docs_site.SITE_URL + "findings/#rule-ids" in emitted  # the SARIF helpUri
    assert docs_site.SITE_URL + "http-api/#problem-" in emitted  # RFC 9457 problem types


def test_every_address_pydiggs_emits_resolves_in_the_assembled_site(tmp_path: Path) -> None:
    pages = sorted(
        md.relative_to(ROOT / "docs").with_suffix("").as_posix()
        for md in (ROOT / "docs").rglob("*.md")
        if md.name != "index.md"
    )
    for tree in ("dev", "latest"):
        _pages(tmp_path, tree, pages)
        for published in mkdocs_hooks.DOCUMENTS.values():
            (tmp_path / tree / published).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / tree / published).write_text("{}", encoding="utf-8")
    docs_site.assemble(tmp_path, "1.14.0")
    urls = _emitted() | {docs_site.SITE_URL + "http-api/#problem-busy"}
    for url in sorted(urls):
        rel = re.split(r"[#?]", url.removeprefix(docs_site.SITE_URL), maxsplit=1)[0]
        if rel == "" or rel.endswith("/"):
            target = tmp_path / rel / "index.html"
            assert target.is_file(), url
            if rel:
                assert _resolves(tmp_path, tmp_path / rel).is_file(), url
        else:
            assert (tmp_path / rel).is_file(), url
