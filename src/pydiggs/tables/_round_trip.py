"""The round-trip check: tables to DIGGS and back, and every value that did not return.

This is the test a vendor runs on their own export: it writes the tables as DIGGS,
validates the file, reads it back into tables, and compares what came back with what was
sent, value by value. Rows that could not be written are in the findings; values that
were written but read back differently are differences.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydiggs._reader import read_bytes
from pydiggs._write import write_bytes
from pydiggs.convert._values import parse_number
from pydiggs.findings.model import Finding, Report
from pydiggs.findings.run import validate_bytes
from pydiggs.tables._from_model import from_model
from pydiggs.tables._profile import RESULT_KEYS, RESULTS_PREFIX, TABLES
from pydiggs.tables._to_model import _text, to_model

__all__ = ["Difference", "RoundTrip", "round_trip"]

Row = Mapping[str, Any]
#: Tables whose rows are identified by ``key``.
_KEYED: Final = tuple(name for name, table in TABLES.items() if table.primary_key == ("key",))
_MISSING: Final = "(row)"


@dataclass(frozen=True, slots=True)
class Difference:
    """One value that was sent and did not come back the same.

    ``row`` identifies the row (its key, or the test and row number of a result);
    ``column`` is ``"(row)"`` when the whole row is missing from what came back.
    """

    table: str
    row: str
    column: str
    sent: Any
    got: Any


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """The outcome of :func:`round_trip`.

    ``findings`` is what writing left out or assumed; ``validation`` is the report of
    :func:`pydiggs.validate_bytes` on the DIGGS written (``diggs``); ``differences``
    lists the values that came back changed.
    """

    diggs: bytes
    findings: tuple[Finding, ...]
    validation: Report
    differences: tuple[Difference, ...]

    @property
    def ok(self) -> bool:
        """True when nothing was left out, the file has no error and nothing changed."""
        lost = any(finding.severity.rank for finding in self.findings)
        invalid = any(f.severity.rank > 1 for f in self.validation.findings)
        return not lost and not invalid and not self.differences


def _same(sent: Any, got: Any) -> bool:
    """True when two cells hold the same value: the same number, or the same text."""
    a, b = _text(sent), _text(got)
    if a == b:
        return True
    if a is None or b is None:
        return False
    x, y = parse_number(a), parse_number(b)
    return x is not None and x == y


class _Comparison:
    def __init__(self, keys: Mapping[str, str]) -> None:
        self.keys = keys
        self.found: list[Difference] = []

    def cell(self, table: str, row: str, column: str, sent: Any, got: Any, *, ref: bool) -> None:
        if _text(sent) is None:
            return
        wanted = self.keys.get(str(_text(sent)), sent) if ref else sent
        if not _same(wanted, got):
            self.found.append(Difference(table, row, column, sent, got))

    def keyed(self, name: str, sent: Sequence[Row], back: Sequence[Row]) -> None:
        """Compare a table whose rows have a ``key``; a row that was left out is a finding."""
        by_key = {str(row.get("key")): row for row in back}
        refs = {field.name for field in TABLES[name].fields if field.references}
        known = set(TABLES[name].names())
        for row in sent:
            given = _text(row.get("key"))
            made = self.keys.get(given or "")
            if given is None or made is None:
                continue  # not converted: the findings name it
            got = by_key.get(made)
            if got is None:
                self.found.append(Difference(name, given, _MISSING, given, None))
                continue
            for column, value in row.items():
                if column in known and column != "key":
                    self.cell(name, given, column, value, got.get(column), ref=column in refs)

    def params(self, sent: Sequence[Row], back: Sequence[Row]) -> None:
        index = {
            (str(row.get("node")), _text(row.get("scope")) or "", str(row.get("name"))): row
            for row in back
        }
        for row in sent:
            node, name = _text(row.get("node")), _text(row.get("name"))
            made = self.keys.get(node or "")
            if node is None or name is None or made is None:
                continue
            label = f"{node} {name}"
            got = index.get((made, _text(row.get("scope")) or "", name))
            if got is None:
                self.found.append(Difference("params", label, _MISSING, row.get("value"), None))
                continue
            for column in ("value", "uom", "code_space"):
                self.cell("params", label, column, row.get(column), got.get(column), ref=False)

    def results(self, name: str, sent: Sequence[Row], back: Sequence[Row]) -> None:
        """Compare a result table test by test, each test's rows in their order."""
        returned: dict[str, list[Row]] = {}
        for row in back:
            returned.setdefault(str(row.get("measurement")), []).append(row)
        grouped: dict[str, list[tuple[float, int, Row]]] = {}
        for at, row in enumerate(sent):
            owner = _text(row.get("measurement"))
            if owner is not None:
                order = parse_number(_text(row.get("row")) or "")
                grouped.setdefault(owner, []).append((order or 0.0, at, row))
        for owner, rows in grouped.items():
            made = self.keys.get(owner)
            if made is None:
                continue
            got_rows = returned.get(made, [])
            for number, (_order, _at, row) in enumerate(sorted(rows, key=lambda r: r[:2])):
                label = f"{owner} row {number}"
                if number >= len(got_rows):
                    values = [v for c, v in row.items() if c not in RESULT_KEYS]
                    if any(_text(v) is not None for v in values):
                        self.found.append(Difference(name, label, _MISSING, owner, None))
                    continue
                for column, value in row.items():
                    if column not in RESULT_KEYS and column != "depth_uom":
                        got = got_rows[number].get(column)
                        self.cell(name, label, column, value, got, ref=False)


def round_trip(tables: Mapping[str, Iterable[Mapping[str, Any]]]) -> RoundTrip:
    """Write ``tables`` as DIGGS, read the file back, and compare it with what was sent.

    Every non-empty value of ``document``, of the tables whose rows have a ``key``, of
    ``params`` and of the result tables is looked up in what came back; numbers are
    compared as numbers. ``remarks``, ``links`` and ``result_columns`` are covered by the
    findings, not by the comparison.

    Args:
        tables: The tables, as for :func:`~pydiggs.tables.to_model`.

    Returns:
        The DIGGS written, the findings of writing it, its validation report, and every
        value that did not come back the same. ``ok`` is true when all three are clean.

    Raises:
        TablesError: See :func:`~pydiggs.tables.to_model`.

    Examples:
        >>> checked = round_trip(
        ...     {
        ...         "document": [{"creation_date": "2026-11-21"}],
        ...         "projects": [{"key": "P1", "name": "Riverside"}],
        ...         "features": [{"key": "BH-1", "project": "P1", "depth_uom": "ft"}],
        ...     }
        ... )
        >>> checked.ok, checked.differences
        (True, ())
    """
    sent = {name: [dict(row) for row in rows] for name, rows in tables.items()}
    result = to_model(sent)
    diggs = write_bytes(result.model)
    back = from_model(read_bytes(diggs))
    compare = _Comparison(result.keys)
    for name in _KEYED:
        if name in sent:
            compare.keyed(name, sent[name], back.rows(name))
    if sent.get("document"):
        returned = (back.rows("document") or [{}])[0]
        for column, value in sent["document"][0].items():
            compare.cell("document", "1", column, value, returned.get(column), ref=False)
    compare.params(sent.get("params", []), back.rows("params"))
    for name, rows in sent.items():
        if name.startswith(RESULTS_PREFIX):
            compare.results(name, rows, back.rows(name))
    return RoundTrip(
        diggs=diggs,
        findings=result.findings,
        validation=validate_bytes(diggs),
        differences=tuple(compare.found),
    )
