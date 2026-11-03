"""``pydiggs serve``: run pydiggs as a stdio sidecar, an HTTP server or an MCP server.

Stdlib only. The adapters are imported only for the mode asked for, so ``serve
--stdio`` needs no extra and a missing ``[server]`` or ``[mcp]`` extra is reported
(exit 2) only when that mode is chosen.

The three entry points have fixed signatures:

* ``pydiggs.sidecar.serve_stdio(options: ServeOptions) -> int``
* ``pydiggs.server.serve_http(settings: ServerSettings, host: str, port: int) -> int``
* ``pydiggs.mcp_server.serve_mcp(options: ServeOptions) -> int``

``serve`` never accepts a schema, dictionary or Schematron path, network access,
snippets, a base directory or local dictionaries: remote callers get the bundled
reference data only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from pydiggs.server import ServerSettings

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FINDINGS",
    "DEFAULT_MAX_PATH_BYTES",
    "DEFAULT_MCP_MAX_BYTES",
    "DEFAULT_MCP_MAX_FINDINGS",
    "DEFAULT_TIMEOUT",
    "LOG_LEVELS",
    "MAX_TIMEOUT",
    "ServeOptions",
    "build_parser",
    "configure_logging",
    "default_workers",
    "main",
]

#: Content limit for ``--stdio`` and ``--http`` (bytes).
DEFAULT_MAX_BYTES: Final = 25 * 2**20
#: Path-mode limit for ``--stdio`` (bytes).
DEFAULT_MAX_PATH_BYTES: Final = 100 * 2**20
#: Findings kept per document for ``--stdio`` and ``--http``.
DEFAULT_MAX_FINDINGS: Final = 10_000
#: Decoded content limit for ``--mcp`` (bytes).
DEFAULT_MCP_MAX_BYTES: Final = 10 * 2**20
#: Findings kept per document for ``--mcp``.
DEFAULT_MCP_MAX_FINDINGS: Final = 200
#: Seconds: hard limit for ``--http`` and ``--mcp``, default time budget for ``--stdio``.
DEFAULT_TIMEOUT: Final = 60.0
#: Largest ``--timeout``: one day (as ``validate --timeout``).
MAX_TIMEOUT: Final = 86_400
#: Accepted ``--log-level`` values.
LOG_LEVELS: Final = ("debug", "info", "warning", "error", "critical")

#: The modules each extra provides (import names), to recognise a missing extra.
_EXTRA_MODULES: Final = {
    "server": frozenset({"fastapi", "starlette", "uvicorn", "multipart", "python_multipart"}),
    "mcp": frozenset({"mcp"}),
}


def default_workers() -> int:
    """``clamp(cpu_count - 1, 1, 4)``: worker processes for ``--http`` by default.

    Example:
        >>> 1 <= default_workers() <= 4
        True
    """
    return max(1, min(4, (os.cpu_count() or 1) - 1))


def _positive_int(name: str, value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive int, got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ServeOptions:
    """Launch settings for ``serve --stdio`` and ``serve --mcp``, fixed for the process.

    ``timeout`` is the default per-request time budget for the sidecar (cooperative;
    hard limits belong to the host) and the hard per-call limit for MCP.
    ``allow_paths`` are the ``--allow-path`` roots exactly as given; the sidecar
    canonicalises and vets them. Every field is validated here.

    Example:
        >>> ServeOptions().max_bytes == 25 * 2**20
        True
        >>> ServeOptions(timeout=0)
        Traceback (most recent call last):
        ...
        ValueError: timeout must be a positive number of seconds, at most 86,400
    """

    max_bytes: int = DEFAULT_MAX_BYTES
    max_path_bytes: int = DEFAULT_MAX_PATH_BYTES
    max_findings: int = DEFAULT_MAX_FINDINGS
    timeout: float = DEFAULT_TIMEOUT
    workers: int = 1
    allow_paths: tuple[str, ...] = ()
    log_level: str = "warning"
    log_format: Literal["text", "json"] = "text"

    def __post_init__(self) -> None:
        for name in ("max_bytes", "max_path_bytes", "max_findings", "workers"):
            _positive_int(name, getattr(self, name))
        timeout = self.timeout
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise ValueError(f"timeout must be a number of seconds, got {timeout!r}")
        if not 0 < timeout <= MAX_TIMEOUT:  # also NaN and inf
            raise ValueError(
                f"timeout must be a positive number of seconds, at most {MAX_TIMEOUT:,}"
            )
        if not isinstance(self.allow_paths, tuple) or not all(
            isinstance(p, str) and p for p in self.allow_paths
        ):
            raise ValueError("allow_paths must be a tuple of non-empty strings")
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {', '.join(LOG_LEVELS)}")
        if self.log_format not in ("text", "json"):
            raise ValueError("log_format must be 'text' or 'json'")


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs serve`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pydiggs serve",
        description=(
            "Serve DIGGS validation to other programs: a JSON-RPC sidecar on stdin/stdout"
            " (--stdio), an HTTP API (--http, needs pydiggs[server]) or an MCP server"
            " (--mcp, needs pydiggs[mcp]). Only the bundled schema, dictionaries and"
            " Schematron rules are used."
        ),
        epilog=(
            "Exit status: 0 after a clean shutdown; 2 for bad arguments or a missing extra."
            " --timeout is a hard limit (process kill) for --http and --mcp, and the default"
            " per-request time budget for --stdio, where the host owns hard limits."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stdio", action="store_true", help="JSON-RPC 2.0 over NDJSON on stdio")
    mode.add_argument("--http", action="store_true", help="HTTP API (pydiggs[server])")
    mode.add_argument("--mcp", action="store_true", help="MCP server on stdio (pydiggs[mcp])")
    parser.add_argument(
        "--max-bytes",
        type=int,
        metavar="N",
        help=(
            f"largest document in bytes (default {DEFAULT_MAX_BYTES:,};"
            f" --mcp {DEFAULT_MCP_MAX_BYTES:,})"
        ),
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        metavar="N",
        help=(
            f"findings kept per document (default {DEFAULT_MAX_FINDINGS:,};"
            f" --mcp {DEFAULT_MCP_MAX_FINDINGS})"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        help=f"per-request limit in seconds (default {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--workers", type=int, metavar="N", help="--http worker processes (default: CPUs-1, 1-4)"
    )
    parser.add_argument("--host", metavar="H", help="--http bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, metavar="P", help="--http port (default 8000)")
    parser.add_argument(
        "--allow-path",
        action="append",
        default=[],
        metavar="DIR",
        help="--stdio: also accept files by absolute path under DIR (repeatable)",
    )
    parser.add_argument("--log-level", choices=LOG_LEVELS, default="warning")
    parser.add_argument("--log-format", choices=["text", "json"], default="text")
    return parser


def _check_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse options that do not belong to the chosen mode, and out-of-range values."""
    if not args.http:
        for flag, value in (("--workers", args.workers), ("--host", args.host)):
            if value is not None:
                parser.error(f"{flag} is only for --http")
        if args.port is not None:
            parser.error("--port is only for --http")
    if args.allow_path and not args.stdio:
        parser.error("--allow-path is only for --stdio")
    for flag, value in (
        ("--max-bytes", args.max_bytes),
        ("--max-findings", args.max_findings),
        ("--workers", args.workers),
    ):
        if value is not None and value < 1:
            parser.error(f"{flag} must be positive")
    if args.port is not None and not 0 < args.port < 2**16:
        parser.error("--port must be between 1 and 65535")
    if args.timeout is not None and not 0 < args.timeout <= MAX_TIMEOUT:  # also NaN, inf
        parser.error(f"--timeout must be a positive number of seconds, at most {MAX_TIMEOUT:,}")
    if any(not p for p in args.allow_path):
        parser.error("--allow-path needs a directory")


def _options(args: argparse.Namespace) -> ServeOptions:
    """``ServeOptions`` for ``--stdio`` or ``--mcp``, with that mode's defaults."""
    mcp = bool(args.mcp)
    max_bytes = args.max_bytes or (DEFAULT_MCP_MAX_BYTES if mcp else DEFAULT_MAX_BYTES)
    return ServeOptions(
        max_bytes=max_bytes,
        max_path_bytes=max(DEFAULT_MAX_PATH_BYTES, max_bytes),
        max_findings=args.max_findings
        or (DEFAULT_MCP_MAX_FINDINGS if mcp else DEFAULT_MAX_FINDINGS),
        timeout=DEFAULT_TIMEOUT if args.timeout is None else args.timeout,
        workers=1,
        allow_paths=tuple(args.allow_path),
        log_level=args.log_level,
        log_format=args.log_format,
    )


def _server_settings(cls: type[ServerSettings], args: argparse.Namespace) -> ServerSettings:
    """``ServerSettings.from_env()`` (``PYDIGGS_*``), overridden by the flags given."""
    settings = cls.from_env(isolation="process")
    changes: dict[str, Any] = {}
    if args.max_bytes is not None:
        changes["max_body_bytes"] = args.max_bytes
    if args.max_findings is not None:
        changes["max_findings"] = args.max_findings
    if args.timeout is not None:
        changes["timeout_s"] = args.timeout
    if args.workers is not None:
        changes["workers"] = args.workers
        if "PYDIGGS_QUEUE_DEPTH" not in os.environ:
            changes["queue_depth"] = None  # follow the new worker count
    return replace(settings, **changes) if changes else settings


def _missing_extra(exc: ImportError, extra: str) -> bool:
    name = exc.name or ""
    return name.partition(".")[0] in _EXTRA_MODULES[extra]


def _extra_message(extra: str, exc: ImportError) -> str:
    text = str(exc)
    if f"'{extra}' extra" in text:
        return text
    module = "pydiggs.server" if extra == "server" else "pydiggs.mcp_server"
    return f"{module} requires the '{extra}' extra: pip install 'pydiggs[{extra}]'"


def _escape(text: str) -> str:
    """Make ``text`` one printable line: control and format characters as escapes."""
    return "".join(
        c if c.isprintable() or c == " " else c.encode("unicode_escape").decode("ascii")
        for c in text
    )


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        request_id = getattr(record, "request_id", None)
        suffix = f" request_id={_escape(str(request_id))}" if request_id is not None else ""
        return f"{stamp}Z {record.levelname.lower()} {_escape(record.getMessage())}{suffix}"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        request_id = getattr(record, "request_id", None)
        return json.dumps(
            {
                "ts": f"{stamp}.{int(record.msecs):03d}Z",
                "level": record.levelname.lower(),
                "msg": record.getMessage(),
                "request_id": None if request_id is None else str(request_id),
            },
            ensure_ascii=True,
        )


def configure_logging(level: str, fmt: Literal["text", "json"]) -> logging.Handler:
    """Send the ``pydiggs`` loggers to stderr, one line per record.

    ``json`` lines carry ``ts``, ``level``, ``msg`` and ``request_id`` (from
    ``extra={"request_id": ...}``). Text lines escape control characters, so a client
    string cannot forge a log line. stdout is never used: it may carry frames.

    Args:
        level: One of :data:`LOG_LEVELS`.
        fmt: ``text`` or ``json``.

    Returns:
        The installed handler (replacing one installed earlier by this function).

    Example:
        >>> import logging
        >>> handler = configure_logging("error", "json")
        >>> logging.getLogger("pydiggs").level == logging.ERROR
        True
    """
    logger = logging.getLogger("pydiggs")
    for old in [h for h in logger.handlers if getattr(h, "_pydiggs_serve", False)]:
        logger.removeHandler(old)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _TextFormatter())
    handler._pydiggs_serve = True  # type: ignore[attr-defined]  # marks our own handler
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return handler


def main(argv: Sequence[str]) -> int:
    """Run ``pydiggs serve`` with ``argv`` (the arguments after ``serve``)."""
    parser = build_parser()
    args = parser.parse_args(list(argv))
    _check_args(parser, args)
    configure_logging(args.log_level, args.log_format)
    if args.stdio:
        from pydiggs.sidecar import serve_stdio  # noqa: PLC0415  # only for this mode

        return serve_stdio(_options(args))
    extra = "mcp" if args.mcp else "server"
    try:
        if args.mcp:
            from pydiggs.mcp_server import serve_mcp  # noqa: PLC0415  # needs [mcp]
        else:
            from pydiggs.server import ServerSettings, serve_http  # noqa: PLC0415  # [server]
    except ImportError as exc:
        if not _missing_extra(exc, extra):
            raise
        print(f"pydiggs serve: {_extra_message(extra, exc)}", file=sys.stderr)
        return 2
    if args.mcp:
        return serve_mcp(_options(args))
    try:
        settings = _server_settings(ServerSettings, args)
    except ValueError as exc:
        print(f"pydiggs serve: {exc}", file=sys.stderr)
        return 2
    return serve_http(settings, args.host or "127.0.0.1", 8000 if args.port is None else args.port)
