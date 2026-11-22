"""``pydiggs tables``: profile tables to DIGGS, DIGGS to profile tables (preview).

``pydiggs tables schema`` prints the profile as a Data Package descriptor.
``pydiggs tables to-diggs FOLDER -o FILE`` reads a folder of CSV files and writes a DIGGS
3.0.0 file. ``pydiggs tables from-diggs FILE -o FOLDER`` writes a DIGGS file as CSV files
with ``datapackage.json`` and ``plan.json``.

Exit codes, as for ``pydiggs convert``: 0 when no finding is at or above ``--fail-on``; 1
when some are (``error``, the default: a row was left out; ``loss``: anything was left
out or assumed); 2 on a usage error, an input that cannot be read or is not of the
expected format, or an output that cannot be written.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydiggs import _stdout
from pydiggs._convert_cli import _UsageError, _write_atomically
from pydiggs.errors import PydiggsError
from pydiggs.findings._cli import _safe
from pydiggs.findings.model import Finding

__all__ = ["build_parser", "main"]

_PROG: Final = "pydiggs tables"


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs tables`` argument parser."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Convert between tables of the DIGGS tables profile and DIGGS files"
        " (preview). Shape your database into the profile's tables with SQL views, export"
        " them as CSV, and pyDIGGS writes DIGGS 3.0.0; or turn a DIGGS file into tables"
        " with a load plan. No value is converted to another unit, and every row or value"
        " that could not be carried is named.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    schema = commands.add_parser(
        "schema", help="print the profile: every table, column and key (a Data Package)"
    )
    schema.add_argument("-o", "--output", help="write the descriptor to this file")
    schema.add_argument("--force", action="store_true", help="replace an existing file")
    to_diggs = commands.add_parser("to-diggs", help="a folder of CSV tables to a DIGGS 3.0.0 file")
    to_diggs.add_argument("input", metavar="FOLDER", help="the folder of CSV files")
    to_diggs.add_argument("-o", "--output", required=True, help="the DIGGS file to write")
    from_diggs = commands.add_parser(
        "from-diggs", help="a DIGGS file to a folder of CSV tables with a load plan"
    )
    from_diggs.add_argument("input", metavar="FILE", help="the DIGGS file")
    from_diggs.add_argument("-o", "--output", required=True, help="the folder to write")
    for command in (to_diggs, from_diggs):
        command.add_argument("--force", action="store_true", help="replace existing files")
        command.add_argument("--format", choices=["text", "json"], default="text")
        command.add_argument(
            "--fail-on",
            choices=["error", "loss"],
            default="error",
            help="exit 1 on an error (the default) or on anything left out or assumed",
        )
    return parser


def _entry(finding: Finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity.value,
        "message": finding.message,
        "path": finding.path,
    }


def _failed(findings: Sequence[Finding], fail_on: str) -> bool:
    worst = max((finding.severity.rank for finding in findings), default=0)
    return worst >= (2 if fail_on == "error" else 1)


def fact_line(key: str, value: Any) -> str:
    """One ``key: value`` line of a text summary; a mapping of counts is listed in order."""
    if isinstance(value, dict):  # the rows of each table, in load order
        value = ", ".join(f"{name} {count}" for name, count in value.items())
    return f"{key}: {value}"


def finding_line(finding: Finding) -> str:
    """One finding as a line of a text summary."""
    return f"{finding.severity.value:7} {finding.rule_id}  {finding.path or ''}  {finding.message}"


def text_summary(lines: Sequence[str]) -> str:
    """Lines for a terminal: each escaped, since a message may quote the source's values."""
    return "\n".join(_safe(line) for line in lines) + "\n"


def _report(args: argparse.Namespace, findings: Sequence[Finding], facts: dict[str, Any]) -> str:
    if args.format == "json":
        body = {**facts, "findings": [_entry(finding) for finding in findings]}
        return json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    lines = [fact_line(key, value) for key, value in facts.items()]
    return text_summary([*lines, *(finding_line(finding) for finding in findings)])


def _schema(args: argparse.Namespace) -> int:
    from pydiggs import tables  # noqa: PLC0415 - loads the profile on demand

    body = json.dumps(tables.profile(), indent=2, ensure_ascii=False) + "\n"
    if args.output is None:
        problem = _stdout.write_report(body, json_report=True, what="descriptor")
        if problem is not None:
            raise _UsageError(problem)
        return 0
    _write_atomically(Path(args.output), body.encode("utf-8"), force=args.force)
    return 0


def _to_diggs(args: argparse.Namespace) -> tuple[Sequence[Finding], dict[str, Any]]:
    import pydiggs  # noqa: PLC0415 - loads the writer on demand
    from pydiggs import tables  # noqa: PLC0415

    result = tables.to_model(tables.read_package(args.input))
    data = pydiggs.write_bytes(result.model)
    _write_atomically(Path(args.output), data, force=args.force)
    model = result.model
    facts = {
        "output": args.output,
        "diggs_version": "3.0.0",
        "profile_version": tables.PROFILE_VERSION,
        "features": len(model.features),
        "samples": len(model.samples),
        "layers": sum(len(observation.layers) for observation in model.observations),
        "measurements": len(model.measurements),
        "lossless": result.lossless,
    }
    return result.findings, facts


def _from_diggs(args: argparse.Namespace) -> tuple[Sequence[Finding], dict[str, Any]]:
    import pydiggs  # noqa: PLC0415 - loads the reader on demand
    from pydiggs import tables  # noqa: PLC0415

    target = Path(args.output)
    if target.exists() and not target.is_dir():
        raise _UsageError(f"{target} exists and is not a folder")
    if target.is_dir() and any(target.iterdir()) and not args.force:
        raise _UsageError(f"{target} is not empty; pass --force to write into it")
    found = tables.from_model(pydiggs.read(args.input))
    written = tables.write_package(found, target)
    facts = {
        "output": args.output,
        "profile_version": tables.PROFILE_VERSION,
        "tables": {step.table: step.rows for step in found.plan.steps},
        "files": len(written),
    }
    return found.findings, facts


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs tables`` with ``argv`` (the arguments after ``tables``)."""
    args = build_parser().parse_args(list(argv))
    try:
        if args.command == "schema":
            return _schema(args)
        findings, facts = (_to_diggs if args.command == "to-diggs" else _from_diggs)(args)
    except (_UsageError, PydiggsError) as exc:
        print(f"{_PROG}: {_safe(str(exc))}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{_PROG}: {_safe(f'cannot read or write: {exc.strerror or exc}')}", file=sys.stderr)
        return 2
    body = _report(args, findings, facts)
    problem = _stdout.write_report(body, json_report=args.format == "json", what="summary")
    if problem is not None:
        print(f"{_PROG}: {_safe(problem)}", file=sys.stderr)
        return 2
    return 1 if _failed(findings, args.fail_on) else 0
