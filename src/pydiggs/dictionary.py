"""DIGGS dictionary / codeSpace semantic validation.

Ports the progressive checks documented in DIGGSml/validation
(modules/dictionary-validation.xsl) for offline use with bundled resources.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from lxml import etree

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_DICT_DIR = _PACKAGE_DIR / "dictionaries"
_DEFAULT_PROPERTIES = _DEFAULT_DICT_DIR / "properties.xml"
_DEFAULT_CODES_DIR = _DEFAULT_DICT_DIR / "codes"
_DEFAULT_WHITELIST = _DEFAULT_DICT_DIR / "whiteList.xml"
_DEFAULT_UOM = _DEFAULT_DICT_DIR / "DiggsUomDictionary.xml"

_SKIP_LOCAL_NAMES = frozenset({"identifier", "internalIdentifier", "axisDirection", "rangeMeaning"})

_GML_NS = "http://www.opengis.net/gml/3.2"
_GML_ID = f"{{{_GML_NS}}}id"
_URL_PREFIXES = ("http://", "https://", "file:", "./", "../")

# Pre-def/codes URLs used widely in diggs-examples; resolve against the current
# properties dictionary first (legacy DIGGSTestPropertyDefinitions lacks quantityClass).
_LEGACY_TEST_PROPERTY_URLS = frozenset(
    {
        "http://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml",
        "https://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml",
        "http://diggsml.org/terms/DIGGSTestPropertyDefinitions.xml",
        "https://diggsml.org/terms/DIGGSTestPropertyDefinitions.xml",
    }
)
_MODERN_PROPERTIES_URL = "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"

# Fragment ids that appear in official examples but were renamed in 0.1 properties.
_PROPERTY_FRAGMENT_ALIASES = {
    "water_depth_calc": "water_depth",
    "pore_water_pressure": "pore_pressure_u2",
}

# DIGGS 2.6 AllUnits uses abbreviated spellings that DiggsUomDictionary expands
# (Kernel.xsd documents cm2/m as "square centimeter per minute").
_UOM_ALIASES = {
    "cm2/m": "cm2/min",
}

# XSD integer-family tokens accepted interchangeably for DIGGS typeData / dataType.
_INTEGER_DATA_TYPES = frozenset(
    {
        "integer",
        "int",
        "long",
        "short",
        "byte",
        "positiveinteger",
        "nonnegativeinteger",
        "negativeinteger",
        "nonpositiveinteger",
        "unsignedbyte",
        "unsignedshort",
        "unsignedint",
        "unsignedlong",
    }
)


def _data_types_compatible(expected: str, actual: str) -> bool:
    """Return True when DIGGS dictionary dataType and instance typeData agree."""
    left = expected.strip().lower()
    right = actual.strip().lower()
    if left == right:
        return True
    return left in _INTEGER_DATA_TYPES and right in _INTEGER_DATA_TYPES


@dataclass
class ValidationMessage:
    """One semantic-validation finding."""

    severity: str
    element_path: str
    text: str
    check: int


@dataclass
class DictionaryValidationResult:
    """Aggregated dictionary validation outcome."""

    messages: list[ValidationMessage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(m.severity == "ERROR" for m in self.messages)

    def add(self, severity: str, path: str, text: str, check: int) -> None:
        self.messages.append(
            ValidationMessage(severity=severity, element_path=path, text=text, check=check)
        )


def _local(el: etree._Element) -> str:
    return str(etree.QName(el).localname)


def _element_path(el: etree._Element) -> str:
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None and isinstance(node.tag, str):
        name = _local(node)
        siblings = [
            s
            for s in node.itersiblings(preceding=True)
            if isinstance(s.tag, str) and _local(s) == name
        ]
        parts.append(f"{name}[{len(siblings) + 1}]")
        parent = node.getparent()
        node = parent if parent is not None else None
    return "/" + "/".join(reversed(parts))


def _text(el: etree._Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return (el.text or "").strip()


def _parse_xpath_segment(segment: str) -> tuple[str, str]:
    """Return (namespace-prefix, local-name) from ``diggs:Foo[1]``."""
    cleaned = segment.strip()
    if "[" in cleaned:
        cleaned = cleaned.split("[", 1)[0]
    if ":" in cleaned:
        prefix, local_name = cleaned.split(":", 1)
        return prefix, local_name
    return "", cleaned


def _element_matches_prefix(el: etree._Element, prefix: str) -> bool:
    if not isinstance(el.tag, str):
        return False
    namespace = etree.QName(el).namespace or ""
    if prefix == "gml":
        return namespace == _GML_NS
    if prefix == "diggs":
        return "diggsml.org" in namespace
    return True


class DictionarySemanticValidator:
    """Offline semantic validator for DIGGS codeSpace-bearing elements."""

    def __init__(
        self,
        *,
        dictionary_path: str | os.PathLike[str] | None = None,
        whitelist_path: str | os.PathLike[str] | None = None,
        uom_dictionary_path: str | os.PathLike[str] | None = None,
        extra_dictionaries: Iterable[str | os.PathLike[str]] | None = None,
        allow_network: bool = False,
    ) -> None:
        self.allow_network = allow_network
        self._whitelist_patterns = self._load_whitelist(
            Path(whitelist_path) if whitelist_path else _DEFAULT_WHITELIST
        )
        self._dict_cache: dict[str, etree._ElementTree] = {}
        self._default_properties = Path(dictionary_path) if dictionary_path else _DEFAULT_PROPERTIES
        self._register_local_dictionary(
            "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml",
            self._default_properties,
        )
        self._register_local_dictionary(
            "http://diggsml.org/def/codes/DIGGS/0.1/properties.xml",
            self._default_properties,
        )
        if _DEFAULT_CODES_DIR.is_dir():
            for xml_path in sorted(_DEFAULT_CODES_DIR.glob("*.xml")):
                filename = xml_path.name
                for scheme in ("https", "http"):
                    url = f"{scheme}://diggsml.org/def/codes/DIGGS/0.1/{filename}"
                    self._register_local_dictionary(url, xml_path)
        legacy = _DEFAULT_DICT_DIR / "DIGGSTestPropertyDefinitions.xml"
        if legacy.is_file():
            for url in (
                "http://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml",
                "https://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml",
                "http://diggsml.org/terms/DIGGSTestPropertyDefinitions.xml",
                "https://diggsml.org/terms/DIGGSTestPropertyDefinitions.xml",
            ):
                self._register_local_dictionary(url, legacy)
        if extra_dictionaries:
            for path in extra_dictionaries:
                p = Path(path)
                self._register_local_dictionary(p.resolve().as_uri(), p)
                self._register_local_dictionary(str(p.resolve()), p)

        self._uom_units_by_class = self._load_uom_map(
            Path(uom_dictionary_path) if uom_dictionary_path else _DEFAULT_UOM
        )

    def _load_whitelist(self, path: Path) -> list[str]:
        if not path.is_file():
            return ["https://diggsml.org", "http://diggsml.org"]
        root = etree.parse(str(path)).getroot()
        return [
            (el.text or "").strip() for el in root.xpath("//pattern") if (el.text or "").strip()
        ]

    def _register_local_dictionary(self, key: str, path: Path) -> None:
        if path.is_file():
            self._dict_cache[key] = etree.parse(str(path))

    def _load_uom_map(self, path: Path) -> dict[str, set[str]]:
        mapping: dict[str, set[str]] = {}
        if not path.is_file():
            return mapping
        root = etree.parse(str(path)).getroot()
        for qc in root.xpath("//*[local-name()='quantityClass']"):
            name_el = next((c for c in qc if isinstance(c.tag, str) and _local(c) == "name"), None)
            if name_el is None:
                continue
            name = _text(name_el)
            units = {
                _text(c)
                for c in qc
                if isinstance(c.tag, str) and _local(c) == "memberUnit" and _text(c)
            }
            mapping[name] = units
        return mapping

    def _is_whitelisted(self, url: str) -> bool:
        return url.startswith(tuple(self._whitelist_patterns))

    def _is_definition_url(self, code_space: str) -> bool:
        if "#" not in code_space:
            return False
        return code_space.startswith(_URL_PREFIXES)

    def _is_legacy_dictionary_url(self, code_space: str) -> bool:
        return "#" not in code_space and code_space.startswith(("http://", "https://", "file:"))

    def _resolve_dictionary(self, base_url: str) -> etree._ElementTree | None:
        if base_url in self._dict_cache:
            return self._dict_cache[base_url]
        path_candidate = (
            Path(unquote(urlparse(base_url).path))
            if base_url.startswith("file:")
            else Path(base_url)
        )
        if path_candidate.is_file():
            tree = etree.parse(str(path_candidate))
            self._dict_cache[base_url] = tree
            return tree
        if self.allow_network and base_url.startswith(("http://", "https://")):
            try:
                tree = etree.parse(base_url)
                self._dict_cache[base_url] = tree
                return tree
            except (OSError, etree.XMLSyntaxError):
                return None
        if base_url.rstrip("/").endswith("properties.xml"):
            return self._dict_cache.get("https://diggsml.org/def/codes/DIGGS/0.1/properties.xml")
        return None

    def _find_definition(
        self, dictionary: etree._ElementTree, fragment: str
    ) -> etree._Element | None:
        fragment = _PROPERTY_FRAGMENT_ALIASES.get(fragment, fragment)
        for el in dictionary.xpath("//*[local-name()='Definition']"):
            gml_id = el.get(_GML_ID) or el.get("id")
            if gml_id == fragment:
                return el
        return None

    def _find_definition_by_value(
        self, dictionary: etree._ElementTree, value: str
    ) -> etree._Element | None:
        value_lower = value.lower()
        aliased = _PROPERTY_FRAGMENT_ALIASES.get(value_lower)
        for el in dictionary.xpath("//*[local-name()='Definition']"):
            gml_id = el.get(_GML_ID) or el.get("id")
            if gml_id == value or (aliased and gml_id == aliased):
                return el
            for name_el in el.xpath(".//*[local-name()='name']"):
                if _text(name_el).lower() == value_lower:
                    return el
        return None

    def _lookup_definition(
        self, base_url: str, *, fragment: str | None = None, value: str | None = None
    ) -> tuple[etree._ElementTree | None, etree._Element | None]:
        """Resolve a Definition, preferring modern properties for legacy test URLs."""
        search_order: list[str] = []
        if base_url in _LEGACY_TEST_PROPERTY_URLS:
            search_order.append(_MODERN_PROPERTIES_URL)
        search_order.append(base_url)

        seen: set[str] = set()
        for url in search_order:
            if url in seen:
                continue
            seen.add(url)
            dictionary = self._resolve_dictionary(url)
            if dictionary is None:
                continue
            if fragment is not None:
                definition = self._find_definition(dictionary, fragment)
            else:
                definition = self._find_definition_by_value(dictionary, value or "")
            if definition is not None:
                return dictionary, definition
        return self._resolve_dictionary(base_url), None

    def _definition_names(self, definition: etree._Element) -> set[str]:
        return {_text(c).lower() for c in definition.xpath(".//*[local-name()='name']") if _text(c)}

    def _source_xpaths(self, definition: etree._Element) -> list[str]:
        return [
            _text(c)
            for c in definition.xpath(
                ".//*[local-name()='occurrences']/*[local-name()='Occurrence']"
                "/*[local-name()='sourceElementXpath']"
            )
            if _text(c)
        ]

    def _conditional_xpaths(self, definition: etree._Element) -> list[str]:
        return [
            _text(c)
            for c in definition.xpath(
                ".//*[local-name()='occurrences']/*[local-name()='Occurrence']"
                "/*[local-name()='conditionalElementXpath']"
            )
            if _text(c)
        ]

    def _evaluate_xpath_match(self, el: etree._Element, xpath_expr: str, *, relative: bool) -> bool:
        """Approximate DIGGSml ``evaluateXPathMatch`` for bundled offline validation."""
        expr = xpath_expr.strip()
        if not expr:
            return True
        if expr.startswith("ancestor::"):
            return self._match_ancestor_xpath(el, expr)
        if expr.startswith("//"):
            return self._match_absolute_xpath(el, expr)
        if relative:
            return self._match_ancestor_xpath(el, f"ancestor::{expr}")
        return self._match_absolute_xpath(el, f"//{expr.lstrip('/')}")

    def _match_absolute_xpath(self, el: etree._Element, xpath_expr: str) -> bool:
        path = xpath_expr.lstrip("/")
        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return False

        parsed = [_parse_xpath_segment(segment) for segment in segments]
        terminal_prefix, terminal_local = parsed[-1]
        if _local(el) != terminal_local or not _element_matches_prefix(el, terminal_prefix):
            return False
        if len(parsed) == 1:
            return True

        needed = list(reversed(parsed[:-1]))
        current: etree._Element | None = el.getparent()
        for prefix, local_name in needed:
            found = False
            while current is not None:
                if (
                    isinstance(current.tag, str)
                    and _local(current) == local_name
                    and _element_matches_prefix(current, prefix)
                ):
                    found = True
                    current = current.getparent()
                    break
                current = current.getparent()
            if not found:
                return False
        return True

    def _match_ancestor_xpath(self, el: etree._Element, xpath_expr: str) -> bool:
        after_axis = xpath_expr.split("::", 1)[1]
        if "//" in after_axis:
            ancestor_part, remainder = after_axis.split("//", 1)
            remainder = f"//{remainder}"
        elif "/" in after_axis:
            ancestor_part, remainder = after_axis.split("/", 1)
        else:
            ancestor_part = after_axis
            remainder = ""

        ancestor_prefix, ancestor_local = _parse_xpath_segment(ancestor_part)
        for ancestor in el.iterancestors():
            if not isinstance(ancestor.tag, str):
                continue
            if _local(ancestor) != ancestor_local or not _element_matches_prefix(
                ancestor, ancestor_prefix
            ):
                continue
            if not remainder:
                return True
            if self._match_within_subtree(ancestor, remainder):
                return True
        return False

    def _match_within_subtree(self, root: etree._Element, path: str) -> bool:
        normalized = path.lstrip("/")
        if normalized.startswith("//"):
            segments = [
                _parse_xpath_segment(segment)
                for segment in normalized.lstrip("/").split("/")
                if segment
            ]
            if not segments:
                return False
            first_prefix, first_local = segments[0]
            for candidate in root.iter():
                if not isinstance(candidate.tag, str):
                    continue
                if _local(candidate) != first_local or not _element_matches_prefix(
                    candidate, first_prefix
                ):
                    continue
                if len(segments) == 1:
                    return True
                if self._match_child_path(candidate, segments[1:]):
                    return True
            return False

        segments = [_parse_xpath_segment(segment) for segment in normalized.split("/") if segment]
        return self._match_child_path(root, segments)

    def _match_child_path(self, node: etree._Element, segments: list[tuple[str, str]]) -> bool:
        if not segments:
            return True
        prefix, local_name = segments[0]
        for child in node:
            if not isinstance(child.tag, str):
                continue
            if _local(child) != local_name or not _element_matches_prefix(child, prefix):
                continue
            if len(segments) == 1:
                return True
            if self._match_child_path(child, segments[1:]):
                return True
        return False

    def _matches_source_xpath(self, el: etree._Element, xpath_expr: str) -> bool:
        return self._evaluate_xpath_match(el, xpath_expr, relative=False)

    def _conditional_ok(self, el: etree._Element, xpath_expr: str) -> bool:
        return self._evaluate_xpath_match(el, xpath_expr, relative=True)

    def _has_measurement_without_procedure(self, el: etree._Element) -> bool:
        has_measurement = self._evaluate_xpath_match(
            el, "ancestor::diggs:measurement", relative=True
        )
        has_procedure = self._evaluate_xpath_match(
            el, "ancestor::diggs:measurement//diggs:procedure", relative=True
        )
        return has_measurement and not has_procedure

    def validate_instance(
        self, instance_path: str | os.PathLike[str]
    ) -> DictionaryValidationResult:
        result = DictionaryValidationResult()
        doc = etree.parse(str(instance_path))
        root = doc.getroot()

        for el in root.xpath("//*[@codeSpace]"):
            if _local(el) in _SKIP_LOCAL_NAMES:
                continue
            self._validate_element(el, result)
        return result

    def _validate_element(self, el: etree._Element, result: DictionaryValidationResult) -> None:
        code_space = el.get("codeSpace") or ""
        if self._is_definition_url(code_space):
            self._validate_fragment_encoding(el, result, code_space)
        elif self._is_legacy_dictionary_url(code_space):
            self._validate_legacy_encoding(el, result, code_space)
        else:
            self._validate_uncontrolled(el, result, code_space)

    def _validate_uncontrolled(
        self, el: etree._Element, result: DictionaryValidationResult, code_space: str
    ) -> None:
        value = _text(el)
        path = _element_path(el)
        name = _local(el)
        result.add(
            "INFO",
            path,
            (
                f"Check 1:\nThe value of {name} cannot be validated against a code list "
                f'dictionary. If "{code_space}" references an authority, be sure that '
                f'the value "{value}" is a valid term controlled by {code_space}.'
            ),
            1,
        )

    def _validate_legacy_encoding(
        self, el: etree._Element, result: DictionaryValidationResult, code_space: str
    ) -> None:
        value = _text(el)
        path = _element_path(el)
        base_url = code_space

        if not self._is_whitelisted(base_url):
            result.add(
                "WARNING",
                path,
                (
                    f'Check 2:\nThe code list dictionary "{base_url}" is not on the white list '
                    "of approved URL's. Choose a DIGGS standard code list dictionary or add "
                    "this URL to a whiteList.xml parameter file and validate locally."
                ),
                2,
            )
            return

        dictionary, definition = self._lookup_definition(base_url, value=value)
        if dictionary is None:
            result.add(
                "WARNING",
                path,
                f'Check 3:\nThe resource at "{base_url}" could not be accessed.',
                3,
            )
            return

        dict_root = dictionary.getroot()
        if _local(dict_root) != "Dictionary":
            result.add(
                "ERROR",
                path,
                f'Check 4:\nThe resource at "{base_url}" \n is not a DIGGS code list dictionary.',
                4,
            )
            return

        if definition is None:
            result.add(
                "ERROR",
                path,
                (
                    f'Check 5:\nCode "{value}" is not found in the code list dictionary '
                    f'at "{base_url}".'
                ),
                5,
            )
            return

        fragment = definition.get(_GML_ID) or definition.get("id") or value
        self._validate_definition_semantics(
            el,
            result,
            base_url=base_url,
            fragment=fragment,
            definition=definition,
            run_name_check=False,
        )

    def _validate_fragment_encoding(
        self, el: etree._Element, result: DictionaryValidationResult, code_space: str
    ) -> None:
        path = _element_path(el)
        name = _local(el)
        base_url, fragment = code_space.split("#", 1)

        if not self._is_whitelisted(base_url):
            result.add(
                "WARNING",
                path,
                (
                    f'Check 2:\nThe code list dictionary "{base_url}" is not on the white list '
                    "of approved URL's. Choose a DIGGS standard code list dictionary or add "
                    "this URL to a whiteList.xml parameter file and validate locally."
                ),
                2,
            )
            return

        dictionary, definition = self._lookup_definition(base_url, fragment=fragment)
        if dictionary is None:
            result.add(
                "WARNING",
                path,
                f'Check 3:\nThe resource at "{base_url}" could not be accessed.',
                3,
            )
            return

        dict_root = dictionary.getroot()
        if _local(dict_root) != "Dictionary":
            result.add(
                "ERROR",
                path,
                f'Check 4:\nThe resource at "{base_url}" \n is not a DIGGS code list dictionary.',
                4,
            )
            return

        if definition is None:
            result.add(
                "ERROR",
                path,
                (
                    f'Check 5:\nCode "{fragment}" is not found in the code list dictionary '
                    f'at "{base_url}".'
                ),
                5,
            )
            return

        resolved_fragment = definition.get(_GML_ID) or definition.get("id") or fragment
        self._validate_definition_semantics(
            el,
            result,
            base_url=base_url,
            fragment=resolved_fragment,
            definition=definition,
            run_name_check=name != "propertyClass",
        )

    def _validate_definition_semantics(
        self,
        el: etree._Element,
        result: DictionaryValidationResult,
        *,
        base_url: str,
        fragment: str,
        definition: etree._Element,
        run_name_check: bool,
    ) -> None:
        path = _element_path(el)
        name = _local(el)
        value = _text(el)

        source_xpaths = self._source_xpaths(definition)
        # DIGGS properties dictionary documentation allows the same codes on
        # Detector/measurand as on Property/propertyClass, but many Occurrence
        # entries only list propertyClass.
        if _local(el) == "measurand" and any("propertyClass" in xp for xp in source_xpaths):
            source_xpaths = [*source_xpaths, "//diggs:measurand"]
        if source_xpaths and not any(self._matches_source_xpath(el, xp) for xp in source_xpaths):
            formatted = "\n     ".join(source_xpaths)
            result.add(
                "ERROR",
                path,
                (
                    f'Check 6:\n"{fragment}" is not a valid code for  <{name}> in this specific '
                    f"context. This element is not matched by any of these allowable xPaths:\n"
                    f"     {formatted}"
                ),
                6,
            )
            return

        if run_name_check:
            names = self._definition_names(definition)
            if value.lower() not in names:
                result.add(
                    "INFO",
                    path,
                    (
                        f'Check 7:\nThe value "{value}" in <{name}> does not match any of the '
                        f'names assigned to its definition in "{base_url}". Be sure that '
                        f'"{value}" is a synonymous term.'
                    ),
                    7,
                )
                return

        if name != "propertyClass":
            return

        definition_name = next(iter(self._definition_names(definition)), fragment)

        if not self._has_measurement_without_procedure(el):
            conditionals = self._conditional_xpaths(definition)
            if conditionals and not any(self._conditional_ok(el, xp) for xp in conditionals):
                targets = []
                for xp in conditionals:
                    term = xp.rstrip("/").split("/")[-1]
                    if ":" in term:
                        term = term.split(":", 1)[1]
                    if "[" in term:
                        term = term.split("[", 1)[0]
                    targets.append(term)
                target_list = "\n     " + "\n     ".join(targets)
                result.add(
                    "ERROR",
                    path,
                    (
                        f'Check 8:\nThe property "{definition_name}" is invalid for this type '
                        f'of measurement. "{definition_name}" is only allowed in the following '
                        f"types of measurements: {target_list}."
                    ),
                    8,
                )
                return

        parent = el.getparent()
        sibling_type = None
        sibling_uom = None
        if parent is not None:
            for child in parent:
                if not isinstance(child.tag, str):
                    continue
                if _local(child) == "typeData":
                    sibling_type = child
                if _local(child) == "uom":
                    sibling_uom = child

        def_data_type_el = next(
            (c for c in definition.xpath(".//*[local-name()='dataType']")), None
        )
        if (
            sibling_type is not None
            and def_data_type_el is not None
            and not _data_types_compatible(_text(def_data_type_el), _text(sibling_type))
        ):
            result.add(
                "ERROR",
                path,
                (
                    f'Check 9:\nThe data type for "{definition_name}" should be '
                    f'"{_text(def_data_type_el)}", but instead is defined as '
                    f'"{_text(sibling_type)}". The value of the sibling <typeData> element '
                    "should match the data type defined in the dictionary."
                ),
                9,
            )
            return

        quantity_el = next(
            (c for c in definition.xpath(".//*[local-name()='quantityClass']")), None
        )
        quantity = _text(quantity_el) if quantity_el is not None else ""
        if not quantity:
            if sibling_uom is not None:
                result.add(
                    "ERROR",
                    path,
                    (
                        f'Check 10:\n"{definition_name}" is defined as unitless. '
                        "The sibling <uom> element should be removed."
                    ),
                    10,
                )
            return

        if sibling_uom is None:
            # DIGGS examples often omit <uom> for dimensionless ratios; treat as advisory.
            severity = "WARNING" if quantity == "dimensionless" else "ERROR"
            result.add(
                severity,
                path,
                (
                    f'Check 11:\n"{definition_name}" requires a unit of measure. '
                    "Add a <uom> element following this <propertyClass>."
                ),
                11,
            )
            return

        uom_value = _text(sibling_uom)
        classes_to_check = (
            ["force per volume", "mass per volume"]
            if quantity == "force or mass per volume"
            else [quantity]
        )
        allowed: set[str] = set()
        for qc in classes_to_check:
            allowed |= self._uom_units_by_class.get(qc, set())

        if not allowed:
            result.add(
                "WARNING",
                path,
                (
                    f'Check 12:\nUnable to validate unit of measure "{uom_value}" for quantity '
                    f'class "{quantity}". The units dictionary has no members for this class.'
                ),
                12,
            )
            return

        canonical_uom = _UOM_ALIASES.get(uom_value, uom_value)
        if canonical_uom not in allowed and uom_value not in allowed:
            preview = ", ".join(sorted(allowed)[:15])
            extra = len(allowed) - 15
            if extra > 0:
                preview = f"{preview}, ... and {extra} more"
            result.add(
                "ERROR",
                path,
                (
                    f'Check 12:\nThe unit of measure "{uom_value}" is not valid for quantity '
                    f'class "{quantity}". Valid units include: {preview}.'
                ),
                12,
            )
