"""Hostile-scale inputs: locating findings stays linear and memory-bounded (round-2 audit)."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from lxml import etree

from pydiggs.findings.locate import Locator

NS = "http://diggsml.org/schemas/2.6"

# Prepended to every measuring child process. Linux keeps ru_maxrss across exec (the
# child would report pytest's own peak), so read VmHWM, which exec resets.
_PEAK = """
import resource, sys
def peak():
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 1024
"""


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
    return peak() * 2**20
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
        [sys.executable, "-c", _PEAK + _BIG_NS], capture_output=True, text=True, check=True
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


def _deep_chain(tmp_path, findings: int, depth: int = 50, name_len: int = 1000):
    name = "n" * name_len
    chain_open = "".join(f"<p{i}{name}>" for i in range(depth - 1))
    chain_close = "".join(f"</p{i}{name}>" for i in reversed(range(depth - 1)))
    leaves = "<Borehole><totalMeasuredDepth>-1</totalMeasuredDepth></Borehole>" * findings
    path = tmp_path / "deep.xml"
    path.write_text(f'<Diggs xmlns="{NS}">{chain_open}{leaves}{chain_close}</Diggs>')
    return path


def test_long_xpaths_are_dropped_but_lines_kept(tmp_path):
    from pydiggs import validate
    from pydiggs.findings.run import MAX_XPATH_CHARS

    name = "n" * 1000
    path = tmp_path / "deep.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}">'
        + "".join(f"<p{i}{name}>" for i in range(49))
        + '<q codeSpace="urn:x">v</q>\n' * 300
        + "".join(f"</p{i}{name}>" for i in reversed(range(49)))
        + "</Diggs>"
    )
    report = validate(path, checks=["dictionary"])
    assert len(report.findings) == 300
    assert all(f.xpath is None and f.line for f in report.findings)
    assert len(report.to_json()) < 300 * 1000  # no 50 KB paths in the output
    short = validate(_deep_chain(tmp_path, 1, depth=3, name_len=10), checks=["schematron"])
    assert short.findings[0].xpath is not None
    assert len(short.findings[0].xpath) <= MAX_XPATH_CHARS


def test_message_cap_and_report_size_budget(monkeypatch):
    from pydiggs.findings import run
    from pydiggs.findings.model import Severity, Step

    builder = run._Builder(None, snippets=False, scrub=("", ""), max_findings=None)
    builder.add(Step.SCHEMA, "schema.x", Severity.ERROR, "x" * 10_000)
    assert len(builder.findings[0].message) == run.MAX_MESSAGE_CHARS
    assert builder.findings[0].message.endswith("…")
    monkeypatch.setattr(run, "MAX_REPORT_BYTES", 3 * (run._FINDING_OVERHEAD + 20))
    for _ in range(10):
        builder.add(Step.SCHEMATRON, "schematron.x", Severity.WARNING, "short")
    assert builder.full
    assert len(builder.findings) == 1  # the 4 KiB message used the budget at once
    marker = builder.truncation()
    assert marker is not None
    assert marker.severity is Severity.WARNING
    assert marker.message == (
        "Stopped after 1 finding; 10 more were not reported (steps: schematron). "
        "The report reached its 0 MiB size budget."
    )


_MEMORY = """
import resource, sys, tempfile
from pathlib import Path
from pydiggs import validate
NS = "http://diggsml.org/schemas/2.6"
path = Path(tempfile.mkdtemp()) / "mem.xml"
path.write_text(f'<Diggs xmlns="{NS}">' + "<a>" * 200 + '<q codeSpace="urn:x">v</q>' * 300_000
                + "</a>" * 200 + "</Diggs>")
report = validate(path, checks=["dictionary"])
print(len(report.findings), peak(), report.steps["dictionary"])
"""


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
def test_eight_megabytes_of_findings_stay_bounded():
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _PEAK + _MEMORY], capture_output=True, text=True, check=True
    ).stdout.split()
    assert out[0] == "10001"  # 10,000 kept plus the truncation marker
    assert float(out[1]) < 700, out[1]  # peak RSS in MB, 7.8 MB input
    assert out[2] == "incomplete"


_RUN = """
import resource, sys, tempfile, time
from pathlib import Path
from pydiggs import validate
NS = "http://diggsml.org/schemas/2.6"
case = sys.argv[1]
if case == "deep":
    nm = "a" * 2500
    xml = (f'<Diggs xmlns="{NS}">' + f"<{nm}>" * 200 + '<q codeSpace="urn:x">v</q>' * 8000
           + f"</{nm}>" * 200 + "</Diggs>")
    checks = ["dictionary"]
else:
    xml = (f'<Diggs xmlns="{NS}" xmlns:xlink="http://www.w3.org/1999/xlink">'
           + '<r xlink:href="#no"/>' * 40000 + "</Diggs>")
    checks = ["context"]
path = Path(tempfile.mkdtemp()) / "case.xml"
path.write_text(xml)
start = time.perf_counter()
report = validate(path, checks=checks)
elapsed = time.perf_counter() - start
print(elapsed, peak(), len(report.findings))
"""


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
@pytest.mark.parametrize(
    ("case", "seconds", "megabytes", "findings"),
    [("deep", 20, 300, 8000), ("href", 10, 300, 10_001)],
)
def test_legacy_path_builders_do_not_run_in_validate(case, seconds, megabytes, findings):
    # deep: 8,000 findings under 200 elements with 2,500-character names (was 4 GB);
    # href: 820 KiB of unresolved xlink:href siblings (was quadratic, over 400 s).
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _PEAK + _RUN, case], capture_output=True, text=True, check=True
    ).stdout.split()
    assert float(out[0]) < seconds, out
    assert float(out[1]) < megabytes, out
    assert int(out[2]) == findings


def test_xpath_walk_stops_at_the_cap():
    root = etree.fromstring(("<a>" * 200 + "<b/>" + "</a>" * 200).encode())
    leaf = next(root.iter("b"))
    loc = Locator(etree.ElementTree(root))
    assert loc.xpath(leaf, 100) is None
    assert len(loc._steps) < 30  # the capped walk stopped early
    assert loc.xpath(leaf).count("/") == 201


_BH = (
    '<samplingFeature><Borehole gml:id="BH-{i}"><totalMeasuredDepth uom="ft">-1'
    "</totalMeasuredDepth></Borehole></samplingFeature>"
)


def _schematron_case(tmp_path, *, depth: int, uri: int = 0, name: int = 0, failing: int = 20):
    ns = f' xmlns:x="urn:{"a" * uri}"' if uri else ""
    tag = "x:j" if uri else "n" * max(name, 1)
    path = tmp_path / "s.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}" xmlns:gml="http://www.opengis.net/gml/3.2"{ns}>'
        + f"<{tag}>" * depth
        + "".join(_BH.format(i=i) for i in range(failing))
        + f"</{tag}>" * depth
        + "</Diggs>"
    )
    return path


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"depth": 100, "uri": 1000}, "location would exceed 16 KiB"),
        ({"depth": 100, "name": 1000}, "location would exceed 16 KiB"),
        ({"depth": 3, "uri": 2000}, "namespace URI is longer than 1024"),
    ],
    ids=["deep-namespace", "deep-names", "long-namespace"],
)
def test_schematron_is_skipped_when_locations_would_blow_up(tmp_path, kwargs, why):
    from pydiggs import Severity, Step, validate

    report = validate(_schematron_case(tmp_path, **kwargs), checks=["schematron"])
    (finding,) = report.findings
    assert finding.rule_id == "schematron.skipped-location-budget"
    assert finding.severity is Severity.WARNING
    assert why in finding.message
    assert report.steps[Step.SCHEMATRON] == "incomplete"


def test_schematron_runs_within_the_location_budget(tmp_path):
    from pydiggs import validate

    report = validate(_schematron_case(tmp_path, depth=10, uri=1000), checks=["schematron"])
    assert [f.rule_id for f in report.findings] == ["schematron.Borehole.totalMeasuredDepth"] * 20


_SKIP_RSS = """
import resource, sys, tempfile
from pathlib import Path
from pydiggs import validate
bh = ('<samplingFeature><Borehole gml:id="BH-{i}"><totalMeasuredDepth uom="ft">-1'
      '</totalMeasuredDepth></Borehole></samplingFeature>')
path = Path(tempfile.mkdtemp()) / "s.xml"
path.write_text('<Diggs xmlns="http://diggsml.org/schemas/2.6" '
                'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:x="urn:' + "a" * 200000 + '">'
                + "<x:j>" * 3 + "".join(bh.format(i=i) for i in range(2000)) + "</x:j>" * 3
                + "</Diggs>")
report = validate(path, checks=["schematron"])
print(peak(), report.findings[0].rule_id)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
def test_skipping_schematron_keeps_memory_bounded():
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _PEAK + _SKIP_RSS], capture_output=True, text=True, check=True
    ).stdout.split()
    assert float(out[0]) < 200, out  # was 1,252 MB with the skeleton run
    assert out[1] == "schematron.skipped-location-budget"


@pytest.mark.slow
def test_a_huge_gml_id_on_an_ancestor_is_read_once(tmp_path):
    from pydiggs import validate

    path = tmp_path / "id.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}" xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="{"é" * 4_000_000}">'
        + '<q codeSpace="urn:x">v</q>' * 10_000
        + "</Diggs>",
        encoding="utf-8",
    )
    start = time.perf_counter()
    report = validate(path, checks=["dictionary"])
    assert time.perf_counter() - start < 20  # was 95 s
    assert len(report.findings) == 10_000
    assert all(f.gml_id is None for f in report.findings)  # over the 4,096-character cap


def test_gml_id_lookup_is_memoised():
    root = etree.fromstring(
        b'<a xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="top"><b><c/><c/></b></a>'
    )
    loc = Locator(etree.ElementTree(root))
    leaves = list(root.iter("c"))
    assert [loc.gml_id(c) for c in leaves] == ["top", "top"]
    assert root in loc._gml_ids


_UNBOUNDED = """
import resource, sys, tempfile
from pathlib import Path
from pydiggs import validate
path = Path(tempfile.mkdtemp()) / "u.xml"
path.write_text('<Diggs xmlns="http://diggsml.org/schemas/2.6">' + "<a>" * 200
                + '<q codeSpace="urn:x">v</q>' * 600_000 + "</a>" * 200 + "</Diggs>")
report = validate(path, checks=["dictionary"], max_findings=None)
print(peak(), len(report.findings),
      report.steps["dictionary"])
"""


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
def test_no_findings_cap_still_bounds_the_legacy_layers():
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _PEAK + _UNBOUNDED], capture_output=True, text=True, check=True
    ).stdout.split()
    # 15 MB input with 600,000 findings; the lxml tree alone is about 385 MB here.
    assert float(out[0]) < 800, out  # was 859 MB and grew with the input
    assert 10_000 < int(out[1]) < 20_000  # the 16 MiB budget, plus the marker
    assert out[2] == "incomplete"
