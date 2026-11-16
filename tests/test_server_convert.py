"""``POST /v1/convert`` and ``POST /v1/ags4/check`` over HTTP (thread mode)."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from starlette.testclient import TestClient

import pydiggs
from pydiggs import _service, ags4, convert
from pydiggs.convert import _api as convert_api
from pydiggs.server import ServerSettings, _app, _schemas, create_app

ROOT = Path(__file__).resolve().parent.parent
AGS4 = ROOT / "tests" / "fixtures" / "ags4"
SITE = AGS4 / "site_tier_a.ags"
TEXT = {"content-type": "text/plain"}
BASE = ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",))
FINDINGS = jsonschema.Draft202012Validator(pydiggs.findings_schema())
RESULT = jsonschema.Draft202012Validator(
    _schemas._conversion(pydiggs.findings_schema()) | {"$defs": pydiggs.findings_schema()["$defs"]}
)


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(BASE))


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["code"]) == (status, code)
    return body


# --- /v1/convert ------------------------------------------------------------------------------


def test_the_service_lists_the_versions_the_converter_writes():
    assert _service.CONVERT_DIGGS_VERSIONS == convert_api.DIGGS_VERSIONS


def test_convert_returns_the_converters_bytes_and_report(client):
    data = SITE.read_bytes()
    response = client.post(
        "/v1/convert", params={"name": "up/site.ags"}, content=data, headers=TEXT
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(RESULT.iter_errors(body)) == []
    expected = convert.ags4_to_diggs(data, name="site.ags")
    xml = base64.b64decode(body["output_base64"], validate=True)
    assert xml == expected.xml
    assert body["output_sha256"] == hashlib.sha256(xml).hexdigest()
    assert body["media_type"] == "application/xml"
    assert body["output_name"] == "site.xml"
    assert (body["to"], body["diggs_version"]) == ("diggs", "3.0.0")
    assert body["mapping_version"] == expected.mapping_version
    assert body["lossless"] is expected.lossless and body["complete"] is True
    assert body["report"] == expected.report.to_document()
    assert list(FINDINGS.iter_errors(body["report"])) == []
    assert body["validation"] == expected.validation.to_document()
    assert pydiggs.validate_bytes(xml).ok
    assert expected.residual is not None
    assert base64.b64decode(body["residual_base64"]) == ags4.dumps(expected.residual)
    assert body["ags_version"] is None


def test_multipart_and_raw_give_the_same_result(client):
    data = SITE.read_bytes()
    raw = client.post("/v1/convert", params={"name": "site.ags"}, content=data, headers=TEXT)
    form = client.post("/v1/convert", files={"file": ("site.ags", data, "text/plain")})
    octets = client.post(
        "/v1/convert",
        params={"name": "site.ags"},
        content=data,
        headers={"content-type": "application/octet-stream"},
    )
    assert form.status_code == octets.status_code == 200
    assert form.content == raw.content == octets.content


@pytest.mark.parametrize(
    ("data", "kind"),
    [(b"<Diggs/>", "not-ags4"), ((AGS4 / "ags3.ags").read_bytes(), "unsupported-version")],
)
def test_what_is_not_ags4_is_a_422_with_its_kind(client, data, kind):
    body = _problem(client.post("/v1/convert", content=data, headers=TEXT), 422, "invalid-document")
    assert body["kind"] == kind


def test_convert_parameters_are_checked(client):
    data = SITE.read_bytes()
    for params, name in (
        ({"to": "csv"}, "to"),
        ({"diggs_version": "2.6"}, "diggs_version"),
        ({"ags_version": "4.2"}, "ags_version"),  # only for to=ags4
    ):
        body = _problem(
            client.post("/v1/convert", params=params, content=data, headers=TEXT),
            422,
            "invalid-parameter",
        )
        assert [e["parameter"] for e in body["errors"]] == [name]
    # a form value wins, but a bad query value is still refused
    shadowed = client.post(
        "/v1/convert",
        params={"to": "nope"},
        files={"file": ("site.ags", data, "text/plain")},
        data={"to": "diggs"},
    )
    _problem(shadowed, 422, "invalid-parameter")
    unknown = client.post(
        "/v1/convert", files={"file": ("site.ags", data, "text/plain")}, data={"select": "x"}
    )
    _problem(unknown, 400, "invalid-multipart")
    two = client.post(
        "/v1/convert",
        files={"file": ("a.ags", data, "text/plain"), "dictionary": ("d.ags", data, "text/plain")},
    )
    _problem(two, 400, "invalid-multipart")


def test_convert_media_types_and_accept(client):
    data = SITE.read_bytes()
    _problem(
        client.post("/v1/convert", content=data, headers={"content-type": "application/xml"}),
        415,
        "unsupported-media-type",
    )
    _problem(
        client.post("/v1/convert", content=data, headers={**TEXT, "accept": "application/xml"}),
        406,
        "not-acceptable",
    )
    zipped = b"PK\x03\x04" + data
    _problem(client.post("/v1/convert", content=zipped, headers=TEXT), 415, "compressed-input")


def test_an_oversized_body_is_413():
    small = TestClient(create_app(dataclasses.replace(BASE, max_body_bytes=1000)))
    data = SITE.read_bytes()
    _problem(small.post("/v1/convert", content=data, headers=TEXT), 413, "input-too-large")
    _problem(small.post("/v1/ags4/check", content=data, headers=TEXT), 413, "input-too-large")


def test_a_result_over_four_times_the_body_limit_is_result_too_large():
    data = SITE.read_bytes()
    limited = dataclasses.replace(BASE, max_body_bytes=len(data) + 1)
    response = TestClient(create_app(limited)).post("/v1/convert", content=data, headers=TEXT)
    assert len(data) * _app.RESULT_FACTOR < 34_000  # the result is ~34 kB
    _problem(response, 422, "result-too-large")


def test_the_time_budget_gives_a_partial_conversion():
    timed = dataclasses.replace(BASE, timeout_s=1e-9)
    response = TestClient(create_app(timed)).post(
        "/v1/convert", content=SITE.read_bytes(), headers=TEXT
    )
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is False
    assert list(RESULT.iter_errors(body)) == []


def test_cross_site_requests_are_refused(client):
    response = client.post(
        "/v1/convert",
        content=SITE.read_bytes(),
        headers={**TEXT, "origin": "https://evil.example"},
    )
    _problem(response, 403, "cross-site")


# --- /v1/ags4/check ---------------------------------------------------------------------------


def _check_document(path: Path, name: str, dictionary: Path | None = None) -> dict:
    """What ags4.check gives for the file, reported under ``name`` (no absolute path)."""
    used = ags4.read(dictionary) if dictionary is not None else None
    report = ags4.check(path, dictionary=used)
    document = report.to_document()
    document["files"][0]["path"] = name
    return document


def test_check_is_what_ags4_check_gives(client):
    path = AGS4 / "c2_rule_10a.ags"
    response = client.post(
        "/v1/ags4/check", params={"name": "x/c2.ags"}, content=path.read_bytes(), headers=TEXT
    )
    assert response.status_code == 200
    body = response.json()
    assert list(FINDINGS.iter_errors(body)) == []
    assert body == _check_document(path, "c2.ags")
    lines = {(f["rule_id"], f["location"]["line"]) for f in body["files"][0]["findings"]}
    assert ("ags4.rule-10a", 77) in lines


def test_check_with_a_dictionary_part(client):
    path, dictionary = AGS4 / "c2_user_group.ags", AGS4 / "c2_dictionary.ags"
    response = client.post(
        "/v1/ags4/check",
        files={
            "file": ("c2.ags", path.read_bytes(), "text/plain"),
            "dictionary": ("dict.ags", dictionary.read_bytes(), "text/plain"),
        },
    )
    assert response.status_code == 200
    assert response.json() == _check_document(path, "c2.ags", dictionary)


def test_check_refuses_an_unusable_dictionary(client):
    response = client.post(
        "/v1/ags4/check",
        files={
            "file": ("c2.ags", SITE.read_bytes(), "text/plain"),
            "dictionary": ("dict.ags", b"not a dictionary", "text/plain"),
        },
    )
    body = _problem(response, 422, "invalid-parameter")
    assert [e["parameter"] for e in body["errors"]] == ["dictionary"]
    assert "not a dictionary" not in response.text


def test_check_takes_exactly_one_file_part(client):
    """Two parts named ``file`` are refused, not "the last one wins"."""
    clean, other = (AGS4 / "c2_clean.ags").read_bytes(), SITE.read_bytes()
    two_files = client.post(
        "/v1/ags4/check",
        files=[("file", ("a.ags", clean, "text/plain")), ("file", ("b.ags", other, "text/plain"))],
    )
    body = _problem(two_files, 400, "invalid-multipart")
    assert body["detail"] == "Send exactly one part named 'file'."
    two_dictionaries = client.post(
        "/v1/ags4/check",
        files=[
            ("file", ("a.ags", clean, "text/plain")),
            ("dictionary", ("d.ags", clean, "text/plain")),
            ("dictionary", ("e.ags", clean, "text/plain")),
        ],
    )
    _problem(two_dictionaries, 400, "invalid-multipart")
    stranger = client.post(
        "/v1/ags4/check",
        files=[("file", ("a.ags", clean, "text/plain")), ("other", ("o.ags", clean, "text/plain"))],
    )
    _problem(stranger, 400, "invalid-multipart")


def test_check_of_what_is_not_ags4_is_a_finding(client):
    response = client.post("/v1/ags4/check", content=b"<Diggs/>", headers=TEXT)
    assert response.status_code == 200
    entry = response.json()["files"][0]
    assert (entry["path"], [f["rule_id"] for f in entry["findings"]]) == (
        "input.ags",
        ["ags4.rule-2"],
    )


def test_check_answers_sarif_when_asked(client):
    response = client.post(
        "/v1/ags4/check",
        content=SITE.read_bytes(),
        headers={**TEXT, "accept": "application/sarif+json"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/sarif+json"
    assert response.json()["version"] == "2.1.0"


def test_check_leaves_no_file_behind(client, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    response = client.post("/v1/ags4/check", content=SITE.read_bytes(), headers=TEXT)
    assert response.status_code == 200
    assert list(tmp_path.iterdir()) == []
    assert str(tmp_path) not in response.text


def test_the_openapi_document_describes_both_operations():
    document = json.loads((ROOT / "src" / "pydiggs" / "server" / "openapi.json").read_text())
    convert = document["paths"]["/v1/convert"]["post"]
    schema = convert["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/ConversionResult"}
    result = document["components"]["schemas"]["ConversionResult"]
    assert result["properties"]["output_base64"]["contentEncoding"] == "base64"
    assert result["properties"]["report"] == {"$ref": "#/components/schemas/DiggsFindingsDocument"}
    assert set(convert["requestBody"]["content"]) == {
        *_schemas.AGS4_MEDIA_TYPES,
        *_schemas.XML_MEDIA_TYPES,
        "multipart/form-data",
    }
    assert {p["name"] for p in convert["parameters"]} == {
        "name",
        "to",
        "diggs_version",
        "ags_version",
    }
    assert result["properties"]["to"] == {
        "enum": ["diggs", "ags4"],
        "description": "The output format.",
    }
    assert {"ags_version", "residual_base64"} <= set(result["required"])
    check = document["paths"]["/v1/ags4/check"]["post"]
    form = check["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert set(form["properties"]) == {"file", "dictionary", "name"}
    assert set(check["responses"]["200"]["content"]) == {
        "application/json",
        "application/sarif+json",
    }


# --- /v1/convert to=ags4 ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def site_xml() -> bytes:
    xml = convert.ags4_to_diggs(AGS4 / "convert_tier_a.ags").xml
    assert xml is not None
    return xml


@pytest.mark.parametrize("version", [None, "4.2", "4.1.1"])
def test_convert_to_ags4(client, site_xml, version):
    params = {"to": "ags4", "name": "up/site.xml"} | ({"ags_version": version} if version else {})
    response = client.post(
        "/v1/convert", params=params, content=site_xml, headers={"content-type": "application/xml"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(RESULT.iter_errors(body)) == []
    expected = convert.diggs_to_ags4(site_xml, name="site.xml", ags_version=version or "4.2")
    assert expected.ags4 is not None and expected.validation is not None
    output = base64.b64decode(body["output_base64"], validate=True)
    assert output == ags4.dumps(expected.ags4)
    assert body["output_sha256"] == hashlib.sha256(output).hexdigest()
    assert (body["to"], body["media_type"], body["output_name"]) == (
        "ags4",
        "text/plain; charset=utf-8",
        "site.ags",
    )
    assert (body["ags_version"], body["diggs_version"]) == (version or "4.2", "3.0.0")
    assert body["residual_base64"] is None
    assert body["report"] == expected.report.to_document()
    assert body["validation"] == expected.validation.to_document()


def test_convert_to_ags4_multipart_gives_the_same_result(client, site_xml):
    raw = client.post(
        "/v1/convert",
        params={"to": "ags4", "name": "site.xml"},
        content=site_xml,
        headers={"content-type": "text/xml"},
    )
    form = client.post(
        "/v1/convert",
        files={"file": ("site.xml", site_xml, "application/xml")},
        data={"to": "ags4", "ags_version": "4.2"},
    )
    assert raw.status_code == form.status_code == 200
    assert raw.content == form.content


def test_convert_to_ags4_refusals(client, site_xml):
    xml = {"content-type": "application/xml"}
    # A raw body's media type follows the direction: XML for to=ags4, AGS4 text otherwise.
    _problem(
        client.post("/v1/convert", params={"to": "ags4"}, content=site_xml, headers=TEXT),
        415,
        "unsupported-media-type",
    )
    body = _problem(
        client.post("/v1/convert", params={"to": "ags4"}, content=b"<html/>", headers=xml),
        422,
        "invalid-document",
    )
    assert (body["kind"], body["title"]) == ("not-diggs", "Invalid document")
    body = _problem(
        client.post(
            "/v1/convert",
            params={"to": "ags4", "diggs_version": "3.0.0", "ags_version": "4"},
            content=site_xml,
            headers=xml,
        ),
        422,
        "invalid-parameter",
    )
    assert [e["parameter"] for e in body["errors"]] == ["diggs_version", "ags_version"]


def test_the_invalid_document_title_fits_both_inputs(client):
    ags = _problem(
        client.post("/v1/convert", content=b"<Diggs/>", headers=TEXT), 422, "invalid-document"
    )
    diggs = _problem(
        client.post("/v1/read", content=b"<html/>", headers={"content-type": "application/xml"}),
        422,
        "invalid-document",
    )
    assert ags["title"] == diggs["title"] == "Invalid document"
