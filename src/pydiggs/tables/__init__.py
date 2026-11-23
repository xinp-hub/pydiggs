"""Tables in, DIGGS out, and back: the DIGGS tables profile (**preview**).

A software vendor shapes their own database into the tables of the profile (SQL views
are enough), and pyDIGGS does the rest: :func:`to_model` builds a model that
:func:`pydiggs.write_bytes` writes as DIGGS 3.0.0, and :func:`from_model` turns what
:func:`pydiggs.read` returns into tables with a load plan. Every row and value that could
not be carried is named by a finding; no value is ever converted to another unit.

The profile (:data:`PROFILE_VERSION`, :func:`profile`) and these names are a preview:
they may change in a minor release until the profile reaches 1.0.

Examples:
    >>> import pydiggs
    >>> from pydiggs import tables
    >>> result = tables.to_model(
    ...     {
    ...         "features": [{"key": "BH-1", "name": "BH-1", "depth_uom": "m"}],
    ...         "measurements": [
    ...             {
    ...                 "key": "SPT-1",
    ...                 "feature": "BH-1",
    ...                 "procedure": "DrivenPenetrationTest",
    ...                 "depth": 3,
    ...             }
    ...         ],
    ...         "params": [
    ...             {
    ...                 "node": "SPT-1",
    ...                 "scope": "procedure",
    ...                 "name": "penetrationTestType",
    ...                 "value": "SPT",
    ...             }
    ...         ],
    ...         "results_DrivenPenetrationTest": [{"measurement": "SPT-1", "n_value": 21}],
    ...     }
    ... )
    >>> xml = pydiggs.write_bytes(result.model)
    >>> back = tables.from_model(pydiggs.read_bytes(xml))
    >>> back.rows("results_DrivenPenetrationTest")
    [{'measurement': 'SPT-1', 'result': 0, 'row': 0, 'n_value': 21.0}]
"""

from __future__ import annotations

from pydiggs.errors import TablesError
from pydiggs.tables._check import check
from pydiggs.tables._from_model import LoadPlan, LoadStep, TableSet, from_model
from pydiggs.tables._io import read_package, rows_from_cursor, write_package
from pydiggs.tables._profile import PROFILE_VERSION, profile
from pydiggs.tables._round_trip import Difference, RoundTrip, round_trip
from pydiggs.tables._to_model import TablesResult, to_model

__all__ = [
    "PROFILE_VERSION",
    "Difference",
    "LoadPlan",
    "LoadStep",
    "RoundTrip",
    "TableSet",
    "TablesError",
    "TablesResult",
    "check",
    "from_model",
    "profile",
    "read_package",
    "round_trip",
    "rows_from_cursor",
    "to_model",
    "write_package",
]
