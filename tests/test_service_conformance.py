"""``pydiggs._service``: the conformance suite's operations, for an adapter on the caller's machine.

What each function answers is what :mod:`pydiggs.conformance` itself produces for the same
files; the files a caller gives are untrusted, so the limits and every refusal are held
here too. No HTTP route, sidecar method or MCP tool offers these operations.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import logging
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import pydiggs
from pydiggs import _service, conformance
from pydiggs._service import Code, ConformanceNewRequest, ConformanceRequest, Outcome
from pydiggs.errors import ResourceError

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "conformance" / "try" / "conformance_page.py"

EXPORTS = [c.id for c in conformance.classes() if c.direction == "export"]
IMPORTS = [c.id for c in conformance.classes() if c.direction == "import"]
ANSWERS = b"question_id,answer\r\n"


def _doc(outcome: Outcome) -> dict[str, Any]:
    assert outcome.ok, outcome
    assert outcome.document is not None and b"\n" not in outcome.document
    return dict(json.loads(outcome.document))


def _file(document: dict[str, Any]) -> bytes:
    """The file a result carries, checked against its sha256 as the npm client checks it."""
    data = base64.b64decode(document["output_base64"])
    assert hashlib.sha256(data).hexdigest() == document["output_sha256"]
    return data


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _deflated(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _new(class_id: str, **kwargs: Any) -> dict[str, Any]:
    given = {"product": "MyLog", "version": "4.2.1", "seed": "svc", **kwargs}
    return _doc(_service.conformance_new(ConformanceNewRequest(class_id=class_id, **given)))


def _manifest(class_id: str = "spt-export") -> bytes:
    return conformance.new(class_id, product="P", version="1", seed="svc").files["challenge.json"]


def _pair() -> tuple[conformance.Challenge, bytes, bytes]:
    """A pairwise spt-import challenge, with the evidence and export it was made from."""
    source = conformance.new("spt-export", product="A", version="1", seed="svc")
    exported = source.reference()
    evidence = conformance.grade(source, exported).to_json().encode()
    pair = conformance.new(
        "spt-import", product="B", version="2", from_export=evidence, export=exported
    )
    return pair, evidence, exported


@pytest.fixture(scope="module")
def pair() -> tuple[conformance.Challenge, bytes, bytes]:
    return _pair()


# --- classes ----------------------------------------------------------------------------


def test_classes_lists_what_the_suite_lists():
    listing = _service.conformance_classes()
    assert list(listing) == ["suite_version", "pydiggs", "statement", "classes", "untestable"]
    assert listing["suite_version"] == conformance.SUITE_VERSION == "1.0-beta.3"
    assert listing["pydiggs"] == pydiggs.__version__
    assert listing["statement"] == conformance.STATEMENT
    assert listing["classes"] == [
        {"id": c.id, "title": c.title, "direction": c.direction, "family": c.family}
        for c in conformance.classes()
    ]
    assert listing["untestable"] == [
        f.title for f in conformance.families() if not f.testable and not f.embedded
    ]
    assert listing["untestable"]
    json.dumps(listing, allow_nan=False)


# --- new --------------------------------------------------------------------------------


@pytest.mark.parametrize("class_id", [*EXPORTS, *IMPORTS])
def test_new_gives_the_folder_the_command_line_writes(class_id):
    made = _new(class_id)
    expected = conformance.new(class_id, product="MyLog", version="4.2.1", seed="svc")
    folder = f"{class_id}-svc"
    assert list(made) == [
        "class_id",
        "title",
        "direction",
        "seed",
        "token",
        "id",
        "folder",
        "files",
        "manifest",
        "readme",
        "output_sha256",
        "output_base64",
    ]
    assert (made["class_id"], made["title"]) == (class_id, expected.info.title)
    assert (made["direction"], made["seed"]) == (expected.info.direction, "svc")
    assert (made["token"], made["id"], made["folder"]) == (expected.token, expected.id, folder)
    assert made["files"] == sorted(expected.files)
    assert made["manifest"].encode("utf-8") == expected.files["challenge.json"]
    assert made["readme"].encode("utf-8") == expected.files["README.md"]
    archive = zipfile.ZipFile(io.BytesIO(_file(made)))
    assert {i.filename: archive.read(i) for i in archive.infolist()} == {
        f"{folder}/{name}": data for name, data in expected.files.items()
    }
    # The same seed gives the same ZIP, byte for byte.
    assert _new(class_id)["output_base64"] == made["output_base64"]


def test_new_draws_a_fresh_seed_when_none_is_given():
    first, second = _new("cpt-export", seed=None), _new("cpt-export", seed=None)
    assert first["seed"] != second["seed"]
    assert first["folder"] == f"cpt-export-{first['seed']}"


@pytest.mark.skipif(not PAGE.is_file(), reason="docs/ is not in the sdist")
def test_new_and_classes_answer_as_the_in_browser_page_does():
    # The page keeps its own copy of this code while it pins a released wheel.
    spec = importlib.util.spec_from_file_location("conformance_page", PAGE)
    assert spec is not None and spec.loader is not None
    page = importlib.util.module_from_spec(spec)
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(page)
    finally:
        sys.dont_write_bytecode = writes
    listed = json.loads(page.catalogue())
    assert {k: v for k, v in listed.items() if k != "ok"} == _service.conformance_classes()
    theirs = json.loads(page.make("mwd-import", "MyLog", "4.2.1", "svc"))
    ours = _new("mwd-import")
    assert ours["output_base64"] == theirs["zip"]
    shared = set(ours) & set(theirs)
    assert shared == set(ours) - {"output_base64", "output_sha256"}
    assert {k: ours[k] for k in shared} == {k: theirs[k] for k in shared}


@pytest.mark.parametrize(
    ("given", "parameter"),
    [
        ({"class_id": "no-such-class<script>"}, "class_id"),
        ({"class_id": 7}, "class_id"),
        ({"product": "  "}, "product"),
        ({"product": "x" * 201}, "product"),
        ({"version": ""}, "version"),
        ({"version": None}, "version"),
        ({"seed": "../x"}, "seed"),
        ({"seed": ""}, "seed"),
        ({"seed": "s" * 65}, "seed"),
        ({"seed": 5}, "seed"),
    ],
)
def test_new_refuses_a_parameter_with_a_fixed_text(given, parameter):
    request = {"class_id": "spt-export", "product": "P", "version": "1", **given}
    outcome = _service.conformance_new(ConformanceNewRequest(**request))
    assert outcome.code is Code.INVALID_PARAMETER
    assert [name for name, _ in outcome.errors] == [parameter]
    assert outcome.document is None
    assert "script" not in json.dumps(outcome.errors)  # the value is never echoed


def test_new_names_every_bad_parameter_at_once():
    outcome = _service.conformance_new(
        ConformanceNewRequest(class_id="x", product="", version="", seed="?")
    )
    assert [name for name, _ in outcome.errors] == ["class_id", "product", "version", "seed"]
    assert ", ".join(c.id for c in conformance.classes()) in outcome.errors[0][1]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"evidence": b"{}"}, "together"),
        ({"export": b"<x/>"}, "together"),
        ({"evidence": "text", "export": b"<x/>"}, "evidence must be bytes"),
        ({"evidence": b"{}", "export": bytearray(b"x")}, "export must be bytes"),
        ({"max_bytes": 0}, "max_bytes"),
        ({"max_bytes": True}, "max_bytes"),
        ({"time_budget": None}, "time_budget"),
        ({"time_budget": 0}, "time_budget"),
        ({"time_budget": _service.MAX_SECONDS + 1}, "time_budget"),
    ],
)
def test_the_adapters_own_mistakes_raise(kwargs, error):
    with pytest.raises((TypeError, ValueError), match=error):
        ConformanceNewRequest(class_id="spt-import", product="P", version="1", **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"challenge": "text"}, "challenge must be bytes"),
        ({"submission": "text"}, "submission must be bytes"),
        ({"evidence": bytearray(b"{}")}, "evidence must be bytes"),
        ({"max_bytes": -1}, "max_bytes"),
        ({"time_budget": float("inf")}, "time_budget"),
        ({"time_budget": None}, "time_budget"),
    ],
)
def test_a_request_about_a_challenge_checks_the_adapters_limits(kwargs, error):
    with pytest.raises((TypeError, ValueError), match=error):
        ConformanceRequest(**{"challenge": b"{}", **kwargs})


def test_the_limits_are_the_ones_the_design_names():
    request = ConformanceRequest(challenge=b"{}")
    assert request.max_bytes == 25 * 1024 * 1024
    assert request.time_budget == _service.CONFORMANCE_TIME_BUDGET == 120.0
    assert _service.CONFORMANCE_MAX_MANIFEST_BYTES == 64 * 1024
    assert _service.CONFORMANCE_MAX_EVIDENCE_BYTES == 8 * 1024 * 1024
    assert _service.CONFORMANCE_MAX_ZIP_MEMBERS == 200
    from pydiggs.conformance import _challenge, _pairwise

    assert _service.CONFORMANCE_MAX_MANIFEST_BYTES == _challenge._MAX_MANIFEST_BYTES
    assert _service.CONFORMANCE_MAX_EVIDENCE_BYTES == _pairwise.MAX_EVIDENCE_BYTES


# --- a pairwise challenge ---------------------------------------------------------------


def test_new_makes_a_pairwise_challenge_from_evidence_and_export(pair):
    expected, evidence, exported = pair
    made = _new(
        "spt-import", product="B", version="2", seed=None, evidence=evidence, export=exported
    )
    assert (made["seed"], made["id"], made["token"]) == ("svc", expected.id, expected.token)
    assert json.loads(made["manifest"])["source_export"] == expected.source_export.to_dict()
    archive = zipfile.ZipFile(io.BytesIO(_file(made)))
    assert {i.filename: archive.read(i) for i in archive.infolist()} == {
        f"spt-import-svc/{name}": data for name, data in expected.files.items()
    }
    assert archive.read("spt-import-svc/challenge.xml") == exported


def test_a_pairwise_challenge_is_graded_and_verified_from_its_zip(pair, tmp_path):
    expected, evidence, exported = pair
    made = _new(
        "spt-import", product="B", version="2", seed=None, evidence=evidence, export=exported
    )
    challenge = _file(made)
    request = ConformanceRequest(challenge=challenge, submission=ANSWERS, name="answers.csv")
    graded = _doc(_service.conformance_grade(request))
    # What `pydiggs conformance grade` writes for the same folder and answers.
    folder = expected.write(tmp_path / "challenge")
    (tmp_path / "answers.csv").write_bytes(ANSWERS)
    result = conformance.grade(folder, tmp_path / "answers.csv")
    assert graded["evidence"] == result.to_dict()
    assert graded["evidence"]["source_export"]["export_sha256"] == (
        hashlib.sha256(exported).hexdigest()
    )
    assert graded["evidence_json"] == result.to_json() + "\n"
    checked = _doc(
        _service.conformance_verify(
            ConformanceRequest(
                challenge=challenge,
                submission=ANSWERS,
                evidence=graded["evidence_json"].encode(),
                name="renamed.csv",
            )
        )
    )
    assert (checked["reproduced"], checked["differences"]) == (True, [])
    assert (checked["verdict"], checked["class_id"]) == ("fail", "spt-import")
    # The reference of a pairwise challenge is its own data's, from the same ZIP.
    reference = _doc(_service.conformance_reference(ConformanceRequest(challenge=challenge)))
    assert _file(reference) == expected.reference()


def test_a_pairwise_manifest_without_its_zip_is_refused(pair):
    manifest = pair[0].files["challenge.json"]
    for outcome in (
        _service.conformance_grade(ConformanceRequest(challenge=manifest, submission=ANSWERS)),
        _service.conformance_verify(
            ConformanceRequest(challenge=manifest, submission=ANSWERS, evidence=b"{}")
        ),
        _service.conformance_reference(ConformanceRequest(challenge=manifest)),
    ):
        assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "source-export")
        assert "give the challenge ZIP" in outcome.detail


def test_a_pairwise_zip_must_hold_exactly_one_challenge_xml(pair):
    files = pair[0].files
    without = _zip({f"p/{n}": d for n, d in files.items() if n != "challenge.xml"})
    outcome = _service.conformance_grade(ConformanceRequest(challenge=without, submission=ANSWERS))
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "source-export")
    assert "does not hold that export as challenge.xml" in outcome.detail
    twice = _zip({**{f"p/{n}": d for n, d in files.items()}, "q/challenge.xml": b"<x/>"})
    outcome = _service.conformance_grade(ConformanceRequest(challenge=twice, submission=ANSWERS))
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "not-a-challenge")
    assert outcome.detail == "The challenge ZIP must hold exactly one challenge.xml."


def test_a_challenge_xml_of_another_digest_is_refused(pair):
    files = {**pair[0].files, "challenge.xml": pair[0].files["challenge.xml"] + b"\n"}
    swapped = _zip({f"p/{n}": d for n, d in files.items()})
    outcome = _service.conformance_grade(ConformanceRequest(challenge=swapped, submission=ANSWERS))
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "source-export")
    assert outcome.detail.startswith("challenge.xml is not the export this challenge was made from")
    assert hashlib.sha256(files["challenge.xml"]).hexdigest() in outcome.detail


def test_a_challenge_xml_over_the_limit_is_refused(pair):
    files = pair[0].files
    small = _deflated({f"p/{n}": files[n] for n in ("challenge.json", "challenge.xml")})
    limit = len(small) + 1
    assert len(files["challenge.xml"]) > limit  # the ZIP is smaller than its member
    outcome = _service.conformance_grade(
        ConformanceRequest(challenge=small, submission=ANSWERS, max_bytes=limit)
    )
    assert outcome.code is Code.INPUT_TOO_LARGE
    assert outcome.detail == "The challenge.xml in the challenge ZIP is larger than allowed."


def test_the_member_path_of_a_pairwise_zip_is_never_used(pair, tmp_path, monkeypatch):
    files = pair[0].files
    hostile = _zip(
        {
            "../../outside/challenge.json": files["challenge.json"],
            "/abs/else\\challenge.xml": files["challenge.xml"],
        }
    )
    monkeypatch.chdir(tmp_path)
    request = ConformanceRequest(challenge=hostile, submission=ANSWERS)
    assert _doc(_service.conformance_grade(request))["passed"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("change", "code", "kind"),
    [
        ({"class_id": "spt-export"}, Code.INVALID_PARAMETER, None),
        ({"seed": "another"}, Code.INVALID_PARAMETER, None),
        ({"class_id": "cpt-import"}, Code.INVALID_DOCUMENT, "source-export"),
        ({"evidence": b"not json"}, Code.INVALID_DOCUMENT, "not-evidence"),
        ({"evidence": b'{"format": "other"}'}, Code.INVALID_DOCUMENT, "not-evidence"),
        ({"export": b"<x/>"}, Code.INVALID_DOCUMENT, "source-export"),
    ],
)
def test_new_refuses_a_pairwise_challenge_the_suite_refuses(pair, change, code, kind):
    _, evidence, exported = pair
    given = {
        "class_id": "spt-import",
        "product": "B",
        "version": "2",
        "evidence": evidence,
        "export": exported,
        **change,
    }
    outcome = _service.conformance_new(ConformanceNewRequest(**given))
    assert (outcome.code, outcome.kind) == (code, kind)
    assert outcome.detail and outcome.errors == ()
    # The answer is the suite's own.
    with pytest.raises(ValueError) as raised:  # DocumentError is one too
        conformance.new(
            given["class_id"],
            product="B",
            version="2",
            seed=given.get("seed"),
            from_export=given["evidence"],
            export=given["export"],
        )
    assert outcome.detail == str(raised.value)


def test_new_limits_the_evidence_and_the_export(pair, monkeypatch):
    _, evidence, exported = pair
    request = {"class_id": "spt-import", "product": "B", "version": "2"}
    small = ConformanceNewRequest(**request, evidence=evidence, export=exported, max_bytes=100)
    outcome = _service.conformance_new(small)
    assert (outcome.code, outcome.detail) == (
        Code.INPUT_TOO_LARGE,
        "The export is larger than allowed.",
    )
    monkeypatch.setattr(_service, "CONFORMANCE_MAX_EVIDENCE_BYTES", len(evidence) - 1)
    outcome = _service.conformance_new(
        ConformanceNewRequest(**request, evidence=evidence, export=exported)
    )
    assert (outcome.code, outcome.detail) == (
        Code.INPUT_TOO_LARGE,
        "The evidence is larger than allowed.",
    )


# --- reference, grade, verify -----------------------------------------------------------


@pytest.mark.parametrize("class_id", EXPORTS)
def test_grade_gives_the_evidence_the_command_line_gives(class_id, tmp_path):
    made = _new(class_id)
    challenge = _file(made)
    reference = _doc(_service.conformance_reference(ConformanceRequest(challenge=challenge)))
    assert list(reference) == ["output_name", "output_sha256", "output_base64"]
    assert reference["output_name"] == f"{class_id}-svc-pydiggs.xml"
    xml = _file(reference)
    expected = conformance.new(class_id, product="MyLog", version="4.2.1", seed="svc")
    assert xml == expected.reference()
    graded = _doc(
        _service.conformance_grade(
            ConformanceRequest(challenge=challenge, submission=xml, name="up/my-export.xml")
        )
    )
    assert list(graded) == ["passed", "evidence", "evidence_json", "report_html"]
    assert graded["passed"] is True
    # What `pydiggs conformance grade` writes for the same files.
    folder = expected.write(tmp_path / "challenge")
    (tmp_path / "my-export.xml").write_bytes(xml)
    result = conformance.grade(folder, tmp_path / "my-export.xml")
    assert graded["evidence"] == result.to_dict()
    assert graded["evidence_json"] == result.to_json() + "\n"
    assert graded["report_html"] == result.to_html()
    assert graded["evidence"]["suite_version"] == "1.0-beta.3"
    # ... and the suite's own verify confirms it.
    assert conformance.verify(json.loads(graded["evidence_json"]), folder, xml).ok


def test_the_manifest_alone_is_the_challenge_too():
    made = _new("spt-export")
    by_zip = ConformanceRequest(challenge=_file(made), submission=b"<x/>", name="x.xml")
    by_manifest = ConformanceRequest(
        challenge=made["manifest"].encode(), submission=b"<x/>", name="x.xml"
    )
    first = _service.conformance_grade(by_zip)
    assert first.document == _service.conformance_grade(by_manifest).document
    assert _doc(first)["passed"] is False


@pytest.mark.parametrize("class_id", IMPORTS)
def test_grade_takes_answers_for_an_import_class(class_id):
    challenge = conformance.new(class_id, product="P", version="1", seed="svc")
    request = ConformanceRequest(
        challenge=challenge.files["challenge.json"], submission=ANSWERS, name="answers.csv"
    )
    graded = _doc(_service.conformance_grade(request))
    assert graded["evidence"] == conformance.grade(challenge, ANSWERS).to_dict() | {
        "submission": graded["evidence"]["submission"]
    }
    assert graded["evidence"]["submission"]["name"] == "answers.csv"
    assert graded["passed"] is False
    assert [s["id"] for s in graded["evidence"]["stages"]] == ["core", "values", "robustness"]


def test_verify_reproduces_and_catches_a_tampered_document():
    challenge = _file(_new("cpt-export"))
    xml = _file(_doc(_service.conformance_reference(ConformanceRequest(challenge=challenge))))
    evidence = _doc(
        _service.conformance_grade(
            ConformanceRequest(challenge=challenge, submission=xml, name="x.xml")
        )
    )["evidence_json"]

    def verify(given: bytes, submission: bytes = xml) -> dict[str, Any]:
        request = ConformanceRequest(
            challenge=challenge, submission=submission, evidence=given, name="renamed.xml"
        )
        return _doc(_service.conformance_verify(request))

    assert verify(evidence.encode()) == {
        "reproduced": True,
        "differences": [],
        "notes": [],
        "verdict": "pass",
        "class_id": "cpt-export",
    }
    assert verify(b"\xef\xbb\xbf" + evidence.encode())["reproduced"] is True  # a byte order mark
    tampered = json.dumps(json.loads(evidence) | {"verdict": "fail"}).encode()
    again = verify(tampered)
    expected = conformance.verify(
        json.loads(tampered),
        conformance.new("cpt-export", product="MyLog", version="4.2.1", seed="svc"),
        xml,
    )
    assert (again["reproduced"], again["differences"]) == (False, ["verdict"])
    assert (expected.ok, list(expected.differences)) == (False, ["verdict"])
    # Another file: the digest and the verdict differ.
    other = verify(evidence.encode(), b"<x/>")
    assert "submission" in other["differences"] and other["verdict"] == "fail"


@pytest.mark.parametrize(
    ("evidence", "detail"),
    [
        (b"not json", "The evidence is not JSON."),
        (b"\xff\xfe", "The evidence is not JSON."),
        (b"[1, 2]", "The evidence is not a diggs-conformance document."),
        (b'{"format": "something-else"}', "the evidence is not a diggs-conformance document"),
        (
            b'{"format": "diggs-conformance", "schema_version": "9"}',
            "evidence schema '9' is not one this release reads",
        ),
    ],
)
def test_verify_refuses_what_is_not_evidence(evidence, detail):
    outcome = _service.conformance_verify(
        ConformanceRequest(challenge=_manifest(), submission=b"<x/>", evidence=evidence)
    )
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "not-evidence")
    assert outcome.detail.startswith(detail)


def test_verify_limits_the_evidence(monkeypatch):
    monkeypatch.setattr(_service, "CONFORMANCE_MAX_EVIDENCE_BYTES", 10)
    outcome = _service.conformance_verify(
        ConformanceRequest(challenge=_manifest(), submission=b"<x/>", evidence=b" " * 11)
    )
    assert (outcome.code, outcome.detail) == (
        Code.INPUT_TOO_LARGE,
        "The evidence is larger than allowed.",
    )


def test_what_an_operation_needs_is_required():
    request = ConformanceRequest(challenge=_manifest())
    assert _service.conformance_grade(request).errors == (("submission", "is required"),)
    outcome = _service.conformance_verify(request)
    assert outcome.code is Code.INVALID_PARAMETER
    assert outcome.errors == (("submission", "is required"), ("evidence", "is required"))
    long_name = ConformanceRequest(
        challenge=_manifest(), submission=b"<x/>", name="x" * (_service.MAX_NAME_INPUT_CHARS + 1)
    )
    assert [name for name, _ in _service.conformance_grade(long_name).errors] == ["name"]
    assert _service.conformance_reference(request).ok  # the challenge alone


def test_the_limits_reach_the_suite(monkeypatch):
    seen: dict[str, Any] = {}
    real = conformance.grade

    def spy(challenge, submission, **kwargs):
        seen.update(kwargs, submission=submission)
        return real(challenge, submission, **kwargs)

    monkeypatch.setattr(conformance, "grade", spy)
    request = ConformanceRequest(
        challenge=_manifest(), submission=b"<x/>", name="a.xml", max_bytes=4096, time_budget=7.5
    )
    assert _service.conformance_grade(request).ok
    assert (seen["max_bytes"], seen["time_budget"]) == (4096, 7.5)
    assert seen["submission"].name == "a.xml"
    assert not seen["submission"].exists()  # the private folder is gone


# --- hostile files ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("challenge", "code", "kind", "detail"),
    [
        (b"{}", Code.INVALID_DOCUMENT, "not-a-challenge", "challenge.json is not a challenge"),
        (b"[]", Code.INVALID_DOCUMENT, "not-a-challenge", "challenge.json is not a challenge"),
        (b"", Code.INVALID_DOCUMENT, "not-a-challenge", "cannot read the challenge"),
        (b"x" * (64 * 1024 + 1), Code.INPUT_TOO_LARGE, None, "at most 65536 bytes"),
        (
            b"PK\x03\x04 broken",
            Code.INVALID_DOCUMENT,
            "not-a-challenge",
            "The challenge ZIP cannot be read.",
        ),
        (
            _zip({"a/README.md": b"x"}),
            Code.INVALID_DOCUMENT,
            "not-a-challenge",
            "The challenge ZIP must hold exactly one challenge.json.",
        ),
        (
            _zip({"a/challenge.json": b"{}", "b/challenge.json": b"{}"}),
            Code.INVALID_DOCUMENT,
            "not-a-challenge",
            "The challenge ZIP must hold exactly one challenge.json.",
        ),
        (
            _zip({"a/challenge.json": b" " * (64 * 1024 + 1)}),
            Code.INPUT_TOO_LARGE,
            None,
            "The challenge.json in the challenge ZIP is larger than allowed.",
        ),
        (
            _zip({f"f{i}": b"" for i in range(201)}),
            Code.INPUT_TOO_LARGE,
            None,
            "The challenge ZIP holds more than 200 files.",
        ),
    ],
)
def test_a_challenge_that_is_not_one_is_refused(challenge, code, kind, detail):
    for outcome in (
        _service.conformance_grade(ConformanceRequest(challenge=challenge, submission=b"<x/>")),
        _service.conformance_verify(
            ConformanceRequest(challenge=challenge, submission=b"<x/>", evidence=b"{}")
        ),
        _service.conformance_reference(ConformanceRequest(challenge=challenge)),
    ):
        assert (outcome.code, outcome.kind) == (code, kind)
        assert detail in outcome.detail
        assert outcome.kind is None or outcome.kind in _service.CONFORMANCE_KINDS
        # The private temporary folder is never named to the caller.
        assert "tmp" not in outcome.detail.lower() and "/" not in outcome.detail


def test_two_hundred_members_are_accepted():
    files = {f"f{i}": b"" for i in range(199)} | {"c/challenge.json": _manifest()}
    assert _service.conformance_reference(ConformanceRequest(challenge=_zip(files))).ok


def test_a_compressed_member_is_read_within_its_limit(monkeypatch):
    # Small in the ZIP, large once inflated: refused by the size the member states.
    bomb = _deflated({"c/challenge.json": _manifest() + b" " * (1024 * 1024)})
    assert len(bomb) < _service.CONFORMANCE_MAX_MANIFEST_BYTES
    outcome = _service.conformance_reference(ConformanceRequest(challenge=bomb))
    assert outcome.code is Code.INPUT_TOO_LARGE
    assert outcome.detail == "The challenge.json in the challenge ZIP is larger than allowed."
    # A member that gives more than it states is cut at the limit all the same.
    archive = _zip({"c/challenge.json": _manifest()})
    monkeypatch.setattr(_service, "CONFORMANCE_MAX_MANIFEST_BYTES", 4096)
    asked: list[int] = []

    class Endless(io.BytesIO):
        def read(self, size=-1):
            asked.append(size)
            return b" " * size

    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, *args, **kwargs: Endless())
    outcome = _service.conformance_reference(ConformanceRequest(challenge=archive))
    assert asked == [4097]
    assert outcome.code is Code.INPUT_TOO_LARGE
    assert outcome.detail == "The challenge.json in the challenge ZIP is larger than allowed."


def test_an_encrypted_member_is_refused(monkeypatch):
    def encrypted(self, *args, **kwargs):
        raise RuntimeError("File 'c/challenge.json' is encrypted, password required")

    archive = _zip({"c/challenge.json": _manifest()})
    monkeypatch.setattr(zipfile.ZipFile, "open", encrypted)
    outcome = _service.conformance_reference(ConformanceRequest(challenge=archive))
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "not-a-challenge")
    assert outcome.detail == "The challenge ZIP cannot be read."


def test_a_file_over_the_limit_is_refused():
    manifest = _manifest()
    outcome = _service.conformance_grade(
        ConformanceRequest(challenge=manifest, submission=b"x" * 11, max_bytes=10)
    )
    assert (outcome.code, outcome.detail) == (
        Code.INPUT_TOO_LARGE,
        "The submission is larger than allowed.",
    )
    archive = _zip({"c/challenge.json": manifest})
    outcome = _service.conformance_reference(ConformanceRequest(challenge=archive, max_bytes=10))
    assert (outcome.code, outcome.detail) == (
        Code.INPUT_TOO_LARGE,
        "The challenge ZIP is larger than allowed.",
    )


def test_another_suite_versions_challenge_says_so():
    manifest = json.loads(_manifest()) | {"suite_version": "0.1"}
    outcome = _service.conformance_grade(
        ConformanceRequest(challenge=json.dumps(manifest).encode(), submission=b"<x/>")
    )
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "suite-version")
    assert "'0.1'" in outcome.detail and "1.0-beta.3" in outcome.detail


def test_a_long_value_of_a_file_is_cut_in_the_message():
    manifest = json.loads(_manifest()) | {"suite_version": "<b>" * 5000}
    outcome = _service.conformance_grade(
        ConformanceRequest(challenge=json.dumps(manifest).encode(), submission=b"<x/>")
    )
    assert outcome.kind == "suite-version"
    assert len(outcome.detail) == _service._CONFORMANCE_MAX_DETAIL
    assert outcome.detail.endswith("…")


@pytest.mark.parametrize(
    "class_id", ["spt-export", "cpt-export", "lab-index-export", "deep-foundations-export"]
)
def test_a_challenge_of_the_earlier_suite_is_still_graded(class_id):
    from pydiggs.conformance import _challenge

    assert "1.0-beta.2" in conformance.GRADED_SUITES
    earlier = _challenge._make(
        conformance.class_info(class_id), "MyLog", "4.2.1", "svc", "1.0-beta.2"
    )
    challenge = _zip({f"{class_id}-svc/{name}": data for name, data in earlier.files.items()})
    exported = _file(_doc(_service.conformance_reference(ConformanceRequest(challenge=challenge))))
    assert exported == earlier.reference()
    request = ConformanceRequest(challenge=challenge, submission=exported, name="export.xml")
    graded = _doc(_service.conformance_grade(request))
    assert graded["passed"] is True
    assert graded["evidence"]["suite_version"] == "1.0-beta.2"
    assert graded["evidence"] == conformance.grade(earlier, exported).to_dict() | {
        "submission": graded["evidence"]["submission"]
    }
    again = ConformanceRequest(
        challenge=challenge, submission=exported, evidence=graded["evidence_json"].encode()
    )
    assert _doc(_service.conformance_verify(again))["reproduced"] is True


@pytest.mark.parametrize(
    ("name", "recorded"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\export.xml", "export.xml"),
        ("challenge", "challenge"),
        ("", "submission"),
        (None, "submission"),
        ("..", "submission"),
        ("<img src=x>.xml", "_img src_x_.xml"),
        ("n" * 300 + ".xml", "n" * 120),
    ],
)
def test_a_file_name_only_names_the_file(name, recorded):
    request = ConformanceRequest(challenge=_manifest(), submission=b"<x/>", name=name)
    graded = _doc(_service.conformance_grade(request))
    assert graded["evidence"]["submission"]["name"] == recorded
    assert graded["passed"] is False


# --- failing closed ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RuntimeError("secret /srv/path"), Code.INTERNAL),
        (ResourceError("secret /srv/path"), Code.RESOURCE_ERROR),
    ],
)
def test_an_unexpected_error_fails_closed(error, code, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise error

    manifest = _manifest()
    monkeypatch.setattr(conformance, "grade", broken)
    monkeypatch.setattr(conformance, "new", broken)
    with caplog.at_level(logging.ERROR, logger="pydiggs.service"):
        graded = _service.conformance_grade(
            ConformanceRequest(challenge=manifest, submission=b"<x/>")
        )
        made = _service.conformance_new(
            ConformanceNewRequest(class_id="spt-export", product="P", version="1")
        )
    for outcome in (graded, made):
        assert outcome.code is code
        assert outcome.detail == _service._GENERIC[code]
        assert outcome.correlation_id and outcome.correlation_id in caplog.text
    assert "secret /srv/path" in caplog.text


def test_an_unreadable_file_is_input_unreadable(monkeypatch):
    def unreadable(*args, **kwargs):
        raise pydiggs.InstanceReadError("cannot read the submission")

    monkeypatch.setattr(conformance, "grade", unreadable)
    outcome = _service.conformance_grade(
        ConformanceRequest(challenge=_manifest(), submission=b"<x/>")
    )
    assert (outcome.code, outcome.detail) == (
        Code.INPUT_UNREADABLE,
        "cannot read the submission",
    )


def test_a_private_folder_that_cannot_be_made_is_internal(monkeypatch):
    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    manifest = _manifest()
    monkeypatch.setattr(_service.tempfile, "TemporaryDirectory", full)
    outcome = _service.conformance_reference(ConformanceRequest(challenge=manifest))
    assert (outcome.code, outcome.detail) == (Code.INTERNAL, _service._GENERIC[Code.INTERNAL])


# --- where the operations are, and are not ----------------------------------------------


def test_the_service_layer_loads_the_suite_only_when_asked():
    code = (
        "import sys\n"
        "from pydiggs import _service\n"
        "before = 'pydiggs.conformance' in sys.modules\n"
        "_service.conformance_classes()\n"
        "print(before, 'pydiggs.conformance' in sys.modules)\n"
    )
    out = subprocess.run(  # noqa: S603  # our own interpreter, fixed code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.split() == ["False", "True"]


def test_no_remote_adapter_offers_the_suite():
    # Grading stays on the caller's own machine: no HTTP route, sidecar method or MCP tool.
    openapi = json.loads((ROOT / "src/pydiggs/server/openapi.json").read_text("utf-8"))
    assert not [path for path in openapi["paths"] if "conformance" in path]
    openrpc = json.loads((ROOT / "src/pydiggs/sidecar/openrpc.json").read_text("utf-8"))
    assert not [m["name"] for m in openrpc["methods"] if "conformance" in m["name"]]
    for package in ("server", "sidecar", "mcp_server"):
        for path in (ROOT / "src" / "pydiggs" / package).glob("*.py"):
            assert "conformance" not in path.read_text("utf-8"), path
    assert _service._AnyRequest.__args__ == (  # a pool worker is never handed one
        _service.Request,
        _service.ReadRequest,
        _service.ConvertRequest,
        _service.Ags4CheckRequest,
        _service.DiffRequest,
        _service.TablesRequest,
        _service.TablesFromDiggsRequest,
    )


def test_the_capabilities_entries_name_what_the_functions_take():
    by_id = {op.id: op for op in pydiggs.capabilities().operations}
    suite = [op_id for op_id in by_id if op_id.startswith("conformance.")]
    assert suite == [
        "conformance.classes",
        "conformance.new",
        "conformance.grade",
        "conformance.verify",
    ]
    assert {by_id[op_id].stability for op_id in suite} == {"preview"}
    assert all(by_id[op_id].available and not by_id[op_id].needs for op_id in suite)
    new = by_id["conformance.new"]
    assert new.options["class_id"].choices == tuple(c.id for c in conformance.classes())
    assert new.outputs == ("diggs-conformance-challenge",)
    evidence = f"diggs-conformance-{conformance.CONFORMANCE_SCHEMA_VERSION}"
    assert by_id["conformance.grade"].outputs == (evidence,) == ("diggs-conformance-1.0-beta.3",)
    assert by_id["conformance.grade"].inputs == ("diggs-3.0.0", "text/csv")
    assert by_id["conformance.verify"].options["evidence"].required
