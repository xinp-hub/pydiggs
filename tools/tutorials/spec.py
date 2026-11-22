"""Tutorial specs and the coverage manifest: load, validate, and discover features.

Shared by ``check.py`` (the gate), ``build.py`` (the renderer) and ``mkdocs_tutorials.py``
(the docs hook). Standard library plus PyYAML, which MkDocs already brings.

A tutorial is a YAML file in ``docs/tutorials/<id>.yml``. Its narration is the single
source for the voice-over, the captions and the transcript, so the three never disagree.
See ``tools/tutorials/README.md`` for the format and the narration style.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discover as sources
import yaml

#: Bumped when the spec format or the rendering changes what a video looks like.
ENGINE_VERSION = "1.1.0"

ROOT = Path(__file__).resolve().parents[2]
TUTORIALS_DIR = ROOT / "docs" / "tutorials"
MANIFEST = TUTORIALS_DIR / "manifest.yml"
DOCS_DIR = ROOT / "docs"

KINDS = ("terminal", "browser")
STEP_ACTIONS = ("goto", "click", "fill", "upload", "press", "wait_text", "expect_text", "wait")

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
#: ``<!-- tutorial: validate -->`` in a docs page marks where a video goes.
MARKER = re.compile(r"<!--\s*tutorial:\s*([a-z0-9][a-z0-9-]*)\s*-->")
#: Sentence ends: punctuation followed by space. Narration avoids abbreviations.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'`(])")
#: Text that must never appear in a spec, rendered video or caption.
_SECRET = re.compile(
    r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:password|passwd|secret|token)\s*[=:]\s*\S+)"
)
#: A path the video would show or depend on that is not inside the repository.
_ABSOLUTE = re.compile(
    r"(?:^|[\s'\"=])(?:/(?:Users|home|root|etc|var|private|tmp)\b|~/|[A-Za-z]:\\)"
)

NARRATION_MIN, NARRATION_MAX = 20, 1200
#: What a viewer may download to follow a tutorial: plain text files of these kinds only.
FILE_TYPES = frozenset({".xml", ".ags", ".py", ".json", ".csv", ".txt", ".toml"})
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
WAIT_MAX_MS = 30_000
WAIVER_MIN = 15
SPEED_MIN, SPEED_MAX = 0.7, 1.3


class SpecError(ValueError):
    """A tutorial spec or the manifest is not valid; ``problems`` lists every reason."""

    def __init__(self, source: str, problems: list[str]) -> None:
        """Keep where the problems were found, and every one of them."""
        self.source = source
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


@dataclass(frozen=True)
class Step:
    """One Playwright action of a browser scene."""

    action: str
    value: Any


@dataclass(frozen=True)
class Scene:
    """One chapter: what is said, and what happens on screen while it is said."""

    say: str
    title: str = ""
    run: str = ""
    expect: str = ""
    steps: tuple[Step, ...] = ()
    claims: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tutorial:
    """One video, as written in ``docs/tutorials/<id>.yml``."""

    id: str
    title: str
    summary: str
    kind: str
    scenes: tuple[Scene, ...]
    path: Path
    setup: tuple[str, ...] = ()
    serve: str = ""
    ready: str = ""
    files: tuple[str, ...] = ()

    def transcript(self) -> list[tuple[str, str]]:
        """Return ``(chapter title, narration)`` per scene, in order."""
        return [(scene.title, scene.say) for scene in self.scenes]


@dataclass(frozen=True)
class Feature:
    """A user-facing feature: the public items it covers, and its tutorial or waiver."""

    id: str
    covers: tuple[str, ...]
    tutorial: str = ""
    waiver: str = ""


@dataclass
class Manifest:
    """``docs/tutorials/manifest.yml``: voice, discovery and the feature coverage map."""

    voice: str
    speed: float
    discover: dict[str, Any]
    features: list[Feature]
    pronounce: dict[str, str] = field(default_factory=dict)


def _text(data: dict[str, Any], key: str, problems: list[str], *, required: bool = True) -> str:
    value = data.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        problems.append(f"{key} must be text")
        return ""
    text = " ".join(value.split()) if key in {"say", "summary", "title"} else value.strip()
    if required and not text:
        problems.append(f"{key} is required")
    return text


def _check_safe(where: str, text: str, problems: list[str]) -> None:
    if _SECRET.search(text):
        problems.append(f"{where} looks like it holds a secret")
    if _ABSOLUTE.search(text):
        problems.append(f"{where} names an absolute path; use {{repo}} or a relative path")


def _step_problem(action: str, value: Any) -> str:
    """Return why one browser step is not valid, or an empty string."""
    if action not in STEP_ACTIONS:
        return f"unknown action {action!r}"
    if action in {"fill", "upload", "press"} and not (
        isinstance(value, dict) and "selector" in value
    ):
        return f"{action} needs a mapping with a selector"
    if action == "upload" and not str(value.get("file", "")).startswith(("{repo}/", "{work}/")):
        return "upload a file under {repo}/ or {work}/"
    if action == "wait" and not (isinstance(value, int) and 0 < value <= WAIT_MAX_MS):
        return f"wait is milliseconds, 1 to {WAIT_MAX_MS}"
    return ""


def _steps(raw: Any, where: str, problems: list[str]) -> tuple[Step, ...]:
    if not isinstance(raw, list) or not raw:
        problems.append(f"{where}: steps must be a non-empty list")
        return ()
    steps: list[Step] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or len(item) != 1:
            problems.append(f"{where} step {index}: one action per step, as `action: value`")
            continue
        ((action, value),) = item.items()
        problem = _step_problem(str(action), value)
        if problem:
            problems.append(f"{where} step {index}: {problem}")
            continue
        _check_safe(f"{where} step {index}", str(value), problems)
        steps.append(Step(str(action), value))
    return tuple(steps)


def _check_regex(where: str, expect: str, problems: list[str]) -> None:
    if not expect:
        return
    try:
        re.compile(expect)
    except re.error as exc:
        problems.append(f"{where}: expect is not a valid regex: {exc}")


def _matches(expect: str, text: str) -> bool:
    try:
        return re.search(expect, text) is not None
    except re.error:
        return False


def _files(data: dict[str, Any], kind: str, problems: list[str]) -> tuple[str, ...]:
    """The files a viewer downloads to follow along: names in the work directory.

    Every terminal tutorial lists them (they are what its setup made, which the video
    cannot show), so that the page can offer exactly the files the video used.
    """
    raw = data.get("files")
    if raw is None:
        if kind == "terminal":
            problems.append("files is required: the work files a viewer downloads to follow along")
        return ()
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        problems.append("files must be a list of file names")
        return ()
    for name in raw:
        if not _FILE_NAME.match(name) or ".." in name:
            problems.append(f"files: {name!r} must be a plain file name in the work directory")
        elif Path(name).suffix not in FILE_TYPES:
            allowed = ", ".join(sorted(FILE_TYPES))
            problems.append(f"files: {name!r} is not one of the text types offered ({allowed})")
    if len(set(raw)) != len(raw):
        problems.append("files: a name is listed twice")
    return tuple(raw)


#: A quantity in the narration. Narration speaks quantities as words ("two errors",
#: "twelve and a half metres") so the voice reads them naturally, and keeps digits for
#: names ("DIGGS 3.0", "rule 13"); so a number word is a claim about the output.
_QUANTITY = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|half|twice|double)\b",
    re.IGNORECASE,
)


def _claims(
    raw: dict[str, Any], where: str, say: str, run: str, problems: list[str]
) -> tuple[str, ...]:
    """What the narration states about the output, each a regex the output must match.

    A scene whose narration names a quantity must list at least one, so that a video
    whose output changed while its ``expect`` still matches cannot keep saying "two
    errors" over a screen showing three.
    """
    value = raw.get("claims")
    quantity = _QUANTITY.search(say)
    if value is None:
        if quantity:
            problems.append(
                f"{where}: the narration states a quantity ({quantity.group(0)!r}); "
                "list it under claims:, as a regex the output must match"
            )
        return ()
    if not isinstance(value, list) or not all(isinstance(c, str) and c for c in value):
        problems.append(f"{where}: claims is a list of regexes")
        return ()
    for claim in value:
        _check_regex(f"{where} claim", claim, problems)
        if run and _matches(claim, run):
            problems.append(f"{where}: claim {claim!r} also matches the typed command")
    return tuple(value)


def _load_scene(raw: Any, where: str, kind: str, problems: list[str]) -> Scene | None:
    if not isinstance(raw, dict):
        problems.append(f"{where} must be a mapping")
        return None
    extra = set(raw) - {"title", "say", "run", "expect", "steps", "claims"}
    if extra:
        problems.append(f"{where}: unknown keys {', '.join(sorted(extra))}")
    say = _text(raw, "say", problems)
    if say and not NARRATION_MIN <= len(say) <= NARRATION_MAX:
        problems.append(
            f"{where}: narration is {len(say)} characters; keep it {NARRATION_MIN}-{NARRATION_MAX}"
        )
    _check_safe(f"{where} narration", say, problems)
    run = _text(raw, "run", problems, required=False)
    expect = _text(raw, "expect", problems, required=False)
    if kind == "browser" and (run or expect):
        problems.append(f"{where}: run and expect are for terminal tutorials")
    if run:
        _check_safe(f"{where} command", run, problems)
        if "\n" in run:
            problems.append(f"{where}: run is one command line")
        if not expect:
            problems.append(f"{where}: a command needs expect, the text that proves it worked")
    _check_regex(where, expect, problems)
    if run and expect and _matches(expect, run):
        problems.append(
            f"{where}: expect also matches the typed command, so it proves nothing;"
            " anchor it to the output, for example (?m)^text$"
        )
    steps: tuple[Step, ...] = ()
    if "steps" in raw:
        if kind != "browser":
            problems.append(f"{where}: steps are for browser tutorials")
        else:
            steps = _steps(raw["steps"], where, problems)
    elif kind == "browser":
        problems.append(f"{where}: a browser scene needs steps")
    claims = _claims(raw, where, say, run, problems)
    return Scene(say, _text(raw, "title", problems, required=False), run, expect, steps, claims)


def load_tutorial(path: Path) -> Tutorial:
    """Load and validate one spec.

    Raises:
        SpecError: with every problem found, not only the first.
    """
    problems: list[str] = []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(path.name, [f"not valid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise SpecError(path.name, ["must be a mapping"])
    unknown = set(data) - {
        "id",
        "title",
        "summary",
        "kind",
        "setup",
        "serve",
        "ready",
        "files",
        "scenes",
    }
    if unknown:
        problems.append(f"unknown keys: {', '.join(sorted(unknown))}")
    tid = _text(data, "id", problems)
    if tid and (not _ID.match(tid) or tid != path.stem):
        problems.append(f"id {tid!r} must match the file name and [a-z0-9-]")
    kind = _text(data, "kind", problems)
    if kind and kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    setup = data.get("setup") or []
    if not isinstance(setup, list) or not all(isinstance(line, str) for line in setup):
        problems.append("setup must be a list of shell lines")
        setup = []
    for index, line in enumerate(setup, 1):
        _check_safe(f"setup line {index}", line, problems)
    serve = _text(data, "serve", problems, required=kind == "browser")
    ready = _text(data, "ready", problems, required=kind == "browser")
    if kind == "terminal" and (serve or ready):
        problems.append("serve and ready are for browser tutorials")

    title = _text(data, "title", problems)
    files = _files(data, kind, problems)
    summary = _text(data, "summary", problems)
    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        problems.append("scenes must be a non-empty list")
        raw_scenes = []
    scenes = [
        _load_scene(raw, f"scene {index}", kind, problems)
        for index, raw in enumerate(raw_scenes, 1)
    ]
    if problems:
        raise SpecError(path.name, problems)
    return Tutorial(
        id=tid,
        title=title,
        summary=summary,
        kind=kind,
        scenes=tuple(scene for scene in scenes if scene is not None),
        path=path,
        setup=tuple(setup),
        serve=serve,
        ready=ready,
        files=files,
    )


def tutorial_paths(directory: Path = TUTORIALS_DIR) -> list[Path]:
    """Every spec file, sorted; the manifest is not one."""
    return sorted(p for p in directory.glob("*.yml") if p.name != MANIFEST.name)


def load_manifest(path: Path = MANIFEST) -> Manifest:
    """Load and validate the coverage manifest.

    Raises:
        SpecError: with every problem found.
    """
    problems: list[str] = []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise SpecError(path.name, ["must be a mapping with version: 1"])
    discover = data.get("discover") or {}
    if not isinstance(discover, dict):
        problems.append("discover maps a label to a source")
        discover = {}
    for label, source in discover.items():
        problems += [f"discover {label}: {p}" for p in sources.problems(source)]
    pronounce = data.get("pronounce") or {}
    if not isinstance(pronounce, dict):
        problems.append("pronounce maps a written word to how it is said")
        pronounce = {}
    features: list[Feature] = []
    seen: set[str] = set()
    for index, raw in enumerate(data.get("features") or [], 1):
        if not isinstance(raw, dict):
            problems.append(f"feature {index} must be a mapping")
            continue
        fid = str(raw.get("id") or "")
        if not _ID.match(fid) or fid in seen:
            problems.append(f"feature {index}: id {fid!r} is missing, malformed or repeated")
        seen.add(fid)
        covers = raw.get("covers") or []
        if not isinstance(covers, list) or not covers:
            problems.append(f"feature {fid}: covers must list what it documents")
            covers = []
        tutorial = str(raw.get("tutorial") or "")
        waiver = " ".join(str(raw.get("waiver") or "").split())
        if bool(tutorial) == bool(waiver):
            problems.append(f"feature {fid}: give a tutorial or a waiver with a reason, not both")
        if waiver and len(waiver) < WAIVER_MIN:
            problems.append(f"feature {fid}: a waiver needs a real reason")
        features.append(Feature(fid, tuple(str(c) for c in covers), tutorial, waiver))
    speed = data.get("speed", 1.0)
    if not isinstance(speed, int | float) or not SPEED_MIN <= speed <= SPEED_MAX:
        problems.append("speed must be between 0.7 and 1.3")
        speed = 1.0
    if problems:
        raise SpecError(path.name, problems)
    return Manifest(
        voice=str(data.get("voice") or "af_heart"),
        speed=float(speed),
        discover={str(k): v for k, v in discover.items()},
        features=features,
        pronounce={str(k): str(v) for k, v in pronounce.items()},
    )


def discover(manifest: Manifest) -> set[str]:
    """Return every public item the manifest says to look for, as ``label:name``.

    The sources are described in ``discover.py``.
    """
    found: set[str] = set()
    for label, source in manifest.discover.items():
        found |= {f"{label}:{name}" for name in sources.names(source, ROOT)}
    return found


def markers(docs_dir: Path = DOCS_DIR) -> dict[str, list[str]]:
    """Return tutorial id -> the docs pages that embed it."""
    found: dict[str, list[str]] = {}
    for page in sorted(docs_dir.rglob("*.md")):
        for tid in MARKER.findall(page.read_text(encoding="utf-8")):
            found.setdefault(tid, []).append(page.relative_to(docs_dir).as_posix())
    return found


def sentences(text: str) -> list[str]:
    """Split narration into sentences; each becomes one voice clip and caption cue."""
    return [part.strip() for part in _SENTENCE_END.split(" ".join(text.split())) if part.strip()]


#: ``{glob:dist/*.whl}`` is the one file in the work directory matching the pattern (its
#: absolute path); ``{globname:...}`` is its name. Expanded after setup has run.
_GLOB = re.compile(r"\{(glob|globname):([^{}]+)\}")


def expand(text: str, *, work: Path, port: int | None = None) -> str:
    """Fill ``{repo}``, ``{work}``, ``{port}`` and the ``{glob:...}`` placeholders.

    Raises:
        SpecError: a glob does not match exactly one file.
    """

    def one(match: re.Match[str]) -> str:
        found = sorted(work.glob(match.group(2)))
        if len(found) != 1:
            msg = f"{match.group(0)} matches {len(found)} files in the work directory, not 1"
            where = "placeholder"
            raise SpecError(where, [msg])
        return found[0].name if match.group(1) == "globname" else str(found[0])

    text = _GLOB.sub(one, text)
    text = text.replace("{repo}", str(ROOT)).replace("{work}", str(work))
    return text.replace("{port}", str(port)) if port is not None else text


def spoken(text: str, pronounce: dict[str, str]) -> str:
    """Return ``text`` as the voice should read it (captions keep the written form)."""
    for written, said in sorted(pronounce.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(written)}(?![\w-])", said, text)
    return text
