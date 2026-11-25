"""The in-browser conformance page (``docs/conformance/try/``), without a browser.

The page's worker runs ``conformance_page.py`` in Pyodide. Here the same functions run
under CPython: what they return, that the page's evidence is the command line's, and how
they treat hostile files. The pins of the worker and the page's own promises are checked
as text. ``js/e2e/conformance/`` runs the page itself in three browsers (CI).
"""

from __future__ import annotations

import ast
import base64
import doctest
import hashlib
import importlib.util
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import pydiggs
from pydiggs import conformance

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "conformance" / "try"
pytestmark = pytest.mark.skipif(not PAGE.is_dir(), reason="docs/ is not in the sdist")


@pytest.fixture(scope="module")
def page() -> ModuleType:
    spec = importlib.util.spec_from_file_location("conformance_page", PAGE / "conformance_page.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # No __pycache__ next to the page: the docs build would publish it.
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module


def _ok(text: str) -> dict[str, Any]:
    body = json.loads(text)
    assert body["ok"] is True, body
    return dict(body)


def _refused(text: str) -> str:
    body = json.loads(text)
    assert body["ok"] is False, body
    return str(body["error"])


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


EXPORTS = [c.id for c in conformance.classes() if c.direction == "export"]
IMPORTS = [c.id for c in conformance.classes() if c.direction == "import"]


# --- What each step returns -----------------------------------------------------------


def test_the_docstring_examples_run(page: ModuleType) -> None:
    failed, tried = doctest.testmod(page, raise_on_error=False)
    assert tried > 0
    assert failed == 0


def test_the_catalogue_lists_every_class(page: ModuleType) -> None:
    listing = _ok(page.catalogue())
    assert [c["id"] for c in listing["classes"]] == [c.id for c in conformance.classes()]
    assert listing["suite_version"] == conformance.SUITE_VERSION
    assert listing["pydiggs"] == pydiggs.__version__
    assert listing["statement"] == conformance.STATEMENT
    assert listing["untestable"]  # the parts of DIGGS the suite cannot test yet


@pytest.mark.parametrize("class_id", [*EXPORTS, *IMPORTS])
def test_make_gives_the_folder_the_command_line_writes(page: ModuleType, class_id: str) -> None:
    made = _ok(page.make(class_id, "MyLog", "4.2.1", "page"))
    expected = conformance.new(class_id, product="MyLog", version="4.2.1", seed="page")
    folder = f"{class_id}-page"
    assert made["folder"] == folder
    assert made["id"] == expected.id
    assert made["token"] == expected.token
    assert made["files"] == sorted(expected.files)
    assert made["manifest"].encode("utf-8") == expected.files["challenge.json"]
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(made["zip"])))
    assert {i.filename: archive.read(i) for i in archive.infolist()} == {
        f"{folder}/{name}": data for name, data in expected.files.items()
    }
    # The same seed gives the same ZIP, byte for byte.
    assert _ok(page.make(class_id, "MyLog", "4.2.1", "page"))["zip"] == made["zip"]


def test_make_draws_a_fresh_seed_when_none_is_given(page: ModuleType) -> None:
    first = _ok(page.make("cpt-export", "MyLog", "1", ""))
    second = _ok(page.make("cpt-export", "MyLog", "1", ""))
    assert first["seed"] != second["seed"]


@pytest.mark.parametrize(
    ("given", "error"),
    [
        (("no-such-class", "P", "1", ""), "no conformance class"),
        (("spt-export", "  ", "1", ""), "product must be"),
        (("spt-export", "P", "", ""), "version must be"),
        (("spt-export", "P", "1", "../x"), "seed must be"),
    ],
)
def test_make_refuses_what_the_command_line_refuses(
    page: ModuleType, given: tuple[str, str, str, str], error: str
) -> None:
    assert error in _refused(page.make(*given))


@pytest.mark.parametrize("class_id", EXPORTS)
@pytest.mark.parametrize("given", ["manifest", "zip"])
def test_grade_gives_the_evidence_the_command_line_gives(
    page: ModuleType, class_id: str, given: str, tmp_path: Path
) -> None:
    made = _ok(page.make(class_id, "MyLog", "4.2.1", "page"))
    challenge = made["manifest"].encode() if given == "manifest" else base64.b64decode(made["zip"])
    xml = base64.b64decode(_ok(page.reference(challenge))["data"])
    graded = _ok(page.grade(challenge, xml, "my-export.xml"))
    assert graded["passed"] is True
    # What `pydiggs conformance grade` writes for the same files.
    folder = conformance.new(class_id, product="MyLog", version="4.2.1", seed="page").write(
        tmp_path / "challenge"
    )
    (tmp_path / "my-export.xml").write_bytes(xml)
    result = conformance.grade(folder, tmp_path / "my-export.xml")
    assert graded["evidence"] == result.to_dict()
    assert graded["evidence_json"] == result.to_json() + "\n"
    assert graded["report_html"] == result.to_html()
    # ... and the command line's verify confirms the page's evidence.
    assert conformance.verify(json.loads(graded["evidence_json"]), folder, xml).ok


@pytest.mark.parametrize("class_id", IMPORTS)
def test_grade_takes_answers_for_an_import_class(page: ModuleType, class_id: str) -> None:
    made = _ok(page.make(class_id, "MyLog", "1", "page"))
    graded = _ok(page.grade(made["manifest"].encode(), b"question_id,answer\r\n", "answers.csv"))
    evidence = graded["evidence"]
    assert graded["passed"] is False
    assert [s["id"] for s in evidence["stages"]] == ["core", "values", "robustness"]
    checks = {i["check"] for s in evidence["stages"] for i in s["items"]}
    assert checks == {"conformance.answer-missing"}


def test_the_reference_is_the_challenges_own(page: ModuleType) -> None:
    made = _ok(page.make("spt-export", "P", "1", "page"))
    reference = _ok(page.reference(made["manifest"].encode()))
    assert reference["name"] == "spt-export-page-pydiggs.xml"
    expected = conformance.new("spt-export", product="P", version="1", seed="page").reference()
    assert base64.b64decode(reference["data"]) == expected


def test_verify_reproduces_and_catches_a_tampered_document(page: ModuleType) -> None:
    made = _ok(page.make("cpt-export", "P", "1", "page"))
    zipped = base64.b64decode(made["zip"])
    xml = base64.b64decode(_ok(page.reference(zipped))["data"])
    evidence = _ok(page.grade(zipped, xml, "x.xml"))["evidence_json"]
    checked = _ok(page.verify(evidence.encode(), zipped, xml, "renamed.xml"))
    assert checked == {
        "ok": True,
        "reproduced": True,
        "differences": [],
        "notes": [],
        "verdict": "pass",
        "class_id": "cpt-export",
    }
    tampered = json.loads(evidence) | {"verdict": "fail"}
    again = _ok(page.verify(json.dumps(tampered).encode(), zipped, xml, "x.xml"))
    assert again["reproduced"] is False
    assert again["differences"] == ["verdict"]
    # Another file: the digest and the verdict differ.
    other = _ok(page.verify(evidence.encode(), zipped, b"<x/>", "x.xml"))
    assert "submission" in other["differences"]


@pytest.mark.parametrize(
    ("evidence", "error"),
    [
        (b"not json", "not JSON"),
        (b"[1, 2]", "not a diggs-conformance document"),
        (b'{"format": "something-else"}', "not a diggs-conformance document"),
        (b"\xff\xfe", "not JSON"),
    ],
)
def test_verify_refuses_what_is_not_evidence(page: ModuleType, evidence: bytes, error: str) -> None:
    made = _ok(page.make("cpt-export", "P", "1", "page"))
    assert error in _refused(page.verify(evidence, made["manifest"].encode(), b"<x/>", "x.xml"))


# --- Hostile files --------------------------------------------------------------------


def _manifest() -> bytes:
    return conformance.new("spt-export", product="P", version="1", seed="page").files[
        "challenge.json"
    ]


@pytest.mark.parametrize(
    ("challenge", "error"),
    [
        (b"{}", "challenge.json is not a challenge manifest"),
        (b"[]", "challenge.json is not a challenge manifest"),
        (b"x" * (64 * 1024 + 1), "at most 64 KiB"),
        (b"PK\x03\x04 broken", "the ZIP cannot be read"),
        (_zip({"a/README.md": b"x"}), "exactly one challenge.json"),
        (_zip({"a/challenge.json": b"{}", "b/challenge.json": b"{}"}), "exactly one"),
        (_zip({"a/challenge.json": b" " * (64 * 1024 + 1)}), "too large"),
        (_zip({f"f{i}": b"" for i in range(201)}), "too many files"),
    ],
)
def test_a_challenge_that_is_not_one_is_refused(
    page: ModuleType, challenge: bytes, error: str
) -> None:
    message = _refused(page.grade(challenge, b"<x/>", "x.xml"))
    assert error in message
    # The private temporary folder is never named to the visitor.
    assert "tmp" not in message.lower()


def test_another_suite_versions_challenge_says_so(page: ModuleType) -> None:
    manifest = json.loads(_manifest()) | {"suite_version": "0.1"}
    message = _refused(page.grade(json.dumps(manifest).encode(), b"<x/>", "x.xml"))
    assert "suite" in message
    assert "0.1" in message


@pytest.mark.parametrize(
    "class_id", ["spt-export", "cpt-export", "lab-index-export", "deep-foundations-export"]
)
@pytest.mark.parametrize("given", ["manifest", "zip"])
def test_a_challenge_of_the_earlier_suite_is_still_graded(
    page: ModuleType, class_id: str, given: str
) -> None:
    # The page ran pyDIGGS 1.17 before: a challenge downloaded from it then is of suite
    # 1.0-beta.2, and is graded and verified as that suite graded it.
    from pydiggs.conformance import _challenge

    assert "1.0-beta.2" in conformance.GRADED_SUITES
    info = conformance.class_info(class_id)
    earlier = _challenge._make(info, "MyLog", "4.2.1", "page", "1.0-beta.2")
    assert earlier.token != conformance.new(class_id, product="P", version="1", seed="page").token
    folder = _zip({f"{class_id}-page/{name}": data for name, data in earlier.files.items()})
    challenge = earlier.files["challenge.json"] if given == "manifest" else folder
    exported = base64.b64decode(_ok(page.reference(challenge))["data"])
    assert exported == earlier.reference()
    graded = _ok(page.grade(challenge, exported, "export.xml"))
    assert graded["passed"] is True
    assert graded["evidence"]["suite_version"] == "1.0-beta.2"
    assert graded["evidence"]["challenge"]["token"] == earlier.token
    outcome = _ok(page.verify(graded["evidence_json"].encode(), challenge, exported, "export.xml"))
    assert outcome["reproduced"] is True
    assert outcome["verdict"] == "pass"


def test_a_pairwise_challenge_is_sent_to_the_command_line(page: ModuleType) -> None:
    # Its challenge.xml is another product's export, which the page does not take; the
    # refusal says where such a challenge is graded, whichever step it is given to.
    source = conformance.new("spt-export", product="A", version="1", seed="page")
    exported = source.reference()
    evidence = conformance.grade(source, exported).to_json().encode()
    pair = conformance.new(
        "spt-import", product="B", version="2", from_export=evidence, export=exported
    )
    folder = _zip({f"pair/{name}": data for name, data in pair.files.items()})
    answers = b"question_id,answer\r\n"
    for given in (pair.files["challenge.json"], folder):
        for message in (
            _refused(page.grade(given, answers, "answers.csv")),
            _refused(page.verify(b"{}", given, answers, "answers.csv")),
            _refused(page.reference(given)),
        ):
            assert "pairwise challenge" in message
            assert "command line" in message
            assert "tmp" not in message.lower()


def test_a_compressed_member_is_read_within_its_limit(page: ModuleType) -> None:
    # A member that claims a small size but inflates to more is cut at the limit.
    bomb = _zip({"c/challenge.json": _manifest() + b" " * (1024 * 1024)})
    assert "too large" in _refused(page.grade(bomb, b"<x/>", "x.xml"))


def test_a_file_over_the_limit_is_refused(
    page: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(page, "MAX_BYTES", 10)
    assert "larger than 10 bytes" in _refused(page.grade(_manifest(), b"x" * 11, "x.xml"))


@pytest.mark.parametrize(
    ("name", "recorded"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\export.xml", "export.xml"),
        ("challenge", "challenge"),
        ("", "submission"),
        ("..", "submission"),
        ("<img src=x>.xml", "_img src_x_.xml"),
    ],
)
def test_a_file_name_only_names_the_file(page: ModuleType, name: str, recorded: str) -> None:
    graded = _ok(page.grade(_manifest(), b"<x/>", name))
    assert graded["evidence"]["submission"]["name"] == recorded
    assert graded["passed"] is False


def test_a_typed_array_proxy_is_read_as_bytes(
    page: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class JsProxy:  # what a JavaScript Uint8Array is in Pyodide
        def __init__(self, data: bytes) -> None:
            self.data = data

        def to_bytes(self) -> bytes:
            return self.data

    ffi = ModuleType("pyodide.ffi")
    ffi.JsProxy = JsProxy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodide", ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    graded = _ok(page.grade(JsProxy(_manifest()), JsProxy(b"<x/>"), "x.xml"))
    assert graded["evidence"]["submission"]["sha256"] == hashlib.sha256(b"<x/>").hexdigest()
    assert _ok(page.grade(bytearray(_manifest()), memoryview(b"<x/>"), "x.xml"))
    assert "bytes of a file" in _refused(page.grade(_manifest(), 42, "x.xml"))


def test_the_glue_uses_only_the_public_api() -> None:
    # The page pins a released wheel: a private name could be gone or changed in it.
    tree = ast.parse((PAGE / "conformance_page.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pydiggs"):
            assert not any(part.startswith("_") for part in node.module.split("."))
            assert not any(alias.name.startswith("_") for alias in node.names)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"pydiggs", "conformance"}
        ):
            assert not node.attr.startswith("_") or node.attr == "__version__"
        if isinstance(node, ast.Import):
            assert not any(alias.name in {"socket", "urllib", "http"} for alias in node.names)


# --- The page's pins and promises -----------------------------------------------------

WORKER = (PAGE / "worker.mjs").read_text(encoding="utf-8") if PAGE.is_dir() else ""
FACTS = json.loads((ROOT / "tests" / "pyodide" / "pyodide-facts.json").read_text("utf-8"))


def _const(source: str, name: str) -> str:
    match = re.search(rf'^const {name} = "([^"]+)";$', source, re.MULTILINE)
    assert match, f"{name} is not a plain string constant"
    return match.group(1)


def test_the_worker_loads_the_tested_pyodide_with_the_engines_sri() -> None:
    assert _const(WORKER, "PYODIDE_VERSION") == FACTS["pyodide"]
    example = (ROOT / "examples" / "browser-worker" / "worker.mjs").read_text(encoding="utf-8")
    assert _const(WORKER, "PYODIDE_SRI") == _const(example, "PYODIDE_SRI")
    assert _const(WORKER, "PYODIDE_SRI").startswith("sha384-")
    base = "const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;"
    assert base in WORKER


def test_the_worker_pins_one_released_wheel_by_digest() -> None:
    version = _const(WORKER, "PYDIGGS_VERSION")
    url = re.search(r'url: "(https://files\.pythonhosted\.org/packages/[^"]+)"', WORKER)
    sha256 = re.search(r'sha256: "([0-9a-f]{64})"', WORKER)
    assert url and sha256
    assert url.group(1).endswith(f"/pydiggs-{version}-py3-none-any.whl")
    # The page runs a release that exists, never one ahead of this checkout.
    released = tuple(int(p) for p in version.split("."))
    here = tuple(int(p) for p in re.findall(r"\d+", pydiggs.__version__)[:3])
    assert released <= here
    # The page says which pyDIGGS it runs.
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    assert f"<span data-pydiggs-version>{version}</span>" in html
    assert f"pyDIGGS {version}." in html
    assert f"Pyodide {FACTS['pyodide']}," in html
    if version == pydiggs.__version__:
        assert sha256.group(1) == "9a352a1180e38b2f7843b35e9819cce7ad335915db2daa73292ae7c42028b243"


def test_the_page_says_what_it_is_and_connects_nowhere() -> None:
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    policy = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html)
    assert policy
    directives = dict(d.strip().split(" ", 1) for d in policy.group(1).split(";"))
    assert directives["connect-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["default-src"] == "'none'"
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    assert "never uploaded" in text
    assert "self-tested" in text
    assert "not a certification" in text
    assert 'role="status" aria-live="polite"' in html


def test_every_control_has_a_label() -> None:
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    labelled = set(re.findall(r'<label for="([^"]+)"', html))
    controls = set(re.findall(r'<(?:input|select|textarea)\b[^>]*\bid="([^"]+)"', html))
    assert controls
    assert controls <= labelled


def test_the_scripts_never_parse_strings_as_html() -> None:
    for path in PAGE.glob("*.mjs"):
        code = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert sink not in code, f"{path.name} uses {sink}"


def test_the_e2e_server_serves_every_file_of_the_page() -> None:
    server = (ROOT / "js" / "e2e" / "conformance" / "serve.mjs").read_text(encoding="utf-8")
    served = set(re.findall(r'^  "([^"]+)": "', server, re.MULTILINE))
    assert served == {p.name for p in PAGE.iterdir()}
