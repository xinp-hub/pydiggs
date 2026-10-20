"""Locate Schematron failures without lxml's SVRL location strings.

validate() compiles Schematron with ``generate-paths=false``: lxml's ISO skeleton then
stops building, for every failed assert, a location string with one step per ancestor
(which cost memory per failure and time per sibling). The failures are located here
instead.

The skeleton runs each active pattern (in schema order, filtered by the phase) as one
pass over the instance's elements in document order, and every element matched by one
of the pattern's rules (the first matching rule wins) yields one ``svrl:fired-rule``,
followed by that element's failed asserts and successful reports. So the n-th
``fired-rule`` of a pattern belongs to the n-th element, in document order, matched by
any rule context of that pattern. The patterns are read from the same included and
expanded schema lxml compiles. The Schematron file is trusted input, so its XPaths may
be evaluated; the instance's data never are.

A pattern's failures are left without a location rather than located wrongly when the
pairing cannot be verified: the ``active-pattern`` ids differ, the number of matched
elements differs from the number of ``fired-rule`` elements, or the n-th element's first
matching rule is not the n-th ``fired-rule``'s context (contexts that select attributes,
text or the root, or that lxml evaluates differently).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from lxml import etree

__all__ = ["SVRL", "FiredPattern", "RuleContexts", "fired_patterns", "svrl_items"]

SVRL: Final = "{http://purl.oclc.org/dsdl/svrl}"
_SCHEMATRON_NAMESPACES: Final = (
    "http://purl.oclc.org/dsdl/schematron",
    "http://www.ascc.net/xml/schematron",
)


_NAMED: Final = re.compile(r"^\*\[local-name\(\)\s*=\s*'([\w.\-]+)'\](.*)$", re.S)
_PARENT: Final = re.compile(r"\[parent::\*\[local-name\(\)\s*=\s*'([\w.\-]+)'\]\]")


def _only_predicates(text: str) -> bool:
    """True if ``text`` is nothing but ``[...]`` predicates (so it narrows, never moves)."""
    depth, quote = 0, ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and not ch.isspace():
            return False
    return depth == 0 and not quote


def _tags(local: str) -> tuple[str, ...]:
    return tuple(f"{{{ns}}}{local}" for ns in _SCHEMATRON_NAMESPACES)


def _branches(context: str) -> list[str]:
    """Split a match pattern at its top-level ``|``."""
    out, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(context):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == "|" and depth == 0:
            out.append(context[start:i].strip())
            start = i + 1
    out.append(context[start:].strip())
    return [b for b in out if b]


class RuleContexts:
    """The rule contexts of one Schematron schema, grouped by active pattern."""

    def __init__(self, schema: etree._ElementTree, phase: str | None = None) -> None:
        """Read the patterns, rules and namespace bindings of ``schema``.

        ``schema`` must be included and expanded already (lxml's
        ``store_schematron=True`` tree); ``phase`` is the compile-time phase (None:
        the schema's ``defaultPhase``, else ``#ALL``), as in lxml's skeleton.
        """
        root = schema.getroot()
        self.namespaces = {
            str(el.get("prefix")): str(el.get("uri"))
            for el in root.iter(*_tags("ns"))
            if el.get("prefix") and el.get("uri")
        }
        phase = phase if phase is not None else root.get("defaultPhase") or "#ALL"
        active: set[str] | None = None
        if phase != "#ALL":
            active = {
                str(a.get("pattern"))
                for ph in root.iterchildren(*_tags("phase"))
                if ph.get("id") == phase
                for a in ph.iterchildren(*_tags("active"))
            }
        self.ids: list[str | None] = []
        self.patterns: list[list[str]] = []
        for pattern in root.iterchildren(*_tags("pattern")):
            if active is not None and pattern.get("id") not in active:
                continue
            self.ids.append(pattern.get("id"))
            if pattern.get("abstract") == "true":
                # Defence in depth: lxml's stored schema has already expanded these away.
                self.patterns.append([])
                continue
            self.patterns.append(
                [
                    str(rule.get("context"))
                    for rule in pattern.iterchildren(*_tags("rule"))
                    if rule.get("abstract") != "true" and rule.get("context")
                ]
            )

    def matched(
        self, doc: etree._ElementTree, pattern: int, fired: FiredPattern
    ) -> list[etree._Element] | None:
        """Elements of pattern number ``pattern`` that fired a rule, in document order.

        None when they cannot be paired reliably with the report's ``fired`` rules.
        Each context is evaluated on its own; one pass in document order then picks
        each element's first matching rule (a union XPath over many contexts is
        quadratic in libxml2). Callers cache the answer per document (this object is
        shared between documents).
        """
        if pattern >= len(self.patterns) or not self.patterns[pattern]:
            return None
        expected = self.ids[pattern]
        if fired.id is not None and expected is not None and fired.id != expected:
            return None
        contexts = self.patterns[pattern]
        first: dict[etree._Element, int] = {}
        for index in range(len(contexts) - 1, -1, -1):  # earlier rules overwrite later
            found = self._select(doc, contexts[index])
            if found is None:
                return None
            first.update(dict.fromkeys(found, index))
        if len(first) != len(fired.contexts):
            return None
        nodes: list[etree._Element] = []
        if first:
            for el in doc.getroot().iter():
                rule = first.get(el)
                if rule is None:
                    continue
                if contexts[rule] != fired.contexts[len(nodes)]:
                    return None
                nodes.append(el)
                if len(nodes) == len(first):
                    break
        return nodes

    def fired_count(self, doc: etree._ElementTree, limit: int) -> int:
        """How many elements the active patterns' rules will fire on, or more.

        Each pattern counts the elements its rule contexts match (an element matched
        by two rules of one pattern counts twice, so this is an upper bound). A context
        named by a local name, ``*[local-name()='X']...``, is counted with lxml's
        C-level tag filter (an exact parent test, or every ``X``); any other is counted
        with XPath ``count()``. A context XPath cannot evaluate (a ``$variable``, an
        XSLT function) is not counted. Counting stops once it passes ``limit``.
        """
        total = 0
        for contexts in self.patterns:
            for context in contexts:
                for branch in _branches(context):
                    total += self._count(doc, branch, limit - total + 1)
                    if total > limit:
                        return total
        return total

    def _count(self, doc: etree._ElementTree, branch: str, stop: int) -> int:
        named = _NAMED.match(branch)
        if named is not None and _only_predicates(named[2]):
            local, rest = named[1], named[2]
            parent = _PARENT.fullmatch(rest)
            count = 0
            for el in doc.getroot().iter("{*}" + local):
                if parent is not None:
                    up = el.getparent()
                    if up is None or etree.QName(up).localname != parent[1]:
                        continue
                count += 1
                if count >= stop:
                    break
            return count
        xpath = branch if branch.startswith("/") else f"//{branch}"
        try:
            found = doc.xpath(f"count({xpath})", namespaces=self.namespaces)
        except (etree.XPathError, ValueError):
            return 0
        return int(found) if isinstance(found, float) else 0

    def _select(self, doc: etree._ElementTree, context: str) -> list[etree._Element] | None:
        """The elements ``context`` matches, or None if it may match anything else."""
        found: list[etree._Element] = []
        for branch in _branches(context):
            xpath = branch if branch.startswith("/") else f"//{branch}"
            try:
                result = doc.xpath(xpath, namespaces=self.namespaces)
            except (etree.XPathError, ValueError):
                return None
            if not isinstance(result, list):
                return None
            for node in result:
                if not (isinstance(node, etree._Element) and isinstance(node.tag, str)):
                    return None
                found.append(node)
        return found


@dataclass(frozen=True)
class FiredPattern:
    """One ``svrl:active-pattern``: its id and the context of each ``fired-rule``."""

    id: str | None
    contexts: list[str]


def svrl_items(
    report: etree._ElementTree,
) -> Iterator[tuple[str, etree._Element, int, int]]:
    """Each failed assert or successful report, with its context and position.

    Yields (rule context, item, pattern number, 0-based index of its ``fired-rule``
    within that pattern).
    """
    pattern, fired, context = -1, -1, ""
    for el in report.getroot():
        if el.tag == f"{SVRL}active-pattern":
            pattern, fired = pattern + 1, -1
        elif el.tag == f"{SVRL}fired-rule":
            fired += 1
            context = el.get("context") or ""
        elif el.tag in (f"{SVRL}failed-assert", f"{SVRL}successful-report"):
            yield context, el, max(pattern, 0), fired


def fired_patterns(report: etree._ElementTree) -> dict[int, FiredPattern]:
    """Each pattern number's ``active-pattern`` id and ``fired-rule`` contexts."""
    patterns: dict[int, FiredPattern] = {}
    pattern = -1
    for el in report.getroot():
        if el.tag == f"{SVRL}active-pattern":
            pattern += 1
            patterns[pattern] = FiredPattern(el.get("id"), [])
        elif el.tag == f"{SVRL}fired-rule":
            current = patterns.setdefault(max(pattern, 0), FiredPattern(None, []))
            current.contexts.append(el.get("context") or "")
    return patterns
