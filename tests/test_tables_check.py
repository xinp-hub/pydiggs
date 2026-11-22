"""``pydiggs.tables.check``: profile tables against the DIGGS dictionaries, offline."""

from __future__ import annotations

import doctest
from typing import Any

import pytest

from pydiggs import TablesError, tables
from pydiggs.findings.model import Finding, Severity, Step
from pydiggs.findings.rules import explain_rule
from pydiggs.tables import _check, _report

CODES = "https://diggsml.org/def/codes/DIGGS/0.1"
CRS = "http://www.opengis.net/def/crs/EPSG/0/27700"


def site() -> dict[str, list[dict[str, Any]]]:
    """A small, clean site: a borehole, a sample, a layer, an SPT and a CPT."""
    return {
        "document": [{"creation_date": "2026-11-21"}],
        "projects": [{"key": "P1", "name": "Riverside"}],
        "features": [
            {
                "key": "BH-1",
                "project": "P1",
                "x": 451200,
                "y": 206500,
                "crs": CRS,
                "total_depth": 12.5,
                "total_depth_uom": "m",
                "depth_uom": "m",
            }
        ],
        "sampling_activities": [
            {"key": "A1", "feature": "BH-1", "top": 2, "base": 2.45, "recovery": 0.4}
        ],
        "samples": [
            {
                "key": "S1",
                "sampling_activity": "A1",
                "type": "open_tube",
                "type_code_space": f"{CODES}/sampleType.xml",
            }
        ],
        "observations": [{"key": "LOG", "feature": "BH-1"}],
        "layers": [
            {
                "key": "L1",
                "observation": "LOG",
                "top": 0,
                "base": 1.2,
                "description": "Grey CLAY",
                "classification": "CL",
                "classification_code_space": f"{CODES}/grp-astmD2487.xml",
            }
        ],
        "measurements": [
            {"key": "SPT-1", "feature": "BH-1", "procedure": "DrivenPenetrationTest", "depth": 7},
            {"key": "CPT-1", "feature": "BH-1", "procedure": "StaticConePenetrationTest"},
        ],
        "params": [
            {"node": "LOG", "name": "lithologyClassificationType", "value": "USCS"},
            {
                "node": "SPT-1",
                "scope": "procedure",
                "name": "penetrationTestType",
                "value": "SPT",
            },
        ],
        "result_columns": [
            {"procedure": "StaticConePenetrationTest", "column": "tip_resistance", "uom": "MPa"}
        ],
        "results_DrivenPenetrationTest": [{"measurement": "SPT-1", "n_value": 21}],
        "results_StaticConePenetrationTest": [
            {"measurement": "CPT-1", "row": 0, "position": 0.02, "tip_resistance": 2.5},
            {"measurement": "CPT-1", "row": 1, "position": 0.04, "tip_resistance": 2.7},
        ],
    }


def found(findings: tuple[Finding, ...]) -> list[tuple[str, str | None]]:
    return [(finding.rule_id, finding.path) for finding in findings]


def test_a_clean_site_has_no_finding_and_passes_the_round_trip() -> None:
    assert tables.check(site()) == ()
    assert tables.round_trip(site()).ok


def test_check_takes_rows_from_iterators_and_does_not_change_them() -> None:
    data = site()
    assert tables.check({name: iter(rows) for name, rows in data.items()}) == ()
    assert data == site()


def test_a_result_column_that_is_not_a_property_is_named() -> None:
    data = site()
    data["results_DrivenPenetrationTest"][0]["blows"] = 21
    (finding,) = tables.check(data)
    assert (finding.rule_id, finding.path) == (
        "tables.unknown-property",
        "results_DrivenPenetrationTest/1/blows",
    )
    assert finding.step is Step.TABLES
    assert finding.severity is Severity.WARNING
    assert "'blows' is not a property" in finding.message


def test_result_columns_can_name_the_property_of_a_column() -> None:
    data = site()
    data["results_DrivenPenetrationTest"][0] = {"measurement": "SPT-1", "blows": 21}
    data["result_columns"].append(
        {"procedure": "DrivenPenetrationTest", "column": "blows", "property": "n_value"}
    )
    assert tables.check(data) == ()
    data["result_columns"][-1]["property"] = "blows_n"
    assert found(tables.check(data)) == [("tables.unknown-property", "result_columns/2/property")]


def test_a_result_column_described_for_one_test_only_is_checked_there() -> None:
    data = site()
    data["result_columns"] = [{"measurement": "CPT-1", "column": "tip_resistance", "uom": "MPa"}]
    assert tables.check(data) == ()


def test_a_quantity_with_no_unit_and_a_count_with_one_are_named() -> None:
    data = site()
    data["result_columns"] = [
        {"procedure": "DrivenPenetrationTest", "column": "n_value", "uom": "m"}
    ]
    findings = tables.check(data)
    assert found(findings) == [
        ("tables.unknown-unit", "result_columns/1/property"),
        ("tables.unknown-unit", "results_StaticConePenetrationTest/1/tip_resistance"),
    ]
    assert "has no unit in the DIGGS property dictionary" in findings[0].message
    assert "is a quantity (pressure) and needs a unit" in findings[1].message


def test_a_unit_of_another_quantity_on_a_result_column_is_named() -> None:
    data = site()
    data["result_columns"][0]["uom"] = "m"
    (finding,) = tables.check(data)
    assert (finding.rule_id, finding.path) == ("tables.unknown-unit", "result_columns/1/uom")
    assert "'m' is not a unit of pressure" in finding.message
    # The check agrees with validation of the file written.
    invalid = [f for f in tables.round_trip(data).validation.findings if f.severity.rank > 1]
    assert [f.rule_id for f in invalid] == ["dictionary.12"]


@pytest.mark.parametrize(
    ("table", "column", "path"),
    [
        ("features", "total_depth_uom", "features/1/total_depth_uom"),
        ("sampling_activities", "recovery_uom", "sampling_activities/1/recovery_uom"),
    ],
)
def test_a_length_in_a_unit_that_is_not_a_length_is_named(
    table: str, column: str, path: str
) -> None:
    data = site()
    data[table][0][column] = "kg"
    assert ("tables.unknown-unit", path) in found(tables.check(data))
    data[table][0][column] = "ft"
    assert "tables.unknown-unit" not in {f.rule_id for f in tables.check(data)}


def test_a_parameter_unit_that_is_no_diggs_unit_is_named() -> None:
    data = site()
    data["params"] += [
        {"node": "SPT-1", "scope": "procedure", "name": "hammerMass", "value": 63.5, "uom": "kg"},
        {
            "node": "SPT-1",
            "scope": "procedure",
            "name": "hammerDropHeight",
            "value": 30,
            "uom": "inches",
        },
    ]
    assert found(tables.check(data)) == [("tables.unknown-unit", "params/4/uom")]


@pytest.mark.parametrize(
    ("table", "column", "space_column", "space"),
    [
        ("samples", "type", "type_code_space", "sampleType.xml"),
        ("layers", "classification", "classification_code_space", "grp-astmD2487.xml"),
    ],
)
def test_a_code_that_is_not_in_its_bundled_code_list_is_named(
    table: str, column: str, space_column: str, space: str
) -> None:
    data = site()
    data[table][0][column] = "nonsense"
    (finding,) = tables.check(data)
    assert (finding.rule_id, finding.path) == ("tables.unknown-code", f"{table}/1/{column}")
    assert finding.severity is Severity.WARNING
    assert f"'nonsense' is not a code of the code list {CODES}/{space}" in finding.message
    # A code space with a fragment names the code itself.
    data[table][0][space_column] = f"{CODES}/{space}#nonsense"
    assert found(tables.check(data)) == [("tables.unknown-code", f"{table}/1/{column}")]


def test_a_sampling_method_and_a_parameter_code_are_checked() -> None:
    data = site()
    data["sampling_activities"][0] |= {
        "method": "hand",
        "method_code_space": f"{CODES}/samplerInsertionMethod.xml",
    }
    data["params"].append(
        {
            "node": "BH-1",
            "name": "boreholePurpose",
            "value": "curiosity",
            "code_space": f"{CODES}/boreholePurpose.xml",
        }
    )
    assert found(tables.check(data)) == [
        ("tables.unknown-code", "params/3/value"),
        ("tables.unknown-code", "sampling_activities/1/method"),
    ]


def test_a_code_from_a_list_meant_for_another_value_is_named() -> None:
    data = site()
    data["layers"][0]["classification_code_space"] = f"{CODES}/astmD2487.xml"
    (finding,) = tables.check(data)
    assert (finding.rule_id, finding.path) == ("tables.unknown-code", "layers/1/classification")
    assert "'CL' is a code for classificationCode, not for classificationSymbol" in finding.message
    # Validation of the file written refuses the same code, for the same reason.
    invalid = [f for f in tables.round_trip(data).validation.findings if f.severity.rank > 1]
    assert [f.rule_id for f in invalid] == ["dictionary.6"]


def test_a_code_list_that_is_not_bundled_is_not_checked() -> None:
    data = site()
    data["samples"][0] |= {"type": "U100", "type_code_space": "https://example.com/ours.xml"}
    data["layers"][0] |= {"classification": "Clay", "classification_code_space": "our-list"}
    assert tables.check(data) == ()


def test_a_code_with_no_code_space_is_plain_text() -> None:
    data = site()
    data["samples"][0] |= {"type": "U100", "type_code_space": None}
    assert tables.check(data) == ()


def test_check_is_a_superset_of_what_to_model_reports() -> None:
    data = site()
    data["layers"].append({**data["layers"][0], "top": 1.2, "base": 3})
    data["samples"].append({"key": "S2", "sampling_activity": "A9"})
    data["results_DrivenPenetrationTest"][0]["blows"] = 5
    structure = tables.to_model(data).findings
    assert {f.rule_id for f in structure} == {"tables.duplicate-key", "tables.orphan-row"}
    findings = tables.check(data)
    assert findings[: len(structure)] == structure
    assert found(findings[len(structure) :]) == [
        ("tables.unknown-property", "results_DrivenPenetrationTest/1/blows")
    ]


def test_an_issue_on_many_rows_is_one_finding_that_counts_them() -> None:
    data = site()
    data["samples"] = [
        {
            "key": f"S{n}",
            "sampling_activity": "A1",
            "type": "tube",
            "type_code_space": f"{CODES}/sampleType.xml",
        }
        for n in range(3)
    ]
    (finding,) = tables.check(data)
    assert finding.path == "samples/1/type"
    assert "(3 rows)" in finding.message


def test_input_that_is_not_the_profile_raises() -> None:
    with pytest.raises(TablesError, match="not a table of the profile"):
        tables.check({"boreholes": []})


def test_file_text_in_a_message_is_cut_short_and_has_no_control_characters() -> None:
    data = site()
    data["samples"][0]["type"] = "x\u202e" + "y" * 500
    (finding,) = tables.check(data)
    assert "\u202e" not in finding.message
    assert len(finding.message) < 400


@pytest.mark.parametrize("rule", ["tables.unknown-property", "tables.unknown-code"])
def test_the_new_rules_are_catalogued_with_guidance(rule: str) -> None:
    info = explain_rule(rule)
    assert info.known
    assert info.step == "tables"
    assert info.severities == (_report.RULES[rule],) == (Severity.WARNING,)
    assert info.guidance is not None


def test_check_is_exported() -> None:
    assert "check" in tables.__all__
    assert tables.check is _check.check


def test_docstring_examples_run() -> None:
    result = doctest.testmod(_check)
    assert result.attempted > 0
    assert result.failed == 0
