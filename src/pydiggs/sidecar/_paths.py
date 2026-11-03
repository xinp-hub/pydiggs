"""The sidecar's path policy: ``--allow-path`` roots and open-then-verify reads.

By-path validation is off unless the host passes ``--allow-path DIR`` at launch.
Roots are canonicalised once; a filesystem root, a drive or share root, the home
folder or one of its ancestors is refused. A request path must be a plain absolute
path (no URI, no ``..``, no UNC or device name) that, as spelled, lies under a root
(in its given or canonical spelling); anything else is refused without being
opened. The file is then **opened**, without following a final symlink, and the
opened descriptor's real location is checked against the canonical roots, so a
symlink or a swap between check and use cannot escape. Every refusal looks the same
to the caller (``path-not-permitted``), whether the file is outside the roots,
missing or not a regular file.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

__all__ = [
    "NotPermittedError",
    "canonical_roots",
    "open_verified",
    "root_spellings",
    "vet_request_path",
]

#: Windows reserved device names (any extension, any case), refused in every component.
_DEVICE_RE: Final = re.compile(
    r"(con|prn|aux|nul|conin\$|conout\$|clock\$|(com|lpt)[0-9\u00b9\u00b2\u00b3])(\..*)?",
    re.IGNORECASE,
)
#: A URI scheme: two or more characters before ``:`` (``C:`` is a drive, not a scheme).
_SCHEME_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]+:")

#: The platform, as a plain string (checks on it are not narrowed away by mypy).
_PLATFORM: Final[str] = sys.platform
_WINDOWS: Final = _PLATFORM == "win32"
#: macOS ``fcntl`` command returning a descriptor's path, and its buffer size.
_F_GETPATH: Final = 50
_MAXPATHLEN: Final = 1024


class NotPermittedError(Exception):
    """The path is refused. Deliberately carries no detail for the caller."""


def _is_root_like(path: Path) -> bool:
    """A filesystem, drive or UNC share root (the path is its own anchor)."""
    return path == Path(path.anchor) or str(path) == path.anchor


def canonical_roots(raw: tuple[str, ...], *, home: Path | None = None) -> tuple[Path, ...]:
    """Canonicalise ``--allow-path`` roots once, refusing dangerous ones.

    Args:
        raw: The roots exactly as given at launch.
        home: The home folder to protect (default: the current user's).

    Returns:
        The canonical roots, duplicates removed, in the given order.

    Raises:
        ValueError: A root is missing, not a directory, a filesystem/drive/share
            root, or the home folder or one of its ancestors. The message names the
            root as given (it comes from the host, not a remote caller).

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     roots = canonical_roots((d, d))
        >>> len(roots)
        1
        >>> canonical_roots(("/",))
        Traceback (most recent call last):
        ...
        ValueError: --allow-path /: a filesystem root is not allowed
    """
    if home is None:
        try:
            home = Path(_real_dir(str(Path.home())))
        except (OSError, RuntimeError, KeyError):
            home = None
    roots: list[Path] = []
    for given in raw:
        try:
            root = Path(_real_dir(given))
        except OSError:
            raise ValueError(f"--allow-path {given}: not an existing directory") from None
        if _is_root_like(root):
            raise ValueError(f"--allow-path {given}: a filesystem root is not allowed")
        if home is not None and _within(home, root):
            raise ValueError(
                f"--allow-path {given}: the home folder or one of its ancestors is not allowed"
            )
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def root_spellings(raw: tuple[str, ...], roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Every spelling a request path may start with: canonical and as given.

    On macOS ``--allow-path /tmp`` canonicalises to ``/private/tmp``; a host may
    spell request paths either way. Given roots are made absolute but not resolved.

    Args:
        raw: The roots exactly as given at launch.
        roots: Their canonical forms, from :func:`canonical_roots`.

    Returns:
        The canonical roots, then each given root not already among them.

    Example:
        >>> from pathlib import Path
        >>> canonical = Path("/private/tmp")
        >>> spellings = root_spellings(("/tmp", "/private/tmp"), (canonical,))
        >>> spellings[0] == canonical, len(spellings)
        (True, 2)
    """
    spellings = list(roots)
    for given in raw:
        spelled = Path(os.path.abspath(given))  # noqa: PTH100  # lexical: resolve() would canonicalise
        if spelled not in spellings:
            spellings.append(spelled)
    return tuple(spellings)


def _real_dir(given: str) -> str:
    """The real location of an existing directory, spelled as the OS reports it."""
    if _WINDOWS:  # pragma: no cover - Windows only
        real = os.path.realpath(given, strict=True)
        if not Path(real).is_dir():
            raise NotADirectoryError(given)
        return real
    fd = os.open(given, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC)
    try:
        return _fd_path(fd, given)
    finally:
        os.close(fd)


def _within(path: Path, root: Path) -> bool:
    """``path`` is ``root`` or inside it, compared case-normalised for the OS."""
    return Path(os.path.normcase(path)).is_relative_to(Path(os.path.normcase(root)))


def vet_request_path(path: str) -> str:
    r"""Refuse every path form that is not a plain absolute local path.

    Refused: NUL characters, any URI scheme (``file:`` included), relative paths,
    ``..`` components, UNC and ``\\?\`` / ``\\.\`` prefixes, Windows device
    names in any component (on every OS, so behaviour does not depend on the host).

    Args:
        path: The caller's ``path`` parameter.

    Returns:
        The same string.

    Raises:
        NotPermittedError: For any refused form.

    Example:
        >>> vet_request_path("/data/BH-01.xml")
        '/data/BH-01.xml'
        >>> vet_request_path("file:///data/BH-01.xml")
        Traceback (most recent call last):
        ...
        pydiggs.sidecar._paths.NotPermittedError
    """
    if "\x00" in path or _SCHEME_RE.match(path):
        raise NotPermittedError
    if path.startswith(("\\\\", "//", "\\/", "/\\")):  # UNC, \\?\, \\.\, POSIX //
        raise NotPermittedError
    pure = PureWindowsPath(path) if _WINDOWS else PurePosixPath(path)
    if not pure.is_absolute():
        raise NotPermittedError
    parts = re.split(r"[\\/]", path)
    if any(p == ".." for p in parts) or any(_DEVICE_RE.fullmatch(p.rstrip(" .")) for p in parts):
        raise NotPermittedError
    return path


_OPEN_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOINHERIT", 0)
)


def open_verified(path: str, roots: tuple[Path, ...], *, spellings: tuple[Path, ...] = ()) -> int:
    """Open ``path`` read-only, then prove the opened file is a regular file in a root.

    A path that, as spelled, is not under one of ``roots`` or ``spellings`` is
    refused before anything is opened, so no outside file is ever touched. POSIX
    opens with ``O_NOFOLLOW | O_NONBLOCK`` (a FIFO cannot block the open), then
    checks ``fstat`` and the descriptor's own path: ``/proc/self/fd`` on Linux,
    ``F_GETPATH`` on macOS, and elsewhere the resolved path, accepted only if it is
    the same file (device and inode) as the descriptor. Windows asks the opened
    handle for its final path. Never a string prefix test.

    Args:
        path: A path already passed by :func:`vet_request_path`.
        roots: From :func:`canonical_roots`; the opened file must be in one.
        spellings: More accepted spellings of the roots for the check before
            opening (from :func:`root_spellings`); ``roots`` are always accepted.

    Returns:
        An open descriptor the caller must close.

    Raises:
        NotPermittedError: Missing, unreadable, not regular, or outside every root.
    """
    if not roots:
        raise NotPermittedError
    spelled = Path(path)
    if not any(_within(spelled, root) for root in (*roots, *spellings)):
        raise NotPermittedError
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError):
        raise NotPermittedError from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotPermittedError
        real = Path(_fd_path(fd, path))
        if not any(_within(real, root) for root in roots):
            raise NotPermittedError
        if not _WINDOWS:
            os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _fd_path(fd: int, given: str) -> str:
    """The real path of an open descriptor.

    Raises:
        NotPermittedError: The path cannot be established.
    """
    try:
        if _PLATFORM == "win32":  # pragma: no cover - Windows only
            return _windows_final_path(fd)
        if _PLATFORM.startswith("linux"):  # pragma: no cover - Linux only
            return str(Path(f"/proc/self/fd/{fd}").readlink())
        if _PLATFORM == "darwin":  # pragma: no cover - macOS only
            import fcntl  # noqa: PLC0415  # POSIX only

            raw = fcntl.fcntl(fd, _F_GETPATH, bytes(_MAXPATHLEN))
            return os.fsdecode(raw.split(b"\x00", 1)[0])
        return _same_file_path(fd, given)  # pragma: no cover - other POSIX
    except OSError:
        raise NotPermittedError from None


def _same_file_path(fd: int, given: str) -> str:
    """Resolve ``given`` and accept it only if it names the descriptor's own file."""
    real = os.path.realpath(given, strict=True)
    opened, resolved = os.fstat(fd), Path(real).stat()
    if (opened.st_dev, opened.st_ino) != (resolved.st_dev, resolved.st_ino):
        raise NotPermittedError
    return real


def _windows_final_path(fd: int) -> str:  # pragma: no cover - Windows only
    r"""``GetFinalPathNameByHandleW`` for the descriptor's handle, without ``\\?\``."""
    import ctypes  # noqa: PLC0415  # Windows only
    import msvcrt  # noqa: PLC0415  # Windows only
    from ctypes import wintypes  # noqa: PLC0415  # Windows only

    handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined,unused-ignore]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    get_path = kernel32.GetFinalPathNameByHandleW
    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_path.restype = wintypes.DWORD
    size = 32_768
    buffer = ctypes.create_unicode_buffer(size)
    length = get_path(handle, buffer, size, 0)  # FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
    if not 0 < length < size:
        raise OSError("GetFinalPathNameByHandleW failed")
    final = buffer.value
    if final.startswith("\\\\?\\UNC\\"):
        return "\\\\" + final[8:]
    if final.startswith("\\\\?\\"):
        return final[4:]
    return final
