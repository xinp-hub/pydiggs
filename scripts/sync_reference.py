"""Sync and verify pyDIGGS's vendored DIGGS reference data.

pyDIGGS bundles the DIGGS schemas, dictionaries and deprecation registry, and tests
against the official example files. Each comes from a DIGGSml repository, pinned to a
commit and recorded with a sha256 per file:

* ``src/pydiggs/PROVENANCE.toml`` — package data (ships in the wheel)
* ``tests/fixtures/official/PROVENANCE.toml`` — the official example corpus

Usage (from the repository root)::

    # Verify every vendored file against its recorded digest (offline; CI runs this).
    python scripts/sync_reference.py check

    # Re-sync from local clones of DIGGSml/schemas, DIGGSml/def and
    # DIGGSml/diggs-examples. Files are read from each clone's HEAD commit (git objects,
    # never the working tree), so check out the commit you intend to pin.
    python scripts/sync_reference.py sync --schemas ../schemas --def ../def \
        --examples ../diggs-examples

The script never downloads anything: clone the upstream repositories yourself and review
the resulting diff like a dependency bump.

Text files are stored with LF line endings and hashed after CRLF -> LF normalisation,
so a checkout with ``core.autocrlf`` still verifies.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "pydiggs"
FIXTURES = ROOT / "tests" / "fixtures" / "official"
PKG_PROVENANCE = PKG / "PROVENANCE.toml"
FIXTURE_PROVENANCE = FIXTURES / "PROVENANCE.toml"

REPOS = {
    "schemas": "https://github.com/DIGGSml/schemas",
    "def": "https://github.com/DIGGSml/def",
    "examples": "https://github.com/DIGGSml/diggs-examples",
}

# Every component of a vendored path must match this. It keeps out names that a wheel
# installer could reinterpret (backslashes, "..", ":" streams, control characters).
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ +-]*")
# Windows refuses these as file names, whatever the extension.
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?", re.IGNORECASE)

# Files copied out of an upstream schema tree. Directory listings (index*.html) and
# dotfiles are not schema content and are left behind.
_SCHEMA_SUFFIXES = (".xsd", ".md")
_SCHEMA_NAMES = ("LICENSE",)

# Package files that are code or packaging, not vendored data; check() skips them.
_PKG_NON_DATA_SUFFIXES = (".py", ".pyc", ".typed")


@dataclass(frozen=True)
class TreeRule:
    """Mirror ``source:src_dir`` into ``dest``, relative to the package.

    Files under ``dest`` that upstream no longer has are deleted.
    """

    source: str
    src_dir: str
    dest: str
    suffixes: tuple[str, ...]
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileRule:
    """Copy one upstream file ``source:src`` to ``dest``, relative to the package."""

    source: str
    src: str
    dest: str


PACKAGE_TREES = (
    TreeRule("schemas", "2.5.a", "schemas/diggs-schema-2.5.a", _SCHEMA_SUFFIXES, _SCHEMA_NAMES),
    TreeRule("schemas", "2.6", "schemas/diggs-schema-2.6", _SCHEMA_SUFFIXES, _SCHEMA_NAMES),
    TreeRule("schemas", "3.0.0", "schemas/diggs-schema-3.0.0", _SCHEMA_SUFFIXES, _SCHEMA_NAMES),
    # diggsml.org/def is published from def/docs, so that tree is the canonical one.
    TreeRule("def", "docs/codes/DIGGS/0.1", "dictionaries/codes", (".xml",)),
)

PACKAGE_FILES = (
    FileRule("schemas", "LICENSE", "schemas/LICENSE"),
    FileRule(
        "schemas", "deprecation/DeprecationRegistry.xml", "deprecation/DeprecationRegistry.xml"
    ),
    FileRule(
        "schemas", "deprecation/DeprecationRegistry.xsd", "deprecation/DeprecationRegistry.xsd"
    ),
    FileRule("def", "docs/codes/DIGGS/0.1/properties.xml", "dictionaries/properties.xml"),
    FileRule("def", "docs/units/DiggsUomDictionary.xml", "dictionaries/DiggsUomDictionary.xml"),
)

# Package files maintained in this repository rather than synced. They are still
# digest-checked, so an accidental edit shows up in review.
PACKAGE_LOCAL = {
    "dictionaries/DIGGSTestPropertyDefinitions.xml": (
        "Legacy pre-def/codes property dictionary; no current upstream equivalent."
    ),
    "dictionaries/NOTICE": "Licence notice for the DIGGSml/def dictionaries.",
    "findings/diggs-findings-1.0.schema.json": (
        "The diggs-findings JSON Schema: pyDIGGS's own output contract, not upstream data."
    ),
    "dictionaries/whiteList.xml": (
        "Network and local-path allow-list for codeSpace URLs. Security policy, so it is "
        "reviewed here rather than synced (DIGGSml/def docs/validation/whiteList.xml is "
        "the upstream starting point)."
    ),
    "schematron/diggs_schematron_rules_2.6.sch": (
        "Adapted from DIGGSml/validation diggs_schematron_rules.sch for lxml (xslt1); "
        "see the file header for the changes."
    ),
}

# Fixture files copied as-is from DIGGSml/diggs-examples (beside the example map below).
FIXTURE_FILES = {"LICENSE": "LICENSE"}

# Local fixture name -> path in DIGGSml/diggs-examples.
FIXTURE_MAP = {
    "2.5.a/AtterbergExample.xml": "2.5.a Example Instances/AtterbergExample.xml",
    "2.5.a/LithoGrading.xml": "2.5.a Example Instances/LithoGrading.xml",
    "2.5.a/ODOT_600304_20180702.xml": (
        "2.5.a Example Instances/ODOT Example 600304_20180702/600304_20180702.xml"
    ),
    "2.5.a/ODOT_600339_20180702.xml": (
        "2.5.a Example Instances/ODOT Example 600339_20180702/600339_20180702.xml"
    ),
    "2.5.a/ODOT_Example_2_20180702.xml": (
        "2.5.a Example Instances/ODOT Example Example_2_20180702/Example_2_20180702.xml"
    ),
    "2.5.a/PorePressureDissipation.xml": "2.5.a Example Instances/PorePressureDissipation.xml",
    "2.5.a/StrengthExample.xml": "2.5.a Example Instances/StrengthExample.xml",
    "2.5.a/cptExample.xml": "2.5.a Example Instances/cptExample.xml",
    "2.5.a/water_content.xml": "2.5.a Example Instances/water_content.xml",
    "2.6/Aeromag.xml": "2.6 Example instances/Aeromag.xml",
    "2.6/AtterbergExample-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/AtterbergExample.xml"
    ),
    "2.6/CPT_and_PorePressureDissipation.xml": (
        "2.6 Example instances/CPT_and_PorePressureDissipation.xml"
    ),
    "2.6/CompactionGroutingExample.xml": "2.6 Example instances/CompactionGroutingExample.xml",
    "2.6/DeepSoilMixingExample-Shortened.xml": (
        "2.6 Example instances/DeepSoilMixingExample-Shortened.xml"
    ),
    "2.6/DocumentationExample.xml": "2.6 Example instances/DocumentationExample.xml",
    "2.6/JetGroutingExample.xml": "2.6 Example instances/JetGroutingExample.xml",
    "2.6/LabCompaction.xml": "2.6 Example instances/LabCompaction.xml",
    "2.6/LithoGrading-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/LithoGrading.xml"
    ),
    "2.6/LugeonTestExample.xml": "2.6 Example instances/LugeonTestExample.xml",
    "2.6/ODOT_600304_20180702-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/"
        "ODOT Example 600304_20180702/600304_20180702.xml"
    ),
    "2.6/ODOT_600339_20180702-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/"
        "ODOT Example 600339_20180702/600339_20180702.xml"
    ),
    "2.6/PermeationGroutingExample.xml": "2.6 Example instances/PermeationGroutingExample.xml",
    "2.6/PorePressureDissipation.xml": "2.6 Example instances/PorePressureDissipation.xml",
    "2.6/PorePressureDissipationOnly.xml": "2.6 Example instances/PorePressureDissipationOnly.xml",
    "2.6/RockGroutingExample.xml": "2.6 Example instances/RockGroutingExample.xml",
    "2.6/StrengthExample-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/StrengthExample.xml"
    ),
    "2.6/cptExample-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/cptExample.xml"
    ),
    "2.6/water_content-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/water_content.xml"
    ),
    "3.0/Borehole-CPT_Example_Annotated.xml": (
        "3.x Example Instances/Borehole-CPT_Example_Annotated.xml"
    ),
    "3.0/MWDExample.xml": "3.x Example Instances/MWDExample.xml",
    "3.0/Piezometer_Example.xml": "3.x Example Instances/Piezometer Example.xml",
    # Upstream rewrote it with schema-dev (3.1 draft) types but still declares 3.0.0.
    "known_invalid/3.0_PileDrivingExample_schema-dev.xml": (
        "3.x Example Instances/PileDrivingExample.xml"
    ),
    "3.0/PorePressureDissipation_Example.xml": (
        "3.x Example Instances/PorePressureDissipation Example.xml"
    ),
    "3.0/TrenchWallExample.xml": "3.x Example Instances/TrenchWallExample.xml",
    "2.6/FieldSurveyExample.xml": "2.6 Example instances/FieldSurveyExample.xml",
    "2.6/ODOT_Example_2_20180702-from25.xml": (
        "2.6 Example instances/diggs-examples-2.5-validated_to_2.6/"
        "ODOT Example Example_2_20180702/Example_2_20180702.xml"
    ),
    "known_invalid/2.6_Temporal_Spatial_CRS_Example.xml": (
        "2.6 Example instances/Temporal_Spatial_CRS_Example.xml"
    ),
    "3.0/Aeromag.xml": "3.x Example Instances/Aeromag.xml",
    "3.0/GeophysicalFieldSurveyExamples.xml": (
        "3.x Example Instances/GeophysicalFieldSurveyExamples.xml"
    ),
    "known_invalid/PlanarReference_3ns.xml": "2.6 Example instances/PlanarReference.xml",
}

_T50_FIX = (
    "t50 uom kPa -> s, as in the 3.0 example (https://github.com/DIGGSml/diggs-examples/issues/4)"
)


@dataclass(frozen=True)
class Correction:
    """A local fix to an upstream fixture.

    ``old`` becomes ``new`` on the one line, within six lines after a line containing
    ``anchor``. Matching nothing, or more than once, fails the sync, so an upstream fix
    is noticed and the correction can be dropped.
    """

    fixture: str
    old: str
    new: str
    anchor: str
    reason: str


FIXTURE_CORRECTIONS = (
    Correction(
        "2.6/PorePressureDissipation.xml", "<uom>kPa</uom>", "<uom>s</uom>", "t50", _T50_FIX
    ),
    Correction(
        "2.6/PorePressureDissipationOnly.xml", "<uom>kPa</uom>", "<uom>s</uom>", "t50", _T50_FIX
    ),
    Correction(
        "2.6/CPT_and_PorePressureDissipation.xml",
        "<uom>kPa</uom>",
        "<uom>s</uom>",
        "t50",
        _T50_FIX,
    ),
)


class SyncError(Exception):
    """The upstream checkout or the vendored files are not in the expected state."""


def _normalise(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(_normalise(data)).hexdigest()


def _digest(path: Path) -> str:
    return _sha256(path.read_bytes())


def _unsafe_reason(rel: str) -> str | None:
    """Why ``rel`` (a POSIX relative path) may not be vendored, or None if it may."""
    for part in rel.split("/"):
        if not _SAFE_COMPONENT.fullmatch(part):
            return f"unsafe path component {part!r}"
        if part.endswith((".", " ")) or _RESERVED.fullmatch(part):
            return f"name {part!r} is not portable to Windows"
    return None


def _git_bin() -> str:
    git = shutil.which("git")
    if git is None:
        raise SyncError("git is not on PATH")
    return git


def _git(checkout: Path, *args: str) -> bytes:
    # Fixed argument list (no shell); the checkout path is the operator's own input.
    return subprocess.run(  # noqa: S603
        [_git_bin(), "-C", str(checkout), *args], check=True, capture_output=True
    ).stdout


class Upstream:
    """Read files from one clone's HEAD commit through git, never the working tree."""

    def __init__(self, key: str, checkout: Path) -> None:
        self.key = key
        self.checkout = checkout
        self.commit = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        remote = _git(checkout, "remote", "get-url", "origin").decode().strip()
        if remote.removesuffix(".git").rstrip("/") != REPOS[key]:
            raise SyncError(f"{checkout}: origin is {remote}, expected {REPOS[key]}")
        self.date = _git(checkout, "log", "-1", "--format=%cI", self.commit).decode().strip()
        listing = _git(checkout, "ls-tree", "-r", "-z", "--full-tree", self.commit)
        # "<mode> <type> <object>\t<path>" per entry; blobs only.
        self._blobs: dict[str, str] = {}
        for entry in filter(None, listing.split(b"\0")):
            meta, path = entry.decode("utf-8").split("\t", 1)
            mode, kind, _ = meta.split(" ")
            if kind == "blob":
                self._blobs[path] = mode

    def info(self) -> dict[str, str]:
        """The ``[sources.<key>]`` table recorded in PROVENANCE.toml."""
        return {"repo": REPOS[self.key], "commit": self.commit, "date": self.date}

    def read(self, rel: str) -> bytes:
        """The bytes of regular file ``rel`` at the pinned commit."""
        mode = self._blobs.get(rel)
        if mode is None:
            raise SyncError(f"{self.key}@{self.commit[:7]} has no file {rel!r}")
        if mode not in ("100644", "100755"):
            raise SyncError(f"refusing {self.key}:{rel}: not a regular file (mode {mode})")
        return _git(self.checkout, "cat-file", "blob", f"{self.commit}:{rel}")

    def tree(self, rule: TreeRule) -> list[str]:
        """Paths under ``rule.src_dir`` (relative to it) that the rule vendors."""
        prefix = rule.src_dir.rstrip("/") + "/"
        found = []
        for path in sorted(self._blobs):
            if not path.startswith(prefix):
                continue
            rel = path[len(prefix) :]
            name = rel.rsplit("/", 1)[-1]
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if name.endswith(rule.suffixes) or name in rule.names:
                found.append(rel)
        if not found:
            raise SyncError(f"{self.key}@{self.commit[:7]}: nothing to vendor in {rule.src_dir}")
        return found


_FIRST_PRINTABLE = 0x20
_DEL = 0x7F


def _toml_str(value: str) -> str:
    out = []
    for ch in value:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < _FIRST_PRINTABLE or ord(ch) == _DEL:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _render_provenance(
    header: str,
    sources: dict[str, dict[str, str]],
    files: dict[str, dict[str, str]],
) -> bytes:
    lines = [f"# {line}".rstrip() for line in header.splitlines()]
    lines.append("# Generated by scripts/sync_reference.py. Do not edit by hand.")
    for key, info in sources.items():
        lines += ["", f"[sources.{key}]"]
        lines += [f"{k} = {_toml_str(v)}" for k, v in info.items()]
    for rel in sorted(files):
        lines += ["", f"[files.{_toml_str(rel)}]"]
        lines += [f"{k} = {_toml_str(v)}" for k, v in files[rel].items()]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _apply_correction(text: str, corr: Correction) -> tuple[str, str]:
    lines = text.split("\n")
    hits = [
        i
        for i, line in enumerate(lines)
        if corr.old in line and any(corr.anchor in prev for prev in lines[max(0, i - 6) : i])
    ]
    if len(hits) != 1:
        raise SyncError(
            f"{corr.fixture}: expected one {corr.old!r} after {corr.anchor!r}, found "
            f"{len(hits)}; upstream may have fixed it, so drop the correction"
        )
    lines[hits[0]] = lines[hits[0]].replace(corr.old, corr.new, 1)
    return "\n".join(lines), f"line {hits[0] + 1} {corr.old} -> {corr.new}"


def _plan(upstreams: dict[str, Upstream]) -> tuple[dict[Path, bytes], set[Path]]:
    """Everything the sync will write, and the directories it mirrors.

    Nothing is written here, so a failure leaves the tree untouched.
    """
    writes: dict[Path, bytes] = {}
    pkg_files: dict[str, dict[str, str]] = {}

    def add(dest_rel: str, base: Path, data: bytes, entry: dict[str, str]) -> None:
        reason = _unsafe_reason(dest_rel)
        if reason:
            raise SyncError(f"refusing {dest_rel!r}: {reason}")
        target = base / dest_rel
        if any(p.as_posix().casefold() == target.as_posix().casefold() for p in writes):
            raise SyncError(f"{dest_rel!r} differs from another vendored path only in case")
        writes[target] = _normalise(data)
        entry["sha256"] = _sha256(data)

    for rule in PACKAGE_TREES:
        up = upstreams[rule.source]
        for rel in up.tree(rule):
            src = f"{rule.src_dir}/{rel}"
            entry = {"source": rule.source, "path": src}
            add(f"{rule.dest}/{rel}", PKG, up.read(src), entry)
            pkg_files[f"{rule.dest}/{rel}"] = entry
    for file_rule in PACKAGE_FILES:
        up = upstreams[file_rule.source]
        entry = {"source": file_rule.source, "path": file_rule.src}
        add(file_rule.dest, PKG, up.read(file_rule.src), entry)
        pkg_files[file_rule.dest] = entry
    for rel, note in PACKAGE_LOCAL.items():
        if not (PKG / rel).is_file():
            raise SyncError(f"missing local package file {rel}")
        pkg_files[rel] = {"source": "local", "note": note, "sha256": _digest(PKG / rel)}

    examples = upstreams["examples"]
    fixture_files: dict[str, dict[str, str]] = {}
    for local, upstream_rel in {**FIXTURE_FILES, **FIXTURE_MAP}.items():
        text = _normalise(examples.read(upstream_rel)).decode("utf-8")
        notes = []
        for corr in FIXTURE_CORRECTIONS:
            if corr.fixture == local:
                text, note = _apply_correction(text, corr)
                notes.append(f"{note}: {corr.reason}")
        entry = {"source": "examples", "path": upstream_rel}
        add(local, FIXTURES, text.encode("utf-8"), entry)
        if notes:
            entry["corrections"] = "; ".join(notes)
        fixture_files[local] = entry

    writes[PKG_PROVENANCE] = _render_provenance(
        "DIGGS reference data vendored into pyDIGGS.",
        {k: upstreams[k].info() for k in ("schemas", "def")},
        pkg_files,
    )
    writes[FIXTURE_PROVENANCE] = _render_provenance(
        "Official DIGGS example instances used as the pyDIGGS test corpus.",
        {"examples": examples.info()},
        fixture_files,
    )
    mirrored = {PKG / rule.dest for rule in PACKAGE_TREES}
    return writes, mirrored


def sync(schemas: Path, defs: Path, examples: Path) -> list[str]:
    """Re-vendor from upstream clones; return a summary of what changed."""
    upstreams = {
        "schemas": Upstream("schemas", schemas),
        "def": Upstream("def", defs),
        "examples": Upstream("examples", examples),
    }
    writes, mirrored = _plan(upstreams)
    changes: list[str] = []
    # Remove stale files first, so a case-only rename upstream can't delete the new name
    # on a case-insensitive filesystem.
    for directory in sorted(mirrored):
        for existing in sorted(_data_files(directory)):
            if existing not in writes:
                existing.unlink()
                changes.append(f"removed {existing.relative_to(ROOT)}")
    for target, data in writes.items():
        before = target.read_bytes() if target.is_file() else None
        if before != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if target not in (PKG_PROVENANCE, FIXTURE_PROVENANCE):
                kind = "updated" if before is not None else "added"
                changes.append(f"{kind} {target.relative_to(ROOT)}")
    return changes


def _data_files(directory: Path) -> list[Path]:
    """Files under ``directory``, skipping dotfiles (e.g. .DS_Store) and __pycache__."""
    if not directory.is_dir():
        return []
    return [
        p
        for p in directory.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.relative_to(directory).parts
        and not any(part.startswith(".") for part in p.relative_to(directory).parts)
    ]


def check() -> list[str]:
    """Return every mismatch between the vendored files and their recorded digests."""
    problems: list[str] = []
    for prov_path, base in ((PKG_PROVENANCE, PKG), (FIXTURE_PROVENANCE, FIXTURES)):
        if not prov_path.is_file():
            problems.append(f"missing {prov_path.relative_to(ROOT)}")
            continue
        try:
            recorded = tomllib.loads(prov_path.read_text(encoding="utf-8"))["files"]
        except (tomllib.TOMLDecodeError, KeyError, UnicodeDecodeError) as err:
            problems.append(f"unreadable {prov_path.relative_to(ROOT)}: {err}")
            continue
        for rel, entry in recorded.items():
            path = base / rel
            reason = _unsafe_reason(rel)
            if reason:
                problems.append(f"unsafe recorded path {rel!r}: {reason}")
            elif not path.is_file():
                problems.append(f"missing {path.relative_to(ROOT)}")
            elif _digest(path) != entry.get("sha256"):
                problems.append(f"digest mismatch {path.relative_to(ROOT)}")
        # Every data file that ships (or is a test fixture) must be recorded.
        for path in sorted(_data_files(base)):
            rel = path.relative_to(base).as_posix()
            if base == PKG and (path.suffix in _PKG_NON_DATA_SUFFIXES or path == PKG_PROVENANCE):
                continue
            if base == FIXTURES and path.name in ("PROVENANCE.toml", "PROVENANCE.txt"):
                continue
            if rel not in recorded:
                problems.append(f"unrecorded file {path.relative_to(ROOT)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Run the ``check`` or ``sync`` command; return the process exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="verify vendored files against PROVENANCE.toml")
    sync_p = sub.add_parser("sync", help="re-vendor from local upstream clones")
    sync_p.add_argument("--schemas", type=Path, required=True)
    sync_p.add_argument("--def", dest="defs", type=Path, required=True)
    sync_p.add_argument("--examples", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            for change in sync(args.schemas, args.defs, args.examples):
                print(change)
        problems = check()
    except (SyncError, subprocess.CalledProcessError, OSError, UnicodeDecodeError) as err:
        detail = getattr(err, "stderr", None)
        print(f"error: {err}" + (f"\n{detail.decode(errors='replace')}" if detail else ""))
        return 2
    for problem in problems:
        print(problem, file=sys.stderr)
    print("reference data OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
