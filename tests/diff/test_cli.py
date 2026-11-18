"""``pydiggs diff``: the two reports, the exit codes, the refusals and the help text."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import pydiggs
from pydiggs import diff
from pydiggs.cli import main
from pydiggs.diff import _cli, _text

from ._helpers import OFFICIAL, ROOT, schema_errors

AEROMAG = OFFICIAL / "3.0" / "Aeromag.xml"
OTHER = OFFICIAL / "3.0" / "MWDExample.xml"


def _run(capsys, *argv: str | Path) -> tuple[int, str, str]:
    code = main(["diff", *(str(a) for a in argv)])
    out, err = capsys.readouterr()
    return code, out, err


def _copy(tmp_path: Path, old: str, new: str, name: str = "changed.xml") -> Path:
    """A copy of the Aeromag file with one piece of text replaced."""
    text = AEROMAG.read_text("utf-8")
    assert text.count(old) == 1
    target = tmp_path / name
    target.write_text(text.replace(old, new), "utf-8")
    return target


@pytest.fixture
def changed(tmp_path) -> Path:
    return _copy(tmp_path, "<creationDate>2022-08-17<", "<creationDate>2022-08-18<")


@pytest.fixture
def reformatted(tmp_path) -> Path:
    return _copy(tmp_path, '<totalSurveyArea uom="km2">1<', '<totalSurveyArea uom="km2">1.0<')


@pytest.fixture
def renamed(tmp_path) -> Path:
    """The Aeromag file with the ``gml:id`` of its test changed and nothing else."""
    return _copy(tmp_path, '<Test gml:id="amt">', '<Test gml:id="amt-2">', "renamed.xml")


# --- exit codes ---------------------------------------------------------------------------


def test_a_file_against_itself_exits_0(capsys):
    code, out, err = _run(capsys, AEROMAG, AEROMAG)
    assert (code, err) == (0, "")
    assert out == diff.compare(AEROMAG, AEROMAG).to_text()  # nothing in it to escape
    assert "The two files are identical." in out


def test_a_changed_copy_exits_1_and_names_the_change(changed, capsys):
    code, out, err = _run(capsys, AEROMAG, changed)
    assert (code, err) == (1, "")
    assert "The two files differ." in out
    assert "creation_date: 2022-08-17 -> 2022-08-18" in out
    assert "document: 0 added, 0 removed, 1 changed, 0 unchanged" in out


def test_the_acceptance_command_through_the_console_script(changed):
    def run(*paths: Path) -> int:
        command = [sys.executable, "-m", "pydiggs", "diff", *(str(p) for p in paths)]
        return subprocess.run(command, capture_output=True, cwd=ROOT, check=False).returncode  # noqa: S603

    assert run(AEROMAG, AEROMAG) == 0
    assert run(AEROMAG, changed) == 1


@pytest.mark.parametrize(
    ("option", "value", "limit", "rule"),
    [
        ("--max-changes", "1", "max_changes", "diff.limit"),
        ("--time-budget", "0.000001", "time_budget", "diff.timeout"),
    ],
)
def test_an_incomplete_comparison_exits_1(option, value, limit, rule, capsys):
    code, out, err = _run(capsys, AEROMAG, OTHER, option, value)
    assert (code, err) == (1, "")
    assert "The comparison is incomplete (stopped by " in out
    assert limit in out and f"note {rule}:" in out


def test_identical_files_cut_short_by_the_time_budget_exit_1(capsys):
    # Nothing differs in what was compared, but the diff cannot say the files are the same.
    code, out, _ = _run(capsys, AEROMAG, AEROMAG, "--time-budget", "0.000001", "--format", "json")
    document = json.loads(out)
    assert code == 1
    assert (document["identical"], document["complete"]) == (False, False)
    assert "time_budget" in document["limits_hit"]


def test_a_file_that_vanishes_after_the_comparison_failed_still_exits_2(tmp_path, capsys):
    from pydiggs.errors import DocumentError

    def refuse(*_args, **_kwargs):
        raise DocumentError("not-diggs", "not DIGGS")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(diff, "compare", refuse)
        code, _, err = _run(capsys, tmp_path / "gone.xml", AEROMAG)
    assert (code, err) == (2, "pydiggs diff: an input is not a DIGGS file: not DIGGS\n")


def test_a_missing_file_exits_2_and_is_named(tmp_path, capsys):
    missing = tmp_path / "nope.xml"
    for pair in ((AEROMAG, missing), (missing, AEROMAG)):
        code, out, err = _run(capsys, *pair)
        assert (code, out) == (2, "")
        assert err.startswith(f"pydiggs diff: cannot read {missing}: ")
        assert len(err.splitlines()) == 1


def test_a_directory_and_a_file_over_the_size_limit_exit_2(tmp_path, capsys):
    code, _, err = _run(capsys, AEROMAG, tmp_path)
    assert code == 2 and f"cannot read {tmp_path}" in err
    code, _, err = _run(capsys, AEROMAG, AEROMAG, "--max-bytes", "100")
    assert code == 2 and err.startswith(f"pydiggs diff: cannot read {AEROMAG}: ")


@pytest.mark.parametrize(
    "content",
    [b"<a><b/></a>", b'"GROUP","PROJ"\r\n', b"", b"not xml at all"],
    ids=["other-xml", "ags4", "empty", "text"],
)
def test_a_file_that_is_not_diggs_exits_2_and_is_named(content, tmp_path, capsys):
    other = tmp_path / "other.xml"
    other.write_bytes(content)
    for pair in ((AEROMAG, other), (other, AEROMAG), (other, other)):
        code, out, err = _run(capsys, *pair)
        assert (code, out) == (2, "")
        assert err.startswith(f"pydiggs diff: {other} is not a DIGGS file: ")


def test_a_control_character_in_a_path_is_escaped_on_stderr(tmp_path, capsys):
    code, _, err = _run(capsys, AEROMAG, tmp_path / "a\x1b[2Jb.xml")
    assert code == 2
    assert "\x1b" not in err and "\\x1b[2Jb.xml" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--rel-tol", "-1"],
        ["--rel-tol", "nan"],
        ["--abs-tol", "inf"],
        ["--abs-tol", "x"],
        ["--max-changes", "-1"],
        ["--max-changes", "1.5"],
        ["--time-budget", "0"],
        ["--time-budget", "nan"],
        ["--time-budget", "86401"],
        ["--max-bytes", "0"],
        ["--match", "name"],
        ["--format", "sarif"],
        ["--nope"],
    ],
    ids=" ".join,
)
def test_a_bad_option_value_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["diff", str(AEROMAG), str(AEROMAG), *argv])
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "pydiggs diff: error:" in re.sub(r"\x1b\[[0-9;]*m", "", err)


@pytest.mark.parametrize("argv", [[], [str(AEROMAG)], [str(AEROMAG)] * 3])
def test_it_takes_exactly_two_files(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["diff", *argv])
    assert exc.value.code == 2
    capsys.readouterr()


# --- the reports --------------------------------------------------------------------------


def test_json_is_the_diggs_diff_document_of_compare(changed, capsys):
    code, out, err = _run(capsys, AEROMAG, changed, "--format", "json")
    assert (code, err) == (1, "")
    document = json.loads(out)
    assert schema_errors(document) == []
    assert (document["format"], document["schema_version"]) == ("diggs-diff", "1.0-beta.1")
    assert document["tool"]["version"] == pydiggs.__version__
    assert (document["old"]["name"], document["new"]["name"]) == ("Aeromag.xml", "changed.xml")
    assert out == diff.compare(AEROMAG, changed).to_json(indent=2) + "\n"
    assert diff.Diff.from_dict(document) == diff.compare(AEROMAG, changed)


def test_json_of_identical_files_validates_and_exits_0(capsys):
    code, out, _ = _run(capsys, AEROMAG, AEROMAG, "--format", "json")
    document = json.loads(out)
    assert code == 0
    assert schema_errors(document) == []
    assert document["identical"] is True and document["changes"] == []


def test_every_option_reaches_compare(changed, capsys):
    argv = ["--match", "natural", "--rel-tol", "0.01", "--abs-tol", "0.5", "--max-changes", "7"]
    code, out, _ = _run(capsys, AEROMAG, changed, *argv, "--time-budget", "600", "--format", "json")
    assert code == 1
    assert json.loads(out)["options"] == {
        "match": "natural",
        "rel_tol": 0.01,
        "abs_tol": 0.5,
        "max_changes": 7,
        "time_budget": 600.0,
    }


def test_the_defaults_are_those_of_compare(capsys):
    _, out, _ = _run(capsys, AEROMAG, AEROMAG, "--format", "json")
    assert json.loads(out)["options"] == dict(diff.compare(AEROMAG, AEROMAG).options)


def test_a_tolerance_makes_a_small_change_no_change(tmp_path, capsys):
    near = _copy(tmp_path, '<totalSurveyArea uom="km2">1<', '<totalSurveyArea uom="km2">1.001<')
    assert _run(capsys, AEROMAG, near)[0] == 1
    assert _run(capsys, AEROMAG, near, "--rel-tol", "0.01")[0] == 0
    assert _run(capsys, AEROMAG, near, "--abs-tol", "0.01")[0] == 0


def test_verbose_lists_the_reformatted_values(reformatted, capsys):
    code, out, _ = _run(capsys, AEROMAG, reformatted)
    assert code == 0  # the same value, written differently: identical
    assert "1 value(s) were only reformatted" in out and "; not listed." in out
    assert "reformatted: " not in out

    code, out, _ = _run(capsys, AEROMAG, reformatted, "--verbose")
    assert code == 0
    result = diff.compare(AEROMAG, reformatted, show_reformatted=True)
    assert out == _text.render(result, markup=False)
    assert "reformatted: 1 km2 -> 1.0 km2" in out and "; not listed" not in out

    _, out, _ = _run(capsys, AEROMAG, reformatted, "--verbose", "--format", "json")
    document = json.loads(out)
    assert schema_errors(document) == []
    assert [c["op"] for c in document["changes"]] == ["reformatted"]


_RENAMED_LINE = "1 record(s) have a different gml:id (matched by natural key; not a change)"


def test_a_copy_with_a_renamed_id_exits_0_and_says_so(renamed, capsys):
    code, out, err = _run(capsys, AEROMAG, renamed)
    assert (code, err) == (0, "")
    assert "The two files are identical." in out
    assert f"{_RENAMED_LINE}; not listed." in out
    assert "amt-2" not in out and "gml_id" not in out

    code, out, _ = _run(capsys, AEROMAG, renamed, "--format", "json")
    document = json.loads(out)
    assert code == 0 and schema_errors(document) == []
    assert (document["identical"], document["renamed"], document["changes"]) == (True, 1, [])


def test_verbose_lists_the_renamed_ids(renamed, capsys):
    code, out, _ = _run(capsys, AEROMAG, renamed, "--verbose")
    assert code == 0
    result = diff.compare(AEROMAG, renamed, show_reformatted=True, show_renamed=True)
    assert out == _text.render(result, markup=False)
    assert f"{_RENAMED_LINE}." in out
    assert "renamed measurements amt-2" in out and "matched by natural key (amt -> amt-2)" in out
    assert "    gml_id: renamed: amt -> amt-2\n" in out

    _, out, _ = _run(capsys, AEROMAG, renamed, "--verbose", "--format", "json")
    document = json.loads(out)
    assert schema_errors(document) == []
    (change,) = document["changes"]
    assert (change["op"], change["old_key"], change["new_key"]) == ("renamed", "amt", "amt-2")
    assert [(v["op"], v["path"]) for v in change["values"]] == [("renamed", "gml_id")]


def test_matching_by_gml_id_only_makes_a_renamed_record_removed_and_added(renamed, capsys):
    code, out, _ = _run(capsys, AEROMAG, renamed, "--match", "gml-id")
    assert code == 1
    assert "The two files differ." in out
    assert "removed measurements amt" in out and "added measurements amt-2" in out
    assert "have a different gml:id" not in out


def test_a_renamed_id_with_a_real_change_exits_1(tmp_path, capsys):
    text = AEROMAG.read_text("utf-8")
    both = tmp_path / "both.xml"
    both.write_text(
        text.replace('<Test gml:id="amt">', '<Test gml:id="amt-2">').replace(
            "<investigationTarget>Natural Ground<", "<investigationTarget>Fill<"
        ),
        "utf-8",
    )
    code, out, _ = _run(capsys, AEROMAG, both)
    assert code == 1 and "The two files differ." in out
    assert "gml_id" not in out and f"{_RENAMED_LINE}; not listed." in out
    code, out, _ = _run(capsys, AEROMAG, both, "--verbose")
    assert code == 1
    assert "changed measurements amt-2" in out and "    gml_id: renamed: amt -> amt-2\n" in out


def test_markup_and_emoji_codes_in_a_value_are_printed_as_they_are(tmp_path, capsys):
    name = "[red]Survey[/red] [link=https://example.com]x[/link] :warning: \\[b]"
    marked = _copy(tmp_path, "Aeromag Survey Area", name)
    code, out, _ = _run(capsys, AEROMAG, marked)
    assert code == 1
    assert f"name: Aeromag Survey Area -> {name}" in out
    assert "\x1b" not in out
    # The library's text is escaped for a rich console; stdout reads no markup.
    result = diff.compare(AEROMAG, marked)
    assert "\\[red]Survey\\[/red]" in result.to_text()
    assert out == _text.render(result, markup=False)


def test_markup_split_over_two_values_hides_nothing(tmp_path, capsys):
    # Printed through a rich console, "[black on black" in one value and "]" in the next
    # would read as one tag and swallow what lies between. The command prints plain text.
    text = AEROMAG.read_text("utf-8")
    split = tmp_path / "split.xml"
    split.write_text(
        text.replace("<sensorMix>receivers<", "<sensorMix>[black on black<").replace(
            'uom="km2">1<', 'uom="k]m2">1<'
        ),
        "utf-8",
    )
    code, out, _ = _run(capsys, AEROMAG, split)
    assert code == 1
    assert "    params.sensorMix: receivers -> [black on black\n" in out
    assert "    params.totalSurveyArea: 1 km2 -> 1 k]m2\n" in out


def test_the_plain_report_differs_from_to_text_only_in_markup_escapes(changed):
    for new, verbose in ((changed, False), (OTHER, True), (AEROMAG, False)):
        result = diff.compare(AEROMAG, new, show_reformatted=verbose)
        plain = _text.render(result, markup=False)
        assert plain.replace("[", "") == result.to_text().replace("\\[", "").replace("[", "")


def test_control_characters_stay_spelled_out(tmp_path, capsys):
    override, zero_width, next_line = chr(0x202E), chr(0x200B), chr(0x85)
    hostile = _copy(tmp_path, "Aeromag Survey Area", f"A{override}B{zero_width}C&#x85;D")
    code, out, _ = _run(capsys, AEROMAG, hostile)
    assert code == 1
    assert "name: Aeromag Survey Area -> A\\u202eB\\u200bC\\x85D" in out
    assert not {override, zero_width, next_line} & set(out)


# --- -o / --output ------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-o", "--output"])
@pytest.mark.parametrize("fmt", ["text", "json"])
def test_output_goes_to_the_file_and_not_to_stdout(flag, fmt, changed, tmp_path, capsys):
    target = tmp_path / f"report.{fmt}"
    code, out, err = _run(capsys, AEROMAG, changed, "--format", fmt, flag, target)
    assert (code, out, err) == (1, "", "")
    written = target.read_bytes()
    assert b"\r" not in written
    _, stdout, _ = _run(capsys, AEROMAG, changed, "--format", fmt)
    assert written.decode("utf-8") == stdout
    if fmt == "json":
        assert schema_errors(json.loads(written)) == []


def test_an_existing_output_is_replaced(changed, tmp_path, capsys):
    target = tmp_path / "report.txt"
    target.write_text("old report, longer than nothing\n" * 400, "utf-8")
    assert _run(capsys, AEROMAG, AEROMAG, "-o", target)[0] == 0
    assert target.read_text("utf-8") == diff.compare(AEROMAG, AEROMAG).to_text()


def test_output_over_an_input_is_refused(changed, capsys):
    before = changed.read_bytes()
    for pair in ((changed, AEROMAG), (AEROMAG, changed)):
        code, out, err = _run(capsys, *pair, "-o", changed)
        assert (code, out) == (2, "")
        assert "over an input" in err
    assert changed.read_bytes() == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_output_refuses_a_symlink(tmp_path, capsys):
    (tmp_path / "link.txt").symlink_to(tmp_path / "target.txt")
    code, out, err = _run(capsys, AEROMAG, AEROMAG, "-o", tmp_path / "link.txt")
    assert (code, out) == (2, "")
    assert "symlink" in err and not (tmp_path / "target.txt").exists()


def test_an_output_that_cannot_be_written_exits_2(tmp_path, capsys):
    code, out, err = _run(capsys, AEROMAG, AEROMAG, "-o", tmp_path / "no" / "such" / "dir.txt")
    assert (code, out) == (2, "")
    assert err.startswith("pydiggs diff: cannot write ")


# --- help, wiring and imports -------------------------------------------------------------


def test_help_names_every_option_and_the_exit_codes(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["diff", "--help"])
    assert exc.value.code == 0
    help_text = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out).split())
    for option in (
        "OLD NEW",
        "--format {text,json}",
        "-o FILE",
        "--match {auto,gml-id,natural}",
        "--rel-tol R",
        "--abs-tol A",
        "--max-changes N",
        "--time-budget SECONDS",
        "--max-bytes N",
        "--verbose",
    ):
        assert option in help_text, option
    assert "Exit status: 0" in help_text
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "pydiggs diff --help" in capsys.readouterr().out
    assert _cli.build_parser().prog == "pydiggs diff"


def test_the_parser_defaults_are_the_defaults_of_compare():
    import inspect

    args = _cli.build_parser().parse_args(["a", "b"])
    parameters = inspect.signature(diff.compare).parameters
    for name in ("match", "rel_tol", "abs_tol", "max_changes", "time_budget", "max_bytes"):
        assert getattr(args, name) == parameters[name].default, name
    assert args.verbose is parameters["show_reformatted"].default is False
    assert args.verbose is parameters["show_renamed"].default is False
    assert _cli.build_parser().get_default("format") == "text"


def test_starting_the_cli_does_not_import_the_comparison_or_rich(tmp_path):
    code = (
        "import sys\n"
        "from pydiggs import cli\n"
        "watched = ('pydiggs.diff', 'pydiggs.diff._cli', 'rich')\n"
        "print(sorted(m for m in watched if m in sys.modules))\n"
        "argv = ['diff', sys.argv[1], sys.argv[1], '-o', sys.argv[2]]\n"
        "print(cli.main(argv), 'pydiggs.diff._cli' in sys.modules, 'rich' in sys.modules)\n"
    )
    out = subprocess.run(  # noqa: S603  # our own interpreter and code
        [sys.executable, "-c", code, str(AEROMAG), str(tmp_path / "d.txt")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout.splitlines()
    # Nothing at start-up; the comparison on demand; rich not even for the text report.
    assert out == ["[]", "0 True False"]


# --- a report that cannot be written to stdout --------------------------------------------


class _FullStream:
    """A stdout whose device is full: the write is accepted, the flush fails."""

    def __init__(self) -> None:
        self.flushes = 0

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        self.flushes += 1
        raise OSError(28, "No space left on device")


def test_a_report_stdout_cannot_take_exits_2_with_a_message(monkeypatch, capsys):
    stream = _FullStream()
    monkeypatch.setattr(sys, "stdout", stream)
    code = main(["diff", str(AEROMAG), str(AEROMAG)])
    monkeypatch.undo()
    assert code == 2  # not 0: nobody received "identical"
    assert stream.flushes == 1
    err = capsys.readouterr().err
    assert err == "pydiggs diff: cannot write the report: No space left on device\n"


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="needs /dev/full (Linux)")
def test_stdout_redirected_to_a_full_device_exits_2_without_a_traceback():
    command = [sys.executable, "-m", "pydiggs", "diff", str(AEROMAG), str(AEROMAG)]
    with Path("/dev/full").open("w", encoding="utf-8") as full:
        done = subprocess.run(  # noqa: S603  # our own interpreter and code
            command, stdout=full, stderr=subprocess.PIPE, cwd=ROOT, check=False, text=True
        )
    assert done.returncode == 2
    assert "cannot write the report" in done.stderr
    assert "Traceback" not in done.stderr and "Exception ignored" not in done.stderr
