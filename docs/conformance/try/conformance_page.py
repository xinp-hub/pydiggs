"""The Python side of the in-browser conformance page (``docs/conformance/try/``).

``worker.mjs`` installs the pinned pyDIGGS wheel into Pyodide, writes this file into
Pyodide's in-memory file system and imports it. Each function below is one step of the
page and does what the matching ``pydiggs conformance`` command does, through the public
:mod:`pydiggs.conformance` API only, so the page needs nothing newer than the wheel it
pins. ``tests/test_conformance_page.py`` runs every function under CPython.

Every function returns one JSON text: ``{"ok": true, ...}`` or ``{"ok": false, "error":
"..."}``. JSON text, never a proxy of a Python object, is what crosses into JavaScript.
The files a visitor gives are untrusted: their sizes are limited, a ZIP is read member by
member within a limit, and a file name only ever becomes the last part of a path in a
private temporary folder. Nothing here opens a network connection.
"""

from __future__ import annotations

import base64
import importlib
import json
import re
import tempfile
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pydiggs
from pydiggs import conformance
from pydiggs.errors import PydiggsError

__all__ = ["MAX_BYTES", "catalogue", "grade", "make", "reference", "verify"]

#: The largest file the page takes: the limit of pyDIGGS's other browser engines.
MAX_BYTES: Final = 25 * 1024 * 1024
#: Seconds for reading, and again for validating, an export. A page that is not
#: cross-origin isolated cannot interrupt Python, so pyDIGGS stops itself instead.
TIME_BUDGET: Final = 120.0
_MAX_MANIFEST: Final = 64 * 1024
_MAX_EVIDENCE: Final = 8 * 1024 * 1024
_MAX_ZIP_MEMBERS: Final = 200
_MANIFEST: Final = "challenge.json"
_ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)
_NAME: Final = re.compile(r"[^A-Za-z0-9._ -]")


class _RefusedError(Exception):
    """A file the page cannot use; the message is for the visitor."""


def _reply(**members: Any) -> str:
    return json.dumps({"ok": True, **members}, ensure_ascii=False)


def _refuse(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _bytes(data: Any) -> bytes:
    """``data`` as bytes. A JavaScript ``Uint8Array`` arrives in Pyodide as a ``JsProxy``."""
    if isinstance(data, bytes | bytearray | memoryview):
        return bytes(data)
    try:
        ffi: ModuleType | None = importlib.import_module("pyodide.ffi")  # only in Pyodide
    except ImportError:
        ffi = None
    proxy = getattr(ffi, "JsProxy", None)
    if isinstance(proxy, type) and isinstance(data, proxy):
        typed_array: Any = data  # a JsProxy has to_bytes(); typeshed does not know it
        return bytes(typed_array.to_bytes())
    raise _RefusedError("expected the bytes of a file")


def _safe_name(name: str, fallback: str) -> str:
    """The last part of ``name``, with characters a file system may refuse replaced."""
    last = re.split(r"[\\/]", name or "")[-1].strip()
    cleaned = _NAME.sub("_", last)[:120].strip(" .")
    return cleaned or fallback


def catalogue() -> str:
    """The classes a challenge can be made for, and the parts of DIGGS not yet testable.

    Examples:
        >>> import json
        >>> listing = json.loads(catalogue())
        >>> listing["classes"][0]["id"], listing["suite_version"] == conformance.SUITE_VERSION
        ('spt-export', True)
    """
    return _reply(
        suite_version=conformance.SUITE_VERSION,
        pydiggs=pydiggs.__version__,
        statement=conformance.STATEMENT,
        classes=[
            {"id": c.id, "title": c.title, "direction": c.direction, "family": c.family}
            for c in conformance.classes()
        ],
        untestable=[f.title for f in conformance.families() if not f.testable and not f.embedded],
    )


def _zip(folder: str, files: dict[str, bytes]) -> bytes:
    """The files in one folder of a ZIP, written the same way every time."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(f"{folder}/{name}", date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()


def make(class_id: str, product: str, version: str, seed: str = "") -> str:
    """``pydiggs conformance new``: the challenge, as a ZIP of its folder (base64).

    The folder in the ZIP is the one ``pydiggs conformance new`` writes, so the
    command line can grade or verify it too. An empty ``seed`` draws a fresh one.

    Examples:
        >>> import json
        >>> made = json.loads(make("spt-export", "MyLog", "4.2.1", "demo"))
        >>> made["ok"], made["folder"], made["direction"]
        (True, 'spt-export-demo', 'export')
    """
    try:
        challenge = conformance.new(class_id, product=product, version=version, seed=seed or None)
    except ValueError as exc:
        return _refuse(str(exc))
    folder = f"{challenge.info.id}-{challenge.seed}"
    return _reply(
        class_id=challenge.info.id,
        title=challenge.info.title,
        direction=challenge.info.direction,
        seed=challenge.seed,
        token=challenge.token,
        id=challenge.id,
        folder=folder,
        files=sorted(challenge.files),
        manifest=challenge.files[_MANIFEST].decode("utf-8"),
        readme=challenge.files["README.md"].decode("utf-8"),
        zip=base64.b64encode(_zip(folder, dict(challenge.files))).decode("ascii"),
    )


def _manifest(given: Any) -> bytes:
    """``challenge.json`` from the bytes of that file, or of a challenge ZIP."""
    data = _bytes(given)
    if not data.startswith(b"PK"):
        if len(data) > _MAX_MANIFEST:
            raise _RefusedError("that is not a challenge: challenge.json is at most 64 KiB")
        return data
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > _MAX_ZIP_MEMBERS:
                raise _RefusedError("the ZIP holds too many files to be a challenge")
            found = [
                m
                for m in members
                if not m.is_dir() and m.filename.replace("\\", "/").split("/")[-1] == _MANIFEST
            ]
            if len(found) != 1:
                raise _RefusedError("the ZIP must hold exactly one challenge.json")
            if found[0].file_size > _MAX_MANIFEST:
                raise _RefusedError("the challenge.json in the ZIP is too large")
            with archive.open(found[0]) as member:
                manifest = member.read(_MAX_MANIFEST + 1)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
        raise _RefusedError(f"the ZIP cannot be read: {exc}") from exc
    if len(manifest) > _MAX_MANIFEST:
        raise _RefusedError("the challenge.json in the ZIP is too large")
    return manifest


def _load(challenge: bytes, scratch: Path) -> conformance.Challenge:
    """The challenge the manifest describes, rebuilt from its seed by ``conformance.load``.

    A pairwise challenge (``new --from-export``) cannot be rebuilt from its manifest: it
    needs the other product's export beside it. The page does not take one.
    """
    manifest = _manifest(challenge)
    try:
        described = json.loads(manifest)
    except (ValueError, RecursionError):
        described = None  # conformance.load says what is wrong with it
    if isinstance(described, dict) and "source_export" in described:
        raise _RefusedError(
            "this is a pairwise challenge, made from another product's export; this page"
            " does not grade or verify one: use pydiggs conformance grade (or verify) on"
            " the command line, on the challenge folder"
        )
    folder = scratch / "challenge"
    folder.mkdir()
    (folder / _MANIFEST).write_bytes(manifest)
    return conformance.load(folder)


def _submission(given: Any, name: str, scratch: Path) -> Path:
    data = _bytes(given)
    if len(data) > MAX_BYTES:
        raise _RefusedError(f"the file is larger than {MAX_BYTES} bytes")
    folder = scratch / "submission"
    folder.mkdir()
    path = folder / _safe_name(name, "submission")
    path.write_bytes(data)
    return path


def _run(task: Callable[[Path], str]) -> str:
    """``task`` in a private temporary folder; a refusal becomes a reply.

    The folder's path is taken out of a message: the visitor never named it.
    """
    with tempfile.TemporaryDirectory() as scratch:
        try:
            return task(Path(scratch))
        except (_RefusedError, PydiggsError) as exc:
            message = str(exc).replace(str(Path(scratch) / "challenge" / _MANIFEST), _MANIFEST)
            return _refuse(message.replace(str(Path(scratch)), "."))


def reference(challenge: bytes) -> str:
    """The known-good DIGGS file of a challenge (``Challenge.reference()``), as base64.

    It is pyDIGGS's own export of the challenge's data: a way to try the page.
    """

    def task(scratch: Path) -> str:
        loaded = _load(challenge, scratch)
        return _reply(
            name=f"{loaded.info.id}-{loaded.seed}-pydiggs.xml",
            data=base64.b64encode(loaded.reference()).decode("ascii"),
        )

    return _run(task)


def grade(challenge: bytes, submission: bytes, name: str) -> str:
    """``pydiggs conformance grade``: the verdict, ``evidence.json`` and ``report.html``.

    ``challenge`` is the bytes of ``challenge.json`` or of the challenge's ZIP;
    ``submission`` is the exported DIGGS file, or the filled ``answers.csv`` of an import
    class, and ``name`` its file name (the evidence records it).
    """

    def task(scratch: Path) -> str:
        result = conformance.grade(
            _load(challenge, scratch),
            _submission(submission, name, scratch),
            max_bytes=MAX_BYTES,
            time_budget=TIME_BUDGET,
        )
        return _reply(
            passed=result.passed,
            evidence=result.to_dict(),
            evidence_json=result.to_json() + "\n",
            report_html=result.to_html(),
        )

    return _run(task)


def verify(evidence: bytes, challenge: bytes, submission: bytes, name: str) -> str:
    """``pydiggs conformance verify``: grade again, and compare with ``evidence``."""

    def task(scratch: Path) -> str:
        raw = _bytes(evidence)
        if len(raw) > _MAX_EVIDENCE:
            raise _RefusedError("that file is too large to be conformance evidence")
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError) as exc:
            raise _RefusedError("the evidence is not JSON") from exc
        if not isinstance(document, dict):
            raise _RefusedError("the evidence is not a diggs-conformance document")
        outcome = conformance.verify(
            document,
            _load(challenge, scratch),
            _submission(submission, name, scratch),
            max_bytes=MAX_BYTES,
            time_budget=TIME_BUDGET,
        )
        return _reply(
            reproduced=outcome.ok,
            differences=list(outcome.differences),
            notes=list(outcome.notes),
            verdict="pass" if outcome.result.passed else "fail",
            class_id=outcome.result.info.id,
        )

    return _run(task)
