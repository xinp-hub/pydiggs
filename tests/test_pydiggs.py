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


def test_schema_check_1(instance_path='tests/test_files/No_Error.xml'):

    test = validator(instance_path)
    test.schema_check()
    error_log = test.schema_validation_log
    assert error_log is None

def test_schema_check_2(instance_path='tests/test_files/Syntax_Error_1.xml'):

    test = validator(instance_path)
    test.schema_check()
    error_log = test.syntax_error_log
    assert error_log is not None

def test_schema_check_3(instance_path='tests/test_files/Schema_Error_1.xml'):

    test = validator(instance_path)
    test.schema_check()
    error_log = test.schema_validation_log
    assert error_log is not None

def test_schematron_check_1(instance_path='tests/test_files/Schematron_Error_1.xml', schematron_path = 'tests/test_schematron_schema/test_schematron_1.sch'):

    test = validator(instance_path, schematron_path = schematron_path)
    test.schematron_check()
    error_log = test.schematron_validation_log
    assert error_log is None

def test_schematron_check_2(instance_path='tests/test_files/Schematron_Error_1.xml', schematron_path = 'tests/test_schematron_schema/test_schematron_2.sch'):

    test = validator(instance_path, schematron_path = schematron_path)
    test.schematron_check()
    error_log = test.schematron_validation_log
    assert error_log is not None

