"""AGS4 and DIGGS conversion, both ways, with every loss listed.

:func:`ags4_to_diggs` converts an AGS4 file to a DIGGS 3.0.0 document;
:func:`diggs_to_ags4` and :func:`model_to_ags4` convert a DIGGS document, or a
:class:`pydiggs.Model` in memory, to an AGS4 file (4.2 by default, or 4.1.1). Both
directions use one bundled mapping table (:data:`MAPPING_VERSION`, see
:func:`mapping_info` and the generated mapping page), and every value of the source is
either carried into the output or named by a ``convert`` finding in the
:class:`Conversion`'s report. Nothing is fetched from the network and nothing a file
names is opened.

Examples:
    >>> from pydiggs import ags4, convert
    >>> c = convert.ags4_to_diggs("tests/fixtures/ags4/convert_tier_a.ags")
    >>> c.validation.ok, c.mapping_version
    (True, '1.0')
    >>> back = convert.model_to_ags4(c.model)
    >>> back.validation.ok, back.ags4.version
    (True, '4.2')
"""

from __future__ import annotations

from pydiggs.convert._api import (
    MAPPING_VERSION,
    Conversion,
    ags4_to_diggs,
    diggs_to_ags4,
    mapping_info,
    model_to_ags4,
)
from pydiggs.convert._to_model import Origin

__all__ = [
    "MAPPING_VERSION",
    "Conversion",
    "Origin",
    "ags4_to_diggs",
    "diggs_to_ags4",
    "mapping_info",
    "model_to_ags4",
]
