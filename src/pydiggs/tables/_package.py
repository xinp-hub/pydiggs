"""A data package as one ZIP file: unpacked in memory with limits, and packed reproducibly.

The HTTP API and the sidecar take a tables package as a ZIP of its CSV files and
``datapackage.json``, and give one back. A ZIP from a caller is untrusted: it is read in
memory, never extracted to disk, and refused when it has too many entries, unpacks to
more than allowed (counted while decompressing, not taken from the headers), or holds an
entry that is not a plain CSV or JSON file at the top of the package (an absolute path,
``..``, a backslash, a symbolic link, an encrypted entry, a nested folder). A single
folder that wraps every entry, as a ZIP of a folder has, is unwrapped; directory entries,
macOS ``__MACOSX/`` metadata and hidden files (``.DS_Store``) are skipped.
"""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from collections.abc import Iterable, Mapping
from typing import Final

__all__ = [
    "MAX_FILES",
    "PackageError",
    "digest",
    "pack",
    "unpack",
    "valid_name",
]

#: Most files a package may hold: the profile's tables, a result table per test type,
#: the descriptor and the plan fit many times over.
MAX_FILES: Final = 256
#: Most entries of any kind a ZIP may list (folders and skipped metadata included).
_MAX_ENTRIES: Final = 4 * MAX_FILES
#: Longest file name accepted.
_MAX_NAME_CHARS: Final = 255
#: The file kinds a package holds.
_SUFFIXES: Final = (".csv", ".json")
#: A fixed timestamp, so the same files always give the same ZIP bytes.
_EPOCH: Final = (1980, 1, 1, 0, 0, 0)
_CHUNK: Final = 1 << 16


class PackageError(ValueError):
    """The ZIP cannot be used as a package. ``kind`` says why; the text names no entry.

    ``kind`` is ``"not-zip"`` (the bytes are not a ZIP file), ``"unsafe-package"`` (an
    entry is refused) or ``"too-large"`` (too many files, or too many bytes unpacked).
    """

    def __init__(self, kind: str, text: str) -> None:
        super().__init__(text)
        self.kind = kind


def valid_name(name: str) -> bool:
    """Whether ``name`` is a plain CSV or JSON file name, as a package may hold.

    Examples:
        >>> valid_name("features.csv"), valid_name("datapackage.json")
        (True, True)
        >>> valid_name("../features.csv"), valid_name("a/b.csv"), valid_name("run.exe")
        (False, False, False)
    """
    return (
        0 < len(name) <= _MAX_NAME_CHARS
        and name.lower().endswith(_SUFFIXES)
        and not name.startswith(".")
        and not any(ch in name for ch in "/\\:\x00")
        and name.isprintable()
    )


def _skipped(name: str) -> bool:
    """Directory entries, macOS metadata and hidden files: never part of a package."""
    parts = name.split("/")
    return name.endswith("/") or parts[0] == "__MACOSX" or parts[-1].startswith(".")


def _wrapper(names: Iterable[str]) -> str:
    """The one folder that wraps every entry (``"pkg/"``), or ``""`` when there is none."""
    tops = {name.split("/", 1)[0] for name in names}
    nested = all("/" in name for name in names)
    if len(tops) != 1 or not nested:
        return ""
    top = next(iter(tops))
    plain = top not in ("", ".", "..") and top.isprintable()
    return f"{top}/" if plain and not any(ch in top for ch in "\\:\x00") else ""


def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


#: The end of central directory record: its signature, and the longest comment after it.
_EOCD: Final = b"PK\x05\x06"
_EOCD_SEARCH: Final = 22 + 0xFFFF


def _listed_entries(content: bytes) -> int:
    """How many entries the ZIP's last end-of-central-directory record says it has.

    Read from the record alone, so a ZIP that lists a million entries is refused before
    a header is parsed. A ZIP64 count (``0xFFFF``) is taken as more than any package
    holds; bytes with no record are left to :mod:`zipfile` to refuse.

    Examples:
        >>> _listed_entries(pack([("a.csv", b""), ("b.csv", b"")]))
        2
    """
    at = content.rfind(_EOCD, max(0, len(content) - _EOCD_SEARCH))
    if at == -1 or at + 22 > len(content):
        return 0
    return int.from_bytes(content[at + 10 : at + 12], "little")


def unpack(content: bytes, *, max_unpacked_bytes: int) -> dict[str, bytes]:
    r"""The files of a package ZIP, by name, read in memory within the limits.

    Args:
        content: The ZIP file's bytes.
        max_unpacked_bytes: Most bytes all files may unpack to together (and so each).

    Returns:
        File name -> bytes, for every CSV and JSON file of the package.

    Raises:
        PackageError: The bytes are not a ZIP, an entry is refused, or a limit is passed.

    Examples:
        >>> archive = pack([("features.csv", b"key,depth_uom\nBH-1,m\n")])
        >>> unpack(archive, max_unpacked_bytes=1024)
        {'features.csv': b'key,depth_uom\nBH-1,m\n'}
        >>> unpack(b"not a zip", max_unpacked_bytes=1024)
        Traceback (most recent call last):
        ...
        pydiggs.tables._package.PackageError: The body is not a ZIP file.
    """
    if _listed_entries(content) > _MAX_ENTRIES:  # before zipfile reads every header
        raise PackageError("too-large", f"The package has more than {MAX_FILES} files.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, ValueError, OSError, EOFError):
        raise PackageError("not-zip", "The body is not a ZIP file.") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > _MAX_ENTRIES:
            raise PackageError("too-large", f"The package has more than {MAX_FILES} files.")
        kept = [info for info in infos if not _skipped(info.filename)]
        prefix = _wrapper([info.filename for info in kept])
        files: dict[str, bytes] = {}
        total = 0
        for info in kept:
            name = info.filename.removeprefix(prefix)
            if (
                not valid_name(name)
                or _is_link(info)
                or info.flag_bits & 0x1  # encrypted
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or name in files
            ):
                raise PackageError(
                    "unsafe-package",
                    "The package holds an entry that is not a plain CSV or JSON file at its"
                    " top level (or one that is linked, encrypted or listed twice).",
                )
            if len(files) >= MAX_FILES:
                raise PackageError("too-large", f"The package has more than {MAX_FILES} files.")
            total, data = _read(archive, info, total, max_unpacked_bytes)
            files[name] = data
    return files


def _read(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, total: int, limit: int
) -> tuple[int, bytes]:
    """One entry's bytes, counted as they are decompressed against what is left of ``limit``."""
    chunks: list[bytes] = []
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise PackageError(
                        "too-large", "The package unpacks to more bytes than allowed."
                    )
                chunks.append(chunk)
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, ValueError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError("not-zip", "The body is not a ZIP file that can be read.") from None
    return total, b"".join(chunks)


def pack(files: Iterable[tuple[str, bytes]]) -> bytes:
    r"""A ZIP of ``files`` in the order given: deflated, fixed timestamps, the same bytes each time.

    Examples:
        >>> pack([("a.csv", b"x\n")]) == pack([("a.csv", b"x\n")])
        True
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, on every platform: the same bytes everywhere
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def digest(files: Mapping[str, bytes]) -> str:
    """One sha256 for a package: of each file's name and sha256, in name order.

    The same files give the same digest whether they came as a folder or in a ZIP.

    Examples:
        >>> len(digest({"a.csv": b"x"})), digest({"a.csv": b"x"}) == digest({"a.csv": b"y"})
        (64, False)
    """
    listing = "".join(
        f"{name}\0{hashlib.sha256(data).hexdigest()}\n" for name, data in sorted(files.items())
    )
    return hashlib.sha256(listing.encode("utf-8", "surrogateescape")).hexdigest()
