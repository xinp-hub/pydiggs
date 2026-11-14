"""``pydiggs.capabilities()``: the catalogue is complete, true to the code, and on every surface."""

from __future__ import annotations

import codecs
import copy
import doctest
import inspect
import json
import pickle
import re
import subprocess
import sys
from importlib import resources
from pathlib import Path

import anyio
import jsonschema
import pytest

import pydiggs
from pydiggs import _capabilities, _service, ags4, frames, gis
from pydiggs._capabilities import (
    CAPABILITIES_SCHEMA_URL,
    OPTION_TYPES,
    STABILITIES,
    Capabilities,
    Operation,
    OptionSpec,
    capabilities,
    capabilities_schema,
)
from pydiggs._capabilities import _cli as capabilities_cli
from pydiggs.cli import main as cli_main
from pydiggs.detect import SCHEMA_PATHS
from pydiggs.findings.model import Step
from pydiggs.findings.run import NATIVE_STEPS
from pydiggs.preview import convert as preview_convert

ROOT = Path(__file__).resolve().parent.parent

# --- what every public operation and surface maps to ------------------------------------
#
# Each public function (pydiggs, pydiggs.ags4, pydiggs.frames, pydiggs.gis, and the
# previews in pydiggs.preview), HTTP route,
# MCP tool, sidecar method and CLI command is listed here with the operation it is, or
# in a META set with the reason it is not one. A new name anywhere fails a test below
# until it is listed, so a new operation cannot ship without a capabilities entry.

#: Public function -> the operation(s) it performs.
PUBLIC_FUNCTIONS: dict[str, tuple[str, ...]] = {
    "pydiggs.validate": ("validate",),
    "pydiggs.validate_bytes": ("validate",),
    "pydiggs.findings_document": ("validate",),  # validate's report as diggs-findings
    "pydiggs.findings_json": ("validate",),
    "pydiggs.read": ("read",),
    "pydiggs.read_bytes": ("read",),
    "pydiggs.detect_diggs_version": ("detect",),
    "pydiggs.rule_catalogue": ("rules.list",),
    "pydiggs.explain_rule": ("rules.explain",),
    "pydiggs.ags4.read": ("ags4.read",),
    "pydiggs.ags4.read_bytes": ("ags4.read",),
    "pydiggs.ags4.dumps": ("ags4.write",),
    "pydiggs.ags4.write": ("ags4.write",),
    "pydiggs.ags4.check": ("ags4.check",),
    "pydiggs.preview.convert.ags4_to_diggs": ("convert.ags4-to-diggs",),
    "pydiggs.frames.to_pandas": ("export.pandas",),
    "pydiggs.frames.to_polars": ("export.polars",),
    "pydiggs.gis.transform": ("gis.transform",),
    "pydiggs.gis.to_geodataframe": ("export.geodataframe",),
    "pydiggs.gis.write": ("export.gpkg", "export.geoparquet", "export.geojson"),
}
#: Public callables that describe pydiggs rather than act on data.
META_FUNCTIONS = {
    "pydiggs.capabilities": "this description",
    "pydiggs.capabilities_schema": "the schema of this description",
    "pydiggs.findings_schema": "a contract's schema",
    "pydiggs.model_schema": "a contract's schema",
    "pydiggs.version_info": "versions",
    "pydiggs.warm_up": "a performance helper",
    "pydiggs.validator": "the legacy one-check-at-a-time class; validate covers its checks",
    "pydiggs.preview.convert.mapping_info": "describes the mapping table, converts nothing",
}
#: The function whose signature each operation's options must match.
SIGNATURES: dict[str, tuple[str, ...]] = {
    "validate": ("pydiggs.validate", "pydiggs.validate_bytes"),
    "read": ("pydiggs.read", "pydiggs.read_bytes"),
    "detect": ("pydiggs.detect_diggs_version",),
    "rules.list": ("pydiggs._service.rules",),  # rule_catalogue() filters nothing
    "rules.explain": ("pydiggs.explain_rule",),
    "ags4.read": ("pydiggs.ags4.read", "pydiggs.ags4.read_bytes"),
    "ags4.write": ("pydiggs.ags4.dumps",),
    "ags4.check": ("pydiggs.ags4.check",),
    "convert.ags4-to-diggs": ("pydiggs.preview.convert.ags4_to_diggs",),
    "export.pandas": ("pydiggs.frames.to_pandas",),
    "export.polars": ("pydiggs.frames.to_polars",),
    "gis.transform": ("pydiggs.gis.transform",),
    "export.geodataframe": ("pydiggs.gis.to_geodataframe",),
    "export.gpkg": ("pydiggs.gis.write",),
    "export.geoparquet": ("pydiggs.gis.write",),
    "export.geojson": ("pydiggs.gis.write",),
}
#: HTTP route -> operation, or None for a route that describes the server.
HTTP_ROUTES: dict[str, str | None] = {
    "/v1/validate": "validate",
    "/v1/read": "read",
    "/v1/convert": "convert.ags4-to-diggs",
    "/v1/ags4/check": "ags4.check",
    "/v1/rules": "rules.list",
    "/v1/rules/{rule_id}": "rules.explain",
    "/v1/versions": None,
    "/v1/capabilities": None,
    "/v1/readyz": None,
    "/livez": None,
    "/readyz": None,
}
MCP_TOOLS: dict[str, str | None] = {
    "validate_diggs": "validate",
    "explain_rule": "rules.explain",
    "list_rules": "rules.list",
    "supported_versions": None,
    "list_features": "read",
    "get_feature": "read",
    "list_measurements": "read",
    "get_results": "read",
}
SIDECAR_METHODS: dict[str, str | None] = {
    "initialize": None,
    "validate": "validate",
    "read": "read",
    "convert": "convert.ags4-to-diggs",
    "ags4.check": "ags4.check",
    "ping": None,
    "versions": None,
    "rules.list": "rules.list",
    "rules.explain": "rules.explain",
    "shutdown": None,
}
#: ``pydiggs ags4 <command>`` -> operation.
AGS4_COMMANDS = {"check": "ags4.check"}

_CORE_INPUTS = tuple(f"diggs-{v}" for v in SCHEMA_PATHS)


def _resolve(dotted: str):
    module, _, name = dotted.rpartition(".")
    owner = {
        "pydiggs": pydiggs,
        "pydiggs.ags4": ags4,
        "pydiggs.frames": frames,
        "pydiggs.gis": gis,
        "pydiggs._service": _service,
        "pydiggs.preview.convert": preview_convert,
    }[module]
    return getattr(owner, name)


@pytest.fixture(scope="module")
def caps() -> Capabilities:
    return capabilities()


@pytest.fixture(scope="module")
def by_id(caps) -> dict[str, Operation]:
    return {op.id: op for op in caps.operations}


# --- the document ------------------------------------------------------------------------


def test_the_document_is_valid_against_the_packaged_schema(caps):
    schema = capabilities_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    document = json.loads(json.dumps(caps.to_dict()))
    assert list(document) == ["$schema", "schema_version", "pydiggs_version", "operations"]
    jsonschema.Draft202012Validator(schema).validate(document)
    assert schema["$id"] == CAPABILITIES_SCHEMA_URL == document["$schema"]
    assert schema["title"] == f"diggs-capabilities {pydiggs.CAPABILITIES_SCHEMA_VERSION}"


def test_the_schema_vocabularies_are_the_python_ones():
    defs = capabilities_schema()["$defs"]
    assert tuple(defs["stability"]["enum"]) == STABILITIES
    assert tuple(defs["option"]["properties"]["type"]["enum"]) == OPTION_TYPES
    assert set(defs["operation"]["required"]) == set(Operation.__dataclass_fields__)
    assert set(defs["option"]["required"]) == set(OptionSpec.__dataclass_fields__)


def test_the_schema_rejects_what_it_should(caps):
    validator = jsonschema.Draft202012Validator(capabilities_schema())
    document = caps.to_dict()
    for broken in (
        {**document, "operations": [{**document["operations"][0], "stability": "beta"}]},
        {**document, "schema_version": "2.0"},
        {k: v for k, v in document.items() if k != "operations"},
    ):
        assert not validator.is_valid(broken)
    # Open for consumers: an unknown field is fine.
    assert validator.is_valid({**document, "later": 1})


def test_the_schema_is_packaged_and_returned_fresh():
    packaged = resources.files("pydiggs._capabilities").joinpath(
        "diggs-capabilities-1.0.schema.json"
    )
    assert json.loads(packaged.read_text("utf-8")) == capabilities_schema()
    first = capabilities_schema()
    first["title"] = "changed"
    assert capabilities_schema()["title"] != "changed"


def test_a_broken_packaged_schema_is_a_resource_error(monkeypatch):
    monkeypatch.setattr(_capabilities, "_SCHEMA_RESOURCE", "missing.schema.json")
    with pytest.raises(pydiggs.ResourceError, match=r"missing\.schema\.json"):
        capabilities_schema()


def test_header_fields(caps):
    assert caps.pydiggs_version == pydiggs.__version__
    assert caps.schema_version == "1.0"
    ids = [op.id for op in caps.operations]
    assert len(ids) == len(set(ids))


def test_values_are_frozen_hashable_and_pickle(caps):
    op = caps.operations[0]
    with pytest.raises(AttributeError):
        op.id = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        op.options["new"] = OptionSpec(type="string", label="x")  # type: ignore[index]
    assert hash(op) == hash(copy.deepcopy(op))
    assert pickle.loads(pickle.dumps(caps)) == caps  # noqa: S301  # our own bytes
    assert copy.deepcopy(op) == op


def test_constructors_check_their_vocabularies():
    with pytest.raises(ValueError, match="stability"):
        Operation(id="x", label="x", stability="beta")
    with pytest.raises(ValueError, match=r"OptionSpec\.type"):
        OptionSpec(type="date", label="x")
    spec = OptionSpec(type="string[]", label="x", default=("a",), choices=["a", "b"])  # type: ignore[arg-type]
    assert spec.choices == ("a", "b")
    assert spec.to_dict()["default"] == ["a"]
    op = Operation(id="x", label="x", stability="stable", inputs=["a"], options={"o": spec})  # type: ignore[arg-type]
    assert op.inputs == ("a",)
    assert pickle.loads(pickle.dumps(op)) == op  # noqa: S301  # our own bytes


# --- complete and true to the code -------------------------------------------------------


def _public_functions() -> set[str]:
    out = set()
    for prefix, module in (
        ("pydiggs", pydiggs),
        ("pydiggs.ags4", ags4),
        ("pydiggs.frames", frames),
        ("pydiggs.gis", gis),
        ("pydiggs.preview.convert", preview_convert),
    ):
        for name in module.__all__:
            obj = getattr(module, name)
            if inspect.isfunction(obj) or obj is pydiggs.validator:
                out.add(f"{prefix}.{name}")
    return out


def test_every_public_function_is_an_operation_or_listed_as_meta(by_id):
    assert _public_functions() == set(PUBLIC_FUNCTIONS) | set(META_FUNCTIONS)
    for name, ops in PUBLIC_FUNCTIONS.items():
        assert set(ops) <= set(by_id), name


def test_every_operation_is_some_public_function(by_id):
    covered = {op for ops in PUBLIC_FUNCTIONS.values() for op in ops}
    assert covered == set(by_id)
    assert set(SIGNATURES) == set(by_id)


def test_every_http_route_is_mapped(by_id):
    from pydiggs.server import ServerSettings, create_app

    paths = set(create_app(ServerSettings(workers=1)).openapi()["paths"])
    assert paths == set(HTTP_ROUTES)
    assert {v for v in HTTP_ROUTES.values() if v} <= set(by_id)


def test_every_mcp_tool_is_mapped(by_id):
    pytest.importorskip("mcp")
    from pydiggs.mcp_server._tools import TOOL_NAMES

    assert set(TOOL_NAMES) == set(MCP_TOOLS)
    assert {v for v in MCP_TOOLS.values() if v} <= set(by_id)


def test_every_sidecar_method_is_mapped(by_id):
    document = json.loads((ROOT / "src/pydiggs/sidecar/openrpc.json").read_text("utf-8"))
    assert {m["name"] for m in document["methods"]} == set(SIDECAR_METHODS)
    assert {v for v in SIDECAR_METHODS.values() if v} <= set(by_id)


def test_every_ags4_command_is_mapped(by_id):
    from pydiggs.ags4 import _cli

    parser = _cli.build_parser()
    commands = next(a for a in parser._actions if a.dest == "command").choices
    assert set(commands) == set(AGS4_COMMANDS)
    assert set(AGS4_COMMANDS.values()) <= set(by_id)


@pytest.mark.parametrize("op_id", sorted(SIGNATURES))
def test_options_are_keyword_parameters_with_the_same_defaults(op_id, by_id):
    op = by_id[op_id]
    for dotted in SIGNATURES[op_id]:
        params = inspect.signature(_resolve(dotted)).parameters
        for name, spec in op.options.items():
            assert name in params, f"{op_id}: {dotted} has no parameter {name}"
            param = params[name]
            if spec.required:
                assert param.default is inspect.Parameter.empty, (op_id, name)
            else:
                assert param.default == spec.default, (op_id, name, param.default)


def test_choices_come_from_the_code(by_id):
    validate = by_id["validate"]
    assert validate.options["diggs_version"].choices == tuple(SCHEMA_PATHS)
    assert validate.options["checks"].choices == tuple(s.value for s in NATIVE_STEPS)
    assert by_id["read"].options["diggs_version"].choices == tuple(SCHEMA_PATHS)
    assert by_id["rules.list"].options["step"].choices == tuple(s.value for s in Step)
    assert by_id["export.pandas"].options["view"].choices == frames.VIEWS
    from pydiggs.ags4._io import _ENCODINGS

    encoding = by_id["ags4.check"].options["encoding"].choices
    assert encoding is not None
    assert {codecs.lookup(c).name for c in encoding} == {codecs.lookup(c).name for c in _ENCODINGS}


def test_inputs_and_outputs_name_the_real_versions(by_id):
    findings = f"diggs-findings-{pydiggs.FINDINGS_SCHEMA_VERSION}"
    model = f"diggs-model-{pydiggs.MODEL_SCHEMA_VERSION}"
    for op_id in ("validate", "read", "detect"):
        assert by_id[op_id].inputs == _CORE_INPUTS
    assert by_id["validate"].outputs == (findings,)
    assert by_id["read"].outputs == (model,)
    ags = tuple(f"ags4-{v}" for v in ags4.AGS_VERSIONS)
    assert by_id["ags4.read"].inputs == ags == by_id["ags4.write"].outputs
    assert by_id["ags4.check"].outputs == (findings,)
    # One file export per gis.write driver.
    exports = {i for i in by_id if i in _capabilities._DRIVER_OF}
    assert set(_capabilities._DRIVER_OF.values()) == set(gis.DRIVERS.values())
    assert len(exports) == len(gis.DRIVERS)


def test_stability_matches_what_the_docs_promise(by_id):
    preview = {i for i, op in by_id.items() if op.stability == "preview"}
    assert preview == {"ags4.read", "ags4.write", "ags4.check", "convert.ags4-to-diggs"}
    assert not {i for i, op in by_id.items() if op.stability == "experimental"}


def test_needs_name_real_extras(by_id):
    from importlib import metadata

    extras = set(metadata.metadata("pydiggs").get_all("Provides-Extra") or [])
    for op in by_id.values():
        assert set(op.needs) <= extras, op.id
    core = {i for i, op in by_id.items() if not op.needs}
    assert core == {
        "validate",
        "read",
        "detect",
        "rules.list",
        "rules.explain",
        "ags4.read",
        "ags4.write",
        "ags4.check",
        "convert.ags4-to-diggs",
    }


# --- availability ------------------------------------------------------------------------


def test_the_core_is_always_available(by_id):
    assert all(op.available for op in by_id.values() if not op.needs)


def test_a_missing_extra_is_reported_unavailable(monkeypatch):
    real = _capabilities._installed
    monkeypatch.setattr(_capabilities, "_installed", lambda m: m != "pyogrio" and real(m))
    ops = {op.id: op for op in capabilities().operations}
    assert not ops["export.gpkg"].available
    assert not ops["export.geojson"].available
    assert ops["validate"].available

    monkeypatch.setattr(_capabilities, "_installed", lambda m: False)
    ops = {op.id: op for op in capabilities().operations}
    assert {i for i, op in ops.items() if op.available} == {
        i for i, op in ops.items() if not op.needs
    }


def test_installed_never_imports_and_survives_odd_modules(monkeypatch):
    assert _capabilities._installed("json")
    assert not _capabilities._installed("pydiggs_no_such_module")
    assert not _capabilities._installed("pydiggs_no_such_module.child")
    fake = type(sys)("pydiggs_fake_no_spec")
    fake.__spec__ = None
    monkeypatch.setitem(sys.modules, "pydiggs_fake_no_spec", fake)
    assert not _capabilities._installed("pydiggs_fake_no_spec")


def test_asking_imports_no_optional_library():
    code = (
        "import sys, pydiggs\n"
        "pydiggs.capabilities()\n"
        "heavy = {'pandas', 'polars', 'geopandas', 'pyproj', 'shapely', 'pyogrio', 'pyarrow',"
        " 'fastapi', 'mcp', 'rich'}\n"
        "print(sorted(heavy & set(sys.modules)))\n"
    )
    out = subprocess.run(  # noqa: S603  # our own interpreter, fixed code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip() == "[]"


# --- surfaces ----------------------------------------------------------------------------


def test_cli_json_is_the_document(capsys):
    assert cli_main(["capabilities", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == capabilities().to_dict()


def test_cli_text_lists_every_operation(capsys, monkeypatch):
    real = _capabilities._installed
    monkeypatch.setattr(_capabilities, "_installed", lambda m: m != "polars" and real(m))
    assert cli_main(["capabilities"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"pydiggs {pydiggs.__version__}\n")
    for op in capabilities().operations:
        assert op.id in out
    assert "(needs pydiggs[polars])" in out


def test_cli_usage_error_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["capabilities", "--nope"])
    assert exc.value.code == 2
    # Python 3.14's argparse colours its usage line when FORCE_COLOR is set (as in CI).
    err = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().err)
    assert "usage: pydiggs capabilities" in err


def test_service_answers_the_document():
    assert _service.capabilities() == capabilities().to_dict()


def test_http_capabilities():
    from starlette.testclient import TestClient

    from pydiggs.server import ServerSettings, create_app

    settings = ServerSettings(workers=1, isolation="thread", allowed_hosts=("testserver",))
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/capabilities")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body == capabilities().to_dict()
        jsonschema.Draft202012Validator(capabilities_schema()).validate(body)
    off = ServerSettings(
        workers=1, isolation="thread", allowed_hosts=("testserver",), versions_enabled=False
    )
    with TestClient(create_app(off)) as client:
        assert client.get("/v1/capabilities").status_code == 404


def test_openapi_documents_the_route_with_the_packaged_schema():
    from pydiggs.server import _schemas

    document = json.loads((ROOT / "src/pydiggs/server/openapi.json").read_text("utf-8"))
    route = document["paths"]["/v1/capabilities"]["get"]
    schema = route["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/Capabilities"}
    component = document["components"]["schemas"]["Capabilities"]
    assert component == _schemas._capabilities_inline()
    jsonschema.Draft202012Validator(component).validate(capabilities().to_dict())


def test_mcp_resource():
    pytest.importorskip("mcp")
    from mcp import Client

    from pydiggs.mcp_server import create_server
    from pydiggs.mcp_server._tools import RESOURCE_URIS

    async def main():
        async with Client(create_server()) as client:
            listed = (await client.list_resources()).resources
            read = await client.read_resource(RESOURCE_URIS[0])
            return listed, read

    listed, read = anyio.run(main)
    assert [str(r.uri) for r in listed] == list(RESOURCE_URIS)
    assert listed[0].mime_type == "application/json"
    assert json.loads(read.contents[0].text) == capabilities().to_dict()


def test_pyodide_case_holds_the_core_operations():
    spec_dir = ROOT / "tests" / "pyodide"
    sys.path.insert(0, str(spec_dir))
    try:
        import cases  # type: ignore[import-not-found]

        out = cases.run(str(spec_dir / "fixtures"))
    finally:
        sys.path.remove(str(spec_dir))
    document = json.loads(out["capabilities"])
    assert [op["id"] for op in document["operations"]] == [
        op.id for op in capabilities().operations if not op.needs
    ]


def test_docs_hook_publishes_the_schema_byte_for_byte(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
    )
    assert spec is not None
    assert spec.loader is not None
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    hooks.on_post_build({"site_dir": str(tmp_path)})
    published = tmp_path / "schemas" / "diggs-capabilities-1.0.schema.json"
    packaged = resources.files("pydiggs._capabilities").joinpath(published.name)
    assert published.read_bytes() == packaged.read_bytes()
    assert (
        "https://xinp-hub.github.io/pydiggs/" + published.relative_to(tmp_path).as_posix()
    ) == CAPABILITIES_SCHEMA_URL


@pytest.mark.parametrize("module", [_capabilities, capabilities_cli], ids=str)
def test_docstring_examples_run(module, monkeypatch):
    monkeypatch.chdir(ROOT)
    failed, attempted = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert attempted > 0
    assert failed == 0, module.__name__


def test_every_new_public_name_has_a_runnable_example():
    for name in ("Capabilities", "Operation", "OptionSpec", "capabilities", "capabilities_schema"):
        doc = inspect.getdoc(getattr(pydiggs, name)) or ""
        assert "Examples:" in doc and ">>>" in doc, name
