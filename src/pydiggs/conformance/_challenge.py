"""A challenge: the files a vendor works from, and the manifest grading starts from.

A challenge is a pure function of the suite version, the class and the seed. The manifest
(``challenge.json``) records those three, so grading rebuilds the answer key from the
manifest alone and never trusts the other files of the folder.

One kind of challenge has a file that is not rebuilt: an import challenge made from another
product's graded export (:class:`SourceExport`). Its ``challenge.xml`` is that product's
file, byte for byte. The manifest records the file's SHA-256, and loading the challenge
reads the file and refuses it when the digest differs, or when the file does not pass the
export class for the challenge's seed: the manifest is the vendor's own file, so what it
says of the export is checked against the export.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pydiggs.conformance import _registry, _scenario
from pydiggs.conformance._catalogue import ClassInfo, class_info
from pydiggs.errors import DocumentError, InstanceReadError, ResourceError
from pydiggs.findings.run import DEFAULT_MAX_BYTES

__all__ = [
    "CHALLENGE_FORMAT",
    "GRADED_SUITES",
    "MANIFEST_NAME",
    "SUITE_VERSION",
    "Challenge",
    "SourceExport",
    "load",
    "new",
]

#: The version of the suite: the classes, the scenarios, the facts, the questions and the
#: tolerances. It changes whenever those could grade a file differently. The validation
#: stage is pyDIGGS's own validator, whose release the evidence records beside it.
SUITE_VERSION: Final = "1.0-beta.3"
#: Every suite version this release grades. A new challenge is always of
#: :data:`SUITE_VERSION`; a challenge an earlier suite made is still loaded, graded and
#: verified, by the rules of the suite that made it.
GRADED_SUITES: Final = ("1.0-beta.2", "1.0-beta.3")
#: The suites whose spt, cpt, lab-index and deep-foundations classes read a cell that
#: holds its property's declared ``nullValue`` as a value. Later suites read it as empty.
_NULL_VALUE_IS_A_VALUE: Final = frozenset({"1.0-beta.2"})
#: The suites that have no import challenge made from another product's export.
_NO_PAIRWISE: Final = frozenset({"1.0-beta.2"})
CHALLENGE_FORMAT: Final = "diggs-conformance-challenge"
#: The manifest members that define a challenge, and so its :attr:`Challenge.id`. A
#: challenge made from another product's export adds ``source_export`` to them.
IDENTITY_KEYS: Final = ("suite_version", "class", "diggs_version", "seed", "token", "product")
MANIFEST_NAME: Final = "challenge.json"
#: The DIGGS file of an import challenge.
IMPORT_FILE: Final = "challenge.xml"

_MAX_MANIFEST_BYTES: Final = 64 * 1024
_MAX_TEXT: Final = 200
_SEED: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KEYS: Final = frozenset({"product", "export_sha256", "evidence_sha256", "suite_version"})


def honours_null_value(suite_version: str) -> bool:
    """Whether ``suite_version`` reads a declared ``nullValue`` as an empty cell everywhere."""
    return suite_version not in _NULL_VALUE_IS_A_VALUE


@dataclass(frozen=True, slots=True)
class SourceExport:
    """Where the DIGGS file of a pairwise import challenge came from.

    An import challenge made with ``new(..., from_export=...)`` hands the importing
    product another product's export instead of the suite's own file. This records that
    export by digest, never by path: the exporting product's name and version as its
    evidence states them, the SHA-256 of the exported file, the SHA-256 of the exporter's
    ``evidence.json`` as it was given, and the suite version that graded the export. The
    challenge's manifest and the evidence graded from it both carry it as
    ``source_export``.

    Examples:
        >>> from pydiggs import conformance
        >>> source = conformance.SourceExport("LogA", "2.0", "a" * 64, "b" * 64, "1.0-beta.3")
        >>> source.to_dict()["product"]
        {'name': 'LogA', 'version': '2.0'}
        >>> conformance.SourceExport.from_dict(source.to_dict()) == source
        True
    """

    product_name: str
    product_version: str
    export_sha256: str
    evidence_sha256: str
    suite_version: str

    def to_dict(self) -> dict[str, Any]:
        """The record as ``challenge.json`` and the evidence hold it."""
        return {
            "product": {"name": self.product_name, "version": self.product_version},
            "export_sha256": self.export_sha256,
            "evidence_sha256": self.evidence_sha256,
            "suite_version": self.suite_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceExport:
        """The record a document holds as ``source_export``.

        The document is untrusted: every member must be there, of the right kind, and
        nothing else.

        Raises:
            ValueError: ``value`` is not a ``source_export`` object.
        """
        if not isinstance(value, dict) or set(value) != _SOURCE_KEYS:
            raise ValueError("source_export must hold exactly " + ", ".join(sorted(_SOURCE_KEYS)))
        product = value["product"]
        if not isinstance(product, dict) or set(product) != {"name", "version"}:
            raise ValueError("source_export.product must hold exactly name and version")
        name, version = product["name"], product["version"]
        if _clean(name, "source_export.product.name") != name:
            raise ValueError("source_export.product.name is not as a challenge writes it")
        if _clean(version, "source_export.product.version") != version:
            raise ValueError("source_export.product.version is not as a challenge writes it")
        for key in ("export_sha256", "evidence_sha256"):
            if not isinstance(value[key], str) or not _SHA256.fullmatch(value[key]):
                raise ValueError(f"source_export.{key} must be a SHA-256 in lower-case hex")
        suite = value["suite_version"]
        if not isinstance(suite, str) or not 0 < len(suite) <= _MAX_TEXT:
            raise ValueError("source_export.suite_version must be text")
        return cls(name, version, value["export_sha256"], value["evidence_sha256"], suite)


@dataclass(frozen=True, slots=True)
class Challenge:
    """One challenge: its class, its seed, the product it is for and its files.

    ``files`` maps each file name to its bytes, the manifest included. ``token`` is
    derived from the seed and appears in the project's name, so a submission can be tied
    to its challenge by reading it. ``suite_version`` is the suite that made the
    challenge and grades it. ``source_export`` is None, except for an import challenge
    made from another product's export (:class:`SourceExport`).
    """

    info: ClassInfo
    seed: str
    token: str
    product_name: str
    product_version: str
    files: Mapping[str, bytes]
    suite_version: str = SUITE_VERSION
    source_export: SourceExport | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.files, MappingProxyType):
            object.__setattr__(self, "files", MappingProxyType(dict(self.files)))

    @property
    def manifest(self) -> dict[str, Any]:
        """The manifest, as ``challenge.json`` holds it."""
        return dict(json.loads(self.files[MANIFEST_NAME]))

    @property
    def sha256(self) -> str:
        """The digest of ``challenge.json`` as written, file digests included.

        Some of the challenge's files are written by pyDIGGS itself, so another pyDIGGS
        release may write them, and this digest, differently for the same challenge. Use
        :attr:`id` to name the challenge.
        """
        return hashlib.sha256(self.files[MANIFEST_NAME]).hexdigest()

    @property
    def id(self) -> str:
        """The stable identity of this challenge, the same in every pyDIGGS release.

        A SHA-256 of what defines the challenge, as canonical JSON (sorted keys, no
        spaces): the suite version, the class, the DIGGS version, the seed, the token and
        the product. It does not depend on how the files are written. A challenge made
        from another product's export adds its ``source_export`` member, so the identity
        also names that export, by its digests.

        Examples:
            >>> from pydiggs import conformance
            >>> a = conformance.new("spt-export", product="MyLog", version="4.2.1", seed="demo")
            >>> b = conformance.new("spt-export", product="MyLog", version="4.2.1", seed="demo")
            >>> a.id == b.id, len(a.id)
            (True, 64)
        """
        manifest = self.manifest
        identity = {key: manifest[key] for key in IDENTITY_KEYS}
        if "source_export" in manifest:
            identity["source_export"] = manifest["source_export"]
        text = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def dataset(self) -> _scenario.Dataset:
        """The source data, rebuilt from the seed."""
        return _registry.build(self.info.scenario, self.stream, self.token)

    @property
    def stream(self) -> str:
        """What the challenge's data and questions are drawn from: suite, scenario, seed."""
        return _stream(self.info, self.seed, self.suite_version)

    def reference(self) -> bytes:
        """The challenge's data as a DIGGS 3.0.0 file, written by pyDIGGS's own converter.

        It is a known-good export of the source tables: use it to try the suite, or to
        compare with what your software writes. For an import class it is
        ``challenge.xml``, unless the challenge was made from another product's export:
        then ``challenge.xml`` is that product's file, and this is still pyDIGGS's own.
        """
        return _registry.reference_xml(self.dataset())

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


def _stream(info: ClassInfo, seed: str, suite: str = SUITE_VERSION) -> str:
    # Export and import of one family share a dataset for a seed; the suite version is
    # part of the stream, so a new suite version never reuses an old dataset.
    return f"{suite}|{info.scenario}|{seed}"


def _token(info: ClassInfo, seed: str, suite: str = SUITE_VERSION) -> str:
    digest = hashlib.sha256(_stream(info, seed, suite).encode("utf-8")).hexdigest()
    return digest[:6].upper()


#: Scenarios whose export challenge is a DIGGS tables profile package (pydiggs.tables).
#: The pile scenario is not: the profile has no pile tables yet.
PROFILE_SCENARIOS: Final = frozenset(
    {"spt", "cpt", "lab", "mwd", "dissipation", "in-situ-strength", "pressuremeter", "monitoring"}
)
#: Scenario -> what its export challenge's README adds to "What is graded".
_GRADED_TOO: Final = {
    "monitoring": (
        "- A reading is found by its time, compared as an instant: any offset from UTC that",
        "  names the same instant is right, and a time with no offset names none.",
        "- A time the tables have no reading at has none in the file either.",
        "- The sensor depth is the place the series' `monitorLocation` gives.",
    ),
    "pressuremeter": (
        "- An unload-reload loop is a `trial` of its test's `PressuremeterTest` (a",
        "  `PressuremeterTestTrial`), found by its number, `trialNo`.",
        "- A test the tables give no loop has none in the file, and a limit pressure the",
        "  tables leave empty was not reached: it is not worked out from anything.",
    ),
}


def _profile_package(reference: bytes) -> dict[str, bytes]:
    """The export challenge's tables, as a DIGGS tables profile package.

    The tables are what :func:`pydiggs.tables.from_model` gives for the reference file, so
    they are exactly what a vendor's own profile export holds: CSV files, a
    ``datapackage.json`` that describes every table, column and unit, and ``plan.json``,
    the load order. The answer key is still built from the scenario's tables alone.
    """
    import tempfile  # noqa: PLC0415  # only an export challenge needs a scratch folder

    from pydiggs import read_bytes, tables  # noqa: PLC0415  # the kit loads on demand

    with tempfile.TemporaryDirectory() as scratch:
        written = tables.write_package(tables.from_model(read_bytes(reference)), scratch)
        return {path.name: path.read_bytes() for path in written}


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


def _readme(
    info: ClassInfo,
    token: str,
    dataset: _scenario.Dataset,
    product: str,
    *,
    suite: str,
    source: SourceExport | None,
) -> bytes:
    lines = [
        f"# DIGGS conformance challenge: {info.id}",
        "",
        f"For: {product}. Suite {suite}, DIGGS {info.diggs_version}, token {token}.",
        "",
        "The data is synthetic. It was generated for this challenge and for no other.",
        "",
    ]
    if info.direction == "export" and info.scenario in PROFILE_SCENARIOS:
        lines += [
            "## What to do",
            "",
            "1. Load the tables into your software, as a user would enter the data. They are",
            "   a DIGGS tables profile package: `datapackage.json` describes every table and",
            "   column, with its unit; `plan.json` gives the load order.",
            "   The profile: https://xinp-hub.github.io/pydiggs/tables/",
            f"2. Export one DIGGS {info.diggs_version} file from your software.",
            "3. Grade it: `pydiggs conformance grade <this folder> <your file>`.",
            "",
            "## What is graded",
            "",
            "- The file is valid DIGGS: schema, dictionaries and business rules.",
            "- The project's name and every location name (borings, soundings, piles) are",
            "  carried exactly as written.",
            "- Every value of the tables is in the file, at its depth, within the precision",
            "  the table gives. You may export in any unit, as long as the file states it.",
            "- Where the file gives a source value more than once, every one must agree.",
            "- A value the tables leave empty is not in the file as a zero.",
            *_GRADED_TOO.get(info.scenario, ()),
            "",
        ]
    elif info.direction == "export":
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
            "- The project's name and every location name (borings, soundings, piles) are",
            "  carried exactly as written.",
            "- Every value of the tables is in the file, at its depth, within the precision",
            "  the table gives. You may export in any unit, as long as the file states it.",
            "- Where the file gives a source value more than once, every one must agree.",
            "",
            "## Tables",
            "",
        ]
        lines += [f"- `{t.name}.csv`: {len(t.rows)} row(s)" for t in dataset.tables]
    else:
        if source is not None:
            from pydiggs.conformance._report import _markdown  # noqa: PLC0415  # shared escaping

            exporter = _markdown(f"{source.product_name} {source.product_version}")
            lines += [
                "## Where `challenge.xml` comes from",
                "",
                f"`challenge.xml` is a DIGGS file exported by {exporter} (as its evidence",
                "declares it), copied byte for byte. That export passed the export class of",
                "this family, so the questions below have the same answers as for the",
                "suite's own file. `defective.xml` is the suite's own file.",
                "",
                f"- SHA-256 of the export: `{source.export_sha256}`",
                f"- SHA-256 of its evidence: `{source.evidence_sha256}`",
                "",
            ]
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
    from_export: str | os.PathLike[str] | bytes | bytearray | memoryview | None = None,
    export: str | os.PathLike[str] | bytes | bytearray | memoryview | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Challenge:
    """Make a challenge for the class ``class_id``.

    With ``from_export``, an import challenge is a *pairwise* one: its ``challenge.xml`` is
    another product's graded export instead of the suite's own file, so this product is
    tested on the file that product wrote. The export must have passed the export
    class of the same family, under this suite version: the new challenge takes the seed
    of that export challenge, so the questions and their answers are the ones the suite
    already has. The evidence is verified first (the export is graded again), and both
    files are untrusted: their size is limited and a link is never followed.

    Args:
        class_id: A class from :func:`pydiggs.conformance.classes`, such as ``spt-export``.
        product: The name of the software under test, as the evidence will state it.
        version: The exact version of that software.
        seed: Letters, digits, ``.``, ``_`` and ``-``, at most 64. None draws a fresh one;
            give one to rebuild a challenge. The same seed always gives the same files.
            With ``from_export`` the seed is the export challenge's: leave it None, or
            give that same seed.
        from_export: The ``evidence.json`` of the exporting product's passing
            ``<family>-export`` result: a path, or its bytes (``bytes``, ``bytearray``
            or ``memoryview``). Only for an import class.
        export: The DIGGS file that evidence graded: a path, or its bytes. None looks
            for the file the evidence names, beside the evidence, which needs the
            evidence given as a path. Its SHA-256 must be the one the evidence records.
        max_bytes: Largest export file accepted (default 100 MiB). A challenge made
            with a larger limit is graded with that limit too (``grade(...,
            max_bytes=...)``, ``--max-bytes``), since grading reads the export again.
        time_budget: Seconds for reading and for validating the export, each, when its
            evidence is verified.

    Returns:
        The challenge; :meth:`Challenge.write` puts its files in a folder.

    Raises:
        ValueError: An unknown class, an empty or over-long product or version, or a
            seed with other characters; ``from_export`` with an export class or with
            another seed; ``export`` without ``from_export``; ``from_export`` given as
            bytes without ``export`` (bytes have no folder to look in).
        TypeError: ``from_export`` or ``export`` is neither a path nor bytes.
        DocumentError: ``from_export`` is not conformance evidence, is for another
            family or not for an export class, was graded under another suite version,
            did not pass, does not verify, or the export file is not the one it graded
            (``kind`` is ``not-evidence`` or ``source-export``).
        InstanceReadError: The evidence or the export file is missing, a link, not a
            plain file, or too large.

    Examples:
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("cpt-export", product="MyLog", version="4.2.1", seed="demo")
        >>> sorted(challenge.files)[:5]
        ['README.md', 'challenge.json', 'datapackage.json', 'document.csv', 'features.csv']
        >>> challenge.token == conformance.new(
        ...     "cpt-export", product="X", version="1", seed="demo"
        ... ).token
        True

        A pairwise import challenge, from the export of another product:

        >>> exported = challenge.reference()  # stands for the file MyLog exported
        >>> graded = conformance.grade(challenge, exported)
        >>> pair = conformance.new(
        ...     "cpt-import",
        ...     product="Viewer",
        ...     version="7",
        ...     from_export=graded.to_json().encode(),
        ...     export=exported,
        ... )
        >>> pair.files["challenge.xml"] == exported, pair.seed
        (True, 'demo')
        >>> source = pair.source_export
        >>> source.product_name, source.export_sha256 == graded.submission_sha256
        ('MyLog', True)
    """
    info = class_info(class_id)
    product_name = _clean(product, "product")
    product_version = _clean(version, "version")
    source: SourceExport | None = None
    exported: bytes | None = None
    if from_export is None:
        if export is not None:
            raise ValueError("export is the file of from_export; give the evidence too")
        chosen = secrets.token_hex(8) if seed is None else seed
    else:
        if info.direction != "import":
            raise ValueError(
                f"{info.id} is an export class; a challenge is made from another product's"
                f" export only for an import class, here {info.family}-import"
            )
        from pydiggs.conformance import _pairwise  # noqa: PLC0415  # grading loads on demand

        source, exported, chosen = _pairwise.source(
            info, from_export, export, max_bytes=max_bytes, time_budget=time_budget
        )
        if seed is not None and seed != chosen:
            raise ValueError(
                f"the export was graded against the challenge of seed {chosen!r}; a challenge"
                " made from it has that seed, so leave the seed out"
            )
    if not isinstance(chosen, str) or not _SEED.fullmatch(chosen):
        raise ValueError("seed must be 1 to 64 letters, digits, '.', '_' or '-'")
    return _make(
        info, product_name, product_version, chosen, SUITE_VERSION, source=source, exported=exported
    )


def _make(
    info: ClassInfo,
    product_name: str,
    product_version: str,
    seed: str,
    suite: str,
    *,
    source: SourceExport | None = None,
    exported: bytes | None = None,
) -> Challenge:
    """The challenge of ``seed`` as ``suite`` makes it; ``exported`` is ``source``'s file."""
    stream = _stream(info, seed, suite)
    token = _token(info, seed, suite)
    dataset = _registry.build(info.scenario, stream, token)
    files: dict[str, bytes] = {
        "README.md": _readme(
            info, token, dataset, f"{product_name} {product_version}", suite=suite, source=source
        )
    }
    if info.direction == "export" and info.scenario in PROFILE_SCENARIOS:
        files.update(_profile_package(_registry.reference_xml(dataset)))
    elif info.direction == "export":
        for table in dataset.tables:
            files[f"{table.name}.csv"] = _csv(tuple(f.name for f in table.fields), table.rows)
        files["columns.csv"] = _columns_csv(dataset)
    else:
        asked = _registry.questions(dataset, stream)
        reference = _registry.reference_xml(dataset)
        # A pairwise challenge hands over the other product's bytes, verbatim. The
        # defective file stays the suite's own: its defect is planted in a file whose
        # layout is known.
        files[IMPORT_FILE] = reference if exported is None else exported
        files["defective.xml"] = _defective(reference, asked[-1].expected)
        files["answers.csv"] = _csv(
            ("question_id", "question", "unit", "answer"),
            tuple((q.id, q.text, q.unit, "") for q in asked),
        )
    manifest: dict[str, Any] = {
        "format": CHALLENGE_FORMAT,
        "suite_version": suite,
        "class": info.id,
        "diggs_version": info.diggs_version,
        "seed": seed,
        "token": token,
        "product": {"name": product_name, "version": product_version},
    }
    if source is not None:
        manifest["source_export"] = source.to_dict()
    manifest["files"] = {
        name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())
    }
    files[MANIFEST_NAME] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    return Challenge(info, seed, token, product_name, product_version, files, suite, source)


def read_plain_file(path: Path, max_bytes: int, what: str) -> bytes:
    """The bytes of ``path``, which another party may have written.

    Only a plain file is read: a link is never followed, and nothing larger than
    ``max_bytes``.

    Raises:
        InstanceReadError: ``path`` is missing, a link, not a plain file, or too large.
    """
    try:
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode):
            raise InstanceReadError(f"{what} {path} is a link, and a link is not followed")
        if not stat.S_ISREG(status.st_mode):
            raise InstanceReadError(f"{what} {path} is not a file")
        if status.st_size > max_bytes:
            raise InstanceReadError(f"{what} {path} is larger than {max_bytes} bytes")
        # Opened without following a link either, where the platform can say so: the
        # file may be swapped between the look above and the read.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InstanceReadError(f"{what} {path} is not a file")
            data = stream.read(max_bytes + 1)
    except OSError as exc:
        if isinstance(exc, InstanceReadError):
            raise
        raise InstanceReadError(f"cannot read {what} {path}: {exc.strerror or exc}") from exc
    if len(data) > max_bytes:
        raise InstanceReadError(f"{what} {path} is larger than {max_bytes} bytes")
    return data


def _loaded_source(manifest: dict[str, Any], info: ClassInfo, path: Path) -> SourceExport:
    """The ``source_export`` of a manifest, checked against the class and the suite."""
    try:
        source = SourceExport.from_dict(manifest["source_export"])
    except ValueError as exc:
        raise DocumentError("not-a-challenge", f"{path}: {exc}") from exc
    suite = manifest.get("suite_version")
    if suite in _NO_PAIRWISE:
        raise DocumentError(
            "not-a-challenge",
            f"{path} names a source export, and suite {suite} has no challenge made from"
            " another product's export",
        )
    if info.direction != "import" or source.suite_version != suite:
        raise DocumentError(
            "not-a-challenge",
            f"{path} names a source export, which only an import challenge of the same"
            " suite version has",
        )
    return source


def _source_file(
    folder: Path,
    info: ClassInfo,
    seed: str,
    source: SourceExport,
    *,
    max_bytes: int,
    time_budget: float | None,
) -> bytes:
    """The export a pairwise challenge was made from: ``challenge.xml`` of its folder.

    The manifest is the importing party's own file, so what it says of the export is
    checked against the file, every time the challenge is loaded: the file has the digest
    the manifest records, and it is a correct export of this challenge's data. For the
    second, the export challenge the manifest implies (the family's export class, the
    exporting product as named, this seed) is rebuilt and the file is graded against it.
    A manifest that names an export of other data, or a file that is no export at all,
    is refused here, whatever digests it gives.
    """
    try:
        data = read_plain_file(folder / IMPORT_FILE, max_bytes, "the challenge's")
    except InstanceReadError as exc:
        raise DocumentError(
            "source-export",
            f"this challenge was made from another product's export, and {exc}; it is"
            f" graded only with that file as {IMPORT_FILE} in its folder",
        ) from exc
    found = hashlib.sha256(data).hexdigest()
    if found != source.export_sha256:
        raise DocumentError(
            "source-export",
            f"{folder / IMPORT_FILE} is not the export this challenge was made from: its"
            f" SHA-256 is {found}, and the challenge records {source.export_sha256}",
        )
    from pydiggs.conformance._grade import _grade_export  # noqa: PLC0415  # loads on demand

    theirs = _make(
        class_info(f"{info.family}-export"),
        source.product_name,
        source.product_version,
        seed,
        source.suite_version,
    )
    stages = _grade_export(theirs, IMPORT_FILE, data, time_budget)
    failed = [stage.id for stage in stages if stage.required and stage.status != "pass"]
    if failed:
        raise DocumentError(
            "source-export",
            f"{folder / IMPORT_FILE} is not a passing {theirs.info.id} export of this"
            f" challenge's data (seed {seed}): it fails {', '.join(failed)}. A challenge"
            " made from another product's export holds an export that passed",
        )
    return data


def load(
    source: str | os.PathLike[str] | Challenge,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Challenge:
    """The challenge a folder (or its ``challenge.json``) describes, rebuilt from its seed.

    Only the manifest is read. The other files are rebuilt, so a folder whose tables were
    edited still grades against the data the seed gives. A challenge of an earlier suite
    this release still grades (:data:`GRADED_SUITES`) is rebuilt as that suite made it.

    One file cannot be rebuilt: the ``challenge.xml`` of a challenge made from another
    product's export. It is read from the manifest's folder (never through a link), must
    have the SHA-256 the manifest records, and is graded again as an export of this
    challenge's seed: it must pass. So a manifest cannot name a file that is not a
    correct export of the data the questions are about. ``max_bytes`` and
    ``time_budget`` limit that file and that grading; nothing else uses them.

    Args:
        source: A challenge folder, its ``challenge.json``, or a :class:`Challenge`
            (returned as it is).
        max_bytes: Largest ``challenge.xml`` of a pairwise challenge accepted (default
            100 MiB).
        time_budget: Seconds for reading and for validating that file, each.

    Raises:
        DocumentError: The manifest is missing, too large, not a challenge manifest, or
            from a suite version this release does not grade; or the export a pairwise
            challenge was made from is not in its folder, is another file, or does not
            pass the export class for this seed (``kind`` says which:
            ``not-a-challenge``, ``suite-version`` or ``source-export``).
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
        suite = manifest.get("suite_version")
        if suite not in GRADED_SUITES:
            raise DocumentError(
                "suite-version",
                f"the challenge was made by suite {suite!r} and this release grades suite"
                f" {' and '.join(GRADED_SUITES)}; make a new challenge, or install the"
                " pyDIGGS release that has that suite",
            )
        product = manifest["product"]
        seed = manifest["seed"]
        if not isinstance(seed, str) or not isinstance(product, dict):
            raise DocumentError("not-a-challenge", f"{path} is not a challenge manifest")
        info = class_info(manifest["class"])
        if not _SEED.fullmatch(seed):
            raise DocumentError("not-a-challenge", f"the seed in {path} is not a seed")
        record: SourceExport | None = None
        exported: bytes | None = None
        if "source_export" in manifest:
            record = _loaded_source(manifest, info, path)
            exported = _source_file(
                path.parent, info, seed, record, max_bytes=max_bytes, time_budget=time_budget
            )
        rebuilt = _make(
            info,
            _clean(product["name"], "product"),
            _clean(product["version"], "version"),
            seed,
            suite,
            source=record,
            exported=exported,
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
