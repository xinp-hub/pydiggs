r"""``pydiggs diff``: what changed between two DIGGS files, as text or diggs-diff JSON.

Stable from pyDIGGS 1.13 (a preview in 1.12): the JSON report is a diggs-diff 1.0 document.

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
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydiggs import _service, _stdout
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
            "Compare two DIGGS files by what they describe, not by how they are written:"
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
        # gml_id, as the report spells it, is gml-id: converted before the choice is checked.
        type=lambda mode: _engine.MATCH_ALIASES.get(mode, mode),
        default=_engine.MATCH_MODES[0],
        help=(
            "how records are paired: auto (the default; by gml:id, then by natural key),"
            " gml-id (ids only, also written gml_id; a record whose gml:id changed is"
            " removed and added) or natural (natural keys only)"
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
            " (1.50 and 1.5, other separators that read the same values, double and"
            " float) and records whose gml:id changed; both are always counted"
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
    never status 1, which would say "the files differ" to a caller that got no report
    (:func:`pydiggs._stdout.write_report`, as for ``validate`` and ``convert``).
    """
    return _stdout.write_report(
        body, json_report=fmt == "json", output=output, open_output=_open_output
    )
