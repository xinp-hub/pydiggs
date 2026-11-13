"""``pydiggs ags4 check``: the AGS4 rules for one or more files, as text, JSON or SARIF.

Exit codes, as for ``pydiggs validate``: 0 when no finding is at or above ``--fail-on``
(default ``error``); 1 when some are; 2 on a usage error, an unreadable input, or a
dictionary that cannot be used (2 outranks 1). A file that is not AGS4 at all is a
finding, so it gives 1.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import pydiggs
from pydiggs import ags4
from pydiggs.ags4._io import DEFAULT_MAX_BYTES
from pydiggs.errors import DocumentError, InstanceReadError, ResourceError
from pydiggs.findings import _json, _sarif, export
from pydiggs.findings._cli import _open_output, _safe
from pydiggs.findings.model import Report, Severity, Step, UnreadableFile
from pydiggs.findings.run import DEFAULT_MAX_FINDINGS, display_paths

__all__ = ["build_parser", "main"]

_PROG = "pydiggs ags4"
#: INFO findings the text report shows, because they say what was not checked.
_SHOWN_INFO = frozenset({"ags4.dictionary-not-checked", "structure.findings-truncated"})


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs ags4`` argument parser (one command so far: ``check``)."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Work with AGS4 files. See: pydiggs ags4 check --help.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    check = commands.add_parser(
        "check",
        prog=f"{_PROG} check",
        help="check AGS4 files against the AGS4 rules (Rules 1 to 20)",
        description=(
            "Check AGS4 files against the AGS4 rules (Rules 1 to 20 of the AGS4"
            " specification, Edition 4.2) and report the findings as text, JSON"
            " (diggs-findings 1.1) or SARIF 2.1.0. pyDIGGS bundles no AGS data dictionary:"
            " the rules that need one (7, 9, 10a, 10b, 10c, 18, and parts of 11c, 13, 14"
            " and 19b) run for the file's own DICT group, and for standard groups only with"
            " --dictionary. Not an AGS-approved validator."
        ),
        epilog=(
            "Exit status: 0 no finding at or above --fail-on; 1 some; 2 usage error, an"
            " unreadable input, or an unusable dictionary."
        ),
    )
    check.add_argument("paths", nargs="+", metavar="FILE", help="AGS4 files to check")
    check.add_argument(
        "--dictionary",
        metavar="FILE",
        help="an AGS4 data dictionary (such as the AGS standard dictionary of the version)",
    )
    check.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    check.add_argument("--output", metavar="FILE", help="write the report here, not stdout")
    check.add_argument("--fail-on", choices=["error", "warning"], default="error")
    check.add_argument(
        "--max-findings",
        type=int,
        default=DEFAULT_MAX_FINDINGS,
        metavar="N",
        help=f"keep at most N findings per file (default {DEFAULT_MAX_FINDINGS}; 0: no limit)",
    )
    check.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        metavar="N",
        help="refuse files larger than N bytes (default 100 MiB)",
    )
    check.add_argument(
        "--time-budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="stop checking a file after SECONDS; what was found so far is reported",
    )
    check.add_argument(
        "--absolute-paths",
        action="store_true",
        help="report absolute paths in JSON and SARIF (default: relative to here)",
    )
    return parser


def _options(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    args = parser.parse_args(list(argv))
    if args.max_findings < 0:
        parser.error("--max-findings must be 0 (no limit) or positive")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    if args.time_budget is not None and not args.time_budget > 0:  # also NaN
        parser.error("--time-budget must be a positive number of seconds")
    return args


def _not_ags4(path: str, exc: DocumentError, max_bytes: int) -> Report:
    """A report for a file that has no AGS4 table at all: its one finding is the reason.

    Raises:
        OSError: The file can no longer be opened to hash it (the caller reports it as
            unreadable).
    """
    shown, absolute = display_paths(path)
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:  # read() just read it, within max_bytes
        digest.update(stream.read(max_bytes + 1))
    return Report(
        path=shown,
        sha256=digest.hexdigest(),
        diggs_version=None,
        steps={Step.AGS4: "failed"},
        findings=() if exc.finding is None else (exc.finding,),
        tool_version=pydiggs.__version__,
        absolute_path=absolute,
        format="ags4",
    )


def _run(
    args: argparse.Namespace, dictionary: ags4.File | None
) -> tuple[list[Report], list[UnreadableFile]]:
    reports: list[Report] = []
    unreadable: list[UnreadableFile] = []
    for path in args.paths:
        try:
            reports.append(
                ags4.check(
                    path,
                    dictionary=dictionary,
                    max_bytes=args.max_bytes,
                    max_findings=args.max_findings or None,
                    time_budget=args.time_budget,
                )
            )
        except DocumentError as exc:
            try:
                reports.append(_not_ags4(path, exc, args.max_bytes))
            except OSError as gone:  # changed or removed since it was read: unreadable
                shown, absolute = display_paths(path)
                unreadable.append(
                    UnreadableFile(
                        path=shown,
                        error=gone.strerror or "not readable",
                        absolute_path=absolute,
                    )
                )
        except (InstanceReadError, ValueError) as exc:
            shown, absolute = display_paths(path)
            why = exc.strerror if isinstance(exc, InstanceReadError) else None
            unreadable.append(
                UnreadableFile(
                    path=shown,
                    error=why or "not readable as AGS4 text",
                    absolute_path=absolute,
                )
            )
    reports.sort(key=lambda r: (r.path, r.absolute_path or ""))
    unreadable.sort(key=lambda u: (u.path, u.absolute_path or ""))
    return reports, unreadable


def _text(reports: Sequence[Report], unreadable: Sequence[UnreadableFile], out: TextIO) -> None:
    for report in reports:
        counts = report.counts
        path = _safe(report.path)
        out.write(
            f"{path}: {report.verdict}: {counts['error']} error(s),"
            f" {counts['warning']} warning(s), {counts['info']} info\n"
        )
        for finding in report.findings:
            if finding.severity is Severity.INFO and finding.rule_id not in _SHOWN_INFO:
                continue
            where = f"{path}:{finding.line}" if finding.line else path
            place = f" [{_safe(finding.path)}]" if finding.path else ""
            out.write(
                f"  {where}: {finding.severity.value} {finding.rule_id}{place}:"
                f" {_safe(finding.message)}\n"
            )
    for item in unreadable:
        out.write(f"{_safe(item.path)}: unreadable: {_safe(item.error)}\n")


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
        return _json.dumps(export._document(entries)) + "\n"
    log = _sarif.sarif_log(
        reports, unreadable, tool_version=pydiggs.__version__, absolute_paths=args.absolute_paths
    )
    return _json.dumps(log) + "\n"


def _load_dictionary(args: argparse.Namespace) -> ags4.File | None:
    """The ``--dictionary`` file, read once; ResourceError when it cannot be used."""
    if args.dictionary is None:
        return None
    try:
        dictionary = ags4.read(args.dictionary, max_bytes=args.max_bytes)
    except InstanceReadError as exc:
        reason = exc.strerror or "it could not be read"
        raise ResourceError(f"the AGS4 dictionary could not be read: {reason}") from exc
    except (DocumentError, ValueError) as exc:
        raise ResourceError(f"the AGS4 dictionary is not an AGS4 file: {exc}") from exc
    from pydiggs.ags4._check import _dictionary  # noqa: PLC0415  # the same test as check()

    _dictionary(dictionary, args.max_bytes)
    return dictionary


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs ags4`` with ``argv`` (the arguments after ``ags4``)."""
    args = _options(build_parser(), argv)
    try:
        dictionary = _load_dictionary(args)
    except ResourceError as exc:
        print(f"{_PROG} check: {_safe(str(exc))}", file=sys.stderr)
        return 2
    reports, unreadable = _run(args, dictionary)
    body = _render(args, reports, unreadable)
    if args.output:
        try:
            with _open_output(args.output) as stream:
                stream.write(body)
        except OSError as exc:
            message = _safe(f"cannot write {args.output}: {exc}")
            print(f"{_PROG} check: {message}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(body)
    if unreadable:
        return 2
    threshold = Severity(args.fail_on).rank
    failed = any(f.severity.rank >= threshold for r in reports for f in r.findings)
    return 1 if failed else 0
