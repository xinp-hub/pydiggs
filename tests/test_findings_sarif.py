"""SARIF 2.1.0 output, validated against the vendored OASIS schema (Draft 4)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
from hypothesis import given
from hypothesis import strategies as st

from pydiggs.findings import _sarif
from pydiggs.findings.model import Finding, Report, UnreadableFile

from .strategies import reports, unreadable_files

SCHEMA_PATH = Path(__file__).parent / "fixtures" / "sarif" / "sarif-schema-2.1.0.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text("utf-8"))
VALIDATOR = jsonschema.Draft4Validator(SCHEMA, format_checker=jsonschema.FormatChecker())


def test_vendored_schema_is_the_official_errata01_file():
    digest = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    assert digest == "c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e"
    assert SCHEMA["id"] == _sarif.SARIF_SCHEMA
    assert "c3b4bb2d" in (SCHEMA_PATH.parent / "NOTICE").read_text("utf-8")


def _validate(log):
    VALIDATOR.validate(json.loads(json.dumps(log)))


@given(st.lists(reports(), max_size=3), st.lists(unreadable_files(), max_size=2), st.booleans())
def test_every_log_validates_against_sarif_2_1_0(checked, unreadable, absolute):
    log = _sarif.sarif_log(checked, unreadable, tool_version="1.2.0", absolute_paths=absolute)
    _validate(log)
    for run in log["runs"]:
        rule_ids = [r["id"] for r in run["tool"]["driver"]["rules"]]
        assert rule_ids == sorted(set(rule_ids))
        for result in run["results"]:
            assert rule_ids[result["ruleIndex"]] == result["ruleId"]
            region = result["locations"][0]["physicalLocation"].get("region")
            assert region is None or region["startLine"] >= 1
            artifact = result["locations"][0]["physicalLocation"]["artifactLocation"]
            assert run["artifacts"][artifact["index"]]["location"]["uri"] == artifact["uri"]
        for artifact in run["artifacts"]:
            uri = artifact["location"]["uri"]
            if absolute and "uriBaseId" not in artifact["location"]:
                assert urlsplit(uri).scheme == "file"
            else:
                assert not urlsplit(uri).scheme
                assert not uri.startswith("/")


def _report(*found, path="site/BH 01.xml", absolute="/home/me/site/BH 01.xml"):
    return Report(
        path=path,
        absolute_path=absolute,
        sha256="b" * 64,
        diggs_version="3.0.0",
        steps={},
        findings=found,
        tool_version="1.2.0",
    )


def test_result_mapping():
    finding = Finding(
        step="dictionary",
        rule_id="dictionary.5",
        severity="info",
        message="hello",
        line=7,
        column=3,
        xpath="/diggs:Diggs[1]/@gml:id",
        gml_id="d1",
    )
    log = _sarif.sarif_log([_report(finding)], [], tool_version="1.2.0")
    _validate(log)
    assert log["version"] == "2.1.0"
    (run,) = log["runs"]
    assert run["properties"] == {"layer": "native"}
    (result,) = run["results"]
    assert result["level"] == "note"
    assert result["message"] == {"text": "hello"}
    location = result["locations"][0]
    assert location["physicalLocation"] == {
        "artifactLocation": {"uri": "site/BH%2001.xml", "uriBaseId": "SRCROOT", "index": 0},
        "region": {"startLine": 7, "startColumn": 3},
    }
    assert location["logicalLocations"] == [
        {"fullyQualifiedName": "/diggs:Diggs[1]/@gml:id", "kind": "attribute"}
    ]
    assert result["partialFingerprints"] == {
        "diggsConformanceKey/v1": "dictionary|info|xpath:/Diggs[1]/@id|dictionary.5"
    }
    assert result["properties"] == {"step": "dictionary", "gmlId": "d1"}
    (rule,) = run["tool"]["driver"]["rules"]
    assert rule == {
        "id": "dictionary.5",
        "shortDescription": {"text": "Code is not found in the code list dictionary"},
        "helpUri": "https://xinp-hub.github.io/pydiggs/findings/#rule-ids",
        "defaultConfiguration": {"level": "note"},
    }
    assert "/home/me" not in json.dumps(log)


def test_no_region_without_line_and_element_kind_and_worst_level_wins():
    a = Finding(step="schema", rule_id="schema.x", severity="warning", message="", xpath="/a[1]")
    b = Finding(step="schema", rule_id="schema.x", severity="error", message="")
    log = _sarif.sarif_log([_report(a, b)], [], tool_version="1")
    _validate(log)
    run = log["runs"][0]
    assert run["tool"]["driver"]["rules"][0]["defaultConfiguration"] == {"level": "error"}
    for result in run["results"]:
        assert "region" not in result["locations"][0]["physicalLocation"]
        assert result["message"]["text"] == "XML Schema validation rule x"
    assert run["results"][1]["locations"][0]["logicalLocations"][0]["kind"] == "element"


def test_absolute_paths_and_unreadable_notifications():
    log = _sarif.sarif_log(
        [_report()],
        [UnreadableFile(path="gone.xml", error="No such file", absolute_path="/home/me/gone.xml")],
        tool_version="1",
        absolute_paths=True,
    )
    _validate(log)
    run = log["runs"][0]
    assert "originalUriBaseIds" not in run
    assert run["artifacts"][0]["location"] == {"uri": "file:///home/me/site/BH%2001.xml"}
    (invocation,) = run["invocations"]
    assert invocation["executionSuccessful"] is False
    note = invocation["toolExecutionNotifications"][0]
    assert note["level"] == "error"
    assert note["locations"][0]["physicalLocation"]["artifactLocation"]["index"] == 1


def test_one_run_per_layer():
    native = Finding(step="schema", rule_id="schema.x", severity="error", message="")
    official = Finding(
        layer="official", step="schema", rule_id="schema.y", severity="error", message=""
    )
    log = _sarif.sarif_log([_report(native, official)], [], tool_version="1")
    _validate(log)
    assert [r["properties"]["layer"] for r in log["runs"]] == ["native", "official"]
    assert [len(r["results"]) for r in log["runs"]] == [1, 1]
    assert _report(native).to_sarif()["runs"][0]["results"][0]["ruleId"] == "schema.x"
