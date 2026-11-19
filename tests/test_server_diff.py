"""``POST /v1/diff``: the diggs-diff 1.0 document over HTTP (thread and process mode)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from pydiggs import _pool, _service
from pydiggs import diff as diff_module
from pydiggs.server import ServerSettings, _app, _schemas, create_app
from tests.diff._helpers import schema_errors

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official" / "3.0"
OLD = OFFICIAL / "MWDExample.xml"
NEW = OFFICIAL / "TrenchWallExample.xml"
SAME = OFFICIAL / "Aeromag.xml"
BASE = ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",))


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(BASE))


def _files(old: bytes, new: bytes, *, old_name: str = "a.xml", new_name: str = "b.xml"):
    return {"old": (old_name, old, "application/xml"), "new": (new_name, new, "application/xml")}


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["code"]) == (status, code)
    return body


def _expected(old: bytes, new: bytes, old_name: str, new_name: str, **options) -> bytes:
    """What compare() gives, as the server reports it; thread mode gives the time budget."""
    options.setdefault("time_budget", BASE.timeout_s)
    document = diff_module.compare(old, new, **options).to_dict()
    document["old"]["name"], document["new"]["name"] = old_name, new_name
    return json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()


# --- the document -------------------------------------------------------------------------------


def test_the_body_is_what_compare_gives(client):
    old, new = OLD.read_bytes(), NEW.read_bytes()
    response = client.post("/v1/diff", files=_files(old, new, old_name="up/dir/a.xml"))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == _expected(old, new, "a.xml", "b.xml")
    document = response.json()
    assert schema_errors(document) == []
    assert document["identical"] is False and document["schema_version"] == "1.0"


def test_identical_files_are_a_200(client):
    data = SAME.read_bytes()
    response = client.post("/v1/diff", files=_files(data, data))
    assert response.status_code == 200
    assert response.json()["identical"] is True


def test_every_option_reaches_compare(client):
    old, new = OLD.read_bytes(), NEW.read_bytes()
    options = {
        "match": "gml_id",
        "rel_tol": "0.001",
        "abs_tol": "1e-3",
        "max_changes": "2",
        "show_reformatted": "true",
        "show_renamed": "true",
    }
    response = client.post(
        "/v1/diff", files=_files(old, new), data={**options, "old_name": "x.xml"}
    )
    assert response.status_code == 200, response.text
    expected = _expected(
        old,
        new,
        "x.xml",
        "b.xml",
        match="gml-id",
        rel_tol=0.001,
        abs_tol=1e-3,
        max_changes=2,
        show_reformatted=True,
        show_renamed=True,
    )
    assert response.content == expected
    document = response.json()
    assert document["complete"] is False and document["limits_hit"] == ["max_changes"]
    assert document["options"]["match"] == "gml-id"


def test_options_may_come_in_the_query(client):
    old, new = OLD.read_bytes(), NEW.read_bytes()
    query = client.post(
        "/v1/diff", params={"max_changes": "1", "new_name": "n.xml"}, files=_files(old, new)
    )
    assert query.status_code == 200
    assert query.content == _expected(old, new, "a.xml", "n.xml", max_changes=1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("match", "fuzzy"),
        ("rel_tol", "nan"),
        ("rel_tol", "1"),
        ("rel_tol", "1_0"),
        ("abs_tol", "-1"),
        ("abs_tol", "1e999"),
        ("max_changes", "-1"),
        ("max_changes", " 5"),
        ("show_reformatted", "yes"),
        ("show_renamed", "1"),
        ("old_name", "x" * 5000),
    ],
)
def test_a_bad_option_is_refused_with_a_fixed_text(client, field, value):
    data = SAME.read_bytes()
    response = client.post("/v1/diff", files=_files(data, data), data={field: value})
    body = _problem(response, 422, "invalid-parameter")
    assert [e["parameter"] for e in body["errors"]] == [field]
    assert value not in response.text or value in ("1", "-1")


def test_a_bad_query_value_is_refused_even_when_the_form_overrides_it(client):
    data = SAME.read_bytes()
    response = client.post(
        "/v1/diff", params={"match": "fuzzy"}, files=_files(data, data), data={"match": "auto"}
    )
    _problem(response, 422, "invalid-parameter")


# --- refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "data", "kind"),
    [
        ("old", b"<Diggs", "malformed"),
        ("new", b"<html/>", "not-diggs"),
        ("new", b'<!DOCTYPE Diggs [<!ENTITY x "<a/>">]><Diggs>&x;</Diggs>', "unsafe-entity"),
    ],
)
def test_what_is_not_a_diggs_document_is_a_422_naming_the_file(client, side, data, kind):
    good = SAME.read_bytes()
    pair = {"old": good, "new": good, side: data}
    response = client.post("/v1/diff", files=_files(pair["old"], pair["new"]))
    body = _problem(response, 422, "invalid-document")
    assert body["kind"] == kind
    assert body["errors"] == [{"parameter": side, "problem": "is not a DIGGS document"}]
    assert body["detail"].startswith(f"The {side} file: ")
    # Never the file's text: only the fixed texts.
    assert "html" not in response.text and "ENTITY" not in response.text


def test_both_files_are_required_once_each(client):
    data = SAME.read_bytes()
    one = client.post("/v1/diff", files={"old": ("a.xml", data, "application/xml")})
    assert "'old' and 'new'" in _problem(one, 400, "missing-input")["detail"]
    twice = client.post(
        "/v1/diff",
        files=[("old", ("a.xml", data)), ("old", ("b.xml", data))],
    )
    assert "'old'" in _problem(twice, 400, "invalid-multipart")["detail"]
    third = client.post(
        "/v1/diff",
        files=[("old", ("a.xml", data)), ("new", ("b.xml", data)), ("file", ("c.xml", data))],
    )
    _problem(third, 400, "invalid-multipart")
    field = client.post("/v1/diff", files=_files(data, data), data={"checks": "schema"})
    _problem(field, 400, "invalid-multipart")
    text = client.post("/v1/diff", data={"old": "<Diggs/>", "new": "<Diggs/>"}, files={"x": b""})
    assert text.status_code == 400


def test_a_raw_body_is_unsupported(client):
    response = client.post(
        "/v1/diff", content=SAME.read_bytes(), headers={"content-type": "application/xml"}
    )
    body = _problem(response, 415, "unsupported-media-type")
    assert body["detail"] == "Send multipart/form-data."


def test_only_json_is_acceptable(client):
    data = SAME.read_bytes()
    response = client.post(
        "/v1/diff", files=_files(data, data), headers={"accept": "application/sarif+json"}
    )
    _problem(response, 406, "not-acceptable")


def test_cross_site_requests_are_refused(client):
    data = SAME.read_bytes()
    response = client.post(
        "/v1/diff",
        files=_files(data, data),
        headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )
    _problem(response, 403, "cross-site")


# --- limits: the pair ---------------------------------------------------------------------------


def _small(max_body_bytes: int, **extra) -> TestClient:
    settings = ServerSettings(
        workers=1,
        isolation="thread",
        allowed_hosts=("testserver",),
        max_body_bytes=max_body_bytes,
        **extra,
    )
    return TestClient(create_app(settings))


def test_each_file_is_held_to_the_per_file_limit():
    client = _small(2_000)
    small = b'<Diggs xmlns="http://diggsml.org/schemas/3"/>'
    big = small + b" " * 2_000
    # Two files within the limit each, together over it: accepted.
    pair = small + b" " * 1_500
    ok = client.post("/v1/diff", files=_files(pair, pair))
    assert ok.status_code == 200, ok.text
    for files in (_files(big, small), _files(small, big)):
        _problem(client.post("/v1/diff", files=files), 413, "input-too-large")


def test_a_body_over_two_files_and_the_slack_is_413_before_it_is_read():
    client = _small(2_000)
    limit = _app._body_limit(client.app_state if False else BASE, multipart=True)
    assert limit == BASE.max_body_bytes + _app.MULTIPART_SLACK
    length = 2 * 2_000 + _app.MULTIPART_SLACK + 1
    response = client.post(
        "/v1/diff",
        content=b"x",
        headers={
            "content-type": "multipart/form-data; boundary=b",
            "content-length": str(length),
        },
    )
    _problem(response, 413, "input-too-large")


def test_a_streamed_body_over_the_pair_limit_is_413():
    client = _small(2_000)
    huge = b"<a/>" + b" " * (2 * 2_000 + _app.MULTIPART_SLACK)

    def chunks():
        yield b'--b\r\nContent-Disposition: form-data; name="old"; filename="a"\r\n\r\n'
        yield huge
        yield b"\r\n--b--\r\n"

    response = client.post(
        "/v1/diff", content=chunks(), headers={"content-type": "multipart/form-data; boundary=b"}
    )
    _problem(response, 413, "input-too-large")


def test_a_document_over_four_times_the_body_limit_is_result_too_large():
    # Each empty borehole, 46 bytes of XML, is listed as added in some 210 bytes.
    old = b'<Diggs xmlns="http://diggsml.org/schemas/3"/>'
    new = (
        b'<Diggs xmlns="http://diggsml.org/schemas/3">'
        + b"<samplingFeature><Borehole/></samplingFeature>" * 100
        + b"</Diggs>"
    )
    client = _small(len(new))
    response = client.post("/v1/diff", files=_files(old, new))
    body = _problem(response, 422, "result-too-large")
    assert body["detail"] == (
        "The comparison of a.xml and b.xml is larger than allowed; list fewer changes with"
        " max_changes."
    )
    # Listing fewer changes brings it under the limit.
    fewer = client.post("/v1/diff", files=_files(old, new), data={"max_changes": "10"})
    assert fewer.status_code == 200
    assert fewer.json()["summary"]["features"]["added"] == 100


def test_the_time_budget_covers_both_files_and_gives_a_partial_document():
    client = _small(BASE.max_body_bytes, timeout_s=1e-9)
    old, new = OLD.read_bytes(), NEW.read_bytes()
    response = client.post("/v1/diff", files=_files(old, new))
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["complete"] is False and document["identical"] is False
    assert "time_budget" in document["limits_hit"] or any(
        "read_incomplete" in limit for limit in document["limits_hit"]
    )


# --- process isolation --------------------------------------------------------------------------

PROCESS = ServerSettings(workers=1, isolation="process", allowed_hosts=("testserver",))


class _FakePool:
    """Runs jobs in-process; ``fail`` makes the next job raise."""

    def __init__(self, workers, *, timeout, memory_mb):
        del workers, timeout, memory_mb
        self.fail: BaseException | None = None
        self.jobs: list[object] = []

    def start(self):
        pass

    def ready(self):
        return True

    def close(self):
        pass

    def run(self, function, *args, timeout):
        del timeout
        self.jobs.append(args[0])
        if self.fail is not None:
            raise self.fail
        return function(*args)


@pytest.fixture
def fake_pool(monkeypatch):
    pools: list[_FakePool] = []

    def make(*args, **kwargs):
        pools.append(_FakePool(*args, **kwargs))
        return pools[-1]

    monkeypatch.setattr(_service, "make_pool", make)
    return pools


def test_the_pair_runs_as_one_job_in_the_worker(fake_pool):
    old, new = OLD.read_bytes(), NEW.read_bytes()
    with TestClient(create_app(PROCESS)) as client:
        response = client.post("/v1/diff", files=_files(old, new))
    assert response.status_code == 200
    (pool,) = fake_pool
    (job,) = pool.jobs
    assert isinstance(job, _service.DiffRequest)
    assert (job.old, job.new) == (old, new)
    assert job.max_bytes == PROCESS.max_body_bytes
    assert job.max_result_bytes == _app.RESULT_FACTOR * PROCESS.max_body_bytes
    assert job.time_budget is None  # the hard timeout covers the pair
    assert response.content == _expected(old, new, "a.xml", "b.xml", time_budget=None)


def test_a_hard_timeout_is_504(fake_pool):
    data = SAME.read_bytes()
    with TestClient(create_app(PROCESS)) as client:
        fake_pool[0].fail = _pool.JobTimeoutError("late")
        response = client.post("/v1/diff", files=_files(data, data))
    body = _problem(response, 504, "timeout")
    assert body["detail"] == "Comparison did not finish within 60 s."


def test_a_worker_crash_is_a_fixed_500_without_paths(fake_pool):
    data = SAME.read_bytes()
    with TestClient(create_app(PROCESS)) as client:
        fake_pool[0].fail = _pool.WorkerCrashedError("died at /opt/venv/x")
        response = client.post("/v1/diff", files=_files(data, data))
    _problem(response, 500, "internal")
    assert "/opt/venv" not in response.text


@pytest.mark.timeout(180)
def test_a_real_worker_times_out_on_the_pair_and_is_replaced():
    settings = dataclasses.replace(PROCESS, timeout_s=0.2)
    old, new = OLD.read_bytes(), NEW.read_bytes()
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/diff", files=_files(old, new))
        _problem(response, 504, "timeout")
        data = SAME.read_bytes()
        again = client.post("/v1/diff", files=_files(data, data))
        assert again.status_code in (200, 504)


# --- the contract -------------------------------------------------------------------------------


def test_the_openapi_document_describes_the_operation():
    document = json.loads((ROOT / "src" / "pydiggs" / "server" / "openapi.json").read_text())
    operation = document["paths"]["/v1/diff"]["post"]
    assert operation["operationId"] == "diff"
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/DiggsDiffDocument"}
    components = document["components"]["schemas"]
    notes = components["DiggsDiffDocument"]["properties"]["notes"]["items"]
    assert notes == {"$ref": "#/components/schemas/DiggsDiffNote"}
    # A note is open, as every diggs-diff 1.0 object is: a later findings field stays valid.
    assert "additionalProperties" not in json.dumps(components["DiggsDiffNote"])
    assert {"DiggsDiffSource", "DiggsDiffChange", "DiggsDiffValueChange"} <= set(components)
    assert set(operation["requestBody"]["content"]) == {"multipart/form-data"}
    form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert form["required"] == ["old", "new"]
    assert set(form["properties"]) == {"old", "new", *_app._DIFF_FIELDS}
    assert {p["name"] for p in operation["parameters"]} == set(_app._DIFF_FIELDS)
    assert set(operation["responses"]) >= {"200", "413", "422", "504"}


def test_the_options_documented_are_compares_defaults():
    import inspect

    signature = inspect.signature(diff_module.compare).parameters
    for name, default in _service.DIFF_DEFAULTS.items():
        assert signature[name].default == default, name
    options = _schemas._diff_options()
    assert {k: v["default"] for k, v in options.items()} == dict(_service.DIFF_DEFAULTS)
    assert set(_service.DIFF_MATCH) == {"auto", "gml-id", "natural", "gml_id"}


def test_the_route_schema_inlines_the_findings_it_names():
    inline = _schemas._diff_inline()
    assert "$ref" not in json.dumps(inline)
    assert inline["properties"]["notes"]["items"]["properties"]["rule_id"]
