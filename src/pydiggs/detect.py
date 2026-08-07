"""Detect DIGGS schema version from instance namespace URIs."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

# Instance root (or any element) namespace → profile key used by bundled assets.
NAMESPACE_PROFILES: dict[str, str] = {
    "http://diggsml.org/schemas/2.5.a": "2.5.a",
    "http://diggsml.org/schemas/2.6": "2.6",
    "http://diggsml.org/schemas/3": "3.0.0",
    # schema-dev NS appears in some official Schematron / experimental docs
    "http://diggsml.org/schema-dev": "3.0.0",
}

DEFAULT_PROFILE = "3.0.0"

_PACKAGE_DIR = Path(__file__).resolve().parent

SCHEMA_PATHS: dict[str, Path] = {
    "2.5.a": _PACKAGE_DIR / "schemas" / "diggs-schema-2.5.a" / "Diggs.xsd",
    "2.6": _PACKAGE_DIR / "schemas" / "diggs-schema-2.6" / "Diggs.xsd",
    "3.0.0": _PACKAGE_DIR / "schemas" / "diggs-schema-3.0.0" / "Diggs.xsd",
}


def namespace_of_root(doc: etree._ElementTree | etree._Element) -> str | None:
    """Return the DIGGS-ish namespace URI of the document root, if any."""
    root = doc.getroot() if hasattr(doc, "getroot") else doc
    tag = root.tag
    if isinstance(tag, str) and tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    # fallback: default xmlns on root
    ns = root.nsmap.get(None) or root.nsmap.get("diggs")
    return str(ns) if ns is not None else None


def detect_diggs_version(
    source: str | Path | etree._ElementTree | etree._Element,
    *,
    default: str = DEFAULT_PROFILE,
) -> str:
    """Detect a DIGGS profile key from an instance path or parsed document.

    Unknown or missing namespaces fall back to ``default`` (3.0.0 per HG1/HG2).
    """
    if isinstance(source, (str, Path)):
        doc = etree.parse(str(source))
        ns = namespace_of_root(doc)
    else:
        ns = namespace_of_root(source)

    if ns is None:
        return default
    return NAMESPACE_PROFILES.get(ns, default)


def schema_path_for(profile: str) -> Path:
    """Return the bundled Diggs.xsd path for a profile key."""
    try:
        return SCHEMA_PATHS[profile]
    except KeyError as exc:
        raise ValueError(
            f"Unknown DIGGS profile {profile!r}; expected one of {sorted(SCHEMA_PATHS)}"
        ) from exc
