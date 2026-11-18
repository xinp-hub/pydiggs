"""The assembled documentation site serves every schema at the URL its ``$id`` names."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("docs_site", ROOT / "scripts" / "docs_site.py")
assert _SPEC is not None and _SPEC.loader is not None
docs_site = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(docs_site)

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
