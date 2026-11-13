"""Check that every commit of a pull request carries a Developer Certificate of Origin sign-off.

A commit passes when its message has a ``Signed-off-by: Name <email>`` line whose name
and email are those of the commit's author or of its committer (what ``git commit -s``
writes). Merge commits are skipped. Commits authored by one of ``EXEMPT_BOTS`` are exempt.

    python3 .github/scripts/dco_check.py <base-sha> <head-sha>

Exit 0 when every commit in ``base..head`` passes, 1 when one does not, 2 on a usage or
git error. Standard library only. See CONTRIBUTING.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# GitHub accounts whose commits need no sign-off: the bots that write commits here.
# Dependabot authors its own commits. The merge-policy App (merge-policy.yml) approves and
# merges but writes none in a pull request; it is listed so that stays true if it ever
# does. Its slug is not in the workflow (it comes from vars.AUTOMERGE_APP_CLIENT_ID), so
# keep this name in step with the App installed on the repository.
EXEMPT_BOTS: frozenset[str] = frozenset({"dependabot[bot]", "xinp-merge-policy[bot]"})

# A bot's commits carry the address GitHub gives it: <account id>+<login>@users.noreply...
_BOT_EMAIL = re.compile(r"\d+\+(?P<login>[^@]+)@users\.noreply\.github\.com")
_SIGN_OFF = re.compile(
    r"^Signed-off-by:[ \t]*(?P<name>[^<>\n]*?)[ \t]*<(?P<email>[^<>\s]+)>[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
_FIELDS = 5  # author name, author email, committer name, committer email, message

Git = Callable[..., str]


@dataclass(frozen=True)
class Commit:
    """What the check reads from one commit."""

    sha: str
    author: tuple[str, str]
    committer: tuple[str, str]
    message: str


def _identity(name: str, email: str) -> tuple[str, str]:
    """Name and email as compared: case-insensitive, whitespace collapsed."""
    return " ".join(name.split()).casefold(), email.strip().casefold()


def sign_offs(message: str) -> list[tuple[str, str]]:
    """Every ``Signed-off-by`` name and email in a commit message."""
    return [(m["name"], m["email"]) for m in _SIGN_OFF.finditer(message)]


def is_exempt(commit: Commit) -> bool:
    """Whether the commit is authored by a bot in ``EXEMPT_BOTS``."""
    name, email = commit.author
    match = _BOT_EMAIL.fullmatch(email)
    return name in EXEMPT_BOTS and match is not None and match["login"] == name


def is_signed_off(commit: Commit) -> bool:
    """Whether a sign-off names the commit's author or its committer."""
    wanted = {_identity(*commit.author), _identity(*commit.committer)}
    return any(_identity(name, email) in wanted for name, email in sign_offs(commit.message))


def missing(commits: Sequence[Commit]) -> list[Commit]:
    """The commits that are neither exempt nor signed off."""
    return [c for c in commits if not is_exempt(c) and not is_signed_off(c)]


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603  # fixed argv; the revisions are validated SHAs
        ["git", *args],  # noqa: S607  # git from PATH
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def read_commits(base: str, head: str, git: Git = _git) -> list[Commit]:
    """The non-merge commits reachable from ``head`` and not from ``base``, oldest first."""
    shas = git("rev-list", "--no-merges", "--reverse", f"{base}..{head}").split()
    commits = []
    for sha in shas:
        # A name or an email cannot hold a newline, so the first four lines are safe to split.
        shown = git("show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", sha)
        fields = shown.split("\n", _FIELDS - 1)
        if len(fields) != _FIELDS:
            raise ValueError(f"unexpected git output for {sha}")
        author_name, author_email, committer_name, committer_email, message = fields
        commits.append(
            Commit(sha, (author_name, author_email), (committer_name, committer_email), message)
        )
    return commits


def _printable(text: str) -> str:
    """Commit metadata is untrusted: keep it on one line, with no control characters."""
    return "".join(ch if ch.isprintable() else "?" for ch in text)


def report(commits: Sequence[Commit], failed: Sequence[Commit]) -> str:
    """The text printed for a run. Every line starts with fixed text or a commit id."""
    if not failed:
        return f"DCO: all {len(commits)} commit(s) are signed off or exempt."
    lines = [f"DCO: {len(failed)} of {len(commits)} commit(s) have no matching Signed-off-by:"]
    for commit in failed:
        name, email = commit.author
        found = sign_offs(commit.message)
        reason = "the sign-off names someone else" if found else "no Signed-off-by line"
        lines.append(f"  {commit.sha[:12]} by {_printable(name)} <{_printable(email)}>: {reason}")
    lines += [
        "",
        "Each commit needs a line 'Signed-off-by: Your Name <your@email>' that matches its",
        "author or committer. It certifies the Developer Certificate of Origin 1.1",
        "(https://developercertificate.org/); see CONTRIBUTING.md.",
        "  last commit only:  git commit --amend --signoff --no-edit",
        "  the whole branch:  git rebase --signoff <base branch>",
        "then force-push the branch.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, git: Git = _git) -> int:
    """Check ``base..head``; see the module docstring for the exit codes."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or not all(_SHA.fullmatch(arg) for arg in args):  # noqa: PLR2004
        print("usage: dco_check.py <base-sha> <head-sha> (full commit ids)", file=sys.stderr)
        return 2
    try:
        commits = read_commits(args[0], args[1], git)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        print(f"dco_check: git failed: {_printable(str(detail).strip())}", file=sys.stderr)
        return 2
    failed = missing(commits)
    print(report(commits, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
