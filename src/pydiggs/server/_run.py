"""``serve_http``: run the HTTP API under uvicorn (``pydiggs serve --http``)."""

from __future__ import annotations

import dataclasses
import logging
import math
import sys
from typing import Final

import uvicorn

from pydiggs.server._app import create_app
from pydiggs.server._settings import ServerSettings

__all__ = ["serve_http"]

#: Connections uvicorn accepts beyond the admission slots (probes, rejections).
_SPARE_CONNECTIONS: Final = 4
#: Largest request line plus headers, in bytes.
_MAX_HEADER_BYTES: Final = 16 * 2**10
_KEEP_ALIVE_S: Final = 5
#: Seconds beyond ``timeout_s`` that a shutdown waits for requests in flight.
_SHUTDOWN_GRACE_S: Final = 5

_log = logging.getLogger("pydiggs.server")


def uvicorn_config(settings: ServerSettings, host: str, port: int) -> uvicorn.Config:
    """The uvicorn configuration :func:`serve_http` runs, with explicit limits.

    ``isolation`` None becomes ``"process"``; ``allowed_hosts`` None becomes the
    loopback names for a loopback ``host``.

    Raises:
        ValueError: ``host`` is not loopback and ``allowed_hosts`` is unset.

    Example:
        >>> from pydiggs.server import ServerSettings
        >>> config = uvicorn_config(ServerSettings(workers=1), "127.0.0.1", 8000)
        >>> config.limit_concurrency, config.access_log, config.server_header
        (7, False, False)
    """
    hosts = settings.hosts_for(host)
    settings = dataclasses.replace(
        settings, allowed_hosts=hosts, isolation=settings.isolation or "process"
    )
    return uvicorn.Config(
        create_app(settings),
        host=host,
        port=port,
        http="h11",
        ws="none",
        lifespan="on",
        log_config=None,
        access_log=False,  # query strings carry file names
        server_header=False,
        proxy_headers=False,
        limit_concurrency=settings.admission_slots + _SPARE_CONNECTIONS,
        timeout_keep_alive=_KEEP_ALIVE_S,
        h11_max_incomplete_event_size=_MAX_HEADER_BYTES,
        timeout_graceful_shutdown=math.ceil(settings.timeout_s) + _SHUTDOWN_GRACE_S,
    )


def serve_http(settings: ServerSettings, host: str, port: int) -> int:
    """Serve the HTTP API on ``host:port`` until stopped; return the exit status.

    Runs :func:`~pydiggs.server.create_app` under uvicorn with HTTP/1.1 only, no
    access log, no ``Server`` header, a 16 KiB header limit, a 5 s keep-alive, a
    connection limit of the admission slots plus 4, and a graceful shutdown that waits
    at most ``timeout_s`` plus 5 s for requests in flight before cancelling them.

    Args:
        settings: Limits and switches (``isolation`` None means ``"process"``).
        host: Bind address.
        port: TCP port.

    Returns:
        0 after a clean shutdown; 2 when it cannot start (bad settings, port in use).

    Example:
        >>> from pydiggs.server import ServerSettings, serve_http
        >>> serve_http(ServerSettings(), "0.0.0.0", 8000)  # doctest: +SKIP
        2
    """
    try:
        config = uvicorn_config(settings, host, port)
    except ValueError as exc:
        print(f"pydiggs serve: {exc}", file=sys.stderr)
        return 2
    if settings.isolation == "thread":
        _log.warning("serving with isolation='thread': no hard timeout")
    server = uvicorn.Server(config)
    try:
        server.run()
    except SystemExit as exc:  # uvicorn exits when it cannot bind or start
        return 0 if exc.code in (None, 0) else 2
    except KeyboardInterrupt:  # uvicorn re-raises the SIGINT it handled
        return 0
    return 0 if server.started else 2
