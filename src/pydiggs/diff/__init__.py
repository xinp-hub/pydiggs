"""Model-level comparison of two DIGGS files: what was added, removed or changed.

A preview in pyDIGGS 1.12: the report format and the comparison rules may change in a
minor release. The stable ``pydiggs.diff`` and diggs-diff 1.0 are planned for pyDIGGS 1.13.

:func:`compare` reads two files (or takes two :class:`~pydiggs.model.Model`) and says what
differs *in what they describe*, not in how they are written: element order, number
formatting, provenance and the ``gml:id`` of a record do not count. Nodes are matched by
``gml:id`` first, then by a natural key (a borehole's kind and name, a sample's borehole,
name and top, ...), so nodes stay paired when their ids change, and two exports of the
same data with new ids are identical. The renamed ids are counted (``Diff.renamed``) and
listed on request. The ``gml:id`` of an element inside a node (a geometry) is not
compared, and a reference to something in the same file is compared by what it points
to; one that cannot be resolved for certain is compared as text. A reference to an
element the model does not keep (a borehole's centre line) is resolved from the file, so
only when both inputs are paths or bytes: a model has neither that element nor its id,
and two models cannot show that two such elements of one record exchanged their ids. A
natural key that more than one node has is never guessed at.
Numbers compare within a tolerance, a change of unit is always a change (nothing is
converted), and result tables align their rows by the domain index (depth) when it is
unique.

Comparing two files (paths or bytes) is the full comparison: it also covers what each
file holds of a record that the model does not keep (the unit labels of a location, the
geometry of a layer, a grid, a value a column could not read), under paths that start
with ``xml.``. Comparing two models compares the two models, and can only see what a
model holds; a file and a model are compared as two models.

The result is a :class:`Diff`: a summary, the listed :class:`Change` items, and notes that
are findings of step ``diff``. :meth:`Diff.to_json` writes it as a diggs-diff document,
described by the JSON Schema :func:`diff_schema` returns. Both files are untrusted: their
size, the time spent and the number of changes listed are limited.

``import pydiggs`` does not import this module.

Examples:
    >>> from pydiggs import diff
    >>> path = "tests/fixtures/official/3.0/Aeromag.xml"
    >>> result = diff.compare(path, path)
    >>> result.identical, result.complete, len(result.changes)
    (True, True, 0)
    >>> result.to_dict()["format"], diff.DIFF_SCHEMA_VERSION
    ('diggs-diff', '1.0-beta.1')
"""

from __future__ import annotations

import json
import math
import os
from importlib import resources
from typing import Any

from pydiggs.diff import _engine
from pydiggs.diff._document import DIFF_SCHEMA_URL, DIFF_SCHEMA_VERSION, SCHEMA_RESOURCE
from pydiggs.diff._types import Change, Diff, ValueChange
from pydiggs.findings.run import DEFAULT_MAX_BYTES
from pydiggs.model import Model

__all__ = [
    "DIFF_SCHEMA_URL",
    "DIFF_SCHEMA_VERSION",
    "Change",
    "Diff",
    "ValueChange",
    "compare",
    "diff_schema",
]

Source = str | os.PathLike[str] | bytes | bytearray | memoryview | Model


def diff_schema() -> dict[str, Any]:
    """The packaged diggs-diff JSON Schema, as a fresh dict.

    Examples:
        >>> from pydiggs import diff
        >>> diff.diff_schema()["$id"] == diff.DIFF_SCHEMA_URL
        True
    """
    text = resources.files("pydiggs.diff").joinpath(SCHEMA_RESOURCE).read_text("utf-8")
    return dict(json.loads(text))


def _number(name: str, value: object, *, allow_zero: bool) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not allow_zero)
    ):
        kind = "a finite number, 0 or more" if allow_zero else "a positive finite number"
        raise ValueError(f"{name} must be {kind}, got {value!r}")
    return float(value)


def _options(
    *,
    match: str,
    rel_tol: float,
    abs_tol: float,
    max_changes: int,
    time_budget: float | None,
    max_bytes: int,
    show_reformatted: bool,
    show_renamed: bool,
) -> _engine.Options:
    if match not in _engine.MATCH_MODES:
        raise ValueError(f"match must be one of {', '.join(_engine.MATCH_MODES)}, got {match!r}")
    rel = _number("rel_tol", rel_tol, allow_zero=True)
    if rel >= 1:
        # At 1 every number equals 0 (|a - 0| <= 1 x |a|), and so any other of its sign.
        raise ValueError(f"rel_tol must be less than 1, got {rel_tol!r}")
    absolute = _number("abs_tol", abs_tol, allow_zero=True)
    if isinstance(max_changes, bool) or not isinstance(max_changes, int) or max_changes < 0:
        raise ValueError(f"max_changes must be an integer, 0 or more, got {max_changes!r}")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive integer, got {max_bytes!r}")
    if time_budget is not None:
        _number("time_budget", time_budget, allow_zero=False)
    return _engine.Options(
        match=match,
        rel_tol=rel,
        abs_tol=absolute,
        max_changes=max_changes,
        time_budget=time_budget,
        show_reformatted=bool(show_reformatted),
        show_renamed=bool(show_renamed),
    )


def _load(
    source: object, role: str, max_bytes: int, deadline: float | None
) -> tuple[Model, _engine.Seen | None]:
    """The model of ``source`` and, when it was read here, what the reader saw beyond it.

    A :class:`~pydiggs.model.Model` no longer has the file, so it comes with nothing more.
    """
    from pydiggs._reader import read_with_places  # noqa: PLC0415

    if isinstance(source, Model):
        return source, None
    remaining = None if deadline is None else max(deadline - _engine._now(), 1e-3)
    if isinstance(source, str | os.PathLike | bytes | bytearray | memoryview):
        model, places, systems, marks, unread, names = read_with_places(
            source, max_bytes=max_bytes, time_budget=remaining
        )
        return model, _engine.Seen(places, systems, marks, unread, names)
    raise TypeError(f"{role} must be a path, bytes or a pydiggs.Model, got {type(source).__name__}")


def compare(
    old: Source,
    new: Source,
    *,
    match: str = "auto",
    rel_tol: float = 1e-9,
    abs_tol: float = 0.0,
    max_changes: int = 10_000,
    time_budget: float | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    show_reformatted: bool = False,
    show_renamed: bool = False,
) -> Diff:
    """Compare two DIGGS files at model level.

    A preview in pyDIGGS 1.12: the report format and the comparison rules may change in
    a minor release.

    Give it the two files (paths or bytes) for the full comparison: what a model holds,
    and what each file holds of a record that its model does not keep. Given two
    :class:`~pydiggs.model.Model`, it compares the two models and can only see what a
    model holds, so an identical result then says that the models are equal. A file and
    a model are compared as two models.

    Args:
        old: The earlier file: a path, the bytes of a file, or a :class:`~pydiggs.model.Model`.
            A reference to an element the model does not keep (a borehole's centre line)
            is compared by what it points to only when both are paths or bytes. With a
            model on either side it is compared by the record that holds the element and
            the text of the id, which cannot show that two such elements of one record
            exchanged their ids: only the files can. A model read with
            ``results=False`` has no cells to compare: when one of its tables has
            columns, the result is ``complete=False`` (limit ``old_results_not_read``
            or ``new_results_not_read``) with a ``diff.limit`` note, and so never
            identical.
        new: The later file, in the same forms. The two may be different DIGGS versions;
            both are recorded in the result.
        match: How nodes are paired. ``"auto"``: by ``gml:id`` and element, then by natural
            key; ``"gml-id"``: the first pass only, so a node whose id changed is
            removed and added; ``"natural"``: the second pass only. A natural key that
            several nodes share is never matched.
        rel_tol: Two numbers are equal when ``|a - b| <= max(abs_tol, rel_tol * max(|a|,
            |b|))``. The default is exact up to float noise. From 0 to less than 1: at
            1 every number would equal 0.
        abs_tol: The absolute part of that bound (default 0).
        max_changes: Most entries listed (changed nodes plus their value changes). The
            counts in the summary stay exact; when the list is cut the result is
            ``complete=False`` with a ``diff.limit`` note.
        time_budget: Seconds for the whole call, reading both files included. When it runs
            out the result is ``complete=False`` with a ``diff.timeout`` note.
        max_bytes: Largest file read (default 100 MiB, as for :func:`pydiggs.read`).
        show_reformatted: List the values written differently but equal (``1.50`` and
            ``1.5``); they are always counted.
        show_renamed: List the nodes matched by natural key whose own ``gml:id`` differs,
            each with the value ``gml_id`` (old and new id); they are always counted. A
            changed id is not a difference: such a node is unchanged unless one of its
            values differs.

    Returns:
        A :class:`Diff`. A problem with the *data* is a finding in ``Diff.notes``.

    Raises:
        InstanceReadError: A file is missing, unreadable, too large or compressed.
        DocumentError: A file is not a readable DIGGS document.
        TypeError: ``old`` or ``new`` is not a path, bytes or a model.
        ValueError: An option is out of range.

    Examples:
        >>> from pydiggs import diff
        >>> a = "tests/fixtures/official/3.0/MWDExample.xml"
        >>> b = "tests/fixtures/official/3.0/TrenchWallExample.xml"
        >>> diff.compare(a, a).identical
        True
        >>> result = diff.compare(a, b)
        >>> result.identical, result.summary["features"]["removed"] > 0
        (False, True)
    """
    options = _options(
        match=match,
        rel_tol=rel_tol,
        abs_tol=abs_tol,
        max_changes=max_changes,
        time_budget=time_budget,
        max_bytes=max_bytes,
        show_reformatted=show_reformatted,
        show_renamed=show_renamed,
    )
    deadline = None if time_budget is None else _engine._now() + float(time_budget)
    for role, source in (("old", old), ("new", new)):
        if not isinstance(source, Model | str | os.PathLike | bytes | bytearray | memoryview):
            raise TypeError(
                f"{role} must be a path, bytes or a pydiggs.Model, got {type(source).__name__}"
            )
    old_model, old_seen = _load(old, "old", max_bytes, deadline)
    new_model, new_seen = _load(new, "new", max_bytes, deadline)
    # A place means something only against the other file's: with a model on one side,
    # every such reference is compared as text, as it is between two models.
    seen = None if old_seen is None or new_seen is None else (old_seen, new_seen)
    return _engine.compare_models(old_model, new_model, options, deadline=deadline, seen=seen).diff
