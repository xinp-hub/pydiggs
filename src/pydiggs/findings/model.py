"""The findings model: one :class:`Finding` per problem, one :class:`Report` per file.

Pure: no lxml, no I/O. Everything here can be built from any source of findings,
including the official DIGGS validator's ``messageSet`` output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Literal, get_args

from pydiggs.findings.keys import ConformanceKey, location_key

__all__ = [
    "RULE_ID_PATTERN",
    "FileStatus",
    "Finding",
    "Layer",
    "Report",
    "Severity",
    "Step",
    "StepStatus",
    "UnreadableFile",
    "Verdict",
    "is_valid_rule_id",
]


class Severity(StrEnum):
    """How serious a finding is: the official validator's ERROR, WARNING and INFO.

    Example:
        >>> Severity("warning").rank > Severity.INFO.rank
        True
        >>> max(Severity, key=lambda s: s.rank)
        <Severity.ERROR: 'error'>
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        """2 for error, 1 for warning, 0 for info; higher is more serious."""
        return _RANK[self]


_RANK: Final = {Severity.ERROR: 2, Severity.WARNING: 1, Severity.INFO: 0}


class Step(StrEnum):
    """The validation step a finding belongs to, in the official pipeline's order.

    The names follow DIGGSml/validation's steps (``DIGGS Structure``, ``Schema
    Location``, ``CodeType Validation`` ...), plus ``context`` for pydiggs's
    cross-reference checks. New steps may be added in a minor release.

    Example:
        >>> [s.value for s in Step][:4]
        ['structure', 'schema', 'codeType', 'dictionary']
        >>> Step.SCHEMA.order < Step.DICTIONARY.order
        True
    """

    STRUCTURE = "structure"
    SCHEMA = "schema"
    CODE_TYPE = "codeType"
    DICTIONARY = "dictionary"
    SCHEMATRON = "schematron"
    GEOMETRY = "geometry"
    CRS = "crs"
    COORDINATE = "coordinate"
    DEPRECATION = "deprecation"
    CONTEXT = "context"

    @property
    def order(self) -> int:
        """Position in the pipeline; findings sort by it."""
        return _ORDER[self]


_ORDER: Final = {step: i for i, step in enumerate(Step)}
_STEP_VALUES: Final = frozenset(s.value for s in Step)

#: Which validator produced a finding.
Layer = Literal["native", "official"]
_LAYERS: Final = frozenset(get_args(Layer))

#: What happened to a step: ran with no errors, ran with errors, not run, or could not
#: finish (its findings are then partial).
StepStatus = Literal["passed", "partial", "failed", "skipped", "incomplete"]
#: A checked file's status: ``not-diggs`` when its root is not a DIGGS ``Diggs`` element.
FileStatus = Literal["checked", "not-diggs"]
#: One word for what a report means, derived from its status, steps and findings.
Verdict = Literal[
    "valid", "valid-with-warnings", "not-valid", "partly-checked", "not-diggs", "unreadable"
]
_STEP_STATUSES: Final = frozenset(get_args(StepStatus))

#: Every rule id matches this; the part before the first dot is a :class:`Step` value.
RULE_ID_PATTERN: Final = re.compile(r"^(?P<step>[A-Za-z]+)\.[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_valid_rule_id(rule_id: str, step: Step | str | None = None) -> bool:
    """Return True if ``rule_id`` follows the grammar (and belongs to ``step``, if given).

    Example:
        >>> is_valid_rule_id("dictionary.12", "dictionary")
        True
        >>> is_valid_rule_id("dictionary..12"), is_valid_rule_id("bogus.1")
        (False, False)
    """
    match = RULE_ID_PATTERN.match(rule_id)
    if match is None or match["step"] not in _STEP_VALUES:
        return False
    return step is None or match["step"] == str(step)


def _positive(name: str, value: object) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive int or None, got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """One problem found in one file.

    ``line`` and ``column`` are 1-based; ``column`` is known only for XML syntax
    errors. ``xpath`` is the official ``diggs:get-path`` form
    (``/diggs:Diggs[1]/diggs:samplingFeature[2]``). ``gml_id`` is the ``gml:id`` of
    the nearest element at or above the problem. ``source_snippet`` is the offending
    element's XML, only when snippets were asked for.

    Example:
        >>> f = Finding(
        ...     step=Step.DICTIONARY,
        ...     rule_id="dictionary.5",
        ...     severity=Severity.ERROR,
        ...     message='Code "x" is not found.',
        ...     line=12,
        ...     xpath="/diggs:Diggs[1]/diggs:Borehole[1]",
        ... )
        >>> f.conformance_key()
        ('dictionary', 'error', 'xpath:/Diggs[1]/Borehole[1]', 'dictionary.5')
        >>> Finding(step="schema", rule_id="dictionary.5", severity="error", message="")
        Traceback (most recent call last):
        ...
        ValueError: rule_id 'dictionary.5' is not a valid rule id for step 'schema'
    """

    layer: Layer = "native"
    step: Step
    rule_id: str
    severity: Severity
    message: str
    line: int | None = None
    column: int | None = None
    xpath: str | None = None
    gml_id: str | None = None
    source_snippet: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step", Step(self.step))
        object.__setattr__(self, "severity", Severity(self.severity))
        if self.layer not in _LAYERS:
            raise ValueError(f"layer must be one of {sorted(_LAYERS)}, got {self.layer!r}")
        if not isinstance(self.rule_id, str) or not is_valid_rule_id(self.rule_id, self.step):
            raise ValueError(
                f"rule_id {self.rule_id!r} is not a valid rule id for step {self.step.value!r}"
            )
        if not isinstance(self.message, str):
            raise TypeError(f"message must be str, got {type(self.message).__name__}")
        _positive("line", self.line)
        _positive("column", self.column)
        for name in ("xpath", "gml_id", "source_snippet"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be str or None, got {type(value).__name__}")

    def conformance_key(self) -> ConformanceKey:
        """``(step, severity, location, rule_id)`` used to match findings across layers.

        The location is ``"xpath:"`` plus the normalised xpath when there is one, else
        ``"line:<n>"``, else ``"file"``. The message is deliberately left out.
        """
        return (
            self.step.value,
            self.severity.value,
            location_key(self.xpath, self.line),
            self.rule_id,
        )

    def sort_key(self) -> tuple[object, ...]:
        """Deterministic order within a file: step, line, column, severity, rule, xpath, text.

        Findings without a line come first; at the same place, errors come first. The
        remaining fields break any tie, so the order is total.
        """
        return (
            self.step.order,
            self.line is not None,
            self.line or 0,
            self.column is not None,
            self.column or 0,
            -self.severity.rank,
            self.rule_id,
            self.xpath is not None,
            self.xpath or "",
            self.message,
            self.layer,
            self.gml_id is not None,
            self.gml_id or "",
            self.source_snippet is not None,
            self.source_snippet or "",
        )


def _freeze(mapping: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, kw_only=True)
class Report:
    """Everything :func:`pydiggs.validate` found in one file.

    ``path`` is the path as reported: relative to the working directory when the file
    is under it, else the bare file name. ``absolute_path`` is only written out when
    asked for (``absolute_paths=True``). ``steps`` says what happened to each native
    step, in pipeline order; ``findings`` are sorted deterministically.

    Example:
        >>> r = Report(
        ...     path="site/BH-01.xml",
        ...     sha256="0" * 64,
        ...     diggs_version="3.0.0",
        ...     steps={Step.SCHEMA: "failed"},
        ...     findings=(
        ...         Finding(
        ...             step="schema",
        ...             rule_id="schema.cvc-elt.1",
        ...             severity="error",
        ...             message="No matching global declaration.",
        ...             line=2,
        ...         ),
        ...     ),
        ...     tool_version="1.2.0",
        ... )
        >>> r.ok, r.counts
        (False, {'error': 1, 'warning': 0, 'info': 0})
        >>> r.to_dict()["findings"][0]["location"]
        {'line': 2, 'column': None, 'xpath': None, 'gml_id': None}
    """

    path: str
    sha256: str
    diggs_version: str | None
    steps: Mapping[Step, StepStatus] = field(hash=False)
    findings: tuple[Finding, ...] = ()
    tool_version: str
    reference_data: Mapping[str, str] = field(default_factory=dict, hash=False)
    absolute_path: str | None = None
    status: FileStatus = "checked"

    def __post_init__(self) -> None:
        steps = {Step(k): v for k, v in self.steps.items()}
        for step, status in steps.items():
            if status not in _STEP_STATUSES:
                raise ValueError(f"step {step.value!r} has unknown status {status!r}")
        object.__setattr__(self, "steps", _freeze(dict(sorted(steps.items(), key=_step_order))))
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=Finding.sort_key)))
        object.__setattr__(
            self, "reference_data", _freeze(dict(sorted(self.reference_data.items())))
        )

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        """Pickle support (``steps`` and ``reference_data`` are read-only mappings).

        Built from :func:`dataclasses.fields`, so a new field cannot be forgotten.
        """
        values: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            values[item.name] = dict(value) if isinstance(value, Mapping) else value
        return _rebuild_report, (values,)

    @property
    def ok(self) -> bool:
        """True when there is no error-severity finding."""
        return not any(f.severity is Severity.ERROR for f in self.findings)

    @property
    def verdict(self) -> Verdict:
        """One word for the result, derived only from status, steps and findings.

        In order: ``not-diggs`` (the root is not ``Diggs``); ``not-valid`` (any error,
        which includes every ``incomplete`` step); ``partly-checked`` (no error, but a
        step is ``partial`` or ``incomplete``); ``valid-with-warnings``; ``valid``.
        """
        if self.status == "not-diggs":
            return "not-diggs"
        if not self.ok:
            return "not-valid"
        if any(v in ("partial", "incomplete") for v in self.steps.values()):
            return "partly-checked"
        if any(f.severity is Severity.WARNING for f in self.findings):
            return "valid-with-warnings"
        return "valid"

    @property
    def counts(self) -> dict[str, int]:
        """Number of findings per severity, always with all three keys."""
        counts = {s.value: 0 for s in Severity}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return counts

    def to_dict(self, *, absolute_paths: bool = False) -> dict[str, Any]:
        """This file's entry of the ``diggs-findings`` JSON document (``files[]`` item)."""
        from pydiggs.findings import _json  # noqa: PLC0415  # _json imports this module

        return _json.file_entry(self, absolute_paths=absolute_paths)

    def to_json(self, indent: int | None = 2, *, absolute_paths: bool = False) -> str:
        """A complete ``diggs-findings`` 1.0 JSON document for this one file."""
        from pydiggs.findings import _json  # noqa: PLC0415  # _json imports this module

        return _json.dumps(
            _json.document(
                [self.to_dict(absolute_paths=absolute_paths)],
                tool_version=self.tool_version,
                reference_data=self.reference_data,
            ),
            indent=indent,
        )

    def to_sarif(self, *, absolute_paths: bool = False) -> dict[str, Any]:
        """A SARIF 2.1.0 log (as a JSON-serialisable dict) for this one file."""
        from pydiggs.findings import _sarif  # noqa: PLC0415  # _sarif imports this module

        return _sarif.sarif_log(
            [self], [], tool_version=self.tool_version, absolute_paths=absolute_paths
        )


def _rebuild_report(values: dict[str, Any]) -> Report:
    return Report(**values)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnreadableFile:
    """A batch input that could not be read; it becomes a ``status: unreadable`` entry."""

    path: str
    error: str
    absolute_path: str | None = None


def _step_order(item: tuple[Step, object]) -> int:
    return item[0].order
