"""One place that says, for each scenario, how its data, key, reading and file are made.

SPT and CPT share the generic answer key and reader of :mod:`._facts`. The laboratory
scenario brings its own key; the pile scenario its own key, reader and file builder; so
do the MWD, dissipation, in-situ strength and monitoring scenarios (:data:`_OWN`).
Everything else in the suite asks this module.
"""

from __future__ import annotations

from typing import Final, Protocol

from pydiggs.conformance import (
    _dissipation,
    _facts,
    _insitu,
    _lab,
    _monitoring,
    _mwd,
    _piles,
    _requirements,
    _scenario,
)
from pydiggs.conformance._catalogue import Family
from pydiggs.conformance._facts import Fact, Observed, Question
from pydiggs.conformance._requirements import Profile
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset
from pydiggs.model import Model

__all__ = ["acceptance", "build", "facts", "observe", "questions", "reference_xml"]


class _Scenario(Protocol):
    """A scenario module that brings its own dataset, key, questions, reader and file."""

    def build(self, seed_stream: str, token: str) -> Dataset: ...
    def facts(self, dataset: Dataset) -> tuple[Fact, ...]: ...
    def questions(self, dataset: Dataset, stream: str) -> tuple[Question, ...]: ...
    def observe(self, model: Model) -> Observed: ...
    def reference_xml(self, dataset: Dataset) -> bytes: ...


#: Scenario name -> the module that makes everything of it.
_OWN: Final[dict[str, _Scenario]] = {
    _mwd.SCENARIO: _mwd,
    _dissipation.SCENARIO: _dissipation,
    _insitu.SCENARIO: _insitu,
    _monitoring.SCENARIO: _monitoring,
}


def build(scenario: str, stream: str, token: str) -> Dataset:
    """The dataset of ``scenario`` for ``stream``; the same arguments give the same data."""
    if scenario == "lab":
        return _lab.build(Rng(stream), token)
    if scenario == _piles.SCENARIO:
        return _piles.build(stream, token)
    if scenario in _OWN:
        return _OWN[scenario].build(stream, token)
    return _scenario.build(scenario, stream, token)


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key of ``dataset``, from its tables alone."""
    if dataset.scenario == "lab":
        return _lab.facts(dataset)
    if dataset.scenario == _piles.SCENARIO:
        return _piles.facts(dataset)
    if dataset.scenario in _OWN:
        return _OWN[dataset.scenario].facts(dataset)
    return _facts.facts(dataset)


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The questions of the import challenge for ``dataset``."""
    if dataset.scenario == "lab":
        return _lab.questions(dataset, stream)
    if dataset.scenario == _piles.SCENARIO:
        return _piles.questions(dataset, stream)
    if dataset.scenario in _OWN:
        return _OWN[dataset.scenario].questions(dataset, stream)
    return _facts.questions(dataset, stream)


def observe(scenario: str, model: Model, *, nulls: bool = True) -> Observed:
    """What the answer key of ``scenario`` is looked up in.

    ``nulls`` says whether a cell that holds its property's declared ``nullValue`` is
    empty. It is, since suite 1.0-beta.3. Suite 1.0-beta.2 read it as a value in the
    SPT, CPT, laboratory and pile scenarios, and a challenge of that suite is still
    graded that way; the scenarios added after it have always read it as empty.
    """
    if scenario == _piles.SCENARIO:
        return _piles.observe(model, nulls=nulls)
    if scenario in _OWN:
        return _OWN[scenario].observe(model)
    return _facts.observe(model, nulls=nulls)


def reference_xml(dataset: Dataset) -> bytes:
    """A known-good DIGGS 3.0.0 export of ``dataset``."""
    if dataset.scenario == _piles.SCENARIO:
        return _piles.reference_xml(dataset)
    if dataset.scenario in _OWN:
        return _OWN[dataset.scenario].reference_xml(dataset)
    return _scenario.reference_xml(dataset)


def acceptance(family: Family) -> Profile:
    """What a delivered file of ``family`` must hold (``pydiggs conformance accept``).

    A family whose catalogue elements are test procedures gets the generic test profile;
    a family with another shape (piles, drilling logs, monitoring series) names its own
    profile here.
    """
    if family.scenario == _piles.SCENARIO:
        return _requirements.pile_profile(family)
    if family.scenario == _mwd.SCENARIO:
        return _requirements.mwd_profile(family)
    if family.scenario == _monitoring.SCENARIO:
        return _requirements.monitoring_profile(family)
    return _requirements.test_profile(family)
