"""Mapping files: your own tables and columns to the DIGGS tables profile (**preview**).

A vendor who would rather not write SQL views writes one TOML file, a ``diggs-mapping``
1.0 document, that says where each of their tables and columns goes in the
:mod:`tables profile <pydiggs.tables>`: a column copied, a constant, a key built from
columns, a unit renamed, a code translated. :func:`load` reads it, :func:`check` lints it
against the profile and the DIGGS vocabularies, ``Mapping.apply`` shapes source rows into
profile tables, and :func:`scaffold` and :func:`coverage` start one and measure it.

A mapping is data. Nothing in it is evaluated, no number is changed (a unit is renamed
or reported), and every source value is used or named by a finding.

These names and the format are a preview: they may change in a minor release.

Examples:
    >>> from pydiggs import mapping, tables
    >>> loaded = mapping.load('''
    ... mapping_format = "1.0"
    ... name = "demo"
    ... version = "1"
    ... method = "manual"
    ... profile_version = "1.0-beta.1"
    ...
    ... [[tables]]
    ... source = "hole"
    ... target = "features"
    ... columns = { key = "id", name = "label", depth_uom = { value = "ft" } }
    ...
    ... [[tables]]
    ... source = "blow"
    ... target = "measurements"
    ... [tables.columns]
    ... key = { template = "{hole}-SPT-{seq}" }
    ... feature = "hole"
    ... procedure = { value = "DrivenPenetrationTest" }
    ... depth = "at_ft"
    ... [[tables.params]]
    ... name = "penetrationTestType"
    ... scope = "procedure"
    ... value = "SPT"
    ...
    ... [[tables]]
    ... source = "blow"
    ... target = "results_DrivenPenetrationTest"
    ... [tables.columns]
    ... measurement = { template = "{hole}-SPT-{seq}" }
    ... n_value = "n"
    ... ''')
    >>> mapping.check(loaded)
    ()
    >>> applied = loaded.apply(
    ...     {
    ...         "hole": [{"id": "B-7", "label": "Boring 7"}],
    ...         "blow": [{"hole": "B-7", "seq": 1, "at_ft": 5, "n": 14}],
    ...     }
    ... )
    >>> applied.findings, applied.tables["results_DrivenPenetrationTest"]
    ((), [{'measurement': 'B-7-SPT-1', 'n_value': 14}])
    >>> len(tables.to_model(applied.tables).model.measurements)
    1
"""

from __future__ import annotations

from pydiggs.errors import MappingError
from pydiggs.mapping._apply import Applied
from pydiggs.mapping._format import MAPPING_FORMAT_VERSION, Mapping, load, mapping_schema
from pydiggs.mapping._lint import check
from pydiggs.mapping._scaffold import (
    Coverage,
    columns_from_cursor,
    coverage,
    read_source,
    scaffold,
    schema_from_csv,
)

__all__ = [
    "MAPPING_FORMAT_VERSION",
    "Applied",
    "Coverage",
    "Mapping",
    "MappingError",
    "check",
    "columns_from_cursor",
    "coverage",
    "load",
    "mapping_schema",
    "read_source",
    "scaffold",
    "schema_from_csv",
]
