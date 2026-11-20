"""The video tutorials: the coverage gate, the specs, the captions and the docs embed.

Rendering needs VHS, ffmpeg and a voice model, so it runs in the docs workflow, not here.
What is tested here is everything that decides whether a video is right: every public
feature has a tutorial or a waiver, every terminal tutorial's commands still print what
its narration says (the rehearsal), and the caption and page markup are well formed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "tutorials"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location(f"tutorials_{name}", TOOLS / f"{name}.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOOLS))


spec = _load("spec")
check = _load("check")
build = _load("build")
hook = _load("mkdocs_tutorials")
files = _load("files")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / f"{name}.yml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD = """\
id: demo
title: A demo
summary: Shows the demo.
kind: terminal
files: []
scenes:
  - title: First
    say: This scene shows the demo command and what it prints on screen.
    run: echo hello | tr a-z A-Z
    expect: HELLO
"""


def test_gate_passes() -> None:
    assert check.problems() == []


@pytest.mark.slow
def test_every_terminal_tutorial_still_shows_what_it_says() -> None:
    found = []
    for path in spec.tutorial_paths():
        found += check.rehearse(spec.load_tutorial(path))
    assert found == []


def test_every_cli_subcommand_and_public_name_is_covered() -> None:
    manifest = spec.load_manifest()
    covered = {item for feature in manifest.features for item in feature.covers}
    assert spec.discover(manifest) <= covered


def test_a_valid_spec_loads(tmp_path: Path) -> None:
    tutorial = spec.load_tutorial(_write(tmp_path, "demo", GOOD))
    assert tutorial.transcript() == [
        ("First", "This scene shows the demo command and what it prints on screen.")
    ]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (("expect: HELLO\n", ""), "a command needs expect"),
        (("run: echo hello", "run: cat /Users/someone/file"), "absolute path"),
        (("run: echo hello", "run: echo token=abc123"), "secret"),
        (("kind: terminal", "kind: video"), "kind must be one of"),
        (("id: demo", "id: other"), "must match the file name"),
        (("expect: HELLO", "expect: '('"), "not a valid regex"),
        (
            (
                "say: This scene shows the demo command and what it prints on screen.",
                "say: Too short.",
            ),
            "narration is",
        ),
        (("    expect: HELLO\n", "    expect: HELLO\n    steps: [{goto: /}]\n"), "browser"),
    ],
)
def test_a_bad_spec_is_refused_with_a_reason(
    tmp_path: Path, change: tuple[str, str], reason: str
) -> None:
    old, new = change
    text = GOOD.replace(old, new, 1)
    with pytest.raises(spec.SpecError) as raised:
        spec.load_tutorial(_write(tmp_path, "demo", text))
    assert any(reason in problem for problem in raised.value.problems), raised.value.problems


def test_an_expect_that_the_typed_command_satisfies_is_refused(tmp_path: Path) -> None:
    text = GOOD.replace("run: echo hello", "run: ls -1 report.sarif").replace(
        "expect: HELLO", "expect: 'report\\.sarif'"
    )
    with pytest.raises(spec.SpecError, match="also matches the typed command"):
        spec.load_tutorial(_write(tmp_path, "demo", text))
    anchored = text.replace("'report\\.sarif'", "'(?m)^report\\.sarif\\s*$'")
    assert spec.load_tutorial(_write(tmp_path, "demo", anchored)).scenes[0].run


@pytest.mark.parametrize("field", ["title", "summary"])
def test_a_spec_without_a_title_or_summary_is_refused(tmp_path: Path, field: str) -> None:
    text = "\n".join(line for line in GOOD.splitlines() if not line.startswith(f"{field}:"))
    with pytest.raises(spec.SpecError, match=f"{field} is required"):
        spec.load_tutorial(_write(tmp_path, "demo", text + "\n"))


def test_glob_placeholders_name_exactly_one_file(tmp_path: Path) -> None:
    (tmp_path / "dist").mkdir()
    wheel = tmp_path / "dist" / "pydiggs-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"")
    assert spec.expand("--wheel {glob:dist/*.whl}", work=tmp_path) == f"--wheel {wheel}"
    assert spec.expand("?w={globname:dist/*.whl}&p={port}", work=tmp_path, port=8) == (
        "?w=pydiggs-9.9.9-py3-none-any.whl&p=8"
    )
    (tmp_path / "dist" / "pydiggs-0.0.1-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(spec.SpecError, match="matches 2 files"):
        spec.expand("{glob:dist/*.whl}", work=tmp_path)


def test_the_recorded_shell_runs_the_project_not_the_renderer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TUTORIALS_PROJECT_BIN", str(tmp_path))
    renderer = str(tmp_path / "renderer")
    monkeypatch.setenv("PATH", renderer)
    assert build.project_env()["PATH"].split(os.pathsep) == [str(tmp_path), renderer]


def test_a_browser_upload_must_stay_in_the_repo_or_work_dir(tmp_path: Path) -> None:
    text = """\
id: web
title: Web
summary: A page.
kind: browser
serve: python3 -m http.server {port}
ready: http://127.0.0.1:{port}/
scenes:
  - say: We pick a file from somewhere it should not come from.
    steps:
      - upload: {selector: '#file', file: 'secrets.xml'}
"""
    with pytest.raises(spec.SpecError, match="upload a file under"):
        spec.load_tutorial(_write(tmp_path, "web", text))


def test_sentences_and_pronunciation() -> None:
    said = "pyDIGGS checks DIGGS files. It writes SARIF! Then it stops."
    assert spec.sentences(said) == [
        "pyDIGGS checks DIGGS files.",
        "It writes SARIF!",
        "Then it stops.",
    ]
    pronounce = {"pyDIGGS": "pie diggs", "DIGGS": "diggs", "SARIF": "sarriff"}
    assert spec.spoken(said, pronounce) == (
        "pie diggs checks diggs files. It writes sarriff! Then it stops."
    )


def test_caption_cues_are_two_short_lines_and_fill_the_sentence() -> None:
    sentence = (
        "Each finding gives the line number, the rule that failed, and what is wrong with"
        " the file, so you can fix it at the source."
    )
    cues = build.cues_for(sentence, start=1.0, length=6.0)
    assert cues[0].start == 1.0
    assert cues[-1].end == pytest.approx(7.0)
    for cue in cues:
        lines = build.wrap(cue.text)
        assert len(lines) <= 2
        assert all(len(line) <= build.CAPTION_LINE for line in lines)
    assert " ".join(c.text for c in cues) == sentence


def test_webvtt_is_escaped_and_timed(tmp_path: Path) -> None:
    target = tmp_path / "c.vtt"
    build.write_vtt(target, [build.Cue(0.4, 61.25, "a <b> & c")])
    assert target.read_text(encoding="utf-8") == (
        "WEBVTT\n\n1\n00:00:00.400 --> 00:01:01.250\na &lt;b&gt; &amp; c\n"
    )


def test_tape_types_the_command_and_waits_for_the_expected_screen(tmp_path: Path) -> None:
    scene = spec.Scene(
        say="x" * 30, run='pydiggs validate "my file.xml"', expect="valid/ok", title="t"
    )
    tape = build.tape(scene, tmp_path, tmp_path / "out.mp4")
    assert "Type 'pydiggs validate \"my file.xml\"'" in tape
    assert r"Wait+Screen@60s /valid\/ok/" in tape
    assert tape.index("Hide") < tape.index("Show") < tape.index("Type 'pydiggs")


def test_a_browser_scene_is_cut_from_the_frames_painted_in_its_window() -> None:
    a, b, c, d = (Path(f"{n}.jpg") for n in "abcd")
    frames = [(10.0, a), (12.0, b), (15.0, c), (21.0, d)]
    # Opens on the frame already on screen, then each frame until the next or the end.
    assert build.scene_frames(frames, 11.0, 16.0) == [(a, 1.0), (b, 3.0), (c, 1.0)]
    # A later scene never shows a frame painted after it ended (d at 21 s).
    assert build.scene_frames(frames, 16.0, 20.0) == [(c, 4.0)]
    assert build.scene_frames([], 0.0, 1.0) == []


def _page(src_uri: str, url: str) -> SimpleNamespace:
    return SimpleNamespace(url=url, file=SimpleNamespace(src_uri=src_uri))


def test_the_page_gets_the_transcript_when_no_video_was_rendered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hook, "MEDIA", tmp_path)
    monkeypatch.delenv("TUTORIALS_REQUIRE", raising=False)
    hook.on_config({})
    html = hook.on_page_markdown(
        "Intro\n\n<!-- tutorial: validate -->\n", page=_page("usage.md", "usage/")
    )
    assert "<video" not in html
    assert "tutorial-pending" in html
    assert "<summary>Transcript</summary>" in html
    assert "site dot X M L" in html


def test_the_page_gets_the_player_with_captions_when_rendered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "validate"
    folder.mkdir()
    for suffix in (".mp4", ".webm", ".en.vtt", ".chapters.vtt", ".jpg"):
        (folder / f"validate{suffix}").write_bytes(b"x")
    monkeypatch.setattr(hook, "MEDIA", tmp_path)
    hook.on_config({})
    html = hook.on_page_markdown("<!-- tutorial: validate -->", page=_page("a/b.md", "a/b/"))
    assert 'preload="none"' in html
    assert "autoplay" not in html
    assert '<source src="../../tutorials/validate/validate.webm" type="video/webm">' in html
    assert 'kind="captions" src="../../tutorials/validate/validate.en.vtt"' in html
    root = hook.on_page_markdown("<!-- tutorial: validate -->", page=_page("index.md", ""))
    assert 'src="tutorials/validate/validate.mp4"' in root


def test_publishing_without_a_rendered_video_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hook, "MEDIA", tmp_path)
    monkeypatch.setenv("TUTORIALS_REQUIRE", "1")
    hook.on_config({})
    with pytest.raises(ValueError, match="no rendered video"):
        hook.on_page_markdown("<!-- tutorial: validate -->", page=_page("q.md", "q/"))


def test_an_unknown_marker_fails_the_docs_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TUTORIALS_REQUIRE", raising=False)
    hook.on_config({})
    with pytest.raises(ValueError, match="no tutorial named"):
        hook.on_page_markdown("<!-- tutorial: nope -->", page=_page("q.md", "q/"))


# --- the follow-along files ----------------------------------------------------------


def test_a_terminal_tutorial_must_say_which_files_a_viewer_gets(tmp_path: Path) -> None:
    with pytest.raises(spec.SpecError, match="files is required"):
        spec.load_tutorial(_write(tmp_path, "demo", GOOD.replace("files: []\n", "")))


@pytest.mark.parametrize(
    ("listed", "reason"),
    [
        ("[../secret.xml]", "plain file name"),
        ("[sub/site.xml]", "plain file name"),
        ("[run.sh]", "not one of the text types"),
        ("[site.xml, site.xml]", "listed twice"),
    ],
)
def test_only_plain_text_files_in_the_work_directory_are_offered(
    tmp_path: Path, listed: str, reason: str
) -> None:
    text = GOOD.replace("files: []", f"files: {listed}")
    with pytest.raises(spec.SpecError, match=reason):
        spec.load_tutorial(_write(tmp_path, "demo", text))


def _snapshot(work: Path, out: Path, names: tuple[str, ...]) -> dict[str, object]:
    result: dict[str, object] = files.snapshot(
        "demo",
        "A demo",
        names,
        work,
        out,
        commands=["pydiggs validate a.xml"],
        install=files.install_steps("pydiggs", "1.13.1"),
    )
    return result


def test_the_zip_is_the_same_bytes_on_every_build(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.xml").write_text("<a/>\n", encoding="utf-8")
    (work / "b.ags").write_text('"GROUP","PROJ"\n', encoding="utf-8")
    first = _snapshot(work, tmp_path / "one", ("a.xml", "b.ags"))
    import os

    os.utime(work / "a.xml", (1_000_000_000, 1_000_000_000))
    second = _snapshot(work, tmp_path / "two", ("a.xml", "b.ags"))
    assert first["zip"] == second["zip"]
    archive = tmp_path / "one" / "files" / "demo-files.zip"
    import zipfile

    with zipfile.ZipFile(archive) as opened:
        assert opened.namelist() == ["README.txt", "a.xml", "b.ags"]
        assert {info.date_time for info in opened.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        readme = opened.read("README.txt").decode()
    # REUSE-IgnoreStart: the README's tag, not this file's
    assert "SPDX-License-Identifier: Apache-2.0" in readme
    # REUSE-IgnoreEnd
    assert 'uv add "pydiggs==1.13.1"' in readme
    assert "pydiggs validate a.xml" in readme
    assert files.published(tmp_path / "one") == first


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"x" * (1_048_576 + 1), "at most"),
        (b"\xff\xfe not text", "not UTF-8"),
        (b"token ghp_" + b"a" * 30, "credential"),
        (b"<path>/Users/someone/site.xml</path>", "path from the machine"),
    ],
)
def test_a_file_unfit_to_publish_is_refused(tmp_path: Path, content: bytes, reason: str) -> None:
    (tmp_path / "a.xml").write_bytes(content)
    with pytest.raises(files.FilesError, match=reason):
        _snapshot(tmp_path, tmp_path / "out", ("a.xml",))


def test_a_missing_or_linked_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(files.FilesError, match="regular file"):
        _snapshot(tmp_path, tmp_path / "out", ("a.xml",))
    (tmp_path / "real.xml").write_text("<a/>", encoding="utf-8")
    try:
        (tmp_path / "a.xml").symlink_to(tmp_path / "real.xml")
    except OSError:
        pytest.skip("this system cannot make symlinks")
    with pytest.raises(files.FilesError, match="regular file"):
        _snapshot(tmp_path, tmp_path / "out", ("a.xml",))


def test_install_steps_pin_a_release_and_carry_no_comment() -> None:
    assert files.install_steps("pydiggs", "1.13.1") == [
        "uv init --bare",
        'uv add "pydiggs==1.13.1"',
        "source .venv/bin/activate",
    ]
    assert files.install_steps("pydiggs", "1.14.0.dev3")[1] == "uv add pydiggs"
    assert not any("#" in step for step in files.install_steps("pydiggs", "1.0"))


def test_the_page_offers_the_files_and_the_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "site.xml").write_text("<a/>", encoding="utf-8")
    (work / "broken.xml").write_text("<b/>", encoding="utf-8")
    files.snapshot(
        "validate",
        "t",
        ("site.xml", "broken.xml"),
        work,
        tmp_path / "validate",
        commands=["x"],
        install=files.install_steps("pydiggs", "1.13.1"),
    )
    monkeypatch.setattr(hook, "MEDIA", tmp_path)
    monkeypatch.delenv("TUTORIALS_REQUIRE", raising=False)
    hook.on_config({})
    page = hook.on_page_markdown("<!-- tutorial: validate -->", page=_page("q.md", "q/"))
    assert '!!! example "Try it yourself"' in page
    assert '<a href="../tutorials/validate/files/validate-files.zip" download>' in page
    assert '<a href="../tutorials/validate/files/broken.xml" download>' in page
    assert "SHA-256 <code>" in page
    tutorial = spec.load_tutorial(spec.TUTORIALS_DIR / "validate.yml")
    for line in ['    uv add "pydiggs==1.13.1"', *(f"    {s.run}" for s in tutorial.scenes)]:
        assert line in page.splitlines()


def test_without_rendered_files_the_commands_are_still_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hook, "MEDIA", tmp_path)
    monkeypatch.delenv("TUTORIALS_REQUIRE", raising=False)
    hook.on_config({})
    page = hook.on_page_markdown("<!-- tutorial: diff -->", page=_page("q.md", "q/"))
    assert "published with the site" in page
    assert "    uv add pydiggs" in page.splitlines()
    assert "    pydiggs diff site.xml site-rev2.xml" in page.splitlines()


def test_every_tutorial_offers_its_files() -> None:
    for path in spec.tutorial_paths():
        assert spec.load_tutorial(path).files, path.name
