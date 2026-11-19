r"""Writing a command's report to stdout or to a file, never losing it to an encoding.

``pydiggs validate``, ``pydiggs convert`` and ``pydiggs diff`` write a report built from
file text, which can hold any character, to a stream that may be ASCII or cp1252 (a
redirected stdout on Windows), or UTF-8 faced with a lone surrogate (a file name that is
not valid UTF-8). What the stream can encode is written as it is; anything else is
escaped: JSON's own ``\uXXXX`` in a JSON report, which is then plain ASCII and the same
document, and a backslash escape (``φ``) in a text report. A report that cannot be
written at all is one line for stderr, never a traceback. ``rich`` is not imported.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from typing import Final, TextIO

__all__ = ["encodable", "silence_stdout", "write_report"]

_NOT_ASCII: Final = re.compile(r"[^\x00-\x7f]")
_BMP_END: Final = 0x10000


def _json_escape(match: re.Match[str]) -> str:
    """One character as JSON writes it in ASCII (two escapes above the basic plane)."""
    code = ord(match[0])
    if code < _BMP_END:
        return f"\\u{code:04x}"
    code -= _BMP_END
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def encodable(body: str, *, json_report: bool, encoding: str) -> str:
    r"""``body`` as it is when ``encoding`` can write it, else with escapes in place.

    A JSON report escapes every character outside ASCII the way JSON does: outside ASCII
    a JSON document has only the characters inside its strings, so it is the same
    document, readable in any encoding. A text report keeps what the stream can write
    and spells out the rest (``φ``). An encoding this Python does not know is the
    stream's to deal with.
    """
    try:
        body.encode(encoding)
    except LookupError:
        return body
    except UnicodeError:
        pass
    else:
        return body
    if json_report:
        return _NOT_ASCII.sub(_json_escape, body)
    return body.encode(encoding, "backslashreplace").decode(encoding)


def write_report(
    body: str,
    *,
    json_report: bool,
    output: str | None = None,
    open_output: Callable[[str], TextIO] | None = None,
    what: str = "report",
) -> str | None:
    """Write ``body`` to ``output`` (UTF-8, opened by ``open_output``) or to stdout.

    Returns None, or what went wrong as one line for stderr: the command then exits 2,
    never with a traceback, and never with a status that would say something about a
    report nobody received. ``what`` names the report in that line.
    """
    try:
        if output:
            if open_output is None:  # pragma: no cover - every caller passes one
                raise ValueError("no way to open the output")
            with open_output(output) as stream:
                stream.write(encodable(body, json_report=json_report, encoding="utf-8"))
            return None
        stdout = sys.stdout
        if stdout is None:  # started with no standard output at all
            return f"cannot write the {what}: there is no standard output"
        encoding = getattr(stdout, "encoding", None) or "utf-8"
        stdout.write(encodable(body, json_report=json_report, encoding=encoding))
        stdout.flush()
    except (OSError, ValueError) as exc:  # ValueError: a closed stream, an encoding
        if output:
            return f"cannot write {output}: {getattr(exc, 'strerror', None) or exc}"
        silence_stdout()
        return f"cannot write the {what}: {getattr(exc, 'strerror', None) or exc}"
    return None


def silence_stdout() -> None:
    """Point stdout at the null device after a failed write.

    Text may still sit in the stream's buffer. Python flushes it again at exit, and a
    second failure there would replace the command's exit status with a traceback.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass  # stdout has no file descriptor (it was replaced by an in-memory stream)
