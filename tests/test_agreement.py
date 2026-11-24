"""Cross-implementation agreement with the DIGGSml XSLT validator (scripts/agreement.py).

The validator itself runs only in CI's agreement workflow (it needs SaxonC-HE and the
validator's files); these tests check everything else offline: that every disagreement in
the committed records is explained, that the explanations are checked strictly, that the
run is offline by construction, and that the docs page is built from the records.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "agreement.py"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")


@pytest.fixture(scope="module")
def agr():
    spec = importlib.util.spec_from_file_location("agreement", _SCRIPT)
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


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# --- the committed records -------------------------------------------------------------


def test_every_disagreement_in_the_records_is_explained(agr):
    # The gate on every pull request: a change to conformance/expected.toml that makes a
    # new difference with the XSLT validator fails here until intended.toml says why.
    reasons, cited = agr.load_intended()
    problems = agr.explain(_toml(agr.EXPECTED), _toml(agr.XSLT_EXPECTED), reasons, cited)
    assert problems == []


def test_the_command_line_check_passes_offline(agr, capsys):
    assert agr.main(["check"]) == 0
    assert capsys.readouterr().out.strip().endswith("0 problem(s)")


def test_the_xslt_record_covers_the_corpus_and_names_no_local_path(agr):
    xslt = _toml(agr.XSLT_EXPECTED)
    corpus = {k for k in _toml(agr.EXPECTED) if "@" not in k}
    assert set(xslt) == corpus
    text = agr.XSLT_EXPECTED.read_text(encoding="utf-8")
    # The record is the same on every machine: no checkout path, no cache path.
    assert "/Users/" not in text
    assert "/home/" not in text
    assert ".cache" not in text
    for outcome in xslt.values():
        assert outcome["ok"] == (outcome["errors"] == 0 and "stopped" not in outcome)
        assert sum(outcome.get("error_kinds", {}).values()) == outcome["errors"]


# --- the validator manifest and the offline catalog ------------------------------------


def test_the_validator_manifest_pins_every_module_the_master_stylesheet_imports(agr):
    entries = agr.validator_entries()
    keys = {e.key for e in entries}
    assert all(e.url.startswith("https://raw.githubusercontent.com/DIGGSml/") for e in entries)
    assert f"validation/{agr.STYLESHEET}" in keys
    for module in (
        "diggs-functions",
        "diggs-check",
        "schema-check",
        "codeType-validation",
        "dictionary-validation",
        "schematron-validation",
        "geometry-validation",
        "crs-validation",
        "coordinate-validation",
        "deprecation-validation",
    ):
        assert f"validation/modules/{module}.xsl" in keys
    assert "validation/modules/diggs_schematron_rules.sch" in keys
    assert "validation/modules/schxslt_2.0/pipeline-for-svrl.xsl" in keys


def test_the_dictionaries_come_from_the_commit_pydiggs_bundles(agr):
    manifest = _toml(agr.MANIFEST)
    provenance = _toml(agr.PROVENANCE)
    assert manifest["sources"]["def"]["commit"] == provenance["sources"]["def"]["commit"]


def _resolve(catalog: Path, url: str) -> str:
    """Resolve ``url`` as an OASIS catalog does: exact uri entry, else longest prefix."""
    ns = {"c": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}
    root = ET.parse(catalog).getroot()  # noqa: S314 - written by agreement.write_catalog
    for entry in root.findall("c:uri", ns):
        if entry.get("name") == url:
            return str(entry.get("uri"))
    prefixes = [
        (e.get("uriStartString", ""), e.get("rewritePrefix", ""))
        for e in root.findall("c:rewriteURI", ns)
    ]
    start, prefix = max(
        ((s, p) for s, p in prefixes if url.startswith(s)), key=lambda sp: len(sp[0])
    )
    return prefix + url[len(start) :]


@pytest.fixture
def catalog(agr, tmp_path):
    mapping = agr.mirror_map(_toml(agr.MANIFEST), cache=tmp_path / "cache")
    return agr.write_catalog(mapping, tmp_path / "offline", tmp_path / "catalog.xml"), tmp_path


@pytest.mark.parametrize(
    "url",
    [
        "https://diggs.geosetta.org/api/units/classes/length",
        "https://diggs.geosetta.org/api/units/convert?sourceValue=1",
        "http://www.opengis.net/def/crs/EPSG/0/4326",
        "https://example.com/anything.xml",
    ],
)
def test_every_url_not_mirrored_goes_nowhere(catalog, url):
    # Nothing is fetched while the validator runs: Geosetta's units API above all
    # (owner decision: the agreement job is independent of Geosetta).
    path, tmp = catalog
    target = _resolve(path, url)
    assert target.startswith((tmp / "offline").resolve().as_uri() + "/")


def test_the_bundled_dictionaries_and_schemas_answer_their_published_urls(agr, catalog):
    path, _ = catalog
    package = agr.PACKAGE.resolve()
    cases = {
        "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml": (
            package / "dictionaries" / "properties.xml"
        ),
        "https://diggsml.org/def/units/DiggsUomDictionary.xml": (
            package / "dictionaries" / "DiggsUomDictionary.xml"
        ),
        "https://diggsml.org/schemas/3.0.0/Diggs.xsd": (
            package / "schemas" / "diggs-schema-3.0.0" / "Diggs.xsd"
        ),
        "https://diggsml.org/schemas/2.6/Diggs.xsd": (
            package / "schemas" / "diggs-schema-2.6" / "Diggs.xsd"
        ),
    }
    for url, local in cases.items():
        assert _resolve(path, url) == local.as_uri()
        assert local.is_file()


def test_every_package_mirror_exists(agr):
    for url, local in agr.mirror_map(_toml(agr.MANIFEST)).items():
        if agr.PACKAGE in local.parents or local == agr.PACKAGE:
            assert local.exists(), url
        assert "geosetta" not in url


def test_a_mirror_may_not_point_at_the_network(agr, tmp_path):
    bad = {"mirror": {"https://diggsml.org/x.xml": "https://elsewhere.example/x.xml"}}
    with pytest.raises(agr.AgreementError, match="cache:<key> or package:<path>"):
        agr.mirror_map(bad, cache=tmp_path)
    unsafe = {"mirror": {"https://diggsml.org/x.xml": "package:../../etc/passwd"}}
    with pytest.raises(Exception, match="unsafe path"):
        agr.mirror_map(unsafe, cache=tmp_path)


def test_the_requirements_pin_every_wheel_by_hash(agr):
    lines = [
        line.strip()
        for line in agr.REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines[0] == "saxonche==12.7.0 \\"
    hashes = lines[1:]
    assert hashes
    assert all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}( \\)?", h) for h in hashes)


def test_the_workflow_runs_offline_with_hashes_checked():
    text = (ROOT / ".github" / "workflows" / "agreement.yml").read_text(encoding="utf-8")
    # Both address families: with the IPv4 line alone the name still resolves over IPv6.
    assert '"0.0.0.0 diggs.geosetta.org" |' in text
    assert '":: diggs.geosetta.org" |' in text
    assert "uv pip sync --require-hashes" in text
    # uv run --with ignores the hashes of a requirements file.
    assert "--with-requirements" not in text
    assert "scripts/agreement.py check --run" in text


# --- summarising a run -----------------------------------------------------------------


def test_message_kinds_drop_values_but_keep_check_numbers(agr):
    a = agr.message_kind("CRS Validation", 'The resource at "http://a/1" could not be accessed.')
    b = agr.message_kind("CRS Validation", 'The resource at "http://b/22" could not be accessed.')
    assert a == b == 'CRS Validation: The resource at "…" could not be accessed.'
    assert agr.message_kind("Dictionary Validation", "Check 13: unit 3 m").startswith(
        "Dictionary Validation: Check 13: unit # m"
    )
    assert len(agr.message_kind("S", "x" * 500)) <= len("S: ") + agr.KIND_CHARS


def test_summarize_counts_by_severity(agr):
    out = agr.summarize(
        [
            "CRS Validation\tERROR\tbad 1",
            "CRS Validation\tERROR\tbad 2",
            "Dictionary Validation\tWARNING\tw",
            "Deprecation Validation\tINFO\ti",
        ]
    )
    assert out.to_record() == {
        "ok": False,
        "errors": 2,
        "warnings": 1,
        "infos": 1,
        "error_kinds": {"CRS Validation: bad #": 2},
    }
    stopped = agr.XsltOutcome(stopped="Error on line 1")
    assert stopped.ok is False
    assert stopped.to_record()["stopped"] == "Error on line 1"


def test_a_run_that_differs_from_the_record_is_reported(agr):
    fresh = {"a.xml": agr.summarize(["X\tERROR\tboom"]), "b.xml": agr.XsltOutcome()}
    recorded = {
        "a.xml": {"ok": True, "errors": 0, "warnings": 0, "infos": 0},
        "c.xml": {"ok": True, "errors": 0, "warnings": 0, "infos": 0},
    }
    problems = agr.compare_records(recorded, fresh)
    assert "c.xml: recorded but not run" in problems
    assert "b.xml: no record (run record and review)" in problems
    assert any(p.startswith("a.xml: ok recorded True, got False") for p in problems)
    assert any(p.startswith("a.xml: error_kinds recorded None") for p in problems)


# --- the explanations are strict -------------------------------------------------------

INTENDED = """
[reasons.crs]
title = "CRS"
side = "xslt"
kinds = ['^CRS Validation: ']
why = "Because."

[reasons.strict-schema]
title = "Schema"
side = "pydiggs"
rules = ["schema.cvc-*"]
why = "Because."
"""

PASS = {"ok": True, "errors": 0, "error_rules": []}
FAIL_SCHEMA = {"ok": False, "errors": 1, "error_rules": ["schema.cvc-complex-type.2.4"]}
X_OK = {"ok": True, "errors": 0}
X_CRS = {"ok": False, "errors": 1, "error_kinds": {"CRS Validation: bad": 1}}
X_CRS_AND_GEOM = {
    "ok": False,
    "errors": 2,
    "error_kinds": {"CRS Validation: bad": 1, "Geometry Validation: no srs": 1},
}


def _explain(agr, tmp_path, files: str, expected: dict, xslt: dict) -> list[str]:
    path = tmp_path / "intended.toml"
    path.write_text(INTENDED + files, encoding="utf-8")
    reasons, cited = agr.load_intended(path)
    return agr.explain(expected, xslt, reasons, cited)


def test_explained_disagreements_pass(agr, tmp_path):
    files = '[files."a.xml"]\nreasons = ["crs"]\n[files."b.xml"]\nreasons = ["strict-schema"]\n'
    expected = {"a.xml": PASS, "b.xml": FAIL_SCHEMA, "a.xml@3.1-dev": FAIL_SCHEMA}
    assert _explain(agr, tmp_path, files, expected, {"a.xml": X_CRS, "b.xml": X_OK}) == []


def test_a_disagreement_without_an_entry_fails(agr, tmp_path):
    files = '[files."b.xml"]\nreasons = ["strict-schema"]\n'
    expected = {"a.xml": PASS, "b.xml": FAIL_SCHEMA}
    problems = _explain(agr, tmp_path, files, expected, {"a.xml": X_CRS, "b.xml": X_OK})
    assert "a.xml: verdicts differ and intended.toml does not say why" in problems


def test_an_error_no_cited_reason_covers_fails(agr, tmp_path):
    files = '[files."a.xml"]\nreasons = ["crs"]\n[files."b.xml"]\nreasons = ["strict-schema"]\n'
    expected = {"a.xml": PASS, "b.xml": FAIL_SCHEMA}
    xslt = {"a.xml": X_CRS_AND_GEOM, "b.xml": X_OK}
    problems = _explain(agr, tmp_path, files, expected, xslt)
    assert problems == ["a.xml: not explained: XSLT Geometry Validation: no srs"]


def test_a_stale_entry_an_unknown_reason_and_an_unused_reason_fail(agr, tmp_path):
    files = '[files."a.xml"]\nreasons = ["crs"]\n[files."b.xml"]\nreasons = ["nope"]\n'
    expected = {"a.xml": PASS, "b.xml": FAIL_SCHEMA}
    problems = _explain(agr, tmp_path, files, expected, {"a.xml": X_OK, "b.xml": X_OK})
    assert "a.xml: the verdicts agree now; remove it from intended.toml" in problems
    assert "b.xml: reason 'nope' is not defined in intended.toml" in problems
    assert "reason 'crs' is not cited by any file" in problems
    assert "reason 'strict-schema' is not cited by any file" in problems


def test_a_reason_that_does_not_apply_fails(agr, tmp_path):
    files = '[files."a.xml"]\nreasons = ["crs", "strict-schema"]\n'
    problems = _explain(agr, tmp_path, files, {"a.xml": PASS}, {"a.xml": X_CRS})
    assert problems == ["a.xml: reason 'strict-schema' does not apply to this file"]


def test_a_record_missing_on_either_side_fails(agr, tmp_path):
    problems = _explain(agr, tmp_path, "", {"a.xml": PASS}, {"z.xml": X_OK})
    assert "a.xml: no record of the XSLT validator (run record)" in problems
    assert "z.xml: in xslt-expected.toml but not in the corpus" in problems


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ('[reasons.x]\ntitle = "t"\nside = "both"\nkinds = ["a"]\nwhy = "w"\n', "needs side"),
        ('[reasons.x]\ntitle = "t"\nside = "xslt"\nrules = ["a"]\nwhy = "w"\n', "lists kinds"),
        ('[reasons.x]\ntitle = "t"\nside = "xslt"\nkinds = ["("]\nwhy = "w"\n', "kinds"),
        ('[reasons.Bad_Id]\ntitle = "t"\nside = "xslt"\nkinds = ["a"]\nwhy = "w"\n', "an id"),
        ('[files."a.xml"]\nreasons = []\n', "non-empty list"),
        ("not toml [", "intended.toml"),
    ],
)
def test_a_malformed_intended_file_is_refused(agr, tmp_path, body, match):
    path = tmp_path / "intended.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(agr.AgreementError, match=match):
        agr.load_intended(path)


# --- the docs page ---------------------------------------------------------------------


def test_the_page_lists_every_difference_with_its_reasons(agr):
    page = agr.page_from_records()
    reasons, cited = agr.load_intended()
    assert "every one explained below" in page
    assert '!!! failure "Not explained"' not in page
    for key in cited:
        assert f"| `{key}` |" in page
    for rid, reason in reasons.items():
        assert f"### {reason.title} {{#{rid}}}" in page
        assert f"](#{rid})" in page


def test_the_page_shows_a_failure_when_something_is_unexplained(agr, tmp_path):
    path = tmp_path / "intended.toml"
    path.write_text(INTENDED, encoding="utf-8")
    reasons, cited = agr.load_intended(path)
    page = agr.render_page({"a|b.xml": PASS}, {"a|b.xml": X_CRS}, reasons, cited, {})
    assert '!!! failure "Not explained"' in page
    assert "a\\|b.xml" in page  # a pipe in a file name cannot break the table
    assert "**not explained**" in page


def test_the_docs_build_fills_the_page_from_the_records(agr):
    spec = importlib.util.spec_from_file_location(
        "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
    )
    assert spec is not None
    assert spec.loader is not None
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    source = (ROOT / "docs" / "conformance-agreement.md").read_text(encoding="utf-8")
    assert source.count(hooks.AGREEMENT_MARKER) == 1
    assert hooks.AGREEMENT_MARKER == agr.PAGE_MARKER
    built = hooks.on_page_markdown(source, config={"extra": {}})
    assert hooks.AGREEMENT_MARKER not in built
    assert agr.page_from_records() in built
