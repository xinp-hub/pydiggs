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
import dataclasses
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

FIXTURES = ("casing_units.xml", "markup_in_value.xml")
# The service-layer cases (what @pydiggs/client's worker and `pydiggs serve --http` run):
# a subset of steps, and the result tables of one node only.
CHECKS = ("structure", "schema")
SELECT_KEYS = ("ATTERBERG_B-01_4.0",)
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
    string for JavaScript to ``JSON.parse``). Then the same through the service layer
    that @pydiggs/client's worker and the HTTP API use: SARIF (``sarif:<name>``), a
    subset of steps (``checks:<name>``), one node's tables (``select:<name>``), and the
    versions, rule list and one rule (``versions``, ``rules:schema``, ``explain``), and the core
    operations of ``capabilities()``.
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
    # The core operations only: whether an extra is installed differs between the two.
    caps = pydiggs.capabilities().to_dict()
    caps["operations"] = [op for op in caps["operations"] if not op["needs"]]
    out["capabilities"] = json.dumps(caps, ensure_ascii=False, sort_keys=True)
    out.update(service_cases(fixture_dir))
    return out


def service_cases(fixture_dir: str) -> dict[str, str]:
    """The service-layer documents, each as the one JSON string the layer writes."""
    from pydiggs import _service as svc  # lazily: see the module docstring

    def text(outcome: svc.Outcome) -> str:
        assert outcome.ok and outcome.document is not None, outcome
        return outcome.document.decode("utf-8")

    out: dict[str, str] = {}
    for name in FIXTURES:
        data = Path(fixture_dir, name).read_bytes()
        request = svc.Request(content=data, name=name, max_bytes=MAX_BYTES)
        out[f"sarif:{name}"] = text(svc.validate(dataclasses.replace(request, format="sarif")))
        out[f"checks:{name}"] = text(
            svc.validate(dataclasses.replace(request, checks=list(CHECKS)))
        )
        select = {"results": list(SELECT_KEYS)}
        read = svc.ReadRequest(content=data, name=name, select=select, max_bytes=MAX_BYTES)
        out[f"select:{name}"] = text(svc.read(read))
    out["versions"] = json.dumps({"api_version": "1", **svc.versions()})
    rules = svc.rules("schema")
    assert isinstance(rules, list) and rules
    out["rules:schema"] = json.dumps(rules)
    out["explain"] = json.dumps(svc.explain(rules[0]["id"]))
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
