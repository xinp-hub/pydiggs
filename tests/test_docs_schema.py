"""The JSON Schema published on the docs site is the one in the package."""

from __future__ import annotations

import hashlib
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
    assert "container.md" in config


# --- OpenAPI and OpenRPC documents -------------------------------------------------
#
# Validated against meta-schemas vendored in tests/meta_schemas/ so the tests need no
# network. Provenance (all Apache-2.0):
#
# - openapi-3.1-schema-2025-11-23.json: https://spec.openapis.org/oas/3.1/schema/2025-11-23
#   (OpenAPI Initiative), byte for byte. It describes OpenAPI 3.1.x documents without
#   Schema Object validation; the component schemas are checked against JSON Schema
#   2020-12 separately below.
# - openrpc-meta-schema-1.14.9.json: the ``openrpcDocument`` object of npm
#   @open-rpc/meta-schema 1.14.9 (tarball sha1 a2f834adf74d309fa19fead7f9484e0243dc337b,
#   package/index.js), re-serialised as JSON with indent=2. The repository's schema.json
#   is a template (its ``openrpc`` enum is a placeholder); the released package is not.
# - json-schema-tools-meta-schema-1.7.5.json: package/schema.json of npm
#   @json-schema-tools/meta-schema 1.7.5 (tarball sha1
#   2cd7992eee79c912d0d860b45c6064994ded99f2), byte for byte; the OpenRPC meta-schema
#   uses it (https://meta.json-schema.tools/) for every ``schema`` field.

META = ROOT / "tests" / "meta_schemas"
VENDORED_SHA256 = {
    "openapi-3.1-schema-2025-11-23.json": (
        "1b8ccc6e34234b17536f2dd0eb3597142a32bd108438cd42471a5fca4c1a07ef"
    ),
    "openrpc-meta-schema-1.14.9.json": (
        "c1314ab1a30a252013d6139c9c2eb042b91633f8aa3bddbb7f8ef3f9f7e240a3"
    ),
    "json-schema-tools-meta-schema-1.7.5.json": (
        "ebaa0daa2efb3f8247ae62f6fea63a11a73fa7f6c9663c7cc3f38430d147a366"
    ),
}


def _load(path: Path):
    return json.loads(path.read_text("utf-8"))


def _errors(validator, instance) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    ]


def test_vendored_meta_schemas_are_unchanged():
    for name, expected in VENDORED_SHA256.items():
        assert hashlib.sha256((META / name).read_bytes()).hexdigest() == expected, name


def test_openapi_document_is_valid_openapi_3_1():
    from jsonschema import Draft202012Validator

    hooks = _hooks()
    document = _load(hooks.OPENAPI)
    meta = _load(META / "openapi-3.1-schema-2025-11-23.json")
    Draft202012Validator.check_schema(meta)
    assert document["openapi"].startswith("3.1.")
    assert _errors(Draft202012Validator(meta), document) == []


def test_openapi_component_schemas_are_valid_json_schema_2020_12():
    from jsonschema import Draft202012Validator

    document = _load(_hooks().OPENAPI)
    schemas = document["components"]["schemas"]
    assert schemas
    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # pragma: no cover - the message is the point
            raise AssertionError(f"components/schemas/{name}: {exc}") from exc


def test_openrpc_document_is_valid_openrpc():
    from jsonschema import Draft7Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    document = _load(_hooks().OPENRPC)
    meta = _load(META / "openrpc-meta-schema-1.14.9.json")
    tools = Resource.from_contents(
        _load(META / "json-schema-tools-meta-schema-1.7.5.json"), default_specification=DRAFT7
    )
    registry = Registry().with_resources(
        [("https://meta.json-schema.tools/", tools), ("https://meta.json-schema.tools", tools)]
    )
    assert document["openrpc"] in meta["properties"]["openrpc"]["enum"]
    assert _errors(Draft7Validator(meta, registry=registry), document) == []


def test_hook_publishes_the_api_documents_byte_for_byte(tmp_path):
    hooks = _hooks()
    hooks.on_post_build({"site_dir": str(tmp_path)})
    for package, name in (("pydiggs.server", "openapi.json"), ("pydiggs.sidecar", "openrpc.json")):
        published = tmp_path / "schemas" / name
        assert published.read_bytes() == resources.files(package).joinpath(name).read_bytes()
