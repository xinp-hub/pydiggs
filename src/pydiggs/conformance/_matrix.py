"""The round-trip matrix: which products passed which classes, and which pairs could meet.

Built from evidence documents, each verified first: a document counts only when grading
its challenge and its submission again gives it back (:func:`pydiggs.conformance.verify`).
Evidence that cannot be verified is listed with the reason and never counted.

Most evidence says "A passed the export class" or "B passed the import class", each
against the suite's own challenge. Testing B on *A's* export needs an import challenge
made from A's export (``new(..., from_export=...)``); the evidence graded from one carries
a ``source_export`` that names A's file by its SHA-256. That is B's statement, so it fills
a round-trip cell in only beside A's own passing export evidence, in the same folder and
verified: ``read`` when B's answers passed, ``not-read`` when they failed. A pair without
both documents is ``not-tested-together``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from typing import Any, Final

from pydiggs.conformance._catalogue import DIRECTIONS, ClassInfo, classes
from pydiggs.conformance._challenge import SUITE_VERSION, SourceExport
from pydiggs.conformance._pairwise import plain_name
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

#: Why every round-trip cell says ``not-tested-together``, when none says anything else.
ROUND_TRIP_NOTE: Final = (
    "Each product passed its class separately, against the suite's own challenge. No"
    " evidence yet records an import challenge made from another product's export, so no"
    " cell shows that one product's export was read by another."
)
#: What the cells mean, when evidence of a pairwise import challenge fills one in.
PAIRWISE_NOTE: Final = (
    "read: the exporting product's file, named by its SHA-256, passed the export class;"
    " that file was the one in the importing product's challenge; and the answers given"
    " for the importing product are right for it. Each of the three is verified here from"
    " evidence in this folder. not-read: the same, and none of the answers given passed."
    " not-tested-together: no such evidence, or the exporter's own evidence is not in"
    " this folder. Self-tested: a cell does not show which software opened the file, nor"
    " that the answers were taken from that file rather than from another copy of the"
    " same data."
)
#: On verified pairwise evidence that decides no cell, in place of "reproduced".
UNBOUND: Final = (
    "reproduced; it decides no round-trip cell: the passing export evidence it was made"
    " from (same family, seed, evidence digest and export digest) is not in this folder"
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
    readable as evidence); only a verified entry counts. ``sha256`` is the digest of the
    document as found, and ``submission_sha256`` the digest it records for the file it
    graded (both empty when it was not read); ``seed`` is the seed of its challenge.
    ``source_export`` is None, except for evidence of an import challenge made from
    another product's export: such an entry decides a round-trip cell and no class, and
    its ``reason`` says so when the exporter's own evidence is not in the folder.
    """

    path: str
    verified: bool
    reason: str
    product_name: str = ""
    product_version: str = ""
    class_id: str = ""
    verdict: str = ""
    sha256: str = ""
    submission_sha256: str = ""
    source_export: SourceExport | None = None
    seed: str = ""

    def to_dict(self) -> dict[str, Any]:
        """The entry as plain data."""
        out: dict[str, Any] = {
            "path": self.path,
            "verified": self.verified,
            "reason": self.reason,
            "product": {"name": self.product_name, "version": self.product_version},
            "class": self.class_id,
            "verdict": self.verdict,
            "sha256": self.sha256,
        }
        if self.source_export is not None:
            out["source_export"] = self.source_export.to_dict()
        return out


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """One cell of a family's round-trip table: an exporter and an importer.

    ``status`` is ``read`` when verified evidence of an import challenge made from the
    exporter's file passed, ``not-read`` when such evidence exists and none passed, and
    ``not-tested-together`` when there is none: then both products passed their class
    separately, against the suite's own challenge. A ``read`` or ``not-read`` cell cites
    what decided it: ``export_sha256``, the digest of the exported file in the challenge,
    and ``evidence``, the path of the import evidence under the matrix folder.
    """

    family: str
    exporter: tuple[str, str]
    importer: tuple[str, str]
    status: str = "not-tested-together"
    export_sha256: str | None = None
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The cell as plain data."""
        out: dict[str, Any] = {
            "family": self.family,
            "export": {"name": self.exporter[0], "version": self.exporter[1]},
            "import": {"name": self.importer[0], "version": self.importer[1]},
            "status": self.status,
        }
        if self.export_sha256 is not None:
            out["export_sha256"] = self.export_sha256
        if self.evidence is not None:
            out["evidence"] = self.evidence
        return out


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
        until the file passes, as the suite allows. Evidence of an import challenge made
        from another product's export does not count here: it decides a round-trip cell
        (:meth:`round_trips`), and a class is passed against the suite's own file.
        """
        verdicts = {
            e.verdict
            for e in self.entries
            if e.verified
            and e.source_export is None
            and (e.product_name, e.product_version) == product
            and e.class_id == class_id
        }
        if "pass" in verdicts:
            return "pass"
        return "fail" if verdicts else "not-run"

    def _pairwise(self, family: str) -> dict[tuple[tuple[str, str], tuple[str, str]], Entry]:
        """For each (exporter, importer) of ``family``: the pairwise evidence that decides.

        Pairwise evidence counts only when the export evidence it was made from is in the
        folder too (:func:`_source_entry`). What it says of its source is the importing
        party's own statement, so a name alone never decides a cell. A passing entry
        decides before a failing one.
        """
        found: dict[tuple[tuple[str, str], tuple[str, str]], Entry] = {}
        for entry in self.entries:
            source = entry.source_export
            if not entry.verified or source is None or entry.class_id != f"{family}-import":
                continue
            if _source_entry(entry, self.entries) is None:
                continue
            exporter = (source.product_name, source.product_version)
            pair = (exporter, (entry.product_name, entry.product_version))
            if pair not in found or (found[pair].verdict != "pass" and entry.verdict == "pass"):
                found[pair] = entry
        return found

    def round_trips(self) -> tuple[RoundTrip, ...]:
        """Every (exporter, importer) pair of each family, and whether they were tested together.

        A pair is listed when both passed their class of the family. Its cell is ``read``
        or ``not-read`` only when the folder holds both halves, each verified: evidence
        of an import challenge made from the exporter's file, and the exporter's own
        passing export evidence that the challenge was made from, matched by family,
        seed, the digest of that evidence and the digest of the exported file. Pairwise
        evidence whose other half is not in the folder decides nothing, and its entry
        says so.
        """
        out: list[RoundTrip] = []
        for family in dict.fromkeys(c.family for c in self.classes):
            exporters = [p for p in self.products if self.cell(p, f"{family}-export") == "pass"]
            importers = [p for p in self.products if self.cell(p, f"{family}-import") == "pass"]
            pairwise = self._pairwise(family)
            pairs = dict.fromkeys((a, b) for a in exporters for b in importers)
            pairs.update(dict.fromkeys(sorted(pairwise)))
            for exporter, importer in pairs:
                entry = pairwise.get((exporter, importer))
                if entry is None or entry.source_export is None:
                    out.append(RoundTrip(family, exporter, importer))
                    continue
                status = "read" if entry.verdict == "pass" else "not-read"
                digest = entry.source_export.export_sha256
                out.append(RoundTrip(family, exporter, importer, status, digest, entry.path))
        return tuple(out)

    @property
    def round_trip_note(self) -> str:
        """What the round-trip cells of this matrix mean, in a sentence or two."""
        tested = any(r.status != "not-tested-together" for r in self.round_trips())
        return PAIRWISE_NOTE if tested else ROUND_TRIP_NOTE

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
            "round_trip_note": self.round_trip_note,
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
                f" {r.importer[1]}: {r.status}{_cited(r)}"
            )
            for r in trips
        ] or ["  none: no family has a product that passed export and one that passed import"]
        lines += ["", f"  {self.round_trip_note}"]
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
        tested = [r for r in self.round_trips() if r.status != "not-tested-together"]
        if tested:
            lines += [
                "",
                "| Family | Export by | Import by | Status | Export file (SHA-256) |",
                "| --- | --- | --- | --- | --- |",
            ]
            lines += [
                f"| {r.family} | {_markdown(f'{r.exporter[0]} {r.exporter[1]}')}"
                f" | {_markdown(f'{r.importer[0]} {r.importer[1]}')} | {r.status}"
                f" | `{r.export_sha256}` |"
                for r in tested
            ]
        lines += ["", _markdown(self.round_trip_note)]
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
            f"<td>{escape(r.status)}</td>"
            f"<td><code>{escape(r.export_sha256 or '')}</code></td></tr>"
            for r in self.round_trips()
        )
        trip_table = (
            "<table><tr><th>Family</th><th>Export by</th><th>Import by</th><th>Status</th>"
            f"<th>Export file (SHA-256)</th></tr>{trips}</table>"
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
            f"<h2>Round trips</h2><p>{escape(self.round_trip_note)}</p>{trip_table}"
            f"{unverified}"
            '<p class="note">Not a certification by the DIGGS committee or the ASCE'
            " Geo-Institute. A product's name and version are as its vendor declared them.</p>"
            "</main></body></html>\n"
        )


def _source_entry(entry: Entry, entries: tuple[Entry, ...]) -> Entry | None:
    """The export evidence a pairwise ``entry`` was made from, when the folder holds it.

    It must have verified and passed, be of the export class of the same family, for the
    product the pairwise evidence names, of the same seed, and be the very document
    (``evidence_sha256``) for the very file (``export_sha256``).
    """
    source = entry.source_export
    if source is None or not entry.class_id.endswith("-import"):
        return None
    wanted = entry.class_id.removesuffix("-import") + "-export"
    for other in entries:
        if (
            other.verified
            and other.source_export is None
            and other.verdict == "pass"
            and other.class_id == wanted
            and (other.product_name, other.product_version)
            == (source.product_name, source.product_version)
            and other.seed == entry.seed
            and other.sha256 == source.evidence_sha256
            and other.submission_sha256 == source.export_sha256
        ):
            return other
    return None


def _cited(trip: RoundTrip) -> str:
    """What a tested round-trip cell cites, for the text report."""
    return "" if trip.export_sha256 is None else f" (export sha256 {trip.export_sha256})"


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
    plain = plain_name(name)
    if plain is None:
        return None
    path = folder / plain
    return path if path.is_file() and not path.is_symlink() else None


class _UnverifiedError(Exception):
    """Why an evidence document does not count."""


def _document(path: Path) -> tuple[dict[str, Any], str]:
    """The evidence document at ``path``, and the SHA-256 of the file as found."""
    try:
        if path.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise _UnverifiedError("too large to be conformance evidence")
        raw = path.read_bytes()
        document = json.loads(raw)
    except OSError as exc:
        raise _UnverifiedError(f"cannot be read: {exc.strerror or exc}") from None
    except (ValueError, RecursionError):
        raise _UnverifiedError("not JSON") from None
    if not isinstance(document, dict) or document.get("format") != "diggs-conformance":
        raise _UnverifiedError("not a diggs-conformance document")
    return document, hashlib.sha256(raw).hexdigest()


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
    source: SourceExport | None = None
    try:
        if path.is_symlink():
            # Never followed: a link can point outside the folder being laid out.
            raise _UnverifiedError("a link, not read")
        document, digest = _document(path)
        stated = {**_stated(document), "sha256": digest}
        if document.get("suite_version") != SUITE_VERSION:
            # A matrix is of one suite version. The evidence may still be true of its
            # own suite, and `verify` says so; it is not laid out beside this one's.
            raise _UnverifiedError(
                f"graded under suite {document.get('suite_version')!r}; this matrix counts"
                f" suite {SUITE_VERSION} only"
            )
        submission = document.get("submission")
        named = submission.get("name") if isinstance(submission, dict) else None
        graded = submission.get("sha256") if isinstance(submission, dict) else None
        stated["submission_sha256"] = graded if isinstance(graded, str) else ""
        challenge = document.get("challenge")
        seed = challenge.get("seed") if isinstance(challenge, dict) else None
        stated["seed"] = seed if isinstance(seed, str) else ""
        if "source_export" in document:
            try:
                source = SourceExport.from_dict(document["source_export"])
            except ValueError as exc:
                raise _UnverifiedError(f"a malformed diggs-conformance document: {exc}") from None
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
        return Entry(shown, False, str(exc), **stated, source_export=source)
    return Entry(shown, True, "reproduced", **stated, source_export=source)


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

    Evidence of an import challenge made from another product's export
    (``new(..., from_export=...)``) fills a round-trip cell in, when the exporter's own
    passing export evidence is in the folder as well: see :meth:`Matrix.round_trips`.
    Its challenge folder must still hold that export as ``challenge.xml``; verifying
    checks its digest and grades it again as an export of the challenge's seed
    (``max_bytes`` and ``time_budget`` apply to it too).

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
    # Pairwise evidence without its other half is true of itself and claims nothing here.
    noted = tuple(
        replace(entry, reason=UNBOUND)
        if entry.verified
        and entry.source_export is not None
        and _source_entry(entry, entries) is None
        else entry
        for entry in entries
    )
    return Matrix(noted)
