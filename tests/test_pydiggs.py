#!/usr/bin/env python

"""Tests for `pydiggs` package."""

import pytest


from pydiggs import validator


@pytest.fixture
def response():
    """Sample pytest fixture.

    See more at: http://doc.pytest.org/en/latest/fixture.html
    """
    # import requests
    # return requests.get('https://github.com/audreyr/cookiecutter-pypackage')


def test_content(response):
    """Sample pytest test function with the pytest fixture as an argument."""
    # from bs4 import BeautifulSoup
    # assert 'GitHub' in BeautifulSoup(response.content).title.string


def test_schema_check_1(instance_path='tests/test_files/2.6/No_Error-2_6.xml'):

    test = validator(instance_path)
    test.schema_check()
    error_log = test.schema_validation_log
    assert error_log is None


def test_schema_check_2(instance_path='tests/test_files/Syntax_Error_1.xml'):

    test = validator(instance_path)
    test.schema_check()
    error_log = test.syntax_error_log
    assert error_log is not None
    assert str(error_log) == "Opening and ending tag mismatch: creationDate line 4 and CreationDate, line 4, column 52 (Syntax_Error_1.xml, line 4)"


def test_schema_check_3(instance_path='tests/test_files/2.6/Schema_Error_1-2_6.xml'):

    test = validator(instance_path)
    test.schema_check()
    error_log = test.schema_validation_log
    assert error_log is not None
    expected_error = (
        "tests/test_files/2.6/Schema_Error_1-2_6.xml:12:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: Element "
        "'{http://diggsml.org/schemas/2.6}businessAssociate': This element is not expected. Expected is "
        "( {http://diggsml.org/schemas/2.6}BusinessAssociate ).\n"
        "tests/test_files/2.6/Schema_Error_1-2_6.xml:17:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: Element "
        "'{http://diggsml.org/schemas/2.6}AuditTrail': This element is not expected. Expected is one of "
        "( {http://diggsml.org/schemas/2.6}sourceSoftware, {http://diggsml.org/schemas/2.6}destination, "
        "{http://diggsml.org/schemas/2.6}destinationSoftware, {http://diggsml.org/schemas/2.6}auditTrail )."
    )
    assert str(error_log) == expected_error



def test_schematron_check_1(instance_path='tests/test_files/Schematron_Error_1.xml',
                            schematron_path='tests/test_schematron_schema/test_schematron_1.sch'):

    test = validator(instance_path, schematron_path=schematron_path)
    test.schematron_check()
    error_log = test.schematron_validation_log
    assert error_log is None


def test_schematron_check_2(instance_path='tests/test_files/2.6/Schematron_Error_1-2_6.xml',
                            schematron_path='tests/test_schematron_schema/test_schematron_2.sch'):

    test = validator(instance_path, schematron_path=schematron_path)
    test.schematron_check()
    error_log = test.schematron_validation_log
    assert error_log is not None
    expected_error = (
        "tests/test_files/2.6/Schematron_Error_1-2_6.xml:0:0:ERROR:SCHEMATRONV:SCHEMATRONV_ASSERT: <svrl:failed-assert xmlns:svrl=\"http://purl.oclc.org/dsdl/svrl\" xmlns:xs=\"http://www.w3.org/2001/XMLSchema\" xmlns:schold=\"http://www.ascc.net/xml/schematron\" xmlns:sch=\"http://www.ascc.net/xml/schematron\" xmlns:iso=\"http://purl.oclc.org/dsdl/schematron\" xmlns:diggs=\"http://diggsml.org/schemas/2.6\" xmlns:gml=\"http://www.opengis.net/gml/3.2\" test=\"$length &gt;=0\" location=\"/*[local-name()='Diggs' and namespace-uri()='http://diggsml.org/schemas/2.6']/*[local-name()='samplingActivity' and namespace-uri()='http://diggsml.org/schemas/2.6'][1]/*[local-name()='SamplingActivity' and namespace-uri()='http://diggsml.org/schemas/2.6']/*[local-name()='totalSampleRecoveryLength' and namespace-uri()='http://diggsml.org/schemas/2.6']\"><svrl:text>'totalSampleRecoveryLength' must be greater than or equal to 0.</svrl:text></svrl:failed-assert>\n"
        "tests/test_files/2.6/Schematron_Error_1-2_6.xml:0:0:ERROR:SCHEMATRONV:SCHEMATRONV_ASSERT: <svrl:failed-assert xmlns:svrl=\"http://purl.oclc.org/dsdl/svrl\" xmlns:xs=\"http://www.w3.org/2001/XMLSchema\" xmlns:schold=\"http://www.ascc.net/xml/schematron\" xmlns:sch=\"http://www.ascc.net/xml/schematron\" xmlns:iso=\"http://purl.oclc.org/dsdl/schematron\" xmlns:diggs=\"http://diggsml.org/schemas/2.6\" xmlns:gml=\"http://www.opengis.net/gml/3.2\" test=\"$length &gt;=0\" location=\"/*[local-name()='Diggs' and namespace-uri()='http://diggsml.org/schemas/2.6']/*[local-name()='samplingActivity' and namespace-uri()='http://diggsml.org/schemas/2.6'][2]/*[local-name()='SamplingActivity' and namespace-uri()='http://diggsml.org/schemas/2.6']/*[local-name()='totalSampleRecoveryLength' and namespace-uri()='http://diggsml.org/schemas/2.6']\"><svrl:text>'totalSampleRecoveryLength' must be greater than or equal to 0.</svrl:text></svrl:failed-assert>"
    )
    assert str(error_log) == expected_error
