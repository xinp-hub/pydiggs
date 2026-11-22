"""The tables profile off Python: package ZIPs, Arrow input and the service (preview).

The sidecar and the HTTP API reach ``pydiggs.tables`` only through ``pydiggs._service``;
these tests hold the service to the Python API byte for byte, and the package ZIP reader
to its limits with hostile archives. ``test_sidecar_tables.py`` and
``test_server_tables.py`` drive the two surfaces.
"""

from __future__ import annotations

import base64
import doctest
import hashlib
import io
import json
import pickle
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _service, tables
from pydiggs._service import Code, TablesFromDiggsRequest, TablesRequest
from pydiggs.errors import TablesError
from pydiggs.tables import _arrow, _io, _package

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official" / "3.0"

#: A small export: a project, a borehole with a CPT-free SPT test and its result.
FILES: dict[str, bytes] = {
    "document.csv": b"creation_date\n2026-11-21\n",
    "projects.csv": b"key,name\nP1,Riverside\n",
    "features.csv": (
        b"key,name,project,x,y,crs,total_depth,depth_uom\n"
        b"BH-1,BH-1,P1,1200.5,880.25,http://www.opengis.net/def/crs/EPSG/0/27700,40,ft\n"
    ),
    "measurements.csv": (b"key,feature,procedure,depth\nSPT-1,BH-1,DrivenPenetrationTest,5\n"),
    "params.csv": b"node,scope,name,value\nSPT-1,procedure,penetrationTestType,SPT\n",
    "results_DrivenPenetrationTest.csv": b"measurement,n_value\nSPT-1,14\n",
}


def _zip(entries: dict[str, bytes] | list[tuple[str, bytes]]) -> bytes:
    return _package.pack(list(entries.items()) if isinstance(entries, dict) else entries)


def _doc(outcome: _service.Outcome) -> dict:
    assert outcome.ok, (outcome.code, outcome.detail)
    assert outcome.document is not None
    return json.loads(outcome.document)


def _folder(tmp_path: Path, files: dict[str, bytes] = FILES) -> Path:
    folder = tmp_path / "pkg"
    folder.mkdir()
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return folder


# --- the package ZIP ------------------------------------------------------------------------


def test_the_service_and_the_package_agree_on_the_file_limit():
    assert _service.TABLES_MAX_FILES == _package.MAX_FILES


def test_pack_is_reproducible_and_unpack_reads_it_back():
    archive = _zip(FILES)
    assert archive == _zip(FILES)
    assert _package.unpack(archive, max_unpacked_bytes=1 << 20) == FILES
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        infos = opened.infolist()
    assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}
    assert {i.create_system for i in infos} == {3}


def test_one_wrapping_folder_and_metadata_are_skipped():
    entries = [
        ("pkg/", b""),
        ("pkg/features.csv", FILES["features.csv"]),
        ("pkg/.DS_Store", b"junk"),
        ("__MACOSX/pkg/._features.csv", b"junk"),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    files = _package.unpack(buffer.getvalue(), max_unpacked_bytes=1 << 20)
    assert files == {"features.csv": FILES["features.csv"]}


def _raw_zip(*infos: tuple[zipfile.ZipInfo | str, bytes], **kwargs) -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns of a duplicate name
        with zipfile.ZipFile(buffer, "w", **kwargs) as archive:
            for info, data in infos:
                archive.writestr(info, data)
    return buffer.getvalue()


def _link(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _encrypted(data: bytes) -> bytes:
    """``data`` with the encrypted flag set on every entry (zipfile cannot write one)."""
    out = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = out.find(signature)
        while at != -1:
            out[at + offset] |= 0x1
            at = out.find(signature, at + 4)
    return bytes(out)


@pytest.mark.parametrize(
    ("archive", "kind"),
    [
        pytest.param(b"not a zip at all", "not-zip", id="not-a-zip"),
        pytest.param(_zip(FILES)[:-40], "not-zip", id="truncated"),
        pytest.param(_raw_zip(("/etc/features.csv", b"key\n")), "unsafe-package", id="absolute"),
        pytest.param(_raw_zip(("../features.csv", b"key\n")), "unsafe-package", id="dot-dot"),
        pytest.param(
            _raw_zip(("pkg/../../features.csv", b"key\n")), "unsafe-package", id="dot-dot-inside"
        ),
        pytest.param(_raw_zip(("a\\features.csv", b"key\n")), "unsafe-package", id="backslash"),
        pytest.param(_raw_zip(("C:features.csv", b"key\n")), "unsafe-package", id="drive"),
        pytest.param(
            _raw_zip(("features.csv", b"k\n"), ("sub/x.csv", b"k\n")),
            "unsafe-package",
            id="nested-folder",
        ),
        pytest.param(_raw_zip(("run.exe", b"MZ")), "unsafe-package", id="not-csv-or-json"),
        pytest.param(
            _raw_zip((_link("features.csv"), b"/etc/passwd")), "unsafe-package", id="link"
        ),
        pytest.param(
            _encrypted(_raw_zip(("features.csv", b"k\n"))), "unsafe-package", id="encrypted"
        ),
        pytest.param(
            _raw_zip(("features.csv", b"k\n"), ("features.csv", b"k\n")),
            "unsafe-package",
            id="listed-twice",
        ),
        pytest.param(
            _raw_zip(("features.csv", b"k\n"), compression=zipfile.ZIP_BZIP2),
            "unsafe-package",
            id="bzip2",
        ),
    ],
)
def test_a_hostile_zip_is_refused_before_anything_is_parsed(archive, kind):
    with pytest.raises(_package.PackageError) as caught:
        _package.unpack(archive, max_unpacked_bytes=1 << 20)
    assert caught.value.kind == kind
    # The text is fixed: it never names the entry.
    assert "etc" not in str(caught.value) and "exe" not in str(caught.value)


def test_a_zip_bomb_is_stopped_while_it_decompresses():
    bomb = _raw_zip(("features.csv", b"0" * (8 << 20)), compression=zipfile.ZIP_DEFLATED)
    assert len(bomb) < 64 << 10
    with pytest.raises(_package.PackageError) as caught:
        _package.unpack(bomb, max_unpacked_bytes=1 << 20)
    assert caught.value.kind == "too-large"


def test_too_many_files_or_entries_are_refused():
    many = _raw_zip(*((f"results_T{n}.csv", b"measurement\n") for n in range(257)))
    with pytest.raises(_package.PackageError, match="more than 256 files"):
        _package.unpack(many, max_unpacked_bytes=1 << 20)
    folders = _raw_zip(*((f"d{n}/", b"") for n in range(1025)))
    with pytest.raises(_package.PackageError, match="more than 256 files"):
        _package.unpack(folders, max_unpacked_bytes=1 << 20)


def test_a_zip_that_claims_too_many_entries_is_refused_before_they_are_read():
    archive = bytearray(_zip({"features.csv": b"key\n"}))
    at = archive.rfind(b"PK\x05\x06")
    archive[at + 8 : at + 12] = b"\xff\xff\xff\xff"  # ZIP64-sized counts
    with pytest.raises(_package.PackageError) as caught:
        _package.unpack(bytes(archive), max_unpacked_bytes=1 << 20)
    assert caught.value.kind == "too-large"


def test_the_digest_names_the_files_not_the_container():
    assert _package.digest(FILES) == _package.digest(dict(reversed(FILES.items())))
    assert _package.digest(FILES) != _package.digest({**FILES, "projects.csv": b"key\nP2\n"})


# --- tables from files in memory --------------------------------------------------------------


def test_files_in_memory_read_as_a_folder_reads(tmp_path):
    folder = _folder(tmp_path)
    from_folder = {k: list(v) for k, v in tables.read_package(folder).items()}
    from_files = {k: list(v) for k, v in _io.tables_from_files(FILES).items()}
    assert from_files == from_folder


def test_files_in_memory_follow_the_descriptor(tmp_path):
    made = tables.from_model(
        pydiggs.read_bytes(
            pydiggs.write_bytes(
                tables.to_model({n: list(r) for n, r in _io.tables_from_files(FILES).items()}).model
            )
        )
    )
    files = dict(_io.package_files(made))
    assert "datapackage.json" in files and "plan.json" in files
    found = _io.tables_from_files(files)
    assert set(found) == set(made.tables)
    # A resource whose file is missing, or a table the profile lacks, is TablesError.
    with pytest.raises(TablesError, match="has no file"):
        _io.tables_from_files({"datapackage.json": files["datapackage.json"]})
    with pytest.raises(TablesError, match="not a table of the profile"):
        _io.tables_from_files({"boreholes.csv": b"key\nBH-1\n"})
    with pytest.raises(TablesError, match="not a data package descriptor"):
        _io.tables_from_files({"datapackage.json": b"[1, 2]"})


def test_package_files_are_the_bytes_write_package_writes(tmp_path):
    made = tables.from_model(pydiggs.read_bytes((OFFICIAL / "Aeromag.xml").read_bytes()))
    written = tables.write_package(made, tmp_path)
    assert [(p.name, p.read_bytes()) for p in written] == _io.package_files(made)


# --- Arrow input ----------------------------------------------------------------------------------


class _NoArrowLibrary:
    """An Arrow stream whose reader is never reached (pyarrow 'missing')."""

    def __arrow_c_stream__(self, requested_schema=None):  # pragma: no cover - never called
        raise AssertionError("not read")


class _NotAStream:
    """Claims the Arrow stream interface but hands back no capsule."""

    def __arrow_c_stream__(self, requested_schema=None):
        return "not a capsule"


def test_an_arrow_stream_without_pyarrow_names_the_extra(monkeypatch):
    real = _arrow.importlib.import_module

    def missing(name, *args):
        if name == "pyarrow":
            raise ImportError(name)
        return real(name, *args)

    monkeypatch.setattr(_arrow.importlib, "import_module", missing)
    with pytest.raises(TablesError, match=r"pydiggs\[gis\]"):
        tables.to_model({"features": _NoArrowLibrary()})


def test_arrow_tables_give_the_same_file_as_rows():
    pa = pytest.importorskip("pyarrow")
    rows = {n: list(r) for n, r in _io.tables_from_files(FILES).items()}
    arrow = {n: pa.Table.from_pylist(r) for n, r in rows.items()}
    expected = pydiggs.write_bytes(tables.to_model(rows).model)
    assert pydiggs.write_bytes(tables.to_model(arrow).model) == expected
    assert tables.check(arrow) == tables.check(rows)
    assert tables.round_trip(arrow).diggs == expected


def test_arrow_is_read_in_batches_and_dates_become_iso_text():
    pa = pytest.importorskip("pyarrow")
    import datetime as dt

    schema = pa.schema([("key", pa.string()), ("depth_uom", pa.string())])
    batches = [pa.record_batch([[f"BH-{n}"], ["m"]], schema=schema) for n in range(3)]
    reader = pa.RecordBatchReader.from_batches(schema, iter(batches))
    rows = _arrow.rows_of(reader, "features")
    first = next(iter(rows))  # one batch read, not all of them
    assert first == {"key": "BH-0", "depth_uom": "m"}
    dated = pa.table({"creation_date": [dt.date(2026, 11, 21)]})
    assert list(_arrow.rows_of(dated, "document")) == [{"creation_date": "2026-11-21"}]
    with pytest.raises(TablesError, match="not a readable Arrow stream"):
        list(_arrow.rows_of(_NotAStream(), "features"))


def test_polars_frames_are_arrow_streams():
    pl = pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    frame = pl.DataFrame({"key": ["BH-1"], "name": ["BH-1"], "depth_uom": ["m"]})
    assert _arrow.is_arrow(frame)
    assert [f.key for f in tables.to_model({"features": frame}).model.features] == ["BH-1"]


# --- the service ----------------------------------------------------------------------------------


def test_to_diggs_gives_the_python_bytes_from_a_zip_or_from_files():
    expected = pydiggs.write_bytes(
        tables.to_model({n: list(r) for n, r in _io.tables_from_files(FILES).items()}).model
    )
    by_zip = _doc(_service.tables(TablesRequest(content=_zip(FILES), name="up/site.zip")))
    by_files = _doc(_service.tables(TablesRequest(files=FILES, name="site.zip")))
    assert by_zip == by_files
    assert base64.b64decode(by_zip["output_base64"]) == expected
    assert by_zip["output_sha256"] == hashlib.sha256(expected).hexdigest()
    assert (by_zip["output_name"], by_zip["media_type"], by_zip["diggs_version"]) == (
        "site.xml",
        "application/xml",
        "3.0.0",
    )
    assert by_zip["profile_version"] == tables.PROFILE_VERSION
    assert by_zip["counts"] == {"features": 1, "samples": 0, "layers": 0, "measurements": 1}
    entry = by_zip["report"]["files"][0]
    assert (entry["path"], entry["sha256"]) == ("site.zip", _package.digest(FILES))
    assert [s["step"] for s in entry["steps"]] == ["tables"]
    pydiggs_schema = pydiggs.findings_schema()
    import jsonschema

    jsonschema.Draft202012Validator(pydiggs_schema).validate(by_zip["report"])


def test_check_and_round_trip_are_the_python_answers():
    rows = {n: list(r) for n, r in _io.tables_from_files(FILES).items()}
    checked = _doc(_service.tables(TablesRequest(content=_zip(FILES), operation="check")))
    expected = [f.rule_id for f in tables.check(rows)]
    assert [f["rule_id"] for f in checked["files"][0]["findings"]] == sorted(expected)
    trip = _doc(_service.tables(TablesRequest(content=_zip(FILES), operation="round-trip")))
    python = tables.round_trip(rows)
    assert trip["ok"] is python.ok
    assert base64.b64decode(trip["output_base64"]) == python.diggs
    assert trip["validation"]["files"][0]["path"] == "tables.xml"
    assert trip["differences"] == []


def test_round_trip_differences_are_json():
    changed = {**FILES, "results_DrivenPenetrationTest.csv": b"measurement,n_value\nSPT-1,14x\n"}
    trip = _doc(_service.tables(TablesRequest(files=changed, operation="round-trip")))
    assert not trip["ok"]
    assert _service._json_value(float("nan")) == "nan"
    assert _service._json_value(object()).startswith("<object")


def test_from_diggs_is_write_package_as_a_zip():
    data = (OFFICIAL / "Aeromag.xml").read_bytes()
    result = _doc(
        _service.tables_from_diggs(TablesFromDiggsRequest(content=data, name="C:\\up\\aero.xml"))
    )
    made = tables.from_model(pydiggs.read_bytes(data, name="aero.xml"))
    archive = base64.b64decode(result["output_base64"])
    assert archive == _package.pack(_io.package_files(made))
    assert result["output_name"] == "aero.zip"
    assert result["plan"] == made.plan.to_dict()
    assert result["diggs_version"] == "3.0.0" and result["complete"] is True
    assert result["report"]["files"][0]["path"] == "aero.xml"
    # The ZIP is a package the to-diggs side reads back.
    assert _service.tables(TablesRequest(content=archive)).ok


@pytest.mark.parametrize(
    ("request_", "code", "kind"),
    [
        (TablesRequest(content=b"nope"), Code.INVALID_DOCUMENT, "not-zip"),
        (
            TablesRequest(content=_raw_zip(("../x.csv", b"k\n"))),
            Code.INVALID_DOCUMENT,
            "unsafe-package",
        ),
        (
            TablesRequest(content=_zip({"boreholes.csv": b"key\nBH-1\n"})),
            Code.INVALID_DOCUMENT,
            "not-tables",
        ),
        (
            TablesRequest(content=_zip({"features.csv": b"\xff\xfe\x00key\n"})),
            Code.INPUT_UNREADABLE,
            None,
        ),
        (TablesRequest(content=_zip(FILES), max_bytes=10), Code.INPUT_TOO_LARGE, None),
        (
            TablesRequest(content=_zip(FILES), max_unpacked_bytes=100),
            Code.INPUT_TOO_LARGE,
            None,
        ),
        (TablesRequest(files={"x.txt": b""}), Code.INVALID_DOCUMENT, "unsafe-package"),
        (
            TablesRequest(files={f"results_T{n}.csv": b"" for n in range(257)}),
            Code.INPUT_TOO_LARGE,
            None,
        ),
        (TablesRequest(content=_zip(FILES), max_result_bytes=100), Code.RESULT_TOO_LARGE, None),
        (TablesRequest(content=_zip(FILES), operation="drop"), Code.INVALID_PARAMETER, None),
        (TablesRequest(content=_zip(FILES), name="x" * 5000), Code.INVALID_PARAMETER, None),
    ],
)
def test_a_package_that_cannot_be_used_is_an_error_code(request_, code, kind):
    outcome = _service.tables(request_)
    assert (outcome.code, outcome.kind) == (code, kind)
    assert "boreholes" not in outcome.detail and ".." not in outcome.detail


def test_from_diggs_errors_are_those_of_read():
    bad = _service.tables_from_diggs(TablesFromDiggsRequest(content=b"<html/>"))
    assert (bad.code, bad.kind) == (Code.INVALID_DOCUMENT, "not-diggs")
    big = TablesFromDiggsRequest(content=b"<Diggs/>", max_bytes=3)
    assert _service.tables_from_diggs(big).code is Code.INPUT_TOO_LARGE
    data = (OFFICIAL / "Aeromag.xml").read_bytes()
    small = TablesFromDiggsRequest(content=data, max_result_bytes=10)
    assert _service.tables_from_diggs(small).code is Code.RESULT_TOO_LARGE
    named = TablesFromDiggsRequest(content=data, name="x" * 5000)
    assert _service.tables_from_diggs(named).code is Code.INVALID_PARAMETER


def test_cancellation_is_checked_before_and_after():
    assert _service.tables(TablesRequest(content=b""), should_stop=lambda: True).code is (
        Code.CANCELLED
    )
    data = (OFFICIAL / "Aeromag.xml").read_bytes()
    stopped = _service.tables_from_diggs(
        TablesFromDiggsRequest(content=data), should_stop=lambda: True
    )
    assert stopped.code is Code.CANCELLED


def test_requests_check_their_own_fields():
    with pytest.raises(ValueError, match="exactly one"):
        TablesRequest()
    with pytest.raises(TypeError, match="content must be bytes"):
        TablesRequest(content="text")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="map str to bytes"):
        TablesRequest(files={"a.csv": "text"})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="max_unpacked_bytes"):
        TablesRequest(content=b"", max_unpacked_bytes=0)
    with pytest.raises(ValueError, match="max_result_bytes"):
        TablesFromDiggsRequest(content=b"", max_result_bytes=0)


def test_parameter_errors_cover_the_tables_requests():
    assert _service.parameter_errors(TablesRequest(content=b"", operation="x")) == (
        ("operation", "expected one of to-diggs, check, round-trip"),
    )
    assert _service.parameter_errors(TablesFromDiggsRequest(content=b"", name="x" * 5000))


def test_requests_travel_to_a_worker_and_back():
    request = TablesRequest(files=FILES, operation="check")
    assert pickle.loads(pickle.dumps(request)) == request  # noqa: S301  # our own bytes
    outcome = _service._decode(_service._pooled(request))
    assert outcome.ok and outcome.document == _service.tables(request).document
    from_diggs = TablesFromDiggsRequest(content=b"<html/>")
    assert _service._decode(_service._pooled(from_diggs)).kind == "not-diggs"


def test_package_file_names_follow_the_zip_rule():
    assert _service.package_file("features.csv")
    assert not _service.package_file("notes.txt")
    assert _service.tables_info() == {"tables_profile_version": tables.PROFILE_VERSION}


@pytest.mark.parametrize("module", [_package, _arrow, _io], ids=lambda m: m.__name__)
def test_docstring_examples_run(module, monkeypatch):
    monkeypatch.chdir(ROOT)
    failed, attempted = doctest.testmod(module)
    assert attempted > 0 and failed == 0


def test_a_zip_of_an_official_example_reads_back_with_its_long_cells() -> None:
    """An extra.csv cell over the csv module's 128 KiB default: as the folder reader."""
    import csv

    from pydiggs.tables._io import package_files, tables_from_files

    source = pydiggs.read("tests/fixtures/official/3.0/Piezometer_Example.xml")
    files = dict(package_files(tables.from_model(source)))
    assert max(len(data) for data in files.values()) > 131_072
    before = csv.field_size_limit()
    made = tables.to_model(tables_from_files(files))
    assert csv.field_size_limit() == before
    assert made.model.features


def test_a_zip_table_that_is_not_utf8_is_a_tables_error() -> None:
    from pydiggs.tables._io import tables_from_files

    with pytest.raises(tables.TablesError, match="not UTF-8"):
        tables.to_model(tables_from_files({"features.csv": "key\nCaf\xe9\n".encode("cp1252")}))


def test_a_backslash_name_is_refused_as_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """zipfile rewrites a backslash to "/" in ``filename`` when os.sep is a backslash."""
    import os
    import zipfile

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("a\\features.csv", b"key\n")
    monkeypatch.setattr(os, "sep", "\\")  # read as on Windows
    assert zipfile.ZipFile(io.BytesIO(data.getvalue())).infolist()[0].filename == "a/features.csv"
    with pytest.raises(_package.PackageError):
        _package.unpack(data.getvalue(), max_unpacked_bytes=1 << 20)
