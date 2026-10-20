"""Stable rule ids: the grammar, and the tables that name every native check.

A rule id is ``<step>.<name>``: the step a finding belongs to, a dot, then a name that
starts with a letter or digit and continues with letters, digits, ``.``, ``_`` or ``-``.
Rule ids are part of the stable output contract: renaming one is a breaking change.

Pure: no lxml, no I/O.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

from pydiggs.findings.keys import TRUNCATED_RULE
from pydiggs.findings.model import RULE_ID_PATTERN, Severity, Step, is_valid_rule_id

__all__ = [
    "CONTEXT_RULES",
    "DEPRECATED",
    "DICTIONARY_13",
    "DICTIONARY_OFFLINE",
    "FINDINGS_TRUNCATED",
    "HELP_URI",
    "INTERNAL_ERROR",
    "MARKUP_ENTITY",
    "MAX_USER_ID_CHARS",
    "REMOVED",
    "RULE_ID_PATTERN",
    "SARIF_LEVEL",
    "SCHEMATRON_BUNDLED",
    "SCHEMATRON_BUNDLED_ALIASES",
    "SCHEMATRON_CASING_NUMERIC",
    "SCHEMATRON_CASING_UNITS",
    "SCHEMA_INCOMPLETE",
    "TIMEOUT",
    "WELL_FORMED",
    "describe",
    "dictionary_rule",
    "incomplete",
    "is_valid_rule_id",
    "schema_rule",
    "schematron_bundled_rule",
    "schematron_user_rule",
]


_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Longest user Schematron ``@id`` used as a rule id; longer ones are hashed.
MAX_USER_ID_CHARS: Final = 4096

#: Where every rule is documented.
HELP_URI: Final = "https://xinp-hub.github.io/pydiggs/findings/#rule-ids"

WELL_FORMED: Final = "structure.well-formed"
#: The internal DTD declares an entity whose value contains markup; nothing else ran.
MARKUP_ENTITY: Final = "structure.markup-entity"
FINDINGS_TRUNCATED: Final = TRUNCATED_RULE
#: pydiggs validate --timeout stopped the file's validation.
TIMEOUT: Final = "structure.timeout"
#: pydiggs validate caught an unexpected error for one file (the batch went on).
INTERNAL_ERROR: Final = "structure.internal-error"
SCHEMA_INCOMPLETE: Final = "schema.incomplete"
DICTIONARY_13: Final = "dictionary.13"
DICTIONARY_OFFLINE: Final = "dictionary.offline"
DEPRECATED: Final = "deprecation.deprecated"
REMOVED: Final = "deprecation.removed"

#: Legacy context-check numbers (``ValidationMessage.check``) → (step, rule id).
CONTEXT_RULES: Final[dict[int, tuple[Step, str]]] = {
    100: (Step.STRUCTURE, "structure.diggs-root"),
    101: (Step.GEOMETRY, "geometry.srs-attributes"),
    102: (Step.CONTEXT, "context.xlink-href-resolves"),
    103: (Step.CONTEXT, "context.datablock-arity"),
}


def _rule(context: str, test: str) -> tuple[str, str]:
    return (context, " ".join(test.split()))


#: Bundled Schematron rules (``schematron/diggs_schematron_rules_2.6.sch``), keyed by
#: the rule's ``@context`` and the assert's whitespace-normalised ``@test``.
SCHEMATRON_BUNDLED: Final[dict[tuple[str, str], str]] = {
    _rule(
        "*[local-name()='totalMeasuredDepth'][parent::*[local-name()='Borehole']]", "number(.) > 0"
    ): "schematron.Borehole.totalMeasuredDepth",
    _rule(
        "*[local-name()='penetration'][parent::*[local-name()='DriveSet']]", "number(.) >= 0"
    ): "schematron.DriveSet.penetration",
    _rule(
        "*[local-name()='hammerEfficiency'][parent::*[local-name()='DrivenPenetrationTest']]",
        "number(.) >= 0 and number(.) <= 100",
    ): "schematron.DrivenPenetrationTest.hammerEfficiency",
    _rule(
        "*[local-name()='casingOutsideDiameter'][parent::*[local-name()='Casing']]", "number(.) > 0"
    ): "schematron.Casing.casingOutsideDiameter",
    _rule(
        "*[local-name()='casingInsideDiameter'][parent::*[local-name()='Casing']]", "number(.) > 0"
    ): "schematron.Casing.casingInsideDiameter",
    _rule(
        "*[local-name()='plunge'][parent::*[local-name()='AbstractLinearSamplingFeature']]",
        "number(.) >= -45 and number(.) <= -10",
    ): "schematron.AbstractLinearSamplingFeature.plunge",
    _rule(
        "*[local-name()='waterContent'][parent::*[local-name()='CasagrandeTrial']]",
        "number(.) >= 0",
    ): "schematron.CasagrandeTrial.waterContent",
    _rule(
        "*[local-name()='blowCount'][parent::*[local-name()='CasagrandeTrial']]", "number(.) >= 0"
    ): "schematron.CasagrandeTrial.blowCount",
    _rule(
        "*[local-name()='plasticity'][parent::*[local-name()='FieldProperties']]", "number(.) >= 0"
    ): "schematron.FieldProperties.plasticity",
    _rule(
        "*[local-name()='weight'][parent::*[local-name()='Cement']]", "number(.) > 0"
    ): "schematron.Cement.weight",
    _rule(
        "*[local-name()='specificGravity'][parent::*[local-name()='Cement']]", "number(.) > 0"
    ): "schematron.Cement.specificGravity",
    _rule(
        "*[local-name()='voidRatio'][parent::*[local-name()='SpecimenConditions']]",
        "number(.) >= 0",
    ): "schematron.SpecimenConditions.voidRatio",
    _rule(
        "*[local-name()='percentPassing'][parent::*[local-name()='Grading']]", "number(.) >= 0"
    ): "schematron.Grading.percentPassing",
    _rule(
        "*[local-name()='percentRetained'][parent::*[local-name()='Grading']]", "number(.) >= 0"
    ): "schematron.Grading.percentRetained",
    _rule(
        "*[local-name()='weightRetained'][parent::*[local-name()='Grading']]", "number(.) >= 0"
    ): "schematron.Grading.weightRetained",
    _rule(
        "*[local-name()='particleSize'][parent::*[local-name()='Grading']]", "number(.) > 0"
    ): "schematron.Grading.particleSize",
    _rule(
        "*[local-name()='specificGravityMix'][parent::*[local-name()='AbstractTrialGroutBatch']]",
        "number(.) > 1.0",
    ): "schematron.AbstractTrialGroutBatch.specificGravityMix",
    _rule(
        "*[local-name()='Casing'][*[local-name()='casingOutsideDiameter'] and "
        "*[local-name()='casingInsideDiameter']]",
        "not(string($outsideDiameter) = 'NaN') and not(string($insideDiameter) = 'NaN')",
    ): "schematron.Casing.diameters-numeric",
    _rule(
        "*[local-name()='Casing'][*[local-name()='casingOutsideDiameter'] and "
        "*[local-name()='casingInsideDiameter']]",
        "not($outsideUom = $insideUom) or $outsideDiameter > $insideDiameter",
    ): "schematron.Casing.outside-gt-inside",
}
_PARENT_FORM: Final = re.compile(
    r"^\*\[local-name\(\)='(\w+)'\]\[parent::\*\[local-name\(\)='(\w+)'\]\]$"
)
#: The same rules under the contexts the bundled file used before 1.2.0
#: (``//*[local-name()='P']/*[local-name()='C']``, which matches the same elements),
#: so a report keyed by either spelling gets the same rule id.
SCHEMATRON_BUNDLED_ALIASES: Final[dict[tuple[str, str], str]] = {
    (f"//*[local-name()='{m[2]}']/*[local-name()='{m[1]}']", test): rule_id
    for (context, test), rule_id in SCHEMATRON_BUNDLED.items()
    if (m := _PARENT_FORM.match(context))
}


def schematron_bundled_rule(context: str, test: str) -> str | None:
    """The rule id of a bundled Schematron assert, from its rule context and test."""
    key = _rule(context, test)
    return SCHEMATRON_BUNDLED.get(key) or SCHEMATRON_BUNDLED_ALIASES.get(key)


#: pydiggs's own cross-unit casing rule (``validator._check_casing_diameters``).
SCHEMATRON_CASING_UNITS: Final = "schematron.Casing.outside-gt-inside-converted"
SCHEMATRON_CASING_NUMERIC: Final = "schematron.Casing.diameters-numeric-converted"


# libxml2 2.14 XML_SCHEMAV_CVC_* stems → the XSD 1.1 Part 1 validation-rule names.
_CVC_STEMS: Final[dict[str, str]] = {
    "ATTRIBUTE": "attribute",
    "AU": "au",
    "COMPLEX_TYPE": "complex-type",
    "DATATYPE_VALID": "datatype-valid",
    "ELT": "elt",
    "ENUMERATION_VALID": "enumeration-valid",
    "FACET_VALID": "facet-valid",
    "FRACTIONDIGITS_VALID": "fractionDigits-valid",
    "IDC": "identity-constraint",
    "LENGTH_VALID": "length-valid",
    "MAXEXCLUSIVE_VALID": "maxExclusive-valid",
    "MAXINCLUSIVE_VALID": "maxInclusive-valid",
    "MAXLENGTH_VALID": "maxLength-valid",
    "MINEXCLUSIVE_VALID": "minExclusive-valid",
    "MININCLUSIVE_VALID": "minInclusive-valid",
    "MINLENGTH_VALID": "minLength-valid",
    "PATTERN_VALID": "pattern-valid",
    "TOTALDIGITS_VALID": "totalDigits-valid",
    "TYPE": "type",
    "WILDCARD": "wildcard",
}
# Non-CVC libxml2 codes whose emitting code names the rule in a SPEC comment
# (xmlschemas.c, 2.14.6: "SPEC (2.4) ... Element Sequence Locally Valid").
_SPEC_ALIASES: Final[dict[str, str]] = {
    "SCHEMAV_ELEMENT_CONTENT": "schema.cvc-complex-type.2.4",
}
_CVC_PATTERN: Final = re.compile(r"^SCHEMAV_CVC_(?P<stem>[A-Z_]+?)(?P<clause>(?:_\d+)*)$")

_DICTIONARY_CHECKS: Final[dict[int, str]] = {
    1: "Value cannot be checked against a code list (uncontrolled codeSpace)",
    2: "Code list dictionary is not on the white list",
    3: "Code list dictionary could not be accessed",
    4: "Resource is not a DIGGS code list dictionary",
    5: "Code is not found in the code list dictionary",
    6: "Code is not valid for this element in this context",
    7: "Value does not match any name of its definition",
    8: "Property is not allowed for this type of measurement",
    9: "Sibling typeData does not match the dictionary data type",
    10: "Unitless property has a uom",
    11: "Property requires a unit of measure",
    12: "Unit of measure is not valid for the quantity class",
    13: "Result values are not terms of the property's value code list",
}

_DESCRIPTIONS: Final[dict[str, str]] = {
    WELL_FORMED: "The file is not well-formed XML",
    MARKUP_ENTITY: "The internal DTD declares an entity whose value contains markup",
    FINDINGS_TRUNCATED: "More findings than max_findings; the rest were not reported",
    TIMEOUT: "Validation did not finish within --timeout",
    INTERNAL_ERROR: "Validation failed with an unexpected error",
    SCHEMA_INCOMPLETE: "Schema validation could not complete",
    DICTIONARY_OFFLINE: "A whitelisted remote code list is not bundled and network access is off",
    DEPRECATED: "Uses something the DIGGS schema deprecates",
    REMOVED: "Uses something the DIGGS schema has removed",
    "structure.diggs-root": "Document root and documentInformation structure",
    "geometry.srs-attributes": "Geometry should declare srsName and srsDimension",
    "context.xlink-href-resolves": "Local xlink:href does not resolve to a gml:id",
    "context.datablock-arity": "dataBlock tuple size does not match the Property count",
    SCHEMATRON_CASING_UNITS: "Casing outside diameter must exceed inside diameter (any unit)",
    SCHEMATRON_CASING_NUMERIC: "Casing diameters must be numeric",
}


def incomplete(step: Step | str) -> str:
    """Rule id of a step that could not finish (fail closed: always an error).

    Example:
        >>> incomplete("schematron")
        'schematron.incomplete'
    """
    return f"{Step(step).value}.incomplete"


def dictionary_rule(check: int) -> str:
    """Rule id of a numbered dictionary check.

    Example:
        >>> dictionary_rule(5)
        'dictionary.5'
    """
    return f"dictionary.{check}"


def schema_rule(type_name: str | None) -> str:
    """Rule id for a libxml2 schema-validation error type such as ``SCHEMAV_CVC_ELT_1``.

    ``SCHEMAV_CVC_*`` names become the XSD validation rule they report
    (``schema.cvc-elt.1``); anything else keeps libxml2's own name.

    Example:
        >>> schema_rule("SCHEMAV_CVC_COMPLEX_TYPE_2_4")
        'schema.cvc-complex-type.2.4'
        >>> schema_rule("SCHEMAV_CVC_MININCLUSIVE_VALID")
        'schema.cvc-minInclusive-valid'
        >>> schema_rule("SCHEMAV_ELEMENT_CONTENT")
        'schema.cvc-complex-type.2.4'
        >>> schema_rule("SCHEMAV_MISC")
        'schema.libxml2.SCHEMAV_MISC'
    """
    name = type_name or "UNKNOWN"
    if name in _SPEC_ALIASES:
        return _SPEC_ALIASES[name]
    match = _CVC_PATTERN.match(name)
    if match is not None and match["stem"] in _CVC_STEMS:
        clause = match["clause"].replace("_", ".")
        return f"schema.cvc-{_CVC_STEMS[match['stem']]}{clause}"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return f"schema.libxml2.{safe}"


def schematron_user_rule(assert_id: str | None, context: str, test: str) -> str:
    """Rule id for an assert or report in a user-supplied Schematron file.

    The assert's ``@id`` when it has one that fits the grammar and is at most
    :data:`MAX_USER_ID_CHARS` long; otherwise a stable hash of its rule context and test.

    Example:
        >>> schematron_user_rule("borehole-depth", "//Borehole", "@depth > 0")
        'schematron.borehole-depth'
        >>> schematron_user_rule(None, "/*", "name()='Diggs'")
        'schematron.user-45b6d8b8510e'
    """
    if assert_id and len(assert_id) <= MAX_USER_ID_CHARS and _NAME_PATTERN.match(assert_id):
        return f"schematron.{assert_id}"
    digest = hashlib.sha256(f"{context}\n{' '.join(test.split())}".encode()).hexdigest()
    return f"schematron.user-{digest[:12]}"


def describe(rule_id: str) -> str:
    """A one-line description of ``rule_id``, for SARIF ``shortDescription``.

    Example:
        >>> describe("dictionary.5")
        'Code is not found in the code list dictionary'
        >>> describe("schema.cvc-elt.1")
        'XML Schema validation rule cvc-elt.1'
    """
    if rule_id in _DESCRIPTIONS:
        return _DESCRIPTIONS[rule_id]
    step, _, name = rule_id.partition(".")
    if step == Step.DICTIONARY and name.isdigit() and int(name) in _DICTIONARY_CHECKS:
        return _DICTIONARY_CHECKS[int(name)]
    if step == Step.SCHEMA:
        if name.startswith("libxml2."):
            return f"XML Schema validation error {name.removeprefix('libxml2.')}"
        return f"XML Schema validation rule {name}"
    if step == Step.SCHEMATRON:
        return f"Schematron rule {name}"
    return rule_id


#: Severity → SARIF ``level``.
SARIF_LEVEL: Final[dict[Severity, str]] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}
