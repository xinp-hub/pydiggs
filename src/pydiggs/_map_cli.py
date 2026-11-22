"""``pydiggs map``: test, check, start and measure a vendor's export (preview).

``pydiggs map test FOLDER`` writes a folder of CSV tables as DIGGS, validates the file,
reads it back and compares it with what was sent. ``pydiggs map check FOLDER`` checks
the tables against the DIGGS vocabularies without writing a file. Both take profile
tables, or with ``--mapping FILE`` a vendor's own tables and the mapping file that
shapes them. ``pydiggs map init FOLDER -o FILE`` starts a mapping file from the CSV
headers, and ``pydiggs map coverage FOLDER --mapping FILE`` says how much of the source
a mapping reads.

Exit codes, as for ``pydiggs tables``: 0 when the test passes, or no finding is at or
above ``--fail-on``; 1 when it does not, or some are; 2 on a usage error, an input that
cannot be read or is not of the expected format, or an output that cannot be written.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from pydiggs import _stdout
from pydiggs._convert_cli import _UsageError, _write_atomically
from pydiggs._tables_cli import _entry, _failed, fact_line, finding_line, text_summary
from pydiggs.errors import PydiggsError
from pydiggs.findings._cli import _safe
from pydiggs.findings.model import Finding

__all__ = ["build_parser", "main"]

_PROG: Final = "pydiggs map"
Tables = Mapping[str, Iterable[Mapping[str, Any]]]


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs map`` argument parser."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Test and check an export to DIGGS, and start and measure a mapping"
        " file (preview). FOLDER holds CSV files: tables of the DIGGS tables profile, or"
        " with --mapping your own tables and the mapping file that shapes them into the"
        " profile. No value is converted to another unit, and every row or value that"
        " could not be carried is named.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    test = commands.add_parser(
        "test",
        help="write the tables as DIGGS, read the file back, and list what did not return",
    )
    check = commands.add_parser(
        "check",
        help="check the tables (and the mapping) against the DIGGS vocabularies; no file"
        " is written",
    )
    coverage = commands.add_parser(
        "coverage", help="which source columns a mapping reads, and which it does not"
    )
    for command in (test, check, coverage):
        command.add_argument("input", metavar="FOLDER", help="the folder of CSV files")
        command.add_argument(
            "--mapping",
            metavar="FILE",
            required=command is coverage,
            help="a diggs-mapping file; FOLDER then holds your own tables",
        )
        command.add_argument("--format", choices=["text", "json"], default="text")
    test.add_argument("-o", "--output", help="keep the DIGGS file written, here")
    test.add_argument("--force", action="store_true", help="replace an existing file")
    check.add_argument(
        "--fail-on",
        choices=["error", "loss"],
        default="error",
        help="exit 1 on an error (the default) or on anything left out or assumed",
    )
    init = commands.add_parser("init", help="start a mapping file from the CSV headers of FOLDER")
    init.add_argument("input", metavar="FOLDER", help="the folder of your own CSV files")
    init.add_argument("-o", "--output", required=True, help="the mapping file to write")
    init.add_argument("--name", default="my-mapping", help="the name of the mapping")
    init.add_argument("--force", action="store_true", help="replace an existing file")
    return parser


def _shaped(args: argparse.Namespace) -> tuple[Tables, tuple[Finding, ...], dict[str, Any]]:
    """The profile tables of FOLDER, what the mapping reported, and facts for the report."""
    from pydiggs import mapping, tables  # noqa: PLC0415 - loads the profile on demand

    facts: dict[str, Any] = {"profile_version": tables.PROFILE_VERSION}
    if args.mapping is None:
        return tables.read_package(args.input), (), facts
    loaded = mapping.load(Path(args.mapping))
    facts["mapping"] = f"{loaded.name} {loaded.version}"
    applied = loaded.apply(mapping.read_source(args.input))
    missing = mapping.coverage(loaded, mapping.schema_from_csv(args.input)).source_missing
    if missing:
        facts["columns_the_mapping_reads_that_the_source_lacks"] = {
            table: list(columns) for table, columns in missing.items()
        }
    return applied.tables, (*mapping.check(loaded), *applied.findings), facts


def _test(args: argparse.Namespace) -> tuple[bool, list[Finding], dict[str, Any], list[str]]:
    from pydiggs import tables  # noqa: PLC0415 - loads the writer on demand
    from pydiggs.tables._report import shown  # noqa: PLC0415

    shaped, reported, facts = _shaped(args)
    checked = tables.round_trip(shaped)
    if args.output is not None:
        _write_atomically(Path(args.output), checked.diggs, force=args.force)
        facts["output"] = args.output
    findings = [*reported, *checked.findings]
    invalid = [finding for finding in checked.validation.findings if finding.severity.rank]
    ok = checked.ok and not any(finding.severity.rank for finding in reported)
    facts = {"ok": ok, **facts, "diggs_version": "3.0.0"}
    facts["validation"] = [_entry(finding) for finding in invalid]
    facts["differences"] = [
        {"table": d.table, "row": d.row, "column": d.column, "sent": d.sent, "got": d.got}
        for d in checked.differences
    ]
    lines = [
        f"validate {f.severity.value:7} {f.rule_id}  {f.path or ''}  {shown(f.message, 300)}"
        for f in invalid
    ]
    lines += [
        f"differs  {d.table}  {shown(d.row)}  {shown(d.column)}"
        f"  sent={shown(d.sent)!r}  got={shown(d.got)!r}"
        for d in checked.differences
    ]
    return ok, findings, facts, lines


def _check(args: argparse.Namespace) -> tuple[bool, list[Finding], dict[str, Any], list[str]]:
    from pydiggs import tables  # noqa: PLC0415 - loads the dictionaries on demand

    shaped, reported, facts = _shaped(args)
    held = {name: list(rows) for name, rows in shaped.items()}
    findings = [*reported, *tables.check(held)]
    facts["rows"] = {name: len(rows) for name, rows in held.items()}
    return not _failed(findings, args.fail_on), findings, facts, []


def _coverage(args: argparse.Namespace) -> tuple[bool, list[Finding], dict[str, Any], list[str]]:
    from pydiggs import mapping  # noqa: PLC0415 - loads the profile on demand

    loaded = mapping.load(Path(args.mapping))
    covered = mapping.coverage(loaded, mapping.schema_from_csv(args.input))
    facts = {"mapping": f"{loaded.name} {loaded.version}", **covered.to_dict()}
    lines = [
        f"source columns read: {covered.source_percent}%",
        f"profile columns filled (of the tables the mapping fills): {covered.profile_percent}%",
    ]
    for title, listed in (
        ("not read", covered.source_unmapped),
        ("read by the mapping but not in the source", covered.source_missing),
        ("profile columns left empty", covered.profile_empty),
    ):
        lines += [f"{title}: {table}: {', '.join(columns)}" for table, columns in listed.items()]
    return True, [], facts, lines


def _init(args: argparse.Namespace) -> int:
    from pydiggs import mapping  # noqa: PLC0415 - loads the profile on demand

    schema = mapping.schema_from_csv(args.input)
    if not schema:
        raise _UsageError(f"{args.input} holds no CSV file")
    text = mapping.scaffold(schema, name=args.name)
    _write_atomically(Path(args.output), text.encode("utf-8"), force=args.force)
    return 0


def _report(
    args: argparse.Namespace, findings: Sequence[Finding], facts: dict[str, Any], lines: list[str]
) -> str:
    if args.format == "json":
        body = {**facts, "findings": [_entry(finding) for finding in findings]}
        return json.dumps(body, indent=2, ensure_ascii=False, default=str) + "\n"
    if args.command == "coverage":
        out = [f"mapping: {facts['mapping']}", *lines]
    else:
        out = [
            fact_line(key, value)
            for key, value in facts.items()
            if key not in ("validation", "differences")
        ]
        out += [finding_line(finding) for finding in findings]
        out += lines
    return text_summary(out)


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs map`` with ``argv`` (the arguments after ``map``)."""
    args = build_parser().parse_args(list(argv))
    try:
        if args.command == "init":
            return _init(args)
        run = {"test": _test, "check": _check, "coverage": _coverage}[args.command]
        ok, findings, facts, lines = run(args)
    except (_UsageError, PydiggsError) as exc:
        print(f"{_PROG}: {_safe(str(exc))}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{_PROG}: {_safe(f'cannot read or write: {exc.strerror or exc}')}", file=sys.stderr)
        return 2
    except (UnicodeDecodeError, csv.Error) as exc:
        print(f"{_PROG}: {_safe(f'a file is not UTF-8 CSV: {exc}')}", file=sys.stderr)
        return 2
    body = _report(args, findings, facts, lines)
    problem = _stdout.write_report(body, json_report=args.format == "json", what="summary")
    if problem is not None:
        print(f"{_PROG}: {_safe(problem)}", file=sys.stderr)
        return 2
    return 0 if ok else 1
