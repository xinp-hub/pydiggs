"""The MCP server, run by ``pydiggs serve --mcp``: not available in this release.

This release ships no ``mcp`` extra. Importing this package without the MCP SDK
raises :class:`ImportError` saying so; any other import error propagates unchanged.
"""

from __future__ import annotations

from pydiggs.mcp_server._run import serve_mcp

try:
    import mcp  # noqa: F401
except ModuleNotFoundError as exc:
    if (exc.name or "").partition(".")[0] != "mcp":
        raise
    raise ImportError(
        "pydiggs.mcp_server is not available in this release (it needs the MCP SDK,"
        " which no pydiggs extra installs yet)",
        name=exc.name,
    ) from exc

__all__ = ["serve_mcp"]
