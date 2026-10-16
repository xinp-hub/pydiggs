"""``pydiggs validate``: findings for many files as text, JSON or SARIF.

Exit codes: 0 when no finding is at or above ``--fail-on`` (default ``error``); 1 when
some are; 2 on a usage error, when no input file was found, or when any input could
not be read (2 outranks 1). An XML syntax error is a finding, so it gives 1.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TextIO

import pydiggs
from pydiggs.detect import SCHEMA_PATHS
from pydiggs.errors import InstanceReadError, ResourceError
from pydiggs.findings import _json, _sarif
from pydiggs.findings.model import Report, Severity, UnreadableFile
from pydiggs.findings.run import (
    DEFAULT_MAX_FINDINGS,
    NATIVE_STEPS,
    display_paths,
    reference_data,
    validate,
)

__all__ = ["build_parser", "expand_inputs", "main"]


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs validate`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pydiggs validate",
        description=(
            "Validate DIGGS instance files with every native check and report the "
            "findings as text, JSON (diggs-findings 1.0) or SARIF 2.1.0."
        ),
        epilog=(
            "Exit status: 0 no finding at or above --fail-on; 1 some; 2 usage error, "
            "no input files, or an unreadable input."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="instance files, or directories searched recursively for *.xml",
    )
    parser.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    parser.add_argument("--output", metavar="FILE", help="write the report here, not stdout")
    parser.add_argument(
        "--checks",
        metavar="STEP[,STEP...]",
        help=f"steps to run (default: all of {','.join(s.value for s in NATIVE_STEPS)})",
    )
    parser.add_argument("--diggs-version", choices=sorted(SCHEMA_PATHS))
    parser.add_argument("--schema-path", metavar="P", help="XSD instead of the bundled one")
    parser.add_argument("--dictionary-path", metavar="P", help="primary property dictionary")
    parser.add_argument("--schematron-path", metavar="P", help="Schematron rules file")
    parser.add_argument("--fail-on", choices=["error", "warning"], default="error")
    parser.add_argument(
        "--max-findings",
        type=int,
        default=DEFAULT_MAX_FINDINGS,
        metavar="N",
        help=f"keep at most N findings per file (default {DEFAULT_MAX_FINDINGS}; 0: no limit)",
    )
    parser.add_argument(
        "--snippets",
        action="store_true",
        help="include each finding's element XML (off by default: it carries file data)",
    )
    parser.add_argument(
        "--absolute-paths",
        action="store_true",
        help="report absolute paths (default: relative to here, else the bare file name)",
    )
    parser.add_argument(
        "--base-dir",
        metavar="DIR",
        help="report paths relative to DIR (default: here, unless here is / or above home)",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="fetch whitelisted remote code lists that are not bundled",
    )
    return parser


def _walk(directory: Path) -> Iterator[Path]:
    for root, dirs, files in os.walk(directory, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            candidate = Path(root) / name
            if name.lower().endswith(".xml") and not candidate.is_symlink():
                yield candidate


def expand_inputs(paths: Sequence[str]) -> list[Path]:
    """Expand directories to their ``*.xml`` files (recursive, no symlinks, sorted).

    Files named explicitly are kept even if they do not exist (they become
    ``unreadable`` entries). Duplicates are dropped.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        for candidate in _walk(path) if path.is_dir() else [path]:
            key = candidate.resolve()
            if key not in seen:
                seen.add(key)
                out.append(candidate)
    return out


def _text(reports: Sequence[Report], unreadable: Sequence[UnreadableFile], out: TextIO) -> None:
    for report in reports:
        c = report.counts
        out.write(
            f"{report.path}: {c['error']} error(s), {c['warning']} warning(s), {c['info']} info\n"
        )
        for f in report.findings:
            if f.severity is Severity.INFO:
                continue
            where = f"{report.path}:{f.line}" if f.line else report.path
            if f.line and f.column:
                where += f":{f.column}"
            first = f.message.splitlines()[0] if f.message else ""
            out.write(f"  {where}: {f.severity.value} {f.rule_id}: {first}\n")
    for item in unreadable:
        out.write(f"{item.path}: unreadable: {item.error}\n")


def _open_output(name: str) -> TextIO:
    """Open ``--output`` for writing, refusing to write through a symlink."""
    if Path(name).is_symlink():
        raise OSError(f"refusing to write through a symlink: {name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(name, flags, 0o644), "w", encoding="utf-8")


def _options(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    args = parser.parse_args(list(argv))
    args.check_list = None
    if args.checks is not None:
        args.check_list = [c.strip() for c in args.checks.split(",") if c.strip()]
        allowed = {s.value for s in NATIVE_STEPS}
        if not args.check_list or any(c not in allowed for c in args.check_list):
            parser.error(f"--checks: expected some of {','.join(sorted(allowed))}")
    if args.max_findings < 0:
        parser.error("--max-findings must be 0 (no limit) or positive")
    return args


def _run(
    args: argparse.Namespace, inputs: Sequence[Path]
) -> tuple[list[Report], list[UnreadableFile]]:
    """Validate every input; ResourceError propagates (it aborts the batch)."""
    reports: list[Report] = []
    unreadable: list[UnreadableFile] = []
    for path in inputs:
        try:
            reports.append(
                validate(
                    path,
                    diggs_version=args.diggs_version,
                    checks=args.check_list,
                    schema_path=args.schema_path,
                    dictionary_path=args.dictionary_path,
                    schematron_path=args.schematron_path,
                    allow_network=args.allow_network,
                    include_snippets=args.snippets,
                    max_findings=args.max_findings or None,
                    base_dir=args.base_dir,
                )
            )
        except InstanceReadError as exc:
            shown, absolute = display_paths(path, args.base_dir)
            unreadable.append(
                UnreadableFile(path=shown, error=exc.strerror or str(exc), absolute_path=absolute)
            )
    reports.sort(key=lambda r: (r.path, r.absolute_path or ""))
    unreadable.sort(key=lambda u: (u.path, u.absolute_path or ""))
    return reports, unreadable


def _render(
    args: argparse.Namespace, reports: Sequence[Report], unreadable: Sequence[UnreadableFile]
) -> str:
    if args.format == "text":
        buffer = io.StringIO()
        _text(reports, unreadable, buffer)
        return buffer.getvalue()
    if args.format == "json":
        entries = [r.to_dict(absolute_paths=args.absolute_paths) for r in reports]
        entries += [
            _json.unreadable_entry(u, absolute_paths=args.absolute_paths) for u in unreadable
        ]
        entries.sort(key=lambda e: str(e["path"]))
        doc = _json.document(
            entries, tool_version=pydiggs.__version__, reference_data=reference_data()
        )
        return _json.dumps(doc) + "\n"
    log = _sarif.sarif_log(
        reports, unreadable, tool_version=pydiggs.__version__, absolute_paths=args.absolute_paths
    )
    return _json.dumps(log) + "\n"


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs validate`` with ``argv`` (the arguments after ``validate``)."""
    args = _options(build_parser(), argv)
    inputs = expand_inputs(args.paths)
    if not inputs:
        print("pydiggs validate: no XML files found", file=sys.stderr)
        return 2
    try:
        reports, unreadable = _run(args, inputs)
    except ResourceError as exc:
        print(f"pydiggs validate: {exc}", file=sys.stderr)
        return 2
    body = _render(args, reports, unreadable)
    if args.output:
        try:
            with _open_output(args.output) as fh:
                fh.write(body)
        except OSError as exc:
            print(f"pydiggs validate: cannot write {args.output}: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(body)
    if unreadable:
        return 2
    threshold = Severity(args.fail_on).rank
    failed = any(f.severity.rank >= threshold for r in reports for f in r.findings)
    return 1 if failed else 0
