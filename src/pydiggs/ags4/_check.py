"""The AGS4 checker's adapter: a path or a :class:`~pydiggs.ags4.File` in, a Report out.

Reads the file (and a dictionary the caller names), runs the pure rules of
:mod:`pydiggs.ags4._rules` under one time budget, and builds a findings report of step
``ags4``. Nothing the file names is opened, and nothing is fetched from the network.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Final

from pydiggs.ags4._dictionary import Dictionary, from_file
from pydiggs.ags4._emit import emit
from pydiggs.ags4._io import deadline, load_path, load_path_text
from pydiggs.ags4._parse import TIMEOUT
from pydiggs.ags4._rules import (
    DICTIONARY_NOT_CHECKED,
    GroupLines,
    Stopped,
    check_tables,
    index_lines,
)
from pydiggs.ags4._table import File
from pydiggs.errors import DocumentError, InstanceReadError, ResourceError
from pydiggs.findings.model import Finding, Report, Severity, Step, StepStatus

__all__ = ["DEFAULT_INPUT_NAME", "run"]

#: The reported name of a :class:`File` that was not read from a file.
DEFAULT_INPUT_NAME: Final = "input.ags"
_FINDINGS_TRUNCATED: Final = "structure.findings-truncated"


def _dictionary(dictionary: object, max_bytes: int) -> Dictionary | None:
    """The view of a supplied dictionary; ResourceError when it is not one."""
    if dictionary is None:
        return None
    if isinstance(dictionary, File):
        source = dictionary
    elif isinstance(dictionary, str | os.PathLike):
        try:
            source = load_path(dictionary, encoding=None, max_bytes=max_bytes, time_budget=None)
        except InstanceReadError as exc:
            reason = exc.strerror or "it could not be read"
            raise ResourceError(f"the AGS4 dictionary could not be read: {reason}") from exc
        except (DocumentError, ValueError) as exc:
            raise ResourceError(f"the AGS4 dictionary is not an AGS4 file: {exc}") from exc
    else:
        raise TypeError(
            f"dictionary must be a path, an ags4.File or None, not {type(dictionary).__name__}"
        )
    view = from_file(source)
    if not any(group.defined for group in view.groups.values()):
        raise ResourceError(
            "the AGS4 dictionary has no DICT group that defines groups (DICT_TYPE GROUP)"
        )
    return view


def _sha256(file: File) -> str:
    if file.source is not None:
        return file.source.sha256
    try:
        data = emit(file).encode("utf-8", "surrogatepass")
    except ValueError:  # a ragged file built in code: hash its tables instead
        tables = [[g.name, g.headings, g.units, g.types, g.rows] for g in file.groups.values()]
        data = json.dumps(tables, ensure_ascii=True).encode("ascii")
    return hashlib.sha256(data).hexdigest()


def _cap(findings: list[Finding], max_findings: int | None) -> tuple[list[Finding], bool]:
    """At most ``max_findings`` findings, plus one that counts the rest."""
    if max_findings is None or len(findings) <= max_findings:
        return findings, False
    findings.sort(key=Finding.sort_key)
    kept, dropped = findings[:max_findings], findings[max_findings:]
    worst = max((f.severity for f in dropped), key=lambda s: s.rank)
    more = "1 more was" if len(dropped) == 1 else f"{len(dropped)} more were"
    noun = "finding" if max_findings == 1 else "findings"
    kept.append(
        Finding(
            step=Step.STRUCTURE,
            rule_id=_FINDINGS_TRUNCATED,
            severity=worst,
            message=(
                f"Stopped after {max_findings} {noun}; {more} not reported (steps: ags4)."
                " Raise max_findings to see them."
            ),
        )
    )
    return kept, True


def _status(findings: list[Finding], *, complete: bool) -> StepStatus:
    if not complete:
        return "incomplete"
    if any(f.step is Step.AGS4 and f.severity is Severity.ERROR for f in findings):
        return "failed"
    return "passed"


def run(
    source: object,
    *,
    dictionary: object,
    encoding: str | None,
    max_bytes: int,
    max_findings: int | None,
    time_budget: float | None,
) -> Report:
    """:func:`pydiggs.ags4.check` without its docstring."""
    import pydiggs  # noqa: PLC0415  # the package is imported by now; this is its version
    from pydiggs.findings.run import display_paths  # noqa: PLC0415  # lxml is loaded already

    if max_findings is not None and (type(max_findings) is not int or max_findings < 1):
        raise ValueError(f"max_findings must be a positive int or None, got {max_findings!r}")
    should_stop = deadline(time_budget)
    finished = True
    lines: dict[str, GroupLines] | None = None
    broken: list[int] = []
    absolute: str | None = None
    if isinstance(source, File):
        file = source
        shown = (file.source.name if file.source else None) or DEFAULT_INPUT_NAME
    elif isinstance(source, str | os.PathLike):
        file, text = load_path_text(
            source,
            encoding=encoding,
            max_bytes=max_bytes,
            time_budget=time_budget,
            should_stop=should_stop,
        )
        shown, absolute = display_paths(source)
        try:
            lines, broken = index_lines(text, file, should_stop)
        except Stopped:
            finished = False
        del text
    else:
        raise TypeError(f"source must be a path or an ags4.File, not {type(source).__name__}")
    view = _dictionary(dictionary, max_bytes)
    findings = list(file.notes)
    if finished:
        found, finished = check_tables(
            file, dictionary=view, lines=lines, broken=broken, should_stop=should_stop
        )
        findings += found
    if view is None:
        findings.append(
            Finding(
                step=Step.AGS4,
                rule_id=DICTIONARY_NOT_CHECKED,
                severity=Severity.INFO,
                message=(
                    "No AGS4 data dictionary was supplied, so Rules 7, 9, 10a, 10b, 10c and"
                    " 18, and the parts of Rules 11c, 13, 14 and 19b that need it, were"
                    " checked only for the groups and headings the file's own DICT group"
                    " defines. Pass dictionary= (--dictionary) to check standard groups."
                ),
            )
        )
    if not finished:
        findings.append(
            Finding(
                step=Step.AGS4,
                rule_id=TIMEOUT,
                severity=Severity.ERROR,
                message="Checking stopped at its time budget; the rest of the file was not"
                " checked.",
            )
        )
    findings, truncated = _cap(findings, max_findings)
    complete = file.complete and finished and not truncated
    return Report(
        path=shown,
        sha256=_sha256(file),
        diggs_version=None,
        steps={Step.AGS4: _status(findings, complete=complete)},
        findings=tuple(findings),
        tool_version=pydiggs.__version__,
        absolute_path=absolute,
        format="ags4",
    )
