"""An open, reproducible test of how software reads and writes DIGGS 3.

**Preview** in pyDIGGS 1.15: the classes, the checks and the evidence document may still
change before they are frozen. The suite has its own version, :data:`SUITE_VERSION`,
which changes whenever a file could be graded differently.

A vendor runs it on their own machine, with no account and nothing uploaded:

1. :func:`new` makes a *challenge* for a *class* such as ``spt-export``. An export
   challenge is a set of plain CSV tables to load into the software; an import challenge
   is a DIGGS file to open, with questions to answer from what the software shows.
   Every challenge is generated from a seed, so no two need be alike and software cannot
   be tuned to one file.
2. :func:`grade` grades the DIGGS file the software wrote (or the answers). An export is
   checked in stages: it is DIGGS 3, it is valid (schema, dictionaries, business rules),
   and every value of the source tables is in it at its depth, compared in SI units
   within the precision the source gives. The answer key is built from the source
   tables alone, so pyDIGGS's own converter and reader are tested by the suite too.
3. The :class:`Result` is evidence anyone can re-run with :func:`verify`: the same
   challenge and the same file give the same document on any machine.

:func:`families` lists every part of DIGGS 3.0.0, including the parts the suite cannot
test yet, and :func:`scorecard` shows a product against all of them, so a few passing
classes are never mistaken for full DIGGS support.

Two more preview tools: :func:`accept` checks one delivered file against the published
requirements of a family (:func:`requirements`), for the owner who receives it, and
:func:`matrix` lays verified evidence of several products out class by class.

Two products are tested together with a *pairwise* import challenge:
``new("spt-import", ..., from_export=evidence)`` hands the importing product another
product's graded export instead of the suite's own file. Its evidence names that export
by digest (:class:`SourceExport`), and :func:`matrix`, given that evidence beside the
exporter's own, says ``read`` or ``not-read`` for the pair.

A result is *self-tested*: it shows that a file passed, not which software wrote it, and
it is not a certification by the DIGGS committee or the ASCE Geo-Institute. The evidence
says so in its ``statement``.

``import pydiggs`` does not import this module.

Examples:
    >>> from pydiggs import conformance
    >>> challenge = conformance.new("spt-export", product="MyLog", version="4.2.1", seed="demo")
    >>> challenge.info.title, len(challenge.token)
    ('Standard penetration and other driven tests: export', 6)
    >>> conformance.SUITE_VERSION, conformance.CONFORMANCE_SCHEMA_VERSION
    ('1.0-beta.3', '1.0-beta.3')
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from pydiggs.conformance._accept import (
    ACCEPTANCE_SCHEMA_RESOURCE,
    ACCEPTANCE_SCHEMA_URL,
    ACCEPTANCE_SCHEMA_VERSION,
    ACCEPTANCE_STATEMENT,
    Acceptance,
    Outcome,
    accept,
    acceptance_families,
    requirements,
)
from pydiggs.conformance._catalogue import ClassInfo, Family, class_info, classes, families
from pydiggs.conformance._challenge import (
    GRADED_SUITES,
    SUITE_VERSION,
    Challenge,
    SourceExport,
    load,
    new,
)
from pydiggs.conformance._facts import DEFECTS
from pydiggs.conformance._grade import (
    CONFORMANCE_SCHEMA_URL,
    CONFORMANCE_SCHEMA_VERSION,
    SCHEMA_RESOURCE,
    STATEMENT,
    Item,
    Result,
    Stage,
    Verification,
    grade,
    verify,
)
from pydiggs.conformance._matrix import (
    MATRIX_SCHEMA_RESOURCE,
    MATRIX_SCHEMA_URL,
    MATRIX_SCHEMA_VERSION,
    Entry,
    Matrix,
    RoundTrip,
    matrix,
)
from pydiggs.conformance._report import Scorecard, ScorecardRow, scorecard
from pydiggs.conformance._requirements import Requirement

__all__ = [
    "ACCEPTANCE_SCHEMA_URL",
    "ACCEPTANCE_SCHEMA_VERSION",
    "ACCEPTANCE_STATEMENT",
    "CONFORMANCE_SCHEMA_URL",
    "CONFORMANCE_SCHEMA_VERSION",
    "DEFECTS",
    "GRADED_SUITES",
    "MATRIX_SCHEMA_URL",
    "MATRIX_SCHEMA_VERSION",
    "STATEMENT",
    "SUITE_VERSION",
    "Acceptance",
    "Challenge",
    "ClassInfo",
    "Entry",
    "Family",
    "Item",
    "Matrix",
    "Outcome",
    "Requirement",
    "Result",
    "RoundTrip",
    "Scorecard",
    "ScorecardRow",
    "SourceExport",
    "Stage",
    "Verification",
    "accept",
    "acceptance_families",
    "class_info",
    "classes",
    "conformance_schema",
    "families",
    "grade",
    "load",
    "matrix",
    "new",
    "requirements",
    "scorecard",
    "verify",
]


_SCHEMAS = {
    "evidence": SCHEMA_RESOURCE,
    "acceptance": ACCEPTANCE_SCHEMA_RESOURCE,
    "matrix": MATRIX_SCHEMA_RESOURCE,
}


def conformance_schema(document: str = "evidence") -> dict[str, Any]:
    """A packaged JSON Schema of the suite, as a fresh dict.

    Args:
        document: ``evidence`` (diggs-conformance, the default), ``acceptance``
            (diggs-conformance-acceptance, what :func:`accept` writes) or ``matrix``
            (diggs-conformance-matrix, what :func:`matrix` writes).

    Raises:
        ValueError: ``document`` is none of these.

    Examples:
        >>> from pydiggs import conformance
        >>> conformance.conformance_schema()["$id"] == conformance.CONFORMANCE_SCHEMA_URL
        True
        >>> conformance.conformance_schema("matrix")["$id"] == conformance.MATRIX_SCHEMA_URL
        True
    """
    if document not in _SCHEMAS:
        raise ValueError(f"no conformance schema {document!r}; they are: {', '.join(_SCHEMAS)}")
    resource = _SCHEMAS[document]
    text = resources.files("pydiggs.conformance").joinpath(resource).read_text("utf-8")
    return dict(json.loads(text))
