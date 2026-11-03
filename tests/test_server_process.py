"""``pydiggs.server`` with process isolation, the uvicorn runner and a real ``serve --http``."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import ClassVar

import pytest
import uvicorn
from fastapi import FastAPI
from starlette.testclient import TestClient

from pydiggs import _pool, _service
from pydiggs._service import Request
from pydiggs.server import ServerSettings, _run, create_app, create_router, serve_http

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"
XML = {"content-type": "application/xml"}
PROCESS = ServerSettings(workers=1, isolation="process", allowed_hosts=("testserver",))


class FakePool:
    """Stands in for ``WorkerPool``: runs jobs in-process, becomes ready on the 2nd probe."""

    instances: ClassVar[list[FakePool]] = []

    def __init__(self, workers, *, timeout, memory_mb):
        self.args = (workers, timeout, memory_mb)
        self.started = 0
        self.probes = 0
        self.closed = False
        self.fail: BaseException | None = None
        FakePool.instances.append(self)

    def start(self):
        self.started += 1

    def ready(self):
        self.probes += 1
        return self.probes > 1

    def close(self):
        self.closed = True

    def run(self, function, *args, timeout):
        if self.fail is not None:
            raise self.fail
        return function(*args)


@pytest.fixture
def fake_pool(monkeypatch):
    FakePool.instances = []
    monkeypatch.setattr(_service, "make_pool", FakePool)
    return FakePool


def test_the_lifespan_starts_and_closes_the_pool(fake_pool):
    app = create_app(dataclasses.replace(PROCESS, timeout_s=30, worker_memory_mb=512))
    data = SCHEMA_ERROR.read_bytes()
    with TestClient(app) as client:
        (pool,) = fake_pool.instances
        assert pool.args == (1, 30, 512) and pool.started == 1
        first = client.get("/readyz")
        assert first.status_code == 503 and first.headers["retry-after"] == "1"
        assert first.json()["code"] == "not-ready"
        assert client.get("/readyz").json() == {"status": "ready"}
        response = client.post("/v1/validate", params={"name": "a.xml"}, content=data, headers=XML)
        assert response.status_code == 200
        expected = _service.validate(Request(content=data, name="a.xml"))
        assert response.content == expected.document
    assert pool.closed


def test_without_a_lifespan_the_first_probe_starts_the_pool(fake_pool):
    app = FastAPI()
    app.include_router(create_router(dataclasses.replace(PROCESS, isolation="process")))
    client = TestClient(app)  # no ``with``: the lifespan never runs
    assert fake_pool.instances == []
    assert client.get("/v1/readyz").status_code == 503
    (pool,) = fake_pool.instances
    assert pool.started == 1
    assert client.get("/v1/readyz").status_code == 200


def test_a_hard_timeout_is_504(fake_pool):
    client = TestClient(create_app(PROCESS))
    client.get("/readyz")
    fake_pool.instances[0].fail = _pool.JobTimeoutError("late")
    response = client.post("/v1/validate", content=b"<Diggs/>", headers=XML)
    assert response.status_code == 504
    body = response.json()
    assert (body["code"], body["detail"]) == ("timeout", "Validation did not finish within 60 s.")


def test_a_worker_crash_is_a_fixed_500(fake_pool):
    client = TestClient(create_app(PROCESS))
    client.get("/readyz")
    fake_pool.instances[0].fail = _pool.WorkerCrashedError("died at /opt/venv/x")
    response = client.post("/v1/validate", content=b"<Diggs/>", headers=XML)
    assert response.status_code == 500
    assert response.json()["code"] == "internal" and "/opt/venv" not in response.text


@pytest.mark.timeout(180)
def test_a_real_worker_times_out_and_is_replaced():
    settings = dataclasses.replace(PROCESS, timeout_s=0.2)
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/validate", content=SCHEMA_ERROR.read_bytes(), headers=XML)
        assert response.status_code == 504 and response.json()["code"] == "timeout"


# --- serve_http --------------------------------------------------------------------------------


def test_uvicorn_gets_explicit_limits():
    config = _run.uvicorn_config(ServerSettings(workers=2, queue_depth=3), "127.0.0.1", 8123)
    assert (config.host, config.port, config.limit_concurrency) == ("127.0.0.1", 8123, 9)
    assert (config.http, config.ws, config.lifespan) == ("h11", "none", "on")
    assert config.access_log is False and config.server_header is False
    assert config.timeout_keep_alive == 5 and config.h11_max_incomplete_event_size == 16 * 2**10
    assert config.log_config is None and config.proxy_headers is False
    assert config.timeout_graceful_shutdown == 65
    config = _run.uvicorn_config(ServerSettings(workers=1, timeout_s=0.5), "127.0.0.1", 8123)
    assert config.timeout_graceful_shutdown == 6


def test_serve_http_refuses_a_public_bind_without_hosts(capsys):
    assert serve_http(ServerSettings(), "0.0.0.0", 8000) == 2  # noqa: S104  # never bound
    assert "PYDIGGS_ALLOWED_HOSTS" in capsys.readouterr().err


def test_serve_http_reports_a_port_in_use():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert serve_http(ServerSettings(workers=1, isolation="thread"), "127.0.0.1", port) == 2


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [("interrupt", 0), ("never-started", 2), ("clean-exit", 0), ("started", 0)],
)
def test_serve_http_exit_statuses(monkeypatch, behaviour, expected):
    def run(self, sockets=None):
        if behaviour == "interrupt":
            raise KeyboardInterrupt
        if behaviour == "clean-exit":
            raise SystemExit(0)
        self.started = behaviour == "started"

    monkeypatch.setattr(uvicorn.Server, "run", run)
    settings = ServerSettings(workers=1, isolation="thread")
    assert serve_http(settings, "127.0.0.1", 8000) == expected


def test_serve_http_logs_thread_isolation(monkeypatch, caplog):
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: None)
    serve_http(ServerSettings(workers=1, isolation="thread"), "127.0.0.1", 8000)
    assert "isolation='thread'" in caplog.text


# --- a real server -----------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(url: str, headers: dict | None = None, data: bytes | None = None):
    request = urllib.request.Request(url, data=data, headers=headers or {})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # loopback
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _wait_for(url: str, status: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            if _get(url)[0] == status:
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"{url} never answered {status}")


@pytest.mark.timeout(180)
def test_pydiggs_serve_http_in_a_subprocess():
    port = _free_port()
    env = {**os.environ, "PYDIGGS_WORKERS": "1"}
    env.pop("PYDIGGS_ALLOWED_HOSTS", None)
    server = subprocess.Popen(  # noqa: S603  # fixed argv
        [sys.executable, "-m", "pydiggs", "serve", "--http", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 120
        _wait_for(f"{base}/livez", 200, deadline)
        _wait_for(f"{base}/readyz", 200, deadline)
        data = SCHEMA_ERROR.read_bytes()
        status, headers, body = _get(
            f"{base}/v1/validate?name=secret-name.xml", headers=XML, data=data
        )
        assert status == 200 and "server" not in {k.lower() for k in headers}
        expected = _service.validate(Request(content=data, name="secret-name.xml"))
        assert body == expected.document
        status, _, body = _get(f"{base}/livez", headers={"Host": "rebind.example"})
        assert status == 400 and json.loads(body)["code"] == "invalid-host"
    finally:
        if sys.platform == "win32":  # pragma: no cover
            server.terminate()
        else:
            server.send_signal(signal.SIGINT)
        out, err = server.communicate(timeout=60)
    assert server.returncode in (0, -signal.SIGINT)
    assert out == b""
    assert b"secret-name" not in err  # no access log


@pytest.mark.timeout(120)
@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is TerminateProcess on Windows")
def test_sigterm_stops_serve_http_while_a_body_is_stalled():
    port = _free_port()
    env = {
        **os.environ,
        "PYDIGGS_WORKERS": "1",
        "PYDIGGS_ISOLATION": "thread",
        "PYDIGGS_TIMEOUT_S": "1",
        "PYDIGGS_BODY_TIMEOUT_S": "600",  # only the graceful-shutdown limit can end it
    }
    env.pop("PYDIGGS_ALLOWED_HOSTS", None)
    server = subprocess.Popen(  # noqa: S603  # fixed argv
        [sys.executable, "-m", "pydiggs", "serve", "--http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        _wait_for(f"http://127.0.0.1:{port}/livez", 200, time.monotonic() + 60)
        with socket.create_connection(("127.0.0.1", port), timeout=30) as stalled:
            stalled.sendall(
                b"POST /v1/validate HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: application/xml\r\nContent-Length: 1000\r\n\r\n<"
            )
            time.sleep(0.5)
            started = time.monotonic()
            server.send_signal(signal.SIGTERM)
            returncode = server.wait(timeout=60)
            elapsed = time.monotonic() - started
    finally:
        if server.poll() is None:  # pragma: no cover - only when the test fails
            server.kill()
            server.wait()
    assert returncode in (0, -signal.SIGTERM)  # uvicorn re-raises the signal it handled
    assert elapsed < 30  # timeout_s + 5 s of grace, not body_timeout_s
