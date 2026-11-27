"""The owner's acceptance check of one delivered DIGGS file (``pydiggs conformance accept``).

A conformance class tests software against a challenge whose content is known. An owner
who receives a file has no such key: the check here is what can be said of any file
without one. It is valid DIGGS 3, and it holds what DIGGS defines for the family it is
delivered as, by published requirements (:func:`requirements`). The file is named by its
SHA-256, so a report cannot be moved to another file.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from html import escape
from typing import Any, Final

from pydiggs import __version__
from pydiggs.conformance import _registry
from pydiggs.conformance._catalogue import Family, _load, families
from pydiggs.conformance._challenge import SUITE_VERSION
from pydiggs.conformance._requirements import COMMON, MAX_ITEMS, Check, Requirement
from pydiggs.errors import DocumentError
from pydiggs.findings.run import DEFAULT_MAX_BYTES

__all__ = [
    "ACCEPTANCE_SCHEMA_URL",
    "ACCEPTANCE_SCHEMA_VERSION",
    "ACCEPTANCE_STATEMENT",
    "Acceptance",
    "Outcome",
    "accept",
    "acceptance_families",
    "requirements",
]

#: The version of the diggs-conformance-acceptance document; preview.
ACCEPTANCE_SCHEMA_VERSION: Final = "1.0-beta.1"
#: The resource name of its packaged JSON Schema.
ACCEPTANCE_SCHEMA_RESOURCE: Final = "diggs-conformance-acceptance-1.0-beta.1.schema.json"
#: Where that schema is published; the ``$id`` of the schema.
#: (The schema's own ``$id`` spells it out in full.)
ACCEPTANCE_SCHEMA_URL: Final = "https://xinp-hub.github.io/pydiggs/" + (
    "schemas/preview/" + ACCEPTANCE_SCHEMA_RESOURCE
)

#: What an acceptance check does and does not show; every document carries it.
ACCEPTANCE_STATEMENT: Final = (
    "Acceptance check. This records that the file with this SHA-256 is valid DIGGS 3 by"
    " pyDIGGS's validator and holds what the published requirements of the named family"
    " ask, each taken from DIGGS 3.0.0 or its dictionaries. It does not compare any value"
    " with the field or laboratory records, does not show that every test performed was"
    " delivered, makes no engineering judgement, does not show which software wrote the"
    " file, and is not a certification by the DIGGS committee or the ASCE Geo-Institute."
)

#: What the check does not prove, item by item, for the reports.
NOT_SHOWN: Final = (
    "that any value is right: nothing is compared with the field or laboratory records",
    "that every test, sample or pile of the job is in the file",
    "anything about engineering adequacy: pyDIGGS makes no engineering calculation",
    "which software wrote the file",
    "a certification by the DIGGS committee or the ASCE Geo-Institute",
)

_MARK: Final = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIPPED"}


@dataclass(frozen=True, slots=True)
class Outcome:
    """One requirement and what the file showed for it.

    ``status`` is ``pass``, ``fail`` or ``skipped`` (the file could not be read, or not
    to its end, so the requirement was not looked at). ``items`` is (what, message) per
    failure, at most 200; ``failed`` counts them all.
    """

    requirement: Requirement
    status: str
    passed: int
    total: int
    items: tuple[tuple[str, str], ...] = ()
    failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """The outcome as the acceptance document holds it."""
        return {
            **self.requirement.to_dict(),
            "status": self.status,
            "passed": self.passed,
            "total": self.total,
            "failed": self.failed,
            "items": [{"id": what, "message": message} for what, message in self.items],
            "items_truncated": self.failed > len(self.items),
        }


@dataclass(frozen=True, slots=True)
class Acceptance:
    """The result of checking one delivered file against one family's requirements.

    ``accepted`` is the verdict: every requirement passed. :meth:`to_dict` is the
    machine-readable document; :meth:`to_html` the one-page report for the owner.
    """

    family: Family
    diggs_version: str
    file_name: str
    file_sha256: str
    file_bytes: int
    file_diggs_version: str | None
    outcomes: tuple[Outcome, ...]

    @property
    def accepted(self) -> bool:
        """Whether every requirement passed."""
        return all(outcome.status == "pass" for outcome in self.outcomes)

    def outcome(self, requirement_id: str) -> Outcome:
        """The outcome of ``requirement_id``.

        Raises:
            KeyError: The family has no such requirement.
        """
        for outcome in self.outcomes:
            if outcome.requirement.id == requirement_id:
                return outcome
        raise KeyError(requirement_id)

    def to_dict(self) -> dict[str, Any]:
        """The result as plain data: a ``diggs-conformance-acceptance`` document (preview).

        File text (names, messages) is kept as it is; escape it before showing it.
        """
        return {
            "$schema": ACCEPTANCE_SCHEMA_URL,
            "format": "diggs-conformance-acceptance",
            "schema_version": ACCEPTANCE_SCHEMA_VERSION,
            "suite_version": SUITE_VERSION,
            "tool": {"name": "pydiggs", "version": __version__},
            "statement": ACCEPTANCE_STATEMENT,
            "family": {
                "id": self.family.id,
                "title": self.family.title,
                "diggs_version": self.diggs_version,
            },
            "file": {
                "name": self.file_name,
                "sha256": self.file_sha256,
                "bytes": self.file_bytes,
                "diggs_version": self.file_diggs_version,
            },
            "verdict": "pass" if self.accepted else "fail",
            "requirements": [outcome.to_dict() for outcome in self.outcomes],
            "not_shown": list(NOT_SHOWN),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """The result as JSON text."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_text(self) -> str:
        """A report for a terminal; control characters in file text are spelled out."""
        from pydiggs.findings._cli import _safe  # noqa: PLC0415  # the CLI's own escaping

        verdict = "ACCEPTED" if self.accepted else "NOT ACCEPTED"
        lines = [
            f"{self.family.id}: {verdict}",
            f"  file     {_safe(self.file_name)}, {self.file_bytes} bytes",
            f"  sha256   {self.file_sha256}",
            f"  checked  {self.family.title} requirements, suite {SUITE_VERSION},"
            f" DIGGS {self.diggs_version}",
            "",
        ]
        for outcome in self.outcomes:
            count = f"{outcome.passed}/{outcome.total}" if outcome.total else "-"
            lines.append(
                f"  {_MARK[outcome.status]:<8} {outcome.requirement.id:<16} ({count})"
                f" {outcome.requirement.text}"
            )
            for what, message in outcome.items[:20]:
                lines.append(f"           {_safe(what)}: {_safe(message)}")
            if outcome.failed > min(len(outcome.items), 20):
                lines.append(f"           … {outcome.failed} in all; the JSON lists up to 200")
        lines += ["", "  This does not show:"]
        lines += [f"  - {item}" for item in NOT_SHOWN]
        return "\n".join(lines) + "\n"

    def to_html(self) -> str:
        """The one-page HTML report; every value from the file is escaped."""
        from pydiggs.conformance._report import _STYLE  # noqa: PLC0415  # rendering on demand

        verdict = "pass" if self.accepted else "fail"
        rows = []
        for outcome in self.outcomes:
            count = f"{outcome.passed}/{outcome.total}" if outcome.total else ""
            rows.append(
                f'<tr><td><span class="v {outcome.status}">{_MARK[outcome.status]}</span></td>'
                f"<td><code>{escape(outcome.requirement.id)}</code></td>"
                f"<td>{escape(outcome.requirement.text)}"
                f'<br><span class="note">{escape(outcome.requirement.source)}</span></td>'
                f"<td>{count}</td></tr>"
            )
        items = [
            f"<tr><td><code>{escape(outcome.requirement.id)}</code></td>"
            f"<td>{escape(what)}</td><td>{escape(message)}</td></tr>"
            for outcome in self.outcomes
            for what, message in outcome.items
        ]
        failures = (
            "<h2>What failed</h2><table><tr><th>Requirement</th><th>Where</th><th>What</th>"
            f"</tr>{''.join(items)}</table>"
            if items
            else ""
        )
        not_shown = "".join(f"<li>{escape(item)}</li>" for item in NOT_SHOWN)
        word = "ACCEPTED" if self.accepted else "NOT ACCEPTED"
        version = escape(self.file_diggs_version or "unknown")
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(self.family.id)} acceptance check</title><style>{_STYLE}</style>"
            f"</head><body><main><h1>Acceptance check: {escape(self.family.title)}</h1>"
            f'<p><span class="v {verdict}">{word}</span> <code>{escape(self.file_name)}</code>'
            f" ({self.file_bytes} bytes, DIGGS {version})</p>"
            f"<p>SHA-256 <code>{escape(self.file_sha256)}</code></p>"
            f'<p class="note">pyDIGGS {escape(__version__)}, conformance suite'
            f" {escape(SUITE_VERSION)}, requirements of <code>{escape(self.family.id)}</code>"
            f" from DIGGS {escape(self.diggs_version)} and its dictionaries.</p>"
            "<table><tr><th>Result</th><th>Requirement</th><th>What must hold, and where"
            f" DIGGS says so</th><th>Held</th></tr>{''.join(rows)}</table>{failures}"
            "<h2>What this shows, and what it does not</h2>"
            "<p>It shows that this exact file (by its SHA-256) is valid DIGGS 3 by pyDIGGS's"
            " validator and holds what the requirements above ask.</p>"
            f"<p>It does not show:</p><ul>{not_shown}</ul>"
            '<p class="note">Anyone can re-run it: <code>pydiggs conformance accept FILE'
            f" --class {escape(self.family.id)}</code>.</p></main></body></html>\n"
        )


def acceptance_families() -> tuple[Family, ...]:
    """The families a delivered file can be checked as: every family with a scenario.

    Examples:
        >>> from pydiggs import conformance
        >>> ids = [f.id for f in conformance.acceptance_families()]
        >>> ids[:5]
        ['spt', 'cpt', 'dissipation', 'in-situ-strength', 'mwd']
        >>> ids[5:]
        ['lab-index', 'deep-foundations', 'monitoring']
    """
    return tuple(f for f in families() if f.testable and not f.embedded)


def _family(family_id: str) -> Family:
    for family in acceptance_families():
        if family.id == family_id:
            return family
    known = ", ".join(f.id for f in acceptance_families())
    raise ValueError(f"no acceptance requirements for {family_id!r}; the families are: {known}")


def requirements(family: str) -> tuple[Requirement, ...]:
    """The published requirements a delivered file of ``family`` must meet.

    Raises:
        ValueError: ``family`` has no requirements; the message lists the ones that do.

    Examples:
        >>> from pydiggs import conformance
        >>> [r.id for r in conformance.requirements("spt")][2:]
        ['accept.present', 'accept.located', 'accept.result', 'accept.position']
    """
    return _registry.acceptance(_family(family)).requirements


def _from_check(requirement: Requirement, check: Check) -> Outcome:
    return Outcome(
        requirement,
        "fail" if check.failed else "pass",
        check.passed,
        check.total,
        check.items,
        check.failed,
    )


def accept(
    source: str | os.PathLike[str] | bytes,
    family: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Acceptance:
    """Check a delivered DIGGS file against the published requirements of ``family``.

    The file is untrusted: its size and the time spent reading and validating it are
    limited, and a file that hits a limit fails ``accept.diggs-3``; what the family asks
    of its content is then ``skipped``, since nothing true can be said of a file read in
    part. A file that is not DIGGS at all is a failing result, not an exception.

    Args:
        source: A path, or the bytes of the file.
        family: A family id, such as ``spt`` (see :func:`acceptance_families`).
        max_bytes: Largest file accepted (default 100 MiB).
        time_budget: Seconds for reading and for validating the file, each.

    Raises:
        ValueError: ``family`` has no acceptance requirements.
        InstanceReadError: The file is missing, unreadable or too large.

    Examples:
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("cpt-export", product="Demo", version="1", seed="demo")
        >>> result = conformance.accept(challenge.reference(), "cpt")
        >>> result.accepted, result.outcome("accept.result").total
        (True, 2)
        >>> conformance.accept(b"<not-diggs/>", "cpt").accepted
        False
    """
    from pydiggs import read_bytes, validate_bytes  # noqa: PLC0415  # lxml loads on demand
    from pydiggs.conformance._grade import _read_submission  # noqa: PLC0415  # sibling helper

    profile = _registry.acceptance(_family(family))
    name, data = _read_submission(source, max_bytes)
    diggs_version = _load()[0]
    digest = hashlib.sha256(data).hexdigest()
    limit = max(len(data), 1)
    try:
        model = read_bytes(data, name=name, max_bytes=limit, time_budget=time_budget)
    except DocumentError as exc:
        readable, valid = COMMON
        outcomes = (
            Outcome(readable, "fail", 0, 1, (("file", f"not a DIGGS document: {exc}"),), 1),
            Outcome(valid, "skipped", 0, 0),
            *(Outcome(r, "skipped", 0, 0) for r, _ in profile.checks),
        )
        return Acceptance(profile.family, diggs_version, name, digest, len(data), None, outcomes)
    report = validate_bytes(data, name=name, max_bytes=limit, time_budget=time_budget)
    version = report.diggs_version or model.source.diggs_version or None
    problems: list[tuple[str, str]] = []
    if not (version or "").startswith("3."):
        problems.append(("file", f"the file is DIGGS {version or 'of an unknown version'}, not 3"))
    if not model.complete:
        hit = ", ".join(model.limits_hit) or "the time budget"
        problems.append(("file", f"the file could not be read to its end ({hit})"))
    errors = [f for f in report.findings if f.severity.value == "error"]
    validation = Check(
        1 if report.ok else 0,
        1,
        tuple(
            (f"line {f.line}" if f.line else "file", f"{f.rule_id}: {f.message}")
            for f in errors[:MAX_ITEMS]
        ),
        0 if report.ok else len(errors),
    )
    if not report.ok and not errors:  # not ok without an error finding: say so
        validation = Check(0, 1, (("file", "validation did not complete"),), 1)
    readable, valid = COMMON
    # A model cut short holds part of the file: "no test", or "15 of 15 tests", would be
    # a statement about the part that was read. The content is not looked at.
    content = (
        (_from_check(requirement, check(model)) for requirement, check in profile.checks)
        if model.complete
        else (Outcome(requirement, "skipped", 0, 0) for requirement, _ in profile.checks)
    )
    outcomes = (
        _from_check(readable, Check(0 if problems else 1, 1, tuple(problems), len(problems))),
        _from_check(valid, validation),
        *content,
    )
    return Acceptance(profile.family, diggs_version, name, digest, len(data), version, outcomes)
