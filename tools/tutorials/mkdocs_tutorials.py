"""MkDocs hook: put each tutorial video where a page says ``<!-- tutorial: <id> -->``.

The marker becomes a ``<figure>`` with the video (WebM first, MP4 for every other
browser), its captions and chapters, a poster, and the full transcript under it. The
transcript comes from the spec's narration, so the page has a text alternative even where
the video is not built; a local ``mkdocs serve`` with no ``build/tutorials/`` shows the
transcript and a note instead of the player, and ``--strict`` passes either way.

Set ``TUTORIALS_REQUIRE=1`` (the publishing docs build does) to fail the build when a
rendered video is missing, so the site never ships a page without its video.

The video never autoplays, loads nothing until asked (``preload="none"``), and is served
from the docs site itself: no third-party player and no cookies.
"""

from __future__ import annotations

import html
import os
import posixpath
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from files import FOLDER, install_steps, published
from spec import (
    MARKER,
    ROOT,
    Tutorial,
    load_tutorial,
    tutorial_paths,
)

#: Where build.py writes, and where the site serves the files.
MEDIA = ROOT / "build" / "tutorials"
SITE_PREFIX = "tutorials"

_TUTORIALS: dict[str, Tutorial] = {}
_USED: set[str] = set()


def _rendered(tid: str) -> bool:
    folder = MEDIA / tid
    return all((folder / f"{tid}{suffix}").is_file() for suffix in (".mp4", ".en.vtt", ".jpg"))


def figure(tutorial: Tutorial, base: str, *, rendered: bool) -> str:
    """Return the HTML for one tutorial; ``base`` is the media folder's URL from the page."""
    tid = html.escape(tutorial.id)
    title = html.escape(tutorial.title)
    parts = [f'<figure class="tutorial" id="tutorial-{tid}">']
    if rendered:
        src = f"{base}/{tid}"
        webm = (
            f'  <source src="{src}.webm" type="video/webm">\n'
            if (MEDIA / tutorial.id / f"{tutorial.id}.webm").is_file()
            else ""
        )
        parts.append(
            f'<video controls preload="none" playsinline width="1280" height="720"'
            f' poster="{src}.jpg" aria-label="Video: {title}">\n'
            f"{webm}"
            f'  <source src="{src}.mp4" type="video/mp4">\n'
            f'  <track kind="captions" src="{src}.en.vtt" srclang="en" label="English" default>\n'
            f'  <track kind="chapters" src="{src}.chapters.vtt" srclang="en" label="Chapters">\n'
            f'  <a href="{src}.mp4">Download the video</a>\n'
            "</video>"
        )
    else:
        parts.append(
            '<p class="tutorial-pending"><em>The video is built with the published site; '
            "its transcript follows.</em></p>"
        )
    parts.append(
        f"<figcaption><strong>{title}</strong>. {html.escape(tutorial.summary)}</figcaption>"
    )
    parts.append('<details class="tutorial-transcript">\n<summary>Transcript</summary>')
    for chapter, said in tutorial.transcript():
        if chapter:
            parts.append(f"<p><strong>{html.escape(chapter)}.</strong> {html.escape(said)}</p>")
        else:
            parts.append(f"<p>{html.escape(said)}</p>")
    parts += ["</details>", "</figure>"]
    return "\n".join(parts)


def _package() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["name"])


def _size(count: int) -> str:
    return f"{count / 1024:.1f} KB" if count >= 1024 else f"{count} bytes"  # noqa: PLR2004


def _downloads(base: str, description: dict[str, Any]) -> str:
    folder = f"{base}/{FOLDER}"
    archive = description["zip"]
    singles = ", ".join(
        f'<a href="{folder}/{html.escape(str(f["name"]))}" download>'
        f"<code>{html.escape(str(f['name']))}</code></a>"
        for f in description["files"]
    )
    return (
        "The files this video starts from, exactly as it used them: "
        f'<a href="{folder}/{html.escape(str(archive["name"]))}" download>'
        f"<code>{html.escape(str(archive['name']))}</code></a> "
        f"({_size(int(archive['bytes']))}, SHA-256 <code>{archive['sha256']}</code>), "
        f"or one by one: {singles}. They are synthetic examples, Apache-2.0."
    )


def try_it(tutorial: Tutorial, base: str) -> str:
    """The "Try it yourself" admonition: the files the video starts from, and its commands.

    The files and their SHA-256 come from what build.py published (``files/files.json``);
    the commands come from the spec, so the page and the video cannot disagree. Links are
    HTML (``download``), not Markdown, because the files are build output, not pages.
    """
    if not tutorial.files:
        return ""
    description = published(MEDIA / tutorial.id)
    if description is not None:
        body = [_downloads(base, description)]
        install = [str(step) for step in description["install"]]
    else:
        body = ["The files this video starts from are published with the site, next to it."]
        install = install_steps(_package(), "")
    if tutorial.kind == "terminal":
        body += [
            "",
            "Set up a folder with [uv](https://docs.astral.sh/uv/) and the version the video "
            "shows, put the files in it, and run each command there (on Windows, activate "
            r"with `.venv\Scripts\activate`):",
            "",
            "```bash",
            *install,
            *(scene.run for scene in tutorial.scenes if scene.run),
            "```",
        ]
    else:
        body += ["", "Run the browser example as described below, then pick these files."]
    indented = "\n".join(f"    {line}" if line else "" for line in body)
    return f'\n!!! example "Try it yourself"\n\n{indented}\n'


def on_config(config: Any) -> Any:
    """Load every spec once per build; a bad spec stops the build."""
    _TUTORIALS.clear()
    _USED.clear()
    for path in tutorial_paths():
        tutorial = load_tutorial(path)
        _TUTORIALS[tutorial.id] = tutorial
    return config


def on_page_markdown(markdown: str, *, page: Any, **_: Any) -> str:
    """Replace each marker with its figure.

    Raises:
        ValueError: a marker names no tutorial, or (``TUTORIALS_REQUIRE=1``) its video was
            not rendered.
    """
    if "<!--" not in markdown:
        return markdown
    require = os.environ.get("TUTORIALS_REQUIRE") == "1"
    # The page's URL is a directory (``usage/``) or the root; media live under the root.
    url = page.url or ""
    depth = url.rstrip("/") if url.endswith("/") else posixpath.dirname(url)
    up = "/".join(".." for part in depth.split("/") if part)
    base = posixpath.join(up, SITE_PREFIX) if up else SITE_PREFIX

    def replace(match: Any) -> str:
        tid = match.group(1)
        tutorial = _TUTORIALS.get(tid)
        if tutorial is None:
            msg = f"{page.file.src_uri}: no tutorial named {tid!r}"
            raise ValueError(msg)
        rendered = _rendered(tid)
        if require and not rendered:
            msg = f"{page.file.src_uri}: tutorial {tid!r} has no rendered video in {MEDIA}"
            raise ValueError(msg)
        _USED.add(tid)
        media = posixpath.join(base, tid)
        return "\n" + figure(tutorial, media, rendered=rendered) + "\n" + try_it(tutorial, media)

    return MARKER.sub(replace, markdown)


def on_post_build(config: Any) -> None:
    """Copy the rendered media of every embedded tutorial into the site."""
    for tid in sorted(_USED):
        source = MEDIA / tid
        if not source.is_dir():
            continue
        target = Path(config["site_dir"]) / SITE_PREFIX / tid
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if item.suffix in {".mp4", ".webm", ".vtt", ".jpg", ".json"}:
                shutil.copyfile(item, target / item.name)
        files = source / FOLDER
        if files.is_dir():
            shutil.copytree(files, target / FOLDER, dirs_exist_ok=True)
