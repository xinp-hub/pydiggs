"""AGS4 to DIGGS: read, map, write, read back, validate. The adapter around the pure parts.

``ags4_to_diggs`` reads the AGS4 source (:mod:`pydiggs.ags4`), maps it by the bundled
table (:mod:`._to_model`), writes DIGGS 3.0.0 with the internal writer, reads the XML
back as the :class:`~pydiggs.model.Model` a user holds, and validates it. Every step but
the first and the last is pure.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Final

import pydiggs
from pydiggs import ags4
from pydiggs._reader import read_bytes as read_model
from pydiggs._writer import write_model
from pydiggs.convert._ledger import RULES
from pydiggs.convert._mapping import MappingTable, load_mapping
from pydiggs.convert._to_model import Built, Origin, components_of, drop, map_file
from pydiggs.findings.model import Finding, Report, Severity, Step, StepStatus
from pydiggs.findings.run import DEFAULT_MAX_BYTES as XML_MAX_BYTES
from pydiggs.findings.run import validate_bytes
from pydiggs.model import Model

__all__ = ["DIGGS_VERSIONS", "MAPPING_VERSION", "Conversion", "ags4_to_diggs", "mapping_info"]

#: The version of the bundled mapping table (``major.minor``, separate from pyDIGGS's).
MAPPING_VERSION: Final = "0.1"
#: The DIGGS versions the converter writes.
DIGGS_VERSIONS: Final = ("3.0.0",)
_DEFAULT_NAME: Final = "input.ags"
#: Largest input accepted by default (100 MiB, as :func:`pydiggs.ags4.read`).
DEFAULT_MAX_BYTES: Final = 100 * 1024 * 1024
_NAME_SEPARATORS: Final = re.compile(r"[/\\]")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_MAX_NAME_CHARS: Final = 255
#: The budget passed on once the time budget is spent: the readers take only a positive
#: one, and this one runs out at their first look at the clock.
_SPENT: Final = 1e-9
#: Where the writer names the node it refuses: ``<Element> '<key>'`` at the start.
_REFUSED: Final = re.compile(r"^[A-Za-z][\w.-]* '((?:[^'\\]|\\.)*)'")

Source = str | os.PathLike[str] | bytes | bytearray | memoryview | BinaryIO | ags4.File


@dataclass(frozen=True, slots=True, kw_only=True)
class Conversion:
    """The result of a conversion: the output, what it means, and what was lost.

    ``xml`` is the DIGGS document; ``model`` is that document read back with
    :func:`pydiggs.read_bytes`, so it is always the model of a real DIGGS file.
    ``report`` is about the source: the AGS4 reader's notes (step ``ags4``) and the
    conversion's findings (step ``convert``). ``losses`` are the ``convert`` findings of
    severity warning or error, each naming what did not reach the output or what was
    written by default; ``lossless`` is True when there are none. ``validation`` is
    :func:`pydiggs.validate_bytes` on ``xml`` (None when not asked for). ``trace`` maps a
    node's key in ``model`` to the AGS4 row it came from. ``complete`` is False when a
    size or time limit stopped reading, converting or reading back: ``xml`` or ``model``
    is then partial (see ``time_budget`` of :func:`ags4_to_diggs`).
    """

    xml: bytes
    model: Model
    report: Report
    validation: Report | None
    trace: Mapping[str, Origin] = field(default_factory=lambda: MappingProxyType({}))
    mapping_version: str = MAPPING_VERSION
    complete: bool = True

    @property
    def losses(self) -> tuple[Finding, ...]:
        """The ``convert`` findings of severity warning or error, in report order."""
        return tuple(
            f
            for f in self.report.findings
            if f.step is Step.CONVERT and f.severity is not Severity.INFO
        )

    @property
    def lossless(self) -> bool:
        """True when nothing was lost or assumed (``losses`` is empty)."""
        return not self.losses

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write ``xml`` to ``path``, replacing a file that is there.

        Raises:
            OSError: The path cannot be written.
        """
        Path(path).write_bytes(self.xml)


def _shown(name: str | None) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str):
        raise TypeError(f"name must be str or None, got {type(name).__name__}")
    base = _NAME_SEPARATORS.split(name)[-1]
    if base in ("", ".", ".."):
        return None
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", base)[:_MAX_NAME_CHARS]


def _read(
    source: Source,
    name: str | None,
    encoding: str | None,
    max_bytes: int,
    time_budget: float | None,
) -> ags4.File:
    if isinstance(source, ags4.File):
        return source
    if isinstance(source, str | os.PathLike):
        return ags4.read(source, encoding=encoding, max_bytes=max_bytes, time_budget=time_budget)
    return ags4.read_bytes(
        source, name=name, encoding=encoding, max_bytes=max_bytes, time_budget=time_budget
    )


def _sha256(file: ags4.File) -> str:
    if file.source is not None:
        return file.source.sha256
    try:
        return hashlib.sha256(ags4.dumps(file)).hexdigest()
    except ValueError:
        return hashlib.sha256(b"").hexdigest()


def _holder(model: Model, key: str) -> str | None:
    """The key of the member or layer that holds node ``key`` (itself when it is one)."""
    node = model.get(key)
    if node is None:
        return None
    holders = [*model.features, *model.measurements]
    holders += [layer for o in model.observations for layer in o.layers]
    top = {n.key for n in model.nodes()} - {c.key for h in holders for c in components_of(h)}
    if key in top:
        return key
    for holder in holders:
        if any(c.key == key for c in components_of(holder)):
            return holder.key
    return None  # pragma: no cover - every component has a holder


def _write(built: Built, diggs_version: str) -> tuple[bytes, Built]:
    """Write ``built``; drop each node the writer refuses (naming its cells) and retry."""
    for _ in range(sum(1 for _ in built.model.nodes()) + 1):
        try:
            return write_model(built.model, diggs_version), built
        except ValueError as exc:
            match = _REFUSED.match(str(exc))
            key = None if match is None else _holder(built.model, match.group(1))
            if key is None:
                raise
            built = drop(built, key)
    raise AssertionError("the writer kept refusing")  # pragma: no cover


def _status(findings: list[Finding], complete: bool) -> StepStatus:
    if not complete:
        return "incomplete"
    return "failed" if any(f.severity is Severity.ERROR for f in findings) else "passed"


def ags4_to_diggs(
    source: Source,
    *,
    diggs_version: str = "3.0.0",
    validate: bool = True,
    name: str | None = None,
    encoding: str | None = None,
    max_bytes: int = ags4._io.DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Conversion:
    """Convert an AGS4 file to a DIGGS 3.0.0 document, listing everything that was lost.

    **Preview** (pyDIGGS 1.9): the table covers the Tier A groups (PROJ, TRAN, the
    ABBR/UNIT/TYPE/DICT lookups, LOCA, GEOL, SAMP, ISPT, LLPL, GRAG with GRAT, LNMC),
    every group mapping is a draft, and names and output may change before the API is
    stable in ``pydiggs.convert``.

    Every non-null value of the source is either carried into DIGGS or named by a
    ``convert`` finding in ``report``; nothing is dropped silently. Values are carried
    as reported: units are renamed only through the unit table, and nothing is
    converted numerically. Nothing the file names is opened, nothing is fetched, and the
    same file always gives the same bytes.

    Args:
        source: A path, the bytes of a file (or a binary stream), or an
            :class:`~pydiggs.ags4.File` already read.
        diggs_version: The DIGGS version to write; only ``"3.0.0"``.
        validate: Validate the output with :func:`pydiggs.validate_bytes`.
        name: A file name to report for bytes, a stream or a ``File``.
        encoding: As for :func:`pydiggs.ags4.read`.
        max_bytes: As for :func:`pydiggs.ags4.read`.
        time_budget: Seconds for the whole call, measured from its start: reading,
            converting, validating and reading back are each given what is left. When
            it runs out while reading or converting, ``xml`` holds what was converted
            and ``convert.incomplete`` (or an ``ags4`` note) says what was not; while
            validating, ``validation`` has a ``structure.timeout`` error, as
            :func:`pydiggs.validate_bytes` gives; while reading back, ``model`` holds
            the members read so far (``model.complete`` is False, with a
            ``structure.timeout`` note) while ``xml`` stays whole. ``complete`` is False
            in every case but a validation timeout (check ``validation`` for that).
            Writing the XML is not stopped part way (it runs to its end), and none of
            the steps can interrupt a running libxml2 call, so the call can overrun
            the budget by up to one write and one such call.

    Returns:
        The :class:`Conversion`.

    Raises:
        InstanceReadError: As :func:`pydiggs.ags4.read`.
        DocumentError: As :func:`pydiggs.ags4.read` (not AGS4, or AGS 3).
        ValueError: ``diggs_version`` is not supported, or an argument is out of range.
        ResourceError: A bundled mapping table, dictionary or layout is broken.

    Examples:
        >>> from pydiggs.preview import convert
        >>> c = convert.ags4_to_diggs("tests/fixtures/ags4/convert_tier_a.ags")
        >>> c.validation.ok, c.model.source.diggs_version
        (True, '3.0.0')
        >>> c.trace["BH01"]
        Origin(group='LOCA', row=1, line=60)
        >>> sorted({f.rule_id for f in c.losses})[:2]
        ['convert.assumed', 'convert.unmapped-code']
    """
    if diggs_version not in DIGGS_VERSIONS:
        raise ValueError(
            f"the converter writes DIGGS {', '.join(DIGGS_VERSIONS)}, not {diggs_version!r}"
        )
    started = time.monotonic()
    file = _read(source, name, encoding, max_bytes, time_budget)
    mapping: MappingTable = load_mapping()
    shown = _shown(name) or (file.source.name if file.source else None) or _DEFAULT_NAME
    deadline = None if time_budget is None else started + time_budget

    def left() -> float | None:
        return None if deadline is None else max(deadline - time.monotonic(), _SPENT)

    if not file.groups:  # pragma: no cover - the reader refuses a file with no group
        raise ValueError("an AGS4 file with no group cannot be converted")
    built = map_file(file, mapping, name=shown, deadline=deadline, clock=time.monotonic)
    xml, built = _write(built, diggs_version)
    limit = max(len(xml), XML_MAX_BYTES)  # pyDIGGS's own XML, often ~19x the input
    findings = built.ledger.findings()
    stem = shown.rsplit(".", 1)[0] or "output"
    validation = None
    if validate:
        validation = validate_bytes(xml, name=f"{stem}.xml", max_bytes=limit, time_budget=left())
    report = Report(
        path=shown,
        sha256=_sha256(file),
        diggs_version=None,
        steps={
            Step.AGS4: _status(list(file.notes), file.complete),
            Step.CONVERT: _status(findings, built.complete),
        },
        findings=(*file.notes, *findings),
        tool_version=pydiggs.__version__,
        format="ags4",
    )
    model = read_model(xml, name=f"{stem}.xml", max_bytes=limit, time_budget=left())
    complete = file.complete and built.complete and model.complete
    trace = {key: origin for key, origin in built.trace.items() if model.get(key) is not None}
    return Conversion(
        xml=xml,
        model=model,
        report=report,
        validation=validation,
        trace=MappingProxyType(trace),
        mapping_version=mapping.mapping_version,
        complete=complete,
    )


def mapping_info() -> dict[str, Any]:
    """What the bundled mapping table covers: its version, groups and headings.

    Returns:
        A JSON-serialisable dict: ``mapping_version``, ``ags_versions``,
        ``diggs_versions``, ``rules`` (each ``convert`` rule id and its severity) and
        ``groups``, each with its ``name``, ``pattern``, ``status`` (``draft`` or
        ``reviewed``), ``keys``, ``parent`` and ``headings`` (``name``, ``to``,
        ``target``, ``property``, ``reason``, and ``quantity`` for a measure).

    Examples:
        >>> from pydiggs.preview import convert
        >>> info = convert.mapping_info()
        >>> info["mapping_version"], info["diggs_versions"]
        ('0.1', ['3.0.0'])
        >>> [g["name"] for g in info["groups"]][:3]
        ['PROJ', 'TRAN', 'ABBR']
    """
    mapping = load_mapping()
    return {
        "mapping_version": mapping.mapping_version,
        "ags_versions": list(mapping.ags_versions),
        "diggs_versions": list(mapping.diggs_versions),
        "rules": {rule: severity.value for rule, severity in RULES.items()},
        "groups": [
            {
                "name": group.name,
                "pattern": group.pattern,
                "status": group.status,
                "keys": list(group.keys),
                "parent": group.parent,
                "headings": [
                    {
                        "name": h.name,
                        "to": h.to,
                        "target": h.target,
                        "property": h.property,
                        "reason": h.reason,
                        "quantity": h.quantity,
                    }
                    for h in group.headings.values()
                ],
            }
            for group in mapping.groups.values()
        ],
    }
