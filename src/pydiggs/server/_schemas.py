"""OpenAPI 3.1 pieces: request and response schemas, and the app's components.

The diggs-findings schemas are derived from the packaged JSON Schema at run time,
never written by hand. Each route carries *self-contained* schemas, so a host app
that mounts only :func:`~pydiggs.server.create_router` still publishes a valid
document. :func:`lift_components` then turns them into ``#/components/schemas``
references for :func:`~pydiggs.server.create_app` (``DiggsFindingsDocument``,
``DiggsFindingsFile``, ``Problem`` …).
"""

from __future__ import annotations

import copy
import json
from functools import cache
from importlib import resources
from typing import Any, Final

from pydiggs import _service
from pydiggs.server._problems import PROBLEM_MEDIA_TYPE, PROBLEMS

__all__ = [
    "FINDINGS_MEDIA_TYPE",
    "MAX_CHECKS_CHARS",
    "SARIF_MEDIA_TYPE",
    "XML_MEDIA_TYPES",
    "components",
    "error_responses",
    "lift_components",
    "readyz_responses",
    "rule_id_parameter",
    "rule_responses",
    "rules_parameters",
    "rules_responses",
    "validate_parameters",
    "validate_request_body",
    "validate_responses",
    "versions_responses",
]

FINDINGS_MEDIA_TYPE: Final = "application/json"
SARIF_MEDIA_TYPE: Final = "application/sarif+json"
XML_MEDIA_TYPES: Final = ("application/xml", "text/xml")
#: Longest ``checks`` value, as documented in OpenAPI (``maxLength``).
MAX_CHECKS_CHARS: Final = 512
_SCHEMA_FILE: Final = "diggs-findings-1.0.schema.json"
_PREFIX: Final = "DiggsFindings"
_COMPONENTS: Final = "#/components/schemas/"
_JsonSchema = dict[str, Any]


@cache
def _packaged() -> _JsonSchema:
    """The packaged diggs-findings JSON Schema (read once)."""
    text = resources.files("pydiggs.findings").joinpath(_SCHEMA_FILE).read_text("utf-8")
    loaded: _JsonSchema = json.loads(text)
    return loaded


def _component_name(definition: str) -> str:
    return _PREFIX + definition[:1].upper() + definition[1:]


def _rewrite(node: Any, replace: Any) -> Any:
    """Copy ``node``, calling ``replace(target)`` for every ``{"$ref": "#/$defs/..."}``."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/") and len(node) == 1:
            return replace(ref.removeprefix("#/$defs/"))
        return {k: _rewrite(v, replace) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite(v, replace) for v in node]
    return node


def _root(schema: _JsonSchema) -> _JsonSchema:
    """The document schema without its resource keywords (``$schema``, ``$id``, ``$defs``)."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "$id", "$defs")}


def _findings_components() -> dict[str, _JsonSchema]:
    """``DiggsFindings*`` components: the root and each ``$defs`` entry, refs rewritten."""
    schema = _packaged()

    def to_component(name: str) -> _JsonSchema:
        return {"$ref": _COMPONENTS + _component_name(name)}

    out = {_PREFIX + "Document": _rewrite(_root(schema), to_component)}
    for name, definition in schema.get("$defs", {}).items():
        out[_component_name(name)] = _rewrite(definition, to_component)
    return out


@cache
def _findings_inline() -> _JsonSchema:
    """The document schema with every ``$defs`` reference inlined (it has no cycles)."""
    schema = _packaged()
    defs = schema.get("$defs", {})

    def inline(name: str) -> Any:
        return _rewrite(defs[name], inline)

    inlined: _JsonSchema = _rewrite(_root(schema), inline)
    return inlined


_SARIF: Final[_JsonSchema] = {
    "title": "SARIF 2.1.0 log",
    "description": "A SARIF 2.1.0 log with one run (sent for Accept: application/sarif+json).",
    "type": "object",
    "required": ["version", "runs"],
    "properties": {"version": {"const": "2.1.0"}, "runs": {"type": "array"}},
}

_PROBLEM: Final[_JsonSchema] = {
    "title": "Problem",
    "description": "RFC 9457 problem details. Switch on `code`; every text is fixed.",
    "type": "object",
    "required": ["type", "title", "status", "detail", "code"],
    "properties": {
        "type": {"type": "string", "format": "uri"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
        "code": {"enum": sorted(PROBLEMS)},
        "errors": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["parameter", "problem"],
                "properties": {"parameter": {"type": "string"}, "problem": {"type": "string"}},
            },
        },
        "correlation_id": {"type": "string"},
    },
}


@cache
def _steps() -> tuple[str, ...]:
    return tuple(_packaged()["$defs"]["step"]["enum"])


@cache
def _rule_info() -> _JsonSchema:
    return {
        "title": "RuleInfo",
        "description": "One rule id pydiggs can emit (or an open family, as a pattern).",
        "type": "object",
        "required": ["id", "pattern", "step", "description", "severities", "help_uri", "known"],
        "properties": {
            "id": {"type": "string"},
            "pattern": {"type": ["string", "null"]},
            "step": {"enum": list(_steps())},
            "description": {"type": "string"},
            "severities": {
                "type": "array",
                "items": {"enum": list(_packaged()["$defs"]["severity"]["enum"])},
            },
            "help_uri": {"type": "string"},
            "known": {"type": "boolean"},
        },
    }


_VERSIONS: Final[_JsonSchema] = {
    "title": "Versions",
    "description": "The running pydiggs, its findings schema and bundled reference data.",
    "type": "object",
    "required": [
        "api_version",
        "pydiggs_version",
        "findings_schema_version",
        "findings_schema_url",
        "diggs_versions",
        "reference_data",
    ],
    "properties": {
        "api_version": {"const": "1"},
        "pydiggs_version": {"type": "string"},
        "findings_schema_version": {"type": "string"},
        "findings_schema_url": {"type": "string", "format": "uri"},
        "diggs_versions": {"type": "array", "items": {"type": "string"}},
        "reference_data": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_READY: Final[_JsonSchema] = {
    "title": "Ready",
    "type": "object",
    "required": ["status"],
    "properties": {"status": {"const": "ready"}},
}


def _named() -> dict[str, _JsonSchema]:
    """Component name -> the self-contained schema the routes carry inline."""
    return {
        "DiggsFindingsDocument": _findings_inline(),
        "SarifLog": _SARIF,
        "Problem": _PROBLEM,
        "RuleInfo": _rule_info(),
        "Versions": _VERSIONS,
        "Ready": _READY,
    }


def components() -> dict[str, _JsonSchema]:
    """Every component :func:`lift_components` refers to, sorted by name.

    Examples:
        >>> sorted(components())[:3]
        ['DiggsFindingsCommit', 'DiggsFindingsCounts', 'DiggsFindingsDocument']
    """
    out = {k: copy.deepcopy(v) for k, v in _named().items() if k != "DiggsFindingsDocument"}
    out.update(_findings_components())
    return dict(sorted(out.items()))


def lift_components(document: dict[str, Any]) -> dict[str, Any]:
    """Replace the routes' inline schemas with component references, and add them.

    Args:
        document: An OpenAPI document generated by FastAPI for these routes.

    Returns:
        The same document, changed in place.
    """
    named = list(_named().items())

    def lift(node: Any) -> Any:
        if isinstance(node, dict):
            for name, schema in named:
                if node == schema:
                    return {"$ref": _COMPONENTS + name}
            return {k: lift(v) for k, v in node.items()}
        if isinstance(node, list):
            return [lift(v) for v in node]
        return node

    document["paths"] = lift(document.get("paths", {}))
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(components())
    document["components"]["schemas"] = dict(sorted(schemas.items()))
    return document


# --- per-route OpenAPI fragments ---------------------------------------------------------


def _problem_content() -> dict[str, Any]:
    return {PROBLEM_MEDIA_TYPE: {"schema": _PROBLEM}}


_STATUS_TEXT: Final = {
    400: "Missing or malformed input",
    403: "Cross-site request refused",
    406: "Accept allows neither JSON nor SARIF",
    408: "The body did not arrive within the body timeout",
    413: "Body larger than the limit",
    415: "Unsupported media type, or compressed input",
    422: "Invalid parameter, or unreadable input",
    500: "Internal error (fixed text and a correlation id)",
    503: "Busy (see Retry-After) or not ready",
    504: "Validation passed the hard time limit (process isolation)",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Problem responses for ``statuses``.

    Examples:
        >>> sorted(error_responses(503, 413))
        [413, 503]
    """
    out: dict[int | str, dict[str, Any]] = {}
    for status in statuses:
        entry: dict[str, Any] = {"description": _STATUS_TEXT[status], "content": _problem_content()}
        if status == 503:  # noqa: PLR2004
            entry["headers"] = {
                "Retry-After": {"description": "Seconds to wait", "schema": {"type": "string"}}
            }
        out[status] = entry
    return out


def _query(name: str, schema: _JsonSchema, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": False,
        "schema": schema,
        "description": description,
    }


def _checks_schema() -> _JsonSchema:
    step = "(" + "|".join(sorted(_service._STEP_VALUES)) + ")"
    return {"type": "string", "pattern": f"^{step}(,{step})*$", "maxLength": MAX_CHECKS_CHARS}


def _diggs_version_schema() -> _JsonSchema:
    return {"enum": list(_service.versions()["diggs_versions"])}


def _name_schema() -> _JsonSchema:
    return {"type": "string", "maxLength": _service.MAX_NAME_INPUT_CHARS}


def validate_parameters() -> list[dict[str, Any]]:
    """The query parameters of ``POST /v1/validate``."""
    return [
        _query("name", _name_schema(), "File name to report (reduced to a basename)."),
        _query("checks", _checks_schema(), "Comma-separated steps to run (default: all)."),
        _query("diggs_version", _diggs_version_schema(), "DIGGS version (default: detected)."),
    ]


def validate_request_body() -> dict[str, Any]:
    """The request body of ``POST /v1/validate``: raw XML or one multipart file."""
    raw = {"schema": {"type": "string", "format": "binary"}}
    form = {
        "schema": {
            "type": "object",
            "required": ["file"],
            "additionalProperties": False,
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "name": _name_schema(),
                "checks": _checks_schema(),
                "diggs_version": _diggs_version_schema(),
            },
        },
        "encoding": {"file": {"contentType": "application/xml, text/xml, */*"}},
    }
    return {
        "required": True,
        "content": {**dict.fromkeys(XML_MEDIA_TYPES, raw), "multipart/form-data": form},
    }


def validate_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``POST /v1/validate``."""
    return {
        200: {
            "description": (
                "A diggs-findings 1.0 document (or a SARIF 2.1.0 log). Malformed XML,"
                " truncation and an exhausted time budget are 200s with findings."
            ),
            "content": {
                FINDINGS_MEDIA_TYPE: {"schema": _findings_inline()},
                SARIF_MEDIA_TYPE: {"schema": _SARIF},
            },
        },
        **error_responses(400, 403, 406, 408, 413, 415, 422, 500, 503, 504),
    }


def versions_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``GET /v1/versions``."""
    return {
        200: {
            "description": "Versions",
            "content": {FINDINGS_MEDIA_TYPE: {"schema": _VERSIONS}},
        },
        **error_responses(403, 500),
    }


def rules_parameters() -> list[dict[str, Any]]:
    """The query parameters of ``GET /v1/rules``."""
    return [_query("step", {"enum": list(_steps())}, "Only the rules of this step.")]


def rules_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``GET /v1/rules``."""
    return {
        200: {
            "description": "The rule catalogue",
            "content": {FINDINGS_MEDIA_TYPE: {"schema": {"type": "array", "items": _rule_info()}}},
        },
        **error_responses(403, 422, 500),
    }


def rule_id_parameter() -> list[dict[str, Any]]:
    """The path parameter of ``GET /v1/rules/{rule_id}``."""
    steps = "|".join(_steps())
    return [
        {
            "name": "rule_id",
            "in": "path",
            "required": True,
            "description": "A rule id, <step>.<name>.",
            "schema": {
                "type": "string",
                "maxLength": 256,
                "pattern": f"^({steps})\\.[A-Za-z0-9][A-Za-z0-9._-]*$",
            },
        }
    ]


def rule_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``GET /v1/rules/{rule_id}``."""
    return {
        200: {
            "description": "The rule (``known: false`` for a valid id pydiggs does not emit)",
            "content": {FINDINGS_MEDIA_TYPE: {"schema": _rule_info()}},
        },
        **error_responses(403, 422, 500),
    }


def readyz_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of the readiness probes."""
    return {
        200: {"description": "Ready", "content": {FINDINGS_MEDIA_TYPE: {"schema": _READY}}},
        **error_responses(403, 503),
    }
