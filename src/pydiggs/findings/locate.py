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
#: Longest location path considered; real ones are far shorter.
_MAX_PATH: Final = 64 * 1024
_NAME: Final = r"[A-Za-z_][A-Za-z0-9_.\-]*"
_LOG_PATH: Final = re.compile(rf"(?:/(?:\*|{_NAME}(?::{_NAME})?)(?:\[[1-9][0-9]{{0,8}}\])?)+")
_ATTRIBUTE_STEP: Final = re.compile(
    rf"(?:{_NAME}|\*\[local-name\(\)='{_NAME}' and namespace-uri\(\)='[^']*'\])"
)


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
        self._svrl_steps: dict[etree._Element, str] = {}

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
        """The element libxml2's ``_LogEntry.path`` names.

        libxml2 writes these as ``/name[n]``, ``/prefix:name[n]`` or ``/*[n]`` steps. The
        path is checked against that grammar before it is evaluated, so it can contain
        no function call or predicate other than a position.
        """
        if not path or len(path) > _MAX_PATH or not _LOG_PATH.fullmatch(path):
            return None
        try:
            result = self._doc.xpath(path)
        except (etree.XPathError, ValueError):
            return None
        if not isinstance(result, list) or len(result) != 1:
            return None
        node = result[0]
        return node if isinstance(node, etree._Element) and isinstance(node.tag, str) else None

    def svrl_element(self, location: str | None) -> etree._Element | None:
        """The element an SVRL ``@location`` from lxml's ISO Schematron skeleton names.

        The location is never evaluated: it is built from the instance's own namespace
        URIs, which may contain quotes and XPath. Instead the tree is walked from the
        root, regenerating for each child the step the skeleton would write
        (``*[local-name()='X' and namespace-uri()='U']`` or ``name``, plus ``[n]`` when a
        sibling shares the local name) and matching it literally. A trailing attribute
        step names its element. Anything else gives None.
        """
        if not location or len(location) > _MAX_PATH:
            return None
        node: etree._Element | None = None
        candidates: list[etree._Element] = [self._doc.getroot()]
        pos = 0
        while pos < len(location):
            if location.startswith("/@", pos) and node is not None:
                rest = location[pos + 2 :]
                return node if _ATTRIBUTE_STEP.fullmatch(rest) else None
            matched = [
                (len(text), c)
                for c in candidates
                if location.startswith(text := "/" + self._svrl_step(c), pos)
                and location[pos + len(text) : pos + len(text) + 1] in ("", "/")
            ]
            if not matched:
                return None
            length, node = max(matched, key=lambda m: m[0])
            pos += length
            candidates = [c for c in node if isinstance(c.tag, str)]
        return node

    def _svrl_step(self, el: etree._Element) -> str:
        if el not in self._svrl_steps:
            parent = el.getparent()
            siblings = [el] if parent is None else [c for c in parent if isinstance(c.tag, str)]
            self._svrl_steps.update(_skeleton_steps(siblings))
        return self._svrl_steps[el]


def _skeleton_steps(siblings: list[etree._Element]) -> dict[etree._Element, str]:
    """The step lxml's ``schematron-get-full-path`` template writes for each sibling.

    Elements in no namespace are written ``name()``, counted by ``name()``; the others
    as a local-name/namespace test, counted by local name alone (as the skeleton does).
    A position is added when more than one sibling shares the counted name.
    """
    keys = []
    for el in siblings:
        qname = etree.QName(el)
        keys.append(("n", _qname(el)) if not qname.namespace else ("l", str(qname.localname)))
    totals: dict[tuple[str, str], int] = {}
    for key in keys:
        totals[key] = totals.get(key, 0) + 1
    seen: dict[tuple[str, str], int] = {}
    steps: dict[etree._Element, str] = {}
    for el, key in zip(siblings, keys, strict=True):
        seen[key] = seen.get(key, 0) + 1
        qname = etree.QName(el)
        if key[0] == "n":
            step = key[1]
        else:
            step = f"*[local-name()='{qname.localname}' and namespace-uri()='{qname.namespace}']"
        if totals[key] > 1:
            step += f"[{seen[key]}]"
        steps[el] = step
    return steps
