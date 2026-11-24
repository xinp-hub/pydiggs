"""Checking another product's export before an import challenge is made from it.

``new(..., from_export=...)`` hands the importing product a file the suite did not write.
The answer key is right for that file only when the file is a correct export of the same
data, so the exporter's evidence is not taken at its word: the export is graded again
here, against the challenge its evidence names, and anything that does not hold refuses
the challenge. Both files come from another party: their size is limited, a link is never
followed, and no path a document names is trusted beyond a plain file name beside it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Final

from pydiggs.conformance._catalogue import ClassInfo, class_info
from pydiggs.conformance._challenge import (
    _SEED,
    SUITE_VERSION,
    SourceExport,
    _clean,
    _make,
    read_plain_file,
)
from pydiggs.errors import DocumentError, InstanceReadError

__all__ = ["evidence_document", "plain_name", "source"]

#: The largest evidence document read; the size ``verify`` and ``matrix`` allow.
MAX_EVIDENCE_BYTES: Final = 8 * 1024 * 1024

Given = str | os.PathLike[str] | bytes | bytearray | memoryview


def _refused(why: str) -> DocumentError:
    return DocumentError("source-export", why)


def plain_name(name: object) -> str | None:
    """``name`` when it is a plain file name, else None.

    The name comes from a document another party wrote: a path, a parent folder or a
    name with a separator in it is never used.
    """
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\x00" in name or Path(name).name != name:
        return None
    return name


def evidence_document(raw: bytes) -> dict[str, Any]:
    """The evidence document ``raw`` holds.

    Raises:
        DocumentError: ``raw`` is not JSON, or not a diggs-conformance document
            (``kind`` is ``not-evidence``).
    """
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise DocumentError("not-evidence", f"the evidence is not JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != "diggs-conformance":
        raise DocumentError("not-evidence", "the evidence is not a diggs-conformance document")
    return document


def _stated(document: dict[str, Any], info: ClassInfo) -> tuple[ClassInfo, str, str, str]:
    """What the evidence must state to be the source of a challenge of ``info``.

    Returns the export class, the exporting product's name and version, and the seed.
    """
    tested, product = document.get("class"), document.get("product")
    challenge = document.get("challenge")
    stated = tested.get("id") if isinstance(tested, dict) else None
    try:
        exported = class_info(stated) if isinstance(stated, str) else None
    except ValueError:
        exported = None
    wanted = f"{info.family}-export"
    if exported is None:
        raise _refused(f"the evidence names no class of this suite; {info.id} needs {wanted}")
    if exported.direction != "export":
        raise _refused(
            f"the evidence is for {exported.id}, an import class; {info.id} is made from"
            f" passing evidence of {wanted}"
        )
    if exported.family != info.family:
        raise _refused(
            f"the evidence is for {exported.id}, another family; {info.id} is made from"
            f" passing evidence of {wanted}"
        )
    suite = document.get("suite_version")
    if suite != SUITE_VERSION:
        raise _refused(
            f"the export was graded under suite {suite!r} and this is suite"
            f" {SUITE_VERSION!r}: the questions of a new challenge are this suite's, so"
            " grade the export against a challenge of this suite first"
        )
    if document.get("verdict") != "pass":
        raise _refused(
            f"the export did not pass {exported.id}: the questions have the suite's answers"
            " only for a correct export"
        )
    try:
        if not isinstance(product, dict) or not isinstance(challenge, dict):
            raise ValueError("no product or no challenge")
        name, version = product.get("name"), product.get("version")
        if _clean(name, "product") != name or _clean(version, "version") != version:
            raise ValueError("a product name or version no challenge writes")
        seed = challenge.get("seed")
        if not isinstance(seed, str) or not _SEED.fullmatch(seed):
            raise ValueError("no seed")
    except ValueError as exc:
        raise DocumentError("not-evidence", f"the evidence is malformed: {exc}") from exc
    return exported, name, version, seed


def _export_bytes(
    export: Given | None, evidence: Given, document: dict[str, Any], max_bytes: int
) -> bytes:
    if isinstance(export, bytes | bytearray | memoryview):
        if len(export) > max_bytes:
            raise InstanceReadError(f"the export is larger than {max_bytes} bytes")
        return bytes(export)
    if export is not None:
        return read_plain_file(Path(export), max_bytes, "the export")
    if isinstance(evidence, bytes | bytearray | memoryview):
        raise ValueError("give the export file: evidence given as bytes has no folder to look in")
    submission = document.get("submission")
    name = plain_name(submission.get("name") if isinstance(submission, dict) else None)
    if name is None:
        raise _refused("the evidence does not name its file by a plain name; give the export file")
    return read_plain_file(Path(evidence).parent / name, max_bytes, "the export")


def source(
    info: ClassInfo,
    evidence: Given,
    export: Given | None,
    *,
    max_bytes: int,
    time_budget: float | None,
) -> tuple[SourceExport, bytes, str]:
    """The source of a pairwise challenge of ``info``: its record, its bytes and its seed.

    ``evidence`` must be passing evidence of the export class of ``info``'s family under
    this suite version, ``export`` the file it graded (by SHA-256), and grading that file
    again against the challenge the evidence names must give the evidence back.

    Raises:
        DocumentError: Any of that does not hold (``not-evidence`` or ``source-export``).
        InstanceReadError: A file is missing, a link, not a plain file, or too large.
        ValueError: ``export`` is None and ``evidence`` is bytes.
        TypeError: ``evidence`` or ``export`` is neither a path nor bytes.
    """
    from pydiggs.conformance._grade import (  # noqa: PLC0415  # grading loads on demand
        READ_SCHEMA_VERSIONS,
        verify,
    )

    if isinstance(evidence, bytes | bytearray | memoryview):
        if len(evidence) > MAX_EVIDENCE_BYTES:
            raise InstanceReadError("the evidence is too large to be conformance evidence")
        raw = bytes(evidence)
    else:
        raw = read_plain_file(Path(evidence), MAX_EVIDENCE_BYTES, "the evidence")
    document = evidence_document(raw)
    if document.get("schema_version") not in READ_SCHEMA_VERSIONS:
        raise DocumentError(
            "not-evidence",
            f"evidence schema {document.get('schema_version')!r} is not one this release reads"
            f" ({', '.join(READ_SCHEMA_VERSIONS)})",
        )
    exported, name, version, seed = _stated(document, info)
    data = _export_bytes(export, evidence, document, max_bytes)
    digest = hashlib.sha256(data).hexdigest()
    submission = document.get("submission")
    recorded = submission.get("sha256") if isinstance(submission, dict) else None
    if digest != recorded:
        raise _refused(
            f"the export file is not the one the evidence graded: its SHA-256 is {digest},"
            f" and the evidence records {recorded!r}"
        )
    # The evidence names its challenge by class, product and seed, which is all a
    # challenge is made of: it is rebuilt here, and nothing else of the exporter's
    # folder is needed or trusted.
    theirs = _make(exported, name, version, seed, SUITE_VERSION)
    outcome = verify(document, theirs, data, max_bytes=max(max_bytes, 1), time_budget=time_budget)
    if not outcome.ok:
        raise _refused(
            "the evidence does not verify: grading the export again differs in "
            + ", ".join(outcome.differences)
        )
    record = SourceExport(
        product_name=name,
        product_version=version,
        export_sha256=digest,
        evidence_sha256=hashlib.sha256(raw).hexdigest(),
        suite_version=SUITE_VERSION,
    )
    return record, data, seed
