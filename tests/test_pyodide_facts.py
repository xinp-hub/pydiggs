"""The browser contract that can be checked without Pyodide.

``tests/pyodide/pyodide-facts.json`` is the one place that says what the tested Pyodide
ships (its CPython and lxml, read from that release's ``pyodide-lock.json``). The Node
smoke checks every value against the running Pyodide, so the file cannot drift from the
npm pin; these tests keep pyproject and the npm pin in line with it on every platform,
without Node.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

import pydiggs

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tests" / "pyodide"
FACTS = json.loads((SMOKE / "pyodide-facts.json").read_text(encoding="utf-8"))


def _lxml_floor() -> Version:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (req,) = [Requirement(d) for d in project["dependencies"] if Requirement(d).name == "lxml"]
    floors = [Version(s.version) for s in req.specifier if s.operator in {">=", "==", "~="}]
    assert len(floors) == 1, f"expected one lxml floor, got {req}"
    return floors[0]


def test_lxml_floor_is_installable_in_the_tested_pyodide():
    # Pyodide installs pydiggs with deps=False on top of its own lxml build: a floor above
    # it would make the browser install silently unsupported.
    assert _lxml_floor() <= Version(FACTS["lxml"]), (
        f"pyproject's lxml floor {_lxml_floor()} is above Pyodide {FACTS['pyodide']}'s "
        f"lxml {FACTS['lxml']}; raise the tested Pyodide first (tests/pyodide)"
    )


def test_the_npm_pin_is_exact_and_matches_the_facts():
    package = json.loads((SMOKE / "package.json").read_text(encoding="utf-8"))
    pin = package["dependencies"]["pyodide"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin), f"pyodide must be pinned exactly, not {pin!r}"
    assert pin == FACTS["pyodide"]
    lock = json.loads((SMOKE / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/pyodide"]["version"] == pin


WORKER = ROOT / "examples" / "browser-worker" / "worker.mjs"
needs_example = pytest.mark.skipif(not WORKER.is_file(), reason="examples/ is not in the sdist")


@needs_example
def test_the_browser_example_loads_the_tested_pyodide_and_this_pydiggs():
    worker = WORKER.read_text(encoding="utf-8")
    assert f'const PYODIDE_VERSION = "{FACTS["pyodide"]}";' in worker
    # A release bumps the example together with the package.
    assert f'const PYDIGGS_VERSION = "{pydiggs.__version__}";' in worker


@needs_example
def test_the_browser_example_never_parses_strings_as_html():
    for path in WORKER.parent.glob("*.mjs"):
        source = path.read_text(encoding="utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            # The page's comment names innerHTML to forbid it; no code may use it.
            code = "\n".join(
                line for line in source.splitlines() if not line.lstrip().startswith("//")
            )
            assert sink not in code, f"{path.name} uses {sink}"
