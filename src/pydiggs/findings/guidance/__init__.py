"""Plain-language guidance for rule ids: a title, what it means, and how to fix it.

The text is written for geotechnical and geoenvironmental engineers, not for DIGGS or
XML specialists, and lives in one data file per locale (``en.toml`` here), keyed by rule
id or by an open family's template (``"schema.cvc-<rule>"``). Only English ships.

The findings document does not carry guidance: an app joins a finding to its guidance
on ``rule_id`` through :func:`pydiggs.explain_rule`.

Reads only its own packaged data file; no lxml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Final

__all__ = ["DEFAULT_LOCALE", "LOCALES", "Guidance", "guidance_table"]

#: The locale :func:`pydiggs.explain_rule` and :func:`pydiggs.rule_catalogue` use.
DEFAULT_LOCALE: Final = "en"
#: Every locale with a packaged guidance file.
LOCALES: Final = (DEFAULT_LOCALE,)

_FIELDS: Final = ("title", "meaning", "fix")


@dataclass(frozen=True, slots=True, kw_only=True)
class Guidance:
    """What a rule means for your data, in plain words, and what to do about it.

    Attributes:
        title: A few words that name the problem ("Unknown code").
        meaning: What is wrong with the data, and why it matters.
        fix: What to do, in your software or with your data supplier.
        locale: The language of the text, as a BCP 47 tag (``"en"``).

    Examples:
        >>> from pydiggs import explain_rule
        >>> guidance = explain_rule("dictionary.5").guidance
        >>> guidance.title, guidance.locale
        ('Unknown code', 'en')
        >>> sorted(guidance.to_dict())
        ['fix', 'locale', 'meaning', 'title']
    """

    title: str
    meaning: str
    fix: str
    locale: str

    def to_dict(self) -> dict[str, str]:
        """A JSON-serialisable dict, in field order."""
        return {
            "title": self.title,
            "meaning": self.meaning,
            "fix": self.fix,
            "locale": self.locale,
        }


def _entry(key: str, value: Any, locale: str) -> Guidance:
    if not isinstance(value, dict) or sorted(value) != sorted(_FIELDS):
        raise ValueError(f"guidance {key!r} ({locale}) must have exactly {', '.join(_FIELDS)}")
    texts = {name: value[name] for name in _FIELDS}
    for name, text in texts.items():
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"guidance {key!r} ({locale}): {name} must be non-empty text")
    return Guidance(
        title=texts["title"].strip(),
        meaning=" ".join(texts["meaning"].split()),
        fix=" ".join(texts["fix"].split()),
        locale=locale,
    )


def guidance_table(locale: str = DEFAULT_LOCALE) -> MappingProxyType[str, Guidance]:
    """Every guidance entry of ``locale``, keyed by rule id or family template.

    Args:
        locale: One of :data:`LOCALES`.

    Returns:
        A read-only mapping, the same object on every call.

    Raises:
        ValueError: ``locale`` has no guidance file, or the file is malformed.

    Examples:
        >>> table = guidance_table()
        >>> table["dictionary.5"].title
        'Unknown code'
        >>> table["schema.cvc-<rule>"].locale
        'en'
    """
    if locale not in LOCALES:
        raise ValueError(f"no guidance for locale {locale!r}; available: {', '.join(LOCALES)}")
    return _load(locale)


@cache
def _load(locale: str) -> MappingProxyType[str, Guidance]:
    text = resources.files(__name__).joinpath(f"{locale}.toml").read_text("utf-8")
    rules = tomllib.loads(text).get("rules")
    if not isinstance(rules, dict):
        raise ValueError(f"guidance file {locale}.toml has no [rules] table")
    return MappingProxyType({key: _entry(key, value, locale) for key, value in rules.items()})
