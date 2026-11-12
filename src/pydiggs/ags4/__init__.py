"""AGS4 files as tables of strings: read, build and write them.

AGS4 is the quoted, comma-separated exchange format for ground-investigation data
published by the Association of Geotechnical and Geoenvironmental Specialists. This
module reads a file into a :class:`File` of :class:`Group` tables, keeping every value
as the text the file holds, and writes a :class:`File` back the same way every time.

Reading is lenient: a file that breaks the AGS4 rules still reads, and each thing the
reader had to work around is a note in ``File.notes``. Every file is treated as
untrusted: its size, its number of groups, headings, rows and cells, the length of a
value and the time spent are all limited, nothing it names is opened, and nothing is
fetched from the network.

pyDIGGS implements the published AGS4 rules independently and bundles no AGS data
dictionary. It is not an AGS-approved tool.

Examples:
    >>> from pydiggs import ags4
    >>> f = ags4.read("tests/fixtures/ags4/clean_4_1_1.ags")
    >>> f.version, list(f.groups)[:3]
    ('4.1.1', ['PROJ', 'TRAN', 'ABBR'])
    >>> ags4.dumps(f) == open("tests/fixtures/ags4/clean_4_1_1.ags", "rb").read()
    True
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, BinaryIO, Literal

from pydiggs.ags4 import _io
from pydiggs.ags4._io import DEFAULT_MAX_BYTES
from pydiggs.ags4._table import AGS_VERSIONS, File, Group

if TYPE_CHECKING:
    from pydiggs.findings.model import Report

__all__ = ["AGS_VERSIONS", "File", "Group", "check", "dumps", "read", "read_bytes", "write"]

#: Most findings :func:`check` reports by default, as for ``pydiggs.validate``.
_DEFAULT_MAX_FINDINGS = 10_000


def read(
    path: str | os.PathLike[str],
    *,
    encoding: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> File:
    """Read an AGS4 file into a :class:`File`.

    Reading is lenient: a file that breaks the AGS4 rules still reads, and what
    reading had to work around is in ``file.notes`` (a row with too few values is
    padded with empty ones, an unquoted field is read up to the next comma, a second
    group of the same name is left out, and so on). The notes are not a validity
    verdict. Every value is kept as the string the file holds. Nothing the file names
    is opened and nothing is fetched from the network.

    Args:
        path: The file. It must be a regular file of at most ``max_bytes`` bytes and
            not compressed (gzip or zip).
        encoding: None (the default) reads UTF-8, with or without a byte order mark,
            and, when the bytes are not UTF-8, Windows-1252 with an
            ``ags4.encoding-assumed`` note. Otherwise one of ``"ascii"``, ``"utf-8"``,
            ``"utf-8-sig"``, ``"cp1252"`` or ``"iso-8859-1"``, read strictly.
        max_bytes: Largest file accepted (default 100 MiB, as for ``pydiggs.read``).
        time_budget: Seconds to spend; when they run out the groups and rows read so
            far are returned with ``complete=False`` and an ``ags4.timeout`` note.
            Reading also stops, the same way with an ``ags4.limit`` note, at a work
            limit: more than 10,000 groups, 2,000 headings in a group, 5,000,000 DATA
            rows, 1,000,000 characters in a value, or more cells than a file of its
            size may hold (short rows under a long HEADING row).

    Returns:
        The file's groups in file order, with ``source``, ``notes``, ``complete`` and
        ``limits_hit``.

    Raises:
        InstanceReadError: The file is missing, unreadable, too large, compressed or
            not a regular file.
        DocumentError: No table can be built. ``kind`` is ``not-ags4`` (no GROUP row,
            or binary content) or ``unsupported-version`` (an AGS 3 or older file).
        ValueError: An argument is out of range, or the file is not valid text in the
            ``encoding`` asked for.

    Examples:
        >>> from pydiggs import ags4
        >>> f = ags4.read("tests/fixtures/ags4/clean_4_1_1.ags")
        >>> loca = f.groups["LOCA"]
        >>> loca.headings[:3], loca.types[:3]
        (('LOCA_ID', 'LOCA_TYPE', 'LOCA_NATE'), ('ID', 'PA', '2DP'))
        >>> loca.rows[0][:3], f.complete, f.notes
        (('BH01', 'CP', '451200.00'), True, ())
    """
    return _io.load_path(path, encoding=encoding, max_bytes=max_bytes, time_budget=time_budget)


def read_bytes(
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    name: str | None = None,
    encoding: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> File:
    r"""Read an AGS4 file held in memory, such as an upload.

    The same as :func:`read`. A ``bytearray`` or ``memoryview`` is copied first, a
    binary stream is read to its end and left open, and ``name`` is reduced to its last
    path component (it is only reported, in ``file.source.name``; nothing is opened).

    Args:
        data: The bytes, or a binary stream of them.
        name: A file name to report, or None.
        encoding: As for :func:`read`.
        max_bytes: Largest input accepted (default 100 MiB).
        time_budget: As for :func:`read`.

    Returns:
        The file, as :func:`read` returns it.

    Raises:
        InstanceReadError: The input is too large, compressed or an unreadable stream.
        DocumentError: No table can be built (``not-ags4`` or ``unsupported-version``).
        TypeError: ``data`` is text, a text stream or a non-contiguous memoryview.
        ValueError: An argument is out of range, or the bytes are not valid text in the
            ``encoding`` asked for.

    Examples:
        >>> from pydiggs import ags4
        >>> upload = b'"GROUP","PROJ"\r\n"HEADING","PROJ_ID"\r\n"UNIT",""\r\n"TYPE","ID"\r\n'
        >>> f = ags4.read_bytes(upload + b'"DATA","P1"\r\n', name="uploads/site.ags")
        >>> f.source.name, f.groups["PROJ"].records()
        ('site.ags', [{'PROJ_ID': 'P1'}])
        >>> lenient = ags4.read_bytes(upload + b'"DATA"\r\n')
        >>> lenient.groups["PROJ"].rows, [n.rule_id for n in lenient.notes]
        ((('',),), ['ags4.rule-4'])
    """
    return _io.load_bytes(
        data, name=name, encoding=encoding, max_bytes=max_bytes, time_budget=time_budget
    )


def dumps(file: File) -> bytes:
    r"""Return ``file`` as AGS4 bytes.

    The output is the same for the same file every time: every field in double
    quotes, a quote inside a value doubled, each group as its GROUP, HEADING, UNIT and
    TYPE rows and then its DATA rows, groups in the order given with one blank line
    between them, and CR LF after every row. A group with headings whose ``units`` or
    ``types`` is empty is written without that row, as it was read: the writer adds no
    row the file lacked. Values are written exactly as held; in
    particular a value a spreadsheet would read as a formula (one starting with ``=``,
    ``+``, ``-`` or ``@``) is not changed. The bytes are UTF-8, which is plain ASCII
    for a file that follows AGS4 Rule 1.

    Reading a file that follows the rules and writing it again gives the same bytes,
    and ``read_bytes(dumps(f)) == f`` for every file that can be written.

    Args:
        file: The file to write.

    Returns:
        The AGS4 bytes.

    Raises:
        ValueError: The file cannot be written: it has no group, or a group has an
            empty name, UNIT, TYPE or DATA rows that are not as long as its HEADING
            row (an empty ``units`` or ``types`` is no row, and is allowed), or a value
            that holds a line break, a NUL character or a character UTF-8 cannot
            encode. A file over the work limits of :func:`read` is refused
            too, as it would not read back whole: more than 10,000 groups, 2,000
            headings in a group, 5,000,000 DATA rows, or 1,000,000 characters in a value.
        TypeError: ``file`` is not a :class:`File`.

    Examples:
        >>> from pydiggs import ags4
        >>> group = ags4.Group(
        ...     name="PROJ",
        ...     headings=("PROJ_ID", "PROJ_NAME"),
        ...     units=("", ""),
        ...     types=("ID", "X"),
        ...     rows=[("P1", 'The "Old" Quay')],
        ... )
        >>> print(ags4.dumps(ags4.File(groups=[group])).decode().replace("\r\n", "\n"), end="")
        "GROUP","PROJ"
        "HEADING","PROJ_ID","PROJ_NAME"
        "UNIT","",""
        "TYPE","ID","X"
        "DATA","P1","The ""Old"" Quay"
    """
    return _io.dump(file)


def write(
    file: File, path: str | os.PathLike[str], *, line_ending: Literal["\r\n"] = "\r\n"
) -> None:
    r"""Write ``file`` to ``path`` as AGS4, replacing a file that is already there.

    The bytes are those of :func:`dumps`.

    Args:
        file: The file to write.
        path: Where to write it.
        line_ending: ``"\r\n"``, the only line ending AGS4 allows.

    Raises:
        ValueError: The file cannot be written (see :func:`dumps`), or ``line_ending``
            is not ``"\r\n"``. Nothing is written.
        TypeError: ``file`` is not a :class:`File`.
        OSError: The path cannot be written.

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from pydiggs import ags4
        >>> f = ags4.read("tests/fixtures/ags4/clean_4_1_1.ags")
        >>> with tempfile.TemporaryDirectory() as folder:
        ...     ags4.write(f, Path(folder) / "out.ags")
        ...     ags4.read(Path(folder) / "out.ags") == f
        True
    """
    _io.save(file, path, line_ending)


def check(
    source: str | os.PathLike[str] | File,
    *,
    dictionary: str | os.PathLike[str] | File | None = None,
    encoding: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_findings: int | None = _DEFAULT_MAX_FINDINGS,
    time_budget: float | None = None,
) -> Report:
    """Check an AGS4 file against the AGS4 rules (Rules 1 to 20) and report the findings.

    The rules are those of section 4.1.1 of the AGS4 specification, Edition 4.2
    (December 2025), which are unchanged from 4.1.1 and serve every version in
    :data:`AGS_VERSIONS`. Each finding has step ``ags4`` and the rule id
    ``ags4.rule-<number>`` (``ags4.rule-10a`` and so on); its ``path`` is the place in
    the file, ``GROUP``, ``GROUP/row`` or ``GROUP/row/HEADING`` with DATA rows counted
    from 1, and its ``line`` the line in the file when known. The report also holds
    what reading had to work around (``file.notes``), and ``ags4.formula-text``
    warnings for text a spreadsheet would read as a formula. Messages are fixed
    templates: they name groups and headings, never a value from the file.

    Most rules need nothing but the file. Rules 7, 9, 10a, 10b, 10c and 18, and parts
    of 11c, 13, 14 and 19b, need the AGS4 data dictionary: they run for the groups and
    headings the file's own DICT group defines, and for the standard groups only when
    ``dictionary`` is given. pyDIGGS bundles no AGS dictionary; without one the report
    says so in an ``ags4.dictionary-not-checked`` info finding. Rule 20's FILE folder
    is never opened: only the FILE group's own consistency is checked.

    pyDIGGS implements the published rules independently; it is not an AGS-approved
    validator.

    Args:
        source: The file's path, or a :class:`File` already read or built in code (its
            rows then have no line numbers).
        dictionary: An AGS4 data dictionary (an AGS4 file whose DICT group defines the
            groups and headings, such as the standard dictionary of the file's version),
            as a path or a :class:`File`. None checks without one.
        encoding: As for :func:`read`.
        max_bytes: Largest file accepted, for the source and the dictionary (default
            100 MiB).
        max_findings: Most findings to report (default 10,000); one more finding,
            ``structure.findings-truncated``, counts the rest. None for no limit.
        time_budget: Seconds for reading and checking together. When they run out, the
            report holds what was found so far, an ``ags4.timeout`` error, and the step
            is ``incomplete``.

    Returns:
        A :class:`pydiggs.Report` with ``format="ags4"``, no ``diggs_version``, and the
        one step ``ags4``.

    Raises:
        InstanceReadError: The source is missing, unreadable, too large, compressed or
            not a regular file.
        DocumentError: No table can be built from the source (``not-ags4`` or
            ``unsupported-version``).
        ResourceError: ``dictionary`` cannot be read, was read only in part (a limit
            stopped the read), or has no DICT group that defines groups.
        ValueError: An argument is out of range, or the source is not valid text in
            the ``encoding`` asked for.
        TypeError: ``source`` or ``dictionary`` is of the wrong type.

    Examples:
        >>> from pydiggs import ags4
        >>> report = ags4.check("tests/fixtures/ags4/c2_clean.ags")
        >>> report.ok, report.format, [f.rule_id for f in report.findings]
        (True, 'ags4', ['ags4.dictionary-not-checked'])
        >>> bad = ags4.check("tests/fixtures/ags4/c2_rule_10a.ags")
        >>> [(f.rule_id, f.line, f.path) for f in bad.findings if f.severity == "error"]
        [('ags4.rule-10a', 77, 'XTST/2')]
    """
    from pydiggs.ags4 import _check  # noqa: PLC0415  # the checker loads on first use

    return _check.run(
        source,
        dictionary=dictionary,
        encoding=encoding,
        max_bytes=max_bytes,
        max_findings=max_findings,
        time_budget=time_budget,
    )
