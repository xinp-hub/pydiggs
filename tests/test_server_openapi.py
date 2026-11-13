"""``pydiggs.server``: the committed OpenAPI document, its components, and Schemathesis."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import schemathesis
from fastapi import FastAPI
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import negative_data_rejection, positive_data_acceptance

import pydiggs
from pydiggs import findings_schema
from pydiggs.server import ServerSettings, _app, _schemas, create_app, create_router

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "src" / "pydiggs" / "server" / "openapi.json"
REGENERATE = (
    'uv run python -c "from pydiggs.server._app import openapi_text; import pathlib;'
    " pathlib.Path('src/pydiggs/server/openapi.json').write_text(openapi_text())\""
)


def _refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"]
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def _resolves(document, ref):
    assert ref.startswith("#/"), ref
    node = document
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def test_the_committed_openapi_document_has_not_drifted():
    assert COMMITTED.read_text("utf-8") == _app.openapi_text(), (
        f"regenerate with: {REGENERATE}, then update its sha256 in src/pydiggs/PROVENANCE.toml"
    )


def test_the_document_is_openapi_3_1_with_every_reference_resolving():
    document = json.loads(COMMITTED.read_text("utf-8"))
    assert document["openapi"].startswith("3.1.")
    assert set(document["paths"]) == {
        "/v1/validate",
        "/v1/read",
        "/v1/versions",
        "/v1/capabilities",
        "/v1/rules",
        "/v1/rules/{rule_id}",
        "/v1/readyz",
        "/livez",
        "/readyz",
    }
    for ref in set(_refs(document)):
        _resolves(document, ref)
    # No FastAPI validation-error schema: every error is a Problem.
    assert "HTTPValidationError" not in document["components"]["schemas"]


def test_findings_components_are_derived_from_the_packaged_schema():
    packaged = findings_schema()
    components = _schemas.components()
    names = {f"DiggsFindings{k[:1].upper()}{k[1:]}" for k in packaged["$defs"]}
    assert names | {"DiggsFindingsDocument"} <= set(components)
    document = components["DiggsFindingsDocument"]
    assert set(document["properties"]) == set(packaged["properties"])
    assert document["required"] == packaged["required"]
    assert "$id" not in document and "$defs" not in document
    assert {r for r in _refs(components) if "DiggsFindings" in r} <= {
        f"#/components/schemas/{n}" for n in names
    }

    def component_ref(match):
        return f'"#/components/schemas/DiggsFindings{match[1].upper()}{match[2]}"'

    for name, definition in packaged["$defs"].items():
        expected = re.sub(r'"#/\$defs/(\w)(\w*)"', component_ref, json.dumps(definition))
        assert components[f"DiggsFindings{name[:1].upper()}{name[1:]}"] == json.loads(expected)


def test_a_mounted_router_publishes_a_self_contained_document():
    host = FastAPI()
    host.include_router(create_router(ServerSettings(workers=1)))
    document = host.openapi()
    for ref in set(_refs(document)):
        _resolves(document, ref)  # no dangling DiggsFindings* reference
    response = document["paths"]["/v1/validate"]["post"]["responses"]["200"]
    assert response["content"]["application/json"]["schema"]["title"] == "diggs-findings 1.1"
    read = document["paths"]["/v1/read"]["post"]["responses"]["200"]
    assert read["content"]["application/json"]["schema"]["title"] == "diggs-model 1.0"


def test_model_components_are_derived_from_the_packaged_schema():
    packaged = pydiggs.model_schema()
    components = _schemas.components()

    def name(definition):
        return "DiggsModel" + "".join(part.capitalize() for part in definition.split("_"))

    names = {name(k) for k in packaged["$defs"]}
    assert names | {"DiggsModelDocument"} <= set(components)
    document = components["DiggsModelDocument"]
    assert set(document["properties"]) == set(packaged["properties"])
    assert document["required"] == packaged["required"]
    assert "$id" not in document and "$defs" not in document
    # the model's recursion survives as component references
    assert "#/components/schemas/DiggsModelExtraNode" in json.dumps(
        components["DiggsModelExtraNode"]
    )
    assert "#/components/schemas/DiggsModelComponent" in json.dumps(
        components["DiggsModelComponent"]
    )
    # a note is a finding: the one reference outside the model schema
    assert "#/components/schemas/DiggsFindingsFinding" in json.dumps(document)


def test_the_committed_document_carries_the_model_schema_by_reference():
    document = json.loads(COMMITTED.read_text("utf-8"))
    schema = document["paths"]["/v1/read"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema == {"$ref": "#/components/schemas/DiggsModelDocument"}


def test_versions_disabled_drops_the_operation():
    document = create_app(ServerSettings(workers=1, versions_enabled=False)).openapi()
    assert "/v1/versions" not in document["paths"]
    assert "/v1/capabilities" not in document["paths"]


# --- Schemathesis against the in-process app ---------------------------------------------------

_APP = create_app(ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",)))
_SCHEMA = schemathesis.openapi.from_dict(json.loads(COMMITTED.read_text("utf-8")))
_SCHEMA.app = _APP


def _starts_gzipped(body) -> bool:
    raw = body.get("file") if isinstance(body, dict) else body
    raw = getattr(raw, "data", raw)  # a schemathesis ``Binary`` goes out as its bytes
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "surrogatepass")
    return isinstance(raw, bytes | bytearray) and bytes(raw[:2]) == b"\x1f\x8b"


@pytest.mark.timeout(600)
@_SCHEMA.parametrize()
@settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
def test_the_api_conforms_to_its_openapi_document(case):
    excluded = []
    # A schema-valid binary body that happens to start with the gzip magic is refused
    # with 415 compressed-input on purpose; nothing in OpenAPI can express that.
    if _starts_gzipped(case.body):
        excluded.append(positive_data_acceptance)
    # /v1/read answers 422 invalid-document for bytes that are not a DIGGS document
    # (/v1/validate reports the same as findings); OpenAPI cannot say "a DIGGS file".
    if case.operation.path == "/v1/read" and positive_data_acceptance not in excluded:
        excluded.append(positive_data_acceptance)
    # Multipart fields are text on the wire: a "negative" null, boolean or number value
    # goes out as text (or not at all), which can be a valid value.
    if isinstance(case.body, dict) and any(
        not isinstance(v, str) for k, v in case.body.items() if k != "file"
    ):
        excluded.append(negative_data_rejection)
    response = case.call_and_validate(excluded_checks=excluded)
    assert response.status_code < 500 or response.status_code in (503, 504)
    if response.status_code >= 400:
        assert response.headers["content-type"][0] == "application/problem+json"
