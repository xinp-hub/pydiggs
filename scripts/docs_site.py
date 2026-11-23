#!/usr/bin/env python3
"""Assemble the published documentation site from its two builds.

``.github/workflows/docs.yml`` builds the default branch into ``<site>/dev/`` and the newest
release tag into ``<site>/latest/``. This script picks that tag and writes what ties the two
trees into one site:

``latest-tag --pypi PROJECT`` considers only the tags whose version PyPI lists with at least
one file that is not yanked, so ``latest/`` never describes a release no one can install
yet (1.17.0 was tagged at 17:15 and approved for PyPI at 22:45 on 2026-11-23).

- ``versions.json`` in the format mike writes, which Material's version switcher reads;
- ``<version>/index.html``, a redirect to ``latest/``, because the switcher links to a
  version by its number;
- a root ``index.html`` that redirects to ``latest/``, or to ``dev/`` before any release;
- a root copy of ``llms.txt`` and ``llms-full.txt`` from the tree the root redirects to;
- ``schemas/`` at the root, because every published JSON Schema and every document
  pyDIGGS writes names its schema by a URL there (``$id``, ``$schema``), and those URLs
  must keep resolving. The release's schemas are copied first; a schema only ``dev/``
  has (one that is not released yet) is added beside them, never over a released one;
- a redirect stub at the unversioned address of every page of that tree, so
  ``<site>/http-api/`` leads to ``latest/http-api/`` (``dev/`` before any release) with
  its ``?query`` and ``#fragment``. pyDIGGS emits such addresses (RFC 9457 problem types,
  the SARIF ``helpUri``, the newsletter, old READMEs on PyPI) and they must keep working.
  A page whose top-level folder is one of the names above is skipped, never overwritten;
- a root ``404.html``, unless one exists, that sends any other unknown unversioned path to
  the same path in that tree. It only ever builds a path under the site base, and it does
  nothing on a path inside a tree, so a page missing from ``latest/`` too cannot loop.

Older releases are not kept: every deploy replaces the whole site.

    python scripts/docs_site.py latest-tag [--pypi PROJECT]
    python scripts/docs_site.py assemble --site DIR [--latest X.Y.Z] [--base-url URL]

Standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Collection, Iterable
from pathlib import Path
from urllib.parse import urlsplit

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
#: The address the site is published at when ``--base-url`` is not given (mkdocs.yml).
SITE_URL = "https://xinp-hub.github.io/pydiggs/"
#: Folders of a tree that hold no pages, whatever they contain.
_NOT_PAGES = frozenset({"assets", "search"})
#: A site base path: absolute, ending in "/", plain characters only.
_BASE_PATH = re.compile(r"/(?:[A-Za-z0-9._~-]+/)*")
#: What the root 404.html accepts after the base: plain segments joined by single slashes,
#: so no "%", no backslash and no "//". The page's script uses this same pattern.
SAFE_REST = r"^(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*$"
# A meta refresh drops the #fragment, so a script carries it over; the meta refresh, the
# canonical link and the plain link are the fallback without JavaScript. Inline scripts are
# fine: the site sends no Content-Security-Policy (GitHub Pages sets none, and neither
# mkdocs.yml nor overrides/ adds one).
_STUB = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Redirecting</title>
<meta name="robots" content="noindex">
<script>location.replace({target_js} + location.search + location.hash);</script>
<meta http-equiv="refresh" content="0; url={target}">
<link rel="canonical" href="{target}">
</head>
<body><p>This page has moved to <a href="{target}">{target}</a>.</p></body>
</html>
"""
_NOT_FOUND = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Page not found</title>
<meta name="robots" content="noindex">
<script>
(function () {{
  var base = {base_js}, home = {home_js}, keep = {keep_js};
  var path = location.pathname;
  if (path.indexOf(base) !== 0) return;
  var rest = path.slice(base.length);
  if (!new RegExp({safe_js}).test(rest)) return;
  var parts = rest.split("/");
  if (keep.indexOf(parts[0]) !== -1) return;
  if (parts.indexOf("..") !== -1 || parts.indexOf(".") !== -1) return;
  location.replace(base + home + "/" + rest + location.search + location.hash);
}})();
</script>
</head>
<body><h1>Page not found</h1>
<p>Go to the <a href="{home_href}">pyDIGGS documentation</a>.</p></body>
</html>
"""


def newest_release(tags: Iterable[str], published: Collection[str] | None = None) -> str:
    """Return the highest ``vX.Y.Z`` tag as ``X.Y.Z``, or ``""`` when there is none.

    With ``published``, only a tag whose ``X.Y.Z`` is in it counts.
    """
    found = [m for m in map(RELEASE_TAG.fullmatch, (tag.strip() for tag in tags)) if m]
    if published is not None:
        found = [m for m in found if ".".join(m.groups()) in published]
    if not found:
        return ""
    return ".".join(max((m.groups() for m in found), key=lambda parts: tuple(map(int, parts))))


def pypi_releases(project: str, document: bytes | None = None) -> set[str]:
    """Return the versions PyPI lists for ``project`` with a file that is not yanked.

    ``document`` is PyPI's JSON for the project; it is fetched when not given.
    """
    if document is None:
        document = _fetch(f"https://pypi.org/pypi/{project}/json")
    releases = json.loads(document).get("releases", {})
    return {
        version
        for version, files in releases.items()
        if any(not file.get("yanked", False) for file in files)
    }


def _fetch(url: str, attempts: int = 3) -> bytes:
    """Read ``url``, trying ``attempts`` times.

    The last error propagates, so the docs build fails rather than publish a ``latest/``
    it could not check.
    """
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - https URL built here
                body: bytes = response.read()
                return body
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(10)
    msg = "attempts must be at least 1"
    raise ValueError(msg)


def versions(latest: str) -> list[dict[str, object]]:
    """Return the ``versions.json`` entries, newest first, as mike would list them."""
    entries: list[dict[str, object]] = []
    if latest:
        entries.append({"version": latest, "title": latest, "aliases": ["latest"]})
    entries.append({"version": "dev", "title": "dev (unreleased)", "aliases": []})
    return entries


def assemble(site: Path, latest: str = "", base_url: str = SITE_URL) -> None:
    """Write the files that join ``site/dev`` and ``site/latest`` into one site.

    ``base_url`` is where the site is published; the root 404.html, served at any path,
    needs its absolute path.

    Raises:
        ValueError: ``latest`` is not ``X.Y.Z``, ``base_url`` has an unusual path, or a
            tree it needs has not been built.
    """
    if latest and not _VERSION.fullmatch(latest):
        msg = f"not a release version: {latest!r}"
        raise ValueError(msg)
    base_path(base_url)
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
    _root_schemas(site, trees[::-1])
    # Both tree names are reserved even before the first release, so a dev-only build
    # never writes a root latest/ stub or sends a missing /latest/... path into dev/.
    reserved = {"dev", "latest", *([latest] if latest else []), *_root_names(site)}
    _page_stubs(site, home, reserved)
    _not_found(site, home, reserved, base_path(base_url))


def base_path(base_url: str) -> str:
    """Return the path of ``base_url`` ending in ``/``, e.g. ``/pydiggs/``.

    Raises:
        ValueError: the path has characters other than letters, digits and ``._~-``.
    """
    path = urlsplit(base_url).path.rstrip("/") + "/"
    if not _BASE_PATH.fullmatch(path):
        msg = f"not a plain site base path: {path!r}"
        raise ValueError(msg)
    return path


def fallback(path: str, base: str, home: str, keep: Iterable[str]) -> str | None:
    """Where the root 404.html sends ``path``, or ``None`` to show "not found" instead.

    The same rule as the page's script: a path under ``base`` whose rest is plain, has no
    ``.`` or ``..`` segment and is not inside a tree or other root folder in ``keep``.
    The result always starts with ``base + home + "/"``, so it stays on the site.
    """
    if not path.startswith(base):
        return None
    rest = path[len(base) :]
    if not re.fullmatch(SAFE_REST, rest):
        return None
    parts = rest.split("/")
    if parts[0] in set(keep) or {".", ".."} & set(parts):
        return None
    return f"{base}{home}/{rest}"


def _js(value: object) -> str:
    """``value`` as a JavaScript literal that is safe inside an HTML ``<script>``."""
    return json.dumps(value).replace("<", "\\u003c")


def _root_names(site: Path) -> set[str]:
    """Every name at the site root once the trees, redirects and copies are written.

    The names this script may write are reserved even when absent this time.
    """
    fixed = {"index.html", "versions.json", "llms.txt", "llms-full.txt", "schemas", "404.html"}
    return {path.name for path in site.iterdir()} | fixed


def _page_stubs(site: Path, home: str, reserved: set[str]) -> None:
    """Write ``site/<page>/index.html`` redirecting to ``<home>/<page>/`` for each page.

    A page is a folder of the home tree holding ``index.html`` (MkDocs directory URLs), at
    any depth. A page under a reserved top-level name is skipped and reported.
    """
    tree = site / home
    for index in sorted(tree.rglob("index.html")):
        rel = index.parent.relative_to(tree)
        if not rel.parts or rel.parts[0] in _NOT_PAGES:
            continue
        if rel.parts[0] in reserved:
            print(f"no redirect for {rel.as_posix()}/: {rel.parts[0]} is taken", file=sys.stderr)
            continue
        target = "../" * len(rel.parts) + f"{home}/{rel.as_posix()}/"
        stub = site / rel / "index.html"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(
            _STUB.format(target=html.escape(target), target_js=_js(target)), encoding="utf-8"
        )


def _not_found(site: Path, home: str, reserved: set[str], base: str) -> None:
    """Write the root ``404.html`` unless there is one; see ``fallback``."""
    page = site / "404.html"
    if page.exists():
        return
    page.write_text(
        _NOT_FOUND.format(
            base_js=_js(base),
            home_js=_js(home),
            keep_js=_js(sorted(reserved | _NOT_PAGES)),
            safe_js=_js(SAFE_REST),
            home_href=f"{base}{home}/",
        ),
        encoding="utf-8",
    )


def _root_schemas(site: Path, trees: list[str]) -> None:
    """Copy ``<tree>/schemas/`` of each tree to ``site/schemas/``, the first tree winning.

    A schema's URL is fixed by its ``$id`` at the site root, so it must be served there
    whichever version the root redirects to.
    """
    root = site / "schemas"
    for tree in trees:
        source = site / tree / "schemas"
        if not source.is_dir():
            continue
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            target = root / path.relative_to(source)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand; see the module docstring."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    latest_tag = commands.add_parser(
        "latest-tag", help="print tag= and version= for $GITHUB_OUTPUT"
    )
    latest_tag.add_argument(
        "--pypi", metavar="PROJECT", help="only tags whose version PyPI lists for PROJECT"
    )
    build = commands.add_parser("assemble", help="write versions.json and the redirects")
    build.add_argument("--site", type=Path, required=True)
    build.add_argument("--latest", default="", help="the release built into latest/, if any")
    build.add_argument("--base-url", default=SITE_URL, help="where the site is published")
    args = parser.parse_args(argv)
    if args.command == "latest-tag":
        listed = subprocess.run(
            ["git", "tag", "--list", "v*"],  # noqa: S607 - git from PATH, as CI provides it
            capture_output=True,
            text=True,
            check=True,
        )
        published = pypi_releases(args.pypi) if args.pypi else None
        version = newest_release(listed.stdout.splitlines(), published)
        print(f"tag={'v' + version if version else ''}")
        print(f"version={version}")
        return 0
    try:
        assemble(args.site, args.latest, args.base_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"assembled {args.site}: {', '.join(str(v['version']) for v in versions(args.latest))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
