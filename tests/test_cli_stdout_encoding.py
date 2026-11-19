"""``pydiggs validate`` and ``pydiggs convert`` on a stdout that cannot encode the report.

A redirected stdout on Windows is cp1252, and a name or a message can hold any character.
The report is then written with escapes (a backslash escape in text, JSON's own
``\\uXXXX`` in JSON, which is the same document), never lost to a traceback; a stdout
that takes the characters gets them as they are. ``pydiggs diff`` does the same
(tests/diff/test_review.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pydiggs import _stdout
from pydiggs.cli import main

ROOT = Path(__file__).resolve().parent.parent
NO_ERROR = ROOT / "tests" / "test_files" / "2.6" / "No_Error_2_6.xml"
SCHEMA_ERROR = ROOT / "tests" / "test_files" / "2.6" / "Schema_Error_1-2_6.xml"
SITE = ROOT / "tests" / "fixtures" / "ags4" / "convert_tier_a.ags"
GREEK = "φ≤"


def _run(*args: str, encoding: str) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    env.pop("PYTHONUTF8", None)
    command = [sys.executable, "-m", "pydiggs", *args]
    return subprocess.run(command, capture_output=True, cwd=ROOT, env=env, check=False)  # noqa: S603


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp(f"names-{GREEK}")
    named = {
        "valid": folder / f"ok-{GREEK}.xml",
        "invalid": folder / f"bad-{GREEK}.xml",
        "ags": folder / f"site-{GREEK}.ags",
    }
    shutil.copy(NO_ERROR, named["valid"])
    shutil.copy(SCHEMA_ERROR, named["invalid"])
    shutil.copy(SITE, named["ags"])
    return named


# --- validate ---------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_a_validate_text_report_stdout_cannot_encode_is_written_with_escapes(files, encoding):
    done = _run("validate", str(files["invalid"]), encoding=encoding)
    assert done.returncode == 1 and done.stderr == b""
    out = done.stdout.decode(encoding)
    assert "bad-\\u03c6\\u2264.xml: not-valid" in out


@pytest.mark.parametrize("fmt", ["json", "sarif"])
def test_a_validate_json_report_stdout_cannot_encode_is_the_same_document(files, fmt):
    narrow = _run("validate", str(files["invalid"]), "--format", fmt, encoding="ascii")
    wide = _run("validate", str(files["invalid"]), "--format", fmt, encoding="utf-8")
    assert narrow.returncode == wide.returncode == 1 and narrow.stderr == b""
    if fmt == "json":  # SARIF writes the name as a URI, which is ASCII anyway
        assert GREEK in wide.stdout.decode("utf-8")
    assert json.loads(narrow.stdout.decode("ascii")) == json.loads(wide.stdout.decode("utf-8"))


def test_a_validate_report_stdout_can_encode_is_written_as_before(files, capsys):
    # The bytes a UTF-8 stdout gets are the report as rendered, character for character.
    assert main(["validate", str(files["valid"]), "--format", "json"]) == 0
    rendered = capsys.readouterr().out
    done = _run("validate", str(files["valid"]), "--format", "json", encoding="utf-8")
    assert done.stdout.decode("utf-8") == rendered
    assert f"ok-{GREEK}.xml" in rendered


def test_a_validate_report_stdout_cannot_take_exits_2_with_a_message(files, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdout", _FullStream())
    code = main(["validate", str(files["valid"])])
    monkeypatch.undo()
    assert code == 2  # not 0: nobody received the verdict
    err = capsys.readouterr().err
    assert err == "pydiggs validate: cannot write the report: No space left on device\n"


# --- convert ----------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_convert_summary_stdout_cannot_encode_is_written_with_escapes(files, tmp_path, fmt):
    narrow_out, wide_out = tmp_path / f"a-{GREEK}.xml", tmp_path / f"b-{GREEK}.xml"
    narrow = _run(
        "convert", str(files["ags"]), "-o", str(narrow_out), "--format", fmt, encoding="ascii"
    )
    wide = _run(
        "convert", str(files["ags"]), "-o", str(wide_out), "--format", fmt, encoding="utf-8"
    )
    assert narrow.returncode == wide.returncode == 0 and narrow.stderr == b""
    assert narrow_out.read_bytes() == wide_out.read_bytes()
    text = narrow.stdout.decode("ascii")
    if fmt == "text":
        assert "site-\\u03c6\\u2264.ags -> " in text and "a-\\u03c6\\u2264.xml" in text
    else:
        summary = json.loads(text)
        assert summary["output_path"] == str(narrow_out)
        assert summary == {
            **json.loads(wide.stdout.decode("utf-8")),
            "output_path": str(narrow_out),
        }


def test_a_convert_summary_stdout_cannot_take_exits_2_with_a_message(
    files, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdout", _FullStream())
    code = main(["convert", str(files["ags"]), "-o", str(tmp_path / "s.xml")])
    monkeypatch.undo()
    assert code == 2
    err = capsys.readouterr().err
    assert err == "pydiggs convert: cannot write the summary: No space left on device\n"
    assert (tmp_path / "s.xml").is_file()  # the conversion itself was written


# --- the shared writer ------------------------------------------------------------------


def test_the_shared_writer_keeps_what_the_stream_can_encode():
    body = f"name {GREEK}\n"
    assert _stdout.encodable(body, json_report=False, encoding="utf-8") == body
    assert _stdout.encodable(body, json_report=False, encoding="ascii") == "name \\u03c6\\u2264\n"
    document = json.dumps({"name": GREEK}, ensure_ascii=False)
    escaped = _stdout.encodable(document, json_report=True, encoding="cp1252")
    assert escaped.isascii() and json.loads(escaped) == {"name": GREEK}
    assert _stdout.encodable(body, json_report=False, encoding="no-such-codec") == body


class _FullStream:
    """A stdout whose device is full: the text is taken, and flushing it fails."""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        raise OSError(28, "No space left on device")
