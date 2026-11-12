"""The DCO sign-off check for pull requests (.github/scripts/dco_check.py) and its CI job."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "dco_check.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason=".github/ is not in this tree")

ALICE = ("Alice Example", "alice@example.org")
BOB = ("Bob Example", "bob@example.org")
GITHUB = ("GitHub", "noreply@github.com")
DEPENDABOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture(scope="module")
def dco() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pydiggs_dco_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module's postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _commit(dco, message, author=ALICE, committer=None, sha=SHA_A):
    return dco.Commit(sha, author, committer or author, message)


def _signed(who):
    return f"Fix a thing\n\nBody text.\n\nSigned-off-by: {who[0]} <{who[1]}>\n"


# --- the matching rule ----------------------------------------------------------------


def test_sign_off_by_the_author_passes(dco: ModuleType) -> None:
    assert dco.is_signed_off(_commit(dco, _signed(ALICE)))


def test_no_sign_off_fails(dco: ModuleType) -> None:
    assert not dco.is_signed_off(_commit(dco, "Fix a thing\n\nBody text.\n"))


def test_sign_off_by_the_committer_passes(dco: ModuleType) -> None:
    # A maintainer applies someone's patch and signs it off themselves.
    assert dco.is_signed_off(_commit(dco, _signed(BOB), author=ALICE, committer=BOB))


def test_author_sign_off_survives_a_web_commit(dco: ModuleType) -> None:
    # GitHub is the committer of commits made in the browser and of rebases it performs.
    assert dco.is_signed_off(_commit(dco, _signed(ALICE), author=ALICE, committer=GITHUB))


def test_sign_off_by_someone_else_fails(dco: ModuleType) -> None:
    assert not dco.is_signed_off(_commit(dco, _signed(BOB)))


@pytest.mark.parametrize(
    "who",
    [
        ("Alice Example", "someone-else@example.org"),  # right name, wrong email
        ("Somebody Else", "alice@example.org"),  # right email, wrong name
    ],
)
def test_name_and_email_must_both_match(dco: ModuleType, who) -> None:
    assert not dco.is_signed_off(_commit(dco, _signed(who)))


def test_name_and_email_may_not_come_from_different_people(dco: ModuleType) -> None:
    mixed = (ALICE[0], BOB[1])
    assert not dco.is_signed_off(_commit(dco, _signed(mixed), author=ALICE, committer=BOB))


def test_matching_ignores_case_and_extra_spaces(dco: ModuleType) -> None:
    message = "Fix\n\nsigned-off-by:   alice   EXAMPLE  <Alice@Example.ORG>  \n"
    assert dco.is_signed_off(_commit(dco, message))


def test_any_one_of_several_sign_offs_is_enough(dco: ModuleType) -> None:
    message = _signed(BOB) + f"Signed-off-by: {ALICE[0]} <{ALICE[1]}>\n"
    assert dco.sign_offs(message) == [BOB, ALICE]
    assert dco.is_signed_off(_commit(dco, message))


@pytest.mark.parametrize(
    "line",
    [
        "Signed-off-by: Alice Example",  # no email
        "Signed-off-by: Alice Example alice@example.org",  # email not in angle brackets
        "> Signed-off-by: Alice Example <alice@example.org>",  # quoted, not at line start
        "Signed-off-by: Alice Example <alice@example.org> and more",  # trailing text
        "Co-authored-by: Alice Example <alice@example.org>",  # another trailer
    ],
)
def test_malformed_lines_are_not_sign_offs(dco: ModuleType, line: str) -> None:
    assert not dco.is_signed_off(_commit(dco, f"Fix\n\n{line}\n"))


# --- exemptions -----------------------------------------------------------------------


def test_dependabot_commits_are_exempt(dco: ModuleType) -> None:
    # Dependabot signs off as <support@github.com>, which matches neither identity.
    message = "build: bump lxml\n\nSigned-off-by: dependabot[bot] <support@github.com>\n"
    commit = _commit(dco, message, author=DEPENDABOT, committer=GITHUB)
    assert not dco.is_signed_off(commit)
    assert dco.is_exempt(commit)
    assert dco.missing([commit]) == []


def test_every_listed_bot_is_exempt_with_its_github_address(dco: ModuleType) -> None:
    assert "dependabot[bot]" in dco.EXEMPT_BOTS
    for login in dco.EXEMPT_BOTS:
        author = (login, f"12345+{login}@users.noreply.github.com")
        assert dco.is_exempt(_commit(dco, "chore: automated\n", author=author))


@pytest.mark.parametrize(
    "author",
    [
        ("dependabot[bot]", "alice@example.org"),  # the name alone is not enough
        ("dependabot[bot]", "1+someone-else@users.noreply.github.com"),
        ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com.example.org"),
        ("Alice Example", "49699333+dependabot[bot]@users.noreply.github.com"),
        ("some-other[bot]", "1+some-other[bot]@users.noreply.github.com"),
    ],
)
def test_other_identities_are_not_exempt(dco: ModuleType, author) -> None:
    commit = _commit(dco, "build: bump lxml\n", author=author)
    assert not dco.is_exempt(commit)
    assert dco.missing([commit]) == [commit]


def test_a_person_committing_for_a_bot_is_not_exempt(dco: ModuleType) -> None:
    # Exemption follows the author. A person's own commit on a Dependabot branch needs
    # their sign-off.
    commit = _commit(dco, "fix the bump\n", author=ALICE, committer=DEPENDABOT)
    assert dco.missing([commit]) == [commit]


# --- main, with git replaced ----------------------------------------------------------


def _fake_git(commits):
    def git(*args: str) -> str:
        if args[0] == "rev-list":
            assert args[1:] == ("--no-merges", "--reverse", f"{SHA_A}..{SHA_B}")
            return "".join(f"{sha}\n" for sha in commits)
        assert args[:2] == ("show", "-s")
        author, committer, message = commits[args[-1]]
        return "\n".join([*author, *committer, message])

    return git


def test_main_passes_when_every_commit_is_signed_or_exempt(dco: ModuleType, capsys) -> None:
    commits = {
        "1" * 40: (ALICE, ALICE, _signed(ALICE)),
        "2" * 40: (DEPENDABOT, GITHUB, "build: bump lxml\n"),
    }
    assert dco.main([SHA_A, SHA_B], git=_fake_git(commits)) == 0
    assert "all 2 commit(s)" in capsys.readouterr().out


def test_main_fails_and_names_the_unsigned_commit(dco: ModuleType, capsys) -> None:
    commits = {
        "1" * 40: (ALICE, ALICE, _signed(ALICE)),
        "2" * 40: (BOB, BOB, "Fix\n\nNo trailer here.\n"),
        "3" * 40: (BOB, BOB, _signed(ALICE)),
    }
    assert dco.main([SHA_A, SHA_B], git=_fake_git(commits)) == 1
    out = capsys.readouterr().out
    assert "2 of 3 commit(s)" in out
    assert "1" * 12 not in out
    assert f"{'2' * 12} by Bob Example <bob@example.org>: no Signed-off-by line" in out
    assert f"{'3' * 12} by Bob Example <bob@example.org>: the sign-off names someone else" in out
    assert "https://developercertificate.org/" in out
    assert "git rebase --signoff" in out


def test_main_passes_on_an_empty_range(dco: ModuleType) -> None:
    assert dco.main([SHA_A, SHA_B], git=_fake_git({})) == 0


def test_report_never_starts_a_line_with_commit_text(dco: ModuleType) -> None:
    # Commit text is untrusted, and a line starting with "::" is a workflow command.
    hostile = ("::error::x\x1b[31m", "::warning::y@example.org")
    commit = _commit(dco, "::set-output name=x::y\n", author=hostile)
    text = dco.report([commit], [commit])
    assert "set-output" not in text  # the message is never echoed
    assert "\x1b" not in text
    assert not [line for line in text.splitlines() if line.lstrip().startswith("::")]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [SHA_A],
        [SHA_A, SHA_B, SHA_A],
        ["master", SHA_B],  # names are refused: only full commit ids
        [SHA_A, "--output=/tmp/x"],  # nothing that git could read as an option
        [SHA_A[:12], SHA_B],
    ],
)
def test_main_refuses_anything_but_two_full_commit_ids(dco: ModuleType, argv, capsys) -> None:
    def no_git(*args: str) -> str:
        raise AssertionError("git must not run")

    assert dco.main(argv, git=no_git) == 2
    assert "usage" in capsys.readouterr().err


def test_main_reports_a_git_failure_as_exit_2(dco: ModuleType, capsys) -> None:
    def broken(*args: str) -> str:
        raise subprocess.CalledProcessError(128, ["git"], stderr="fatal: bad object")

    assert dco.main([SHA_A, SHA_B], git=broken) == 2
    assert "bad object" in capsys.readouterr().err


# --- against a real repository --------------------------------------------------------


def _run_git(repo: Path, *args: str, who=ALICE) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": who[0],
        "GIT_AUTHOR_EMAIL": who[1],
        "GIT_COMMITTER_NAME": who[0],
        "GIT_COMMITTER_EMAIL": who[1],
    }
    result = subprocess.run(  # noqa: S603
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args],  # noqa: S607
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_against_a_real_history_with_a_merge(dco: ModuleType, tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "commit", "-q", "--allow-empty", "-m", "base, unsigned and out of range")
    base = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "checkout", "-q", "-b", "topic")
    _run_git(repo, "commit", "-q", "--allow-empty", "-s", "-m", "signed with git commit -s")
    _run_git(repo, "commit", "-q", "--allow-empty", "-m", "bump", who=DEPENDABOT)
    _run_git(repo, "checkout", "-q", "main")
    _run_git(repo, "commit", "-q", "--allow-empty", "-s", "-m", "main moves on")
    _run_git(repo, "checkout", "-q", "topic")
    # The merge commit itself has no sign-off and must be skipped.
    _run_git(repo, "merge", "-q", "--no-ff", "-m", "Merge main into topic", "main")
    good = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "commit", "-q", "--allow-empty", "-m", "forgot the sign-off", who=BOB)
    bad = _run_git(repo, "rev-parse", "HEAD")

    monkeypatch.chdir(repo)
    # Commits made in the same second have no fixed order between the two branches.
    assert sorted(c.message.splitlines()[0] for c in dco.read_commits(base, bad)) == [
        "bump",
        "forgot the sign-off",
        "main moves on",
        "signed with git commit -s",
    ]
    assert dco.main([base, good]) == 0
    assert dco.main([base, bad]) == 1
    assert dco.main([base, "f" * 40]) == 2  # no such commit


# --- the CI job -----------------------------------------------------------------------


def _job(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", text, re.M | re.S)
    assert match is not None, f"no job {name!r} in ci.yml"
    return match.group(1)


def test_ci_runs_the_check_on_pull_requests() -> None:
    text = CI.read_text(encoding="utf-8")
    job = _job(text, "dco")
    assert "if: github.event_name == 'pull_request'" in job
    assert "fetch-depth: 0" in job
    assert "persist-credentials: false" in job
    # The commit ids reach the shell through the environment, never by interpolation.
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in job
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in job
    assert 'run: python3 .github/scripts/dco_check.py "$BASE_SHA" "$HEAD_SHA"' in job
    run_lines = [line for line in job.splitlines() if line.strip().startswith("run:")]
    assert all("${{" not in line for line in run_lines)
    # No third-party action, no secret, and nothing beyond the workflow's read permission.
    uses = re.findall(r"uses: (\S+)", job)
    assert len(uses) == 1 and re.fullmatch(r"actions/checkout@[0-9a-f]{40}", uses[0])
    assert "secrets." not in job
    assert "permissions:" not in job
    assert "permissions:\n  contents: read\n" in text
    # Staged rollout (plan risk R13): the job reports but `all green` does not need it yet.
    # When it becomes required, turn this into `"dco" in ...`.
    needs = re.search(r"^    needs: \[(.*)\]$", _job(text, "all-green"), re.M)
    assert needs is not None and "dco" not in [n.strip() for n in needs.group(1).split(",")]
