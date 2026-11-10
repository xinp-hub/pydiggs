"""Deprecated alias of the read API, kept for code written against pyDIGGS 1.6.

pyDIGGS 1.6.0 shipped the read API here as a preview. From 1.7.0 the same names are
top-level and stable (``pydiggs.read``, ``pydiggs.Model`` …) and this module only
re-exports them. Importing it warns; it will be removed in pyDIGGS 2.0.

Examples:
    >>> import pydiggs
    >>> m = pydiggs.read("tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml")
    >>> [f.kind for f in m.features][:1]
    ['sounding']
"""

from __future__ import annotations

import warnings

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

warnings.warn(
    "pydiggs.preview is deprecated and will be removed in pyDIGGS 2.0; use pydiggs.read,"
    " pydiggs.read_bytes and pydiggs.Model instead",
    DeprecationWarning,
    stacklevel=2,
)
