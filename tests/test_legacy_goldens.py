"""The legacy subcommands print, return and write exactly what they did before.

Goldens come from git (1.1.1, or PR #248 for the files whose verdict it changed on
purpose) via ``scripts/regen_legacy_goldens.py``; see that script. Every case runs the
working tree's ``pydiggs.cli.main`` in a fresh interpreter with plain rich output.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_regen():
    spec = importlib.util.spec_from_file_location(
        "regen_legacy_goldens", ROOT / "scripts" / "regen_legacy_goldens.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


regen = _load_regen()
REVIEWED_248_FILES = regen.REVIEWED_248_FILES
capture_env = regen.capture_env
case_file = regen.case_file
GOLDENS = json.loads((ROOT / "tests" / "legacy_goldens" / "legacy_cli.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def captured() -> dict[str, dict[str, object]]:
    out = subprocess.run(  # noqa: S603  # our own capture script, fixed argv
        [sys.executable, str(ROOT / "tests" / "legacy_goldens" / "capture.py"), str(ROOT)],
        env=capture_env(None),
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    cases: dict[str, dict[str, object]] = json.loads(out)["cases"]
    return cases


@pytest.mark.slow
def test_legacy_output_is_byte_identical(captured):
    expected = GOLDENS["cases"]
    assert captured.keys() == expected.keys()
    different = [
        case_id
        for case_id, case in expected.items()
        if {k: v for k, v in case.items() if k != "source"} != captured[case_id]
    ]
    assert not different, f"{len(different)} legacy cases changed, e.g. {different[:5]}"


def test_goldens_cover_every_subcommand_flag_and_file():
    cases = GOLDENS["cases"]
    assert len(cases) >= 400
    files = {case_file(c) for c in cases}
    corpus = {
        p.relative_to(ROOT).as_posix()
        for d in ("tests/fixtures/official", "tests/test_files")
        for p in (ROOT / d).rglob("*.xml")
    }
    assert corpus <= files
    for command in ("schema_check", "schematron_check", "dictionary_check", "context_check"):
        for flag in ("--output_log", "--no-output_log"):
            assert sum(c.startswith(f"{command} {flag} ") for c in cases) > len(corpus)


def test_goldens_come_from_reviewed_sources():
    for case_id, case in GOLDENS["cases"].items():
        expected = "pr-248" if case_file(case_id) in REVIEWED_248_FILES else "1.1.1"
        assert case["source"] == expected, case_id
    assert GOLDENS["legacy"]["ref"] == "v1.1.1"


def test_regeneration_needs_explicit_flag_and_refuses_ci(monkeypatch, capsys):
    regen_legacy_goldens = regen
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit) as exc:
        regen_legacy_goldens.main([])
    assert exc.value.code == 2
    monkeypatch.setenv("CI", "true")
    assert regen_legacy_goldens.main(["--confirm-regenerate"]) == 2
    assert "refusing" in capsys.readouterr().err
