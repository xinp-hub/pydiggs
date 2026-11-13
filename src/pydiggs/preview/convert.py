"""Preview: AGS4 to DIGGS conversion with every loss listed (pyDIGGS 1.9).

**Outside Semantic Versioning.** Names, output and the mapping table may change in any
release; the stable API arrives in ``pydiggs.convert`` with pyDIGGS 1.10, when the owner
signs off mapping table 1.0. Every group mapping in this preview is a draft; see
`AGS4 to DIGGS mapping <https://xinp-hub.github.io/pydiggs/convert-mapping/>`_.

The converter writes DIGGS 3.0.0. It covers the Tier A groups (PROJ, TRAN, the
ABBR/UNIT/TYPE/DICT lookups, LOCA, GEOL, SAMP, ISPT, LLPL, GRAG with GRAT, LNMC); any
other group is reported and not converted. Every non-null source value is either carried
into the output or named by a ``convert`` finding.

Examples:
    >>> from pydiggs.preview import convert
    >>> c = convert.ags4_to_diggs("tests/fixtures/ags4/convert_tier_a.ags")
    >>> c.complete, c.validation.ok, c.mapping_version
    (True, True, '0.1')
    >>> [f.kind for f in c.model.features]
    ['borehole', 'borehole', 'trial_pit']
"""

from __future__ import annotations

from pydiggs.convert._api import MAPPING_VERSION, Conversion, ags4_to_diggs, mapping_info
from pydiggs.convert._to_model import Origin

__all__ = ["MAPPING_VERSION", "Conversion", "Origin", "ags4_to_diggs", "mapping_info"]
