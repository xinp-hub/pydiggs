"""The round-trip matrix: which products passed which classes, and which pairs could meet.

Built from evidence documents, each verified first: a document counts only when grading
its challenge and its submission again gives it back (:func:`pydiggs.conformance.verify`).
Evidence that cannot be verified is listed with the reason and never counted.

What evidence can say today is "A passed the export class" and "B passed the import
class", each against the suite's own challenge. Whether B reads *A's* export needs an
import challenge made from A's export, which neither challenges nor evidence record yet,
so every round-trip cell is ``not-tested-together``. The cell's format is fixed now so
that such evidence, once it exists, fills it in (``read`` or ``not-read``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Final

from pydiggs.conformance._catalogue import DIRECTIONS, ClassInfo, classes
from pydiggs.conformance._challenge import SUITE_VERSION
from pydiggs.errors import PydiggsError
from pydiggs.findings.run import DEFAULT_MAX_BYTES

__all__ = [
    "MATRIX_SCHEMA_URL",
    "MATRIX_SCHEMA_VERSION",
    "Entry",
    "Matrix",
    "RoundTrip",
    "matrix",
]

#: The version of the diggs-conformance-matrix document; preview.
MATRIX_SCHEMA_VERSION: Final = "1.0-beta.1"
#: The resource name of its packaged JSON Schema.
MATRIX_SCHEMA_RESOURCE: Final = "diggs-conformance-matrix-1.0-beta.1.schema.json"
#: Where that schema is published; the ``$id`` of the schema.
#: (The schema's own ``$id`` spells it out in full.)
MATRIX_SCHEMA_URL: Final = "https://xinp-hub.github.io/pydiggs/" + (
    "schemas/preview/" + MATRIX_SCHEMA_RESOURCE
)
#: The name an evidence document must have to be found.
EVIDENCE_NAME: Final = "evidence.json"
#: At most this many evidence documents are read from one folder.
MAX_EVIDENCE: Final = 1000
#: At most this many folder entries are looked at while searching.
_MAX_ENTRIES: Final = 200_000
_MAX_EVIDENCE_BYTES: Final = 8 * 1024 * 1024

#: Why every round-trip cell says ``not-tested-together``, in every report.
ROUND_TRIP_NOTE: Final = (
    "Each product passed its class separately, against the suite's own challenge. No"
    " evidence yet records an import challenge made from another product's export, so no"
    " cell shows that one product's export was read by another."
)

_CELL: Final = {
    "pass": "pass",
    "fail": "FAIL",
    "not-run": "not run",
}


@dataclass(frozen=True, slots=True)
class Entry:
    """One evidence document found under the folder.

    ``verified`` says whether grading again gave the document back; ``reason`` says why
    not. Product, class and verdict are as the document states them (empty when it is not
    readable as evidence); only a verified entry counts.
    """

    path: str
    verified: bool
    reason: str
    product_name: str = ""
    product_version: str = ""
    class_id: str = ""
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        """The entry as plain data."""
        return {
            "path": self.path,
            "verified": self.verified,
            "reason": self.reason,
            "product": {"name": self.product_name, "version": self.product_version},
            "class": self.class_id,
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """One cell of a family's round-trip table: an exporter and an importer.

    Only products that passed (verified) are paired. ``status`` is ``read`` or
    ``not-read`` when evidence of an import challenge made from the exporter's file
    exists, and ``not-tested-together`` until then, which is always today.
    """

    family: str
    exporter: tuple[str, str]
    importer: tuple[str, str]
    status: str = "not-tested-together"

    def to_dict(self) -> dict[str, Any]:
        """The cell as plain data."""
        return {
            "family": self.family,
            "export": {"name": self.exporter[0], "version": self.exporter[1]},
            "import": {"name": self.importer[0], "version": self.importer[1]},
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Matrix:
    """Products against classes, from verified evidence, and the round-trip pairs."""

    entries: tuple[Entry, ...]

    @property
    def classes(self) -> tuple[ClassInfo, ...]:
        """Every class the suite has, in catalogue order."""
        return classes()

    @property
    def products(self) -> tuple[tuple[str, str], ...]:
        """Every product with verified evidence, as (name, version), sorted."""
        return tuple(
            sorted({(e.product_name, e.product_version) for e in self.entries if e.verified})
        )

    @property
    def unverified(self) -> tuple[Entry, ...]:
        """The evidence that could not be verified, and so does not count."""
        return tuple(e for e in self.entries if not e.verified)

    def cell(self, product: tuple[str, str], class_id: str) -> str:
        """``pass``, ``fail`` or ``not-run`` for ``product`` in ``class_id``.

        A class passes when any verified evidence of it passed: a vendor may grade again
        until the file passes, as the suite allows.
        """
        verdicts = {
            e.verdict
            for e in self.entries
            if e.verified
            and (e.product_name, e.product_version) == product
            and e.class_id == class_id
        }
        if "pass" in verdicts:
            return "pass"
        return "fail" if verdicts else "not-run"

    def round_trips(self) -> tuple[RoundTrip, ...]:
        """Every (exporter, importer) pair of each family where both passed."""
        out: list[RoundTrip] = []
        for family in dict.fromkeys(c.family for c in self.classes):
            exporters = [p for p in self.products if self.cell(p, f"{family}-export") == "pass"]
            importers = [p for p in self.products if self.cell(p, f"{family}-import") == "pass"]
            out.extend(RoundTrip(family, a, b) for a in exporters for b in importers)
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        """The matrix as a ``diggs-conformance-matrix`` document (preview).

        File text (paths, product names) is kept as it is; escape it before showing it.
        """
        return {
            "$schema": MATRIX_SCHEMA_URL,
            "format": "diggs-conformance-matrix",
            "schema_version": MATRIX_SCHEMA_VERSION,
            "suite_version": SUITE_VERSION,
            "tier": "self-tested",
            "classes": [c.id for c in self.classes],
            "products": [
                {
                    "name": name,
                    "version": version,
                    "classes": {c.id: self.cell((name, version), c.id) for c in self.classes},
                }
                for name, version in self.products
            ],
            "round_trips": [r.to_dict() for r in self.round_trips()],
            "round_trip_note": ROUND_TRIP_NOTE,
            "evidence": [e.to_dict() for e in self.entries],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """The matrix as JSON text."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_text(self) -> str:
        """The matrix as plain text; control characters in file text are spelled out."""
        from pydiggs.findings._cli import _safe  # noqa: PLC0415  # the CLI's own escaping

        products = [f"{name} {version}" for name, version in self.products]
        width = max([len(c.id) for c in self.classes] + [5])
        lines = [
            f"Round-trip matrix, suite {SUITE_VERSION}, self-tested:"
            f" {len(self.products)} product(s) with verified evidence,"
            f" {len(self.unverified)} evidence file(s) not verified",
            "",
        ]
        for product in self.products:
            lines.append(_safe(f"{product[0]} {product[1]}"))
            lines += [f"  {c.id:<{width}}  {_CELL[self.cell(product, c.id)]}" for c in self.classes]
        if not products:
            lines.append("No verified evidence.")
        lines += ["", "Round trips (export by A, import by B):"]
        trips = self.round_trips()
        lines += [
            _safe(
                f"  {r.family}: {r.exporter[0]} {r.exporter[1]} -> {r.importer[0]}"
                f" {r.importer[1]}: {r.status}"
            )
            for r in trips
        ] or ["  none: no family has a product that passed export and one that passed import"]
        lines += ["", f"  {ROUND_TRIP_NOTE}"]
        if self.unverified:
            lines += ["", "Not verified, not counted:"]
            lines += [_safe(f"  {e.path}: {e.reason}") for e in self.unverified]
        return "\n".join(lines) + "\n"

    def to_markdown(self) -> str:
        """The matrix as a Markdown table, for a README or a product page."""
        from pydiggs.conformance._report import _markdown  # noqa: PLC0415  # shared escaping

        head = ["Product", *(c.id for c in self.classes)]
        lines = [
            f"Round-trip matrix (pyDIGGS conformance suite {SUITE_VERSION}, self-tested,"
            " verified evidence only).",
            "",
            "| " + " | ".join(head) + " |",
            "| " + " | ".join("---" for _ in head) + " |",
        ]
        for product in self.products:
            cells = [_CELL[self.cell(product, c.id)] for c in self.classes]
            lines.append(
                f"| {_markdown(f'{product[0]} {product[1]}')} | " + " | ".join(cells) + " |"
            )
        lines += ["", _markdown(ROUND_TRIP_NOTE)]
        if self.unverified:
            lines += ["", "Not verified, not counted:", ""]
            lines += [f"- {_markdown(e.path)}: {_markdown(e.reason)}" for e in self.unverified]
        return "\n".join(lines) + "\n"

    def to_html(self) -> str:
        """The matrix as one self-contained HTML page; every value is escaped."""
        from pydiggs.conformance._report import _STYLE  # noqa: PLC0415  # shared look

        head = "".join(f"<th><code>{escape(c.id)}</code></th>" for c in self.classes)
        rows = []
        for product in self.products:
            cells = []
            for c in self.classes:
                cell = self.cell(product, c.id)
                css = {"pass": "pass", "fail": "fail"}.get(cell, "skipped")
                cells.append(f'<td><span class="v {css}">{escape(_CELL[cell])}</span></td>')
            name = escape(f"{product[0]} {product[1]}")
            rows.append(f"<tr><th>{name}</th>{''.join(cells)}</tr>")
        table = (
            f"<table><tr><th>Product (as declared)</th>{head}</tr>{''.join(rows)}</table>"
            if rows
            else "<p>No verified evidence.</p>"
        )
        trips = "".join(
            f"<tr><td><code>{escape(r.family)}</code></td>"
            f"<td>{escape(f'{r.exporter[0]} {r.exporter[1]}')}</td>"
            f"<td>{escape(f'{r.importer[0]} {r.importer[1]}')}</td>"
            f"<td>{escape(r.status)}</td></tr>"
            for r in self.round_trips()
        )
        trip_table = (
            "<table><tr><th>Family</th><th>Export by</th><th>Import by</th><th>Status</th></tr>"
            f"{trips}</table>"
            if trips
            else "<p>No family has a product that passed export and one that passed import.</p>"
        )
        bad = "".join(
            f"<tr><td><code>{escape(e.path)}</code></td><td>{escape(e.reason)}</td></tr>"
            for e in self.unverified
        )
        unverified = (
            "<h2>Not verified, not counted</h2><table><tr><th>Evidence</th><th>Why</th></tr>"
            f"{bad}</table>"
            if bad
            else ""
        )
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Round-trip matrix</title><style>{_STYLE}"
            "main{max-width:1100px}table{display:block;overflow-x:auto}</style></head>"
            "<body><main><h1>Round-trip matrix</h1>"
            f'<p class="note">pyDIGGS conformance suite {escape(SUITE_VERSION)}, self-tested.'
            " Only evidence that verified (grading its challenge and submission again gave it"
            " back) is counted.</p>"
            f"<h2>Classes passed</h2>{table}"
            f"<h2>Round trips</h2><p>{escape(ROUND_TRIP_NOTE)}</p>{trip_table}"
            f"{unverified}"
            '<p class="note">Not a certification by the DIGGS committee or the ASCE'
            " Geo-Institute. A product's name and version are as its vendor declared them.</p>"
            "</main></body></html>\n"
        )


def _evidence_files(folder: Path) -> Iterator[Path]:
    """Every ``evidence.json`` under ``folder``, in sorted order, without following links.

    One that is itself a link is yielded too: it is never read, and is listed as such.
    """
    seen = 0
    found = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        seen += len(dirs) + len(files)
        if seen > _MAX_ENTRIES:
            raise ValueError(f"{folder} holds more than {_MAX_ENTRIES} files and folders")
        dirs.sort()
        if EVIDENCE_NAME in files:
            path = Path(root) / EVIDENCE_NAME
            found += 1
            if found > MAX_EVIDENCE:
                raise ValueError(f"{folder} holds more than {MAX_EVIDENCE} evidence documents")
            yield path


def _submission(folder: Path, name: object) -> Path | None:
    """The submitted file named in the evidence, in the evidence's own folder, or None.

    The name is untrusted: only a plain file name, never a path, and never a link.
    """
    if not isinstance(name, str) or not name or name in (".", "..") or Path(name).name != name:
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    path = folder / name
    return path if path.is_file() and not path.is_symlink() else None


class _UnverifiedError(Exception):
    """Why an evidence document does not count."""


def _document(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise _UnverifiedError("too large to be conformance evidence")
        document = json.loads(path.read_bytes())
    except OSError as exc:
        raise _UnverifiedError(f"cannot be read: {exc.strerror or exc}") from None
    except (ValueError, RecursionError):
        raise _UnverifiedError("not JSON") from None
    if not isinstance(document, dict) or document.get("format") != "diggs-conformance":
        raise _UnverifiedError("not a diggs-conformance document")
    return document


def _stated(document: dict[str, Any]) -> dict[str, str]:
    """What the document says of itself: product, class and verdict."""
    product, tested = document.get("product"), document.get("class")
    if not (
        isinstance(product, dict)
        and isinstance(tested, dict)
        and all(isinstance(product.get(k), str) for k in ("name", "version"))
        and isinstance(tested.get("id"), str)
        and tested.get("direction") in DIRECTIONS
        and document.get("verdict") in ("pass", "fail")
    ):
        raise _UnverifiedError("a malformed diggs-conformance document")
    return {
        "product_name": product["name"],
        "product_version": product["version"],
        "class_id": tested["id"],
        "verdict": document["verdict"],
    }


def _entry(path: Path, base: Path, max_bytes: int, time_budget: float | None) -> Entry:
    from pydiggs.conformance._grade import verify  # noqa: PLC0415  # grading loads on demand

    shown = path.relative_to(base).as_posix()
    stated: dict[str, str] = {}
    try:
        if path.is_symlink():
            # Never followed: a link can point outside the folder being laid out.
            raise _UnverifiedError("a link, not read")
        document = _document(path)
        stated = _stated(document)
        submission = document.get("submission")
        named = submission.get("name") if isinstance(submission, dict) else None
        found = _submission(path.parent, named)
        if found is None:
            raise _UnverifiedError(
                "the submitted file it names is not beside it, so it cannot be graded again"
            )
        try:
            outcome = verify(
                document, path.parent, found, max_bytes=max_bytes, time_budget=time_budget
            )
        except (PydiggsError, ValueError, OSError) as exc:
            raise _UnverifiedError(f"cannot be graded again: {exc}") from None
        if not outcome.ok:
            parts = ", ".join(outcome.differences)
            raise _UnverifiedError(f"not reproduced: grading again differs in {parts}")
    except _UnverifiedError as exc:
        return Entry(shown, False, str(exc), **stated)
    return Entry(shown, True, "reproduced", **stated)


def matrix(
    folder: str | os.PathLike[str],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Matrix:
    """Build the round-trip matrix from every ``evidence.json`` under ``folder``.

    Each evidence document must sit in its challenge folder (where ``grade`` writes it)
    with the file it graded beside it, under the name the evidence gives. It is verified
    first; evidence that does not verify (tampered, of another suite version, its file
    missing) is listed in :attr:`Matrix.unverified` and never counted. Links are not
    followed: an ``evidence.json`` that is a link is listed as not read.

    Raises:
        ValueError: ``folder`` is not a folder, or holds too many files or evidence
            documents (more than 1000).

    Examples:
        >>> import tempfile
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("spt-export", product="Demo", version="1", seed="demo")
        >>> folder = challenge.write(tempfile.mkdtemp() + "/spt")
        >>> _ = (folder / "export.xml").write_bytes(challenge.reference())
        >>> evidence = conformance.grade(folder, folder / "export.xml").to_json()
        >>> _ = (folder / "evidence.json").write_text(evidence, encoding="utf-8")
        >>> found = conformance.matrix(folder.parent)
        >>> found.cell(("Demo", "1"), "spt-export"), len(found.unverified)
        ('pass', 0)
    """
    base = Path(folder)
    if not base.is_dir():
        raise ValueError(f"{base} is not a folder")
    entries = tuple(_entry(path, base, max_bytes, time_budget) for path in _evidence_files(base))
    return Matrix(entries)
