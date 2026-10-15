#!/usr/bin/env python3
"""Keep AI coding agents out of a repository's GitHub contributor list.

GitHub lists as a contributor every identity that appears as a commit author, committer,
or `Co-authored-by:` trailer on the default branch, and it caches that list. Removing an
agent afterwards means rewriting history and flushing GitHub's cache. So the only cheap
fix is never to record one. This module is the single definition of "an AI agent's
identity", shared by:

    adapters/git_gate.py                      commit-msg strips AI trailers; pre-push refuses
                                              AI authors, committers and trailers
    templates/pkg/.github/scripts/            an identical copy, run by CI on every PR so
      ai_attribution.py                       the rule holds even when hooks are skipped

Command line (stdlib only, so CI needs nothing installed):

    python3 ai_attribution.py check BASE..HEAD   exit 1 and list commits that credit an AI
    python3 ai_attribution.py strip MSGFILE      remove AI trailers from a commit message

Matching is deliberately narrow. A person called Claude, or an engineer who works at an
AI company, is never matched by name or employer alone: an identity counts only when its
email belongs to an agent (a vendor's no-reply or agent address, or a GitHub bot or app
account) or when a known agent name comes with a no-reply or bot address.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Trailer keys that credit a tool or a co-author. Matching is case-insensitive.
TRAILER_KEYS = (
    "co-authored-by",
    "made-with",
    "generated-by",
    "generated-with",
    "assisted-by",
    "ai-assisted-by",
)

# Agent and product names, matched as whole words. Used on their own only for tool
# trailers such as `Made-with: Cursor`, which carry no email.
AGENT_NAMES = re.compile(
    r"\b("
    r"claude(?:[\s-]+(?:code|opus|sonnet|haiku|fable))?|anthropic|"
    r"cursor(?:[\s-]+agent)?|cursoragent|"
    r"(?:github[\s-]+)?copilot(?:[\s-]+swe[\s-]+agent)?|"
    r"(?:openai[\s-]+)?codex|chatgpt|gpt-\d[\w.-]*|"
    r"gemini(?:[\s-]+(?:code[\s-]+assist|cli))?|antigravity|jules|"
    r"aider|devin(?:[\s-]+ai)?|windsurf|cascade|codeium|"
    r"amazon[\s-]+q(?:[\s-]+developer)?|codewhisperer|"
    r"openhands|cline|roo[\s-]*code|kilo[\s-]*code|junie|tabnine|sweep"
    r")\b",
    re.IGNORECASE,
)

# GitHub bot and app accounts used by agents: `123+login@users.noreply.github.com` or
# `login@users.noreply.github.com`.
AGENT_GITHUB_LOGINS = re.compile(
    r"^(?:\d+\+)?("
    r"copilot|copilot-swe-agent(?:\[bot\])?|"
    r"claude(?:\[bot\])?|anthropic-claude(?:\[bot\])?|"
    r"cursor(?:\[bot\])?|cursoragent|cursor-agent(?:\[bot\])?|"
    r"chatgpt-codex-connector(?:\[bot\])?|openai-codex(?:\[bot\])?|codex(?:\[bot\])?|"
    r"gemini-code-assist(?:\[bot\])?|google-labs-jules(?:\[bot\])?|"
    r"devin-ai-integration(?:\[bot\])?|amazon-q-developer(?:\[bot\])?|"
    r"openhands(?:-agent)?(?:\[bot\])?|sweep-ai(?:\[bot\])?|windsurf(?:\[bot\])?"
    r")@users\.noreply\.github\.com$",
    re.IGNORECASE,
)

# Vendor domains, matched only together with an agent-style local part, so a human at
# that company committing under their own address is never matched.
AGENT_VENDOR_EMAIL = re.compile(
    r"^(noreply|no-reply|[\w.+-]*(?:agent|bot|claude|cursor|copilot|codex|gemini|jules|"
    r"devin|windsurf|cascade|aider|antigravity)[\w.+-]*)@"
    r"(?:[\w-]+\.)*(anthropic\.com|claude\.ai|cursor\.com|cursor\.sh|anysphere\.co|"
    r"openai\.com|google\.com|aider\.chat|codeium\.com|windsurf\.com|devin\.ai|"
    r"cognition\.ai|cognition-labs\.com|cline\.bot|kilocode\.ai|tabnine\.com)$",
    re.IGNORECASE,
)

NOREPLY_OR_BOT = re.compile(r"(no-?reply|\bbot\b|\[bot\]|agent)", re.IGNORECASE)
IDENTITY = re.compile(r"^\s*(?P<name>[^<]*?)\s*<(?P<email>[^>]*)>\s*$")
TRAILER = re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z-]*)\s*:\s*(?P<value>.*\S)\s*$")


def is_ai_identity(name: str, email: str) -> bool:
    """True when `name <email>` is an AI agent's identity rather than a person's."""
    email = email.strip().lower()
    if AGENT_GITHUB_LOGINS.match(email) or AGENT_VENDOR_EMAIL.match(email):
        return True
    # A bare agent name is not enough (people are called Claude, Devin or Jules); it
    # needs a no-reply or bot address beside it.
    return bool(AGENT_NAMES.fullmatch(name.strip())) and bool(NOREPLY_OR_BOT.search(email))


def ai_trailer(line: str) -> bool:
    """True when a commit-message line is a trailer that credits an AI agent."""
    m = TRAILER.match(line)
    if not m or m.group("key").lower() not in TRAILER_KEYS:
        return False
    value = m.group("value")
    ident = IDENTITY.match(value)
    if ident:
        return is_ai_identity(ident.group("name"), ident.group("email"))
    # Tool trailers (`Made-with: Cursor`, `Generated-by: Claude Code`) name a product.
    return m.group("key").lower() != "co-authored-by" and bool(AGENT_NAMES.search(value))


def strip_message(text: str) -> tuple[str, list[str]]:
    """Remove AI-crediting trailers; return (new message, removed lines)."""
    kept, removed = [], []
    for line in text.splitlines():
        (removed if ai_trailer(line) else kept).append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    new = "\n".join(kept) + "\n" if kept else ""
    return new, removed


_FIELD = "\x1f"
_RECORD = "\x1e"
_LOG_FIELDS = 6  # sha, author name, author email, committer name, committer email, trailers
_CLI_ARGS = 2  # subcommand and its one argument


def offending_commits(log_args: list[str], cwd: str | None = None) -> list[str]:
    """Describe each commit in `git log <log_args>` that credits an AI agent."""
    fmt = _FIELD.join(["%h", "%an", "%ae", "%cn", "%ce", "%(trailers:unfold)"]) + _RECORD
    # A fixed argv list for git, resolved from PATH like every FORGE hook; no shell.
    out = subprocess.run(  # noqa: S603
        ["git", "log", f"--format={fmt}", *log_args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    problems = []
    for record in out.split(_RECORD):
        fields = record.strip("\n").split(_FIELD)
        if len(fields) < _LOG_FIELDS:
            continue
        sha, an, ae, cn, ce, trailers = fields
        reasons = []
        if is_ai_identity(an, ae):
            reasons.append(f"author {an} <{ae}>")
        if is_ai_identity(cn, ce):
            reasons.append(f"committer {cn} <{ce}>")
        reasons += [f"trailer '{t.strip()}'" for t in trailers.splitlines() if ai_trailer(t)]
        if reasons:
            problems.append(f"{sha}: " + "; ".join(reasons))
    return problems


def _cli(argv: list[str]) -> int:
    if len(argv) == _CLI_ARGS and argv[0] == "strip":
        path = Path(argv[1])
        new, removed = strip_message(path.read_text(encoding="utf-8"))
        if removed:
            path.write_text(new, encoding="utf-8")
            print("removed AI attribution: " + "; ".join(r.strip() for r in removed))
        return 0
    if len(argv) == _CLI_ARGS and argv[0] == "check":
        problems = offending_commits([argv[1]])
        if problems:
            print(
                "::error::These commits credit an AI agent, which would list it as a "
                "GitHub contributor. Remove the trailer or reset the author "
                "(git commit --amend --reset-author, or an interactive rebase):"
            )
            print("\n".join(problems))
            return 1
        print("no AI attribution in " + argv[1])
        return 0
    print(__doc__.split("\n\n", 1)[0], file=sys.stderr)
    print("usage: ai_attribution.py check BASE..HEAD | strip MSGFILE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
