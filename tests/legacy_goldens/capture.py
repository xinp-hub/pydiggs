"""Capture what the legacy ``pydiggs`` subcommands print, return and write.

Run as a script, in a fresh interpreter, with whichever ``pydiggs`` is first on
``sys.path``: the working tree (``tests/test_legacy_goldens.py``) or an older release
extracted from git (``scripts/regen_legacy_goldens.py``). It copies the corpus into a
scratch directory so that every path the legacy code prints is relative and identical
across machines, runs each case in-process through ``pydiggs.cli.main`` and prints one
JSON document on stdout.

The caller sets ``COLUMNS=200 NO_COLOR=1 TERM=dumb`` (and clears ``FORCE_COLOR``) so
rich renders the same plain text everywhere.

Usage: ``python capture.py REPO_ROOT``
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

#: Directories (relative to the repository root) whose ``*.xml`` files form the corpus.
CORPUS_DIRS = ("tests/fixtures/official", "tests/test_files")
#: Extra files the legacy cases name (user Schematron rules).
EXTRA_DIRS = ("tests/test_schematron_schema",)
COMMANDS = ("schema_check", "schematron_check", "dictionary_check", "context_check")
LOG_FLAGS = ("--output_log", "--no-output_log")


def cases(corpus: Path) -> list[tuple[str, list[str]]]:
    """Return ``(case id, argv)`` for every legacy case, in a fixed order."""
    files = sorted(
        p.relative_to(corpus).as_posix() for d in CORPUS_DIRS for p in (corpus / d).rglob("*.xml")
    )
    out: list[tuple[str, list[str]]] = [
        (f"{command} {flag} {rel}", [command, rel, flag])
        for rel in [*files, "tests/test_files/does_not_exist.xml"]
        for command in COMMANDS
        for flag in LOG_FLAGS
    ]
    for sch in ("test_schematron_1.sch", "test_schematron_2.sch"):
        for rel in (
            "tests/test_files/Schematron_Error_1.xml",
            "tests/test_files/AtterbergExample.xml",
        ):
            for flag in LOG_FLAGS:
                argv = [
                    "schematron_check",
                    rel,
                    "--schematron_path",
                    f"tests/test_schematron_schema/{sch}",
                    flag,
                ]
                out.append((" ".join(argv), argv))
    explicit = [
        ["schema_check", "tests/test_files/2.6/No_Error_2_6.xml", "--schema_path", "nope.xsd"],
        ["schema_check", "tests/test_files/2.6/No_Error_2_6.xml", "--schema_path",
         "tests/test_schematron_schema/test_schematron_1.sch"],
        ["dictionary_check", "tests/test_files/AtterbergExample.xml", "--dictionary_path",
         "nope.xml"],
        ["schematron_check", "tests/test_files/AtterbergExample.xml", "--schematron_path",
         "tests/test_files/AtterbergExample.xml"],
        [],
        ["validate_everything", "x.xml"],
        ["schema_check"],
    ]  # fmt: skip
    for argv in explicit:
        for flag in LOG_FLAGS:
            full = [*argv, flag] if argv else [flag]
            out.append((" ".join(full), full))
    return out


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_case(main: Callable[[list[str]], int], argv: list[str], workdir: Path) -> dict[str, object]:
    """Run one case in ``workdir`` and return what it produced."""
    for log in workdir.glob("*.log"):
        log.unlink()
    stdout, stderr = io.StringIO(), io.StringIO()
    exit_code: object
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exit_code = main(argv)
        except SystemExit as exc:
            exit_code = f"SystemExit({exc.code})"
    logs = {}
    for log in sorted(workdir.glob("*.log")):
        data = log.read_bytes()
        logs[log.name] = {"sha256": _digest(data), "size": len(data)}
    return {
        "exit": exit_code,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "logs": logs,
    }


def main(repo_root: str) -> int:
    """Copy the corpus, run every case and print the results as JSON."""
    import pydiggs
    from pydiggs.cli import main as cli_main

    root = Path(repo_root)
    sys.argv[0] = "pydiggs"  # argparse names the program after argv[0], as the console script does
    with tempfile.TemporaryDirectory(prefix="pydiggs-goldens-") as tmp:
        workdir = Path(tmp) / "w"
        for d in (*CORPUS_DIRS, *EXTRA_DIRS):
            shutil.copytree(root / d, workdir / d)
        previous = Path.cwd()
        os.chdir(workdir)
        try:
            results = {
                case_id: run_case(cli_main, argv, workdir) for case_id, argv in cases(workdir)
            }
        finally:
            os.chdir(previous)
    json.dump(
        {"pydiggs": pydiggs.__version__, "cases": results},
        sys.stdout,
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
