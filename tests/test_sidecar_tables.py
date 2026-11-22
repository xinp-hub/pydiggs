"""The sidecar's protocol 1.4 ``tables.*`` methods, in-process (preview).

A host on .NET, Java or any language sends a package folder by path (or a package ZIP
inline) and gets DIGGS back, or sends DIGGS and gets a package ZIP back, with no Python
code of its own. The answers are the service's, byte for byte.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _service, tables
from pydiggs._serve_cli import ServeOptions
from pydiggs.tables import _io, _package
from tests.test_sidecar_loop import Session
from tests.test_tables_surfaces import FILES

ROOT = Path(__file__).resolve().parent.parent
AEROMAG = ROOT / "tests" / "fixtures" / "official" / "3.0" / "Aeromag.xml"


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(_service, "warm_worker", lambda versions=None: None)
    sessions: list[Session] = []

    def make(**kwargs) -> Session:
        s = Session(**kwargs)
        sessions.append(s)
        return s

    yield make
    for s in sessions:
        s.close()


@pytest.fixture
def root(tmp_path):
    base = Path(os.path.realpath(tmp_path))
    (base / "root" / "site").mkdir(parents=True)
    (base / "outside").mkdir()
    for name, data in FILES.items():
        (base / "root" / "site" / name).write_bytes(data)
    (base / "outside" / "secret.csv").write_bytes(b"key\nSECRET\n")
    return base / "root"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _slug(reply: dict) -> str:
    return reply["error"]["data"]["code"]


def _initialize(s: Session, version: str = "1.4") -> dict:
    return s.call("initialize", {"protocol_version": version}, id="init")["result"]


def test_protocol_1_4_advertises_the_tables_methods(session):
    result = _initialize(session())
    assert result["protocol_version"] == "1.4"
    assert result["tables_profile_version"] == tables.PROFILE_VERSION
    assert result["capabilities"]["tables"] == {
        "content": True,
        "path": False,
        "methods": ["tables.check", "tables.from_diggs", "tables.round_trip", "tables.to_diggs"],
        "profile_version": tables.PROFILE_VERSION,
    }
    assert result["capabilities"]["diff"]["content"] is True  # 1.3's diff is still there


@pytest.mark.parametrize("version", ["1.0", "1.3"])
def test_older_sessions_do_not_have_the_tables_methods(session, version):
    s = session()
    result = _initialize(s, version)
    assert "tables" not in result["capabilities"]
    assert "tables_profile_version" not in result
    package = _b64(_package.pack(list(FILES.items())))
    reply = s.call("tables.to_diggs", {"package_base64": package})
    assert _slug(reply) == "method-not-found"
    reply = s.call("tables.from_diggs", {"content_base64": _b64(AEROMAG.read_bytes())}, id=2)
    assert _slug(reply) == "method-not-found"


def test_a_package_inline_gives_the_service_answer(session):
    s = session()
    _initialize(s)
    package = _package.pack(list(FILES.items()))
    for method, operation in (
        ("tables.to_diggs", "to-diggs"),
        ("tables.check", "check"),
        ("tables.round_trip", "round-trip"),
    ):
        reply = s.call(method, {"package_base64": _b64(package), "name": "site.zip"})
        expected = _service.tables(
            _service.TablesRequest(content=package, name="site.zip", operation=operation)
        )
        assert expected.document is not None
        assert reply["result"] == json.loads(expected.document), method


def test_a_folder_by_path_is_the_same_package_as_its_zip(session, root):
    s = session(roots=(root,))
    result = _initialize(s)
    assert result["capabilities"]["tables"]["path"] is True
    (root / "site" / "README.txt").write_text("not read")  # other files are ignored
    (root / "site" / "nested").mkdir()
    by_path = s.call("tables.to_diggs", {"path": str(root / "site")})["result"]
    inline = s.call(
        "tables.to_diggs",
        {"package_base64": _b64(_package.pack(list(FILES.items()))), "name": "site"},
        id=2,
    )["result"]
    assert by_path == inline
    assert by_path["output_name"] == "site.xml"
    rows = {n: list(r) for n, r in tables.read_package(root / "site").items()}
    expected = pydiggs.write_bytes(tables.to_model(rows).model)
    assert base64.b64decode(by_path["output_base64"]) == expected
    assert str(root) not in json.dumps(by_path)


def test_from_diggs_gives_the_package_as_a_zip(session, root):
    s = session(roots=(root,))
    _initialize(s)
    (root / "aero.xml").write_bytes(AEROMAG.read_bytes())
    by_path = s.call("tables.from_diggs", {"path": str(root / "aero.xml")})["result"]
    inline = s.call(
        "tables.from_diggs",
        {"content_base64": _b64(AEROMAG.read_bytes()), "name": "aero.xml"},
        id=2,
    )["result"]
    assert by_path == inline
    made = tables.from_model(pydiggs.read_bytes(AEROMAG.read_bytes(), name="aero.xml"))
    assert base64.b64decode(by_path["output_base64"]) == _package.pack(_io.package_files(made))
    assert by_path["output_name"] == "aero.zip"


def test_paths_outside_a_root_or_through_a_link_are_refused(session, root):
    s = session(roots=(root,))
    _initialize(s)
    outside = s.call("tables.to_diggs", {"path": str(root.parent / "outside")})
    assert _slug(outside) == "path-not-permitted"
    (root / "linked").symlink_to(root.parent / "outside", target_is_directory=True)
    linked = s.call("tables.check", {"path": str(root / "linked")}, id=2)
    assert _slug(linked) == "path-not-permitted"
    (root / "site" / "features.csv").unlink()
    (root / "site" / "features.csv").symlink_to(root.parent / "outside" / "secret.csv")
    through = s.call("tables.to_diggs", {"path": str(root / "site")}, id=3)
    assert _slug(through) == "path-not-permitted"
    relative = s.call("tables.to_diggs", {"path": "site"}, id=4)
    assert _slug(relative) == "path-not-permitted"
    a_file = s.call("tables.to_diggs", {"path": str(root / "site" / "projects.csv")}, id=5)
    assert _slug(a_file) == "path-not-permitted"
    for reply in (outside, linked, through, a_file):
        assert str(root.parent) not in json.dumps(reply)


def test_without_allow_path_a_folder_is_refused(session, root):
    s = session()
    _initialize(s)
    assert _slug(s.call("tables.to_diggs", {"path": str(root / "site")})) == "path-not-permitted"


def test_a_folder_is_held_to_max_path_bytes_in_total(session, root):
    total = sum(len(data) for data in FILES.values())
    s = session(options=ServeOptions(max_path_bytes=total - 1), roots=(root,))
    _initialize(s)
    reply = s.call("tables.to_diggs", {"path": str(root / "site")})
    assert _slug(reply) == "input-too-large"


def test_a_folder_with_too_many_package_files_is_refused(session, root, monkeypatch):
    monkeypatch.setattr(_service, "TABLES_MAX_FILES", 2)
    s = session(roots=(root,))
    _initialize(s)
    reply = s.call("tables.to_diggs", {"path": str(root / "site")})
    assert _slug(reply) == "input-too-large"


def test_hostile_or_unusable_packages_are_errors(session):
    s = session()
    _initialize(s)
    cases = {
        "not-zip": _b64(b"not a zip"),
        "unsafe-package": _b64(_package.pack([("features.csv", b"k\n"), ("run.exe", b"MZ")])),
        "not-tables": _b64(_package.pack([("boreholes.csv", b"key\nBH-1\n")])),
    }
    for n, (kind, package) in enumerate(cases.items()):
        reply = s.call("tables.to_diggs", {"package_base64": package}, id=n)
        assert _slug(reply) == "invalid-document"
        assert reply["error"]["data"]["kind"] == kind
        assert "boreholes" not in json.dumps(reply)
    bomb = _package.pack([("features.csv", b"0" * (8 << 20))])
    small = session(options=ServeOptions(max_bytes=1 << 20))
    _initialize(small)
    assert _slug(small.call("tables.to_diggs", {"package_base64": _b64(bomb)})) == (
        "input-too-large"
    )


def test_parameters_are_checked(session):
    s = session()
    _initialize(s)
    both = s.call("tables.to_diggs", {"package_base64": "", "path": "/x"})
    assert _slug(both) == "invalid-params"
    neither = s.call("tables.check", {}, id=2)
    assert _slug(neither) == "invalid-params"
    unknown = s.call("tables.to_diggs", {"package_base64": "", "time_budget_s": 5}, id=3)
    assert unknown["error"]["data"]["errors"] == [
        {"param": "time_budget_s", "problem": "unknown parameter"}
    ]
    bad = s.call("tables.round_trip", {"package_base64": "%%%"}, id=4)
    assert _slug(bad) == "invalid-params"
    budget = s.call(
        "tables.from_diggs", {"content_base64": _b64(b"<x/>"), "time_budget_s": 0}, id=5
    )
    assert _slug(budget) == "invalid-params"
