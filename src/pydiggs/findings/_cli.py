"""``pydiggs validate``: findings for many files as text, JSON or SARIF.

Exit codes: 0 when no finding is at or above ``--fail-on`` (default ``error``); 1 when
some are; 2 on a usage error, when no input file was found, or when any input could
not be read (2 outranks 1). An XML syntax error is a finding, so it gives 1.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import multiprocessing
import os
import pickle
import stat
import sys
import unicodedata
import urllib.parse
from collections.abc import Iterator, Sequence
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, TextIO

import pydiggs
from pydiggs import _xml
from pydiggs.detect import SCHEMA_PATHS
from pydiggs.errors import InstanceReadError, ResourceError
from pydiggs.findings import _json, _sarif, rules
from pydiggs.findings.model import Finding, Report, Severity, Step, UnreadableFile
from pydiggs.findings.run import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FINDINGS,
    NATIVE_STEPS,
    display_paths,
    reference_data,
    selected_steps,
    validate,
)

__all__ = ["MAX_TIMEOUT", "build_parser", "expand_inputs", "main"]

#: Largest ``--timeout``: one day.
MAX_TIMEOUT = 86_400


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
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        metavar="N",
        help="refuse instances larger than N bytes (default 100 MiB)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="validate each file in its own process and stop it after SECONDS (an error)",
    )
    parser.add_argument(
        "--local-dictionaries",
        choices=["instance-dir", "off"],
        default="instance-dir",
        help="read local dictionaries only from each instance's folder (default), or never",
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


def _is_regular(path: Path) -> bool:
    """A regular file, not a symlink, FIFO, socket or device (``lstat``)."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _walk(directory: Path) -> Iterator[Path]:
    for root, dirs, files in os.walk(directory, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            candidate = Path(root) / name
            if name.lower().endswith(".xml") and _is_regular(candidate):
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


_ONE_BYTE = 0x100


def _safe(text: str) -> str:
    """Escape control, format and line/paragraph separators (Cc, Cf, Zl, Zp; not tab).

    Paths and messages come from the instance; raw escape sequences or bidi overrides
    could rewrite what the reader sees.
    """
    out = []
    for ch in text:
        if ch != "\t" and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            code = ord(ch)
            out.append(f"\\x{code:02x}" if code < _ONE_BYTE else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _text(reports: Sequence[Report], unreadable: Sequence[UnreadableFile], out: TextIO) -> None:
    for report in reports:
        c = report.counts
        path = _safe(report.path)
        out.write(
            f"{path}: {report.verdict}: {c['error']} error(s), {c['warning']} warning(s),"
            f" {c['info']} info\n"
        )
        # A partly checked file says why, even though INFO findings are not listed:
        # which steps did not finish, and each code list that could not be fetched.
        shown = {rules.FINDINGS_TRUNCATED}
        if report.verdict == "partly-checked":
            unfinished = ", ".join(
                f"{step.value} ({status})"
                for step, status in report.steps.items()
                if status in ("partial", "incomplete")
            )
            out.write(f"  {path}: not fully checked: {unfinished}\n")
            shown.add(rules.DICTIONARY_OFFLINE)
        for f in report.findings:
            if f.severity is Severity.INFO and f.rule_id not in shown:
                continue
            where = f"{path}:{f.line}" if f.line else path
            if f.line and f.column:
                where += f":{f.column}"
            first = f.message.split("\n", 1)[0] if f.message else ""
            out.write(f"  {where}: {f.severity.value} {f.rule_id}: {_safe(first)}\n")
    for item in unreadable:
        out.write(f"{_safe(item.path)}: unreadable: {_safe(item.error)}\n")


def _open_output(name: str) -> TextIO:
    """Open ``--output`` for writing, refusing to write through a symlink.

    A new file is created with mode 0600. An existing file is truncated and keeps
    its mode (the user chose it).
    """
    if Path(name).is_symlink():
        raise OSError(f"refusing to write through a symlink: {name}")
    # O_BINARY and newline="": the same bytes on every OS (no CRLF on Windows).
    extra = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | extra, 0o600)
    except FileExistsError:
        fd = os.open(name, os.O_WRONLY | os.O_TRUNC | extra)
    return os.fdopen(fd, "w", encoding="utf-8", newline="")


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
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    if args.timeout is not None and not 0 < args.timeout <= MAX_TIMEOUT:  # also NaN, inf
        parser.error(f"--timeout must be a positive number of seconds, at most {MAX_TIMEOUT:,}")
    return args


def _validate_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "diggs_version": args.diggs_version,
        "checks": args.check_list,
        "schema_path": args.schema_path,
        "dictionary_path": args.dictionary_path,
        "schematron_path": args.schematron_path,
        "allow_network": args.allow_network,
        "include_snippets": args.snippets,
        "max_findings": args.max_findings or None,
        "max_bytes": args.max_bytes,
        "base_dir": args.base_dir,
        "local_dictionaries": args.local_dictionaries,
    }


def _worker(conn: Connection, path: str, kwargs: dict[str, Any]) -> None:
    """Validate one file in a child process and send back the report or the error."""
    try:
        conn.send(("report", validate(path, **kwargs)))
    except (InstanceReadError, ResourceError) as exc:
        conn.send(("error", exc))
    except Exception as exc:  # becomes an ERROR report in the parent
        conn.send(("crash", _describe(exc, Path(path), kwargs)))
    finally:
        conn.close()


def _describe(exc: BaseException, path: Path, kwargs: dict[str, Any]) -> str:
    """The exception's type and its text: scrubbed, then cut short, then scrubbed again.

    The raw text is scrubbed first, so a path is never cut in half before it can be
    recognised; the cut text is scrubbed again by :func:`_failed`.
    """
    shown, _ = display_paths(path, kwargs.get("base_dir"))
    text = " ".join(_scrub(str(exc), path, shown).split())
    return f"{type(exc).__name__}: {text[:_MAX_ERROR_TEXT]}" if text else type(exc).__name__


_MAX_ERROR_TEXT = 300


def _spellings(location: Path) -> list[str]:
    """Ways an error text may spell an absolute path: plain, file: URI, %-encoded."""
    plain = str(location)
    posix = location.as_posix()  # on Windows: forward slashes, no %5C when encoded
    forms = {plain, posix, urllib.parse.quote(posix)}
    try:
        uri = location.as_uri()
    except ValueError:
        uri = ""
    if uri:
        forms |= {uri, urllib.parse.unquote(uri)}
    return [f for f in forms if len(f) > 1]


def _scrub(text: str, path: Path, shown: str) -> str:
    """Replace the file's absolute path, then the home folder, as validate() does."""
    instance: set[str] = set()
    for location in {path.resolve(), path.absolute()}:
        instance.update(_spellings(location))
    for spelling in sorted(instance, key=len, reverse=True):
        text = text.replace(spelling, shown)
    home = _home()
    if home is not None:
        for spelling in sorted(_spellings(home), key=len, reverse=True):
            text = text.replace(spelling, "~")
    return text


def _home() -> Path | None:
    """The home folder, or None when it is unknown or the filesystem root."""
    try:
        home = Path.home()
    except (KeyError, RuntimeError, OSError):
        return None
    return None if home == Path(home.anchor) else home


def _validate_isolated(path: Path, kwargs: dict[str, Any], timeout: float) -> Report:
    """Run validate() in a spawned process; past ``timeout`` seconds, stop it.

    A file that times out gets a report with one structure.timeout ERROR and every
    requested step incomplete (fail closed). Works the same on Windows, macOS and
    Linux: no signals, a fresh interpreter per file.
    """
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    worker = context.Process(target=_worker, args=(send, str(path), kwargs), daemon=True)
    worker.start()
    send.close()
    answer: tuple[str, Any] | None = None
    died = False
    try:
        if receive.poll(timeout):
            answer = receive.recv()
    except (EOFError, OSError, pickle.UnpicklingError):  # died without a usable answer
        died = True
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
            if worker.is_alive():
                worker.kill()
        worker.join()
        receive.close()
    if answer is None:
        why = "stopped unexpectedly" if died else f"did not finish within {timeout:g} s (--timeout)"
        return _failed(path, kwargs, rules.TIMEOUT, f"Validation {why}")
    kind, value = answer
    if kind == "error":
        raise value
    if kind == "crash":
        return _failed(path, kwargs, rules.INTERNAL_ERROR, f"Validation failed ({value})")
    report: Report = value
    return report


def _failed(path: Path, kwargs: dict[str, Any], rule_id: str, why: str) -> Report:
    """A report with one ERROR and every requested step incomplete (fail closed)."""
    shown, absolute = display_paths(path, kwargs.get("base_dir"))
    why = _scrub(why, path, shown)
    try:
        data, _ = _xml.read_limited(path, kwargs["max_bytes"], regular_only=True)
    except OSError as exc:
        raise InstanceReadError(exc.errno or 0, exc.strerror or "could not be read", shown) from exc
    wanted = selected_steps(kwargs.get("checks"))
    return Report(
        path=shown,
        absolute_path=absolute,
        sha256=hashlib.sha256(data).hexdigest(),
        diggs_version=None,
        steps={s: "incomplete" if s in wanted else "skipped" for s in NATIVE_STEPS},
        findings=(
            Finding(
                step=Step.STRUCTURE,
                rule_id=rule_id,
                severity=Severity.ERROR,
                message=f"{why}; the file is not reported as passing.",
            ),
        ),
        tool_version=pydiggs.__version__,
        reference_data=reference_data(),
    )


def _run(
    args: argparse.Namespace, inputs: Sequence[Path]
) -> tuple[list[Report], list[UnreadableFile]]:
    """Validate every input; ResourceError propagates (it aborts the batch).

    Any other unexpected error for one file becomes an ERROR report for that file
    (``structure.internal-error``) and the batch goes on.
    """
    reports: list[Report] = []
    unreadable: list[UnreadableFile] = []
    kwargs = _validate_kwargs(args)
    for path in inputs:
        try:
            if args.timeout is None:
                try:
                    reports.append(validate(path, **kwargs))
                except (InstanceReadError, ResourceError):
                    raise
                except Exception as exc:  # fail closed for this file only
                    why = f"Validation failed ({_describe(exc, path, kwargs)})"
                    reports.append(_failed(path, kwargs, rules.INTERNAL_ERROR, why))
            else:
                reports.append(_validate_isolated(path, kwargs, args.timeout))
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
