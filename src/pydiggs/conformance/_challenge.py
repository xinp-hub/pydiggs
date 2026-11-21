"""A challenge: the files a vendor works from, and the manifest grading starts from.

A challenge is a pure function of the suite version, the class and the seed. The manifest
(``challenge.json``) records those three, so grading rebuilds the answer key from the
manifest alone and never trusts the other files of the folder.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pydiggs.conformance import _facts, _scenario
from pydiggs.conformance._catalogue import ClassInfo, class_info
from pydiggs.errors import DocumentError, ResourceError

__all__ = ["CHALLENGE_FORMAT", "MANIFEST_NAME", "SUITE_VERSION", "Challenge", "load", "new"]

#: The version of the suite: the classes, the scenarios, the facts, the questions and the
#: tolerances. It changes whenever those could grade a file differently. The validation
#: stage is pyDIGGS's own validator, whose release the evidence records beside it.
SUITE_VERSION: Final = "1.0-beta.1"
CHALLENGE_FORMAT: Final = "diggs-conformance-challenge"
MANIFEST_NAME: Final = "challenge.json"

_MAX_MANIFEST_BYTES: Final = 64 * 1024
_MAX_TEXT: Final = 200
_SEED: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Challenge:
    """One challenge: its class, its seed, the product it is for and its files.

    ``files`` maps each file name to its bytes, the manifest included. ``token`` is
    derived from the seed and appears in the project's name, so a submission can be tied
    to its challenge by reading it.
    """

    info: ClassInfo
    seed: str
    token: str
    product_name: str
    product_version: str
    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not isinstance(self.files, MappingProxyType):
            object.__setattr__(self, "files", MappingProxyType(dict(self.files)))

    @property
    def manifest(self) -> dict[str, Any]:
        """The manifest, as ``challenge.json`` holds it."""
        return dict(json.loads(self.files[MANIFEST_NAME]))

    @property
    def sha256(self) -> str:
        """The digest of the manifest: the identity of this challenge."""
        return hashlib.sha256(self.files[MANIFEST_NAME]).hexdigest()

    def dataset(self) -> _scenario.Dataset:
        """The source data, rebuilt from the seed."""
        return _scenario.build(self.info.scenario, _stream(self.info, self.seed), self.token)

    def reference(self) -> bytes:
        """The challenge's data as a DIGGS 3.0.0 file, written by pyDIGGS's own converter.

        It is a known-good export of the source tables: use it to try the suite, or to
        compare with what your software writes. For an import class it is
        ``challenge.xml``.
        """
        return _scenario.reference_xml(self.dataset())

    def write(self, directory: str | os.PathLike[str]) -> Path:
        """Write the files into ``directory`` (created if needed) and return it.

        Raises:
            FileExistsError: The directory already holds a challenge, or a file with the
                name of one of the challenge's files. Nothing is overwritten.
        """
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        taken = sorted(name for name in self.files if (target / name).exists())
        if MANIFEST_NAME in taken:
            raise FileExistsError(f"{target} already holds a challenge")
        if taken:
            raise FileExistsError(f"{target} already holds {taken[0]}; choose an empty folder")
        for name, data in self.files.items():
            (target / name).write_bytes(data)
        return target


def _stream(info: ClassInfo, seed: str) -> str:
    # Export and import of one family share a dataset for a seed; the suite version is
    # part of the stream, so a new suite version never reuses an old dataset.
    return f"{SUITE_VERSION}|{info.scenario}|{seed}"


def _token(info: ClassInfo, seed: str) -> str:
    digest = hashlib.sha256(_stream(info, seed).encode("utf-8")).hexdigest()
    return digest[:6].upper()


def _csv(header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _columns_csv(dataset: _scenario.Dataset) -> bytes:
    rows = tuple(
        (f"{table.name}.csv", field.name, field.unit, field.meaning)
        for table in dataset.tables
        for field in table.fields
    )
    return _csv(("file", "column", "unit", "meaning"), rows)


def _defective(xml: bytes, defect: str) -> bytes:
    """``xml`` with one planted defect; pyDIGGS's own validation reports each of them."""
    text = xml.decode("utf-8")
    if defect == "broken-reference":
        changed = re.sub(
            r'(<samplingFeatureRef xlink:href="#[^"]+)"', r'\1-missing"', text, count=1
        )
    elif defect == "unknown-property":
        changed = re.sub(
            r'(<propertyClass codeSpace="[^"#]*#)([^"]+)">([^<]+)<', r'\1\2_x">\3_x<', text, count=1
        )
    else:
        changed = re.sub(r'(<totalMeasuredDepth) uom="[^"]*"', r'\1 uom="furlong"', text, count=1)
    if changed == text:
        raise ResourceError(f"the defect {defect!r} could not be planted")
    return changed.encode("utf-8")


def _readme(info: ClassInfo, token: str, dataset: _scenario.Dataset, product: str) -> bytes:
    lines = [
        f"# DIGGS conformance challenge: {info.id}",
        "",
        f"For: {product}. Suite {SUITE_VERSION}, DIGGS {info.diggs_version}, token {token}.",
        "",
        "The data is synthetic. It was generated for this challenge and for no other.",
        "",
    ]
    if info.direction == "export":
        lines += [
            "## What to do",
            "",
            "1. Load the CSV tables into your software, as a user would enter the data.",
            "   `columns.csv` gives the unit and the meaning of every column.",
            f"2. Export one DIGGS {info.diggs_version} file from your software.",
            "3. Grade it: `pydiggs conformance grade <this folder> <your file>`.",
            "",
            "## What is graded",
            "",
            "- The file is valid DIGGS: schema, dictionaries and business rules.",
            "- The project's name and every location name are carried exactly as written.",
            "- Every value of the tables is in the file, at its depth, within the precision",
            "  the table gives. You may export in any unit, as long as the file states it.",
            "- Where the file gives a source value more than once, every one must agree.",
            "",
            "## Tables",
            "",
        ]
        lines += [f"- `{t.name}.csv`: {len(t.rows)} row(s)" for t in dataset.tables]
    else:
        lines += [
            "## What to do",
            "",
            "1. Open `challenge.xml` in your software.",
            "2. Fill the `answer` column of `answers.csv` from what your software shows,",
            "   in the unit each question asks for. Leave the other columns as they are.",
            "3. Open `defective.xml`, which has one planted defect, and answer the last",
            "   question with what your software reports.",
            "4. Grade it: `pydiggs conformance grade <this folder> answers.csv`.",
            "",
            "## What is graded",
            "",
            "- Counts must be exact. Names ignore letter case. Numbers must agree within",
            "  0.5 % or the precision of the value in the file, whichever is larger; use a",
            "  decimal point and no thousands separator.",
            "- The question about `defective.xml` is reported but does not decide the",
            "  verdict at this level.",
        ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _clean(text: object, what: str) -> str:
    if not isinstance(text, str):
        raise ValueError(f"{what} must be text")
    value = " ".join(text.split())
    if not value or len(value) > _MAX_TEXT:
        raise ValueError(f"{what} must be 1 to {_MAX_TEXT} characters")
    return value


def new(
    class_id: str,
    *,
    product: str,
    version: str,
    seed: str | None = None,
) -> Challenge:
    """Make a challenge for the class ``class_id``.

    Args:
        class_id: A class from :func:`pydiggs.conformance.classes`, such as ``spt-export``.
        product: The name of the software under test, as the evidence will state it.
        version: The exact version of that software.
        seed: Letters, digits, ``.``, ``_`` and ``-``, at most 64. None draws a fresh one;
            give one to rebuild a challenge. The same seed always gives the same files.

    Returns:
        The challenge; :meth:`Challenge.write` puts its files in a folder.

    Raises:
        ValueError: An unknown class, an empty or over-long product or version, or a
            seed with other characters.

    Examples:
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("cpt-export", product="MyLog", version="4.2.1", seed="demo")
        >>> sorted(challenge.files)[:5]
        ['README.md', 'challenge.json', 'columns.csv', 'cpt_data.csv', 'cpt_tests.csv']
        >>> challenge.token == conformance.new(
        ...     "cpt-export", product="X", version="1", seed="demo"
        ... ).token
        True
    """
    info = class_info(class_id)
    product_name = _clean(product, "product")
    product_version = _clean(version, "version")
    chosen = secrets.token_hex(8) if seed is None else seed
    if not isinstance(chosen, str) or not _SEED.fullmatch(chosen):
        raise ValueError("seed must be 1 to 64 letters, digits, '.', '_' or '-'")
    token = _token(info, chosen)
    dataset = _scenario.build(info.scenario, _stream(info, chosen), token)
    files: dict[str, bytes] = {
        "README.md": _readme(info, token, dataset, f"{product_name} {product_version}")
    }
    if info.direction == "export":
        for table in dataset.tables:
            files[f"{table.name}.csv"] = _csv(tuple(f.name for f in table.fields), table.rows)
        files["columns.csv"] = _columns_csv(dataset)
    else:
        asked = _facts.questions(dataset, _stream(info, chosen))
        reference = _scenario.reference_xml(dataset)
        files["challenge.xml"] = reference
        files["defective.xml"] = _defective(reference, asked[-1].expected)
        files["answers.csv"] = _csv(
            ("question_id", "question", "unit", "answer"),
            tuple((q.id, q.text, q.unit, "") for q in asked),
        )
    manifest = {
        "format": CHALLENGE_FORMAT,
        "suite_version": SUITE_VERSION,
        "class": info.id,
        "diggs_version": info.diggs_version,
        "seed": chosen,
        "token": token,
        "product": {"name": product_name, "version": product_version},
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files[MANIFEST_NAME] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    return Challenge(info, chosen, token, product_name, product_version, files)


def load(source: str | os.PathLike[str] | Challenge) -> Challenge:
    """The challenge a folder (or its ``challenge.json``) describes, rebuilt from its seed.

    Only the manifest is read. The other files are rebuilt, so a folder whose tables were
    edited still grades against the data the seed gives.

    Raises:
        DocumentError: The manifest is missing, too large, not a challenge manifest, or
            from another suite version (``kind`` says which).
    """
    if isinstance(source, Challenge):
        return source
    path = Path(source)
    if path.is_dir():
        path = path / MANIFEST_NAME
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise DocumentError("not-a-challenge", "the challenge manifest is too large")
        manifest = json.loads(path.read_bytes())
        if not isinstance(manifest, dict) or manifest.get("format") != CHALLENGE_FORMAT:
            raise DocumentError("not-a-challenge", f"{path} is not a challenge manifest")
        if manifest.get("suite_version") != SUITE_VERSION:
            raise DocumentError(
                "suite-version",
                f"the challenge was made by suite {manifest.get('suite_version')!r} and this is"
                f" suite {SUITE_VERSION!r}; make a new challenge, or install the pyDIGGS"
                " release that has that suite",
            )
        product = manifest["product"]
        seed = manifest["seed"]
        if not isinstance(seed, str) or not isinstance(product, dict):
            raise DocumentError("not-a-challenge", f"{path} is not a challenge manifest")
        rebuilt = new(
            manifest["class"], product=product["name"], version=product["version"], seed=seed
        )
        if manifest.get("token") != rebuilt.token:
            raise DocumentError(
                "not-a-challenge", f"the token in {path} does not belong to its seed"
            )
        return rebuilt
    except DocumentError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise DocumentError(
            "not-a-challenge", f"cannot read the challenge at {path}: {exc}"
        ) from exc
