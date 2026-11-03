"""validate_bytes(): the same checks as validate(), on a document held in memory."""

from __future__ import annotations

import dataclasses
import errno
import gzip
import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import InstanceReadError, Report, Severity, Step, validate, validate_bytes
from pydiggs.findings import rules, run
from pydiggs.findings.run import (
    DEFAULT_INPUT_NAME,
    MAX_NAME_CHARS,
    NATIVE_STEPS,
    _Cancelled,
    _validate_bytes,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS = sorted(
    p for d in ("tests/fixtures/official", "tests/test_files") for p in (ROOT / d).rglob("*.xml")
)
NO_ERROR = ROOT / "tests/test_files/2.6/No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests/test_files/2.6/Schema_Error_1-2_6.xml"
DIGGS_26 = b'<Diggs xmlns="http://diggsml.org/schemas/2.6"/>'


def _same_as_validate(path: Path) -> tuple[Report, Report]:
    by_path = validate(path, local_dictionaries="off", base_dir=path.parent)
    by_bytes = validate_bytes(path.read_bytes(), name=path.name)
    return dataclasses.replace(by_path, absolute_path=None), by_bytes


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_bytes_equal_path_on_the_corpus(path):
    by_path, by_bytes = _same_as_validate(path)
    assert by_bytes == by_path
    assert by_bytes.to_json() == by_path.to_json()


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    source=st.sampled_from([p for p in CORPUS if p.stat().st_size < 200_000]),
    cut=st.floats(0, 1),
    insert=st.binary(max_size=8),
)
def test_bytes_equal_path_on_mutations(tmp_path, source, cut, insert):
    data = source.read_bytes()
    at = int(len(data) * cut)
    path = tmp_path / "mutant.xml"
    path.write_bytes(data[:at] + insert + data[at:])
    try:
        by_path, by_bytes = _same_as_validate(path)
    except InstanceReadError as refused:
        # A mutation can start the file with the gzip magic: both inputs refuse it alike.
        with pytest.raises(InstanceReadError) as by_path_refused:
            validate(path, local_dictionaries="off", base_dir=path.parent)
        assert (refused.errno, refused.strerror) == (
            by_path_refused.value.errno,
            by_path_refused.value.strerror,
        )
        return
    assert by_bytes == by_path


@pytest.mark.parametrize(
    "make",
    [bytes, bytearray, memoryview, io.BytesIO, lambda d: io.BufferedReader(io.BytesIO(d))],
    ids=["bytes", "bytearray", "memoryview", "BytesIO", "BufferedReader"],
)
def test_every_input_type_gives_the_same_report(make):
    data = SCHEMA_ERROR.read_bytes()
    assert validate_bytes(make(data), name="a.xml") == validate_bytes(data, name="a.xml")


def test_a_stream_is_read_whole_and_left_open():
    stream = io.BytesIO(NO_ERROR.read_bytes())
    report = validate_bytes(stream)
    assert not stream.closed
    assert report.verdict == validate(NO_ERROR).verdict


def test_a_bytearray_is_copied_before_the_run(monkeypatch):
    buffer = bytearray(SCHEMA_ERROR.read_bytes())
    original = run._validate_input

    def mutate_then_run(source, **kwargs):
        buffer[:] = b"<oops"  # another thread changes the caller's buffer mid-run
        return original(source, **kwargs)

    monkeypatch.setattr(run, "_validate_input", mutate_then_run)
    assert validate_bytes(buffer).steps[Step.SCHEMA] == "failed"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("<Diggs/>", "not str"),
        (io.StringIO("<Diggs/>"), "text stream"),
        (memoryview(b"<Diggs/>  ")[::2], "non-contiguous"),
        (12, "not int"),
    ],
)
def test_unsupported_data_is_a_type_error(data, message):
    with pytest.raises(TypeError, match=message):
        validate_bytes(data)


class _OddStream:
    def __init__(self, chunk):
        self.chunk = chunk

    def read(self, n):
        return self.chunk


def test_a_stream_returning_neither_bytes_nor_text_is_a_type_error():
    with pytest.raises(TypeError, match="not bytes"):
        validate_bytes(_OddStream(42))


def test_a_non_blocking_stream_without_data_is_unreadable():
    with pytest.raises(InstanceReadError) as info:
        validate_bytes(_OddStream(None), name="up.xml")
    assert info.value.errno == errno.EAGAIN
    assert info.value.filename == "up.xml"


def test_a_failing_stream_is_unreadable_under_its_reported_name():
    class Broken:
        name = "/secret/folder/x.xml"  # never used

        def read(self, n):
            raise OSError(errno.EIO, "Input/output error")

    with pytest.raises(InstanceReadError) as info:
        validate_bytes(Broken(), name="up.xml")
    assert info.value.errno == errno.EIO
    assert info.value.filename == "up.xml"
    assert "secret" not in str(info.value)


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        (None, DEFAULT_INPUT_NAME),
        ("", DEFAULT_INPUT_NAME),
        (".", DEFAULT_INPUT_NAME),
        ("..", DEFAULT_INPUT_NAME),
        ("site/", DEFAULT_INPUT_NAME),
        ("BH-01.xml", "BH-01.xml"),
        ("/home/me/uploads/BH-01.xml", "BH-01.xml"),
        ("C:\\Users\\me\\BH-01.xml", "BH-01.xml"),
        ("a/b\\..\\c.xml", "c.xml"),
        ("evil\u202egnp.xml", "evil\\u202egnp.xml"),
        ("tab\there.xml", "tab\\x09here.xml"),
        ("lone\ud800.xml", "lone\\ud800.xml"),
        ("esc\udcff.xml", "esc\\xff.xml"),
    ],
)
def test_the_name_is_reduced_to_a_safe_basename(name, shown):
    report = validate_bytes(DIGGS_26, name=name, checks=["structure"])
    assert report.path == shown
    assert report.absolute_path is None


def test_a_long_name_is_capped():
    assert len(validate_bytes(DIGGS_26, name="x" * 1000 + ".xml").path) == MAX_NAME_CHARS


def test_a_long_name_is_never_cut_inside_an_escape():
    shown = validate_bytes(DIGGS_26, name="a" * 252 + "\u202e.xml", checks=["structure"]).path
    assert shown == "a" * 252  # "\u202e" (6 characters) does not fit, so it is left out whole


def test_the_name_does_not_depend_on_the_host_os(monkeypatch):
    # os.fsencode uses surrogatepass on Windows; a wire name must not go through it.
    def windows_fsencode(name):
        return name.encode("utf-8", "surrogatepass")

    monkeypatch.setattr(os, "fsencode", windows_fsencode)
    assert validate_bytes(DIGGS_26, name="esc\udcff.xml", checks=["structure"]).path == (
        "esc\\xff.xml"
    )
    assert validate_bytes(DIGGS_26, name="lone\ud800.xml", checks=["structure"]).path == (
        "lone\\ud800.xml"
    )


def test_a_closed_stream_is_unreadable_input():
    stream = io.BytesIO(DIGGS_26)
    stream.close()
    with pytest.raises(InstanceReadError) as refused:
        validate_bytes(stream)
    assert refused.value.errno == errno.EBADF


def test_a_budget_past_float_range_is_no_budget():
    report = validate_bytes(DIGGS_26, checks=["structure"], time_budget=10**400)
    assert report == validate_bytes(DIGGS_26, checks=["structure"])


def test_a_non_string_name_is_a_type_error():
    with pytest.raises(TypeError, match="name"):
        validate_bytes(DIGGS_26, name=Path("a.xml"))  # type: ignore[arg-type]


# --- every safety limit fires the same way for a path, bytes and a stream ----------

SAFETY_CASES = {
    "markup-entity": b'<!DOCTYPE Diggs [<!ENTITY e "<b/>">]><Diggs>&e;</Diggs>',
    "entity-expansion": (
        b'<!DOCTYPE Diggs [<!ENTITY a "' + b"x" * 100 + b'">]><Diggs>' + b"&a;" * 50 + b"</Diggs>"
    ),
    "namespace-too-long": b'<Diggs xmlns="http://e.org/' + b"n" * 1100 + b'"/>',
    "well-formed": b"<Diggs><unclosed></Diggs>",
    "diggs-root": b"<NotDiggs/>",
    "xxe": (
        b'<!DOCTYPE Diggs [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<Diggs xmlns="http://diggsml.org/schemas/2.6">&x;</Diggs>'
    ),
}


def _three_ways(tmp_path: Path, data: bytes, **kwargs: object) -> list[Report]:
    path = tmp_path / "case.xml"
    path.write_bytes(data)
    by_path = validate(path, local_dictionaries="off", base_dir=tmp_path, **kwargs)  # type: ignore[arg-type]
    return [
        dataclasses.replace(by_path, absolute_path=None),
        validate_bytes(data, name="case.xml", **kwargs),  # type: ignore[arg-type]
        validate_bytes(io.BytesIO(data), name="case.xml", **kwargs),  # type: ignore[arg-type]
    ]


@pytest.mark.parametrize("case", sorted(SAFETY_CASES))
def test_safety_refusals_are_identical_for_every_input(tmp_path, case):
    path_report, bytes_report, stream_report = _three_ways(tmp_path, SAFETY_CASES[case])
    assert bytes_report == path_report == stream_report
    assert "passwd" not in bytes_report.to_json()


def test_findings_limits_are_identical_for_every_input(tmp_path):
    reports = _three_ways(tmp_path, SCHEMA_ERROR.read_bytes(), max_findings=1)
    assert reports[0] == reports[1] == reports[2]
    assert rules.FINDINGS_TRUNCATED in [f.rule_id for f in reports[1].findings]


@pytest.mark.parametrize(
    ("data", "code"),
    [(b"<Diggs/>" + b" " * 2000, errno.EFBIG), (gzip.compress(DIGGS_26), errno.EINVAL)],
)
def test_size_and_compression_refusals_are_identical_for_every_input(tmp_path, data, code):
    path = tmp_path / "big.xml"
    path.write_bytes(data)
    errors = []
    for call in (
        lambda: validate(path, max_bytes=1000),
        lambda: validate_bytes(data, name="big.xml", max_bytes=1000),
        lambda: validate_bytes(bytearray(data), name="big.xml", max_bytes=1000),
        lambda: validate_bytes(memoryview(data), name="big.xml", max_bytes=1000),
        lambda: validate_bytes(io.BytesIO(data), name="big.xml", max_bytes=1000),
    ):
        with pytest.raises(InstanceReadError) as info:
            call()
        errors.append((info.value.errno, info.value.strerror, info.value.filename))
    assert errors == [(code, errors[0][1], "big.xml")] * 5


def test_exactly_max_bytes_is_accepted():
    for data in (DIGGS_26, io.BytesIO(DIGGS_26)):
        report = validate_bytes(data, max_bytes=len(DIGGS_26), checks=["structure"])
        assert report.diggs_version == "2.6"


def test_a_stream_is_read_only_one_byte_past_the_limit():
    stream = io.BytesIO(b"x" * 1000)
    with pytest.raises(InstanceReadError):
        validate_bytes(stream, max_bytes=10)
    assert stream.tell() == 11


@pytest.mark.parametrize(
    "kwargs",
    [
        {"diggs_version": "9.9"},
        {"checks": ["nope"]},
        {"checks": "schema"},
        {"max_findings": 0},
        {"max_bytes": 0},
        {"time_budget": 0},
        {"time_budget": -1.0},
        {"time_budget": float("nan")},
        {"time_budget": True},
        {"time_budget": "5"},
    ],
)
def test_bad_arguments_are_value_errors_before_reading(kwargs):
    stream = io.BytesIO(DIGGS_26)
    with pytest.raises(ValueError):
        validate_bytes(stream, **kwargs)
    assert stream.tell() == 0


_LOCAL_DICT = """<gml:Dictionary xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:diggs="http://diggsml.org/schemas/3" gml:id="d">
  <gml:dictionaryEntry><diggs:Definition gml:id="surveyor"><gml:name>surveyor</gml:name>
  </diggs:Definition></gml:dictionaryEntry>
</gml:Dictionary>"""


def test_local_dictionaries_are_never_read_even_from_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / "local.xml").write_text(_LOCAL_DICT, encoding="utf-8")
    data = (
        b'<Diggs xmlns="http://diggsml.org/schemas/3">'
        b'<role codeSpace="./local.xml#surveyor">surveyor</role></Diggs>'
    )
    (tmp_path / "site.xml").write_bytes(data)
    assert validate(tmp_path / "site.xml", checks=["dictionary"]).findings == ()  # it is read
    monkeypatch.chdir(tmp_path)
    ((finding),) = validate_bytes(data, checks=["dictionary"]).findings
    assert finding.rule_id == "dictionary.3"
    assert "local_dictionaries='off'" in finding.message


# --- time budget ---------------------------------------------------------------------


def _ticks(*values: float):
    clock = iter(values)
    return lambda: next(clock, values[-1])


def test_an_exhausted_budget_fails_closed_in_the_cli_timeout_shape(monkeypatch):
    clock = iter([0.0, 0.0, 100.0])  # start, before schema, before dictionary
    monkeypatch.setattr(run, "_now", lambda: next(clock, 100.0))
    report = validate_bytes(SCHEMA_ERROR.read_bytes(), time_budget=30)
    assert report.steps[Step.SCHEMA] == "failed"  # ran before the budget ran out
    unfinished = [s for s in NATIVE_STEPS if s is not Step.SCHEMA]
    assert all(report.steps[s] == "incomplete" for s in unfinished)
    timeouts = [f for f in report.findings if f.rule_id == rules.TIMEOUT]
    assert len(timeouts) == 1
    assert timeouts[0].severity is Severity.ERROR
    assert "time budget of 30 s" in timeouts[0].message
    assert "not run: dictionary, schematron, deprecation, structure, geometry, context" in (
        timeouts[0].message
    )
    assert report.verdict == "not-valid"
    assert not any(f.rule_id.endswith(".incomplete") for f in report.findings)


def test_the_budget_report_has_the_cli_timeout_shape_for_unrequested_steps(monkeypatch):
    monkeypatch.setattr(run, "_now", _ticks(0.0, 5.0))
    report = validate_bytes(NO_ERROR.read_bytes(), checks=["schema"], time_budget=1)
    assert report.steps == {
        s: "incomplete" if s is Step.SCHEMA else "skipped" for s in NATIVE_STEPS
    }
    assert [f.rule_id for f in report.findings] == [rules.TIMEOUT]


def test_the_timeout_finding_survives_a_full_report(monkeypatch):
    clock = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(run, "_now", lambda: next(clock, 100.0))
    report = validate_bytes(SCHEMA_ERROR.read_bytes(), time_budget=1, max_findings=1)
    ids = [f.rule_id for f in report.findings]
    assert rules.TIMEOUT in ids
    assert rules.FINDINGS_TRUNCATED in ids


def test_validate_takes_the_same_budget(monkeypatch):
    monkeypatch.setattr(run, "_now", _ticks(0.0, 5.0))
    report = validate(NO_ERROR, checks=["schema", "dictionary"], time_budget=1)
    assert [f.rule_id for f in report.findings] == [rules.TIMEOUT]


def test_a_generous_or_infinite_budget_changes_nothing():
    data = NO_ERROR.read_bytes()
    plain = validate_bytes(data)
    assert validate_bytes(data, time_budget=3600) == plain
    assert validate_bytes(data, time_budget=float("inf")) == plain


def test_should_stop_cancels_at_a_step_boundary():
    calls = []

    def stop() -> bool:
        calls.append(1)
        return len(calls) > 1  # let the schema step run, then cancel

    with pytest.raises(_Cancelled):
        _validate_bytes(NO_ERROR.read_bytes(), should_stop=stop)
    assert len(calls) == 2


# --- threads -------------------------------------------------------------------------


def test_threads_give_the_single_threaded_results():
    inputs = [p for p in CORPUS if p.stat().st_size < 500_000][:8]
    expected = [validate_bytes(p.read_bytes(), name=p.name) for p in inputs]
    with ThreadPoolExecutor(4, initializer=pydiggs.warm_up) as pool:
        got = list(pool.map(lambda p: validate_bytes(p.read_bytes(), name=p.name), inputs * 3))
    assert got == expected * 3
