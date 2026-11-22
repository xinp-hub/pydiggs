"""A :class:`~pydiggs.model.Model` as profile tables, with a plan for loading them.

Every value of the model lands in a column of a typed table, in ``params``, ``remarks``
or ``links``, or, when the profile has no place for it, in ``extra`` as JSON. Nothing is
dropped; what went to ``extra`` is counted in a finding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pydiggs.convert._from_model import _helper
from pydiggs.convert._mapping import PROPERTIES_URL
from pydiggs.findings.model import Finding
from pydiggs.model import (
    Code,
    Column,
    Component,
    Feature,
    Interval,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Param,
    Quantity,
    ResultTable,
    TimeSpan,
)
from pydiggs.model._model import _encode
from pydiggs.tables import _report as rep
from pydiggs.tables._profile import (
    PROFILE_VERSION,
    RESULT_KEYS,
    RESULT_PLACES,
    RESULTS_PREFIX,
    TABLES,
    profile,
    results_schema,
    results_table_name,
)
from pydiggs.tables._to_model import exact

__all__ = ["LoadPlan", "LoadStep", "TableSet", "from_model"]

Row = dict[str, Any]
#: Links a typed column already holds, or that the writer makes itself.
_STRUCTURE: Final = frozenset({"linearElement", "lrm", "sampleProduced", "samplingActivity"})
_LITHOLOGY_COLUMNS: Final = ("lithDescription", "classificationSymbol", "legendCode")


@dataclass(frozen=True, slots=True)
class LoadStep:
    """One table to load: its identity columns and the tables to load before it."""

    table: str
    identity: tuple[str, ...]
    depends_on: tuple[str, ...]
    rows: int


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """The order to load a :class:`TableSet` in, so every reference finds its row.

    ``steps`` lists the tables that have rows, parents before children. ``identity`` is
    what makes a row the same row on a second load (use it for an upsert); pyDIGGS writes
    to no database and generates no SQL.
    """

    profile_version: str
    source_sha256: str | None
    steps: tuple[LoadStep, ...]

    def to_dict(self) -> dict[str, Any]:
        """The plan as plain dicts and lists (what ``plan.json`` holds)."""
        return {
            "profile_version": self.profile_version,
            "source_sha256": self.source_sha256,
            "steps": [
                {
                    "table": step.table,
                    "identity": list(step.identity),
                    "depends_on": list(step.depends_on),
                    "rows": step.rows,
                }
                for step in self.steps
            ],
        }


@dataclass(frozen=True, slots=True)
class TableSet:
    """Profile tables made from a model: rows by table, the plan, and the findings.

    ``tables`` maps each table that has rows to its rows, in load order; a row is a dict
    of column name to a string, a number, a boolean or None.
    """

    tables: Mapping[str, list[Row]]
    plan: LoadPlan
    findings: tuple[Finding, ...]
    result_columns: Mapping[str, Mapping[str, Mapping[str, Any]]] = field(default_factory=dict)

    def rows(self, table: str) -> list[Row]:
        """The rows of ``table`` (none when it has none)."""
        return self.tables.get(table, [])

    def descriptor(self) -> dict[str, Any]:
        """The Data Package descriptor of these tables: the profile's, with the result tables."""
        described = profile()
        described["resources"] = [r for r in described["resources"] if r["name"] in self.tables]
        for name, columns in self.result_columns.items():
            described["resources"].append(
                {
                    "name": name,
                    "path": f"{name}.csv",
                    "type": "table",
                    "format": "csv",
                    "mediatype": "text/csv",
                    "encoding": "utf-8",
                    "schema": results_schema({k: dict(v) for k, v in columns.items()}),
                }
            )
        order = {step.table: n for n, step in enumerate(self.plan.steps)}
        described["resources"].sort(key=lambda r: order.get(r["name"], len(order)))
        return described


def _depth_unit(feature: Feature) -> str | None:
    """The unit of a feature's own linear referencing system, read from where it is written.

    The reader lists a reference system in ``model.crs`` only when a location uses it; a
    feature with no depths yet still states the unit of its depth axis.
    """
    stack = [extra for extra in feature.extra if extra.qname.endswith("linearReferencing")]
    while stack:
        extra = stack.pop()
        if extra.qname.rpartition(":")[2] == "units" and extra.text:
            return extra.text
        stack.extend(extra.children)
    return None


def _written(value: float) -> str:
    """A number as the writer writes it: an integer without a decimal point, else ``repr``."""
    if value.is_integer() and abs(value) < 1e15:  # noqa: PLR2004 - exact as an integer
        return str(int(value))
    return repr(value)


def _extra_json(value: Any) -> str:
    """``value`` (a model value, or plain JSON) as JSON text, the same every time."""
    return json.dumps(_encode(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


class _Flattener:
    def __init__(self, model: Model) -> None:
        self.model = model
        self.notes = rep.Notes()
        self.tables: dict[str, list[Row]] = {}
        self.result_columns: dict[str, dict[str, dict[str, Any]]] = {}
        self.extra_count: dict[str, int] = {}
        self.out_links: dict[str, list[Any]] = {}
        for link in model.links:
            self.out_links.setdefault(link.source, []).append(link)
        #: (source, relation, target) of every link a typed column holds.
        self.implied: set[tuple[str, str, str | None]] = set()

    def add(self, table: str, row: Row) -> None:
        self.tables.setdefault(table, []).append(row)

    def num(self, quantity: Quantity | None, table: str) -> float | str | None:
        """A quantity's number (its text when it is not one), noting a changed form."""
        if quantity is None:
            return None
        if quantity.value is None:
            return quantity.text
        if _written(quantity.value) == quantity.text:
            return quantity.value
        if not exact(quantity.text, quantity.value):
            self.notes.add(
                rep.NOT_CARRIED,
                table,
                f"Numbers of {table} have more digits than a 64-bit number holds, so they are"
                " given as written, as text ({times}).",
                row=len(self.tables.get(table, ())),
            )
            return quantity.text
        self.notes.add(
            rep.REFORMATTED,
            table,
            f"Numbers of {table} are given as numbers, so a form such as trailing zeros"
            " is not kept; the values are the same ({times}).",
            row=len(self.tables.get(table, ())),
        )
        return quantity.value

    # --- what every node has --------------------------------------------------------

    def target(self, key: str, relation: str) -> str | None:
        """The first resolved target of ``key``'s ``relation`` links, noted as implied."""
        for link in self.out_links.get(key, ()):
            if link.relation == relation and link.target is not None:
                self.implied.add((key, relation, link.target))
                return str(link.target)
        return None

    def keep(self, node: str | None, path: str, content: Any) -> None:
        """Put content the profile has no column for in the ``extra`` table, as JSON."""
        seq = sum(1 for row in self.tables.get("extra", ()) if row["node"] == node)
        self.add("extra", {"node": node, "seq": seq, "path": path, "content": _extra_json(content)})
        self.extra_count[path] = self.extra_count.get(path, 0) + 1

    def params(self, key: str, params: Mapping[str, Param], scope: str | None = None) -> None:
        for name, value in params.items():
            text, uom, space = value, None, None
            if isinstance(value, Quantity):
                text, uom = value.text, value.uom
            elif isinstance(value, Code):
                text, space = value.value, value.code_space
            self.add(
                "params",
                {
                    "node": key,
                    "scope": scope,
                    "name": name,
                    "value": text,
                    "uom": uom,
                    "code_space": space,
                },
            )

    def header(self, node: Node, *, skip: tuple[str, ...] = ()) -> Row:
        """Write the node's params, remarks, other names and extra; give its name columns."""
        self.params(node.key, {k: v for k, v in node.params.items() if k not in skip})
        for seq, name in enumerate(node.names[1:], start=1):
            self.add("remarks", {"node": node.key, "kind": "name", "seq": seq, "text": name})
        for seq, remark in enumerate(node.remarks):
            self.add("remarks", {"node": node.key, "kind": "remark", "seq": seq, "text": remark})
        for extra in node.extra:
            if not _helper(node, extra):
                self.keep(node.key, extra.qname, extra)
        return {"key": node.key, "name": node.name, "description": node.description}

    def interval(
        self, interval: Interval | None, lrs: str | None, key: str, path: str, table: str
    ) -> Row:
        """``top``, ``base`` and ``depth_uom`` of an interval along the feature's own depths."""
        empty: Row = {"top": None, "base": None, "depth_uom": None}
        if interval is None:
            return empty
        if lrs is not None and interval.lrs not in (None, lrs):
            self.keep(key, path, interval)
            return empty
        top, base = interval.top, interval.base
        uom = next((q.uom for q in (top, base) if q is not None and q.uom), None)
        return {"top": self.num(top, table), "base": self.num(base, table), "depth_uom": uom}

    @staticmethod
    def span(span: TimeSpan | None) -> Row:
        return {
            "start": None if span is None else span.start,
            "end": None if span is None else span.end,
        }

    # --- nodes ----------------------------------------------------------------------

    def run(self) -> TableSet:
        model = self.model
        if model.document is not None:
            self.header(model.document)
            self.add(
                "document",
                {
                    "creation_date": model.document.creation_date,
                    "description": model.document.description,
                },
            )
        for project in model.projects:
            row = self.header(project)
            if project.client is not None:
                self.keep(project.key, "client", project.client)
            self.add("projects", {**row, "locality": project.locality})
        lrs_of: dict[str, str | None] = {}
        for feature in model.features:
            lrs_of[feature.key] = feature.lrs
            self.feature(feature)
        for activity in model.sampling_activities:
            lrs_of[activity.key] = lrs_of.get(activity.feature or "")
            row = self.header(activity)
            self.target(activity.key, "samplingFeature")
            self.add(
                "sampling_activities",
                {
                    **row,
                    "feature": activity.feature,
                    **self.interval(
                        activity.interval,
                        lrs_of[activity.key],
                        activity.key,
                        "samplingLocation",
                        "sampling_activities",
                    ),
                    **self.span(activity.date),
                    "method": None if activity.method is None else activity.method.value,
                    "method_code_space": None
                    if activity.method is None
                    else activity.method.code_space,
                    "recovery": self.num(activity.recovery, "sampling_activities"),
                    "recovery_uom": None if activity.recovery is None else activity.recovery.uom,
                },
            )
        activities = {a.key: a for a in model.sampling_activities}
        for sample in model.samples:
            self.sample(sample, activities, lrs_of)
        for observation in model.observations:
            lrs = lrs_of.get(observation.feature or "")
            row = self.header(observation)
            self.target(observation.key, "samplingFeature")
            self.add(
                "observations",
                {
                    "key": observation.key,
                    "feature": observation.feature,
                    "element": observation.element,
                    "name": row["name"],
                    "description": row["description"],
                },
            )
            for layer in observation.layers:
                self.layer(layer, observation.key, observation.feature, lrs)
        for measurement in model.measurements:
            self.measurement(measurement, lrs_of.get(measurement.feature or ""))
        body = model.to_dict()
        for name in ("associates", "others"):
            for node in body.get(name, ()):
                self.keep(None, str(node.get("element")), node)
        self.project_links()
        self.links()
        return self.finish()

    def project_links(self) -> None:
        """A ``project`` link is implied when it is the project the node's feature names."""
        features = {f.key: self.target(f.key, "project") for f in self.model.features}
        with_feature: list[tuple[str, str | None]] = [
            *((a.key, a.feature) for a in self.model.sampling_activities),
            *((o.key, o.feature) for o in self.model.observations),
            *((m.key, m.feature) for m in self.model.measurements),
        ]
        for key, feature in with_feature:
            own = features.get(feature or "")
            if own is not None:
                self.implied.add((key, "project", own))
        activity_feature = {a.key: a.feature for a in self.model.sampling_activities}
        for sample in self.model.samples:
            own = features.get(activity_feature.get(sample.sampling_activity or "") or "")
            if own is not None:
                self.implied.add((sample.key, "project", own))

    def links(self) -> None:
        for link in self.model.links:
            if link.relation in _STRUCTURE:
                continue
            if (link.source, link.relation, link.target) in self.implied:
                continue
            self.add(
                "links",
                {
                    "source": link.source,
                    "relation": link.relation,
                    "target": link.target,
                    "href": None if link.target is not None else link.href,
                },
            )

    def feature(self, feature: Feature) -> None:
        row = self.header(feature)
        place = feature.location
        own = place is not None and place.crs == feature.lrs and place.dim == 1
        coordinates = () if place is None or own else place.coordinates
        if len(coordinates) > 3:  # noqa: PLR2004 - x, y and z
            self.keep(feature.key, "referencePoint", place)
        if feature.centerline is not None and len(feature.centerline) > 1:
            self.keep(feature.key, "centerLine", list(feature.centerline))
        ref = self.model.crs.get(feature.lrs or "")
        depth = feature.total_depth
        self.add(
            "features",
            {
                "key": feature.key,
                "project": self.target(feature.key, "project"),
                "element": feature.element,
                "name": row["name"],
                "description": row["description"],
                "x": coordinates[0] if coordinates else None,
                "y": coordinates[1] if len(coordinates) > 1 else None,
                "z": coordinates[2] if len(coordinates) > 2 else None,  # noqa: PLR2004 - z
                "crs": None if place is None or own else place.crs,
                "total_depth": self.num(depth, "features"),
                "total_depth_uom": None if depth is None else depth.uom,
                "depth_uom": _depth_unit(feature) if ref is None else ref.unit,
                **self.span(feature.when_constructed),
                "parent": self.target(feature.key, "samplingFeature"),
            },
        )
        for component in (*feature.construction, *feature.water_strikes):
            self.component(component, feature.key, None, feature.lrs)

    def sample(self, sample: Any, activities: Mapping[str, Any], lrs_of: dict[str, Any]) -> None:
        row = self.header(sample, skip=("type",))
        kind = sample.params.get("type")
        activity = activities.get(sample.sampling_activity or "")
        feature = sample.feature if activity is None else activity.feature
        lrs = lrs_of.get(feature or "")
        lrs_of[sample.key] = lrs
        interval = sample.interval if activity is None else activity.interval
        self.add(
            "samples",
            {
                "key": sample.key,
                "sampling_activity": sample.sampling_activity,
                "name": row["name"],
                "description": row["description"],
                "type": kind.value if isinstance(kind, Code) else kind,
                "type_code_space": kind.code_space if isinstance(kind, Code) else None,
                "classification": None if sample.type is None else sample.type.value,
                "feature": feature,
                **self.interval(interval, lrs, sample.key, "samplingLocation", "samples"),
                **self.span(None if activity is None else activity.date),
            },
        )

    def layer(
        self, layer: LithologyLayer, system: str, feature: str | None, lrs: str | None
    ) -> None:
        row = self.header(layer)
        primary = next((c for c in layer.components if c.role == "primaryLithology"), None)
        params: Mapping[str, Param] = {} if primary is None else primary.params
        symbol = params.get("classificationSymbol")
        legend = params.get("legendCode")
        self.add(
            "layers",
            {
                "key": layer.key,
                "observation": system,
                "feature": feature,
                **self.interval(layer.interval, lrs, layer.key, "location", "layers"),
                "description": layer.description,
                "classification": symbol.value if isinstance(symbol, Code) else symbol,
                "classification_code_space": symbol.code_space
                if isinstance(symbol, Code)
                else None,
                "legend_code": legend.value if isinstance(legend, Code) else legend,
                "geologic_unit": layer.geologic_unit,
            },
        )
        del row
        for component in layer.components:
            plain = (
                component is primary
                and not component.components
                and not component.extra
                and not component.remarks
                and component.name is None
                and all(name in _LITHOLOGY_COLUMNS for name in component.params)
                and not isinstance(legend, Code)
            )
            if not plain:
                skip = _LITHOLOGY_COLUMNS if component is primary else ()
                self.component(component, layer.key, None, lrs, skip=skip)

    def component(
        self,
        component: Component,
        holder: str,
        parent: str | None,
        lrs: str | None,
        *,
        skip: tuple[str, ...] = (),
    ) -> None:
        row = self.header(component, skip=skip)
        place = component.location
        along = place is not None and place.dim == 1 and place.crs == lrs and lrs is not None
        if place is not None and not along:
            self.keep(component.key, "location", place)
        self.add(
            "components",
            {
                "key": component.key,
                "holder": holder,
                "parent": parent,
                "role": component.role,
                "element": component.element,
                "name": row["name"],
                "description": row["description"],
                **self.interval(component.interval, lrs, component.key, "interval", "components"),
                "depth": place.coordinates[0] if along and place is not None else None,
                **self.span(component.time),
            },
        )
        for child in component.components:
            self.component(child, holder, component.key, lrs)

    def measurement(self, measurement: Measurement, lrs: str | None) -> None:
        row = self.header(measurement)
        procedure = measurement.procedure
        self.target(measurement.key, "samplingFeature")
        sample = measurement.samples[0] if measurement.samples else None
        if sample is not None:
            self.implied.add((measurement.key, "sample", sample))
        time = measurement.result_time
        if time is not None and time.end is not None:
            self.keep(measurement.key, "resultTime", time)
        out: Row = {
            "key": measurement.key,
            "feature": measurement.feature,
            "procedure": None if procedure is None else procedure.type,
            "name": row["name"],
            "description": row["description"],
            "sample": sample,
            "result_time": None if time is None else time.start,
            "depth": None,
            "top": None,
            "base": None,
            "depth_uom": None,
        }
        if measurement.element != "Test":
            self.keep(measurement.key, "element", measurement.element)
        if procedure is not None:
            self.params(measurement.key, procedure.params, "procedure")
            for component in procedure.components:
                self.component(component, measurement.key, None, lrs)
            for extra in procedure.extra:
                self.keep(measurement.key, f"procedure/{extra.qname}", extra)
        for number, table in enumerate(measurement.results):
            if number or procedure is None:
                self.keep(measurement.key, f"results[{number}]", table)
                continue
            out.update(self.results(measurement.key, procedure.type, table, lrs))
        self.add("measurements", out)

    def place(self, key: str, table: ResultTable, lrs: str | None) -> Row:
        """The one depth or interval the whole table was measured at, as columns."""
        place: Row = {}
        location = table.location
        if location is not None:
            if location.dim == 1 and location.crs == lrs and lrs is not None:
                ref = self.model.crs.get(lrs)
                place = {
                    "depth": location.coordinates[0],
                    "depth_uom": None if ref is None else ref.unit,
                }
            else:
                self.keep(key, "results[0]/location", location)
        if table.extent is not None:
            place = self.interval(table.extent, lrs, key, "results[0]/extent", "measurements")
        return place

    def results(self, key: str, procedure: str, table: ResultTable, lrs: str | None) -> Row:
        """Write the rows of ``table``; give the test's own ``depth`` or ``top`` and ``base``."""
        place = self.place(key, table, lrs)
        index = table.domain.index
        names = [column.name for column in index]
        indexed = names in (["position"], ["top", "base"]) and table.domain.crs in (None, lrs)
        if index and not indexed:
            self.keep(key, "results[0]/index", {"domain": table.domain})
        name = results_table_name(procedure)
        known = self.result_columns.setdefault(name, {})
        cells: dict[str, tuple[Any, ...]] = {}
        if indexed:
            for column in index:
                cells[column.name] = column.values
                known.setdefault(column.name, {"type": "number"})
            unit = next((c.uom for c in index if c.uom), None)
            if unit is not None:
                place = {**place, "depth_uom": unit}
        for column in table.columns:
            label = column.key or column.name
            while label in cells or label in RESULT_KEYS or label in RESULT_PLACES:
                label += "_value"
            cells[label] = column.values
            self.column_facts(procedure, key, label, column)
            facts: dict[str, Any] = {"type": _schema_type(column.type)}
            if column.uom is not None:
                facts["unit"] = column.uom
            if column.key is not None:
                facts["property"] = column.key
            known.setdefault(label, facts)
        for number in range(table.row_count):
            self.add(
                name,
                {
                    "measurement": key,
                    "row": number,
                    **{label: values[number] for label, values in cells.items()},
                },
            )
        return place

    def column_facts(self, procedure: str, key: str, label: str, column: Column) -> None:
        """A ``result_columns`` row for what the column's name alone does not say."""
        prop = column.key
        default_definition = None if prop is None else f"{PROPERTIES_URL}#{prop}"
        facts: Row = {
            "procedure": procedure,
            "measurement": key,
            "column": label,
            "name": None if column.name == label else column.name,
            "property": None if prop == label else prop,
            "definition": None if column.definition == default_definition else column.definition,
            "uom": column.uom,
            "type": None if column.type == "number" else column.type,
            "null_value": column.null_value,
        }
        if any(facts[name] is not None for name in facts if name not in _FACT_KEYS):
            self.add("result_columns", facts)

    # --- the set --------------------------------------------------------------------

    def merge_columns(self) -> None:
        """One ``result_columns`` row per procedure when every test of it agrees."""
        rows = self.tables.get("result_columns")
        if not rows:
            return
        tests: dict[str, set[str]] = {}
        for measurement in self.model.measurements:
            if measurement.procedure is not None:
                tests.setdefault(measurement.procedure.type, set()).add(measurement.key)
        groups: dict[tuple[str, str], list[Row]] = {}
        for row in rows:
            groups.setdefault((row["procedure"], row["column"]), []).append(row)
        having = self.users()
        merged: list[Row] = []
        for (procedure, column), group in groups.items():
            facts = [{k: v for k, v in row.items() if k != "measurement"} for row in group]
            users = having.get((procedure, column), set())
            if all(f == facts[0] for f in facts) and {r["measurement"] for r in group} >= users:
                merged.append({**group[0], "measurement": None})
            else:
                merged.extend(group)
        self.tables["result_columns"] = merged

    def users(self) -> dict[tuple[str, str], set[str]]:
        """(procedure, column) -> the tests that have a value in that result column."""
        having: dict[tuple[str, str], set[str]] = {}
        for name, found in self.tables.items():
            if not name.startswith(RESULTS_PREFIX):
                continue
            procedure = name[len(RESULTS_PREFIX) :]
            for row in found:
                for column, value in row.items():
                    if value is not None and column not in RESULT_KEYS:
                        having.setdefault((procedure, column), set()).add(row["measurement"])
        return having

    def finish(self) -> TableSet:
        self.merge_columns()
        for path, count in self.extra_count.items():
            self.notes.add(
                rep.NOT_CARRIED,
                "extra",
                f"DIGGS content the profile has no column for ({rep.shown(path)}) was kept as"
                f" JSON in the extra table ({count} {'piece' if count == 1 else 'pieces'}).",
            )
        if not self.model.complete:
            self.notes.add(
                rep.INCOMPLETE,
                "document",
                "The DIGGS file was only partly read, so the tables are incomplete.",
            )
        results = sorted(name for name in self.tables if name not in TABLES)
        order: list[str] = []
        for name in TABLES:
            order.append(name)
            if name == "result_columns":
                order.extend(results)
        ordered = {name: self.tables[name] for name in order if name in self.tables}
        steps = tuple(
            LoadStep(
                table=name,
                identity=_identity(name),
                depends_on=_depends(name, ordered),
                rows=len(rows),
            )
            for name, rows in ordered.items()
        )
        source = self.model.source.sha256 or None
        return TableSet(
            tables=ordered,
            plan=LoadPlan(profile_version=PROFILE_VERSION, source_sha256=source, steps=steps),
            findings=self.notes.findings(),
            result_columns={k: v for k, v in self.result_columns.items() if k in ordered},
        )


_FACT_KEYS: Final = frozenset({"procedure", "measurement", "column"})


def _schema_type(kind: str) -> str:
    return {"number": "number", "boolean": "boolean"}.get(kind, "string")


def _identity(name: str) -> tuple[str, ...]:
    if name in TABLES:
        return TABLES[name].primary_key
    return RESULT_KEYS


def _depends(name: str, present: Mapping[str, Any]) -> tuple[str, ...]:
    if name not in TABLES:
        return ("measurements",)
    found: list[str] = []
    for column in TABLES[name].fields:
        if column.references == "*":
            found.extend(
                t
                for t in present
                if t in TABLES and TABLES[t].primary_key == ("key",) and t != name
            )
        elif column.references is not None and column.references != name:
            found.append(column.references)
    return tuple(dict.fromkeys(t for t in found if t in present))


def from_model(model: Model) -> TableSet:
    """The tables of ``model``: every value in a typed column, in ``params`` or in ``extra``.

    Use it on what :func:`pydiggs.read` returns to load a DIGGS file into a database:
    load the tables in the order of ``plan.steps``.

    Args:
        model: The model, as read from a file or built by :func:`to_model`.

    Returns:
        The tables that have rows, their load plan, and a finding for each kind of
        content that only the ``extra`` table could hold.

    Examples:
        >>> import pydiggs
        >>> from pydiggs import tables
        >>> made = tables.to_model(
        ...     {"features": [{"key": "BH-1", "name": "BH-1", "depth_uom": "ft"}]}
        ... )
        >>> back = tables.from_model(pydiggs.read_bytes(pydiggs.write_bytes(made.model)))
        >>> [step.table for step in back.plan.steps]
        ['document', 'projects', 'features']
        >>> back.rows("features")[0]["depth_uom"]
        'ft'
    """
    return _Flattener(model).run()
