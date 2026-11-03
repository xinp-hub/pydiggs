"""Bundled reference data paths and pydiggs's own casing rule.

Shared by the legacy :class:`pydiggs.validator` and :func:`pydiggs.validate`. It lives
apart from ``pydiggs.py`` so the findings layer never imports the legacy module's
console output (rich).
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from pydiggs.units import UnitConverter, casing_outside_gt_inside

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_DICTIONARY = _PACKAGE_DIR / "dictionaries" / "properties.xml"
_DEFAULT_SCHEMATRON = _PACKAGE_DIR / "schematron" / "diggs_schematron_rules_2.6.sch"


def _casing_diameter_problems(
    instance_doc: etree._ElementTree,
) -> list[tuple[etree._Element, str]]:
    """Return ``(Casing element, message)`` for every casing whose diameters fail."""
    converter = UnitConverter()
    problems: list[tuple[etree._Element, str]] = []
    for casing in instance_doc.xpath("//*[local-name()='Casing']"):
        outside_el = next(
            (
                c
                for c in casing
                if isinstance(c.tag, str) and etree.QName(c).localname == "casingOutsideDiameter"
            ),
            None,
        )
        inside_el = next(
            (
                c
                for c in casing
                if isinstance(c.tag, str) and etree.QName(c).localname == "casingInsideDiameter"
            ),
            None,
        )
        if outside_el is None or inside_el is None:
            continue
        try:
            outside = float((outside_el.text or "").strip())
            inside = float((inside_el.text or "").strip())
        except ValueError:
            problems.append((casing, "Casing diameter values must be numeric."))
            continue
        ok, msg = casing_outside_gt_inside(
            outside,
            outside_el.get("uom") or "",
            inside,
            inside_el.get("uom") or "",
            converter,
        )
        if not ok:
            problems.append((casing, msg))
    return problems
