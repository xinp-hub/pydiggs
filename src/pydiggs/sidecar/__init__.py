"""The universal stdio sidecar: ``pydiggs serve --stdio`` (core, stdlib only).

JSON-RPC 2.0 over NDJSON on stdin/stdout, for any host that can spawn a process.
Never imports :mod:`multiprocessing` or any extra.
"""

from __future__ import annotations

import sys

from pydiggs._serve_cli import ServeOptions

__all__ = ["serve_stdio"]


def serve_stdio(options: ServeOptions) -> int:
    """Serve JSON-RPC requests on stdin/stdout until shutdown, EOF or a signal.

    Not implemented yet in this build: it reports that and returns 2.

    Args:
        options: Launch limits, ``--allow-path`` roots and logging settings.

    Returns:
        The exit status: 0 after shutdown and exit, EOF, a broken pipe or a signal;
        1 for ``exit`` without ``shutdown``; 2 for bad launch arguments; 3 when the
        bundled data cannot be loaded.
    """
    del options
    print("pydiggs serve --stdio: not implemented in this build", file=sys.stderr)
    return 2
