"""``pydiggs conformance``: test how software reads and writes DIGGS, on your own machine.

Preview in pyDIGGS 1.15. Five commands::

    pydiggs conformance classes
    pydiggs conformance new spt-export --product "MyLog" --product-version 4.2.1 -o challenge
    pydiggs conformance grade challenge my-export.xml
    pydiggs conformance verify challenge/evidence.json challenge my-export.xml
    pydiggs conformance scorecard */evidence.json

and two in preview since pyDIGGS 1.18::

    pydiggs conformance accept delivered.xml --class spt
    pydiggs conformance matrix evidence-folder

Exit codes follow ``pydiggs validate`` and ``pydiggs diff``: 0 the submission passed (or
the evidence was reproduced, or the listing was written); 1 it failed (or was not
reproduced); 2 a usage error, an input that cannot be read, or an output that cannot be
written.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydiggs import _stdout, conformance
from pydiggs.errors import PydiggsError
from pydiggs.findings._cli import _open_output, _safe
from pydiggs.findings.run import DEFAULT_MAX_BYTES

__all__ = ["build_parser", "main"]

_PROG: Final = "pydiggs conformance"
_EXIT: Final = (
    "Exit status: 0 passed (or reproduced); 1 failed (or not reproduced); 2 usage error,"
    " an input that cannot be read, or an output that cannot be written."
)


_MAX_EVIDENCE_BYTES: Final = 8 * 1024 * 1024


class _UsageError(Exception):
    """An exit-2 problem with a message for stderr."""


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs conformance`` argument parser."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Test how software reads and writes DIGGS 3 (preview). Make a challenge, run"
            " your software on it, and grade the result here: nothing is uploaded, and"
            " anyone can re-run the result. A result is self-tested: it shows that a file"
            " passed, not which software wrote it, and it is not a certification by the"
            " DIGGS committee or the ASCE Geo-Institute."
        ),
        epilog=_EXIT,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    listing = commands.add_parser(
        "classes",
        help="list every part of DIGGS and whether the suite can test it",
        description=(
            "List the conformance classes, and every part of DIGGS 3.0.0 the suite cannot test yet."
        ),
    )
    listing.add_argument("--format", choices=["text", "json"], default="text")

    new = commands.add_parser(
        "new",
        help="make a challenge for one class",
        description=(
            "Make a challenge folder. An export class gives CSV tables to load into your"
            " software; an import class gives a DIGGS file to open and questions to answer."
        ),
    )
    new.add_argument("class_id", metavar="CLASS", help="a class, such as spt-export")
    new.add_argument("--product", required=True, help="the name of the software under test")
    new.add_argument(
        "--product-version", required=True, metavar="VERSION", help="its exact version"
    )
    new.add_argument(
        "-o", "--output", metavar="DIR", default=None, help="the folder to make (default: CLASS)"
    )
    new.add_argument(
        "--seed",
        default=None,
        help="rebuild the challenge of this seed (default: a fresh one each time)",
    )

    grade = commands.add_parser(
        "grade",
        help="grade what your software produced",
        description=(
            "Grade a submission: the DIGGS file your software exported, or the filled"
            " answers.csv of an import class. Writes evidence.json and report.html into"
            " the challenge folder unless --no-save is given."
        ),
        epilog=_EXIT,
    )
    grade.add_argument("challenge", metavar="CHALLENGE", help="the challenge folder")
    grade.add_argument("submission", metavar="SUBMISSION", help="the DIGGS file, or answers.csv")
    grade.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="what is printed: text (the default), or the evidence as JSON",
    )
    grade.add_argument(
        "--no-save", action="store_true", help="do not write evidence.json and report.html"
    )
    _limits(grade)

    verify = commands.add_parser(
        "verify",
        help="re-run a result and confirm its evidence",
        description=(
            "Grade the submission again and compare with the evidence. Anyone who has the"
            " three files can do this."
        ),
        epilog=_EXIT,
    )
    verify.add_argument("evidence", metavar="EVIDENCE", help="the evidence.json to check")
    verify.add_argument("challenge", metavar="CHALLENGE", help="the challenge folder")
    verify.add_argument("submission", metavar="SUBMISSION", help="the graded file")
    _limits(verify)

    card = commands.add_parser(
        "scorecard",
        help="show a product against every part of DIGGS",
        description=(
            "Build a scorecard from evidence files: what passed, what failed, what was not"
            " run, and what the suite cannot test yet."
        ),
    )
    card.add_argument("evidence", metavar="EVIDENCE", nargs="+", help="evidence.json files")
    card.add_argument("--format", choices=["text", "json", "markdown"], default="text")

    accept = commands.add_parser(
        "accept",
        help="check a delivered DIGGS file against what its family must hold",
        description=(
            "Check one delivered DIGGS file: it is valid DIGGS 3, and it holds what the"
            " published requirements of the family ask (each from DIGGS 3.0.0 or its"
            " dictionaries). The report names the file by its SHA-256. It does not compare"
            " values with the field or laboratory records."
        ),
        epilog=(
            "Exit status: 0 every requirement met; 1 one or more not met (a file that is"
            " not DIGGS included); 2 usage error, a file that cannot be read, or an output"
            " that cannot be written."
        ),
    )
    accept.add_argument("file", metavar="FILE", help="the delivered DIGGS file")
    accept.add_argument(
        "--class",
        dest="family",
        required=True,
        metavar="FAMILY",
        choices=[f.id for f in conformance.acceptance_families()],
        help="what the file is delivered as: "
        + ", ".join(f.id for f in conformance.acceptance_families()),
    )
    accept.add_argument(
        "--format",
        choices=["text", "json", "html"],
        default="text",
        help="what is printed: text (the default), the result as JSON, or the HTML report",
    )
    accept.add_argument(
        "--report", metavar="PATH", default=None, help="also write the HTML report to PATH"
    )
    _limits(accept)

    grid = commands.add_parser(
        "matrix",
        help="lay out verified evidence of several products, class by class",
        description=(
            "Build the round-trip matrix from every evidence.json under DIR. Each must sit in"
            " its challenge folder with the file it graded beside it; it is verified first,"
            " and evidence that does not verify is listed and not counted."
        ),
        epilog=(
            "Exit status: 0 every evidence document verified; 1 one or more did not (the"
            " matrix is still written, without them); 2 usage error, no evidence found, or"
            " an output that cannot be written."
        ),
    )
    grid.add_argument("folder", metavar="DIR", help="a folder of challenge folders")
    grid.add_argument("--format", choices=["text", "json", "markdown", "html"], default="text")
    _limits(grid)
    return parser


def _limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        metavar="N",
        help="refuse a submission larger than N bytes (default 100 MiB)",
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="stop reading or validating an export after SECONDS",
    )


def _classes(args: argparse.Namespace) -> tuple[str, bool, int]:
    families = conformance.families()
    if args.format == "json":
        body = {
            "suite_version": conformance.SUITE_VERSION,
            "classes": [c.to_dict() for c in conformance.classes()],
            "families": [
                {
                    "id": f.id,
                    "group": f.group,
                    "title": f.title,
                    "testable": f.testable,
                    "embedded": f.embedded,
                    "elements": list(f.elements),
                }
                for f in families
            ],
        }
        return json.dumps(body, indent=2) + "\n", True, 0
    lines = [f"pyDIGGS conformance suite {conformance.SUITE_VERSION} (preview)", "", "Classes:"]
    lines += [f"  {c.id:<12} {c.title}" for c in conformance.classes()]
    lines += ["", "Parts of DIGGS 3.0.0 the suite cannot test yet:"]
    lines += [
        f"  {f.title} ({len(f.elements)} element(s))"
        for f in families
        if not f.testable and not f.embedded
    ]
    return "\n".join(lines) + "\n", False, 0


def _new(args: argparse.Namespace) -> tuple[str, bool, int]:
    try:
        challenge = conformance.new(
            args.class_id, product=args.product, version=args.product_version, seed=args.seed
        )
        folder = challenge.write(args.output or args.class_id)
    except (ValueError, FileExistsError) as exc:
        raise _UsageError(str(exc)) from None
    except OSError as exc:
        raise _UsageError(f"cannot write the challenge: {exc.strerror or exc}") from None
    submission = "<your DIGGS file>" if challenge.info.direction == "export" else "answers.csv"
    lines = [
        f"Challenge {challenge.info.id} written to {_safe(str(folder))}"
        f" (seed {challenge.seed}, token {challenge.token}).",
        f"Read {_safe(str(folder / 'README.md'))}, then:",
        f"  {_PROG} grade {_safe(str(folder))} {submission}",
    ]
    return "\n".join(lines) + "\n", False, 0


def _folder(challenge: str) -> Path:
    path = Path(challenge)
    return path if path.is_dir() else path.parent


def _grade(args: argparse.Namespace) -> tuple[str, bool, int]:
    result = conformance.grade(
        args.challenge, args.submission, max_bytes=args.max_bytes, time_budget=args.time_budget
    )
    saved = ""
    if not args.no_save:
        folder = _folder(args.challenge)
        try:
            for name, body in (
                ("evidence.json", result.to_json() + "\n"),
                ("report.html", result.to_html()),
            ):
                with _open_output(str(folder / name)) as stream:
                    stream.write(body)
        except (OSError, ValueError) as exc:  # ValueError: a name that cannot be encoded
            why = getattr(exc, "strerror", None) or exc
            raise _UsageError(f"cannot write the evidence: {why}") from None
        saved = (
            f"\n  Evidence: {_safe(str(folder / 'evidence.json'))}\n"
            f"  Report:   {_safe(str(folder / 'report.html'))}\n"
        )
    code = 0 if result.passed else 1
    if args.format == "json":
        return result.to_json() + "\n", True, code
    return result.to_text() + saved, False, code


def _verify(args: argparse.Namespace) -> tuple[str, bool, int]:
    outcome = conformance.verify(
        args.evidence,
        args.challenge,
        args.submission,
        max_bytes=args.max_bytes,
        time_budget=args.time_budget,
    )
    verdict = "pass" if outcome.result.passed else "fail"
    notes = "".join(f"Note: {note}.\n" for note in outcome.notes)
    if outcome.ok:
        return f"Reproduced: {outcome.result.info.id} {verdict}.\n{notes}", False, 0
    parts = ", ".join(outcome.differences)
    body = f"Not reproduced: grading again gives {verdict}, and these parts differ: {parts}.\n"
    return body, False, 1


def _scorecard(args: argparse.Namespace) -> tuple[str, bool, int]:
    documents = []
    for name in args.evidence:
        try:
            path = Path(name)
            if path.stat().st_size > _MAX_EVIDENCE_BYTES:
                raise _UsageError(f"{name} is too large to be conformance evidence")
            documents.append(json.loads(path.read_bytes()))
        except OSError as exc:
            raise _UsageError(f"cannot read {name}: {exc.strerror or exc}") from None
        except (ValueError, RecursionError):
            raise _UsageError(f"{name} is not JSON") from None
    try:
        cards = conformance.scorecard(documents)
    except ValueError as exc:
        raise _UsageError(f"not conformance evidence: {exc}") from None
    if args.format == "json":
        return json.dumps([c.to_dict() for c in cards], indent=2) + "\n", True, 0
    if args.format == "markdown":
        return "\n".join(_safe_lines(c.to_markdown()) for c in cards), False, 0
    return "\n".join(_safe_lines(c.to_text()) for c in cards), False, 0


def _accept(args: argparse.Namespace) -> tuple[str, bool, int]:
    result = conformance.accept(
        args.file, args.family, max_bytes=args.max_bytes, time_budget=args.time_budget
    )
    if args.report:
        try:
            with _open_output(args.report) as stream:
                stream.write(result.to_html())
        except (OSError, ValueError) as exc:  # ValueError: a name that cannot be encoded
            why = getattr(exc, "strerror", None) or exc
            raise _UsageError(f"cannot write the report: {why}") from None
    code = 0 if result.accepted else 1
    if args.format == "json":
        return result.to_json() + "\n", True, code
    if args.format == "html":
        return result.to_html(), False, code
    saved = f"\n  Report: {_safe(args.report)}\n" if args.report else ""
    return result.to_text() + saved, False, code


def _matrix(args: argparse.Namespace) -> tuple[str, bool, int]:
    try:
        found = conformance.matrix(
            args.folder, max_bytes=args.max_bytes, time_budget=args.time_budget
        )
    except ValueError as exc:
        raise _UsageError(str(exc)) from None
    if not found.entries:
        raise _UsageError(f"no evidence.json under {args.folder}")
    code = 1 if found.unverified else 0
    if args.format == "json":
        return found.to_json() + "\n", True, code
    if args.format == "html":
        return found.to_html(), False, code
    if args.format == "markdown":
        return found.to_markdown(), False, code
    return found.to_text(), False, code


def _safe_lines(text: str) -> str:
    return "\n".join(_safe(line) for line in text.split("\n"))


_COMMANDS: Final = {
    "classes": _classes,
    "new": _new,
    "grade": _grade,
    "verify": _verify,
    "scorecard": _scorecard,
    "accept": _accept,
    "matrix": _matrix,
}


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs conformance`` with ``argv`` (the arguments after ``conformance``)."""
    parser = build_parser()
    args = parser.parse_args(list(argv))
    if getattr(args, "max_bytes", 1) < 1:
        parser.error("--max-bytes must be positive")
    budget = getattr(args, "time_budget", None)
    if budget is not None and budget <= 0:
        parser.error("--time-budget must be a positive number of seconds")
    try:
        body, is_json, code = _COMMANDS[args.command](args)
    except _UsageError as exc:
        print(f"{_PROG}: {_safe(str(exc))}", file=sys.stderr)
        return 2
    except PydiggsError as exc:
        why = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        print(f"{_PROG}: {_safe(why)}", file=sys.stderr)
        return 2
    problem = _stdout.write_report(body, json_report=is_json, open_output=_open_output)
    if problem is not None:
        print(f"{_PROG}: {_safe(problem)}", file=sys.stderr)
        return 2
    return code
