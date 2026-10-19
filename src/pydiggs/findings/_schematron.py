"""Locate Schematron failures without lxml's SVRL location strings.

validate() compiles Schematron with ``generate-paths=false``: lxml's ISO skeleton then
stops building, for every failed assert, a location string with one step per ancestor
(which cost memory per failure and time per sibling). The failures are located here
instead.

The skeleton runs each pattern as one pass over the instance's elements in document
order, and every element matched by one of the pattern's rules (the first matching
rule wins) yields one ``svrl:fired-rule``, followed by that element's failed asserts
and successful reports. So the n-th ``fired-rule`` of a pattern belongs to the n-th
element, in document order, matched by any rule context of that pattern: the union of
those contexts, evaluated once per pattern. The Schematron file is trusted input, so its
XPaths may be evaluated; the instance's data never are.

If the count of matched elements differs from the count of ``fired-rule`` elements
(abstract patterns, includes, or contexts lxml evaluates differently), that pattern's
failures are left without a location rather than located wrongly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from lxml import etree

__all__ = ["SVRL", "RuleContexts", "fired_counts", "svrl_items"]

SVRL: Final = "{http://purl.oclc.org/dsdl/svrl}"
_SCHEMATRON_NAMESPACES: Final = (
    "http://purl.oclc.org/dsdl/schematron",
    "http://www.ascc.net/xml/schematron",
)


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


def _as_xpath(context: str) -> str:
    """An XPath selecting the nodes an XSLT match pattern matches (for element patterns)."""
    return " | ".join(f"({b})" if b.startswith("/") else f"(//{b})" for b in _branches(context))


class RuleContexts:
    """The rule contexts of one Schematron schema, grouped by active pattern."""

    def __init__(self, schema: etree._ElementTree) -> None:
        """Read the patterns, rules and namespace bindings of ``schema``."""
        root = schema.getroot()
        self.namespaces = {
            str(el.get("prefix")): str(el.get("uri"))
            for el in root.iter(*_tags("ns"))
            if el.get("prefix") and el.get("uri")
        }
        self.patterns: list[list[str]] = []
        for pattern in root.iter(*_tags("pattern")):
            if pattern.get("abstract") == "true" or pattern.get("is-a"):
                self.patterns.append([])  # instantiated by lxml's expansion; not located
                continue
            self.patterns.append(
                [
                    str(rule.get("context"))
                    for rule in pattern.iterchildren(*_tags("rule"))
                    if rule.get("abstract") != "true" and rule.get("context")
                ]
            )

    def matched(
        self, doc: etree._ElementTree, pattern: int, fired: int
    ) -> list[etree._Element] | None:
        """Elements of pattern number ``pattern`` that fired a rule, in document order.

        None when they cannot be paired reliably with the report's ``fired`` count.
        Callers cache the answer per document (this object is shared between documents).
        """
        contexts = self.patterns[pattern] if pattern < len(self.patterns) else []
        if not contexts:
            return None
        try:
            found = doc.xpath(
                " | ".join(_as_xpath(c) for c in contexts), namespaces=self.namespaces
            )
        except (etree.XPathError, ValueError):
            return None
        if not isinstance(found, list):
            return None
        nodes = [n for n in found if isinstance(n, etree._Element) and isinstance(n.tag, str)]
        return nodes if len(nodes) == fired else None


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


def fired_counts(report: etree._ElementTree) -> dict[int, int]:
    """Number of ``fired-rule`` elements per pattern number."""
    counts: dict[int, int] = {}
    pattern = -1
    for el in report.getroot():
        if el.tag == f"{SVRL}active-pattern":
            pattern += 1
            counts.setdefault(pattern, 0)
        elif el.tag == f"{SVRL}fired-rule":
            counts[max(pattern, 0)] = counts.get(max(pattern, 0), 0) + 1
    return counts
