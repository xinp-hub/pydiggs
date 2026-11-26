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
# AGS4 files: checked, converted to DIGGS and that DIGGS back to AGS4 (pyDIGGS 1.11). Not
# validated or read as XML.
AGS4_FIXTURES = ("site_tier_a.ags",)
# The AGS4 versions DIGGS to AGS4 writes (4.2 is the default).
AGS_VERSIONS = ("4.2", "4.1.1")
# The service-layer cases (what @pydiggs/client's worker and `pydiggs serve --http` run):
# a subset of steps, and the result tables of one node only.
CHECKS = ("structure", "schema")
SELECT_KEYS = ("ATTERBERG_B-01_4.0",)
# An AGS4 check against a data dictionary (pyDIGGS 1.10 and later), as
# tests/test_server_convert.py pairs them; ags4_cases covers checks without one.
AGS4_CHECK = ("c2_user_group.ags", "c2_dictionary.ags")
# The tables profile (pyDIGGS 1.17): an official DIGGS 3.0 example turned into tables, and
# that package written back as DIGGS. It is read from tests/fixtures/official/3.0, not
# copied here.
TABLES_FIXTURE = "Borehole-CPT_Example_Annotated.xml"
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


def run(fixture_dir: str, official_dir: str | None = None) -> dict[str, str]:
    """Validate and read every fixture.

    ``official_dir`` holds :data:`TABLES_FIXTURE`; by default it is the repository's
    ``tests/fixtures/official/3.0``, found from ``fixture_dir`` (``tests/pyodide/fixtures``).

    Returns each ``report.to_json()``, the combined findings document, and each
    fixture's diggs-model document (``read:<name>``: ``read_bytes(...).to_json()``, one
    string for JavaScript to ``JSON.parse``). Then the same through the service layer
    that @pydiggs/client's worker and the HTTP API use: SARIF (``sarif:<name>``), a
    subset of steps (``checks:<name>``), a finding cap of one with a time budget
    (``limits:<name>``), one node's tables (``select:<name>``), and the
    versions, rule list and one rule (``versions``, ``rules:schema``, ``explain``), the core
    operations of ``capabilities()``, two model comparisons (:func:`diff_cases`), and the
    tables profile both ways (:func:`tables_cases`).
    """
    import pydiggs  # lazily: see the module docstring

    reports = [
        pydiggs.validate_bytes(Path(fixture_dir, name).read_bytes(), name=name, max_bytes=MAX_BYTES)
        for name in FIXTURES
    ]
    out_ags4 = ags4_cases(fixture_dir)
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
    out.update(out_ags4)
    out.update(diff_cases(fixture_dir))
    if official_dir is None:
        official_dir = str(Path(fixture_dir).resolve().parents[1] / "fixtures" / "official" / "3.0")
    out.update(tables_cases(official_dir))
    return out


def tables_cases(official_dir: str) -> dict[str, str]:
    """The tables profile through the service layer (pyDIGGS 1.17), both ways.

    ``service-tables-from-diggs:<name>`` is the document the HTTP API and the sidecar send
    for :data:`TABLES_FIXTURE` (the tables as a package ZIP, in base64), and
    ``service-tables-to-diggs:<zip>`` the one they send for that package (the DIGGS file,
    in base64). ``tables-to-diggs:<zip>`` is the DIGGS file the Python API writes from the
    same package, which the service's bytes must equal.
    """
    import base64

    import pydiggs
    from pydiggs import _service as svc  # lazily: see the module docstring
    from pydiggs import tables
    from pydiggs.tables import _io, _package

    name = TABLES_FIXTURE
    zip_name = name.rsplit(".", 1)[0] + ".zip"
    data = Path(official_dir, name).read_bytes()
    made = svc.tables_from_diggs(
        svc.TablesFromDiggsRequest(content=data, name=name, max_bytes=MAX_BYTES)
    )
    assert made.ok and made.document is not None, made
    package = base64.b64decode(json.loads(made.document)["output_base64"])
    written = svc.tables(svc.TablesRequest(content=package, name=zip_name, max_bytes=MAX_BYTES))
    assert written.ok and written.document is not None, written
    found = _io.tables_from_files(_package.unpack(package, max_unpacked_bytes=4 * MAX_BYTES))
    xml = pydiggs.write_bytes(tables.to_model(found).model)
    assert base64.b64decode(json.loads(written.document)["output_base64"]) == xml
    return {
        f"service-tables-from-diggs:{name}": made.document.decode("utf-8"),
        f"service-tables-to-diggs:{zip_name}": written.document.decode("utf-8"),
        f"tables-to-diggs:{zip_name}": xml.decode("utf-8"),
    }


def diff_cases(fixture_dir: str) -> dict[str, str]:
    """Model comparison (pyDIGGS 1.12): ``diff:<old>:<new>`` is ``compare(...).to_json()``.

    The first fixture against itself (identical) and against the second (they differ). The
    text report is not made here: it needs ``rich``, which the core path never imports.
    """
    from pydiggs import diff  # lazily: see the module docstring

    old = Path(fixture_dir, FIXTURES[0]).read_bytes()
    out: dict[str, str] = {}
    for name in FIXTURES:
        new = Path(fixture_dir, name).read_bytes()
        result = diff.compare(old, new, max_bytes=MAX_BYTES)
        assert result.complete and result.identical == (name == FIXTURES[0]), name
        out[f"diff:{FIXTURES[0]}:{name}"] = result.to_json()
    return out


def ags4_cases(fixture_dir: str) -> dict[str, str]:
    """AGS4 checking and conversion both ways, through the API and the service layer.

    ``convert:<name>`` is the DIGGS XML the converter writes (UTF-8 text),
    ``convert-report:<name>`` its report, ``residual:<name>`` the residual AGS4 file,
    ``ags4-check:<name>`` the AGS4 check report, and ``service-convert:<name>`` /
    ``service-ags4-check:<name>`` the documents the HTTP API and the sidecar send for the
    same bytes. That DIGGS converted back to AGS4 is ``to-ags4:<version>:<name>`` (the
    AGS4 file), ``to-ags4-report:<version>:<name>`` and ``to-ags4-check:<version>:<name>``
    (its report and its ``ags4.check``), and ``service-to-ags4:<version>:<name>``.
    """
    from pydiggs import _service as svc  # lazily: see the module docstring
    from pydiggs import ags4, convert

    out: dict[str, str] = {}
    for name in AGS4_FIXTURES:
        data = Path(fixture_dir, name).read_bytes()
        conversion = convert.ags4_to_diggs(data, name=name)
        assert conversion.xml is not None and conversion.residual is not None
        out[f"convert:{name}"] = conversion.xml.decode("utf-8")
        out[f"convert-report:{name}"] = conversion.report.to_json()
        out[f"residual:{name}"] = ags4.dumps(conversion.residual).decode("utf-8")
        out[f"ags4-check:{name}"] = ags4.check(ags4.read_bytes(data, name=name)).to_json()
        converted = svc.convert(svc.ConvertRequest(content=data, name=name, max_bytes=MAX_BYTES))
        checked = svc.ags4_check(svc.Ags4CheckRequest(content=data, name=name, max_bytes=MAX_BYTES))
        for key, outcome in (("service-convert", converted), ("service-ags4-check", checked)):
            assert outcome.ok and outcome.document is not None, outcome
            out[f"{key}:{name}"] = outcome.document.decode("utf-8")
        xml_name = name.rsplit(".", 1)[0] + ".xml"
        for version in AGS_VERSIONS:
            back = convert.diggs_to_ags4(conversion.xml, name=xml_name, ags_version=version)
            assert back.ags4 is not None and back.validation is not None
            out[f"to-ags4:{version}:{name}"] = ags4.dumps(back.ags4).decode("utf-8")
            out[f"to-ags4-report:{version}:{name}"] = back.report.to_json()
            out[f"to-ags4-check:{version}:{name}"] = back.validation.to_json()
            request = svc.ConvertRequest(
                content=conversion.xml,
                name=xml_name,
                to="ags4",
                ags_version=version,
                max_bytes=MAX_BYTES,
            )
            outcome = svc.convert(request)
            assert outcome.ok and outcome.document is not None, outcome
            out[f"service-to-ags4:{version}:{name}"] = outcome.document.decode("utf-8")
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
        # A finding cap of one, and a time budget that does not run out.
        out[f"limits:{name}"] = text(
            svc.validate(dataclasses.replace(request, max_findings=1, time_budget=3600))
        )
        select = {"results": list(SELECT_KEYS)}
        read = svc.ReadRequest(content=data, name=name, select=select, max_bytes=MAX_BYTES)
        out[f"select:{name}"] = text(svc.read(read))
    if hasattr(svc, "convert"):
        name, dictionary = AGS4_CHECK
        check = svc.Ags4CheckRequest(
            content=Path(fixture_dir, name).read_bytes(),
            name=name,
            dictionary=Path(fixture_dir, dictionary).read_bytes(),
            max_bytes=MAX_BYTES,
        )
        out[f"ags4:{name}"] = text(svc.ags4_check(check))
        bare = dataclasses.replace(check, dictionary=None)
        out[f"ags4-no-dictionary:{name}"] = text(svc.ags4_check(bare))
        out[f"ags4-sarif:{name}"] = text(svc.ags4_check(dataclasses.replace(check, format="sarif")))
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
