"""The conformance suite: catalogue, challenges, grading, evidence and the command."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import cli, conformance
from pydiggs.conformance import _challenge, _facts, _rng
from pydiggs.errors import DocumentError, InstanceReadError

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "src" / "pydiggs" / "_writer" / "layout-3.0.0.toml"
EXPORTS = ("spt-export", "cpt-export")
IMPORTS = ("spt-import", "cpt-import")
SEEDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12)


def _challenge_for(class_id: str, seed: str = "demo") -> conformance.Challenge:
    return conformance.new(class_id, product="Demo Log", version="1.2.3", seed=seed)


def _reference(challenge: conformance.Challenge) -> bytes:
    return challenge.reference()


def _right_answers(challenge: conformance.Challenge) -> dict[str, str]:
    stream = _challenge._stream(challenge.info, challenge.seed)
    return {q.id: q.expected for q in _facts.questions(challenge.dataset(), stream)}


def _answers_csv(answers: dict[str, str]) -> bytes:
    lines = ["question_id,question,unit,answer"]
    lines += [f'{key},,,"{value}"' for key, value in answers.items()]
    return "\r\n".join(lines).encode()


# --- catalogue ------------------------------------------------------------------------


def test_catalogue_names_only_real_elements_and_each_once() -> None:
    elements = set(tomllib.loads(LAYOUT.read_text("utf-8"))["elements"])
    named = [e for family in conformance.families() for e in family.elements]
    assert not [e for e in named if e not in elements]
    assert len(named) == len(set(named))


def test_every_test_procedure_of_the_schema_is_in_a_family() -> None:
    elements = set(tomllib.loads(LAYOUT.read_text("utf-8"))["elements"])
    procedures = {e for e in elements if e.endswith("Test") and e != "Test"}
    named = {e for family in conformance.families() for e in family.elements}
    assert procedures - named == set()


def test_classes_are_the_testable_families_in_both_directions() -> None:
    assert [c.id for c in conformance.classes()] == [
        *EXPORTS[:1],
        IMPORTS[0],
        EXPORTS[1],
        IMPORTS[1],
    ]
    assert all(c.diggs_version == "3.0.0" and c.level == "core" for c in conformance.classes())
    with pytest.raises(ValueError, match="spt-export"):
        conformance.class_info("pile-export")


# --- challenges -----------------------------------------------------------------------


def test_rng_is_the_same_everywhere() -> None:
    # Golden values: a change here changes every challenge, so SUITE_VERSION must change.
    rng = _rng.Rng("golden")
    assert [rng.integer(0, 999) for _ in range(6)] == [397, 30, 943, 82, 355, 357]


@pytest.mark.parametrize("class_id", [*EXPORTS, *IMPORTS])
def test_a_seed_always_gives_the_same_files(class_id: str) -> None:
    first, second = _challenge_for(class_id), _challenge_for(class_id)
    assert first.files == second.files
    assert first.files != _challenge_for(class_id, "other").files
    manifest = first.manifest
    assert manifest["suite_version"] == conformance.SUITE_VERSION
    assert first.token in first.dataset().table("project").rows[0][1]
    for name, digest in manifest["files"].items():
        assert _challenge.hashlib.sha256(first.files[name]).hexdigest() == digest


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    challenge = _challenge_for("spt-export")
    folder = challenge.write(tmp_path / "c")
    assert conformance.load(folder).files == challenge.files
    assert conformance.load(folder / "challenge.json").sha256 == challenge.sha256
    with pytest.raises(FileExistsError):
        challenge.write(folder)


def test_load_rebuilds_from_the_seed_not_from_edited_tables(tmp_path: Path) -> None:
    challenge = _challenge_for("spt-export")
    folder = challenge.write(tmp_path / "c")
    (folder / "spt.csv").write_text("location_id\r\nedited\r\n")
    assert conformance.load(folder).files["spt.csv"] == challenge.files["spt.csv"]


def test_load_refuses_another_suite_and_a_non_manifest(tmp_path: Path) -> None:
    folder = _challenge_for("spt-export").write(tmp_path / "c")
    manifest = json.loads((folder / "challenge.json").read_text())
    manifest["suite_version"] = "0.9"
    (folder / "challenge.json").write_text(json.dumps(manifest))
    with pytest.raises(DocumentError) as caught:
        conformance.load(folder)
    assert caught.value.kind == "suite-version"
    (folder / "challenge.json").write_text("[1, 2]")
    with pytest.raises(DocumentError) as caught:
        conformance.load(folder)
    assert caught.value.kind == "not-a-challenge"
    with pytest.raises(DocumentError):
        conformance.load(tmp_path / "missing")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"product": " ", "version": "1"}, "product"),
        ({"product": "x", "version": "v" * 201}, "version"),
        ({"product": "x", "version": "1", "seed": "no spaces"}, "seed"),
    ],
)
def test_new_refuses_bad_arguments(kwargs: dict[str, str], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        conformance.new("spt-export", **kwargs)


# --- export grading -------------------------------------------------------------------


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(class_id=st.sampled_from(EXPORTS), seed=SEEDS)
def test_the_reference_file_of_any_seed_passes_its_own_challenge(class_id: str, seed: str) -> None:
    # The answer key comes from the tables, the file from the converter and the reader:
    # agreement on every seed is the suite checking pyDIGGS itself.
    challenge = _challenge_for(class_id, seed)
    result = conformance.grade(challenge, _reference(challenge))
    assert result.passed, result.to_text()
    assert all(stage.failed == 0 and not stage.items for stage in result.stages)


def test_a_changed_value_is_named() -> None:
    challenge = _challenge_for("cpt-export")
    row = challenge.dataset().table("cpt_data").rows[3]
    xml = _reference(challenge).decode()
    changed = xml.replace(
        f"{row[3]} {row[4]} {row[5]}", f"{float(row[3]) + 1:.2f} {row[4]} {row[5]}", 1
    )
    if changed == xml:  # the data block separates values differently
        changed = re.sub(rf"\b{re.escape(row[3])}\b", f"{float(row[3]) + 1:.2f}", xml, count=1)
    assert changed != xml
    result = conformance.grade(challenge, changed.encode())
    assert not result.passed
    fidelity = result.stage("fidelity")
    assert fidelity.status == "fail" and fidelity.failed >= 1
    assert fidelity.items[0].check == "conformance.value-differs"
    assert "tip_resistance" in fidelity.items[0].id
    assert result.stage("core").status == "pass"


def test_a_renamed_location_and_project_fail_core() -> None:
    challenge = _challenge_for("spt-export")
    name = challenge.dataset().table("locations").rows[0][0]
    xml = _reference(challenge).decode()
    changed = xml.replace(f">{name}<", f">{name.lower()}x<").replace(
        "&amp; Approach", "and Approach"
    )
    result = conformance.grade(challenge, changed.encode())
    checks = {item.check for item in result.stage("core").items}
    assert checks == {"conformance.fact-missing", "conformance.project-name"}
    assert not result.passed


def test_a_value_in_another_declared_unit_still_passes() -> None:
    challenge = _challenge_for("spt-export")
    xml = _reference(challenge).decode()
    unit = challenge.dataset().table("spt").fields[3].unit
    factor = {"mm": 0.001, "in": 0.0254}[unit]

    def metres(match: re.Match[str]) -> str:
        return f'<totalPenetration uom="m">{float(match.group(1)) * factor:.4f}<'

    changed = re.sub(rf'<totalPenetration uom="{unit}">([0-9.]+)<', metres, xml)
    assert changed != xml
    assert conformance.grade(challenge, changed.encode()).passed


def test_a_file_that_is_not_diggs_fails_readable_and_skips_the_rest() -> None:
    result = conformance.grade(_challenge_for("spt-export"), b"<root/>")
    assert [s.status for s in result.stages] == ["fail", "skipped", "skipped", "skipped"]
    assert result.stage("readable").items[0].check == "conformance.not-diggs"
    assert not result.passed


def test_an_older_diggs_version_fails_readable() -> None:
    old = next((ROOT / "tests" / "fixtures" / "official" / "2.6").glob("*.xml"))
    result = conformance.grade(_challenge_for("spt-export"), old)
    assert result.stage("readable").items[0].check == "conformance.diggs-version"
    assert not result.passed


def test_an_invalid_file_fails_validation_with_the_rule_ids() -> None:
    challenge = _challenge_for("spt-export")
    xml = _reference(challenge).decode().replace('uom="%"', 'uom="furlong"', 1)
    result = conformance.grade(challenge, xml.encode())
    validation = result.stage("validation")
    assert validation.status == "fail"
    assert validation.items[0].check.startswith("schema.")


def test_a_missing_or_oversized_submission_is_an_error(tmp_path: Path) -> None:
    challenge = _challenge_for("spt-export")
    with pytest.raises(InstanceReadError):
        conformance.grade(challenge, tmp_path / "none.xml")
    with pytest.raises(InstanceReadError):
        conformance.grade(challenge, b"x" * 20, max_bytes=10)


# --- import grading -------------------------------------------------------------------


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(class_id=st.sampled_from(IMPORTS), seed=SEEDS)
def test_the_right_answers_pass_and_the_defect_is_one_pydiggs_reports(
    class_id: str, seed: str
) -> None:
    challenge = _challenge_for(class_id, seed)
    answers = _right_answers(challenge)
    result = conformance.grade(challenge, _answers_csv(answers))
    assert result.passed, result.to_text()
    assert result.stage("robustness").status == "pass"
    expected_rule = {
        "broken-reference": "context.xlink-href-resolves",
        "unknown-property": "dictionary.",
        "invalid-unit": "schema.",
    }[answers["robustness.defect"]]
    report = pydiggs.validate_bytes(challenge.files["defective.xml"])
    assert any(f.rule_id.startswith(expected_rule) for f in report.findings)
    clean = pydiggs.validate_bytes(challenge.files["challenge.xml"])
    assert not any(f.rule_id.startswith(expected_rule) for f in clean.findings)


def test_answers_tolerate_a_unit_and_rounding_but_not_a_wrong_value() -> None:
    challenge = _challenge_for("spt-import")
    answers = _right_answers(challenge)
    asked = {
        q.id: q
        for q in _facts.questions(
            challenge.dataset(), _challenge._stream(challenge.info, challenge.seed)
        )
    }
    depth = asked["core.total-depth"]
    answers["core.total-depth"] = f"{float(depth.expected) * 1.004:.3f} {depth.unit}"
    answers["core.project-name"] = "  " + answers["core.project-name"].upper()
    assert conformance.grade(challenge, _answers_csv(answers)).passed
    for unreadable in ("9,6", "sNaN", "inf", "about ten"):
        wrong = {**answers, "core.total-depth": unreadable}
        assert not conformance.grade(challenge, json.dumps(wrong).encode()).passed
    answers["values.n-value"] = str(int(answers["values.n-value"]) + 1)
    result = conformance.grade(challenge, _answers_csv(answers))
    assert not result.passed
    assert [i.check for i in result.stage("values").items] == ["conformance.answer-differs"]


def test_a_wrong_or_missing_defect_answer_is_advisory() -> None:
    challenge = _challenge_for("cpt-import")
    answers = _right_answers(challenge)
    answers["robustness.defect"] = "none"
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert result.passed
    robustness = result.stage("robustness")
    assert (robustness.status, robustness.required) == ("fail", False)
    del answers["robustness.defect"], answers["values.readings"]
    result = conformance.grade(challenge, json.dumps(answers).encode())
    assert not result.passed
    assert result.stage("values").items[0].check == "conformance.answer-missing"


def test_the_untouched_template_fails_and_other_files_are_refused() -> None:
    challenge = _challenge_for("spt-import")
    assert not conformance.grade(challenge, challenge.files["answers.csv"]).passed
    huge = b"question_id,answer\r\nx," + b"9" * 200_000 + b"\r\n"
    nested = b"[" * 100_000
    for bad in (b"a,b\r\n1,2\r\n", b"\xff\xfe\x00", b"{not json", huge, b"{" + nested):
        with pytest.raises(DocumentError) as caught:
            conformance.grade(challenge, bad)
        assert caught.value.kind == "not-answers"


# --- evidence -------------------------------------------------------------------------


@pytest.mark.parametrize("class_id", [*EXPORTS, *IMPORTS])
def test_evidence_matches_its_schema_and_is_reproducible(class_id: str) -> None:
    challenge = _challenge_for(class_id)
    good = _reference(challenge) if class_id in EXPORTS else _answers_csv(_right_answers(challenge))
    schema = conformance.conformance_schema()
    for submission in (good, b"<root/>" if class_id in EXPORTS else challenge.files["answers.csv"]):
        result = conformance.grade(challenge, submission)
        document = json.loads(result.to_json())
        jsonschema.Draft202012Validator(schema).validate(document)
        assert document == conformance.grade(challenge, submission).to_dict()
        assert document["tier"] == "self-tested" and "not a certification" in document["statement"]
        assert conformance.verify(document, challenge, submission).ok


def test_verify_names_what_differs(tmp_path: Path) -> None:
    challenge = _challenge_for("spt-export")
    xml = _reference(challenge)
    evidence = conformance.grade(challenge, xml).to_dict()
    evidence["tool"]["version"] = "9.9.9"
    assert conformance.verify(evidence, challenge, xml).ok
    evidence["product"]["name"] = "Another"
    other = xml.replace(b"Approach", b"Approach ")
    outcome = conformance.verify(evidence, challenge, other)
    assert not outcome.ok
    assert set(outcome.differences) >= {"product", "submission", "verdict"}
    path = tmp_path / "e.json"
    path.write_text("{}")
    with pytest.raises(DocumentError) as caught:
        conformance.verify(path, challenge, xml)
    assert caught.value.kind == "not-evidence"


def test_reports_escape_what_comes_from_files() -> None:
    challenge = conformance.new(
        "spt-export", product="<script>x</script>", version="1\x1b[31m", seed="demo"
    )
    result = conformance.grade(challenge, b"<root/>")
    page = result.to_html()
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "\x1b" not in result.to_text()


def test_scorecard_shows_every_family() -> None:
    documents = []
    for class_id in ("spt-export", "cpt-export"):
        challenge = _challenge_for(class_id)
        submission = _reference(challenge) if class_id == "spt-export" else b"<root/>"
        documents.append(conformance.grade(challenge, submission).to_dict())
    (card,) = conformance.scorecard(documents)
    rows = {row.family: row for row in card.rows}
    assert len(rows) == len(conformance.families())
    assert (rows["spt"].export, rows["spt"].import_) == ("pass", "not-run")
    assert rows["cpt"].export == "fail"
    assert rows["core"].export == "fail" and rows["core"].import_ == "not-run"
    assert rows["deep-foundations"].export == "not-testable"
    assert (card.passed, card.testable) == (1, 4)
    assert card.untestable == 2 * (len(rows) - 3)
    assert "not yet testable" in card.to_text() and "| Export |" in card.to_markdown()
    assert card.to_dict()["classes_passed"] == 1
    with pytest.raises(ValueError, match="suite"):
        conformance.scorecard([{**documents[0], "suite_version": "0.1"}])


# --- command --------------------------------------------------------------------------


def test_the_command_runs_the_whole_journey(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "spt"
    new = ["conformance", "new", "spt-export", "--product", "Demo", "--product-version", "1", "-o"]
    assert cli.main([*new, str(folder), "--seed", "demo"]) == 0
    assert "token" in capsys.readouterr().out
    assert cli.main([*new, str(folder)]) == 2
    assert "already holds a challenge" in capsys.readouterr().err

    good = tmp_path / "export.xml"
    good.write_bytes(_reference(conformance.load(folder)))
    assert cli.main(["conformance", "grade", str(folder), str(good)]) == 0
    out = capsys.readouterr().out
    assert "spt-export: PASS" in out and "evidence.json" in out
    evidence = folder / "evidence.json"
    assert json.loads(evidence.read_text())["verdict"] == "pass"
    assert "<!doctype html>" in (folder / "report.html").read_text()
    assert cli.main(["conformance", "verify", str(evidence), str(folder), str(good)]) == 0
    assert "Reproduced" in capsys.readouterr().out

    bad = tmp_path / "bad.xml"
    bad.write_bytes(good.read_bytes().replace(b"&amp; Approach", b"and Approach"))
    assert (
        cli.main(["conformance", "grade", str(folder), str(bad), "--no-save", "--format", "json"])
        == 1
    )
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"
    assert cli.main(["conformance", "verify", str(evidence), str(folder), str(bad)]) == 1
    assert "Not reproduced" in capsys.readouterr().out

    assert cli.main(["conformance", "scorecard", str(evidence), "--format", "markdown"]) == 0
    assert "| Cone penetration tests | not run | not run |" in capsys.readouterr().out
    assert cli.main(["conformance", "grade", str(folder), str(tmp_path / "none.xml")]) == 2
    assert cli.main(["conformance", "scorecard", str(good)]) == 2
    capsys.readouterr()
    assert cli.main(["conformance", "classes", "--format", "json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in listing["classes"]] == [c.id for c in conformance.classes()]
    assert cli.main(["conformance", "classes"]) == 0
    assert "Piles and pile driving records" in capsys.readouterr().out


def test_importing_pydiggs_does_not_import_the_suite() -> None:
    import subprocess
    import sys

    code = "import sys, pydiggs; sys.exit('pydiggs.conformance' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", code], check=False)  # noqa: S603  # fixed arguments
    assert done.returncode == 0


# --- found by the pre-release review --------------------------------------------------


def test_every_boring_of_every_seed_has_each_trap() -> None:
    for number in range(400):
        table = _challenge_for("spt-import", f"s{number}").dataset().table("spt")
        for name in set(table.column("location_id")):
            remarks = [row[5] for row in table.rows if row[0] == name]
            assert remarks.count("Weight of hammer") == 1 and remarks.count("Refusal") == 1


def test_a_right_value_does_not_hide_a_wrong_one_at_the_same_place() -> None:
    challenge = _challenge_for("spt-export")
    xml = challenge.reference().decode()
    test = re.search(r"<measurement>\s*<Test .*?</measurement>", xml, re.S)
    assert test is not None
    twin = re.sub(r'gml:id="([^"]+)"', r'gml:id="\1-twin"', test.group(0))
    twin = re.sub(r"(<dataValues[^>]*>)\s*[0-9.]+", r"\g<1>99", twin, count=1)
    assert twin != test.group(0).replace("ISPT", "ISPT")
    doubled = xml.replace(test.group(0), test.group(0) + twin, 1)
    result = conformance.grade(challenge, doubled.encode())
    assert result.stage("validation").status == "pass", result.to_text()
    fidelity = result.stage("fidelity")
    assert fidelity.status == "fail" and fidelity.items[0].check == "conformance.value-differs"


def test_a_value_in_a_unit_of_the_wrong_kind_is_not_read_as_right() -> None:
    challenge = _challenge_for("spt-export")
    xml = challenge.reference().decode()
    unit = challenge.dataset().table("spt").fields[3].unit
    factor = {"mm": 0.001, "in": 0.0254}[unit]

    def seconds(match: re.Match[str]) -> str:
        return f'<totalPenetration uom="s">{float(match.group(1)) * factor:.4f}<'

    changed = re.sub(rf'<totalPenetration uom="{unit}">([0-9.]+)<', seconds, xml)
    fidelity = conformance.grade(challenge, changed.encode()).stage("fidelity")
    assert fidelity.status == "fail"
    assert {item.check for item in fidelity.items} == {"conformance.value-differs"}
    assert fidelity.items[0].expected.endswith(" m") and fidelity.items[0].got.endswith(" s")


def test_observing_a_file_is_linear_in_names_times_rows() -> None:
    from pydiggs.model import Model  # noqa: F401  # the reader's model is what is observed

    challenge = _challenge_for("cpt-export")
    xml = challenge.reference().decode()
    name = challenge.dataset().table("locations").rows[0][0]
    many = "".join(f"<gml:name>alias-{i}</gml:name>" for i in range(3000))
    changed = xml.replace(f"<gml:name>{name}</gml:name>", f"<gml:name>{name}</gml:name>{many}", 1)
    seen = _facts.observe(pydiggs.read_bytes(changed.encode()))
    assert len(seen.named) >= 3000
    assert sum(len(found) for found in seen.readings.values()) < 1000


def test_skipped_and_validation_stages_count_what_failed() -> None:
    challenge = _challenge_for("cpt-export")
    document = conformance.grade(challenge, b"<root/>").to_dict()
    skipped = [s for s in document["stages"] if s["status"] == "skipped"]
    assert skipped and all(s["failed"] == 0 and not s["items_truncated"] for s in skipped)
    assert "in all" not in conformance.grade(challenge, b"<root/>").to_text()
    broken = challenge.reference().replace(b'uom="m"', b'uom="furlong"')
    validation = conformance.grade(challenge, broken).stage("validation")
    assert validation.failed == len(validation.items) >= 2


def test_a_tampered_manifest_is_refused(tmp_path: Path) -> None:
    folder = _challenge_for("spt-export").write(tmp_path / "c")
    good = json.loads((folder / "challenge.json").read_text())
    for change in (
        {"seed": None},
        {"seed": "demo\n"},
        {"token": "000000"},
        {"product": {"name": 5, "version": "1"}},
        {"product": "x"},
        {"class": "pile-export"},
    ):
        (folder / "challenge.json").write_text(json.dumps({**good, **change}))
        with pytest.raises(DocumentError) as caught:
            conformance.load(folder)
        assert caught.value.kind == "not-a-challenge"
    (folder / "challenge.json").write_text("[" * 100_000)
    with pytest.raises(DocumentError):
        conformance.load(folder)


def test_write_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("mine")
    with pytest.raises(FileExistsError, match=r"README\.md"):
        _challenge_for("spt-export").write(tmp_path)
    assert (tmp_path / "README.md").read_text() == "mine"
    assert not (tmp_path / "challenge.json").exists()


def test_verify_ignores_message_wording_but_not_what_was_found() -> None:
    challenge = _challenge_for("spt-export")
    broken = challenge.reference().replace(b"&amp; Approach", b"and Approach")
    evidence = conformance.grade(challenge, broken).to_dict()
    core = next(s for s in evidence["stages"] if s["id"] == "core")
    core["items"][0]["message"] = "worded otherwise"
    assert conformance.verify(evidence, challenge, broken).ok
    core["items"][0]["got"] = "something else"
    assert conformance.verify(evidence, challenge, broken).differences == ("stages",)
    evidence["\x1b[31mextra"] = 1
    assert "unknown member" in conformance.verify(evidence, challenge, broken).differences


def test_scorecard_refuses_malformed_evidence_and_escapes_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    challenge = conformance.new(
        "spt-export", product="X | pass |\x1b[31m *b*", version="1", seed="demo"
    )
    evidence = conformance.grade(challenge, challenge.reference()).to_dict()
    (card,) = conformance.scorecard([evidence])
    first = card.to_markdown().splitlines()[0]
    assert "\\|" in first and "\\*b\\*" in first and first.count("|") == first.count("\\|")
    for change in ({"verdict": "banana"}, {"product": 3}, {"stages": {}}, {"class": {"id": 1}}):
        with pytest.raises(ValueError, match="malformed"):
            conformance.scorecard([{**evidence, **change}])
    path = tmp_path / "e.json"
    path.write_text(json.dumps(evidence))
    assert cli.main(["conformance", "scorecard", str(path), "--format", "markdown"]) == 0
    assert "\x1b" not in capsys.readouterr().out
    path.write_text(json.dumps({**evidence, "verdict": "banana"}))
    assert cli.main(["conformance", "scorecard", str(path)]) == 2
    path.write_text("[" * 100_000)
    assert cli.main(["conformance", "scorecard", str(path)]) == 2
    assert "Traceback" not in capsys.readouterr().err
