"""Pairwise import challenges: one product is tested on the graded export of another.

``new(..., from_export=...)`` makes an import challenge whose DIGGS file is another
product's export. Everything it is given comes from another party, so most of these tests
are about what it refuses; the rest follow the record of that export (``source_export``)
from the challenge through the evidence into the matrix.
"""

from __future__ import annotations

import dataclasses
import doctest
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from pydiggs import cli, conformance
from pydiggs.conformance import _challenge, _matrix, _pairwise, _registry
from pydiggs.errors import DocumentError, InstanceReadError

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = ROOT / "src" / "pydiggs" / "conformance"
HOSTILE = "<script>alert(1)</script>"
links = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")


def _exported(challenge: conformance.Challenge) -> bytes:
    """A correct export that is not the suite's own bytes: another product wrote it."""
    return challenge.reference() + b"<!-- written by another product -->\n"


def _export_folder(
    folder: Path, family: str = "spt", product: str = "LogA", *, seed: str = "pair"
) -> Path:
    """Product A's challenge folder: its export and the evidence ``grade`` writes for it."""
    challenge = conformance.new(f"{family}-export", product=product, version="2.0", seed=seed)
    target = challenge.write(folder)
    (target / "export.xml").write_bytes(_exported(challenge))
    result = conformance.grade(target, target / "export.xml")
    assert result.passed
    (target / "evidence.json").write_text(result.to_json() + "\n", encoding="utf-8")
    return target


def _answers(challenge: conformance.Challenge, *, right: bool = True) -> bytes:
    asked = _registry.questions(challenge.dataset(), challenge.stream)
    lines = ["question_id,answer"]
    lines += [f'{q.id},"{q.expected if right else "0"}"' for q in asked]
    return ("\n".join(lines) + "\n").encode()


def _import_folder(
    folder: Path, source: Path, product: str = "Viewer", *, right: bool = True
) -> Path:
    """Product B's pairwise challenge folder, answered and graded."""
    family = json.loads((source / "evidence.json").read_text("utf-8"))["class"]["family"]
    challenge = conformance.new(
        f"{family}-import", product=product, version="7", from_export=source / "evidence.json"
    )
    target = challenge.write(folder)
    (target / "answers.csv").write_bytes(_answers(challenge, right=right))
    result = conformance.grade(target, target / "answers.csv")
    assert result.passed is right
    (target / "evidence.json").write_text(result.to_json() + "\n", encoding="utf-8")
    return target


@pytest.fixture(scope="module")
def exporter(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _export_folder(tmp_path_factory.mktemp("pairwise") / "a-spt-export")


@pytest.fixture(scope="module")
def importer(exporter: Path) -> Path:
    return _import_folder(exporter.parent / "b-spt-import", exporter)


def _copy(src: Path, dst: Path) -> Path:
    dst.mkdir(parents=True)
    for path in src.iterdir():
        (dst / path.name).write_bytes(path.read_bytes())
    return dst


def _rewrite(path: Path, change: Any) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    return dict(document)


# --- the challenge --------------------------------------------------------------------


def test_the_challenge_hands_over_the_other_products_bytes(exporter: Path) -> None:
    export = (exporter / "export.xml").read_bytes()
    evidence = (exporter / "evidence.json").read_bytes()
    pair = conformance.new(
        "spt-import", product="Viewer", version="7", from_export=exporter / "evidence.json"
    )
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="pair")
    assert pair.files["challenge.xml"] == export != plain.files["challenge.xml"]
    # The same seed, so the same data, questions and defective file as the suite's own.
    assert (pair.seed, pair.token) == (plain.seed, plain.token)
    assert pair.files["answers.csv"] == plain.files["answers.csv"]
    assert pair.files["defective.xml"] == plain.files["defective.xml"]
    assert pair.reference() == plain.files["challenge.xml"]
    source = pair.source_export
    assert source == conformance.SourceExport(
        "LogA",
        "2.0",
        hashlib.sha256(export).hexdigest(),
        hashlib.sha256(evidence).hexdigest(),
        conformance.SUITE_VERSION,
    )
    manifest = pair.manifest
    assert manifest["source_export"] == source.to_dict()
    assert manifest["files"]["challenge.xml"] == source.export_sha256
    assert plain.source_export is None and "source_export" not in plain.manifest
    assert pair.id != plain.id  # the identity names the export too
    readme = pair.files["README.md"].decode()
    assert source.export_sha256 in readme and "LogA 2.0" in readme


def test_the_record_holds_digests_and_never_a_path(exporter: Path) -> None:
    pair = conformance.new(
        "spt-import", product="Viewer", version="7", from_export=exporter / "evidence.json"
    )
    for name in ("challenge.json", "README.md", "answers.csv"):
        text = pair.files[name].decode()
        assert str(exporter) not in text and exporter.name not in text
        assert "export.xml" not in text


def test_evidence_and_export_may_be_given_as_bytes(exporter: Path) -> None:
    evidence = (exporter / "evidence.json").read_bytes()
    export = (exporter / "export.xml").read_bytes()
    by_path = conformance.new(
        "spt-import",
        product="Viewer",
        version="7",
        from_export=str(exporter / "evidence.json"),
        export=exporter / "export.xml",
    )
    by_bytes = conformance.new(
        "spt-import", product="Viewer", version="7", from_export=evidence, export=export
    )
    assert by_path.files == by_bytes.files
    with pytest.raises(ValueError, match="give the export file"):
        conformance.new("spt-import", product="Viewer", version="7", from_export=evidence)


def test_usage_errors_say_what_is_wrong(exporter: Path) -> None:
    evidence = exporter / "evidence.json"
    with pytest.raises(ValueError, match="only for an import class"):
        conformance.new("spt-export", product="V", version="7", from_export=evidence)
    with pytest.raises(ValueError, match="seed 'pair'"):
        conformance.new("spt-import", product="V", version="7", from_export=evidence, seed="x")
    with pytest.raises(ValueError, match="give the evidence too"):
        conformance.new("spt-import", product="V", version="7", export=exporter / "export.xml")
    same = conformance.new(
        "spt-import", product="V", version="7", from_export=evidence, seed="pair"
    )
    assert same.seed == "pair"


def test_write_and_load_give_the_challenge_back(exporter: Path, tmp_path: Path) -> None:
    pair = conformance.new(
        "spt-import", product="Viewer", version="7", from_export=exporter / "evidence.json"
    )
    folder = pair.write(tmp_path / "c")
    loaded = conformance.load(folder)
    assert loaded.files == pair.files
    assert (loaded.id, loaded.sha256, loaded.source_export) == (
        pair.id,
        pair.sha256,
        pair.source_export,
    )


# --- what new refuses -----------------------------------------------------------------


def _refusal(tmp_path: Path, exporter: Path, change: Any, class_id: str = "spt-import") -> str:
    folder = _copy(exporter, tmp_path / "theirs")
    change(folder)
    with pytest.raises(DocumentError) as caught:
        conformance.new(
            class_id, product="Viewer", version="7", from_export=folder / "evidence.json"
        )
    assert caught.value.kind in ("source-export", "not-evidence")
    return str(caught.value)


def test_evidence_that_does_not_verify_is_refused(exporter: Path, tmp_path: Path) -> None:
    def fewer_checks(folder: Path) -> None:
        def change(document: dict[str, Any]) -> None:
            fidelity = next(s for s in document["stages"] if s["id"] == "fidelity")
            fidelity["passed"] = fidelity["total"] = 1

        _rewrite(folder / "evidence.json", change)

    assert "does not verify" in _refusal(tmp_path, exporter, fewer_checks)


def test_evidence_naming_another_product_is_refused(exporter: Path, tmp_path: Path) -> None:
    # The product is part of the challenge's identity, so a renamed product is a
    # challenge the evidence's id does not belong to.
    def renamed(folder: Path) -> None:
        _rewrite(folder / "evidence.json", lambda d: d["product"].update(name="LogB"))

    message = _refusal(tmp_path, exporter, renamed)
    assert "does not verify" in message and "challenge" in message


def test_evidence_of_another_family_or_direction_is_refused(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    message = _refusal(tmp_path / "a", exporter, lambda folder: None, "cpt-import")
    assert "another family" in message and "cpt-export" in message
    with pytest.raises(DocumentError) as caught:
        conformance.new(
            "spt-import",
            product="Third",
            version="1",
            from_export=importer / "evidence.json",
            export=importer / "challenge.xml",
        )
    assert caught.value.kind == "source-export" and "an import class" in str(caught.value)

    def unknown(folder: Path) -> None:
        _rewrite(folder / "evidence.json", lambda d: d["class"].update(id="nothing-export"))

    assert "names no class" in _refusal(tmp_path / "b", exporter, unknown)


def test_a_failing_export_is_refused(tmp_path: Path) -> None:
    challenge = conformance.new("spt-export", product="LogA", version="2.0", seed="pair")
    folder = challenge.write(tmp_path / "a")
    # One blow count more than the source gives, wherever the seed put the first test.
    wrong = re.sub(
        rb'(<dataValues cs="," ts=" ">)(\d+)(</dataValues>)',
        lambda m: m.group(1) + str(int(m.group(2)) + 1).encode() + m.group(3),
        challenge.reference(),
        count=1,
    )
    assert wrong != challenge.reference()
    (folder / "export.xml").write_bytes(wrong)
    result = conformance.grade(folder, folder / "export.xml")
    assert not result.passed
    (folder / "evidence.json").write_text(result.to_json(), encoding="utf-8")
    with pytest.raises(DocumentError, match="did not pass spt-export") as caught:
        conformance.new(
            "spt-import", product="Viewer", version="7", from_export=folder / "evidence.json"
        )
    assert caught.value.kind == "source-export"
    # Calling it a pass does not help: the export is graded again.
    _rewrite(folder / "evidence.json", lambda d: d.update(verdict="pass"))
    with pytest.raises(DocumentError, match="does not verify"):
        conformance.new(
            "spt-import", product="Viewer", version="7", from_export=folder / "evidence.json"
        )


def test_another_file_than_the_one_graded_is_refused(exporter: Path, tmp_path: Path) -> None:
    def swapped(folder: Path) -> None:
        other = conformance.new("spt-export", product="LogA", version="2.0", seed="pair")
        (folder / "export.xml").write_bytes(other.reference())  # correct, and not the file

    message = _refusal(tmp_path, exporter, swapped)
    assert "is not the one the evidence graded" in message
    recorded = json.loads((exporter / "evidence.json").read_text("utf-8"))["submission"]["sha256"]
    assert recorded in message


def test_evidence_of_another_suite_version_is_refused(tmp_path: Path) -> None:
    # The questions of a new challenge are this suite's, drawn from this suite's data: an
    # export graded under suite 1.0-beta.2 answers other questions.
    info = conformance.class_info("spt-export")
    challenge = _challenge._make(info, "Demo", "1", "demo", "1.0-beta.2")
    evidence = conformance.grade(challenge, challenge.reference())
    assert evidence.passed and evidence.suite_version == "1.0-beta.2"
    with pytest.raises(DocumentError, match=r"under suite '1\.0-beta\.2'") as caught:
        conformance.new(
            "spt-import",
            product="Viewer",
            version="7",
            from_export=evidence.to_json().encode(),
            export=challenge.reference(),
        )
    assert caught.value.kind == "source-export"


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (b"{not json", "not-evidence"),
        (b"[" * 100_000, "not-evidence"),
        (b'{"format": "diggs-findings"}', "not-evidence"),
        (b'{"format": "diggs-conformance", "schema_version": "9"}', "not-evidence"),
        (b'{"format": "diggs-conformance", "schema_version": "1.0-beta.3"}', "source-export"),
    ],
)
def test_a_document_that_is_not_evidence_is_refused(body: bytes, kind: str) -> None:
    with pytest.raises(DocumentError) as caught:
        conformance.new("spt-import", product="V", version="7", from_export=body, export=b"<x/>")
    assert caught.value.kind == kind


def test_malformed_members_are_refused_and_never_crash(exporter: Path) -> None:
    good = json.loads((exporter / "evidence.json").read_text("utf-8"))
    export = (exporter / "export.xml").read_bytes()
    changes: list[dict[str, Any]] = [
        {"product": None},
        {"product": {"name": "  LogA ", "version": "2.0"}},
        {"product": {"name": "x" * 201, "version": "2.0"}},
        {"product": {"name": ["LogA"], "version": "2.0"}},
        {"challenge": {"seed": "../../etc"}},
        {"challenge": []},
        {"submission": None},
        {"submission": {"sha256": ["x"]}},
        {"class": "spt-export"},
        {"stages": None},
    ]
    for change in changes:
        raw = json.dumps({**good, **change}).encode()
        with pytest.raises(DocumentError):
            conformance.new("spt-import", product="V", version="7", from_export=raw, export=export)


def test_a_path_in_the_evidence_is_never_followed(exporter: Path, tmp_path: Path) -> None:
    # The evidence names its file; only a plain name beside the evidence is looked for.
    inner = _copy(exporter, tmp_path / "outer" / "inner")
    (tmp_path / "outer" / "export.xml").write_bytes((inner / "export.xml").read_bytes())
    (inner / "export.xml").unlink()
    _rewrite(inner / "evidence.json", lambda d: d["submission"].update(name="../export.xml"))
    with pytest.raises(DocumentError, match="plain name") as caught:
        conformance.new("spt-import", product="V", version="7", from_export=inner / "evidence.json")
    assert caught.value.kind == "source-export"
    for name in ("", ".", "..", "a/b", "a\\b", "a\x00b", 7, None):
        assert _pairwise.plain_name(name) is None
    assert _pairwise.plain_name("export.xml") == "export.xml"


def test_missing_and_oversized_files_are_refused(exporter: Path, tmp_path: Path) -> None:
    evidence, export = exporter / "evidence.json", exporter / "export.xml"
    with pytest.raises(InstanceReadError, match="cannot read the evidence"):
        conformance.new("spt-import", product="V", version="7", from_export=tmp_path / "none.json")
    with pytest.raises(InstanceReadError, match="cannot read the export"):
        conformance.new(
            "spt-import", product="V", version="7", from_export=evidence, export=tmp_path / "none"
        )
    with pytest.raises(InstanceReadError, match="is not a file"):
        conformance.new("spt-import", product="V", version="7", from_export=tmp_path)
    for given in (export, export.read_bytes()):
        with pytest.raises(InstanceReadError, match="larger than 100 bytes"):
            conformance.new(
                "spt-import",
                product="V",
                version="7",
                from_export=evidence,
                export=given,
                max_bytes=100,
            )
    with pytest.raises(InstanceReadError, match="too large"):
        conformance.new(
            "spt-import",
            product="V",
            version="7",
            from_export=b" " * (_pairwise.MAX_EVIDENCE_BYTES + 1),
            export=b"",
        )
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (_pairwise.MAX_EVIDENCE_BYTES + 1))
    with pytest.raises(InstanceReadError, match="larger than"):
        conformance.new("spt-import", product="V", version="7", from_export=big, export=b"")


@links
def test_a_link_is_never_followed(exporter: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "evidence.json").symlink_to(exporter / "evidence.json")
    (linked / "export.xml").symlink_to(exporter / "export.xml")
    with pytest.raises(InstanceReadError, match=r"the evidence .* is a link"):
        conformance.new(
            "spt-import", product="V", version="7", from_export=linked / "evidence.json"
        )
    with pytest.raises(InstanceReadError, match=r"the export .* is a link"):
        conformance.new(
            "spt-import",
            product="V",
            version="7",
            from_export=exporter / "evidence.json",
            export=linked / "export.xml",
        )
    # The file the evidence names, beside it, is not followed either.
    beside = tmp_path / "beside"
    beside.mkdir()
    (beside / "evidence.json").write_bytes((exporter / "evidence.json").read_bytes())
    (beside / "export.xml").symlink_to(exporter / "export.xml")
    with pytest.raises(InstanceReadError, match="is a link"):
        conformance.new(
            "spt-import", product="V", version="7", from_export=beside / "evidence.json"
        )


# --- loading: the export is part of the challenge --------------------------------------


def test_load_refuses_a_folder_whose_export_was_changed(importer: Path, tmp_path: Path) -> None:
    folder = _copy(importer, tmp_path / "changed")
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="pair")
    (folder / "challenge.xml").write_bytes(plain.files["challenge.xml"])  # the suite's own file
    with pytest.raises(DocumentError, match="is not the export this challenge was made from") as e:
        conformance.load(folder)
    assert e.value.kind == "source-export"
    with pytest.raises(DocumentError):
        conformance.grade(folder, folder / "answers.csv")
    (folder / "challenge.xml").unlink()
    with pytest.raises(DocumentError, match="graded only with that file") as e:
        conformance.load(folder / "challenge.json")
    assert e.value.kind == "source-export"


@links
def test_load_never_follows_a_link_to_the_export(importer: Path, tmp_path: Path) -> None:
    folder = _copy(importer, tmp_path / "linked")
    (folder / "challenge.xml").unlink()
    (folder / "challenge.xml").symlink_to(importer / "challenge.xml")
    with pytest.raises(DocumentError, match="is a link") as caught:
        conformance.load(folder)
    assert caught.value.kind == "source-export"


@pytest.mark.parametrize(
    "source",
    [
        None,
        "export.xml",
        {},
        {"product": {"name": "LogA", "version": "2.0"}},
        {
            "product": {"name": "LogA", "version": "2.0"},
            "export_sha256": "../../export.xml",
            "evidence_sha256": "0" * 64,
            "suite_version": conformance.SUITE_VERSION,
        },
        {
            "product": {"name": "LogA", "version": "2.0", "path": "/etc/passwd"},
            "export_sha256": "0" * 64,
            "evidence_sha256": "0" * 64,
            "suite_version": conformance.SUITE_VERSION,
        },
        {
            "product": {"name": "LogA", "version": "2.0"},
            "export_sha256": "0" * 64,
            "evidence_sha256": "0" * 64,
            "suite_version": conformance.SUITE_VERSION,
            "path": "export.xml",
        },
        {
            "product": {"name": "LogA", "version": "2.0"},
            "export_sha256": "A" * 64,
            "evidence_sha256": "0" * 64,
            "suite_version": conformance.SUITE_VERSION,
        },
        {
            "product": {"name": "LogA", "version": "2.0"},
            "export_sha256": "0" * 64,
            "evidence_sha256": "0" * 64,
            "suite_version": "1.0-beta.2",
        },
    ],
)
def test_a_malformed_source_in_a_manifest_is_refused(
    importer: Path, tmp_path: Path, source: object
) -> None:
    folder = _copy(importer, tmp_path / "c")
    _rewrite(folder / "challenge.json", lambda d: d.update(source_export=source))
    with pytest.raises(DocumentError) as caught:
        conformance.load(folder)
    assert caught.value.kind == "not-a-challenge"


def test_an_export_challenge_cannot_name_a_source(exporter: Path, tmp_path: Path) -> None:
    folder = _copy(exporter, tmp_path / "c")
    source = conformance.SourceExport("X", "1", "0" * 64, "0" * 64, conformance.SUITE_VERSION)
    _rewrite(folder / "challenge.json", lambda d: d.update(source_export=source.to_dict()))
    with pytest.raises(DocumentError, match="only an import challenge") as caught:
        conformance.load(folder)
    assert caught.value.kind == "not-a-challenge"


# --- the evidence ---------------------------------------------------------------------


def test_the_evidence_names_the_export_it_was_read_from(exporter: Path, importer: Path) -> None:
    document = json.loads((importer / "evidence.json").read_text("utf-8"))
    challenge = conformance.load(importer)
    assert document["schema_version"] == conformance.CONFORMANCE_SCHEMA_VERSION == "1.0-beta.3"
    assert document["source_export"] == challenge.manifest["source_export"]
    assert document["source_export"] == {
        "product": {"name": "LogA", "version": "2.0"},
        "export_sha256": hashlib.sha256((exporter / "export.xml").read_bytes()).hexdigest(),
        "evidence_sha256": hashlib.sha256((exporter / "evidence.json").read_bytes()).hexdigest(),
        "suite_version": conformance.SUITE_VERSION,
    }
    assert document["challenge"]["id"] == challenge.id
    assert (document["product"], document["verdict"]) == (
        {"name": "Viewer", "version": "7"},
        "pass",
    )
    jsonschema.Draft202012Validator(conformance.conformance_schema()).validate(document)
    assert conformance.verify(document, importer, importer / "answers.csv").ok
    result = conformance.grade(importer, importer / "answers.csv")
    assert result.source_export == challenge.source_export
    assert "exported by LogA 2.0" in result.to_text()
    assert document["source_export"]["export_sha256"] in result.to_html()


def test_evidence_without_a_source_is_what_it_was(exporter: Path) -> None:
    document = json.loads((exporter / "evidence.json").read_text("utf-8"))
    assert "source_export" not in document
    jsonschema.Draft202012Validator(conformance.conformance_schema()).validate(document)
    # Apart from its version, it is a 1.0-beta.2 document: nothing else was added.
    earlier = json.loads((PACKAGED / "diggs-conformance-1.0-beta.2.schema.json").read_text("utf-8"))
    as_before = {**document, "schema_version": "1.0-beta.2"}
    jsonschema.Draft202012Validator(earlier).validate(as_before)
    assert conformance.verify(as_before, exporter, exporter / "export.xml").ok
    result = conformance.grade(exporter, exporter / "export.xml")
    assert result.source_export is None and "file given" not in result.to_text()


def test_the_schema_is_in_step_with_the_code(importer: Path) -> None:
    schema = conformance.conformance_schema()
    assert schema["$id"] == conformance.CONFORMANCE_SCHEMA_URL
    assert schema["properties"]["schema_version"]["const"] == "1.0-beta.3"
    assert "source_export" not in schema["required"]
    document = json.loads((importer / "evidence.json").read_text("utf-8"))
    assert set(document) == set(schema["properties"])
    source = schema["properties"]["source_export"]
    assert set(source["required"]) == set(source["properties"]) == set(document["source_export"])
    for earlier in ("1.0-beta.1", "1.0-beta.2"):
        assert (PACKAGED / f"diggs-conformance-{earlier}.schema.json").is_file()
    validator = jsonschema.Draft202012Validator(schema)
    for bad in (
        {"path": "export.xml"},
        {"export_sha256": "x"},
        {"product": {"name": "LogA"}},
    ):
        changed = {**document, "source_export": {**document["source_export"], **bad}}
        assert not validator.is_valid(changed)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["product"].update(name="LogB"),
        lambda s: s["product"].update(version="9"),
        lambda s: s.update(export_sha256="0" * 64),
        lambda s: s.update(evidence_sha256="0" * 64),
        lambda s: s.update(suite_version="1.0-beta.2"),
    ],
)
def test_a_source_edited_in_the_evidence_does_not_verify(importer: Path, change: Any) -> None:
    document = json.loads((importer / "evidence.json").read_text("utf-8"))
    change(document["source_export"])
    outcome = conformance.verify(document, importer, importer / "answers.csv")
    assert not outcome.ok and outcome.differences == ("source_export",)


def test_a_source_cannot_be_removed_or_added_after_the_fact(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    document = json.loads((importer / "evidence.json").read_text("utf-8"))
    source = document.pop("source_export")
    assert conformance.verify(document, importer, importer / "answers.csv").differences == (
        "source_export",
    )
    # Evidence of the suite's own import challenge, given a source: the answers are the
    # same, and still it does not verify.
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="pair")
    folder = plain.write(tmp_path / "plain")
    answers = _answers(plain)
    claimed = {**conformance.grade(folder, answers).to_dict(), "source_export": source}
    assert conformance.verify(claimed, folder, answers).differences == ("source_export",)


def test_a_source_edited_in_challenge_and_evidence_alike_does_not_verify(
    importer: Path, tmp_path: Path
) -> None:
    # The challenge's identity covers the source, and the evidence names the challenge by
    # that identity: editing both documents the same way still leaves the id behind.
    folder = _copy(importer, tmp_path / "both")
    for name in ("challenge.json", "evidence.json"):
        _rewrite(folder / name, lambda d: d["source_export"]["product"].update(name="LogB"))
    outcome = conformance.verify(folder / "evidence.json", folder, folder / "answers.csv")
    assert outcome.differences == ("challenge",)
    # Naming another file does not get as far as grading: the folder does not hold it.
    _rewrite(folder / "challenge.json", lambda d: d["source_export"].update(export_sha256="0" * 64))
    with pytest.raises(DocumentError) as caught:
        conformance.verify(folder / "evidence.json", folder, folder / "answers.csv")
    assert caught.value.kind == "source-export"


def test_a_scorecard_leaves_pairwise_evidence_out(exporter: Path, importer: Path) -> None:
    documents = [
        json.loads((folder / "evidence.json").read_text("utf-8")) for folder in (exporter, importer)
    ]
    cards = {card.product_name: card for card in conformance.scorecard(documents)}
    assert cards["LogA"].passed == 1
    # Reading LogA's file is not the spt-import class, which is passed on the suite's own.
    assert cards["Viewer"].passed == 0
    assert next(r for r in cards["Viewer"].rows if r.family == "spt").import_ == "not-run"


# --- the matrix -----------------------------------------------------------------------


def test_the_matrix_says_read_and_cites_the_export(exporter: Path, importer: Path) -> None:
    found = conformance.matrix(exporter.parent)
    assert not found.unverified
    (trip,) = found.round_trips()
    assert (trip.family, trip.exporter, trip.importer) == ("spt", ("LogA", "2.0"), ("Viewer", "7"))
    assert trip.status == "read"
    assert trip.export_sha256 == hashlib.sha256((exporter / "export.xml").read_bytes()).hexdigest()
    assert trip.evidence == "b-spt-import/evidence.json"
    # The pairwise result decides the pair, and no class of the importer.
    assert found.cell(("Viewer", "7"), "spt-import") == "not-run"
    assert found.cell(("LogA", "2.0"), "spt-export") == "pass"
    document = json.loads(found.to_json())
    jsonschema.Draft202012Validator(conformance.conformance_schema("matrix")).validate(document)
    assert document["schema_version"] == conformance.MATRIX_SCHEMA_VERSION == "1.0-beta.1"
    assert document["round_trips"][0]["export_sha256"] == trip.export_sha256
    assert document["round_trip_note"].startswith("read: ")
    entry = next(e for e in document["evidence"] if e["path"] == "b-spt-import/evidence.json")
    assert entry["source_export"]["product"] == {"name": "LogA", "version": "2.0"}
    for report in (found.to_text(), found.to_markdown(), found.to_html()):
        assert "read" in report and trip.export_sha256 in report


def test_the_matrix_says_not_read_when_the_answers_are_wrong(
    exporter: Path, tmp_path: Path
) -> None:
    base = tmp_path / "evidence"
    _copy(exporter, base / "a")
    _import_folder(base / "b", base / "a", right=False)
    found = conformance.matrix(base)
    assert not found.unverified
    (trip,) = found.round_trips()
    assert trip.status == "not-read" and trip.export_sha256 and trip.evidence == "b/evidence.json"
    jsonschema.Draft202012Validator(conformance.conformance_schema("matrix")).validate(
        found.to_dict()
    )
    # A later passing run of the same pair decides, as a class may be graded again.
    _import_folder(base / "b-again", base / "a")
    (trip,) = conformance.matrix(base).round_trips()
    assert (trip.status, trip.evidence) == ("read", "b-again/evidence.json")


def test_a_pair_tested_apart_stays_not_tested_together(exporter: Path, tmp_path: Path) -> None:
    base = tmp_path / "evidence"
    _copy(exporter, base / "a")
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="other")
    folder = plain.write(base / "b")
    (folder / "answers.csv").write_bytes(_answers(plain))
    evidence = conformance.grade(folder, folder / "answers.csv").to_json()
    (folder / "evidence.json").write_text(evidence, encoding="utf-8")
    found = conformance.matrix(base)
    (trip,) = found.round_trips()
    assert trip.status == "not-tested-together"
    assert trip.export_sha256 is None and trip.evidence is None
    assert set(trip.to_dict()) == {"family", "export", "import", "status"}
    assert "No evidence yet records" in found.to_dict()["round_trip_note"]
    jsonschema.Draft202012Validator(conformance.conformance_schema("matrix")).validate(
        found.to_dict()
    )


def test_the_exporters_evidence_in_the_folder_must_be_the_one_read(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    # LogA graded again, with another file: the evidence in the folder is no longer the
    # document, nor the file, that Viewer's challenge was made from. Naming LogA is not
    # enough then, and the pair is not claimed.
    base = tmp_path / "evidence"
    theirs = _copy(exporter, base / "a")
    _copy(importer, base / "b")
    challenge = conformance.load(theirs)
    (theirs / "export.xml").write_bytes(challenge.reference())
    evidence = conformance.grade(theirs, theirs / "export.xml").to_json()
    (theirs / "evidence.json").write_text(evidence, encoding="utf-8")
    found = conformance.matrix(base)
    assert not found.unverified
    assert found.round_trips() == ()
    # The same file and verdict, the evidence document written another way: its digest
    # is not the one the challenge recorded.
    theirs = _copy(exporter, base / "a2")
    (base / "a" / "evidence.json").unlink()
    _rewrite(theirs / "evidence.json", lambda d: None)
    assert conformance.matrix(base).round_trips() == ()


def test_exporter_evidence_that_does_not_verify_does_not_let_the_name_decide(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    base = tmp_path / "evidence"
    theirs = _copy(exporter, base / "a")
    _copy(importer, base / "b")
    (theirs / "export.xml").unlink()  # LogA's evidence is there and cannot be graded again
    found = conformance.matrix(base)
    assert [e.path for e in found.unverified] == ["a/evidence.json"]
    assert found.round_trips() == ()


def test_the_matrix_does_not_count_tampered_pairwise_evidence(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    base = tmp_path / "evidence"
    _copy(exporter, base / "a")
    _copy(exporter, base / "other")  # the same product's evidence, twice
    renamed = _copy(importer, base / "renamed")
    _rewrite(renamed / "evidence.json", lambda d: d["source_export"]["product"].update(name="LogB"))
    swapped = _copy(importer, base / "swapped")
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="pair")
    (swapped / "challenge.xml").write_bytes(plain.files["challenge.xml"])
    junk = _copy(importer, base / "junk")
    _rewrite(junk / "evidence.json", lambda d: d.update(source_export={"path": "../a/export.xml"}))
    found = conformance.matrix(base)
    reasons = {e.path: e.reason for e in found.unverified}
    assert "source_export" in reasons["renamed/evidence.json"]
    assert "is not the export this challenge was made from" in reasons["swapped/evidence.json"]
    assert "malformed" in reasons["junk/evidence.json"]
    assert found.round_trips() == ()
    jsonschema.Draft202012Validator(conformance.conformance_schema("matrix")).validate(
        found.to_dict()
    )


def test_the_matrix_lists_evidence_of_an_earlier_suite_and_does_not_count_it(
    tmp_path: Path,
) -> None:
    info = conformance.class_info("spt-export")
    folder = _challenge._make(info, "Old", "1", "demo", "1.0-beta.2").write(tmp_path / "e" / "old")
    (folder / "export.xml").write_bytes(conformance.load(folder).reference())
    result = conformance.grade(folder, folder / "export.xml")
    (folder / "evidence.json").write_text(result.to_json(), encoding="utf-8")
    assert conformance.verify(folder / "evidence.json", folder, folder / "export.xml").ok
    found = conformance.matrix(tmp_path / "e")
    (entry,) = found.unverified
    assert "suite '1.0-beta.2'" in entry.reason and found.products == ()
    with pytest.raises(ValueError, match=r"1\.0-beta\.2"):
        conformance.scorecard([result.to_dict()])


def test_reports_escape_the_exporters_name(tmp_path: Path) -> None:
    base = tmp_path / "evidence"
    theirs = _export_folder(base / "a", product=HOSTILE)
    ours = _import_folder(base / "b", theirs)
    result = conformance.grade(ours, ours / "answers.csv")
    assert HOSTILE not in result.to_html() and "&lt;script&gt;" in result.to_html()
    readme = (ours / "README.md").read_text("utf-8")
    assert HOSTILE not in readme and "\\<script\\>" in readme
    found = conformance.matrix(base)
    assert found.round_trips()[0].status == "read"
    assert HOSTILE not in found.to_html() and HOSTILE not in found.to_markdown()


def test_without_the_exporters_evidence_no_cell_is_claimed(importer: Path, tmp_path: Path) -> None:
    # What pairwise evidence says of its source is the importing party's own statement.
    # Alone it verifies, and decides nothing; its entry says why.
    base = tmp_path / "evidence"
    _copy(importer, base / "b")
    found = conformance.matrix(base)
    assert found.round_trips() == () and not found.unverified
    (entry,) = found.entries
    assert entry.verified and entry.reason == _matrix.UNBOUND
    assert "is not in this folder" in entry.reason
    document = found.to_dict()
    assert document["evidence"][0]["reason"] == _matrix.UNBOUND
    jsonschema.Draft202012Validator(conformance.conformance_schema("matrix")).validate(document)


def test_the_exporters_evidence_must_be_of_the_same_seed(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    # Every digest matches and the seed the exporter's evidence states does not: the two
    # documents are not about the same data. (Its own evidence no longer verifies either;
    # the seed is checked on its own account.)
    base = tmp_path / "evidence"
    _copy(exporter, base / "a")
    _copy(importer, base / "b")
    found = conformance.matrix(base)
    assert found.round_trips()[0].status == "read"
    entries = tuple(
        dataclasses.replace(e, seed="other") if e.class_id == "spt-export" else e
        for e in found.entries
    )
    assert conformance.Matrix(entries).round_trips() == ()


# --- a source nobody exported: what a hand-written manifest cannot do -------------------


def _claim(folder: Path, source: dict[str, Any], held: bytes) -> None:
    """Make the ordinary import challenge in ``folder`` say it was made from an export."""
    (folder / "challenge.xml").write_bytes(held)
    _rewrite(folder / "challenge.json", lambda d: d.update(source_export=source))


def test_an_invented_exporter_and_a_file_that_is_no_export_are_refused(tmp_path: Path) -> None:
    # Found in review: challenge.json is the vendor's own file and challenge.id is a plain
    # hash, so an ordinary import challenge with a hand-added source_export, naming an
    # exporter that never ran the suite and the digest of whatever challenge.xml holds,
    # graded PASS, verified, and filled a matrix cell with `read`.
    base = tmp_path / "evidence"
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="mine")
    folder = plain.write(base / "b")
    held = b"not xml at all"
    source = {
        "product": {"name": "gINT", "version": "10"},
        "export_sha256": hashlib.sha256(held).hexdigest(),
        "evidence_sha256": "0" * 64,
        "suite_version": conformance.SUITE_VERSION,
    }
    _claim(folder, source, held)
    (folder / "answers.csv").write_bytes(_answers(plain))
    with pytest.raises(DocumentError, match="is not a passing spt-export export") as caught:
        conformance.grade(folder, folder / "answers.csv")
    assert caught.value.kind == "source-export"
    with pytest.raises(DocumentError):
        conformance.load(folder)
    # Evidence written for it by hand is not counted, and no cell is claimed.
    faked = conformance.grade(plain, _answers(plain)).to_dict() | {"source_export": source}
    faked["submission"]["name"] = "answers.csv"
    (folder / "evidence.json").write_text(json.dumps(faked), encoding="utf-8")
    with pytest.raises(DocumentError):
        conformance.verify(folder / "evidence.json", folder, folder / "answers.csv")
    found = conformance.matrix(base)
    (entry,) = found.unverified
    assert "is not a passing spt-export export" in entry.reason
    assert found.round_trips() == ()


def test_an_invented_exporter_with_a_correct_file_claims_no_cell(tmp_path: Path) -> None:
    # The same false claim with the suite's own file left in place: the file is a correct
    # export of the seed, so the challenge loads and its evidence reproduces. The
    # exporter is still invented, and only the exporter's own evidence can say otherwise.
    base = tmp_path / "evidence"
    plain = conformance.new("spt-import", product="Viewer", version="7", seed="mine")
    folder = plain.write(base / "b")
    held = plain.files["challenge.xml"]
    source = {
        "product": {"name": "gINT", "version": "10"},
        "export_sha256": hashlib.sha256(held).hexdigest(),
        "evidence_sha256": "0" * 64,
        "suite_version": conformance.SUITE_VERSION,
    }
    _claim(folder, source, held)
    (folder / "answers.csv").write_bytes(_answers(plain))
    result = conformance.grade(folder, folder / "answers.csv")
    assert result.passed
    (folder / "evidence.json").write_text(result.to_json(), encoding="utf-8")
    found = conformance.matrix(base)
    assert not found.unverified and found.round_trips() == ()
    assert found.entries[0].reason == _matrix.UNBOUND
    assert "read" not in {r.status for r in found.round_trips()}


def test_another_products_export_of_other_data_is_refused(tmp_path: Path) -> None:
    # Found in review: an ordinary cpt-import challenge of an unrelated seed, with a
    # hand-added source_export carrying another product's real evidence and export
    # digests and that product's export copied in as challenge.xml, gave `read`, although
    # the answers graded were for data that file does not hold.
    base = tmp_path / "evidence"
    theirs = _export_folder(base / "a", "cpt")
    export = (theirs / "export.xml").read_bytes()
    plain = conformance.new("cpt-import", product="Viewer", version="7", seed="unrelated")
    folder = plain.write(base / "b")
    source = {
        "product": {"name": "LogA", "version": "2.0"},
        "export_sha256": hashlib.sha256(export).hexdigest(),
        "evidence_sha256": hashlib.sha256((theirs / "evidence.json").read_bytes()).hexdigest(),
        "suite_version": conformance.SUITE_VERSION,
    }
    _claim(folder, source, export)
    (folder / "answers.csv").write_bytes(_answers(plain))
    with pytest.raises(DocumentError, match="seed unrelated") as caught:
        conformance.grade(folder, folder / "answers.csv")
    assert caught.value.kind == "source-export"
    faked = conformance.grade(plain, _answers(plain)).to_dict() | {"source_export": source}
    faked["submission"]["name"] = "answers.csv"
    (folder / "evidence.json").write_text(json.dumps(faked), encoding="utf-8")
    found = conformance.matrix(base)
    assert [e.path for e in found.unverified] == ["b/evidence.json"]
    assert found.round_trips() == ()
    assert found.cell(("LogA", "2.0"), "cpt-export") == "pass"
    # The legitimate way, from the same export, still says read.
    _import_folder(base / "c", theirs)
    (trip,) = conformance.matrix(base).round_trips()
    assert (trip.status, trip.evidence) == ("read", "c/evidence.json")


def test_a_suite_without_pairwise_challenges_cannot_name_a_source(tmp_path: Path) -> None:
    info = conformance.class_info("spt-import")
    old = _challenge._make(info, "Viewer", "7", "demo", "1.0-beta.2")
    folder = old.write(tmp_path / "c")
    held = old.files["challenge.xml"]
    source = conformance.SourceExport(
        "LogA", "2.0", hashlib.sha256(held).hexdigest(), "0" * 64, "1.0-beta.2"
    )
    _rewrite(folder / "challenge.json", lambda d: d.update(source_export=source.to_dict()))
    with pytest.raises(DocumentError, match=r"suite 1\.0-beta\.2 has no challenge made from") as e:
        conformance.load(folder)
    assert e.value.kind == "not-a-challenge"


def test_the_callers_limits_apply_to_the_export_of_a_pairwise_challenge(
    exporter: Path, importer: Path, tmp_path: Path
) -> None:
    # Found in review: load read challenge.xml with a fixed 100 MiB limit, so a challenge
    # made with a larger --max-bytes could never be graded, and matrix(max_bytes=...) did
    # not bound that file.
    size = (importer / "challenge.xml").stat().st_size
    assert size > (importer / "answers.csv").stat().st_size
    with pytest.raises(DocumentError, match=f"larger than {size - 1} bytes") as caught:
        conformance.load(importer, max_bytes=size - 1)
    assert caught.value.kind == "source-export"
    assert conformance.load(importer, max_bytes=size).source_export is not None
    with pytest.raises(DocumentError, match="larger than"):
        conformance.grade(importer, importer / "answers.csv", max_bytes=size - 1)
    base = tmp_path / "evidence"
    _copy(exporter, base / "a")
    _copy(importer, base / "b")
    found = conformance.matrix(base, max_bytes=size - 1)
    assert "larger than" in {e.path: e.reason for e in found.unverified}["b/evidence.json"]
    assert conformance.matrix(base, max_bytes=size).round_trips()[0].status == "read"
    # A limit above the default is honoured from new through to grade.
    over = 200 * 1024 * 1024
    pair = conformance.new(
        "spt-import",
        product="Viewer",
        version="7",
        from_export=exporter / "evidence.json",
        max_bytes=over,
    )
    folder = pair.write(tmp_path / "over")
    assert conformance.grade(folder, _answers(pair), max_bytes=over).passed
    with pytest.raises(DocumentError, match="fails readable"):
        conformance.load(importer, time_budget=1e-9)


def test_bytes_like_inputs_are_read_and_other_things_are_a_type_error(exporter: Path) -> None:
    evidence = (exporter / "evidence.json").read_bytes()
    export = (exporter / "export.xml").read_bytes()
    made = conformance.new(
        "spt-import",
        product="V",
        version="7",
        from_export=bytearray(evidence),
        export=memoryview(export),
    )
    assert made.files["challenge.xml"] == export
    with pytest.raises(ValueError, match="give the export file"):
        conformance.new("spt-import", product="V", version="7", from_export=memoryview(evidence))
    for bad in (7, ["evidence.json"]):
        with pytest.raises(TypeError):
            conformance.new("spt-import", product="V", version="7", from_export=bad, export=export)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            conformance.new(
                "spt-import", product="V", version="7", from_export=evidence, export=bad
            )  # type: ignore[arg-type]


def test_the_schema_allows_a_source_only_on_import_evidence(exporter: Path, importer: Path) -> None:
    validator = jsonschema.Draft202012Validator(conformance.conformance_schema())
    ours = json.loads((importer / "evidence.json").read_text("utf-8"))
    theirs = json.loads((exporter / "evidence.json").read_text("utf-8"))
    assert validator.is_valid(ours) and validator.is_valid(theirs)
    assert not validator.is_valid({**theirs, "source_export": ours["source_export"]})
    long = {**ours["source_export"], "suite_version": "x" * 201}
    assert not validator.is_valid({**ours, "source_export": long})
    assert not validator.is_valid({**ours, "source_export": {**long, "suite_version": ""}})
    with pytest.raises(ValueError, match="suite_version"):
        conformance.SourceExport.from_dict(long)
    edge = {**ours["source_export"], "suite_version": "x" * 200}
    assert validator.is_valid({**ours, "source_export": edge})
    assert conformance.SourceExport.from_dict(edge).suite_version == "x" * 200


# --- the command ----------------------------------------------------------------------


def test_the_command_makes_grades_and_lays_out_a_pair(
    exporter: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "evidence"
    theirs = _copy(exporter, base / "a")
    new = ["conformance", "new", "spt-import", "--product", "Viewer", "--product-version", "7"]
    code = cli.main([*new, "--from-export", str(theirs / "evidence.json"), "-o", str(base / "b")])
    out = capsys.readouterr().out
    assert code == 0
    digest = hashlib.sha256((theirs / "export.xml").read_bytes()).hexdigest()
    assert "the export of LogA 2.0 (as declared)" in out and digest in out
    assert (base / "b" / "challenge.xml").read_bytes() == (theirs / "export.xml").read_bytes()
    answers = base / "b" / "answers.csv"
    answers.write_bytes(_answers(conformance.load(base / "b")))
    assert cli.main(["conformance", "grade", str(base / "b"), str(answers)]) == 0
    assert "file given  exported by LogA 2.0" in capsys.readouterr().out
    verify = ["conformance", "verify", str(base / "b" / "evidence.json"), str(base / "b")]
    assert cli.main([*verify, str(answers)]) == 0
    capsys.readouterr()
    assert cli.main(["conformance", "matrix", str(base)]) == 0
    assert f"spt: LogA 2.0 -> Viewer 7: read (export sha256 {digest})" in capsys.readouterr().out
    # --export names the file when it is not beside the evidence.
    moved = tmp_path / "moved.xml"
    moved.write_bytes((theirs / "export.xml").read_bytes())
    (theirs / "export.xml").unlink()
    again = [*new, "--from-export", str(theirs / "evidence.json")]
    assert cli.main([*again, "-o", str(tmp_path / "c")]) == 2
    assert "cannot read the export" in capsys.readouterr().err
    assert cli.main([*again, "--export", str(moved), "-o", str(tmp_path / "c")]) == 0


def test_the_command_refuses_with_a_message_and_exit_2(
    exporter: Path, importer: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = str(exporter / "evidence.json")
    new = ["conformance", "new", "--product", "Viewer", "--product-version", "7"]
    out = ["-o", str(tmp_path / "never")]
    tampered = _copy(exporter, tmp_path / "tampered")
    _rewrite(tampered / "evidence.json", lambda d: d["submission"].update(bytes=1))
    other = tmp_path / "other.xml"
    other.write_bytes(b"<Diggs/>")
    cases = [
        ([*new, "cpt-import", "--from-export", evidence], "another family"),
        ([*new, "spt-export", "--from-export", evidence], "only for an import class"),
        ([*new, "spt-import", "--from-export", str(importer / "evidence.json")], "import class"),
        ([*new, "spt-import", "--from-export", evidence, "--export", str(other)], "not the one"),
        ([*new, "spt-import", "--from-export", str(tampered / "evidence.json")], "does not verify"),
        ([*new, "spt-import", "--from-export", str(tmp_path / "none.json")], "cannot read"),
        ([*new, "spt-import", "--from-export", evidence, "--seed", "x"], "seed"),
        ([*new, "spt-import", "--export", str(other)], "--from-export"),
        ([*new, "spt-import", "--from-export", evidence, "--max-bytes", "10"], "larger than 10"),
    ]
    for argv, message in cases:
        assert cli.main([*argv, *out]) == 2, argv
        err = capsys.readouterr().err
        assert message in err and "Traceback" not in err, (argv, err)
    assert not (tmp_path / "never").exists()  # nothing is written for a refused challenge
    with pytest.raises(SystemExit):
        cli.main([*new, "spt-import", "--from-export", evidence, "--max-bytes", "0", *out])


@links
def test_the_command_refuses_a_link(
    exporter: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    link = tmp_path / "evidence.json"
    link.symlink_to(exporter / "evidence.json")
    new = ["conformance", "new", "spt-import", "--product", "V", "--product-version", "7"]
    assert cli.main([*new, "--from-export", str(link), "-o", str(tmp_path / "never")]) == 2
    assert "is a link" in capsys.readouterr().err


# --- every family, and the docstrings ---------------------------------------------------


@pytest.mark.parametrize("family", sorted({c.family for c in conformance.classes()} - {"spt"}))
def test_every_family_has_a_pairwise_import_challenge(family: str, tmp_path: Path) -> None:
    theirs = _export_folder(tmp_path / "a", family)
    ours = _import_folder(tmp_path / "b", theirs)
    assert (ours / "challenge.xml").read_bytes() == (theirs / "export.xml").read_bytes()
    (trip,) = conformance.matrix(tmp_path).round_trips()
    assert (trip.family, trip.status) == (family, "read")


def test_docstring_examples_run() -> None:
    for module in (_challenge, _pairwise):
        failed, _attempted = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
        assert failed == 0
    assert doctest.testmod(_challenge).attempted
