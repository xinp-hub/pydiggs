"""The deprecation check: uses of deprecated or removed DIGGS schema items.

A port of DIGGSml/validation ``modules/deprecation-validation.xsl`` to the bundled
registry (``deprecation/DeprecationRegistry.xml``, from DIGGSml/schemas): each entry
with an ``instanceXPath`` is evaluated against the instance with the registry's
namespace bindings, and every match is reported, as a warning for ``deprecated`` and an
error for ``removed``, with the official message text.

Unlike the official module, an entry whose XPath fails to evaluate is not skipped:
:func:`matches` raises, so validate() reports the step as incomplete.

One deliberate difference from the official module: an entry applies only to
instances whose DIGGS version is at or after the entry's ``since`` (deprecated) or
``removedIn`` (removed) release, so entries introduced in 3.1.0 never fire on 3.0.0,
2.6 or 2.5.a files.

Used by :func:`pydiggs.validate` only; the legacy ``validator`` has no such check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Final

from lxml import etree

from pydiggs import _xml
from pydiggs.errors import ResourceError

__all__ = ["DeprecationEntry", "Match", "applies", "load_registry", "matches", "version_key"]

_DR: Final = "{http://diggsml.org/schemas/3/deprecation}"
_KIND_LABELS: Final = {
    "element": "Element",
    "branchRef": "Element",
    "type": "Type",
    "attribute": "Attribute",
    "enumValue": "Value",
}


@dataclass(frozen=True, slots=True)
class DeprecationEntry:
    """One registry entry that names a place in an instance."""

    key: str
    status: str
    kind: str
    name: str
    host: str
    since: str
    removed_in: str
    replaced_by: str
    note: str
    instance_xpath: str
    exact: bool

    @property
    def applies_from(self) -> str:
        """The first DIGGS release the entry concerns ("" when the registry gives none)."""
        return self.removed_in or self.since if self.status == "removed" else self.since

    def text(self) -> str:
        """The official message text (``diggs:deprecationText``)."""
        label = _KIND_LABELS.get(self.kind, "Content branch")
        what = f"{label} {self.name}" + (f" in {self.host}" if self.host else "")
        if self.replaced_by and self.note:
            instead = f"{self.replaced_by} ({self.note})"
        else:
            instead = self.replaced_by or self.note
        if self.status == "removed":
            tail = f"; use {instead}." if self.replaced_by else f". {self.note}"
            return f"{what} was removed in {self.removed_in}{tail}"
        exactness = (
            ""
            if self.exact
            else " This match is by name only and may be a valid use of the same name;"
            " check its context."
        )
        use = f"; use {instead}." if instead else "."
        return f"{what} is deprecated since {self.since}{use}{exactness}"


@dataclass(frozen=True, slots=True)
class Match:
    """A registry entry matched at an element (``attribute`` set for an attribute match)."""

    entry: DeprecationEntry
    element: etree._Element
    attribute: str | None


def version_key(version: str) -> tuple[int, ...]:
    """Order DIGGS versions: ``2.5.a`` < ``2.6`` < ``3.0.0`` < ``3.1.0``.

    Example:
        >>> sorted(["3.1.0", "2.6", "3.0.0", "2.5.a"], key=version_key)
        ['2.5.a', '2.6', '3.0.0', '3.1.0']
        >>> version_key("3") == version_key("3.0.0")
        True
    """
    parts = [int(p) if p.isdigit() else 0 for p in re.split(r"[.]", version.strip())]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def applies(entry: DeprecationEntry, diggs_version: str) -> bool:
    """True when ``entry`` concerns an instance of ``diggs_version``.

    Example:
        >>> e = DeprecationEntry(
        ...     "k", "deprecated", "element", "diggs:x", "", "3.1.0", "", "", "", "//diggs:x", True
        ... )
        >>> applies(e, "3.0.0"), applies(e, "3.1.0")
        (False, True)
    """
    return not entry.applies_from or version_key(entry.applies_from) <= version_key(diggs_version)


def _text(el: etree._Element | None) -> str:
    return (el.text or "") if el is not None else ""


@cache
def load_registry() -> tuple[dict[str, str], tuple[DeprecationEntry, ...]]:
    """Parse the bundled registry once: (namespace bindings, entries with an XPath).

    Raises:
        ResourceError: if the bundled registry is missing or malformed.
    """
    resource = resources.files("pydiggs").joinpath("deprecation/DeprecationRegistry.xml")
    try:
        with resources.as_file(resource) as path:
            root = _xml.parse(path).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ResourceError(f"bundled deprecation registry is unreadable: {exc}") from exc
    if root.tag != f"{_DR}DeprecationRegistry":
        raise ResourceError("bundled deprecation registry has an unexpected root element")
    namespaces = {
        str(ns.get("prefix")): str(ns.get("uri"))
        for ns in root.iter(f"{_DR}namespace")
        if ns.get("prefix") and ns.get("uri")
    }
    entries = []
    for el in root.iter(f"{_DR}entry"):
        xpath_el = el.find(f"{_DR}instanceXPath")
        xpath = " ".join(_text(xpath_el).split())
        if not xpath:
            continue  # types: an instance never names them
        entries.append(
            DeprecationEntry(
                key=el.get("key") or "",
                status=el.get("status") or "deprecated",
                kind=el.get("kind") or "",
                name=el.get("name") or "",
                host=el.get("host") or "",
                since=el.get("since") or "",
                removed_in=el.get("removedIn") or "",
                replaced_by=el.get("replacedBy") or "",
                note=_text(el.find(f"{_DR}note")),
                instance_xpath=xpath,
                exact=(xpath_el.get("exact") if xpath_el is not None else None) != "false",
            )
        )
    return namespaces, tuple(entries)


def matches(
    doc: etree._ElementTree, diggs_version: str, limit: int | None = None
) -> tuple[list[Match], dict[str, int]]:
    """Uses of deprecated or removed items that apply to ``diggs_version``.

    Returns the matches (at most ``limit`` of them) and, per entry status, how many
    more there were. An XPath that cannot be evaluated (a registry entry lxml rejects,
    or a libxml2 limit) raises :class:`lxml.etree.XPathError`: the check is then not
    complete, and the caller must say so rather than pass the file.
    """
    namespaces, entries = load_registry()
    found: list[Match] = []
    more: dict[str, int] = {}
    for entry in entries:
        if not applies(entry, diggs_version):
            continue
        result = doc.xpath(entry.instance_xpath, namespaces=namespaces)
        if not isinstance(result, list):
            continue
        for node in result:
            if isinstance(node, etree._Element) and isinstance(node.tag, str):
                match = Match(entry, node, None)
            elif getattr(node, "is_attribute", False):
                name = str(getattr(node, "attrname", ""))
                match = Match(entry, node.getparent(), etree.QName(name).localname)
            else:
                continue
            if limit is not None and len(found) >= limit:
                more[entry.status] = more.get(entry.status, 0) + 1
            else:
                found.append(match)
    return found, more
