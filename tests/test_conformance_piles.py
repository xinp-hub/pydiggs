"""The deep-foundations scenario: piles and pile driving records."""

from __future__ import annotations

import copy
import dataclasses
import re
from collections.abc import Callable
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

import pydiggs
from pydiggs.conformance import _challenge, _facts, _piles
from pydiggs.conformance._scenario import Dataset
from pydiggs.model import ExtraNode, Model

SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)
DIGGS = "http://diggsml.org/schemas/3"
GML = "http://www.opengis.net/gml/3.2"
NS = {"d": DIGGS, "gml": GML}
GML_ID = f"{{{GML}}}id"


def _dataset(seed: str = "demo") -> Dataset:
    return _piles.build(f"1.0-beta.1|piles|{seed}", "ABC123")


def _seed_with(unit: str) -> str:
    """A seed whose dataset is in ``unit`` (``m`` or ``ft``)."""
    for n in range(100):
        if _dataset(f"s{n}").table("driving").fields[1].unit == unit:
            return f"s{n}"
    raise AssertionError(unit)


def _outcomes(dataset: Dataset, xml: bytes) -> dict[str, _facts.Outcome]:
    seen = _piles.observe(pydiggs.read_bytes(xml))
    return {f.id: _facts.match(f, seen) for f in _piles.facts(dataset)}


def _failed(outcomes: dict[str, _facts.Outcome]) -> dict[str, _facts.Outcome]:
    return {k: o for k, o in outcomes.items() if o.status != "found"}


def _errors(xml: bytes) -> list[str]:
    report = pydiggs.validate_bytes(xml)
    return [f"{f.rule_id}: {f.message}" for f in report.findings if f.severity.value == "error"]


def _rows(dataset: Dataset, table: str) -> list[dict[str, str]]:
    found = dataset.table(table)
    return [dict(zip([f.name for f in found.fields], row, strict=True)) for row in found.rows]


# --- the reference file -----------------------------------------------------------------


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=SEEDS)
def test_the_reference_file_of_any_seed_is_valid_and_holds_every_fact(seed: str) -> None:
    dataset = _dataset(seed)
    xml = _piles.reference_xml(dataset)
    report = pydiggs.validate_bytes(xml)
    assert report.ok and not _errors(xml)
    # The only warnings: no srsName on the site-grid positions, as for SPT and CPT.
    assert {f.rule_id for f in report.findings} <= {"geometry.srs-attributes"}
    model = pydiggs.read_bytes(xml)
    piles = dataset.table("piles").column("pile_id")
    assert sorted(f.name or "" for f in model.features if f.element.endswith("Pile")) == sorted(
        piles
    )
    assert not _failed(_outcomes(dataset, xml))


def test_the_dataset_has_its_traps() -> None:
    units = set()
    for n in range(60):
        dataset = _dataset(f"t{n}")
        piles = _rows(dataset, "piles")
        driving = _rows(dataset, "driving")
        units.add(dataset.table("driving").fields[1].unit)
        assert 2 <= len(piles) <= 3 and any(" " in p["pile_id"] for p in piles)
        assert "&" in dataset.table("project").rows[0][1]
        for pile in piles:
            mine = [r for r in driving if r["pile_id"] == pile["pile_id"]]
            assert 15 <= len(mine) <= 40
            assert int(mine[-1]["blows"]) >= 60  # refusal over a short last increment
            assert Decimal(mine[-1]["pen_increment"]) < Decimal(mine[0]["pen_increment"])
            assert sum(int(r["blows"]) <= 1 for r in mine) == 1  # the pile ran once
            assert all(r["stroke"] == "" for r in mine if r["blows"] == "0")
            assert mine[-1]["tip_depth"] == pile["embedded_length"]
            assert pile["pile_type"] in _piles.ELEMENTS
    assert units == {"m", "ft"}


def test_the_reference_file_is_the_same_for_the_same_dataset() -> None:
    assert _piles.reference_xml(_dataset()) == _piles.reference_xml(_dataset())
    assert _piles.reference_xml(_dataset()) != _piles.reference_xml(_dataset("other"))


def test_the_reference_file_carries_the_values_in_the_tables_units() -> None:
    dataset = _dataset(_seed_with("ft"))
    xml = _piles.reference_xml(dataset).decode()
    pile = _rows(dataset, "piles")[0]
    assert f'<totalPileLength uom="ft">{pile["total_length"]}<' in xml
    assert f'<widthAtTop uom="in">{pile["width"]}<' in xml
    assert f'<nominalCapacity uom="klbf">{pile["nominal_capacity"]}<' in xml
    assert "&amp; Approach" in xml


@pytest.mark.parametrize("defect", _facts.DEFECTS)
def test_every_defect_of_the_suite_can_be_planted_and_is_reported(defect: str) -> None:
    xml = _piles.reference_xml(_dataset())
    rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[defect]
    planted = pydiggs.validate_bytes(_challenge._defective(xml, defect))
    assert any(f.rule_id.startswith(rule) for f in planted.findings)
    assert not any(f.rule_id.startswith(rule) for f in pydiggs.validate_bytes(xml).findings)


# --- grading an export ------------------------------------------------------------------


def test_a_changed_blow_count_differs() -> None:
    dataset = _dataset()
    xml = _piles.reference_xml(dataset).decode()
    first = _rows(dataset, "driving")[0]
    changed = re.sub(
        r'(<dataValues cs="," ts=" ">)(\d+),',
        lambda m: f"{m.group(1)}{int(m.group(2)) + 1},",
        xml,
        count=1,
    )
    assert changed != xml
    failed = _failed(_outcomes(dataset, changed.encode()))
    fact_id = f"piles/{first['pile_id']}/{first['tip_depth']}/blows"
    assert set(failed) == {fact_id}
    assert failed[fact_id].check == "conformance.value-differs"


def test_a_missing_pile_is_missing() -> None:
    dataset = _dataset()
    name = _rows(dataset, "piles")[1]["pile_id"]
    xml = _piles.reference_xml(dataset).decode()
    changed = xml.replace(f"<gml:name>{name}</gml:name>", "<gml:name>renamed</gml:name>")
    assert changed != xml
    failed = _failed(_outcomes(dataset, changed.encode()))
    assert failed[f"core/{name}"].check == "conformance.fact-missing"
    assert all(o.check == "conformance.fact-missing" for o in failed.values())
    assert all(k == f"core/{name}" or k.startswith(f"piles/{name}/") for k in failed)


def _in_other_units(xml: bytes, unit: str) -> bytes:
    """``xml`` with every length in ``unit`` restated in the other length unit."""
    other = "m" if unit == "ft" else "ft"
    factor = 0.3048 if unit == "ft" else 1 / 0.3048
    root = etree.fromstring(xml)

    def numbers(text: str | None) -> str:
        return " ".join(f"{float(t) * factor:.6f}" for t in (text or "").split())

    for element in root.iter():
        if element.get("uom") == unit:
            element.set("uom", other)
            element.text = numbers(element.text)
    for units in root.iterfind(".//d:LinearReferencingMethod/d:units", NS):
        units.text = other
    for points in root.iterfind(".//*[@srsDimension='1']/gml:posList", NS):
        points.text = numbers(points.text)
    for result in root.iterfind(".//d:pileDrivingRecordResults/d:ResultSet", NS):
        for uom in result.iterfind(".//d:Property/d:uom", NS):
            uom.text = other
        values = result.find("d:dataValues", NS)
        assert values is not None
        rows = []
        for row in (values.text or "").split():
            blows, increment, stroke = row.split(",")
            restated = [f"{float(v) * factor:.6f}" if v else "" for v in (increment, stroke)]
            rows.append(",".join([blows, *restated]))
        values.text = " ".join(rows)
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))


@pytest.mark.parametrize("unit", ["m", "ft"])
def test_the_same_data_in_the_other_length_unit_is_found(unit: str) -> None:
    dataset = _dataset(_seed_with(unit))
    xml = _in_other_units(_piles.reference_xml(dataset), unit)
    other = "m" if unit == "ft" else "ft"
    assert f'<totalPileLength uom="{other}">'.encode() in xml
    assert not _errors(xml)
    assert not _failed(_outcomes(dataset, xml))


def _tree(dataset: Dataset) -> etree._Element:
    return etree.fromstring(_piles.reference_xml(dataset))


def _records(root: etree._Element) -> list[etree._Element]:
    return list(root.iterfind(".//d:pileDrivingRecord", NS))


def _renamed(element: etree._Element, suffix: str) -> etree._Element:
    twin = copy.deepcopy(element)
    for node in twin.iter():
        if node.get(GML_ID):
            node.set(GML_ID, node.get(GML_ID, "") + suffix)
    return twin


def test_a_duplicated_record_with_one_wrong_value_differs() -> None:
    dataset = _dataset()
    root = _tree(dataset)
    record = _records(root)[0]
    twin = _renamed(record, "-twin")
    values = twin.find(".//d:dataValues", NS)
    assert values is not None and values.text
    first, rest = values.text.split(" ", 1)
    blows, others = first.split(",", 1)
    values.text = f"{int(blows) + 7},{others} {rest}"
    record.addnext(twin)
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    assert not _errors(xml)
    failed = _failed(_outcomes(dataset, xml))
    row = _rows(dataset, "driving")[0]
    assert set(failed) == {f"piles/{row['pile_id']}/{row['tip_depth']}/blows"}
    assert next(iter(failed.values())).check == "conformance.value-differs"
    # The same record twice, both right, still passes: every reading agrees.
    twin_values = twin.find(".//d:dataValues", NS)
    assert twin_values is not None
    twin_values.text = values.text.replace(f"{int(blows) + 7},", f"{blows},", 1)
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    assert not _failed(_outcomes(dataset, xml))


def _split(record: etree._Element) -> None:
    """Replace a driving record by two: its first rows, then the rest."""
    first = _renamed(record, "-a")
    second = _renamed(record, "-b")
    for part, take in ((first, slice(None, 5)), (second, slice(5, None))):
        tips = part.find(".//gml:posList", NS)
        values = part.find(".//d:dataValues", NS)
        assert tips is not None and values is not None
        tips.text = " ".join((tips.text or "").split()[take])
        values.text = " ".join((values.text or "").split()[take])
    record.addnext(second)
    record.addnext(first)
    parent = record.getparent()
    assert parent is not None
    parent.remove(record)


def test_a_record_split_in_two_is_found() -> None:
    dataset = _dataset()
    root = _tree(dataset)
    records = _records(root)
    _split(records[0])
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    assert not _errors(xml)
    assert len(re.findall(rb"<PileDrivingRecord ", xml)) == len(records) + 1
    assert not _failed(_outcomes(dataset, xml))


def _driving_failed(failed: dict[str, _facts.Outcome], dataset: Dataset) -> set[str]:
    """The piles whose driving facts (and only those) failed."""
    piles = set(dataset.table("piles").column("pile_id"))
    found = {k.split("/")[1] for k in failed}
    assert all(k.startswith("piles/") and k.count("/") == 3 for k in failed)
    assert found <= piles
    return found


def _refs(root: etree._Element) -> list[etree._Element]:
    return list(root.iterfind(".//d:PileDrivingActivity/d:samplingFeatureRef", NS))


XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def test_records_of_swapped_activities_are_not_read() -> None:
    dataset = _dataset()
    names = dataset.table("piles").column("pile_id")
    root = _tree(dataset)
    first, second = _refs(root)[:2]
    first.set(XLINK_HREF, "#pile-2")
    second.set(XLINK_HREF, "#pile-1")
    xml = etree.tostring(root)
    assert not _errors(xml)
    failed = _failed(_outcomes(dataset, xml))
    assert _driving_failed(failed, dataset) == {names[0], names[1]}
    assert {o.check for o in failed.values()} == {"conformance.fact-missing"}


def test_two_activities_naming_one_pile_place_only_its_record() -> None:
    dataset = _dataset()
    names = dataset.table("piles").column("pile_id")
    root = _tree(dataset)
    _refs(root)[1].set(XLINK_HREF, "#pile-1")
    failed = _failed(_outcomes(dataset, etree.tostring(root)))
    assert _driving_failed(failed, dataset) == {names[1]}


def test_one_activity_for_every_pile_places_only_its_own_pile() -> None:
    # Not valid 3.0.0 in spirit (an activity drives one pile): the other records' tip
    # locations name other piles, so they are not placed.
    dataset = _dataset()
    names = dataset.table("piles").column("pile_id")
    root = _tree(dataset)
    records = _records(root)
    first_activity = root.find(".//d:PileDrivingActivity", NS)
    assert first_activity is not None
    for record in records[1:]:
        activity = record.getparent()
        assert activity is not None
        first_activity.append(record)
        holder = activity.getparent()
        assert holder is not None
        root.remove(holder)
    failed = _failed(_outcomes(dataset, etree.tostring(root)))
    assert _driving_failed(failed, dataset) == set(names[1:])


def test_an_activity_naming_no_pile_is_placed_by_the_lrs_owner() -> None:
    dataset = _dataset()
    root = _tree(dataset)
    _refs(root)[1].set(XLINK_HREF, "#boring-1")
    xml = etree.tostring(root)
    assert not _errors(xml)
    assert not _failed(_outcomes(dataset, xml))


def test_an_lrs_owned_by_two_piles_is_not_placed() -> None:
    dataset = _dataset()
    names = dataset.table("piles").column("pile_id")
    model = pydiggs.read_bytes(_piles.reference_xml(dataset))
    features = tuple(
        dataclasses.replace(f, lrs="#pile-1-lrs") if f.key == "pile-2" else f
        for f in model.features
    )
    seen = _piles.observe(dataclasses.replace(model, features=features))
    failed = _failed({f.id: _facts.match(f, seen) for f in _piles.facts(dataset)})
    assert names[0] in _driving_failed(failed, dataset)


def _seed_where(found: Callable[[Dataset], bool]) -> str:
    for n in range(200):
        if found(_dataset(f"s{n}")):
            return f"s{n}"
    raise AssertionError("no seed")


def test_an_invented_stroke_is_named() -> None:
    seed = _seed_where(lambda d: "0" in d.table("driving").column("blows"))
    dataset = _dataset(seed)
    (row,) = [r for r in _rows(dataset, "driving") if r["blows"] == "0"]
    xml = _piles.reference_xml(dataset).decode()
    changed, count = re.subn(
        rf"(?<=[ >])0,{re.escape(row['pen_increment'])},(?=[ <])",
        f"0,{row['pen_increment']},0",
        xml,
    )
    assert count == 1
    assert not _errors(changed.encode())
    failed = _failed(_outcomes(dataset, changed.encode()))
    fact_id = f"piles/{row['pile_id']}/{row['tip_depth']}/stroke"
    assert set(failed) == {fact_id}
    assert failed[fact_id].check == "conformance.value-invented"
    # The reference itself gives no stroke there, and passes.
    assert not _failed(_outcomes(dataset, xml.encode()))


def test_an_invented_wall_thickness_is_named() -> None:
    seed = _seed_where(lambda d: "concrete" in d.table("piles").column("pile_type"))
    dataset = _dataset(seed)
    pile = next(p for p in _rows(dataset, "piles") if p["pile_type"] == "concrete")
    unit = dataset.table("piles").fields[11].unit
    xml = _piles.reference_xml(dataset).decode()
    changed = xml.replace(
        "<shape>square solid</shape>",
        f'<shape>square solid</shape><wallThickness uom="{unit}">0</wallThickness>',
    )
    assert changed != xml
    assert not _errors(changed.encode())
    failed = _failed(_outcomes(dataset, changed.encode()))
    assert failed[f"piles/{pile['pile_id']}/wall_thickness"].check == ("conformance.value-invented")
    assert all(o.check == "conformance.value-invented" for o in failed.values())


def _wide(dataset: Dataset, rows: int, columns: int) -> bytes:
    """The reference with a first record of ``rows`` one-value rows and ``columns`` columns."""
    root = _tree(dataset)
    record = root.find(".//d:PileDrivingRecord", NS)
    assert record is not None
    tips = record.find(".//gml:posList", NS)
    values = record.find(".//d:dataValues", NS)
    properties = record.find(".//d:properties", NS)
    assert tips is not None and values is not None and properties is not None
    tips.text = " ".join(f"{i / 100:.2f}" for i in range(1, rows + 1))
    values.text = " ".join("5" for _ in range(rows))
    first = properties[0]
    for _ in range(columns - len(properties)):
        properties.append(copy.deepcopy(first))
    return bytes(etree.tostring(root))


def test_observing_a_record_is_linear_in_its_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = _dataset()
    spent: list[int] = []
    original = _piles._Budget.spend

    def counting(self: _piles._Budget) -> bool:
        spent[-1] += 1
        return original(self)

    monkeypatch.setattr(_piles._Budget, "spend", counting)
    work = []
    for rows in (2000, 4000):
        model = pydiggs.read_bytes(_wide(dataset, rows, 1000))
        spent.append(0)
        seen = _piles.observe(model)
        assert len(seen.readings[("pile-1", _piles.RECORD, "blow_count")]) == rows
        work.append(spent[-1])
    # Doubling the rows adds about one unit of work per added cell, not one per column.
    assert work[1] - work[0] <= 2 * 2000


def test_a_value_in_a_unit_of_the_wrong_kind_is_not_read_as_right() -> None:
    dataset = _dataset(_seed_with("m"))
    xml = _piles.reference_xml(dataset).decode()
    changed = xml.replace("<uom>m</uom>", "<uom>s</uom>")
    failed = _failed(_outcomes(dataset, changed.encode()))
    assert failed and all(o.check == "conformance.value-differs" for o in failed.values())
    assert all(o.got is not None and o.got.endswith(" s") for o in failed.values())
    unknown = xml.replace("<uom>m</uom>", "<uom>furlongs-ish</uom>")
    failed = _failed(_outcomes(dataset, unknown.encode()))
    assert failed and all(o.check == "conformance.fact-missing" for o in failed.values())


def test_records_that_cannot_be_placed_are_not_read() -> None:
    dataset = _dataset()
    root = _tree(dataset)
    tips = root.find(".//d:PileDrivingRecord//gml:posList", NS)
    assert tips is not None and tips.text
    tips.text = tips.text.rsplit(" ", 1)[0]  # one depth fewer than rows
    seen = _piles.observe(pydiggs.read_bytes(etree.tostring(root)))
    assert ("pile-1", _piles.RECORD, "blow_count") not in seen.readings
    assert ("pile-2", _piles.RECORD, "blow_count") in seen.readings


def _with_extra(model: Model, extra: tuple[ExtraNode, ...]) -> Model:
    other = dataclasses.replace(model.others[0], extra=extra)
    return dataclasses.replace(model, others=(other, *model.others[1:]))


def test_a_hostile_extra_tree_does_not_crash_observe() -> None:
    dataset = _dataset()
    model = pydiggs.read_bytes(_piles.reference_xml(dataset))
    deep = ExtraNode(qname="PileDrivingRecord", ns=DIGGS)
    for _ in range(200_000):
        deep = ExtraNode(qname="x", ns=DIGGS, children=(deep,))
    seen = _piles.observe(_with_extra(model, (deep,)))
    assert seen.named
    leaf = ExtraNode(qname="PileDrivingRecord", ns=DIGGS)
    wide = ExtraNode(qname="x", ns=DIGGS, children=(leaf,) * (_piles._MAX_NODES + 10))
    assert _piles.observe(_with_extra(model, (wide, *model.others[0].extra))) is not None


def test_garbage_in_a_record_is_left_out() -> None:
    dataset = _dataset()
    xml = _piles.reference_xml(dataset).decode()
    changed = re.sub(r'(<dataValues cs="," ts=" ">)(\d+),', r"\g<1>nan,", xml, count=1).replace(
        'srsName="#pile-2-lrs" srsDimension="1"', 'srsName="#nowhere" srsDimension="1"'
    )
    seen = _piles.observe(pydiggs.read_bytes(changed.encode()))
    first = _rows(dataset, "driving")[0]
    failed = _failed(_outcomes(dataset, changed.encode()))
    assert failed[f"piles/{first['pile_id']}/{first['tip_depth']}/blows"].status == "missing"
    # Pile 2's record still names its pile through its activity; its LRS is unknown, so
    # its depths, and with them its values, are not read.
    assert ("pile-2", _piles.RECORD, "blow_count") not in seen.readings


# --- the import questions ---------------------------------------------------------------


def _other(text: str, unit: str) -> float:
    factor = {"m": 1 / 0.3048, "ft": 0.3048, "mm": 1 / 25.4, "in": 25.4}[unit]
    return float(text) * factor


def _pile_in(text: str, names: list[str]) -> str:
    (name,) = [n for n in names if f"pile {n} " in text or text.endswith(f"pile {n}?")]
    return name


@pytest.mark.parametrize("seed", ["demo", "s1", "s2", "s3", "s4", "s5"])
def test_questions_are_answered_by_the_tables(seed: str) -> None:
    dataset = _dataset(seed)
    asked = {q.id: q for q in _piles.questions(dataset, seed)}
    assert asked == {q.id: q for q in _piles.questions(dataset, seed)}
    piles = {p["pile_id"]: p for p in _rows(dataset, "piles")}
    driving = _rows(dataset, "driving")
    names = list(piles)
    unit = dataset.table("driving").fields[1].unit
    size = dataset.table("piles").fields[10].unit
    assert asked["core.project-name"].expected == dataset.table("project").rows[0][1]
    assert asked["core.piles"].expected == str(len(piles))
    q = asked["core.embedded-length"]
    pile = piles[_pile_in(q.text, names)]
    assert float(q.expected) == pytest.approx(_other(pile["embedded_length"], unit))
    for qid in ("values.blow-count", "values.blow-count-low"):
        q = asked[qid]
        (row,) = [
            r for r in driving if f"pile {r['pile_id']} ending at {r['tip_depth']} {unit}" in q.text
        ]
        assert q.expected == row["blows"]
    assert int(asked["values.blow-count-low"].expected) <= 1
    q = asked["values.last-blows"]
    mine = [r for r in driving if r["pile_id"] == _pile_in(q.text, names)]
    assert q.expected == mine[-1]["blows"]
    q = asked["values.deepest"]
    mine = [r for r in driving if r["pile_id"] == _pile_in(q.text, names)]
    assert float(q.expected) == pytest.approx(_other(mine[-1]["tip_depth"], unit))
    q = asked["values.width"]
    assert float(q.expected) == pytest.approx(_other(piles[_pile_in(q.text, names)]["width"], size))
    assert q.unit == ("mm" if size == "in" else "in")
    assert list(asked)[-1] == "robustness.defect"
    assert asked["robustness.defect"].expected in _facts.DEFECTS


def test_the_defect_is_drawn_like_every_scenario() -> None:
    dataset = _dataset()
    drawn = {_piles.questions(dataset, f"q{n}")[-1].expected for n in range(40)}
    assert drawn == set(_facts.DEFECTS)
