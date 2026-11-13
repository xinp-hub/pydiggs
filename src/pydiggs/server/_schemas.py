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
    "AGS4_MEDIA_TYPES",
    "FINDINGS_MEDIA_TYPE",
    "MAX_CHECKS_CHARS",
    "SARIF_MEDIA_TYPE",
    "XML_MEDIA_TYPES",
    "ags4_check_parameters",
    "ags4_check_request_body",
    "ags4_check_responses",
    "components",
    "convert_parameters",
    "convert_request_body",
    "convert_responses",
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
#: Raw AGS4 bodies: AGS4 has no registered media type, so the generic ones for text.
AGS4_MEDIA_TYPES: Final = ("application/octet-stream", "text/csv", "text/plain")
#: Longest ``checks`` value, as documented in OpenAPI (``maxLength``).
MAX_CHECKS_CHARS: Final = 512
_SCHEMA_FILE: Final = "diggs-findings-1.1.schema.json"
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


# --- diggs-model -------------------------------------------------------------------------

_MODEL_FILE: Final = "diggs-model-1.0.schema.json"
_MODEL_PREFIX: Final = "DiggsModel"
#: The one reference the model schema makes outside itself: a note is a finding.
_FINDING_REF: Final = "/diggs-findings-1.0.schema.json#/$defs/finding"


@cache
def _model_packaged() -> _JsonSchema:
    """The packaged diggs-model JSON Schema (read once)."""
    text = resources.files("pydiggs.model").joinpath(_MODEL_FILE).read_text("utf-8")
    loaded: _JsonSchema = json.loads(text)
    return loaded


def _model_component_name(definition: str) -> str:
    return _MODEL_PREFIX + "".join(part.capitalize() for part in definition.split("_"))


def _rewrite_model(node: Any, local: Any, finding: Any) -> Any:
    """Copy ``node``; ``local(name)`` replaces a ``#/$defs`` reference, ``finding()`` a note's."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and len(node) == 1:
            if ref.startswith("#/$defs/"):
                return local(ref.removeprefix("#/$defs/"))
            if ref.endswith(_FINDING_REF):
                return finding()
        return {k: _rewrite_model(v, local, finding) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_model(v, local, finding) for v in node]
    return node


def _model_components() -> dict[str, _JsonSchema]:
    """``DiggsModel*`` components: the root and each ``$defs`` entry, refs rewritten."""
    schema = _model_packaged()

    def local(name: str) -> _JsonSchema:
        return {"$ref": _COMPONENTS + _model_component_name(name)}

    def finding() -> _JsonSchema:
        return {"$ref": _COMPONENTS + _component_name("finding")}

    out = {_MODEL_PREFIX + "Document": _rewrite_model(_root(schema), local, finding)}
    for name, definition in schema.get("$defs", {}).items():
        out[_model_component_name(name)] = _rewrite_model(definition, local, finding)
    return out


@cache
def _model_inline() -> _JsonSchema:
    """The document schema with its references inlined, for a router mounted in another app.

    The model is recursive in two places (an ``extra`` element's children, a
    component's components); there the inlined schema says only "an object". The
    full, recursive definitions are the ``DiggsModel*`` components that
    :func:`lift_components` adds to this package's own OpenAPI document.
    """
    defs = _model_packaged().get("$defs", {})
    findings = _packaged().get("$defs", {})
    open_stack: list[str] = []

    def local(name: str) -> Any:
        if name in open_stack:
            return {"type": "object", "description": f"As the enclosing {name}."}
        open_stack.append(name)
        try:
            return _rewrite_model(defs[name], local, finding)
        finally:
            open_stack.pop()

    def inline_finding(name: str) -> Any:
        return _rewrite(findings[name], inline_finding)

    def finding() -> Any:
        return inline_finding("finding")

    inlined: _JsonSchema = _rewrite_model(_root(_model_packaged()), local, finding)
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
        "kind": {
            "type": "string",
            "description": (
                "For invalid-document: why the bytes are not a document of the expected"
                " format (DIGGS XML, or AGS4 for a conversion)."
            ),
            "examples": list(_service.DOCUMENT_KINDS),
        },
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


def _conversion(findings: _JsonSchema) -> _JsonSchema:
    """The result of ``POST /v1/convert``; ``findings`` is the diggs-findings schema to use."""
    return {
        "title": "ConversionResult",
        "description": (
            "One conversion: the output as base64 with its media type and sha256, and the"
            " findings about the source (report) and the output (validation). Every surface"
            " (HTTP, sidecar, CLI) produces the same result for the same input."
        ),
        "type": "object",
        "required": [
            "to",
            "media_type",
            "output_name",
            "diggs_version",
            "mapping_version",
            "complete",
            "lossless",
            "report",
            "validation",
            "output_sha256",
            "output_base64",
        ],
        "properties": {
            "to": {"enum": list(_service.CONVERT_TARGETS), "description": "The output format."},
            "media_type": {"type": "string", "examples": [_service.DIGGS_MEDIA_TYPE]},
            "output_name": {
                "type": "string",
                "description": "A file name for the output: the input's name, new extension.",
            },
            "diggs_version": {"type": ["string", "null"], "description": "Of the output."},
            "mapping_version": {"type": "string", "description": "Of the mapping table used."},
            "complete": {
                "type": "boolean",
                "description": "False when a size or time limit stopped the conversion.",
            },
            "lossless": {
                "type": "boolean",
                "description": "True when no convert finding of severity warning or error.",
            },
            "report": findings,
            "validation": {
                "anyOf": [findings, {"type": "null"}],
                "description": "pydiggs.validate on the output.",
            },
            "output_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "output_base64": {
                "type": "string",
                "contentEncoding": "base64",
                "contentMediaType": _service.DIGGS_MEDIA_TYPE,
                "description": "The output's bytes, standard base64 (RFC 4648, padded).",
            },
        },
    }


def _named() -> dict[str, _JsonSchema]:
    """Component name -> the self-contained schema the routes carry inline."""
    return {
        "ConversionResult": _conversion(_findings_inline()),
        "DiggsFindingsDocument": _findings_inline(),
        "DiggsModelDocument": _model_inline(),
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
        ['ConversionResult', 'DiggsFindingsCommit', 'DiggsFindingsCounts']
    """
    inlined = ("ConversionResult", "DiggsFindingsDocument", "DiggsModelDocument")
    out = {k: copy.deepcopy(v) for k, v in _named().items() if k not in inlined}
    out["ConversionResult"] = _conversion({"$ref": _COMPONENTS + "DiggsFindingsDocument"})
    out.update(_findings_components())
    out.update(_model_components())
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
    406: "Accept allows none of the media types this operation sends",
    408: "The body did not arrive within the body timeout",
    413: "Body larger than the limit",
    415: "Unsupported media type, or compressed input",
    422: (
        "Invalid parameter, unreadable input, not a document of the expected format"
        " (reading, converting), or a result over the limit"
    ),
    500: "Internal error (fixed text and a correlation id)",
    503: "Busy (see Retry-After) or not ready",
    504: "The request passed the hard time limit (process isolation)",
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
                "A diggs-findings 1.1 document (or a SARIF 2.1.0 log). Malformed XML,"
                " truncation and an exhausted time budget are 200s with findings."
            ),
            "content": {
                FINDINGS_MEDIA_TYPE: {"schema": _findings_inline()},
                SARIF_MEDIA_TYPE: {"schema": _SARIF},
            },
        },
        **error_responses(400, 403, 406, 408, 413, 415, 422, 500, 503, 504),
    }


#: Longest ``select`` value accepted (JSON text in a query or form field).
MAX_SELECT_CHARS: Final = 64 * 2**10


def _select_schema() -> _JsonSchema:
    return {
        "type": "string",
        "maxLength": MAX_SELECT_CHARS,
        "contentMediaType": "application/json",
        "examples": ['{"results": "none"}', '{"results": ["Test_1", "Test_2"]}'],
    }


_SELECT_TEXT: Final = (
    'JSON text: {"results": "all" | "none" | [node keys]}. Which result tables keep their'
    " values (default: all); the others keep their columns and row_count."
)


def read_parameters() -> list[dict[str, Any]]:
    """The query parameters of ``POST /v1/read``."""
    return [
        _query("name", _name_schema(), "File name to report (reduced to a basename)."),
        _query("diggs_version", _diggs_version_schema(), "DIGGS version (default: detected)."),
        _query("select", _select_schema(), _SELECT_TEXT),
    ]


def read_request_body() -> dict[str, Any]:
    """The request body of ``POST /v1/read``: raw XML or one multipart file."""
    raw = {"schema": {"type": "string", "format": "binary"}}
    form = {
        "schema": {
            "type": "object",
            "required": ["file"],
            "additionalProperties": False,
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "name": _name_schema(),
                "diggs_version": _diggs_version_schema(),
                "select": _select_schema(),
            },
        },
        "encoding": {"file": {"contentType": "application/xml, text/xml, */*"}},
    }
    return {
        "required": True,
        "content": {**dict.fromkeys(XML_MEDIA_TYPES, raw), "multipart/form-data": form},
    }


def read_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``POST /v1/read``."""
    return {
        200: {
            "description": (
                "A diggs-model 1.0 document: what pydiggs.read() returns for the same"
                " bytes. A schema-invalid file still reads (run /v1/validate for a"
                " verdict); an exhausted time budget is a 200 with complete: false."
            ),
            "content": {FINDINGS_MEDIA_TYPE: {"schema": _model_inline()}},
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


# --- AGS4: check and convert -------------------------------------------------------------


def _ags4_raw() -> dict[str, Any]:
    return {media: {"schema": {"type": "string", "format": "binary"}} for media in AGS4_MEDIA_TYPES}


_AGS4_ENCODING: Final = "application/octet-stream, text/csv, text/plain, */*"


def convert_parameters() -> list[dict[str, Any]]:
    """The query parameters of ``POST /v1/convert``."""
    return [
        _query("name", _name_schema(), "File name to report (reduced to a basename)."),
        _query(
            "to",
            {"enum": list(_service.CONVERT_TARGETS)},
            "The format to convert to (default: diggs; the body is then AGS4).",
        ),
        _query(
            "diggs_version",
            {"enum": list(_service.CONVERT_DIGGS_VERSIONS)},
            "The DIGGS version to write (default: 3.0.0).",
        ),
    ]


def convert_request_body() -> dict[str, Any]:
    """The request body of ``POST /v1/convert``: a raw AGS4 file or one multipart file."""
    form = {
        "schema": {
            "type": "object",
            "required": ["file"],
            "additionalProperties": False,
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "name": _name_schema(),
                "to": {"enum": list(_service.CONVERT_TARGETS)},
                "diggs_version": {"enum": list(_service.CONVERT_DIGGS_VERSIONS)},
            },
        },
        "encoding": {"file": {"contentType": _AGS4_ENCODING}},
    }
    return {"required": True, "content": {**_ags4_raw(), "multipart/form-data": form}}


def convert_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``POST /v1/convert``."""
    return {
        200: {
            "description": (
                "The conversion: the DIGGS XML (base64) and the findings. Losses, an output"
                " that fails validation and an exhausted time budget (complete: false) are"
                " 200s; the findings say what happened."
            ),
            "content": {FINDINGS_MEDIA_TYPE: {"schema": _conversion(_findings_inline())}},
        },
        **error_responses(400, 403, 406, 408, 413, 415, 422, 500, 503, 504),
    }


def ags4_check_parameters() -> list[dict[str, Any]]:
    """The query parameters of ``POST /v1/ags4/check``."""
    return [_query("name", _name_schema(), "File name to report (reduced to a basename).")]


def ags4_check_request_body() -> dict[str, Any]:
    """The request body of ``POST /v1/ags4/check``: a raw AGS4 file, or a multipart form.

    The form holds the file and, optionally, an AGS4 data dictionary.
    """
    form = {
        "schema": {
            "type": "object",
            "required": ["file"],
            "additionalProperties": False,
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "dictionary": {
                    "type": "string",
                    "format": "binary",
                    "description": (
                        "An AGS4 data dictionary (pyDIGGS bundles none); without one, the rules"
                        " that need it run only for the file's own DICT group."
                    ),
                },
                "name": _name_schema(),
            },
        },
        "encoding": {
            "file": {"contentType": _AGS4_ENCODING},
            "dictionary": {"contentType": _AGS4_ENCODING},
        },
    }
    return {"required": True, "content": {**_ags4_raw(), "multipart/form-data": form}}


def ags4_check_responses() -> dict[int | str, dict[str, Any]]:
    """The responses of ``POST /v1/ags4/check``."""
    return {
        200: {
            "description": (
                "A diggs-findings 1.1 document of step ags4 (or a SARIF 2.1.0 log). Rule"
                " breaks, content that is not AGS4 at all, truncation and an exhausted time"
                " budget are 200s with findings."
            ),
            "content": {
                FINDINGS_MEDIA_TYPE: {"schema": _findings_inline()},
                SARIF_MEDIA_TYPE: {"schema": _SARIF},
            },
        },
        **error_responses(400, 403, 406, 408, 413, 415, 422, 500, 503, 504),
    }
