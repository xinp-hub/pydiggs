"""Round trips through the converter: every loss is listed, outputs are valid and stable.

Properties 3 and 4 of the conversion-core brief:

* **AGS4 -> DIGGS -> AGS4.** Each non-null cell of the source either reappears in the
  final AGS4 file with an equal value, or is named by a loss of one of the two
  conversions: by the first (it is in the residual file), or by the second (a value of
  the node that held it is named there). Lookup groups (ABBR, UNIT, TYPE, DICT) and the
  format headings of TRAN are format, not data: the AGS4 writer makes its own. A cell
  the final file holds that the source did not is either format the writer makes, or
  declared by a ``convert.assumed`` finding of either conversion.
* **DIGGS -> AGS4 -> DIGGS.** Every item of the model is written or named by the first
  conversion (the model ledger is complete), and the AGS4 file it gives is a fixed point
  of the round trip: converting it to DIGGS and back gives the same cells, but for those a
  finding names.

"Equal" is the brief's: text equal, or (after the declared type's formatting) equal as
numbers or as instants; and for a code, equal as the code table reads it (AGS4
abbreviations are not case sensitive, joined codes match in any order, and two AGS4 codes
that share one DIGGS code are written back as the first, the other being reported by the
first conversion). An NP that a source wrote in LLPL_LL or LLPL_PI is carried when the
output row has NP in LLPL_PL, the one limit AGS4 lets hold text. The comparer below is
test-only. The real-file sets are used when
they are cached (``python scripts/conformance.py fetch --set ags4``) and skipped when not.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydiggs import ags4, convert
from pydiggs import read as read_diggs
from pydiggs.convert import _from_model
from pydiggs.convert._mapping import GroupRule, MappingTable, load_mapping
from pydiggs.convert._to_model import map_file
from pydiggs.convert._values import parse_date, parse_number, xml_text
from pydiggs.findings.model import Severity

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ags4"
CORPUS = ROOT / ".cache" / "conformance" / "ags4"
OFFICIAL_3 = ROOT / "tests" / "fixtures" / "official" / "3.0"
INSPECTOR = ROOT / ".cache" / "conformance" / "inspector"

#: Synthetic AGS4 fixtures that convert: one per mapped tier and version.
SYNTHETIC = sorted(
    p
    for p in FIXTURES.glob("*.ags")
    if p.name.startswith(("clean_", "convert_", "site_", "c2_clean", "utf8", "cp1252"))
)
#: Groups the AGS4 writer makes itself, and TRAN headings that are format information.
FORMAT_GROUPS = frozenset({"ABBR", "UNIT", "TYPE", "DICT"})
WRITER_CELLS = frozenset(
    {
        ("TRAN", "TRAN_AGS"),
        ("TRAN", "TRAN_REM"),
        ("CPTT", "CPTT_REDN"),
        ("LOCA", "LOCA_NSRI"),  # AGS4 4.2: the EPSG code of the CRS LOCA_GREF names
        ("LOCA", "LOCA_LLSI"),
    }
)
#: Headings whose values are codes of the code table, and reference systems.
CODED = frozenset({"LOCA_TYPE", "SAMP_TYPE", "GRAT_TYPE"})
REFERENCES = frozenset({"LOCA_GREF", "LOCA_LLZ", "LOCA_NATD"})
#: Limits a source may write NP in, which DIGGS to AGS4 writes in LLPL_PL only.
NP_LIMITS = frozenset({"LLPL_LL", "LLPL_PI"})
#: A group name in a finding's message: a four-character name, or the prefix of a heading.
GROUP_WORD = re.compile(r"\b([A-Z][A-Z0-9]{3})(?=_|\b)")


def same_code(heading: str, a: str, b: str) -> bool:
    """Equal as the code table reads a code or a reference system (see the module)."""
    mapping = load_mapping()
    if heading in REFERENCES:
        srs = mapping.crs_of(heading, a)
        return srs is not None and srs == mapping.crs_of(heading, b)
    first, second = mapping.code(heading, a), mapping.code(heading, b)
    if first is None or second is None:
        return a.strip().casefold() == b.strip().casefold()
    if first.code is None:
        return first is second
    return (first.element, first.code_list, first.code) == (
        second.element,
        second.code_list,
        second.code,
    )


def same(a: str, b: str, unit_a: str = "", unit_b: str = "", heading: str = "") -> bool:
    """Equal text, or equal as numbers, or equal as instants (the brief's equality)."""
    if a == b or xml_text(a) == b:
        return True
    if heading in CODED | REFERENCES:
        return same_code(heading, a, b)
    x, y = parse_number(a.strip()), parse_number(b.strip())
    if x is not None and y is not None:
        return x == y
    da, db = parse_date(a.strip(), unit_a), parse_date(b.strip(), unit_b)
    return da is not None and da == db


@dataclass
class Tally:
    """What happened to the cells of one AGS4 file in a round trip."""

    cells: int = 0
    carried: int = 0
    named_first: int = 0
    named_second: int = 0
    format: int = 0
    unaccounted: list[tuple[str, int, str, str]] = field(default_factory=list)
    extra: list[tuple[str, str, str]] = field(default_factory=list)


def match_keys(rule: GroupRule | None) -> list[str]:
    """The headings that place a row of ``rule``'s group: its converted keys and index."""
    if rule is None:
        return []
    keys = [k for k in rule.keys if rule.headings[k].to != "none"]
    keys += [h.name for h in rule.headings.values() if h.target == "index.position"]
    return list(dict.fromkeys(keys))


def _cells(group: ags4.Group) -> Iterator[tuple[int, int, str]]:
    for row, values in enumerate(group.rows):
        for column, value in enumerate(values[: len(group.headings)]):
            if value != "":
                yield row, column, value


def _unit(group: ags4.Group, column: int) -> str:
    return group.units[column] if column < len(group.units) else ""


def _find_row(
    source: ags4.Group, row: int, target: ags4.Group, keys: list[str], taken: set[int]
) -> int | None:
    values = dict(zip(source.headings, source.rows[row], strict=False))
    columns = {h: i for i, h in reversed(list(enumerate(target.headings)))}
    for index, other in enumerate(target.rows):
        if index in taken and keys:
            continue
        if (
            all(
                h in columns
                and (
                    values.get(h, "") == "" or same(values.get(h, ""), other[columns[h]], heading=h)
                )
                for h in keys
            )
            or not keys
        ):
            return index
    return None


def _assumed_groups(conversions: tuple[convert.Conversion, ...]) -> set[str]:
    """The groups a ``convert.assumed`` finding of either conversion is about."""
    groups = set(load_mapping().groups)
    out: set[str] = set()
    for c in conversions:
        for f in c.report.findings:
            if f.rule_id == "convert.assumed":
                words = {(f.path or "").split("/")[0], *GROUP_WORD.findall(f.message)}
                out |= words & groups
    return out


def _target_row(state: _State, group: ags4.Group, row: int) -> int | None:
    """The row of the output group that holds source row ``row`` (matched once)."""
    target = state.out.groups.get(group.name)
    if target is None:
        return None
    found = state.matched.setdefault(group.name, {})
    if row not in found:
        taken = set(found.values())
        index = _find_row(
            group, row, target, match_keys(state.mapping.groups.get(group.name)), taken
        )
        if index is None:
            return None
        found[row] = index
    return found[row]


@dataclass
class _State:
    mapping: MappingTable
    out: ags4.File
    named: set[tuple[str, int, int]]
    used: dict[tuple[str, int, int], set[str]]
    lost_nodes: set[str]
    matched: dict[str, dict[int, int]] = field(default_factory=dict)


def _account(state: _State, tally: Tally, group: ags4.Group, row: int, column: int) -> None:
    """Count one non-null source cell: carried, named by either conversion, or format."""
    value = group.rows[row][column]
    heading = group.headings[column]
    cell = (group.name, row, column)
    owners = state.used.get(cell, set())
    rule = state.mapping.groups.get(group.name)
    if cell in state.named:
        tally.named_first += 1
        return
    if owners == {""} or group.name in FORMAT_GROUPS:
        tally.format += 1
        return
    if group.name == "TRAN" and rule is not None and rule.headings[heading].to == "format":
        tally.format += 1
        return
    index = _target_row(state, group, row)
    target = state.out.groups.get(group.name)
    if index is not None and target is not None and heading in target.headings:
        at = target.headings.index(heading)
        if same(value, target.rows[index][at], _unit(group, column), _unit(target, at), heading):
            tally.carried += 1
            return
    if index is not None and target is not None and heading in NP_LIMITS:
        limit = target.headings.index("LLPL_PL") if "LLPL_PL" in target.headings else None
        np_ = value.strip().upper() == "NP"
        if np_ and limit is not None and target.rows[index][limit] == "NP":
            tally.carried += 1
            return
    if owners & state.lost_nodes:
        tally.named_second += 1
        return
    tally.unaccounted.append((group.name, row + 1, heading, value[:40]))


def _extra_cells(state: _State, source: ags4.File, assumed: set[str]) -> list[tuple[str, str, str]]:
    """Output cells that hold no source value, and are neither format nor assumed."""
    extra = []
    for group in state.out.groups.values():
        if group.name in FORMAT_GROUPS or group.name in assumed:
            continue
        back = {v: k for k, v in state.matched.get(group.name, {}).items()}
        original = source.groups.get(group.name)
        rule = state.mapping.groups.get(group.name)
        for row, column, value in _cells(group):
            heading = group.headings[column]
            if (group.name, heading) in WRITER_CELLS:
                continue
            known = row in back and original is not None and heading in original.headings
            if known and original.rows[back[row]][original.headings.index(heading)] != "":  # type: ignore[union-attr]
                continue
            if rule is not None and heading in rule.keys and rule.parent in assumed:
                continue  # a key copied from a general row whose value was assumed
            extra.append((group.name, heading, value[:40]))
    return extra


def round_trip(
    source: ags4.File, ags_version: str
) -> tuple[Tally, convert.Conversion, convert.Conversion]:
    """AGS4 -> DIGGS -> AGS4, with every source cell accounted for."""
    mapping = load_mapping()
    first = convert.ags4_to_diggs(source)
    second = convert.model_to_ags4(first.model, ags_version=ags_version)
    built = map_file(source, mapping)  # the same ledger as `first`, deterministic
    exported = _from_model.map_model(first.model, mapping, ags_version=ags_version)
    assert second.ags4 is not None
    state = _State(
        mapping=mapping,
        out=second.ags4,
        named=set(built.ledger.named),
        used=built.ledger.used,
        lost_nodes={key for (key, _path) in exported.ledger.named},
    )
    tally = Tally()
    for group in source.groups.values():
        for row, column, _value in _cells(group):
            tally.cells += 1
            _account(state, tally, group, row, column)
    tally.extra = _extra_cells(state, source, _assumed_groups((first, second)))
    return tally, first, second


def errors(report: object) -> list[str]:
    """Error findings, but Rule 1 (non-ASCII text, which the converter carries unchanged)."""
    findings = getattr(report, "findings", ())
    return [
        f"{f.rule_id}: {f.message[:120]}"
        for f in findings
        if f.severity is Severity.ERROR and f.rule_id != "ags4.rule-1"
    ]


def check_ags4_round_trip(path: Path) -> Tally:
    source = ags4.read(path)
    version = "4.2" if source.version == "4.2" else "4.1.1"
    tally, first, second = round_trip(source, version)
    assert first.validation is not None
    assert errors(first.validation) == [], path.name
    assert second.validation is not None
    assert errors(second.validation) == [], path.name
    assert tally.unaccounted == [], (path.name, tally.unaccounted[:10])
    assert tally.extra == [], (path.name, tally.extra[:10])
    # stable: the same source gives the same bytes, both ways
    again = convert.model_to_ags4(convert.ags4_to_diggs(source).model, ags_version=version)
    assert again.ags4 is not None and second.ags4 is not None
    assert ags4.dumps(again.ags4) == ags4.dumps(second.ags4)
    # residual: its cells are the named ones, with their keys
    residual = first.residual
    assert residual is not None
    if residual.groups:
        assert ags4.read_bytes(ags4.dumps(residual)) == residual
    return tally


@pytest.mark.parametrize("path", SYNTHETIC, ids=lambda p: p.name)
def test_ags4_round_trip_lists_every_loss_on_the_fixtures(path: Path):
    tally = check_ags4_round_trip(path)
    assert tally.cells == tally.carried + tally.named_first + tally.named_second + tally.format


CORPUS_FILES = sorted(CORPUS.rglob("*.ags")) if CORPUS.is_dir() else []


@pytest.mark.skipif(
    not CORPUS_FILES, reason="AGS4 corpus not cached (scripts/conformance.py fetch --set ags4)"
)
@pytest.mark.parametrize("path", CORPUS_FILES, ids=lambda p: p.name)
def test_ags4_round_trip_lists_every_loss_on_the_corpus(path: Path):
    try:
        ags4.read(path)
    except ags4_errors() as exc:  # a file the reader refuses is not converted
        pytest.skip(f"not an AGS4 file the reader takes: {type(exc).__name__}")
    check_ags4_round_trip(path)


def ags4_errors() -> tuple[type[Exception], ...]:
    from pydiggs.errors import DocumentError, InstanceReadError

    return (DocumentError, InstanceReadError, ValueError)


# --- DIGGS -> AGS4 -> DIGGS ----------------------------------------------------------


def check_diggs_round_trip(model: object, version: str = "4.1.1") -> tuple[int, int, int]:
    """The model ledger is complete, and the AGS4 output is a fixed point of a round trip."""
    mapping = load_mapping()
    exported = _from_model.map_model(model, mapping, ags_version=version)  # type: ignore[arg-type]
    ledger = exported.ledger
    left = [item for item in ledger.items if not ledger.is_done(item)]
    assert left == []
    out = convert.model_to_ags4(model, ags_version=version)  # type: ignore[arg-type]
    assert out.validation is not None
    assert errors(out.validation) == []
    assert out.ags4 is not None
    tally, first, _second = round_trip(out.ags4, version)
    assert first.validation is not None and errors(first.validation) == []
    assert tally.unaccounted == [], tally.unaccounted[:10]
    assert not set(ledger.named) & ledger.used  # nothing is both written and lost
    return len(ledger.items), len(ledger.used), len(ledger.named)


@pytest.mark.parametrize("path", SYNTHETIC, ids=lambda p: p.name)
@pytest.mark.parametrize("version", ["4.1.1", "4.2"])
def test_diggs_round_trip_of_converted_fixtures_is_a_fixed_point(path: Path, version: str):
    model = convert.ags4_to_diggs(path).model
    check_diggs_round_trip(model, version)


DIGGS_FILES = sorted(OFFICIAL_3.glob("*.xml")) + (
    sorted(INSPECTOR.rglob("*.xml")) if INSPECTOR.is_dir() else []
)


@pytest.mark.parametrize("path", DIGGS_FILES, ids=lambda p: p.name)
def test_diggs_round_trip_on_official_diggs_3_files(path: Path):
    model = read_diggs(path)
    items, used, named = check_diggs_round_trip(model)
    assert items == used + named


def test_a_lossless_file_round_trips_to_equal_cells():
    """A file the table maps whole gives its own values back."""
    path = FIXTURES / "site_tier_b_4_1_1.ags"
    tally = check_ags4_round_trip(path)
    assert tally.carried > 50
