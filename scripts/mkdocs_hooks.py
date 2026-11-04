"""MkDocs hooks: publish pydiggs's machine-readable documents with the docs site.

- the diggs-findings JSON Schema, at the path its ``$id`` names;
- the HTTP API's OpenAPI 3.1 document (``pydiggs/server/openapi.json``);
- the stdio sidecar's OpenRPC 1.3 document (``pydiggs/sidecar/openrpc.json``).

Each is copied byte for byte from the package at build time, so the published file can
never drift from the one in the wheel (``tests/test_docs_schema.py``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "pydiggs"
SCHEMA = PACKAGE / "findings" / "diggs-findings-1.0.schema.json"
#: Path of the schema under the site root; matches the schema's ``$id``.
PUBLISHED = Path("schemas") / SCHEMA.name
OPENAPI = PACKAGE / "server" / "openapi.json"
OPENRPC = PACKAGE / "sidecar" / "openrpc.json"
#: Every published document: packaged source -> path under the site root.
DOCUMENTS: dict[Path, Path] = {
    SCHEMA: PUBLISHED,
    OPENAPI: Path("schemas") / "openapi.json",
    OPENRPC: Path("schemas") / "openrpc.json",
}


def on_post_build(config: Any) -> None:
    """Copy every document in ``DOCUMENTS`` into ``<site_dir>/schemas/``."""
    for source, published in DOCUMENTS.items():
        target = Path(config["site_dir"]) / published
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
