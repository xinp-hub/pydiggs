"""Previews of new APIs, outside Semantic Versioning, and the deprecated 1.6 read API.

``pydiggs.preview.convert`` is the pyDIGGS 1.9 preview of AGS4 to DIGGS conversion.

The other names here are a deprecated alias of the read API, kept for code written
against pyDIGGS 1.6, which shipped the read API here as a preview. From 1.7.0 the same
names are top-level and stable (``pydiggs.read``, ``pydiggs.Model`` …). Using one of them
from here warns; they will be removed in pyDIGGS 2.0. (Up to 1.8 importing this module
warned; from 1.9 using a deprecated name does, so importing ``pydiggs.preview.convert``
does not.)

Examples:
    >>> import warnings
    >>> import pydiggs
    >>> from pydiggs import preview
    >>> with warnings.catch_warnings(record=True) as caught:
    ...     warnings.simplefilter("always")
    ...     preview.read is pydiggs.read
    True
    >>> caught[0].category.__name__
    'DeprecationWarning'
"""

from __future__ import annotations

import warnings
from typing import Any

from pydiggs._reader import read, read_bytes
from pydiggs.errors import DocumentError
from pydiggs.model import MODEL_SCHEMA_URL, MODEL_SCHEMA_VERSION, Model, model_schema

__all__ = [
    "MODEL_SCHEMA_URL",
    "MODEL_SCHEMA_VERSION",
    "DocumentError",
    "Model",
    "model_schema",
    "read",
    "read_bytes",
]

_DEPRECATED: dict[str, Any] = {
    "MODEL_SCHEMA_URL": MODEL_SCHEMA_URL,
    "MODEL_SCHEMA_VERSION": MODEL_SCHEMA_VERSION,
    "DocumentError": DocumentError,
    "Model": Model,
    "model_schema": model_schema,
    "read": read,
    "read_bytes": read_bytes,
}
# The deprecated names are served by __getattr__, so that using one warns.
del MODEL_SCHEMA_URL, MODEL_SCHEMA_VERSION, DocumentError, Model, model_schema, read, read_bytes


def __getattr__(name: str) -> Any:
    """Serve a deprecated name of the 1.6 read API, with a ``DeprecationWarning``."""
    if name in _DEPRECATED:
        warnings.warn(
            "pydiggs.preview is deprecated and will be removed in pyDIGGS 2.0; use"
            " pydiggs.read, pydiggs.read_bytes and pydiggs.Model instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return _DEPRECATED[name]
    raise AttributeError(f"module 'pydiggs.preview' has no attribute {name!r}")
