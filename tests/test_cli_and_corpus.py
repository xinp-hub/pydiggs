"""CLI and official DIGGSml/diggs-examples corpus tests.

Fixtures under ``tests/fixtures/official/`` are synced from
https://github.com/DIGGSml/diggs-examples (see ``PROVENANCE.txt``).

Layout:
  * ``2.5.a/``, ``2.6/``, ``3.0/`` — schema-valid golden instances
  * ``known_invalid/`` — official examples that fail schema (or XML syntax) today
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

import pydiggs
from pydiggs import detect_diggs_version, validator
from pydiggs.cli import main
from pydiggs.detect import namespace_of_root, schema_path_for

FIXTURES = Path("tests/fixtures/official")
_PKG = Path(pydiggs.__file__).resolve().parent


def _xml_paths(subdir: str) -> list[Path]:
    return sorted((FIXTURES / subdir).glob("*.xml"))


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


def test_cli_schema_check_25a_auto_detect():
    """Auto-detected 2.5.a must resolve Complete.xsd (not Diggs.xsd)."""
    path = FIXTURES / "2.5.a" / "AtterbergExample.xml"
    assert main(["schema_check", str(path), "--no-output_log"]) == 0


def test_cli_schema_check_30():
    path = FIXTURES / "3.0" / "PileDrivingExample.xml"
    assert main(["schema_check", str(path), "--no-output_log"]) == 0


@pytest.mark.parametrize("path", _xml_paths("2.5.a"), ids=lambda p: p.name)
def test_official_25a_full_feature_corpus(path: Path):
    assert detect_diggs_version(path) == "2.5.a"
    v = validator(str(path), output_log=False)
    assert v.schema_check() is True
    assert v.diggs_version == "2.5.a"
    assert v.dictionary_check() is True
    assert isinstance(v.schematron_check(), bool)
    assert v.context_check() is True


@pytest.mark.parametrize("path", _xml_paths("2.6"), ids=lambda p: p.name)
def test_official_26_full_feature_corpus(path: Path):
    assert detect_diggs_version(path) == "2.6"
    v = validator(str(path), output_log=False)
    assert v.schema_check() is True
    assert v.diggs_version == "2.6"
    assert v.dictionary_check() is True
    assert v.schematron_check() is True
    assert v.context_check() is True


@pytest.mark.parametrize("path", _xml_paths("3.0"), ids=lambda p: p.name)
def test_official_30_full_feature_corpus(path: Path):
    assert detect_diggs_version(path) == "3.0.0"
    v = validator(str(path), output_log=False)
    assert v.schema_check() is True
    assert v.diggs_version == "3.0.0"
    assert v.dictionary_check() is True
    assert v.schematron_check() is True
    assert v.context_check() is True


# Non-Diggs roots (e.g. gml:CompoundCRS) can "pass" Diggs.xsd only because the
# schema imports GML globals; context_check must still reject them.
_NON_DIGGS_ROOTS = frozenset({"2.6_Temporal_Spatial_CRS_Example.xml"})


@pytest.mark.parametrize("path", _xml_paths("known_invalid"), ids=lambda p: p.name)
def test_official_known_invalid_do_not_silently_pass(path: Path):
    """Official examples that are not acceptable Diggs documents must not pass.

    Prefer ``schema_check() is False``. When XMLSchema accepts a non-Diggs global
    element via GML imports, require ``context_check() is False`` instead.
    """
    v = validator(str(path), output_log=False)
    if path.name in _NON_DIGGS_ROOTS:
        assert v.schema_check() is True  # Diggs.xsd imports GML globals
        assert v.context_check() is False
        return
    assert v.schema_check() is False


def test_detect_diggs_version_profiles():
    assert detect_diggs_version(FIXTURES / "2.5.a" / "AtterbergExample.xml") == "2.5.a"
    assert detect_diggs_version(FIXTURES / "2.6" / "Aeromag.xml") == "2.6"
    assert detect_diggs_version(FIXTURES / "3.0" / "PileDrivingExample.xml") == "3.0.0"
    # HG2: unknown namespace → 3.0.0
    assert detect_diggs_version(etree.fromstring(b"<root xmlns='http://example.com'/>")) == "3.0.0"


def test_detect_unamespaced_root_uses_default():
    root = etree.fromstring(b"<Diggs/>")
    assert namespace_of_root(root) is None
    assert detect_diggs_version(root) == "3.0.0"


def test_schema_path_for_unknown_raises():
    with pytest.raises(ValueError, match="Unknown DIGGS profile"):
        schema_path_for("9.9.9")


def test_schema_path_for_25a_is_complete_xsd():
    path = schema_path_for("2.5.a")
    assert path.name == "Complete.xsd"
    assert path.is_file()


def test_schematron_and_dictionary_on_diggs_30_cli():
    path = str(FIXTURES / "3.0" / "MWDExample.xml")
    assert main(["dictionary_check", path, "--no-output_log"]) == 0
    assert main(["schematron_check", path, "--no-output_log"]) == 0
    assert main(["context_check", path, "--no-output_log"]) == 0
