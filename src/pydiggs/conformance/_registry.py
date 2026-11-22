"""One place that says, for each scenario, how its data, key, reading and file are made.

SPT and CPT share the generic answer key and reader of :mod:`._facts`. The laboratory
scenario brings its own key; the pile scenario its own key, reader and file builder.
Everything else in the suite asks this module.
"""

from __future__ import annotations

from pydiggs.conformance import _facts, _lab, _piles, _scenario
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset
from pydiggs.model import Model

__all__ = ["build", "facts", "observe", "questions", "reference_xml"]


def build(scenario: str, stream: str, token: str) -> Dataset:
    """The dataset of ``scenario`` for ``stream``; the same arguments give the same data."""
    if scenario == "lab":
        return _lab.build(Rng(stream), token)
    if scenario == _piles.SCENARIO:
        return _piles.build(stream, token)
    return _scenario.build(scenario, stream, token)


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of ``dataset``, from its tables alone."""
    if dataset.scenario == "lab":
        return _lab.facts(dataset)
    if dataset.scenario == _piles.SCENARIO:
        return _piles.facts(dataset)
    return _facts.facts(dataset)


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for ``dataset``."""
    if dataset.scenario == "lab":
        return _lab.questions(dataset, stream)
    if dataset.scenario == _piles.SCENARIO:
        return _piles.questions(dataset, stream)
    return _facts.questions(dataset, stream)


def observe(scenario: str, model: Model) -> Observed:
    """What the answer key of ``scenario`` is looked up in."""
    if scenario == _piles.SCENARIO:
        return _piles.observe(model)
    return _facts.observe(model)


def reference_xml(dataset: Dataset) -> bytes:
    """A known-good DIGGS 3.0.0 export of ``dataset``."""
    if dataset.scenario == _piles.SCENARIO:
        return _piles.reference_xml(dataset)
    return _scenario.reference_xml(dataset)
