"""Generate ``src/pydiggs/_writer/layout-<version>.toml`` from the bundled DIGGS XSDs.

The internal DIGGS writer (``pydiggs._writer``) builds every element from a model, so it
has to know two things the model does not say: the namespace of each object element, and
the order the schema wants its children in. Both are facts of the XSD. This script reads
them out once per DIGGS version the writer targets (see ``PROFILES``), and the result is
committed and reviewed like a golden file; it is the inverse of ``_reader/aliases.toml``.

For every concrete object element (a global element whose name starts with a capital, in
the DIGGS, GML or GML linear-referencing namespace) the layout lists:

* ``ns``        the prefix of its namespace;
* ``id``        ``"required"`` or ``"optional"`` when the element takes a ``gml:id``;
* ``interval``, ``location``, ``time``  the first child that may hold a ``LinearExtent``,
  a ``PointLocation`` and a ``TimeInterval``;
* ``children``  its child elements in schema order, base types first. A child in the
  DIGGS namespace is written bare, any other as ``prefix:name``. Where the schema names an
  abstract element or the head of a substitution group, its concrete members follow it.
  Where a choice puts one element in two branches, the order fits every branch.
* ``required``  the children the schema requires: each entry lists the elements of which
  at least one must be present (one, or the members of a required choice or substitution
  group). It never asks for more than the schema does, so the writer can refuse a model
  that lacks one instead of writing a document that is not valid.
* ``when``      what is required once a child is present that only a branch of a choice,
  or an optional sequence, holds: ``present`` lists those children, ``required`` is as
  above (``samplingLocation`` needs ``samplingFeatureRef``, for example).

Usage (from the package root)::

    python scripts/writer_layout.py                    # rewrite every layout and its digest
    python scripts/writer_layout.py --check            # exit 1 when a committed file is stale
    python scripts/writer_layout.py --version 3.0.0    # one version only

Nothing is downloaded: the only inputs are the XSDs under ``src/pydiggs/schemas``, read
from each version's ``Diggs.xsd`` through its includes and imports.
``tests/test_writer_min.py`` runs the check, so CI fails on a stale layout. To add a
version, add a ``Profile`` here, run the script, and record the new file in
``scripts/sync_reference.py`` (``PACKAGE_LOCAL``) and ``src/pydiggs/PROVENANCE.toml``.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lxml import etree  # noqa: E402

from pydiggs import _xml  # noqa: E402

PKG = ROOT / "src" / "pydiggs"
PROVENANCE = PKG / "PROVENANCE.toml"
XS = "http://www.w3.org/2001/XMLSchema"
XML = "http://www.w3.org/XML/1998/namespace"
_PARTICLES = frozenset({"sequence", "choice", "all"})
#: Particles that may hold elements, as the branches of a choice.
_CONTENT = _PARTICLES | {"group", "element", "any"}
#: Layout key -> the DIGGS object whose first holder among an element's children it names.
_HELD = {"interval": "LinearExtent", "location": "PointLocation", "time": "TimeInterval"}


@dataclass(frozen=True)
class Profile:
    """One DIGGS version the writer targets: where its XSDs are and how it is written.

    ``namespaces`` maps prefix to namespace as instance documents write them; ``diggs``
    is the default namespace. ``emitted`` names the namespaces whose object elements get
    a layout entry; where two share an element name, the earlier one keeps it.
    """

    version: str
    namespaces: dict[str, str]
    emitted: tuple[str, ...] = ("diggs", "gml", "glr")
    entry: str = "Diggs.xsd"

    @property
    def schema_dir(self) -> Path:
        """The bundled schema tree of this version."""
        return PKG / "schemas" / f"diggs-schema-{self.version}"

    @property
    def out(self) -> Path:
        """The layout file of this version."""
        return PKG / "_writer" / f"layout-{self.version}.toml"

    @property
    def out_rel(self) -> str:
        """The layout file's path as PROVENANCE.toml records it."""
        return self.out.relative_to(PKG).as_posix()


PROFILES = {
    "3.0.0": Profile(
        version="3.0.0",
        namespaces={
            "diggs": "http://diggsml.org/schemas/3",
            "gml": "http://www.opengis.net/gml/3.2",
            "glr": "http://www.opengis.net/gml/3.3/lr",
            "glrov": "http://www.opengis.net/gml/3.3/lrov",
            "xlink": "http://www.w3.org/1999/xlink",
            "xml": XML,
        },
    ),
}

QName = tuple[str, str]


class LayoutError(Exception):
    """The XSDs hold something this generator does not understand."""


@dataclass
class _Schema:
    """Every global declaration of the bundled XSDs, by qualified name."""

    elements: dict[QName, etree._Element] = field(default_factory=dict)
    types: dict[QName, etree._Element] = field(default_factory=dict)
    groups: dict[QName, etree._Element] = field(default_factory=dict)
    attribute_groups: dict[QName, etree._Element] = field(default_factory=dict)
    target: dict[etree._Element, str] = field(default_factory=dict)
    prefix: dict[str, str] = field(default_factory=dict)
    members: dict[QName, list[QName]] = field(default_factory=dict)


def _tag(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _kids(el: etree._Element, *names: str) -> Iterator[etree._Element]:
    return (c for c in el if _tag(c) in names and etree.QName(c).namespace == XS)


def _resolve(el: etree._Element, qname: str) -> QName:
    """A QName attribute value as (namespace, local), by the declarations in scope."""
    prefix, _, local = qname.rpartition(":")
    uri = XML if prefix == "xml" else el.nsmap.get(prefix or None)
    if uri is None:
        raise LayoutError(f"undeclared prefix in {qname!r} (line {el.sourceline})")
    return uri, local


def _schema_files(profile: Profile) -> list[Path]:
    """The profile's entry XSD and every bundled file it includes or imports, in order.

    A ``schemaLocation`` that is not a file inside the schema tree (a URL, or a name the
    bundle does not have) is skipped: libxml2 does the same when it validates.
    """
    root_dir = profile.schema_dir.resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    queue = [root_dir / profile.entry]
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        found.append(path)
        for ref in _kids(_xml.parse(path).getroot(), "include", "import"):
            location = ref.get("schemaLocation", "")
            if not location or "://" in location:
                continue
            target = (path.parent / location).resolve()
            if target.is_file() and root_dir in target.parents:
                queue.append(target)
    return found


def load(profile: Profile) -> _Schema:
    """Read the profile's XSDs into one index of global declarations."""
    schema = _Schema(prefix={uri: prefix for prefix, uri in profile.namespaces.items()})
    for path in _schema_files(profile):
        root = _xml.parse(path).getroot()
        target = root.get("targetNamespace")
        if target not in schema.prefix:
            continue
        for decl in root:
            name = decl.get("name") if isinstance(decl.tag, str) else None
            if name is None:
                continue
            table = {
                "element": schema.elements,
                "complexType": schema.types,
                "simpleType": schema.types,
                "group": schema.groups,
                "attributeGroup": schema.attribute_groups,
            }.get(_tag(decl))
            if table is None:
                continue
            key = (target, name)
            if key in table:
                raise LayoutError(f"{name} is declared twice in {target}")
            table[key] = decl
            schema.target[decl] = target
    for key, decl in sorted(schema.elements.items()):
        head = decl.get("substitutionGroup")
        if head is not None:
            schema.members.setdefault(_resolve(decl, head), []).append(key)
    return schema


def _type_of(schema: _Schema, element: etree._Element) -> etree._Element | None:
    """The complex or simple type declaration of an element, or None for a built-in."""
    named = element.get("type")
    if named is not None:
        return schema.types.get(_resolve(element, named))
    return next(_kids(element, "complexType", "simpleType"), None)


def _derivation(type_decl: etree._Element) -> tuple[str, etree._Element] | None:
    """``("extension" | "restriction", element)`` of a derived complex type."""
    for content in _kids(type_decl, "complexContent", "simpleContent"):
        for step in _kids(content, "extension", "restriction"):
            return _tag(step), step
    return None


def _target(schema: _Schema, el: etree._Element) -> str:
    node: etree._Element | None = el
    while node is not None:
        if node in schema.target:
            return schema.target[node]
        node = node.getparent()
    raise LayoutError(f"no target namespace for line {el.sourceline}")


Edges = set[tuple[QName, QName]]


def _sequence(
    schema: _Schema, holder: etree._Element, out: list[QName], edges: Edges
) -> set[QName]:
    """The elements of ``holder``'s particles, taken as a sequence; see :func:`_particle`.

    Every element of an earlier particle must come before every element of a later one,
    so each such pair is an edge.
    """
    before: set[QName] = set()
    for item in holder:
        names = _particle(schema, item, out, edges)
        edges.update((a, b) for a in before for b in names if a != b)
        before |= names
    return before


def _particle(schema: _Schema, item: etree._Element, out: list[QName], edges: Edges) -> set[QName]:
    """The elements particle ``item`` may hold, appended to ``out`` in schema order.

    ``edges`` gets ``(a, b)`` for every pair the schema puts ``a`` before ``b`` in. The
    branches of a choice (and the members of an ``all``) are alternatives, so they give
    no edge between them: an element that appears in two branches is ordered by what
    each branch puts around it, not by where it appears first.
    """
    kind = _tag(item)
    if kind == "sequence":
        return _sequence(schema, item, out, edges)
    if kind in _PARTICLES:
        found: set[QName] = set()
        for branch in item:
            found |= _particle(schema, branch, out, edges)
        return found
    if kind == "group":
        group = schema.groups.get(_resolve(item, item.get("ref", "")))
        if group is None:
            raise LayoutError(f"unknown group {item.get('ref')!r}")
        return _sequence(schema, group, out, edges)
    if kind == "element":
        ref = item.get("ref")
        name = _resolve(item, ref) if ref else (_target(schema, item), item.get("name", ""))
        decl = schema.elements.get(name) if ref else None
        names = [] if decl is not None and decl.get("abstract") == "true" else [name]
        names.extend(_substitutes(schema, name))
        out.extend(names)
        return set(names)
    return set()  # an annotation, an attribute: no element


Needs = list[frozenset[QName]]
#: ``(when, needs)``: when one of ``when`` is present, ``needs`` are required too.
Condition = tuple[frozenset[QName], Needs]


def _elements_of(schema: _Schema, item: etree._Element) -> frozenset[QName]:
    return frozenset(_particle(schema, item, [], set()))


def _needs(schema: _Schema, item: etree._Element, conditions: list[Condition]) -> Needs:
    """What particle ``item`` requires: each entry a set of which one must be present.

    A required choice requires one element of its branches, unless a branch requires
    nothing; an optional particle (``minOccurs="0"``) requires nothing. What a branch
    of a choice, or an optional sequence, requires once it is there goes to
    ``conditions``, keyed by the elements that show it is there.
    """
    kind = _tag(item)
    if kind in ("sequence", "all", "group"):
        holder = item
        if kind == "group":
            found = schema.groups.get(_resolve(item, item.get("ref", "")))
            if found is None:
                raise LayoutError(f"unknown group {item.get('ref')!r}")
            holder = found
        inner = [need for child in holder for need in _needs(schema, child, conditions)]
    elif kind == "choice":
        branches = [branch for branch in item if _tag(branch) in _CONTENT]
        needs = [_needs(schema, branch, conditions) for branch in branches]
        for number, branch in enumerate(branches):
            others = frozenset().union(
                *(_elements_of(schema, b) for n, b in enumerate(branches) if n != number)
            )
            if needs[number] and len(branches) > 1:
                conditions.append((_elements_of(schema, branch) - others, needs[number]))
        if not needs or not all(needs):
            return []
        inner = [frozenset().union(*(need for branch in needs for need in branch))]
    elif kind == "element":
        names = _elements_of(schema, item)
        inner = [names] if names else []
    else:
        return []
    if item.get("minOccurs", "1") == "0":
        if inner and kind in ("sequence", "all", "group"):
            conditions.append((_elements_of(schema, item), inner))
        return []
    return inner


def _required(
    schema: _Schema, type_decl: etree._Element | None, conditions: list[Condition]
) -> Needs:
    """What a type's content model requires, base types first (see :func:`_needs`)."""
    if type_decl is None or _tag(type_decl) != "complexType":
        return []
    derived = _derivation(type_decl)
    if derived is None:
        return [need for item in type_decl for need in _needs(schema, item, conditions)]
    kind, step = derived
    base: Needs = []
    if kind == "extension":
        declared = schema.types.get(_resolve(step, step.get("base", "")))
        base = _required(schema, declared, conditions)
    return base + [need for item in step for need in _needs(schema, item, conditions)]


def _rules(
    schema: _Schema, type_decl: etree._Element | None
) -> tuple[Needs, list[tuple[list[QName], Needs]]]:
    """What a type requires, and what it requires when some child is present.

    A condition only counts children that appear once in the content model: one that
    appears in two places does not show which of them it fills.
    """
    out: list[QName] = []
    _content(schema, type_decl, out, set())
    once = {name for name, count in Counter(out).items() if count == 1}
    conditions: list[Condition] = []
    required = _required(schema, type_decl, conditions)
    met = set(required)
    kept: list[tuple[list[QName], Needs]] = []
    for when, needs in conditions:
        wanted = [need for need in needs if need not in met]
        # A child that meets every need itself shows nothing new.
        shows = sorted(n for n in when & once if not all(n in need for need in wanted))
        if shows and wanted and (shows, wanted) not in kept:
            kept.append((shows, wanted))
    return required, kept


def _ordered(out: list[QName], edges: Edges) -> list[QName]:
    """Each element of ``out`` once, in an order that keeps every edge.

    Among the elements free to come next, the one ``out`` lists first goes first, so a
    content model without choices keeps exactly its schema order.

    Raises:
        LayoutError: The edges have a cycle: no one order fits every branch.
    """
    names = list(dict.fromkeys(out))
    rank = {name: number for number, name in enumerate(names)}
    after: dict[QName, set[QName]] = {name: set() for name in names}
    waiting = dict.fromkeys(names, 0)
    for a, b in edges:
        if b not in after[a]:
            after[a].add(b)
            waiting[b] += 1
    ready = [rank[name] for name in names if not waiting[name]]
    heapq.heapify(ready)
    order: list[QName] = []
    while ready:
        name = names[heapq.heappop(ready)]
        order.append(name)
        for later in after[name]:
            waiting[later] -= 1
            if not waiting[later]:
                heapq.heappush(ready, rank[later])
    if len(order) != len(names):
        stuck = sorted(local for (_, local), count in waiting.items() if count)
        raise LayoutError(f"no single child order fits {', '.join(stuck)}")
    return order


def _substitutes(schema: _Schema, head: QName) -> Iterator[QName]:
    """Every concrete element that may stand in for ``head``, in name order."""
    for member in schema.members.get(head, ()):
        if schema.elements[member].get("abstract") != "true":
            yield member
        yield from _substitutes(schema, member)


def _content(
    schema: _Schema, type_decl: etree._Element | None, out: list[QName], edges: Edges
) -> set[QName]:
    """The elements a type's content model holds, base types first (see :func:`_particle`)."""
    if type_decl is None or _tag(type_decl) != "complexType":
        return set()
    derived = _derivation(type_decl)
    if derived is None:
        return _sequence(schema, type_decl, out, edges)
    kind, step = derived
    base: set[QName] = set()
    if kind == "extension":
        base = _content(schema, schema.types.get(_resolve(step, step.get("base", ""))), out, edges)
    own = _sequence(schema, step, out, edges)
    edges.update((a, b) for a in base for b in own if a != b)
    return base | own


def _children(schema: _Schema, type_decl: etree._Element | None) -> list[QName]:
    """The child elements a type allows, each once, in an order every branch accepts."""
    out: list[QName] = []
    edges: Edges = set()
    _content(schema, type_decl, out, edges)
    return _ordered(out, edges)


def _attributes(schema: _Schema, holder: etree._Element) -> Iterator[etree._Element]:
    for item in _kids(holder, "attribute", "attributeGroup"):
        if _tag(item) == "attribute":
            yield item
            continue
        group = schema.attribute_groups.get(_resolve(item, item.get("ref", "")))
        if group is not None:
            yield from _attributes(schema, group)


def _gml_id(schema: _Schema, type_decl: etree._Element | None) -> str | None:
    """``"required"``, ``"optional"`` or None: how a type takes ``gml:id``."""
    if type_decl is None or _tag(type_decl) != "complexType":
        return None
    derived = _derivation(type_decl)
    holder = type_decl if derived is None else derived[1]
    for attribute in _attributes(schema, holder):
        ref = attribute.get("ref")
        if ref is not None and _resolve(attribute, ref)[1] == "id" and ref.startswith("gml:"):
            use = attribute.get("use", "optional")
            return None if use == "prohibited" else use
    if derived is None:
        return None
    base = derived[1].get("base", "")
    return _gml_id(schema, schema.types.get(_resolve(derived[1], base)))


def _written(schema: _Schema, name: QName) -> str:
    uri, local = name
    prefix = schema.prefix.get(uri)
    if prefix is None:
        raise LayoutError(f"no prefix for namespace {uri} (element {local})")
    return local if prefix == "diggs" else f"{prefix}:{local}"


def build(profile: Profile) -> dict[str, dict[str, object]]:
    """The layout: object element name -> its namespace prefix, id use and children."""
    schema = load(profile)
    layout: dict[str, dict[str, object]] = {}
    for prefix in profile.emitted:
        uri = profile.namespaces[prefix]
        for (ns, name), decl in sorted(schema.elements.items()):
            if ns != uri or not name[:1].isupper() or decl.get("abstract") == "true":
                continue
            if name in layout:  # the earlier namespace in EMITTED keeps the name
                continue
            type_decl = _type_of(schema, decl)
            entry: dict[str, object] = {"ns": prefix}
            use = _gml_id(schema, type_decl)
            if use is not None:
                entry["id"] = use
            children = _children(schema, type_decl)
            for key, held in _HELD.items():
                wanted = (profile.namespaces["diggs"], held)
                holder = next((c for c in children if wanted in _holds(schema, type_decl, c)), None)
                if holder is not None:
                    entry[key] = _written(schema, holder)
            entry["children"] = [_written(schema, child) for child in children]
            place = {child: number for number, child in enumerate(children)}

            def written(needs: Needs, place: dict[QName, int] = place) -> list[list[str]]:
                ordered = {tuple(sorted(need, key=place.__getitem__)) for need in needs}
                return [
                    [_written(schema, child) for child in need]
                    for need in sorted(ordered, key=lambda need: [place[c] for c in need])
                ]

            required, conditions = _rules(schema, type_decl)
            entry["required"] = written(required)
            rules = {
                (
                    tuple(_written(schema, c) for c in sorted(shows, key=place.__getitem__)),
                    tuple(map(tuple, written(needs))),
                )
                for shows, needs in conditions
            }
            entry["when"] = [
                ([*shows], [[*need] for need in needs]) for shows, needs in sorted(rules)
            ]
            layout[name] = entry
    return layout


def _local_element(
    schema: _Schema, type_decl: etree._Element | None, name: QName
) -> etree._Element | None:
    """The declaration of child ``name`` in a type or its bases (global when a ``ref``)."""
    if name in schema.elements:
        return schema.elements[name]
    while type_decl is not None:
        for item in type_decl.iter(f"{{{XS}}}element"):
            if item.get("name") == name[1] and _target(schema, item) == name[0]:
                return item
        derived = _derivation(type_decl)
        if derived is None:
            return None
        type_decl = schema.types.get(_resolve(derived[1], derived[1].get("base", "")))
    return None


def _holds(schema: _Schema, type_decl: etree._Element | None, child: QName) -> list[QName]:
    """The object elements a property child may hold."""
    decl = _local_element(schema, type_decl, child)
    return [] if decl is None else _children(schema, _type_of(schema, decl))


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(profile: Profile, layout: dict[str, dict[str, object]]) -> str:
    """The layout as TOML text: stable order, one child per line, LF line endings."""
    lines = [
        f"# Element layout of DIGGS {profile.version} for the internal pyDIGGS writer.",
        "# Generated from the bundled XSDs by scripts/writer_layout.py. Do not edit by hand:",
        "# run `python scripts/writer_layout.py` and review the diff like a golden file.",
        "#",
        "# [elements.<Name>]: an object element the writer may emit.",
        "#   ns        prefix of its namespace (see [namespaces])",
        '#   id        "required" or "optional" when it takes a gml:id; absent when it takes none',
        "#   interval  the first child that may hold a LinearExtent (where it applies)",
        "#   location  the first child that may hold a PointLocation",
        "#   time      the first child that may hold a TimeInterval (when it applies)",
        "#   children  its child elements in schema order; bare names are in the DIGGS namespace",
        "#   required  what the schema requires: per entry, one of these children at least",
        "#   when      what it requires once one of `present` is there (a choice branch, or an",
        "#             optional sequence): `required` as above",
        "",
        "format = 1",
        f"diggs_version = {_string(profile.version)}",
        "",
        "[namespaces]",
    ]
    lines += [
        f"{prefix if _BARE_KEY.fullmatch(prefix) else _string(prefix)} = {_string(uri)}"
        for prefix, uri in profile.namespaces.items()
    ]
    for name in sorted(layout):
        entry = layout[name]
        lines += ["", f"[elements.{name}]", f"ns = {_string(str(entry['ns']))}"]
        lines += [f"{key} = {_string(str(entry[key]))}" for key in ("id", *_HELD) if key in entry]
        children = entry["children"]
        assert isinstance(children, list)  # noqa: S101 - build() makes it one
        if children:
            lines.append("children = [")
            lines += [f"  {_string(child)}," for child in children]
            lines.append("]")
        else:
            lines.append("children = []")
        required = entry["required"]
        assert isinstance(required, list)  # noqa: S101 - build() makes it one
        if required:
            lines.append("required = [")
            lines += [f"  [{', '.join(map(_string, need))}]," for need in required]
            lines.append("]")
        when = entry["when"]
        assert isinstance(when, list)  # noqa: S101 - build() makes it one
        if when:
            lines.append("when = [")
            for present, needs in when:
                listed = ", ".join(f"[{', '.join(map(_string, need))}]" for need in needs)
                shown = ", ".join(map(_string, present))
                lines.append(f"  {{ present = [{shown}], required = [{listed}] }},")
            lines.append("]")
    return "\n".join(lines) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provenance_entry(profile: Profile) -> re.Pattern[str]:
    return re.compile(
        r'(\[files\."'
        + re.escape(profile.out_rel)
        + r'"\]\n(?:(?!\[).*\n)*?sha256 = ")[0-9a-f]{64}(")'
    )


def check(profile: Profile) -> list[str]:
    """Why the committed layout is stale, or an empty list when it is current."""
    expected = render(profile, build(profile))
    out = profile.out
    if not out.is_file():
        return [f"missing {out.relative_to(ROOT)}"]
    committed = out.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    problems = []
    if committed != expected:
        problems.append(f"{out.relative_to(ROOT)} is stale: run `python scripts/writer_layout.py`")
    recorded = _provenance_entry(profile).search(PROVENANCE.read_text(encoding="utf-8"))
    if recorded is None:
        problems.append(f"{profile.out_rel} is not recorded in {PROVENANCE.relative_to(ROOT)}")
    elif recorded.group(0).rpartition('"')[0][-64:] != _sha256(committed):
        problems.append(f"the digest of {profile.out_rel} in PROVENANCE.toml is stale")
    return problems


def write(profile: Profile) -> list[str]:
    """Rewrite the layout and its digest in PROVENANCE.toml; return what changed."""
    text = render(profile, build(profile))
    out = profile.out
    changed = []
    if not out.is_file() or out.read_bytes() != text.encode("utf-8"):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        changed.append(f"wrote {out.relative_to(ROOT)}")
    provenance = PROVENANCE.read_text(encoding="utf-8")
    updated = _provenance_entry(profile).sub(rf"\g<1>{_sha256(text)}\g<2>", provenance)
    if updated != provenance:
        PROVENANCE.write_bytes(updated.encode("utf-8"))
        changed.append(f"updated the digest in {PROVENANCE.relative_to(ROOT)}")
    return changed


def main(argv: list[str] | None = None) -> int:
    """Write the layouts, or with ``--check`` verify them; return the process exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="verify the committed layouts; write nothing"
    )
    parser.add_argument(
        "--version",
        action="append",
        choices=sorted(PROFILES),
        help="a DIGGS version to (re)generate or check; default: every one",
    )
    args = parser.parse_args(argv)
    lines: list[str] = []
    try:
        for version in args.version or sorted(PROFILES):
            profile = PROFILES[version]
            lines += check(profile) if args.check else write(profile)
    except (LayoutError, OSError, etree.XMLSyntaxError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    for line in lines:
        print(line, file=sys.stderr if args.check else sys.stdout)
    if args.check:
        print("writer layout OK" if not lines else f"{len(lines)} problem(s)")
        return 1 if lines else 0
    if not lines:
        print("writer layout unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
