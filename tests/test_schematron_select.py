"""RuleContexts._select's tag-filter fast path must find what the XPath finds."""

from __future__ import annotations

import pytest
from lxml import etree

from pydiggs.findings import _schematron, run

_EDGE = (
    b'<X xmlns:a="urn:a" xmlns:b="urn:b"><Y><X/><a:X/></Y><a:Y><b:X/></a:Y>'
    b"<Z><X/></Z><X><X/></X><!--X--><?X?></X>"
)
_CONTEXTS = [
    "*[local-name()='X']",
    "*[local-name()='X'][parent::*[local-name()='Y']]",
    "*[local-name() = 'X'][parent::*[local-name() = 'X']]",
    "*[local-name()='X'] | *[local-name()='Y'][parent::*[local-name()='X']]",
]


@pytest.mark.parametrize("context", _CONTEXTS)
def test_fast_path_matches_xpath_on_edge_cases(context: str) -> None:
    _, contexts = run._schematron(None)
    doc = etree.ElementTree(etree.fromstring(_EDGE))
    expected = []
    for branch in _schematron._branches(context):
        expected += doc.xpath(f"//{branch}")
    assert contexts._select(doc, context) == expected


def test_fast_path_matches_xpath_for_every_bundled_context() -> None:
    _, contexts = run._schematron(None)
    doc = etree.ElementTree(etree.fromstring(_EDGE))
    branches = [b for cs in contexts.patterns for c in cs for b in _schematron._branches(c)]
    assert branches, "the bundled Schematron has rule contexts"
    for branch in branches:
        xpath = branch if branch.startswith("/") else f"//{branch}"
        assert contexts._select(doc, branch) == doc.xpath(xpath, namespaces=contexts.namespaces)
