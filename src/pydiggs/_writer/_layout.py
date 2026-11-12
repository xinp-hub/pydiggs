"""The writer's layout data and ``gml:id`` assignment. Pure: no XML, no I/O after load.

``layout-<version>.toml`` says, for every object element of one DIGGS version, which
namespace it is in, whether it takes a ``gml:id``, and the order the schema wants its
children in. It is generated from the bundled XSDs by ``scripts/writer_layout.py``.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Final

from pydiggs.errors import ResourceError

__all__ = ["DIGGS_VERSIONS", "ElementLayout", "Ids", "Layout", "load_layout"]

#: DIGGS versions the writer can target.
DIGGS_VERSIONS: Final = ("3.0.0",)
_FORMAT: Final = 1
#: An XML name without a colon (close enough to NCName: what ``xs:ID`` accepts).
_NCNAME: Final = re.compile(r"[^\W\d][\w.\-]*")


@dataclass(frozen=True, slots=True)
class ElementLayout:
    """One object element: its tag, how it takes ``gml:id``, and its children in order.

    ``children`` are the children's tags (Clark notation) in schema order; ``by_local``
    finds a child's tag by its local name (the first one, where two namespaces share a
    name). ``interval``, ``location`` and ``time`` are the tags of the first child that
    may hold a ``LinearExtent``, a ``PointLocation`` and a ``TimeInterval``.
    """

    name: str
    tag: str
    id: str | None
    children: tuple[str, ...]
    by_local: Mapping[str, str]
    interval: str | None = None
    location: str | None = None
    time: str | None = None


@dataclass(frozen=True, slots=True)
class Layout:
    """The layout of one DIGGS version."""

    diggs_version: str
    namespaces: Mapping[str, str]
    elements: Mapping[str, ElementLayout]

    def element(self, name: str) -> ElementLayout:
        """The layout of object element ``name``.

        Raises:
            ValueError: DIGGS ``diggs_version`` has no such object element.
        """
        found = self.elements.get(name)
        if found is None:
            raise ValueError(f"DIGGS {self.diggs_version} has no object element {name!r}")
        return found

    def tag(self, written: str) -> str:
        """``prefix:name`` (or a bare DIGGS name) in Clark notation."""
        prefix, _, local = written.rpartition(":")
        return f"{{{self.namespaces[prefix or 'diggs']}}}{local}"


def _build(data: Mapping[str, Any], diggs_version: str) -> Layout:
    if data["format"] != _FORMAT or data["diggs_version"] != diggs_version:
        raise ValueError("unexpected format or version")
    namespaces = {str(prefix): str(uri) for prefix, uri in data["namespaces"].items()}

    def clark(written: str) -> str:
        prefix, _, local = written.rpartition(":")
        return f"{{{namespaces[prefix or 'diggs']}}}{local}"

    elements: dict[str, ElementLayout] = {}
    for name, entry in data["elements"].items():
        children = tuple(clark(child) for child in entry["children"])
        by_local: dict[str, str] = {}
        for child in children:
            by_local.setdefault(child.rpartition("}")[2], child)
        held = {key: clark(entry[key]) for key in ("interval", "location", "time") if key in entry}
        elements[name] = ElementLayout(
            name=name,
            tag=f"{{{namespaces[entry['ns']]}}}{name}",
            id=entry.get("id"),
            children=children,
            by_local=MappingProxyType(by_local),
            **held,
        )
    return Layout(
        diggs_version=diggs_version,
        namespaces=MappingProxyType(namespaces),
        elements=MappingProxyType(elements),
    )


@cache
def load_layout(diggs_version: str) -> Layout:
    """The bundled layout of ``diggs_version`` (read once, then shared).

    Raises:
        ValueError: The writer has no layout for ``diggs_version``.
        ResourceError: The bundled layout file is missing or broken.

    Examples:
        >>> layout = load_layout("3.0.0")
        >>> layout.element("Borehole").id
        'required'
        >>> layout.element("Borehole").by_local["totalMeasuredDepth"]
        '{http://diggsml.org/schemas/3}totalMeasuredDepth'
    """
    if diggs_version not in DIGGS_VERSIONS:
        raise ValueError(
            f"the writer targets DIGGS {', '.join(DIGGS_VERSIONS)}, not {diggs_version!r}"
        )
    resource = f"layout-{diggs_version}.toml"
    try:
        text = resources.files("pydiggs._writer").joinpath(resource).read_text("utf-8")
        return _build(tomllib.loads(text), diggs_version)
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ResourceError(f"bundled writer layout {resource} is unreadable: {exc}") from exc


class Ids:
    """The ``gml:id`` values of one document: the model's own, and the ones made up.

    A made-up id is its ``base`` when that is free, else ``base-2``, ``base-3`` …, so the
    same model always gets the same ids. Work is linear: each base keeps its own counter.

    Examples:
        >>> ids = Ids()
        >>> ids.claim("bh1", "feature")
        >>> ids.new("bh1.referencePoint"), ids.new("bh1.referencePoint")
        ('bh1.referencePoint', 'bh1.referencePoint-2')
    """

    def __init__(self, reserved: Iterable[str] = ()) -> None:
        self._taken: set[str] = set(reserved)
        self._next: dict[str, int] = {}

    def claim(self, gml_id: str, where: str) -> None:
        """Take ``gml_id`` as written in the model.

        Raises:
            ValueError: It is not a valid XML id, or another element has it.
        """
        if not _NCNAME.fullmatch(gml_id):
            raise ValueError(f"{where}: gml_id {gml_id!r} is not a valid XML id")
        if gml_id in self._taken:
            raise ValueError(f"{where}: gml_id {gml_id!r} is used more than once")
        self._taken.add(gml_id)

    def new(self, base: str) -> str:
        """A free id made from ``base``."""
        candidate = base
        count = self._next.get(base, 1)
        while candidate in self._taken:
            count += 1
            candidate = f"{base}-{count}"
        self._next[base] = count
        self._taken.add(candidate)
        return candidate
