"""Calibrate the Check 13 work budget: how long one unit takes in each charged loop.

The budget (``MAX_CHECK13_WORK`` in ``pydiggs.dictionary``) only keeps Check 13 under
the 30 s bar if every unit costs about the same time, whatever the loop. This script
runs one shape per charged loop, each built so that loop dominates, and prints the
dictionary step's extra CPU per Check 13 unit (the step's CPU minus that of the same
file under a Definition without a ``valueCodeSpace``, where Check 13 does not run)
and the time the whole budget would take at that rate.

Usage (from the repository root; not a test, it takes about a minute)::

    uv run python scripts/calibrate_check13.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from pydiggs import _xml
from pydiggs.dictionary import MAX_CHECK13_WORK, DictionarySemanticValidator

NS = "http://diggsml.org/schemas/2.6"
GML = "http://www.opengis.net/gml/3.2"
XL = 'xmlns:xlink="http://www.w3.org/1999/xlink"'
PC = '<propertyClass codeSpace="./d.xml#t">t</propertyClass>'
_DEFINITION = (
    f'<gml:Dictionary xmlns:gml="{GML}" xmlns:diggs="{NS}" gml:id="x">'
    '<gml:dictionaryEntry><diggs:Definition gml:id="t"><gml:name>t</gml:name>'
    "<diggs:occurrences><diggs:Occurrence><diggs:sourceElementXpath>//diggs:propertyClass"
    "</diggs:sourceElementXpath></diggs:Occurrence></diggs:occurrences>"
    "{value}</diggs:Definition></gml:dictionaryEntry></gml:Dictionary>"
)
DEFINITION = _DEFINITION.format(value="<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>")
BASELINE = _DEFINITION.format(value="")
CODES = (
    f'<gml:Dictionary xmlns:gml="{GML}" xmlns:diggs="{NS}" gml:id="c">'
    + "".join(f'<diggs:Definition gml:id="v{i}"/>' for i in range(5_000))
    + "</gml:Dictionary>"
)
ONE = f'<ResultSet><parameters><PropertyParameters><Property index="1">{PC}</Property>'


def _shapes() -> dict[str, Callable[[], str]]:
    def layout() -> str:  # element events: a flat tree of empty elements
        return ONE + "</PropertyParameters></parameters></ResultSet>" + "<e/>" * 1_500_000

    def holders() -> str:  # index holders, each a distinct column
        return (
            ONE
            + "".join(f'<a index="{i}"/>' for i in range(2, 400_000))
            + "</PropertyParameters></parameters></ResultSet>"
        )

    def nested() -> str:  # nested groups, holders innermost (round 13)
        return (
            "<ResultSet>"
            + "<PropertyParameters>" * 250
            + f'<Property index="1">{PC}</Property>'
            + "".join(f'<a index="{i}"/>' for i in range(2, 300_000))
            + "</PropertyParameters>" * 250
            + "</ResultSet>"
        )

    def hrefs() -> str:  # references from one ResultSet
        return (
            '<PropertyParameters gml:id="B"><Property index="1">' + PC + "</Property>"
            "</PropertyParameters><ResultSet>"
            + f'<parameters {XL} xlink:href="#B"/>' * 120_000
            + "</ResultSet>"
        )

    def groups() -> str:  # many groups owned by one ResultSet
        pps = "".join(
            f'<PropertyParameters><Property index="{i}">{PC}</Property></PropertyParameters>'
            for i in range(1, 60_000)
        )
        return f"<ResultSet><parameters>{pps}</parameters><dataValues>v1</dataValues></ResultSet>"

    def chars() -> str:  # one long token: characters
        return (
            ONE
            + "</PropertyParameters></parameters><dataValues>"
            + "x" * 9_000_000
            + ("</dataValues></ResultSet>")
        )

    def separators() -> str:  # empty tokens only
        return (
            ONE
            + '</PropertyParameters></parameters><dataValues ts=",">'
            + "," * 9_000_000
            + "</dataValues></ResultSet>"
        )

    def tokens() -> str:  # one-field rows
        return (
            ONE
            + "</PropertyParameters></parameters><dataValues>"
            + "v1 " * 2_500_000
            + "</dataValues></ResultSet>"
        )

    def fields() -> str:  # wide rows, every column wanted
        props = "".join(f'<Property index="{i}">{PC}</Property>' for i in range(1, 501))
        row = ",".join(["v1"] * 500)
        return (
            f"<ResultSet><parameters><PropertyParameters>{props}</PropertyParameters></parameters>"
            f"<dataValues>{' '.join([row] * 3_000)}</dataValues></ResultSet>"
        )

    def merged() -> str:  # distinct values over many citing ResultSets
        return (
            '<ResultSet><parameters><PropertyParameters gml:id="B"><Property index="1">'
            + PC
            + "</Property></PropertyParameters></parameters></ResultSet>"
            + "".join(
                f'<ResultSet><parameters {XL} xlink:href="#B"/><dataValues>x{i} y{i}</dataValues>'
                "</ResultSet>"
                for i in range(80_000)
            )
        )

    def cross() -> str:  # every ResultSet cites every group: min-walk
        pps = "".join(
            f'<PropertyParameters gml:id="P{p}"><Property index="1">{PC}</Property>'
            + "".join(f'<Property index="{c}"/>' for c in range(2, 401))
            + "</PropertyParameters>"
            for p in range(400)
        )
        cites = "".join(f'<parameters {XL} xlink:href="#P{p}"/>' for p in range(400))
        row = ",".join(["a"] * 400)
        return f"<group>{pps}</group>" + (
            f"<ResultSet>{cites}<dataValues>{row}</dataValues></ResultSet>" * 200
        )

    return {
        "layout events": layout,
        "index holders": holders,
        "nested groups": nested,
        "references": hrefs,
        "groups": groups,
        "characters": chars,
        "separators": separators,
        "tokens": tokens,
        "fields": fields,
        "merged values": merged,
        "cross citing": cross,
    }


def _measure(body: str, definition: str = DEFINITION) -> tuple[float, int, float, bool]:
    folder = Path(tempfile.mkdtemp())
    (folder / "d.xml").write_text(definition)
    (folder / "c.xml").write_text(CODES)
    instance = folder / "i.xml"
    instance.write_text(f'<Diggs xmlns="{NS}" xmlns:gml="{GML}">{body}</Diggs>')
    size = instance.stat().st_size / 1e6
    validator = DictionarySemanticValidator(
        check_value_code_spaces=True,
        local_dictionaries="instance-dir",
        legacy_paths=False,
        dictionary_path_wins=True,
        bounded=True,
    )
    tree = _xml.parse(str(instance))
    start = time.process_time()
    validator.validate_tree(tree, instance)
    cpu = time.process_time() - start
    index = validator._check13
    return cpu, index._work if index else 0, size, bool(validator.limits_hit)


def main() -> int:
    """Print the calibration table."""
    print(f"MAX_CHECK13_WORK = {MAX_CHECK13_WORK:,} units")
    print(f"{'loop':<15} {'MB':>5} {'C13 s':>6} {'units':>13} {'ns/unit':>8} {'cap s':>6}")
    worst = 0.0
    for name, build in _shapes().items():
        body = build()
        cpu, units, size, stopped = _measure(body)
        base = _measure(body, BASELINE)[0]
        cpu = max(cpu - base, 0.0)
        rate = cpu / units * 1e9 if units else 0.0
        worst = max(worst, rate)
        note = "  (stopped at a limit)" if stopped else ""
        print(
            f"{name:<15} {size:>5.1f} {cpu:>6.2f} {units:>13,} {rate:>8.1f}"
            f" {rate * MAX_CHECK13_WORK / 1e9:>6.1f}{note}"
        )
    print(f"worst: {worst:.1f} ns/unit, the whole budget in {worst * MAX_CHECK13_WORK / 1e9:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
