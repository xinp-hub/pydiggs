"""Download the released pydiggs wheel from PyPI, verified, for the container build.

Usage: python3 .github/scripts/fetch_release_wheel.py VERSION DIGESTS OUTDIR

The wheel must be the one this release run built and published: its sha256 is read
from the PyPI JSON API and must equal the line for the same file in DIGESTS (the
build job's ``dist-digests.txt``). The file is then downloaded and its sha256 checked
again before it is written to OUTDIR. Nothing unverified reaches the image build.
Retries while PyPI's JSON API catches up with the upload. Stdlib only.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

PROJECT = "pydiggs"
ATTEMPTS = 10
DELAY_S = 30


def fetch(url: str) -> bytes:
    """GET ``url`` (https only) and return the body."""
    if not url.startswith("https://"):
        raise SystemExit(f"refusing a non-https URL: {url}")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310  # https checked
        body: bytes = response.read()
    return body


def release_wheel(version: str) -> dict[str, Any]:
    """The pure-Python wheel entry of ``version`` in the PyPI JSON API."""
    url = f"https://pypi.org/pypi/{PROJECT}/{version}/json"
    expected = f"{PROJECT}-{version}-py3-none-any.whl"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            data = json.loads(fetch(url))
            for entry in data.get("urls", []):
                if entry.get("packagetype") == "bdist_wheel" and entry["filename"] == expected:
                    return dict(entry)
            print(f"{expected} not listed yet (attempt {attempt})")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # HTTPError (404 until PyPI lists the release), network errors, short reads.
            print(f"{url}: {exc!r} (attempt {attempt})")
        time.sleep(DELAY_S)
    raise SystemExit(f"{expected} did not appear on PyPI")


def download(url: str) -> bytes:
    """``fetch`` with retries for transient network errors."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fetch(url)
        except (OSError, http.client.HTTPException) as exc:
            print(f"{url}: {exc!r} (attempt {attempt})")
            time.sleep(DELAY_S)
    raise SystemExit(f"could not download {url}")


def built_digest(digests: Path, filename: str) -> str:
    """The sha256 the build job recorded for ``filename``."""
    for line in digests.read_text("utf-8").splitlines():
        digest, _, name = line.strip().partition("  ")
        if Path(name).name == filename:
            return digest.lower()
    raise SystemExit(f"{filename} is not in {digests}")


def main(argv: list[str]) -> int:
    """Download, verify and write the wheel; print its path."""
    if len(argv) != 4:  # noqa: PLR2004
        print(__doc__, file=sys.stderr)
        return 2
    version, digests, outdir = argv[1], Path(argv[2]), Path(argv[3])
    entry = release_wheel(version)
    filename = entry["filename"]
    pypi_sha = entry["digests"]["sha256"].lower()
    ours = built_digest(digests, filename)
    if pypi_sha != ours:
        raise SystemExit(f"{filename}: PyPI has sha256 {pypi_sha}, this run built {ours}")
    body = download(entry["url"])
    got = hashlib.sha256(body).hexdigest()
    if got != pypi_sha:
        raise SystemExit(f"{filename}: downloaded sha256 {got}, PyPI lists {pypi_sha}")
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / filename
    target.write_bytes(body)
    print(f"{target} sha256={got} (matches PyPI and dist-digests.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
