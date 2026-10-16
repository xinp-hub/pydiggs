"""Hostile-scale inputs: locating findings stays linear and memory-bounded (round-2 audit)."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from lxml import etree

from pydiggs.findings.locate import Locator

NS = "http://diggsml.org/schemas/2.6"


def _ns_step(local: str, ns: str, index: int | None) -> str:
    step = f"/*[local-name()='{local}' and namespace-uri()='{ns}']"
    return step + (f"[{index}]" if index is not None else "")


def _resolve_all(n: int) -> float:
    xml = f'<Diggs xmlns="{NS}"><s>' + "<Borehole><d>-1</d></Borehole>" * n + "</s></Diggs>"
    doc = etree.ElementTree(etree.fromstring(xml.encode()))
    loc = Locator(doc)
    prefix = _ns_step("Diggs", NS, None) + _ns_step("s", NS, None)
    boreholes = list(doc.getroot()[0])  # lxml's parent[i] is O(i): index once
    start = time.perf_counter()
    for i in range(1, n + 1):
        found = loc.svrl_element(prefix + _ns_step("Borehole", NS, i) + _ns_step("d", NS, None))
        assert found is not None
        assert found.getparent() is boreholes[i - 1]
        assert loc.xpath(found) == f"/Diggs[1]/s[1]/Borehole[{i}]/d[1]"
    return time.perf_counter() - start


def test_svrl_locations_resolve_in_linear_time():
    small, large = _resolve_all(2_000), _resolve_all(8_000)
    assert large < 3.0, large
    assert large < 8 * max(small, 0.05), (small, large)  # 4x the input, well under 16x


_BIG_NS = """
import resource, sys, time
from lxml import etree
from pydiggs.findings.locate import Locator
ns = "urn:" + "a" * (1024 * 1024)
xml = ('<r xmlns:x="' + ns + '"><x:p>' + "<x:c/>" * 3000 + "</x:p></r>").encode()
doc = etree.ElementTree(etree.fromstring(xml))
def rss():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024
base = rss()
loc = Locator(doc)
step = lambda local, i=None: (
    f"/*[local-name()='{local}' and namespace-uri()='{ns}']" + (f"[{i}]" if i else "")
)
start = time.perf_counter()
for i in (1, 1500, 3000):
    path = "/r" + step("p") + step("c", i)
    el = loc.svrl_element(path)
    assert el is not None and el.getparent().index(el) == i - 1
    assert loc.xpath(el) == f"/r[1]/x:p[1]/x:c[{i}]"
print(time.perf_counter() - start, (rss() - base) / 2**20)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
def test_huge_namespace_uri_on_many_siblings_stays_bounded():
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _BIG_NS], capture_output=True, text=True, check=True
    ).stdout.split()
    elapsed, extra_mb = float(out[0]), float(out[1])
    assert elapsed < 5.0, elapsed
    assert extra_mb < 100, extra_mb


def test_log_entry_paths_resolve_in_linear_time():
    n = 20_000
    doc = etree.ElementTree(
        etree.fromstring(
            (f'<r xmlns:p="urn:p"><q xmlns="{NS}">' + "<a/><p:b/>" * n + "</q></r>").encode()
        )
    )
    loc = Locator(doc)
    q = doc.getroot()[0]
    children = list(q)  # lxml's parent[i] is O(i): index once
    start = time.perf_counter()
    for i in range(1, n + 1):
        assert loc.element_at(f"/r/*/*[{2 * i}]") is children[2 * i - 1]
        assert loc.element_at(f"/r/*/p:b[{i}]") is children[2 * i - 1]
    assert time.perf_counter() - start < 5.0
    assert loc.element_at("/r/*/a[1]") is None  # a is in the default namespace: only "*"
    assert loc.element_at("/r/*/p:b") is None  # not unique, so libxml2 writes [n]
