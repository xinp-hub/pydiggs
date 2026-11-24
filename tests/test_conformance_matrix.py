"""``pydiggs conformance matrix``: verified evidence of several products, class by class."""

from __future__ import annotations

import doctest
import json
import sys
from pathlib import Path

import jsonschema
import pytest

from pydiggs import cli, conformance
from pydiggs.conformance import _accept, _challenge, _matrix, _registry

HOSTILE = "<script>alert(1)</script>"


def _graded(folder: Path, class_id: str, product: str, *, right: bool = True) -> Path:
    """A challenge folder with a submission and the evidence ``grade`` writes for it."""
    challenge = conformance.new(class_id, product=product, version="1", seed=f"s{len(product)}")
    target = challenge.write(folder)
    if challenge.info.direction == "export":
        submission = target / "export.xml"
        submission.write_bytes(challenge.reference())
    else:
        stream = _challenge._stream(challenge.info, challenge.seed)
        asked = _registry.questions(challenge.dataset(), stream)
        lines = ["question_id,answer"]
        lines += [f'{q.id},"{q.expected if right else "0"}"' for q in asked]
        submission = target / "answers.csv"
        submission.write_text("\n".join(lines) + "\n", encoding="utf-8")
    evidence = conformance.grade(target, submission).to_json()
    (target / "evidence.json").write_text(evidence, encoding="utf-8")
    return target


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("matrix")
    _graded(base / "a-spt-export", "spt-export", "Alpha")
    _graded(base / "b-spt-import", "spt-import", "Bravo")
    _graded(base / "b-cpt-import", "cpt-import", "Bravo", right=False)
    return base


def _copy(src: Path, dst: Path) -> Path:
    dst.mkdir(parents=True)
    for path in src.iterdir():
        (dst / path.name).write_bytes(path.read_bytes())
    return dst


def test_verified_evidence_is_counted_class_by_class(corpus: Path) -> None:
    found = conformance.matrix(corpus)
    assert not found.unverified
    assert found.products == (("Alpha", "1"), ("Bravo", "1"))
    assert found.cell(("Alpha", "1"), "spt-export") == "pass"
    assert found.cell(("Bravo", "1"), "spt-import") == "pass"
    assert found.cell(("Bravo", "1"), "cpt-import") == "fail"
    assert found.cell(("Alpha", "1"), "cpt-export") == "not-run"


def test_a_round_trip_pair_is_listed_and_never_claimed_as_read(corpus: Path) -> None:
    (trip,) = conformance.matrix(corpus).round_trips()
    assert (trip.family, trip.exporter, trip.importer) == ("spt", ("Alpha", "1"), ("Bravo", "1"))
    assert trip.status == "not-tested-together"


def test_tampered_evidence_is_shown_and_not_counted(corpus: Path, tmp_path: Path) -> None:
    folder = _copy(corpus / "b-cpt-import", tmp_path / "evidence" / "tampered")
    path = folder / "evidence.json"
    document = json.loads(path.read_text("utf-8"))
    document["verdict"] = "pass"
    path.write_text(json.dumps(document), encoding="utf-8")
    found = conformance.matrix(tmp_path / "evidence")
    (entry,) = found.unverified
    assert entry.path == "tampered/evidence.json"
    assert "verdict" in entry.reason
    assert found.products == ()
    assert found.to_dict()["products"] == []


def test_evidence_without_its_submission_is_not_counted(corpus: Path, tmp_path: Path) -> None:
    folder = _copy(corpus / "a-spt-export", tmp_path / "e" / "moved")
    (folder / "export.xml").unlink()
    (entry,) = conformance.matrix(tmp_path / "e").unverified
    assert "not beside it" in entry.reason
    assert entry.product_name == "Alpha"


def test_a_submission_name_with_a_path_is_never_followed(corpus: Path, tmp_path: Path) -> None:
    folder = _copy(corpus / "a-spt-export", tmp_path / "e" / "escape")
    path = folder / "evidence.json"
    document = json.loads(path.read_text("utf-8"))
    document["submission"]["name"] = "../../a-spt-export/export.xml"
    path.write_text(json.dumps(document), encoding="utf-8")
    (entry,) = conformance.matrix(tmp_path / "e").unverified
    assert "not beside it" in entry.reason


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"{not json", "not JSON"),
        (b'{"format": "diggs-findings"}', "not a diggs-conformance document"),
        (b'{"format": "diggs-conformance", "product": 1}', "malformed"),
        (b"[" * 100_000, "not JSON"),
    ],
)
def test_unreadable_evidence_is_listed_with_its_reason(
    tmp_path: Path, body: bytes, reason: str
) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "evidence.json").write_bytes(body)
    (entry,) = conformance.matrix(tmp_path).unverified
    assert reason in entry.reason


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_evidence_that_is_a_link_is_listed_and_never_read(corpus: Path, tmp_path: Path) -> None:
    # Found in review: a symlinked evidence.json was skipped without a word, so a folder
    # could look fully verified while one of its documents was never looked at.
    real = _copy(corpus / "a-spt-export", tmp_path / "outside" / "real")
    linked = tmp_path / "evidence" / "linked"
    linked.mkdir(parents=True)
    for path in real.iterdir():
        if path.name != "evidence.json":
            (linked / path.name).write_bytes(path.read_bytes())
    (linked / "evidence.json").symlink_to(real / "evidence.json")
    found = conformance.matrix(tmp_path / "evidence")
    (entry,) = found.unverified
    assert (entry.path, entry.reason) == ("linked/evidence.json", "a link, not read")
    assert not entry.verified and entry.product_name == ""  # nothing was read from it
    assert not found.products
    assert "a link, not read" in found.to_text()
    assert cli.main(["conformance", "matrix", str(tmp_path / "evidence")]) == 1


def test_the_types_public_results_return_are_public() -> None:
    assert conformance.Entry is _matrix.Entry
    assert conformance.RoundTrip is _matrix.RoundTrip
    assert conformance.Outcome is _accept.Outcome
    reference = (Path(__file__).parent.parent / "docs" / "reference.md").read_text("utf-8")
    for name in ("Entry", "RoundTrip", "Outcome"):
        assert name in conformance.__all__
        assert f"::: pydiggs.conformance.{name}\n" in reference


def test_not_a_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a folder"):
        conformance.matrix(tmp_path / "none")


def test_reports_escape_product_names(tmp_path: Path) -> None:
    _graded(tmp_path / "evil", "spt-export", HOSTILE)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "evidence.json").write_text("{", encoding="utf-8")
    found = conformance.matrix(tmp_path)
    page = found.to_html()
    assert HOSTILE not in page
    assert "&lt;script&gt;" in page
    assert "Not verified, not counted" in page
    markdown = found.to_markdown()
    assert HOSTILE not in markdown and "\\<script\\>" in markdown
    assert json.loads(found.to_json())["products"][0]["name"] == HOSTILE


def test_the_document_has_a_format_and_says_what_it_cannot_show(corpus: Path) -> None:
    document = conformance.matrix(corpus).to_dict()
    assert document["format"] == "diggs-conformance-matrix"
    assert document["tier"] == "self-tested"
    assert "No evidence yet records" in document["round_trip_note"]
    assert document["classes"][:2] == ["spt-export", "spt-import"]
    assert [e["verified"] for e in document["evidence"]] == [True, True, True]


def test_cli_exit_codes(corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    grid = ["conformance", "matrix"]
    assert cli.main([*grid, str(corpus)]) == 0
    assert "Alpha 1" in capsys.readouterr().out
    for fmt in ("json", "markdown", "html"):
        assert cli.main([*grid, str(corpus), "--format", fmt]) == 0
    capsys.readouterr()
    _copy(corpus / "a-spt-export", tmp_path / "mixed" / "good")
    (tmp_path / "mixed" / "bad").mkdir()
    (tmp_path / "mixed" / "bad" / "evidence.json").write_text("{", encoding="utf-8")
    assert cli.main([*grid, str(tmp_path / "mixed"), "--format", "json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in document["products"]] == ["Alpha"]
    (tmp_path / "empty").mkdir()
    assert cli.main([*grid, str(tmp_path / "empty")]) == 2
    assert "no evidence.json" in capsys.readouterr().err
    assert cli.main([*grid, str(tmp_path / "none")]) == 2


def test_docstring_examples_run() -> None:
    failed, attempted = doctest.testmod(_matrix, optionflags=doctest.ELLIPSIS)
    assert attempted and failed == 0


def test_every_document_matrix_writes_matches_its_schema(corpus: Path, tmp_path: Path) -> None:
    validator = jsonschema.Draft202012Validator(conformance.conformance_schema("matrix"))
    tampered = _copy(corpus / "a-spt-export", tmp_path / "mixed" / "tampered")
    path = tampered / "evidence.json"
    document = json.loads(path.read_text("utf-8"))
    document["verdict"] = "fail"
    path.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "mixed" / "junk").mkdir()
    (tmp_path / "mixed" / "junk" / "evidence.json").write_text("[]", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    for found in (
        conformance.matrix(corpus),  # verified pass, fail and a round trip
        conformance.matrix(tmp_path / "mixed"),  # only evidence that does not count
        conformance.matrix(tmp_path / "empty"),  # nothing at all
    ):
        written = json.loads(found.to_json())
        validator.validate(written)
        assert written["$schema"] == conformance.MATRIX_SCHEMA_URL
    assert len(conformance.matrix(tmp_path / "mixed").unverified) == 2


def test_the_matrix_schema_is_in_step_with_the_code(corpus: Path) -> None:
    schema = conformance.conformance_schema("matrix")
    assert schema["$id"] == conformance.MATRIX_SCHEMA_URL
    assert schema["$id"].endswith(
        f"/schemas/preview/diggs-conformance-matrix-{conformance.MATRIX_SCHEMA_VERSION}.schema.json"
    )
    assert schema["properties"]["schema_version"]["const"] == conformance.MATRIX_SCHEMA_VERSION
    found = conformance.matrix(corpus)
    document = found.to_dict()
    assert set(schema["required"]) == set(schema["properties"]) == set(document)
    props = schema["properties"]
    assert set(props["evidence"]["items"]["required"]) == set(document["evidence"][0])
    assert set(props["round_trips"]["items"]["required"]) == set(document["round_trips"][0])
    statuses = props["round_trips"]["items"]["properties"]["status"]["enum"]
    assert found.round_trips()[0].status in statuses
    cells = props["products"]["items"]["properties"]["classes"]["additionalProperties"]["enum"]
    assert set(cells) == {"pass", "fail", "not-run"}
