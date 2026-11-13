"""The bundled mapping tables: load them once, check their shape, and give typed rows.

The tables are data (``mappings/*.toml``, read with :mod:`tomllib`); their format is
``convert-mapping-1.schema.json``. Loading checks what the converter relies on (closed
sets, required keys, no heading listed twice) and raises
:class:`~pydiggs.errors.ResourceError` when a bundled table is broken. Cross-checks
against the DIGGS dictionaries and the writer layout are in the tests. Pure after load.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Final

from lxml import etree

from pydiggs import _xml
from pydiggs._bundled import _DEFAULT_DICTIONARY, _PACKAGE_DIR
from pydiggs.errors import ResourceError

__all__ = [
    "PATTERNS",
    "PROPERTIES_URL",
    "TARGETS",
    "CodeRule",
    "GroupRule",
    "HeadingRule",
    "MappingTable",
    "MeasureFacts",
    "PropertyFacts",
    "load_mapping",
    "measure_facts",
    "property_facts",
]

#: The group patterns the converter implements (a new pattern is code, and needs review).
PATTERNS: Final = frozenset(
    {
        "project",
        "transmission",
        "lookup",
        "feature",
        "interval-observation",
        "sample",
        "test-per-row",
        "test-table",
    }
)
#: Where a heading's values go (see the table's header comment).
TARGETS: Final = frozenset({"key", "link", "field", "param", "column", "format", "none"})
_DIRECTIONS: Final = frozenset({"both", "to-diggs", "to-ags4"})
_STATUSES: Final = frozenset({"draft", "reviewed"})
#: The published DIGGS property dictionary, the ``definition`` of every result column.
PROPERTIES_URL: Final = "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"
_GML_ID: Final = "{http://www.opengis.net/gml/3.2}id"
_TABLES: Final = ("ags4-diggs.toml", "units.toml", "codes.toml")
_XS: Final = "{http://www.w3.org/2001/XMLSchema}"
_SCHEMA_3: Final = _PACKAGE_DIR / "schemas" / "diggs-schema-3.0.0"
#: The DIGGS 3.0.0 measure types, and the Energistics unit lists they draw on.
_MEASURE_TYPES: Final = _SCHEMA_3 / "core" / "MeasureTypes.xsd"
_COMMON_TYPES: Final = _SCHEMA_3 / "core" / "Common.xsd"
_QUANTITY_CLASSES: Final = (
    _SCHEMA_3 / "energistics/2.0/data/common/v2.1/xsd_schemas/QuantityClass.xsd"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class HeadingRule:
    """Where one AGS4 heading goes."""

    name: str
    to: str
    target: str | None = None
    property: str | None = None
    reason: str | None = None
    direction: str = "both"
    #: For a ``param`` that is a DIGGS measure: its quantity, the measure element being
    #: of type ``diggs:<quantity>MeasureType`` (for example ``Length``).
    quantity: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupRule:
    """How one AGS4 group is converted: its pattern, keys and headings."""

    name: str
    pattern: str
    status: str
    keys: tuple[str, ...]
    headings: Mapping[str, HeadingRule]
    parent: str | None = None
    element: str | None = None
    procedure: str | None = None
    procedure_params: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    data: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeRule:
    """One AGS4 code: the DIGGS element it selects and its DIGGS code, where there is one."""

    heading: str
    ags: str
    element: str | None = None
    code_list: str | None = None
    code: str | None = None
    exact: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class MappingTable:
    """The three bundled tables, typed."""

    mapping_version: str
    ags_versions: tuple[str, ...]
    diggs_versions: tuple[str, ...]
    groups: Mapping[str, GroupRule]
    units: Mapping[str, str]
    codes: Mapping[tuple[str, str], CodeRule]
    crs: Mapping[tuple[str, str], str]
    code_list_base: str
    #: The unit (a DIGGS uom) of the axes of each CRS the code table gives one for.
    crs_units: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def code_space(self, code_list: str) -> str:
        """The URL of the DIGGS code list ``code_list``."""
        return f"{self.code_list_base}{code_list}.xml"


@dataclass(frozen=True, slots=True, kw_only=True)
class PropertyFacts:
    """What the DIGGS property dictionary says about one property."""

    data_type: str
    has_unit: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class MeasureFacts:
    """What DIGGS 3.0.0 allows as the unit of one measure type.

    ``uoms`` are the enumerated units of its ``uom`` attribute (the ``Other: ...``
    escape the schema also allows is never written by the converter); ``required`` says
    whether the attribute is required.
    """

    uoms: frozenset[str]
    required: bool


def _text(entry: Mapping[str, Any], key: str, where: str, *, required: bool = True) -> str | None:
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: {key!r} must be a non-empty string")
    return value


def _heading(entry: Mapping[str, Any], where: str) -> HeadingRule:
    name = _text(entry, "name", where)
    where = f"{where} heading {name}"
    to = _text(entry, "to", where)
    if to not in TARGETS:
        raise ValueError(f"{where}: unknown target kind {to!r}")
    rule = HeadingRule(
        name=str(name),
        to=to,
        target=_text(entry, "target", where, required=to in ("key", "link", "field", "param")),
        property=_text(entry, "property", where, required=to == "column"),
        reason=_text(entry, "reason", where, required=to in ("none", "format")),
        direction=str(entry.get("direction", "both")),
        quantity=_text(entry, "quantity", where, required=False),
    )
    if rule.quantity is not None and to != "param":
        raise ValueError(f"{where}: only a param has a quantity")
    if rule.direction not in _DIRECTIONS:
        raise ValueError(f"{where}: unknown direction {rule.direction!r}")
    if to == "param" and (rule.target or "").count("/") != 1:
        raise ValueError(f"{where}: a param target is '<Element>/<child>'")
    return rule


def _group(entry: Mapping[str, Any]) -> GroupRule:
    name = str(_text(entry, "name", "group"))
    where = f"group {name}"
    pattern = _text(entry, "pattern", where)
    if pattern not in PATTERNS:
        raise ValueError(f"{where}: unknown pattern {pattern!r}")
    status = _text(entry, "status", where)
    if status not in _STATUSES:
        raise ValueError(f"{where}: unknown status {status!r}")
    headings: dict[str, HeadingRule] = {}
    for item in entry.get("heading", []):
        rule = _heading(item, where)
        if rule.name in headings:
            raise ValueError(f"{where}: heading {rule.name} is listed twice")
        headings[rule.name] = rule
    keys = tuple(str(key) for key in entry.get("keys", []))
    if any(key not in headings for key in keys):
        raise ValueError(f"{where}: every key must be a listed heading")
    params = entry.get("procedure_params", {})
    return GroupRule(
        name=name,
        pattern=pattern,
        status=status,
        keys=keys,
        headings=MappingProxyType(headings),
        parent=_text(entry, "parent", where, required=False),
        element=_text(entry, "element", where, required=False),
        procedure=_text(entry, "procedure", where, required=False),
        procedure_params=MappingProxyType({str(k): str(v) for k, v in params.items()}),
        data=_text(entry, "data", where, required=False),
    )


def _units(table: Mapping[str, Any]) -> dict[str, str]:
    unit_map: dict[str, str] = {}
    for item in table["unit"]:
        ags, diggs = _text(item, "ags", "unit"), _text(item, "diggs", "unit")
        if ags in unit_map:
            raise ValueError(f"unit {ags!r} is listed twice")
        unit_map[str(ags)] = str(diggs)
    return unit_map


def _codes(
    table: Mapping[str, Any],
) -> tuple[dict[tuple[str, str], CodeRule], dict[tuple[str, str], str], dict[str, str]]:
    code_map: dict[tuple[str, str], CodeRule] = {}
    for item in table["code"]:
        rule = CodeRule(
            heading=str(_text(item, "heading", "code")),
            ags=str(_text(item, "ags", "code")),
            element=_text(item, "element", "code", required=False),
            code_list=_text(item, "code_list", "code", required=False),
            code=_text(item, "code", "code", required=False),
            exact=bool(item.get("exact", False)),
        )
        if (rule.code is None) != (rule.code_list is None):
            raise ValueError(f"code {rule.ags!r}: give code and code_list together")
        if (rule.heading, rule.ags) in code_map:
            raise ValueError(f"code {rule.heading}={rule.ags!r} is listed twice")
        code_map[(rule.heading, rule.ags)] = rule
    crs_map: dict[tuple[str, str], str] = {}
    crs_units: dict[str, str] = {}
    for item in table["crs"]:
        key = (str(_text(item, "heading", "crs")), str(_text(item, "ags", "crs")))
        if key in crs_map:
            raise ValueError(f"crs {key!r} is listed twice")
        srs = str(_text(item, "srs", "crs"))
        crs_map[key] = srs
        unit = _text(item, "unit", "crs", required=False)
        if unit is not None and crs_units.setdefault(srs, unit) != unit:
            raise ValueError(f"crs {srs!r} is listed with two units")
    return code_map, crs_map, crs_units


def _build(tables: Mapping[str, Mapping[str, Any]]) -> MappingTable:
    main, units, codes = (tables[name] for name in _TABLES)
    version = _text(main, "mapping_version", "ags4-diggs.toml")
    for name in _TABLES[1:]:
        if tables[name].get("mapping_version") != version:
            raise ValueError(f"{name}: mapping_version differs from ags4-diggs.toml")
    groups: dict[str, GroupRule] = {}
    for entry in main["group"]:
        group = _group(entry)
        if group.name in groups:
            raise ValueError(f"group {group.name} is listed twice")
        groups[group.name] = group
    for group in groups.values():
        for other in (group.parent, group.data):
            if other is not None and other not in groups:
                raise ValueError(f"group {group.name}: {other} is not a listed group")
    unit_map = _units(units)
    code_map, crs_map, crs_units = _codes(codes)
    return MappingTable(
        mapping_version=str(version),
        ags_versions=tuple(str(v) for v in main["ags_versions"]),
        diggs_versions=tuple(str(v) for v in main["diggs_versions"]),
        groups=MappingProxyType(groups),
        units=MappingProxyType(unit_map),
        codes=MappingProxyType(code_map),
        crs=MappingProxyType(crs_map),
        code_list_base=str(_text(codes, "code_list_base", "codes.toml")),
        crs_units=MappingProxyType(crs_units),
    )


def read_table(name: str) -> dict[str, Any]:
    """One bundled table, parsed (``name`` is its file name under ``mappings/``)."""
    text = resources.files("pydiggs.convert").joinpath("mappings", name).read_text("utf-8")
    return tomllib.loads(text)


@cache
def load_mapping() -> MappingTable:
    """The bundled mapping tables (read once, then shared).

    Raises:
        ResourceError: A bundled table is missing or broken.

    Examples:
        >>> mapping = load_mapping()
        >>> mapping.groups["ISPT"].procedure, mapping.groups["ISPT"].status
        ('DrivenPenetrationTest', 'draft')
        >>> mapping.groups["ISPT"].headings["ISPT_NVAL"].property
        'n_value'
    """
    try:
        return _build({name: read_table(name) for name in _TABLES})
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ResourceError(f"bundled AGS4 mapping table is unreadable: {exc}") from exc


@cache
def property_facts() -> Mapping[str, PropertyFacts]:
    """Each property of the bundled DIGGS property dictionary: data type, and whether it has a unit.

    A property with a quantity class needs a unit of measure; one without may not have
    one (the dictionary checks of :func:`pydiggs.validate`).

    Raises:
        ResourceError: The bundled property dictionary is missing or broken.
    """
    try:
        root = _xml.parse(_DEFAULT_DICTIONARY).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ResourceError(f"bundled property dictionary is unreadable: {exc}") from exc
    facts: dict[str, PropertyFacts] = {}
    for definition in root.iter("{*}Definition"):
        key = definition.get(_GML_ID)
        if not key:
            continue
        data_type = next(definition.iter("{*}dataType"), None)
        quantity = next(definition.iter("{*}quantityClass"), None)
        facts[key] = PropertyFacts(
            data_type=(data_type.text or "").strip().lower() if data_type is not None else "",
            has_unit=quantity is not None and bool((quantity.text or "").strip()),
        )
    return MappingProxyType(facts)


def _enumerated(
    name: str, types: Mapping[str, Any], seen: frozenset[str] = frozenset()
) -> set[str]:
    """The values simple type ``name`` (``prefix:local``) enumerates, through unions."""
    if name in seen:
        return set()
    found = types.get(name)
    if found is None:
        raise ValueError(f"no simple type {name}")
    out = {str(e.get("value")) for e in found.iter(f"{_XS}enumeration")}
    for union in found.iter(f"{_XS}union"):
        for member in (union.get("memberTypes") or "").split():
            out |= _enumerated(member, types, seen | {name})
    return out


@cache
def _measure_types() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """The DIGGS 3.0.0 measure types by local name, and every simple type by prefixed name."""
    complex_types: dict[str, Any] = {}
    simple_types: dict[str, Any] = {}
    sources = (("diggs", _MEASURE_TYPES), ("diggs", _COMMON_TYPES), ("eml", _QUANTITY_CLASSES))
    for prefix, path in sources:
        for node in _xml.parse(path).getroot():
            name = node.get("name")
            if node.tag == f"{_XS}simpleType" and name:
                simple_types[f"{prefix}:{name}"] = node
            elif node.tag == f"{_XS}complexType" and name and prefix == "diggs":
                complex_types[name] = node
    return MappingProxyType(complex_types), MappingProxyType(simple_types)


@cache
def measure_facts(quantity: str) -> MeasureFacts:
    """The units DIGGS 3.0.0 allows for ``diggs:<quantity>MeasureType``, from the bundled XSD.

    Raises:
        ResourceError: The bundled schema is unreadable or has no such measure type.

    Examples:
        >>> facts = measure_facts("Dimensionless")
        >>> "%" in facts.uoms, "m" in facts.uoms, facts.required
        (True, False, False)
        >>> measure_facts("ParticleSize").required, "mm" in measure_facts("ParticleSize").uoms
        (True, True)
    """
    try:
        complex_types, simple_types = _measure_types()
        found = complex_types.get(f"{quantity}MeasureType")
        attributes = [] if found is None else list(found.iter(f"{_XS}attribute"))
        uom = next((a for a in attributes if a.get("name") == "uom"), None)
        if uom is None:
            raise ValueError(f"DIGGS 3.0.0 has no {quantity}MeasureType with a uom")
        uoms = _enumerated(str(uom.get("type")), simple_types)
    except (OSError, etree.XMLSyntaxError, ValueError) as exc:
        raise ResourceError(f"bundled DIGGS measure types are unreadable: {exc}") from exc
    return MeasureFacts(uoms=frozenset(uoms), required=uom.get("use") == "required")
