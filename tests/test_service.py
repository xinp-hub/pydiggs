"""``pydiggs._service``: the one untrusted-input route every adapter takes."""

from __future__ import annotations

import ast
import dataclasses
import gzip
import json
import logging
import os
import site
import sys
import tempfile
from pathlib import Path

import jsonschema
import pytest

import pydiggs
from pydiggs import _service, findings_schema, validate_bytes
from pydiggs._pool import JobFailedError, JobTimeoutError, WorkerCrashedError
from pydiggs._service import Code, Outcome, Request
from pydiggs.errors import ResourceError
from pydiggs.findings import _json, run
from pydiggs.findings.model import Finding, Severity, Step

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "pydiggs"
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"
FINDINGS = jsonschema.Draft202012Validator(findings_schema())


def _doc(outcome: Outcome) -> dict:
    assert outcome.ok, outcome
    assert outcome.document is not None and b"\n" not in outcome.document
    return json.loads(outcome.document)


@pytest.mark.parametrize("fixture", [NO_ERROR, SCHEMA_ERROR])
def test_the_document_is_validate_bytes_with_the_pinned_settings(fixture):
    data = fixture.read_bytes()
    outcome = _service.validate(Request(content=data, name=f"C:\\up\\{fixture.name}"))
    expected = validate_bytes(data, name=fixture.name, max_bytes=25 * 2**20).to_document()
    assert _doc(outcome) == expected
    FINDINGS.validate(_doc(outcome))
    assert _doc(outcome)["files"][0]["path"] == fixture.name


def test_sarif_on_request():
    outcome = _service.validate(Request(content=SCHEMA_ERROR.read_bytes(), format="sarif"))
    assert _doc(outcome)["version"] == "2.1.0"


def test_pinned_settings_cannot_be_overridden(monkeypatch):
    seen = {}
    report = validate_bytes(b"<Diggs/>")

    def spy(data, **kwargs):
        seen.update(kwargs)
        return report

    monkeypatch.setattr(run, "_validate_bytes", spy)
    _service.validate(Request(content=b"<Diggs/>", checks=["schema"], diggs_version="2.6"))
    assert seen["allow_network"] is False
    assert seen["include_snippets"] is False
    assert (seen["checks"], seen["diggs_version"]) == (["schema"], "2.6")


@pytest.mark.parametrize(
    ("request_kwargs", "param"),
    [
        ({"name": "x" * (_service.MAX_NAME_INPUT_CHARS + 1)}, "name"),
        ({"name": 7}, "name"),
        ({"checks": "schema"}, "checks"),
        ({"checks": []}, "checks"),
        ({"checks": ["schema", "nope<script>"]}, "checks"),
        ({"checks": ["schema"] * 33}, "checks"),
        ({"checks": {"schema"}}, "checks"),
        ({"diggs_version": "9.9<b>"}, "diggs_version"),
        ({"diggs_version": 2.6}, "diggs_version"),
    ],
)
def test_bad_parameters_are_refused_without_echoing_them(request_kwargs, param):
    outcome = _service.validate(Request(content=b"<Diggs/>", **request_kwargs))
    assert outcome.code is Code.INVALID_PARAMETER and outcome.document is None
    ((name, problem),) = outcome.errors
    assert name == param
    assert "<" not in problem and "x" * 100 not in problem  # the value is never echoed


@pytest.mark.parametrize(
    ("content", "max_bytes", "code"),
    [
        (b"x" * 11, 10, Code.INPUT_TOO_LARGE),
        (gzip.compress(b"<Diggs/>"), 1000, Code.COMPRESSED_INPUT),
    ],
)
def test_refused_inputs(content, max_bytes, code):
    outcome = _service.validate(
        Request(content=content, name="/home/me/a.xml", max_bytes=max_bytes)
    )
    assert outcome.code is code
    assert outcome.detail.startswith("a.xml ")
    assert "/home" not in outcome.detail


def test_an_unreadable_stream_maps_to_input_unreadable(monkeypatch):
    def broken(*args, **kwargs):
        raise pydiggs.InstanceReadError(5, "I/O error", "a.xml")

    monkeypatch.setattr(run, "_validate_bytes", broken)
    assert _service.validate(Request(content=b"<Diggs/>")).code is Code.INPUT_UNREADABLE


def test_cancellation_at_a_step_boundary():
    outcome = _service.validate(Request(content=NO_ERROR.read_bytes()), should_stop=lambda: True)
    assert outcome.code is Code.CANCELLED and outcome.document is None


def test_a_time_budget_still_gives_a_document(monkeypatch):
    clock = iter([0.0, 100.0, 200.0, 300.0, 400.0, 500.0])
    monkeypatch.setattr(run, "_now", lambda: next(clock, 1000.0))
    doc = _doc(_service.validate(Request(content=NO_ERROR.read_bytes(), time_budget=30)))
    assert "structure.timeout" in {f["rule_id"] for f in doc["files"][0]["findings"]}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ResourceError("bundled schema missing at /opt/venv/lib/x.xsd"), Code.RESOURCE_ERROR),
        (RuntimeError("boom in /opt/venv/lib/x.py"), Code.INTERNAL),
    ],
)
def test_internal_errors_carry_generic_text_and_a_correlation_id(monkeypatch, caplog, error, code):
    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(run, "_validate_bytes", broken)
    with caplog.at_level(logging.ERROR, logger="pydiggs.service"):
        outcome = _service.validate(Request(content=b"<Diggs/>"))
    assert outcome.code is code
    assert "/opt/venv" not in outcome.detail
    assert outcome.correlation_id and len(outcome.correlation_id) == 16
    assert outcome.correlation_id in caplog.text and "/opt/venv" in caplog.text


def test_finding_text_is_scrubbed_of_install_temp_and_home_folders(monkeypatch):
    tmp = tempfile.gettempdir()
    sites = [d for d in site.getsitepackages() if Path(d).name == "site-packages"]
    message = f"failed at {tmp}/spool/x.xsd and {sites[0]}/lxml/y and {Path.home()}/z"
    report = validate_bytes(b"<Diggs/>", name="a.xml")
    extra = Finding(
        step=Step.STRUCTURE,
        rule_id="structure.incomplete",
        severity=Severity.ERROR,
        message=message,
    )
    monkeypatch.setattr(
        run,
        "_validate_bytes",
        lambda *a, **k: dataclasses.replace(report, findings=(*report.findings, extra)),
    )
    doc = _doc(_service.validate(Request(content=b"<Diggs/>")))
    text = json.dumps(doc)
    assert tmp not in text and sites[0] not in text and str(Path.home()) not in text
    assert "<tmp>/spool/x.xsd" in text and "<site-packages>/lxml/y" in text and "~/z" in text


def test_scrub_leaves_clean_text_alone():
    report = validate_bytes(b"<Diggs/>")
    assert _service._scrubbed(report) is report
    assert _service._scrub("plain") == "plain"


def test_a_lone_surrogate_cannot_make_invalid_utf8(monkeypatch):
    real = _json.dumps
    monkeypatch.setattr(_json, "dumps", lambda doc, indent=2: real(doc, indent=indent) + "\udcff")
    outcome = _service.validate(Request(content=b"<Diggs/>"))
    outcome.document.decode("utf-8")  # strict


def test_request_limits_are_the_adapters_and_checked():
    for bad in (
        {"content": "text"},
        {"content": b"", "format": "xml"},
        {"content": b"", "max_bytes": True},
        {"content": b"", "max_findings": 0},
        {"content": b"", "time_budget": 0},
        {"content": b"", "time_budget": float("nan")},
        {"content": b"", "time_budget": True},
        {"content": b"", "time_budget": 86_401},
    ):
        with pytest.raises((TypeError, ValueError)):
            Request(**bad)
    assert Request(content=b"", max_findings=None, time_budget=1).max_findings is None


# --- read_fd --------------------------------------------------------------------------------


def test_read_fd(tmp_path):
    good = tmp_path / "a.xml"
    good.write_bytes(b"<Diggs/>")
    packed = tmp_path / "b.xml"
    packed.write_bytes(gzip.compress(b"<Diggs/>"))
    with good.open("rb") as fh:
        assert _service.read_fd(fh.fileno(), max_bytes=100, name="a.xml") == b"<Diggs/>"
    with packed.open("rb") as fh:
        assert _service.read_fd(fh.fileno(), max_bytes=100, name=None).code is (
            Code.COMPRESSED_INPUT
        )
    # A descriptor that cannot be read: write-only everywhere, and a directory on POSIX
    # (Windows refuses to open a directory with os.open at all).
    unreadable = [(tmp_path / "w.xml", os.O_WRONLY | os.O_CREAT, "w.xml")]
    if sys.platform != "win32":
        unreadable.append((tmp_path, os.O_RDONLY, "dir"))
    for path, flags, name in unreadable:
        fd = os.open(path, flags)
        try:
            outcome = _service.read_fd(fd, max_bytes=100, name=name)
        finally:
            os.close(fd)
        assert outcome.code is Code.INPUT_UNREADABLE
        assert outcome.detail == f"{name} could not be read"


# --- process isolation ------------------------------------------------------------------------


class _Pool:
    def __init__(self, answer):
        self.answer = answer

    def run(self, function, *args, timeout):
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def test_pooled_round_trip_in_process():
    request = Request(content=SCHEMA_ERROR.read_bytes(), name="a.xml")
    frame = _service._pooled(request)
    outcome = _service.validate_in_pool(_Pool(frame), request, timeout=5)
    assert outcome == _service.validate(request)
    refused = _service._pooled(Request(content=b"<Diggs/>", checks=["x"]))
    outcome = _service.validate_in_pool(_Pool(refused), request, timeout=5)
    assert outcome.code is Code.INVALID_PARAMETER and outcome.errors[0][0] == "checks"


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (JobTimeoutError("late"), Code.TIMEOUT),
        (JobFailedError("init", "ResourceError", "missing /opt/venv/x", None), Code.RESOURCE_ERROR),
        (JobFailedError("job", "MemoryError", "", None), Code.INTERNAL),
        (WorkerCrashedError("died"), Code.INTERNAL),
        (b"no newline", Code.INTERNAL),
        (b"[]\n", Code.INTERNAL),
        (b'{"code": null, "detail": 1, "errors": []}\n', Code.INTERNAL),
        (b'{"code": "bogus", "detail": "", "errors": []}\n', Code.INTERNAL),
    ],
)
def test_pool_failures_map_to_codes(answer, code):
    outcome = _service.validate_in_pool(_Pool(answer), Request(content=b"<Diggs/>"), timeout=7)
    assert outcome.code is code
    assert "/opt/venv" not in outcome.detail
    if code is Code.TIMEOUT:
        assert outcome.detail == "Validation did not finish within 7 s."


def test_decode_tolerates_odd_errors_and_correlation_ids():
    frame = b'{"code": "internal", "detail": "x", "errors": [["a", "b"], 3], "correlation_id": 5}\n'
    outcome = _service._decode(frame)
    assert outcome.errors == (("a", "b"),) and outcome.correlation_id is None


@pytest.mark.slow
@pytest.mark.timeout(180)
def test_a_real_pool_validates_times_out_and_recycles():
    data = SCHEMA_ERROR.read_bytes()
    with _service.make_pool(1, timeout=120, memory_mb=None) as pool:
        first = _service.validate_in_pool(pool, Request(content=data, name="a.xml"), timeout=120)
        assert first == _service.validate(Request(content=data, name="a.xml"))
        pids = {w.process.pid for w in pool._idle if w is not None}
        late = _service.validate_in_pool(pool, Request(content=data), timeout=0.01)
        assert late.code is Code.TIMEOUT
        assert pids.isdisjoint({w.process.pid for w in pool._idle if w is not None})
        again = _service.validate_in_pool(pool, Request(content=data, name="a.xml"), timeout=120)
        assert again == first


def test_make_pool_checks_the_timeout():
    with pytest.raises(ValueError):
        _service.make_pool(1, timeout=0)
    pool = _service.make_pool(2, timeout=10)
    assert pool.workers == 2 and pool._cpu_seconds == 15.0 and pool._memory_mb == 2048
    pool.close()


def test_warm_worker_warms_every_version(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "warm_up", lambda: calls.append(1))
    _service.warm_worker()
    assert calls == [1]


# --- rules and versions -------------------------------------------------------------------------


def test_rules_explain_and_versions():
    assert _service.versions() == pydiggs.version_info().to_dict()
    every = _service.rules()
    assert every == [r.to_dict() for r in pydiggs.rule_catalogue()]
    assert {r["step"] for r in _service.rules("dictionary")} == {"dictionary"}
    for bad in ("nope", 3):
        outcome = _service.rules(bad)
        assert isinstance(outcome, Outcome) and outcome.errors[0][0] == "step"
    assert _service.explain("dictionary.5") == pydiggs.explain_rule("dictionary.5").to_dict()
    for bad in ("x" * 257, "no dots", 5):
        outcome = _service.explain(bad)
        assert isinstance(outcome, Outcome) and outcome.code is Code.INVALID_PARAMETER


# --- the adapters' only route -------------------------------------------------------------------

#: What an adapter package may import from pydiggs.
_ALLOWED = {"pydiggs._service", "pydiggs._pool", "pydiggs._serve_cli", "pydiggs.errors"}
_FORBIDDEN_NAMES = {
    "validate",
    "validate_bytes",
    "_validate_bytes",
    "validator",
    "detect_diggs_version",
    "read_limited",
    "warm_up",
    "ags4_to_diggs",
}


@pytest.mark.parametrize("package", ["sidecar", "server", "mcp_server"])
def test_adapters_reach_pydiggs_only_through_the_service(package):
    files = sorted((SRC / package).rglob("*.py"))
    assert files
    own = f"pydiggs.{package}"
    for path in files:
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if module == "pydiggs":
                    names = {a.name for a in node.names}
                    # ``from pydiggs import _service`` is ``import pydiggs._service``.
                    allowed = {"__version__"} | {m.rpartition(".")[2] for m in _ALLOWED}
                    assert names <= allowed, (path, names)
                elif module.startswith("pydiggs"):
                    assert module in _ALLOWED or module.startswith(own), (path, module)
                assert not {a.name for a in node.names} & _FORBIDDEN_NAMES, (path, module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("pydiggs"):
                        assert alias.name in _ALLOWED or alias.name.startswith(own), (path, alias)
            elif isinstance(node, ast.Attribute):
                # ``_service.validate`` is the sanctioned route; any other owner is not.
                via_service = isinstance(node.value, ast.Name) and node.value.id == "_service"
                assert via_service or node.attr not in _FORBIDDEN_NAMES, (path, node.attr)


# --- reading ------------------------------------------------------------------------------------

from pydiggs._service import ReadRequest  # noqa: E402 - the read section's own import

CPT = ROOT / "tests" / "fixtures" / "official" / "2.6" / "CPT_and_PorePressureDissipation.xml"


@pytest.fixture
def fresh_cache():
    _service._read_cache.clear()
    yield _service._read_cache
    _service._read_cache.clear()


def test_read_is_read_bytes_as_json(fresh_cache):
    import pydiggs

    data = CPT.read_bytes()
    outcome = _service.read(ReadRequest(content=data, name="C:\\up\\cpt.xml"))
    assert outcome.ok
    assert outcome.document == pydiggs.read_bytes(data, name="cpt.xml").to_json().encode("utf-8")


def test_read_serves_a_repeat_from_the_cache_under_the_new_name(fresh_cache, monkeypatch):
    data = CPT.read_bytes()
    first = _service.read(ReadRequest(content=data, name="a.xml"))
    calls = []
    real = _service._reader.read_stoppable
    monkeypatch.setattr(
        _service._reader, "read_stoppable", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    second = _service.read(ReadRequest(content=data, name="b.xml"))
    assert calls == []
    assert json.loads(second.document)["source"]["name"] == "b.xml"
    assert second.document.replace(b'"b.xml"', b'"a.xml"', 1) == first.document
    # another version or an inventory is another entry
    _service.read(ReadRequest(content=data, select={"results": "none"}))
    assert calls == [1]


def test_the_cache_is_bounded_and_never_overrides_the_size_limit(fresh_cache):
    data = CPT.read_bytes()
    assert _service.read(ReadRequest(content=data)).ok
    too_big = _service.read(ReadRequest(content=data, name="c.xml", max_bytes=10))
    assert too_big.code is Code.INPUT_TOO_LARGE and too_big.detail == "c.xml is larger than allowed"
    small = _service._ModelCache(2, 1000)
    for index in range(3):
        small.put((str(index), None, True), object(), 400)  # type: ignore[arg-type]
    assert small.get(("0", None, True)) is None and small.get(("2", None, True)) is not None
    small.put(("big", None, True), object(), 2000)  # type: ignore[arg-type]
    assert small.get(("big", None, True)) is None


def test_a_model_cut_short_by_the_time_budget_is_not_cached(fresh_cache):
    data = CPT.read_bytes()
    partial = _service.read(ReadRequest(content=data, time_budget=1e-9))
    assert json.loads(partial.document)["complete"] is False
    assert json.loads(_service.read(ReadRequest(content=data)).document)["complete"] is True


def test_a_model_cut_short_by_a_work_limit_is_cached(fresh_cache, monkeypatch):
    """The same bytes stop at the same place, so paging such a file parses it once."""
    from pydiggs._reader import _read as reader

    data = CPT.read_bytes()
    monkeypatch.setattr(reader, "LIMIT_BASE", 2 - len(data) // reader.LIMIT_BYTES_PER_ITEM)
    first = _service.read(ReadRequest(content=data))
    document = json.loads(first.document)
    assert document["complete"] is False and "time_budget" not in document["limits_hit"]
    calls = []
    real = _service._reader.read_stoppable
    monkeypatch.setattr(
        _service._reader, "read_stoppable", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    assert _service.read(ReadRequest(content=data)).document == first.document
    assert calls == []


@pytest.mark.parametrize(
    "select",
    [5, "all", [], {"results": 5}, {"results": [""]}, {"results": ["k"] * 1001}, {"x": "all"}],
)
def test_read_refuses_a_bad_select(select):
    outcome = _service.read(ReadRequest(content=b"<Diggs/>", select=select))
    assert outcome.code is Code.INVALID_PARAMETER
    assert [e[0] for e in outcome.errors] == ["select"]
    assert "k" * 5 not in outcome.errors[0][1]


@pytest.mark.parametrize(
    ("data", "kind"),
    [(b"<Diggs", "malformed"), (b"<html/>", "not-diggs")],
)
def test_read_answers_invalid_document_with_its_kind(data, kind):
    outcome = _service.read(ReadRequest(content=data, name="a.xml"))
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, kind)
    assert kind in _service.DOCUMENT_KINDS
    assert outcome.detail == _service._DOCUMENT_TEXT[kind]  # fixed: never the file's own text
    assert set(_service._DOCUMENT_TEXT) == set(_service.DOCUMENT_KINDS)


def test_read_limits_the_result_and_can_be_cancelled(fresh_cache):
    data = CPT.read_bytes()
    big = _service.read(ReadRequest(content=data, name="a.xml", max_result_bytes=100))
    assert big.code is Code.RESULT_TOO_LARGE and "select" in big.detail
    stopped = _service.read(ReadRequest(content=data), should_stop=lambda: True)
    assert stopped.code is Code.CANCELLED


def test_read_fails_closed(fresh_cache, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("/secret/place")

    monkeypatch.setattr(_service._reader, "read_stoppable", broken)
    with caplog.at_level(logging.ERROR, logger="pydiggs.service"):
        outcome = _service.read(ReadRequest(content=b"<Diggs/>"))
    assert outcome.code is Code.INTERNAL and "secret" not in outcome.detail
    assert outcome.correlation_id and outcome.correlation_id in caplog.text


def test_pooled_read_round_trip_in_process(fresh_cache):
    request = ReadRequest(content=CPT.read_bytes(), name="a.xml")
    frame = _service._pooled(request)
    outcome = _service.read_in_pool(_Pool(frame), request, timeout=5)
    assert outcome == _service.read(request)
    refused = _service._pooled(ReadRequest(content=b"<html/>"))
    outcome = _service.read_in_pool(_Pool(refused), request, timeout=5)
    assert (outcome.code, outcome.kind) == (Code.INVALID_DOCUMENT, "not-diggs")
    slow = _service.read_in_pool(_Pool(JobTimeoutError("x")), request, timeout=5)
    assert slow.code is Code.TIMEOUT and slow.detail == "Reading did not finish within 5 s."


def _many_unknown_members(count: int) -> bytes:
    """One feature holding ``count`` elements the model has no typed field for."""
    return (
        b'<Diggs xmlns="http://diggsml.org/schemas/3"><samplingFeature><Borehole>'
        + b"<a><b/></a>" * (count // 2)
        + b"</Borehole></samplingFeature></Diggs>"
    )


def test_a_read_is_cancelled_inside_a_member(fresh_cache):
    calls = []

    def should_stop() -> bool:
        calls.append(1)
        return len(calls) > 3  # not before it starts: only once the member is under way

    outcome = _service.read(
        ReadRequest(content=_many_unknown_members(20_000)), should_stop=should_stop
    )
    assert outcome.code is Code.CANCELLED
    assert len(calls) > 3


def test_the_time_budget_stops_a_read_inside_a_member(fresh_cache):
    outcome = _service.read(ReadRequest(content=_many_unknown_members(20_000), time_budget=1e-9))
    document = json.loads(outcome.document)
    assert document["complete"] is False and document["limits_hit"] == ["time_budget"]


def test_the_cache_weighs_a_model_by_its_document(fresh_cache, monkeypatch):
    seen = []
    real = _service._read_cache.put
    monkeypatch.setattr(
        _service._read_cache,
        "put",
        lambda key, model, size: seen.append(size) or real(key, model, size),
    )
    data = _many_unknown_members(2_000)
    outcome = _service.read(ReadRequest(content=data))
    assert seen == [len(outcome.document)] and seen[0] > len(data)
    assert "namespace-too-long" in _service.DOCUMENT_KINDS


# --- AGS4: conversion and checking --------------------------------------------------------------

from pydiggs._service import Ags4CheckRequest, ConvertRequest  # noqa: E402 - this section's own

AGS4_DIR = ROOT / "tests" / "fixtures" / "ags4"


def test_convert_and_check_through_the_pool_frames():
    data = (AGS4_DIR / "site_tier_a.ags").read_bytes()
    for request, in_pool, direct in (
        (ConvertRequest(content=data, name="s.ags"), _service.convert_in_pool, _service.convert),
        (
            Ags4CheckRequest(content=data, name="s.ags"),
            _service.ags4_check_in_pool,
            _service.ags4_check,
        ),
    ):
        frame = _service._pooled(request)
        assert in_pool(_Pool(frame), request, timeout=5) == direct(request)
    timed_out = _service.convert_in_pool(
        _Pool(JobTimeoutError("late")), ConvertRequest(content=data), timeout=3
    )
    assert (
        timed_out.code is Code.TIMEOUT
        and timed_out.detail == "Conversion did not finish within 3 s."
    )


def test_convert_refusals_never_echo_the_value():
    data = (AGS4_DIR / "site_tier_a.ags").read_bytes()
    bad = _service.convert(ConvertRequest(content=data, to="<script>", diggs_version="9.9"))
    assert bad.code is Code.INVALID_PARAMETER
    assert [e[0] for e in bad.errors] == ["to", "diggs_version"]
    assert "<script>" not in repr(bad)
    assert _service.convert(ConvertRequest(content=data, max_bytes=10)).code is Code.INPUT_TOO_LARGE
    zipped = _service.convert(ConvertRequest(content=b"PK\x03\x04" + data))
    assert zipped.code is Code.COMPRESSED_INPUT
    ags3 = _service.convert(ConvertRequest(content=(AGS4_DIR / "ags3.ags").read_bytes()))
    assert (ags3.code, ags3.kind) == (Code.INVALID_DOCUMENT, "unsupported-version")
    tight = _service.convert(ConvertRequest(content=data, max_result_bytes=1000))
    assert tight.code is Code.RESULT_TOO_LARGE


def test_convert_and_check_honour_cancellation():
    data = (AGS4_DIR / "site_tier_a.ags").read_bytes()
    assert _service.convert(ConvertRequest(content=data), should_stop=lambda: True).code is (
        Code.CANCELLED
    )
    assert _service.ags4_check(Ags4CheckRequest(content=data), should_stop=lambda: True).code is (
        Code.CANCELLED
    )


def test_parameter_errors_dispatches_on_the_request_type():
    assert _service.parameter_errors(Request(content=b"", checks=["x"]))[0][0] == "checks"
    assert _service.parameter_errors(ReadRequest(content=b"", select=5))[0][0] == "select"
    assert _service.parameter_errors(Ags4CheckRequest(content=b"", name="x" * 5000))[0][0] == "name"
    with pytest.raises(TypeError):
        _service.parameter_errors(object())


def test_request_limits_are_checked():
    with pytest.raises(TypeError):
        ConvertRequest(content="text")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_result_bytes"):
        ConvertRequest(content=b"", max_result_bytes=0)
    with pytest.raises(TypeError):
        Ags4CheckRequest(content=b"", dictionary="text")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="format"):
        Ags4CheckRequest(content=b"", format="xml")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_findings"):
        Ags4CheckRequest(content=b"", max_findings=0)
    with pytest.raises(ValueError, match="max_bytes"):
        Ags4CheckRequest(content=b"", max_bytes=0)
