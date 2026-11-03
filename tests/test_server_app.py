"""``pydiggs.server``: endpoints, problems, limits, admission and request hygiene (thread mode)."""

from __future__ import annotations

import dataclasses
import doctest
import gzip
import json
import logging
import shutil
import site
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import jsonschema
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.testclient import TestClient

from pydiggs import _service, findings_schema
from pydiggs._service import Code, Outcome
from pydiggs.cli import main as cli_main
from pydiggs.server import (
    ServerSettings,
    _app,
    _problems,
    _run,
    _schemas,
    create_app,
    create_router,
)

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"
FINDINGS = jsonschema.Draft202012Validator(findings_schema())
XML = {"content-type": "application/xml"}
BASE = ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",))
PROBLEM_KEYS = {"type", "title", "status", "detail", "code"}


def _settings(**changes) -> ServerSettings:
    return dataclasses.replace(BASE, **changes)


def _client(**changes) -> TestClient:
    return TestClient(create_app(_settings(**changes)))


def _router_app(**changes) -> FastAPI:
    """Only the router, in a bare FastAPI app: no middleware, no exception handlers."""
    app = FastAPI()
    app.include_router(create_router(_settings(**changes)))
    return app


@pytest.fixture(scope="module")
def client():
    return _client()


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert set(body) >= PROBLEM_KEYS
    assert (body["status"], body["code"]) == (status, code)
    assert body["type"] == f"https://xinp-hub.github.io/pydiggs/http-api/#problem-{code}"
    assert body["title"] == _problems.PROBLEMS[code][1]
    return body


def _cli_document(tmp_path: Path, monkeypatch, capsys, fixture: Path) -> dict:
    shutil.copy(fixture, tmp_path / fixture.name)
    monkeypatch.chdir(tmp_path)
    cli_main(["validate", "--format", "json", fixture.name])
    return json.loads(capsys.readouterr().out)


# --- a tiny ASGI driver: chunked bodies, disconnects, and how much of a body was read ---------


async def _asgi(
    app, *, headers=(), chunks=(), path="/v1/validate", query=b"", disconnect=False, stall=False
):
    """Send one request; return ``(status, headers, body, bytes_pulled)``.

    ``stall`` announces more body after the last chunk and then sends nothing.
    """
    pending = list(chunks)
    pulled = 0
    sent: list[dict] = []

    async def receive():
        nonlocal pulled
        if disconnect:
            return {"type": "http.disconnect"}
        if pending:
            chunk = pending.pop(0)
            pulled += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": stall or bool(pending)}
        await anyio.sleep(3600)  # only a stalled body is asked again
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(b"host", b"testserver"), *[(k.encode(), v.encode()) for k, v in headers]],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], Headers(raw=start["headers"]), body, pulled


# --- validate ---------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", [NO_ERROR, SCHEMA_ERROR])
def test_raw_xml_equals_the_cli_json_apart_from_path(
    client, fixture, tmp_path, monkeypatch, capsys
):
    response = client.post(
        "/v1/validate", params={"name": "up/load.xml"}, content=fixture.read_bytes(), headers=XML
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Accept"
    document = response.json()
    FINDINGS.validate(document)
    assert document["files"][0]["path"] == "load.xml"
    expected = _cli_document(tmp_path, monkeypatch, capsys, fixture)
    for doc in (document, expected):
        doc["files"][0]["path"] = "x"
    assert document == expected


def test_text_xml_and_multipart_give_the_same_document(client):
    data = SCHEMA_ERROR.read_bytes()
    raw = client.post(
        "/v1/validate", params={"name": "a.xml"}, content=data, headers={"content-type": "text/xml"}
    )
    form = client.post("/v1/validate", files={"file": ("dir\\a.xml", data, "application/xml")})
    assert raw.status_code == form.status_code == 200
    assert raw.json() == form.json()


def test_multipart_names_and_parameters(client):
    data = NO_ERROR.read_bytes()

    def post(params=None, data_fields=None):
        return client.post(
            "/v1/validate",
            params=params,
            data=data_fields,
            files={"file": ("part.xml", data, "application/xml")},
        )

    assert post().json()["files"][0]["path"] == "part.xml"
    assert post(params={"name": "query.xml"}).json()["files"][0]["path"] == "query.xml"
    document = post(
        params={"name": "query.xml", "checks": "schema"},
        data_fields={"name": "form.xml", "checks": "structure", "diggs_version": "2.6"},
    ).json()
    assert document["files"][0]["path"] == "form.xml"
    assert [s["step"] for s in document["files"][0]["steps"] if s["status"] != "skipped"] == [
        "structure"
    ]
    # The form wins, but an invalid overridden query value is still refused.
    refused = post(params={"diggs_version": "9.9"}, data_fields={"diggs_version": "2.6"})
    body = _problem(refused, 422, "invalid-parameter")
    assert body["errors"] == [
        {"parameter": "diggs_version", "problem": "expected one of 2.5.a, 2.6, 3.0.0"}
    ]


def test_parameters_are_checked_with_fixed_texts(client):
    response = client.post(
        "/v1/validate",
        params={"checks": "schema,<script>", "diggs_version": "<b>9</b>"},
        content=b"<Diggs/>",
        headers=XML,
    )
    body = _problem(response, 422, "invalid-parameter")
    assert {e["parameter"] for e in body["errors"]} == {"checks", "diggs_version"}
    assert "<script>" not in response.text and "<b>" not in response.text
    empty = client.post("/v1/validate", params={"checks": ","}, content=b"<Diggs/>", headers=XML)
    _problem(empty, 422, "invalid-parameter")


def test_checks_repeat_and_split_and_each_step_runs_once(client):
    response = client.post(
        "/v1/validate?checks=structure,schema&checks=schema",
        content=NO_ERROR.read_bytes(),
        headers=XML,
    )
    steps = [s["step"] for s in response.json()["files"][0]["steps"] if s["status"] != "skipped"]
    assert steps == ["structure", "schema"]
    assert _app._checks([]) is None
    assert _app._checks(["a, b", "a"]) == ["a", "b"]


def test_malformed_xml_is_a_200_with_findings(client):
    response = client.post("/v1/validate", content=b"<Diggs", headers=XML)
    assert response.status_code == 200
    rules = {f["rule_id"] for f in response.json()["files"][0]["findings"]}
    assert any(r.startswith("structure.") for r in rules)


def test_truncation_is_a_200_with_the_truncation_finding():
    response = _client(max_findings=2).post(
        "/v1/validate", content=SCHEMA_ERROR.read_bytes(), headers=XML
    )
    assert response.status_code == 200
    findings = response.json()["files"][0]["findings"]
    assert "structure.findings-truncated" in {f["rule_id"] for f in findings}


def test_an_exhausted_time_budget_is_a_200_with_structure_timeout():
    response = _client(timeout_s=1e-9).post(
        "/v1/validate", content=SCHEMA_ERROR.read_bytes(), headers=XML
    )
    assert response.status_code == 200
    findings = response.json()["files"][0]["findings"]
    assert "structure.timeout" in {f["rule_id"] for f in findings}


def test_sarif_on_request_and_406_when_nothing_acceptable(client):
    data = SCHEMA_ERROR.read_bytes()
    sarif = client.post(
        "/v1/validate", content=data, headers={**XML, "accept": "application/sarif+json"}
    )
    assert sarif.status_code == 200
    assert sarif.headers["content-type"] == "application/sarif+json"
    assert sarif.json()["version"] == "2.1.0"
    refused = client.post("/v1/validate", content=data, headers={**XML, "accept": "text/html"})
    _problem(refused, 406, "not-acceptable")


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        (None, "findings"),
        ("", "findings"),
        ("*/*", "findings"),
        ("application/*", "findings"),
        ("application/json", "findings"),
        ("application/sarif+json", "sarif"),
        ("Application/SARIF+JSON; charset=utf-8", "sarif"),
        ("application/sarif+json, application/json", "findings"),
        ("application/json;q=0.5, application/sarif+json", "sarif"),
        ("*/*;q=0.1, application/sarif+json;q=0.2", "sarif"),
        ("application/json;q=0, */*", "sarif"),
        ("application/json;q=bogus", None),
        ("application/json;q=nan", None),
        ("application/json;q=7", "findings"),
        ("text/html, application/xml", None),
        ("*/*;q=0", None),
    ],
)
def test_negotiation(accept, expected):
    assert _app.negotiate(accept) == expected


@pytest.mark.parametrize(
    "headers",
    [{"content-type": "text/plain"}, {"content-type": "application/json"}, {}],
)
def test_other_media_types_are_415(client, headers):
    response = client.post("/v1/validate", content=b"<Diggs/>", headers=headers)
    _problem(response, 415, "unsupported-media-type")


def test_compressed_input_is_415(client):
    encoded = client.post(
        "/v1/validate", content=b"<Diggs/>", headers={**XML, "content-encoding": "gzip"}
    )
    _problem(encoded, 415, "compressed-input")
    identity = client.post(
        "/v1/validate", content=b"<Diggs/>", headers={**XML, "content-encoding": "identity"}
    )
    assert identity.status_code == 200
    magic = client.post("/v1/validate", content=gzip.compress(b"<Diggs/>"), headers=XML)
    assert "compressed" in _problem(magic, 415, "compressed-input")["detail"]


# --- body limits ------------------------------------------------------------------------------


def test_content_length_over_the_limit_is_413_without_reading():
    app = _router_app(max_body_bytes=1000)
    status, headers, body, pulled = anyio.run(
        lambda: _asgi(
            app,
            headers=[("content-type", "application/xml"), ("content-length", "1001")],
            chunks=[b"x" * 1001],
        )
    )
    assert (status, pulled) == (413, 0)
    assert headers["connection"] == "close"
    assert json.loads(body)["code"] == "input-too-large"


def test_a_chunked_body_over_the_limit_stops_being_read():
    app = _router_app(max_body_bytes=100_000)
    chunk = b"<" * 16_384
    status, _, body, pulled = anyio.run(
        lambda: _asgi(app, headers=[("content-type", "application/xml")], chunks=[chunk] * 64)
    )
    assert status == 413 and json.loads(body)["code"] == "input-too-large"
    assert pulled <= 100_000 + len(chunk)  # not the whole megabyte


def test_a_malformed_content_length_falls_back_to_counting():
    app = _router_app(max_body_bytes=100)
    status, _, body, _ = anyio.run(
        lambda: _asgi(
            app,
            headers=[("content-type", "application/xml"), ("content-length", "lots")],
            chunks=[b"<" * 101],
        )
    )
    assert status == 413 and json.loads(body)["code"] == "input-too-large"


def test_a_body_at_the_limit_is_accepted():
    app = _router_app(max_body_bytes=8)
    status, _, body, _ = anyio.run(
        lambda: _asgi(app, headers=[("content-type", "application/xml")], chunks=[b"<Diggs/>"])
    )
    assert status == 200 and json.loads(body)["format"] == "diggs-findings"


def test_an_oversized_multipart_file_is_413():
    client = TestClient(_router_app(max_body_bytes=1000))
    small_overhead = client.post(
        "/v1/validate", files={"file": ("a.xml", b"<" * 1001, "application/xml")}
    )
    _problem(small_overhead, 413, "input-too-large")
    huge = client.post(
        "/v1/validate", files={"file": ("a.xml", b"<" * 2_000_000, "application/xml")}
    )
    _problem(huge, 413, "input-too-large")


def test_a_multipart_body_streams_against_the_limit():
    app = _router_app(max_body_bytes=1000)
    boundary = "b0undary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.xml"\r\n\r\n'
    ).encode()
    chunk = b"<" * 65_536
    status, _, _, pulled = anyio.run(
        lambda: _asgi(
            app,
            headers=[("content-type", f"multipart/form-data; boundary={boundary}")],
            chunks=[head, *[chunk] * 32],
        )
    )
    assert status == 413
    assert pulled <= 1000 + _app.MULTIPART_SLACK + len(chunk)


def test_the_app_answers_a_huge_content_length_with_a_problem():
    # No outer body-limit middleware: Starlette's would answer plain text instead.
    app = create_app(_settings(max_body_bytes=1000))
    status, headers, body, pulled = anyio.run(
        lambda: _asgi(
            app,
            headers=[("content-type", "application/xml"), ("content-length", str(2**40))],
            chunks=[b"x"],
        )
    )
    assert (status, pulled) == (413, 0)
    assert headers["content-type"] == "application/problem+json"
    assert json.loads(body)["code"] == "input-too-large"


@pytest.mark.parametrize(
    ("files", "data", "headers", "code"),
    [
        (None, {"name": "a.xml"}, None, "missing-input"),
        ({"other": ("a.xml", b"<Diggs/>")}, None, None, "missing-input"),
        (
            {"file": ("a.xml", b"<Diggs/>")},
            {f"f{i}": "x" for i in range(9)},
            None,
            "invalid-multipart",
        ),
        ({"file": ("a.xml", b"<Diggs/>")}, {"format": "sarif"}, None, "invalid-multipart"),
        ({"file": ("a.xml", b"<Diggs/>")}, {"name": "x" * 70_000}, None, "invalid-multipart"),
        (None, None, {"content-type": "multipart/form-data"}, "invalid-multipart"),
    ],
)
def test_bad_multipart_bodies_are_400(files, data, headers, code):
    client = TestClient(_router_app())
    if headers:
        response = client.post("/v1/validate", content=b"--x--", headers=headers)
    elif files is None:
        response = client.post(
            "/v1/validate",
            content=b'--b\r\nContent-Disposition: form-data; name="file"\r\n\r\nx\r\n--b--\r\n',
            headers={"content-type": "multipart/form-data; boundary=b"},
        )
    else:
        response = client.post("/v1/validate", files=files, data=data)
    _problem(response, 400, code)


def test_spooled_parts_are_closed(monkeypatch):
    closed = []
    original = _app.UploadFile.close

    async def spy(self):
        closed.append(self.filename)
        await original(self)

    monkeypatch.setattr(_app.UploadFile, "close", spy)
    client = TestClient(_router_app())
    assert client.post("/v1/validate", files={"file": ("a.xml", b"<Diggs/>")}).status_code == 200
    assert closed == ["a.xml"]


def test_a_client_that_disconnects_gets_a_problem():
    status, _, body, _ = anyio.run(
        lambda: _asgi(_router_app(), headers=[("content-type", "application/xml")], disconnect=True)
    )
    assert status == 400 and json.loads(body)["code"] == "missing-input"


# --- admission --------------------------------------------------------------------------------


def test_admission_full_is_503_before_the_body_is_read(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = _service.validate

    def slow(request, **kwargs):
        entered.set()
        release.wait(30)
        return real(request, **kwargs)

    monkeypatch.setattr(_service, "validate", slow)
    app = _router_app(workers=1, queue_depth=0)
    xml = [("content-type", "application/xml")]
    results = {}

    async def scenario():
        async with anyio.create_task_group() as group:

            async def first():
                results["first"] = await _asgi(app, headers=xml, chunks=[b"<Diggs/>"])

            group.start_soon(first)
            while not entered.is_set():
                await anyio.sleep(0.01)
            results["second"] = await _asgi(app, headers=xml, chunks=[b"<Diggs/>"])
            release.set()

    anyio.run(scenario)
    status, headers, body, pulled = results["second"]
    assert (status, pulled) == (503, 0)
    assert headers["retry-after"] == "1"
    assert json.loads(body)["code"] == "busy"
    assert results["first"][0] == 200
    # The slot was given back: the next request is admitted.
    assert anyio.run(lambda: _asgi(app, headers=xml, chunks=[b"<Diggs/>"]))[0] == 200


def test_a_stalled_body_is_408_and_frees_its_slot():
    app = _router_app(workers=1, queue_depth=0, body_timeout_s=0.2)
    xml = [("content-type", "application/xml"), ("content-length", "1000")]
    status, headers, body, pulled = anyio.run(
        lambda: _asgi(app, headers=xml, chunks=[b"<", b"Di"], stall=True)
    )
    assert (status, pulled) == (408, 3)
    assert headers["connection"] == "close"
    assert headers["content-type"] == "application/problem+json"
    assert json.loads(body)["code"] == "request-timeout"
    # The only slot was given back: the next request is admitted, not 503 busy.
    xml = [("content-type", "application/xml")]
    assert anyio.run(lambda: _asgi(app, headers=xml, chunks=[b"<Diggs/>"]))[0] == 200


def test_a_stalled_multipart_body_is_408_too():
    app = _router_app(workers=1, queue_depth=0, body_timeout_s=0.2)
    headers = [("content-type", "multipart/form-data; boundary=b")]
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.xml"\r\n\r\n<Di'
    status, _, body, _ = anyio.run(lambda: _asgi(app, headers=headers, chunks=[head], stall=True))
    assert status == 408 and json.loads(body)["code"] == "request-timeout"
    xml = [("content-type", "application/xml")]
    assert anyio.run(lambda: _asgi(app, headers=xml, chunks=[b"<Diggs/>"]))[0] == 200


def test_admission_counts_slots():
    admission = _app._Admission(2)
    assert admission.try_enter() and admission.try_enter()
    assert not admission.try_enter()
    admission.leave()
    assert admission.try_enter()


def test_threads_get_the_single_threaded_answer(client):
    data = SCHEMA_ERROR.read_bytes()

    def post(_):
        return client.post("/v1/validate", content=data, headers=XML).json()

    single = post(0)
    with ThreadPoolExecutor(4) as pool:
        assert all(result == single for result in pool.map(post, range(4)))


# --- cross-site, Host, CORS -------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        {"origin": "http://testserver.evil.example"},
        {"origin": "http://[::1"},
    ],
)
def test_cross_site_requests_are_403_on_the_bare_router(headers):
    client = TestClient(_router_app())
    response = client.post("/v1/validate", content=b"<Diggs/>", headers={**XML, **headers})
    _problem(response, 403, "cross-site")
    _problem(client.get("/v1/rules", headers=headers), 403, "cross-site")


@pytest.mark.parametrize(
    ("settings", "headers"),
    [
        ({}, {"origin": "http://testserver", "sec-fetch-site": "same-origin"}),
        ({}, {"sec-fetch-site": "none"}),
        ({"cors_origins": ("https://app.example",)}, {"origin": "https://app.example"}),
        (
            {"cors_origins": ("https://app.example",)},
            {"origin": "https://app.example", "sec-fetch-site": "cross-site"},
        ),
        ({"reject_cross_site": False}, {"origin": "https://evil.example"}),
    ],
)
def test_same_origin_listed_and_unguarded_requests_pass(settings, headers):
    client = TestClient(_router_app(**settings))
    response = client.post("/v1/validate", content=b"<Diggs/>", headers={**XML, **headers})
    assert response.status_code == 200


def test_is_cross_site_needs_a_host():
    assert _app.is_cross_site(Headers({"origin": "http://a"}), ServerSettings())


def test_cors_headers_only_for_listed_origins():
    client = _client(cors_origins=("https://app.example",))
    listed = client.options(
        "/v1/validate",
        headers={"origin": "https://app.example", "access-control-request-method": "POST"},
    )
    assert listed.status_code == 200
    assert listed.headers["access-control-allow-origin"] == "https://app.example"
    other = client.get("/v1/rules", headers={"origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    no_cors = _client().get("/v1/rules", headers={"origin": "http://testserver"})
    assert "access-control-allow-origin" not in no_cors.headers


@pytest.mark.parametrize("host", ["evil.example", "testserver.evil.example", "", "[::1"])
def test_an_unlisted_host_is_refused(client, host):
    response = client.get("/livez", headers={"host": host})
    _problem(response, 400, "invalid-host")


def test_loopback_hosts_by_default():
    app = create_app(ServerSettings(workers=1, isolation="thread"))
    client = TestClient(app, base_url="http://localhost:8000")
    assert client.get("/livez").status_code == 200
    for host in ("127.0.0.1:8000", "[::1]:8000", "LOCALHOST"):
        assert client.get("/livez", headers={"host": host}).status_code == 200
    _problem(client.get("/livez", headers={"host": "rebind.example"}), 400, "invalid-host")


@pytest.mark.parametrize(
    ("header", "name"),
    [("a.example:80", "a.example"), ("[::1]:8000", "[::1]"), ("[::1]", "[::1]"), ("[x", "")],
)
def test_host_names_drop_the_port(header, name):
    assert _app._HostCheck.host_name(header) == name


def test_the_host_check_lets_other_scopes_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    anyio.run(lambda: _app._HostCheck(inner, ["a"])({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


# --- the other endpoints ----------------------------------------------------------------------


def test_versions(client):
    body = client.get("/v1/versions").json()
    assert body == {"api_version": "1", **_service.versions()}
    _problem(_client(versions_enabled=False).get("/v1/versions"), 404, "not-found")


def test_rules(client):
    every = client.get("/v1/rules").json()
    assert {r["id"] for r in every} == {r["id"] for r in _service.rules()}
    schema = client.get("/v1/rules", params={"step": "schema"}).json()
    assert schema and {r["step"] for r in schema} == {"schema"}
    _problem(client.get("/v1/rules", params={"step": "nope"}), 422, "invalid-parameter")


def test_explain_rule(client):
    assert client.get("/v1/rules/dictionary.5").json()["known"] is True
    unknown = client.get("/v1/rules/crs.unknown")
    assert unknown.status_code == 200 and unknown.json()["known"] is False
    body = _problem(client.get("/v1/rules/no-dots"), 422, "invalid-parameter")
    assert body["errors"][0]["parameter"] == "rule_id"
    _problem(client.get("/v1/rules/schema." + "a" * 300), 422, "invalid-parameter")


def test_probes_in_thread_mode(client):
    assert client.get("/livez").json() == {"status": "ready"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/v1/readyz").json() == {"status": "ready"}


def test_unknown_paths_and_methods_are_problems(client):
    _problem(client.get("/nope"), 404, "not-found")
    response = client.get("/v1/validate")
    _problem(response, 405, "method-not-allowed")
    assert response.headers["allow"] == "POST"


def test_docs_are_off_by_default_and_on_when_enabled():
    off = _client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        _problem(off.get(path), 404, "not-found")
    on = _client(docs_enabled=True)
    assert on.get("/docs").status_code == 200
    committed = json.loads((ROOT / "src/pydiggs/server/openapi.json").read_text("utf-8"))
    assert on.get("/openapi.json").json() == committed


def test_create_router_defaults_to_thread_and_create_app_to_process():
    router = create_router()
    assert router.prefix == "/v1"
    assert {r.path for r in router.routes} >= {"/v1/validate", "/v1/readyz"}
    app = create_app(ServerSettings(workers=1))
    assert app.docs_url is None and app.openapi_url is None


def test_create_app_warns_about_thread_isolation(caplog):
    with caplog.at_level(logging.WARNING, logger="pydiggs.server"):
        create_app(_settings(isolation="thread"))
    assert "unsafe for untrusted input" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="pydiggs.server"):
        create_app(_settings(isolation="process"))
    assert caplog.text == ""


# --- errors and disclosure --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "status", "name"),
    [
        (Code.INPUT_TOO_LARGE, 413, "input-too-large"),
        (Code.COMPRESSED_INPUT, 415, "compressed-input"),
        (Code.INPUT_UNREADABLE, 422, "input-unreadable"),
        (Code.INVALID_PARAMETER, 422, "invalid-parameter"),
        (Code.RESOURCE_ERROR, 500, "resource-error"),
        (Code.INTERNAL, 500, "internal"),
        (Code.CANCELLED, 500, "internal"),
        (Code.TIMEOUT, 504, "timeout"),
    ],
)
def test_every_code_maps_to_a_problem(code, status, name):
    response = _problems.from_outcome(Outcome(code=code, detail="text", correlation_id="c1"))
    body = json.loads(response.body)
    assert (response.status_code, body["code"], body["status"]) == (status, name, status)
    assert body["correlation_id"] == "c1"


def test_problems_cap_errors_and_fall_back_to_generic_text():
    errors = tuple((f"p{i}", "bad") for i in range(30))
    body = json.loads(_problems.problem("invalid-parameter", "x", errors=errors).body)
    assert len(body["errors"]) == _problems.MAX_ERRORS
    blank = json.loads(_problems.from_outcome(Outcome(code=Code.INTERNAL)).body)
    assert blank["detail"] == "An internal error occurred."


def test_a_resource_error_answer_has_a_correlation_id(client, monkeypatch):
    failure = Outcome(code=Code.RESOURCE_ERROR, detail="generic", correlation_id="abc123")
    monkeypatch.setattr(_service, "validate", lambda request, **kw: failure)
    response = client.post("/v1/validate", content=b"<Diggs/>", headers=XML)
    assert _problem(response, 500, "resource-error")["correlation_id"] == "abc123"


def test_an_unexpected_exception_is_a_fixed_500(client, monkeypatch, caplog):
    secret = str(Path(site.getsitepackages()[0]) / "secret.py")

    def boom():
        raise RuntimeError(secret)

    monkeypatch.setattr(_service, "versions", boom)
    with caplog.at_level(logging.ERROR, logger="pydiggs.server"):
        response = client.get("/v1/versions")
    body = _problem(response, 500, "internal")
    assert secret not in response.text and len(body["correlation_id"]) == 16
    assert body["correlation_id"] in caplog.text and secret in caplog.text


def test_app_level_handlers_are_problems():
    async def call(handler, exc):
        return await handler(None, exc)

    from starlette.exceptions import HTTPException

    teapot = anyio.run(call, _problems.http_exception_handler, HTTPException(418))
    assert json.loads(teapot.body)["code"] == "internal"
    plain = anyio.run(call, _problems.http_exception_handler, HTTPException(404))
    assert "allow" not in plain.headers
    crash = anyio.run(call, _problems.unhandled_exception_handler, ValueError("x"))
    assert crash.status_code == 500


def test_no_response_reveals_install_temp_or_home_paths(client):
    folders = {*site.getsitepackages(), tempfile.gettempdir(), str(Path.home()), "/opt/venv"}
    responses = [
        client.post("/v1/validate", content=b"<Diggs", headers=XML),
        client.post("/v1/validate", content=SCHEMA_ERROR.read_bytes(), headers=XML),
        client.post("/v1/validate", params={"checks": "x"}, content=b"<a/>", headers=XML),
        client.post(
            "/v1/validate",
            content=b'<!DOCTYPE d SYSTEM "file:///etc/passwd"><d/>',
            headers=XML,
        ),
        client.get("/v1/rules/bad"),
        client.get("/v1/versions"),
        client.get("/nope"),
    ]
    for response in responses:
        for folder in folders:
            assert folder not in response.text, (response.request.url, folder)


def test_the_server_modules_doctests_pass():
    for module in (_app, _problems, _run, _schemas):
        failed, _ = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
        assert failed == 0, module.__name__
