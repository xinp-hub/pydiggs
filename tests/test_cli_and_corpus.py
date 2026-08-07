"""CLI and official schema corpus tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree
from pydiggs import validator
from pydiggs.cli import main

FIXTURES = Path("tests/fixtures/official")


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
    schema = etree.XMLSchema(etree.parse("pydiggs/schemas/diggs-schema-2.5.a/Complete.xsd"))
    assert schema.validate(etree.parse(str(path))), str(schema.error_log)


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "2.6").glob("*.xml")),
    ids=lambda p: p.name,
)
def test_official_26_schema_corpus(path: Path):
    v = validator(str(path), output_log=False)
    assert v.schema_check() is True


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "known_invalid").glob("*.xml")),
    ids=lambda p: p.name,
)
def test_official_known_invalid_do_not_silently_pass(path: Path):
    v = validator(str(path), output_log=False)
    # These curated files are known not to validate as Diggs 2.6 document roots.
    assert v.schema_check() is False
