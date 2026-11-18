"""Tests that keep the documentation true to the code.

A page nobody re-reads drifts: an example stops running, a table lists an extra that was
renamed. These fail in CI instead. Docstring examples are run by ``test_packaging.py``.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import unittest
from pathlib import Path
from types import ModuleType

import pytest
from sybil import Sybil
from sybil.parsers.markdown import PythonCodeBlockParser, SkipParser

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

# ```python blocks are executed, top to bottom, sharing one namespace per file; a block
# written as an interpreter session (>>>) also has its output compared. To leave a block
# out, put `<!-- skip: next "why" -->` on the line above it: the opt-out is in the source,
# next to the block, where a reviewer sees it.
#
# pyDIGGS: the guides read the reader's own files by placeholder name ("site.xml", "site.ags",
# "BH-01.xml"). Each example runs in a scratch directory where those names are real files
# (``DOC_FILES``), so the example runs as written and anything it writes (``out.ags``,
# ``site.gpkg``) lands in the scratch directory, never in the checkout.
OFFICIAL = ROOT / "tests" / "fixtures" / "official" / "3.0" / "Borehole-CPT_Example_Annotated.xml"
AGS4 = ROOT / "tests" / "fixtures" / "ags4" / "convert_tier_a.ags"
SCHEMATRON = ROOT / "src" / "pydiggs" / "schematron" / "diggs_schematron_rules_2.6.sch"
XSD = ROOT / "src" / "pydiggs" / "schemas" / "diggs-schema-3.0.0" / "Diggs.xsd"
DOC_FILES: dict[str, Path] = {
    "site.xml": OFFICIAL,
    "site-rev1.xml": OFFICIAL,
    "site.ags": AGS4,
    "Standard_dictionary.ags": ROOT / "tests" / "fixtures" / "ags4" / "c2_dictionary.ags",
    "BH-01.xml": OFFICIAL,
    "site/BH-01.xml": OFFICIAL,
    "SF_Project_Lith_Test.xml": OFFICIAL,
    "path/to/your/diggs_file.xml": OFFICIAL,
    "path/to/schematron.sch": SCHEMATRON,
    "path/to/rules.sch": SCHEMATRON,
    "path/to/Diggs.xsd": XSD,
}


def _setup(namespace: dict[str, object], path: Path) -> None:
    """Names the guides use for "a document you received" and "another report"."""
    import pydiggs

    namespace["upload"] = (AGS4 if path.name == "ags4.md" else OFFICIAL).read_bytes()
    namespace["other"] = pydiggs.validate(str(OFFICIAL))


EXAMPLES = Sybil(parsers=[PythonCodeBlockParser(), SkipParser()], patterns=["*.md"])


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_pages_are_current():
    docs_gen = _script("docs_gen")
    if docs_gen.help_depends_on_interpreter():
        pytest.skip("--help text in the generated pages is captured on the oldest supported Python")
    assert docs_gen.stale() == [], "run `python scripts/docs_gen.py` and commit the result"


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_markdown_examples_run(path, tmp_path, monkeypatch):
    for name, source in DOC_FILES.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (tmp_path / "site-rev2.xml").write_text(
        OFFICIAL.read_text(encoding="utf-8").replace("160.00", "161.00", 1), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    document = EXAMPLES.parse(path)
    _setup(document.namespace, path)
    for example in document.examples():
        try:
            example.evaluate()
        except unittest.SkipTest:
            continue


def test_version_stamp_reads_the_source_not_an_installed_copy():
    hooks = _script("mkdocs_hooks")
    init = (ROOT / "src" / "pydiggs" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{hooks.package_version()}"' in init
    config = hooks.on_config({"extra": {}})
    assert config["extra"]["package_version"] == hooks.package_version()


def test_changelog_page_is_the_root_changelog_and_its_links_resolve():
    hooks = _script("mkdocs_hooks")
    assert (ROOT / "docs" / "history.md").read_text(encoding="utf-8").strip() == (
        '--8<-- "CHANGELOG.md"'
    )
    assert hooks.changelog_links("[x](docs/http-api.md) [y](#a)") == "[x](http-api.md) [y](#a)"
    embedded = hooks.embed_changelog(hooks.CHANGELOG_MARKER)
    assert embedded.startswith("# Changelog")
    assert "](docs/" not in embedded


def test_llms_documents_list_every_page_in_navigation_order():
    """llms.txt is written by the docs hook, not by a plugin (see scripts/mkdocs_hooks.py)."""
    hooks = _script("mkdocs_hooks")
    config = hooks.on_config(
        {
            "extra": {},
            "site_name": "pyDIGGS",
            "site_description": "d",
            "site_url": "https://e.test/dev/",
        }
    )
    hooks._ORDER[:] = ["index.md", "reference.md"]
    hooks._PAGES["reference.md"] = hooks.LlmsPage("Python API", "reference/", "::: pydiggs")
    hooks._PAGES["index.md"] = hooks.LlmsPage("Home", "", "# pyDIGGS\n\nText.")
    hooks._PAGES["extra.md"] = hooks.LlmsPage("Extra", "extra/", "Not in the navigation.")

    class _File:
        src_uri = "reference.md"

    class _Page:
        file = _File()

    html = '<h2 id="x">validate<a class="headerlink" href="#x">¶</a></h2><p>Checks &amp; reports.</p><script>x()</script>'
    assert hooks.on_page_content(html, page=_Page()) == html
    index, full = hooks.llms_documents(config)
    assert index.startswith("# pyDIGGS\n\n> d\n\nVersion " + hooks.package_version())
    links = [line for line in index.splitlines() if line.startswith("- [")]
    assert links == [
        "- [Home](https://e.test/dev/)",
        "- [Python API](https://e.test/dev/reference/)",
        "- [Extra](https://e.test/dev/extra/)",
    ]
    assert "## validate\n\nChecks & reports." in full
    assert ":::" not in full
    assert "x()" not in full
    assert full.index("Text.") < full.index("## validate") < full.index("Not in the navigation.")
    hooks.on_config(config)
    assert hooks._PAGES == {}


def test_dev_notice_links_to_the_site_root_from_every_page():
    """The dev/ notice link must work where base_url is absolute (404.html) as well."""
    override = (ROOT / "overrides" / "main.html").read_text(encoding="utf-8")
    assert "{{ base_url.rstrip('/') }}/../" in override
    assert "'../' ~ base_url" not in override
