"""The HTTP API (``pip install 'pydiggs[server]'``).

Importing this package without the ``server`` extra raises :class:`ImportError`
naming the extra; any other import error propagates unchanged.
"""

from __future__ import annotations

from typing import Final

from pydiggs.server._run import serve_http
from pydiggs.server._settings import ServerSettings

_NEEDED: Final = frozenset({"fastapi", "starlette", "uvicorn", "multipart", "python_multipart"})

try:
    import fastapi  # noqa: F401
    import python_multipart  # noqa: F401
    import starlette  # noqa: F401
    import uvicorn  # noqa: F401
except ModuleNotFoundError as exc:
    if (exc.name or "").partition(".")[0] not in _NEEDED:
        raise
    raise ImportError(
        "pydiggs.server requires the 'server' extra: pip install 'pydiggs[server]'",
        name=exc.name,
    ) from exc

__all__ = ["ServerSettings", "serve_http"]
