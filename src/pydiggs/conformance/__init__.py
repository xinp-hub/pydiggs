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
    ('1.0-beta.2', '1.0-beta.1')
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from pydiggs.conformance._catalogue import ClassInfo, Family, class_info, classes, families
from pydiggs.conformance._challenge import SUITE_VERSION, Challenge, load, new
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
from pydiggs.conformance._report import Scorecard, ScorecardRow, scorecard

__all__ = [
    "CONFORMANCE_SCHEMA_URL",
    "CONFORMANCE_SCHEMA_VERSION",
    "DEFECTS",
    "STATEMENT",
    "SUITE_VERSION",
    "Challenge",
    "ClassInfo",
    "Family",
    "Item",
    "Result",
    "Scorecard",
    "ScorecardRow",
    "Stage",
    "Verification",
    "class_info",
    "classes",
    "conformance_schema",
    "families",
    "grade",
    "load",
    "new",
    "scorecard",
    "verify",
]


def conformance_schema() -> dict[str, Any]:
    """The packaged diggs-conformance JSON Schema, as a fresh dict.

    Examples:
        >>> from pydiggs import conformance
        >>> conformance.conformance_schema()["$id"] == conformance.CONFORMANCE_SCHEMA_URL
        True
    """
    text = resources.files("pydiggs.conformance").joinpath(SCHEMA_RESOURCE).read_text("utf-8")
    return dict(json.loads(text))
