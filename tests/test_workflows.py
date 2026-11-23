"""CI runs the same Python on the same image every time.

Root cause of a CI failure in November 2026: ``astral-sh/setup-uv`` with ``python-version``
let uv use the runner image's own Python when its version matched (Ubuntu 24.04 ships 3.12,
26.04 ships 3.14). Those are distro builds that differ from the python-build-standalone
interpreters uv installs (argparse output differed), and GitHub moves ``ubuntu-latest``
between images without notice. So the image is named and uv may only use its own Pythons.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
SETUP_UV = "astral-sh/setup-uv@"
ONLY_MANAGED = re.compile(r"^  UV_PYTHON_PREFERENCE: only-managed$", re.MULTILINE)
#: The top-level keys GitHub Actions accepts in a workflow file.
WORKFLOW_KEYS = frozenset(
    {"name", "run-name", "on", "permissions", "env", "defaults", "concurrency", "jobs"}
)
TOP_LEVEL_ENV = re.compile(r"^env:\n((?:  .*\n|\n)+)", re.MULTILINE)

pytestmark = pytest.mark.skipif(not WORKFLOWS, reason="not a repository checkout")


def uses_managed_python_only(text: str) -> bool:
    """True when the workflow-level ``env:`` block sets UV_PYTHON_PREFERENCE: only-managed."""
    block = TOP_LEVEL_ENV.search(text)
    return block is not None and ONLY_MANAGED.search(block.group(1)) is not None


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_runs_on_a_moving_latest_image(path: Path) -> None:
    labels = re.findall(r"ubuntu-latest", path.read_text(encoding="utf-8"))
    assert labels == [], f"{path.name} names ubuntu-latest; name the image (ubuntu-26.04)"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_using_setup_uv_uses_only_managed_pythons(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if SETUP_UV not in text:
        pytest.skip(f"{path.name} does not use setup-uv")
    assert uses_managed_python_only(text), (
        f"{path.name} needs `env: UV_PYTHON_PREFERENCE: only-managed` at workflow level"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("env:\n  A: b\n  UV_PYTHON_PREFERENCE: only-managed\n\njobs:\n", True),
        ("env:\n  UV_PYTHON_PREFERENCE: system\n\njobs:\n", False),
        ("jobs:\n  x:\n    env:\n      UV_PYTHON_PREFERENCE: only-managed\n", False),
        ("jobs:\n", False),
    ],
    ids=["set", "other-value", "job-level-only", "absent"],
)
def test_the_check_reads_only_the_workflow_level_env(text: str, expected: bool) -> None:
    assert uses_managed_python_only(text) is expected


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_is_a_workflow(path: Path) -> None:
    # A workflow GitHub cannot read fails at once with no jobs and never runs again, with
    # nothing in the pull request to say so: #393 dropped the "# " from the first line of
    # docs.yml (the file became one string) and docs-links.yml (invalid YAML).
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path.name} is not a mapping"
    # PyYAML reads the key `on` as the boolean True (YAML 1.1).
    keys = {"on" if key is True else key for key in workflow}
    assert {"on", "jobs"} <= keys, f"{path.name} has no trigger or no jobs"
    assert keys <= WORKFLOW_KEYS, f"{path.name}: GitHub refuses the keys {keys - WORKFLOW_KEYS}"


def test_the_docs_check_trusts_only_what_a_cdn_cannot_serve_stale() -> None:
    # GitHub Pages' CDN served the old versions.json for the whole 15 minutes after the
    # 1.16.0 deployment went live, so the release run failed although nothing was wrong.
    # What decides now is a page that first exists in the release's own deployment; a
    # stale copy of a page that only changes is a warning.
    text = (ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    step = text[text.index("name: The site serves what was just published") :]
    decides = step[step.index("missing() {") : step.index("stale() {")]
    warns = step[step.index("stale() {") :]
    assert '"$site/$RELEASED/"' in decides and "url=../latest/" in decides
    assert "lists " not in decides and "package-version" not in decides
    assert "lists " in warns and "package-version" in warns
    assert "::warning title=The CDN still serves an older copy::" in warns
    assert "::error" not in warns[warns.index("for attempt in $(seq 1 40)") :]
