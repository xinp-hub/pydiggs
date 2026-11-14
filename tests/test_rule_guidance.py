"""Plain-language rule guidance: coverage of every rule, the shape, and the wording rules."""

from __future__ import annotations

import doctest
import json
import re
from importlib import resources

import pytest

from pydiggs import Guidance, RuleInfo, explain_rule, rule_catalogue
from pydiggs.findings import guidance as guidance_module
from pydiggs.findings.guidance import DEFAULT_LOCALE, LOCALES, guidance_table

#: Words the guidance is written without: its readers are engineers, not XML specialists.
JARGON = re.compile(r"\b(xml|elements?|attributes?|namespaces?|xpath|schemas?|gml)\b", re.I)


def _family_members(key: str) -> bool:
    """True when ``key`` is a concrete id of an open family (a per-id override)."""
    info = explain_rule(key)
    return info.known and info.pattern is not None and info.id == key


def test_every_rule_and_family_in_the_catalogue_has_guidance():
    missing = [r.id for r in rule_catalogue() if r.guidance is None]
    assert not missing, f"rules without guidance in en.toml: {missing}"
    for info in rule_catalogue():
        assert info.guidance == guidance_table()[info.id]
        assert info.guidance.locale == DEFAULT_LOCALE


def test_every_guidance_key_matches_a_rule():
    catalogued = {r.id for r in rule_catalogue()}
    stray = [k for k in guidance_table() if k not in catalogued and not _family_members(k)]
    assert not stray, f"guidance keys that match no rule id or family: {stray}"


@pytest.mark.parametrize("key", sorted(guidance_table()))
def test_guidance_follows_the_writing_rules(key):
    entry = guidance_table()[key]
    assert 2 <= len(entry.title.split()) <= 5, entry.title
    for text in (entry.title, entry.meaning, entry.fix):
        assert not JARGON.search(text), (key, text)
        assert text == text.strip()
    for text in (entry.meaning, entry.fix):
        assert text.endswith("."), (key, text)
        assert len(text.split()) <= 60, (key, len(text.split()))


def test_explain_rule_prefers_an_ids_own_guidance_over_its_familys():
    family = guidance_table()["schema.cvc-<rule>"]
    specific = explain_rule("schema.cvc-complex-type.2.4")
    assert specific.guidance == guidance_table()["schema.cvc-complex-type.2.4"]
    assert specific.guidance != family
    assert explain_rule("schema.cvc-length-valid").guidance == family
    assert explain_rule("geometry.incomplete").guidance == guidance_table()["<step>.incomplete"]
    assert explain_rule("schematron.my-rule").guidance == guidance_table()["schematron.<id>"]
    assert (
        explain_rule("schematron.Grading.particleSize").guidance
        == guidance_table()["schematron.Grading.particleSize"]
    )


def test_an_unknown_rule_has_no_guidance():
    info = explain_rule("crs.whatever")
    assert info.guidance is None
    assert info.to_dict()["guidance"] is None


def test_guidance_serialises_into_the_rule_info_dict():
    data = explain_rule("dictionary.5").to_dict()
    assert data["guidance"] == {
        "title": "Unknown code",
        "meaning": guidance_table()["dictionary.5"].meaning,
        "fix": guidance_table()["dictionary.5"].fix,
        "locale": "en",
    }
    assert json.loads(json.dumps(data)) == data


def test_rule_info_stays_constructible_without_guidance():
    info = RuleInfo(
        id="crs.x",
        pattern=None,
        step=explain_rule("crs.x").step,
        description="d",
        severities=(),
        help_uri="u",
        known=False,
    )
    assert info.guidance is None


def test_guidance_is_frozen():
    entry = guidance_table()["dictionary.5"]
    with pytest.raises(AttributeError):
        entry.title = "x"  # type: ignore[misc]
    assert isinstance(entry, Guidance)


def test_only_english_ships_and_other_locales_are_refused():
    assert LOCALES == ("en",)
    assert guidance_table("en") is guidance_table()
    with pytest.raises(ValueError, match="no guidance for locale 'fr'"):
        guidance_table("fr")


def test_the_guidance_file_is_packaged_next_to_its_module():
    resource = resources.files("pydiggs.findings.guidance").joinpath("en.toml")
    assert resource.is_file()
    assert "[rules." in resource.read_text("utf-8")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("text", "must have exactly title, meaning, fix"),
        ({"title": "T", "meaning": "M"}, "must have exactly title, meaning, fix"),
        ({"title": "T", "meaning": "M", "fix": "F", "extra": "x"}, "must have exactly"),
        ({"title": " ", "meaning": "M", "fix": "F"}, "title must be non-empty text"),
        ({"title": "T", "meaning": 3, "fix": "F"}, "meaning must be non-empty text"),
    ],
)
def test_a_malformed_entry_is_refused(value, message):
    with pytest.raises(ValueError, match=message):
        guidance_module._entry("k", value, "en")


def test_an_entry_has_its_whitespace_normalised():
    entry = guidance_module._entry(
        "k", {"title": " T ", "meaning": "a\n  b.", "fix": "c   d."}, "xx"
    )
    assert entry == Guidance(title="T", meaning="a b.", fix="c d.", locale="xx")


def test_a_file_without_a_rules_table_is_refused(monkeypatch):
    guidance_module._load.cache_clear()

    class _Fake:
        def joinpath(self, _name: str) -> _Fake:
            return self

        def read_text(self, _encoding: str) -> str:
            return 'title = "no table"\n'

    monkeypatch.setattr(guidance_module.resources, "files", lambda _name: _Fake())
    try:
        with pytest.raises(ValueError, match=r"has no \[rules\] table"):
            guidance_table()
    finally:
        guidance_module._load.cache_clear()


def test_the_guidance_docstring_examples_run():
    failed, attempted = doctest.testmod(guidance_module, optionflags=doctest.ELLIPSIS)
    assert attempted and failed == 0
