"""The HTTP API (``pip install 'pydiggs[server]'``).

:func:`create_app` is the complete application (what ``pydiggs serve --http`` runs);
:func:`create_router` is the ``/v1`` router to mount in your own FastAPI app;
:class:`ServerSettings` holds the limits and switches. See the HTTP API page of the
documentation for the endpoints, problem codes and deployment notes.

Importing this package without the ``server`` extra raises :class:`ImportError`
naming the extra; any other import error propagates unchanged.

Example:
    >>> from pydiggs.server import ServerSettings, create_app
    >>> app = create_app(ServerSettings(workers=1))
"""

from __future__ import annotations

from typing import Final

_NEEDED: Final = frozenset({"fastapi", "starlette", "uvicorn", "multipart", "python_multipart"})

try:
    import fastapi  # noqa: F401
    import python_multipart  # noqa: F401
    import starlette  # noqa: F401
    import uvicorn  # noqa: F401

    from pydiggs.server._app import create_app, create_router
    from pydiggs.server._run import serve_http
    from pydiggs.server._settings import ServerSettings
except ModuleNotFoundError as exc:
    if (exc.name or "").partition(".")[0] not in _NEEDED:
        raise
    raise ImportError(
        "pydiggs.server requires the 'server' extra: pip install 'pydiggs[server]'",
        name=exc.name,
    ) from exc

__all__ = ["ServerSettings", "create_app", "create_router", "serve_http"]
