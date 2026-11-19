# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "kokoro-onnx==0.6.1",
#   "pyyaml==6.0.3",
#   "playwright==1.65.2",
# ]
# ///
"""Render the tutorial videos: narration, screen, captions, chapters and poster.

Run it from the repository root, with the package's own commands on ``PATH``::

    uv run --script tools/tutorials/build.py                 # every tutorial
    uv run --script tools/tutorials/build.py --only validate

For each ``docs/tutorials/<id>.yml`` it writes ``build/tutorials/<id>/``: ``<id>.mp4``
(H.264, for every browser), ``<id>.webm`` (AV1, or VP9 where the ffmpeg has no AV1
encoder), ``<id>.en.vtt`` (captions), ``<id>.chapters.vtt``, ``<id>.jpg`` (poster) and
``meta.json``.

How a video is made, scene by scene:

1. The narration is split into sentences, and each sentence is spoken by Kokoro-82M
   (local and offline, through kokoro-onnx). The caption cues are cut from the real
   length of each clip, so they are exact, not estimated by speech recognition.
2. The screen is recorded: a terminal scene by VHS, typing the scene's command into a
   real shell in a work directory that persists from scene to scene; a browser scene by
   Playwright, doing the scene's steps. A command whose output does not match ``expect``,
   or a step that fails, fails the build, so a video can never show a feature that no
   longer works.
3. Each scene's clip is held on its last frame until the narration has finished, then
   all scenes are joined and encoded once.

Needs: ffmpeg and ffprobe; VHS (and its ttyd) for terminal tutorials; Chromium for
browser tutorials (``playwright install chromium``, or an installed Chrome with
``TUTORIALS_CHROME_CHANNEL=chrome``). The voice model is downloaded once,
checked against pinned SHA-256 digests, into ``$TUTORIALS_MODEL_DIR`` (default
``~/.cache/forge-tutorials``).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec import (
    ENGINE_VERSION,
    ROOT,
    Manifest,
    Scene,
    Step,
    Tutorial,
    load_manifest,
    load_tutorial,
    sentences,
    spoken,
    tutorial_paths,
)

#: Kokoro v1.0 model files, as released by kokoro-onnx; Apache-2.0 weights.
MODEL_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}

WIDTH, HEIGHT, FPS = 1280, 720, 30
SAMPLE_RATE = 48_000
#: Seconds of screen before the voice starts, between sentences, and after it ends.
LEAD, GAP, TAIL = 0.4, 0.25, 0.8
#: A caption line holds at most this many characters, and a cue at most two lines.
CAPTION_LINE = 42
#: The terminal's look; VHS built-in theme names.
TERMINAL = {
    "FontSize": "22",
    "Theme": '"Catppuccin Mocha"',
    "TypingSpeed": "45ms",
    "Padding": "28",
    "Framerate": str(FPS),
}


# --- small helpers ----------------------------------------------------------------------


def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``command``; on failure, show its output and stop the build."""
    result = subprocess.run(command, check=False, capture_output=True, text=True, **kwargs)  # noqa: S603
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        msg = f"{command[0]} failed with exit status {result.returncode}"
        raise SystemExit(msg)
    return result


def duration(media: Path) -> float:
    """Length of a media file in seconds, by ffprobe."""
    out = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(media)]
    )
    return float(out.stdout.strip())


def timestamp(seconds: float) -> str:
    """WebVTT time: ``hh:mm:ss.mmm``."""
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def wrap(text: str, width: int = CAPTION_LINE) -> list[str]:
    """Split ``text`` into lines of at most ``width`` characters, at spaces."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def vtt_escape(text: str) -> str:
    """Escape the three characters WebVTT cue text gives meaning to."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class Cue:
    """A caption on screen from ``start`` to ``end`` seconds."""

    start: float
    end: float
    text: str


def cues_for(sentence: str, start: float, length: float) -> list[Cue]:
    """Cut one spoken sentence into cues of at most two lines, timed by character count."""
    lines = wrap(sentence)
    chunks = [" ".join(lines[i : i + 2]) for i in range(0, len(lines), 2)]
    total = sum(len(chunk) for chunk in chunks)
    cues: list[Cue] = []
    at = start
    for chunk in chunks:
        span = length * len(chunk) / total
        cues.append(Cue(at, at + span, chunk))
        at += span
    return cues


def write_vtt(path: Path, cues: list[Cue]) -> None:
    """Write WebVTT; a cue's text is wrapped to the caption line width."""
    blocks = ["WEBVTT", ""]
    for index, cue in enumerate(cues, 1):
        blocks += [str(index), f"{timestamp(cue.start)} --> {timestamp(cue.end)}"]
        blocks += [vtt_escape(line) for line in wrap(cue.text)]
        blocks.append("")
    path.write_text("\n".join(blocks), encoding="utf-8", newline="\n")


# --- the voice -------------------------------------------------------------------------


class Voice:
    """Kokoro-82M through kokoro-onnx: one WAV per sentence, at ``SAMPLE_RATE``."""

    def __init__(self, manifest: Manifest) -> None:
        """Fetch (once) and verify the model, then load it."""
        from kokoro_onnx import Kokoro  # noqa: PLC0415 - heavy; only the renderer needs it

        directory = Path(
            os.environ.get("TUTORIALS_MODEL_DIR", Path.home() / ".cache" / "forge-tutorials")
        )
        directory.mkdir(parents=True, exist_ok=True)
        for name, digest in MODEL_FILES.items():
            target = directory / name
            if not target.exists():
                print(f"downloading {name}")
                with urllib.request.urlopen(MODEL_BASE + name, timeout=300) as response:  # noqa: S310 - fixed https URL
                    target.write_bytes(response.read())
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != digest:
                target.unlink()
                msg = f"{name}: sha256 {actual} is not the pinned {digest}"
                raise SystemExit(msg)
        self.engine = Kokoro(
            str(directory / "kokoro-v1.0.onnx"), str(directory / "voices-v1.0.bin")
        )
        self.voice = manifest.voice
        self.speed = manifest.speed
        self.pronounce = manifest.pronounce

    def say(self, text: str, target: Path) -> float:
        """Speak ``text`` into ``target`` (mono 16-bit WAV); return its length in seconds."""
        samples, rate = self.engine.create(
            spoken(text, self.pronounce), voice=self.voice, speed=self.speed, lang="en-us"
        )
        raw = target.with_suffix(".raw.wav")
        with wave.open(str(raw), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            pcm = (samples.clip(-1, 1) * 32767).astype("<i2")
            out.writeframes(pcm.tobytes())
        # Resample, and trim the model's leading and trailing silence.
        run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
                "silenceremove=start_periods=1:start_threshold=-50dB,"
                "areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                "-ar", str(SAMPLE_RATE), "-ac", "1", str(target),
            ]
        )  # fmt: skip
        raw.unlink()
        return duration(target)


@dataclass
class Narration:
    """One scene's voice track, and its caption cues relative to the scene start."""

    wav: Path
    length: float
    cues: list[Cue]


def narrate(voice: Voice, scene: Scene, work: Path, index: int) -> Narration:
    """Speak each sentence of a scene, join them with short pauses, and time the cues."""
    parts: list[Path] = []
    cues: list[Cue] = []
    at = 0.0
    silence = work / "gap.wav"
    if not silence.exists():
        run(
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", str(GAP), str(silence),
            ]
        )  # fmt: skip
    for number, sentence in enumerate(sentences(scene.say), 1):
        clip = work / f"say-{index:02d}-{number:02d}.wav"
        length = voice.say(sentence, clip)
        if parts:
            parts.append(silence)
            at += GAP
        cues += cues_for(sentence, at, length)
        parts.append(clip)
        at += length
    joined = work / f"say-{index:02d}.wav"
    listing = work / f"say-{index:02d}.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(joined)],
        cwd=work,
    )  # fmt: skip
    return Narration(joined, duration(joined), cues)


# --- the screen: terminal ----------------------------------------------------------------


def _vhs_string(text: str) -> str:
    """Quote ``text`` for a VHS ``Type`` command."""
    for quote in ('"', "'", "`"):
        if quote not in text:
            return f"{quote}{text}{quote}"
    msg = f"cannot quote for VHS (uses all three quote marks): {text}"
    raise SystemExit(msg)


def tape(scene: Scene, work: Path, output: Path) -> str:
    """Return the VHS tape that records one terminal scene."""
    lines = [f'Output "{output}"', 'Set Shell "bash"']
    lines += [f"Set {key} {value}" for key, value in TERMINAL.items()]
    lines += [f"Set Width {WIDTH}", f"Set Height {HEIGHT}"]
    # The prompt and the directory are set off camera, so every scene looks the same.
    lines += [
        "Hide",
        "Type " + _vhs_string(f"cd {shlex.quote(str(work))} && export PS1='$ ' && clear"),
        "Enter",
        "Sleep 300ms",
        "Show",
        "Sleep 400ms",
    ]
    if scene.run:
        pattern = scene.expect.replace("/", r"\/")
        lines += [
            "Type " + _vhs_string(scene.run),
            "Sleep 400ms",
            "Enter",
            f"Wait+Screen@60s /{pattern}/",
            "Sleep 1200ms",
        ]
    else:
        lines.append("Sleep 1s")
    return "\n".join(lines) + "\n"


def run_setup(tutorial: Tutorial, work: Path) -> None:
    """Run the spec's setup lines, off camera, in the work directory."""
    for line in tutorial.setup:
        command = line.replace("{repo}", shlex.quote(str(ROOT)))
        run(["bash", "-euo", "pipefail", "-c", command], cwd=work)


def record_terminal(tutorial: Tutorial, work: Path, scratch: Path) -> list[Path]:
    """Record each scene with VHS, in the work directory; return the clips."""
    clips: list[Path] = []
    for index, scene in enumerate(tutorial.scenes, 1):
        clip = scratch / f"scene-{index:02d}.mp4"
        script = scratch / f"scene-{index:02d}.tape"
        script.write_text(tape(scene, work, clip), encoding="utf-8")
        result = subprocess.run(  # noqa: S603
            [shutil.which("vhs") or "vhs", str(script)],
            check=False,
            capture_output=True,
            text=True,
            cwd=work,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout + result.stderr)
            msg = (
                f"{tutorial.id} scene {index}: the screen never showed {scene.expect!r} "
                f"after `{scene.run}` (or VHS failed)"
            )
            raise SystemExit(msg)
        clips.append(clip)
    return clips


# --- the screen: browser -----------------------------------------------------------------

#: A visible pointer: Playwright's recordings show no mouse, so clicks would be invisible.
POINTER = """
(() => {
  const dot = document.createElement('div');
  dot.setAttribute('aria-hidden', 'true');
  dot.style.cssText = 'position:fixed;z-index:2147483647;width:22px;height:22px;'
    + 'margin:-11px 0 0 -11px;border-radius:50%;pointer-events:none;'
    + 'background:rgba(255,196,0,.55);border:2px solid #b8860b;transition:transform .15s;'
    + 'left:-50px;top:-50px';
  addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px';
                                       dot.style.top = e.clientY + 'px'; }, true);
  addEventListener('mousedown', () => dot.style.transform = 'scale(.7)', true);
  addEventListener('mouseup', () => dot.style.transform = '', true);
  addEventListener('DOMContentLoaded', () => document.body.appendChild(dot));
})();
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for(url: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - localhost only
                return
        except OSError:
            time.sleep(0.25)
    msg = f"{url} did not answer within {seconds:.0f} s"
    raise SystemExit(msg)


def _do(page: Any, step: Step, base: str, work: Path) -> None:
    """Perform one step; assertions fail the build."""
    from playwright.sync_api import expect  # noqa: PLC0415

    value = step.value
    if step.action == "goto":
        page.goto(base + str(value).lstrip("/"))
    elif step.action == "wait":
        page.wait_for_timeout(int(value))
    elif step.action in {"wait_text", "expect_text"}:
        expect(page.get_by_text(re.compile(str(value))).first).to_be_visible(timeout=120_000)
    elif step.action == "click":
        target = page.locator(str(value)).first
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=15)
        target.click()
    elif step.action == "fill":
        page.locator(value["selector"]).first.fill(str(value.get("value", "")))
    elif step.action == "press":
        page.locator(value["selector"]).first.press(str(value.get("key", "Enter")))
    elif step.action == "upload":
        path = str(value["file"]).replace("{repo}", str(ROOT)).replace("{work}", str(work))
        page.locator(value["selector"]).first.set_input_files(path)


def record_browser(tutorial: Tutorial, work: Path, scratch: Path) -> list[Path]:
    """Start the tutorial's server, do each scene's steps on camera; return the clips."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    port = _free_port()
    command = tutorial.serve.replace("{port}", str(port)).replace("{repo}", str(ROOT))
    server = subprocess.Popen(  # noqa: S603
        shlex.split(command), cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    base = tutorial.ready.replace("{port}", str(port))
    if not base.endswith("/"):
        base += "/"
    marks: list[tuple[float, float]] = []
    try:
        _wait_for(base)
        with sync_playwright() as playwright:
            # TUTORIALS_CHROME_CHANNEL=chrome uses an installed Chrome (CI runners have one).
            channel = os.environ.get("TUTORIALS_CHROME_CHANNEL") or None
            browser = playwright.chromium.launch(channel=channel)
            context = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                record_video_dir=str(scratch),
                record_video_size={"width": WIDTH, "height": HEIGHT},
                color_scheme="light",
                reduced_motion="reduce",
                locale="en-US",
            )
            context.add_init_script(POINTER)
            page = context.new_page()
            started = time.monotonic()
            for index, scene in enumerate(tutorial.scenes, 1):
                begin = time.monotonic() - started
                for step in scene.steps:
                    try:
                        _do(page, step, base, work)
                    except Exception as exc:
                        msg = f"{tutorial.id} scene {index}: {step.action} failed: {exc}"
                        raise SystemExit(msg) from exc
                page.wait_for_timeout(800)
                marks.append((begin, time.monotonic() - started))
            video = Path(page.video.path())
            context.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    clips: list[Path] = []
    for index, (begin, end) in enumerate(marks, 1):
        clip = scratch / f"scene-{index:02d}.mp4"
        run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{begin:.3f}", "-to", f"{end:.3f}",
             "-i", str(video), "-an", "-c:v", "libx264", "-crf", "16", "-r", str(FPS),
             "-pix_fmt", "yuv420p", str(clip)]
        )  # fmt: skip
        clips.append(clip)
    return clips


# --- assembly ---------------------------------------------------------------------------


def scene_segment(clip: Path, voice: Narration, target: Path) -> float:
    """Hold ``clip`` on its last frame until the narration ends; mux; return its length."""
    length = max(duration(clip), LEAD + voice.length + TAIL)
    video = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={FPS},"
        f"tpad=stop_mode=clone:stop_duration={length:.3f},trim=duration={length:.3f},"
        "setpts=PTS-STARTPTS,format=yuv420p[v]"
    )
    audio = (
        f"[1:a]adelay={round(LEAD * 1000)}:all=1,apad,atrim=duration={length:.3f},"
        "asetpts=PTS-STARTPTS[a]"
    )
    run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-i", str(voice.wav),
         "-filter_complex", f"{video};{audio}", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", "14", "-preset", "fast",
         "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), str(target)]
    )  # fmt: skip
    return length


def webm_codec() -> list[str]:
    """AV1 (SVT-AV1) where this ffmpeg has it; VP9 otherwise."""
    encoders = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
    if "libsvtav1" in encoders:
        return ["-c:v", "libsvtav1", "-crf", "36", "-preset", "8", "-g", "240"]
    return ["-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0", "-row-mt", "1"]


def encode(segments: list[Path], out: Path, tid: str) -> None:
    """Join the segments and encode the two published files and the poster."""
    listing = out.parent / f"{tid}.segments.txt"
    listing.write_text("".join(f"file '{s}'\n" for s in segments), encoding="utf-8")
    source = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
    common = ["-pix_fmt", "yuv420p", "-r", str(FPS), "-map_metadata", "-1"]
    run([
        *source, *common, "-c:v", "libx264", "-crf", "23", "-preset", "slow",
        "-profile:v", "high", "-tune", "stillimage", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(out / f"{tid}.mp4"),
    ])  # fmt: skip
    run([*source, *common, *webm_codec(), "-c:a", "libopus", "-b:a", "64k",
         str(out / f"{tid}.webm")])  # fmt: skip
    listing.unlink()
    # The poster is the end of the last scene: the result the tutorial leads to.
    run(["ffmpeg", "-y", "-v", "error", "-sseof", "-1", "-i", str(out / f"{tid}.mp4"),
         "-frames:v", "1", "-q:v", "3", str(out / f"{tid}.jpg")])  # fmt: skip


def fingerprint(tutorial: Tutorial, manifest: Manifest) -> str:
    """What the video depends on: the spec, the engine, the voice and the package version."""
    digest = hashlib.sha256(tutorial.path.read_bytes())
    digest.update(f"{ENGINE_VERSION}|{manifest.voice}|{manifest.speed}".encode())
    digest.update(json.dumps(manifest.pronounce, sort_keys=True).encode())
    return digest.hexdigest()


def package_version() -> str:
    """The installed version of the package the tutorials show (``[project] name``)."""
    if os.environ.get("TUTORIALS_PACKAGE_VERSION"):
        return os.environ["TUTORIALS_PACKAGE_VERSION"]
    name = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build(tutorial: Tutorial, manifest: Manifest, voice: Voice, out_root: Path) -> Path:
    """Render one tutorial into ``out_root/<id>/``; return that directory."""
    out = out_root / tutorial.id
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=f"tutorial-{tutorial.id}-") as tmp:
        scratch = Path(tmp)
        work = scratch / "work"
        work.mkdir()
        narrations = [narrate(voice, s, scratch, i) for i, s in enumerate(tutorial.scenes, 1)]
        run_setup(tutorial, work)
        if tutorial.kind == "terminal":
            clips = record_terminal(tutorial, work, scratch)
        else:
            clips = record_browser(tutorial, work, scratch)
        segments: list[Path] = []
        captions: list[Cue] = []
        chapters: list[Cue] = []
        at = 0.0
        for index, (clip, narration, scene) in enumerate(
            zip(clips, narrations, tutorial.scenes, strict=True), 1
        ):
            segment = scratch / f"segment-{index:02d}.mkv"
            length = scene_segment(clip, narration, segment)
            segments.append(segment)
            captions += [
                Cue(at + LEAD + c.start, at + LEAD + c.end, c.text) for c in narration.cues
            ]
            chapters.append(Cue(at, at + length, scene.title or f"Step {index}"))
            at += length
        encode(segments, out, tutorial.id)
    write_vtt(out / f"{tutorial.id}.en.vtt", captions)
    write_vtt(out / f"{tutorial.id}.chapters.vtt", chapters)
    meta = {
        "id": tutorial.id,
        "title": tutorial.title,
        "duration": round(at, 2),
        "engine": ENGINE_VERSION,
        "voice": manifest.voice,
        "package_version": package_version(),
        "fingerprint": fingerprint(tutorial, manifest),
        "chapters": [{"start": round(c.start, 2), "title": c.text} for c in chapters],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    """Render every tutorial, or those named with ``--only``."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", default=[], metavar="ID")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "tutorials")
    args = parser.parse_args(argv)
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            parser.error(f"{tool} is not on PATH")
    manifest = load_manifest()
    tutorials = [load_tutorial(path) for path in tutorial_paths()]
    if args.only:
        unknown = set(args.only) - {t.id for t in tutorials}
        if unknown:
            parser.error(f"no such tutorial: {', '.join(sorted(unknown))}")
        tutorials = [t for t in tutorials if t.id in args.only]
    if any(t.kind == "terminal" for t in tutorials) and shutil.which("vhs") is None:
        parser.error("vhs is not on PATH (terminal tutorials need it)")
    voice = Voice(manifest)
    for tutorial in tutorials:
        started = time.monotonic()
        out = build(tutorial, manifest, voice, args.out)
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        print(
            f"{tutorial.id}: {meta['duration']:.1f} s video in {time.monotonic() - started:.0f} s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
