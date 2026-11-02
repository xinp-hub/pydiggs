#!/usr/bin/env python3
"""Decide, deterministically, which risk tier a pull request is in.

    Tier 0  Dependabot patch/minor update of Python dependencies that touches only
            `uv.lock` and requirement strings in `pyproject.toml`, from PyPI. Auto-merges
            when CI is green.
    Tier 1  Small same-repository PR whose opener and every pusher of its branch (from
            GitHub's activity log, not from forgeable commit emails) are allowlisted, touching only Python under src/ and tests/ and Markdown
            docs, that breaks no public API, deletes no tests and weakens no check.
            Auto-merges when CI is green and an independent agent review approves.
    Tier 2  Everything else -- including every GitHub Actions bump. A human reviews and
            merges it (FORGE gate HG4).

The tier comes from the author, the file list, file modes and the diff -- never from an
agent's opinion -- so a persuasive pull request cannot talk its way into a lower tier.
Tier 1 is an allowlist of paths, not a denylist: a file type nobody thought about is
Tier 2 by default.

What makes the tier binding is outside this script: merge-policy.yml runs it from the base
branch, the merge App's key lives in an environment only the default branch can use, and
branch protection wants one approval that a push dismisses.

The diff is read with the base branch's .gitattributes (`--attr-source`) and `--text`, so
a pull request cannot mark its own files binary to hide them. Anything the parser cannot
attribute stops the run, and the pull request falls back to a human.

Stdlib only.

    python3 merge_policy.py classify --base SHA --head SHA [--deps-json F] [--commits-json F]
                                     [--pushers-json F] [--api-break]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DEPENDABOT = "dependabot[bot]"
# GitHub's own committer identity for commits made through the web UI or the API.
WEB_FLOW = "web-flow"

# Tier 1 may touch these and nothing else.
TIER1_ROOTS = (("src/", ".py"), ("tests/", ".py"), ("docs/", ".md"))
TIER1_FILES = frozenset({"README.md", "CHANGELOG.md"})
# ...and never these, wherever they sit: agent instructions, test-collection hooks, and
# git metadata that changes how every other file is read.
NEVER_TIER1_NAMES = frozenset(
    {
        "agents.md",
        "claude.md",
        "gemini.md",
        "codeowners",
        "conftest.py",
        ".cursorrules",
        ".clinerules",
        ".rules",
        ".windsurfrules",
        ".gitattributes",
        ".gitmodules",
        ".gitignore",
    }
)
# Local and override variants agents also load (CLAUDE.local.md, AGENTS.override.md, ...).
NEVER_TIER1_SUFFIXES = (".local.md", ".override.md")
# The commits API stops at 250; a longer history cannot be checked whole.
MAX_COMMITS = 249

# Tier 0: what a Dependabot uv update may touch. Workflow files are never Tier 0 -- an
# action runs with this repository's tokens, and merge-policy.yml holds the merge App's.
TIER0_PATHS = frozenset({"uv.lock", "pyproject.toml"})
PYPI_SIMPLE = 'registry = "https://pypi.org/simple"'
PYPI_FILES = "https://files.pythonhosted.org/"

HOLD_LABELS = frozenset({"hold", "do-not-merge", "needs-human"})
MAX_LINES = 400
MAX_FILES = 20
REGULAR_FILE = "100644"

_OP = r"(?:==|>=|<=|~=|!=|<|>)\s*[0-9][0-9A-Za-z.*+!-]*"
REQUIREMENT_LINE = re.compile(
    rf'^\s*"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?\s*{_OP}(?:\s*,\s*{_OP})*'
    rf'(?:\s*;[^"@]*)?",?\s*$'
)
TEST_DEF = re.compile(r"^\s*(async\s+)?def\s+test_")
# Lines that switch a check off. An agent PR that adds one is asking for a human.
WEAKENING = re.compile(
    r"\bmark\.(skip|skipif|xfail)\b|pytest\.skip\(|importorskip|unittest\.skip|\bSkipTest\b"
    r"|add_marker|collect_ignore|pytestmark"
    r"|#\s*pragma:\s*no\s*cover|#\s*type:\s*ignore|#\s*(ruff|flake8):\s*noqa|#\s*noqa"
    r"|#\s*nosec|#\s*mypy:|#\s*pyright:|#\s*pylint:\s*disable"
    r"|--no-verify|fail_under|cov-fail-under|--deselect|--ignore",
    re.IGNORECASE,
)


@dataclass
class FileChange:
    path: str
    status: str  # A, M, D, T (git --raw status letters; renames are off)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    old_mode: str = REGULAR_FILE
    new_mode: str = REGULAR_FILE


@dataclass
class PullRequest:
    author: str
    is_fork: bool = False
    draft: bool = False
    labels: frozenset[str] = frozenset()
    sender: str = ""  # who triggered this run
    commits: tuple[tuple[str, str], ...] = ()  # (author login, committer login) per commit
    # Who actually pushed to the head branch, from the activity log. Commit logins come
    # from commit emails, which anyone can set; a push is attributed by GitHub itself.
    pushers: tuple[str, ...] = ()


@dataclass
class Decision:
    tier: int
    reasons: list[str]

    @property
    def needs_review(self) -> bool:
        return self.tier == 1


def version_parts(version: str | None) -> tuple[int, ...] | None:
    """(major, minor, patch) from a dotted version; None for a commit SHA or nothing."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[.+-]?[0-9A-Za-z.+-]*)?", version or "")
    return tuple(int(g or 0) for g in m.groups()) if m else None


def update_kind(dep: dict) -> str:
    """patch | minor | major | downgrade | unknown, from the versions where they are given.

    For a 0.x package a minor bump is a breaking one, as semver allows, so it counts as major.
    """
    old, new = version_parts(dep.get("prevVersion")), version_parts(dep.get("newVersion"))
    if old and new:
        if new < old:
            return "downgrade"
        if new[0] != old[0] or (old[0] == 0 and new[1] != old[1]):
            return "major"
        return "minor" if new[1] != old[1] else "patch"
    return {
        "version-update:semver-patch": "patch",
        "version-update:semver-minor": "minor",
        "version-update:semver-major": "major",
    }.get(dep.get("updateType") or "", "unknown")


def file_shape_reasons(f: FileChange) -> list[str]:
    """What makes a file unreadable as a plain text diff: modes, links, binaries."""
    reasons = []
    if f.status == "D":
        reasons.append(f"{f.path}: deleted")
    elif f.new_mode != REGULAR_FILE:
        reasons.append(f"{f.path}: mode {f.new_mode} (symlink, submodule or executable)")
    elif f.status == "M" and f.old_mode != f.new_mode:
        reasons.append(f"{f.path}: mode changed {f.old_mode} -> {f.new_mode}")
    elif f.status == "T":
        reasons.append(f"{f.path}: type changed")
    elif not f.added and not f.removed and f.status != "A":
        reasons.append(f"{f.path}: no readable text change (binary or empty)")
    if any("\0" in ln for ln in f.added):
        reasons.append(f"{f.path}: contains NUL bytes")
    return reasons


def dependency_file_reasons(f: FileChange) -> list[str]:
    """pyproject.toml may only change requirement strings; uv.lock may only point at PyPI."""
    reasons = []
    if f.path == "pyproject.toml":
        odd = [ln for ln in f.added + f.removed if ln.strip() and not REQUIREMENT_LINE.match(ln)]
        if odd:
            reasons.append(f"pyproject.toml: changes more than a version spec ({odd[0]!r})")
    if f.path == "uv.lock":
        for ln in f.added:
            if "source = {" in ln and PYPI_SIMPLE not in ln:
                reasons.append(f"uv.lock: a package source other than PyPI ({ln.strip()!r})")
            if re.search(r'\burl = "', ln) and f'url = "{PYPI_FILES}' not in ln:
                reasons.append(f"uv.lock: a download from outside PyPI ({ln.strip()[:120]!r})")
    return reasons


def classify_dependabot(pr: PullRequest, files: list[FileChange], deps: list[dict]) -> Decision:
    reasons: list[str] = []
    if not deps:
        reasons.append("Dependabot metadata is missing or unverified, so the update is unknown")
    for dep in deps:
        name = dep.get("dependencyName", "?")
        kind = update_kind(dep)
        if kind not in ("patch", "minor"):
            reasons.append(f"{name}: {kind} update")
        if dep.get("maintainerChanges"):
            reasons.append(f"{name}: the package's maintainers changed in this release")
    others = sorted({a for a, _ in pr.commits if a != DEPENDABOT})
    if not pr.commits or others:
        reasons.append(f"commits not authored by Dependabot: {', '.join(others) or 'none listed'}")
    pushers = sorted({p for p in pr.pushers if p != DEPENDABOT})
    if not pr.pushers or pushers:
        reasons.append(f"pushes not made by Dependabot: {', '.join(pushers) or 'none listed'}")
    for f in files:
        if f.path not in TIER0_PATHS:
            reasons.append(f"{f.path}: outside what a Python dependency bump may touch")
            continue
        reasons += file_shape_reasons(f) + dependency_file_reasons(f)
    if reasons:
        return Decision(2, sorted(set(reasons), key=reasons.index))
    return Decision(0, ["Dependabot patch/minor update of Python dependencies from PyPI"])


def in_tier1_paths(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in NEVER_TIER1_NAMES or name.endswith(NEVER_TIER1_SUFFIXES):
        return False
    if path in TIER1_FILES:
        return True
    return any(path.startswith(root) and path.endswith(ext) for root, ext in TIER1_ROOTS)


def identity_reasons(pr: PullRequest, authors: frozenset[str]) -> list[str]:
    """Everyone who triggered, pushed to or wrote commits on the branch must be allowlisted.

    The activity log is what GitHub itself attributes; commit logins are derived from commit
    emails and only raise the bar.
    """
    reasons: list[str] = []
    if pr.sender and pr.sender not in authors:
        reasons.append(f"this run was triggered by {pr.sender}, who is not in AUTOMERGE_AUTHORS")
    strangers = sorted(
        {a for a, _ in pr.commits if a not in authors}
        | {c for _, c in pr.commits if c not in authors and c != WEB_FLOW}
    )
    outsiders = sorted({p for p in pr.pushers if p not in authors})
    if not pr.pushers:
        reasons.append("the branch's push history could not be read")
    elif outsiders:
        reasons.append(f"pushed to by someone outside AUTOMERGE_AUTHORS: {', '.join(outsiders)}")
    if len(pr.commits) > MAX_COMMITS:
        reasons.append(f"{len(pr.commits)} commits; too many to check every one")
    if not pr.commits:
        reasons.append("the pull request's commits could not be listed")
    elif strangers:
        reasons.append(f"commits by someone outside AUTOMERGE_AUTHORS: {', '.join(strangers)}")
    return reasons


def classify_change(
    pr: PullRequest, files: list[FileChange], api_break: bool, authors: frozenset[str]
) -> list[str]:
    reasons = identity_reasons(pr, authors)
    lines = sum(len(f.added) + len(f.removed) for f in files)
    if len(files) > MAX_FILES:
        reasons.append(f"{len(files)} files changed (Tier 1 allows {MAX_FILES})")
    if lines > MAX_LINES:
        reasons.append(f"{lines} lines changed (Tier 1 allows {MAX_LINES})")
    if api_break:
        reasons.append("griffe reports a breaking change to the public API (HG2)")
    removed_tests = added_tests = 0
    for f in files:
        if not in_tier1_paths(f.path):
            reasons.append(f"{f.path}: outside the paths Tier 1 may touch")
        reasons += file_shape_reasons(f)
        removed_tests += sum(1 for ln in f.removed if TEST_DEF.match(ln))
        added_tests += sum(1 for ln in f.added if TEST_DEF.match(ln))
        weak = [ln for ln in f.added if WEAKENING.search(ln)]
        if weak:
            reasons.append(f"{f.path}: adds a line that switches a check off ({weak[0].strip()!r})")
    if removed_tests > added_tests:
        reasons.append(f"removes {removed_tests - added_tests} more test(s) than it adds")
    return reasons


def classify(
    pr: PullRequest,
    files: list[FileChange],
    *,
    deps: list[dict] | None = None,
    api_break: bool = False,
    authors: frozenset[str] = frozenset(),
) -> Decision:
    held = sorted({label.lower() for label in pr.labels} & HOLD_LABELS)
    blockers = [
        reason
        for applies, reason in (
            (bool(held), f"labelled {', '.join(held)}"),
            (pr.draft, "draft"),
            (pr.is_fork, "opened from a fork"),
            (not files, "no changed files were found"),
        )
        if applies
    ]
    if blockers:
        return Decision(2, blockers)
    if pr.author == DEPENDABOT:
        return classify_dependabot(pr, files, deps or [])
    if pr.author not in authors:
        return Decision(2, [f"{pr.author} is not in AUTOMERGE_AUTHORS"])
    reasons = classify_change(pr, files, api_break, authors)
    if reasons:
        return Decision(2, reasons)
    return Decision(1, ["small change to source, tests or docs; needs an agent review"])


def git(*args: str) -> str:
    # A fixed argv list for git, resolved from PATH like every FORGE hook; no shell.
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    ).stdout


def changed_files(base: str, head: str) -> list[FileChange]:
    """Files, modes and added/removed lines between the merge base of base and head.

    Attributes come from the base tree and every file is diffed as text, so nothing in the
    pull request -- a `.gitattributes` with `-diff`, a diff driver -- changes what is read.
    """
    span = f"{base}...{head}"
    files: dict[str, FileChange] = {}
    fields = git("diff", "--raw", "-z", "--no-renames", "--abbrev=40", span).split("\0")
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not path:
            continue
        old_mode, new_mode, _, _, status = meta.lstrip(":").split()
        files[path] = FileChange(path, status[:1], old_mode=old_mode, new_mode=new_mode)
    current: FileChange | None = None
    in_header = False
    patch = git(
        f"--attr-source={base}", "-c", "core.quotePath=false",
        "diff", "-U0", "--no-renames", "--no-color", "--text", "--no-textconv", "--no-ext-diff",
        "--src-prefix=a/", "--dst-prefix=b/", span,
    )  # fmt: skip
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current, in_header = None, True
        elif line.startswith("@@"):
            in_header = False
        elif in_header and line.startswith(("+++ ", "--- ")):
            # Git ends a name containing a space with a tab.
            name = line[4:].rstrip("\t")
            if name != "/dev/null":
                current = files.get(name[2:])
                if current is None:
                    # Lines we cannot attribute are lines we cannot judge (a name git still
                    # quotes, say): stop, and the pull request falls back to a human.
                    raise SystemExit(f"merge policy: cannot place diff lines for {name!r}")
        elif in_header and line.startswith("Binary files "):
            raise SystemExit(f"merge policy: git still reports a binary diff: {line!r}")
        elif current is not None and line.startswith("+"):
            current.added.append(line[1:])
        elif current is not None and line.startswith("-"):
            current.removed.append(line[1:])
    return list(files.values())


def load_json(path: str | None) -> list:
    if not path or not Path(path).is_file():
        return []
    text = Path(path).read_text(encoding="utf-8").strip()
    data = json.loads(text) if text else []
    return data if isinstance(data, list) else []


def pull_request_from_event(commits: list, pushers: list) -> PullRequest:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr = event["pull_request"]
    head_repo = (pr["head"].get("repo") or {}).get("full_name")
    return PullRequest(
        author=pr["user"]["login"],
        is_fork=head_repo != pr["base"]["repo"]["full_name"],
        draft=bool(pr.get("draft")),
        labels=frozenset(label["name"] for label in pr.get("labels", [])),
        sender=(event.get("sender") or {}).get("login", ""),
        commits=tuple(
            (str(c.get("author") or ""), str(c.get("committer") or ""))
            for c in commits
            if isinstance(c, dict)
        ),
        pushers=tuple(str(p) for p in pushers if p),
    )


def write_outputs(decision: Decision) -> None:
    summary = [f"### Merge policy: Tier {decision.tier}", ""]
    summary += [f"- {r}" for r in decision.reasons]
    if decision.tier == 2:
        summary += ["", "A human reviews and merges this pull request (HG4)."]
    print("\n".join(summary))
    append_env_file("GITHUB_STEP_SUMMARY", "\n".join(summary) + "\n")
    append_env_file("GITHUB_OUTPUT", f"tier={decision.tier}\n")


def append_env_file(name: str, text: str) -> None:
    """Append to one of the files Actions names in the environment; a no-op off CI."""
    if os.environ.get(name):
        with Path(os.environ[name]).open("a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--deps-json", help="fetch-metadata's updated-dependencies-json, as a file")
    c.add_argument("--commits-json", help="[{author, committer}] logins for every PR commit")
    c.add_argument("--pushers-json", help="[login] of every actor in the head branch's activity")
    c.add_argument("--api-break", action="store_true")
    args = parser.parse_args(argv)

    authors = frozenset(
        a for a in re.split(r"[\s,]+", os.environ.get("AUTOMERGE_AUTHORS", "")) if a
    )
    decision = classify(
        pull_request_from_event(load_json(args.commits_json), load_json(args.pushers_json)),
        changed_files(args.base, args.head),
        deps=load_json(args.deps_json),
        api_break=args.api_break,
        authors=authors,
    )
    write_outputs(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
