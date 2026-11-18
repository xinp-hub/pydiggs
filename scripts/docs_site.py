#!/usr/bin/env python3
"""Assemble the published documentation site from its two builds.

``.github/workflows/docs.yml`` builds the default branch into ``<site>/dev/`` and the newest
release tag into ``<site>/latest/``. This script picks that tag and writes what ties the two
trees into one site:

- ``versions.json`` in the format mike writes, which Material's version switcher reads;
- ``<version>/index.html``, a redirect to ``latest/``, because the switcher links to a
  version by its number;
- a root ``index.html`` that redirects to ``latest/``, or to ``dev/`` before any release;
- a root copy of ``llms.txt`` and ``llms-full.txt`` from the tree the root redirects to.

Older releases are not kept: every deploy replaces the whole site.

    python scripts/docs_site.py latest-tag
    python scripts/docs_site.py assemble --site DIR [--latest X.Y.Z]

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

#: A final release. Pre-releases (v1.0.0rc1) never become ``latest``.
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_REDIRECT = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Redirecting</title>
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={target}">
<link rel="canonical" href="{target}">
</head>
<body><p>Redirecting to <a href="{target}">{target}</a>.</p></body>
</html>
"""


def newest_release(tags: Iterable[str]) -> str:
    """Return the highest ``vX.Y.Z`` tag as ``X.Y.Z``, or ``""`` when there is none."""
    found = [m for m in map(RELEASE_TAG.fullmatch, (tag.strip() for tag in tags)) if m]
    if not found:
        return ""
    return ".".join(max((m.groups() for m in found), key=lambda parts: tuple(map(int, parts))))


def versions(latest: str) -> list[dict[str, object]]:
    """Return the ``versions.json`` entries, newest first, as mike would list them."""
    entries: list[dict[str, object]] = []
    if latest:
        entries.append({"version": latest, "title": latest, "aliases": ["latest"]})
    entries.append({"version": "dev", "title": "dev (unreleased)", "aliases": []})
    return entries


def assemble(site: Path, latest: str = "") -> None:
    """Write the files that join ``site/dev`` and ``site/latest`` into one site.

    Raises:
        ValueError: ``latest`` is not ``X.Y.Z``, or a tree it needs has not been built.
    """
    if latest and not _VERSION.fullmatch(latest):
        msg = f"not a release version: {latest!r}"
        raise ValueError(msg)
    trees = ["dev", "latest"] if latest else ["dev"]
    for tree in trees:
        if not (site / tree / "index.html").is_file():
            msg = f"{site / tree} has not been built"
            raise ValueError(msg)
    if not latest and (site / "latest").exists():
        msg = f"{site / 'latest'} exists but no release version was given"
        raise ValueError(msg)
    home = trees[-1]
    if latest:
        (site / latest).mkdir(exist_ok=True)
        (site / latest / "index.html").write_text(
            _REDIRECT.format(target="../latest/"), encoding="utf-8"
        )
    (site / "index.html").write_text(_REDIRECT.format(target=f"{home}/"), encoding="utf-8")
    (site / "versions.json").write_text(json.dumps(versions(latest)) + "\n", encoding="utf-8")
    for name in ("llms.txt", "llms-full.txt"):
        if (site / home / name).is_file():
            shutil.copyfile(site / home / name, site / name)


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand; see the module docstring."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("latest-tag", help="print tag= and version= for $GITHUB_OUTPUT")
    build = commands.add_parser("assemble", help="write versions.json and the redirects")
    build.add_argument("--site", type=Path, required=True)
    build.add_argument("--latest", default="", help="the release built into latest/, if any")
    args = parser.parse_args(argv)
    if args.command == "latest-tag":
        listed = subprocess.run(
            ["git", "tag", "--list", "v*"],  # noqa: S607 - git from PATH, as CI provides it
            capture_output=True,
            text=True,
            check=True,
        )
        version = newest_release(listed.stdout.splitlines())
        print(f"tag={'v' + version if version else ''}")
        print(f"version={version}")
        return 0
    try:
        assemble(args.site, args.latest)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"assembled {args.site}: {', '.join(str(v['version']) for v in versions(args.latest))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
