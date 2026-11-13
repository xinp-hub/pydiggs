"""The MCP server (``pip install 'pydiggs[mcp]'``), run by ``pydiggs serve --mcp``.

:func:`create_server` builds an MCP server with four read-only tools
(``validate_diggs``, ``explain_rule``, ``list_rules`` and ``supported_versions``) on
the official MCP Python SDK, plus the read tools and the ``pydiggs://capabilities``
resource (see :mod:`pydiggs.mcp_server._tools`). ``serve_mcp`` runs it on stdin/stdout.

Importing this package without the ``mcp`` extra raises :class:`ImportError` naming
the extra; any other import error propagates unchanged.
"""

from __future__ import annotations

try:
    import mcp  # noqa: F401
except ModuleNotFoundError as exc:
    if (exc.name or "").partition(".")[0] != "mcp":
        raise
    raise ImportError(
        "pydiggs.mcp_server requires the 'mcp' extra: pip install 'pydiggs[mcp]'",
        name=exc.name,
    ) from exc

from pydiggs.mcp_server._run import serve_mcp
from pydiggs.mcp_server._tools import create_server

__all__ = ["create_server", "serve_mcp"]
