"""The JSON Schema published on the docs site is the one in the package."""

from __future__ import annotations

import importlib.util
import json
from importlib import resources
from pathlib import Path

from pydiggs.findings import _json

ROOT = Path(__file__).resolve().parent.parent


def _hooks():
    spec = importlib.util.spec_from_file_location(
        "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_publishes_the_packaged_schema_byte_for_byte(tmp_path):
    hooks = _hooks()
    hooks.on_post_build({"site_dir": str(tmp_path)})
    published = tmp_path / "schemas" / "diggs-findings-1.0.schema.json"
    packaged = resources.files("pydiggs.findings").joinpath(_json.SCHEMA_RESOURCE).read_bytes()
    assert published.read_bytes() == packaged


def test_published_path_matches_the_schema_id():
    hooks = _hooks()
    schema = json.loads(hooks.SCHEMA.read_text("utf-8"))
    assert schema["$id"] == "https://xinp-hub.github.io/pydiggs/" + hooks.PUBLISHED.as_posix()


def test_mkdocs_config_wires_hook_and_nav():
    config = (ROOT / "mkdocs.yml").read_text("utf-8")
    assert "scripts/mkdocs_hooks.py" in config
    assert "findings.md" in config
