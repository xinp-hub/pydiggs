"""Complexity tests: each attack shape the security reviews found, at sizes N and 2N.

Every test measures a deterministic work counter (the dictionary work budget, the
dataBlock work count, the number of cached entries), never wall time, so CI noise
cannot flake them. Linear work doubles with the input (ratio about 2; at most 2.5 is
allowed); quadratic work would quadruple. Shapes that are refused instead are tested
on both sides of their limit. Run in CI's Linux job (``-m scaling``).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from lxml import etree

from pydiggs import _xml, validate
from pydiggs.dictionary import DictionarySemanticValidator
from pydiggs.findings import run
from pydiggs.semantic import ContextValidationResult, _check_datablocks_linear

pytestmark = pytest.mark.scaling

NS = "http://diggsml.org/schemas/2.6"
GML = "http://www.opengis.net/gml/3.2"
LINEAR = 2.5
PC = '<propertyClass codeSpace="./d.xml#t">t</propertyClass>'


@pytest.fixture(scope="module")
def semantic() -> DictionarySemanticValidator:
    """One validator set up as validate() does (building it reads the bundled data)."""
    return DictionarySemanticValidator(
        check_value_code_spaces=True,
        local_dictionaries="instance-dir",
        legacy_paths=False,
        dictionary_path_wins=True,
        bounded=True,
    )


def _definition(inner: str) -> str:
    return (
        f'<gml:Dictionary xmlns:gml="{GML}" xmlns:diggs="{NS}" gml:id="x">'
        f'<gml:dictionaryEntry><diggs:Definition gml:id="t">{inner}'
        "</diggs:Definition></gml:dictionaryEntry></gml:Dictionary>"
    )


def _sources(*paths: str, conditionals: tuple[str, ...] = ()) -> str:
    return (
        "<diggs:occurrences><diggs:Occurrence>"
        + "".join(f"<diggs:sourceElementXpath>{p}</diggs:sourceElementXpath>" for p in paths)
        + "".join(
            f"<diggs:conditionalElementXpath>{c}</diggs:conditionalElementXpath>"
            for c in conditionals
        )
        + "</diggs:Occurrence></diggs:occurrences>"
    )


def _work(
    semantic: DictionarySemanticValidator,
    folder: Path,
    body: str,
    files: dict[str, str] | None = None,
    measure: Callable[[DictionarySemanticValidator], int] | None = None,
) -> int:
    """Run the dictionary checks on ``body`` and return the work counter (or ``measure``)."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (folder / name).write_text(text)
    instance = folder / "i.xml"
    instance.write_text(f'<Diggs xmlns="{NS}" xmlns:gml="{GML}">{body}</Diggs>')
    semantic.validate_tree(_xml.parse(str(instance)), instance)
    assert not semantic.limits_hit, semantic.limits_hit
    value = measure(semantic) if measure else semantic._work
    semantic.release()
    return value


def _ratio(small: int, large: int, name: str) -> float:
    ratio = large / max(small, 1)
    print(f"RATIO {name}: {small} -> {large} = {ratio:.2f}")
    return ratio


def test_property_class_siblings(semantic, tmp_path):
    def body(n: int) -> str:
        el = (
            '<propertyClass codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml'
            '#aashto_group_index">aashto_group_index</propertyClass>'
        )
        return f"<characteristicProperty>{el * n}</characteristicProperty>"

    small = _work(semantic, tmp_path / "a", body(2_000))
    large = _work(semantic, tmp_path / "b", body(4_000))
    assert _ratio(small, large, "sibling scans") <= LINEAR


def test_definition_size(semantic, tmp_path):
    def run_with(junk: int, elements: int) -> int:
        files = {
            "d.xml": _definition(
                "<gml:name>t</gml:name>" + _sources("//diggs:propertyClass") + "<x/>" * junk
            )
        }
        return _work(semantic, tmp_path / f"{junk}_{elements}", f"<p>{PC}</p>" * elements, files)

    # Each element must not pay for the Definition again: doubling the elements while
    # the Definition is large costs little more.
    small = run_with(9_000, 500)
    large = run_with(9_000, 1_000)
    assert _ratio(small, large, "definition size x elements") <= LINEAR


def test_long_conditional_paths(semantic, tmp_path):
    def run_with(steps: int) -> int:
        path = "ancestor::diggs:a/" + "diggs:b/" * steps + "diggs:z"
        files = {
            "d.xml": _definition(
                "<gml:name>t</gml:name>" + _sources("//diggs:propertyClass", conditionals=(path,))
            )
        }
        body = "<a>" + "<b/>" * 2_000 + PC + "</a>"
        return _work(semantic, tmp_path / str(steps), body, files)

    small = run_with(30)
    large = run_with(60)
    assert _ratio(small, large, "conditional path length") <= LINEAR


def test_candidate_paths(semantic, tmp_path):
    files = {
        "d.xml": _definition(
            "<gml:name>t</gml:name>"
            + _sources(*(f"//gml:propertyClass[{i}]" for i in range(150)), "//diggs:propertyClass")
        )
    }
    small = _work(semantic, tmp_path / "a", PC * 1_000, files)
    large = _work(semantic, tmp_path / "b", PC * 2_000, files)
    assert small >= 150 * 1_000  # every candidate tried is charged
    assert _ratio(small, large, "candidate paths x elements") <= LINEAR


_CODES = (
    f'<gml:Dictionary xmlns:gml="{GML}" xmlns:diggs="{NS}" gml:id="c">'
    + "".join(f'<diggs:Definition gml:id="v{i}"/>' for i in range(5_000))
    + "</gml:Dictionary>"
)
_C13_DEF = _definition(
    "<gml:name>t</gml:name>"
    + _sources("//diggs:propertyClass")
    + "<diggs:valueCodeSpace>./c.xml</diggs:valueCodeSpace>"
)


def _result_sets(properties: int, rows: int, citing: int = 0) -> str:
    props = "".join(f'<Property index="{i % 4 + 1}">{PC}</Property>' for i in range(properties))
    values = " ".join("v1,v2,v3,v4" for _ in range(rows))
    cites = (
        '<ResultSet><parameters xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#PP"/>'
        f"<dataValues>{values}</dataValues></ResultSet>"
    ) * citing
    return (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f"{props}</properties></PropertyParameters></parameters>"
        f"<dataValues>{values}</dataValues></ResultSet>{cites}"
    )


def test_check13_rows_are_read_once(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def fields(s: DictionarySemanticValidator) -> int:
        return s._check13._fields if s._check13 else 0

    small = _work(semantic, tmp_path / "a", _result_sets(400, 2_000), files, fields)
    large = _work(semantic, tmp_path / "b", _result_sets(800, 2_000), files, fields)
    # Twice the properties reading the same rows: the rows are still read once.
    assert small == large == 8_000
    _ratio(small, large, "check 13 properties x rows (fields read)")


def test_check13_citing_result_sets(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", _result_sets(40, 10, citing=200), files)
    large = _work(semantic, tmp_path / "b", _result_sets(40, 10, citing=400), files)
    assert _ratio(small, large, "check 13 citing result sets") <= LINEAR


def test_check13_distinct_values_per_property(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def body(properties: int, distinct: int) -> str:
        props = "".join(f'<Property index="1">{PC}</Property>' for _ in range(properties))
        values = " ".join(f"x{i}" for i in range(distinct))
        return (
            '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
            f"{props}</properties></PropertyParameters></parameters>"
            f"<dataValues>{values}</dataValues></ResultSet>"
        )

    # Properties x distinct values both doubled: linear if each property's invalid
    # values are computed once, quadratic (x4) if recomputed per property.
    small = _work(semantic, tmp_path / "a", body(200, 2_000), files)
    large = _work(semantic, tmp_path / "b", body(400, 4_000), files)
    assert _ratio(small, large, "check 13 properties x distinct values") <= LINEAR


def test_check13_code_list_terms_frozen_once(semantic, tmp_path, monkeypatch):
    from pydiggs import dictionary

    built = []

    def counting_frozenset(items=()):
        built.append(1)
        return frozenset(items)

    monkeypatch.setattr(dictionary, "frozenset", counting_frozenset, raising=False)
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    _work(semantic, tmp_path / "a", PC * 1_000, files)
    small = len(built)
    built.clear()
    _work(semantic, tmp_path / "b", PC * 2_000, files)
    assert small == len(built)  # one frozenset of terms per code list, not per element
    _ratio(small, len(built), "check 13 code-list frozensets x elements")


def test_check13_code_list_terms(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", PC * 2_000, files)
    large = _work(semantic, tmp_path / "b", PC * 4_000, files)
    # The 5,000 terms are read once, not once per element.
    assert large - small < 4_000 * 5
    _ratio(small, large, "check 13 code-list terms x elements")


def test_ancestor_name_sets(semantic, tmp_path):
    files = {"d.xml": _definition("<gml:name>t</gml:name>" + _sources("//diggs:zz/diggs:x"))}
    chain_open = "".join(f"<c{i}>" for i in range(100))
    chain_close = "".join(f"</c{i}>" for i in reversed(range(100)))

    def stored(s: DictionarySemanticValidator) -> int:
        distinct = {id(names): names for names in s._ancestor_cache.values()}
        return sum(len(names) for names in distinct.values())

    def body(parents: int) -> str:
        return chain_open + '<p><x codeSpace="./d.xml#t">t</x></p>' * parents + chain_close

    small = _work(semantic, tmp_path / "a", body(1_000), files, stored)
    large = _work(semantic, tmp_path / "b", body(2_000), files, stored)
    assert small == large  # the parents share one set; it does not grow with them
    _ratio(small, large, "ancestor name sets (stored names)")


def test_child_path_memo(semantic, tmp_path):
    conditionals = tuple(f"ancestor::diggs:a/diggs:b/diggs:z{i}" for i in range(50))
    files = {
        "d.xml": _definition(
            "<gml:name>t</gml:name>" + _sources("//diggs:propertyClass", conditionals=conditionals)
        )
    }

    def memo(s: DictionarySemanticValidator) -> int:
        return len(s._child_memo)

    small = _work(semantic, tmp_path / "a", "<a>" + "<b/>" * 1_000 + PC * 5 + "</a>", files, memo)
    large = _work(semantic, tmp_path / "b", "<a>" + "<b/>" * 2_000 + PC * 5 + "</a>", files, memo)
    assert _ratio(small, large, "child-path memo entries") <= LINEAR


def _datablock_work(depth: int, tuples: int) -> int:
    root = etree.fromstring(
        "<Diggs>"
        + "<measurement>" * depth
        + "<result><Property/></result><dataBlock>"
        + "1 " * tuples
        + "</dataBlock>"
        + "</measurement>" * depth
        + "</Diggs>"
    )
    result = ContextValidationResult(legacy_paths=False)
    _check_datablocks_linear(root, result)
    return result.work


def test_datablocks_in_nested_measurements():
    # Twice the nesting around the same block: each extra pair costs O(1).
    small = _datablock_work(100, 20_000)
    large = _datablock_work(200, 20_000)
    assert large - small <= 100
    assert (
        _ratio(_datablock_work(100, 10_000), _datablock_work(100, 20_000), "datablock tuples")
        <= LINEAR
    )


def _cement(tmp_path: Path, parents: int, per_parent: int) -> Path:
    path = tmp_path / f"c_{parents}_{per_parent}.xml"
    group = "<Cement>" + "<weight>-1</weight>" * per_parent + "</Cement>"
    path.write_text(f'<Diggs xmlns="{NS}">{group * parents}</Diggs>')
    return path


def test_schematron_fired_element_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MAX_SCHEMATRON_FIRED", 200)
    assert (
        validate(_cement(tmp_path, 10, 20), checks=["schematron"]).steps["schematron"] == "failed"
    )
    assert (
        validate(_cement(tmp_path, 10, 21), checks=["schematron"]).steps["schematron"]
        == "incomplete"
    )


def test_schematron_children_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MAX_SCHEMATRON_SIBLINGS", 100)
    assert (
        validate(_cement(tmp_path, 1, 100), checks=["schematron"]).steps["schematron"] == "failed"
    )
    assert (
        validate(_cement(tmp_path, 1, 101), checks=["schematron"]).steps["schematron"]
        == "incomplete"
    )


def test_entity_expansion_cap(tmp_path):
    def file(uses: int) -> Path:
        path = tmp_path / f"e{uses}.xml"
        path.write_text(
            f'<!DOCTYPE Diggs [<!ENTITY e "{"x" * 40}">]><Diggs xmlns="{NS}">'
            + "<a>&e;</a>" * uses
            + "</Diggs>"
        )
        return path

    # Each use is 10 raw bytes and 40 characters of text: refused once text > 2x bytes.
    def rules(path: Path) -> list[str]:
        return [f.rule_id for f in validate(path, checks=["structure"]).findings]

    assert "structure.entity-expansion" not in rules(file(1))
    assert rules(file(200)) == ["structure.entity-expansion"]
