"""Conformance keys: how findings from two validators are matched.

Two findings match when their step, severity, location key and rule id match; the
message text is never compared. Native and official findings are compared as
multisets of keys, restricted to the steps both layers ran.

Pure: no lxml, no I/O.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Final

__all__ = ["ConformanceKey", "compare", "location_key", "normalise_xpath"]

#: ``(step, severity, location, rule_id)``, all plain strings.
ConformanceKey = tuple[str, str, str, str]

_STEP: Final = re.compile(
    r"^(?P<attr>@)?(?:(?P<prefix>[^:\[\]/]+):)?(?P<name>[^\[\]/]+)(?P<pred>\[\d+\])?$"
)


def normalise_xpath(xpath: str) -> str:
    """Normalise an element path of the official ``diggs:get-path`` form for comparison.

    Namespace prefixes are dropped (documents bind them differently) and every element
    step gets an explicit position, so ``/diggs:Diggs/diggs:Borehole[2]`` and
    ``/Diggs[1]/Borehole[2]`` normalise to the same string. A path that is not a
    simple location path is returned with surrounding whitespace removed.

    Example:
        >>> normalise_xpath("/diggs:Diggs[1]/diggs:Borehole[2]/@gml:id")
        '/Diggs[1]/Borehole[2]/@id'
        >>> normalise_xpath(" /Diggs/samplingFeature ")
        '/Diggs[1]/samplingFeature[1]'
    """
    text = xpath.strip()
    if not text.startswith("/") or text.startswith("//"):
        return text
    out: list[str] = []
    for part in text[1:].split("/"):
        match = _STEP.match(part.strip())
        if match is None:
            return text
        if match["attr"]:
            out.append(f"@{match['name']}")
        else:
            out.append(f"{match['name']}{match['pred'] or '[1]'}")
    return "/" + "/".join(out)


def location_key(xpath: str | None, line: int | None) -> str:
    """The location part of a conformance key: xpath if known, else line, else file.

    Example:
        >>> location_key("/Diggs[1]", 3)
        'xpath:/Diggs[1]'
        >>> location_key(None, 3)
        'line:3'
        >>> location_key(None, None)
        'file'
    """
    if xpath:
        return f"xpath:{normalise_xpath(xpath)}"
    if line is not None:
        return f"line:{line}"
    return "file"


def compare(
    left: Iterable[ConformanceKey],
    right: Iterable[ConformanceKey],
    steps: Iterable[str],
) -> tuple[Counter[ConformanceKey], Counter[ConformanceKey]]:
    """Compare two multisets of keys over ``steps``; return (only left, only right).

    Example:
        >>> a = [("schema", "error", "line:3", "schema.cvc-elt.1")] * 2
        >>> b = [("schema", "error", "line:3", "schema.cvc-elt.1")]
        >>> only_a, only_b = compare(a, b, ["schema"])
        >>> sum(only_a.values()), sum(only_b.values())
        (1, 0)
    """
    wanted = set(steps)
    lhs = Counter(k for k in left if k[0] in wanted)
    rhs = Counter(k for k in right if k[0] in wanted)
    return lhs - rhs, rhs - lhs
