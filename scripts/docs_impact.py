#!/usr/bin/env python3
"""Fail a pull request that changes what users see without changing what they read.

When the public API changes (anything added, removed or given a new signature), or the
console scripts or extras in pyproject.toml change, the same pull request must also change
``docs/``, ``README.md`` or ``CHANGELOG.md``, or carry the label ``no-docs-needed``.

    python scripts/docs_impact.py --base origin/main [--labels "a,b"]

The API is read from source by griffe (``--no-inspection``): the pull request's code is
never imported or run. ``griffe check`` names the breaking changes; additions are found by
comparing griffe's JSON dump of the public names before and after. The CI job runs the base
branch's copy of this script, so a pull request cannot relax the check that judges it.

Standard library only; griffe runs through ``uvx``.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MODULE = "pydiggs"
GRIFFE = ("uvx", "--from", "griffe==2.4.1", "griffe")
LABEL = "no-docs-needed"
DOCS = ("docs/", "README.md", "CHANGELOG.md")
#: Pages scripts/docs_gen.py writes. They follow pyproject.toml by themselves, so a change
#: to one is not evidence that anybody documented anything.
GENERATED = ("docs/facts.md",)
_MAX_DEPTH = 12
_MAX_LISTED = 30


def _lookup(dump: dict[str, Any], path: str) -> dict[str, Any] | None:
    """Find the object at a dotted path in a griffe dump, or None."""
    parts = path.split(".")
    node = dump.get(parts[0])
    for part in parts[1:]:
        if not isinstance(node, dict):
            return None
        node = _members(node).get(part)
    return node if isinstance(node, dict) else None


def _members(node: dict[str, Any]) -> dict[str, Any]:
    members = node.get("members") or {}
    if isinstance(members, list):
        return {member["name"]: member for member in members}
    return dict(members)


def _fingerprint(node: dict[str, Any]) -> str:
    """Describe the part of an object a caller depends on."""
    kind = node.get("kind", "")
    shape: Any = None
    if kind == "function":
        keys = ("name", "kind", "default", "annotation")
        shape = {
            "parameters": [{k: p.get(k) for k in keys} for p in node.get("parameters", [])],
            "returns": node.get("returns"),
        }
    elif kind == "class":
        shape = node.get("bases")
    elif kind == "attribute":
        shape = node.get("annotation")
    elif kind == "alias":
        shape = node.get("target_path")
    return f"{kind} {json.dumps(shape, sort_keys=True)}"


def surface(dump: dict[str, Any], module: str = MODULE) -> dict[str, str]:
    """Return the public names in a griffe dump, each with a fingerprint of its signature.

    A module that declares ``__all__`` exposes exactly those names. Anywhere else a name is
    public when it has no leading underscore and is defined there, not imported.
    """
    found: dict[str, str] = {}

    def walk(node: dict[str, Any], path: str) -> None:
        for _ in range(_MAX_DEPTH):
            target = _lookup(dump, node.get("target_path", "")) if "target_path" in node else None
            if target is None:
                break
            node = target
        kind = node.get("kind", "")
        found[path] = _fingerprint(node)
        if kind not in ("module", "class") or path.count(".") >= _MAX_DEPTH:
            return
        exports = node.get("exports") if kind == "module" else None
        for name, member in sorted(_members(node).items()):
            if exports is not None:
                public = name in exports
            else:
                private = name.startswith("_") and not (kind == "class" and name == "__init__")
                public = not private and member.get("kind") != "alias"
            if public:
                walk(member, f"{path}.{name}")

    if module in dump:
        walk(dump[module], module)
    return found


def api_changes(base: dict[str, str], head: dict[str, str]) -> list[str]:
    """List every public name that was added, removed or given a new signature."""
    changes = [f"added    {name}" for name in sorted(head.keys() - base.keys())]
    changes += [f"removed  {name}" for name in sorted(base.keys() - head.keys())]
    changes += [
        f"changed  {name}" for name in sorted(base.keys() & head.keys()) if base[name] != head[name]
    ]
    return changes


def metadata_changes(base_toml: str, head_toml: str) -> list[str]:
    """List the console scripts and extras that differ between two pyproject.toml texts."""
    changes = []
    tables = {
        "scripts": "console script",
        "gui-scripts": "GUI script",
        "optional-dependencies": "extra",
    }
    before = tomllib.loads(base_toml).get("project", {}) if base_toml else {}
    after = tomllib.loads(head_toml).get("project", {}) if head_toml else {}
    for table, noun in tables.items():
        old, new = before.get(table, {}), after.get(table, {})
        for name in sorted(old.keys() | new.keys()):
            if name not in old:
                changes.append(f"added    {noun} {name}")
            elif name not in new:
                changes.append(f"removed  {noun} {name}")
            elif old[name] != new[name]:
                changes.append(f"changed  {noun} {name}")
    return changes


def docs_touched(changed_files: Iterable[str]) -> list[str]:
    """Return the changed files that count as documentation written by a person."""
    return [
        path
        for path in changed_files
        if path not in GENERATED and (path in DOCS or path.startswith("docs/"))
    ]


def decide(
    changes: list[str], changed_files: Iterable[str], labels: Iterable[str]
) -> tuple[bool, str]:
    """Return whether the pull request passes, and the message to print."""
    if not changes:
        return True, "No change to the public API, console scripts or extras."
    listed = "\n".join(f"  {change}" for change in changes[:_MAX_LISTED])
    if len(changes) > _MAX_LISTED:
        listed += f"\n  ... and {len(changes) - _MAX_LISTED} more"
    docs = docs_touched(changed_files)
    if docs:
        return True, f"What users see changed:\n{listed}\nDocumented in: {', '.join(docs)}"
    if LABEL in {label.strip() for label in labels}:
        return True, f"What users see changed:\n{listed}\nAccepted by the `{LABEL}` label."
    return False, (
        f"What users see changed:\n{listed}\n"
        "but docs/, README.md and CHANGELOG.md did not (generated pages do not count).\n"
        "Describe the change in the docs or the changelog in this pull request. If it needs "
        f"no documentation, add the label `{LABEL}` and re-run this job."
    )


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607 - git from PATH, as CI provides it
        cwd=root,
        capture_output=True,
        check=False,
    )


def _dump(search: Path) -> dict[str, Any]:
    """Return griffe's JSON dump of the package under ``search``, or {} when it is absent."""
    if not (search / MODULE).exists():
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dump.json"
        command = [*GRIFFE, "dump", MODULE, "--search", str(search), "--no-inspection"]
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [*command, "-o", str(out)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            msg = f"griffe could not read {search / MODULE}:\n{proc.stdout}{proc.stderr}"
            raise RuntimeError(msg)
        loaded: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
        return loaded


def gather(root: Path, base: str) -> tuple[list[str], list[str]]:
    """Return (what changed for users, the files changed) between the merge base and HEAD.

    Raises:
        RuntimeError: git or griffe failed, so no honest answer exists.
    """
    merged = _git(root, "merge-base", base, "HEAD")
    if merged.returncode != 0:
        msg = f"no merge base with {base}: {merged.stderr.decode(errors='replace').strip()}"
        raise RuntimeError(msg)
    against = merged.stdout.decode().strip()
    diff = _git(root, "diff", "--name-only", against, "HEAD")
    changed_files = diff.stdout.decode().splitlines()

    old_toml = _git(root, "show", f"{against}:pyproject.toml")
    head_toml = (root / "pyproject.toml").read_text(encoding="utf-8")
    changes = metadata_changes(
        old_toml.stdout.decode() if old_toml.returncode == 0 else "", head_toml
    )

    with tempfile.TemporaryDirectory() as tmp:
        archive = _git(root, "archive", "--format=tar", against, "--", "src")
        if archive.returncode == 0:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
                tar.extractall(tmp, filter="data")
        before = surface(_dump(Path(tmp) / "src"))
    after = surface(_dump(root / "src"))
    changes += api_changes(before, after)

    if before:
        command = [*GRIFFE, "check", MODULE, "--search", "src", "--no-inspection"]
        check = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [*command, "--against", against, "--format", "github"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        # griffe writes its findings, as GitHub annotations, to stderr.
        print(check.stdout + check.stderr, end="")
        if check.returncode == 1 and not any(c.startswith(("removed", "changed")) for c in changes):
            changes.append("changed  see the `griffe check` findings above")
        elif check.returncode not in (0, 1):
            msg = f"griffe check failed:\n{check.stdout}{check.stderr}"
            raise RuntimeError(msg)
    return changes, changed_files


def main(argv: list[str] | None = None) -> int:
    """Judge the working tree against ``--base``; exit 1 when the docs were left behind."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", required=True, help="the ref the pull request targets")
    parser.add_argument("--labels", default="", help="the pull request's labels, comma-separated")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="the repository root")
    args = parser.parse_args(argv)
    try:
        changes, changed_files = gather(args.root, args.base)
    except RuntimeError as exc:
        print(f"::error title=Docs impact could not be decided::{str(exc).splitlines()[0]}")
        print(exc, file=sys.stderr)
        return 2
    passed, message = decide(changes, changed_files, args.labels.split(","))
    if not passed:
        print("::error title=Docs do not follow this change::See the job log for what changed.")
    print(message)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
