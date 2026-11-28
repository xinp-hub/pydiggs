"""``pydiggs conformance accept``: the owner's check of one delivered DIGGS file."""

from __future__ import annotations

import doctest
import hashlib
import json
import re
from pathlib import Path

import jsonschema
import pytest
from lxml import etree

from pydiggs import cli, conformance
from pydiggs.conformance import _accept, _mwd, _requirements
from pydiggs.errors import InstanceReadError

#: Every family with a scenario, in catalogue order.
FAMILIES = {
    "spt": "spt-export",
    "cpt": "cpt-export",
    "dissipation": "dissipation-export",
    "in-situ-strength": "in-situ-strength-export",
    "pressuremeter": "pressuremeter-export",
    "mwd": "mwd-export",
    "lab-index": "lab-index-export",
    "deep-foundations": "deep-foundations-export",
    "monitoring": "monitoring-export",
}
FIRST_TEST = 'gml:id="ISPT-1">'
#: In the first SPT test: its reference to the boring, and its one result value. The
#: names and numbers are the seed's, and change with the suite version, so they are found.
FEATURE_REF = re.compile(r'<samplingFeatureRef xlink:href="#B-\d+"/>')
DATA_VALUES = re.compile(r'<dataValues cs="," ts=" ">\d+</dataValues>')


@pytest.fixture(scope="module")
def spt() -> str:
    return (
        conformance.new("spt-export", product="Demo", version="1", seed="demo").reference().decode()
    )


def _first_test(xml: str, old: str | re.Pattern[str], new: str) -> bytes:
    """``xml`` with ``old`` replaced by ``new`` inside the first SPT test only."""
    start = xml.index(FIRST_TEST)
    end = xml.index("</Test>", start)
    block = xml[start:end]
    if isinstance(old, re.Pattern):
        found = old.search(block)
        assert found is not None
        old = found.group(0)
    assert old in block
    return (xml[:start] + block.replace(old, new, 1) + xml[end:]).encode()


def _statuses(result: conformance.Acceptance) -> dict[str, str]:
    return {o.requirement.id: o.status for o in result.outcomes}


# --- the requirements -----------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_family_publishes_its_requirements_with_a_diggs_source(family: str) -> None:
    found = conformance.requirements(family)
    ids = [r.id for r in found]
    assert ids[:3] == ["accept.diggs-3", "accept.valid", "accept.present"]
    assert len(ids) == len(set(ids))
    assert all(r.text and "DIGGS" in r.source for r in found)


def test_the_families_are_those_with_a_scenario() -> None:
    assert [f.id for f in conformance.acceptance_families()] == list(FAMILIES)
    assert len(FAMILIES) == 9
    with pytest.raises(ValueError, match="the families are: " + ", ".join(FAMILIES)):
        conformance.requirements("geophysics")


def test_the_docs_name_every_family_accept_takes() -> None:
    page = (Path(__file__).parent.parent / "docs" / "conformance.md").read_text("utf-8")
    start = page.index("`--class` is one of")
    sentence = " ".join(page[start : page.index(".", start)].split())
    assert sorted(sentence.split("`")[3::2]) == sorted(FAMILIES)


def test_dictionary_properties_come_from_the_bundled_dictionaries() -> None:
    spt = _requirements.defined_properties("properties.xml", "DrivenPenetrationTest")
    assert "n_value" in spt and "tip_resistance" not in spt
    cpt = _requirements.defined_properties("properties.xml", "StaticConePenetrationTest")
    assert {"tip_resistance", "sleeve_friction", "pore_pressure_u2"} <= cpt
    piles = _requirements.defined_properties("codes/pil_properties.xml", "PileDrivingRecord")
    assert {"blow_count", "pen_increment", "stroke"} <= piles


# --- passing files ------------------------------------------------------------------


@pytest.mark.parametrize(("family", "class_id"), sorted(FAMILIES.items()))
@pytest.mark.parametrize("seed", ["demo", "other"])
def test_a_reference_export_is_accepted(family: str, class_id: str, seed: str) -> None:
    data = conformance.new(class_id, product="Demo", version="1", seed=seed).reference()
    result = conformance.accept(data, family)
    assert result.accepted, [(o.requirement.id, o.items[:2]) for o in result.outcomes]
    assert result.file_sha256 == hashlib.sha256(data).hexdigest()
    assert result.file_diggs_version == "3.0.0"
    assert all(o.total >= 1 for o in result.outcomes)


# --- failing files, one per requirement ---------------------------------------------


def test_a_file_that_is_not_diggs_fails_and_nothing_else_is_looked_at() -> None:
    result = conformance.accept(b"<not-diggs/>", "spt")
    assert not result.accepted
    statuses = _statuses(result)
    assert statuses.pop("accept.diggs-3") == "fail"
    assert set(statuses.values()) == {"skipped"}
    assert result.to_dict()["file"]["diggs_version"] is None


def test_a_file_cut_short_by_the_reader_limits_fails(spt: str) -> None:
    result = conformance.accept(spt.encode(), "spt", time_budget=1e-9)
    assert _statuses(result)["accept.diggs-3"] == "fail"
    assert not result.accepted


@pytest.mark.parametrize("family", ["spt", "cpt", "mwd", "deep-foundations"])
def test_nothing_is_said_of_the_content_of_a_file_read_in_part(spt: str, family: str) -> None:
    # Found in review: on a model cut short the content checks ran on the part that was
    # read, and reported "no test" or "15/15" as if of the whole file.
    result = conformance.accept(spt.encode(), family, time_budget=1e-9)
    statuses = _statuses(result)
    assert statuses.pop("accept.diggs-3") == "fail"
    statuses.pop("accept.valid")
    assert statuses and set(statuses.values()) == {"skipped"}
    content = [o for o in result.outcomes if o.status == "skipped"]
    assert all((o.passed, o.total, o.failed, o.items) == (0, 0, 0, ()) for o in content)
    assert not result.accepted
    _schema_check(json.loads(result.to_json()))
    assert "SKIPPED  accept.present" in result.to_text()
    assert "could not be read to its end" in result.to_text()


def test_an_invalid_file_fails_validation(spt: str) -> None:
    data = _first_test(spt, "<penetrationTestType>SPT</penetrationTestType>", "<bogus/>")
    result = conformance.accept(data, "spt")
    statuses = _statuses(result)
    assert statuses["accept.diggs-3"] == "pass"
    assert statuses["accept.valid"] == "fail"
    assert result.outcome("accept.valid").items
    assert not result.accepted


def test_a_file_of_another_family_fails_present(spt: str) -> None:
    result = conformance.accept(spt.encode(), "cpt")
    assert _statuses(result)["accept.present"] == "fail"
    assert result.outcome("accept.present").items == (
        ("file", "no test whose procedure is StaticConePenetrationTest"),
    )


def test_a_test_on_no_feature_fails_located(spt: str) -> None:
    result = conformance.accept(_first_test(spt, FEATURE_REF, ""), "spt")
    located = result.outcome("accept.located")
    assert located.status == "fail"
    assert (located.failed, located.total - located.passed) == (1, 1)
    assert located.items[0][0] == "ISPT-1"


def test_a_test_with_no_dictionary_property_fails_result(spt: str) -> None:
    data = _first_test(spt, DATA_VALUES, '<dataValues cs="," ts=" "></dataValues>')
    result = conformance.accept(data, "spt")
    assert result.outcome("accept.result").status == "fail"
    assert result.outcome("accept.result").items[0][0] == "ISPT-1"


def test_a_value_with_no_position_fails_position(spt: str) -> None:
    start = spt.index("<location>", spt.index(FIRST_TEST))
    end = spt.index("</location>", start) + len("</location>")
    result = conformance.accept((spt[:start] + spt[end:]).encode(), "spt")
    position = result.outcome("accept.position")
    assert position.status == "fail"
    assert position.items[0][0] == "ISPT-1"


def _all_null(xml: bytes, record: str) -> bytes:
    """``xml`` with every cell of the first ``record``'s result -999, declared as nullValue."""
    ns = {"d": "http://diggsml.org/schemas/3"}
    root = etree.fromstring(xml)
    holder = root.find(f".//d:{record}", ns)
    assert holder is not None
    result = holder.find(".//d:ResultSet", ns)
    assert result is not None
    values = result.find("d:dataValues", ns)
    assert values is not None and values.text
    for prop in result.iterfind(".//d:Property", ns):
        etree.SubElement(prop, "{http://diggsml.org/schemas/3}nullValue").text = "-999"
    rows = [row.split(",") for row in values.text.split()]
    values.text = " ".join(",".join("-999" for _ in row) for row in rows)
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))


@pytest.mark.parametrize(
    ("family", "record", "requirement"),
    [
        ("spt", "Test", "accept.result"),
        ("cpt", "Test", "accept.result"),
        ("mwd", "MeasurementWhileDrilling", "accept.result"),
        ("deep-foundations", "PileDrivingRecord", "accept.result"),
    ],
)
def test_a_result_that_is_all_null_values_is_no_result(
    family: str, record: str, requirement: str
) -> None:
    # Found in review: a cell equal to its column's declared nullValue counted as a
    # value, so an SPT file whose every result was -999 was accepted.
    reference = conformance.new(FAMILIES[family], product="D", version="1", seed="demo").reference()
    result = conformance.accept(_all_null(reference, record), family)
    statuses = _statuses(result)
    assert statuses["accept.valid"] == "pass", result.outcome("accept.valid").items
    assert statuses[requirement] == "fail"
    assert result.outcome(requirement).failed == 1
    if "accept.position" in statuses:
        # A null cell is not a result value, so it is not asked for a position either.
        position, results = result.outcome("accept.position"), result.outcome("accept.result")
        assert position.status == "pass" and position.total == results.total - 1


def test_a_null_value_cell_with_no_position_is_not_asked_for_one(spt: str) -> None:
    start = spt.index("<location>", spt.index(FIRST_TEST))
    end = spt.index("</location>", start) + len("</location>")
    unplaced = (spt[:start] + spt[end:]).encode()
    assert conformance.accept(unplaced, "spt").outcome("accept.position").status == "fail"
    position = conformance.accept(_all_null(unplaced, "Test"), "spt").outcome("accept.position")
    assert position.status == "pass"


def test_a_pile_file_without_piles_fails_present() -> None:
    spt = conformance.new("spt-export", product="Demo", version="1", seed="demo").reference()
    result = conformance.accept(spt, "deep-foundations")
    present = result.outcome("accept.present")
    assert present.status == "fail"
    assert present.failed == 2


def test_a_driving_record_on_no_pile_fails_located() -> None:
    challenge = conformance.new("deep-foundations-export", product="D", version="1", seed="demo")
    xml = challenge.reference().decode()
    start = xml.index("<PileDrivingActivity")
    ref = xml.index("<samplingFeatureRef", start)
    end = xml.index("/>", ref) + 2
    # The activity names the test boring instead of its pile: valid DIGGS, wrong pile.
    boring = next(line.split('"')[1] for line in xml.splitlines() if "<Borehole gml:id=" in line)
    data = (xml[:ref] + f'<samplingFeatureRef xlink:href="#{boring}"/>' + xml[end:]).encode()
    result = conformance.accept(data, "deep-foundations")
    assert result.outcome("accept.located").status == "fail"


def test_a_driving_record_with_no_values_fails_result() -> None:
    challenge = conformance.new("deep-foundations-export", product="D", version="1", seed="demo")
    xml = challenge.reference().decode()
    start = xml.index("<pileDrivingRecordResults")
    values = xml.index("<dataValues", start)
    end = xml.index("</dataValues>", values)
    opened = xml.index(">", values) + 1
    data = (xml[:opened] + xml[end:]).encode()
    result = conformance.accept(data, "deep-foundations")
    assert result.outcome("accept.result").status == "fail"


def test_an_unknown_family_is_refused() -> None:
    with pytest.raises(ValueError, match="no acceptance requirements"):
        conformance.accept(b"<a/>", "geophysics")


def test_a_missing_or_large_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(InstanceReadError):
        conformance.accept(tmp_path / "none.xml", "spt")
    with pytest.raises(InstanceReadError):
        conformance.accept(b"x" * 20, "spt", max_bytes=10)


# --- reports ------------------------------------------------------------------------

HOSTILE = "<script>alert(1)</script>"


@pytest.fixture(scope="module")
def hostile(spt: str) -> conformance.Acceptance:
    # A test named with markup, on no feature: its name is shown in the failures.
    named = _first_test(spt, FEATURE_REF, "").decode()
    escaped = HOSTILE.replace("<", "&lt;").replace(">", "&gt;")
    data = named.replace(FIRST_TEST, FIRST_TEST + f"<gml:name>{escaped}</gml:name>", 1)
    result = conformance.accept(data.encode(), "spt")
    assert result.outcome("accept.located").items[0][0] == HOSTILE
    return result


def test_the_html_report_escapes_file_text_and_names_the_digest(
    hostile: conformance.Acceptance,
) -> None:
    page = hostile.to_html()
    assert HOSTILE not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert hostile.file_sha256 in page
    assert "NOT ACCEPTED" in page
    assert "does not show" in page and "engineering" in page


def test_the_text_report_spells_out_control_characters(spt: str) -> None:
    named = _first_test(spt, FEATURE_REF, "").decode()
    data = named.replace(FIRST_TEST, FIRST_TEST + "<gml:name>a&#x1b;[31mb</gml:name>", 1)
    text = conformance.accept(data.encode(), "spt").to_text()
    assert "\x1b" not in text
    assert "NOT ACCEPTED" in text


def test_the_json_document_keeps_file_text_and_says_what_it_does_not_show(
    hostile: conformance.Acceptance,
) -> None:
    document = json.loads(hostile.to_json())
    assert document["format"] == "diggs-conformance-acceptance"
    assert document["verdict"] == "fail"
    assert document["statement"] == conformance.ACCEPTANCE_STATEMENT
    assert document["not_shown"]
    located = next(r for r in document["requirements"] if r["id"] == "accept.located")
    assert located["items"][0]["id"] == HOSTILE
    assert located["source"].startswith("DIGGS 3.0.0")


# --- the command --------------------------------------------------------------------


def test_cli_exit_codes_and_outputs(
    tmp_path: Path, spt: str, capsys: pytest.CaptureFixture[str]
) -> None:
    good = tmp_path / "good.xml"
    good.write_text(spt, encoding="utf-8")
    bad = tmp_path / "bad.xml"
    bad.write_bytes(_first_test(spt, FEATURE_REF, ""))
    report = tmp_path / "report.html"
    accept = ["conformance", "accept"]
    assert cli.main([*accept, str(good), "--class", "spt", "--report", str(report)]) == 0
    assert "ACCEPTED" in capsys.readouterr().out
    assert hashlib.sha256(good.read_bytes()).hexdigest() in report.read_text("utf-8")
    assert cli.main([*accept, str(bad), "--class", "spt", "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"
    assert cli.main([*accept, str(good), "--class", "cpt", "--format", "html"]) == 1
    assert capsys.readouterr().out.startswith("<!doctype html>")
    not_diggs = tmp_path / "notes.txt"
    not_diggs.write_text("hello", encoding="utf-8")
    assert cli.main([*accept, str(not_diggs), "--class", "spt"]) == 1
    capsys.readouterr()
    assert cli.main([*accept, str(tmp_path / "none.xml"), "--class", "spt"]) == 2
    nowhere = tmp_path / "no-folder" / "report.html"
    assert cli.main([*accept, str(good), "--class", "spt", "--report", str(nowhere)]) == 2
    assert "cannot write the report" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exited:
        cli.main([*accept, str(good), "--class", "geophysics"])
    assert exited.value.code == 2


def test_docstring_examples_run() -> None:
    for module in (_accept, _requirements):
        failed, attempted = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
        assert attempted and failed == 0, module.__name__


# --- the schema ---------------------------------------------------------------------


def _schema_check(document: dict[str, object]) -> None:
    schema = conformance.conformance_schema("acceptance")
    jsonschema.Draft202012Validator(schema).validate(document)


def test_every_document_accept_writes_matches_its_schema(
    spt: str, hostile: conformance.Acceptance
) -> None:
    documents = [
        conformance.accept(spt.encode(), "spt"),  # pass
        hostile,  # fail, with file text in the items
        conformance.accept(spt.encode(), "deep-foundations"),  # fail, pile profile
        conformance.accept(b"<not-diggs/>", "cpt"),  # not DIGGS: skipped requirements
        conformance.accept(b"\xff\xfe", "lab-index"),  # not even XML
    ]
    for result in documents:
        document = json.loads(result.to_json())
        _schema_check(document)
        assert document["$schema"] == conformance.ACCEPTANCE_SCHEMA_URL


def test_the_acceptance_schema_is_in_step_with_the_code(spt: str) -> None:
    schema = conformance.conformance_schema("acceptance")
    assert schema["$id"] == conformance.ACCEPTANCE_SCHEMA_URL
    assert schema["$id"].endswith(
        f"/schemas/preview/diggs-conformance-acceptance-{conformance.ACCEPTANCE_SCHEMA_VERSION}"
        ".schema.json"
    )
    assert schema["properties"]["schema_version"]["const"] == conformance.ACCEPTANCE_SCHEMA_VERSION
    document = conformance.accept(spt.encode(), "spt").to_dict()
    assert set(schema["required"]) == set(schema["properties"]) == set(document)
    requirement = schema["properties"]["requirements"]["items"]
    assert set(requirement["required"]) == set(document["requirements"][0])
    for family in conformance.acceptance_families():
        for found in conformance.requirements(family.id):
            assert jsonschema.Draft202012Validator(
                {"pattern": requirement["properties"]["id"]["pattern"]}
            ).is_valid(found.id)
    with pytest.raises(ValueError, match="they are: evidence, acceptance, matrix"):
        conformance.conformance_schema("scorecard")


@pytest.mark.parametrize("family", ["mwd", "dissipation", "in-situ-strength"])
def test_the_reference_export_of_every_newer_family_is_accepted(family: str) -> None:
    # Found when the families were first combined: a drilling log is a measurement of its
    # own, not a test with a catalogue procedure, so the generic profile found none.
    for seed in ("a", "b"):
        challenge = conformance.new(f"{family}-export", product="Demo", version="1", seed=seed)
        result = conformance.accept(challenge.reference(), family).to_dict()
        assert result["verdict"] == "pass", result


def test_a_drilling_log_with_no_channel_of_the_mwd_dictionary_fails_result() -> None:
    challenge = conformance.new("mwd-export", product="Demo", version="1", seed="a")
    xml = challenge.reference().decode("utf-8")
    assert "mwd_properties.xml#" in xml
    result = conformance.accept(
        xml.replace("mwd_properties.xml#", "mwd_properties.xml#not_a_").encode(), "mwd"
    ).to_dict()
    by_id = {r["id"]: r for r in result["requirements"]}
    assert by_id["accept.present"]["status"] == "pass"
    assert by_id["accept.result"]["status"] == "fail"
    assert "accept.position" not in by_id


@pytest.mark.parametrize("renamed", ["not_a_thrust", "time"])
def test_a_drilling_log_with_only_depth_and_time_fails_result(renamed: str) -> None:
    # Found in review: depth and time are in the MWD dictionary, so a log that gave
    # where and when and no drilling channel passed accept.result.
    challenge = conformance.new("mwd-export", product="Demo", version="1", seed="a")
    xml = challenge.reference().decode("utf-8")
    assert "mwd_properties.xml#depth" in xml
    for key, _meaning in _mwd.CHANNELS.values():
        assert f"mwd_properties.xml#{key}" in xml
        new = renamed if key == "crowd_downward_thrust" else f"not_a_{key}"
        xml = xml.replace(f"mwd_properties.xml#{key}", f"mwd_properties.xml#{new}")
    by_id = {r["id"]: r for r in conformance.accept(xml.encode(), "mwd").to_dict()["requirements"]}
    assert by_id["accept.present"]["status"] == "pass"
    assert by_id["accept.located"]["status"] == "pass"
    assert by_id["accept.result"]["status"] == "fail"
    assert by_id["accept.result"]["failed"] == by_id["accept.result"]["total"] >= 2
