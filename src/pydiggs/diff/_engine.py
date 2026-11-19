"""The comparison itself: match, compare each pair, keep exact counts, list a bounded part.

Counts are exact whatever ``max_changes`` says; only the listed changes are bounded, and
the work is bounded by a time budget checked when a run starts and ends and every few
dozen units of work in between (no loop does more than a few hundred without reporting
them). Everything that varies is injected or counted (``_now`` is the one clock), so the
work is testable.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

from pydiggs.diff import _tables
from pydiggs.diff._compare import (
    CHANGED,
    MAX_SHOWN,
    OWN_ID,
    REFORMATTED,
    RENAMED,
    ROOT,
    UNREAD,
    Declared,
    NestedIds,
    Ref,
    Seen,
    Side,
    Tol,
    bound,
    diff_maps,
    flatten,
    href_relation,
    put_extra,
    show,
)
from pydiggs.diff._match import COLLECTIONS, Info, Pair, Pairing, enumerate_nodes, match
from pydiggs.diff._types import Change, Diff, ValueChange
from pydiggs.findings.model import Finding
from pydiggs.model import ExtraNode, Measurement, Model, Other

__all__ = ["MATCH_ALIASES", "MATCH_MODES", "Options", "Run", "Seen", "compare_models"]

MATCH_MODES: Final = ("auto", "gml-id", "natural")
#: Other spellings ``match`` accepts, each for one of :data:`MATCH_MODES`.
MATCH_ALIASES: Final = {"gml_id": "gml-id"}
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


class _Once:
    """Each edit listed once: what a typed field or a link reports is not listed again.

    The file holds more of an element than the model does, and that is compared too
    (paths ``xml.``); ``extra`` keeps elements whole. Two such values repeat one a
    typed field reports: the text of a declared type whose kind the column reports
    changed (``typeData``: ``boolean`` to ``string``), and a link the record's
    ``links`` report changed (``xlink:href`` on an element that holds more). Each is
    held back until the record is compared, and dropped when the typed change it
    repeats was reported; one reported change takes away one repeat. What is not
    matched so is listed: when unsure, report. Every other value goes to ``sink``.
    """

    __slots__ = ("held", "keys", "kinds", "sides", "sink")

    def __init__(self, sink: _Sink, keys: tuple[str, str], sides: tuple[Side, Side]) -> None:
        self.sink = sink
        self.keys = keys
        self.sides = sides
        #: How many columns changed from one kind of value to another, by the two kinds.
        self.kinds: Counter[tuple[Any, Any]] = Counter()
        self.held: list[tuple[str, str, Any, Any, str | None]] = []

    def add(self, path: str, op: str, old: Any, new: Any) -> None:
        if (
            op == CHANGED
            and isinstance(old, Declared)
            and isinstance(new, Declared)
            and old.kind != new.kind
        ):
            self.held.append((path, op, old, new, None))
            return
        relation = href_relation(path)
        if relation is not None:
            self.held.append((path, op, old, new, relation))
            return
        if op == CHANGED and path.startswith("results[") and path.endswith(".type"):
            self.kinds[(old, new)] += 1
        self.sink.add(path, op, old, new)

    def flush(self) -> None:
        moved: dict[str, tuple[Counter[str], Counter[str]]] = {}
        for path, op, old, new, relation in self.held:
            if relation is None:
                kinds = (old.kind, new.kind)
                if self.kinds[kinds] > 0:
                    self.kinds[kinds] -= 1
                    continue
            else:
                if relation not in moved:
                    moved[relation] = self._moved(relation)
                gone, came = moved[relation]
                was, now = _href_text(old), _href_text(new)
                if (was is None or gone[was] > 0) and (now is None or came[now] > 0):
                    if was is not None:
                        gone[was] -= 1
                    if now is not None:
                        came[now] -= 1
                    continue
            self.sink.add(path, op, old, new)

    def _moved(self, relation: str) -> tuple[Counter[str], Counter[str]]:
        """The links of ``relation`` only the old, only the new record has, by ``href``.

        Links are compared by what they point to; those two lists are what ``links``
        reports as changed.
        """
        found = []
        for side, key in zip(self.sides, self.keys, strict=True):
            found.append(
                [
                    (side.link_ref(link).token, link.href.strip())
                    for link in side.links_of(key).get(relation, ())
                ]
            )
        olds, news = found
        tokens = (Counter(token for token, _ in olds), Counter(token for token, _ in news))
        only: list[Counter[str]] = []
        for links, other in ((olds, Counter(tokens[1])), (news, Counter(tokens[0]))):
            alone: Counter[str] = Counter()
            for token, href in links:
                if other[token] > 0:
                    other[token] -= 1
                else:
                    alone[href] += 1
            only.append(alone)
        return only[0], only[1]


def _href_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value.shown if isinstance(value, Ref) else value).strip()


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
        seen: tuple[Seen, Seen] | None = None,
    ) -> None:
        self.old, self.new, self.options, self.budget = old, new, options, budget
        self.seen = seen
        self.tol = Tol(options.rel_tol, options.abs_tol)
        self.listing = _Listing(options.max_changes)
        self.counts: dict[str, dict[str, int]] = {}
        self.value_changes = 0
        self.reformatted = 0
        self.renamed = 0
        self.timed_out = False
        self.differs = False
        self.roots: tuple[tuple[ExtraNode, ...], tuple[ExtraNode, ...]] | None = None
        self.root_with: Pair | None = None
        #: Whether a table of the old, of the new side has columns whose values were not
        #: read (a model read with ``results=False``): its cells were not compared.
        self.unread = [False, False]

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
                side.seen = None if self.seen is None else self.seen[which]
            documents = [p for p in self.pairing.pairs if p.new.collection == "document"]
            self.roots = self._roots()
            # What a file holds outside every record belongs to the document: compared
            # with its record, or on its own when the two files have no pair of those.
            self.root_with = documents[0] if len(documents) == 1 else None
            if self.roots is not None and self.root_with is None:
                self.compare_root(self.roots, sides)
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

    def _roots(self) -> tuple[tuple[ExtraNode, ...], tuple[ExtraNode, ...]] | None:
        """What each file holds outside every record, when both files were read here.

        None when neither holds anything there, or the reader's work limit cut what
        either lacks short (the comparison is then incomplete, and lists none of it).
        """
        if self.seen is None or any("" in seen.unread for seen in self.seen):
            return None
        old, new = (seen.unread.get(ROOT, ()) for seen in self.seen)
        if not (old or new):
            return None
        # A file with nothing outside its records has the same root element, empty: give
        # it that shell, so only what was added or taken away is listed, not the root.
        if not old:
            old = _shells(new)
        elif not new:
            new = _shells(old)
        return old, new

    def compare_root(
        self, roots: tuple[tuple[ExtraNode, ...], tuple[ExtraNode, ...]], sides: tuple[Side, Side]
    ) -> None:
        """Compare what the two files hold outside every record, with no document pair.

        Listed as the root element, in collection ``document``, and counted there as a
        changed document when it differs: it is the document's, and nothing else holds it.
        """
        sink = _Sink(self.listing, self.options)
        tick = self.budget.tick
        outs: tuple[dict[str, Any], dict[str, Any]] = ({}, {})
        put_extra(outs, UNREAD, roots, tick, sides, words=True)
        diff_maps(outs[0], outs[1], self.tol, sink.add, tick)
        self.value_changes += sink.changed
        self.reformatted += sink.reformatted
        if sink.changed:
            self.count("document", "changed")
        if not sink.kept:
            return
        qname = next(node.qname for nodes in roots for node in nodes)
        at = bound(f"/{qname}[1]")
        key, _ = _clip(f"@{at}")
        listed = Change(
            op=_listed_op(sink),
            collection="document",
            element=qname.rpartition(":")[2],
            name=None,
            old_key=key,
            new_key=key,
            matched_by="natural",
            values=_listed_values(sink, at),
        )
        self.listing.changes.append(listed)

    def one_sided(self, kind: str, info: Info) -> None:
        self.count(info.collection, kind)
        if self.listing.room(1):
            self.listing.used += 1
            old, new = (info, None) if kind == "removed" else (None, info)
            self.listing.changes.append(_change(kind, old, new))

    def compare_pair(self, pair: Pair, sides: tuple[Side, Side]) -> None:
        sink = _Sink(self.listing, self.options)
        tick = self.budget.tick
        roots = self.roots if pair is self.root_with else None
        old_map, new_map = flatten(
            pair.old.node, pair.new.node, sides, tick, also=roots or ((), ())
        )
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
        once = _Once(sink, (pair.old.key, pair.new.key), sides)
        diff_maps(old_map, new_map, self.tol, once.add, tick)
        unread = _tables.compare_results(
            _results(pair.old.node),
            _results(pair.new.node),
            sides=sides,
            tol=self.tol,
            emit=once.add,
            tick=tick,
        )
        once.flush()
        self.unread = [was or now for was, now in zip(self.unread, unread, strict=True)]
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
        at = bound(pair.new.node.provenance.xpath)
        listed = _change(_listed_op(sink), pair.old, pair.new, pair.by)
        self.listing.changes.append(_with_values(listed, _listed_values(sink, at)))


def _listed_values(sink: _Sink, at: str) -> tuple[ValueChange, ...]:
    """The values ``sink`` kept, by path, as listed for a record at XPath ``at``."""
    values = []
    for path, op, old, new in sorted(sink.kept, key=lambda v: v[0]):
        shown_old, cut_old = show(old)
        shown_new, cut_new = show(new)
        if shown_old == shown_new:
            shown_new = _retargeted(old, new, shown_new)
        values.append(
            ValueChange(
                op=op,
                path=bound(path),
                old=shown_old,
                new=shown_new,
                at=at,
                truncated=cut_old or cut_new,
            )
        )
    return tuple(values)


def _listed_op(sink: _Sink) -> str:
    """What a record is listed as: changed, renamed or reformatted.

    Changed if any value is, else renamed if its id is listed, else it only lists values
    written differently.
    """
    ops = {op for _, op, _, _ in sink.kept}
    return "changed" if sink.changed else RENAMED if RENAMED in ops else REFORMATTED


#: Said of a reference that reads the same in both files and names another thing.
ANOTHER_TARGET: Final = " (another target)"


def _marked(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return text[: MAX_SHOWN - len(ANOTHER_TARGET)] + ANOTHER_TARGET


def _retargeted(old: Any, new: Any, shown: Any) -> Any:
    """``shown`` (the new value) saying so when it is a reference that names another thing.

    A reference is compared by what it points to and shown as it is written, so one that
    kept its text and now names another element (two elements exchanged their ids) would
    read ``#a -> #a``. The new side then says it names another target.
    """
    if isinstance(old, Ref) and isinstance(new, Ref):
        return _marked(shown)
    if isinstance(old, tuple) and isinstance(new, tuple) and isinstance(shown, list):
        return [
            _marked(text) if isinstance(b, Ref) and a != b else text
            for text, a, b in zip(shown, old, new, strict=False)
        ]
    return shown


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
    seen: tuple[Seen, Seen] | None = None,
) -> Run:
    """Compare two models. ``deadline`` is a reading of :func:`_now` after which work stops.

    ``seen`` is what the reader knew of the two files beyond their models (see
    :class:`~pydiggs.diff._compare.Seen`); without it a reference to an element the
    model does not keep is compared as text.
    """
    budget = _Budget(deadline)
    comparison = _Comparison(old, new, options, budget, seen)
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
    for which, (side, model) in enumerate((("old", old), ("new", new))):
        # Reading for a comparison also collects what the model does not hold, within
        # the same work limit: a file made to exceed it was not compared in full.
        cut = seen is not None and "" in seen[which].unread
        if not model.complete or cut:
            why = ", ".join(model.limits_hit) or (
                "more content outside the model than a file of its size may have"
                if model.complete
                else "a limit"
            )
            limits.append(f"{side}_read_incomplete")
            notes.append(
                _note(
                    "diff.limit",
                    "warning",
                    f"The {side} file was read only in part ({why}), so the comparison is"
                    " incomplete.",
                )
            )
    for side, unread in zip(("old", "new"), comparison.unread, strict=True):
        if unread:
            limits.append(f"{side}_results_not_read")
            notes.append(
                _note(
                    "diff.limit",
                    "warning",
                    f"The {side} model was read without the values of its result tables"
                    " (results=False), so the cells of those tables were not compared and"
                    " the comparison is incomplete.",
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


def _shells(nodes: tuple[ExtraNode, ...]) -> tuple[ExtraNode, ...]:
    """``nodes`` with nothing in them: the same elements, with no attributes, text or children.

    The empty root of the other file. Its namespace is taken as this one's; a file in
    another DIGGS namespace differs in its version, which the comparison already lists.
    """
    return tuple(ExtraNode(qname=node.qname, ns=node.ns) for node in nodes)
