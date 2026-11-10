"""The "Stay updated" sign-up form (docs hook) and the release newsletter draft script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "mkdocs_hooks.py"
DRAFT = ROOT / ".github" / "scripts" / "newsletter_draft.py"
WORKFLOW = ROOT / ".github" / "workflows" / "newsletter.yml"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hooks() -> ModuleType:
    if not HOOKS.is_file():
        pytest.skip("scripts/ is not in this tree")
    return _load(HOOKS, "pydiggs_docs_hooks")


@pytest.fixture(scope="module")
def draft() -> ModuleType:
    if not DRAFT.is_file():
        pytest.skip(".github/ is not in this tree")
    return _load(DRAFT, "pydiggs_newsletter_draft")


# --- docs hook ------------------------------------------------------------------------


def test_stay_updated_page_carries_the_marker(hooks: ModuleType) -> None:
    page = (ROOT / "docs" / "stay-updated.md").read_text(encoding="utf-8")
    assert hooks.NEWSLETTER_MARKER in page


def test_empty_username_renders_a_note_not_a_form(hooks: ModuleType) -> None:
    out = hooks.on_page_markdown(
        f"x\n\n{hooks.NEWSLETTER_MARKER}\n", config={"extra": {"newsletter": {}}}
    )
    assert "<form" not in out
    assert "not open yet" in out


def test_username_renders_a_scriptless_form(hooks: ModuleType) -> None:
    out = hooks.on_page_markdown(
        hooks.NEWSLETTER_MARKER, config={"extra": {"newsletter": {"buttondown": "pydiggs"}}}
    )
    assert 'action="https://buttondown.com/api/emails/embed-subscribe/pydiggs"' in out
    assert 'method="post"' in out
    assert 'type="email"' in out
    assert "<script" not in out
    assert "unsubscribe" in out


def test_pages_without_the_marker_are_untouched(hooks: ModuleType) -> None:
    text = "# Title\n\nbody\n"
    assert hooks.on_page_markdown(text, config={}) == text


@pytest.mark.parametrize("bad", ['x" onfocus="alert(1)', "a/b", "a b", "x" * 65])
def test_a_bad_username_fails_the_build(hooks: ModuleType, bad: str) -> None:
    with pytest.raises(ValueError, match="Buttondown username"):
        hooks.newsletter_block(bad)


# --- newsletter draft script ---------------------------------------------------------

CHANGELOG = """\
# Changelog

## [Unreleased]

### Added
- Something unreleased.

## [1.6.0] - 2027-01-01

### Added
- Read API.

## [1.5.0] - 2026-11-09

### Added
- Older.
"""


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.6.0", ("1.6.0", True)),
        ("v2.0.0", ("2.0.0", True)),
        ("v1.6.1", ("1.6.1", False)),
        ("v1.7.0rc1", ("1.7.0rc1", False)),
    ],
)
def test_parse_version(draft: ModuleType, tag: str, expected: tuple[str, bool]) -> None:
    assert draft.parse_version(tag) == expected


def test_parse_version_rejects_a_branch_name(draft: ModuleType) -> None:
    with pytest.raises(ValueError, match="not a release tag"):
        draft.parse_version("master")


def test_release_notes_is_one_section(draft: ModuleType) -> None:
    notes = draft.release_notes(CHANGELOG, "1.6.0")
    assert notes == "### Added\n- Read API."


def test_release_notes_missing_section(draft: ModuleType) -> None:
    with pytest.raises(LookupError):
        draft.release_notes(CHANGELOG, "9.9.9")


def test_email_is_always_a_draft(draft: ModuleType) -> None:
    email = draft.build_email("1.6.0", "### Added\n- Read API.")
    assert email["status"] == "draft"
    assert email["subject"] == "pyDIGGS 1.6.0 released"
    assert "- Read API." in email["body"]
    assert "releases/tag/v1.6.0" in email["body"]


def test_patch_and_pre_releases_are_skipped(
    draft: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    for tag in ("v1.6.1", "v1.7.0rc1"):
        assert draft.main([tag, "--changelog", str(changelog)]) == 0
    out = capsys.readouterr().out
    assert "patch release" in out
    assert "pre-release" in out


def test_dry_run_prints_the_draft(
    draft: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    assert draft.main(["v1.6.0", "--dry-run", "--changelog", str(changelog)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "draft"


def test_missing_api_key_fails(
    draft: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.delenv("BUTTONDOWN_API_KEY", raising=False)
    assert draft.main(["v1.6.0", "--changelog", str(changelog)]) == 2


def test_workflow_never_sends_and_keeps_the_secret_in_an_environment() -> None:
    if not WORKFLOW.is_file():
        pytest.skip(".github/ is not in this tree")
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "environment: newsletter" in text
    assert "permissions:\n  contents: read" in text
    assert "persist-credentials: false" in text
    assert "--dry-run" not in text
    assert "send-draft" not in text
