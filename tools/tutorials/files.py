"""The follow-along files: what a viewer downloads to repeat a tutorial step by step.

A tutorial's ``setup`` makes its starting files off camera, so the video shows commands
working on files the viewer does not have. ``snapshot`` copies the files the spec lists
(``files:``) from the work directory right after setup, before anything is recorded, so
the download is exactly what the video started from and can never drift from it.

Each snapshot is checked before it is published: a plain regular file (no link), UTF-8
text, at most ``MAX_BYTES``, and nothing that looks like a credential or a local path.
It is published as the single files and as one zip, written reproducibly (fixed times,
sorted names, fixed modes), with a SHA-256 for each, and a README that says what the files
are, their licence, and how to run the tutorial's commands.

Standard library only: the renderer, the rehearsal test and the docs hook all use it.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

#: A follow-along file is a small example, never a data set.
MAX_BYTES = 1_048_576
#: Same patterns the spec check refuses in a narration or command.
_SECRET = re.compile(
    r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_LOCAL_PATH = re.compile(r"(/Users/|/home/runner/|/tmp/|[A-Za-z]:\\Users\\)")
#: 1980-01-01, the earliest time a zip entry can hold: the same bytes on every build.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
FOLDER = "files"
MANIFEST = "files.json"


class FilesError(ValueError):
    """A follow-along file is missing or not fit to publish."""


def sha256(data: bytes) -> str:
    """Hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def _checked(path: Path, name: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        msg = f"{name}: setup did not make it as a regular file in the work directory"
        raise FilesError(msg)
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        msg = f"{name}: {len(data):,} bytes; a follow-along file is at most {MAX_BYTES:,}"
        raise FilesError(msg)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{name}: not UTF-8 text"
        raise FilesError(msg) from exc
    if _SECRET.search(text):
        msg = f"{name}: holds something that looks like a credential"
        raise FilesError(msg)
    if _LOCAL_PATH.search(text):
        msg = f"{name}: holds a path from the machine that made it"
        raise FilesError(msg)
    return data


def readme(
    tutorial_id: str, title: str, names: list[str], commands: list[str], install: list[str]
) -> str:
    """The README.txt that travels in the zip."""
    lines = [
        f"{title}",
        "=" * len(title),
        "",
        f"The files the video tutorial '{tutorial_id}' starts from, exactly as it used them.",
        "They are synthetic examples written for pyDIGGS's tests: no project or client data.",
        "",
        "Files:",
        *(f"  {name}" for name in names),
        "",
        "Set up this folder with uv (https://docs.astral.sh/uv/); on Windows, activate with",
        r".venv\Scripts\activate instead of the third line:",
        *(f"  {step}" for step in install),
    ]
    if commands:
        lines += ["", "Then, in the folder with these files, run:", *(f"  {c}" for c in commands)]
    lines += [
        "",
        # REUSE-IgnoreStart: the README's licence tags, not this file's
        "SPDX-FileCopyrightText: 2021-2026 Xin Peng and the pyDIGGS contributors",
        "SPDX-License-Identifier: Apache-2.0",
        # REUSE-IgnoreEnd
        "",
    ]
    return "\n".join(lines)


def _zip(target: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3  # Unix, so the mode above is what unzip applies
            out.writestr(info, members[name])


def snapshot(
    tutorial_id: str,
    title: str,
    names: tuple[str, ...],
    work: Path,
    out: Path,
    *,
    commands: list[str],
    install: list[str],
) -> dict[str, Any]:
    """Copy the listed files from ``work`` into ``out/files/``; zip them; describe them.

    Returns the description that is also written to ``out/files/files.json``.

    Raises:
        FilesError: a listed file is missing or not fit to publish.
    """
    folder = out / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    members: dict[str, bytes] = {}
    listed: list[dict[str, object]] = []
    for name in names:
        data = _checked(work / name, name)
        (folder / name).write_bytes(data)
        members[name] = data
        listed.append({"name": name, "bytes": len(data), "sha256": sha256(data)})
    members["README.txt"] = readme(tutorial_id, title, list(names), commands, install).encode(
        "utf-8"
    )
    archive = folder / f"{tutorial_id}-files.zip"
    _zip(archive, members)
    zipped = archive.read_bytes()
    description: dict[str, Any] = {
        "files": listed,
        "zip": {"name": archive.name, "bytes": len(zipped), "sha256": sha256(zipped)},
        "install": install,
        "commands": commands,
    }
    (folder / MANIFEST).write_text(json.dumps(description, indent=2) + "\n", encoding="utf-8")
    return description


def install_steps(package: str, version: str) -> list[str]:
    """Set up a folder with the version the video shows, with uv; unpinned for a non-release.

    A project of its own (``uv init --bare`` writes only pyproject.toml) with the package
    added, then its environment activated: the commands then run as in the video, both the
    console script and ``python`` importing the package. No comment goes in a line: an
    interactive zsh would pass ``#`` and the rest on as arguments.
    """
    spec = f'"{package}=={version}"' if re.fullmatch(r"\d+(\.\d+)*", version) else package
    return ["uv init --bare", f"uv add {spec}", "source .venv/bin/activate"]


def published(out: Path) -> dict[str, Any] | None:
    """The description ``snapshot`` wrote under ``out``, or None when there is none."""
    manifest = out / FOLDER / MANIFEST
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None
