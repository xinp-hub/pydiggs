"""``POST /v1/read``: the diggs-model document over HTTP (thread mode)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from starlette.testclient import TestClient

import pydiggs
from pydiggs import _service
from pydiggs.server import ServerSettings, _app, create_app

ROOT = Path(__file__).resolve().parent.parent
CPT = ROOT / "tests" / "fixtures" / "official" / "2.6" / "CPT_and_PorePressureDissipation.xml"
XML = {"content-type": "application/xml"}
BASE = ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",))
MODEL = jsonschema.Draft202012Validator(pydiggs.model_schema())


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(BASE))


@pytest.fixture(autouse=True)
def _fresh_cache():
    _service._read_cache.clear()


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["code"]) == (status, code)
    return body


def test_the_body_is_what_read_bytes_gives(client):
    data = CPT.read_bytes()
    response = client.post("/v1/read", params={"name": "up/cpt.xml"}, content=data, headers=XML)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    expected = pydiggs.read_bytes(data, name="cpt.xml").to_json().encode("utf-8")
    assert response.content == expected
    assert list(MODEL.iter_errors(response.json())) == []


def test_multipart_and_raw_give_the_same_document(client):
    data = CPT.read_bytes()
    raw = client.post("/v1/read", params={"name": "cpt.xml"}, content=data, headers=XML)
    form = client.post("/v1/read", files={"file": ("cpt.xml", data, "application/xml")})
    assert form.status_code == 200
    assert form.content == raw.content


def test_select_none_is_an_inventory_and_a_key_list_fills_those_tables(client):
    data = CPT.read_bytes()
    none = client.post(
        "/v1/read", params={"select": '{"results": "none"}'}, content=data, headers=XML
    )
    assert none.status_code == 200
    expected = pydiggs.read_bytes(data, results=False).to_json().encode("utf-8")
    assert none.content == expected
    document = none.json()
    keys = [m["key"] for m in document["measurements"] if m["results"]]
    assert keys
    one = client.post(
        "/v1/read",
        files={"file": ("cpt.xml", data, "application/xml")},
        data={"select": json.dumps({"results": keys[:1]})},
    ).json()
    filled = {
        m["key"]: any(c["values"] for t in m["results"] for c in t["columns"])
        for m in one["measurements"]
        if m["results"]
    }
    assert filled[keys[0]] is True
    assert not any(v for k, v in filled.items() if k != keys[0])
    assert all(t["row_count"] > 0 for m in one["measurements"] for t in m["results"])


@pytest.mark.parametrize(
    "select",
    ["not json", "5", "null", '{"tables": "all"}', '{"results": 5}', '{"results": [1]}'],
)
def test_a_bad_select_is_refused_with_a_fixed_text(client, select):
    response = client.post(
        "/v1/read",
        files={"file": ("a.xml", b"<Diggs/>", "application/xml")},
        data={"select": select},
    )
    body = _problem(response, 422, "invalid-parameter")
    assert body["errors"] == [
        {
            "parameter": "select",
            "problem": 'expected {"results": "all" | "none" | [at most 1000 node keys]}',
        }
    ]


def test_select_is_parsed_from_json_text_and_capped():
    assert _app._select([]) is None
    assert _app._select(['{"results": "all"}', '{"results": "none"}']) == {"results": "none"}
    # Unusable text stays text, which the service refuses as invalid-parameter.
    long = json.dumps({"results": ["k" * 4000] * 17})
    assert len(long) > 64 * 2**10
    for bad in (long, "not json", "null"):
        outcome = _service.read(
            _service.ReadRequest(content=b"<Diggs/>", select=_app._select([bad]))
        )
        assert outcome.code is _service.Code.INVALID_PARAMETER


def test_other_parameters_are_checked_as_for_validate(client):
    response = client.post(
        "/v1/read", params={"diggs_version": "9.9"}, content=b"<Diggs/>", headers=XML
    )
    body = _problem(response, 422, "invalid-parameter")
    assert [e["parameter"] for e in body["errors"]] == ["diggs_version"]
    form = client.post(
        "/v1/read",
        files={"file": ("a.xml", b"<Diggs/>", "application/xml")},
        data={"checks": "schema"},
    )
    _problem(form, 400, "invalid-multipart")


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"<Diggs", "malformed"),
        (b"<html/>", "not-diggs"),
        (b'<!DOCTYPE Diggs [<!ENTITY x "<a/>">]><Diggs>&x;</Diggs>', "unsafe-entity"),
    ],
)
def test_what_is_not_a_diggs_document_is_a_422_with_its_kind(client, data, kind):
    body = _problem(client.post("/v1/read", content=data, headers=XML), 422, "invalid-document")
    assert body["kind"] == kind
    assert body["title"] == "Invalid document"


def test_a_schema_invalid_file_still_reads(client):
    data = (ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml").read_bytes()
    response = client.post("/v1/read", content=data, headers=XML)
    assert response.status_code == 200
    assert response.json()["complete"] is True


def test_only_json_is_acceptable(client):
    ok = client.post("/v1/read", content=b"<Diggs/>", headers={**XML, "accept": "*/*"})
    assert ok.status_code == 200
    sarif = client.post(
        "/v1/read", content=b"<Diggs/>", headers={**XML, "accept": "application/sarif+json"}
    )
    _problem(sarif, 406, "not-acceptable")


def test_an_oversized_body_is_413():
    small = TestClient(
        create_app(
            ServerSettings(
                workers=1, isolation="thread", allowed_hosts=("testserver",), max_body_bytes=64
            )
        )
    )
    _problem(
        small.post("/v1/read", content=b"<Diggs>" + b" " * 100, headers=XML), 413, "input-too-large"
    )


def test_the_time_budget_gives_a_partial_document(monkeypatch):
    data = CPT.read_bytes()
    client = TestClient(
        create_app(
            ServerSettings(
                workers=1, isolation="thread", allowed_hosts=("testserver",), timeout_s=1e-9
            )
        )
    )
    response = client.post("/v1/read", content=data, headers=XML)
    assert response.status_code == 200
    document = response.json()
    assert document["complete"] is False
    assert document["limits_hit"] == ["time_budget"]


def test_cross_site_requests_are_refused(client):
    response = client.post(
        "/v1/read",
        content=b"<Diggs/>",
        headers={**XML, "origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )
    _problem(response, 403, "cross-site")


def test_accepts_json():
    assert _app.accepts_json("application/json;q=0.1, application/sarif+json")
    assert not _app.accepts_json("application/json;q=0")
    assert not _app.accepts_json("text/html")


def test_a_document_over_four_times_the_body_limit_is_result_too_large():
    # 400 untyped elements in under 2,000 bytes of XML make a document many times larger.
    data = (
        b'<Diggs xmlns="http://diggsml.org/schemas/3"><samplingFeature><Borehole>'
        + b"<a/>" * 400
        + b"</Borehole></samplingFeature></Diggs>"
    )
    settings = ServerSettings(
        workers=1, isolation="thread", allowed_hosts=("testserver",), max_body_bytes=2_000
    )
    response = TestClient(create_app(settings)).post("/v1/read", content=data, headers=XML)
    body = _problem(response, 422, "result-too-large")
    assert "select" in body["detail"]
    assert _app.RESULT_FACTOR == 4
