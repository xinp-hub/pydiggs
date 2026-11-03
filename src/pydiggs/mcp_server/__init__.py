"""The MCP server (``pip install 'pydiggs[mcp]'``), run by ``pydiggs serve --mcp``.

Importing this package without the ``mcp`` extra raises :class:`ImportError` naming
the extra; any other import error propagates unchanged.
"""

from __future__ import annotations

from pydiggs.mcp_server._run import serve_mcp

try:
    import mcp  # noqa: F401
except ModuleNotFoundError as exc:
    if (exc.name or "").partition(".")[0] != "mcp":
        raise
    raise ImportError(
        "pydiggs.mcp_server requires the 'mcp' extra: pip install 'pydiggs[mcp]'",
        name=exc.name,
    ) from exc

__all__ = ["serve_mcp"]
