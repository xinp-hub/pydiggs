"""Locate findings in an lxml tree: line, official xpath, gml:id and source snippet.

The xpath is the official ``diggs:get-path`` form (DIGGSml/validation
``modules/diggs-functions.xsl``): every element on the ancestor-or-self axis as
``name()[n]``, where ``n`` counts preceding siblings with the same ``name()``. An
attribute adds ``/@local-name``, as the official deprecation module does.
"""

from __future__ import annotations

import re
from typing import Final

from lxml import etree

__all__ = ["SNIPPET_LIMIT", "Locator"]

#: Largest source snippet, in UTF-8 bytes.
SNIPPET_LIMIT: Final = 1024
_GML_ID: Final = "{http://www.opengis.net/gml/3.2}id"
_POSITION: Final = re.compile(r"\[\d+\]")


def _qname(el: etree._Element) -> str:
    local = etree.QName(el).localname
    return f"{el.prefix}:{local}" if el.prefix else str(local)


class Locator:
    """Turns elements of one parsed document into finding locations.

    Sibling positions are computed once per parent, so locating many findings in a
    large document stays linear.
    """

    def __init__(self, doc: etree._ElementTree) -> None:
        """Bind to ``doc``."""
        self._doc = doc
        self._positions: dict[etree._Element, int] = {}

    def _position(self, el: etree._Element) -> int:
        if el not in self._positions:
            parent = el.getparent()
            siblings = [el] if parent is None else [c for c in parent if isinstance(c.tag, str)]
            counts: dict[str, int] = {}
            for sibling in siblings:
                name = _qname(sibling)
                counts[name] = counts.get(name, 0) + 1
                self._positions[sibling] = counts[name]
        return self._positions[el]

    def xpath(self, el: etree._Element) -> str:
        """The official get-path of an element.

        Example:
            >>> doc = etree.ElementTree(
            ...     etree.fromstring(b'<d:Diggs xmlns:d="urn:d"><d:a/><d:b/><d:a/></d:Diggs>')
            ... )
            >>> Locator(doc).xpath(doc.getroot()[2])
            '/d:Diggs[1]/d:a[2]'
        """
        steps: list[str] = []
        node: etree._Element | None = el
        while node is not None:
            if isinstance(node.tag, str):
                steps.append(f"{_qname(node)}[{self._position(node)}]")
            node = node.getparent()
        return "/" + "/".join(reversed(steps))

    @staticmethod
    def line(el: etree._Element) -> int | None:
        """1-based source line, or None when libxml2 does not know it."""
        line = el.sourceline
        return line if isinstance(line, int) and line >= 1 else None

    @staticmethod
    def gml_id(el: etree._Element) -> str | None:
        """``gml:id`` of the nearest element at or above ``el``."""
        node: etree._Element | None = el
        while node is not None:
            value = node.get(_GML_ID) if isinstance(node.tag, str) else None
            if value:
                return str(value)
            node = node.getparent()
        return None

    @staticmethod
    def snippet(el: etree._Element) -> str:
        """The element's XML without its tail, cut to :data:`SNIPPET_LIMIT` bytes."""
        data: bytes = etree.tostring(el, encoding="utf-8", with_tail=False)
        if len(data) > SNIPPET_LIMIT:
            data = data[: SNIPPET_LIMIT - 3] + b"..."
        return data.decode("utf-8", errors="ignore")

    def element_at(self, path: str | None) -> etree._Element | None:
        """The single element an absolute XPath (libxml2's ``_LogEntry.path``) selects."""
        if not path:
            return None
        nodes = self._select(path)
        return nodes[0] if len(nodes) == 1 else None

    def svrl_element(self, location: str | None, occurrence: int) -> etree._Element | None:
        """The element an SVRL ``@location`` names.

        lxml's ISO Schematron skeleton adds a position only where same-named siblings
        exist, and counts them by local name alone, so a location can select nothing
        (or, in principle, several elements). Then the positions are dropped and the
        ``occurrence``-th (0-based) match in document order is used: the n-th failed
        assert with this location is taken to be about the n-th such element.
        """
        if not location:
            return None
        nodes = self._select(location)
        if len(nodes) == 1:
            return nodes[0]
        relaxed = self._select(_POSITION.sub("", location))
        return relaxed[occurrence] if 0 <= occurrence < len(relaxed) else None

    def _select(self, path: str) -> list[etree._Element]:
        try:
            result = self._doc.xpath(path)
        except (etree.XPathError, ValueError):
            return []
        if not isinstance(result, list):
            return []
        return [n for n in result if isinstance(n, etree._Element) and isinstance(n.tag, str)]
