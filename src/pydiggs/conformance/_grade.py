"""Grading a submission against a challenge, and the evidence document that records it.

Grading is deterministic: it reads no clock and no network, so the same challenge and
the same submission give the same evidence on any machine. That is what lets anyone
re-run a result (:func:`verify`).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from pydiggs import __version__
from pydiggs.conformance import _facts, _registry
from pydiggs.conformance._catalogue import ClassInfo
from pydiggs.conformance._challenge import (
    SUITE_VERSION,
    Challenge,
    SourceExport,
    honours_null_value,
    load,
)
from pydiggs.errors import DocumentError, InstanceReadError
from pydiggs.findings.run import DEFAULT_MAX_BYTES

__all__ = [
    "CONFORMANCE_SCHEMA_URL",
    "CONFORMANCE_SCHEMA_VERSION",
    "STATEMENT",
    "Item",
    "Result",
    "Stage",
    "Verification",
    "grade",
    "verify",
]

CONFORMANCE_SCHEMA_VERSION: Final = "1.0-beta.3"
#: Every evidence schema version this release reads (verify, scorecard).
READ_SCHEMA_VERSIONS: Final = ("1.0-beta.1", "1.0-beta.2", "1.0-beta.3")
CONFORMANCE_SCHEMA_URL: Final = (
    "https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.3.schema.json"
)
SCHEMA_RESOURCE: Final = "diggs-conformance-1.0-beta.3.schema.json"

#: What a self-tested result does and does not show; every evidence document carries it.
STATEMENT: Final = (
    "Self-tested. This records that the submitted file passed or failed the named class of"
    " the pyDIGGS conformance suite. It does not show which software produced the file,"
    " and it is not a certification by the DIGGS committee or the ASCE Geo-Institute."
)

_MAX_ITEMS: Final = 200
_MAX_ANSWER_BYTES: Final = 1024 * 1024
_STAGE_TITLES: Final = {
    "readable": "The file is a DIGGS 3 document",
    "validation": "Valid DIGGS: schema, dictionaries and business rules",
    "core": "Project, locations and depths",
    "fidelity": "Every source value, at its depth",
    "values": "Values read from the file",
    "robustness": "The planted defect is reported",
}

#: Check id -> what went wrong, in a few words.
_WHY: Final = {
    "conformance.fact-missing": "not in the file",
    "conformance.value-differs": "the file gives another value",
    "conformance.project-name": "not carried exactly",
    "conformance.value-invented": "the file gives a value the source leaves empty",
    "conformance.answer-missing": "no answer",
    "conformance.answer-differs": "the answer differs",
}

Source = str | os.PathLike[str] | bytes


@dataclass(frozen=True, slots=True)
class Item:
    """One thing that failed: the check, what it was about, and what was found."""

    check: str
    id: str
    message: str
    expected: str | None = None
    got: str | None = None

    def to_dict(self) -> dict[str, str]:
        """The item as the evidence document holds it (empty values left out)."""
        out = {"check": self.check, "id": self.id, "message": self.message}
        if self.expected is not None:
            out["expected"] = self.expected
        if self.got is not None:
            out["got"] = self.got
        return out


@dataclass(frozen=True, slots=True)
class Stage:
    """One stage of grading.

    ``status`` is ``pass``, ``fail`` or ``skipped`` (an earlier stage stopped the
    grading). ``required`` says whether the stage decides the verdict. ``items`` lists
    what failed, at most 200; ``failed`` is how many things failed in all (for the
    validation stage, the number of validation errors).
    """

    id: str
    required: bool
    status: str
    passed: int
    total: int
    items: tuple[Item, ...] = ()
    failed: int = 0

    @property
    def title(self) -> str:
        """What the stage checks, in a few words."""
        return _STAGE_TITLES[self.id]

    def to_dict(self) -> dict[str, Any]:
        """The stage as the evidence document holds it."""
        return {
            "id": self.id,
            "title": self.title,
            "required": self.required,
            "status": self.status,
            "passed": self.passed,
            "total": self.total,
            "failed": self.failed,
            "items": [item.to_dict() for item in self.items],
            "items_truncated": self.failed > len(self.items),
        }


@dataclass(frozen=True, slots=True)
class Result:
    """The result of grading one submission.

    ``passed`` is the verdict: every required stage passed. :meth:`to_dict` is the
    evidence, a diggs-conformance document. ``suite_version`` is the suite that made the
    challenge, whose rules graded the submission. ``source_export`` is None, except for
    the answers to an import challenge made from another product's export: then it names
    that export (:class:`SourceExport`), and the evidence carries it.
    """

    info: ClassInfo
    product_name: str
    product_version: str
    seed: str
    token: str
    challenge_id: str
    challenge_sha256: str
    submission_name: str
    submission_sha256: str
    submission_bytes: int
    stages: tuple[Stage, ...]
    suite_version: str = SUITE_VERSION
    source_export: SourceExport | None = None

    @property
    def passed(self) -> bool:
        """Whether every required stage passed."""
        return all(stage.status == "pass" for stage in self.stages if stage.required)

    def stage(self, stage_id: str) -> Stage:
        """The stage ``stage_id``.

        Raises:
            KeyError: The result has no such stage.
        """
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(stage_id)

    def to_dict(self) -> dict[str, Any]:
        """The evidence: a diggs-conformance document (see :func:`conformance_schema`)."""
        document: dict[str, Any] = {
            "$schema": CONFORMANCE_SCHEMA_URL,
            "format": "diggs-conformance",
            "schema_version": CONFORMANCE_SCHEMA_VERSION,
            "suite_version": self.suite_version,
            "tool": {"name": "pydiggs", "version": __version__},
            "tier": "self-tested",
            "statement": STATEMENT,
            "class": self.info.to_dict(),
            "product": {"name": self.product_name, "version": self.product_version},
            "challenge": {
                "id": self.challenge_id,
                "seed": self.seed,
                "token": self.token,
                "sha256": self.challenge_sha256,
            },
        }
        if self.source_export is not None:
            # Only a pairwise import result has it; without it a document means what
            # it always has: the challenge's file was the suite's own.
            document["source_export"] = self.source_export.to_dict()
        document["submission"] = {
            "name": self.submission_name,
            "sha256": self.submission_sha256,
            "bytes": self.submission_bytes,
        }
        document["verdict"] = "pass" if self.passed else "fail"
        document["stages"] = [stage.to_dict() for stage in self.stages]
        return document

    def to_json(self, *, indent: int | None = 2) -> str:
        """The evidence as JSON text."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_text(self) -> str:
        """A report for a terminal; control characters in file text are spelled out."""
        from pydiggs.conformance import _report  # noqa: PLC0415  # rendering loads on demand

        return _report.text(self)

    def to_html(self) -> str:
        """A self-contained HTML report; every value from a file is escaped."""
        from pydiggs.conformance import _report  # noqa: PLC0415  # rendering loads on demand

        return _report.html(self)


@dataclass(frozen=True, slots=True)
class Verification:
    """Whether re-grading gave the evidence again.

    ``differences`` names the top-level parts of the document that differ; ``result`` is
    the fresh grading. ``notes`` says what differs without deciding anything: the digest of
    the challenge's files, which another pyDIGGS release may write differently.
    """

    ok: bool
    differences: tuple[str, ...]
    result: Result
    notes: tuple[str, ...] = ()


def _stage(stage_id: str, outcomes: list[_facts.Outcome], *, required: bool = True) -> Stage:
    failed = [o for o in outcomes if o.status != "found"]
    items = tuple(
        Item(
            check=o.check or "conformance.fact-missing",
            id=o.id,
            message=f"{o.shown}: {_WHY[o.check or 'conformance.fact-missing']}",
            expected=o.expected,
            got=o.got,
        )
        for o in failed[:_MAX_ITEMS]
    )
    return Stage(
        id=stage_id,
        required=required,
        status="fail" if failed else "pass",
        passed=len(outcomes) - len(failed),
        total=len(outcomes),
        items=items,
        failed=len(failed),
    )


def _skipped(stage_id: str, total: int = 0, *, required: bool = True) -> Stage:
    return Stage(id=stage_id, required=required, status="skipped", passed=0, total=total)


def _read_submission(source: Source, max_bytes: int) -> tuple[str, bytes]:
    if isinstance(source, bytes):
        name, data = "submission", source
    else:
        path = Path(source)
        try:
            if path.stat().st_size > max_bytes:
                raise InstanceReadError(f"{path} is larger than {max_bytes} bytes")
            name, data = path.name, path.read_bytes()
        except OSError as exc:
            if isinstance(exc, InstanceReadError):
                raise
            raise InstanceReadError(f"cannot read {path}: {exc.strerror or exc}") from exc
    if len(data) > max_bytes:
        raise InstanceReadError(f"the submission is larger than {max_bytes} bytes")
    return name, data


def _grade_export(
    challenge: Challenge, name: str, data: bytes, time_budget: float | None
) -> tuple[Stage, ...]:
    from pydiggs import read_bytes, validate_bytes  # noqa: PLC0415  # lxml loads on demand

    key = _registry.facts(challenge.dataset())
    core = [f for f in key if f.group == "core"]
    fidelity = [f for f in key if f.group == "fidelity"]
    try:
        model = read_bytes(data, name=name, max_bytes=max(len(data), 1), time_budget=time_budget)
    except DocumentError as exc:
        item = Item("conformance.not-diggs", "readable", f"the file is not a DIGGS document: {exc}")
        return (
            Stage("readable", True, "fail", 0, 1, (item,), 1),
            _skipped("validation"),
            _skipped("core", len(core)),
            _skipped("fidelity", len(fidelity)),
        )
    report = validate_bytes(data, name=name, max_bytes=max(len(data), 1), time_budget=time_budget)
    version = report.diggs_version or ""
    readable: list[Item] = []
    if not version.startswith("3."):
        readable.append(
            Item(
                "conformance.diggs-version",
                "readable",
                "the file is not DIGGS 3",
                expected=challenge.info.diggs_version,
                got=version or "unknown",
            )
        )
    if not model.complete:
        readable.append(
            Item("conformance.incomplete", "readable", "the file could not be read to its end")
        )
    errors = [f for f in report.findings if f.severity.value == "error"]
    validation = Stage(
        "validation",
        True,
        "pass" if report.ok else "fail",
        1 if report.ok else 0,
        1,
        tuple(
            Item(f.rule_id, f"line {f.line}" if f.line else "file", f.message)
            for f in errors[:_MAX_ITEMS]
        ),
        len(errors),
    )
    # A challenge is graded by the rules of the suite that made it.
    nulls = honours_null_value(challenge.suite_version)
    seen = _registry.observe(challenge.info.scenario, model, nulls=nulls)
    return (
        Stage(
            "readable",
            True,
            "fail" if readable else "pass",
            0 if readable else 1,
            1,
            tuple(readable),
            len(readable),
        ),
        validation,
        _stage("core", [_facts.match(f, seen) for f in core]),
        _stage("fidelity", [_facts.match(f, seen) for f in fidelity]),
    )


def _answers(data: bytes) -> dict[str, str]:
    """The answers of a filled ``answers.csv``, or of a JSON object of id to answer."""
    if len(data) > _MAX_ANSWER_BYTES:
        raise DocumentError("not-answers", "the answers file is too large")
    try:
        text = data.decode("utf-8-sig")
        if text.lstrip().startswith("{"):
            loaded = json.loads(text)
            return {
                str(key): str(value).strip()
                for key, value in loaded.items()
                if isinstance(value, str | int | float) and not isinstance(value, bool)
            }
        rows = csv.DictReader(io.StringIO(text, newline=""))
        names = rows.fieldnames
    except (ValueError, csv.Error, RecursionError, AttributeError) as exc:
        # ValueError covers a decoding error and text that is not JSON.
        raise DocumentError("not-answers", f"the answers file cannot be read: {exc}") from exc
    if not names or not {"question_id", "answer"} <= set(names):
        raise DocumentError(
            "not-answers", "the answers file needs the columns question_id and answer"
        )
    try:
        return {
            (row.get("question_id") or "").strip(): (row.get("answer") or "").strip()
            for row in rows
        }
    except (csv.Error, AttributeError) as exc:
        raise DocumentError("not-answers", f"the answers file cannot be read: {exc}") from exc


def _given_number(answer: str) -> float | None:
    """The number an answer starts with, or None.

    A unit typed after the number ("12.5 ft") is tolerated; the asked unit applies. A
    comma is refused: "9,6" could be 9.6 or 96, and guessing would grade the wrong number.
    """
    words = answer.split()
    first = words[0] if words else ""
    if not first or "," in first:
        return None
    try:
        number = float(Decimal(first))
    except (ArithmeticError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _judge(question: _facts.Question, answer: str | None) -> _facts.Outcome:
    def outcome(status: str, check: str | None = None) -> _facts.Outcome:
        expected = f"{question.expected} {question.unit}".strip() if check else None
        return _facts.Outcome(
            question.id, question.group, status, question.text, check, expected, answer or None
        )

    if not answer:
        return outcome("missing", "conformance.answer-missing")
    if question.kind in ("text", "choice"):
        same = " ".join(answer.split()).casefold() == " ".join(question.expected.split()).casefold()
        return outcome("found") if same else outcome("differs", "conformance.answer-differs")
    given = _given_number(answer)
    if given is None:
        return outcome("differs", "conformance.answer-differs")
    close = abs(given - float(question.expected)) <= question.tol + 1e-9
    return outcome("found") if close else outcome("differs", "conformance.answer-differs")


def _grade_import(challenge: Challenge, data: bytes) -> tuple[Stage, ...]:
    asked = _registry.questions(challenge.dataset(), challenge.stream)
    given = _answers(data)
    judged = [_judge(q, given.get(q.id)) for q in asked]
    return (
        _stage("core", [o for o in judged if o.group == "core"]),
        _stage("values", [o for o in judged if o.group == "values"]),
        _stage("robustness", [o for o in judged if o.group == "robustness"], required=False),
    )


def grade(
    challenge: str | os.PathLike[str] | Challenge,
    submission: Source,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Result:
    """Grade ``submission`` against ``challenge``.

    For an export class the submission is the DIGGS file the software wrote; for an
    import class it is the filled ``answers.csv`` (or a JSON object of question id to
    answer). An export is graded in four stages (readable, validation, core, fidelity);
    an import in three (core, values, and robustness, which does not decide the verdict).
    The submission is untrusted: its size and the time spent on it are limited.

    A challenge is graded by the rules of the suite version that made it, which the
    evidence records: one made by an earlier suite this release still grades is graded
    as that suite did. The answers to an import challenge made from another product's
    export (``new(..., from_export=...)``) are graded like any other; the result and its
    evidence then name that export in ``source_export``.

    Args:
        challenge: A challenge folder, its ``challenge.json``, or a :class:`Challenge`.
        submission: A path, or the bytes of the file.
        max_bytes: Largest submission accepted (default 100 MiB).
        time_budget: Seconds for reading and for validating an export, each.

    Returns:
        The :class:`Result`. A file that fails, or is not DIGGS at all, is a failing
        result, not an exception.

    Raises:
        DocumentError: The challenge cannot be read or is from a suite version this
            release does not grade, the export a pairwise challenge was made from is not
            in its folder, or the answers of an import class cannot be read.
        InstanceReadError: The submission is missing, unreadable or too large.

    Examples:
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("spt-export", product="Demo", version="1", seed="demo")
        >>> result = conformance.grade(challenge, challenge.reference())
        >>> result.passed, [s.id for s in result.stages if s.status == "pass"]
        (True, ['readable', 'validation', 'core', 'fidelity'])
    """
    loaded = load(challenge, max_bytes=max_bytes, time_budget=time_budget)
    name, data = _read_submission(submission, max_bytes)
    if loaded.info.direction == "export":
        stages = _grade_export(loaded, name, data, time_budget)
    else:
        stages = _grade_import(loaded, data)
    return Result(
        info=loaded.info,
        product_name=loaded.product_name,
        product_version=loaded.product_version,
        seed=loaded.seed,
        token=loaded.token,
        challenge_id=loaded.id,
        challenge_sha256=loaded.sha256,
        submission_name=name,
        submission_sha256=hashlib.sha256(data).hexdigest(),
        submission_bytes=len(data),
        stages=stages,
        suite_version=loaded.suite_version,
        source_export=loaded.source_export,
    )


def verify(
    evidence: str | os.PathLike[str] | dict[str, Any],
    challenge: str | os.PathLike[str] | Challenge,
    submission: Source,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Verification:
    """Re-grade ``submission`` and say whether it gives ``evidence`` again.

    The verdict, the class, the product, the challenge, the submission's digest and every
    stage are compared: what was checked, the counts, and what was expected and found.
    The challenge is compared by its stable ``id``; a different digest of its files (which
    another pyDIGGS release may write differently) is a note, not a difference. Evidence
    of schema 1.0-beta.1, which has no ``id``, is compared by the digest as before.
    ``source_export`` is compared too, against what the challenge records: evidence of a
    pairwise import challenge cannot be edited to name another export, and evidence of
    any other challenge cannot be given one.

    Evidence of an earlier suite version this release still grades is graded again by
    that suite's rules, so it is reproduced as it was written.
    Not compared: ``tool`` (the pyDIGGS release), the submission's file name, and the
    wording of messages, which for a validation error is the XML library's own.

    The validation stage is pyDIGGS's validator of the release that graded, so a later
    release with more rules can report more errors for the same file; the evidence names
    the release that wrote it.

    Raises:
        DocumentError: The evidence is not a diggs-conformance document, or as
            :func:`grade`.
        InstanceReadError: As :func:`grade`, or the evidence file cannot be read.

    Examples:
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("cpt-export", product="Demo", version="1", seed="demo")
        >>> xml = challenge.reference()
        >>> evidence = conformance.grade(challenge, xml).to_dict()
        >>> conformance.verify(evidence, challenge, xml).ok
        True
        >>> evidence["verdict"] = "fail"
        >>> conformance.verify(evidence, challenge, xml).differences
        ('verdict',)
    """
    if isinstance(evidence, dict):
        document = evidence
    else:
        _, raw = _read_submission(evidence, _MAX_ANSWER_BYTES * 8)
        try:
            document = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise DocumentError("not-evidence", f"the evidence is not JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != "diggs-conformance":
        raise DocumentError("not-evidence", "the evidence is not a diggs-conformance document")
    if document.get("schema_version") not in READ_SCHEMA_VERSIONS:
        raise DocumentError(
            "not-evidence",
            f"evidence schema {document.get('schema_version')!r} is not one this release reads"
            f" ({', '.join(READ_SCHEMA_VERSIONS)})",
        )
    result = grade(challenge, submission, max_bytes=max_bytes, time_budget=time_budget)
    fresh = result.to_dict()
    differences = tuple(
        part
        for part in _COMPARED
        if _comparable(fresh, document).get(part) != _comparable(document, document).get(part)
    )
    if any(not isinstance(key, str) or key not in _MEMBERS for key in document):
        differences = (*differences, "unknown member")
    notes: tuple[str, ...] = ()
    given = document.get("challenge")
    if isinstance(given, dict) and "id" in given and given.get("sha256") != result.challenge_sha256:
        notes = (
            "the challenge's files are written differently by this pyDIGGS release"
            " (challenge.sha256 differs); the challenge itself (challenge.id) is the same",
        )
    return Verification(ok=not differences, differences=differences, result=result, notes=notes)


#: The members of an evidence document, and the ones verify compares. ``tool`` is not
#: compared (a later pyDIGGS release with the same suite version may verify), nor is
#: ``statement`` wording or the submission's file name.
_MEMBERS: Final = frozenset(
    [
        "$schema",
        "format",
        "schema_version",
        "suite_version",
        "tool",
        "tier",
        "statement",
        "class",
        "product",
        "challenge",
        "source_export",
        "submission",
        "verdict",
        "stages",
    ]
)
_COMPARED: Final = (
    "suite_version",
    "tier",
    "class",
    "product",
    "challenge",
    "source_export",
    "submission",
    "verdict",
    "stages",
)


def _comparable_stage(stage: object) -> object:
    if not isinstance(stage, dict):
        return stage
    items = stage.get("items")
    return {
        **{k: v for k, v in stage.items() if k not in ("items", "title")},
        "items": [
            {k: v for k, v in item.items() if k != "message"} if isinstance(item, dict) else item
            for item in (items if isinstance(items, list) else [])
        ],
    }


def _comparable(document: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """``document`` without what may differ between two honest runs.

    Messages are left out: a validation message is the XML library's own wording, which
    differs between builds. What was checked, the counts, and what was expected and found
    are all compared. The challenge is compared by its ``id`` when ``evidence`` (the
    document being verified) has one (diggs-conformance 1.0-beta.2), else by its digest
    (1.0-beta.1, which has no ``id``).
    """
    out = {part: document.get(part) for part in _COMPARED}
    submission = out["submission"]
    if isinstance(submission, dict):
        out["submission"] = {k: v for k, v in submission.items() if k != "name"}
    challenge, given = out["challenge"], evidence.get("challenge")
    if isinstance(challenge, dict):
        by_id = isinstance(given, dict) and "id" in given
        dropped = "sha256" if by_id else "id"
        out["challenge"] = {k: v for k, v in challenge.items() if k != dropped}
    stages = out["stages"]
    if isinstance(stages, list):
        out["stages"] = [_comparable_stage(stage) for stage in stages]
    return out
