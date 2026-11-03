"""``serve_http``: run the HTTP API under uvicorn (``pydiggs serve --http``)."""

from __future__ import annotations

import sys

from pydiggs.server._settings import ServerSettings

__all__ = ["serve_http"]


def serve_http(settings: ServerSettings, host: str, port: int) -> int:
    """Serve the HTTP API on ``host:port`` until stopped; return the exit status.

    Not implemented yet in this build: it reports that and returns 2.

    Args:
        settings: Limits and switches (``isolation`` None means ``"process"``).
        host: Bind address.
        port: TCP port.

    Returns:
        0 after a clean shutdown; 2 when it cannot start.
    """
    del settings, host, port
    print("pydiggs serve --http: not implemented in this build", file=sys.stderr)
    return 2
