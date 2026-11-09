"""The experimental 3.1-dev profile: opt-in only, never detected, always flagged."""

from __future__ import annotations

from pathlib import Path

import pytest

import pydiggs
from pydiggs.deprecations import version_key
from pydiggs.detect import (
    DEFAULT_PROFILE,
    EXPERIMENTAL_PROFILES,
    NAMESPACE_PROFILES,
    SCHEMA_PATHS,
    detect_diggs_version,
)
from pydiggs.findings import rules
from pydiggs.findings.model import Severity, Step

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official"
DRAFT_FILE = OFFICIAL / "known_invalid" / "3.0_PileDrivingExample_schema-dev.xml"
RELEASED = sorted(SCHEMA_PATHS.keys() - EXPERIMENTAL_PROFILES)


def test_3_1_dev_is_bundled_but_never_detected_or_default():
    assert "3.1-dev" in EXPERIMENTAL_PROFILES
    assert SCHEMA_PATHS["3.1-dev"].is_file()
    assert EXPERIMENTAL_PROFILES.isdisjoint(NAMESPACE_PROFILES.values())
    assert DEFAULT_PROFILE not in EXPERIMENTAL_PROFILES
    # The draft shares the 3.0 namespace, so its own files detect as 3.0.0.
    assert detect_diggs_version(DRAFT_FILE) == "3.0.0"


@pytest.mark.parametrize("path", sorted(OFFICIAL.rglob("*.xml")), ids=lambda p: p.name)
def test_no_official_example_detects_as_experimental(path):
    assert detect_diggs_version(path) in RELEASED


def test_explicit_3_1_dev_validates_a_draft_file_and_warns():
    report = pydiggs.validate(DRAFT_FILE, diggs_version="3.1-dev")
    assert report.diggs_version == "3.1-dev"
    assert report.ok
    assert report.verdict == "valid-with-warnings"
    flagged = [f for f in report.findings if f.rule_id == rules.UNRELEASED_PROFILE]
    assert len(flagged) == 1
    assert flagged[0].step == Step.SCHEMA
    assert flagged[0].severity == Severity.WARNING
    assert "unreleased" in flagged[0].message
    # The same file fails the released 3.0.0 schema it declares.
    assert not pydiggs.validate(DRAFT_FILE).ok


def test_released_profiles_never_carry_the_warning():
    for version in RELEASED:
        report = pydiggs.validate_bytes(DRAFT_FILE.read_bytes(), diggs_version=version)
        assert rules.UNRELEASED_PROFILE not in {f.rule_id for f in report.findings}


def test_own_schema_path_is_not_flagged_as_unreleased():
    report = pydiggs.validate(
        DRAFT_FILE, diggs_version="3.1-dev", schema_path=SCHEMA_PATHS["3.0.0"]
    )
    assert rules.UNRELEASED_PROFILE not in {f.rule_id for f in report.findings}


def test_no_warning_when_the_schema_step_is_not_run():
    report = pydiggs.validate(DRAFT_FILE, diggs_version="3.1-dev", checks=["structure"])
    assert report.findings == ()


def test_unreleased_profile_rule_is_catalogued_as_a_warning():
    info = pydiggs.explain_rule(rules.UNRELEASED_PROFILE)
    assert info.known
    assert info.step == Step.SCHEMA
    assert info.severities == (Severity.WARNING,)


def test_version_key_orders_a_dev_profile_as_its_release():
    assert version_key("3.1-dev") == version_key("3.1.0") > version_key("3.0.0")
    assert sorted([*SCHEMA_PATHS], key=version_key) == list(SCHEMA_PATHS)


def test_warm_up_skips_experimental_profiles_unless_named(monkeypatch):
    from pydiggs.findings import run

    warmed: list[str] = []
    monkeypatch.setattr(run, "_schema", lambda version, _path: warmed.append(version))
    run.warm_up()
    assert sorted(warmed) == RELEASED
    warmed.clear()
    run.warm_up("3.1-dev")
    assert warmed == ["3.1-dev"]


def test_unknown_version_is_still_refused():
    with pytest.raises(ValueError, match=r"3\.1-dev"):
        pydiggs.validate(DRAFT_FILE, diggs_version="3.1")
