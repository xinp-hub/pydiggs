"""``@pydiggs/client`` keeps up with :func:`pydiggs.capabilities`.

``js/capabilities-coverage.json`` places every operation in one section: offered by the
npm client, planned for it, or not offered (with the reason). An operation added to the
catalogue without a place there fails here, in the pull request that adds it, so a new
pyDIGGS feature never reaches a release with the client silently behind. An offered or
planned entry may say ``"engines": "pyodide"``: its methods are on the engines that run
pyDIGGS itself and not on the HTTP client, for an operation pyDIGGS has no HTTP route for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pydiggs

COVERAGE = Path(__file__).resolve().parents[1] / "js" / "capabilities-coverage.json"
SECTIONS = ("offered", "planned", "not_offered")

pytestmark = pytest.mark.skipif(not COVERAGE.is_file(), reason="js/ is not in the sdist")


def _coverage() -> dict[str, dict[str, object]]:
    data = json.loads(COVERAGE.read_text(encoding="utf-8"))
    assert set(data) - {"$comment"} == set(SECTIONS)
    return {section: data[section] for section in SECTIONS}


def test_every_operation_has_one_place_in_the_npm_client():
    coverage = _coverage()
    operations = {op.id for op in pydiggs.capabilities().operations}
    placed: dict[str, list[str]] = {}
    for section, entries in coverage.items():
        for op_id in entries:
            placed.setdefault(op_id, []).append(section)

    unplaced = sorted(operations - placed.keys())
    assert not unplaced, (
        f"pyDIGGS offers {unplaced} but js/capabilities-coverage.json does not say whether"
        " @pydiggs/client offers it: add it to 'offered' (with the client methods),"
        " 'planned' or 'not_offered' (with the reason)"
    )
    stale = sorted(placed.keys() - operations)
    assert not stale, (
        f"js/capabilities-coverage.json lists operations pyDIGGS no longer has: {stale}"
    )
    twice = {op_id: sections for op_id, sections in placed.items() if len(sections) > 1}
    assert not twice, f"operations listed in more than one section: {twice}"


def test_reasons_are_written_down():
    coverage = _coverage()
    for op_id, reason in coverage["not_offered"].items():
        assert isinstance(reason, str) and reason.strip(), f"not_offered.{op_id} needs a reason"
    for op_id, plan in coverage["planned"].items():
        assert isinstance(plan, dict), op_id
        assert plan.get("methods"), f"planned.{op_id} names no client method"
        assert str(plan.get("why", "")).strip(), f"planned.{op_id} needs a reason"
    for op_id, entry in coverage["offered"].items():
        assert isinstance(entry, dict), op_id
        assert entry.get("methods"), f"offered.{op_id} names no client method"
        assert isinstance(entry.get("hybrid"), bool), (
            f"offered.{op_id}.hybrid must be true or false"
        )


def test_an_entry_for_the_pyodide_engines_only_says_so_in_one_way():
    coverage = _coverage()
    for section in ("offered", "planned"):
        for op_id, entry in coverage[section].items():
            assert isinstance(entry, dict), op_id
            assert entry.get("engines", "pyodide") == "pyodide", (
                f"{section}.{op_id}.engines is 'pyodide' or is left out"
            )
            # PydiggsHybrid falls back to the HTTP client, which has no such method.
            assert not ("engines" in entry and entry.get("hybrid")), (
                f"{section}.{op_id}: an operation of the Pyodide engines only is not hybrid"
            )


def test_the_conformance_suite_is_for_the_pyodide_engines_only():
    # pyDIGGS has no HTTP route for the suite, so no entry may promise an HTTP method.
    coverage = _coverage()
    placed = {**coverage["offered"], **coverage["planned"]}
    suite = {op_id: entry for op_id, entry in placed.items() if op_id.startswith("conformance.")}
    assert sorted(suite) == sorted(
        op.id for op in pydiggs.capabilities().operations if op.id.startswith("conformance.")
    )
    for op_id, entry in suite.items():
        assert isinstance(entry, dict), op_id
        assert entry.get("engines") == "pyodide", op_id
