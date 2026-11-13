"""``pydiggs convert``: an AGS4 file to a DIGGS 3.0.0 document, with every loss reported.

The conversion runs through :func:`pydiggs._service.convert`, the same code the HTTP API
and the sidecar run, so the three give the same DIGGS bytes and the same report for the
same input. Paths are the caller's own files: the input is read with the size limit,
each output is written to a temporary file beside it and moved into place, an existing
output is replaced only with ``--force``, and no output may be the input itself.

Exit codes, as for ``pydiggs validate``: 0 when no finding is at or above ``--fail-on``;
1 when some are (``loss``: any loss, error, or validation error of the output;
``error``, the default: an error in the report or in the output's validation); 2 on a
usage error, an input that cannot be read or is not AGS4, or an output that cannot be
written.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydiggs import _service
from pydiggs.findings._cli import _safe

__all__ = ["build_parser", "main"]

_PROG: Final = "pydiggs convert"
#: Largest input accepted by default (100 MiB, as ``pydiggs ags4 check``).
_DEFAULT_MAX_BYTES: Final = 100 * 2**20
_SEVERE: Final = frozenset({"warning", "error"})


class _UsageError(Exception):
    """An exit-2 problem with a message for stderr."""


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs convert`` argument parser."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Convert an AGS4 file to a DIGGS 3.0.0 document (preview: the mapping table is a"
            " draft, see https://xinp-hub.github.io/pydiggs/convert/). Every source value"
            " that does not reach the output is reported as a loss; the output is validated."
            " pyDIGGS bundles no AGS data dictionary and is not an AGS-approved tool."
        ),
        epilog=(
            "Exit status: 0 no finding at or above --fail-on; 1 some; 2 usage error, an"
            " input that cannot be read or is not AGS4, or an output that cannot be written."
        ),
    )
    parser.add_argument("input", metavar="IN", help="the AGS4 file to convert")
    parser.add_argument(
        "-o", "--output", required=True, metavar="OUT", help="where to write the DIGGS XML"
    )
    parser.add_argument(
        "--to",
        choices=list(_service.CONVERT_TARGETS),
        default=_service.CONVERT_TARGETS[0],
        help="the format to convert to (default: diggs)",
    )
    parser.add_argument(
        "--diggs-version",
        choices=list(_service.CONVERT_DIGGS_VERSIONS),
        default=_service.CONVERT_DIGGS_VERSIONS[0],
        help="the DIGGS version to write (default: %(default)s)",
    )
    parser.add_argument(
        "--report",
        metavar="FILE",
        help="also write the findings report (diggs-findings 1.1 JSON) here",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="the summary on stdout: text, or JSON (the result without the output bytes)",
    )
    parser.add_argument("--fail-on", choices=["error", "loss"], default="error")
    parser.add_argument(
        "--force", action="store_true", help="replace OUT (and --report) if they exist"
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=_DEFAULT_MAX_BYTES,
        metavar="N",
        help="refuse inputs larger than N bytes (default 100 MiB)",
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="stop converting after SECONDS; the output is then partial and says so",
    )
    return parser


def _options(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    args = parser.parse_args(list(argv))
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    if args.time_budget is not None and not 0 < args.time_budget <= _service.MAX_SECONDS:
        parser.error(
            f"--time-budget must be a positive number of seconds, at most {_service.MAX_SECONDS}"
        )
    return args


def _same_file(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` name the same file (or would, once created)."""
    with contextlib.suppress(OSError):
        if a.exists() and b.exists():
            return a.samefile(b)
    return a.resolve() == b.resolve()


def _check_targets(args: argparse.Namespace) -> list[Path]:
    """The output paths, refused when one is the input, a repeat, or exists without --force."""
    source = Path(args.input)
    targets = [Path(args.output)] + ([Path(args.report)] if args.report else [])
    for i, target in enumerate(targets):
        if _same_file(target, source):
            raise _UsageError(f"refusing to write {target} over the input")
        if any(_same_file(target, other) for other in targets[:i]):
            raise _UsageError(f"{target} is named for two outputs")
        if target.is_symlink():
            raise _UsageError(f"refusing to write through a symlink: {target}")
        if target.exists() and not args.force:
            raise _UsageError(f"{target} exists; pass --force to replace it")
        if target.exists() and not target.is_file():
            raise _UsageError(f"{target} is not a regular file")
    return targets


def _read_input(path: str, max_bytes: int) -> bytes:
    """The input's bytes; a regular file of at most ``max_bytes`` bytes."""
    try:
        # O_NONBLOCK: opening a FIFO must not hang; read_fd then refuses anything irregular.
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
    except OSError as exc:
        raise _UsageError(f"cannot read {path}: {exc.strerror or 'not readable'}") from None
    try:
        data = _service.read_fd(fd, max_bytes=max_bytes, name=Path(path).name)
    finally:
        os.close(fd)
    if isinstance(data, _service.Outcome):
        raise _UsageError(data.detail)
    return data


def _umask() -> int:
    """The process umask (read by setting it and putting it back)."""
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


def _write_atomically(target: Path, data: bytes, *, force: bool) -> None:
    """Write ``data`` beside ``target`` and move it into place.

    Without ``force`` the move never replaces a file that appeared meanwhile: a hard
    link is exclusive, and where links are not supported the name is first claimed
    with an exclusive create (``O_EXCL``), so a file another process made is refused,
    never replaced; the claimed, empty file is then replaced by the output.
    """
    folder = target.parent if str(target.parent) else Path()
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=folder)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp makes the file 0600; an output is an ordinary file of the user's.
        temporary.chmod(0o666 & ~_umask())
        if force:
            temporary.replace(target)
            return
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise _UsageError(f"{target} exists; pass --force to replace it") from None
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
                raise
            _claim_and_replace(temporary, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _claim_and_replace(temporary: Path, target: Path) -> None:
    """Create ``target`` exclusively, then move ``temporary`` over that claimed file."""
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        claim = os.open(target, flags, 0o600)
    except FileExistsError:
        raise _UsageError(f"{target} exists; pass --force to replace it") from None
    os.close(claim)
    try:
        temporary.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            target.unlink()  # the empty claim is ours: do not leave it behind
        raise


def _findings(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    if document is None:
        return []
    return [f for entry in document.get("files", []) for f in entry.get("findings", [])]


def _failed(result: dict[str, Any], fail_on: str) -> bool:
    report, validation = _findings(result["report"]), _findings(result["validation"])
    if any(f["severity"] == "error" for f in (*report, *validation)):
        return True
    if fail_on == "loss":
        return not result["lossless"] or not result["complete"]
    return False


def _summary_text(result: dict[str, Any], source: str, output: str) -> str:
    report, validation = _findings(result["report"]), _findings(result["validation"])
    losses = [f for f in report if f["step"] == "convert" and f["severity"] in _SEVERE]
    invalid = [f for f in validation if f["severity"] == "error"]
    lines = [
        f"{_safe(source)} -> {_safe(output)}: DIGGS {result['diggs_version']},"
        f" mapping {result['mapping_version']}, {len(losses)} loss(es),"
        f" {'complete' if result['complete'] else 'INCOMPLETE'},"
        f" output {'valid' if not invalid else f'INVALID ({len(invalid)} error(s))'}"
    ]
    for finding in report:
        if finding["severity"] not in _SEVERE:
            continue
        line = finding["location"].get("line")
        place = finding["location"].get("path")
        where = f"{_safe(source)}:{line}" if line else _safe(source)
        at = f" [{_safe(place)}]" if place else ""
        message = _safe(finding["message"])
        lines.append(f"  {where}: {finding['severity']} {finding['rule_id']}{at}: {message}")
    lines.extend(f"  {_safe(output)}: error {f['rule_id']}: {_safe(f['message'])}" for f in invalid)
    return "\n".join(lines) + "\n"


def _summary_json(result: dict[str, Any], output: str) -> str:
    shown = {k: v for k, v in result.items() if k != "output_base64"}
    shown["output_path"] = output
    return json.dumps(shown, indent=2, ensure_ascii=False) + "\n"


def _convert(args: argparse.Namespace) -> dict[str, Any]:
    """Run the conversion through the service; the result document, decoded."""
    content = _read_input(args.input, args.max_bytes)
    request = _service.ConvertRequest(
        content=content,
        name=Path(args.input).name,
        to=args.to,
        diggs_version=args.diggs_version,
        max_bytes=args.max_bytes,
        time_budget=args.time_budget,
        max_result_bytes=None,
    )
    outcome = _service.convert(request)
    if not outcome.ok or outcome.document is None:
        detail = outcome.detail or "the conversion failed"
        if outcome.correlation_id:
            detail += f" (correlation id {outcome.correlation_id})"
        raise _UsageError(detail)
    result: dict[str, Any] = json.loads(outcome.document)
    return result


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs convert`` with ``argv`` (the arguments after ``convert``)."""
    args = _options(build_parser(), argv)
    try:
        targets = _check_targets(args)
        result = _convert(args)
        output = base64.b64decode(result["output_base64"], validate=True)
        _write_atomically(targets[0], output, force=args.force)
        if args.report:
            report = json.dumps(result["report"], indent=2, ensure_ascii=False) + "\n"
            _write_atomically(targets[1], report.encode("utf-8"), force=args.force)
    except _UsageError as exc:
        print(f"{_PROG}: {_safe(str(exc))}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"{_PROG}: {_safe(f'cannot write the output: {exc.strerror or exc}')}", file=sys.stderr
        )
        return 2
    if args.format == "json":
        sys.stdout.write(_summary_json(result, args.output))
    else:
        sys.stdout.write(_summary_text(result, args.input, args.output))
    return 1 if _failed(result, args.fail_on) else 0
