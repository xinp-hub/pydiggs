"""The draft diggs-model schema published on the docs site (work package R0).

The read API arrives in 1.6.0; until then the schema and its example live only under
``docs/schemas/preview/`` and are published by MkDocs as-is.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")

ROOT = Path(__file__).resolve().parent.parent
PREVIEW = ROOT / "docs" / "schemas" / "preview"
SCHEMA = PREVIEW / "diggs-model-1.0-draft.schema.json"
EXAMPLE = PREVIEW / "diggs-model-1.0-draft.example.json"
SITE = "https://xinp-hub.github.io/pydiggs/"

pytestmark = pytest.mark.skipif(not SCHEMA.is_file(), reason="docs/ is not in this tree")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _validator():
    findings = json.loads(
        resources.files("pydiggs.findings").joinpath("diggs-findings-1.0.schema.json").read_text()
    )
    registry = referencing.Registry().with_resource(
        findings["$id"], referencing.Resource.from_contents(findings)
    )
    return jsonschema.Draft202012Validator(_load(SCHEMA), registry=registry)


def _walk(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}/{i}")


def test_schema_is_valid_2020_12_and_published_where_its_id_says():
    schema = _load(SCHEMA)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"] == SITE + SCHEMA.relative_to(ROOT / "docs").as_posix()


def test_example_validates():
    errors = list(_validator().iter_errors(_load(EXAMPLE)))
    assert errors == [], [f"{e.json_path}: {e.message}" for e in errors]


@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        (("features", 0, "key"), ""),
        (("links", 0, "resolved"), "yes"),
        (("notes", 0, "severity"), "loud"),
        (("schema_version",), "2.0"),
        (("features", 0, "total_depth", "value"), "15.2"),
    ],
)
def test_example_mutations_are_rejected(pointer, value):
    doc = _load(EXAMPLE)
    target = doc
    for step in pointer[:-1]:
        target = target[step]
    target[pointer[-1]] = value
    assert list(_validator().iter_errors(doc))


def test_schema_is_open_for_consumers():
    """No closed objects, enums or oneOf: a 1.0 consumer must accept any 1.x document."""
    for path, node in _walk(_load(SCHEMA)):
        assert node.get("additionalProperties") is not False, path
        assert "enum" not in node, path
        assert "oneOf" not in node, path
        for union in node.get("anyOf", []):
            assert union.get("type") == "null" or "$ref" in union, path


def test_unknown_fields_are_accepted():
    doc = _load(EXAMPLE)
    doc["future_field"] = {"x": 1}
    doc["features"][0]["future_field"] = [1, 2]
    assert list(_validator().iter_errors(doc)) == []


def test_every_definition_is_used():
    schema = _load(SCHEMA)
    text = json.dumps(schema)
    unused = [name for name in schema["$defs"] if f'"#/$defs/{name}"' not in text]
    assert unused == []


def test_docs_page_links_the_schema_and_example():
    page = (ROOT / "docs" / "model.md").read_text(encoding="utf-8")
    assert EXAMPLE.relative_to(ROOT / "docs").as_posix() in page
    assert "schemas/preview/diggs-model-1.0-beta.2.schema.json" in page
    assert "model.md" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


def test_note_rule_ids_in_the_docs_are_catalogued_or_marked_new():
    import re

    import pydiggs

    page = (ROOT / "docs" / "model.md").read_text(encoding="utf-8")
    section = page.split("### Notes", 1)[1].split("## Versioning", 1)[0]
    for line in section.splitlines():
        match = re.search(r"`([a-z]+\.[a-z0-9-]+)`( \(new\))?", line)
        if match and line.startswith("|"):
            assert pydiggs.explain_rule(match.group(1)).known != bool(match.group(2)), line
