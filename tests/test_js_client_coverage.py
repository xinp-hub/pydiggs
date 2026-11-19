"""``@pydiggs/client`` keeps up with :func:`pydiggs.capabilities`.

``js/capabilities-coverage.json`` places every operation in one section: offered by the
npm client, planned for it, or not offered (with the reason). An operation added to the
catalogue without a place there fails here, in the pull request that adds it, so a new
pyDIGGS feature never reaches a release with the client silently behind.
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
    for section in ("planned", "not_offered"):
        for op_id, reason in coverage[section].items():
            assert isinstance(reason, str) and reason.strip(), f"{section}.{op_id} needs a reason"
    for op_id, entry in coverage["offered"].items():
        assert isinstance(entry, dict), op_id
        assert entry.get("methods"), f"offered.{op_id} names no client method"
        assert isinstance(entry.get("hybrid"), bool), (
            f"offered.{op_id}.hybrid must be true or false"
        )
