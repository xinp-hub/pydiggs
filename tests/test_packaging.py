"""Tests for the distribution itself."""

from __future__ import annotations

import types
from importlib import metadata, resources

import pydiggs as pkg

DIST = "pydiggs"


def test_version_matches_installed_metadata():
    assert pkg.__version__ == metadata.version(DIST)


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
