"""Regenerate the legacy-output goldens in ``tests/legacy_goldens/legacy_cli.json``.

The goldens pin what the legacy subcommands (``schema_check``, ``schematron_check``,
``dictionary_check``, ``context_check``) print, return and write, so that new features
cannot change them by accident. They are generated from git, never from the working
tree:

* every case comes from the 1.1.1 release (``LEGACY_REF``), except
* cases on a file whose verdict the reference-data re-sync (PR #248) changed on purpose
  (``REVIEWED_248_FILES``), which come from that branch's commit (``BASE_REF``).

For every other case the two commits must agree byte for byte; if they do not, this
script stops and lists the cases, because that difference has not been reviewed.

This rewrites test expectations, so it needs ``--confirm-regenerate`` and refuses to run
in CI. Usage::

    uv run python scripts/regen_legacy_goldens.py --confirm-regenerate
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "tests" / "legacy_goldens" / "capture.py"
GOLDENS = ROOT / "tests" / "legacy_goldens" / "legacy_cli.json"

LEGACY_REF = "v1.1.1"
BASE_REF = "182c201912f51f6b720eebcab39d7b5ae19ee2b5"

#: Files whose verdict PR #248 changed (CHANGELOG [Unreleased] and
#: tests/fixtures/official/PROVENANCE.txt). Their goldens come from ``BASE_REF``.
REVIEWED_248_FILES = (
    "tests/fixtures/official/2.6/FieldSurveyExample.xml",
    "tests/fixtures/official/2.6/ODOT_Example_2_20180702-from25.xml",
    "tests/fixtures/official/3.0/Aeromag.xml",
    "tests/fixtures/official/3.0/GeophysicalFieldSurveyExamples.xml",
    "tests/fixtures/official/3.0/PorePressureDissipation_Example.xml",
    "tests/fixtures/official/known_invalid/3.0_PileDrivingExample_schema-dev.xml",
)


def capture_env(pythonpath: str | None) -> dict[str, str]:
    """Environment for a capture run: plain rich output, 200 columns."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"FORCE_COLOR", "CLICOLOR_FORCE", "PYTHONPATH"} and not k.startswith("TTY_")
    }
    env.update({"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb", "PYTHONHASHSEED": "0"})
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    return env


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout  # noqa: S603, S607


def extract_src(ref: str, dest: Path) -> Path:
    """Extract ``src/pydiggs`` at ``ref`` into ``dest`` and return the ``src`` directory."""
    data = _git("archive", "--format=tar", ref, "src/pydiggs")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "src"


def run_capture(src: Path) -> dict[str, Any]:
    """Run the capture script with ``src`` first on the path."""
    env = capture_env(str(src))
    where = subprocess.run(
        [sys.executable, "-c", "import pydiggs; print(pydiggs.__file__)"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not Path(where).resolve().is_relative_to(src.resolve()):
        raise SystemExit(f"capture would import pydiggs from {where}, not {src}")
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(CAPTURE), str(ROOT)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result: dict[str, Any] = json.loads(out)
    return result


def case_file(case_id: str) -> str:
    """Return the instance path a case runs on (its first ``*.xml`` argument)."""
    return next((part for part in case_id.split(" ") if part.endswith(".xml")), "")


def merge(legacy: dict[str, Any], base: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Pick each case's golden; return them and the unreviewed differences."""
    if legacy["cases"].keys() != base["cases"].keys():
        raise SystemExit("the two captures ran different cases")
    cases: dict[str, Any] = {}
    unreviewed: list[str] = []
    for case_id, legacy_case in legacy["cases"].items():
        base_case = base["cases"][case_id]
        if case_file(case_id) in REVIEWED_248_FILES:
            cases[case_id] = {"source": "pr-248", **base_case}
        else:
            if base_case != legacy_case:
                unreviewed.append(case_id)
            cases[case_id] = {"source": "1.1.1", **legacy_case}
    return cases, unreviewed


def main(argv: list[str] | None = None) -> int:
    """Regenerate the goldens after the checks above."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--confirm-regenerate",
        action="store_true",
        help="required: rewrite tests/legacy_goldens/legacy_cli.json",
    )
    args = parser.parse_args(argv)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        print("refusing to regenerate goldens in CI", file=sys.stderr)
        return 2
    if not args.confirm_regenerate:
        parser.error("pass --confirm-regenerate to rewrite the goldens")

    legacy_commit = _git("rev-parse", f"{LEGACY_REF}^{{commit}}").decode().strip()
    with tempfile.TemporaryDirectory(prefix="pydiggs-regen-") as tmp:
        legacy = run_capture(extract_src(legacy_commit, Path(tmp) / "legacy"))
        base = run_capture(extract_src(BASE_REF, Path(tmp) / "base"))
    cases, unreviewed = merge(legacy, base)
    if unreviewed:
        print("these cases changed between 1.1.1 and PR #248 without review:", file=sys.stderr)
        for case_id in unreviewed:
            print(f"  {case_id}", file=sys.stderr)
        return 1
    document = {
        "comment": "Generated by scripts/regen_legacy_goldens.py; do not edit by hand.",
        "legacy": {"ref": LEGACY_REF, "commit": legacy_commit},
        "base": {"ref": "PR #248", "commit": BASE_REF},
        "reviewed_248_files": list(REVIEWED_248_FILES),
        "env": {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"},
        "cases": cases,
    }
    GOLDENS.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {GOLDENS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
