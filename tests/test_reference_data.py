"""The vendored DIGGS reference data matches its recorded provenance."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

import pydiggs

ROOT = Path(__file__).resolve().parent.parent
_PKG = Path(pydiggs.__file__).resolve().parent
_SCRIPT = ROOT / "scripts" / "sync_reference.py"


def _load_sync_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_reference", _SCRIPT)
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


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")
def test_vendored_files_match_provenance():
    assert _load_sync_module().check() == []


@pytest.mark.parametrize(
    ("provenance", "sources"),
    [
        (_PKG / "PROVENANCE.toml", {"schemas", "def"}),
        (ROOT / "tests" / "fixtures" / "official" / "PROVENANCE.toml", {"examples"}),
    ],
    ids=["package", "fixtures"],
)
def test_provenance_pins_full_commits(provenance: Path, sources: set[str]):
    data = tomllib.loads(provenance.read_text(encoding="utf-8"))
    assert set(data["sources"]) == sources
    for source in data["sources"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])
    for rel, entry in data["files"].items():
        assert (provenance.parent / rel).is_file(), rel
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), rel


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")
@pytest.mark.parametrize(
    "rel",
    [
        "diggs-schema-2.6/a\\..\\..\\evil.xsd",  # backslashes: pip normalises on Windows
        "diggs-schema-2.6/../evil.xsd",
        "diggs-schema-2.6/.hidden.xsd",
        "diggs-schema-2.6/stream.xsd:evil",
        "diggs-schema-2.6/CON.xsd",
        "diggs-schema-2.6/trailing.",
        "diggs-schema-2.6/new\nline.xsd",
    ],
)
def test_unsafe_vendored_names_are_refused(rel: str):
    assert _load_sync_module()._unsafe_reason(rel) is not None


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")
def test_safe_vendored_names_are_accepted():
    sync = _load_sync_module()
    for rel in ("schemas/diggs-schema-2.6/witsml/2.0/data/x.xsd", "2.6/Piezometer Example.xml"):
        assert sync._unsafe_reason(rel) is None


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ is not in this tree")
def test_provenance_toml_round_trips_awkward_strings():
    sync = _load_sync_module()
    awkward = 'q"uote\\back\tslash\x7f'
    text = sync._render_provenance("h", {"s": {"repo": awkward}}, {awkward: {"sha256": "0"}})
    data = tomllib.loads(text.decode("utf-8"))
    assert data["sources"]["s"]["repo"] == awkward
    assert awkward in data["files"]


def test_bundle_has_no_directory_listings():
    """Upstream index.html pages are not schema content and must not ship."""
    assert not list((_PKG / "schemas").rglob("*.html"))
    assert not list((_PKG / "dictionaries").rglob("*.html"))
