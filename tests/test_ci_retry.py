"""CI re-runs a run only when its failures are jobs GitHub never gave a runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("ci_retry", ROOT / "scripts" / "ci_retry.py")
assert _SPEC is not None and _SPEC.loader is not None
ci_retry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_retry)

NOT_ACQUIRED = [
    {"message": "The job was not acquired by Runner of type hosted even after multiple attempts"},
    {
        "message": "Due to capacity constraints, jobs targeting macOS arm64 runners may experience longer queue times."
    },
]


def job(name: str, conclusion: str, *, started: bool = True, jid: int = 1) -> dict[str, Any]:
    return {
        "id": jid,
        "name": name,
        "conclusion": conclusion,
        "runner_name": "GitHub Actions 1" if started else "",
        "steps": [{"name": "Set up job"}] if started else [],
    }


def annotations(job_: dict[str, Any]) -> list[dict[str, Any]]:
    return (
        NOT_ACQUIRED if not job_["steps"] else [{"message": "Process completed with exit code 1."}]
    )


MACOS = job("test (macos-latest, 3.13)", "cancelled", started=False, jid=2)
SUMMARY = job("all green", "failure", jid=3)
OK = job("lint & types", "success", jid=4)


def run(attempt: int = 1, conclusion: str = "failure") -> dict[str, Any]:
    return {"conclusion": conclusion, "run_attempt": attempt}


def test_a_run_failed_only_by_a_job_without_a_runner_is_re_run() -> None:
    rerun, reason = ci_retry.decide(run(), [OK, MACOS, SUMMARY], annotations)
    assert rerun is True
    assert "test (macos-latest, 3.13)" in reason


def test_a_real_failure_is_left_for_a_person() -> None:
    broken = job("test (3.12)", "failure", jid=5)
    rerun, reason = ci_retry.decide(run(), [OK, MACOS, broken, SUMMARY], annotations)
    assert rerun is False
    assert "test (3.12)" in reason


def test_a_job_that_timed_out_after_starting_is_a_real_failure() -> None:
    slow = job("test (3.11)", "cancelled", jid=6)
    assert ci_retry.decide(run(), [slow, SUMMARY], annotations)[0] is False


def test_a_job_cancelled_before_starting_for_another_reason_is_not_re_run() -> None:
    def no_annotation(job_: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    assert ci_retry.decide(run(), [MACOS, SUMMARY], no_annotation)[0] is False


@pytest.mark.parametrize("attempt", [3, 4])
def test_after_two_automatic_re_runs_a_person_decides(attempt: int) -> None:
    rerun, reason = ci_retry.decide(run(attempt), [MACOS, SUMMARY], annotations)
    assert rerun is False
    assert "person" in reason


@pytest.mark.parametrize("conclusion", ["success", "cancelled"])
def test_only_failed_runs_are_considered(conclusion: str) -> None:
    assert ci_retry.decide(run(conclusion=conclusion), [MACOS], annotations)[0] is False


def test_a_failed_summary_alone_is_not_re_run() -> None:
    assert ci_retry.decide(run(), [OK, SUMMARY], annotations)[0] is False


def test_the_workflow_only_re_runs_and_never_runs_the_inspected_code() -> None:
    text = (ROOT / ".github" / "workflows" / "ci-retry.yml").read_text(encoding="utf-8")
    assert "workflows: [CI]" in text
    assert "ref:" not in text  # checks out the default branch, never the run's head
    assert "sparse-checkout: scripts/ci_retry.py" in text
    assert "persist-credentials: false" in text
    assert "permissions: {}" in text
    assert "--failed" in text
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "\nname: CI\n" in ci  # the name workflow_run listens for
