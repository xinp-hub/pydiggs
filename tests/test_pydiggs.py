#!/usr/bin/env python

"""Tests for `pydiggs` package."""

import pytest


from pydiggs import pydiggs


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


def test_validate_against_schematron(xml_file='tests/test_files/No_Error.xml', sch_file='tests/test_files/test_schematron.sch'):

    error_log = pydiggs.validate_against_schematron(xml_file, sch_file)

    assert "svrl:successful-report" in str(error_log)
    assert "Project name is 'Demo Project'" in str(error_log)
