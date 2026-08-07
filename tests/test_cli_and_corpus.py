"""CLI and official schema corpus tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

import pydiggs
from pydiggs import detect_diggs_version, validator
from pydiggs.cli import main

FIXTURES = Path("tests/fixtures/official")
_PKG = Path(pydiggs.__file__).resolve().parent


def test_cli_schema_check_success():
    code = main(
        [
            "schema_check",
            "tests/test_files/2.6/No_Error_2_6.xml",
            "--no-output_log",
        ]
    )
    assert code == 0


def test_cli_schema_check_failure():
    code = main(
        [
            "schema_check",
            "tests/test_files/2.6/Schema_Error_1-2_6.xml",
            "--no-output_log",
        ]
    )
    assert code == 1


def test_cli_dictionary_and_schematron():
    assert (
        main(
            [
                "dictionary_check",
                "tests/test_files/2.6/No_Error_2_6.xml",
                "--no-output_log",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "schematron_check",
                "tests/test_files/2.6/No_Error_2_6.xml",
                "--no-output_log",
            ]
        )
        == 0
    )


def test_cli_context_check():
    assert (
        main(
            [
                "context_check",
                "tests/fixtures/official/2.6/DocumentationExample.xml",
                "--no-output_log",
            ]
        )
        == 0
    )


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "2.5.a").glob("*.xml")),
    ids=lambda p: p.name,
)
def test_official_25a_schema_corpus(path: Path):
    xsd = _PKG / "schemas" / "diggs-schema-2.5.a" / "Complete.xsd"
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    assert schema.validate(etree.parse(str(path))), str(schema.error_log)


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "2.6").glob("*.xml")),
    ids=lambda p: p.name,
)
def test_official_26_schema_corpus(path: Path):
    v = validator(str(path), output_log=False)
    assert v.schema_check() is True
    assert v.diggs_version == "2.6"


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "3.0").glob("*.xml")),
    ids=lambda p: p.name,
)
def test_official_30_schema_corpus(path: Path):
    v = validator(str(path), output_log=False)
    assert v.schema_check() is True
    assert v.diggs_version == "3.0.0"


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "known_invalid").glob("*.xml")),
    ids=lambda p: p.name,
)
def test_official_known_invalid_do_not_silently_pass(path: Path):
    # Pin profile so non-Diggs documents are not validated against Diggs 3.0 by
    # the unknown-namespace default (HG2 default profile).
    profile = "3.0.0" if "Aeromag_3" in path.name else "2.6"
    v = validator(str(path), diggs_version=profile, output_log=False)
    assert v.schema_check() is False


def test_detect_diggs_version_profiles():
    assert detect_diggs_version(FIXTURES / "2.6" / "Aeromag.xml") == "2.6"
    assert detect_diggs_version(FIXTURES / "3.0" / "PileDrivingExample.xml") == "3.0.0"


def test_schematron_runs_on_diggs_30():
    v = validator(str(FIXTURES / "3.0" / "PileDrivingExample.xml"), output_log=False)
    assert v.schematron_check() is True
