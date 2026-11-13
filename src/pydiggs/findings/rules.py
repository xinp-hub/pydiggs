"""Stable rule ids: the grammar, and the tables that name every native check.

A rule id is ``<step>.<name>``: the step a finding belongs to, a dot, then a name that
starts with a letter or digit and continues with letters, digits, ``.``, ``_`` or ``-``.
Rule ids are part of the stable output contract: renaming one is a breaking change.

Pure: no lxml, and no I/O beyond reading the packaged guidance file
(:mod:`pydiggs.findings.guidance`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from typing import Any, Final

from pydiggs.findings.guidance import Guidance, guidance_table
from pydiggs.findings.keys import TRUNCATED_RULE
from pydiggs.findings.model import RULE_ID_PATTERN, Severity, Step, is_valid_rule_id

__all__ = [
    "CONTEXT_RULES",
    "DEPRECATED",
    "DICTIONARY_13",
    "DICTIONARY_OFFLINE",
    "DIGGS_ROOT",
    "DUPLICATE_GML_ID",
    "ENTITY_EXPANSION",
    "FINDINGS_TRUNCATED",
    "HELP_URI",
    "INTERNAL_ERROR",
    "MARKUP_ENTITY",
    "MAX_RULE_ID_CHARS",
    "MAX_USER_ID_CHARS",
    "NAMESPACE_TOO_LONG",
    "READ_LIMIT",
    "REMOVED",
    "RULE_ID_PATTERN",
    "SARIF_LEVEL",
    "SCHEMATRON_BUNDLED",
    "SCHEMATRON_BUNDLED_ALIASES",
    "SCHEMATRON_CASING_NUMERIC",
    "SCHEMATRON_CASING_UNITS",
    "SCHEMA_INCOMPLETE",
    "TIMEOUT",
    "UNRELEASED_PROFILE",
    "VALUE_UNPARSED",
    "WELL_FORMED",
    "RuleInfo",
    "describe",
    "dictionary_rule",
    "explain_rule",
    "incomplete",
    "is_valid_rule_id",
    "rule_catalogue",
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
#: The root is not a DIGGS ``Diggs`` element.
DIGGS_ROOT: Final = "structure.diggs-root"
#: The internal DTD declares an entity whose value contains markup; nothing else ran.
MARKUP_ENTITY: Final = "structure.markup-entity"
#: The internal DTD's entities expand the file to more than twice its size; nothing else ran.
ENTITY_EXPANSION: Final = "structure.entity-expansion"
#: A namespace URI longer than 1,024 characters; nothing else ran.
NAMESPACE_TOO_LONG: Final = "structure.namespace-too-long"
FINDINGS_TRUNCATED: Final = TRUNCATED_RULE
#: Validation ran out of time: ``pydiggs validate --timeout`` stopped the file's worker,
#: or the ``time_budget`` of validate() / validate_bytes() ran out.
TIMEOUT: Final = "structure.timeout"
#: Reading (pydiggs.read): a value that should be a number is not one; it is kept
#: as text and read as null.
VALUE_UNPARSED: Final = "structure.value-unparsed"
#: Reading: two or more elements share one gml:id; each is keyed by its xpath, and a
#: link to that id resolves to the first.
DUPLICATE_GML_ID: Final = "structure.duplicate-gml-id"
#: Reading stopped at a work limit (too many or too deeply nested elements that
#: no typed field holds); the model is incomplete.
READ_LIMIT: Final = "structure.read-limit"
#: pydiggs validate caught an unexpected error for one file (the batch went on).
INTERNAL_ERROR: Final = "structure.internal-error"
SCHEMA_INCOMPLETE: Final = "schema.incomplete"
#: The file was checked against an unreleased DIGGS schema (an experimental profile such
#: as ``3.1-dev``, chosen explicitly); a pass is not conformance to a released DIGGS.
UNRELEASED_PROFILE: Final = "schema.unreleased-profile"
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
    # Adapted from the DIGGS File Inspector (pydiggs._geo_checks); validate() only.
    104: (Step.GEOMETRY, "geometry.reported-depth-mismatch"),
    105: (Step.GEOMETRY, "geometry.crs-axis-order-suspect"),
    106: (Step.GEOMETRY, "geometry.crs-unresolvable"),
    107: (Step.CONTEXT, "context.xlink-remote-unresolved"),
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
    ENTITY_EXPANSION: "Entities expand the file to more than twice its size",
    NAMESPACE_TOO_LONG: "A namespace URI is longer than 1,024 characters",
    FINDINGS_TRUNCATED: "More findings than max_findings; the rest were not reported",
    TIMEOUT: "Validation did not finish within its time limit",
    VALUE_UNPARSED: "A value that should be a number is not one (read API note)",
    DUPLICATE_GML_ID: "Two or more elements share one gml:id (read API note)",
    READ_LIMIT: "Reading stopped at a work limit; the model is incomplete (read API note)",
    INTERNAL_ERROR: "Validation failed with an unexpected error",
    SCHEMA_INCOMPLETE: "Schema validation could not complete",
    UNRELEASED_PROFILE: "Checked against an unreleased DIGGS schema (experimental profile)",
    DICTIONARY_OFFLINE: "A whitelisted remote code list is not bundled and network access is off",
    DEPRECATED: "Uses something the DIGGS schema deprecates",
    REMOVED: "Uses something the DIGGS schema has removed",
    "structure.diggs-root": "Document root and documentInformation structure",
    "geometry.srs-attributes": "Geometry should declare srsName and srsDimension",
    "context.xlink-href-resolves": "Local xlink:href does not resolve to a gml:id",
    "context.datablock-arity": "dataBlock tuple size does not match the Property count",
    "geometry.reported-depth-mismatch": (
        "Reported totalMeasuredDepth and centerLine length differ by more than 2 %"
    ),
    "geometry.crs-axis-order-suspect": (
        "Geographic coordinates look like longitude, latitude (axes swapped)"
    ),
    "geometry.crs-unresolvable": "srsName names no recognised or in-file coordinate system",
    "context.xlink-remote-unresolved": "xlink:href into another document, not fetched or checked",
    SCHEMATRON_CASING_UNITS: "Casing outside diameter must exceed inside diameter (any unit)",
    SCHEMATRON_CASING_NUMERIC: "Casing diameters must be numeric",
    # Notes of the AGS4 reader (pydiggs.ags4.read) and findings of the AGS4 checker
    # (pydiggs.ags4.check); the constants live in pydiggs.ags4._parse and ._rules. Rule
    # numbers follow section 4.1.1 of the AGS4 specification, Edition 4.2.
    "ags4.rule-1": "AGS4 Rule 1: the file is ASCII text (a byte order mark is noted too)",
    "ags4.rule-2": "AGS4 Rule 2: a file is made of groups, and every row belongs to one",
    "ags4.rule-2a": "AGS4 Rule 2a: every row ends with CR LF",
    "ags4.rule-2b": "AGS4 Rule 2b: a group has GROUP, HEADING, UNIT and TYPE rows, in that order",
    "ags4.rule-3": "AGS4 Rule 3: every row starts with GROUP, HEADING, UNIT, TYPE or DATA",
    "ags4.rule-4": "AGS4 Rule 4: a GROUP row holds one name; other rows match the HEADING row",
    "ags4.rule-5": "AGS4 Rule 5: every field is in double quotes, a quote inside it doubled",
    "ags4.duplicate-group": "A group name is used by more than one GROUP row (AGS4 read note)",
    "ags4.encoding-assumed": "The file is not UTF-8 and was read as Windows-1252 (AGS4 read note)",
    "ags4.version-unknown": "TRAN_AGS names an AGS4 version pyDIGGS does not know (AGS4 read note)",
    "ags4.unsupported-version": "An AGS 3 or older file; pyDIGGS reads AGS4 only",
    "ags4.limit": "Reading stopped at a work limit; the AGS4 file is incomplete (AGS4 read note)",
    "ags4.timeout": "Reading or checking the AGS4 file did not finish within its time budget",
    "ags4.rule-6": "AGS4 Rule 6: fields are separated by commas, with no line break in a value",
    "ags4.rule-7": "AGS4 Rule 7: headings are in the order the data dictionary gives",
    "ags4.rule-8": "AGS4 Rule 8: values are written as their TYPE (and a DT value as its UNIT)",
    "ags4.rule-9": (
        "AGS4 Rule 9: names come from the data dictionary or the DICT group; user-defined"
        " headings go last"
    ),
    "ags4.rule-10a": "AGS4 Rule 10a: KEY headings are present and no two rows share KEY values",
    "ags4.rule-10b": "AGS4 Rule 10b: REQUIRED headings are present and never null",
    "ags4.rule-10c": "AGS4 Rule 10c: each row's KEY values match a row of its parent group",
    "ags4.rule-11": "AGS4 Rule 11: a Record Link (RL) value is a GROUP name and KEY values",
    "ags4.rule-11a": "AGS4 Rule 11a: TRAN_DLIM, when given, is one delimiter character",
    "ags4.rule-11b": "AGS4 Rule 11b: TRAN_RCON, when given, is one concatenation character",
    "ags4.rule-11c": "AGS4 Rule 11c: a record link refers to a row that is in the file",
    "ags4.rule-13": "AGS4 Rule 13: the file has a PROJ group with exactly one DATA row",
    "ags4.rule-14": "AGS4 Rule 14: the file has a TRAN group with exactly one DATA row",
    "ags4.rule-15": "AGS4 Rule 15: a UNIT group lists every unit used (UNIT rows, PU values)",
    "ags4.rule-16": "AGS4 Rule 16: an ABBR group defines every abbreviation used (PA values)",
    "ags4.rule-16a": "AGS4 Rule 16a: each part of a combined abbreviation is defined in ABBR",
    "ags4.rule-17": "AGS4 Rule 17: a TYPE group lists every data type used (TYPE rows, PT values)",
    "ags4.rule-18": "AGS4 Rule 18: a DICT group defines the non-standard groups and headings",
    "ags4.rule-18a": "AGS4 Rule 18a: user-defined headings are in the order the DICT group gives",
    "ags4.rule-19": "AGS4 Rule 19: a group name is 1 to 4 uppercase letters and digits",
    "ags4.rule-19a": "AGS4 Rule 19a: a heading is 1 to 9 uppercase letters, digits and underscores",
    "ags4.rule-19b": (
        "AGS4 Rule 19b: a heading starts with its group's name and an underscore, or is a"
        " heading of the group it names"
    ),
    "ags4.rule-20": (
        "AGS4 Rule 20: the FILE group lists the file sets named and gives file names (the"
        " FILE folder is never opened)"
    ),
    "ags4.formula-text": "AGS4 text that a spreadsheet would read as a formula; kept as written",
    "ags4.dictionary-not-checked": (
        "No AGS4 data dictionary was supplied; the rules that need one checked only the"
        " file's own DICT group"
    ),
    # What an AGS4 to DIGGS conversion lost (pydiggs.preview.convert); see convert._ledger.
    "convert.unmapped-group": "An AGS4 group has no mapping to DIGGS; its rows were not converted",
    "convert.unmapped-heading": "An AGS4 heading has no DIGGS target; its values are not converted",
    "convert.user-defined": "A group or heading the file's DICT defines; reported, not converted",
    "convert.unmapped-code": "An AGS4 code has no DIGGS code; kept as text or left out",
    "convert.unmapped-unit": "An AGS4 unit has no entry in the unit table; values not converted",
    "convert.orphan-row": "A row names a parent record that was not converted; not converted",
    "convert.value-unparsed": "A value does not parse as the number, date or text DIGGS needs",
    "convert.assumed": "DIGGS needs a value the source lacks; a documented default was written",
    "convert.value-reformatted": "The same value was written in another form (not a loss)",
    "convert.incomplete": "Part of the source was not converted: a limit, or a DIGGS requirement",
}


def incomplete(step: Step | str) -> str:
    """Rule id of a step that could not finish (fail closed: always an error).

    Examples:
        >>> incomplete("schematron")
        'schematron.incomplete'
    """
    return f"{Step(step).value}.incomplete"


def dictionary_rule(check: int) -> str:
    """Rule id of a numbered dictionary check.

    Examples:
        >>> dictionary_rule(5)
        'dictionary.5'
    """
    return f"dictionary.{check}"


def schema_rule(type_name: str | None) -> str:
    """Rule id for a libxml2 schema-validation error type such as ``SCHEMAV_CVC_ELT_1``.

    ``SCHEMAV_CVC_*`` names become the XSD validation rule they report
    (``schema.cvc-elt.1``); anything else keeps libxml2's own name.

    Examples:
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

    Examples:
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

    Examples:
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


#: Longest rule id :func:`explain_rule` accepts.
MAX_RULE_ID_CHARS: Final = 256

_ERROR: Final = (Severity.ERROR,)
_WARNING: Final = (Severity.WARNING,)
_INFO: Final = (Severity.INFO,)
_ERROR_OR_WARNING: Final = (Severity.ERROR, Severity.WARNING)
_ANY: Final = (Severity.ERROR, Severity.WARNING, Severity.INFO)

#: Severities each fixed rule id is reported at (the "Severity" column of the rule-id
#: table in docs/findings.md; a test keeps the two in step). A finding's severity is
#: always one of these.
_SEVERITIES: Final[dict[str, tuple[Severity, ...]]] = {
    WELL_FORMED: _ERROR,
    DIGGS_ROOT: _ERROR,
    MARKUP_ENTITY: _ERROR,
    ENTITY_EXPANSION: _ERROR,
    NAMESPACE_TOO_LONG: _ERROR,
    TIMEOUT: _ERROR,
    VALUE_UNPARSED: _INFO,
    DUPLICATE_GML_ID: _WARNING,
    READ_LIMIT: _ERROR,
    INTERNAL_ERROR: _ERROR,
    UNRELEASED_PROFILE: _WARNING,
    FINDINGS_TRUNCATED: _ANY,  # as severe as the worst finding it stands for
    "dictionary.1": _INFO,
    "dictionary.2": _WARNING,
    "dictionary.3": _WARNING,
    "dictionary.4": _ERROR,
    "dictionary.5": _ERROR,
    "dictionary.6": _ERROR,
    "dictionary.7": _INFO,
    "dictionary.8": _ERROR,
    "dictionary.9": _ERROR,
    "dictionary.10": _ERROR,
    "dictionary.11": _ERROR_OR_WARNING,  # warning for dimensionless quantities
    "dictionary.12": _ERROR_OR_WARNING,
    DICTIONARY_13: _ERROR_OR_WARNING,
    DICTIONARY_OFFLINE: _INFO,
    **dict.fromkeys(SCHEMATRON_BUNDLED.values(), _ERROR),
    SCHEMATRON_CASING_UNITS: _ERROR,
    SCHEMATRON_CASING_NUMERIC: _ERROR,
    "geometry.srs-attributes": _WARNING,
    DEPRECATED: _WARNING,
    REMOVED: _ERROR,
    "context.xlink-href-resolves": _WARNING,
    "context.datablock-arity": _ERROR,
    "geometry.reported-depth-mismatch": _WARNING,
    "geometry.crs-axis-order-suspect": _WARNING,
    "geometry.crs-unresolvable": _WARNING,
    "context.xlink-remote-unresolved": _INFO,
    "ags4.rule-1": _ERROR,
    "ags4.rule-2": _ERROR,
    "ags4.rule-2a": _ERROR,
    "ags4.rule-2b": _ERROR,
    "ags4.rule-3": _ERROR,
    "ags4.rule-4": _ERROR,
    "ags4.rule-5": _ERROR,
    "ags4.duplicate-group": _ERROR,
    "ags4.encoding-assumed": _WARNING,
    "ags4.version-unknown": _WARNING,
    "ags4.unsupported-version": _ERROR,
    "ags4.limit": _ERROR,
    "ags4.timeout": _ERROR,
    "ags4.rule-6": _ERROR,
    "ags4.rule-7": _ERROR,
    "ags4.rule-8": _ERROR,
    "ags4.rule-9": _ERROR,
    "ags4.rule-10a": _ERROR,
    "ags4.rule-10b": _ERROR,
    "ags4.rule-10c": _ERROR,
    "ags4.rule-11": _ERROR,
    "ags4.rule-11a": _ERROR,
    "ags4.rule-11b": _ERROR,
    "ags4.rule-11c": _ERROR,
    "ags4.rule-13": _ERROR,
    "ags4.rule-14": _ERROR,
    "ags4.rule-15": _ERROR,
    "ags4.rule-16": _ERROR,
    "ags4.rule-16a": _ERROR,
    "ags4.rule-17": _ERROR,
    "ags4.rule-18": _ERROR,
    "ags4.rule-18a": _ERROR,
    "ags4.rule-19": _ERROR,
    "ags4.rule-19a": _ERROR,
    "ags4.rule-19b": _ERROR,
    "ags4.rule-20": _ERROR,
    "ags4.formula-text": _WARNING,
    "ags4.dictionary-not-checked": _INFO,
    "convert.unmapped-group": _WARNING,
    "convert.unmapped-heading": _WARNING,
    "convert.user-defined": _WARNING,
    "convert.unmapped-code": _WARNING,
    "convert.unmapped-unit": _WARNING,
    "convert.orphan-row": _ERROR,
    "convert.value-unparsed": _WARNING,
    "convert.assumed": _WARNING,
    "convert.value-reformatted": _INFO,
    "convert.incomplete": _ERROR,
}
#: The steps pydiggs runs (run.NATIVE_STEPS, copied to avoid an import cycle; a test
#: keeps them equal): only they can be ``incomplete``.
_NATIVE_STEP_VALUES: Final = (
    "structure",
    "schema",
    "dictionary",
    "schematron",
    "geometry",
    "deprecation",
    "context",
)
_STEPS_ALTERNATION: Final = "|".join(re.escape(s) for s in _NATIVE_STEP_VALUES)
_NAME: Final = r"[A-Za-z0-9][A-Za-z0-9._-]*"
#: Open families, in matching order: (template, regex, severities, description). The
#: Schematron catch-all comes last, so the bundled rules and hashed ids match first.
_FAMILIES: Final[tuple[tuple[str, str, tuple[Severity, ...], str], ...]] = (
    (
        "schema.cvc-<rule>",
        rf"schema\.cvc-{_NAME}",
        _ERROR_OR_WARNING,
        "An XML Schema validation rule, named from libxml2's error type",
    ),
    (
        "schema.libxml2.<TYPE>",
        rf"schema\.libxml2\.{_NAME}",
        _ERROR_OR_WARNING,
        "A libxml2 schema-validation error type that names no XML Schema rule",
    ),
    (
        "<step>.incomplete",
        rf"(?:{_STEPS_ALTERNATION})\.incomplete",
        _ERROR,
        "The step could not finish; its results are partial (fail closed)",
    ),
    (
        "schematron.user-<hash>",
        r"schematron\.user-[0-9a-f]{12}",
        _ERROR,
        "A rule from your own Schematron file with no usable @id (hash of context and test)",
    ),
    (
        "schematron.<id>",
        rf"schematron\.{_NAME}",
        _ERROR,
        "A rule from your own Schematron file, named by its @id",
    ),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleInfo:
    """What one rule id (or one open family of them) means.

    ``id`` is the rule id. For an open family in :func:`rule_catalogue` it is the
    family's template (``"schema.cvc-<rule>"``) and ``pattern`` is the regular
    expression its ids match in full; for a fixed id ``pattern`` is None.
    :func:`explain_rule` returns the id asked about, with its family's ``pattern``
    when it belongs to one. ``severities`` lists what a finding with this id can be,
    most serious first; ``known`` is False for a well-formed id pydiggs never emits.
    ``guidance`` is the plain-language :class:`~pydiggs.Guidance` for engineers who are
    not DIGGS specialists (title, meaning, fix); it is None only for an unknown id.

    Examples:
        >>> from pydiggs import explain_rule
        >>> info = explain_rule("dictionary.5")
        >>> info.step, info.severities, info.known
        (<Step.DICTIONARY: 'dictionary'>, (<Severity.ERROR: 'error'>,), True)
        >>> info.to_dict()["description"]
        'Code is not found in the code list dictionary'
        >>> info.guidance.title
        'Unknown code'
    """

    id: str
    pattern: str | None
    step: Step
    description: str
    severities: tuple[Severity, ...]
    help_uri: str
    known: bool
    guidance: Guidance | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable dict (enums as their string values), in field order."""
        return {
            "id": self.id,
            "pattern": self.pattern,
            "step": self.step.value,
            "description": self.description,
            "severities": [s.value for s in self.severities],
            "help_uri": self.help_uri,
            "known": self.known,
            "guidance": None if self.guidance is None else self.guidance.to_dict(),
        }


def _fixed(rule_id: str) -> RuleInfo:
    return RuleInfo(
        id=rule_id,
        pattern=None,
        step=Step(rule_id.partition(".")[0]),
        description=describe(rule_id),
        severities=_SEVERITIES[rule_id],
        help_uri=HELP_URI,
        known=True,
        guidance=guidance_table().get(rule_id),
    )


@cache
def _catalogue() -> tuple[RuleInfo, ...]:
    fixed = [_fixed(rule_id) for rule_id in _SEVERITIES]
    families = [
        RuleInfo(
            id=template,
            pattern=f"^{regex}$",
            step=Step(template.partition(".")[0])
            if not template.startswith("<")
            else Step.STRUCTURE,
            description=description,
            severities=severities,
            help_uri=HELP_URI,
            known=True,
            guidance=guidance_table().get(template),
        )
        for template, regex, severities, description in _FAMILIES
    ]
    fixed.sort(key=lambda r: (r.step.order, r.id))
    return (*fixed, *families)


def rule_catalogue() -> tuple[RuleInfo, ...]:
    """Every rule id pydiggs can emit: fixed ids, then open families as patterns.

    Fixed ids come in pipeline (step) order, then alphabetically; the open families
    (``schema.cvc-*``, ``schema.libxml2.*``, ``<step>.incomplete``,
    ``schematron.user-*`` and user Schematron ``@id`` rules) follow, each with a
    ``pattern``. The ``<step>.incomplete`` family is listed under ``structure`` but
    covers every step. A new entry may be added in a minor release. Every entry
    carries plain-language ``guidance``.

    Returns:
        The catalogue, the same tuple on every call.

    Examples:
        >>> from pydiggs import rule_catalogue
        >>> rules = rule_catalogue()
        >>> rules[0].id
        'structure.diggs-root'
        >>> [r.id for r in rules if r.pattern][:2]
        ['schema.cvc-<rule>', 'schema.libxml2.<TYPE>']
    """
    return _catalogue()


def explain_rule(rule_id: str) -> RuleInfo:
    r"""Describe one rule id, such as a finding's ``rule_id``.

    A well-formed id that pydiggs never emits (``"crs.unknown"``) is not an error:
    it gets ``known=False``, no severities, a generic description and no guidance.
    A known id gets plain-language ``guidance``: its own when it has some, else its
    family's (so ``schema.cvc-complex-type.2.4`` is explained more specifically than
    the ``schema.cvc-<rule>`` family as a whole).

    Args:
        rule_id: ``<step>.<name>``, at most 256 characters.

    Returns:
        The :class:`RuleInfo` for exactly this id.

    Raises:
        ValueError: ``rule_id`` does not follow the rule-id grammar, names no step,
            or is longer than 256 characters.
        TypeError: ``rule_id`` is not a string.

    Examples:
        >>> from pydiggs import explain_rule
        >>> explain_rule("schematron.Borehole.totalMeasuredDepth").description
        'Schematron rule Borehole.totalMeasuredDepth'
        >>> explain_rule("schema.cvc-elt.1").pattern
        '^schema\\.cvc-[A-Za-z0-9][A-Za-z0-9._-]*$'
        >>> explain_rule("schema.cvc-complex-type.2.4").guidance.title
        'Missing or misplaced entry'
        >>> explain_rule("schema.cvc-length-valid").guidance.title
        'Breaks the DIGGS format'
        >>> explain_rule("crs.unknown").known
        False
        >>> explain_rule("not a rule")
        Traceback (most recent call last):
        ...
        ValueError: 'not a rule' is not a valid rule id (<step>.<name>)
    """
    if not isinstance(rule_id, str):
        raise TypeError(f"rule_id must be str, got {type(rule_id).__name__}")
    if len(rule_id) > MAX_RULE_ID_CHARS:
        raise ValueError(f"rule id is longer than {MAX_RULE_ID_CHARS} characters")
    if not is_valid_rule_id(rule_id):
        raise ValueError(f"{rule_id[:80]!r} is not a valid rule id (<step>.<name>)")
    step = Step(rule_id.partition(".")[0])
    if rule_id in _SEVERITIES:
        return _fixed(rule_id)
    for template, regex, severities, family in _FAMILIES:
        if re.fullmatch(regex, rule_id):
            text = describe(rule_id)
            table = guidance_table()
            return RuleInfo(
                id=rule_id,
                pattern=f"^{regex}$",
                step=step,
                description=text if text != rule_id else family,
                severities=severities,
                help_uri=HELP_URI,
                known=True,
                guidance=table.get(rule_id) or table.get(template),
            )
    return RuleInfo(
        id=rule_id,
        pattern=None,
        step=step,
        description="Not a rule id this version of pydiggs emits",
        severities=(),
        help_uri=HELP_URI,
        known=False,
    )
