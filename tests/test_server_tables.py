"""``POST /v1/tables/*`` over HTTP (thread mode; preview).

A package goes up as a ZIP (raw ``application/zip`` or a multipart ``file`` part) and the
answer is the service's document; DIGGS goes up as XML and a package ZIP comes back in
base64. A ZIP is untrusted: hostile archives are refused without extracting anything.
"""

from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

import jsonschema
import pytest
from starlette.testclient import TestClient

import pydiggs
from pydiggs import _service, tables
from pydiggs.server import ServerSettings, _schemas, create_app
from pydiggs.tables import _io, _package
from tests.test_tables_surfaces import FILES, _raw_zip

ROOT = Path(__file__).resolve().parent.parent
AEROMAG = ROOT / "tests" / "fixtures" / "official" / "3.0" / "Aeromag.xml"
ZIP = {"content-type": "application/zip"}
XML = {"content-type": "application/xml"}
BASE = ServerSettings(workers=2, isolation="thread", allowed_hosts=("testserver",))
PACKAGE = _package.pack(list(FILES.items()))


def _validator(build) -> jsonschema.Draft202012Validator:
    findings = pydiggs.findings_schema()
    return jsonschema.Draft202012Validator(build(findings) | {"$defs": findings["$defs"]})


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(BASE))


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["code"]) == (status, code)
    return body


@pytest.mark.parametrize("operation", _service.TABLES_OPERATIONS)
def test_each_package_route_answers_what_the_service_answers(client, operation):
    response = client.post(
        f"/v1/tables/{operation}", params={"name": "site.zip"}, content=PACKAGE, headers=ZIP
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    expected = _service.tables(
        _service.TablesRequest(content=PACKAGE, name="site.zip", operation=operation)
    )
    assert response.content == expected.document


def test_to_diggs_gives_the_python_bytes_and_a_valid_result(client):
    body = client.post("/v1/tables/to-diggs", content=PACKAGE, headers=ZIP).json()
    assert list(_validator(_schemas._tables_to_diggs).iter_errors(body)) == []
    rows = {n: list(r) for n, r in _io.tables_from_files(FILES).items()}
    expected = pydiggs.write_bytes(tables.to_model(rows).model)
    assert base64.b64decode(body["output_base64"]) == expected
    assert body["output_name"] == "tables.xml"


def test_round_trip_result_is_valid(client):
    body = client.post("/v1/tables/round-trip", content=PACKAGE, headers=ZIP).json()
    assert list(_validator(_schemas._tables_round_trip).iter_errors(body)) == []


def test_a_multipart_package_and_the_windows_media_type_are_accepted(client):
    by_form = client.post(
        "/v1/tables/to-diggs", files={"file": ("up/site.zip", PACKAGE, "application/zip")}
    )
    assert by_form.status_code == 200
    assert by_form.json()["output_name"] == "site.xml"
    windows = client.post(
        "/v1/tables/check",
        content=PACKAGE,
        headers={"content-type": "application/x-zip-compressed"},
    )
    assert windows.status_code == 200


def test_from_diggs_answers_a_package_zip(client):
    data = AEROMAG.read_bytes()
    response = client.post(
        "/v1/tables/from-diggs", params={"name": "aero.xml"}, content=data, headers=XML
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(_validator(_schemas._tables_from_diggs).iter_errors(body)) == []
    made = tables.from_model(pydiggs.read_bytes(data, name="aero.xml"))
    assert base64.b64decode(body["output_base64"]) == _package.pack(_io.package_files(made))
    by_form = client.post(
        "/v1/tables/from-diggs", files={"file": ("aero.xml", data, "application/xml")}
    )
    assert by_form.content == response.content
    not_diggs = client.post("/v1/tables/from-diggs", content=b"<html/>", headers=XML)
    assert _problem(not_diggs, 422, "invalid-document")["kind"] == "not-diggs"


@pytest.mark.parametrize(
    ("archive", "kind"),
    [
        (b"not a zip", "not-zip"),
        (_raw_zip(("../features.csv", b"key\n")), "unsafe-package"),
        (_raw_zip(("/etc/features.csv", b"key\n")), "unsafe-package"),
        (_raw_zip(("features.csv", b"k\n"), ("evil.sh", b"rm")), "unsafe-package"),
        (_package.pack([("boreholes.csv", b"key\nBH-1\n")]), "not-tables"),
    ],
)
def test_a_hostile_or_unusable_zip_is_422(client, archive, kind):
    response = client.post("/v1/tables/to-diggs", content=archive, headers=ZIP)
    body = _problem(response, 422, "invalid-document")
    assert body["kind"] == kind
    assert "etc" not in body["detail"] and "evil" not in body["detail"]


def test_a_zip_bomb_is_413(client):
    small = TestClient(
        create_app(
            ServerSettings(
                workers=1, isolation="thread", allowed_hosts=("testserver",), max_body_bytes=1 << 20
            )
        )
    )
    bomb = _raw_zip(("features.csv", b"0" * (8 << 20)), compression=zipfile.ZIP_DEFLATED)
    _problem(small.post("/v1/tables/to-diggs", content=bomb, headers=ZIP), 413, "input-too-large")
    too_many = _raw_zip(*((f"results_T{n}.csv", b"measurement\n") for n in range(300)))
    _problem(small.post("/v1/tables/check", content=too_many, headers=ZIP), 413, "input-too-large")


def test_a_package_body_over_the_limit_is_413(client):
    small = TestClient(
        create_app(
            ServerSettings(
                workers=1, isolation="thread", allowed_hosts=("testserver",), max_body_bytes=64
            )
        )
    )
    _problem(
        small.post("/v1/tables/to-diggs", content=PACKAGE, headers=ZIP), 413, "input-too-large"
    )


def test_unknown_query_parameters_media_types_and_accept_are_checked(client):
    unknown = client.post(
        "/v1/tables/to-diggs", params={"operation": "check"}, content=PACKAGE, headers=ZIP
    )
    assert _problem(unknown, 422, "invalid-parameter")["errors"] == [
        {"parameter": "operation", "problem": "unknown parameter"}
    ]
    _problem(
        client.post("/v1/tables/to-diggs", content=PACKAGE, headers={"content-type": "text/csv"}),
        415,
        "unsupported-media-type",
    )
    _problem(
        client.post("/v1/tables/from-diggs", content=PACKAGE, headers=ZIP),
        415,
        "unsupported-media-type",
    )
    _problem(
        client.post(
            "/v1/tables/to-diggs",
            content=PACKAGE,
            headers={**ZIP, "accept": "application/sarif+json"},
        ),
        406,
        "not-acceptable",
    )
    form = client.post(
        "/v1/tables/to-diggs",
        files={"file": ("p.zip", PACKAGE, "application/zip")},
        data={"operation": "check"},
    )
    _problem(form, 400, "invalid-multipart")
    long_name = client.post(
        "/v1/tables/to-diggs", params={"name": "x" * 5000}, content=PACKAGE, headers=ZIP
    )
    _problem(long_name, 422, "invalid-parameter")


def test_the_routes_are_in_the_openapi_document():
    document = json.loads((ROOT / "src/pydiggs/server/openapi.json").read_text("utf-8"))
    for operation in (*_service.TABLES_OPERATIONS, "from-diggs"):
        route = document["paths"][f"/v1/tables/{operation}"]["post"]
        assert [p["name"] for p in route["parameters"]] == ["name"]
    to_diggs = document["paths"]["/v1/tables/to-diggs"]["post"]
    schema = to_diggs["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/TablesToDiggsResult"}
    assert set(to_diggs["requestBody"]["content"]) == {
        "application/zip",
        "application/x-zip-compressed",
        "multipart/form-data",
    }
