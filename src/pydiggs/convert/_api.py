"""AGS4 and DIGGS conversion: the adapters around the pure mapping steps.

``ags4_to_diggs`` reads the AGS4 source (:mod:`pydiggs.ags4`), maps it by the bundled
table (:mod:`._to_model`), writes DIGGS 3.0.0 with the internal writer, reads the XML
back as the :class:`~pydiggs.model.Model` a user holds, and validates it.
``diggs_to_ags4`` reads DIGGS with :func:`pydiggs.read` and ``model_to_ags4`` maps a
model by the same table (:mod:`._from_model`) to an AGS4 file, which is checked with
:func:`pydiggs.ags4.check`. Every step but reading, validating and checking is pure.
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
from typing import Any, BinaryIO, Final, Literal

import pydiggs
from pydiggs import ags4
from pydiggs._reader import read as read_diggs
from pydiggs._reader import read_bytes as read_model
from pydiggs._writer import write_model
from pydiggs.ags4 import File as AgsFile
from pydiggs.convert._from_model import formula_like, map_model
from pydiggs.convert._ledger import RULES
from pydiggs.convert._ledger import name as shown_name
from pydiggs.convert._mapping import MappingTable, load_mapping
from pydiggs.convert._to_model import Built, Origin, components_of, drop, map_file
from pydiggs.findings.model import Finding, Report, Severity, Step, StepStatus
from pydiggs.findings.run import DEFAULT_MAX_BYTES as XML_MAX_BYTES
from pydiggs.findings.run import validate_bytes
from pydiggs.model import Model

__all__ = [
    "AGS_VERSIONS",
    "DIGGS_VERSIONS",
    "MAPPING_VERSION",
    "Conversion",
    "ags4_to_diggs",
    "diggs_to_ags4",
    "mapping_info",
    "model_to_ags4",
]

#: The version of the bundled mapping table (``major.minor``, separate from pyDIGGS's).
MAPPING_VERSION: Final = "1.0"
#: The AGS4 versions DIGGS to AGS4 writes (the default is 4.2).
AGS_VERSIONS: Final = ("4.1.1", "4.2")
_DEFAULT_AGS_VERSION: Final = "4.2"
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
DiggsSource = str | os.PathLike[str] | bytes | bytearray | memoryview | BinaryIO


@dataclass(frozen=True, slots=True, kw_only=True)
class Conversion:
    """The result of a conversion, either way: the output, what it means, and what was lost.

    AGS4 to DIGGS (:func:`ags4_to_diggs`): ``xml`` is the DIGGS document and ``model``
    that document read back with :func:`pydiggs.read_bytes`, so it is always the model
    of a real DIGGS file; ``ags4`` is None; ``residual`` is an AGS4 file of what was not
    converted (every value a ``convert`` finding names, with its row's key fields), so
    that the DIGGS output and the residual file together hold everything the source did;
    ``validation`` is :func:`pydiggs.validate_bytes` on ``xml``; ``trace`` maps a node's
    key in ``model`` to the AGS4 row it came from.

    DIGGS to AGS4 (:func:`diggs_to_ags4`, :func:`model_to_ags4`): ``ags4`` is the AGS4
    file (``ags4.dumps(c.ags4)`` gives its bytes) and ``model`` the model it was made
    from; ``xml`` and ``residual`` are None and ``trace`` is empty; ``validation`` is
    :func:`pydiggs.ags4.check` on the output.

    ``report`` is about the source: what reading it worked around and the conversion's
    findings (step ``convert``; also ``ags4.formula-text`` for AGS4 output). ``losses``
    are the ``convert`` findings of severity warning or error, each naming what did not
    reach the output or what was written by default; ``lossless`` is True when there
    are none. ``complete`` is False when a size or time limit stopped the conversion.
    """

    model: Model
    report: Report
    validation: Report | None
    xml: bytes | None = None
    ags4: AgsFile | None = None
    residual: AgsFile | None = None
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
        """Write the output to ``path`` (the DIGGS XML, or the AGS4 file), replacing a file.

        Raises:
            OSError: The path cannot be written.
        """
        if self.xml is not None:
            Path(path).write_bytes(self.xml)
        elif self.ags4 is not None:
            ags4.write(self.ags4, path)


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

    The bundled mapping table (version :data:`MAPPING_VERSION`, see
    :func:`mapping_info`) covers PROJ, TRAN, the ABBR/UNIT/TYPE/DICT lookups, LOCA,
    GEOL, SAMP, ISPT, LLPL, GRAG with GRAT, LNMC, and HORN, HDIA, CDIA, BKFL, WSTG with
    WSTD, LDEN, and static cone tests (SCPG with SCPT, and CPTG with CPTT); any other
    group is reported and not converted, and its rows are in ``residual``.

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
        >>> from pydiggs import convert
        >>> c = convert.ags4_to_diggs("tests/fixtures/ags4/convert_tier_a.ags")
        >>> c.validation.ok, c.model.source.diggs_version
        (True, '3.0.0')
        >>> c.trace["BH01"]
        Origin(group='LOCA', row=1, line=60)
        >>> sorted({f.rule_id for f in c.losses})[:2]
        ['convert.assumed', 'convert.unmapped-code']
        >>> list(c.residual.groups)[:3]
        ['PROJ', 'TRAN', 'LOCA']
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
    keys = {group.name: group.keys for group in mapping.groups.values()}
    return Conversion(
        xml=xml,
        model=model,
        report=report,
        validation=validation,
        residual=built.ledger.residual(keys),
        trace=MappingProxyType(trace),
        mapping_version=mapping.mapping_version,
        complete=complete,
    )


def _formula_findings(file: ags4.File, guarded: bool) -> list[Finding]:
    """One ``ags4.formula-text`` finding per heading whose text starts like a formula."""
    out: list[Finding] = []
    for group in file.groups.values():
        for column, heading in enumerate(group.headings):
            if heading in ("TRAN_DLIM", "TRAN_RCON"):
                continue
            values = [row[column] for row in group.rows if column < len(row)]
            if guarded:
                count = sum(1 for v in values if v.startswith("'") and formula_like(v[1:]))
                text = "were prefixed with an apostrophe (formula_guard='prefix')"
            else:
                count = sum(1 for v in values if formula_like(v))
                text = "were written unchanged"
            if count:
                noun = "value" if count == 1 else "values"
                out.append(
                    Finding(
                        step=Step.AGS4,
                        rule_id="ags4.formula-text",
                        severity=Severity.WARNING,
                        message=f"{count} {noun} under {shown_name(heading)} in group"
                        f" {shown_name(group.name)}"
                        f" start like a spreadsheet formula (=, +, - or @) and {text}.",
                        path=shown_name(group.name),
                    )
                )
    return out


def model_to_ags4(
    model: Model,
    *,
    ags_version: str = _DEFAULT_AGS_VERSION,
    check: bool = True,
    formula_guard: Literal["prefix"] | None = None,
) -> Conversion:
    """Convert a DIGGS model to an AGS4 file, listing everything that was lost.

    The model is mapped by the same bundled table as :func:`ags4_to_diggs`, read the
    other way. Every value of the model (each name and remark, ``params`` entry, typed
    field, ``extra`` node, link that is not structure, and result-table cell) is either
    written into a cell or named by a ``convert`` finding; a value with no AGS4 target is
    a ``convert.unmapped-element`` warning. Values are written as reported: a
    quantity's text as it was written, a result-table number in its shortest form, a
    unit renamed only through the unit table. pyDIGGS bundles no AGS4 data dictionary,
    so each heading's TYPE is the one its values have.

    Text a spreadsheet would read as a formula (starting with ``=``, ``+``, ``@``, or a
    ``-`` that is not a number) is written unchanged and reported as
    ``ags4.formula-text``. With ``formula_guard="prefix"``, such text under a text
    (``X``) heading that is not a key is written with an apostrophe in front instead,
    and each heading is reported: that changes the value, so use it only for files
    meant for a spreadsheet.

    Args:
        model: The model, as :func:`pydiggs.read` gives it.
        ags_version: The AGS4 version to write: ``"4.2"`` (the default) or ``"4.1.1"``.
            It is written in TRAN_AGS (as ``4.2``, the value python-ags4 recognises,
            not the ``4.2.0`` of the AGS example), and selects the groups that differ
            between the two: static cone tests are CPTG/CPTT in 4.2 and SCPG/SCPT in
            4.1.1, and 4.2 also gives the CRS as an EPSG code (LOCA_NSRI, LOCA_LLSI).
            As of November 2026 the BGS AGS validator (agsapi.bgs.ac.uk) and
            python-ags4 1.2.0 accept AGS4 only up to 4.1.1: pass
            ``ags_version="4.1.1"`` for files meant for them.
        check: Check the output with :func:`pydiggs.ags4.check` (into ``validation``).
        formula_guard: None (the default) or ``"prefix"`` (see above).

    Returns:
        The :class:`Conversion`, with ``ags4`` set.

    Raises:
        ValueError: ``ags_version`` or ``formula_guard`` is not supported.
        TypeError: ``model`` is not a :class:`pydiggs.Model`.
        ResourceError: A bundled mapping table is broken.

    Examples:
        >>> import pydiggs
        >>> from pydiggs import ags4, convert
        >>> c = convert.ags4_to_diggs("tests/fixtures/ags4/site_tier_b_4_1_1.ags")
        >>> back = convert.model_to_ags4(c.model)
        >>> back.validation.ok, back.ags4.version, "CPTT" in back.ags4.groups
        (True, '4.2', True)
        >>> back.ags4.groups["HDIA"].records()[0]["HDIA_DIAM"]
        '200'
        >>> "SCPT" in convert.model_to_ags4(c.model, ags_version="4.1.1").ags4.groups
        True
    """
    if not isinstance(model, Model):
        raise TypeError(f"model must be a pydiggs.Model, got {type(model).__name__}")
    if ags_version not in AGS_VERSIONS:
        raise ValueError(
            f"the converter writes AGS4 {', '.join(AGS_VERSIONS)}, not {ags_version!r}"
        )
    if formula_guard not in (None, "prefix"):
        raise ValueError(f"formula_guard must be None or 'prefix', not {formula_guard!r}")
    mapping: MappingTable = load_mapping()
    exported = map_model(model, mapping, ags_version=ags_version, formula_guard=formula_guard)
    file = exported.file
    findings = [*exported.findings, *_formula_findings(file, guarded=False)]
    if formula_guard is not None:
        findings += _formula_findings(file, guarded=True)
    shown = model.source.name or "model"
    report = Report(
        path=shown,
        sha256=model.source.sha256,
        diggs_version=model.source.diggs_version,
        steps={Step.CONVERT: _status(findings, model.complete)},
        findings=(*model.notes, *findings),
        tool_version=pydiggs.__version__,
    )
    validation = ags4.check(file) if check else None
    return Conversion(
        model=model,
        report=report,
        validation=validation,
        ags4=file,
        mapping_version=mapping.mapping_version,
        complete=model.complete,
    )


def diggs_to_ags4(
    source: DiggsSource,
    *,
    ags_version: str = _DEFAULT_AGS_VERSION,
    check: bool = True,
    formula_guard: Literal["prefix"] | None = None,
    name: str | None = None,
    max_bytes: int = XML_MAX_BYTES,
    time_budget: float | None = None,
) -> Conversion:
    """Convert a DIGGS document to an AGS4 file, listing everything that was lost.

    The document is read with :func:`pydiggs.read` (any DIGGS version it reads) and the
    model converted as :func:`model_to_ags4` does. Nothing is fetched and nothing the
    document names is opened.

    Args:
        source: A path, or the bytes of a document (or a binary stream).
        ags_version: As for :func:`model_to_ags4`: ``"4.2"`` (the default) or
            ``"4.1.1"``. The BGS AGS validator (agsapi.bgs.ac.uk) and python-ags4 1.2.0
            accept AGS4 only up to 4.1.1 (November 2026): pass ``ags_version="4.1.1"``
            for files meant for them.
        check: As for :func:`model_to_ags4`.
        formula_guard: As for :func:`model_to_ags4`.
        name: A file name to report for bytes or a stream.
        max_bytes: As for :func:`pydiggs.read`.
        time_budget: Seconds for reading, as for :func:`pydiggs.read`; when they run out
            the model holds the members read so far and ``complete`` is False.

    Returns:
        The :class:`Conversion`, with ``ags4`` set.

    Raises:
        InstanceReadError: As :func:`pydiggs.read`.
        DocumentError: As :func:`pydiggs.read`.
        ValueError: ``ags_version`` or ``formula_guard`` is not supported.
        ResourceError: A bundled mapping table is broken.

    Examples:
        >>> from pydiggs import convert
        >>> xml = convert.ags4_to_diggs("tests/fixtures/ags4/convert_tier_a.ags").xml
        >>> c = convert.diggs_to_ags4(xml, name="site.xml")
        >>> c.validation.ok, c.report.path, list(c.ags4.groups)[:4]
        (True, 'site.xml', ['PROJ', 'TRAN', 'ABBR', 'TYPE'])
    """
    if ags_version not in AGS_VERSIONS:
        raise ValueError(
            f"the converter writes AGS4 {', '.join(AGS_VERSIONS)}, not {ags_version!r}"
        )
    if isinstance(source, str | os.PathLike):
        model = read_diggs(source, max_bytes=max_bytes, time_budget=time_budget)
    else:
        model = read_model(source, name=name, max_bytes=max_bytes, time_budget=time_budget)
    return model_to_ags4(model, ags_version=ags_version, check=check, formula_guard=formula_guard)


def mapping_info() -> dict[str, Any]:
    """What the bundled mapping table covers: its version, groups and headings.

    Returns:
        A JSON-serialisable dict: ``mapping_version``, ``ags_versions``,
        ``diggs_versions``, ``rules`` (each ``convert`` rule id and its severity) and
        ``groups``, each with its ``name``, ``pattern``, ``status`` (``draft`` or
        ``reviewed``), ``keys``, ``parent``, ``to_ags4_versions`` (the AGS4 versions
        DIGGS to AGS4 writes it for; None for every one) and ``headings`` (``name``,
        ``to``, ``target``, ``property``, ``reason``, ``quantity`` for a measure, and
        ``direction``: ``both``, ``to-diggs`` or ``to-ags4``).

    Examples:
        >>> from pydiggs import convert
        >>> info = convert.mapping_info()
        >>> info["mapping_version"], info["diggs_versions"]
        ('1.0', ['3.0.0'])
        >>> [g["name"] for g in info["groups"]][:3]
        ['PROJ', 'TRAN', 'ABBR']
        >>> sorted({g["status"] for g in info["groups"]}), "ags4" in str(info["rules"])
        (['reviewed'], False)
        >>> "convert.unmapped-element" in info["rules"]
        True
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
                "to_ags4_versions": None
                if group.to_ags4_versions is None
                else list(group.to_ags4_versions),
                "headings": [
                    {
                        "name": h.name,
                        "to": h.to,
                        "target": h.target,
                        "property": h.property,
                        "reason": h.reason,
                        "quantity": h.quantity,
                        "direction": h.direction,
                    }
                    for h in group.headings.values()
                ],
            }
            for group in mapping.groups.values()
        ],
    }
