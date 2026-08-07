#!/usr/bin/env python

"""Tests for `pydiggs` package."""

from pathlib import Path

from lxml import etree, isoschematron
from pydiggs import validator
from pydiggs.dictionary import DictionarySemanticValidator


def test_schema_check_1(instance_path="tests/test_files/2.6/No_Error_2_6.xml"):
    """Test schema check with a valid file."""
    test = validator(instance_path, output_log=False)
    assert test.schema_check() is True
    assert test.schema_validation_log is None


def test_schema_check_2(instance_path="tests/test_files/Syntax_Error_1.xml"):
    """Test schema check with a file containing syntax errors."""
    test = validator(instance_path, output_log=False)
    assert test.schema_check() is False
    error_log = test.syntax_error_log
    assert error_log is not None
    assert str(error_log) == (
        "Opening and ending tag mismatch: creationDate line 4 and CreationDate, "
        "line 4, column 52 (Syntax_Error_1.xml, line 4)"
    )


def test_schema_check_3(instance_path="tests/test_files/2.6/Schema_Error_1-2_6.xml"):
    """Test schema check with a file containing schema errors."""
    test = validator(instance_path, output_log=False)
    assert test.schema_check() is False
    error_log = test.schema_validation_log
    assert error_log is not None
    expected_error = (
        "tests/test_files/2.6/Schema_Error_1-2_6.xml:12:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: "
        "Element '{http://diggsml.org/schemas/2.6}businessAssociate': This element is not expected. "
        "Expected is ( {http://diggsml.org/schemas/2.6}BusinessAssociate ).\n"
        "tests/test_files/2.6/Schema_Error_1-2_6.xml:17:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: "
        "Element '{http://diggsml.org/schemas/2.6}AuditTrail': This element is not expected. "
        "Expected is one of ( {http://diggsml.org/schemas/2.6}sourceSoftware, "
        "{http://diggsml.org/schemas/2.6}destination, {http://diggsml.org/schemas/2.6}destinationSoftware, "
        "{http://diggsml.org/schemas/2.6}auditTrail )."
    )
    assert str(error_log) == expected_error


def test_schematron_check_1(
    instance_path="tests/test_files/Schematron_Error_1.xml",
    schematron_path="tests/test_schematron_schema/test_schematron_1.sch",
):
    """Test schematron check with a valid file."""
    test = validator(instance_path, schematron_path=schematron_path, output_log=False)
    assert test.schematron_check() is True
    assert test.schematron_validation_log is None


def test_schematron_check_2(
    instance_path="tests/test_files/2.6/Schematron_Error_1-2_6.xml",
    schematron_path="tests/test_schematron_schema/test_schematron_2.sch",
):
    """Test schematron check with a file containing schematron errors."""
    test = validator(instance_path, schematron_path=schematron_path, output_log=False)
    assert test.schematron_check() is False
    assert test.schematron_validation_log is not None


def test_dictionary_check_1(instance_path="tests/test_files/AtterbergExample.xml"):
    """Legacy Atterberg instance yields dictionary findings (not silent pass)."""
    test = validator(instance_path, output_log=False)
    test.dictionary_check()
    assert test.dictionary_validation_log is not None
    assert len(test.dictionary_validation_log) >= 1


def test_bundled_schematron_compiles():
    """Bundled DIGGS Schematron adapts compile under lxml."""
    sch = Path("pydiggs/schematron/diggs_schematron_rules_2.6.sch")
    assert sch.is_file()
    schematron = isoschematron.Schematron(etree.parse(str(sch)))
    assert schematron is not None


def test_dictionary_semantic_step_goldens(tmp_path):
    """Minimal goldens for codeSpace Definition resolve (steps 1/5)."""
    props = Path("pydiggs/dictionaries/properties.xml").resolve()
    good = tmp_path / "good.xml"
    good.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Diggs xmlns="http://diggsml.org/schemas/2.6"
       xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="t">
  <Property gml:id="p1">
    <propertyName>Liquid Limit</propertyName>
    <propertyClass codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#liquid_limit">Liquid Limit</propertyClass>
    <typeData>double</typeData>
    <uom>%</uom>
  </Property>
</Diggs>
""",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.xml"
    bad.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Diggs xmlns="http://diggsml.org/schemas/2.6"
       xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="t">
  <Property gml:id="p1">
    <propertyClass codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#not_a_real_code">x</propertyClass>
  </Property>
</Diggs>
""",
        encoding="utf-8",
    )

    v = DictionarySemanticValidator(dictionary_path=props)
    good_result = v.validate_instance(good)
    bad_result = v.validate_instance(bad)
    assert not any(m.severity == "ERROR" and m.check == 5 for m in good_result.messages)
    assert any(m.severity == "ERROR" and m.check == 5 for m in bad_result.messages)


def test_unit_conversion_mm_to_in():
    from pydiggs.units import UnitConverter, casing_outside_gt_inside

    conv = UnitConverter()
    # 100 mm outside, 3 in inside (~76.2 mm) → ok
    ok, _ = casing_outside_gt_inside(100.0, "mm", 3.0, "in", conv)
    assert ok is True
    # 50 mm outside, 3 in inside → fail
    ok, msg = casing_outside_gt_inside(50.0, "mm", 3.0, "in", conv)
    assert ok is False
    assert "must be greater" in msg


def test_legacy_codespace_without_fragment(tmp_path):
    from pydiggs.dictionary import DictionarySemanticValidator

    props = Path("pydiggs/dictionaries/properties.xml").resolve()
    inst = tmp_path / "legacy.xml"
    # liquid_limit exists as gml:id in properties.xml
    inst.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Diggs xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="t">
  <Property gml:id="p1">
    <propertyClass codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml">liquid_limit</propertyClass>
    <typeData>double</typeData>
    <uom>%</uom>
  </Property>
</Diggs>
""",
        encoding="utf-8",
    )
    result = DictionarySemanticValidator(dictionary_path=props).validate_instance(inst)
    assert not any(m.severity == "ERROR" and m.check == 5 for m in result.messages)


def test_codes_bundle_registered():
    from pydiggs.dictionary import DictionarySemanticValidator

    v = DictionarySemanticValidator()
    assert "https://diggsml.org/def/codes/DIGGS/0.1/roles.xml" in v._dict_cache
    assert "https://diggsml.org/def/codes/DIGGS/0.1/astmD2487.xml" in v._dict_cache
