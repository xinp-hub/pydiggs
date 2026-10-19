"""Locate findings in an lxml tree: line, official xpath, gml:id and source snippet.

The xpath is the official ``diggs:get-path`` form (DIGGSml/validation
``modules/diggs-functions.xsl``): every element on the ancestor-or-self axis as
``name()[n]``, where ``n`` counts preceding siblings with the same ``name()``. An
attribute adds ``/@local-name``, as the official deprecation module does.
"""

from __future__ import annotations

import re
from typing import Final, overload

from lxml import etree

__all__ = ["SNIPPET_CHILDREN", "SNIPPET_LIMIT", "Locator"]

#: Largest source snippet, in UTF-8 bytes.
SNIPPET_LIMIT: Final = 1024
#: Child elements kept in a snippet.
SNIPPET_CHILDREN: Final = 8
_GML_ID: Final = "{http://www.opengis.net/gml/3.2}id"
#: Longest location path considered; real ones are far shorter.
_MAX_PATH: Final = 4 * 1024 * 1024
_NAME: Final = r"[^\W\d][\w.\-]*"
_LOG_PATH: Final = re.compile(rf"(?:/(?:\*|{_NAME}(?::{_NAME})?)(?:\[[1-9][0-9]{{0,8}}\])?)+")
_LOG_STEP: Final = re.compile(rf"/(?P<name>\*|{_NAME}(?::{_NAME})?)(?:\[(?P<index>[0-9]+)\])?")


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
        self._steps: dict[etree._Element, str] = {}
        self._gml_ids: dict[etree._Element, str | None] = {}
        self._child_lists: dict[tuple[etree._Element, object], list[etree._Element]] = {}

    def _step(self, el: etree._Element) -> str:
        """``name()[n]`` for one element, cached; ``n`` counts same-``name()`` siblings.

        Siblings are found with a C-level tag filter on the local name, so no sibling's
        (possibly huge) namespace URI is turned into a Python string.
        """
        if el not in self._steps:
            name = _qname(el)
            parent = el.getparent()
            if parent is None:
                self._steps[el] = f"{name}[1]"
            else:
                local = name.split(":", 1)[-1]
                same = self._prefixed(parent, local, el.prefix)
                for i, sibling in enumerate(same, start=1):
                    self._steps[sibling] = f"{name}[{i}]"
        return self._steps[el]

    @overload
    def xpath(self, el: etree._Element) -> str: ...
    @overload
    def xpath(self, el: etree._Element, max_chars: int) -> str | None: ...

    def xpath(self, el: etree._Element, max_chars: int | None = None) -> str | None:
        """The official get-path of an element; None if longer than ``max_chars``.

        The walk stops as soon as the path would exceed ``max_chars``, so a deep tree
        with long names costs at most that much per finding.

        Example:
            >>> doc = etree.ElementTree(
            ...     etree.fromstring(b'<d:Diggs xmlns:d="urn:d"><d:a/><d:b/><d:a/></d:Diggs>')
            ... )
            >>> Locator(doc).xpath(doc.getroot()[2])
            '/d:Diggs[1]/d:a[2]'
            >>> Locator(doc).xpath(doc.getroot()[2], max_chars=10) is None
            True
        """
        steps: list[str] = []
        total = 0
        node: etree._Element | None = el
        while node is not None:
            step = self._step(node)
            total += len(step) + 1
            if max_chars is not None and total > max_chars:
                return None
            steps.append(step)
            node = node.getparent()
        return "/" + "/".join(reversed(steps))

    @staticmethod
    def line(el: etree._Element) -> int | None:
        """1-based source line, or None when libxml2 does not know it."""
        line = el.sourceline
        return line if isinstance(line, int) and line >= 1 else None

    def gml_id(self, el: etree._Element, max_chars: int | None = None) -> str | None:
        """``gml:id`` of the nearest element at or above ``el``, memoised per element.

        An id longer than ``max_chars`` counts as none (and is not kept in memory), so a
        huge id on an ancestor is read once, not once per finding.
        """
        path: list[etree._Element] = []
        node: etree._Element | None = el
        found: str | None = None
        while node is not None:
            if node in self._gml_ids:
                found = self._gml_ids[node]
                break
            path.append(node)
            value = node.get(_GML_ID) if isinstance(node.tag, str) else None
            if value:
                found = str(value)
                if max_chars is not None and len(found) > max_chars:
                    found = None
                break
            node = node.getparent()
        for visited in path:
            self._gml_ids[visited] = found
        return found

    @staticmethod
    def snippet(el: etree._Element) -> str:
        """The element's XML, cut to :data:`SNIPPET_LIMIT` UTF-8 bytes.

        A shallow copy is serialised: the element's attributes and text plus at most
        :data:`SNIPPET_CHILDREN` child elements (their attributes and text only), so the
        cost does not grow with the size of the subtree.
        """
        data: bytes = etree.tostring(_shallow(el), encoding="utf-8", with_tail=False)
        if len(data) > SNIPPET_LIMIT:
            data = data[: SNIPPET_LIMIT - 3] + b"..."
        return data.decode("utf-8", errors="ignore")

    def _children(self, parent: etree._Element, tag: object) -> list[etree._Element]:
        """Element children matching an lxml tag filter, listed once per parent.

        Filtering happens in C (``iterchildren(tag)``), so no tag or namespace string is
        built for siblings that do not match.
        """
        key = (parent, tag)
        if key not in self._child_lists:
            self._child_lists[key] = list(parent.iterchildren(tag))
        return self._child_lists[key]

    def _prefixed(
        self, parent: etree._Element, local: str, prefix: str | None
    ) -> list[etree._Element]:
        """Children with this local name and prefix (any namespace), listed once."""
        key = (parent, ("prefix", local, prefix))
        if key not in self._child_lists:
            self._child_lists[key] = [
                c for c in self._children(parent, "{*}" + local) if c.prefix == prefix
            ]
        return self._child_lists[key]

    def element_at(self, path: str | None) -> etree._Element | None:
        """The element libxml2's ``_LogEntry.path`` (``xmlGetNodePath``) names.

        Steps are ``*`` (an element in a default namespace; counted among all element
        siblings), ``name`` (no namespace) or ``prefix:name`` (counted among siblings
        with that local name and prefix), each with an optional ``[n]``. The path is
        checked against that grammar and resolved by walking the tree; it is never
        evaluated.
        """
        if not path or len(path) > _MAX_PATH or not _LOG_PATH.fullmatch(path):
            return None
        node: etree._Element | None = None
        for match in _LOG_STEP.finditer(path):
            name, index = match["name"], match["index"]
            if node is None:
                candidates = [self._doc.getroot()]
            elif name == "*":
                candidates = self._children(node, etree.Element)
            elif ":" in name:
                prefix, local = name.split(":", 1)
                candidates = self._prefixed(node, local, prefix)
            else:
                candidates = self._children(node, name)
            node = _pick(candidates, index)
            if node is None:
                return None
        return node


def _pick(group: list[etree._Element], index: str | None) -> etree._Element | None:
    """The ``[n]``-th of ``group``, or its only member when there is no ``[n]``."""
    if index is None:
        return group[0] if len(group) == 1 else None
    n = int(index)
    return group[n - 1] if 1 <= n <= len(group) and len(group) > 1 else None


def _shallow(el: etree._Element) -> etree._Element:
    """A bounded copy of ``el`` for a snippet (see :meth:`Locator.snippet`)."""
    children = [c for c in el if isinstance(c.tag, str)]
    kept = children[:SNIPPET_CHILDREN]
    used = {etree.QName(n).namespace for n in (el, *kept)}
    used |= {etree.QName(a).namespace for n in (el, *kept) for a in n.attrib}
    nsmap = {prefix: uri for prefix, uri in el.nsmap.items() if uri in used}
    copy = etree.Element(el.tag, attrib=dict(el.attrib), nsmap=nsmap)
    copy.text = (el.text or "")[:SNIPPET_LIMIT] or None
    for child in kept:
        sub = etree.SubElement(copy, child.tag, attrib=dict(child.attrib))
        sub.text = "..." if len(child) else ((child.text or "")[:SNIPPET_LIMIT] or None)
        sub.tail = (child.tail or "")[:SNIPPET_LIMIT] or None
    if len(children) > SNIPPET_CHILDREN:
        copy.append(etree.Comment(f" {len(children) - SNIPPET_CHILDREN} more "))
    return copy
