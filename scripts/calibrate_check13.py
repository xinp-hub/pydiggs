"""Calibrate the Check 13 work budget: how long one unit takes in each charged loop.

The budget (``MAX_CHECK13_WORK`` in ``pydiggs.dictionary``) only keeps Check 13 under
the 30 s bar if every unit costs about the same time, whatever the loop. This script
runs one shape per charged loop, each built so that loop dominates, and prints the
dictionary step's extra CPU per Check 13 unit (the step's CPU minus that of the same
file under a Definition without a ``valueCodeSpace``, where Check 13 does not run)
and the time the whole budget would take at that rate; then the share of the budget
that files shaped like real data use. The figures in the docs were measured on an
Apple M3 Max; CI runners or older laptops may be two to four times slower.

It exits 1 if any loop costs more than 30 ns per unit on the machine it runs on (a
machine-relative figure: run it on the release machine as part of the release
checklist, not in CI).

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
#: The weights invariant: no loop may cost more than this per unit (on an M3 Max).
LIMIT_NS = 30.0
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

    def attributes() -> str:  # many attributes before a group's id (round 14)
        attrs = " ".join(f'a{i}=""' for i in range(2_000))
        return (
            "<ResultSet>"
            + "".join(
                f'<PropertyParameters {attrs} gml:id="P{k}"><Property index="1">{PC}'
                "</Property></PropertyParameters>"
                for k in range(700)
            )
            + "<dataValues>v1</dataValues></ResultSet>"
        )

    def attribute_search() -> str:  # elements whose many attributes hold no index
        attrs = " ".join(f'a{i}=""' for i in range(200))
        return ONE + "</PropertyParameters></parameters></ResultSet>" + f"<e {attrs}/>" * 7_000

    def huge_index() -> str:  # one Property with a padded index, many coded children
        return (
            f'<ResultSet><PropertyParameters><Property index="{" " * 1_000_000}1">'
            + PC * 150_000
            + "</Property></PropertyParameters><dataValues>v1</dataValues></ResultSet>"
        )

    return {
        "layout events": layout,
        "attributes": attributes,
        "attr search": attribute_search,
        "huge index": huge_index,
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


def _legitimate() -> dict[str, Callable[[], str]]:
    """Files shaped like real data, for the share of the budget they use.

    "cpt-like" is a CPT sounding (6 numeric columns, 230,000 rows); "1-char cells"
    has the most cells per byte (10 one-character columns, 500,000 rows).
    ``C x R x S``: one group of C coded Properties (each with a nullValue), S
    ResultSets citing it, each R rows of C fields from 7 distinct values. "densest" is
    the densest file tried: 2,200 Properties and 2,200 ResultSets of 2,200 cells (4.8
    million cells in 9.9 MB).
    """

    def typical(columns: int, rows: int, result_sets: int) -> Callable[[], str]:
        def build() -> str:
            props = "".join(
                f'<Property index="{j + 1}">{PC}<nullValue>-999</nullValue></Property>'
                for j in range(columns)
            )
            data = " ".join([",".join(f"v{j % 7}" for j in range(columns))] * rows)
            first = (
                '<ResultSet><parameters><PropertyParameters gml:id="PP">'
                f"{props}</PropertyParameters></parameters>"
                f"<dataValues>{data}</dataValues></ResultSet>"
            )
            cite = (
                f'<ResultSet><parameters {XL} xlink:href="#PP"/>'
                f"<dataValues>{data}</dataValues></ResultSet>"
            )
            return first + cite * (result_sets - 1)

        return build

    def densest() -> str:
        k = 2_200
        props = "".join(f'<Property index="{i + 1}">{PC}</Property>' for i in range(k))
        row = ",".join(["a"] * k)
        first = (
            '<ResultSet><parameters><PropertyParameters gml:id="PP">'
            f"{props}</PropertyParameters></parameters><dataValues>{row}</dataValues>"
            "</ResultSet>"
        )
        cite = (
            f'<ResultSet><parameters {XL} xlink:href="#PP"/><dataValues>{row}</dataValues>'
            "</ResultSet>"
        )
        return first + cite * k

    def cpt_like() -> str:  # a CPT sounding: 6 numeric columns, 230,000 depths
        props = "".join(f'<Property index="{j + 1}">{PC}</Property>' for j in range(6))
        rows = " ".join(
            ",".join(f"{(i * 7 + j * 13) % 100_000 / 1000:.3f}" for j in range(6))
            for i in range(230_000)
        )
        return (
            f"<ResultSet><parameters><PropertyParameters>{props}</PropertyParameters>"
            f"</parameters><dataValues>{rows}</dataValues></ResultSet>"
        )

    def one_char() -> str:  # the most cells per byte: 10 one-character columns
        props = "".join(f'<Property index="{j + 1}">{PC}</Property>' for j in range(10))
        row = ",".join("abcdefghij")
        return (
            f"<ResultSet><parameters><PropertyParameters>{props}</PropertyParameters>"
            f"</parameters><dataValues>{' '.join([row] * 500_000)}</dataValues></ResultSet>"
        )

    return {
        "cpt-like": cpt_like,
        "1-char cells": one_char,
        "20 x 30000 x 1": typical(20, 30_000, 1),
        "10 x 50000 x 3": typical(10, 50_000, 3),
        "50 x 4000 x 10": typical(50, 4_000, 10),
        "200 x 1000 x 10": typical(200, 1_000, 10),
        "densest": densest,
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
    print()
    print(f"{'legitimate':<15} {'MB':>5} {'CPU s':>6} {'units':>13} {'of cap':>7}")
    for name, build in _legitimate().items():
        cpu, units, size, _ = _measure(build())
        print(f"{name:<15} {size:>5.1f} {cpu:>6.2f} {units:>13,} {units / MAX_CHECK13_WORK:>7.1%}")
    if worst > LIMIT_NS:
        print(
            f"\nFAIL: a loop costs more than {LIMIT_NS:.0f} ns per unit on this machine."
            " The figure is machine-relative (calibrated on an Apple M3 Max): re-weight"
            " that loop in pydiggs.dictionary, or run this on comparable hardware."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
