"""The universal stdio sidecar: ``pydiggs serve --stdio`` (core, stdlib only).

JSON-RPC 2.0 over NDJSON on stdin/stdout, for any host that can spawn a process and
pipe its stdio (desktop and native applications in any language). The protocol is
specified in ``docs/sidecar.md`` and described by the bundled ``openrpc.json``.
Never imports :mod:`multiprocessing` or any extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydiggs._serve_cli import ServeOptions

__all__ = ["serve_stdio"]


def serve_stdio(options: ServeOptions) -> int:
    """Serve JSON-RPC requests on stdin/stdout until shutdown, EOF or a signal.

    stdout carries only protocol frames: at start the process keeps a private copy
    of its stdout descriptor for frames and points descriptor 1 (and
    ``sys.stdout``) at stderr. Logs go to stderr. The host must drain or redirect
    stderr.

    Args:
        options: Launch limits, ``--allow-path`` roots and logging settings (logging
            is already configured by ``pydiggs serve``).

    Returns:
        The exit status: 0 after shutdown and exit, EOF, a broken pipe or a signal;
        1 for ``exit`` without ``shutdown``; 2 for bad launch arguments (such as a
        refused ``--allow-path`` root); 3 when the bundled data cannot be loaded.

    Example:
        A whole session from a shell, one request per line and one response per
        line (``initialize``, ``shutdown``, then the ``exit`` notification)::

            $ pydiggs serve --stdio < session.ndjson
            {"jsonrpc":"2.0","id":1,"result":{"protocol_version":"1.0",...}}
            {"jsonrpc":"2.0","id":2,"result":null}
    """
    from pydiggs.sidecar._loop import serve  # noqa: PLC0415  # keeps import pydiggs.sidecar light

    return serve(options)
