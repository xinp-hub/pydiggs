"""Drive a frozen (PyInstaller) pydiggs sidecar through one complete session.

Usage: python3 .github/scripts/frozen_sidecar_smoke.py EXECUTABLE FIXTURE

Starts EXECUTABLE (built from tests/frozen/sidecar_main.py, which runs
``pydiggs serve --stdio``), then sends initialize -> validate (FIXTURE as base64
content) -> shutdown -> exit, and checks that every reply is a JSON-RPC result, that
validate returns a diggs-findings 1.0 document, and that the process exits 0.
Stdlib only; runs the same on Linux, macOS and Windows.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class SmokeError(Exception):
    """A smoke check failed."""


def check(condition: bool, detail: object) -> None:
    """Fail the smoke test with ``detail`` unless ``condition`` holds."""
    if not condition:
        raise SmokeError(str(detail))


def main(argv: list[str]) -> int:
    """Run the session; exit non-zero on the first failed check."""
    if len(argv) != 3:  # noqa: PLR2004
        print(__doc__, file=sys.stderr)
        return 2
    executable, fixture = Path(argv[1]).resolve(), Path(argv[2])
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

    def send(message: dict[str, Any]) -> dict[str, Any] | None:
        stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        stdin.flush()
        if "id" not in message:
            return None
        line = stdout.readline()
        check(bool(line), f"no reply to {message['method']}")
        reply: dict[str, Any] = json.loads(line)
        check(reply.get("id") == message["id"] and "result" in reply, reply)
        return reply

    try:
        init = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocol_version": "1.0"},
            }
        )
        assert init is not None  # noqa: S101
        result = init["result"]
        check(result.get("protocol_version") == "1.0", result)
        print(f"initialize: pydiggs {result.get('pydiggs_version')}")

        content = base64.b64encode(fixture.read_bytes()).decode("ascii")
        params = {"content_base64": content, "name": fixture.name}
        validated = send({"jsonrpc": "2.0", "id": 2, "method": "validate", "params": params})
        assert validated is not None  # noqa: S101
        document = validated["result"]
        check(document.get("format") == "diggs-findings", document)
        check(document.get("schema_version") == "1.1", document)
        files = document.get("files")
        check(isinstance(files, list) and len(files) == 1, files)
        entry = files[0]
        check(entry.get("status") != "unreadable", entry)
        print(
            f"validate: format={document['format']} verdict={entry['verdict']}"
            f" findings={len(entry['findings'])}"
        )

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
        raise SystemExit(f"frozen sidecar smoke failed: {exc}") from None
    print("frozen sidecar smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
