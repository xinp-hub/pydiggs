"""What a tables conversion could not carry: one finding per kind of issue and place.

Every value of the source is either carried into the output or named here. An issue that
repeats on many rows of one column is one finding that counts them, as the AGS4 converter
reports its losses.
"""

from __future__ import annotations

from typing import Final

from pydiggs.findings.model import Finding, Severity, Step

__all__ = ["RULES", "Notes"]

UNMAPPED_COLUMN: Final = "tables.unmapped-column"
UNKNOWN_UNIT: Final = "tables.unknown-unit"
ORPHAN_ROW: Final = "tables.orphan-row"
DUPLICATE_KEY: Final = "tables.duplicate-key"
KEY_REWRITTEN: Final = "tables.key-rewritten"
UNPARSED: Final = "tables.value-unparsed"
ASSUMED: Final = "tables.assumed"
INCOMPLETE: Final = "tables.incomplete"
EXTRA_NOT_WRITTEN: Final = "tables.extra-not-written"
NOT_CARRIED: Final = "tables.not-carried"
REFORMATTED: Final = "tables.value-reformatted"
NO_CRS: Final = "tables.no-crs"
UNKNOWN_PROPERTY: Final = "tables.unknown-property"
UNKNOWN_CODE: Final = "tables.unknown-code"
MAPPING_UNMAPPED_SOURCE: Final = "tables.mapping-unmapped-source"
MAPPING_UNMAPPED_CODE: Final = "tables.mapping-unmapped-code"
MAPPING_CODE_NOT_EXACT: Final = "tables.mapping-code-not-exact"
MAPPING_UNKNOWN_UNIT: Final = "tables.mapping-unknown-unit"
MAPPING_UNKNOWN_TARGET: Final = "tables.mapping-unknown-target"

#: Every rule a tables conversion reports, and how serious it is.
RULES: Final[dict[str, Severity]] = {
    UNMAPPED_COLUMN: Severity.WARNING,
    UNKNOWN_UNIT: Severity.WARNING,
    ORPHAN_ROW: Severity.ERROR,
    DUPLICATE_KEY: Severity.ERROR,
    KEY_REWRITTEN: Severity.INFO,
    UNPARSED: Severity.WARNING,
    ASSUMED: Severity.WARNING,
    INCOMPLETE: Severity.ERROR,
    EXTRA_NOT_WRITTEN: Severity.WARNING,
    NOT_CARRIED: Severity.WARNING,
    REFORMATTED: Severity.INFO,
    NO_CRS: Severity.WARNING,
    UNKNOWN_PROPERTY: Severity.WARNING,
    UNKNOWN_CODE: Severity.WARNING,
    MAPPING_UNMAPPED_SOURCE: Severity.WARNING,
    MAPPING_UNMAPPED_CODE: Severity.WARNING,
    MAPPING_CODE_NOT_EXACT: Severity.INFO,
    MAPPING_UNKNOWN_UNIT: Severity.WARNING,
    MAPPING_UNKNOWN_TARGET: Severity.ERROR,
}

_SHOWN: Final = 60


def shown(value: object, limit: int = _SHOWN) -> str:
    """``value`` for a message: its text, cut short, with no control characters."""
    text = "".join(ch if ch.isprintable() else "?" for ch in str(value))
    text = text.replace("{times}", "{ times }")  # a value is never taken for the count
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


class Notes:
    """Collects issues; the same rule, table, column and message is one finding."""

    def __init__(self) -> None:
        self._issues: dict[tuple[str, str, str, str], list[int | None]] = {}
        #: Table -> the position in the input of each row kept (blank rows are not kept),
        #: so a finding names the row as the input numbers it.
        self.positions: dict[str, list[int]] = {}

    def add(
        self,
        rule: str,
        table: str,
        message: str,
        *,
        row: int | None = None,
        column: str = "",
        count: bool = False,
    ) -> None:
        """Record one issue.

        ``message`` may hold ``{times}``, the number of rows the issue was met on. An
        issue with no ``row`` is counted only when ``count`` is set.
        """
        kept = self.positions.get(table)
        if row is not None and kept is not None and 0 <= row < len(kept):
            row = kept[row]
        met = self._issues.setdefault((rule, table, column, message), [])
        if row is not None or count or not met:
            met.append(row)

    def findings(self) -> tuple[Finding, ...]:
        """One finding per issue, in the order the issues were first met."""
        out: list[Finding] = []
        for (rule, table, column, message), met in self._issues.items():
            times = "1 row" if len(met) == 1 else f"{len(met)} rows"
            first = next((row for row in met if row is not None), None)
            path = table
            if first is not None:  # rows are counted from 1; a column is named with its row
                path += f"/{first + 1}"
                if column:
                    path += f"/{column}"
            out.append(
                Finding(
                    step=Step.TABLES,
                    rule_id=rule,
                    severity=RULES[rule],
                    message=message.replace("{times}", times),
                    path=path,
                )
            )
        return tuple(out)
