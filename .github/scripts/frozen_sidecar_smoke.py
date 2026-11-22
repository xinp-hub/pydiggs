"""Drive a frozen (PyInstaller) pydiggs sidecar through one complete session.

Usage: python3 .github/scripts/frozen_sidecar_smoke.py EXECUTABLE FIXTURE

Starts EXECUTABLE (built from tests/frozen/sidecar_main.py, which runs
``pydiggs serve --stdio``) twice. The first session negotiates protocol 1.0 and sends
initialize -> validate (FIXTURE as base64 content) -> diff (refused: a 1.3 method) ->
shutdown -> exit; the second negotiates 1.3 and sends initialize -> diff (FIXTURE
against itself) -> shutdown -> exit. It checks that every reply is the JSON-RPC result
or error expected, that validate returns a diggs-findings 1.3 document and diff an
identical diggs-diff 1.0 document, and that each process exits 0. Stdlib only; runs
the same on Linux, macOS and Windows.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


class SmokeError(Exception):
    """A smoke check failed."""


def check(condition: bool, detail: object) -> None:
    """Fail the smoke test with ``detail`` unless ``condition`` holds."""
    if not condition:
        raise SmokeError(str(detail))


def main(argv: list[str]) -> int:
    """Run both sessions; exit non-zero on the first failed check."""
    if len(argv) != 3:  # noqa: PLR2004
        print(__doc__, file=sys.stderr)
        return 2
    executable, fixture = Path(argv[1]).resolve(), Path(argv[2])
    session(executable, fixture, "1.0")
    session(executable, fixture, "1.3")
    print("frozen sidecar smoke ok")
    return 0


def session(executable: Path, fixture: Path, version: str) -> None:
    """One session at protocol ``version``: 1.0 validates, 1.3 compares."""
    proc = subprocess.Popen(  # noqa: S603  # the executable under test
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # stderr is inherited: the sidecar's log lands in the CI log, and a full pipe
        # can never stall it.
    )
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:  # pragma: no cover - PIPE was requested
        raise SystemExit("no pipes to the sidecar")

    def send(message: dict[str, Any], expect: str = "result") -> dict[str, Any] | None:
        stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        stdin.flush()
        if "id" not in message:
            return None
        line = stdout.readline()
        check(bool(line), f"no reply to {message['method']}")
        reply: dict[str, Any] = json.loads(line)
        check(reply.get("id") == message["id"] and expect in reply, reply)
        return reply

    content = base64.b64encode(fixture.read_bytes()).decode("ascii")
    pair = {"old_base64": content, "new_base64": content, "old_name": fixture.name}

    try:
        init = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocol_version": version},
            }
        )
        assert init is not None  # noqa: S101
        result = init["result"]
        check(result.get("protocol_version") == version, result)
        print(f"initialize {version}: pydiggs {result.get('pydiggs_version')}")

        if version == "1.0":
            validate_then_refuse_diff(send, fixture, content, pair)
        else:
            compare(send, result, fixture, pair)

        shutdown = send({"jsonrpc": "2.0", "id": 3, "method": "shutdown"})
        check(shutdown is not None and shutdown["result"] is None, shutdown)
        send({"jsonrpc": "2.0", "method": "exit"})
        stdin.close()
        code = proc.wait(timeout=120)
        check(code == 0, f"exit code {code}")
        print(f"exit: {code}")
    except (SmokeError, ValueError, subprocess.TimeoutExpired) as exc:
        proc.kill()
        proc.wait()
        raise SystemExit(f"frozen sidecar smoke failed ({version}): {exc}") from None


Send = Callable[..., "dict[str, Any] | None"]


def validate_then_refuse_diff(
    send: Send, fixture: Path, content: str, pair: dict[str, str]
) -> None:
    """Protocol 1.0: validate gives a findings document; diff is a 1.3 method, refused."""
    params = {"content_base64": content, "name": fixture.name}
    validated = send({"jsonrpc": "2.0", "id": 2, "method": "validate", "params": params})
    assert validated is not None  # noqa: S101
    document = validated["result"]
    check(document.get("format") == "diggs-findings", document)
    check(document.get("schema_version") == "1.3", document)
    files = document.get("files")
    check(isinstance(files, list) and len(files) == 1, files)
    entry = files[0]
    check(entry.get("status") != "unreadable", entry)
    print(
        f"validate: format={document['format']} verdict={entry['verdict']}"
        f" findings={len(entry['findings'])}"
    )
    refused = send({"jsonrpc": "2.0", "id": 4, "method": "diff", "params": pair}, expect="error")
    assert refused is not None  # noqa: S101
    code = refused["error"].get("data", {}).get("code")
    check(code == "method-not-found", refused)
    print(f"diff on 1.0: refused ({code})")


def compare(send: Send, result: dict[str, Any], fixture: Path, pair: dict[str, str]) -> None:
    """Protocol 1.3: diff of the fixture against itself is an identical diggs-diff 1.0."""
    check(result.get("capabilities", {}).get("diff", {}).get("content") is True, result)
    compared = send({"jsonrpc": "2.0", "id": 2, "method": "diff", "params": pair})
    assert compared is not None  # noqa: S101
    document = compared["result"]
    check(document.get("format") == "diggs-diff", document)
    check(document.get("schema_version") == "1.0", document)
    check(document.get("identical") is True, document)
    check(document.get("old", {}).get("name") == fixture.name, document)
    print(f"diff: format={document['format']} identical={document['identical']}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
