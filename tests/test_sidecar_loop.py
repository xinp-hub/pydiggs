"""The sidecar session in-process: lifecycle, ordering, busy, cancel, shutdown, EOF, limits.

A :class:`Server` runs on a pair of pipes. Timing-sensitive tests replace
``_service.validate`` with a gate the test opens, so nothing depends on how long a
real validation takes; a few tests run real validations end to end.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import io
import json
import os
import queue
import signal
import sys
import threading
from pathlib import Path

import jsonschema
import pytest

import pydiggs
from pydiggs import _service
from pydiggs._serve_cli import ServeOptions
from pydiggs._service import Code, Outcome
from pydiggs.cli import main as cli_main
from pydiggs.errors import ResourceError
from pydiggs.sidecar import _loop
from pydiggs.sidecar._loop import Server

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "test_files" / "AtterbergExample.xml"
TIMEOUT = 30


class Session:
    """A server on pipes, with a pump thread collecting its output lines."""

    def __init__(self, options: ServeOptions | None = None, roots: tuple[Path, ...] = ()):
        r_in, w_in = os.pipe()
        r_out, w_out = os.pipe()
        self._in = os.fdopen(w_in, "wb", buffering=0)  # closed in close()
        self._reader = os.fdopen(r_in, "rb")
        self._writer = os.fdopen(w_out, "wb")
        self._out = os.fdopen(r_out, "rb")
        self.server = Server(options or ServeOptions(), roots, self._reader, self._writer)
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.code: int | None = None
        self._runner = threading.Thread(target=self._run, daemon=True)
        self._pump = threading.Thread(target=self._read, daemon=True)
        self._runner.start()
        self._pump.start()

    def _run(self) -> None:
        try:
            self.code = self.server.run()
        finally:
            self._writer.close()

    def _read(self) -> None:
        for line in self._out:
            self.lines.put(line)
        self.lines.put(None)

    def send_raw(self, data: bytes) -> None:
        self._in.write(data)

    def send(self, method: str, params: dict | None = None, id: object = "unset") -> None:  # noqa: A002
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if id != "unset":
            message["id"] = id
        self.send_raw(json.dumps(message).encode() + b"\n")

    def recv_line(self) -> bytes | None:
        return self.lines.get(timeout=TIMEOUT)

    def recv(self) -> dict:
        line = self.recv_line()
        assert line is not None, "the server closed its output"
        assert line.endswith(b"\n") and line.count(b"\n") == 1
        return json.loads(line)

    def call(self, method: str, params: dict | None = None, id: object = 1) -> dict:  # noqa: A002
        self.send(method, params, id)
        return self.recv()

    def replies(self, count: int) -> tuple[list[dict], list[dict]]:
        """The next ``count`` responses, and the notifications seen meanwhile."""
        replies: list[dict] = []
        notes: list[dict] = []
        while len(replies) < count:
            message = self.recv()
            (replies if "id" in message else notes).append(message)
        return replies, notes

    def initialize(self, **params) -> dict:
        reply = self.call("initialize", {"protocol_version": "1.0", **params}, id="init")
        assert "result" in reply, reply
        return reply["result"]

    def close_input(self) -> None:
        if not self._in.closed:
            self._in.close()

    def wait(self) -> int:
        self._runner.join(TIMEOUT)
        assert not self._runner.is_alive()
        assert self.code is not None
        return self.code

    def rest(self) -> list[dict]:
        """Every remaining line once the server has finished."""
        self.wait()
        out = []
        while (line := self.recv_line()) is not None:
            out.append(json.loads(line))
        return out

    def close(self) -> None:
        self.close_input()
        self.server.finish(0)
        self._runner.join(TIMEOUT)
        self._pump.join(TIMEOUT)
        for thread in threading.enumerate():
            if thread.name.startswith("sidecar-"):
                thread.join(TIMEOUT)
        self._reader.close()
        self._out.close()


@pytest.fixture
def no_warm(monkeypatch):
    seen = []
    monkeypatch.setattr(_service, "warm_worker", lambda versions=None: seen.append(versions))
    return seen


@pytest.fixture
def session(no_warm):
    sessions: list[Session] = []

    def make(**kwargs) -> Session:
        s = Session(**kwargs)
        sessions.append(s)
        return s

    yield make
    for s in sessions:
        s.close()


class Gate:
    """Stands in for ``_service.validate``: blocks until released, honours should_stop."""

    def __init__(self) -> None:
        self.started: queue.Queue[str] = queue.Queue()
        self.release = threading.Event()
        self.requests: list[_service.Request] = []
        self.action = None

    def __call__(self, request, *, should_stop=None):
        self.requests.append(request)
        self.started.put(request.name)
        while not self.release.wait(0.005):
            if should_stop():
                return Outcome(code=Code.CANCELLED, detail="The request was cancelled.")
        if self.action is not None:
            return self.action(request)
        return Outcome(document=json.dumps({"name": request.name}).encode())

    def wait_started(self, name: str) -> None:
        assert self.started.get(timeout=TIMEOUT) == name


@pytest.fixture
def gate(monkeypatch):
    g = Gate()
    monkeypatch.setattr(_service, "validate", g)
    yield g
    g.release.set()


def _content(data: bytes = b"<Diggs/>") -> str:
    return base64.b64encode(data).decode()


def _slug(reply: dict) -> str:
    return reply["error"]["data"]["code"]


# --- lifecycle ---------------------------------------------------------------------------------


def test_initialize_reports_versions_capabilities_and_limits(session, no_warm):
    s = session(options=ServeOptions(max_bytes=3000, max_findings=7, timeout=9.5))
    result = s.initialize(client_info={"name": "host\x1b", "version": "1"})
    info = pydiggs.version_info().to_dict()
    assert result["protocol_version"] == "1.0"
    for key in ("pydiggs_version", "findings_schema_version", "findings_schema_url"):
        assert result[key] == info[key]
    assert result["diggs_versions"] == info["diggs_versions"]
    assert result["reference_data"] == info["reference_data"]
    assert result["capabilities"] == {
        "validate": {"content": True, "path": False},
        "cancel": True,
        "progress": False,
        "rules": True,
        "ping": True,
    }
    assert result["limits"] == {
        "max_bytes": 3000,
        "max_path_bytes": ServeOptions().max_path_bytes,
        "max_line_bytes": 4000 + 65536,
        "max_findings": 7,
        "max_response_bytes_estimate": 7 * 2048 + 65536,
        "time_budget_s": 9.5,
    }
    assert no_warm == [None]


def test_initialize_warms_only_the_requested_versions(session, no_warm):
    s = session()
    s.initialize(diggs_versions=["2.6"])
    assert no_warm == [("2.6",)]


def test_cheap_methods(session):
    s = session()
    s.initialize()
    assert s.call("ping", {}) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert s.call("ping")["result"] == {}
    assert s.call("versions", id="v")["result"] == pydiggs.version_info().to_dict()
    every = s.call("rules.list")["result"]
    assert every == [r.to_dict() for r in pydiggs.rule_catalogue()]
    schema_rules = s.call("rules.list", {"step": "schema"})["result"]
    assert schema_rules and {r["step"] for r in schema_rules} == {"schema"}
    bad_step = s.call("rules.list", {"step": "nope"})
    assert _slug(bad_step) == "invalid-params"
    assert bad_step["error"]["data"]["errors"][0]["param"] == "step"
    assert s.call("rules.explain", {"rule_id": "dictionary.5"})["result"]["known"] is True
    assert s.call("rules.explain", {"rule_id": "crs.unknown"})["result"]["known"] is False
    assert _slug(s.call("rules.explain", {"rule_id": "no dots"})) == "invalid-params"
    assert _slug(s.call("ping", {"x": 1})) == "invalid-params"
    assert _slug(s.call("rules.list", {"step": "schema", "x": 1})) == "invalid-params"


def test_requests_before_initialize(session):
    s = session()
    for method in ("ping", "validate", "versions", "rules.list", "shutdown"):
        assert _slug(s.call(method, {})) == "not-initialized"
    assert _slug(s.call("nope")) == "method-not-found"
    s.send("$/cancelRequest", {"id": 1})  # notifications before initialize: ignored
    s.send("$/whatever", {})
    s.send("custom/note")
    s.initialize()
    assert s.call("ping")["result"] == {}


def test_initialize_errors_and_retry(session):
    s = session()
    reply = s.call("initialize", {"protocol_version": "2.0"})
    assert reply["error"]["code"] == -32009
    assert reply["error"]["data"] == {"code": "unsupported-protocol-version", "supported": ["1.0"]}
    assert _slug(s.call("initialize", {})) == "invalid-params"
    assert s.initialize()["protocol_version"] == "1.0"  # a failed initialize can be retried
    assert _slug(s.call("initialize", {"protocol_version": "1.0"})) == "invalid-request"


def test_shutdown_then_exit_is_0(session):
    s = session()
    s.initialize()
    assert s.call("shutdown", id=9) == {"jsonrpc": "2.0", "id": 9, "result": None}
    assert _slug(s.call("ping")) == "shutting-down"
    assert _slug(s.call("initialize", {"protocol_version": "1.0"})) == "shutting-down"
    assert _slug(s.call("shutdown")) == "shutting-down"
    s.send("exit")
    assert s.wait() == 0


@pytest.mark.parametrize("initialized", [False, True])
def test_exit_without_shutdown_is_1(session, initialized):
    s = session()
    if initialized:
        s.initialize()
    s.send("exit")
    assert s.wait() == 1


def test_eof_is_0(session):
    s = session()
    s.initialize()
    s.close_input()
    assert s.wait() == 0


def test_warm_up_failure_answers_resource_error_and_exits_3(session, monkeypatch, caplog):
    def broken(versions=None):
        raise ResourceError("cannot read /secret/site-packages/pydiggs/schemas")

    monkeypatch.setattr(_service, "warm_worker", broken)
    s = session()
    reply = s.call("initialize", {"protocol_version": "1.0"})
    assert reply["error"]["code"] == -32007
    assert reply["error"]["data"]["code"] == "resource-error"
    assert "/secret" not in json.dumps(reply)
    assert len(reply["error"]["data"]["correlation_id"]) == 16
    assert s.wait() == 3


# --- framing robustness ------------------------------------------------------------------------


def test_malformed_lines_never_stop_the_reader(session):
    s = session()
    s.initialize()
    for raw in (
        b"\xff\xfe\n",
        b"\xef\xbb\xbf{}\n",
        b"{\n",
        b"[" * 50_000 + b"\n",
        b'{"jsonrpc":"2.0","id":' + b"1" * 5000 + b',"method":"ping"}\n',
        b"[]\n",
        b'{"jsonrpc":"2.0","id":null,"method":"ping"}\n',
    ):
        s.send_raw(raw)
        reply = s.recv()
        assert reply["id"] is None and reply["error"]["code"] in (-32700, -32600)
    s.send_raw(b'{"jsonrpc":"2.0","method":"ping","params":[1]}\n')  # notification: no answer
    s.send_raw(b"\n  \r\n")  # blank lines are skipped
    s.send_raw(b'{"jsonrpc":"2.0","id":"crlf","method":"ping"}\r\n')
    assert s.recv() == {"jsonrpc": "2.0", "id": "crlf", "result": {}}


def test_over_long_lines_are_discarded_and_answered_with_null_id(session):
    s = session(options=ServeOptions(max_bytes=30))
    limit = s.server.max_line_bytes
    s.initialize()
    s.send_raw(b"x" * limit + b"\n")  # at the limit: read, then a parse error
    assert _slug(s.recv()) == "parse-error"
    s.send_raw(b"x" * (limit + 1) + b"\r\n")
    reply = s.recv()
    assert reply["id"] is None and reply["error"]["code"] == -32600
    assert reply["error"]["data"] == {"code": "invalid-request", "max_line_bytes": limit}
    s.send_raw(b'{"jsonrpc":"2.0","id":1,' + b" " * (3 * limit) + b'"method":"ping"}\n')
    assert s.recv()["id"] is None
    assert s.call("ping", id=2)["result"] == {}  # the stream is back in step


def test_a_last_line_without_newline_is_served(session):
    s = session()
    s.initialize()
    s.send_raw(b'{"jsonrpc":"2.0","id":"last","method":"ping"}')
    s.close_input()
    assert s.recv()["id"] == "last"
    assert s.wait() == 0


def test_an_over_long_last_line_without_newline(session):
    s = session(options=ServeOptions(max_bytes=30))
    s.initialize()
    s.send_raw(b"x" * (s.server.max_line_bytes + 5))
    s.close_input()
    assert s.recv()["error"]["code"] == -32600
    assert s.wait() == 0


def test_a_read_error_is_treated_as_eof(no_warm):
    class Failing(io.BytesIO):
        def readline(self, size=-1):
            raise OSError("gone")

    assert Server(ServeOptions(), (), Failing(), io.BytesIO()).run() == 0


def test_a_broken_pipe_ends_quietly_with_0(no_warm):
    class Broken(io.BytesIO):
        def write(self, data):
            raise BrokenPipeError

    lines = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n' * 3
    server = Server(ServeOptions(), (), io.BytesIO(lines), Broken())
    assert server.run() == 0


def test_an_unexpected_error_in_a_cheap_method_is_internal(session, monkeypatch, caplog):
    s = session()
    s.initialize()

    def boom():
        raise RuntimeError("/home/someone/secret")

    monkeypatch.setattr(_service, "versions", boom)
    reply = s.call("versions")
    assert reply["error"]["code"] == -32603 and _slug(reply) == "internal"
    assert "secret" not in json.dumps(reply)
    assert reply["error"]["data"]["correlation_id"] in caplog.text


def test_a_dying_thread_ends_the_session_with_1(session, monkeypatch):
    def boom(self, line):
        raise RuntimeError("bug")

    monkeypatch.setattr(Server, "_handle_line", boom)
    s = session()
    s.send("ping", id=1)
    assert s.wait() == 1


# --- validation --------------------------------------------------------------------------------


def _cli_document(path: Path, capsys) -> dict:
    assert cli_main(["validate", str(path), "--format", "json"]) in (0, 1)
    return json.loads(capsys.readouterr().out)


def test_a_real_validation_matches_the_cli_apart_from_path(session, capsys):
    s = session()
    s.initialize()
    data = SAMPLE.read_bytes()
    reply = s.call("validate", {"content_base64": _content(data), "name": "dir/BH.xml"})
    document = reply["result"]
    jsonschema.validate(document, pydiggs.findings_schema())
    assert document["files"][0]["path"] == "BH.xml"
    expected = _cli_document(SAMPLE, capsys)
    for doc in (document, expected):
        for entry in doc["files"]:
            entry.pop("path")
            entry.pop("absolute_path", None)
    assert document == expected


def test_real_validation_errors_map_to_protocol_errors(session):
    s = session(options=ServeOptions(max_bytes=64))
    s.initialize()
    reply = s.call("validate", {"content_base64": _content(b"<Diggs/>"), "checks": ["bogus"]})
    assert reply["error"]["code"] == -32602
    assert reply["error"]["data"]["errors"][0]["param"] == "checks"
    zipped = s.call("validate", {"content_base64": _content(gzip.compress(b"<a/>"))})
    assert zipped["error"]["code"] == -32004 and _slug(zipped) == "compressed-input"
    large = s.call("validate", {"content_base64": _content(b"x" * 65)})
    assert large["error"]["code"] == -32003
    budget = s.call("validate", {"content_base64": _content(b"<a/>"), "time_budget_s": 1e9})
    assert _slug(budget) == "invalid-params"


def test_progress_is_sent_only_when_negotiated(session):
    s = session()
    s.initialize(capabilities={"progress": True})
    s.send("validate", {"content_base64": _content(SAMPLE.read_bytes())}, id="p")
    notes = []
    while "id" not in (msg := s.recv()):
        notes.append(msg)
    assert msg["id"] == "p" and "result" in msg
    assert notes[0]["params"] == {"request_id": "p", "step": "validate", "status": "started"}
    assert {n["method"] for n in notes} == {"$/progress"}
    assert any(n["params"]["status"] == "running" for n in notes)


def test_service_failures_carry_a_correlation_id(session, gate):
    s = session()
    s.initialize()
    gate.release.set()
    gate.action = lambda request: Outcome(
        code=Code.RESOURCE_ERROR, detail="generic", correlation_id="abc"
    )
    reply = s.call("validate", {"content_base64": _content()})
    assert reply["error"] == {
        "code": -32007,
        "message": "generic",
        "data": {"code": "resource-error", "correlation_id": "abc"},
    }

    def crash(request):
        raise RuntimeError("bug at /secret")

    gate.action = crash
    reply = s.call("validate", {"content_base64": _content()}, id=2)
    assert _slug(reply) == "internal" and "secret" not in json.dumps(reply)


def test_requests_carry_the_launch_limits(session, gate):
    s = session(options=ServeOptions(max_bytes=100, max_findings=5, timeout=7))
    s.initialize()
    gate.release.set()
    s.call("validate", {"content_base64": _content(), "checks": ["schema"], "diggs_version": "2.6"})
    s.call("validate", {"content_base64": _content(), "time_budget_s": 2, "name": "n.xml"}, id=2)
    first, second = gate.requests
    assert (first.max_bytes, first.max_findings, first.time_budget) == (100, 5, 7)
    assert (first.checks, first.diggs_version, first.name) == (["schema"], "2.6", None)
    assert (second.time_budget, second.name) == (2, "n.xml")


# --- ordering, busy, cancel, shutdown ----------------------------------------------------------


def test_cheap_methods_overtake_a_running_validation(session, gate):
    s = session()
    s.initialize()
    s.send("validate", {"content_base64": _content(), "name": "a"}, id="a")
    gate.wait_started("a")
    assert s.call("ping", id="p")["id"] == "p"  # answered while "a" runs
    gate.release.set()
    assert s.recv() == {"jsonrpc": "2.0", "id": "a", "result": {"name": "a"}}


def test_one_running_one_queued_then_busy(session, gate):
    s = session()
    s.initialize(capabilities={"progress": True})
    s.send("validate", {"content_base64": _content(), "name": "a"}, id="a")
    gate.wait_started("a")
    s.send("validate", {"content_base64": _content(), "name": "b"}, id="b")
    s.send("validate", {"content_base64": _content(), "name": "c"}, id="c")
    s.send("validate", {"content_base64": _content()}, id="b")
    replies, notes = s.replies(2)
    busy, dup = replies
    assert busy["id"] == "c" and busy["error"]["code"] == -32000 and _slug(busy) == "busy"
    assert dup["id"] == "b" and _slug(dup) == "invalid-request"
    assert notes[0]["params"] == {"request_id": "a", "step": "validate", "status": "started"}
    assert {"request_id": "b", "step": "queue", "status": "waiting"} in [n["params"] for n in notes]
    gate.release.set()
    replies, _ = s.replies(2)
    assert [r["id"] for r in replies] == ["a", "b"]


def test_cancel_a_queued_and_the_running_validation(session, gate):
    s = session()
    s.initialize()
    s.send("validate", {"content_base64": _content(), "name": "a"}, id=1)
    gate.wait_started("a")
    s.send("validate", {"content_base64": _content(), "name": "b"}, id="1")  # a distinct id
    s.send("$/cancelRequest", {"id": "1"})
    queued = s.recv()
    assert queued["id"] == "1" and queued["error"]["code"] == -32008
    s.send("$/cancelRequest", {"id": 99})  # unknown: ignored
    s.send("$/cancelRequest", {"id": [1]})  # unusable: ignored
    s.send("$/cancelRequest", {"id": 1})
    running = s.recv()
    assert running["id"] == 1 and _slug(running) == "cancelled"
    s.send("$/cancelRequest", {"id": 1})  # finished: ignored
    assert s.call("ping", id="after")["result"] == {}
    assert [r.name for r in gate.requests] == ["a"]


def test_shutdown_lets_the_running_validation_finish(session, gate):
    s = session()
    s.initialize()
    s.send("validate", {"content_base64": _content(), "name": "a"}, id="a")
    gate.wait_started("a")
    s.send("validate", {"content_base64": _content(), "name": "b"}, id="b")
    s.send("shutdown", id="s")
    dropped = s.recv()
    assert dropped["id"] == "b" and _slug(dropped) == "shutting-down"
    assert dropped["error"]["code"] == -32002
    assert _slug(s.call("validate", {"content_base64": _content()}, id="late")) == "shutting-down"
    gate.release.set()
    assert s.recv()["id"] == "a"
    assert s.recv() == {"jsonrpc": "2.0", "id": "s", "result": None}
    s.send("exit")
    assert s.wait() == 0


def test_a_validation_not_yet_started_survives_shutdown(no_warm, gate):
    gate.release.set()
    server = Server(ServeOptions(), (), io.BytesIO(), io.BytesIO())
    server._phase = "ready"
    server._submit_validate("a", {"content_base64": _content()})  # nothing running yet
    server._submit_validate("b", {"content_base64": _content()})
    server._shutdown("s", {})
    assert [j.id for j in server._queue] == ["a", "s"]


def test_eof_abandons_a_running_validation(session, gate):
    s = session()
    s.initialize()
    s.send("validate", {"content_base64": _content(), "name": "a"}, id="a")
    gate.wait_started("a")
    s.close_input()
    assert s.rest() == []  # nothing more is written
    assert s.code == 0


def test_exit_abandons_a_running_validation(session, gate):
    s = session()
    s.initialize()
    s.send("validate", {"content_base64": _content(), "name": "a"}, id="a")
    gate.wait_started("a")
    s.send("exit")
    assert s.rest() == []
    assert s.code == 1


# --- path mode ---------------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    base = Path(os.path.realpath(tmp_path))
    (base / "root").mkdir()
    (base / "outside").mkdir()
    (base / "root" / "BH-01.xml").write_bytes(SAMPLE.read_bytes())
    (base / "outside" / "secret.xml").write_bytes(b"<Secret/>")
    return base / "root"


def test_path_mode_reads_inside_the_roots_only(session, root, gate):
    gate.release.set()
    gate.action = lambda request: Outcome(
        document=json.dumps({"name": request.name, "size": len(request.content)}).encode()
    )
    s = session(roots=(root,))
    assert s.initialize()["capabilities"]["validate"]["path"] is True
    ok = s.call("validate", {"path": str(root / "BH-01.xml")})
    assert ok["result"] == {"name": "BH-01.xml", "size": SAMPLE.stat().st_size}
    renamed = s.call("validate", {"path": str(root / "BH-01.xml"), "name": "x.xml"}, id=2)
    assert renamed["result"]["name"] == "x.xml"
    assert gate.requests[0].max_bytes == ServeOptions().max_path_bytes
    replies = [
        s.call("validate", {"path": p}, id=i)
        for i, p in enumerate(
            [
                str(root.parent / "outside" / "secret.xml"),
                str(root / "missing.xml"),
                "BH-01.xml",
                "file://" + str(root / "BH-01.xml"),
                str(root / ".." / "outside" / "secret.xml"),
            ]
        )
    ]
    assert {json.dumps(r["error"]) for r in replies} == {
        json.dumps(
            {
                "code": -32005,
                "message": "The path is not permitted.",
                "data": {"code": "path-not-permitted"},
            }
        )
    }


def test_path_mode_is_off_without_roots(session, root):
    s = session()
    assert s.initialize()["capabilities"]["validate"]["path"] is False
    assert _slug(s.call("validate", {"path": str(root / "BH-01.xml")})) == "path-not-permitted"


def test_path_mode_read_limits(session, root, monkeypatch):
    (root / "z.xml").write_bytes(gzip.compress(b"<a/>"))
    s = session(options=ServeOptions(max_bytes=10, max_path_bytes=1000), roots=(root,))
    s.initialize()
    large = s.call("validate", {"path": str(root / "BH-01.xml")})
    assert _slug(large) == "input-too-large" and "BH-01.xml" in large["error"]["message"]
    assert str(root) not in json.dumps(large)
    assert _slug(s.call("validate", {"path": str(root / "z.xml")})) == "compressed-input"
    monkeypatch.setattr(
        _service,
        "read_fd",
        lambda fd, max_bytes, name: Outcome(code=Code.INPUT_UNREADABLE, detail=f"{name} no"),
    )
    unreadable = s.call("validate", {"path": str(root / "z.xml")})
    assert unreadable["error"]["code"] == -32006 and unreadable["error"]["message"] == "z.xml no"


def test_path_mode_end_to_end(session, root, capsys):
    s = session(roots=(root,))
    s.initialize()
    document = s.call("validate", {"path": str(root / "BH-01.xml")})["result"]
    assert document["files"][0]["path"] == "BH-01.xml"
    assert "absolute_path" not in document["files"][0] or not document["files"][0]["absolute_path"]
    assert str(root) not in json.dumps(document)


# --- process setup (serve) ---------------------------------------------------------------------


class _Fds:
    """Point fd 0 and fd 1 at pipes for one ``serve()`` call, then restore them.

    A pump thread collects the frames and closes stdin once it has seen the reply
    with ``eof_after`` as its id (None: close only at :meth:`restore`).
    """

    def __init__(self, data: bytes, eof_after: object = None) -> None:
        self.saved = (os.dup(0), os.dup(1))
        r_in, self.w_in = os.pipe()
        r_out, w_out = os.pipe()
        os.write(self.w_in, data)
        os.dup2(r_in, 0)
        os.dup2(w_out, 1)
        os.close(r_in)
        os.close(w_out)
        self.frames: list[bytes] = []
        self._eof_after = eof_after
        self._pump = threading.Thread(target=self._read, args=(r_out,), daemon=True)
        self._pump.start()

    def _read(self, fd: int) -> None:
        with os.fdopen(fd, "rb") as out:
            for line in out:
                self.frames.append(line)
                if self._eof_after is not None and json.loads(line).get("id") == self._eof_after:
                    self.eof()

    def eof(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.w_in)

    def restore(self) -> list[bytes]:
        self.eof()
        os.dup2(self.saved[0], 0)
        os.dup2(self.saved[1], 1)  # the last writer to the frames pipe goes away
        for fd in self.saved:
            os.close(fd)
        self._pump.join(TIMEOUT)
        return self.frames


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptors")
def test_serve_keeps_stdout_for_frames_only(no_warm, gate, capfd):
    def noisy(request):
        print("python print noise")
        os.write(1, b"C-level noise\n")
        return Outcome(document=b'{"ok":true}')

    gate.release.set()
    gate.action = noisy
    session = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_version":"1.0"}}\n'
        b'{"jsonrpc":"2.0","id":2,"method":"validate","params":{"content_base64":"PGEvPg=="}}\n'
        b'{"jsonrpc":"2.0","id":3,"method":"shutdown"}\n'
    )
    fds = _Fds(session, eof_after=3)
    stdout_before = sys.stdout
    try:
        code = _loop.serve(ServeOptions())
    finally:
        frames = fds.restore()
    assert sys.stdout is stdout_before
    assert code == 0
    replies = [json.loads(line) for line in frames]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[1]["result"] == {"ok": True}
    err = capfd.readouterr().err
    assert "python print noise" in err and "C-level noise" in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_serve_ends_with_0_on_sigterm(no_warm):
    fds = _Fds(b"")
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        code = _loop.serve(ServeOptions())
    finally:
        timer.cancel()
        fds.restore()
    assert code == 0
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL or callable(
        signal.getsignal(signal.SIGTERM)
    )


def test_serve_refuses_a_bad_root_with_2(capsys):
    assert _loop.serve(ServeOptions(allow_paths=("/",))) == 2
    assert "filesystem root" in capsys.readouterr().err


def test_serve_needs_usable_stdio(monkeypatch, capsys, tmp_path):
    def no_dup(fd):
        raise OSError("bad fd")

    class NoDup:
        def __getattr__(self, name):
            return getattr(os, name)

        dup = staticmethod(no_dup)

    monkeypatch.setattr(_loop, "os", NoDup())
    assert _loop.serve(ServeOptions(allow_paths=(str(tmp_path),))) == 2
    assert "stdin and stdout" in capsys.readouterr().err


def test_claimed_stdout_gives_back_fd_1_when_dup2_fails(monkeypatch):
    class NoDup2:
        def __getattr__(self, name):
            return getattr(os, name)

        def dup2(self, a, b):
            raise OSError("no")

    monkeypatch.setattr(_loop, "os", NoDup2())  # only the sidecar sees it, not pytest
    with pytest.raises(OSError, match="no"), _loop._claimed_stdout():
        pass


def test_signals_are_left_alone_off_the_main_thread(no_warm):
    seen = []

    def run():
        with _loop._signals(Server(ServeOptions(), (), io.BytesIO(), io.BytesIO())):
            seen.append(signal.getsignal(signal.SIGTERM))

    before = signal.getsignal(signal.SIGTERM)
    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    assert seen == [before]


def test_serve_stdio_is_the_entry_point(monkeypatch):
    import pydiggs.sidecar

    monkeypatch.setattr(_loop, "serve", lambda options: 42)
    assert pydiggs.sidecar.serve_stdio(ServeOptions()) == 42
