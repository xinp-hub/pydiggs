"""The cost of ``diff.compare`` cannot regress unnoticed.

Over 1.12 and 1.13 the reader's residue walk took comparing ODOT_Example_2 (2 MB) with
itself from 0.38 s to 0.55-0.60 s, and nothing in CI would have flagged another 2x. No
test here has a wall-clock threshold: shared runners are too noisy for one. Each bounds a
ratio measured in the same process instead:

* the lines of pydiggs code run to compare a file with itself, against the lines run to
  read it twice (a deterministic count: the same on every run of one Python);
* the CPU time of that comparison against reading the file twice, best of a few runs,
  which also sees the work done in C (lxml) that a line count cannot;
* the lines run to compare a file made of 1 and of 2 copies of the same records, which
  grow about 2x if the work is linear in the file and 4x if it is quadratic.

The bounds were set on 2026-11-18 from the values each test prints (``COST ...``), with a
margin that still catches the comparison doing 2x its work. Raise one only for a change
that is meant to cost more, and say so in its pull request. Run in CI's Linux job
(``-m scaling``): line events and CPU time are not worth comparing across platforms.
"""

from __future__ import annotations

import copy
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any

import pytest
from lxml import etree

import pydiggs
from pydiggs import _xml, diff

from ._helpers import ANNOTATED, OFFICIAL

pytestmark = pytest.mark.scaling

PACKAGE = str(Path(pydiggs.__file__).resolve().parent)
ODOT_EXAMPLE_2 = OFFICIAL / "2.6" / "ODOT_Example_2_20180702-from25.xml"
PIEZOMETER = OFFICIAL / "3.0" / "Piezometer_Example.xml"
GML_ID = "{http://www.opengis.net/gml/3.2}id"

#: Lines run by ``compare(f, f)`` over lines run by reading ``f`` twice. Measured
#: 2026-11-18 on CPython 3.11 / 3.13 / 3.14: Piezometer_Example 1.64 / 1.70 / 1.68,
#: Borehole-CPT_Example_Annotated 2.01 / 2.03 / 2.03 (ODOT_Example_2, not run here to
#: save time, 1.88). 2.75 is 35% above the largest; a comparison doing 2x its work today
#: would be 3.3 to 4.1.
LINES_PER_READ = 2.75
#: CPU seconds of ``compare(f, f)`` over CPU seconds of reading ``f`` twice, best of
#: ``ROUNDS``. Measured 2026-11-18 for ODOT_Example_2 on macOS arm64: 1.90-2.12 (3.11,
#: seven runs), 1.76-1.85 under ``--cov`` (3.11, four runs), 1.80 (3.13, ``--cov``), 2.06
#: (3.14, ``--cov``). 3.2 is 50% above the largest; 2x the work would be about 4.
TIME_PER_READ = 3.2
ROUNDS = 3
#: Lines run for 2 copies of the records over lines run for 1 copy. Measured 2.00 on
#: 2026-11-18 (3.11, 3.13, 3.14); quadratic work would be near 4.
LINEAR = 2.5


def _lines(call: Callable[[], object]) -> int:
    """Line events of pydiggs code while ``call`` runs: a deterministic count of work."""
    count = 0

    def tracer(frame: FrameType, event: str, _arg: Any) -> Any:
        nonlocal count
        if not frame.f_code.co_filename.startswith(PACKAGE):
            return None
        if event == "line":
            count += 1
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        call()
    finally:
        sys.settrace(previous)
    return count


def _identical(data: bytes) -> None:
    result = diff.compare(data, data)
    assert result.identical and result.complete, result.limits_hit


@pytest.mark.parametrize("path", [PIEZOMETER, ANNOTATED], ids=lambda p: p.name.split("_")[0])
def test_compare_runs_a_bounded_multiple_of_the_code_reading_runs(path: Path) -> None:
    data = path.read_bytes()
    _identical(data)  # imports and caches are not counted against either side
    read = _lines(lambda: pydiggs.read_bytes(data))
    compared = _lines(lambda: _identical(data))
    ratio = compared / (2 * read)
    print(f"COST lines {path.name}: read {read}, compare {compared} = {ratio:.2f}x two reads")
    assert ratio <= LINES_PER_READ, f"compare ran {ratio:.2f}x the lines of two reads"


def test_compare_takes_a_bounded_multiple_of_the_cpu_time_of_reading() -> None:
    data = ODOT_EXAMPLE_2.read_bytes()
    reads: list[float] = []
    compares: list[float] = []
    # Interleaved, so a slow spell of the runner hits both; the first round, which also
    # imports and fills caches, is the slowest and so never the best.
    for _ in range(ROUNDS):
        start = time.process_time()
        pydiggs.read_bytes(data)
        reads.append(time.process_time() - start)
        start = time.process_time()
        _identical(data)
        compares.append(time.process_time() - start)
    ratio = min(compares) / (2 * min(reads))
    print(
        f"COST cpu {ODOT_EXAMPLE_2.name}: read {min(reads):.3f}s, compare"
        f" {min(compares):.3f}s = {ratio:.2f}x two reads"
    )
    assert ratio <= TIME_PER_READ, f"compare took {ratio:.2f}x the CPU time of two reads"


def _copies(path: Path, count: int) -> bytes:
    """``path`` with every member but the document information ``count`` times.

    Copy ``k`` has ``-k`` added to each ``gml:id`` it defines and to each local reference
    (an attribute ``#id``) to one, so the copies are separate records that refer to their
    own copy, as the original records do.
    """
    root = _xml.parse(str(path)).getroot()
    members = [
        child
        for child in root
        if isinstance(child.tag, str) and etree.QName(child).localname != "documentInformation"
    ]
    ids = {el.get(GML_ID) for member in members for el in member.iter() if el.get(GML_ID)}
    for k in range(1, count):
        for member in members:
            twin = copy.deepcopy(member)
            for el in twin.iter():
                for name, value in el.attrib.items():
                    if name == GML_ID or (value.startswith("#") and value[1:] in ids):
                        el.set(name, f"{value}-{k}")
            root.append(twin)
    return bytes(etree.tostring(root.getroottree(), xml_declaration=True, encoding="UTF-8"))


def test_compare_work_is_linear_in_the_size_of_the_file() -> None:
    one, two = _copies(ANNOTATED, 1), _copies(ANNOTATED, 2)
    assert len(pydiggs.read_bytes(two).features) == 2 * len(pydiggs.read_bytes(one).features)
    _identical(one)
    small = _lines(lambda: _identical(one))
    large = _lines(lambda: _identical(two))
    ratio = large / small
    print(f"COST lines for 1 -> 2 copies of {ANNOTATED.name}: {small} -> {large} = {ratio:.2f}")
    assert ratio <= LINEAR, f"2x the records ran {ratio:.2f}x the lines"
