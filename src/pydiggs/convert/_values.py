"""AGS4 values as DIGGS values: numbers, dates and text, read with a strict grammar. Pure.

Nothing here evaluates anything: a number is matched by a regular expression on bounded
text before :func:`float` sees it, a date by its pattern, and text is checked for what
XML 1.0 cannot hold. Each reader returns the DIGGS text, or None when the value does not
parse, so the caller can report it.
"""

from __future__ import annotations

import datetime
import math
import re
from typing import Final

__all__ = [
    "MAX_NUMBER_CHARS",
    "IdMaker",
    "parse_date",
    "parse_degrees",
    "parse_number",
    "xml_text",
]

#: Longest number text accepted (longer text is not a number here).
MAX_NUMBER_CHARS: Final = 64
_NUMBER: Final = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?", re.ASCII)
_ISO_DATE: Final = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)
_ISO_TIME: Final = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d{1,9})?)?", re.ASCII
)
_ZONE: Final = re.compile(r"Z|([+-])(\d{2}):(\d{2})", re.ASCII)
_DMY: Final = re.compile(r"(\d{2})/(\d{2})/(\d{4})", re.ASCII)
_DMS: Final = re.compile(r"(\d{1,3}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)([NSEW])", re.ASCII)
#: Code points XML 1.0 forbids in text: the C0 controls but tab, LF and CR; the
#: surrogates; U+FFFE and U+FFFF. Listed one by one (no character ranges), for
#: :meth:`str.translate`, which deletes each.
_FORBIDDEN: Final[dict[int, None]] = dict.fromkeys(
    [
        *(code for code in range(0x20) if code not in (0x09, 0x0A, 0x0D)),
        *range(0xD800, 0xE000),
        0xFFFE,
        0xFFFF,
    ]
)
_NOT_NAME: Final = re.compile(r"[^\w.\-]", re.ASCII)
_MAX_ID_CHARS: Final = 64
_HOURS: Final = 24
_MINUTES: Final = 60
#: The widest zone offset ``xs:dateTime`` allows, in minutes (14:00).
_MAX_ZONE_MINUTES: Final = 14 * 60


def parse_number(text: str) -> float | None:
    """``text`` as a finite number, or None when it is not one in plain decimal form.

    Examples:
        >>> parse_number("12.50"), parse_number("-1e3"), parse_number(".5")
        (12.5, -1000.0, 0.5)
        >>> parse_number("NP"), parse_number("<0.1"), parse_number("1,5"), parse_number("")
        (None, None, None, None)
    """
    if len(text) > MAX_NUMBER_CHARS or not _NUMBER.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _valid(year: str, month: str, day: str) -> bool:
    """True for a real calendar day (leap years counted) of a year after 0."""
    try:
        datetime.date(int(year), int(month), int(day))
    except ValueError:
        return False
    return True


def _zone(text: str) -> bool:
    """True for no zone, ``Z``, or an offset ``xs:dateTime`` allows (up to 14:00)."""
    if text == "":
        return True
    match = _ZONE.fullmatch(text)
    if match is None:
        return False
    if text == "Z":
        return True
    _sign, hours, minutes = match.groups()
    return int(minutes) < _MINUTES and int(hours) * 60 + int(minutes) <= _MAX_ZONE_MINUTES


def parse_date(text: str, unit: str = "") -> str | None:
    """An AGS4 date or date-time as an ISO 8601 ``xs:date`` or ``xs:dateTime``, or None.

    ``yyyy-mm-dd`` and ``yyyy-mm-ddThh:mm[:ss]`` (with an optional zone) are read; a
    time without seconds gets ``:00``, which the caller reports as reformatted. When the
    heading's UNIT row says ``dd/mm/yyyy``, that form is read too. Nothing else is.

    Examples:
        >>> parse_date("2026-03-04"), parse_date("2026-03-04T10:15")
        ('2026-03-04', '2026-03-04T10:15:00')
        >>> parse_date("04/03/2026", "dd/mm/yyyy"), parse_date("04/03/2026")
        ('2026-03-04', None)
        >>> parse_date("2026-13-01"), parse_date("2026-02-30"), parse_date("yesterday")
        (None, None, None)
        >>> parse_date("2024-02-29T23:59+14:00"), parse_date("2026-03-04T10:15+15:00")
        ('2024-02-29T23:59:00+14:00', None)
    """
    match = _ISO_DATE.fullmatch(text)
    if match is not None:
        return text if _valid(*match.groups()) else None
    match = _ISO_TIME.match(text)
    if match is not None and _zone(text[match.end() :]):
        year, month, day, hour, minute, second, fraction = match.groups()
        if not _valid(year, month, day) or int(hour) >= _HOURS or int(minute) >= _MINUTES:
            return None
        if second is not None and int(second) >= _MINUTES:
            return None
        zone = text[match.end() :]
        clock = f"{hour}:{minute}:{second or '00'}{fraction or ''}"
        return f"{year}-{month}-{day}T{clock}{zone}"
    match = _DMY.fullmatch(text)
    if match is not None and unit.strip().lower() == "dd/mm/yyyy":
        day, month, year = match.groups()
        return f"{year}-{month}-{day}" if _valid(year, month, day) else None
    return None


def parse_degrees(text: str) -> float | None:
    """A latitude or longitude: decimal degrees, or ``dd:mm:ss.ss`` and a hemisphere letter.

    Examples:
        >>> parse_degrees("52.5"), parse_degrees("52:30:00.0N"), parse_degrees("1:15:00W")
        (52.5, 52.5, -1.25)
        >>> parse_degrees("52 30 N")
    """
    number = parse_number(text)
    if number is not None:
        return number
    match = _DMS.fullmatch(text)
    if match is None:
        return None
    degrees, minutes, seconds, hemisphere = match.groups()
    if int(minutes) >= _MINUTES or float(seconds) >= _MINUTES:
        return None
    value = int(degrees) + int(minutes) / 60 + float(seconds) / 3600
    return -value if hemisphere in "SW" else value


def xml_text(text: str) -> str:
    r"""``text`` without the characters XML 1.0 forbids, and without surrounding white space.

    Examples:
        >>> xml_text(" Soft\x01 CLAY ")
        'Soft CLAY'
    """
    return text.translate(_FORBIDDEN).strip()


class IdMaker:
    """``gml:id`` values made from AGS4 keys: valid XML ids, unique, the same every run.

    Characters an XML id cannot hold become ``_``; an id that would start with a digit,
    ``.`` or ``-`` gets a ``_`` in front; a taken id gets ``-2``, ``-3`` … .

    Examples:
        >>> ids = IdMaker()
        >>> ids.make("BH 01"), ids.make("BH 01"), ids.make("1"), ids.make("")
        ('BH_01', 'BH_01-2', '_1', '_')
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()
        self._next: dict[str, int] = {}

    def make(self, text: str) -> str:
        """A free id made from ``text``."""
        base = _NOT_NAME.sub("_", text)[:_MAX_ID_CHARS] or "_"
        if not (base[0].isalpha() or base[0] == "_"):
            base = f"_{base}"
        candidate = base
        count = self._next.get(base, 1)
        while candidate in self._taken:
            count += 1
            candidate = f"{base}-{count}"
        self._next[base] = count
        self._taken.add(candidate)
        return candidate
