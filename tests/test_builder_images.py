"""The release container's builder images are pinned in one place, by digest."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / ".github" / "builder-images" / "Dockerfile"
PUBLISH = ROOT / ".github" / "workflows" / "publish.yml"
STAGES = ("binfmt", "buildkit", "sbom")
#: The same rule publish.yml's "Read the builder image pins" step enforces.
DIGEST_PINNED = re.compile(r"^[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")

pytestmark = pytest.mark.skipif(not PINS.exists(), reason="not a repository checkout")


def _pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in PINS.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[0] == "FROM" and fields[2] == "AS":
            pins[fields[3]] = fields[1]
    return pins


def test_every_stage_is_pinned_by_digest() -> None:
    pins = _pins()
    assert sorted(pins) == sorted(STAGES)
    for stage, ref in pins.items():
        assert DIGEST_PINNED.match(ref), (stage, ref)


def test_publish_reads_the_pins_instead_of_repeating_them() -> None:
    workflow = PUBLISH.read_text(encoding="utf-8")
    for ref in _pins().values():
        assert ref.split("@", 1)[1] not in workflow, ref
    for stage in STAGES:
        assert f"steps.builders.outputs.{stage}" in workflow, stage
