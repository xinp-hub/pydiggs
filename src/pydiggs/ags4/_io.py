"""The AGS4 adapter: paths, bytes and streams in, bytes out.

Everything that touches a file, a clock or an encoding is here; the tokenizer, the
tables and the writer are pure. The input rules are those of :func:`pydiggs.read` and
:func:`pydiggs.read_bytes`: a regular file only, a size limit applied before anything
is parsed, compressed input refused, and a reported name that is never a path. This
module does not import lxml.
"""

from __future__ import annotations

import codecs
import errno
import hashlib
import math
import os
import re
import stat
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Final

from pydiggs.ags4._emit import CRLF, emit
from pydiggs.ags4._parse import ENCODING_ASSUMED, RULE_1, Limits, note, parse
from pydiggs.ags4._table import File, SourceInfo
from pydiggs.errors import DocumentError, InstanceReadError
from pydiggs.findings.model import Finding

__all__ = [
    "DEFAULT_MAX_BYTES",
    "MAX_NAME_CHARS",
    "dump",
    "load",
    "load_bytes",
    "load_path",
    "save",
]

#: Largest input accepted by default: 100 MiB, as for ``pydiggs.validate``.
DEFAULT_MAX_BYTES: Final = 100 * 1024 * 1024
#: Longest ``File.source.name``, in characters.
MAX_NAME_CHARS: Final = 255

_CHUNK: Final = 64 * 1024
_BOM: Final = codecs.BOM_UTF8
#: gzip, and zip (a local file header, an empty archive, a spanned archive).
_COMPRESSED: Final = (b"\x1f\x8b", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_NAME_SEPARATORS: Final = re.compile(r"[/\\]")
#: The encodings ``encoding=`` accepts, by their Python codec name.
_ENCODINGS: Final = frozenset({"ascii", "utf-8", "utf-8-sig", "cp1252", "iso8859-1"})
_ONE_BYTE: Final = 0x100


def _check_bytes(data: bytes | bytearray | memoryview, limit: int) -> None:
    """Refuse bytes over ``limit`` or compressed, with an errno and no file name."""
    if len(data) > limit:
        raise OSError(errno.EFBIG, f"AGS4 file exceeds {limit} bytes")
    if bytes(data[:4]).startswith(_COMPRESSED):
        raise OSError(errno.EINVAL, "compressed input is not accepted")


def _read_path(path: str, limit: int) -> bytes:
    """The bytes of a regular file, at most ``limit``; a FIFO or a device is refused."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        # Ask for the file's size first: read(limit) would reserve 100 MiB for any file.
        size = info.st_size if info.st_size > 0 else limit
        data = stream.read(min(size, limit) + 1)
        if size < len(data) <= limit:  # the file grew since fstat()
            data += stream.read(limit + 1 - len(data))
    _check_bytes(data, limit)
    return data


def _read_stream(read: Callable[[int], object], limit: int) -> bytes:
    """Read a binary stream to its end, or to one byte past ``limit``."""
    buffer = bytearray()
    while len(buffer) <= limit:
        chunk = read(min(_CHUNK, limit + 1 - len(buffer)))
        if chunk is None:
            raise OSError(errno.EAGAIN, "the stream has no data available (non-blocking)")
        if isinstance(chunk, str):
            raise TypeError("expected a binary stream, got a text stream (read() returned str)")
        if not isinstance(chunk, bytes | bytearray | memoryview):
            raise TypeError(f"read() returned {type(chunk).__name__}, not bytes")
        if not chunk:
            break
        buffer += chunk
    data = bytes(buffer)
    _check_bytes(data, limit)
    return data


def _payload(data: object, limit: int, name: str | None) -> bytes:
    """Immutable bytes of ``data``, limit-checked; a buffer is copied, a stream read."""
    try:
        if isinstance(data, str):
            raise TypeError("read_bytes() takes bytes, not str; to read a file, use read(path)")
        if isinstance(data, bytes | bytearray | memoryview):
            if isinstance(data, memoryview):
                if not data.contiguous:
                    raise TypeError("a non-contiguous memoryview cannot be read; copy it first")
                data = data.cast("B")
            if len(data) > limit:  # refuse before copying an oversized buffer
                _check_bytes(data, limit)
            payload = data if type(data) is bytes else bytes(data)
            _check_bytes(payload, limit)
            return payload
        read = getattr(data, "read", None)
        if not callable(read):
            raise TypeError(
                "data must be bytes, bytearray, memoryview or a binary stream,"
                f" not {type(data).__name__}"
            )
        try:
            return _read_stream(read, limit)
        except ValueError as exc:  # a closed stream: unreadable input, not a bad argument
            raise OSError(errno.EBADF, str(exc)) from exc
    except OSError as exc:
        raise InstanceReadError(exc.errno or 0, exc.strerror or "could not be read", name) from exc


def _shown_name(name: str | None) -> str | None:
    """``name`` as ``File.source.name``: its last path component, escaped and capped.

    Splits on both ``/`` and backslash, so no folder of the sender's ever appears;
    control and format characters are escaped. Nothing is resolved or opened.
    """
    if name is None:
        return None
    if not isinstance(name, str):
        raise TypeError(f"name must be str or None, got {type(name).__name__}")
    base = _NAME_SEPARATORS.split(name)[-1]
    if base in ("", ".", ".."):
        return None
    out: list[str] = []
    length = 0
    for character in base:
        code = ord(character)
        if unicodedata.category(character) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            piece = f"\\x{code:02x}" if code < _ONE_BYTE else f"\\u{code:04x}"
        else:
            piece = character
        if length + len(piece) > MAX_NAME_CHARS:
            break
        out.append(piece)
        length += len(piece)
    return "".join(out)


def _check_arguments(encoding: str | None, max_bytes: int, time_budget: float | None) -> str | None:
    """Check the three keywords; return the codec name of ``encoding`` (None for auto)."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError(f"max_bytes must be a positive int, got {max_bytes!r}")
    if time_budget is not None and (
        isinstance(time_budget, bool)
        or not isinstance(time_budget, int | float)
        or not math.isfinite(time_budget)
        or time_budget <= 0
    ):
        raise ValueError(f"time_budget must be a positive number or None, got {time_budget!r}")
    if encoding is None:
        return None
    if not isinstance(encoding, str):
        raise ValueError(f"encoding must be str or None, got {type(encoding).__name__}")
    try:
        codec = codecs.lookup(encoding).name
    except (LookupError, ValueError):  # ValueError: a NUL in the name
        codec = ""
    if codec not in _ENCODINGS:
        raise ValueError(
            "encoding must be None or one of ASCII, UTF-8, UTF-8 with a byte order mark,"
            f" Windows-1252 and ISO-8859-1, got {encoding[:40]!r}"
        )
    return codec


def _decode(data: bytes, codec: str | None) -> tuple[str, str, list[Finding]]:
    """``data`` as text, the encoding it was read as, and the notes about that.

    With no ``codec``: UTF-8, and if the bytes are not UTF-8, Windows-1252 with an
    ``ags4.encoding-assumed`` warning. Nothing else is guessed. A UTF-8 byte order
    mark is dropped and noted under Rule 1, also when UTF-8 was asked for.
    """
    if b"\x00" in data:
        finding = note(
            RULE_1,
            "The file holds NUL bytes: it is binary or UTF-16 text, not an AGS4 file.",
        )
        raise DocumentError("not-ags4", finding.message, finding)
    marked = data.startswith(_BOM) and codec in (None, "utf-8", "utf-8-sig")
    body = data[len(_BOM) :] if marked else data
    notes = []
    if marked:
        notes.append(
            note(RULE_1, "The file starts with a UTF-8 byte order mark; it was dropped.", line=1)
        )
    if codec is not None:
        try:
            return body.decode("utf-8" if marked else codec), codec, notes
        except UnicodeDecodeError:
            raise ValueError("the file is not valid text in the encoding asked for") from None
    encoding = "utf-8-sig" if marked else "ascii" if data.isascii() else "utf-8"
    try:
        return body.decode("utf-8"), encoding, notes
    except UnicodeDecodeError:
        pass
    try:
        text = body.decode("cp1252")
        message = "The file is not valid UTF-8; it was read as Windows-1252."
    except UnicodeDecodeError:
        text = body.decode("cp1252", "replace")
        message = (
            "The file is not valid UTF-8; it was read as Windows-1252, and each byte that"
            " Windows-1252 does not define was read as U+FFFD."
        )
    return text, "cp1252", [*notes, note(ENCODING_ASSUMED, message)]


def load(
    data: bytes,
    *,
    name: str | None,
    codec: str | None,
    time_budget: float | None,
    limits: Limits | None = None,
) -> File:
    """Read checked bytes into a :class:`File` (the shared core of ``read`` and ``read_bytes``)."""
    should_stop = None
    if time_budget is not None:
        deadline = time.monotonic() + time_budget

        def should_stop() -> bool:
            return time.monotonic() > deadline

    text, encoding, notes = _decode(data, codec)
    parsed = parse(text, limits=limits, should_stop=should_stop)
    return File(
        groups=parsed.groups,
        source=SourceInfo(
            name=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data), encoding=encoding
        ),
        notes=(*notes, *parsed.notes),
        complete=parsed.complete,
        limits_hit=parsed.limits_hit,
    )


def load_path(
    path: str | os.PathLike[str],
    *,
    encoding: str | None,
    max_bytes: int,
    time_budget: float | None,
) -> File:
    """:func:`pydiggs.ags4.read` without its docstring."""
    codec = _check_arguments(encoding, max_bytes, time_budget)
    shown = os.fspath(path)
    try:
        data = _read_path(shown, max_bytes)
    except OSError as exc:
        raise InstanceReadError(exc.errno or 0, exc.strerror or "could not be read", shown) from exc
    return load(data, name=_shown_name(Path(shown).name), codec=codec, time_budget=time_budget)


def load_bytes(
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    name: str | None,
    encoding: str | None,
    max_bytes: int,
    time_budget: float | None,
) -> File:
    """:func:`pydiggs.ags4.read_bytes` without its docstring."""
    codec = _check_arguments(encoding, max_bytes, time_budget)
    shown = _shown_name(name)
    return load(_payload(data, max_bytes, shown), name=shown, codec=codec, time_budget=time_budget)


def dump(file: File) -> bytes:
    """``file`` as AGS4 bytes (UTF-8, which is ASCII for a file that follows Rule 1)."""
    try:
        return emit(file).encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("a value holds a character that cannot be written as UTF-8") from None


def save(file: File, path: str | os.PathLike[str], line_ending: str) -> None:
    """Write ``file`` to ``path``, replacing what is there."""
    if line_ending != CRLF:
        raise ValueError('line_ending must be "\\r\\n": AGS4 rows end with CR LF')
    data = dump(file)
    with Path(path).open("wb") as stream:
        stream.write(data)
