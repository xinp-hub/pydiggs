"""Shared builders for the diff tests: corpus models and single-edit helpers.

The edit helpers rebuild a frozen ``Model`` with ``dataclasses.replace``, so a test says
exactly which one thing changed.
"""

from __future__ import annotations

import dataclasses
import json
from functools import cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pydiggs
from pydiggs.errors import DocumentError
from pydiggs.model import (
    Column,
    Component,
    Feature,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Observation,
    Quantity,
)

ROOT = Path(__file__).resolve().parent.parent.parent
OFFICIAL = ROOT / "tests" / "fixtures" / "official"
AGS4 = ROOT / "tests" / "fixtures" / "ags4"
ANNOTATED = OFFICIAL / "3.0" / "Borehole-CPT_Example_Annotated.xml"
#: Small, varied files for the hypothesis tests (nodes, tables, components, layers).
SMALL = (
    OFFICIAL / "3.0" / "Borehole-CPT_Example_Annotated.xml",
    OFFICIAL / "2.6" / "AtterbergExample-from25.xml",
    OFFICIAL / "2.5.a" / "LithoGrading.xml",
    OFFICIAL / "3.0" / "MWDExample.xml",
    OFFICIAL / "2.6" / "DocumentationExample.xml",
)

NODE_LISTS = (
    "projects",
    "associates",
    "features",
    "sampling_activities",
    "samples",
    "observations",
    "measurements",
    "others",
)


def corpus_paths() -> list[Path]:
    return sorted(OFFICIAL.rglob("*.xml"))


@cache
def read_corpus(path: Path) -> Model | None:
    """The model of a corpus file, or None for the one file that is not readable DIGGS."""
    try:
        return pydiggs.read(path)
    except DocumentError:
        return None


@cache
def small_models() -> tuple[Model, ...]:
    return tuple(pydiggs.read(p) for p in SMALL)


# --- what a model refers to ----------------------------------------------------------


def referenced_keys(model: Model) -> set[str]:
    """Keys other nodes point at (links, and the typed fields that hold keys)."""
    # a link from a node to itself (a borehole's own centerline) is not a referrer
    keys = {link.target for link in model.links if link.target not in (None, link.source)}
    for node in model.nodes():
        for name in ("feature", "sampling_activity", "parent", "specimen"):
            value = getattr(node, name, None)
            if value:
                keys.add(value)
        keys.update(getattr(node, "samples", ()))
    return keys


def children(node: Node) -> tuple[Component, ...]:
    """The components nested directly in a node."""
    if isinstance(node, Feature):
        return node.construction + node.water_strikes
    if isinstance(node, LithologyLayer | Component):
        return node.components
    if isinstance(node, Measurement) and node.procedure is not None:
        return node.procedure.components
    if isinstance(node, Observation):
        return ()
    return ()


def subtree(node: Node) -> list[Node]:
    out: list[Node] = [node]
    if isinstance(node, Observation):
        for layer in node.layers:
            out.extend(subtree(layer))
    for child in children(node):
        out.extend(subtree(child))
    return out


# --- single edits ---------------------------------------------------------------------


def _without_links(model: Model, keys: set[str]) -> tuple[Any, ...]:
    return tuple(
        link
        for link in model.links
        if link.source not in keys and (link.target not in keys or link.source == link.target)
    )


def drop_node(model: Model, node: Node) -> Model:
    """``model`` without a top-level node (and so its layers and components)."""
    gone = {n.key for n in subtree(node)}
    changes: dict[str, Any] = {"links": _without_links(model, gone)}
    for name in NODE_LISTS:
        current = getattr(model, name)
        if any(n.key == node.key for n in current):
            changes[name] = tuple(n for n in current if n.key != node.key)
    return dataclasses.replace(model, **changes)


def replace_in(model: Model, old: Node, new: Node) -> Model:
    """``model`` with a top-level node swapped for another."""
    for name in NODE_LISTS:
        current = getattr(model, name)
        if any(n is old for n in current):
            return dataclasses.replace(
                model, **{name: tuple(new if n is old else n for n in current)}
            )
    raise AssertionError("not a top-level node")


def with_param(node: Node, name: str, value: Any) -> Node:
    return dataclasses.replace(node, params={**node.params, name: value})


def bump(value: Any) -> Any:
    """A different value of the same kind (text, number, quantity or code)."""
    if isinstance(value, Quantity):
        number = (value.value or 0.0) + 1.0
        return dataclasses.replace(value, value=number, text=repr(number), si=None)
    if isinstance(value, str):
        return value + "x"
    return dataclasses.replace(value, value=value.value + "x")


def other_unit(value: Quantity) -> Quantity:
    return dataclasses.replace(value, uom=(value.uom or "") + "x", si=None)


def edit_table(model: Model, holder: Measurement, index: int, table: Any) -> Model:
    results = tuple(table if i == index else t for i, t in enumerate(holder.results))
    return replace_in(model, holder, dataclasses.replace(holder, results=results))


def with_row(table: Any, row: tuple[Any, ...], index_row: tuple[Any, ...] = ()) -> Any:
    columns = tuple(
        dataclasses.replace(c, values=(*c.values, v))
        for c, v in zip(table.columns, row, strict=True)
    )
    domain = table.domain
    if domain.index:
        index = tuple(
            dataclasses.replace(c, values=(*c.values, v))
            for c, v in zip(domain.index, index_row, strict=True)
        )
        domain = dataclasses.replace(domain, index=index)
    return dataclasses.replace(table, columns=columns, domain=domain, row_count=table.row_count + 1)


def renamed(model: Model, prefix: str = "r") -> Model:
    """Every ``gml:id`` of the model renamed, with every reference to it following."""
    mapping = {
        n.key: f"{prefix}{i}"
        for i, n in enumerate(model.nodes())
        if n.gml_id is not None and n.key == n.gml_id
    }

    def ref(key: str | None) -> str | None:
        return mapping.get(key, key) if key else key

    def fix(node: Node) -> Node:
        changes: dict[str, Any] = {}
        if node.key in mapping:
            changes["key"] = changes["gml_id"] = mapping[node.key]
        for name in ("feature", "sampling_activity", "parent", "specimen"):
            if getattr(node, name, None):
                changes[name] = ref(getattr(node, name))
        if getattr(node, "samples", ()):
            changes["samples"] = tuple(ref(k) for k in node.samples)
        if isinstance(node, Feature):
            changes["construction"] = tuple(fix(c) for c in node.construction)
            changes["water_strikes"] = tuple(fix(c) for c in node.water_strikes)
        if isinstance(node, Component):
            changes["components"] = tuple(fix(c) for c in node.components)
        if isinstance(node, LithologyLayer):
            changes["components"] = tuple(fix(c) for c in node.components)
        if isinstance(node, Observation):
            changes["layers"] = tuple(fix(c) for c in node.layers)
        if isinstance(node, Measurement) and node.procedure is not None:
            comps = tuple(fix(c) for c in node.procedure.components)
            changes["procedure"] = dataclasses.replace(node.procedure, components=comps)
        return dataclasses.replace(node, **changes)

    changes = {name: tuple(fix(n) for n in getattr(model, name)) for name in NODE_LISTS}
    if model.document is not None:
        changes["document"] = fix(model.document)

    def href(text: str) -> str:
        return f"#{mapping[text[1:]]}" if text[1:] in mapping and text.startswith("#") else text

    links = tuple(
        dataclasses.replace(
            link, source=ref(link.source), target=ref(link.target), href=href(link.href)
        )
        for link in model.links
    )
    return dataclasses.replace(model, links=links, **changes)


def column_of(table: Any, name: str) -> Column:
    return next(c for c in table.columns if c.name == name)


# --- schema ---------------------------------------------------------------------------


#: Keywords whose subschemas add conditions to an object and declare none of its fields.
_CONDITIONS = frozenset({"allOf", "if", "then", "else"})


def close_schema(node: Any, name: str = "") -> Any:
    """The strict variant: every object with declared properties accepts no others.

    A condition (``allOf``, ``if`` / ``then`` / ``else``) is left as it is: it says
    something more about fields declared elsewhere.
    """
    if isinstance(node, dict):
        out = {k: v if k in _CONDITIONS else close_schema(v, k) for k, v in node.items()}
        if "properties" in out and name != "properties":
            out["unevaluatedProperties"] = False
        return out
    if isinstance(node, list):
        return [close_schema(v, name) for v in node]
    return node


@cache
def closed_validator() -> Any:
    import jsonschema
    import referencing

    from pydiggs import diff

    findings = json.loads(
        resources.files("pydiggs.findings")
        .joinpath("diggs-findings-1.2.schema.json")
        .read_text("utf-8")
    )
    registry = referencing.Registry().with_resource(
        findings["$id"], referencing.Resource.from_contents(findings)
    )
    return jsonschema.Draft202012Validator(close_schema(diff.diff_schema()), registry=registry)


def schema_errors(document: Any) -> list[str]:
    return [f"{e.json_path}: {e.message}" for e in closed_validator().iter_errors(document)]


EMPTY = MappingProxyType({})


# --- tiny hand-made models ------------------------------------------------------------

from pydiggs.model import (  # noqa: E402
    Domain,
    Link,
    Provenance,
    ResultTable,
    Sample,
    SourceInfo,
)


def _header(key: str | None, name: str | None, element: str, kind: str, at: int) -> dict[str, Any]:
    return {
        "key": key or f"@/Diggs[1]/x[{at}]",
        "gml_id": key,
        "element": element,
        "kind": kind,
        "name": name,
        "names": (name,) if name else (),
        "description": None,
        "remarks": (),
        "provenance": Provenance(xpath=f"/Diggs[1]/x[{at}]", line=at),
        "extra": (),
    }


def make_feature(key: str | None, name: str | None, at: int = 1, **fields: Any) -> Feature:
    return Feature(**{**_header(key, name, "Borehole", "borehole", at), **fields})


def make_sample(
    key: str | None, name: str | None, feature: str, at: int = 2, **fields: Any
) -> Sample:
    return Sample(**{**_header(key, name, "Sample", "sample", at), "feature": feature, **fields})


def make_test(
    key: str | None, name: str | None, feature: str, at: int = 3, **fields: Any
) -> Measurement:
    return Measurement(**{**_header(key, name, "Test", "test", at), "feature": feature, **fields})


def depth(text: str, uom: str | None = "ft") -> Quantity:
    return Quantity(text=text, value=float(text), uom=uom)


def table(*columns: Column, index: tuple[Column, ...] = (), **fields: Any) -> ResultTable:
    rows = len(columns[0].values) if columns else 0
    shape = "multipoint" if index else "none"
    return ResultTable(
        domain=Domain(shape=shape, index=index), columns=columns, row_count=rows, **fields
    )


def column(name: str, *values: Any, key: str | None = None, uom: str | None = None) -> Column:
    return Column(name=name, key=key, uom=uom, type="double", values=tuple(values))


def make_model(*, links: tuple[Link, ...] = (), version: str = "3.0.0", **lists: Any) -> Model:
    return Model(
        schema_version="1.0",
        tool={"name": "pydiggs", "version": "0"},
        source=SourceInfo(
            name="x.xml",
            sha256="0" * 64,
            size=1,
            diggs_version=version,
            namespace=None,
            detected=True,
        ),
        complete=True,
        links=links,
        **lists,
    )


def collection_of(node: Node) -> str:
    from pydiggs.model import (
        Associate,
        DocumentInfo,
        Other,
        Project,
        SamplingActivity,
    )

    table_ = {
        DocumentInfo: "document",
        Project: "projects",
        Associate: "associates",
        Feature: "features",
        SamplingActivity: "sampling_activities",
        Sample: "samples",
        Observation: "observations",
        Measurement: "measurements",
        Other: "others",
        LithologyLayer: "layers",
        Component: "components",
    }
    return table_[type(node)]


def make_component(key: str | None, role: str, at: int = 7, **fields: Any) -> Component:
    return Component(**{**_header(key, None, "DriveSet", "drive_set", at), "role": role, **fields})
