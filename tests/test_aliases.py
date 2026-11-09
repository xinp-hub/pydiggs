"""The reader's element aliases (``_reader/aliases.toml``) agree with the bundled XSDs."""

from __future__ import annotations

import tomllib
from functools import cache
from importlib import resources

import pytest

from pydiggs import _xml
from pydiggs.deprecations import load_registry
from pydiggs.detect import SCHEMA_PATHS

_XS = "{http://www.w3.org/2001/XMLSchema}"


def _aliases() -> list[dict[str, object]]:
    text = resources.files("pydiggs._reader").joinpath("aliases.toml").read_text("utf-8")
    data = tomllib.loads(text)
    assert data["format"] == 1
    return list(data["alias"])


@cache
def _declared(profile: str) -> frozenset[str]:
    """Global element names declared in DIGGS namespaces by a profile's bundled XSDs."""
    names: set[str] = set()
    for xsd in SCHEMA_PATHS[profile].parent.rglob("*.xsd"):
        root = _xml.parse(str(xsd)).getroot()
        if "diggsml.org" not in (root.get("targetNamespace") or ""):
            continue
        names.update(e.get("name") for e in root.iterfind(f"{_XS}element"))
    return frozenset(names)


ALIASES = _aliases()


def test_entries_are_well_formed_and_unique():
    assert ALIASES, "aliases.toml has no entries"
    for alias in ALIASES:
        assert set(alias) == {"from", "to", "versions", "since", "evidence"}
        assert alias["from"] != alias["to"]
        assert alias["versions"]
        assert set(alias["versions"]) | {alias["since"]} <= set(SCHEMA_PATHS)
        assert str(alias["evidence"]).strip()
    sources = [a["from"] for a in ALIASES]
    assert len(sources) == len(set(sources))
    assert not {a["to"] for a in ALIASES} & set(sources), "an alias must not chain"


@pytest.mark.parametrize("alias", ALIASES, ids=lambda a: str(a["from"]))
def test_alias_is_a_rename_in_the_bundled_xsds(alias):
    for profile in alias["versions"]:
        assert alias["from"] in _declared(profile)
        assert alias["to"] not in _declared(profile)
    assert alias["to"] in _declared(alias["since"])
    assert alias["from"] not in _declared(alias["since"])


def test_aliases_do_not_repeat_the_deprecation_registry():
    _, entries = load_registry()
    replaced = {e.name.rpartition(":")[2] for e in entries if e.replaced_by}
    assert not replaced & {a["from"] for a in ALIASES}


def test_reader_package_is_data_only_for_now():
    import pydiggs._reader

    assert not [n for n in vars(pydiggs._reader) if not n.startswith("__")]
