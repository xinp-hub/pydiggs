"""The 1.3 core API surface: names, import cost, rich laziness, warm_up, helpers, doctests."""

from __future__ import annotations

import doctest
import errno
import inspect
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _bundled, _compat, _versions, _xml
from pydiggs import pydiggs as legacy
from pydiggs.findings import export, rules, run

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests/test_files/2.6/No_Error_2_6.xml"

NEW_NAMES = {
    "FINDINGS_SCHEMA_URL",
    "FINDINGS_SCHEMA_VERSION",
    "RuleInfo",
    "VersionInfo",
    "explain_rule",
    "findings_document",
    "findings_json",
    "findings_schema",
    "rule_catalogue",
    "validate_bytes",
    "version_info",
    "warm_up",
}
NAMES_1_2_0 = {
    "Finding",
    "InstanceReadError",
    "PydiggsError",
    "Report",
    "ResourceError",
    "Severity",
    "Step",
    "detect_diggs_version",
    "validate",
    "validator",
}


def test_the_public_api_is_1_2_0_plus_twelve_names():
    assert len(NEW_NAMES) == 12
    assert set(pydiggs.__all__) == NAMES_1_2_0 | NEW_NAMES


def test_validate_keeps_its_1_2_0_signature_plus_time_budget():
    params = inspect.signature(pydiggs.validate).parameters
    assert list(params) == [
        "path",
        "diggs_version",
        "checks",
        "schema_path",
        "dictionary_path",
        "schematron_path",
        "allow_network",
        "include_snippets",
        "max_findings",
        "max_bytes",
        "base_dir",
        "local_dictionaries",
        "time_budget",
    ]
    assert params["time_budget"].default is None
    assert params["time_budget"].kind is inspect.Parameter.KEYWORD_ONLY


def test_validate_bytes_takes_no_trusted_resource_paths():
    params = set(inspect.signature(pydiggs.validate_bytes).parameters)
    assert not params & {"schema_path", "dictionary_path", "schematron_path", "base_dir"}
    assert not params & {"local_dictionaries", "timeout"}


def _fresh(code: str) -> str:
    result = subprocess.run(  # noqa: S603  # our own interpreter, fixed code
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    return result.stdout.strip()


def test_import_and_validate_bytes_do_not_import_rich_or_the_adapters():
    code = (
        "import sys, pydiggs\n"
        f"r = pydiggs.validate_bytes(open({str(NO_ERROR)!r}, 'rb').read())\n"
        "pydiggs.findings_json([r]); pydiggs.version_info(); pydiggs.rule_catalogue()\n"
        "bad = ['rich', 'multiprocessing', 'fastapi', 'mcp', 'pydiggs.cli',"
        " 'pydiggs.server', 'pydiggs.sidecar', 'pydiggs.mcp_server']\n"
        "print(sorted(m for m in bad if m in sys.modules))\n"
    )
    assert _fresh(code) == "[]"


def test_validation_loads_no_networking_modules():
    # Only an allow_network download needs them; the browser build relies on this.
    code = (
        "import sys, pydiggs\n"
        f"pydiggs.validate_bytes(open({str(NO_ERROR)!r}, 'rb').read())\n"
        f"pydiggs.validate({str(NO_ERROR)!r})\n"
        "net = ['socket', 'ssl', 'http.client', 'urllib.request']\n"
        "print(sorted(m for m in net if m in sys.modules))\n"
    )
    assert _fresh(code) == "[]"


def test_the_legacy_checks_still_import_rich_when_they_print():
    code = (
        "import sys\nfrom pydiggs import validator\n"
        f"validator({str(NO_ERROR)!r}, output_log=False).schema_check()\n"
        "print('rich' in sys.modules)\n"
    )
    assert _fresh(code).splitlines()[-1] == "True"


@pytest.mark.parametrize(
    "text",
    [
        "[red]  [ERROR] check 5: a \\[b] [b]bold[/b][/red]",
        "[green]Dictionary check passed (2 warnings)[/green]",
        "[red]x \\\\[b]y[/b] z[/red]",
        "plain [1, 2] list",
    ],
)
def test_rprint_falls_back_to_what_rich_prints_without_rich(monkeypatch, capsys, text):
    legacy.rprint(text)
    with_rich = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)  # FORCE_COLOR in CI
    monkeypatch.setitem(sys.modules, "rich", None)  # import rich -> ModuleNotFoundError
    legacy.rprint(text)
    assert capsys.readouterr().out == with_rich


def test_rprint_uses_rich_when_present(capsys):
    legacy.rprint("[green]No syntax error is detected.[/green]")
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)  # FORCE_COLOR in CI
    assert out == "No syntax error is detected.\n"


def test_rprint_can_still_be_monkeypatched(monkeypatch):
    printed = []
    monkeypatch.setattr(legacy, "rprint", lambda *a, **k: printed.append(a))
    legacy.validator(str(NO_ERROR), output_log=False).schema_check()
    assert printed


def test_the_bundled_names_are_still_importable_from_the_legacy_module():
    assert legacy._DEFAULT_DICTIONARY is _bundled._DEFAULT_DICTIONARY
    assert legacy._DEFAULT_SCHEMATRON is _bundled._DEFAULT_SCHEMATRON
    assert legacy._casing_diameter_problems is _bundled._casing_diameter_problems
    assert run._casing_diameter_problems is _bundled._casing_diameter_problems
    assert _bundled._DEFAULT_DICTIONARY.is_file()
    assert _bundled._DEFAULT_SCHEMATRON.is_file()


# --- warm_up -----------------------------------------------------------------------


def test_warm_up_compiles_into_the_calling_thread_cache(monkeypatch):
    monkeypatch.setattr(run, "_CACHE", run._Cache())
    pydiggs.warm_up("2.6")
    assert set(run._CACHE.schemas) == {str(run.schema_path_for("2.6"))}
    assert len(run._CACHE.schematrons) == 1
    assert {key[2] for key in run._CACHE.dictionaries} == {"off", "instance-dir"}
    pydiggs.warm_up()
    assert len(run._CACHE.schemas) == 3


def test_warm_up_refuses_an_unknown_version():
    with pytest.raises(ValueError, match="unknown DIGGS version"):
        pydiggs.warm_up("9.9")


def test_warm_up_reports_broken_bundled_data(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "_CACHE", run._Cache())
    monkeypatch.setattr(run, "_DEFAULT_SCHEMATRON", tmp_path / "missing.sch")
    with pytest.raises(pydiggs.ResourceError):
        pydiggs.warm_up("2.6")


# --- private helpers WP2 builds on ---------------------------------------------------


def test_check_bytes_is_the_one_limit_check():
    _xml._check_bytes(b"x" * 10, 10)
    with pytest.raises(OSError, match="exceeds 10 bytes") as big:
        _xml._check_bytes(b"x" * 11, 10)
    assert big.value.errno == errno.EFBIG
    with pytest.raises(OSError, match="compressed") as gz:
        _xml._check_bytes(b"\x1f\x8bxx", 10)
    assert gz.value.errno == errno.EINVAL


def test_read_fd_reads_a_verified_descriptor_and_leaves_it_open(tmp_path):
    path = tmp_path / "file:%2e%2e.xml"  # read by descriptor: the name is never decoded
    path.write_bytes(b"<Diggs/>")
    fd = os.open(path, os.O_RDONLY)
    try:
        assert _xml.read_fd(fd, 100) == b"<Diggs/>"
        os.fstat(fd)  # still open
        os.lseek(fd, 0, os.SEEK_SET)
        with pytest.raises(OSError) as info:
            _xml.read_fd(fd, 4)
        assert info.value.errno == errno.EFBIG
    finally:
        os.close(fd)


@pytest.mark.skipif(not hasattr(os, "pipe"), reason="no pipes")
def test_read_fd_refuses_anything_but_a_regular_file():
    read_end, write_end = os.pipe()
    try:
        with pytest.raises(OSError, match="not a regular file") as info:
            _xml.read_fd(read_end, 100)
        assert info.value.errno == errno.EINVAL
    finally:
        os.close(read_end)
        os.close(write_end)


def test_read_chunks_asks_for_at_most_the_limit_plus_one():
    asked = []

    def read(n: int) -> bytes:
        asked.append(n)
        return b"ab"

    with pytest.raises(OSError):
        _xml.read_chunks(read, 5)
    assert sum(asked) <= 6 + 2 * len(asked)
    assert all(n <= 6 for n in asked)


def test_validate_tree_without_an_instance_path_reads_no_local_dictionary(tmp_path):
    from pydiggs.dictionary import DictionarySemanticValidator

    semantic = DictionarySemanticValidator(local_dictionaries="instance-dir", legacy_paths=False)
    doc = _xml.parse_bytes(b"<Diggs xmlns='http://diggsml.org/schemas/3'/>", None)
    semantic.validate_tree(doc, None)
    assert semantic._instance_dir is None
    semantic.validate_tree(doc, tmp_path / "x.xml")
    assert semantic._instance_dir == tmp_path.resolve()


def test_deprecated_warns_with_since_and_removal():
    def old() -> None:
        _compat._deprecated("old() is deprecated; use new()", "1.3.0", "2.0.0")

    with pytest.warns(DeprecationWarning, match=r"since pydiggs 1\.3\.0, removal in 2\.0\.0"):
        old()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old()
    assert caught[0].filename == __file__  # points at the caller of old()


# --- docstring examples ---------------------------------------------------------------


@pytest.mark.parametrize("module", [export, rules, run, _versions, _compat, _xml], ids=str)
def test_docstring_examples_run(module, monkeypatch):
    monkeypatch.chdir(ROOT)
    failed, _ = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert failed == 0, module.__name__


def test_every_new_public_name_has_a_runnable_example():
    for name in NEW_NAMES - {"FINDINGS_SCHEMA_URL", "FINDINGS_SCHEMA_VERSION"}:
        doc = inspect.getdoc(getattr(pydiggs, name)) or ""
        assert "Example:" in doc and ">>>" in doc, name
    assert ">>>" in (inspect.getdoc(pydiggs.Report.to_document) or "")
