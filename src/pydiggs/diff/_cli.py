r"""``pydiggs diff``: what changed between two DIGGS files, as text or diggs-diff JSON.

A preview in pyDIGGS 1.12: the report format and the comparison rules may change in a minor release.

The comparison is :func:`pydiggs.diff.compare`: the same options, the same result. The
JSON report is :meth:`~pydiggs.diff.Diff.to_json` (a diggs-diff document). The text
report is the one :meth:`~pydiggs.diff.Diff.to_text` gives, written for a plain stream:
file text is untrusted, so control and format characters are spelled out in the same way,
but ``rich`` markup is not escaped, because stdout and ``-o FILE`` read no markup (and
``rich`` is not imported).

Exit codes, as for ``diff(1)`` and with the three-way split of ``pydiggs validate``: 0
when the two files are identical and the comparison finished (numbers written differently
and records whose ``gml:id`` changed are not differences); 1 when they differ, or when
the comparison is incomplete (a limit or the time budget stopped it, so it cannot say the
files are the same); 2 on a usage error, an input that cannot be read or is not DIGGS, or
an output that cannot be written.

The report is never lost to an encoding. ``-o FILE`` is UTF-8. On stdout, text the
stream's encoding cannot write (a redirected stdout on Windows is cp1252) is written with
escapes: backslash escapes in the text report, and JSON's own ``\uXXXX`` in the JSON
report, which is then plain ASCII and the same document.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydiggs import _service
from pydiggs import diff as _diff
from pydiggs._convert_cli import _same_file
from pydiggs.diff import _engine, _text
from pydiggs.diff._types import Diff
from pydiggs.errors import DocumentError, InstanceReadError
from pydiggs.findings._cli import _open_output, _safe
from pydiggs.findings.run import DEFAULT_MAX_BYTES

__all__ = ["build_parser", "main"]

_PROG: Final = "pydiggs diff"
_DEFAULT_REL_TOL: Final = 1e-9
_DEFAULT_MAX_CHANGES: Final = 10_000


class _UsageError(Exception):
    """An exit-2 problem with a message for stderr."""


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs diff`` argument parser."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Compare two DIGGS files by what they describe, not by how they are written"
            " (preview: the report format and the comparison rules may change in a minor"
            " release):"
            " element order, number formatting, line numbers and the gml:id of a record"
            " do not count. Boreholes, samples, tests and the other records are matched by"
            " gml:id, then by a natural key (a borehole's kind and name, a sample's"
            " borehole, name and top), so records stay paired when their gml:ids change:"
            " two exports of the same data with new ids are identical. The renamed ids"
            " are counted, and listed with --verbose. Numbers compare within a tolerance;"
            " a change of unit is always a change, and nothing is converted. The two"
            " files may be different DIGGS versions."
        ),
        epilog=(
            "Exit status: 0 the files are identical and the comparison finished; 1 they"
            " differ, or the comparison is incomplete; 2 usage error, an input that cannot"
            " be read or is not DIGGS, or an output that cannot be written."
        ),
    )
    parser.add_argument("old", metavar="OLD", help="the earlier DIGGS file")
    parser.add_argument("new", metavar="NEW", help="the later DIGGS file")
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="the report: text (the default), or JSON (a diggs-diff document)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE", help="write the report here, not to stdout"
    )
    parser.add_argument(
        "--match",
        choices=list(_engine.MATCH_MODES),
        default=_engine.MATCH_MODES[0],
        help=(
            "how records are paired: auto (the default; by gml:id, then by natural key),"
            " gml-id (ids only; a record whose gml:id changed is removed and added) or"
            " natural (natural keys only)"
        ),
    )
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=_DEFAULT_REL_TOL,
        metavar="R",
        help=(
            "two numbers are equal when they differ by at most R times the larger one;"
            f" 0 or more and less than 1 (default {_DEFAULT_REL_TOL:g})"
        ),
    )
    parser.add_argument(
        "--abs-tol",
        type=float,
        default=0.0,
        metavar="A",
        help="or by at most A, in the value's own unit (default 0)",
    )
    parser.add_argument(
        "--max-changes",
        type=int,
        default=_DEFAULT_MAX_CHANGES,
        metavar="N",
        help=(
            f"list at most N changes (default {_DEFAULT_MAX_CHANGES}); the counts stay"
            " exact, and a cut list is an incomplete comparison"
        ),
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="stop after SECONDS, reading included; the comparison is then incomplete",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        metavar="N",
        help="refuse files larger than N bytes (default 100 MiB)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "also list what is not a change: values that are only written differently"
            " (1.50 and 1.5) and records whose gml:id changed; both are always counted"
        ),
    )
    return parser


def _options(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    args = parser.parse_args(list(argv))
    for flag, value in (("--rel-tol", args.rel_tol), ("--abs-tol", args.abs_tol)):
        if not math.isfinite(value) or value < 0:
            parser.error(f"{flag} must be a finite number, 0 or more")
    if args.rel_tol >= 1:
        parser.error("--rel-tol must be less than 1")
    if args.max_changes < 0:
        parser.error("--max-changes must be 0 or more")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    if args.time_budget is not None and not 0 < args.time_budget <= _service.MAX_SECONDS:
        parser.error(
            f"--time-budget must be a positive number of seconds, at most {_service.MAX_SECONDS}"
        )
    return args


def _compare(args: argparse.Namespace) -> Diff:
    """Run :func:`pydiggs.diff.compare`; a problem with an input says which file."""
    started = time.monotonic()
    try:
        return _diff.compare(
            args.old,
            args.new,
            match=args.match,
            rel_tol=args.rel_tol,
            abs_tol=args.abs_tol,
            max_changes=args.max_changes,
            time_budget=args.time_budget,
            max_bytes=args.max_bytes,
            show_reformatted=args.verbose,
            show_renamed=args.verbose,
        )
    except InstanceReadError as exc:
        named = exc.filename if isinstance(exc.filename, str) else None
        which = next((p for p in (args.old, args.new) if named and Path(p) == Path(named)), None)
        why = exc.strerror or str(exc)
        raise _UsageError(f"cannot read {which or 'an input'}: {why}") from None
    except DocumentError as exc:
        raise _UsageError(f"{_not_diggs(args, started)}: {exc}") from None


def _not_diggs(args: argparse.Namespace, started: float) -> str:
    """Name the input that is not a DIGGS document (OLD is read first, then NEW).

    The comparison does not say which of the two it was, so OLD is read once more,
    within what is left of ``--time-budget``. When that cannot settle it (no time left,
    OLD read only in part, OLD gone), neither file is named.
    """
    from pydiggs import _reader  # noqa: PLC0415  # only after a failed comparison

    unknown = "an input is not a DIGGS file"
    left = None
    if args.time_budget is not None:
        left = args.time_budget - (time.monotonic() - started)
        if left <= 0:
            return unknown
    try:
        model = _reader.read(args.old, max_bytes=args.max_bytes, results=False, time_budget=left)
    except DocumentError:
        return f"{args.old} is not a DIGGS file"
    except OSError:
        return unknown
    return f"{args.new} is not a DIGGS file" if model.complete else unknown


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs diff`` with ``argv`` (the arguments after ``diff``)."""
    args = _options(build_parser(), argv)
    try:
        if args.output and any(
            _same_file(Path(args.output), Path(source)) for source in (args.old, args.new)
        ):
            raise _UsageError(f"refusing to write {args.output} over an input")
        result = _compare(args)
    except _UsageError as exc:
        print(f"{_PROG}: {_safe(str(exc))}", file=sys.stderr)
        return 2
    if args.format == "json":
        body = result.to_json(indent=2) + "\n"
    else:
        body = _text.render(result, markup=False)
    problem = _write(body, args.format, args.output)
    if problem is not None:
        print(f"{_PROG}: {_safe(problem)}", file=sys.stderr)
        return 2
    return 0 if result.identical else 1


def _write(body: str, fmt: str, output: str | None) -> str | None:
    """Write the report to ``output``, or to stdout; what went wrong, or None.

    Whatever goes wrong is one line for stderr and exit status 2, never a traceback and
    never status 1, which would say "the files differ" to a caller that got no report.
    """
    try:
        if output:
            with _open_output(output) as stream:
                stream.write(_encodable(body, fmt, "utf-8"))
            return None
        stdout = sys.stdout
        if stdout is None:  # started with no standard output at all
            return "cannot write the report: there is no standard output"
        stdout.write(_encodable(body, fmt, getattr(stdout, "encoding", None) or "utf-8"))
        stdout.flush()
    except (OSError, ValueError) as exc:  # ValueError: a closed stream, an encoding
        if output:
            return f"cannot write {output}: {getattr(exc, 'strerror', None) or exc}"
        _silence_stdout()
        return f"cannot write the report: {getattr(exc, 'strerror', None) or exc}"
    return None


_NOT_ASCII: Final = re.compile(r"[^\x00-\x7f]")
_BMP_END: Final = 0x10000


def _json_escape(match: re.Match[str]) -> str:
    """One character as JSON writes it in ASCII (two escapes above the basic plane)."""
    code = ord(match[0])
    if code < _BMP_END:
        return f"\\u{code:04x}"
    code -= _BMP_END
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def _encodable(body: str, fmt: str, encoding: str) -> str:
    r"""``body`` as it is when ``encoding`` can write it, else with escapes in place.

    A name or a value from a file can hold any character, and a stream may be ASCII or
    cp1252 (a redirected stdout on Windows), or UTF-8 faced with a lone surrogate (a
    file name that is not valid UTF-8). The JSON report then escapes every character
    outside ASCII the way JSON does: the same document, readable in any encoding. The
    text report keeps what the stream can write and spells out the rest
    (``\u03c6``), as it spells out control characters.
    """
    try:
        body.encode(encoding)
    except LookupError:  # an encoding this Python does not know: the stream decides
        return body
    except UnicodeError:
        pass
    else:
        return body
    if fmt == "json":
        # Outside ASCII a JSON document has only the characters inside its strings.
        return _NOT_ASCII.sub(_json_escape, body)
    return body.encode(encoding, "backslashreplace").decode(encoding)


def _silence_stdout() -> None:
    """Point stdout at the null device after a failed write.

    Text may still sit in the stream's buffer. Python flushes it again at exit, and a second
    failure there would replace this command's exit status with a traceback.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass  # stdout has no file descriptor (it was replaced by an in-memory stream)
