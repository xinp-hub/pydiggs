"""Preview of the pyDIGGS read API (diggs-model 1.0-beta).

Everything here may change before pyDIGGS 1.7.0 freezes diggs-model 1.0; it is outside
the library's stability promise until then. From 1.7.0 the same names are also
top-level (``pydiggs.read``), and this module stays as an alias until 2.0.

Examples:
    >>> from pydiggs import preview
    >>> m = preview.read("tests/fixtures/official/2.6/CPT_and_PorePressureDissipation.xml")
    >>> [f.kind for f in m.features][:1]
    ['sounding']
    >>> preview.Model.from_json(m.to_json()) == m
    True
"""

from __future__ import annotations

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
