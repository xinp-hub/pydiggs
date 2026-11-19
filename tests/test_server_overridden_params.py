"""A multipart form overrides query parameters; each overridden query value is checked in context.

The form wins, but the query as sent must be valid too: each overridden query value is
checked against the rest of the effective request, not against the defaults.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from pydiggs import _service, ags4, convert
from pydiggs.server import ServerSettings, create_app

ROOT = Path(__file__).resolve().parent.parent
AGS4 = ROOT / "tests" / "fixtures" / "ags4"
CPT = ROOT / "tests" / "fixtures" / "official" / "2.6" / "CPT_and_PorePressureDissipation.xml"
SAME = ROOT / "tests" / "fixtures" / "official" / "3.0" / "Aeromag.xml"
BASE = ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",))


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(BASE))


@pytest.fixture(scope="module")
def site_xml() -> bytes:
    xml = convert.ags4_to_diggs(AGS4 / "convert_tier_a.ags").xml
    assert xml is not None
    return xml


@pytest.fixture(autouse=True)
def _fresh_cache():
    _service._read_cache.clear()


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["code"]) == (status, code)
    return body


def _convert(client, site_xml, params, data):
    return client.post(
        "/v1/convert",
        params=params,
        files={"file": ("site.xml", site_xml, "application/xml")},
        data=data,
    )


# --- valid in context: 200 ---------------------------------------------------------------------


@pytest.mark.parametrize(("query", "form"), [("4.2", "4.1.1"), ("4.1.1", "4.2")])
def test_an_overridden_ags_version_is_checked_with_the_query_target(client, site_xml, query, form):
    response = _convert(
        client, site_xml, {"to": "ags4", "ags_version": query}, {"ags_version": form}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["to"], body["ags_version"]) == ("ags4", form)
    expected = convert.diggs_to_ags4(site_xml, name="site.xml", ags_version=form)
    assert expected.ags4 is not None
    assert base64.b64decode(body["output_base64"]) == ags4.dumps(expected.ags4)


def test_an_overridden_ags_version_is_checked_with_the_form_target(client, site_xml):
    # The target comes only from the form; the overridden query version is valid for it.
    response = _convert(
        client, site_xml, {"ags_version": "4.2"}, {"to": "ags4", "ags_version": "4.1.1"}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["to"], response.json()["ags_version"]) == ("ags4", "4.1.1")


def test_a_form_target_overriding_a_valid_query_target_is_accepted(client, site_xml):
    response = _convert(client, site_xml, {"to": "ags4", "ags_version": "4.2"}, {"to": "ags4"})
    assert response.status_code == 200, response.text


# --- invalid overridden query values: still 422 ------------------------------------------------


def test_an_invalid_overridden_read_version_is_refused(client):
    response = client.post(
        "/v1/read",
        params={"diggs_version": "9"},
        files={"file": ("cpt.xml", CPT.read_bytes(), "application/xml")},
        data={"diggs_version": "3.0.0"},
    )
    body = _problem(response, 422, "invalid-parameter")
    assert [e["parameter"] for e in body["errors"]] == ["diggs_version"]
    assert "9" not in body["errors"][0]["problem"]


def test_an_invalid_overridden_target_is_refused(client, site_xml):
    response = _convert(client, site_xml, {"to": "bogus"}, {"to": "diggs"})
    body = _problem(response, 422, "invalid-parameter")
    assert body["errors"] == [{"parameter": "to", "problem": "expected one of diggs, ags4"}]
    assert "bogus" not in response.text


def test_an_invalid_overridden_version_is_refused_in_context(client, site_xml):
    response = _convert(
        client, site_xml, {"to": "ags4", "ags_version": "4.0"}, {"ags_version": "4.2"}
    )
    body = _problem(response, 422, "invalid-parameter")
    assert body["errors"] == [{"parameter": "ags_version", "problem": "expected one of 4.2, 4.1.1"}]


def test_an_inconsistent_query_is_refused_even_when_the_form_fixes_it(client, site_xml):
    # As sent, the query asks for an AGS4 version of a DIGGS conversion.
    response = _convert(client, site_xml, {"to": "diggs", "ags_version": "4.2"}, {"to": "ags4"})
    body = _problem(response, 422, "invalid-parameter")
    assert body["errors"] == [{"parameter": "ags_version", "problem": "applies only to to=ags4"}]


# --- diff: the options do not depend on each other -----------------------------------------------


def _pair():
    data = SAME.read_bytes()
    return {"old": ("a.xml", data, "application/xml"), "new": ("b.xml", data, "application/xml")}


def test_diff_accepts_a_valid_overridden_query_value(client):
    response = client.post(
        "/v1/diff",
        params={"match": "natural", "max_changes": "5"},
        files=_pair(),
        data={"match": "gml-id"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["options"]["match"] == "gml-id"


def test_diff_refuses_an_invalid_overridden_query_value(client):
    response = client.post(
        "/v1/diff", params={"rel_tol": "2", "match": "auto"}, files=_pair(), data={"rel_tol": "0"}
    )
    body = _problem(response, 422, "invalid-parameter")
    assert [e["parameter"] for e in body["errors"]] == ["rel_tol"]
