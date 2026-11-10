"""Create a Buttondown *draft* announcing a pyDIGGS release, from CHANGELOG.md.

The draft is never sent from CI: a maintainer reads it in Buttondown and sends it.
Only feature releases (X.Y.0) are drafted unless ``--force`` is given; pre-releases
never are.

    python3 .github/scripts/newsletter_draft.py v1.6.0 [--force] [--dry-run]

``BUTTONDOWN_API_KEY`` must be set unless ``--dry-run`` is given. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API = "https://api.buttondown.com/v1/emails"
REPO = "https://github.com/xinp-hub/pydiggs"
DOCS = "https://xinp-hub.github.io/pydiggs/"
_VERSION = re.compile(r"v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<pre>\S*)")


def parse_version(tag: str) -> tuple[str, bool]:
    """Return ``(version, is_feature_release)`` for a tag such as ``v1.6.0``.

    Raises:
        ValueError: ``tag`` is not a ``vX.Y.Z`` version.
    """
    match = _VERSION.fullmatch(tag.strip())
    if match is None:
        msg = f"not a release tag: {tag!r}"
        raise ValueError(msg)
    version = tag.strip().removeprefix("v")
    return version, match["patch"] == "0" and not match["pre"]


def release_notes(changelog: str, version: str) -> str:
    """Return the body of the ``## [version]`` section of a Keep a Changelog file.

    Raises:
        LookupError: the changelog has no section for ``version``.
    """
    lines = changelog.splitlines()
    heading = re.compile(rf"## \[{re.escape(version)}\](\s|$)")
    for start, line in enumerate(lines):
        if heading.match(line):
            body: list[str] = []
            for following in lines[start + 1 :]:
                if following.startswith("## ["):
                    break
                body.append(following)
            return "\n".join(body).strip()
    msg = f"CHANGELOG.md has no section for {version}"
    raise LookupError(msg)


def build_email(version: str, notes: str) -> dict[str, str]:
    """Return the Buttondown request body for a draft announcing ``version``."""
    body = (
        f"pyDIGGS {version} is on PyPI. Upgrade with "
        f"`pip install --upgrade pydiggs=={version}`.\n\n"
        f"{notes}\n\n"
        "---\n\n"
        f"Release notes: {REPO}/releases/tag/v{version}  \n"
        f"Documentation: {DOCS}  \n"
        f"Questions or problems: {REPO}/issues\n"
    )
    # An explicit status keeps this a draft whatever the account's API version defaults.
    return {"subject": f"pyDIGGS {version} released", "body": body, "status": "draft"}


def create_draft(email: dict[str, str], api_key: str) -> str:
    """POST ``email`` to Buttondown and return the new draft's id."""
    request = urllib.request.Request(
        API,
        data=json.dumps(email).encode(),
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "pydiggs-release-newsletter",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        created = json.load(response)
    if created.get("status") != "draft":
        msg = f"Buttondown created a {created.get('status')!r} email, not a draft"
        raise RuntimeError(msg)
    return str(created.get("id", ""))


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="release tag, e.g. v1.6.0")
    parser.add_argument("--force", action="store_true", help="draft a patch release too")
    parser.add_argument("--dry-run", action="store_true", help="print, do not create")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args(argv)

    version, feature = parse_version(args.tag)
    if not re.fullmatch(r"[\d.]+", version):
        print(f"{version} is a pre-release; no newsletter draft")
        return 0
    if not feature and not args.force:
        print(f"{version} is a patch release; no newsletter draft (use --force)")
        return 0

    notes = release_notes(args.changelog.read_text(encoding="utf-8"), version)
    email = build_email(version, notes)
    if args.dry_run:
        print(json.dumps(email, indent=2))
        return 0

    api_key = os.environ.get("BUTTONDOWN_API_KEY", "")
    if not api_key:
        print("BUTTONDOWN_API_KEY is not set", file=sys.stderr)
        return 2
    draft_id = create_draft(email, api_key)
    print(f"Created Buttondown draft {draft_id} for pyDIGGS {version}; review and send it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
