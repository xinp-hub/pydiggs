"""``pydiggs serve``: arguments, mode dispatch, missing extras, logging and the stubs."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pydiggs import _serve_cli
from pydiggs._serve_cli import ServeOptions
from pydiggs.cli import main

ROOT = Path(__file__).resolve().parent.parent
ANY_ADDRESS = "0.0.0.0"  # noqa: S104  # only ever a string under test, never bound


@pytest.fixture(autouse=True)
def _restore_logging():
    logger = logging.getLogger("pydiggs")
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def _serve(capsys, *argv):
    code = main(["serve", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_help_lists_the_modes_and_none_of_the_trusted_resource_options(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["serve", "--help"])
    assert exit_info.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    for option in ("--stdio", "--http", "--max-bytes", "--timeout", "--allow-path"):
        assert option in text
    for forbidden in (
        "--mcp",  # reserved for a later release: hidden
        "MCP",
        "pydiggs[mcp]",
        "--schema-path",
        "--dictionary-path",
        "--schematron-path",
        "--allow-network",
        "--snippets",
        "--base-dir",
        "--local-dictionaries",
    ):
        assert forbidden not in text
    assert "hard limit" in text


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--stdio", "--http"],
        ["--stdio", "--workers", "2"],
        ["--mcp", "--host", ANY_ADDRESS],
        ["--stdio", "--port", "80"],
        ["--http", "--allow-path", "/data"],
        ["--http", "--port", "0"],
        ["--http", "--port", "70000"],
        ["--stdio", "--max-bytes", "0"],
        ["--stdio", "--max-findings", "-1"],
        ["--http", "--workers", "0"],
        ["--stdio", "--timeout", "0"],
        ["--stdio", "--timeout", "nan"],
        ["--stdio", "--timeout", "86401"],
        ["--stdio", "--allow-path", ""],
        ["--stdio", "--schema-path", "x.xsd"],
        ["--stdio", "--log-level", "loud"],
    ],
)
def test_bad_arguments_exit_2(capsys, argv):
    with pytest.raises(SystemExit) as exit_info:
        main(["serve", *argv])
    assert exit_info.value.code == 2
    assert "pydiggs serve" in capsys.readouterr().err


def test_stdio_gets_its_options(monkeypatch):
    import pydiggs.sidecar

    seen = []
    monkeypatch.setattr(pydiggs.sidecar, "serve_stdio", lambda options: seen.append(options) or 0)
    assert main(["serve", "--stdio", "--allow-path", "/a", "--allow-path", "/b"]) == 0
    assert main(["serve", "--stdio", "--max-bytes", "200000000", "--timeout", "5"]) == 0
    assert main(["serve", "--stdio", "--max-bytes", "1000"]) == 0
    default, custom, small = seen
    assert default == ServeOptions(allow_paths=("/a", "/b"))
    assert default.max_path_bytes == 100 * 2**20
    assert (custom.max_bytes, custom.max_path_bytes, custom.timeout) == (200_000_000,) * 2 + (5.0,)
    # --max-bytes, when given, is the path-mode limit too, smaller or larger.
    assert (small.max_bytes, small.max_path_bytes) == (1000, 1000)


def test_mcp_is_reserved_exits_2_and_imports_nothing(monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "pydiggs.mcp_server", raising=False)
    monkeypatch.setitem(sys.modules, "mcp", None)  # an import would fail loudly
    code, out, err = _serve(capsys, "--mcp", "--log-format", "json")
    assert (code, out) == (2, "")
    assert "--mcp is not available in this release" in err
    assert "pydiggs[mcp]" not in err
    assert "pydiggs.mcp_server" not in sys.modules


def test_http_settings_come_from_the_environment_then_the_flags(monkeypatch):
    import pydiggs.server

    seen = []
    monkeypatch.setattr(
        pydiggs.server, "serve_http", lambda s, host, port: seen.append((s, host, port)) or 0
    )
    monkeypatch.setenv("PYDIGGS_MAX_FINDINGS", "500")
    monkeypatch.setenv("PYDIGGS_WORKERS", "3")
    monkeypatch.delenv("PYDIGGS_ISOLATION", raising=False)
    monkeypatch.delenv("PYDIGGS_QUEUE_DEPTH", raising=False)
    assert main(["serve", "--http"]) == 0
    assert main(["serve", "--http", "--workers", "2", "--timeout", "9", "--max-bytes", "99"]) == 0
    assert main(["serve", "--http", "--max-findings", "7", "--host", "::1", "--port", "9"]) == 0
    (env, host, port), (flags, *_), (third, host3, port3) = seen
    assert (env.max_findings, env.workers, env.isolation, host, port) == (
        500,
        3,
        "process",
        "127.0.0.1",
        8000,
    )
    assert (flags.workers, flags.timeout_s, flags.max_body_bytes, flags.queue_limit) == (
        2,
        9,
        99,
        4,
    )
    assert (third.max_findings, host3, port3) == (7, "::1", 9)


def test_an_explicit_queue_depth_survives_a_workers_flag(monkeypatch):
    import pydiggs.server

    seen = []
    monkeypatch.setattr(pydiggs.server, "serve_http", lambda s, h, p: seen.append(s) or 0)
    monkeypatch.setenv("PYDIGGS_QUEUE_DEPTH", "1")
    assert main(["serve", "--http", "--workers", "4"]) == 0
    assert seen[0].queue_limit == 1


def test_bad_environment_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("PYDIGGS_WORKERS", "lots")
    code, _, err = _serve(capsys, "--http")
    assert code == 2 and "PYDIGGS_WORKERS" in err


@pytest.mark.parametrize(
    "case",
    [
        ("--http", "fastapi", "pydiggs.server", "server"),
        ("--http", "uvicorn", "pydiggs.server", "server"),
    ],
)
def test_a_missing_extra_exits_2_naming_it(monkeypatch, capsys, case):
    mode, module, package, extra = case
    monkeypatch.setitem(sys.modules, module, None)  # import -> ModuleNotFoundError
    monkeypatch.delitem(sys.modules, package, raising=False)
    code, out, err = _serve(capsys, mode)
    assert (code, out) == (2, "")
    assert f"'{extra}' extra" in err and f"pydiggs[{extra}]" in err
    with pytest.raises(ImportError, match=f"requires the '{extra}' extra") as caught:
        importlib.import_module(package)
    assert caught.value.name == module


def test_the_extra_message_is_rebuilt_for_a_bare_import_error():
    exc = ImportError("No module named 'uvicorn'", name="uvicorn")
    assert _serve_cli._extra_message("server", exc) == (
        "pydiggs.server requires the 'server' extra: pip install 'pydiggs[server]'"
    )


class _Broken(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Makes importing ``target`` fail as if one of *its* dependencies were missing."""

    def __init__(self, target):
        self.target = target

    def find_spec(self, name, path=None, target=None):
        if name == self.target:
            return importlib.machinery.ModuleSpec(name, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise ModuleNotFoundError("No module named 'unrelated'", name="unrelated")


@pytest.mark.parametrize(("target", "package", "mode"), [("fastapi", "pydiggs.server", "--http")])
def test_any_other_import_error_propagates(monkeypatch, target, package, mode):
    monkeypatch.delitem(sys.modules, target, raising=False)
    monkeypatch.delitem(sys.modules, package, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Broken(target), *sys.meta_path])
    with pytest.raises(ModuleNotFoundError) as caught:
        importlib.import_module(package)
    assert caught.value.name == "unrelated"
    monkeypatch.delitem(sys.modules, package, raising=False)
    with pytest.raises(ModuleNotFoundError):
        main(["serve", mode])


@pytest.mark.parametrize(
    ("argv", "text"),
    [
        (["--mcp"], "--mcp is not available in this release"),
        # The real server refuses a public bind without allowed hosts, before binding.
        (["--http", "--host", ANY_ADDRESS], "PYDIGGS_ALLOWED_HOSTS"),
    ],
)
def test_the_stubs_report_and_exit_2(capsys, argv, text):
    code, out, err = _serve(capsys, *argv)
    assert (code, out) == (2, "")
    assert text in err


# --- ServeOptions and logging ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_bytes": 0},
        {"max_path_bytes": True},
        {"max_findings": -1},
        {"workers": 1.5},
        {"timeout": "60"},
        {"timeout": True},
        {"timeout": float("inf")},
        {"allow_paths": ["/a"]},
        {"allow_paths": ("",)},
        {"log_level": "loud"},
        {"log_format": "xml"},
    ],
)
def test_serve_options_are_validated(kwargs):
    with pytest.raises(ValueError):
        ServeOptions(**kwargs)


def test_default_workers_is_clamped(monkeypatch):
    for cpus, expected in ((None, 1), (1, 1), (2, 1), (4, 3), (64, 4)):
        monkeypatch.setattr(os, "cpu_count", lambda c=cpus: c)
        assert _serve_cli.default_workers() == expected


def _emit(fmt, message, **extra):
    stream = io.StringIO()
    handler = _serve_cli.configure_logging("info", fmt)
    handler.setStream(stream)
    logging.getLogger("pydiggs.test").info(message, extra=extra)
    return stream.getvalue()


def test_text_logs_cannot_be_forged_by_client_strings():
    line = _emit("text", "client said \x1b[31mhi\nfake line\u202e", request_id="r\n1")
    assert line.count("\n") == 1
    assert "\\x1b" in line and "\\n" in line and "\\u202e" in line and "request_id=r\\n1" in line
    assert " info " in line


def test_json_logs_have_the_documented_fields():
    record = json.loads(_emit("json", "hello", request_id=7))
    assert set(record) == {"ts", "level", "msg", "request_id"}
    assert (record["level"], record["msg"], record["request_id"]) == ("info", "hello", "7")
    assert record["ts"].endswith("Z")
    assert json.loads(_emit("json", "x"))["request_id"] is None


def test_configure_logging_replaces_its_own_handler_only():
    logger = logging.getLogger("pydiggs")
    other = logging.NullHandler()
    logger.addHandler(other)
    first = _serve_cli.configure_logging("warning", "text")
    second = _serve_cli.configure_logging("debug", "json")
    assert first not in logger.handlers and second in logger.handlers and other in logger.handlers
    assert logger.level == logging.DEBUG and logger.propagate is False


# --- ServerSettings ----------------------------------------------------------------------------


def test_server_settings_defaults_and_hosts():
    from pydiggs.server import ServerSettings

    s = ServerSettings(workers=3)
    assert (s.max_body_bytes, s.max_findings, s.timeout_s, s.worker_memory_mb) == (
        25 * 2**20,
        10_000,
        60.0,
        2048,
    )
    assert (s.queue_limit, s.admission_slots, s.isolation, s.cors_origins) == (6, 9, None, ())
    assert (s.reject_cross_site, s.docs_enabled, s.versions_enabled) == (True, False, True)
    assert ServerSettings(workers=2, queue_depth=0).admission_slots == 2
    for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        assert s.hosts_for(host) == ("127.0.0.1", "localhost", "[::1]")
    with pytest.raises(ValueError, match="PYDIGGS_ALLOWED_HOSTS"):
        s.hosts_for(ANY_ADDRESS)
    assert ServerSettings(allowed_hosts=("a.example",)).hosts_for(ANY_ADDRESS) == ("a.example",)
    assert 1 <= ServerSettings().workers <= 4


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_body_bytes": 0},
        {"workers": False},
        {"queue_depth": -1},
        {"timeout_s": 0},
        {"timeout_s": float("nan")},
        {"timeout_s": True},
        {"body_timeout_s": 0},
        {"body_timeout_s": float("inf")},
        {"body_timeout_s": 86_401},
        {"isolation": "fork"},
        {"cors_origins": ["https://a"]},
        {"cors_origins": ("*",)},
        {"allowed_hosts": ("*",)},
        {"allowed_hosts": ("",)},
        {"docs_enabled": "yes"},
    ],
)
def test_server_settings_are_validated(kwargs):
    from pydiggs.server import ServerSettings

    with pytest.raises(ValueError):
        ServerSettings(**kwargs)


def test_server_settings_from_env():
    from pydiggs.server import ServerSettings

    env = {
        "PYDIGGS_MAX_BODY_BYTES": "1000",
        "PYDIGGS_MAX_FINDINGS": " 20 ",
        "PYDIGGS_TIMEOUT_S": "2.5",
        "PYDIGGS_BODY_TIMEOUT_S": " 7.5 ",
        "PYDIGGS_WORKERS": "2",
        "PYDIGGS_QUEUE_DEPTH": "0",
        "PYDIGGS_ISOLATION": "thread",
        "PYDIGGS_WORKER_MEMORY_MB": "512",
        "PYDIGGS_CORS_ORIGINS": "https://a.example, https://b.example,",
        "PYDIGGS_ALLOWED_HOSTS": "a.example",
        "PYDIGGS_REJECT_CROSS_SITE": "off",
        "PYDIGGS_DOCS_ENABLED": "TRUE",
        "PYDIGGS_VERSIONS_ENABLED": "0",
    }
    s = ServerSettings.from_env(env, isolation="process")
    assert (s.max_body_bytes, s.max_findings, s.timeout_s, s.workers, s.queue_limit) == (
        1000,
        20,
        2.5,
        2,
        0,
    )
    assert (s.isolation, s.worker_memory_mb, s.allowed_hosts) == ("thread", 512, ("a.example",))
    assert s.body_timeout_s == 7.5
    assert ServerSettings().body_timeout_s == 30
    assert s.cors_origins == ("https://a.example", "https://b.example")
    assert (s.reject_cross_site, s.docs_enabled, s.versions_enabled) == (False, True, False)
    assert ServerSettings.from_env({}, isolation="process").isolation == "process"
    assert ServerSettings.from_env({}).isolation is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PYDIGGS_TIMEOUT_S", "soon"),
        ("PYDIGGS_BODY_TIMEOUT_S", "soon"),
        ("PYDIGGS_BODY_TIMEOUT_S", "-1"),
        ("PYDIGGS_DOCS_ENABLED", "maybe"),
        ("PYDIGGS_ISOLATION", "fork"),
        ("PYDIGGS_CORS_ORIGINS", "*"),
    ],
)
def test_bad_environment_values_name_the_variable(name, value):
    from pydiggs.server import ServerSettings

    with pytest.raises(ValueError, match="PYDIGGS_"):
        ServerSettings.from_env({name: value})


def test_the_mcp_stub_names_no_extra_that_does_not_exist(monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.delitem(sys.modules, "pydiggs.mcp_server", raising=False)
    with pytest.raises(ImportError, match="not available in this release") as info:
        import pydiggs.mcp_server  # noqa: F401
    assert "pydiggs[mcp]" not in str(info.value)


def test_the_stub_entry_points_have_the_frozen_signatures(monkeypatch):
    import inspect
    import types

    # The MCP SDK is not a dependency of this release; the kept stub only probes for it.
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.delitem(sys.modules, "pydiggs.mcp_server", raising=False)
    from pydiggs.mcp_server import serve_mcp
    from pydiggs.server import ServerSettings, serve_http
    from pydiggs.sidecar import serve_stdio

    assert str(inspect.signature(serve_stdio)) == "(options: 'ServeOptions') -> 'int'"
    assert str(inspect.signature(serve_mcp)) == "(options: 'ServeOptions') -> 'int'"
    assert str(inspect.signature(serve_http)) == (
        "(settings: 'ServerSettings', host: 'str', port: 'int') -> 'int'"
    )
    assert serve_mcp(ServeOptions()) == 2
    assert serve_http(ServerSettings(), ANY_ADDRESS, 8000) == 2  # no allowed hosts: refused


def test_the_core_sidecar_imports_no_extra_and_no_multiprocessing():
    code = (
        "import sys, pydiggs, pydiggs.sidecar, pydiggs._service\n"
        "bad = ['fastapi', 'mcp', 'rich', 'multiprocessing', 'uvicorn', 'starlette']\n"
        "print(sorted(m for m in bad if m in sys.modules))\n"
    )
    out = subprocess.run(  # noqa: S603  # fixed argv
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert out.stdout.strip() == "[]"


def test_python_dash_m_pydiggs_serve(tmp_path):
    out = subprocess.run(  # fixed argv
        [sys.executable, "-m", "pydiggs", "serve", "--stdio"],
        input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
        timeout=60,
    )
    assert out.returncode == 0  # stdin EOF ends the sidecar cleanly
    assert json.loads(out.stdout)["error"]["data"]["code"] == "not-initialized"
