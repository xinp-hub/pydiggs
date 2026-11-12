"""Smoke-test the pydiggs container image with the documented hardened run line.

Usage: python3 .github/scripts/container_smoke.py IMAGE [FIXTURE]

Runs IMAGE read-only, with a noexec tmpfs, no capabilities, no-new-privileges and a
memory cap (docs/container.md), then checks that it

- runs as uid 10001,
- runs its venv (made in the builder stage) on the same Python patch release as the
  runtime base, which the venv links to,
- answers ``GET /livez`` and ``GET /v1/versions``,
- turns one ``POST /v1/validate`` of FIXTURE into a diggs-findings 1.0 document,
- reports healthy through its own HEALTHCHECK,
- refuses a Host header that is not in ``PYDIGGS_ALLOWED_HOSTS``.

Stdlib only, so CI and a developer machine run the same check.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "pydiggs.test"
PORT = 18000
NAME = "pydiggs-smoke"
DEFAULT_FIXTURE = Path("tests/test_files/2.6/Schema_Error_1-2_6.xml")
#: One worker: memory = workers x 2 GiB + 512 MiB; tmpfs >= queue depth x max body.
RUN_LINE = [
    "--read-only",
    "--tmpfs",
    "/tmp:rw,size=64m,noexec,nosuid,nodev",  # noqa: S108  # a tmpfs mount spec
    "--cap-drop=ALL",
    "--security-opt",
    "no-new-privileges",
    "--memory",
    "2560m",
    "--pids-limit",
    "256",
    "-e",
    f"PYDIGGS_ALLOWED_HOSTS={HOST}",
    "-e",
    "PYDIGGS_WORKERS=1",
]


def docker(*args: str, check: bool = True) -> str:
    """Run ``docker args`` and return its stdout."""
    result = subprocess.run(  # noqa: S603  # fixed argv
        ["docker", *args],  # noqa: S607  # docker from PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


class SmokeError(Exception):
    """A smoke check failed."""


def check(condition: bool, detail: object) -> None:
    """Fail the smoke test with ``detail`` unless ``condition`` holds."""
    if not condition:
        raise SmokeError(str(detail))


def request(
    path: str, *, host: str = HOST, body: bytes | None = None, ctype: str | None = None
) -> tuple[int, dict[str, object]]:
    """Send one request to the container; return the status and the JSON body."""
    headers = {"Host": host}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(  # fixed http://127.0.0.1 URL
        f"http://127.0.0.1:{PORT}{path}", data=body, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def wait_until_serving(deadline_s: float = 90) -> None:
    """Poll ``/livez`` until it answers 200 or the deadline passes."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            if request("/livez")[0] == 200:  # noqa: PLR2004
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise SystemExit("the server did not answer /livez in time")


def wait_until_healthy(deadline_s: float = 120) -> str:
    """Poll the container's HEALTHCHECK status until it is healthy."""
    end = time.monotonic() + deadline_s
    status = ""
    while time.monotonic() < end:
        status = docker("inspect", "--format", "{{.State.Health.Status}}", NAME)
        if status == "healthy":
            return status
        time.sleep(2)
    raise SystemExit(f"HEALTHCHECK status is {status!r}, not healthy")


def main(argv: list[str]) -> int:
    """Run the smoke test; exit non-zero on the first failed check."""
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    image = argv[1]
    fixture = Path(argv[2]) if len(argv) == 3 else DEFAULT_FIXTURE  # noqa: PLR2004
    docker("rm", "-f", NAME, check=False)
    docker(
        "run",
        "--detach",
        "--name",
        NAME,
        *RUN_LINE,
        "--health-interval",
        "2s",
        "--health-start-period",
        "60s",
        "-p",
        f"127.0.0.1:{PORT}:8000",
        image,
    )
    try:
        wait_until_serving()

        uid = docker("exec", NAME, "id", "-u")
        check(uid == "10001", f"runs as uid {uid}, not 10001")
        print(f"uid: {uid}")

        cfg = docker("exec", NAME, "cat", "/opt/venv/pyvenv.cfg")
        built = {
            key.strip(): value.strip()
            for key, _, value in (line.partition("=") for line in cfg.splitlines())
        }.get("version_info", "")
        runtime = docker(
            "exec",
            NAME,
            "/usr/local/bin/python3",
            "-c",
            "import platform; print(platform.python_version())",
        )
        check(
            built == runtime,
            f"venv built with Python {built!r}, runtime base has {runtime!r}:"
            " move the Dockerfile's builder and runtime bases together",
        )
        print(f"Python: {runtime} in both stages")

        status, live = request("/livez")
        check(status == 200, (status, live))  # noqa: PLR2004
        print(f"GET /livez: {status} {live}")

        status, versions = request("/v1/versions")
        check(status == 200 and versions.get("api_version") == "1", (status, versions))  # noqa: PLR2004
        print(f"GET /v1/versions: {status} {json.dumps(versions)}")

        status, document = request(
            f"/v1/validate?name={fixture.name}",
            body=fixture.read_bytes(),
            ctype="application/xml",
        )
        check(status == 200, (status, document))  # noqa: PLR2004
        check(document.get("format") == "diggs-findings", document)
        check(document.get("schema_version") == "1.1", document)
        files = document.get("files")
        check(isinstance(files, list) and len(files) == 1, files)
        entry = files[0]
        print(
            f"POST /v1/validate: {status} format={document['format']}"
            f" verdict={entry['verdict']} findings={len(entry['findings'])}"
        )

        status, problem = request("/livez", host="attacker.example")
        check(status == 400, (status, problem))  # noqa: PLR2004
        print(f"GET /livez with a foreign Host: {status} {problem.get('type')}")

        print(f"HEALTHCHECK: {wait_until_healthy()}")
    except SmokeError as exc:
        print(docker("logs", NAME, check=False), file=sys.stderr)
        raise SystemExit(f"container smoke failed: {exc}") from None
    finally:
        print(docker("logs", NAME, check=False))
        docker("rm", "-f", NAME, check=False)
    print("container smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
