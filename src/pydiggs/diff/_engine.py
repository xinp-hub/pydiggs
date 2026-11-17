"""The comparison itself: match, compare each pair, keep exact counts, list a bounded part.

Counts are exact whatever ``max_changes`` says; only the listed changes are bounded, and
the work is bounded by a time budget checked when a run starts and ends and every few
dozen units of work in between (no loop does more than a few hundred without reporting
them). Everything that varies is injected or counted (``_now`` is the one clock), so the
work is testable.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

from pydiggs.diff import _tables
from pydiggs.diff._compare import (
    MAX_SHOWN,
    OWN_ID,
    REFORMATTED,
    RENAMED,
    NestedIds,
    Side,
    Tol,
    diff_maps,
    flatten,
    show,
)
from pydiggs.diff._match import COLLECTIONS, Info, Pair, Pairing, enumerate_nodes, match
from pydiggs.diff._types import Change, Diff, ValueChange
from pydiggs.findings.model import Finding
from pydiggs.model import Measurement, Model, Other

__all__ = ["MATCH_MODES", "Options", "Places", "Run", "compare_models"]

#: Where the identified elements of one file that its model does not keep sit: ``gml:id``
#: to the key of the record holding the element and the element's path below it.
Places = Mapping[str, tuple[str, str]]

MATCH_MODES: Final = ("auto", "gml-id", "natural")
#: Most ambiguity notes written; the rest are summed in one more note.
MAX_AMBIGUITY_NOTES: Final = 50
#: The clock is read once per this many units of work.
_CHECK_EVERY: Final = 64


def _now() -> float:
    return time.monotonic()


@dataclass(frozen=True, slots=True)
class Options:
    """Validated options of one comparison."""

    match: str = "auto"
    rel_tol: float = 1e-9
    abs_tol: float = 0.0
    max_changes: int = 10_000
    time_budget: float | None = None
    show_reformatted: bool = False
    show_renamed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "rel_tol": self.rel_tol,
            "abs_tol": self.abs_tol,
            "max_changes": self.max_changes,
            "time_budget": self.time_budget,
        }


class Run(NamedTuple):
    """A finished comparison and the work it took (the unit the scaling tests count)."""

    diff: Diff
    units: int


class _TimedOut(Exception):  # noqa: N818 - control flow inside this module only
    """The time budget ran out."""


class _Budget:
    __slots__ = ("_next", "deadline", "units")

    def __init__(self, deadline: float | None) -> None:
        self.deadline = deadline
        self.units = 0
        self._next = _CHECK_EVERY

    def tick(self, n: int = 1) -> None:
        self.units += n
        if self.units >= self._next:
            self._next = self.units + _CHECK_EVERY
            self.check()

    def check(self) -> None:
        """Read the clock now.

        A run does it where it starts and ends, because a comparison of a few values
        never counts up to the next reading.
        """
        if self.deadline is not None and _now() >= self.deadline:
            raise _TimedOut


def _clip(text: str | None) -> tuple[str | None, bool]:
    if text is None or len(text) <= MAX_SHOWN:
        return text, False
    return text[:MAX_SHOWN], True


class _Listing:
    """What is listed, within ``max_changes``, and what was left out."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self.changes: list[Change] = []
        self.cut = False

    def room(self, n: int) -> bool:
        if self.used + n <= self.limit:
            return True
        self.cut = True
        return False


class _Sink:
    """The differences of one matched pair: counted always, kept while the listing has room.

    Two kinds are not changes: a number written differently (``reformatted``) and the
    record's own ``gml:id`` (``renamed``). Each is counted apart and kept only on request.
    """

    def __init__(self, listing: _Listing, options: Options) -> None:
        self.listing = listing
        self.show_reformatted = options.show_reformatted
        self.show_renamed = options.show_renamed
        self.changed = 0
        self.reformatted = 0
        self.renamed = 0
        self.kept: list[tuple[str, str, Any, Any]] = []
        self.reserved = False

    def add(self, path: str, op: str, old: Any, new: Any) -> None:
        if path == OWN_ID:
            # An id says which record this is in one file, not what the record holds;
            # one that appears or goes is the same, with nothing on the other side.
            op = RENAMED
            self.renamed += 1
            if not self.show_renamed:
                return
        elif op == REFORMATTED:
            self.reformatted += 1
            if not self.show_reformatted:
                return
        else:
            self.changed += 1
        needed = 1 if self.reserved else 2
        if not self.listing.room(needed):
            return
        self.listing.used += needed
        self.reserved = True
        self.kept.append((path, op, old, new))


def _info_source(model: Model) -> dict[str, Any]:
    source = model.source
    return {
        "name": source.name,
        "sha256": source.sha256,
        "size": source.size,
        "diggs_version": source.diggs_version,
    }


def _note(rule: str, severity: str, message: str) -> Finding:
    return Finding(step="diff", rule_id=rule, severity=severity, message=message)  # type: ignore[arg-type]


def _change(op: str, info_old: Info | None, info_new: Info | None, by: str | None = None) -> Change:
    info = info_new or info_old
    assert info is not None  # noqa: S101 - a change has a node on at least one side
    name, cut_name = _clip(info.node.name)
    old_key, cut_old = _clip(info_old.key if info_old else None)
    new_key, cut_new = _clip(info_new.key if info_new else None)
    return Change(
        op=op,
        collection=info.collection,
        element=info.node.element,
        name=name,
        old_key=old_key,
        new_key=new_key,
        matched_by=by,
        truncated=cut_name or cut_old or cut_new,
    )


def _entries(pairing: Pairing) -> dict[str, list[tuple[tuple[str, int, str], str, Any]]]:
    by_collection: dict[str, list[tuple[tuple[str, int, str], str, Any]]] = {
        c: [] for c in COLLECTIONS
    }
    for pair in pairing.pairs:
        by_collection[pair.new.collection].append(((pair.new.key, 0, pair.old.key), "pair", pair))
    for info in pairing.old_only:
        by_collection[info.collection].append(((info.key, 1, ""), "removed", info))
    for info in pairing.new_only:
        by_collection[info.collection].append(((info.key, 2, ""), "added", info))
    for entries in by_collection.values():
        entries.sort(key=lambda e: e[0])
    return by_collection


def _results(node: Any) -> tuple[Any, ...]:
    return node.results if isinstance(node, Measurement | Other) else ()


class _Comparison:
    def __init__(
        self,
        old: Model,
        new: Model,
        options: Options,
        budget: _Budget,
        places: tuple[Places, Places] | None = None,
    ) -> None:
        self.old, self.new, self.options, self.budget = old, new, options, budget
        self.places = places
        self.tol = Tol(options.rel_tol, options.abs_tol)
        self.listing = _Listing(options.max_changes)
        self.counts: dict[str, dict[str, int]] = {}
        self.value_changes = 0
        self.reformatted = 0
        self.renamed = 0
        self.timed_out = False
        self.differs = False

    def count(self, collection: str, which: str) -> None:
        bucket = self.counts.setdefault(
            collection, {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
        )
        bucket[which] += 1
        if which != "unchanged":
            self.differs = True

    def run(self) -> None:
        try:
            self.budget.check()
            old_infos, new_infos = enumerate_nodes(self.old), enumerate_nodes(self.new)
            self.budget.tick(len(old_infos) + len(new_infos))
            self.pairing = match(old_infos, new_infos, self.options.match, self.budget.tick)
            sides = (
                Side(self.old, "old", self.pairing.old_pair),
                Side(self.new, "new", self.pairing.new_pair),
            )
            nested = NestedIds(
                [(pair.old.node, pair.new.node) for pair in self.pairing.pairs],
                sides,
                self.budget.tick,
            )
            for which, side in enumerate(sides):
                side.nested = nested
                side.places = None if self.places is None else self.places[which]
            for entries in _entries(self.pairing).values():
                for _, kind, payload in entries:
                    self.budget.tick()
                    if kind == "pair":
                        self.compare_pair(payload, sides)
                    else:
                        self.one_sided(kind, payload)
            self.budget.check()
        except _TimedOut:
            self.timed_out = True

    def one_sided(self, kind: str, info: Info) -> None:
        self.count(info.collection, kind)
        if self.listing.room(1):
            self.listing.used += 1
            old, new = (info, None) if kind == "removed" else (None, info)
            self.listing.changes.append(_change(kind, old, new))

    def compare_pair(self, pair: Pair, sides: tuple[Side, Side]) -> None:
        sink = _Sink(self.listing, self.options)
        tick = self.budget.tick
        old_map, new_map = flatten(pair.old.node, pair.new.node, sides, tick)
        # A nested node is where it is: the same drive set under another test is a change.
        # The holder is named by its pair, so renaming the holder is not. Two holders that
        # were both left unmatched are already listed as removed and added, and whether
        # they are one holder is the guess matching refused to make, so they say nothing.
        holders = [
            (leaves, side, info.parent.key)
            for leaves, side, info in ((old_map, sides[0], pair.old), (new_map, sides[1], pair.new))
            if info.parent is not None
        ]
        if any(key in side.pair_of for _, side, key in holders):
            for leaves, side, key in holders:
                leaves["holder"] = side.node_ref(key)
        diff_maps(old_map, new_map, self.tol, sink.add, tick)
        _tables.compare_results(
            _results(pair.old.node),
            _results(pair.new.node),
            sides=sides,
            tol=self.tol,
            emit=sink.add,
            tick=tick,
        )
        self.value_changes += sink.changed
        self.reformatted += sink.reformatted
        self.renamed += sink.renamed
        collection = pair.new.collection
        if sink.changed == 0:
            self.count(collection, "unchanged")
        else:
            self.count(collection, "changed")
        if not sink.kept:
            return
        at = pair.new.node.provenance.xpath
        values = []
        for path, op, old, new in sorted(sink.kept, key=lambda v: v[0]):
            shown_old, cut_old = show(old)
            shown_new, cut_new = show(new)
            values.append(
                ValueChange(
                    op=op,
                    path=path,
                    old=shown_old,
                    new=shown_new,
                    at=at,
                    truncated=cut_old or cut_new,
                )
            )
        # What the record is listed as: changed if any value is, else renamed if its id
        # is listed, else it only lists numbers written differently.
        ops = {op for _, op, _, _ in sink.kept}
        op = "changed" if sink.changed else RENAMED if RENAMED in ops else REFORMATTED
        listed = _change(op, pair.old, pair.new, pair.by)
        self.listing.changes.append(_with_values(listed, tuple(values)))


def _with_values(change: Change, values: tuple[ValueChange, ...]) -> Change:
    return Change(
        op=change.op,
        collection=change.collection,
        element=change.element,
        name=change.name,
        old_key=change.old_key,
        new_key=change.new_key,
        matched_by=change.matched_by,
        values=values,
        truncated=change.truncated,
    )


def _ambiguity_notes(pairing: Pairing) -> list[Finding]:
    notes = [
        _note(
            "diff.ambiguous-match",
            "info",
            f"{a.old_count} node(s) in the old file and {a.new_count} in the new file share the"
            f" key {a.label!r} in {a.collection}, so none was matched; they are listed as"
            " removed and added.",
        )
        for a in pairing.ambiguities[:MAX_AMBIGUITY_NOTES]
    ]
    rest = len(pairing.ambiguities) - MAX_AMBIGUITY_NOTES
    if rest > 0:
        notes.append(
            _note(
                "diff.ambiguous-match",
                "info",
                f"and {rest} more keys with several nodes were not matched and are not listed.",
            )
        )
    return notes


def compare_models(
    old: Model,
    new: Model,
    options: Options,
    *,
    deadline: float | None = None,
    places: tuple[Places, Places] | None = None,
) -> Run:
    """Compare two models. ``deadline`` is a reading of :func:`_now` after which work stops.

    ``places`` is what the reader knew of the two files beyond their models (see
    :data:`Places`); without it a reference to an element the model does not keep is
    compared as text.
    """
    budget = _Budget(deadline)
    comparison = _Comparison(old, new, options, budget, places)
    comparison.run()
    pairing = getattr(comparison, "pairing", None)
    limits: list[str] = []
    notes: list[Finding] = _ambiguity_notes(pairing) if pairing is not None else []
    if comparison.listing.cut:
        limits.append("max_changes")
        counts = (
            "the counts are partial too, because the time budget ran out"
            if comparison.timed_out
            else "the counts are complete"
        )
        notes.append(
            _note(
                "diff.limit",
                "warning",
                f"Stopped listing changes at max_changes={options.max_changes}; {counts}.",
            )
        )
    if comparison.timed_out:
        limits.append("time_budget")
        notes.append(
            _note(
                "diff.timeout",
                "warning",
                f"The comparison did not finish within time_budget={options.time_budget} s; the"
                " counts are partial.",
            )
        )
    for side, model in (("old", old), ("new", new)):
        if not model.complete:
            limits.append(f"{side}_read_incomplete")
            notes.append(
                _note(
                    "diff.limit",
                    "warning",
                    f"The {side} file was read only in part"
                    f" ({', '.join(model.limits_hit) or 'a limit'}), so the comparison is"
                    " incomplete.",
                )
            )
    complete = not limits
    summary: dict[str, Mapping[str, int]] = {
        name: comparison.counts[name] for name in COLLECTIONS if name in comparison.counts
    }
    diff = Diff(
        tool={"name": "pydiggs", "version": _version()},
        identical=complete and not comparison.differs and comparison.value_changes == 0,
        complete=complete,
        limits_hit=tuple(limits),
        old=_info_source(old),
        new=_info_source(new),
        options=options.as_dict(),
        summary=summary,
        value_changes=comparison.value_changes,
        reformatted=comparison.reformatted,
        renamed=comparison.renamed,
        changes=tuple(comparison.listing.changes),
        notes=tuple(notes),
    )
    return Run(diff, budget.units)


def _version() -> str:
    import pydiggs  # noqa: PLC0415  # the package imports nothing from here

    return pydiggs.__version__
