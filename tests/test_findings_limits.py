"""HG1 amendment: findings cap and instance size limit for validate()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydiggs import InstanceReadError, Severity, Step, validate, validator
from pydiggs.cli import main
from pydiggs.findings import rules
from pydiggs.findings.run import DEFAULT_MAX_BYTES, DEFAULT_MAX_FINDINGS

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"


def test_defaults():
    assert DEFAULT_MAX_FINDINGS == 10_000
    assert DEFAULT_MAX_BYTES == 100 * 1024 * 1024


def test_truncation_keeps_n_findings_plus_one_marker():
    full = validate(NO_ERROR)
    assert len(full.findings) == 51
    report = validate(NO_ERROR, max_findings=5)
    marker = [f for f in report.findings if f.rule_id == rules.FINDINGS_TRUNCATED]
    assert len(report.findings) == 6
    (finding,) = marker
    assert finding.step is Step.STRUCTURE
    assert finding.severity is Severity.WARNING  # the worst finding left out
    assert "Stopped after 5 findings; 46 more were not reported" in finding.message
    assert report.steps[Step.DICTIONARY] == "incomplete"
    assert report.steps[Step.SCHEMA] == "passed"


def test_truncated_errors_keep_the_report_failing():
    report = validate(SCHEMA_ERROR, checks=["schema"], max_findings=1)
    (_, marker) = sorted(report.findings, key=lambda f: f.rule_id)
    assert marker.rule_id == rules.FINDINGS_TRUNCATED
    assert marker.severity is Severity.ERROR
    assert report.steps[Step.SCHEMA] == "incomplete"
    assert report.ok is False


def test_no_limit_and_bad_limits():
    assert len(validate(NO_ERROR, max_findings=None).findings) == 51
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="max_findings"):
            validate(NO_ERROR, max_findings=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_bytes"):
        validate(NO_ERROR, max_bytes=0)


def test_size_limit_is_configurable_and_legacy_keeps_its_own(tmp_path, monkeypatch):
    size = NO_ERROR.stat().st_size
    with pytest.raises(InstanceReadError, match=f"exceeds {size - 1} bytes"):
        validate(NO_ERROR, max_bytes=size - 1)
    assert validate(NO_ERROR, max_bytes=size).ok
    from pydiggs.findings import run

    monkeypatch.setattr(run, "DEFAULT_MAX_BYTES", size - 1)
    with pytest.raises(InstanceReadError):
        validate(NO_ERROR)
    assert validator(str(NO_ERROR), output_log=False).schema_check() is True


def test_cli_max_findings(capsys):
    assert main(["validate", "--format", "json", "--max-findings", "3", str(NO_ERROR)]) == 0
    doc = json.loads(capsys.readouterr().out)
    findings = doc["files"][0]["findings"]
    assert len(findings) == 4
    assert findings[0]["rule_id"] == "structure.findings-truncated"
    assert main(["validate", "--format", "json", "--max-findings", "0", str(NO_ERROR)]) == 0
    assert len(json.loads(capsys.readouterr().out)["files"][0]["findings"]) == 51
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--max-findings", "-1", str(NO_ERROR)])
    assert exc.value.code == 2
