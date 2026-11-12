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
import re
import sys
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


def _particle(schema: _Schema, holder: etree._Element, out: list[QName]) -> None:
    """Append the elements of ``holder``'s content model to ``out``, in schema order."""
    for item in holder:
        kind = _tag(item)
        if kind in _PARTICLES:
            _particle(schema, item, out)
        elif kind == "group":
            group = schema.groups.get(_resolve(item, item.get("ref", "")))
            if group is None:
                raise LayoutError(f"unknown group {item.get('ref')!r}")
            _particle(schema, group, out)
        elif kind == "element":
            ref = item.get("ref")
            name = _resolve(item, ref) if ref else (_target(schema, item), item.get("name", ""))
            decl = schema.elements.get(name) if ref else None
            if decl is None or decl.get("abstract") != "true":
                out.append(name)
            out.extend(_substitutes(schema, name))


def _substitutes(schema: _Schema, head: QName) -> Iterator[QName]:
    """Every concrete element that may stand in for ``head``, in name order."""
    for member in schema.members.get(head, ()):
        if schema.elements[member].get("abstract") != "true":
            yield member
        yield from _substitutes(schema, member)


def _children(schema: _Schema, type_decl: etree._Element | None) -> list[QName]:
    """The child elements a type allows, base types first, each once."""
    if type_decl is None or _tag(type_decl) != "complexType":
        return []
    out: list[QName] = []
    derived = _derivation(type_decl)
    if derived is None:
        _particle(schema, type_decl, out)
    else:
        kind, step = derived
        if kind == "extension":
            out.extend(_children(schema, schema.types.get(_resolve(step, step.get("base", "")))))
        _particle(schema, step, out)
    return list(dict.fromkeys(out))


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
