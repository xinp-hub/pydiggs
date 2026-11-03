"""``serve_mcp``: the MCP server on stdio (``pydiggs serve --mcp``)."""

from __future__ import annotations

import sys

from pydiggs._serve_cli import ServeOptions

__all__ = ["serve_mcp"]


def serve_mcp(options: ServeOptions) -> int:
    """Serve MCP tools on stdin/stdout until the client disconnects.

    Not implemented yet in this build: it reports that and returns 2.

    Args:
        options: Limits (``timeout`` is a hard per-call limit) and logging settings.

    Returns:
        0 after a clean shutdown; 2 when it cannot start.
    """
    del options
    print("pydiggs serve --mcp: not implemented in this build", file=sys.stderr)
    return 2
