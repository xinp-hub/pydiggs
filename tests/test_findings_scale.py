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


_BIG_NS = """
import time
from lxml import etree
from pydiggs.findings.locate import Locator
ns = "urn:" + "a" * (1024 * 1024)
xml = ('<r xmlns:x="' + ns + '"><x:p>' + "<x:c/>" * 3000 + "</x:p></r>").encode()
doc = etree.ElementTree(etree.fromstring(xml))
children = list(doc.getroot()[0])
base = peak()
loc = Locator(doc)
start = time.perf_counter()
for i in (1, 1500, 3000):
    assert loc.xpath(children[i - 1]) == f"/r[1]/x:p[1]/x:c[{i}]"
print(time.perf_counter() - start, peak() - base)
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
    "kwargs",
    [{"depth": 100, "uri": 1000}, {"depth": 100, "name": 1000}, {"depth": 3, "uri": 2000}],
    ids=["deep-namespace", "deep-names", "long-namespace"],
)
def test_schematron_runs_and_locates_where_lxml_paths_would_blow_up(tmp_path, kwargs, monkeypatch):
    # With generate-paths=false lxml builds no location strings, so these run in full.
    # (A namespace URI over 1,024 characters is refused first by default; the limit
    # is lifted here to test the layer below it.)
    from pydiggs import Step, validate
    from pydiggs.findings import run

    path = _schematron_case(tmp_path, **kwargs)
    if kwargs.get("uri", 0) > run.MAX_NAMESPACE_CHARS:
        assert [f.rule_id for f in validate(path).findings] == ["structure.namespace-too-long"]
        monkeypatch.setattr(run, "MAX_NAMESPACE_CHARS", 10**9)
    report = validate(path, checks=["schematron"])
    assert [f.rule_id for f in report.findings] == ["schematron.Borehole.totalMeasuredDepth"] * 20
    assert report.steps[Step.SCHEMATRON] == "failed"
    assert {f.gml_id for f in report.findings} == {f"BH-{i}" for i in range(20)}
    assert all(f.line for f in report.findings)


def test_schematron_runs_within_the_location_budget(tmp_path):
    from pydiggs import validate

    report = validate(_schematron_case(tmp_path, depth=10, uri=1000), checks=["schematron"])
    assert [f.rule_id for f in report.findings] == ["schematron.Borehole.totalMeasuredDepth"] * 20


_SKIP_RSS = """
import resource, sys, tempfile
from pathlib import Path
from pydiggs import validate
from pydiggs.findings import run
run.MAX_NAMESPACE_CHARS = 10**9  # by default the file is refused; test the layer below
bh = ('<samplingFeature><Borehole gml:id="BH-{i}"><totalMeasuredDepth uom="ft">-1'
      '</totalMeasuredDepth></Borehole></samplingFeature>')
path = Path(tempfile.mkdtemp()) / "s.xml"
path.write_text('<Diggs xmlns="http://diggsml.org/schemas/2.6" '
                'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:x="urn:' + "a" * 200000 + '">'
                + "<x:j>" * 3 + "".join(bh.format(i=i) for i in range(2000)) + "</x:j>" * 3
                + "</Diggs>")
report = validate(path, checks=["schematron"])
print(peak(), report.findings[0].rule_id, report.ok)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
def test_long_namespace_schematron_stays_bounded():
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _PEAK + _SKIP_RSS], capture_output=True, text=True, check=True
    ).stdout.split()
    assert float(out[0]) < 300, out  # 1,252 MB with lxml's location strings
    assert out[1] == "schematron.Borehole.totalMeasuredDepth"
    assert out[2] == "False"


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


def test_dictionary_by_value_lookups_are_indexed(tmp_path):
    from pydiggs import validate

    # 10,000 legacy (no '#') codeSpaces looked up by value: each used to rescan every
    # Definition of properties.xml and its names.
    path = tmp_path / "byval.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}">'
        + '<q codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml">z</q>' * 10_000
        + "</Diggs>"
    )
    validate(path, checks=["dictionary"], max_findings=1)  # warm the validator cache
    start = time.perf_counter()
    report = validate(path, checks=["dictionary"])
    assert time.perf_counter() - start < 4  # about 0.5 s here
    assert {f.rule_id for f in report.findings} == {"dictionary.5"}


def test_measurement_ancestor_matches_are_memoised(tmp_path):
    from pydiggs import validate

    # 1,000 properties in a measurement with 100,000 other children: each property's
    # Check 8 scanned the whole measurement for a procedure.
    prop = (
        '<Property index="{i}" gml:id="P{i}"><typeData>string</typeData><propertyClass '
        'codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#loading_phase">'
        "Loading Phase</propertyClass></Property>"
    )
    path = tmp_path / "mc.xml"
    path.write_text(
        '<Diggs xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2">'
        "<measurement><Test gml:id='T'><outcome><TestResult gml:id='R'><results><ResultSet>"
        "<parameters><PropertyParameters gml:id='PP'><properties>"
        + "".join(prop.format(i=i + 1) for i in range(1000))
        + "</properties></PropertyParameters></parameters><dataValues>"
        + ",".join(["loading"] * 1000)
        + "</dataValues></ResultSet></results></TestResult></outcome></Test>"
        + "<x/>" * 100_000
        + "</measurement></Diggs>"
    )
    validate(path, checks=["dictionary"], max_findings=1)
    start = time.perf_counter()
    validate(path, checks=["dictionary"])
    assert time.perf_counter() - start < 4  # about 0.5 s here


_C13MEM = """
import tempfile
from pathlib import Path
from lxml import etree
from pydiggs import validate
k = r = 2200
prop = ('<Property index="{i}" gml:id="P{i}"><typeData>string</typeData><propertyClass '
        'codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#loading_phase">'
        'Loading Phase</propertyClass></Property>')
row = ",".join(["a"] * k)
xml = ('<Diggs xmlns="http://diggsml.org/schemas/3" xmlns:gml="http://www.opengis.net/gml/3.2" '
       'xmlns:xlink="http://www.w3.org/1999/xlink"><measurement><Test gml:id="T1"><outcome>'
       '<TestResult gml:id="TR1"><results><ResultSet><parameters><PropertyParameters gml:id="PP">'
       '<properties>' + "".join(prop.format(i=i + 1) for i in range(k))
       + '</properties></PropertyParameters></parameters><dataValues>' + row
       + '</dataValues></ResultSet>'
       + ('<ResultSet><parameters xlink:href="#PP">' + OWN + '</parameters><dataValues>' + row
          + '</dataValues></ResultSet>') * r
       + '</results></TestResult></outcome></Test></measurement></Diggs>')
path = Path(tempfile.mkdtemp()) / "c13mem.xml"
path.write_text(xml)
etree.parse(str(path))  # parsing alone sets the baseline
base = peak()
report = validate(path, checks=["dictionary"])
print(peak() - base, report.counts["error"])
"""


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX only")
@pytest.mark.parametrize("own", ["", "<PropertyParameters/>"], ids=["cite", "cite-and-own"])
def test_check13_many_result_sets_stays_small(own):
    # 2,200 properties x 2,200 ResultSets citing them (9.8 MiB): the per-ResultSet
    # column cache used to grow to gigabytes. With an (empty) PropertyParameters of
    # their own as well, every ResultSet's groups differ, yet the column sets must still
    # be shared (code review, round 12: 718 MB when they were keyed by the groups).
    out = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", _PEAK + f"OWN = {own!r}\n" + _C13MEM],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert float(out[0]) < 300, out  # MB beyond parsing
    assert int(out[1]) == 2200


def test_context_checks_do_not_build_long_namespace_strings(tmp_path):
    import tracemalloc

    from pydiggs import validate

    # 1,000 siblings in a 100 KB namespace: the context checks built each element's
    # '{namespace}name' string (r/nsbomb.py: 3,000 x 1 MB took 3 GB).
    path = tmp_path / "nsbomb.xml"
    path.write_text(
        f'<Diggs xmlns="{NS}" xmlns:x="urn:{"a" * 100_000}">' + "<x:j/>" * 1000 + "</Diggs>"
    )
    tracemalloc.start()
    try:
        validate(path, checks=["structure", "geometry", "context"])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 20 * 2**20, peak  # tags would need 100 MB


def _sc():
    from tests import test_scaling

    return test_scaling


@pytest.mark.slow
@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        (lambda: _sc()._bigcite(55_000, 30_000), "passed"),
        (lambda: _sc()._dupcite(500), "passed"),
        (lambda: _sc()._outside(20_000, 7_000_000), "passed"),
        (lambda: _sc()._propcite(50_000, 50_000), "incomplete"),
    ],
    ids=["bigcite", "dupcite", "outside", "propcite"],
)
def test_check13_ten_megabyte_shapes_stay_within_the_bar(tmp_path, shape, expected):
    """The security review's shapes at about 10 MB: linear (passed) or fail closed."""
    from pydiggs import validate
    from tests.test_scaling import _C13_DEF, _CODES, GML

    (tmp_path / "d.xml").write_text(_C13_DEF)
    (tmp_path / "c.xml").write_text(_CODES)
    instance = tmp_path / "i.xml"
    instance.write_text(f'<Diggs xmlns="{NS}" xmlns:gml="{GML}">{shape()}</Diggs>')
    size = instance.stat().st_size
    assert 7_000_000 < size <= 10_500_000, size
    start = time.process_time()
    report = validate(instance, checks=["dictionary"])
    cpu = time.process_time() - start
    print(f"RATIO 10 MB shape: {size / 1e6:.1f} MB, {cpu:.1f} s CPU, {report.steps['dictionary']}")
    assert report.steps["dictionary"] == expected
    if expected == "incomplete":
        assert "dictionary.incomplete" in {f.rule_id for f in report.findings}
        assert report.verdict == "not-valid"
    assert cpu < 30
