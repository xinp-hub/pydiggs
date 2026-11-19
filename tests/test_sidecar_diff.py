"""The sidecar's protocol 1.3 method ``diff``, in-process."""

from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

import pytest

from pydiggs import _service
from pydiggs import diff as diff_module
from pydiggs._serve_cli import ServeOptions
from pydiggs.sidecar import _protocol
from tests.diff._helpers import schema_errors
from tests.test_sidecar_loop import Session

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official" / "3.0"
OLD = OFFICIAL / "MWDExample.xml"
NEW = OFFICIAL / "TrenchWallExample.xml"
SAME = OFFICIAL / "Aeromag.xml"


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(_service, "warm_worker", lambda versions=None: None)
    sessions: list[Session] = []

    def make(**kwargs) -> Session:
        s = Session(**kwargs)
        sessions.append(s)
        return s

    yield make
    for s in sessions:
        s.close()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _slug(reply: dict) -> str:
    return reply["error"]["data"]["code"]


def _initialize(s: Session, version: str = "1.3") -> dict:
    return s.call("initialize", {"protocol_version": version}, id="init")["result"]


def _expected(old: bytes, new: bytes, old_name: str, new_name: str, **options) -> dict:
    options.setdefault("time_budget", ServeOptions().timeout)
    document = diff_module.compare(old, new, **options).to_dict()
    document["old"]["name"], document["new"]["name"] = old_name, new_name
    return document


def test_protocol_1_3_advertises_diff(session):
    result = _initialize(session())
    assert result["protocol_version"] == "1.3"
    assert result["capabilities"]["diff"] == {
        "content": True,
        "path": False,
        "match": ["auto", "gml-id", "natural", "gml_id"],
    }
    assert result["diff_schema_version"] == diff_module.DIFF_SCHEMA_VERSION == "1.0"
    assert result["diff_schema_url"] == diff_module.DIFF_SCHEMA_URL
    assert result["capabilities"]["convert"]["residual"] is True  # 1.2 is still there


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2"])
def test_older_sessions_refuse_diff(session, version):
    s = session()
    result = _initialize(s, version)
    assert result["protocol_version"] == version
    assert "diff" not in result["capabilities"]
    assert "diff_schema_url" not in result
    content = _b64(SAME.read_bytes())
    reply = s.call("diff", {"old_base64": content, "new_base64": content})
    assert _slug(reply) == "method-not-found"


def test_diff_answers_what_compare_gives(session):
    s = session()
    _initialize(s)
    old, new = OLD.read_bytes(), NEW.read_bytes()
    reply = s.call(
        "diff",
        {
            "old_base64": _b64(old),
            "new_base64": _b64(new),
            "old_name": "up/a.xml",
            "match": "gml_id",
            "max_changes": 3,
            "show_renamed": True,
            "time_budget_s": 30,
        },
    )
    document = reply["result"]
    assert document == _expected(
        old,
        new,
        "a.xml",
        "input.xml",
        match="gml-id",
        max_changes=3,
        show_renamed=True,
        time_budget=30,
    )
    assert schema_errors(document) == []
    identical = s.call("diff", {"old_base64": _b64(old), "new_base64": _b64(old)}, id=2)["result"]
    assert identical["identical"] is True


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"new_base64": "PERpZ2dzLz4="}, "old_base64"),
        ({"old_base64": "PERpZ2dzLz4=", "old_path": "/a", "new_base64": "x"}, "old_base64"),
        ({"old_base64": "PERpZ2dzLz4="}, "new_base64"),
        ({"old_base64": "!!", "new_base64": "PERpZ2dzLz4="}, "old_base64"),
        ({"old_base64": "PERpZ2dzLz4=", "new_path": 5}, "new_path"),
        (
            {"old_base64": "PERpZ2dzLz4=", "new_base64": "PERpZ2dzLz4=", "time_budget_s": 0},
            "time_budget_s",
        ),
        (
            {"old_base64": "PERpZ2dzLz4=", "new_base64": "PERpZ2dzLz4=", "content_base64": "x"},
            "content_base64",
        ),
    ],
)
def test_bad_parameters_are_invalid_params(session, params, param):
    s = session()
    _initialize(s)
    reply = s.call("diff", params)
    assert _slug(reply) == "invalid-params"
    assert reply["error"]["data"]["errors"][0]["param"] == param


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("match", "fuzzy"),
        ("rel_tol", 1),
        ("abs_tol", -1),
        ("abs_tol", 10**400),
        ("rel_tol", 10**400),
        ("max_changes", 1.5),
        ("max_changes", True),
        ("show_reformatted", "true"),
    ],
)
def test_bad_option_values_are_refused_by_the_service(session, option, value):
    s = session()
    _initialize(s)
    content = _b64(SAME.read_bytes())
    reply = s.call("diff", {"old_base64": content, "new_base64": content, option: value})
    assert _slug(reply) == "invalid-params"
    assert reply["error"]["data"]["errors"] == [
        {
            "param": option,
            "problem": dict(
                _service._diff_parameter_errors(
                    _service.DiffRequest(old=b"", new=b"", **{option: value})
                )
            )[option],
        }
    ]


@pytest.mark.parametrize(
    ("side", "data", "kind"),
    [("old", b"<html/>", "not-diggs"), ("new", b"<Diggs", "malformed")],
)
def test_a_file_that_is_not_diggs_is_invalid_document_naming_it(session, side, data, kind):
    s = session()
    _initialize(s)
    good = SAME.read_bytes()
    pair = {"old": good, "new": good, side: data}
    reply = s.call("diff", {f"{k}_base64": _b64(v) for k, v in pair.items()})
    assert _slug(reply) == "invalid-document"
    assert reply["error"]["data"]["kind"] == kind
    assert reply["error"]["data"]["errors"] == [
        {"param": side, "problem": "is not a DIGGS document"}
    ]
    assert "html" not in json.dumps(reply)


def test_each_inline_file_is_held_to_max_bytes(session):
    data = SAME.read_bytes()
    s = session(options=ServeOptions(max_bytes=len(data) - 1))
    _initialize(s)
    small = _b64(b"<Diggs/>")
    for params in (
        {"old_base64": _b64(data), "new_base64": small},
        {"old_base64": small, "new_base64": _b64(data)},
    ):
        assert _slug(s.call("diff", params)) == "input-too-large"


def test_a_result_over_the_limit_is_result_too_large(session, monkeypatch):
    monkeypatch.setattr(_protocol, "MAX_RESULT_BYTES", 1_000)
    s = session()
    _initialize(s)
    reply = s.call(
        "diff", {"old_base64": _b64(OLD.read_bytes()), "new_base64": _b64(NEW.read_bytes())}
    )
    assert _slug(reply) == "result-too-large"
    assert reply["error"]["data"]["max_result_bytes"] == 1_000
    assert "max_changes" in reply["error"]["message"]


def test_the_time_budget_covers_the_pair(session):
    s = session(options=ServeOptions(timeout=1e-6))
    _initialize(s)
    reply = s.call(
        "diff", {"old_base64": _b64(OLD.read_bytes()), "new_base64": _b64(NEW.read_bytes())}
    )
    document = reply["result"]
    assert document["complete"] is False and document["identical"] is False
    assert document["limits_hit"]


@pytest.fixture
def root(tmp_path):
    base = Path(os.path.realpath(tmp_path))
    (base / "root").mkdir()
    (base / "outside").mkdir()
    (base / "root" / "old.xml").write_bytes(OLD.read_bytes())
    (base / "root" / "new.xml").write_bytes(NEW.read_bytes())
    (base / "outside" / "secret.xml").write_bytes(NEW.read_bytes())
    return base / "root"


def test_path_mode_and_mixed_transports(session, root):
    s = session(roots=(root,))
    result = _initialize(s)
    assert result["capabilities"]["diff"]["path"] is True
    by_path = s.call(
        "diff", {"old_path": str(root / "old.xml"), "new_path": str(root / "new.xml")}
    )["result"]
    assert (by_path["old"]["name"], by_path["new"]["name"]) == ("old.xml", "new.xml")
    assert str(root) not in json.dumps(by_path)
    mixed = s.call(
        "diff",
        {"old_path": str(root / "old.xml"), "new_base64": _b64(NEW.read_bytes())},
        id=2,
    )["result"]
    assert mixed["changes"] == by_path["changes"]
    outside = s.call(
        "diff",
        {
            "old_path": str(root / "old.xml"),
            "new_path": str(root.parent / "outside" / "secret.xml"),
        },
        id=3,
    )
    assert _slug(outside) == "path-not-permitted"
    assert str(root.parent) not in json.dumps(outside)
    assert outside["error"]["data"]["errors"] == [
        {"param": "new_path", "problem": "is not permitted"}
    ]


def test_a_compressed_file_by_path_names_its_side(session, root):
    import gzip

    (root / "old.xml.gz").write_bytes(gzip.compress(OLD.read_bytes()))
    s = session(roots=(root,))
    _initialize(s)
    reply = s.call(
        "diff", {"old_path": str(root / "old.xml.gz"), "new_base64": _b64(OLD.read_bytes())}
    )
    assert _slug(reply) == "compressed-input"
    assert reply["error"]["data"]["errors"] == [{"param": "old_path", "problem": "is compressed"}]
    assert str(root) not in json.dumps(reply)


def test_each_file_follows_its_own_transports_limit(session, root):
    """An inline file keeps max_bytes even when the other comes by path."""
    data = OLD.read_bytes()
    options = ServeOptions(max_bytes=len(data) - 1, max_path_bytes=10 * len(data))
    s = session(options=options, roots=(root,))
    _initialize(s)
    by_path = s.call("diff", {"old_path": str(root / "old.xml"), "new_path": str(root / "old.xml")})
    assert by_path["result"]["identical"] is True
    inline = s.call("diff", {"old_path": str(root / "old.xml"), "new_base64": _b64(data)}, id=2)
    assert _slug(inline) == "input-too-large"
    too_big = ServeOptions(max_bytes=10 * len(data), max_path_bytes=len(data) - 1)
    small = session(options=too_big, roots=(root,))
    _initialize(small)
    by_path_big = small.call("diff", {"old_base64": _b64(data), "new_path": str(root / "old.xml")})
    assert _slug(by_path_big) == "input-too-large"
    assert by_path_big["error"]["data"]["errors"] == [
        {"param": "new_path", "problem": "is larger than allowed"}
    ]
    assert str(root) not in json.dumps(by_path_big)


def test_a_queued_diff_can_be_cancelled(session, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(request, *, should_stop=None):
        started.set()
        release.wait(30)
        return _service.Outcome(document=b"{}")

    monkeypatch.setattr(_service, "diff", slow)
    s = session()
    _initialize(s)
    content = _b64(SAME.read_bytes())
    params = {"old_base64": content, "new_base64": content}
    s.send("diff", params, 1)
    assert started.wait(30)
    s.send("diff", params, 2)
    s.send("$/cancelRequest", {"id": 2})
    cancelled = s.recv()
    assert cancelled["id"] == 2 and _slug(cancelled) == "cancelled"
    release.set()
    assert s.recv() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_the_running_diff_is_cancelled_at_its_end(session, monkeypatch):
    real = _service.diff
    seen = threading.Event()

    def flagged(request, *, should_stop=None):
        seen.set()
        return real(request, should_stop=lambda: True)

    monkeypatch.setattr(_service, "diff", flagged)
    s = session()
    _initialize(s)
    content = _b64(SAME.read_bytes())
    reply = s.call("diff", {"old_base64": content, "new_base64": content})
    assert seen.is_set() and _slug(reply) == "cancelled"
