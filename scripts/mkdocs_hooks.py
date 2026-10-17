"""MkDocs hooks: publish the diggs-findings JSON Schema with the docs site.

The schema is copied byte for byte from the package at build time, so the published
file can never drift from the one in the wheel (``tests/test_docs_schema.py``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "src" / "pydiggs" / "findings" / "diggs-findings-1.0.schema.json"
#: Path of the schema under the site root; matches the schema's ``$id``.
PUBLISHED = Path("schemas") / SCHEMA.name


def on_post_build(config: Any) -> None:
    """Copy the schema into ``<site_dir>/schemas/``."""
    target = Path(config["site_dir"]) / PUBLISHED
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCHEMA, target)
