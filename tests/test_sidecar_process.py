"""The sidecar as a real child process: sessions, exit codes, stdout purity, signals, paths."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import jsonschema
import pytest

import pydiggs

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "test_files" / "AtterbergExample.xml"
TIMEOUT = 60
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")

#: Run ``pydiggs serve --stdio ARGS`` after ``prelude`` (patches applied in the child).
_LAUNCH = """
import sys
{prelude}
from pydiggs.cli import main
raise SystemExit(main(["serve", "--stdio", *sys.argv[1:]]))
"""

#: Replaces validation with a loop that only ends when asked to stop.
_BLOCKING = """
import time
from pydiggs import _service
def blocking(request, *, should_stop=None):
    while not should_stop():
        time.sleep(0.01)
    return _service.Outcome(code=_service.Code.CANCELLED, detail="cancelled")
_service.validate = blocking
"""


class Child:
    """``pydiggs serve --stdio`` with pipes for stdin/stdout and stderr in a file."""

    def __init__(self, tmp_path: Path, *args: str, prelude: str | None = None) -> None:
        self.stderr_path = tmp_path / "stderr.log"
        self._stderr = self.stderr_path.open("wb")
        if prelude is None:
            argv = [sys.executable, "-m", "pydiggs", "serve", "--stdio", *args]
        else:
            code = _LAUNCH.format(prelude=textwrap.dedent(prelude))
            argv = [sys.executable, "-c", code, *args]
        self.proc = subprocess.Popen(  # noqa: S603  # fixed argv
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            cwd=tmp_path,
        )

    def send(self, method: str, params: dict | None = None, id: object = "unset") -> None:  # noqa: A002
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if id != "unset":
            message["id"] = id
        self.send_raw(json.dumps(message).encode() + b"\n")

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def recv(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line.endswith(b"\n"), (line, self.stderr())
        return json.loads(line)

    def call(self, method: str, params: dict | None = None, id: object = 1) -> dict:  # noqa: A002
        self.send(method, params, id)
        return self.recv()

    def initialize(self) -> dict:
        reply = self.call("initialize", {"protocol_version": "1.0"}, id=0)
        assert "result" in reply, (reply, self.stderr())
        return reply["result"]

    def close_stdin(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()

    def wait(self) -> int:
        code = self.proc.wait(TIMEOUT)
        self._stderr.close()
        return code

    def rest(self) -> bytes:
        assert self.proc.stdout is not None
        return self.proc.stdout.read()

    def stderr(self) -> str:
        if not self._stderr.closed:
            self._stderr.flush()
        return self.stderr_path.read_text("utf-8", "replace")

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(TIMEOUT)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None and not stream.closed:
                stream.close()
        self._stderr.close()


@pytest.fixture
def child(tmp_path):
    children: list[Child] = []

    def make(*args: str, prelude: str | None = None) -> Child:
        c = Child(tmp_path, *args, prelude=prelude)
        children.append(c)
        return c

    yield make
    for c in children:
        c.kill()


def test_a_whole_session_matches_the_cli(child, tmp_path):
    c = child("--log-level", "debug", "--log-format", "json")
    result = c.initialize()
    assert result["protocol_version"] == "1.0"
    assert result["pydiggs_version"] == pydiggs.__version__
    data = SAMPLE.read_bytes()
    reply = c.call("validate", {"content_base64": base64.b64encode(data).decode()}, id="v")
    document = reply["result"]
    jsonschema.validate(document, pydiggs.findings_schema())
    assert c.call("ping", id=2)["result"] == {}
    assert c.call("shutdown", id=3)["result"] is None
    c.send("exit")
    assert c.wait() == 0
    assert c.rest() == b""
    logs = [json.loads(line) for line in c.stderr().splitlines() if line.startswith("{")]
    assert logs and all(set(r) == {"ts", "level", "msg", "request_id"} for r in logs)
    assert str(tmp_path) not in c.stderr()

    cli = subprocess.run(  # noqa: S603  # fixed argv
        [sys.executable, "-m", "pydiggs", "validate", str(SAMPLE), "--format", "json"],
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )
    expected = json.loads(cli.stdout)
    for doc in (document, expected):
        for entry in doc["files"]:
            entry.pop("path")
            entry.pop("absolute_path", None)
    assert document == expected


def test_exit_without_shutdown_is_1(child):
    c = child()
    c.initialize()
    c.send("exit")
    assert c.wait() == 1


def test_eof_is_0_even_before_initialize(child):
    c = child()
    c.close_stdin()
    assert c.wait() == 0
    assert c.rest() == b""


def test_a_refused_root_is_2(child):
    c = child("--allow-path", "/")
    assert c.wait() == 2
    assert c.rest() == b""
    assert "filesystem root" in c.stderr()


def test_a_warm_up_failure_is_3(child):
    prelude = """
    from pydiggs import _service
    from pydiggs.errors import ResourceError
    def broken(versions=None):
        raise ResourceError("bundled data missing")
    _service.warm_worker = broken
    """
    c = child(prelude=prelude)
    reply = c.call("initialize", {"protocol_version": "1.0"})
    assert reply["error"]["data"]["code"] == "resource-error"
    assert c.wait() == 3


def test_eof_mid_validation_abandons_it_and_exits_0(child):
    c = child(prelude=_BLOCKING)
    c.initialize()
    c.send("validate", {"content_base64": "PGEvPg=="}, id="v")
    assert c.call("ping", id="p")["id"] == "p"  # the validation is running
    c.close_stdin()
    assert c.wait() == 0
    assert c.rest() == b""


def test_cancel_a_running_validation(child):
    c = child(prelude=_BLOCKING)
    c.initialize()
    c.send("validate", {"content_base64": "PGEvPg=="}, id=7)
    assert c.call("ping", id="p")["id"] == "p"
    c.send("$/cancelRequest", {"id": 7})
    assert c.recv()["error"]["data"]["code"] == "cancelled"


def test_stdout_carries_frames_only(child):
    prelude = """
    import os
    from pydiggs import _service
    real = _service.validate
    def noisy(request, *, should_stop=None):
        print("python print noise")
        os.write(1, b"C-level noise\\n")
        os.system("echo child process noise")
        return real(request, should_stop=should_stop)
    _service.validate = noisy
    """
    c = child(prelude=prelude)
    c.initialize()
    reply = c.call("validate", {"content_base64": "PERpZ2dzLz4=", "checks": ["structure"]})
    assert reply["result"]["format"] == "diggs-findings"
    assert c.call("shutdown", id=2)["result"] is None
    c.close_stdin()
    assert c.wait() == 0
    assert c.rest() == b""
    err = c.stderr()
    for noise in ("python print noise", "C-level noise", "child process noise"):
        assert noise in err


@POSIX
def test_sigterm_is_0(child):
    c = child(prelude=_BLOCKING)
    c.initialize()
    c.send("validate", {"content_base64": "PGEvPg=="}, id="v")
    assert c.call("ping", id="p")["id"] == "p"
    c.proc.send_signal(signal.SIGTERM)
    assert c.wait() == 0


def test_a_broken_pipe_is_0(child):
    c = child()
    c.initialize()
    assert c.proc.stdout is not None
    c.proc.stdout.close()  # the host stops reading
    for i in range(5):
        try:
            c.send("ping", id=i)
        except BrokenPipeError:  # pragma: no cover - the sidecar already left
            break
    assert c.wait() == 0


@POSIX
def test_path_mode_in_a_child(child, tmp_path):
    base = Path(os.path.realpath(tmp_path))
    root = base / "data"
    root.mkdir()
    (root / "BH-01.xml").write_bytes(SAMPLE.read_bytes())
    (base / "secret.xml").write_bytes(b"<Secret/>")
    (root / "escape.xml").symlink_to(base / "secret.xml")
    c = child("--allow-path", str(root))
    assert c.initialize()["capabilities"]["validate"]["path"] is True
    document = c.call("validate", {"path": str(root / "BH-01.xml")})["result"]
    assert document["files"][0]["path"] == "BH-01.xml"
    for i, path in enumerate([root / "escape.xml", base / "secret.xml", root / "missing.xml"]):
        reply = c.call("validate", {"path": str(path)}, id=i + 10)
        assert reply["error"]["code"] == -32005


def test_malformed_input_never_kills_the_child(child):
    c = child("--max-bytes", "30")
    c.initialize()
    for raw in (b"\xff\n", b"\xef\xbb\xbf{}\n", b"[" * 100_000 + b"\n", b"x" * 70_000 + b"\n"):
        c.send_raw(raw)
        reply = c.recv()
        assert reply["id"] is None and reply["error"]["code"] in (-32700, -32600)
    assert c.call("ping", id="alive")["result"] == {}
