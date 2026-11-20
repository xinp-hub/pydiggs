"""A query parameter an endpoint does not take is 422 invalid-parameter (pyDIGGS 1.14).

The same answer as @pydiggs/client's createPydiggsHandler: each name once, in URL
order, at most 20, values never echoed; checked after Accept and before the media
type and the body. ``unknown_query="ignore"`` (deprecated) keeps the 1.13 behaviour.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from pydiggs.server import ServerSettings, create_app, create_router

ROOT = Path(__file__).resolve().parent.parent
XML = (ROOT / "tests" / "fixtures" / "official" / "3.0" / "Aeromag.xml").read_bytes()
AGS = next((ROOT / "tests" / "fixtures" / "ags4").glob("*.ags")).read_bytes()
BASE = ServerSettings(workers=1, isolation="thread", allowed_hosts=("testserver",))
PROBE = "value-never-echoed"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(BASE))


def _xml(path: str) -> dict:
    return {"content": XML, "headers": {"Content-Type": "application/xml"}}


def _ags(path: str) -> dict:
    return {"content": AGS, "headers": {"Content-Type": "text/plain"}}


def _pair(path: str) -> dict:
    return {
        "files": {
            "old": ("a.xml", XML, "application/xml"),
            "new": ("b.xml", XML, "application/xml"),
        }
    }


ROUTES = [
    ("POST", "/v1/validate", _xml),
    ("POST", "/v1/read", _xml),
    ("POST", "/v1/convert", _ags),
    ("POST", "/v1/ags4/check", _ags),
    ("POST", "/v1/diff", _pair),
    ("GET", "/v1/rules", lambda path: {}),
]


def _unknown(response, *names: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "invalid-parameter"
    assert body["errors"] == [{"parameter": n, "problem": "unknown parameter"} for n in names]
    assert PROBE not in response.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[r[1] for r in ROUTES])
def test_each_endpoint_refuses_a_parameter_it_does_not_take(client, method, path, body):
    response = client.request(method, path, params={"max_change": PROBE}, **body(path))
    _unknown(response, "max_change")


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/v1/validate", "select"),  # read's option, not validate's
        ("/v1/read", "checks"),
        ("/v1/ags4/check", "dictionary"),  # a file part, never a query parameter
        ("/v1/diff", "old"),
    ],
)
def test_another_endpoints_option_is_unknown_here(client, path, name):
    body = _pair(path) if path == "/v1/diff" else (_ags if "ags4" in path else _xml)(path)
    _unknown(client.post(path, params={name: PROBE}, **body), name)


def test_names_are_listed_once_in_url_order_including_blank_and_empty(client):
    response = client.post(
        "/v1/validate?b=1&name=x.xml&a=2&b=3&foo&=" + PROBE,
        content=XML,
        headers={"Content-Type": "application/xml"},
    )
    _unknown(response, "b", "a", "foo", "")


def test_at_most_twenty_names_are_listed(client):
    query = "&".join(f"p{i}=1" for i in range(30))
    response = client.post(
        f"/v1/validate?{query}", content=XML, headers={"Content-Type": "application/xml"}
    )
    assert response.status_code == 422
    assert [e["parameter"] for e in response.json()["errors"]] == [f"p{i}" for i in range(20)]


def test_unknown_comes_after_accept_and_before_the_media_type(client):
    refused = client.post(
        "/v1/read", params={"x": "1"}, content=XML, headers={"Accept": "text/html"}
    )
    assert (refused.status_code, refused.json()["code"]) == (406, "not-acceptable")
    wrong_media = client.post(
        "/v1/read", params={"x": "1"}, content=b"{}", headers={"Content-Type": "application/json"}
    )
    _unknown(wrong_media, "x")


def test_rules_does_not_negotiate_accept_so_an_unknown_parameter_is_422(client):
    # The GET endpoints always answer JSON (no 406 in their OpenAPI responses).
    assert client.get("/v1/rules", headers={"Accept": "text/html"}).status_code == 200
    response = client.get("/v1/rules", params={"typo": "1"}, headers={"Accept": "text/html"})
    _unknown(response, "typo")


def test_known_parameters_still_work(client):
    response = client.post(
        "/v1/validate",
        params={"name": "a.xml", "checks": "schema", "diggs_version": "3.0.0"},
        content=XML,
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/v1/rules", params={"step": "schema"}).status_code == 200


def test_an_unknown_multipart_field_is_still_invalid_multipart(client):
    response = client.post(
        "/v1/validate", files={"file": ("a.xml", XML, "application/xml")}, data={"foo": "1"}
    )
    assert (response.status_code, response.json()["code"]) == (400, "invalid-multipart")


def test_versions_and_capabilities_ignore_the_query(client):
    assert client.get("/v1/versions", params={"x": "1"}).status_code == 200
    assert client.get("/v1/capabilities", params={"x": "1"}).status_code == 200


# --- the deprecated opt-out ------------------------------------------------------------


def test_ignore_keeps_the_old_behaviour_warns_and_logs(caplog):
    settings = ServerSettings(
        workers=1, isolation="thread", allowed_hosts=("testserver",), unknown_query="ignore"
    )
    with pytest.warns(DeprecationWarning, match="removed in pyDIGGS 2.0"):
        lenient = TestClient(create_app(settings))
    with caplog.at_level(logging.WARNING, logger="pydiggs.server"):
        response = lenient.post(
            "/v1/validate",
            params={"max_change": PROBE},
            content=XML,
            headers={"Content-Type": "application/xml"},
        )
    assert response.status_code == 200, response.text
    assert "ignored 1 unknown query parameter" in caplog.text
    assert PROBE not in caplog.text


def test_a_router_built_with_ignore_warns_too():
    settings = ServerSettings(isolation="thread", unknown_query="ignore")
    with pytest.warns(DeprecationWarning, match="unknown_query='ignore' is deprecated"):
        create_router(settings)


def test_refuse_is_the_default_and_does_not_warn(recwarn):
    assert ServerSettings().unknown_query == "refuse"
    create_router(ServerSettings(isolation="thread"))
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_the_setting_comes_from_the_environment():
    assert ServerSettings.from_env({}).unknown_query == "refuse"
    lenient = ServerSettings.from_env({"PYDIGGS_UNKNOWN_QUERY": " Ignore "})
    assert lenient.unknown_query == "ignore"
    with pytest.raises(ValueError, match="PYDIGGS_UNKNOWN_QUERY: expected refuse or ignore"):
        ServerSettings.from_env({"PYDIGGS_UNKNOWN_QUERY": "warn"})
    with pytest.raises(ValueError, match="unknown_query must be"):
        ServerSettings(unknown_query="warn")  # type: ignore[arg-type]
