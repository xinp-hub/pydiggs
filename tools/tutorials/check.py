"""The tutorial gate: every public feature has a video or a stated reason not to.

Run it from the repository root::

    uv run --group docs python tools/tutorials/check.py

It fails (exit 1) when

1. a tutorial spec in ``docs/tutorials/`` is not valid;
2. a public item found by the manifest's ``discover`` entries (a CLI subcommand, a name in
   ``__all__``) is covered by no feature, or by more than one; or a feature names a
   tutorial that does not exist, or covers an item that is no longer public;
3. a tutorial is embedded by no docs page, or a page embeds a tutorial that does not exist.

So a new subcommand or public name fails CI until it has a tutorial or a waiver. It needs
no network, no ffmpeg and no voice model: it checks the specs, not the rendered videos.

``--rehearse`` also runs every terminal tutorial's setup and commands in a temporary
directory, without recording, and checks each command's output against its ``expect``;
the test suite runs it, so a change that breaks a tutorial fails before any video is
rendered.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from files import FOLDER, FilesError, snapshot
from spec import (
    MANIFEST,
    ROOT,
    SpecError,
    Tutorial,
    discover,
    load_manifest,
    load_tutorial,
    markers,
    tutorial_paths,
)


def problems() -> list[str]:
    """Return every reason the gate fails; empty when it passes."""
    found: list[str] = []
    tutorials: set[str] = set()
    for path in tutorial_paths():
        try:
            tutorials.add(load_tutorial(path).id)
        except SpecError as exc:
            found += [f"{exc.source}: {problem}" for problem in exc.problems]
            tutorials.add(path.stem)
    try:
        manifest = load_manifest()
    except SpecError as exc:
        return found + [f"{exc.source}: {problem}" for problem in exc.problems]

    public = discover(manifest)
    covered: dict[str, str] = {}
    for feature in manifest.features:
        if feature.tutorial and feature.tutorial not in tutorials:
            found.append(f"feature {feature.id}: tutorial {feature.tutorial!r} has no spec")
        for item in feature.covers:
            label = item.partition(":")[0]
            if label in manifest.discover and item not in public:
                found.append(f"feature {feature.id}: {item} is not a public item (any more)")
            if item in covered:
                found.append(f"{item} is covered by both {covered[item]} and {feature.id}")
            covered[item] = feature.id
    for item in sorted(public - covered.keys()):
        found.append(
            f"{item} is public but has no tutorial: add it to a feature's covers in "
            f"{MANIFEST.relative_to(MANIFEST.parents[2])}, with a tutorial or a waiver"
        )

    embedded = markers()
    for tid in sorted(tutorials - embedded.keys()):
        found.append(f"tutorial {tid} is not embedded: put <!-- tutorial: {tid} --> in a page")
    for tid in sorted(embedded.keys() - tutorials):
        found.append(f"{', '.join(embedded[tid])}: embeds tutorial {tid!r}, which has no spec")
    in_manifest = {feature.tutorial for feature in manifest.features if feature.tutorial}
    for tid in sorted(tutorials - in_manifest):
        found.append(f"tutorial {tid} is not named by any feature in the manifest")
    return found


def _shell(command: str, work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the commands are the repository's own specs
        ["bash", "-o", "pipefail", "-c", command],  # noqa: S607
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def rehearse(tutorial: Tutorial) -> list[str]:
    """Follow a terminal tutorial as a viewer would; return how it failed its ``expect``.

    Setup runs in one directory; the files the page offers are taken from it exactly as
    build.py publishes them (the same checks, the same zip); then the zip is unpacked in
    an empty directory and every command runs there. So this proves that the download is
    enough to repeat the video, and that each command still prints what its narration says.
    """
    if tutorial.kind != "terminal":
        return []  # a browser tutorial is checked by recording it (tutorials.yml)
    found: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"rehearse-{tutorial.id}-") as tmp:
        made, published_to, viewer = (Path(tmp) / name for name in ("made", "out", "viewer"))
        made.mkdir()
        viewer.mkdir()
        for line in tutorial.setup:
            result = _shell(line.replace("{repo}", shlex.quote(str(ROOT))), made)
            if result.returncode != 0:
                return [f"{tutorial.id}: setup failed: {line.splitlines()[0]}\n{result.stderr}"]
        commands = [scene.run for scene in tutorial.scenes if scene.run]
        try:
            description = snapshot(
                tutorial.id,
                tutorial.title,
                tutorial.files,
                made,
                published_to,
                commands=commands,
                install=[],
            )
        except FilesError as exc:
            return [f"{tutorial.id}: {exc}"]
        with zipfile.ZipFile(published_to / FOLDER / str(description["zip"]["name"])) as archive:
            archive.extractall(viewer)
        for index, scene in enumerate(tutorial.scenes, 1):
            if not scene.run:
                continue
            result = _shell(scene.run, viewer)
            screen = result.stdout + result.stderr
            if not re.search(scene.expect, screen):
                found.append(
                    f"{tutorial.id} scene {index}: from the downloaded files, `{scene.run}` "
                    f"did not show {scene.expect!r}; it printed:\n{screen[-1500:]}"
                )
    return found


def main(argv: list[str] | None = None) -> int:
    """Print the gate's verdict; return the exit status."""
    args = sys.argv[1:] if argv is None else argv
    found = problems()
    if "--rehearse" in args and not found:
        for path in tutorial_paths():
            found += rehearse(load_tutorial(path))
    for problem in found:
        print(f"tutorials: {problem}", file=sys.stderr)
    if found:
        print(f"tutorials: {len(found)} problem(s)", file=sys.stderr)
        return 1
    print("tutorials: every public feature has a tutorial or a waiver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
