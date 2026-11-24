"""The evidence-signing recipe (docs/conformance-signing.md) is a workflow that would run.

Vendors copy it as it is, so it is held to the rules this repository's own workflows
follow: every action pinned by full commit with its release in a comment, least-privilege
permissions, a named runner image, a timeout.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "conformance-signing.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
_YAML = re.compile(r"^```yaml\n(.*?)^```", re.MULTILINE | re.DOTALL)
_USES = re.compile(r"uses: ([\w.-]+/[\w./-]+)@(\S+)(.*)$", re.MULTILINE)
_PINNED = re.compile(r"[0-9a-f]{40}")
_COMMENT = re.compile(r"\s+# v\d+\.\d+\.\d+\s*$")


def _snippets() -> list[str]:
    return _YAML.findall(PAGE.read_text(encoding="utf-8"))


def test_every_action_in_the_recipe_is_pinned_by_commit_with_its_release():
    uses = [m for snippet in _snippets() for m in _USES.finditer(snippet)]
    assert len(uses) >= 5
    for match in uses:
        action, ref, rest = match.groups()
        assert _PINNED.fullmatch(ref), f"{action}@{ref} is not pinned by commit"
        assert _COMMENT.fullmatch(rest), f"{action}: no '# vX.Y.Z' comment"


def test_actions_shared_with_our_own_workflows_use_the_same_pins():
    ours = dict(m.groups()[:2] for m in _USES.finditer(CI.read_text(encoding="utf-8")))
    for snippet in _snippets():
        for action, ref, _ in _USES.findall(snippet):
            if action in ours:
                assert ref == ours[action], f"{action}: the recipe and ci.yml pin differently"


def test_the_recipe_workflow_has_least_privilege_and_a_timeout():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(_snippets()[0])
    assert workflow["permissions"] == {}
    (job,) = workflow["jobs"].values()
    assert job["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    assert isinstance(job["timeout-minutes"], int)
    assert "latest" not in job["runs-on"]
    steps = job["steps"]
    attest = [s for s in steps if str(s.get("uses", "")).startswith("actions/attest@")]
    assert len(attest) == 1
    subjects = attest[0]["with"]["subject-path"].split()
    assert subjects == ["challenge/evidence.json", "challenge/challenge.json", "export.xml"]
    grade = next(i for i, s in enumerate(steps) if "conformance grade" in str(s.get("run")))
    assert steps.index(attest[0]) > grade  # attest what was graded, after grading
    for step in steps:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


def test_the_predicate_type_is_the_schema_pydiggs_writes():
    from pydiggs import conformance

    schema_id = conformance.conformance_schema()["$id"]
    assert f"predicate-type: {schema_id}" in PAGE.read_text(encoding="utf-8")


def test_the_recipe_names_a_pydiggs_release_that_has_the_suite():
    found = re.search(r'PYDIGGS: "(\d+)\.(\d+)\.(\d+)"', PAGE.read_text(encoding="utf-8"))
    assert found is not None
    assert (int(found.group(1)), int(found.group(2))) >= (1, 15)  # the suite's first release
