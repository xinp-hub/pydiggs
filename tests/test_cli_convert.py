"""``pydiggs convert``: outputs, exit codes, refusals and the help text."""

from __future__ import annotations

import dataclasses
import errno
import json
import os
import re
import stat
import sys
import threading
from pathlib import Path

import pytest

import pydiggs
from pydiggs import _convert_cli, ags4, convert
from pydiggs.cli import main

ROOT = Path(__file__).resolve().parent.parent
AGS4 = ROOT / "tests" / "fixtures" / "ags4"
#: Converts with losses but no error (mapping table 1.0).
SITE = AGS4 / "convert_tier_a.ags"


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["convert", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_the_c6_acceptance_command(tmp_path, capsys):
    out_xml, report = tmp_path / "site.xml", tmp_path / "r.json"
    code, out, err = _run(capsys, str(SITE), "-o", str(out_xml), "--report", str(report))
    assert (code, err) == (0, "")
    assert out_xml.read_bytes() == convert.ags4_to_diggs(SITE).xml
    assert main(["validate", str(out_xml)]) == 0
    document = json.loads(report.read_text("utf-8"))
    assert document["files"][0]["path"] == "convert_tier_a.ags"
    assert document["files"][0]["format"] == "ags4"
    assert "loss(es)" in out and "output valid" in out
    assert "convert.unmapped-heading" in out


def test_outputs_are_ordinary_files_and_no_temporary_is_left(tmp_path, capsys):
    out_xml = tmp_path / "site.xml"
    assert _run(capsys, str(SITE), "-o", str(out_xml))[0] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["site.xml"]
    if sys.platform != "win32":
        mask = os.umask(0)
        os.umask(mask)
        assert stat.S_IMODE(out_xml.stat().st_mode) == 0o666 & ~mask


def test_json_summary_has_no_output_bytes(tmp_path, capsys):
    code, out, _ = _run(capsys, str(SITE), "-o", str(tmp_path / "s.xml"), "--format", "json")
    assert code == 0
    summary = json.loads(out)
    assert "output_base64" not in summary
    assert summary["output_path"] == str(tmp_path / "s.xml")
    assert summary["to"] == "diggs" and summary["diggs_version"] == "3.0.0"


def test_fail_on_loss_exits_1(tmp_path, capsys):
    code, _, _ = _run(capsys, str(SITE), "-o", str(tmp_path / "s.xml"), "--fail-on", "loss")
    assert code == 1  # the fixture has losses
    assert (tmp_path / "s.xml").exists()  # the output is still written


def test_an_existing_output_needs_force(tmp_path, capsys):
    target = tmp_path / "s.xml"
    target.write_bytes(b"keep")
    code, _, err = _run(capsys, str(SITE), "-o", str(target))
    assert code == 2 and "--force" in err
    assert target.read_bytes() == b"keep"
    assert _run(capsys, str(SITE), "-o", str(target), "--force")[0] == 0
    assert target.read_bytes().startswith(b"<?xml")


def test_the_input_is_never_overwritten(tmp_path, capsys):
    source = tmp_path / "site.ags"
    source.write_bytes(SITE.read_bytes())
    for argv in (
        ["-o", str(source), "--force"],
        ["-o", str(tmp_path / "x.xml"), "--report", str(source), "--force"],
        ["-o", str(tmp_path / "." / "site.ags"), "--force"],
    ):
        code, _, err = _run(capsys, str(source), *argv)
        assert code == 2 and "over the input" in err
    assert source.read_bytes() == SITE.read_bytes()


def test_output_and_report_must_differ(tmp_path, capsys):
    target = str(tmp_path / "s.xml")
    code, _, err = _run(capsys, str(SITE), "-o", target, "--report", target)
    assert code == 2 and "two outputs" in err


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_output_is_refused(tmp_path, capsys):
    real = tmp_path / "real.xml"
    real.write_bytes(b"keep")
    link = tmp_path / "link.xml"
    link.symlink_to(real)
    code, _, err = _run(capsys, str(SITE), "-o", str(link), "--force")
    assert code == 2 and "symlink" in err
    assert real.read_bytes() == b"keep"


def test_without_hard_links_an_output_that_appears_is_never_replaced(tmp_path, capsys, monkeypatch):
    """Where links are unsupported, another process's new OUT survives a run without --force."""
    target = tmp_path / "s.xml"
    created: list[bool] = []

    def no_links(src, dst, *args, **kwargs):
        raise OSError(errno.EPERM, "links are not supported here")

    real_replace = Path.replace

    def racing_replace(self, other):
        # Another process creates OUT, exclusively, just before our move.
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            created.append(False)
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"theirs")
            created.append(True)
        return real_replace(self, other)

    monkeypatch.setattr(_convert_cli.os, "link", no_links)
    monkeypatch.setattr(Path, "replace", racing_replace)
    code, _, err = _run(capsys, str(SITE), "-o", str(target))
    if any(created):  # their file appeared first: it is kept and we refuse
        assert code == 2 and "--force" in err
        assert target.read_bytes() == b"theirs"
    else:  # we claimed the name first: their exclusive create failed, ours is written
        assert code == 0
        assert target.read_bytes().startswith(b"<?xml")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.xml"]


def test_without_hard_links_an_existing_output_is_refused_and_force_replaces(
    tmp_path, capsys, monkeypatch
):
    def no_links(src, dst, *args, **kwargs):
        raise OSError(errno.ENOTSUP, "links are not supported here")

    monkeypatch.setattr(_convert_cli.os, "link", no_links)
    target = tmp_path / "s.xml"
    assert _run(capsys, str(SITE), "-o", str(target))[0] == 0
    assert target.read_bytes().startswith(b"<?xml")
    target.write_bytes(b"keep")
    with pytest.raises(_convert_cli._UsageError, match="--force"):
        _convert_cli._write_atomically(target, b"new", force=False)
    assert target.read_bytes() == b"keep"
    _convert_cli._write_atomically(target, b"new", force=True)
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.xml"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_input_is_refused_without_blocking(tmp_path, capsys):
    fifo = tmp_path / "in.ags"
    os.mkfifo(fifo)
    outcome: list[object] = []

    def run() -> None:
        try:
            _convert_cli._read_input(str(fifo), 1024)
        except _convert_cli._UsageError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(10)
    if worker.is_alive():  # opened without O_NONBLOCK: release it, then fail
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(10)
        pytest.fail("opening a FIFO input blocked")
    assert len(outcome) == 1 and "could not be read" in str(outcome[0])
    code, out, err = _run(capsys, str(fifo), "-o", str(tmp_path / "o.xml"))
    assert code == 2 and out == "" and "could not be read" in err
    assert not (tmp_path / "o.xml").exists()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<Diggs/>", "not an AGS4 file"),
        (b"\x1f\x8b\x08\x00compressed", "compressed"),
    ],
)
def test_unusable_inputs_exit_2(tmp_path, capsys, data, message):
    source = tmp_path / "in.ags"
    source.write_bytes(data)
    code, out, err = _run(capsys, str(source), "-o", str(tmp_path / "o.xml"))
    assert code == 2 and message in err and out == ""
    assert not (tmp_path / "o.xml").exists()


def test_missing_large_and_bad_options_exit_2(tmp_path, capsys):
    code, _, err = _run(capsys, str(tmp_path / "missing.ags"), "-o", str(tmp_path / "o.xml"))
    assert code == 2 and "cannot read" in err
    code, _, err = _run(capsys, str(SITE), "-o", str(tmp_path / "o.xml"), "--max-bytes", "10")
    assert code == 2 and "larger than allowed" in err
    for argv in (
        ["--max-bytes", "0"],
        ["--time-budget", "0"],
        ["--to", "csv"],
        ["--diggs-version", "2.6"],
        ["--ags-version", "4.0"],
        ["--ags-version", "4.2"],  # only with --to ags4
        ["--to", "ags4", "--diggs-version", "3.0.0"],  # only with --to diggs
        ["--to", "ags4", "--residual", str(tmp_path / "r.ags")],  # AGS4 to DIGGS only
    ):
        with pytest.raises(SystemExit) as exc:
            main(["convert", str(SITE), "-o", str(tmp_path / "o.xml"), *argv])
        assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["convert", str(SITE)])  # -o is required
    assert exc.value.code == 2
    capsys.readouterr()


def test_a_tiny_time_budget_gives_a_partial_output_and_exit_1(tmp_path, capsys):
    code, out, _ = _run(capsys, str(SITE), "-o", str(tmp_path / "s.xml"), "--time-budget", "1e-9")
    assert code == 1
    assert "INCOMPLETE" in out


def test_help_texts(capsys):
    with pytest.raises(SystemExit):
        main(["convert", "--help"])
    # Python 3.14's argparse colours help when FORCE_COLOR is set (as in CI).
    help_text = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    for option in (
        "-o OUT",
        "--report FILE",
        "--to {diggs,ags4}",
        "--ags-version {4.2,4.1.1}",
        "--residual FILE",
        "--fail-on",
        "--force",
    ):
        assert option in help_text
    assert "not an AGS-approved tool" in help_text
    assert "Exit status: 0" in help_text
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "pydiggs convert --help" in capsys.readouterr().out
    assert _convert_cli.build_parser().prog == "pydiggs convert"


def test_the_version_in_the_report_is_this_pydiggs(tmp_path, capsys):
    report = tmp_path / "r.json"
    _run(capsys, str(SITE), "-o", str(tmp_path / "s.xml"), "--report", str(report))
    assert json.loads(report.read_text())["tool"]["version"] == pydiggs.__version__


# --- DIGGS to AGS4 and the residual file -------------------------------------------------


@pytest.fixture
def site_xml(tmp_path) -> Path:
    path = tmp_path / "in" / "site.xml"
    path.parent.mkdir()
    xml = convert.ags4_to_diggs(SITE).xml
    assert xml is not None
    path.write_bytes(xml)
    return path


@pytest.mark.parametrize("version", [None, "4.2", "4.1.1"])
def test_diggs_to_ags4(site_xml, tmp_path, capsys, version):
    out = tmp_path / "site.ags"
    extra = [] if version is None else ["--ags-version", version]
    code, text, err = _run(capsys, str(site_xml), "-o", str(out), "--to", "ags4", *extra)
    expected = convert.diggs_to_ags4(site_xml, ags_version=version or "4.2")
    assert expected.ags4 is not None
    assert out.read_bytes() == ags4.dumps(expected.ags4)
    assert err == ""
    assert f"AGS4 {version or '4.2'}" in text and "loss(es)" in text
    assert code == (1 if expected.validation and not expected.validation.ok else 0)


def test_diggs_to_ags4_json_summary(site_xml, tmp_path, capsys):
    out = tmp_path / "site.ags"
    _, text, _ = _run(capsys, str(site_xml), "-o", str(out), "--to", "ags4", "--format", "json")
    summary = json.loads(text)
    assert "output_base64" not in summary and "residual_base64" not in summary
    assert (summary["to"], summary["ags_version"], summary["output_path"]) == (
        "ags4",
        "4.2",
        str(out),
    )
    assert summary["residual_path"] is None


def test_what_is_not_diggs_exits_2(tmp_path, capsys):
    code, _, err = _run(capsys, str(SITE), "-o", str(tmp_path / "o.ags"), "--to", "ags4")
    assert code == 2 and "not well-formed XML" in err
    assert not (tmp_path / "o.ags").exists()


def test_the_residual_file(tmp_path, capsys):
    out, residual = tmp_path / "site.xml", tmp_path / "left.ags"
    code, text, _ = _run(capsys, str(SITE), "-o", str(out), "--residual", str(residual))
    assert code == 0
    expected = convert.ags4_to_diggs(SITE)
    assert expected.residual is not None
    assert residual.read_bytes() == ags4.dumps(expected.residual)
    assert f"residual: {residual}" in text
    # It is an output like the others: never over the input, replaced only with --force.
    code, _, err = _run(capsys, str(SITE), "-o", str(out), "--residual", str(residual), "--force")
    assert code == 0
    code, _, err = _run(capsys, str(SITE), "-o", str(tmp_path / "x.xml"), "--residual", str(SITE))
    assert code == 2 and "over the input" in err
    code, _, err = _run(capsys, str(SITE), "-o", str(tmp_path / "x.xml"), "--residual", str(out))
    assert code == 2 and "--force" in err


def test_no_residual_file_when_nothing_is_left_over(tmp_path, capsys, monkeypatch):
    real = convert.ags4_to_diggs
    monkeypatch.setattr(
        convert,
        "ags4_to_diggs",
        lambda *a, **k: dataclasses.replace(real(*a, **k), residual=ags4.File(groups=[])),
    )
    residual = tmp_path / "left.ags"
    code, text, _ = _run(
        capsys, str(SITE), "-o", str(tmp_path / "s.xml"), "--residual", str(residual)
    )
    assert code == 0 and not residual.exists()
    assert "residual: none" in text


def test_an_error_in_the_source_exits_1(tmp_path, capsys):
    # site_tier_a has a GRAG row with fractions only: mapping table 1.0 converts no GRAG
    # fraction, so the row and its GRAT readings are reported incomplete (an error).
    out = tmp_path / "s.xml"
    code, text, _ = _run(capsys, str(AGS4 / "site_tier_a.ags"), "-o", str(out))
    assert code == 1 and out.exists()
    assert "error convert.incomplete" in text and "output valid" in text
