#!/usr/bin/env python

"""Tests for `pydiggs` package."""

from pydiggs import validator


def test_schema_check_1(instance_path='tests/test_files/2.6/No_Error-2_6.xml'):
    """Test schema check with a valid file."""
    test = validator(instance_path, output_log=False)
    test.schema_check()
    error_log = test.schema_validation_log
    assert error_log is None


def test_schema_check_2(instance_path='tests/test_files/Syntax_Error_1.xml'):
    """Test schema check with a file containing syntax errors."""
    test = validator(instance_path, output_log=False)
    test.schema_check()
    error_log = test.syntax_error_log
    assert error_log is not None
    assert str(error_log) == (
        "Opening and ending tag mismatch: creationDate line 4 and CreationDate, "
        "line 4, column 52 (Syntax_Error_1.xml, line 4)"
    )


def test_schema_check_3(instance_path='tests/test_files/2.6/Schema_Error_1-2_6.xml'):
    """Test schema check with a file containing schema errors."""
    test = validator(instance_path, output_log=True)
    test.schema_check()
    error_log = test.schema_validation_log
    assert error_log is not None
    expected_error = (
        "tests/test_files/2.6/Schema_Error_1-2_6.xml:12:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: "
        'Element \'{http://diggsml.org/schemas/2.6}businessAssociate\': This element is not expected. '
        'Expected is ( {http://diggsml.org/schemas/2.6}BusinessAssociate ).\n'
        "tests/test_files/2.6/Schema_Error_1-2_6.xml:17:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: "
        'Element \'{http://diggsml.org/schemas/2.6}AuditTrail\': This element is not expected. '
        'Expected is one of ( {http://diggsml.org/schemas/2.6}sourceSoftware, '
        '{http://diggsml.org/schemas/2.6}destination, {http://diggsml.org/schemas/2.6}destinationSoftware, '
        '{http://diggsml.org/schemas/2.6}auditTrail ).'
    )
    assert str(error_log) == expected_error


def test_schematron_check_1(
    instance_path='tests/test_files/Schematron_Error_1.xml',
    schematron_path='tests/test_schematron_schema/test_schematron_1.sch'
):
    """Test schematron check with a valid file."""
    test = validator(instance_path, schematron_path=schematron_path)
    test.schematron_check()
    error_log = test.schematron_validation_log
    assert error_log is None


def test_schematron_check_2(
    instance_path='tests/test_files/2.6/Schematron_Error_1-2_6.xml',
    schematron_path='tests/test_schematron_schema/test_schematron_2.sch'
):
    """Test schematron check with a file containing schematron errors."""
    test = validator(instance_path, schematron_path=schematron_path, output_log=False)
    test.schematron_check()
    error_log = test.schematron_validation_log
    assert error_log is not None


def test_dictionary_check_1(instance_path='tests/test_files/AtterbergExample.xml'):
    """Test dictionary check with a file containing undefined properties."""
    test = validator(instance_path, output_log=False)
    test.dictionary_check()
    error_log = test.dictionary_validation_log

    expected_error = [
        'liquidity index          (Did you mean any of these? aggregate_elongation_index, '
        'aggregate_flakiness_index, compression_index, consistency_index, horiz_stress_index, '
        'liquidity_index, material_index, plasticity_index, point_load_test_index, '
        'recompression_index, slake_durability_index)',
        'natural water content    (Did you mean any of these? aggregate_water_absorption, '
        'chloride_content, soil_moisture_content, sulfate_content, water_content_natural, '
        'water_content_optimum, water_depth, water_depth_calc, water_depth_estimated, water_elev, '
        'water_elev_calc, water_elev_estimated)',
        'plastic limit            (Did you mean any of these? limit_pressure, liquid_limit, '
        'liquid_limit_oven_dried, non_plastic, plastic_limit, plasticity_index, shrinkage_limit)',
        'plasticity index         (Did you mean any of these? aggregate_elongation_index, '
        'aggregate_flakiness_index, compression_index, consistency_index, horiz_stress_index, '
        'liquidity_index, material_index, plasticity_index, point_load_test_index, '
        'recompression_index, slake_durability_index)',
        'uUSCS symbol             (Did you mean any of these? uscs_symbol, uscs_symbol_425)'
    ]
    assert error_log == expected_error

