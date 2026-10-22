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
        inner = "<gml:name>t</gml:name>" + _sources("//diggs:propertyClass") + "<x/>" * junk
        files = {"d.xml": _definition(inner)}
        return _work(semantic, tmp_path / f"{junk}_{elements}", f"<p>{PC}</p>" * elements, files)

    # Doubling the elements with the Definition fixed: linear.
    assert _ratio(run_with(9_000, 500), run_with(9_000, 1_000), "elements") <= LINEAR
    # Doubling the Definition with the elements fixed: the extra cost is the extra
    # Definition nodes once (4,500), not once per element (500 x 4,500).
    extra = run_with(9_000, 500) - run_with(4_500, 500)
    print(f"RATIO definition size doubled: +{extra} units")
    assert extra <= 4_500 + 100


def test_long_conditional_paths(semantic, tmp_path):
    def run_with(steps: int) -> tuple[int, list[tuple[object, ...]]]:
        # A chain of <b> as deep as the path, so every step is walked to the end.
        path = "ancestor::diggs:a/" + "diggs:b/" * steps + "diggs:z"
        files = {
            "d.xml": _definition(
                "<gml:name>t</gml:name>" + _sources("//diggs:propertyClass", conditionals=(path,))
            )
        }
        body = "<a>" + "<b>" * steps + "</b>" * steps + PC + "</a>"
        keys: list[tuple[object, ...]] = []

        def memo_keys(s: DictionarySemanticValidator) -> int:
            keys.extend(s._child_memo)
            return s._work

        return _work(semantic, tmp_path / str(steps), body, files, memo_keys), keys

    small, keys_small = run_with(30)
    large, keys_large = run_with(60)
    assert _ratio(small, large, "conditional path length, walked to the end") <= LINEAR
    # Memo keys stay three items (node, path identity, offset): no copy of the steps.
    assert {len(k) for k in keys_small + keys_large} == {3}
    assert len(keys_large) >= 60


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


# --- calls to the expensive primitives (a regression that bypasses ``work`` still fails) --


def _counting(monkeypatch, module, name: str, size=lambda result: 1) -> list[int]:
    """Replace ``module.name`` with a wrapper that records ``size(result)`` per call."""
    calls: list[int] = []
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(size(result))
        return result

    monkeypatch.setattr(module, name, wrapper)
    return calls


def test_datablock_tuples_are_counted_once_per_block(monkeypatch):
    from pydiggs import semantic

    counted = _counting(monkeypatch, semantic, "_tuple_lengths", len)
    _datablock_work(100, 20_000)
    small = sum(counted)
    counted.clear()
    _datablock_work(200, 20_000)  # twice the enclosing measurements, same block
    assert small == sum(counted) == 20_000
    _ratio(small, sum(counted), "datablock tuples read (nesting doubled)")


def test_check13_reads_only_the_wanted_column(semantic, tmp_path, monkeypatch):
    from pydiggs import dictionary

    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def body(width: int) -> str:
        row = ",".join(f"x{i}" for i in range(width))
        return (
            '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
            f'<Property index="1">{PC}</Property></properties></PropertyParameters>'
            f"</parameters><dataValues>{row} {row}</dataValues></ResultSet>"
        )

    normalised = _counting(monkeypatch, dictionary, "_normalize_space")

    fields: list[int] = []

    def stored(s: DictionarySemanticValidator) -> int:
        index = s._check13
        if index is None:
            return 0
        fields.append(index._fields)  # each row is split only up to the wanted column
        return sum(len(t) for cols in index._distinct.values() for t in cols.values())

    small = _work(semantic, tmp_path / "a", body(20_000), files, stored)
    calls_small = len(normalised)
    normalised.clear()
    large = _work(semantic, tmp_path / "b", body(40_000), files, stored)
    assert small == large == 1  # one wanted column, one distinct value: nothing else kept
    assert fields == [2, 2]  # two rows, one field read from each
    # Fields are normalised for the wanted column only, not for every field of the row.
    assert len(normalised) <= calls_small + 50
    _ratio(calls_small, len(normalised), "check 13 row width (fields normalised)")


def test_sibling_names_are_read_once_per_parent(semantic, tmp_path, monkeypatch):
    from pydiggs import dictionary

    names = _counting(monkeypatch, dictionary, "_local")

    def body(n: int) -> str:
        el = (
            '<propertyClass codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml'
            '#aashto_group_index">aashto_group_index</propertyClass>'
        )
        return f"<characteristicProperty>{el * n}</characteristicProperty>"

    _work(semantic, tmp_path / "a", body(1_000))
    small = len(names)
    names.clear()
    _work(semantic, tmp_path / "b", body(2_000))
    assert _ratio(small, len(names), "sibling scans (local-name calls)") <= LINEAR


def test_namespace_uri_cap(tmp_path):
    def file(length: int) -> Path:
        path = tmp_path / f"ns{length}.xml"
        path.write_text(f'<Diggs xmlns="urn:{"a" * (length - 4)}"><a/></Diggs>')
        return path

    def rules(path: Path) -> list[str]:
        return [f.rule_id for f in validate(path, checks=["structure"]).findings]

    assert "structure.namespace-too-long" not in rules(file(1_024))
    assert rules(file(1_025)) == ["structure.namespace-too-long"]


def test_namespace_walk_is_linear():
    def walk(n: int) -> int:
        doc = etree.ElementTree(
            etree.fromstring(
                f'<r xmlns="{NS}">'
                + "".join(f'<a xmlns:p{i}="urn:{i}"/>' for i in range(n))
                + "</r>"
            )
        )
        return run._namespaces(doc)[1]  # bytes of all URIs: one entry per declaration

    assert _ratio(walk(5_000), walk(10_000), "namespace declarations (bytes)") <= LINEAR


def test_check13_many_wanted_columns_over_short_rows(semantic, tmp_path):
    """Each row costs what it holds, not the number of columns the properties want."""
    import time

    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def body(columns: int) -> str:
        props = "".join(f'<Property index="{i}">{PC}</Property>' for i in range(1, columns + 1))
        return (
            '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
            f"{props}</properties></PropertyParameters></parameters>"
            f"<dataValues>{' '.join(['z'] * 100_000)}</dataValues></ResultSet>"
        )

    def cpu(columns: int) -> float:
        start = time.process_time()
        _work(semantic, tmp_path / str(columns), body(columns), files)
        return time.process_time() - start

    small, large = cpu(20), cpu(800)  # 40x the wanted columns over one-field rows
    print(f"RATIO check 13 wanted columns x short rows: {small:.2f}s -> {large:.2f}s")
    assert large < 3 * small + 0.5  # wanted x rows would be about 40x (seconds)


def test_geometry_srs_is_one_pass(monkeypatch):
    from pydiggs import semantic

    calls = _counting(monkeypatch, semantic, "_has_both")

    def run_with(depth: int) -> int:
        calls.clear()
        root = etree.fromstring(
            '<Diggs xmlns:gml="http://www.opengis.net/gml/3.2">'
            + '<gml:MultiCurve srsName="x">' * depth
            + "<c/>" * 2_000
            + "</gml:MultiCurve>" * depth
            + "</Diggs>"
        )
        result = ContextValidationResult(legacy_paths=False)
        semantic._check_geometry_srs_official(root, result)
        assert len(result.messages) == depth
        return len(calls)

    # Twice the nested MultiCurves around the same 2,000 children: about twice the
    # calls (two per element), not depth x children.
    assert _ratio(run_with(50), run_with(100), "geometry nesting depth (_has_both calls)") <= LINEAR


def _geometry_calls(monkeypatch, depth: int, points: int) -> int:
    """``_has_both`` calls of the geometry rule on MultiCurves nested ``depth`` deep
    around ``points`` PointLocation/pos (the reviewer's repro shape: 250 deep around
    420,000 took 99 s when each object walked its ancestors and subtree)."""
    from pydiggs import semantic

    calls = _counting(monkeypatch, semantic, "_has_both")
    root = etree.fromstring(
        '<Diggs xmlns:gml="http://www.opengis.net/gml/3.2">'
        + "<gml:MultiCurve>" * depth
        + "<gml:PointLocation><gml:pos>1 2</gml:pos></gml:PointLocation>" * points
        + "</gml:MultiCurve>" * depth
        + "</Diggs>"
    )
    result = ContextValidationResult(legacy_paths=False)
    semantic._check_geometry_srs_official(root, result)
    assert len(result.messages) == depth + points
    monkeypatch.undo()
    return len(calls)


def test_geometry_doubling_depth_around_many_objects(monkeypatch):
    small = _geometry_calls(monkeypatch, 100, 5_000)
    large = _geometry_calls(monkeypatch, 200, 5_000)
    # depth x objects would be about 2x as well, so compare with the element count:
    # 2 calls per element, whatever the depth.
    assert large <= 2 * (200 + 2 * 5_000 + 1) + 1
    assert _ratio(small, large, "geometry nesting depth around 5,000 objects") < 1.05


def test_geometry_doubling_objects_under_deep_nesting(monkeypatch):
    small = _geometry_calls(monkeypatch, 250, 5_000)
    large = _geometry_calls(monkeypatch, 250, 10_000)
    assert large <= 2 * (250 + 2 * 10_000 + 1) + 1  # not 250 per object
    assert _ratio(small, large, "geometry objects under 250 nested MultiCurves") <= LINEAR


def test_check13_one_pass_per_result_set(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def body(groups: int) -> str:
        params = "".join(
            f'<PropertyParameters gml:id="P{i}"><properties><Property index="{i + 2}">{PC}'
            "</Property></properties></PropertyParameters>"
            for i in range(groups)
        )
        return (
            f"<ResultSet><parameters>{params}</parameters>"
            f"<dataValues>{'x' * 50_000}</dataValues></ResultSet>"
        )

    def scanned(s: DictionarySemanticValidator) -> int:
        return s._check13._chars if s._check13 else 0

    small = _work(semantic, tmp_path / "a", body(50), files, scanned)
    large = _work(semantic, tmp_path / "b", body(100), files, scanned)
    # The block is read once, whatever the number of PropertyParameters (each asking
    # for its own column): 50,000 characters, not 50,000 per PropertyParameters.
    assert small == large == 50_000
    _ratio(small, large, "check 13 PropertyParameters (characters scanned)")


def test_check13_doubling_property_parameters_over_many_rows(semantic, tmp_path):
    """The reviewer's repro shape (400 PropertyParameters over 2,000 rows of one 2 KB
    field took 44 s): K PropertyParameters, each asking for its own column, read the
    rows once, so the characters scanned do not grow with K."""
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    row = "x" * 2_000

    def body(groups: int) -> str:
        params = "".join(
            f'<PropertyParameters gml:id="P{i}"><properties><Property index="{i + 1}">{PC}'
            "</Property></properties></PropertyParameters>"
            for i in range(groups)
        )
        values = " ".join([row] * 200)
        return f"<ResultSet><parameters>{params}</parameters><dataValues>{values}</dataValues></ResultSet>"

    def scanned(s: DictionarySemanticValidator) -> int:
        return s._check13._chars if s._check13 else 0

    small = _work(semantic, tmp_path / "a", body(100), files, scanned)
    large = _work(semantic, tmp_path / "b", body(200), files, scanned)
    assert small == large == 200 * 2_000 + 199  # every character, separators included
    _ratio(small, large, "check 13 PropertyParameters x rows (characters scanned)")


def test_check13_splits_a_wide_row_only_up_to_the_wanted_column(semantic, tmp_path):
    """Pins maxsplit: one row of a million fields, one wanted column. Splitting every
    field would hold a million strings (tens of MB); the split stops after field 1."""
    import tracemalloc

    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    body = (
        '<ResultSet><parameters><PropertyParameters gml:id="PP"><properties>'
        f'<Property index="1">{PC}</Property></properties></PropertyParameters>'
        f"</parameters><dataValues>{','.join(['ab'] * 1_000_000)}</dataValues></ResultSet>"
    )
    peaks: list[int] = []

    def peak(s: DictionarySemanticValidator) -> int:
        peaks.append(tracemalloc.get_traced_memory()[1])
        return s._check13._fields if s._check13 else 0

    (tmp_path / "w").mkdir()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        fields = _work(semantic, tmp_path / "w", body, files, peak)
    finally:
        tracemalloc.stop()
    assert fields == 1
    print(f"RATIO check 13 million-field row: peak {peaks[0] / 2**20:.1f} MiB")
    assert peaks[0] < 25 * 2**20, peaks  # a full split alone is about 60 MiB


# --- round 12: every Check 13 unit is charged to one counter --------------------------

_XL = 'xmlns:xlink="http://www.w3.org/1999/xlink"'


def _c13_work(s: DictionarySemanticValidator) -> int:
    return s._check13._work if s._check13 else 0


def _bigcite(result_sets: int, properties: int) -> str:
    """ResultSets each citing #B (``properties`` Properties) beside an empty own group."""
    fill = '<Property index="2"/>' * properties
    return (
        '<ResultSet><parameters><PropertyParameters gml:id="B"><properties>'
        f'<Property index="1">{PC}</Property>{fill}</properties></PropertyParameters>'
        "</parameters></ResultSet>"
        + f'<ResultSet><parameters {_XL} xlink:href="#B"><PropertyParameters/></parameters>'
        "<dataValues>v1,v2</dataValues></ResultSet>" * result_sets
    )


def test_check13_bigcite_doubling_result_sets(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", _bigcite(500, 2_000), files, _c13_work)
    large = _work(semantic, tmp_path / "b", _bigcite(1_000, 2_000), files, _c13_work)
    assert _ratio(small, large, "check 13 bigcite result sets (work)") <= LINEAR


def test_check13_bigcite_doubling_cited_properties(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", _bigcite(500, 2_000), files, _c13_work)
    large = _work(semantic, tmp_path / "b", _bigcite(500, 4_000), files, _c13_work)
    # The extra Properties are charged once each (about 50 units: their layout events
    # and kept index), not once per citing ResultSet (500 x 2,000 more).
    assert large - small <= 2_000 * 64, (small, large)
    _ratio(small, large, "check 13 bigcite cited properties (work)")


def _dupcite(result_sets: int) -> str:
    """ResultSet i cites #B i times."""
    return (
        '<ResultSet><parameters><PropertyParameters gml:id="B"><properties>'
        f'<Property index="1">{PC}</Property>'
        + '<Property index="2"/>' * 200
        + "</properties></PropertyParameters></parameters></ResultSet>"
        + "".join(
            "<ResultSet>" + f'<parameters {_XL} xlink:href="#B"/>' * (i + 1) + "</ResultSet>"
            for i in range(result_sets)
        )
    )


def test_check13_dupcite_work_is_linear_in_the_file(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", _dupcite(100), files, _c13_work)
    large = _work(semantic, tmp_path / "b", _dupcite(200), files, _c13_work)
    # The hrefs themselves grow 4x (R^2/2); the work is charged per href, so it grows
    # with the file, never with R x the cited group's 201 Properties.
    hrefs = (200 * 201 // 2) / (100 * 101 // 2)
    assert _ratio(small, large, "check 13 dupcite (work)") <= hrefs * 1.1


def _outside(groups: int, commas: int) -> str:
    """PropertyParameters at ResultSet/x (outside parameters) over separator-only data."""
    pps = "".join(
        f'<PropertyParameters gml:id="P{k}"><properties><Property index="{k + 1}">{PC}'
        "</Property></properties></PropertyParameters>"
        for k in range(groups)
    )
    return f'<ResultSet><parameters/><x>{pps}</x><dataValues ts=",">{"," * commas}</dataValues></ResultSet>'


def test_check13_outside_groups_share_one_pass(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def chars(s: DictionarySemanticValidator) -> int:
        return s._check13._chars if s._check13 else 0

    small = _work(semantic, tmp_path / "a", _outside(100, 50_000), files, chars)
    large = _work(semantic, tmp_path / "b", _outside(200, 50_000), files, chars)
    # Nearest-ResultSet rule: the groups under x are this ResultSet's, read in one pass.
    assert small == large == 50_000


def test_check13_outside_doubling_commas(semantic, tmp_path):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", _outside(100, 100_000), files, _c13_work)
    large = _work(semantic, tmp_path / "b", _outside(100, 200_000), files, _c13_work)
    assert small >= 100_000 * 4  # each comma is charged: a character and an empty token
    assert _ratio(small, large, "check 13 separator-only data (work)") <= LINEAR


def test_check13_cited_groups_join_the_one_pass(semantic, tmp_path):
    """K ``parameters xlink:href="#Pi"`` on one ResultSet: still one pass."""
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def body(groups: int) -> str:
        params = "".join(
            f'<PropertyParameters gml:id="P{i}"><properties><Property index="{i + 1}">{PC}'
            "</Property></properties></PropertyParameters>"
            for i in range(groups)
        )
        cites = "".join(f'<parameters {_XL} xlink:href="#P{i}"/>' for i in range(groups))
        return (
            f"<group>{params}</group>"
            f"<ResultSet>{cites}<dataValues>{'x' * 50_000}</dataValues></ResultSet>"
        )

    def chars(s: DictionarySemanticValidator) -> int:
        return s._check13._chars if s._check13 else 0

    small = _work(semantic, tmp_path / "a", body(50), files, chars)
    large = _work(semantic, tmp_path / "b", body(100), files, chars)
    assert small == large == 50_000


def _propcite(properties: int, result_sets: int) -> str:
    """``properties`` coded Properties in #B, each read from ``result_sets`` citers:
    inherently properties x ResultSets, as in the official module."""
    props = "".join(f'<Property index="{i + 1}">{PC}</Property>' for i in range(properties))
    return (
        f'<ResultSet><parameters><PropertyParameters gml:id="B"><properties>{props}'
        "</properties></PropertyParameters></parameters></ResultSet>"
        + f'<ResultSet><parameters {_XL} xlink:href="#B"/><dataValues/></ResultSet>'
        * result_sets
    )


def _cross(groups: int, result_sets: int, columns: int) -> str:
    """Every ResultSet cites every group: memberships x columns, which grows faster
    than the file; it must stop at the Check 13 budget."""
    pps = "".join(
        f'<PropertyParameters gml:id="P{p}"><Property index="1">{PC}</Property>'
        + "".join(f'<Property index="{c}"/>' for c in range(2, columns + 1))
        + "</PropertyParameters>"
        for p in range(groups)
    )
    cites = "".join(f'<parameters {_XL} xlink:href="#P{p}"/>' for p in range(groups))
    row = ",".join(["a"] * columns)
    return f"<group>{pps}</group>" + (
        f"<ResultSet>{cites}<dataValues>{row}</dataValues></ResultSet>" * result_sets
    )


def test_check13_an_index_outside_a_property_joins_the_one_pass(semantic, tmp_path):
    """A coded element whose parent is the PropertyParameters itself asks for that
    element's ``index``: the column set includes it, so there is no pass per group."""
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}

    def body(groups: int) -> str:
        pps = "".join(
            f'<PropertyParameters index="{k + 1}">{PC}</PropertyParameters>' for k in range(groups)
        )
        return f"<ResultSet><parameters>{pps}</parameters><dataValues>{'x' * 50_000}</dataValues></ResultSet>"

    def chars(s: DictionarySemanticValidator) -> int:
        return s._check13._chars if s._check13 else 0

    small = _work(semantic, tmp_path / "a", body(50), files, chars)
    large = _work(semantic, tmp_path / "b", body(100), files, chars)
    assert small == large == 50_000


def test_check13_doubling_properties_over_citing_result_sets(semantic, tmp_path):
    """Each property's values are merged once per group: properties x ResultSets is
    gone (the security review's 10 MB shape took 60 s under CI coverage)."""
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(semantic, tmp_path / "a", _propcite(500, 500), files, _c13_work)
    large = _work(semantic, tmp_path / "b", _propcite(1_000, 1_000), files, _c13_work)
    assert _ratio(small, large, "check 13 properties x citing ResultSets (work)") <= LINEAR


def _nestpp(depth: int, holders: int, *, distinct: bool) -> str:
    """One ResultSet, ``depth`` nested PropertyParameters, ``holders`` index holders
    innermost (round 13: each holder was re-read by every enclosing group)."""
    fill = (
        "".join(f'<a index="{i + 2}"/>' for i in range(holders))
        if distinct
        else '<a index="1"/>' * holders
    )
    return (
        "<ResultSet>"
        + "<PropertyParameters>" * depth
        + f'<Property index="1">{PC}</Property>{fill}'
        + "</PropertyParameters>" * depth
        + "<dataValues>v1 v2</dataValues></ResultSet>"
    )


@pytest.mark.parametrize("distinct", [False, True], ids=["nestpp", "nestppd"])
def test_check13_nested_groups_doubling_depth(semantic, tmp_path, distinct):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(
        semantic, tmp_path / "a", _nestpp(100, 5_000, distinct=distinct), files, _c13_work
    )
    large = _work(
        semantic, tmp_path / "b", _nestpp(200, 5_000, distinct=distinct), files, _c13_work
    )
    # Depth x holders would double the work; each extra group costs a few steps.
    assert large - small < 100 * 200, (small, large)
    _ratio(small, large, "check 13 nested groups depth (work)")


@pytest.mark.parametrize("distinct", [False, True], ids=["nestpp", "nestppd"])
def test_check13_nested_groups_doubling_holders(semantic, tmp_path, distinct):
    files = {"d.xml": _C13_DEF, "c.xml": _CODES}
    small = _work(
        semantic, tmp_path / "a", _nestpp(200, 5_000, distinct=distinct), files, _c13_work
    )
    large = _work(
        semantic, tmp_path / "b", _nestpp(200, 10_000, distinct=distinct), files, _c13_work
    )
    assert _ratio(small, large, "check 13 nested groups holders (work)") <= LINEAR


def _merge(groups: int, result_sets: int, values: int, *, skip_own: bool) -> str:
    """Every ResultSet cites every group (or all but one, so each group is read from a
    different set of ResultSets), each with ``values`` distinct values (code review,
    round 13: merge10 / merge10b)."""
    pps = "".join(
        f'<PropertyParameters gml:id="P{p}"><Property index="1">{PC}</Property>'
        "</PropertyParameters>"
        for p in range(groups)
    )
    sets = []
    for r in range(result_sets):
        cites = "".join(
            f'<parameters {_XL} xlink:href="#P{p}"/>'
            for p in range(groups)
            if not (skip_own and p == r)
        )
        data = " ".join(f"r{r}x{j}" for j in range(values))
        sets.append(f"<ResultSet>{cites}<dataValues>{data}</dataValues></ResultSet>")
    return f"<group>{pps}</group>" + "".join(sets)
