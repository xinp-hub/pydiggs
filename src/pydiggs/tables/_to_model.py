"""Profile tables to a :class:`~pydiggs.model.Model` the public writer accepts.

The model is built the way the AGS4 converter builds one: each feature gets a linear
referencing system for its depths, each node its ``project`` link, each sample a sampling
activity. A value is carried as given: a number keeps the text it was written with, a
unit is never converted. A row that cannot be written is left out and named by a finding.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydiggs._writer import write_model
from pydiggs._writer._layout import Layout, load_layout
from pydiggs.convert._mapping import PROPERTIES_URL
from pydiggs.convert._to_model import CLASSIFICATION_DEFAULT, PROJECT_DEFAULT, components_of
from pydiggs.convert._values import IdMaker, parse_number
from pydiggs.errors import ModelWriteError, TablesError
from pydiggs.findings.model import Finding
from pydiggs.model import (
    Code,
    Column,
    Component,
    CrsRef,
    DocumentInfo,
    Domain,
    Feature,
    Interval,
    Link,
    LithologyLayer,
    Measurement,
    Model,
    Node,
    Observation,
    Param,
    Position,
    Procedure,
    Project,
    Provenance,
    Quantity,
    ResultTable,
    Sample,
    SamplingActivity,
    SourceInfo,
    TimeSpan,
)
from pydiggs.tables import _report as rep
from pydiggs.tables._profile import RESULT_KEYS, RESULT_PLACES, RESULTS_PREFIX, TABLES

__all__ = ["TablesResult", "to_model"]

Row = Mapping[str, Any]
#: Depth units DIGGS 3.0.0 names a linear referencing method for.
_DEPTH_UNITS: Final = ("m", "ft", "ft[US]", "cm", "in")
_DEFAULT_DEPTH_UNIT: Final = "m"
_COLUMN_TYPES: Final = ("number", "string", "boolean", "datetime")
#: References a column holds or the model makes itself; a ``links`` row cannot add one.
_MADE_LINKS: Final = frozenset(
    {
        "project",
        "samplingFeature",
        "samplingActivity",
        "sampleProduced",
        "sample",
        "specimen",
        "linearElement",
        "lrm",
        "referencePoint",
    }
)
_TRUE: Final = frozenset({"true", "1"})
_FALSE: Final = frozenset({"false", "0"})


@dataclass(frozen=True, slots=True)
class TablesResult:
    """A model built from tables, and what could not be carried into it.

    ``findings`` names every row and value that was left out or assumed; ``keys`` maps
    each key of the tables to the key (and ``gml:id``) its node has in the model.
    """

    model: Model
    findings: tuple[Finding, ...]
    keys: Mapping[str, str]

    @property
    def lossless(self) -> bool:
        """True when nothing was left out: no finding of severity warning or error."""
        return all(finding.severity.rank == 0 for finding in self.findings)


def _text(value: object) -> str | None:
    """A cell as text: None when it is missing or blank."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return None  # how pandas writes a missing cell
        return _written(value)
    text = str(value).strip()
    return text or None


def _written(value: float) -> str:
    """A number as text: an integer without a decimal point, else its shortest form."""
    if value.is_integer() and abs(value) < 1e15:  # noqa: PLR2004 - exact as an integer
        return str(int(value))
    return repr(value)


def _node(cls: type[Any], key: str, element: str, kind: str, **fields: Any) -> Any:
    names = tuple(fields.pop("names", ()))
    return cls(
        key=key,
        gml_id=fields.pop("gml_id", key),
        element=element,
        kind=kind,
        name=names[0] if names else None,
        names=names,
        description=fields.pop("description", None),
        remarks=tuple(fields.pop("remarks", ())),
        params=fields.pop("params", {}),
        provenance=Provenance(xpath=""),
        extra=(),
        **fields,
    )


def _kind(element: str) -> str:
    out = []
    for i, ch in enumerate(element):
        if ch.isupper() and i and (element[i - 1].islower() or element[i - 1].isdigit()):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


#: One result row: its table, its index in it, and the row.
_Entry = tuple[str, int, Row]


@dataclass(frozen=True, slots=True)
class _Test:
    """A ``measurements`` row that names a feature and a procedure."""

    index: int
    row: Row
    procedure: str
    given: str
    feature: str
    lrs: str
    unit: str


class _Builder:
    """One conversion: the tables, the nodes made so far and the notes."""

    def __init__(self, tables: Mapping[str, Iterable[Row]]) -> None:
        self.layout: Layout = load_layout("3.0.0")
        self.notes = rep.Notes()
        self.ids = IdMaker()
        self.tables: dict[str, list[Row]] = {}
        for name, rows in tables.items():
            if name not in TABLES and not name.startswith(RESULTS_PREFIX):
                known = ", ".join(TABLES)
                raise TablesError(
                    f"{rep.shown(name)!r} is not a table of the profile ({known}, or"
                    f" {RESULTS_PREFIX}<procedure>)"
                )
            self.tables[name] = [row for row in rows if any(_text(v) for v in row.values())]
        self._check_columns()
        #: Key as given in the tables -> key of the node in the model.
        self.keys: dict[str, str] = {}
        #: Node key -> (lrs, depth unit) of the feature its depths are measured along.
        self.lrs: dict[str, tuple[str, str]] = {}
        self.links: list[Link] = []
        self.crs: dict[str, CrsRef] = {}
        self.params: dict[tuple[str, str], dict[str, Param]] = {}
        self.names: dict[str, list[str]] = {}
        self.remarks: dict[str, list[str]] = {}
        #: Key (as given) of a holder -> its components, until the holder is built.
        self.pending: dict[str, list[Component]] = {}
        #: Feature key -> the key of its project; what belongs to the feature shares it.
        self.project_of: dict[str, str] = {}
        #: Model key -> the table its row is in.
        self.origin: dict[str, str] = {}
        #: Keys (as given) of the rows that became nodes.
        self.claimed: set[str] = set()

    # --- cells ----------------------------------------------------------------------

    def _check_columns(self) -> None:
        for name, rows in self.tables.items():
            if name not in TABLES:
                if rows and "measurement" not in rows[0]:
                    raise TablesError(f"table {name} has no `measurement` column")
                continue
            known = set(TABLES[name].names())
            for field in TABLES[name].fields:
                if field.required and rows and not any(field.name in row for row in rows):
                    raise TablesError(f"table {name} has no `{field.name}` column")
            unknown: dict[str, int] = {}
            for row in rows:
                for column, value in row.items():
                    if column not in known and _text(value) is not None:
                        unknown[column] = unknown.get(column, 0) + 1
            for column, count in unknown.items():
                rows_text = "1 row" if count == 1 else f"{count} rows"
                self.notes.add(
                    rep.UNMAPPED_COLUMN,
                    name,
                    f"Column {rep.shown(column)!r} is not a column of table {name}; its values"
                    f" were not converted ({rows_text}).",
                )

    def number(self, table: str, index: int, row: Row, column: str) -> tuple[float, str] | None:
        """The number in ``column`` and the text it is written with, or None."""
        value = row.get(column)
        if isinstance(value, float) and math.isnan(value):
            return None  # how pandas writes a missing cell
        if isinstance(value, int | float) and not isinstance(value, bool):
            if math.isfinite(value):
                return float(value), _written(float(value))
            text: str | None = str(value)
        else:
            text = _text(value)
            if text is None:
                return None
        found = None if isinstance(value, bool | int | float) else parse_number(str(text))
        if found is None:
            self.notes.add(
                rep.UNPARSED,
                table,
                f"Values of {column} in {table} are not numbers; not converted ({{times}}).",
                row=index,
                column=column,
            )
            return None
        return found, str(text)

    def quantity(
        self, table: str, index: int, row: Row, column: str, uom_column: str
    ) -> Quantity | None:
        found = self.number(table, index, row, column)
        if found is None:
            return None
        return Quantity(text=found[1], value=found[0], uom=_text(row.get(uom_column)))

    def code(self, row: Row, column: str) -> Code | None:
        value = _text(row.get(column))
        if value is None:
            return None
        return Code(value=value, code_space=_text(row.get(f"{column}_code_space")))

    def span(self, row: Row) -> TimeSpan | None:
        start, end = _text(row.get("start")), _text(row.get("end"))
        return None if start is None and end is None else TimeSpan(start=start, end=end)

    # --- keys and references --------------------------------------------------------

    def make_key(self, table: str, index: int, row: Row) -> str | None:
        """The model key of a row: its ``key`` as a valid, unique XML id."""
        given = _text(row.get("key"))
        if given is None:
            self.notes.add(
                rep.INCOMPLETE,
                table,
                f"Rows of {table} have no key; not converted ({{times}}).",
                row=index,
            )
            return None
        if given in self.keys:
            self.notes.add(
                rep.DUPLICATE_KEY,
                table,
                f"Rows of {table} repeat a key another row already has, and a key must be"
                " unique across all tables; not converted ({times}).",
                row=index,
                column="key",
            )
            return None
        made = self.ids.make(given)
        self.keys[given] = made
        self.origin[made] = table
        if made != given:
            self.notes.add(
                rep.KEY_REWRITTEN,
                table,
                f"Keys of {table} are not valid XML ids, so the gml:id written differs from"
                " the key; the result's `keys` gives each one ({times}).",
                row=index,
                column="key",
            )
        return made

    def ref(self, table: str, index: int, row: Row, column: str) -> str | None:
        """The model key ``column`` names; an unknown one is an orphan row."""
        given = _text(row.get(column))
        if given is None:
            return None
        found = self.keys.get(given)
        if found is None:
            self.notes.add(
                rep.ORPHAN_ROW,
                table,
                f"Rows of {table} name a {column} that no converted row has; not converted"
                " ({times}).",
                row=index,
                column=column,
            )
        return found

    def need(self, table: str, index: int, what: str) -> None:
        self.notes.add(
            rep.INCOMPLETE,
            table,
            f"Rows of {table} lack {what}, which DIGGS requires; not converted ({{times}}).",
            row=index,
        )

    def link(self, source: str, relation: str, target: str) -> None:
        self.links.append(
            Link(source=source, relation=relation, target=target, href=f"#{target}", resolved=True)
        )

    def header(self, key: str, row: Row, *, name: bool = True) -> dict[str, Any]:
        """The fields every node takes from its row, its remarks and its params."""
        names = [] if not name or _text(row.get("name")) is None else [str(_text(row.get("name")))]
        names += [n for n in self.names.get(key, ()) if n not in names]
        return {
            "names": names,
            "description": _text(row.get("description")),
            "remarks": self.remarks.get(key, ()),
            "params": dict(self.params.get((key, ""), {})),
        }

    # --- auxiliary tables -----------------------------------------------------------

    def read_params(self) -> None:
        """Index ``params`` and ``remarks`` by the key their node has in the tables."""
        for index, row in enumerate(self.tables.get("params", ())):
            node, name = _text(row.get("node")), _text(row.get("name"))
            value = _text(row.get("value"))
            if node is None or name is None or value is None:
                self.need("params", index, "a node, a name or a value")
                continue
            uom, space = _text(row.get("uom")), _text(row.get("code_space"))
            param: Param = value
            if uom is not None:
                parsed = parse_number(value)
                param = Quantity(text=value, value=parsed, uom=uom)
            elif space is not None:
                param = Code(value=value, code_space=space)
            scope = _text(row.get("scope")) or ""
            self.params.setdefault((node, scope), {})[name] = param
        seen: list[tuple[int, int, str, str, str]] = []
        for index, row in enumerate(self.tables.get("remarks", ())):
            node, kind, text = (_text(row.get(c)) for c in ("node", "kind", "text"))
            if node is None or text is None or kind not in ("remark", "name"):
                self.need("remarks", index, "a node, a kind (`remark` or `name`) or a text")
                continue
            order = self.number("remarks", index, row, "seq")
            seen.append((int(order[0]) if order else index, index, node, kind, text))
        for _order, _index, node, kind, text in sorted(seen):
            (self.remarks if kind == "remark" else self.names).setdefault(node, []).append(text)

    def orphans(self) -> None:
        """Name the rows of ``params`` and ``remarks`` whose node was not converted."""
        left = [("params", node) for node, _scope in self.params]
        left += [("remarks", node) for node in (*self.remarks, *self.names)]
        for table, node in left:
            if node not in self.claimed:
                self.notes.add(
                    rep.ORPHAN_ROW,
                    table,
                    f"Rows of {table} name a node that no converted row has; not converted"
                    " ({times}, counted by node).",
                    count=True,
                )

    # --- nodes ----------------------------------------------------------------------

    def document(self) -> DocumentInfo:
        rows = self.tables.get("document", [])
        row: Row = rows[0] if rows else {}
        for index in range(1, len(rows)):
            self.notes.add(
                rep.INCOMPLETE,
                "document",
                "The document table has more than one row; only the first was converted ({times}).",
                row=index,
            )
        date = _text(row.get("creation_date"))
        if date is None:
            self.notes.add(
                rep.ASSUMED,
                "document",
                "DIGGS requires a creation date and the document table gives none; it was"
                ' written as unknown (indeterminatePosition="unknown").',
            )
        key = self.ids.make("document")
        return _node(  # type: ignore[no-any-return]
            DocumentInfo,
            key,
            "DocumentInformation",
            "document_info",
            gml_id=None,
            description=_text(row.get("description")),
            creation_date=date,
        )

    def projects(self) -> list[Project]:
        out: list[Project] = []
        for index, row in enumerate(self.tables.get("projects", ())):
            given = _text(row.get("key"))
            if _text(row.get("name")) is None and given is not None:
                row = {**row, "name": given}  # noqa: PLW2901 - DIGGS requires a name
            key = self.make_key("projects", index, row)
            if key is None:
                continue
            self.claim(given)
            out.append(
                _node(
                    Project,
                    key,
                    "Project",
                    "project",
                    **self.header(str(given), row),
                    locality=_text(row.get("locality")),
                )
            )
        return out

    def claim(self, given: str | None) -> None:
        if given is not None:
            self.claimed.add(given)

    def default_project(self, projects: list[Project], table: str) -> str:
        """The key of the project a row with no ``project`` belongs to."""
        if not projects:
            key = self.ids.make("project")
            projects.append(_node(Project, key, "Project", "project", names=(PROJECT_DEFAULT,)))
            self.notes.add(
                rep.ASSUMED,
                table,
                "The tables have no project; DIGGS requires every feature, sample and test to"
                f" name its project, so a project named {PROJECT_DEFAULT!r} was written.",
            )
        return projects[0].key

    def features(self, projects: list[Project]) -> list[Feature]:
        out: list[Feature] = []
        rows = list(enumerate(self.tables.get("features", ())))
        # A feature that sits in another (a well in a borehole) comes after its parent.
        rows.sort(key=lambda pair: _text(pair[1].get("parent")) is not None)
        for index, row in rows:
            given = _text(row.get("key"))
            if _text(row.get("name")) is None and given is not None:
                row = {**row, "name": given}  # noqa: PLW2901 - DIGGS requires a name
            project = self.ref("features", index, row, "project")
            if _text(row.get("project")) is not None and project is None:
                continue
            parent = self.ref("features", index, row, "parent")
            if _text(row.get("parent")) is not None and parent is None:
                continue
            element = _text(row.get("element")) or "Borehole"
            if element not in self.layout.elements:
                self.need("features", index, f"a DIGGS element ({rep.shown(element)!r} is not one)")
                continue
            unit = _text(row.get("depth_uom")) or _text(row.get("total_depth_uom"))
            if unit is None:
                unit = _DEFAULT_DEPTH_UNIT
                self.notes.add(
                    rep.ASSUMED,
                    "features",
                    "Features with no depth_uom: their depths were written in metres ({times}).",
                    row=index,
                )
            if unit not in _DEPTH_UNITS:
                self.notes.add(
                    rep.UNKNOWN_UNIT,
                    "features",
                    f"Features whose depth unit is {rep.shown(unit)!r}: DIGGS 3.0.0 measures"
                    f" depth along a feature in {', '.join(_DEPTH_UNITS)}; not converted"
                    " ({times}).",
                    row=index,
                    column="depth_uom",
                )
                continue
            key = self.make_key("features", index, row)
            if key is None:
                continue
            self.claim(given)
            lrs = f"#{self.ids.make(f'{key}-lrs')}"
            self.lrs[key] = (lrs, unit)
            self.crs[lrs] = CrsRef(srs_name=lrs, form="local", unit=unit, known=True, local=lrs[1:])
            location = self.location("features", index, row, lrs)
            depth = self.quantity("features", index, row, "total_depth", "total_depth_uom")
            if depth is not None and (depth.value or 0.0) <= 0:
                self.notes.add(
                    rep.UNPARSED,
                    "features",
                    "Values of total_depth in features are not positive, and DIGGS requires a"
                    " positive total depth; not converted ({times}).",
                    row=index,
                    column="total_depth",
                )
                depth = None
            if depth is not None and depth.uom is None:
                depth = Quantity(text=depth.text, value=depth.value, uom=unit)
            out.append(
                _node(
                    Feature,
                    key,
                    element,
                    _kind(element),
                    **self.header(str(given), row),
                    location=location,
                    centerline=(location,),
                    total_depth=depth,
                    lrs=lrs,
                    when_constructed=self.span(row),
                    parent=parent,
                )
            )
            if parent is not None:
                self.link(key, "samplingFeature", parent)
            self.project_of[key] = project or self.default_project(projects, "features")
            self.link(key, "project", self.project_of[key])
        return out

    def location(self, table: str, index: int, row: Row, lrs: str) -> Position:
        found = [self.number(table, index, row, axis) for axis in ("x", "y", "z")]
        if found[0] is not None and found[1] is not None:
            coordinates = tuple(pair[0] for pair in found if pair is not None)
            if _text(row.get("crs")) is None:
                self.notes.add(
                    rep.NO_CRS,
                    table,
                    "Features with x and y but no crs: the coordinates were written without a"
                    " coordinate reference system, which DIGGS expects every location to name"
                    " ({times}).",
                    row=index,
                    column="crs",
                )
            return Position(
                coordinates=coordinates, dim=len(coordinates), crs=_text(row.get("crs"))
            )
        self.notes.add(
            rep.ASSUMED,
            table,
            "Features with no x and y: DIGGS requires a location, so the top of the hole"
            " along its own depth axis was written ({times}).",
            row=index,
        )
        return Position(coordinates=(0.0,), dim=1, crs=lrs)

    def interval(
        self, table: str, index: int, row: Row, holder: str, *, both: bool = False
    ) -> Interval | None:
        """The row's ``top`` and ``base`` along the feature of ``holder``, when it has both."""
        lrs, unit = self.lrs[holder]
        top = self.number(table, index, row, "top")
        base = self.number(table, index, row, "base")
        if top is None and base is None:
            return None
        if not self.depth_unit(table, index, row, unit):
            return None
        if top is None or base is None:
            if both:
                return None
            top = base = top or base
            self.notes.add(
                rep.ASSUMED,
                table,
                f"Rows of {table} with only a top or only a base: the interval was written"
                " from that depth to the same depth ({times}).",
                row=index,
            )
        assert top is not None and base is not None  # noqa: S101 - narrowed above
        return Interval(
            top=Quantity(text=top[1], value=top[0], uom=unit),
            base=Quantity(text=base[1], value=base[0], uom=unit),
            lrs=lrs,
        )

    def depth_unit(self, table: str, index: int, row: Row, unit: str) -> bool:
        """True when the row's depths are in the feature's unit (no depth is converted)."""
        given = _text(row.get("depth_uom"))
        if given in (None, unit):
            return True
        self.notes.add(
            rep.UNKNOWN_UNIT,
            table,
            f"Depths of {table} in {rep.shown(given)!r} along a feature measured in {unit!r}: a"
            " depth is never converted from one unit to another; not converted ({times}).",
            row=index,
            column="depth_uom",
        )
        return False

    def activities(self) -> tuple[list[SamplingActivity], list[Sample]]:
        activities: list[SamplingActivity] = []
        by_key: dict[str, SamplingActivity] = {}

        def make(table: str, index: int, row: Row, key: str, given: str | None) -> bool:
            feature = self.ref(table, index, row, "feature")
            if feature is None:
                if _text(row.get("feature")) is None:
                    self.need(table, index, "a feature")
                return False
            self.lrs[key] = self.lrs[feature]
            recovery = self.quantity(table, index, row, "recovery", "recovery_uom")
            header = (
                self.header(str(given), row)
                if given is not None
                else {"names": (), "description": None, "remarks": (), "params": {}}
            )
            header["params"].setdefault("activityType", "collect")
            activity = _node(
                SamplingActivity,
                key,
                "SamplingActivity",
                "sampling_activity",
                **header,
                feature=feature,
                interval=self.interval(table, index, row, key),
                method=self.code(row, "method"),
                recovery=recovery,
                date=self.span(row),
            )
            activities.append(activity)
            by_key[key] = activity
            self.link(key, "project", self.project_of[feature])
            self.link(key, "samplingFeature", feature)
            return True

        for index, row in enumerate(self.tables.get("sampling_activities", ())):
            key = self.make_key("sampling_activities", index, row)
            if key is not None and make("sampling_activities", index, row, key, _text(row["key"])):
                self.claim(_text(row.get("key")))
        samples: list[Sample] = []
        for index, row in enumerate(self.tables.get("samples", ())):
            given = _text(row.get("key"))
            if _text(row.get("name")) is None and given is not None:
                row = {**row, "name": given}  # noqa: PLW2901 - DIGGS requires a name
            named = _text(row.get("sampling_activity"))
            activity_key = self.ref("samples", index, row, "sampling_activity")
            if named is not None and activity_key not in by_key:
                if activity_key is not None:
                    self.need("samples", index, "a sampling_activity that is a sampling activity")
                continue
            key = self.make_key("samples", index, row)
            if key is None:
                continue
            if activity_key is None:
                activity_key = self.ids.make(f"{key}-sampling")
                if not make("samples", index, row, activity_key, None):
                    del self.keys[str(given)]
                    continue
            activity = by_key[activity_key]
            if activity.interval is None and activity.date is None:
                self.need("samples", index, "a depth or a date of sampling")
                del self.keys[str(given)]
                continue
            self.claim(given)
            self.lrs[key] = self.lrs[activity_key]
            classification = _text(row.get("classification"))
            samples.append(
                _node(
                    Sample,
                    key,
                    "Sample",
                    "sample",
                    **self.sample_header(str(given), row),
                    sampling_activity=activity_key,
                    type=None if classification is None else Code(value=classification),
                )
            )
            self.link(key, "project", self.project_of[str(activity.feature)])
            self.link(key, "samplingActivity", activity_key)
        return activities, samples

    def sample_header(self, given: str, row: Row) -> dict[str, Any]:
        """A sample's header; its ``type`` column is the DIGGS ``type`` parameter."""
        header = self.header(given, row)
        kind = self.code(row, "type")
        if kind is not None:
            header["params"]["type"] = kind if kind.code_space else kind.value
        return header

    def systems(self) -> dict[str, dict[str, Any]]:
        """The rows of ``observations``, by model key, each ready to take its layers."""
        systems: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(self.tables.get("observations", ())):
            feature = self.ref("observations", index, row, "feature")
            if feature is None:
                if _text(row.get("feature")) is None:
                    self.need("observations", index, "a feature")
                continue
            element = _text(row.get("element")) or "LithologySystem"
            if element not in self.layout.elements:
                self.need(
                    "observations", index, f"a DIGGS element ({rep.shown(element)!r} is not one)"
                )
                continue
            key = self.make_key("observations", index, row)
            if key is None:
                continue
            self.claim(_text(row.get("key")))
            self.lrs[key] = self.lrs[feature]
            systems[key] = {
                "element": element,
                "feature": feature,
                "header": self.header(str(_text(row.get("key"))), row),
                "layers": [],
            }
        return systems

    def system_of(
        self, index: int, row: Row, systems: dict[str, dict[str, Any]], made: dict[str, str]
    ) -> str | None:
        """The system a layer belongs to: the one it names, else one made for its feature."""
        system = self.ref("layers", index, row, "observation")
        if _text(row.get("observation")) is not None:
            if system is not None and system not in systems:
                self.need("layers", index, "an observation that is an observation system")
                return None
            return system
        feature = self.ref("layers", index, row, "feature")
        if feature is None:
            if _text(row.get("feature")) is None:
                self.need("layers", index, "a feature or an observation")
            return None
        if feature not in made:
            made[feature] = self.ids.make(f"{feature}-lithology")
            self.lrs[made[feature]] = self.lrs[feature]
            systems[made[feature]] = {
                "element": "LithologySystem",
                "feature": feature,
                "header": {"names": (), "description": None, "remarks": (), "params": {}},
                "layers": [],
            }
        return made[feature]

    def observations(self) -> list[Observation]:
        """Observation systems with their layers; a layer with no system gets one per feature."""
        systems = self.systems()
        made: dict[str, str] = {}
        for index, row in enumerate(self.tables.get("layers", ())):
            system = self.system_of(index, row, systems, made)
            layer = None if system is None else self.layer(index, row, system)
            if layer is not None and system is not None:
                systems[system]["layers"].append(layer)
        out: list[Observation] = []
        for key, system_ in systems.items():
            header = system_["header"]
            if system_["element"] == "LithologySystem" and (
                "lithologyClassificationType" not in header["params"]
            ):
                header["params"]["lithologyClassificationType"] = CLASSIFICATION_DEFAULT
                self.notes.add(
                    rep.ASSUMED,
                    "observations",
                    "DIGGS requires a lithology classification type and the tables give none"
                    " (params, name lithologyClassificationType);"
                    f" {CLASSIFICATION_DEFAULT!r} was written.",
                )
            out.append(
                _node(
                    Observation,
                    key,
                    system_["element"],
                    _kind(system_["element"]),
                    **header,
                    system=system_["element"],
                    feature=system_["feature"],
                    layers=tuple(system_["layers"]),
                )
            )
            self.link(key, "project", self.project_of[system_["feature"]])
            self.link(key, "samplingFeature", system_["feature"])
        return out

    def layer(self, index: int, row: Row, system: str) -> LithologyLayer | None:
        interval = self.interval("layers", index, row, system, both=True)
        description = _text(row.get("description"))
        if interval is None or description is None:
            self.need("layers", index, "a top, a base or a description")
            return None
        key = self.make_key("layers", index, row)
        if key is None:
            return None
        given = str(_text(row.get("key")))
        self.claim(given)
        self.lrs[key] = self.lrs[system]
        held = self.pending.pop(given, [])
        primary = next((c for c in held if c.role == "primaryLithology"), None)
        params: dict[str, Param] = {} if primary is None else dict(primary.params)
        params["lithDescription"] = description
        symbol = self.code(row, "classification")
        if symbol is not None:
            params["classificationSymbol"] = symbol if symbol.code_space else symbol.value
        legend = _text(row.get("legend_code"))
        if legend is not None:
            params["legendCode"] = legend
        ordered = {name: params[name] for name in ("lithDescription",) if name in params}
        ordered.update(params)
        lithology = _node(
            Component,
            f"{key}.lithology" if primary is None else primary.key,
            "Lithology",
            "lithology",
            gml_id=None,
            role="primaryLithology",
            params=ordered,
            components=() if primary is None else primary.components,
        )
        codes = tuple(
            value if isinstance(value, Code) else Code(value=value)
            for name in ("classificationCode", "classificationSymbol", "legendCode")
            if isinstance(value := ordered.get(name), Code | str)
        )
        colour: Code | None = None
        for name in ("colorCode", "colorName"):
            value = ordered.get(name)
            if isinstance(value, Code | str):
                colour = value if isinstance(value, Code) else Code(value=value)
                break
        header = self.header(given, row, name=True)
        header["description"] = description
        return _node(  # type: ignore[no-any-return]
            LithologyLayer,
            key,
            "LithologyObservation",
            "lithology_layer",
            **header,
            interval=interval,
            classification=codes,
            colour=colour,
            geologic_unit=_text(row.get("geologic_unit")),
            components=(lithology, *(c for c in held if c is not primary)),
        )

    # --- components -----------------------------------------------------------------

    def read_components(self, holders: Mapping[str, str]) -> None:
        """Build every component and file it under the model key of its holder.

        ``holders`` maps the key (as given) of every feature and of every row that will
        become a layer or a test to the key (as given) of its feature.
        """
        rows = list(enumerate(self.tables.get("components", ())))
        children: dict[str, list[tuple[int, Row]]] = {}
        tops: list[tuple[int, Row]] = []
        for index, row in rows:
            parent = _text(row.get("parent"))
            (tops if parent is None else children.setdefault(parent, [])).append((index, row))
        known = {_text(row.get("key")) for _i, row in rows}
        for parent, held in children.items():
            if parent not in known:
                for index, _row in held:
                    self.notes.add(
                        rep.ORPHAN_ROW,
                        "components",
                        "Rows of components name a parent that no row has; not converted"
                        " ({times}).",
                        row=index,
                        column="parent",
                    )

        def build(index: int, row: Row, feature: str) -> Component | None:
            role, element = _text(row.get("role")), _text(row.get("element"))
            if role is None or element is None or element not in self.layout.elements:
                self.need("components", index, "a role or a DIGGS element")
                return None
            key = self.make_key("components", index, row)
            if key is None:
                return None
            given = str(_text(row.get("key")))
            self.claim(given)
            self.lrs[key] = self.lrs[feature]
            lrs, unit = self.lrs[feature]
            depth = self.number("components", index, row, "depth")
            location = None
            if depth is not None and self.depth_unit("components", index, row, unit):
                location = Position(coordinates=(depth[0],), dim=1, crs=lrs)
            nested = [build(i, r, feature) for i, r in children.get(given, ())]
            takes_id = self.layout.element(element).id is not None
            return _node(  # type: ignore[no-any-return]
                Component,
                key,
                element,
                _kind(element),
                gml_id=key if takes_id else None,
                **self.header(given, row),
                role=role,
                interval=self.interval("components", index, row, key),
                location=location,
                time=self.span(row),
                components=tuple(c for c in nested if c is not None),
            )

        for index, row in tops:
            holder = _text(row.get("holder"))
            feature_given = holders.get(holder or "")
            feature = self.keys.get(feature_given or "")
            if holder is None or feature is None:
                self.notes.add(
                    rep.ORPHAN_ROW,
                    "components",
                    "Rows of components name a holder that no converted feature, layer or"
                    " test row has; not converted ({times}).",
                    row=index,
                    column="holder",
                )
                continue
            component = build(index, row, feature)
            if component is not None:
                self.pending.setdefault(holder, []).append(component)

    # --- measurements ---------------------------------------------------------------

    def column_facts(self) -> dict[tuple[str, str, str], Row]:
        facts: dict[tuple[str, str, str], Row] = {}
        for index, row in enumerate(self.tables.get("result_columns", ())):
            column = _text(row.get("column"))
            procedure = _text(row.get("procedure")) or ""
            measurement = _text(row.get("measurement")) or ""
            if column is None or not (procedure or measurement):
                self.need("result_columns", index, "a column and a procedure or a measurement")
                continue
            facts[(procedure, measurement, column)] = row
        return facts

    def result_rows(self) -> dict[str, list[_Entry]]:
        """Every row of every result table, by the key (as given) of its test."""
        results: dict[str, list[_Entry]] = {}
        for name, rows in self.tables.items():
            if not name.startswith(RESULTS_PREFIX):
                continue
            for index, row in enumerate(rows):
                owner = _text(row.get("measurement"))
                if owner is None:
                    self.need(name, index, "a measurement")
                else:
                    results.setdefault(owner, []).append((name, index, row))
        return results

    def orphan_results(self, entries: Iterable[_Entry], why: str) -> None:
        for name, at, _row in entries:
            self.notes.add(
                rep.ORPHAN_ROW,
                name,
                f"Rows of {name} name {why}; not converted ({{times}}).",
                row=at,
                column="measurement",
            )

    def test_of(self, index: int, row: Row) -> _Test | None:
        """What a ``measurements`` row is a test of, when it names a feature and a procedure."""
        procedure = _text(row.get("procedure"))
        feature = self.ref("measurements", index, row, "feature")
        if _text(row.get("feature")) is None or procedure is None:
            self.need("measurements", index, "a feature or a procedure")
            return None
        if feature is None:
            return None
        if procedure not in self.layout.elements:
            self.need(
                "measurements",
                index,
                f"a DIGGS procedure element ({rep.shown(procedure)!r} is not one)",
            )
            return None
        lrs, unit = self.lrs[feature]
        given = _text(row.get("key")) or ""
        return _Test(index, row, procedure, given, feature, lrs, unit)

    def measurements(self) -> list[Measurement]:
        facts = self.column_facts()
        results = self.result_rows()
        out: list[Measurement] = []
        for index, row in enumerate(self.tables.get("measurements", ())):
            test = self.test_of(index, row)
            if test is None:
                continue
            sample = self.ref("measurements", index, row, "sample")
            if _text(row.get("sample")) is not None and sample is None:
                continue
            own = results.pop(test.given, [])
            wrong = [r for r in own if r[0] != f"{RESULTS_PREFIX}{test.procedure}"]
            self.orphan_results(wrong, "a test whose procedure is not that of the table")
            table = self.result_table(test, [r for r in own if r not in wrong], facts)
            key = None if table is None else self.make_key("measurements", index, row)
            if key is None or table is None:
                continue
            self.claim(test.given)
            self.lrs[key] = self.lrs[test.feature]
            time = _text(row.get("result_time"))
            out.append(
                _node(
                    Measurement,
                    key,
                    "Test",
                    "test",
                    **self.header(test.given, row),
                    feature=test.feature,
                    samples=() if sample is None else (sample,),
                    result_time=None if time is None else TimeSpan(start=time),
                    procedure=Procedure(
                        type=test.procedure,
                        params=dict(self.params.get((test.given, "procedure"), {})),
                        components=tuple(self.pending.pop(test.given, ())),
                    ),
                    results=(table,),
                )
            )
            self.link(key, "project", self.project_of[test.feature])
            self.link(key, "samplingFeature", test.feature)
            if sample is not None:
                self.link(key, "sample", sample)
        for left in results.values():
            self.orphan_results(left, "a measurement that no converted row of measurements has")
        return out

    def placed(self, test: _Test, ordered: list[_Entry]) -> tuple[list[str], list[_Entry]] | None:
        """How the result rows are placed, and the rows that have that place."""
        places = [p for p in RESULT_PLACES if any(_text(r[2].get(p)) is not None for r in ordered)]
        if places and not all(self.depth_unit(r[0], r[1], r[2], test.unit) for r in ordered):
            return None
        if places not in ([], ["position"], ["top", "base"]):
            self.need(
                "measurements",
                test.index,
                "result rows placed by a position, or by a top and a base",
            )
            return None
        kept = []
        for entry in ordered:
            if all(self.number(entry[0], entry[1], entry[2], p) is not None for p in places):
                kept.append(entry)
            else:
                self.need(entry[0], entry[1], "a depth on every row of a test placed by depth")
        return places, kept

    def result_table(
        self, test: _Test, own: list[_Entry], facts: Mapping[tuple[str, str, str], Row]
    ) -> ResultTable | None:
        """The one result table of ``test``: its columns, and where it was measured."""
        ordered = sorted(
            own,
            key=lambda r: (
                parsed if (parsed := parse_number(_text(r[2].get("row")) or "")) is not None else 0,
                r[1],
            ),
        )
        names: dict[str, None] = {}
        for _name, _at, result in ordered:
            names.update(
                dict.fromkeys(
                    c
                    for c, v in result.items()
                    if c not in RESULT_KEYS and c not in RESULT_PLACES and _text(v) is not None
                )
            )
        found = self.placed(test, ordered)
        if found is None:
            return None
        places, ordered = found
        columns = [
            column
            for name in names
            if (column := self.column(test.procedure, test.given, name, ordered, facts)) is not None
        ]
        if len(columns) == 1 and any(v is None for v in columns[0].values):
            # A one-column table cannot hold an empty row: such rows have nothing to carry.
            keep = [i for i, v in enumerate(columns[0].values) if v is not None]
            ordered = [ordered[i] for i in keep]
            only = columns[0]
            columns = [_recolumn(only, tuple(only.values[i] for i in keep))]
        if not columns or not ordered:
            self.need("measurements", test.index, "a result value")
            return None
        where = self.where(test, places, ordered)
        if where is None:
            return None
        return dataclasses.replace(where, columns=tuple(columns), row_count=len(ordered))

    def where(self, test: _Test, places: list[str], ordered: list[_Entry]) -> ResultTable | None:
        """An empty table that says where ``test`` was measured: per row, or once for all."""
        lrs = test.lrs
        if places == ["top", "base"] and len(ordered) == 1:
            entry = ordered[0]  # one row reads back as the table's own extent
            extent = self.interval(entry[0], entry[1], entry[2], test.feature, both=True)
            return ResultTable(domain=Domain(shape="interval", crs=lrs), extent=extent)
        if places:
            index_columns = tuple(
                Column(
                    name=place,
                    key=place,
                    uom=test.unit,
                    type="number",
                    values=tuple(
                        pair[0]
                        for e in ordered
                        if (pair := self.number(e[0], e[1], e[2], place)) is not None
                    ),
                )
                for place in places
            )
            shape = "point" if places == ["position"] else "interval"
            return ResultTable(domain=Domain(shape=shape, index=index_columns, crs=lrs))
        depth = self.number("measurements", test.index, test.row, "depth")
        if depth is not None:
            if not self.depth_unit("measurements", test.index, test.row, test.unit):
                return None
            place = Position(coordinates=(depth[0],), dim=1, crs=lrs)
            return ResultTable(domain=Domain(shape="point"), location=place)
        extent = self.interval("measurements", test.index, test.row, test.feature, both=True)
        if extent is None:
            self.need(
                "measurements",
                test.index,
                "a depth, a top and a base, or a depth on each result row",
            )
            return None
        return ResultTable(domain=Domain(shape="interval", crs=lrs), extent=extent)

    def column(
        self,
        procedure: str,
        given: str,
        name: str,
        rows: list[tuple[str, int, Row]],
        facts: Mapping[tuple[str, str, str], Row],
    ) -> Column | None:
        fact = facts.get(("", given, name)) or facts.get((procedure, given, name))
        fact = fact or facts.get((procedure, "", name)) or {}
        kind = _text(fact.get("type")) or "number"
        if kind not in _COLUMN_TYPES:
            self.notes.add(
                rep.UNPARSED,
                "result_columns",
                f"Column {rep.shown(name)!r} has type {rep.shown(kind)!r}, which is not one of"
                f" {', '.join(_COLUMN_TYPES)}; its values were not converted.",
            )
            return None
        values = tuple(self.cell(table, at, row, name, kind) for table, at, row in rows)
        if all(value is None for value in values):
            return None
        key = _text(fact.get("property")) or name
        return Column(
            name=_text(fact.get("name")) or name,
            key=key,
            definition=_text(fact.get("definition")) or f"{PROPERTIES_URL}#{key}",
            uom=_text(fact.get("uom")),
            type=kind,
            null_value=_text(fact.get("null_value")),
            values=values,
        )

    def cell(
        self, table: str, index: int, row: Row, name: str, kind: str
    ) -> float | str | bool | None:
        value = row.get(name)
        if kind == "number":
            found = self.number(table, index, row, name)
            return None if found is None else found[0]
        text = _text(value)
        if text is None:
            return None
        if kind == "boolean":
            if text.lower() in _TRUE | _FALSE:
                return text.lower() in _TRUE
        elif len(text.split()) == 1 and "," not in text:
            return text
        self.notes.add(
            rep.UNPARSED,
            table,
            f"Values of {name} in {table} are not a {kind} DIGGS can hold in a result table"
            " (text there has no space or comma); not converted ({times}).",
            row=index,
            column=name,
        )
        return None

    # --- the model ------------------------------------------------------------------

    def extra_links(self, elements: Mapping[str, str], held: set[str]) -> None:
        """The rows of ``links``: references no column holds, when DIGGS has a place for them."""
        for index, row in enumerate(self.tables.get("links", ())):
            source = self.ref("links", index, row, "source")
            relation = _text(row.get("relation"))
            target_given = _text(row.get("target"))
            href = _text(row.get("href"))
            if source is None or relation is None or (target_given is None and href is None):
                if _text(row.get("source")) is None or relation is None:
                    self.need("links", index, "a source, a relation or a target")
                continue
            target = self.ref("links", index, row, "target")
            if target_given is not None and target is None:
                continue
            element = elements.get(source)
            lay = None if element is None else self.layout.elements.get(element)
            direct = lay is not None and (
                f"{relation}Ref" in lay.by_local or relation in lay.by_local
            )
            made = relation in _MADE_LINKS and source not in held
            if not direct or made or (target is None and str(href)[:1] == "#"):
                self.notes.add(
                    rep.NOT_CARRIED,
                    "links",
                    "Rows of links hold a reference DIGGS 3.0.0 has no place for directly in"
                    " the row's element, or that a column already holds; not converted"
                    " ({times}).",
                    row=index,
                )
                continue
            self.links.append(
                Link(
                    source=source,
                    relation=relation,
                    target=target,
                    href=f"#{target}" if target is not None else str(href),
                    resolved=target is not None,
                )
            )

    def run(self) -> TablesResult:
        self.read_params()
        document = self.document()
        projects = self.projects()
        # Components are built before the nodes that hold them, so each holder's feature
        # must be known first: a first pass over the holders' own rows gives it.
        holders: dict[str, str] = {}
        for row in self.tables.get("features", ()):
            if (given := _text(row.get("key"))) is not None:
                holders[given] = given
        systems = {
            _text(r.get("key")): _text(r.get("feature"))
            for r in self.tables.get("observations", ())
        }
        for row in self.tables.get("layers", ()):
            feature = _text(row.get("feature")) or systems.get(_text(row.get("observation")))
            if (given := _text(row.get("key"))) is not None and feature is not None:
                holders[given] = feature
        for row in self.tables.get("measurements", ()):
            feature = _text(row.get("feature"))
            if (given := _text(row.get("key"))) is not None and feature is not None:
                holders[given] = feature
        features = self._features_with_components(projects, holders)
        activities, samples = self.activities()
        observations = self.observations()
        measurements = self.measurements()
        for holder, left in self.pending.items():
            if holder not in self.claimed:
                self.notes.add(
                    rep.ORPHAN_ROW,
                    "components",
                    f"Rows of components are held by a row that was not converted; not"
                    f" converted ({len(left)} at the top level).",
                )
        self.orphans()
        for index, _row in enumerate(self.tables.get("extra", ())):
            self.notes.add(
                rep.EXTRA_NOT_WRITTEN,
                "extra",
                "Rows of extra hold DIGGS content the profile has no column for; pyDIGGS does"
                " not write them back to DIGGS ({times}).",
                row=index,
            )
        nodes: list[Node] = [
            document,
            *projects,
            *features,
            *activities,
            *samples,
            *observations,
            *measurements,
        ]
        nodes += [layer for observation in observations for layer in observation.layers]
        elements = {node.key: node.element for node in nodes}
        elements |= {c.key: c.element for node in nodes for c in components_of(node)}
        held = {c.key for node in nodes for c in components_of(node)}
        present = set(elements)
        self.extra_links(elements, held)
        links = [
            link
            for link in self.links
            if link.source in present and (link.target is None or link.target in present)
        ]
        if len(links) != len(self.links):
            self.notes.add(
                rep.ORPHAN_ROW,
                "links",
                "Rows of links name a row that was not converted; not converted"
                f" ({len(self.links) - len(links)}).",
            )
        model = Model(
            schema_version="1.0",
            tool={"name": "pydiggs.tables"},
            source=SourceInfo(
                name=None, sha256="", size=0, diggs_version="3.0.0", namespace=None, detected=False
            ),
            complete=True,
            document=document,
            projects=tuple(projects),
            features=tuple(features),
            sampling_activities=tuple(activities),
            samples=tuple(samples),
            observations=tuple(observations),
            measurements=tuple(measurements),
            crs=self.crs,
            links=tuple(links),
        )
        model = self.writable(model)
        kept = {node.key for node in model.nodes()}
        keys = {given: made for given, made in self.keys.items() if made in kept}
        return TablesResult(model=model, findings=self.notes.findings(), keys=keys)

    def writable(self, model: Model) -> Model:
        """``model`` without the nodes the writer refuses, each named by a finding."""
        for _ in range(sum(1 for _ in model.nodes()) + 1):
            try:
                write_model(model, "3.0.0")
            except ValueError as exc:
                match = _REFUSED.match(str(exc))
                key = None if match is None else _holder(model, match.group(1))
                if key is None:
                    raise ModelWriteError(str(exc)) from exc
                node = model.get(key)
                element = "row" if node is None else node.element
                reason = _REFUSED.sub(lambda m: m.group(0).partition(" ")[0], str(exc))
                self.notes.add(
                    rep.INCOMPLETE,
                    self.origin.get(key, "document"),
                    f"Rows that are a {element}, and the rows that refer to them, were not"
                    f" converted, because DIGGS 3.0.0 cannot hold them as given"
                    f" ({{times}}): {rep.shown(reason, 200)}",
                    count=True,
                )
                model = _without(model, key)
            else:
                return model
        raise AssertionError("the writer kept refusing")  # pragma: no cover

    def _features_with_components(
        self, projects: list[Project], holders: Mapping[str, str]
    ) -> list[Feature]:
        """Features first (their depth systems), then components, then the features' own."""
        features = self.features(projects)
        self.read_components(holders)
        given_of = {made: given for given, made in self.keys.items()}
        out: list[Feature] = []
        for feature in features:
            held = self.pending.pop(given_of.get(feature.key, ""), [])
            if not held:
                out.append(feature)
                continue
            out.append(
                _replace(
                    feature,
                    construction=tuple(c for c in held if c.role != "waterStrike"),
                    water_strikes=tuple(c for c in held if c.role == "waterStrike"),
                )
            )
        return out


#: Where the writer names the node it refuses: ``<Element> '<key>'`` at the start.
_REFUSED: Final = re.compile(r"^[A-Za-z][\w.-]* '((?:[^'\\]|\\.)*)'")


def _holder(model: Model, key: str) -> str | None:
    """The key of the member or layer that holds node ``key`` (itself when it is one)."""
    if model.get(key) is None:
        return None
    holders: list[Node] = [*model.features, *model.measurements]
    holders += [layer for o in model.observations for layer in o.layers]
    for holder in holders:
        if any(c.key == key for c in components_of(holder)):
            return holder.key
    return key


def _without(model: Model, key: str) -> Model:
    """``model`` without node ``key`` and every node that refers to it, however indirectly."""
    gone = {key}
    changed = True
    while changed:
        changed = False
        for link in model.links:
            if link.target in gone and link.source not in gone:
                gone.add(link.source)
                changed = True
        for sample in model.samples:
            if sample.sampling_activity in gone and sample.key not in gone:
                gone.add(sample.key)
                changed = True
    observations = tuple(
        dataclasses.replace(o, layers=tuple(x for x in o.layers if x.key not in gone))
        for o in model.observations
        if o.key not in gone
    )
    observations = tuple(o for o in observations if o.layers or not _had_layers(model, o.key))
    gone |= {o.key for o in model.observations} - {o.key for o in observations}
    held: set[str] = set()
    for node in model.nodes():
        if node.key in gone:
            held |= {c.key for c in components_of(node)}
    gone |= held

    def keep(nodes: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(node for node in nodes if node.key not in gone)

    return dataclasses.replace(
        model,
        projects=keep(model.projects),
        features=keep(model.features),
        sampling_activities=keep(model.sampling_activities),
        samples=keep(model.samples),
        observations=observations,
        measurements=keep(model.measurements),
        links=tuple(
            link for link in model.links if link.source not in gone and link.target not in gone
        ),
    )


def _had_layers(model: Model, key: str) -> bool:
    return any(o.key == key and o.layers for o in model.observations)


def _replace(node: Node, **changes: Any) -> Any:
    return dataclasses.replace(node, **changes)


def _recolumn(column: Column, values: tuple[Any, ...]) -> Column:
    return dataclasses.replace(column, values=values)


def to_model(tables: Mapping[str, Iterable[Mapping[str, Any]]]) -> TablesResult:
    """Build a model from profile tables.

    ``tables`` maps a table name of the profile to its rows, each row a mapping of
    column name to value. A value is a string, a number, a boolean or None; an empty
    string and a NaN are missing values. Rows may come from anywhere: dictionaries,
    :func:`csv.DictReader`, a database cursor through
    :func:`~pydiggs.tables.rows_from_cursor`.

    Args:
        tables: The tables. A table that is absent is empty.

    Returns:
        The model, ready for :func:`pydiggs.write_bytes`, with a finding for every row
        and value that was left out or assumed.

    Raises:
        TablesError: A table is not one of the profile, or lacks a required column.

    Examples:
        >>> result = to_model(
        ...     {
        ...         "features": [
        ...             {"key": "BH-1", "name": "BH-1", "x": 1.0, "y": 2.0, "depth_uom": "m"}
        ...         ],
        ...         "layers": [
        ...             {
        ...                 "key": "L1",
        ...                 "feature": "BH-1",
        ...                 "top": 0,
        ...                 "base": 1.5,
        ...                 "description": "CLAY",
        ...             }
        ...         ],
        ...     }
        ... )
        >>> result.model.features[0].name, len(result.model.observations[0].layers)
        ('BH-1', 1)
        >>> sorted({finding.rule_id for finding in result.findings})
        ['tables.assumed', 'tables.no-crs']
    """
    return _Builder(tables).run()
