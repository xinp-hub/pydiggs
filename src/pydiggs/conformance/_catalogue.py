"""The class catalogue: what the suite can test, and what it cannot test yet."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from pydiggs.errors import ResourceError

__all__ = ["DIRECTIONS", "ClassInfo", "Family", "class_info", "classes", "families"]

#: The two directions a product is tested in.
DIRECTIONS = ("export", "import")


@dataclass(frozen=True, slots=True)
class Family:
    """One part of DIGGS: the elements it covers and whether it has challenges.

    ``scenario`` names the scenario behind the family's challenges; None means the suite
    cannot test this part of DIGGS yet. ``embedded`` marks the core family, which has no
    class of its own because it is graded as a stage of every challenge.
    """

    id: str
    group: str
    title: str
    elements: tuple[str, ...]
    scenario: str | None = None
    note: str | None = None
    embedded: bool = False

    @property
    def testable(self) -> bool:
        """Whether the suite has a challenge for this family."""
        return self.scenario is not None


@dataclass(frozen=True, slots=True)
class ClassInfo:
    """One conformance class: a family in one direction, at one level."""

    id: str
    family: str
    direction: str
    title: str
    scenario: str
    level: str
    diggs_version: str

    def to_dict(self) -> dict[str, str]:
        """The class as the evidence document records it."""
        return {
            "id": self.id,
            "family": self.family,
            "direction": self.direction,
            "title": self.title,
            "level": self.level,
            "diggs_version": self.diggs_version,
        }


@cache
def _load() -> tuple[str, tuple[Family, ...]]:
    try:
        text = resources.files("pydiggs.conformance").joinpath("catalogue.toml").read_text("utf-8")
        data = tomllib.loads(text)
        found = tuple(
            Family(
                id=item["id"],
                group=item["group"],
                title=item["title"],
                elements=tuple(item["elements"]),
                scenario=item.get("scenario"),
                note=item.get("note"),
                embedded=bool(item.get("embedded", False)),
            )
            for item in data["family"]
        )
        return str(data["diggs_version"]), found
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ResourceError(f"the bundled conformance catalogue is broken: {exc}") from exc


def families() -> tuple[Family, ...]:
    """Every family of the catalogue, testable or not, in catalogue order.

    Examples:
        >>> from pydiggs import conformance
        >>> testable = [f.id for f in conformance.families() if f.testable]
        >>> testable[:5]
        ['spt', 'cpt', 'dissipation', 'in-situ-strength', 'pressuremeter']
        >>> testable[5:]
        ['mwd', 'lab-index', 'deep-foundations', 'monitoring']
        >>> next(f for f in conformance.families() if f.id == "geophysics").testable
        False
    """
    return _load()[1]


def classes() -> tuple[ClassInfo, ...]:
    """Every class that has a challenge: each testable family, export and import.

    Examples:
        >>> from pydiggs import conformance
        >>> [c.id for c in conformance.classes()][:8:2]
        ['spt-export', 'cpt-export', 'dissipation-export', 'in-situ-strength-export']
        >>> [c.id for c in conformance.classes()][8:12:2]
        ['pressuremeter-export', 'mwd-export']
        >>> sorted({c.direction for c in conformance.classes()})
        ['export', 'import']
    """
    diggs_version, found = _load()
    return tuple(
        ClassInfo(
            id=f"{family.id}-{direction}",
            family=family.id,
            direction=direction,
            title=f"{family.title}: {direction}",
            scenario=family.scenario,
            level="core",
            diggs_version=diggs_version,
        )
        for family in found
        if family.scenario is not None
        for direction in DIRECTIONS
    )


def class_info(class_id: str) -> ClassInfo:
    """The class ``class_id``.

    Raises:
        ValueError: There is no such class; the message lists the ones there are.
    """
    for item in classes():
        if item.id == class_id:
            return item
    known = ", ".join(c.id for c in classes())
    raise ValueError(f"no conformance class {class_id!r}; the classes are: {known}")
