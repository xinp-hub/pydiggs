"""Which videos a change touched: the list to watch before merging or releasing.

Run it after build.py, in the pull request's render job::

    python tools/tutorials/report.py --built build/tutorials --summary "$GITHUB_STEP_SUMMARY"

It compares each tutorial's fresh ``meta.json`` with the one published on the docs site
(``<site_url>dev/tutorials/<id>/meta.json``; the site from mkdocs.yml, or ``--base``) and
writes a Markdown table: new tutorials, tutorials whose spec changed, and, scene by scene,
narration that changed and screens that now end differently (a 64-bit average hash of the
scene's last frame, so encoder noise does not count). Unchanged tutorials are counted, not
listed. It is advice for the reviewer and never fails the job: a site that cannot be
reached is reported as such.

What is fetched is data from the published site and is treated as such: only the fields
compared are read, and nothing from it is written into the report except numbers.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
#: Bits that may differ between two renders of the same screen (fonts, encoder noise).
SCREEN_TOLERANCE = 6
_HEX = re.compile(r"^[0-9a-f]{16}$")


def _site_base() -> str:
    """``<site>dev/tutorials/``: ``$DOCS_SITE_URL``, else mkdocs.yml's ``site_url``.

    ``site_url`` may be a plain URL or ``!ENV [DOCS_SITE_URL, "<default>"]``.
    """
    site = os.environ.get("DOCS_SITE_URL", "")
    if not site:
        text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        found = re.search(
            r"^site_url:\s*(?:!ENV\s*\[\s*\w+\s*,\s*)?['\"]?(https://[^'\"\s\]]+)",
            text,
            re.MULTILINE,
        )
        site = found.group(1) if found else ""
    return site.rstrip("/") + "/dev/tutorials/" if site else ""


def _fetch(url: str) -> dict[str, Any] | None:
    if not url.startswith("https://"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - https only
            data = json.loads(response.read(1_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def screen_changed(old: str, new: str) -> bool:
    """Two last-frame hashes differ by more than encoder noise (or one is missing)."""
    if not (_HEX.match(old or "") and _HEX.match(new or "")):
        return bool(old) != bool(new)
    return bin(int(old, 16) ^ int(new, 16)).count("1") > SCREEN_TOLERANCE


def compare(new: dict[str, Any], old: dict[str, Any] | None) -> list[str]:
    """What changed in one tutorial, as short phrases; empty when nothing did."""
    if old is None:
        return ["new tutorial (or the published site could not be read)"]
    changes: list[str] = []
    if new.get("fingerprint") != old.get("fingerprint"):
        changes.append("spec, voice or engine changed")
    previous = old.get("scenes")
    old_scenes: list[Any] = previous if isinstance(previous, list) else []
    new_scenes: list[Any] = list(new.get("scenes") or [])
    for index, scene in enumerate(new_scenes, 1):
        title = str(scene.get("title", f"Step {index}"))
        before = old_scenes[index - 1] if index <= len(old_scenes) else None
        if not isinstance(before, dict):
            changes.append(f"scene {index} ({title}) is new")
            continue
        if scene.get("say_sha256") != before.get("say_sha256"):
            changes.append(f"scene {index} ({title}): narration changed")
        if screen_changed(str(before.get("screen", "")), str(scene.get("screen", ""))):
            changes.append(f"scene {index} ({title}): now ends on a different screen")
    if len(old_scenes) > len(new_scenes):
        changes.append("scenes were removed")
    return changes


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def report(built: Path, base: str) -> str:
    """The Markdown report for every tutorial rendered under ``built``."""
    rows: list[str] = []
    unchanged = 0
    for meta_path in sorted(built.glob("*/meta.json")):
        new = json.loads(meta_path.read_text(encoding="utf-8"))
        tid = meta_path.parent.name
        old = _fetch(f"{base}{tid}/meta.json") if base else None
        changes = compare(new, old)
        if changes:
            rows.append(f"| `{_cell(tid)}` | {_cell('; '.join(changes))} |")
        else:
            unchanged += 1
    lines = ["## Tutorial videos to watch", ""]
    if rows:
        lines += [
            "Download the `tutorials` artifact of this run and watch these before merging:",
            "",
            "| Tutorial | What changed |",
            "|---|---|",
            *rows,
        ]
    else:
        lines.append("No tutorial changed what it shows or says.")
    lines += ["", f"{unchanged} tutorial(s) unchanged. Compared with {base or 'nothing'}.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Write the report to stdout and, when given, the job summary. Always exit 0."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--built", type=Path, default=ROOT / "build" / "tutorials")
    parser.add_argument("--base", default=None, help="published tutorials URL, ending in /")
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)
    base = args.base if args.base is not None else _site_base()
    text = report(args.built, base)
    sys.stdout.write(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
