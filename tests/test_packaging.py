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


def _ruff_order(name: str) -> tuple[int, str]:
    """ruff's RUF022 order: CONSTANTS, then Classes, then everything else."""
    if name.isupper():
        return (0, name)
    return (1, name) if name[:1].isupper() else (2, name)


def test_public_api_is_declared_and_importable():
    assert pkg.__all__ == sorted(pkg.__all__, key=_ruff_order), "__all__ should be sorted"
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


def test_requires_dist_keeps_the_core_small_and_declares_the_extras():
    requires = metadata.requires(DIST) or []
    core = sorted(r for r in requires if "extra ==" not in r)
    assert core == ["lxml>=6.1.3", "rich>=13.9.4"]
    by_extra: dict[str, set[str]] = {}
    for line in requires:
        if "extra ==" in line:
            spec, _, marker = line.partition(";")
            extra = marker.split("extra ==")[1].strip().strip("'\"")
            by_extra.setdefault(extra, set()).add(spec.strip())
    assert by_extra == {
        "server": {
            "fastapi>=0.146.0",
            "starlette>=1.6.0",
            "pydantic>=2.12.0",
            "python-multipart>=0.0.31",
            "uvicorn>=0.54.0",
        },
        "mcp": {"mcp>=2.3.0", "python-multipart>=0.0.31"},
        "frames": {"pandas>=2.2.3"},
        "polars": {"polars>=1.20.0"},
        "gis": {
            "geopandas>=1.1.2",
            "pandas>=2.2.3",
            "pyarrow>=23.0.1",
            "pyogrio>=0.10.0",
            "pyproj>=3.7.0",
            "shapely>=2.0.6",
        },
    }
    assert set(metadata.metadata(DIST).get_all("Provides-Extra") or []) == set(by_extra)


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
    # 1.1 ships beside 1.0, which stays in the wheel (tests/test_findings_schema_11.py).
    newer = resources.files(pkg).joinpath("findings/diggs-findings-1.1.schema.json")
    assert newer.is_file()
    assert '"$id"' in newer.read_text(encoding="utf-8")


def test_ships_the_ags4_subpackage():
    from pydiggs import ags4

    assert ags4.__all__ == [
        "AGS_VERSIONS",
        "File",
        "Group",
        "dumps",
        "read",
        "read_bytes",
        "write",
    ]
    for module in ("_emit", "_io", "_parse", "_table"):
        assert resources.files(pkg).joinpath(f"ags4/{module}.py").is_file()
