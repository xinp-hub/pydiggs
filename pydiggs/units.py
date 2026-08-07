"""Offline unit conversion using DiggsUomDictionary Energistics factors."""

from __future__ import annotations

import math
from pathlib import Path

from lxml import etree

_DEFAULT_UOM = Path(__file__).resolve().parent / "dictionaries" / "DiggsUomDictionary.xml"

_CONSTANTS = {
    "PI": math.pi,
    "pi": math.pi,
    "E": math.e,
}


def _parse_factor(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    if text in _CONSTANTS:
        return _CONSTANTS[text]
    try:
        return float(text)
    except ValueError:
        # Expressions like "2*PI" are rare; skip unit if unparsable.
        return None


class UnitConverter:
    """Convert numeric values between unit symbols using A/B/C/D factors."""

    def __init__(self, uom_path: str | Path | None = None) -> None:
        path = Path(uom_path) if uom_path else _DEFAULT_UOM
        self._factors: dict[str, tuple[float, float, float, float, str | None]] = {}
        if path.is_file():
            self._load(path)

    def _load(self, path: Path) -> None:
        root = etree.parse(str(path)).getroot()
        for unit in root.xpath("//*[local-name()='unit']"):
            symbol = None
            a = b = c = d = None
            base = None
            for child in unit:
                if not isinstance(child.tag, str):
                    continue
                name = etree.QName(child).localname
                text = (child.text or "").strip()
                if name == "symbol":
                    symbol = text
                elif name == "A":
                    a = _parse_factor(text)
                elif name == "B":
                    b = _parse_factor(text)
                elif name == "C":
                    c = _parse_factor(text)
                elif name == "D":
                    d = _parse_factor(text)
                elif name == "baseUnit":
                    base = text
            if symbol and a is not None and b is not None and c is not None and d is not None:
                self._factors[symbol] = (a, b, c, d, base)
            elif symbol and base is None and a is None:
                # Base / SI unit with identity conversion.
                self._factors[symbol] = (0.0, 1.0, 1.0, 0.0, symbol)

    def to_base(self, value: float, symbol: str) -> float | None:
        factors = self._factors.get(symbol)
        if factors is None:
            return None
        a, b, c, d, _base = factors
        denom = c + d * value
        if denom == 0:
            return None
        return (a + b * value) / denom

    def convert(self, value: float, source_uom: str, target_uom: str) -> float | None:
        if source_uom == target_uom:
            return value
        base_val = self.to_base(value, source_uom)
        if base_val is None:
            return None
        src = self._factors.get(source_uom)
        if src and src[4] == target_uom:
            return base_val
        factors = self._factors.get(target_uom)
        if factors is None:
            return None
        a, b, c, d, _ = factors
        # Invert y = (A + B x)/(C + D x)  =>  x = (y C - A)/(B - y D)
        denom = b - d * base_val
        if denom == 0:
            return None
        return (base_val * c - a) / denom


def casing_outside_gt_inside(
    outside: float,
    outside_uom: str,
    inside: float,
    inside_uom: str,
    converter: UnitConverter | None = None,
) -> tuple[bool, str]:
    """Return (ok, message) for casing diameter relationship."""
    conv = converter or UnitConverter()
    if not outside_uom or not inside_uom:
        return False, "Both casing diameters must declare a uom attribute."
    converted_inside = conv.convert(inside, inside_uom, outside_uom)
    if converted_inside is None:
        return (
            False,
            (
                f"Unable to convert casingInsideDiameter from '{inside_uom}' to "
                f"'{outside_uom}' using DiggsUomDictionary."
            ),
        )
    if outside <= converted_inside:
        return (
            False,
            (
                f"casingOutsideDiameter ({outside} {outside_uom}) must be greater than "
                f"casingInsideDiameter ({inside} {inside_uom} = {converted_inside} {outside_uom})."
            ),
        )
    return True, ""
