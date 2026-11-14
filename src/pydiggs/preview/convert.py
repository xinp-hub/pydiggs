"""Deprecated: the pyDIGGS 1.9 preview of AGS4 to DIGGS conversion.

From pyDIGGS 1.10 the conversion API is stable in :mod:`pydiggs.convert`, in both
directions. The names here (``ags4_to_diggs``, ``Conversion``, ``Origin``,
``mapping_info``, ``MAPPING_VERSION``) are the same objects as there; using one of them
from here warns with a ``DeprecationWarning``, and they will be removed in pyDIGGS 2.0.
Importing this module does not warn.

Examples:
    >>> import warnings
    >>> from pydiggs import convert
    >>> from pydiggs.preview import convert as preview_convert
    >>> with warnings.catch_warnings(record=True) as caught:
    ...     warnings.simplefilter("always")
    ...     preview_convert.ags4_to_diggs is convert.ags4_to_diggs
    True
    >>> caught[0].category.__name__
    'DeprecationWarning'
"""

from __future__ import annotations

import warnings
from typing import Any

from pydiggs.convert import MAPPING_VERSION, Conversion, Origin, ags4_to_diggs, mapping_info

__all__ = ["MAPPING_VERSION", "Conversion", "Origin", "ags4_to_diggs", "mapping_info"]

_DEPRECATED: dict[str, Any] = {
    "MAPPING_VERSION": MAPPING_VERSION,
    "Conversion": Conversion,
    "Origin": Origin,
    "ags4_to_diggs": ags4_to_diggs,
    "mapping_info": mapping_info,
}
# The names are served by __getattr__, so that using one warns.
del MAPPING_VERSION, Conversion, Origin, ags4_to_diggs, mapping_info


def __getattr__(name: str) -> Any:
    """Serve a name of the 1.9 preview, with a ``DeprecationWarning``."""
    if name in _DEPRECATED:
        warnings.warn(
            "pydiggs.preview.convert is deprecated and will be removed in pyDIGGS 2.0; use"
            f" pydiggs.convert.{name} instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return _DEPRECATED[name]
    raise AttributeError(f"module 'pydiggs.preview.convert' has no attribute {name!r}")


def __dir__() -> list[str]:
    """The module's names and the deprecated ones, so ``dir()`` lists them until 2.0."""
    return sorted({*globals(), *_DEPRECATED})
