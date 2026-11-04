"""``serve_mcp``: the MCP server on stdio (``pydiggs serve --mcp``).

The same descriptor rule as the stdio sidecar: protocol frames go to a private copy
of fd 1, and fd 1 itself (with ``sys.stdout``) points at stderr before anything else
starts, so a stray ``print``, a C-level write or a worker process can never write
into the frame stream. Logs go to stderr.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal

import anyio
from mcp.server.stdio import stdio_server

from pydiggs.mcp_server._tools import create_server

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from pydiggs._serve_cli import ServeOptions

__all__ = ["serve_mcp"]

_log = logging.getLogger("pydiggs.mcp")


@contextlib.contextmanager
def _claimed_stdout() -> Iterator[int]:
    """Move frames to a private copy of fd 1 and point fd 1 (and ``sys.stdout``) at stderr.

    Restored on exit. Raises ``OSError`` (or ``ValueError``/``AttributeError`` for a
    closed or missing stream) when stdout cannot be claimed.
    """
    sys.stdout.flush()
    frames_fd = os.dup(1)  # non-inheritable (PEP 446): worker processes never get it
    try:
        os.dup2(2, 1)
    except OSError:
        os.close(frames_fd)
        raise
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import msvcrt  # noqa: PLC0415  # Windows only

        msvcrt.setmode(frames_fd, os.O_BINARY)  # type: ignore[attr-defined,unused-ignore]
    try:
        yield frames_fd
    finally:
        sys.stdout = old_stdout
        with contextlib.suppress(OSError):
            os.dup2(frames_fd, 1)
        os.close(frames_fd)


async def _serve(server: MCPServer[Any], frames_fd: int) -> None:
    """Serve one stdio connection until stdin closes."""
    raw = open(frames_fd, "wb", buffering=0, closefd=False)  # noqa: SIM115  # outlives the loop
    # newline="\n": one frame per line on every OS (no \r\n translation on Windows).
    text = io.TextIOWrapper(raw, encoding="utf-8", newline="\n", write_through=True)
    stdout = anyio.wrap_file(text)
    lowlevel = server._lowlevel_server  # the SDK's own run_stdio_async does this
    # stdin is claimed by the SDK (fd 0 then reads EOF for anything else).
    async with stdio_server(stdout=stdout) as (read_stream, write_stream):
        await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())


def _sdk_log_level(level: str) -> Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
    upper = level.upper()
    if upper == "DEBUG":
        return "DEBUG"
    if upper == "INFO":
        return "INFO"
    if upper == "ERROR":
        return "ERROR"
    if upper == "CRITICAL":
        return "CRITICAL"
    return "WARNING"


def _broken_pipe(exc: BaseException) -> bool:
    if isinstance(exc, BrokenPipeError | ConnectionResetError):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return all(_broken_pipe(e) for e in exc.exceptions)
    return False


def serve_mcp(options: ServeOptions) -> int:
    """Serve MCP tools on stdin/stdout until the client disconnects.

    Args:
        options: ``max_bytes`` (decoded content), ``max_findings``, ``timeout`` (a
            hard per-call limit) and the logging settings.

    Returns:
        0 when the client closes stdin, the pipe breaks or the process is
        interrupted; 2 when stdin and stdout are not usable.
    """
    server = create_server(
        max_bytes=options.max_bytes,
        max_findings=options.max_findings,
        timeout=options.timeout,
        log_level=_sdk_log_level(options.log_level),
    )
    with contextlib.ExitStack() as stack:
        try:
            frames_fd = stack.enter_context(_claimed_stdout())
        except (OSError, AttributeError, ValueError):
            print("pydiggs serve: stdin and stdout must be open (pipes)", file=sys.stderr)
            return 2
        _log.info("MCP server ready on stdio")
        try:
            anyio.run(_serve, server, frames_fd)
        except KeyboardInterrupt:
            return 0
        except BaseException as exc:
            if _broken_pipe(exc):
                return 0
            raise
    return 0
