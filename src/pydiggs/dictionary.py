"""DIGGS dictionary / codeSpace semantic validation.

Ports the progressive checks documented in DIGGSml/validation
(modules/dictionary-validation.xsl) for offline use with bundled resources.
"""

from __future__ import annotations

import bisect
import errno
import itertools
import os
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlparse, urlsplit
from urllib.request import url2pathname

from lxml import etree

from pydiggs import _xml

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
    """One semantic-validation finding.

    ``node`` is the element the message is about, when known; it locates the finding
    (line, xpath, gml:id) for :func:`pydiggs.validate` and never affects the text.
    """

    severity: str
    element_path: str
    text: str
    check: int
    node: etree._Element | None = field(default=None, repr=False, compare=False)


@dataclass
class DictionaryValidationResult:
    """Aggregated dictionary validation outcome."""

    messages: list[ValidationMessage] = field(default_factory=list)
    #: Keep at most this many messages (None: all). The rest are only counted in
    #: ``dropped`` as {check: (count, worst severity)}; validate() sets it.
    limit: int | None = None
    dropped: dict[int, tuple[int, str]] = field(default_factory=dict)
    #: validate()'s severity for a message it would report (e.g. offline -> INFO), so
    #: dropped messages are counted at the severity they would have had.
    classify: Callable[[str, str, int], str] | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return not any(m.severity == "ERROR" for m in self.messages) and not any(
            severity == "ERROR" for _, severity in self.dropped.values()
        )

    def add(
        self,
        severity: str,
        path: str,
        text: str,
        check: int,
        node: etree._Element | None = None,
    ) -> None:
        if self.limit is not None and len(self.messages) >= self.limit:
            if self.classify is not None:
                severity = self.classify(severity, text, check)
            count, worst = self.dropped.get(check, (0, severity))
            self.dropped[check] = (count + 1, _worse(worst, severity))
            return
        self.messages.append(
            ValidationMessage(
                severity=severity, element_path=path, text=text, check=check, node=node
            )
        )


_SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "ERROR": 2}


def _worse(a: str, b: str) -> str:
    return a if _SEVERITY_RANK.get(a, 2) >= _SEVERITY_RANK.get(b, 2) else b


def _local(el: etree._Element) -> str:
    return str(etree.QName(el).localname)


def _sibling_positions(root: etree._Element) -> dict[etree._Element, int]:
    """1-based position of every element among its same-named siblings, in one pass."""
    positions: dict[etree._Element, int] = {root: 1}
    for parent in root.iter():
        counts: dict[str, int] = {}
        for child in parent:
            if isinstance(child.tag, str):
                name = _local(child)
                counts[name] = counts.get(name, 0) + 1
                positions[child] = counts[name]
    return positions


def _element_path(el: etree._Element, positions: dict[etree._Element, int] | None = None) -> str:
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None and isinstance(node.tag, str):
        name = _local(node)
        position = positions.get(node) if positions is not None else None
        if position is None:
            # Fallback scan; quadratic over a parent's children, so callers that
            # path many elements pass ``positions`` from _sibling_positions().
            position = 1 + sum(
                1
                for s in node.itersiblings(preceding=True)
                if isinstance(s.tag, str) and _local(s) == name
            )
        parts.append(f"{name}[{position}]")
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
        check_value_code_spaces: bool = False,
        local_dictionaries: str = "legacy",
        legacy_paths: bool = True,
        dictionary_path_wins: bool = False,
        bounded: bool = False,
    ) -> None:
        # Check 13 (official dictionary-validation.xsl Step 14) runs only for
        # pydiggs.validate(); dictionary_check() keeps its 1.1 behaviour.
        self.check_value_code_spaces = check_value_code_spaces
        # "legacy" (dictionary_check): relative paths next to the instance, then the
        # working directory, and any file: URI. validate() uses "instance-dir" or "off".
        if local_dictionaries not in ("legacy", "instance-dir", "off"):
            raise ValueError(f"unknown local_dictionaries mode {local_dictionaries!r}")
        self.local_dictionaries = local_dictionaries
        # validate() passes False: the Locator gives locations, so no legacy path is built.
        self.legacy_paths = legacy_paths
        # validate() passes True: per-Definition path and name caps, a per-instance
        # work budget (both fail closed through ``limits_hit``), Check 6 lists capped at
        # MAX_LISTED_PATHS, and descendant ``//`` paths matched correctly (Check 8).
        self.bounded = bounded
        self.limits_hit: list[str] = []
        self._work = 0
        self._definition_info: dict[etree._Element, _DefinitionPaths] = {}
        self._parsed_paths: dict[str, list[tuple[str, str]]] = {}
        self._ancestor_cache: dict[etree._Element, frozenset[str]] = {}
        self._ancestor_sets: dict[tuple[int, str], tuple[frozenset[str], frozenset[str]]] = {}
        self._strip_cache: dict[str, str] = {}
        self._invalid_cache: dict[tuple[object, ...], list[str]] = {}
        self._columns_cache: dict[etree._Element, frozenset[int]] = {}
        self._local_trees: dict[object, etree._ElementTree | None] = {}
        self._local_by_url: dict[str, etree._ElementTree | None] = {}
        self.local_capped: set[str] = set()
        self.local_over_budget: set[str] = set()
        #: Local dictionaries refused for safety (a markup entity, a libxml2 limit):
        #: URL -> reason. validate() fails closed on them.
        self.local_refused: dict[str, str] = {}
        self._max_bytes: int | None = None
        self._result: DictionaryValidationResult | None = None
        self._check13: _Check13Index | None = None
        self.allow_network = allow_network
        self._whitelist_patterns = self._load_whitelist(
            Path(whitelist_path) if whitelist_path else _DEFAULT_WHITELIST
        )
        self._dict_cache: dict[str, etree._ElementTree] = {}
        self._unreachable: set[str] = set()
        self._positions: dict[etree._Element, int] = {}
        self._instance_dir: Path | None = None
        self._definition_index: dict[etree._ElementTree, dict[str, etree._Element]] = {}
        self._value_index: dict[
            etree._ElementTree,
            tuple[dict[str, tuple[int, etree._Element]], dict[str, tuple[int, etree._Element]]],
        ] = {}
        # Per instance: (ancestor, path) and (node, segments) match results.
        self._subtree_memo: dict[tuple[etree._Element, str], bool] = {}
        self._allowed_terms: dict[etree._ElementTree, tuple[list[str], frozenset[str]]] = {}
        self._child_memo: dict[tuple[etree._Element, int, int], bool] = {}
        self._segment_cache: dict[str, tuple[tuple[str, str], ...]] = {}
        self._sibling_cache: dict[
            etree._Element,
            tuple[etree._Element | None, etree._Element | None, etree._Element | None],
        ] = {}
        self._default_properties = Path(dictionary_path) if dictionary_path else _DEFAULT_PROPERTIES
        self._register_local_dictionary(
            self._default_properties,
            "https://diggsml.org/def/codes/DIGGS/0.1/properties.xml",
            "http://diggsml.org/def/codes/DIGGS/0.1/properties.xml",
        )
        if _DEFAULT_CODES_DIR.is_dir():
            for xml_path in sorted(_DEFAULT_CODES_DIR.glob("*.xml")):
                # The bundled codes/properties.xml is registered under the same URLs as
                # dictionary_path and replaces it (dictionary_check() has always done
                # so). validate() passes dictionary_path_wins=True: the user's file
                # then serves both properties URLs, the legacy test-property URLs
                # (looked up there first) and the *properties.xml fallback.
                if dictionary_path_wins and dictionary_path and xml_path.name == "properties.xml":
                    continue
                self._register_local_dictionary(
                    xml_path,
                    *(
                        f"{scheme}://diggsml.org/def/codes/DIGGS/0.1/{xml_path.name}"
                        for scheme in ("https", "http")
                    ),
                )
        legacy = _DEFAULT_DICT_DIR / "DIGGSTestPropertyDefinitions.xml"
        self._register_local_dictionary(
            legacy,
            "http://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml",
            "https://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml",
            "http://diggsml.org/terms/DIGGSTestPropertyDefinitions.xml",
            "https://diggsml.org/terms/DIGGSTestPropertyDefinitions.xml",
        )
        if extra_dictionaries:
            for path in extra_dictionaries:
                p = Path(path)
                self._register_local_dictionary(p, p.resolve().as_uri(), str(p.resolve()))

        self._uom_units_by_class = self._load_uom_map(
            Path(uom_dictionary_path) if uom_dictionary_path else _DEFAULT_UOM
        )
        # What every instance starts from; validate_tree() resets to it, so dictionaries
        # found next to one instance (or fetched for it) never serve the next one.
        self._baseline_cache = dict(self._dict_cache)

    def _load_whitelist(self, path: Path) -> list[str]:
        if not path.is_file():
            return ["https://diggsml.org", "http://diggsml.org"]
        root = _xml.parse(str(path)).getroot()
        return [
            (el.text or "").strip() for el in root.xpath("//pattern") if (el.text or "").strip()
        ]

    def _register_local_dictionary(self, path: Path, *keys: str) -> None:
        """Parse ``path`` once and serve it for every URL in ``keys``."""
        if path.is_file():
            tree = _xml.parse(str(path))
            for key in keys:
                self._dict_cache[key] = tree

    def _load_uom_map(self, path: Path) -> dict[str, set[str]]:
        mapping: dict[str, set[str]] = {}
        if not path.is_file():
            return mapping
        root = _xml.parse(str(path)).getroot()
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

    def _local_candidates(self, base_url: str) -> list[Path]:
        """Local files a codeSpace may name: relative ones next to the instance first."""
        if self.local_dictionaries != "legacy":
            return self._contained_candidate(base_url)
        path = (
            Path(unquote(urlparse(base_url).path))
            if base_url.startswith("file:")
            else Path(base_url)
        )
        if path.is_absolute() or self._instance_dir is None:
            return [path]
        return [self._instance_dir / path, path]

    def _contained_candidate(self, base_url: str) -> list[Path]:
        """``instance-dir`` mode: the one file inside the instance's folder, or nothing.

        Relative codeSpaces resolve against the instance's folder only (no working
        directory fallback); ``file:`` URIs and paths that leave the folder, through
        ``..`` or a symlink, are refused before the file is opened, so a refusal looks
        the same whether or not the file exists. ``off`` refuses every local file. A
        path the OS cannot handle (an embedded NUL, a symlink loop, a name that is too
        long) is refused the same way.
        """
        if (
            self.local_dictionaries == "off"
            or self._instance_dir is None
            or base_url.startswith(("http://", "https://"))
        ):
            return []
        try:
            if base_url.startswith("file:"):
                path = Path(url2pathname(urlsplit(base_url).path))
            else:
                path = Path(base_url)
            if str(path).startswith(("//", "\\\\")):
                return []
            folder = self._instance_dir
            candidate = path if path.is_absolute() else folder / path
            # Lexical check first, so nothing outside the folder is even looked at.
            if not Path(os.path.normpath(candidate)).is_relative_to(folder):
                return []
            resolved = candidate.resolve()
        except (OSError, ValueError, RuntimeError):
            return []
        return [resolved] if resolved.is_relative_to(folder) else []

    def _resolve_local(self, base_url: str) -> etree._ElementTree | None:
        """``instance-dir`` / ``off`` mode: read a contained local dictionary, once.

        Each file is read once per instance however it is spelled (keyed by device and
        inode), only if it is a regular file, and within one byte budget shared by all
        local dictionaries of the instance (validate() passes what ``max_bytes`` leaves
        after the instance itself). At most
        :data:`MAX_LOCAL_DICTIONARIES` different files are read per instance; the URLs
        refused for that reason are kept in ``local_capped``, and those refused because
        the byte budget ran out in ``local_over_budget``.
        """
        if base_url in self._local_by_url:
            return self._local_by_url[base_url]
        tree: etree._ElementTree | None = None
        for path in self._contained_candidate(base_url):
            try:
                info = path.stat()
            except (OSError, ValueError):
                break
            identity: object = (info.st_dev, info.st_ino) if info.st_ino else str(path)
            if identity in self._local_trees:
                tree = self._local_trees[identity]
                break
            if len(self._local_trees) >= MAX_LOCAL_DICTIONARIES:
                self.local_capped.add(base_url)
                break
            try:
                if self._max_bytes is not None and self._max_bytes < 1:
                    raise OSError(errno.EFBIG, "local dictionary byte budget is used up")
                data, _ = _xml.read_limited(path, self._max_bytes, regular_only=True)
                if self._max_bytes is not None:
                    self._max_bytes -= len(data)  # one budget for all local dictionaries
                entity = _xml.markup_entity(data)
                tree = None if entity is not None else _xml.parse_bytes(data, str(path))
                entity = entity if tree is None else _xml.dtd_markup_entity(tree)
                if entity is not None:
                    tree = None
                    self.local_refused[base_url] = (
                        f"its internal DTD declares the markup entity {entity[:200]!r}"
                    )
            except OSError as exc:
                if exc.errno == errno.EFBIG:
                    self.local_over_budget.add(base_url)
                tree = None
            except etree.XMLSyntaxError as exc:
                errors = getattr(exc, "parser_errors", [])  # this parse's errors only
                limit = next((e for e in errors if _is_parser_limit(e)), None)
                if limit is not None:
                    self.local_refused[base_url] = f"libxml2 refused it ({limit.message})"
                tree = None
            except ValueError:
                tree = None
            self._local_trees[identity] = tree
        self._local_by_url[base_url] = tree
        return tree

    def _resolve_dictionary(self, base_url: str) -> etree._ElementTree | None:
        if base_url in self._dict_cache:
            return self._dict_cache[base_url]
        if self.local_dictionaries != "legacy" and not base_url.startswith(("http://", "https://")):
            tree = self._resolve_local(base_url)
            if tree is not None:
                return tree
            if base_url.rstrip("/").endswith("properties.xml"):
                return self._dict_cache.get(_MODERN_PROPERTIES_URL)
            return None
        for path_candidate in self._local_candidates(base_url):
            # A leading // or \\ is a UNC path: on Windows, merely testing it opens an
            # SMB connection that sends the user's NTLM credentials to that host.
            if str(path_candidate).startswith(("//", "\\\\")) or not path_candidate.is_file():
                continue
            key = base_url if path_candidate.is_absolute() else str(path_candidate.resolve())
            if key in self._dict_cache:
                return self._dict_cache[key]
            try:
                tree = _xml.parse(str(path_candidate))
            except (OSError, etree.XMLSyntaxError):
                return None
            self._dict_cache[key] = tree
            return tree
        if (
            self.allow_network
            and base_url.startswith(("http://", "https://"))
            and base_url not in self._unreachable
        ):
            try:
                tree = _xml.fetch_and_parse(base_url, self._whitelist_patterns)
            except (OSError, etree.XMLSyntaxError):
                # Remember the failure: one file can repeat a codeSpace thousands of times.
                self._unreachable.add(base_url)
                return None
            self._dict_cache[base_url] = tree
            return tree
        if base_url.rstrip("/").endswith("properties.xml"):
            return self._dict_cache.get("https://diggsml.org/def/codes/DIGGS/0.1/properties.xml")
        return None

    def _find_definition(
        self, dictionary: etree._ElementTree, fragment: str
    ) -> etree._Element | None:
        fragment = _PROPERTY_FRAGMENT_ALIASES.get(fragment, fragment)
        index = self._definition_index.get(dictionary)
        if index is None:
            # One pass per dictionary; first Definition with a given id wins, as before.
            index = {}
            for el in dictionary.xpath("//*[local-name()='Definition']"):
                gml_id = el.get(_GML_ID) or el.get("id")
                if gml_id is not None:
                    index.setdefault(gml_id, el)
            self._definition_index[dictionary] = index
        return index.get(fragment)

    def _find_definition_by_value(
        self, dictionary: etree._ElementTree, value: str
    ) -> etree._Element | None:
        """The first Definition, in document order, matching ``value`` by id or name.

        A Definition matches when its id is ``value`` (or its alias) or one of its names
        equals ``value`` ignoring case. Each dictionary is indexed once; this used to rescan every Definition and its
        names for every codeSpace in the instance.
        """
        index = self._value_index.get(dictionary)
        if index is None:
            by_id: dict[str, tuple[int, etree._Element]] = {}
            by_name: dict[str, tuple[int, etree._Element]] = {}
            for position, el in enumerate(dictionary.xpath("//*[local-name()='Definition']")):
                gml_id = el.get(_GML_ID) or el.get("id")
                if gml_id is not None:
                    by_id.setdefault(gml_id, (position, el))
                for name_el in el.xpath(".//*[local-name()='name']"):
                    by_name.setdefault(_text(name_el).lower(), (position, el))
            index = (by_id, by_name)
            self._value_index[dictionary] = index
        by_id, by_name = index
        value_lower = value.lower()
        aliased = _PROPERTY_FRAGMENT_ALIASES.get(value_lower)
        hits = [by_id.get(value), by_id.get(aliased) if aliased else None, by_name.get(value_lower)]
        found = [hit for hit in hits if hit is not None]
        return min(found, key=lambda hit: hit[0])[1] if found else None

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

    def _paths(self, definition: etree._Element) -> _DefinitionPaths:
        """What the checks read from a Definition, read once per instance (not per use)."""
        info = self._definition_info.get(definition)
        if info is None:
            size = sum(1 for _ in itertools.islice(definition.iter(), MAX_DEFINITION_NODES + 1))
            self._spend(size)
            if self.bounded and size > MAX_DEFINITION_NODES:
                info = _DefinitionPaths.too_big()
            else:
                info = _DefinitionPaths(
                    _PathGroup(self._source_xpaths(definition), strip=self.bounded),
                    _PathGroup(self._conditional_xpaths(definition), strip=self.bounded),
                    self._definition_names(definition),
                    definition,
                )
            self._definition_info[definition] = info
        return info

    def _siblings(
        self, parent: etree._Element | None
    ) -> tuple[etree._Element | None, etree._Element | None, etree._Element | None]:
        """A parent's last ``typeData`` and ``uom`` and first ``nullValue`` children.

        Read once per parent: a property with many coded siblings would otherwise
        rescan them for every one.
        """
        if parent is None:
            return None, None, None
        found = self._sibling_cache.get(parent)
        if found is None:
            self._spend(len(parent))
            type_data = uom = null_value = None
            for child in parent:
                if not isinstance(child.tag, str):
                    continue
                local = _local(child)
                if local == "typeData":
                    type_data = child
                elif local == "uom":
                    uom = child
                elif local == "nullValue" and null_value is None:
                    null_value = child
            found = (type_data, uom, null_value)
            self._sibling_cache[parent] = found
        return found

    def _segments(self, path: str) -> tuple[tuple[str, str], ...]:
        """The (prefix, local) steps of a relative path, parsed once per instance."""
        segments = self._segment_cache.get(path)
        if segments is None:
            steps = [step for step in path.split("/") if step]
            self._spend(len(steps))
            if self.bounded and len(steps) > MAX_PATH_STEPS:
                raise _LimitError(f"a dictionary path has more than {MAX_PATH_STEPS} steps")
            segments = tuple(_parse_xpath_segment(step) for step in steps)
            self._segment_cache[path] = segments
        return segments

    def _parsed(self, xpath_expr: str) -> list[tuple[str, str]]:
        """The (prefix, local) steps of an absolute path, parsed once per instance."""
        parsed = self._parsed_paths.get(xpath_expr)
        if parsed is None:
            segments = [segment for segment in xpath_expr.lstrip("/").split("/") if segment]
            self._spend(len(segments))
            if self.bounded and len(segments) > MAX_PATH_STEPS:
                raise _LimitError(f"a dictionary path has more than {MAX_PATH_STEPS} steps")
            parsed = [_parse_xpath_segment(segment) for segment in segments]
            self._parsed_paths[xpath_expr] = parsed
        return parsed

    def _stripped(self, path: str) -> str:
        """``path`` without predicates, once per instance."""
        stripped = self._strip_cache.get(path)
        if stripped is None:
            self._spend(1 + len(path) // 64)
            stripped = self._strip_cache[path] = _strip_predicates(path)
        return stripped

    def _ancestor_names(self, el: etree._Element) -> frozenset[str]:
        """Local names of ``el``'s ancestors, computed once per parent.

        Equal sets are shared: a parent whose name is already in its own ancestors'
        set reuses that set, and one set is kept per (ancestors' set, name), so many
        parents under one deep chain do not each copy the chain.
        """
        parent = el.getparent()
        if parent is None:
            return frozenset()
        names = self._ancestor_cache.get(parent)
        if names is None:
            above = self._ancestor_names(parent)
            local = _local(parent)
            if local in above:
                names = above
            else:
                key = (id(above), local)
                shared = self._ancestor_sets.get(key)
                if shared is None:
                    self._spend(len(above) + 1)
                    names = above | {local}
                    self._ancestor_sets[key] = (above, names)  # keeps ``above`` alive
                else:
                    names = shared[1]
            self._ancestor_cache[parent] = names
        return names

    def _spend(self, units: int = 1) -> None:
        """Count path-matching work; past the budget, stop (validate() only)."""
        self._work += units
        if self.bounded and self._work > MAX_PATH_MATCH_WORK:
            raise _LimitError(
                f"matching dictionary paths took more than {MAX_PATH_MATCH_WORK:,} steps"
            )

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
        self._spend()
        expr = xpath_expr.strip()
        if self.bounded:  # predicates are ignored; a "/" inside one is not a step
            stripped = self._stripped(expr)
            if expr and not stripped:
                return False  # nothing but a predicate: it names no element
            expr = stripped
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
        parsed = self._parsed(xpath_expr)
        if not parsed:
            return False
        terminal_prefix, terminal_local = parsed[-1]
        if _local(el) != terminal_local or not _element_matches_prefix(el, terminal_prefix):
            return False
        if len(parsed) == 1:
            return True
        if self.bounded:
            # Every step must name some ancestor; most paths fail here, in O(steps).
            self._spend()
            names = self._ancestor_names(el)
            if any(local not in names for _, local in parsed[:-1]):
                return False

        needed = list(reversed(parsed[:-1]))
        current: etree._Element | None = el.getparent()
        for prefix, local_name in needed:
            found = False
            while current is not None:
                self._spend()
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
            self._spend()
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
        key = (root, path)
        if key not in self._subtree_memo:
            self._spend(_MEMO_ENTRY_COST)
            self._subtree_memo[key] = self._match_within_subtree_uncached(root, path)
        return self._subtree_memo[key]

    def _match_within_subtree_uncached(self, root: etree._Element, path: str) -> bool:
        # dictionary_check() strips every leading "/" first, so its "//" branch never
        # runs and "a//b" is read as "a/b" (1.1 behaviour, kept). validate() reads
        # "//b" as any descendant, as XPath does (Check 8).
        normalized = path if self.bounded else path.lstrip("/")
        if normalized.startswith("//"):
            segments = self._segments(normalized.lstrip("/"))
            if not segments:
                return False
            first_prefix, first_local = segments[0]
            for candidate in root.iter():
                self._spend()
                if not isinstance(candidate.tag, str):
                    continue
                if _local(candidate) != first_local or not _element_matches_prefix(
                    candidate, first_prefix
                ):
                    continue
                if len(segments) == 1:
                    return True
                if self._match_child_path(candidate, segments, 1):
                    return True
            return False
        return self._match_child_path(root, self._segments(normalized), 0)

    def _match_child_path(
        self, node: etree._Element, segments: tuple[tuple[str, str], ...], offset: int
    ) -> bool:
        """Whether ``segments[offset:]`` names a child path below ``node`` (memoised).

        ``segments`` comes from :meth:`_segments`, which keeps one tuple per path, so
        the memo key holds its identity and an offset: nothing is copied per step.
        """
        key = (node, id(segments), offset)
        if key not in self._child_memo:
            self._spend(_MEMO_ENTRY_COST)  # bounds the memo's memory with the budget
            self._child_memo[key] = self._match_child_path_uncached(node, segments, offset)
        return self._child_memo[key]

    def _match_child_path_uncached(
        self, node: etree._Element, segments: tuple[tuple[str, str], ...], offset: int
    ) -> bool:
        if offset >= len(segments):
            return True
        prefix, local_name = segments[offset]
        for child in node:
            self._spend()
            if not isinstance(child.tag, str):
                continue
            if _local(child) != local_name or not _element_matches_prefix(child, prefix):
                continue
            if offset + 1 == len(segments):
                return True
            if self._match_child_path(child, segments, offset + 1):
                return True
        return False

    def _listed(self, paths: list[str]) -> list[str]:
        """validate() lists at most MAX_LISTED_PATHS paths in a message, then a count."""
        if not self.bounded or len(paths) <= MAX_LISTED_PATHS:
            return paths
        return [*paths[:MAX_LISTED_PATHS], f"... and {len(paths) - MAX_LISTED_PATHS:,} more"]

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
        return self.validate_tree(_xml.parse(str(instance_path)), instance_path)

    def release(self) -> None:
        """Drop everything kept for the last instance (validate() calls it when done).

        The validator itself is cached between files; without this, its caches would
        keep the last instance's whole tree alive.
        """
        self._result = None
        self._check13 = None
        self._instance_dir = None
        self._positions = {}
        self._dict_cache = dict(self._baseline_cache)
        self._local_trees = {}
        self._local_by_url = {}
        self.local_capped = set()
        self.local_over_budget = set()
        self.local_refused = {}
        self._unreachable = set()
        self._subtree_memo = {}
        self._child_memo = {}
        self._segment_cache = {}
        self._sibling_cache = {}
        self._definition_info = {}
        self._parsed_paths = {}
        self._ancestor_cache = {}
        self._ancestor_sets = {}
        self._strip_cache = {}
        self._invalid_cache = {}
        self._columns_cache = {}
        self.limits_hit = []
        self._work = 0
        self._allowed_terms = {}
        baseline = set(map(id, self._baseline_cache.values()))
        self._definition_index = {
            tree: index for tree, index in self._definition_index.items() if id(tree) in baseline
        }
        self._value_index = {
            tree: index for tree, index in self._value_index.items() if id(tree) in baseline
        }

    def _path_of(self, el: etree._Element) -> str:
        """The legacy element path ("" in validate() mode or once the result is full)."""
        result = self._result
        if not self.legacy_paths or (
            result is not None and result.limit is not None and len(result.messages) >= result.limit
        ):
            return ""
        return _element_path(el, self._positions)

    def validate_tree(
        self,
        doc: etree._ElementTree,
        instance_path: str | os.PathLike[str],
        *,
        max_messages: int | None = None,
        max_bytes: int | None = None,
        classify: Callable[[str, str, int], str] | None = None,
    ) -> DictionaryValidationResult:
        """Validate an already-parsed instance; ``instance_path`` anchors relative URLs.

        With ``max_messages``, messages past that number are only counted.
        ``max_bytes`` limits each local dictionary read in ``instance-dir`` mode.
        """
        result = DictionaryValidationResult(limit=max_messages, classify=classify)
        self._result = result
        self._instance_dir = Path(instance_path).resolve().parent
        root = doc.getroot()
        self._positions = _sibling_positions(root) if self.legacy_paths else {}
        self._check13 = None
        self._dict_cache = dict(self._baseline_cache)
        self._unreachable = set()
        self._local_trees = {}
        self._local_by_url = {}
        self.local_capped = set()
        self.local_over_budget = set()
        self.local_refused = {}
        self._max_bytes = max_bytes
        baseline = set(map(id, self._baseline_cache.values()))
        self._definition_index = {
            tree: index for tree, index in self._definition_index.items() if id(tree) in baseline
        }
        self._value_index = {
            tree: index for tree, index in self._value_index.items() if id(tree) in baseline
        }
        self._subtree_memo = {}
        self._child_memo = {}
        self._segment_cache = {}
        self._sibling_cache = {}
        self._definition_info = {}
        self._parsed_paths = {}
        self._ancestor_cache = {}
        self._ancestor_sets = {}
        self._strip_cache = {}
        self._invalid_cache = {}
        self._columns_cache = {}
        self.limits_hit = []
        self._work = 0
        self._allowed_terms = {}

        for el in root.xpath("//*[@codeSpace]"):
            if _local(el) in _SKIP_LOCAL_NAMES:
                continue
            start = len(result.messages)
            try:
                self._validate_element(el, result)
            except _LimitError as exc:
                self.limits_hit.append(str(exc))
                break
            finally:
                for message in result.messages[start:]:
                    message.node = el
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
        path = self._path_of(el)
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
        path = self._path_of(el)
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
        path = self._path_of(el)
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
        path = self._path_of(el)
        name = _local(el)
        value = _text(el)

        info = self._paths(definition)
        if self.bounded and (why := info.over_limits()):
            self.limits_hit.append(why)
            return
        source_xpaths = info.sources.paths
        # DIGGS properties dictionary documentation allows the same codes on
        # Detector/measurand as on Property/propertyClass, but many Occurrence
        # entries only list propertyClass.
        candidates = info.sources.candidates(name)
        self._spend(len(candidates))
        if name == "measurand" and info.sources.mentions_property_class:
            source_xpaths = [*source_xpaths, "//diggs:measurand"]
            candidates = [*candidates, "//diggs:measurand"]
        if source_xpaths and not any(self._matches_source_xpath(el, xp) for xp in candidates):
            formatted = "\n     ".join(self._listed(source_xpaths))
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
            names = info.names
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

        # First name in document order (a set's order depends on PYTHONHASHSEED).
        definition_name = info.first_name or fragment

        if not self._has_measurement_without_procedure(el):
            conditionals = info.conditionals.paths
            relative = info.conditionals.relative_candidates(name)
            self._spend(len(relative))
            if conditionals and not any(self._conditional_ok(el, xp) for xp in relative):
                targets = []
                for xp in self._listed(conditionals):
                    shown = self._stripped(xp) if self.bounded else xp
                    term = shown.rstrip("/").split("/")[-1]
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
        sibling_type, sibling_uom, _ = self._siblings(parent)
        def_data_type_el = info.data_type
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

        quantity_el = info.quantity_class
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
            self._check_value_code_space(el, result, definition=definition, path=path)
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
            self._check_value_code_space(el, result, definition=definition, path=path)
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
            return
        self._check_value_code_space(el, result, definition=definition, path=path)

    def _check_value_code_space(
        self,
        el: etree._Element,
        result: DictionaryValidationResult,
        *,
        definition: etree._Element,
        path: str,
    ) -> None:
        """Check 13: result values must be terms of the definition's valueCodeSpace.

        A port of DIGGSml/validation ``dictionary-validation.xsl`` Step 14 (messages
        labelled "Check 13"): the values in column ``Property/@index`` of the
        ``dataValues`` of the ResultSet holding these parameters, and of every ResultSet
        citing them by ``xlink:href``, must be ``gml:id``s of the code list's
        Definitions. Empty and ``nullValue`` values are allowed.
        """
        if not self.check_value_code_spaces:
            return
        info = self._paths(definition)
        vcs_el = info.value_code_space
        value_code_space = _normalize_space(vcs_el.text if vcs_el is not None else "")
        if not value_code_space:
            return
        name_el = info.name_child
        definition_name = name_el.xpath("string()") if name_el is not None else ""
        code_list_url = value_code_space.split("#", 1)[0]
        if not self._is_whitelisted(code_list_url):
            result.add(
                "WARNING",
                path,
                (
                    f'Check 13:\nThe values of "{definition_name}" come from the code list '
                    f'"{code_list_url}", which is not on the white list of approved URL\'s, '
                    "so the values were not checked."
                ),
                13,
            )
            return
        code_list = self._resolve_dictionary(code_list_url)
        if code_list is None:
            result.add(
                "WARNING",
                path,
                (
                    f'Check 13:\nThe code list "{code_list_url}" for the values of '
                    f'"{definition_name}" could not be accessed, so the values were not checked.'
                ),
                13,
            )
            return
        known = self._allowed_terms.get(code_list)
        if known is None:
            listed = [
                str(v)
                for v in code_list.xpath("//*[local-name()='Definition']/@*[local-name()='id']")
            ]
            self._spend(len(listed))
            known = self._allowed_terms[code_list] = (listed, frozenset(listed))
        allowed = known[0]
        invalid = self._invalid_result_values(el, known[1])
        if invalid:
            shown = ", ".join(f'"{v}"' for v in invalid[:_CHECK13_SHOWN])
            more = " ..." if len(invalid) > _CHECK13_SHOWN else ""
            terms = ", ".join(allowed[:_CHECK13_TERMS])
            if len(allowed) > _CHECK13_TERMS:
                terms += f", ... ({len(allowed)} total)"
            result.add(
                "ERROR",
                path,
                (
                    f'Check 13:\n{len(invalid)} value(s) of "{definition_name}" are not terms '
                    f'of the code list "{code_list_url}": {shown}{more}. '
                    f"Valid terms: {terms}."
                ),
                13,
            )

    def _wanted_columns(self, params: etree._Element) -> frozenset[int]:
        """The column indexes the Properties of one PropertyParameters ask for."""
        wanted = self._columns_cache.get(params)
        if wanted is None:
            found = (_xs_integer(p.get("index")) for p in params.iter("{*}Property"))
            wanted = self._columns_cache[params] = frozenset(c for c in found if c and c > 0)
            self._spend(len(wanted) + 1)
        return wanted

    def _check13_index(self, root: etree._ElementTree) -> _Check13Index:
        # getroottree() returns a new wrapper each call; the root element is stable.
        if self._check13 is None or self._check13.root.getroot() is not root.getroot():
            self._check13 = _Check13Index(root, bounded=self.bounded)
        return self._check13

    def _invalid_result_values(self, el: etree._Element, allowed: frozenset[str]) -> list[str]:
        """Distinct values in this property's dataValues column that are not ``allowed``."""
        prop = el.getparent()
        if prop is None:
            return []
        column = _xs_integer(prop.get("index"))
        if column is None or column < 1:
            return []
        null_el = self._siblings(prop)[2]
        null_text = _normalize_space(null_el.xpath("string()") if null_el is not None else "")
        params = next((a for a in el.iterancestors() if _local(a) == "PropertyParameters"), None)
        if params is None:
            return []
        index = self._check13_index(params.getroottree())
        params_id = next(
            (str(v) for k, v in params.attrib.items() if etree.QName(k).localname == "id"), ""
        )
        result_sets = [a for a in params.iterancestors() if _local(a) == "ResultSet"][:1]
        if params_id:
            result_sets += index.citing(f"#{params_id}")
        self._spend(len(result_sets))  # properties x citing ResultSets
        wanted = self._wanted_columns(params)
        # XPath "$resultSets/*" de-duplicates and returns document order.
        ordered = tuple(index.in_document_order(result_sets))
        key = (ordered, column, null_text, id(allowed))
        cached = self._invalid_cache.get(key)
        if cached is None:
            candidates: dict[str, None] = {}
            for result_set in ordered:
                candidates.update(dict.fromkeys(index.column(result_set, column, wanted)))
            self._spend(len(candidates))
            cached = [v for v in candidates if v and v != null_text and v not in allowed]
            self._invalid_cache[key] = cached
        return cached


class _Check13Index:
    """Per-document lookups for Check 13, built once instead of once per property.

    No rows are kept: each column's distinct values are read in one streaming pass over
    the ResultSet's dataValues and only those values are cached, so memory grows with
    the distinct values, not the cells. validate() reads at most MAX_CHECK13_FIELDS
    fields per document (a 10 MB file holds at most about 5 million).
    """

    def __init__(self, root: etree._ElementTree, *, bounded: bool) -> None:
        self.root = root
        self._bounded = bounded
        self._fields = 0
        self._shared: dict[tuple[str, ...], tuple[str, ...]] = {}
        self._order: dict[etree._Element, int] | None = None
        self._citing: dict[str, list[etree._Element]] | None = None
        self._distinct: dict[etree._Element, dict[int, tuple[str, ...]]] = {}

    def citing(self, ref: str) -> list[etree._Element]:
        """ResultSets whose ``parameters/@*:href`` equals ``ref``."""
        if self._citing is None:
            self._citing = {}
            for rs in self.root.iter():
                if not isinstance(rs.tag, str) or _local(rs) != "ResultSet":
                    continue
                for child in rs:
                    if isinstance(child.tag, str) and _local(child) == "parameters":
                        for key, value in child.attrib.items():
                            if etree.QName(key).localname == "href":
                                self._citing.setdefault(str(value), []).append(rs)
        return self._citing.get(ref, [])

    def in_document_order(self, elements: list[etree._Element]) -> list[etree._Element]:
        """``elements`` (all ResultSets) de-duplicated, in document order."""
        if self._order is None:  # only ResultSets are ordered: no map of the whole tree
            self._order = {el: i for i, el in enumerate(self.root.iter("{*}ResultSet"))}
        order = self._order
        return sorted(dict.fromkeys(elements), key=lambda el: order.get(el, 0))

    @staticmethod
    def _rows(result_set: etree._Element) -> Iterator[tuple[re.Pattern[str], str]]:
        """The ResultSet's dataValues rows, unsplit, each with its field separator."""
        for block in result_set:
            if not isinstance(block.tag, str) or _local(block) != "dataValues":
                continue
            cs = block.get("cs") or ","
            ts = block.get("ts") or " "
            ts_pattern = _XPATH_WS_RUN if _normalize_space(ts) == "" else re.compile(re.escape(ts))
            cs_pattern = re.compile(re.escape(cs))
            for token in ts_pattern.split(_normalize_space(block.xpath("string()"))):
                if token:
                    yield cs_pattern, token

    def column(
        self, result_set: etree._Element, column: int, wanted: frozenset[int] = frozenset()
    ) -> Iterable[str]:
        """Distinct values in ``column`` (1-based) of the ResultSet's dataValues, in order.

        One streaming pass reads ``column`` and the other ``wanted`` columns (the
        indexes the citing properties ask for) not read yet; each row is split only
        up to the last of them, and only their distinct values are kept, so a very
        wide row costs neither memory nor a split of every field. Fields are counted as
        they are read; past MAX_CHECK13_FIELDS validate() stops at once.
        """
        columns = self._distinct.setdefault(result_set, {})
        if column not in columns:
            need = sorted(c for c in wanted | {column} if c not in columns and c >= 1)
            last = need[-1]
            seen: dict[int, dict[str, None]] = {c: {} for c in need}
            for cs_pattern, token in self._rows(result_set):
                parts = cs_pattern.split(token, maxsplit=last)
                self._fields += min(len(parts), last)
                if self._bounded and self._fields > MAX_CHECK13_FIELDS:
                    raise _LimitError(
                        f"Check 13 would read more than {MAX_CHECK13_FIELDS:,} dataValues fields"
                    )
                # Only the wanted columns this row has: never all wanted per row.
                for i in range(bisect.bisect_right(need, len(parts))):
                    c = need[i]
                    seen[c].setdefault(_normalize_space(parts[c - 1]), None)
            shared = self._shared  # equal columns (often one repeated value) share a tuple
            for c, values in seen.items():
                found = tuple(values)
                columns[c] = shared.setdefault(found, found)
        return columns[column]


_XS_INTEGER = re.compile(r"[+-]?[0-9]+")


def _xs_integer(text: str | None) -> int | None:
    """Parse an xs:integer (optional sign, ASCII digits; no underscores or other digits)."""
    value = _normalize_space(text)
    if not _XS_INTEGER.fullmatch(value):
        return None
    digits = value.lstrip("+-").lstrip("0")
    if len(digits) > _MAX_INTEGER_DIGITS:
        return None  # no real column index; int() refuses over 4,300 digits anyway
    number = int(digits or "0")  # leading zeros count towards int()'s digit limit too
    return -number if value.startswith("-") else number


_MAX_INTEGER_DIGITS = 18
#: Work units charged per memo entry, so the budget also caps memo memory (about
#: 1.25 million entries at MAX_PATH_MATCH_WORK).
_MEMO_ENTRY_COST = 4


#: Most local dictionaries read for one instance in ``instance-dir`` mode.
MAX_LOCAL_DICTIONARIES = 16
#: validate(): most source paths, conditional paths or names one Definition may list
#: (the bundled dictionaries list at most 9 paths); above it the element is not
#: checked and the step is incomplete.
MAX_DEFINITION_PATHS = 1_000
#: validate(): most path-matching steps per instance (the largest corpus file needs 30,000).
MAX_PATH_MATCH_WORK = 5_000_000
#: validate(): most dataValues fields Check 13 reads per document.
MAX_CHECK13_FIELDS = 20_000_000
#: validate(): most steps in one dictionary path.
MAX_PATH_STEPS = 64
#: validate(): most nodes in one dictionary Definition (real ones have under 100).
MAX_DEFINITION_NODES = 10_000
#: validate(): paths listed in one Check 6 or Check 8 message.
MAX_LISTED_PATHS = 20


class _LimitError(Exception):
    """A validate()-only limit was reached; the message says which."""


class _PathGroup:
    """One Definition's source (or conditional) paths, grouped for cheap matching.

    A path that is not an ``ancestor::`` path can only match an element whose local
    name is the path's last step, so only those paths are tried for an element.
    """

    def __init__(self, paths: list[str], *, strip: bool = False) -> None:
        self.paths = paths
        self.mentions_property_class = any("propertyClass" in p for p in paths)
        self.ancestor = [p for p in paths if p.startswith("ancestor::")]
        self.by_terminal: dict[str, list[str]] = {}
        for p in paths:
            if p.startswith("ancestor::"):
                continue
            shown = _strip_predicates(p) if strip else p
            steps = [step for step in shown.lstrip("/").split("/") if step]
            if steps:
                local = _parse_xpath_segment(steps[-1])[1]
                self.by_terminal.setdefault(local, []).append(p)

    def candidates(self, local: str) -> list[str]:
        """Paths that can match an element named ``local`` (absolute evaluation)."""
        return [*self.ancestor, *self.by_terminal.get(local, [])]

    def relative_candidates(self, local: str) -> list[str]:
        """The same for relative evaluation, where a plain path means ``ancestor::``."""
        return [p for p in self.paths if not p.startswith("//")] + [
            p for p in self.by_terminal.get(local, []) if p.startswith("//")
        ]


class _DefinitionPaths:
    """What the checks read from one Definition, read once per instance."""

    def __init__(
        self,
        sources: _PathGroup,
        conditionals: _PathGroup,
        names: set[str],
        definition: etree._Element | None,
    ) -> None:
        self.sources = sources
        self.conditionals = conditionals
        self.names = names
        self.too_large = definition is None
        first: dict[str, etree._Element] = {}
        first_name: str | None = None
        if definition is not None:
            for el in definition.iter(etree.Element):
                local = _local(el)
                if local in _DEFINITION_FIELDS and local not in first and el is not definition:
                    first[local] = el
                if local == "name" and first_name is None and el is not definition and _text(el):
                    first_name = _text(el).lower()
        self.first_name = first_name
        self.data_type = first.get("dataType")
        self.quantity_class = first.get("quantityClass")
        self.value_code_space = first.get("valueCodeSpace")
        self.name_child = (
            next((c for c in definition if isinstance(c.tag, str) and _local(c) == "name"), None)
            if definition is not None
            else None
        )

    @classmethod
    def too_big(cls) -> _DefinitionPaths:
        return cls(_PathGroup([]), _PathGroup([]), set(), None)

    def over_limits(self) -> str | None:
        """Why validate() must not check against this Definition, or None."""
        if self.too_large:
            return f"a dictionary definition has more than {MAX_DEFINITION_NODES:,} nodes"
        if max(len(self.sources.paths), len(self.conditionals.paths), len(self.names)) > (
            MAX_DEFINITION_PATHS
        ):
            return (
                f"a dictionary definition lists more than {MAX_DEFINITION_PATHS:,} paths or names"
            )
        return None


_DEFINITION_FIELDS: Final = frozenset({"dataType", "quantityClass", "valueCodeSpace"})


def _is_parser_limit(entry: etree._LogEntry) -> bool:
    """A libxml2 safety limit (amplification, depth, text, name or node size).

    libxml2 builds differ in the error code they use for the same limit (the Windows
    lxml wheel's depth limit is one example), so libxml2's own limit phrases are also
    matched. Codes for ordinary structural errors are never read by their wording:
    they quote names from the file ("maximumDepth", an entity called "depth"), which
    must not turn a malformed dictionary into a refused one.
    """
    if entry.type_name in _LIMIT_ERRORS:
        return True
    if entry.type_name in _STRUCTURAL_ERRORS or entry.type_name.startswith("NS_ERR"):
        return False
    message = (entry.message or "").lower()
    return any(phrase in message for phrase in _LIMIT_PHRASES)


_LIMIT_ERRORS: Final = frozenset(
    {"ERR_RESOURCE_LIMIT", "ERR_NAME_TOO_LONG", "ERR_ENTITY_LOOP", "ERR_ENTITY_AMPLIFICATION"}
)
_STRUCTURAL_ERRORS: Final = frozenset(
    {
        "ERR_TAG_NAME_MISMATCH",
        "ERR_UNDECLARED_ENTITY",
        "WAR_UNDECLARED_ENTITY",
        "ERR_ATTRIBUTE_REDEFINED",
        "ERR_TAG_NOT_FINISHED",
        "ERR_NAME_REQUIRED",
        "ERR_GT_REQUIRED",
        "ERR_LT_IN_ATTRIBUTE",
        "ERR_ATTRIBUTE_WITHOUT_VALUE",
        "ERR_DOCUMENT_END",
        "ERR_UNKNOWN_ENCODING",
        "ERR_UNSUPPORTED_ENCODING",
    }
)
#: libxml2's own wording for its limits (lower case). Multi-word phrases only, so a
#: name quoted in an ordinary message cannot match.
_LIMIT_PHRASES: Final = (
    "excessive depth in document",
    "xml_parse_huge",
    "huge text node",
    "text node too long",
    "name too long",
    "entity amplification",
    "entity reference loop",
    "too big found",  # comment, PI and CDATA size limits (the *_NOT_FINISHED codes)
    "resource limit exceeded",
    "buffer size limit exceeded",
    "huge input lookup",
)


def _strip_predicates(path: str) -> str:
    """``path`` without its ``[...]`` predicates (quote-aware), which matching ignores."""
    out: list[str] = []
    depth, quote = 0, ""
    for ch in path:
        if quote:
            if ch == quote:
                quote = ""
            continue
        if depth and ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        elif not depth:
            out.append(ch)
    return "".join(out)


_XPATH_WS_RUN = re.compile(r"[ \t\n\r]+")
_CHECK13_SHOWN = 5  # the official message lists the first five invalid values
_CHECK13_TERMS = 20  # pydiggs lists at most 20 valid terms (the official module lists all)


def _normalize_space(text: str | None) -> str:
    """XPath normalize-space(): only space, tab, CR and LF count as whitespace."""
    return _XPATH_WS_RUN.sub(" ", text or "").strip(" \t\n\r")
