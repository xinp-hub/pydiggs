"""Tests for the distribution itself."""

from __future__ import annotations

import types
from importlib import metadata, resources

import pydiggs as pkg

DIST = "pydiggs"


def test_version_matches_installed_metadata():
    assert pkg.__version__ == metadata.version(DIST)


def test_reports_name_the_installed_version(tmp_path):
    from pydiggs.findings import _json, _sarif

    path = tmp_path / "a.xml"
    path.write_text('<Diggs xmlns="http://diggsml.org/schemas/2.6"/>', encoding="utf-8")
    report = pkg.validate(path)
    assert report.tool_version == metadata.version(DIST)
    log = _sarif.sarif_log([report], [], tool_version=report.tool_version)
    assert log["runs"][0]["tool"]["driver"]["version"] == metadata.version(DIST)
    doc = _json.document([], tool_version=report.tool_version, reference_data={})
    assert doc["tool"]["version"] == metadata.version(DIST)


def test_ships_py_typed_marker():
    assert resources.files(pkg).joinpath("py.typed").is_file()


def test_public_api_is_declared_and_importable():
    assert pkg.__all__ == sorted(pkg.__all__), "__all__ should be sorted"
    for name in pkg.__all__:
        assert hasattr(pkg, name), f"{name} is exported but missing"

    leaked = {
        name
        for name in dir(pkg)
        if not name.startswith("_")
        and name not in pkg.__all__
        and not isinstance(getattr(pkg, name), types.ModuleType)
        and type(getattr(pkg, name)).__module__ != "__future__"
    }
    assert not leaked, (
        f"public names not listed in __all__ leak an unversioned API surface: {sorted(leaked)}"
    )


def test_metadata_is_complete():
    meta = metadata.metadata(DIST)
    assert meta["Summary"], "description is required"
    assert meta["Requires-Python"], "requires-python is required"
    assert meta["License-Expression"] or meta["License"], "a license is required"
    urls = meta.get_all("Project-URL") or []
    assert any("Source" in u for u in urls), "declare a Source URL"


def test_ships_bundled_schema_profiles():
    root = resources.files(pkg)
    assert root.joinpath("schemas/diggs-schema-2.5.a/Complete.xsd").is_file()
    assert root.joinpath("schemas/diggs-schema-2.6/Diggs.xsd").is_file()
    assert root.joinpath("schemas/diggs-schema-3.0.0/Diggs.xsd").is_file()
    assert root.joinpath("schematron/diggs_schematron_rules_2.6.sch").is_file()
    assert root.joinpath("dictionaries/properties.xml").is_file()


def test_ships_findings_schema():
    schema = resources.files(pkg).joinpath("findings/diggs-findings-1.0.schema.json")
    assert schema.is_file()
    assert '"$id"' in schema.read_text(encoding="utf-8")
