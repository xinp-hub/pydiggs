"""The AGS4 to DIGGS mapping table: its format, its schema, its targets and its page.

The table is data the owner reviews, so these tests check what review cannot: that it
matches its JSON Schema, that every DIGGS name it uses exists (the property dictionary,
the writer layout of DIGGS 3.0.0, the unit dictionary, the code lists), that it covers
the Tier A groups, and that the generated documentation page is current.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from lxml import etree

from pydiggs import _xml
from pydiggs._writer._layout import load_layout
from pydiggs.convert import _mapping
from pydiggs.convert._mapping import load_mapping, property_facts
from pydiggs.errors import ResourceError
from pydiggs.preview import convert
from pydiggs.units import UnitConverter

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mapping_doc.py"
CODES = ROOT / "src" / "pydiggs" / "dictionaries" / "codes"
TIER_A = {
    "PROJ",
    "TRAN",
    "ABBR",
    "UNIT",
    "TYPE",
    "DICT",
    "LOCA",
    "GEOL",
    "SAMP",
    "ISPT",
    "LLPL",
    "GRAG",
    "GRAT",
    "LNMC",
}
LAYOUT = load_layout("3.0.0")


def _schema() -> dict[str, Any]:
    text = resources.files("pydiggs.convert").joinpath("convert-mapping-1.schema.json")
    return dict(json.loads(text.read_text("utf-8")))


def _validator(definition: str | None = None) -> jsonschema.Draft202012Validator:
    schema = _schema()
    if definition is not None:
        schema = {**schema, "$ref": f"#/$defs/{definition}"}
        for key in ("required", "properties", "additionalProperties", "type"):
            schema.pop(key)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=None)


# --- format -----------------------------------------------------------------------------


def test_the_tables_validate_against_their_schema():
    _validator().validate(_mapping.read_table("ags4-diggs.toml"))
    _validator("units_table").validate(_mapping.read_table("units.toml"))
    _validator("codes_table").validate(_mapping.read_table("codes.toml"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda g: g.update(pattern="per-row"), "is not one of"),
        (lambda g: g.update(status="final"), "is not one of"),
        (lambda g: g["heading"][0].update(to="none"), "reason"),
        (lambda g: g["heading"][0].update(description="text from the dictionary"), "Additional"),
        (lambda g: g.update(name="loca"), "does not match"),
        (lambda g: g["heading"][0].update(quantity="Length"), "should not be valid"),
    ],
)
def test_the_schema_refuses_a_bad_table(change: Any, message: str):
    table = _mapping.read_table("ags4-diggs.toml")
    change(next(g for g in table["group"] if g["name"] == "LOCA"))
    errors = list(_validator().iter_errors(table))
    assert errors
    assert any(message in error.message or message in str(error) for error in errors)


def test_the_table_is_versioned_and_preview_names_its_version():
    mapping = load_mapping()
    assert mapping.mapping_version == convert.MAPPING_VERSION == "0.1"
    assert mapping.diggs_versions == ("3.0.0",)
    assert mapping.ags_versions == ("4.0.3", "4.0.4", "4.1", "4.1.1", "4.2")
    for name in ("units.toml", "codes.toml"):
        assert _mapping.read_table(name)["mapping_version"] == mapping.mapping_version


def test_tier_a_is_covered_and_every_group_is_a_draft():
    mapping = load_mapping()
    assert set(mapping.groups) == TIER_A
    assert {group.status for group in mapping.groups.values()} == {"draft"}
    assert mapping.groups["GRAG"].data == "GRAT"
    assert mapping.groups["GRAT"].parent == "GRAG"


def test_every_listed_heading_has_a_target_or_a_reason():
    for group in load_mapping().groups.values():
        for heading in group.headings.values():
            if heading.to in ("none", "format"):
                assert heading.reason, (group.name, heading.name)
            elif heading.to == "column":
                assert heading.property, (group.name, heading.name)
            else:
                assert heading.target, (group.name, heading.name)


# --- every DIGGS name exists -------------------------------------------------------------


def test_every_column_property_is_in_the_bundled_property_dictionary():
    facts = property_facts()
    for group in load_mapping().groups.values():
        for heading in group.headings.values():
            if heading.to == "column":
                assert heading.property in facts, (group.name, heading.name)
    assert "non_plastic" in facts  # written for "NP" limits


def test_every_element_and_param_exists_in_diggs_3_0_0():
    for group in load_mapping().groups.values():
        for element in (group.element, group.procedure):
            if element is not None:
                LAYOUT.element(element)
        if group.procedure is not None:
            lay = LAYOUT.element(group.procedure)
            for name in group.procedure_params:
                assert name in lay.by_local, (group.name, name)
        for heading in group.headings.values():
            if heading.to == "param":
                element, _, child = str(heading.target).partition("/")
                assert child in LAYOUT.element(element).by_local, (group.name, heading.name)


SCHEMA_3 = ROOT / "src" / "pydiggs" / "schemas" / "diggs-schema-3.0.0"
XS = "{http://www.w3.org/2001/XMLSchema}"


def _complex_types() -> dict[str, list[Any]]:
    """Every named complex type of the bundled DIGGS 3.0.0 schema (core, extended, ...)."""
    found: dict[str, list[Any]] = {}
    for path in sorted(SCHEMA_3.rglob("*.xsd")):
        if "energistics" in path.parts or "deprecated" in path.parts:
            continue
        for node in _xml.parse(path).getroot().iter(f"{XS}complexType"):
            if node.get("name"):
                found.setdefault(str(node.get("name")), []).append(node)
    return found


def _child_types(types: dict[str, list[Any]], name: str, child: str) -> set[str]:
    """The types child element ``child`` has in complex type ``name`` and its bases."""
    out: set[str] = set()
    for node in types.get(name, []):
        for element in node.iter(f"{XS}element"):
            if element.get("name") == child:
                out.add(str(element.get("type")))
        for extension in node.iter(f"{XS}extension"):
            base = str(extension.get("base")).partition(":")[2]
            if base != name:
                out |= _child_types(types, base, child)
    return out


def test_every_measure_param_names_the_quantity_of_its_diggs_element():
    types = _complex_types()
    seen = set()
    for group in load_mapping().groups.values():
        for heading in group.headings.values():
            if heading.to != "param":
                continue
            element, _, child = str(heading.target).partition("/")
            found = _child_types(types, f"{element}Type", child)
            # A measure is always resolved; some text children come from a group or a
            # base this small walk does not follow, and have no quantity.
            assert found or heading.quantity is None, (group.name, heading.name)
            measures = {t for t in found if t.endswith("MeasureType")}
            if heading.quantity is None:
                assert not measures, (group.name, heading.name, measures)
            else:
                assert found == {f"diggs:{heading.quantity}MeasureType"}, (heading.name, found)
                facts = _mapping.measure_facts(heading.quantity)
                assert facts.uoms, heading.quantity
                seen.add(heading.quantity)
    assert seen >= {"Length", "Dimensionless", "ParticleSize", "MassPerMass"}


def test_measure_units_come_from_the_bundled_schema():
    dimensionless = _mapping.measure_facts("Dimensionless")
    assert "%" in dimensionless.uoms and "m" not in dimensionless.uoms
    assert not dimensionless.required
    size = _mapping.measure_facts("ParticleSize")
    assert size.required and {"mm", "um", "phi"} <= size.uoms
    with pytest.raises(ResourceError, match="NoSuchMeasureType"):
        _mapping.measure_facts("NoSuch")


def test_every_crs_axis_unit_is_a_diggs_unit_of_the_unit_table():
    mapping = load_mapping()
    assert mapping.crs_units
    for srs, unit in mapping.crs_units.items():
        assert srs in mapping.crs.values()
        assert unit in mapping.units.values(), (srs, unit)


def test_every_diggs_unit_is_in_the_bundled_unit_dictionary():
    converter = UnitConverter()
    for ags, diggs in load_mapping().units.items():
        assert converter.base_unit(diggs) is not None, (ags, diggs)


def _code_list(name: str) -> set[str]:
    root = _xml.parse(CODES / f"{name}.xml").getroot()
    return {
        str(el.get("{http://www.opengis.net/gml/3.2}id"))
        for el in root.iter(etree.Element)
        if etree.QName(el).localname == "Definition"
    }


def test_every_code_is_an_entry_of_its_bundled_code_list():
    mapping = load_mapping()
    for rule in mapping.codes.values():
        if rule.element is not None:
            LAYOUT.element(rule.element)
        if rule.code is not None:
            assert rule.code in _code_list(str(rule.code_list)), (rule.heading, rule.ags)
            assert mapping.code_space(str(rule.code_list)).endswith(f"/{rule.code_list}.xml")


# --- loading --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "break_it",
    [
        lambda t: t["ags4-diggs.toml"]["group"].append(dict(t["ags4-diggs.toml"]["group"][0])),
        lambda t: t["ags4-diggs.toml"]["group"][0]["heading"].append(
            dict(t["ags4-diggs.toml"]["group"][0]["heading"][0])
        ),
        lambda t: t["ags4-diggs.toml"]["group"][0].update(pattern="unknown"),
        lambda t: t["units.toml"].update(mapping_version="9.9"),
        lambda t: t["codes.toml"]["code"].append({"heading": "LOCA_TYPE", "ags": "X", "code": "Y"}),
        lambda t: t["ags4-diggs.toml"]["group"][0]["heading"][0].update(to="param", target="x"),
        lambda t: t["ags4-diggs.toml"]["group"][0]["heading"][0].update(quantity="Length"),
        lambda t: t["codes.toml"]["crs"].append(
            {
                "heading": "LOCA_GREF",
                "ags": "X",
                "srs": t["codes.toml"]["crs"][0]["srs"],
                "unit": "ft",
            }
        ),
    ],
)
def test_a_broken_bundled_table_is_a_resource_error(monkeypatch: pytest.MonkeyPatch, break_it: Any):
    tables = {name: _mapping.read_table(name) for name in _mapping._TABLES}
    break_it(tables)
    monkeypatch.setattr(_mapping, "read_table", lambda name: tables[name])
    _mapping.load_mapping.cache_clear()
    try:
        with pytest.raises(ResourceError, match="mapping table"):
            _mapping.load_mapping()
    finally:
        _mapping.load_mapping.cache_clear()


def test_mapping_info_describes_the_table():
    info = convert.mapping_info()
    assert json.loads(json.dumps(info)) == info
    assert [g["name"] for g in info["groups"]][:3] == ["PROJ", "TRAN", "ABBR"]
    ispt = next(g for g in info["groups"] if g["name"] == "ISPT")
    nval = next(h for h in ispt["headings"] if h["name"] == "ISPT_NVAL")
    assert nval == {
        "name": "ISPT_NVAL",
        "to": "column",
        "target": None,
        "property": "n_value",
        "reason": None,
        "quantity": None,
    }
    assert info["rules"]["convert.orphan-row"] == "error"


def test_the_table_ships_and_is_recorded_in_provenance():
    root = resources.files("pydiggs.convert")
    for name in ("ags4-diggs.toml", "units.toml", "codes.toml"):
        assert root.joinpath("mappings", name).is_file()
    assert root.joinpath("convert-mapping-1.schema.json").is_file()
    provenance = tomllib.loads(
        (ROOT / "src" / "pydiggs" / "PROVENANCE.toml").read_text(encoding="utf-8")
    )
    for name in (
        "convert/mappings/ags4-diggs.toml",
        "convert/mappings/units.toml",
        "convert/mappings/codes.toml",
        "convert/convert-mapping-1.schema.json",
    ):
        assert provenance["files"][name]["source"] == "local"


# --- the generated page ----------------------------------------------------------------


def _script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("mapping_doc", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_mapping_page_is_current():
    result = subprocess.run(  # noqa: S603 - our own interpreter and script
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mapping page OK" in result.stdout


def test_the_mapping_page_check_fails_when_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    script = _script()
    page = tmp_path / "convert-mapping.md"
    page.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(script, "PAGE", page)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    assert script.main(["--check"]) == 1
    assert script.main([]) == 0
    assert script.main(["--check"]) == 0
    assert script.main([]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_the_page_lists_every_heading_and_status():
    text = (ROOT / "docs" / "convert-mapping.md").read_text("utf-8")
    for group in load_mapping().groups.values():
        assert f"## {group.name}" in text
        for heading in group.headings.values():
            assert f"| `{heading.name}` |" in text
    assert "| `deg` | `dega` |" in text
