"""The Pyodide smoke's cases, run unchanged by CPython and by Pyodide.

CPython writes the goldens (``python tests/pyodide/cases.py goldens.json``) and
``smoke.mjs`` runs the same :func:`run` inside Pyodide, then compares the two byte for
byte. The goldens are made from the same checkout in the same CI job rather than
committed, so a rule or message change never needs a golden refresh here: what this
smoke proves is "Pyodide gives the bytes CPython gives", not "the bytes never change"
(the legacy goldens and the findings tests own that).

:func:`watch_imports` and :func:`probe` are for the Pyodide side only. Pyodide boots with
``subprocess``, ``socket`` and ``threading`` already in ``sys.modules``, so "absent from
``sys.modules``" proves nothing for those; the watcher records every ``import`` statement
executed from :func:`watch_imports` on, whether or not the module was already loaded.
This module imports pydiggs lazily so the watcher can be installed first.
"""

from __future__ import annotations

import builtins
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

FIXTURES = ("casing_units.xml", "markup_in_value.xml")
# The browser example's limit (brief D8).
MAX_BYTES = 25 * 1024 * 1024
# Never imported on the core path: rich (Pyodide installs pydiggs with deps=False),
# process pools, the adapters' frameworks, the frame and GIS libraries of the read
# API's extras, subprocesses, and networking (only an allow_network download imports
# urllib.request, http.client, socket and ssl).
_EXTRAS = ("pandas", "polars", "pyproj", "shapely", "geopandas", "pyogrio", "pyarrow")
FORBIDDEN = (
    "rich",
    "multiprocessing",
    "fastapi",
    "mcp",
    *_EXTRAS,
    "subprocess",
    "socket",
    "ssl",
    "http",
)
# Of those, the ones Pyodide does not load itself: these must also be absent from sys.modules.
NOT_LOADED = ("rich", "multiprocessing", "fastapi", "mcp", *_EXTRAS)
_attempts: dict[str, str] = {}


def watch_imports() -> None:
    """Record, from now on, which module first imports each watched top-level name."""
    original: Callable[..., Any] = builtins.__import__
    watched = set(FORBIDDEN)

    def hook(
        name: str,
        globals: Mapping[str, object] | None = None,  # noqa: A002  # __import__'s signature
        locals: Mapping[str, object] | None = None,  # noqa: A002
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        top = name.partition(".")[0]
        if level == 0 and top in watched:
            _attempts.setdefault(top, str((globals or {}).get("__name__", "?")))
        return original(name, globals, locals, fromlist, level)

    builtins.__import__ = hook


def run(fixture_dir: str) -> dict[str, str]:
    """Validate and read every fixture.

    Returns each ``report.to_json()``, the combined findings document, and each
    fixture's diggs-model document (``read:<name>``: ``read_bytes(...).to_json()``, one
    string for JavaScript to ``JSON.parse``).
    """
    import pydiggs  # lazily: see the module docstring

    reports = [
        pydiggs.validate_bytes(Path(fixture_dir, name).read_bytes(), name=name, max_bytes=MAX_BYTES)
        for name in FIXTURES
    ]
    out = {name: report.to_json() for name, report in zip(FIXTURES, reports, strict=True)}
    out["findings_json"] = pydiggs.findings_json(reports)
    for name in FIXTURES:
        data = Path(fixture_dir, name).read_bytes()
        out[f"read:{name}"] = pydiggs.read_bytes(data, name=name, max_bytes=MAX_BYTES).to_json()
    return out


def probe() -> dict[str, object]:
    """What the smoke asserts about the interpreter after :func:`run`."""
    import threading

    import lxml

    import pydiggs
    from pydiggs import units

    default_uom = units._DEFAULT_UOM  # the file the smoke must find
    converter = units.UnitConverter()
    return {
        "python": sys.version.split()[0],
        "lxml": lxml.__version__,
        "pydiggs": pydiggs.__version__,
        "forbidden_imported": {m: _attempts[m] for m in FORBIDDEN if m in _attempts},
        "forbidden_loaded": sorted(m for m in NOT_LOADED if m in sys.modules),
        "threads": threading.active_count(),
        "uom_path": str(default_uom),
        "uom_present": default_uom.is_file(),
        "uom_units": len(converter._factors),
        "inch_in_mm": converter.convert(1.0, "in", "mm"),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/pyodide/cases.py GOLDENS.json")
    fixtures = str(Path(__file__).resolve().parent / "fixtures")
    text = json.dumps(run(fixtures), ensure_ascii=False, indent=2)
    Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
