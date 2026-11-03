"""The sidecar wire protocol: framing, message checks, parameters, errors and OpenRPC."""

from __future__ import annotations

import base64
import doctest
import json
from importlib import resources

import pytest

import pydiggs
from pydiggs._service import Code
from pydiggs.sidecar import _loop, _paths, _protocol
from pydiggs.sidecar._protocol import ErrorCode, Invalid, Message, ProtocolError


def _slug(parsed):
    assert isinstance(parsed, Invalid)
    return parsed.error.error.slug


# --- error codes ------------------------------------------------------------------------------


def test_error_codes_are_unique_and_application_codes_sit_in_the_pydiggs_band():
    codes = [e.code for e in ErrorCode]
    slugs = [e.slug for e in ErrorCode]
    assert len(set(codes)) == len(codes) and len(set(slugs)) == len(slugs)
    app = {e.slug: e.code for e in ErrorCode if e.code > -32100}
    assert app == {
        "busy": -32000,
        "not-initialized": -32001,
        "shutting-down": -32002,
        "input-too-large": -32003,
        "compressed-input": -32004,
        "path-not-permitted": -32005,
        "input-unreadable": -32006,
        "resource-error": -32007,
        "cancelled": -32008,
        "unsupported-protocol-version": -32009,
    }
    assert all(-32099 <= c <= -32000 for c in app.values())


def test_every_service_code_maps_and_shared_slugs_agree():
    for code in Code:
        error = _protocol.service_error(code, "detail")
        assert error.text() == "detail"
    for code in (Code.INPUT_TOO_LARGE, Code.COMPRESSED_INPUT, Code.RESOURCE_ERROR, Code.CANCELLED):
        assert _protocol.service_error(code).error.slug == code.value
    assert _protocol.service_error(Code.INPUT_UNREADABLE).error.slug == "input-unreadable"
    assert _protocol.service_error(Code.INTERNAL).error is ErrorCode.INTERNAL_ERROR
    assert _protocol.service_error(Code.INTERNAL).text() == "An internal error occurred."


# --- framing -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "slug"),
    [
        (b"\xff\xfe", "parse-error"),  # invalid UTF-8
        (b"\xef\xbb\xbf{}", "parse-error"),  # BOM
        (b"{", "parse-error"),
        (b'{"a": NaN}', "parse-error"),
        (b'{"a": Infinity}', "parse-error"),
        (b"[" * 100_000 + b"]" * 100_000, "parse-error"),  # deeper than the interpreter allows
        (b'{"id": ' + b"9" * 5000 + b"}", "parse-error"),  # longer than int conversion allows
        (b"[]", "invalid-request"),
        (b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', "invalid-request"),
        (b'"x"', "invalid-request"),
        (b"3", "invalid-request"),
        (b'{"jsonrpc":"2.0","id":null,"method":"ping"}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":true,"method":"ping"}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":1.5,"method":"ping"}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":{},"method":"ping"}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":"' + b"x" * 257 + b'","method":"ping"}', "invalid-request"),
        (b'{"jsonrpc":"1.0","id":1,"method":"ping"}', "invalid-request"),
        (b'{"id":1,"method":"ping"}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":1,"method":""}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":1,"method":5}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":1,"method":"ping","params":"x"}', "invalid-request"),
        (b'{"jsonrpc":"2.0","id":1,"method":"ping","params":[1]}', "invalid-params"),
        (b'{"jsonrpc":"2.0","id":1,"result":{}}', "invalid-request"),  # hosts send no responses
    ],
)
def test_malformed_lines_are_clean_errors(line, slug):
    assert _slug(_protocol.parse_line(line)) == slug


def test_ids_survive_when_usable_and_are_null_otherwise():
    assert _protocol.parse_line(b'{"jsonrpc":"1.0","id":"a","method":"x"}').id == "a"
    assert _protocol.parse_line(b'{"jsonrpc":"2.0","id":null,"method":"x"}').id is None
    assert _protocol.parse_line(b"{").id is None
    bad_params = _protocol.parse_line(b'{"jsonrpc":"2.0","method":"x","params":[1]}')
    assert bad_params.silent  # a notification is never answered
    assert not _protocol.parse_line(b'{"jsonrpc":"2.0","id":2,"method":"x","params":[1]}').silent


def test_valid_messages():
    assert _protocol.parse_line(b" \t") is None
    assert _protocol.parse_line(b"") is None
    msg = _protocol.parse_line('{"jsonrpc":"2.0","id":"é","method":"m","params":{"a":1}}'.encode())
    assert msg == Message("m", {"a": 1}, "é", is_notification=False)
    note = _protocol.parse_line(b'{"jsonrpc":"2.0","method":"exit"}')
    assert note == Message("exit", {}, None, is_notification=True)
    assert _protocol.parse_line(b'{"jsonrpc":"2.0","id":-7,"method":"m"}').id == -7


def test_frames_are_single_lines_even_with_unicode_line_separators():
    tricky = f"a\nb\rc\u0085d{chr(0x2028)}e{chr(0x2029)}f"
    frame = _protocol.result_frame(1, {"m": tricky})
    assert frame.endswith(b"\n") and frame.count(b"\n") == 1
    assert len(frame.decode("utf-8").splitlines()) == 1
    assert json.loads(frame)["result"]["m"] == tricky
    raw = _protocol.result_frame("x", raw=f'{{"m":"{chr(0x2028)}"}}'.encode())
    assert len(raw.decode().splitlines()) == 1 and json.loads(raw)["result"]["m"] == chr(0x2028)
    lone = _protocol.result_frame(1, {"m": "\udcff"})
    assert json.loads(lone)["result"]["m"] == "\udcff"  # a JSON escape, valid UTF-8


def test_error_frames_carry_slugs_problems_and_extras():
    error = ProtocolError(
        ErrorCode.INVALID_PARAMS, errors=(("checks", "bad"),), extra={"correlation_id": "c"}
    )
    body = json.loads(_protocol.error_frame("r", error))
    assert body == {
        "jsonrpc": "2.0",
        "id": "r",
        "error": {
            "code": -32602,
            "message": "Invalid params",
            "data": {
                "code": "invalid-params",
                "errors": [{"param": "checks", "problem": "bad"}],
                "correlation_id": "c",
            },
        },
    }


def test_max_line_bytes_follows_the_brief():
    assert _protocol.max_line_bytes(25 * 2**20) == -(-4 * 25 * 2**20 // 3) + 64 * 1024


# --- parameters --------------------------------------------------------------------------------

KNOWN = ("2.5.a", "2.6", "3.0.0")


def _problem(call):
    with pytest.raises(ProtocolError) as caught:
        call()
    return caught.value


def test_initialize_params():
    full = _protocol.check_initialize(
        {
            "protocol_version": "1.0",
            "client_info": {"name": "host", "version": "2"},
            "capabilities": {"progress": True, "future": {"x": 1}},
            "diggs_versions": ["3.0.0", "2.6", "2.6"],
        },
        KNOWN,
    )
    assert (full.progress, full.diggs_versions, full.client_name) == (
        True,
        ("2.6", "3.0.0"),
        "host",
    )
    plain = _protocol.check_initialize({"protocol_version": "1.3"}, KNOWN)
    assert (plain.progress, plain.diggs_versions, plain.client_name) == (False, None, None)


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"protocol_version": 1},
        {"protocol_version": "1"},
        {"protocol_version": "01.0"},
        {"protocol_version": "1.0", "extra": 1},
        {"protocol_version": "1.0", "client_info": "x"},
        {"protocol_version": "1.0", "client_info": {"name": "x" * 129}},
        {"protocol_version": "1.0", "client_info": {"version": "x" * 65}},
        {"protocol_version": "1.0", "client_info": {"os": "x"}},
        {"protocol_version": "1.0", "client_info": None},
        {"protocol_version": "1.0", "capabilities": []},
        {"protocol_version": "1.0", "capabilities": {"progress": "yes"}},
        {"protocol_version": "1.0", "diggs_versions": []},
        {"protocol_version": "1.0", "diggs_versions": ["9.9"]},
        {"protocol_version": "1.0", "diggs_versions": "2.6"},
    ],
)
def test_bad_initialize_params(params):
    assert (
        _problem(lambda: _protocol.check_initialize(params, KNOWN)).error.slug == "invalid-params"
    )


def test_negotiation():
    assert _protocol.negotiate("1.0") == "1.0"
    assert _protocol.negotiate("1.9999") == "1.0"
    for bad in ("0.9", "2.0"):
        error = _problem(lambda v=bad: _protocol.negotiate(v))
        assert error.error.slug == "unsupported-protocol-version"
        assert error.data()["supported"] == ["1.0"]


def test_unknown_params_are_refused_with_safe_names():
    error = _problem(lambda: _protocol.check_empty({"\x1b[31m" + "y" * 100: 1, "z": 2}))
    (first, second) = error.data()["errors"]
    assert first["param"].startswith("?[31my") and first["param"].endswith("...")
    assert len(first["param"]) == 67 and second == {"param": "z", "problem": "unknown parameter"}
    many = _problem(lambda: _protocol.check_empty({str(i): i for i in range(50)}))
    assert len(many.data()["errors"]) == 20


def _validate(params, max_bytes=100, time_limit=60.0):
    return _protocol.check_validate(params, max_bytes=max_bytes, time_limit=time_limit)


def test_validate_params():
    content = base64.b64encode(b"<Diggs/>").decode()
    p = _validate(
        {
            "content_base64": content,
            "name": "a.xml",
            "checks": ["schema"],
            "diggs_version": "2.6",
            "time_budget_s": 5,
        }
    )
    assert (p.content, p.path, p.name, p.checks, p.diggs_version, p.time_budget) == (
        b"<Diggs/>",
        None,
        "a.xml",
        ["schema"],
        "2.6",
        5,
    )
    q = _validate({"path": "/x/a.xml"})
    assert (q.content, q.path, q.time_budget) == (None, "/x/a.xml", 60.0)
    assert _validate({"content_base64": ""}).content == b""
    exact = base64.b64encode(b"x" * 99).decode()
    assert len(_validate({"content_base64": exact}).content) == 99


@pytest.mark.parametrize(
    ("params", "slug"),
    [
        ({}, "invalid-params"),
        ({"content_base64": "", "path": "/a"}, "invalid-params"),
        ({"content_base64": 5}, "invalid-params"),
        ({"content_base64": "PERpZ2dzLz4"}, "invalid-params"),  # missing padding
        ({"content_base64": "PERp Z2dzLz4="}, "invalid-params"),  # whitespace
        ({"content_base64": "PERp\nZ2dzLz4="}, "invalid-params"),
        ({"content_base64": "PERp*2dzLz4="}, "invalid-params"),  # junk
        ({"content_base64": "PERpZ2dzLz4=é"}, "invalid-params"),  # non-ASCII
        ({"content_base64": "A" * 140}, "input-too-large"),  # encoded length alone
        ({"content_base64": base64.b64encode(b"x" * 101).decode()}, "input-too-large"),
        ({"path": ""}, "invalid-params"),
        ({"path": 3}, "invalid-params"),
        ({"path": "/" + "a" * 5000}, "invalid-params"),
        ({"path": "/a", "name": 3}, "invalid-params"),
        ({"path": "/a", "name": "n" * 4097}, "invalid-params"),
        ({"path": "/a", "checks": "schema"}, "invalid-params"),
        ({"path": "/a", "checks": [1]}, "invalid-params"),
        ({"path": "/a", "diggs_version": None}, "invalid-params"),
        ({"path": "/a", "time_budget_s": 0}, "invalid-params"),
        ({"path": "/a", "time_budget_s": 61}, "invalid-params"),
        ({"path": "/a", "time_budget_s": True}, "invalid-params"),
        ({"path": "/a", "time_budget_s": "5"}, "invalid-params"),
        ({"path": "/a", "time_budget_s": 10**400}, "invalid-params"),
        ({"path": "/a", "content": "x"}, "invalid-params"),
    ],
)
def test_bad_validate_params(params, slug):
    assert _problem(lambda: _validate(params)).error.slug == slug


def test_too_large_reports_the_limit():
    error = _problem(lambda: _validate({"content_base64": "A" * 400}))
    assert error.data() == {"code": "input-too-large", "max_bytes": 100}


def test_rules_and_cancel_params():
    assert _protocol.check_rules_list({}) is None
    assert _protocol.check_rules_list({"step": "schema"}) == "schema"
    assert _problem(lambda: _protocol.check_rules_list({"step": 1})).error.slug == "invalid-params"
    assert _problem(lambda: _protocol.check_explain({})).error.slug == "invalid-params"
    long_id = "a." + "b" * 300
    assert _problem(lambda: _protocol.check_explain({"rule_id": long_id})).errors[0][0] == "rule_id"
    assert _protocol.check_cancel({"id": "x"}) == "x"
    assert _protocol.check_cancel({}) is None
    assert _protocol.check_cancel({"id": None}) is None
    assert _protocol.check_cancel({"id": 1.0}) is None


# --- OpenRPC and doctests ----------------------------------------------------------------------


def test_the_committed_openrpc_document_matches_the_code():
    committed = json.loads(
        resources.files("pydiggs.sidecar").joinpath("openrpc.json").read_text("utf-8")
    )
    expected = _protocol.openrpc_document(pydiggs.FINDINGS_SCHEMA_URL)
    assert committed == expected, (
        "openrpc.json is stale: regenerate it from"
        " pydiggs.sidecar._protocol.openrpc_document(pydiggs.FINDINGS_SCHEMA_URL)"
    )
    names = [m["name"] for m in committed["methods"]]
    assert names == [
        "initialize",
        "validate",
        "ping",
        "versions",
        "rules.list",
        "rules.explain",
        "shutdown",
    ]
    assert set(committed["components"]["errors"]) == {e.slug for e in ErrorCode}


@pytest.mark.parametrize("module", [_protocol, _paths, _loop])
def test_doctests(module):
    failed, _ = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert failed == 0
