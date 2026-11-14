"""The sidecar's protocol 1.2 methods: ``convert`` and ``ags4.check``, in-process."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from pydiggs import _service
from pydiggs._serve_cli import ServeOptions
from pydiggs.sidecar import _protocol
from tests.test_sidecar_loop import Session

ROOT = Path(__file__).resolve().parent.parent
AGS4 = ROOT / "tests" / "fixtures" / "ags4"
SITE = AGS4 / "site_tier_a.ags"


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


def _initialize(s: Session, version: str = "1.2") -> dict:
    reply = s.call("initialize", {"protocol_version": version}, id="init")
    return reply["result"]


def test_protocol_1_2_advertises_convert_and_ags4_check(session):
    result = _initialize(session())
    assert result["protocol_version"] == "1.2"
    assert result["capabilities"]["convert"] == {"content": True, "path": False, "to": ["diggs"]}
    assert result["capabilities"]["ags4_check"] == {
        "content": True,
        "path": False,
        "dictionary": True,
    }
    assert result["capabilities"]["read"]["select"] is True  # 1.1's read is still there


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_older_sessions_do_not_have_the_new_methods(session, version):
    s = session()
    result = _initialize(s, version)
    assert "convert" not in result["capabilities"]
    assert "ags4_check" not in result["capabilities"]
    content = _b64(SITE.read_bytes())
    assert _slug(s.call("convert", {"content_base64": content})) == "method-not-found"
    assert _slug(s.call("ags4.check", {"content_base64": content}, id=2)) == "method-not-found"


def test_convert_answers_what_the_service_answers(session):
    data = SITE.read_bytes()
    s = session(options=ServeOptions(max_bytes=len(data) + 10))
    _initialize(s)
    result = s.call("convert", {"content_base64": _b64(data), "name": "up/site.ags"})["result"]
    expected = _service.convert(_service.ConvertRequest(content=data, name="site.ags"))
    assert result == json.loads(expected.document)
    assert result["output_name"] == "site.xml"
    assert base64.b64decode(result["output_base64"]).startswith(b"<?xml")


def test_convert_errors(session):
    s = session()
    _initialize(s)
    bad = s.call("convert", {"content_base64": _b64(b"<Diggs/>")})
    assert _slug(bad) == "invalid-document"
    assert bad["error"]["data"]["kind"] == "not-ags4"
    assert bad["error"]["message"] == "The bytes are not an AGS4 file."
    to = s.call("convert", {"content_base64": _b64(SITE.read_bytes()), "to": "ags4"}, id=2)
    assert _slug(to) == "invalid-params"
    assert [e["param"] for e in to["error"]["data"]["errors"]] == ["to"]
    for params in (
        {"content_base64": _b64(b"x"), "select": {}},
        {"content_base64": _b64(b"x"), "to": 1},
        {"content_base64": _b64(b"x"), "diggs_version": "2.6"},
        {},
    ):
        assert _slug(s.call("convert", params, id=3)) == "invalid-params"


def test_a_conversion_over_the_result_limit_is_result_too_large(session, monkeypatch):
    monkeypatch.setattr(_protocol, "MAX_RESULT_BYTES", 64)
    s = session()
    _initialize(s)
    reply = s.call("convert", {"content_base64": _b64(SITE.read_bytes())})
    assert _slug(reply) == "result-too-large"


def test_ags4_check_with_and_without_a_dictionary(session):
    s = session()
    _initialize(s)
    data = (AGS4 / "c2_rule_10a.ags").read_bytes()
    plain = s.call("ags4.check", {"content_base64": _b64(data), "name": "a/b.ags"})["result"]
    entry = plain["files"][0]
    assert entry["path"] == "b.ags"
    assert ("ags4.rule-10a", 77) in {
        (f["rule_id"], f["location"]["line"]) for f in entry["findings"]
    }
    assert "ags4.dictionary-not-checked" in {f["rule_id"] for f in entry["findings"]}
    dictionary = (AGS4 / "c2_dictionary.ags").read_bytes()
    checked = s.call(
        "ags4.check",
        {"content_base64": _b64(data), "dictionary_base64": _b64(dictionary)},
        id=2,
    )["result"]
    rules = {f["rule_id"] for f in checked["files"][0]["findings"]}
    assert "ags4.dictionary-not-checked" not in rules


def test_ags4_check_errors(session):
    s = session()
    _initialize(s)
    content = _b64(SITE.read_bytes())
    junk = s.call("ags4.check", {"content_base64": content, "dictionary_base64": _b64(b"no")})
    assert _slug(junk) == "invalid-params"
    assert [e["param"] for e in junk["error"]["data"]["errors"]] == ["dictionary"]
    both = s.call(
        "ags4.check",
        {"content_base64": content, "dictionary_base64": content, "dictionary_path": "/x"},
        id=2,
    )
    assert _slug(both) == "invalid-params"
    assert _slug(s.call("ags4.check", {"content_base64": content, "to": "x"}, id=3)) == (
        "invalid-params"
    )
    not_ags4 = s.call("ags4.check", {"content_base64": _b64(b"<Diggs/>")}, id=4)["result"]
    assert [f["rule_id"] for f in not_ags4["files"][0]["findings"]] == ["ags4.rule-2"]


@pytest.fixture
def root(tmp_path):
    base = Path(os.path.realpath(tmp_path))
    (base / "root").mkdir()
    (base / "outside").mkdir()
    (base / "root" / "site.ags").write_bytes(SITE.read_bytes())
    (base / "root" / "dict.ags").write_bytes((AGS4 / "c2_dictionary.ags").read_bytes())
    (base / "outside" / "secret.ags").write_bytes(SITE.read_bytes())
    return base / "root"


def test_path_mode(session, root):
    s = session(roots=(root,))
    result = _initialize(s)
    assert result["capabilities"]["convert"]["path"] is True
    converted = s.call("convert", {"path": str(root / "site.ags")})["result"]
    assert converted["output_name"] == "site.xml"
    assert str(root) not in json.dumps(converted)
    checked = s.call(
        "ags4.check",
        {"path": str(root / "site.ags"), "dictionary_path": str(root / "dict.ags")},
        id=2,
    )["result"]
    assert checked["files"][0]["path"] == "site.ags"
    outside = s.call("convert", {"path": str(root.parent / "outside" / "secret.ags")}, id=3)
    assert _slug(outside) == "path-not-permitted"
    dictionary_outside = s.call(
        "ags4.check",
        {
            "path": str(root / "site.ags"),
            "dictionary_path": str(root.parent / "outside" / "secret.ags"),
        },
        id=4,
    )
    assert _slug(dictionary_outside) == "path-not-permitted"


def test_a_queued_conversion_can_be_cancelled(session, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()

    def slow(request, *, should_stop=None):
        started.set()
        release.wait(30)
        return _service.Outcome(document=b"{}")

    monkeypatch.setattr(_service, "convert", slow)
    s = session()
    _initialize(s)
    content = _b64(SITE.read_bytes())
    s.send("convert", {"content_base64": content}, 1)
    assert started.wait(30)
    s.send("ags4.check", {"content_base64": content}, 2)
    s.send("$/cancelRequest", {"id": 2})
    cancelled = s.recv()
    assert cancelled["id"] == 2 and _slug(cancelled) == "cancelled"
    release.set()
    assert s.recv() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_each_ags4_check_input_follows_its_own_transports_limit(session, root):
    """Inline content with a dictionary_path: the dictionary gets the path limit."""
    data = (AGS4 / "c2_user_group.ags").read_bytes()
    dictionary = root / "dict.ags"
    assert len(data) < dictionary.stat().st_size
    options = ServeOptions(max_bytes=len(data), max_path_bytes=10 * len(data))
    s = session(options=options, roots=(root,))
    _initialize(s)
    reply = s.call("ags4.check", {"content_base64": _b64(data), "dictionary_path": str(dictionary)})
    assert "result" in reply, reply
    assert "ags4.dictionary-not-checked" not in {
        f["rule_id"] for f in reply["result"]["files"][0]["findings"]
    }
    (root / "user.ags").write_bytes(data)
    tight = session(options=ServeOptions(max_bytes=len(data) - 1), roots=(root,))
    _initialize(tight)
    inline = tight.call(
        "ags4.check", {"path": str(root / "user.ags"), "dictionary_base64": _b64(data)}, id=2
    )
    assert _slug(inline) == "input-too-large"  # the inline dictionary keeps the inline limit
