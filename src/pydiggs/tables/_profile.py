"""The DIGGS tables profile: every table, its columns, keys and references.

The profile is data. :func:`profile` gives it as a Frictionless Data Package (v2)
descriptor with one Table Schema per table; the code that reads and writes tables is
driven by the same definitions, so the descriptor cannot say one thing and the code do
another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "PROFILE_VERSION",
    "RESULTS_PREFIX",
    "RESULT_TIMES",
    "TABLES",
    "Field",
    "Table",
    "profile",
    "results_table_name",
]

#: Version of the tables profile this release reads and writes.
PROFILE_VERSION: Final = "1.0-beta.1"
#: Result tables are named ``results_<procedure>``, one per procedure type.
RESULTS_PREFIX: Final = "results_"
_PROFILE_URL: Final = "https://xinp-hub.github.io/pydiggs/tables/"


@dataclass(frozen=True, slots=True)
class Field:
    """One column of a profile table.

    ``type`` is a Table Schema type (``string``, ``number``, ``integer``, ``boolean``).
    ``uom_of`` names the quantity column this column holds the unit of; ``references``
    names the table whose ``key`` the column refers to (``*`` for any node).
    """

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    uom_of: str | None = None
    references: str | None = None


@dataclass(frozen=True, slots=True)
class Table:
    """One table of the profile: its columns, its primary key and what a row is."""

    name: str
    description: str
    fields: tuple[Field, ...]
    primary_key: tuple[str, ...]

    def names(self) -> tuple[str, ...]:
        """The column names, in order."""
        return tuple(field.name for field in self.fields)


def _key(description: str) -> Field:
    return Field("key", description=description, required=True)


def _depths(what: str) -> tuple[Field, ...]:
    return (
        Field("top", "number", f"Depth of the top of {what}, along the feature."),
        Field("base", "number", f"Depth of the base of {what}, along the feature."),
        Field(
            "depth_uom",
            description="Unit of top and base. It must be the feature's depth unit.",
            uom_of="top",
        ),
    )


_TIMES: Final = (
    Field("start", description="Start time, ISO 8601, as reported."),
    Field("end", description="End time, ISO 8601, as reported."),
)
_NAME: Final = Field("name", description="The name, as reported (gml:name).")
_DESCRIPTION: Final = Field("description", description="A description, as reported.")

_TABLES: Final = (
    Table(
        "document",
        "The document information: at most one row.",
        (
            Field("creation_date", description="When the data set was made, ISO 8601."),
            _DESCRIPTION,
        ),
        (),
    ),
    Table(
        "projects",
        "One row per project.",
        (
            _key("Your stable identifier of the project. It becomes its gml:id."),
            Field("name", description="The project's name (gml:name); its key when empty."),
            _DESCRIPTION,
            Field("locality", description="Where the project is, in words."),
        ),
        ("key",),
    ),
    Table(
        "features",
        "One row per sampling feature: a borehole, sounding, trial pit, well, station or"
        " trench wall.",
        (
            _key("Your stable identifier of the feature. It becomes its gml:id."),
            Field("project", description="Key of its project.", references="projects"),
            Field(
                "element",
                description="The DIGGS element: Borehole, Sounding, TrialPit, Well, Station,"
                " TrenchWall … (Borehole when empty). A Station and a TrenchWall have no"
                " depth axis: nothing is placed by a depth along them.",
            ),
            Field("name", description="The feature's name (gml:name); its key when empty."),
            _DESCRIPTION,
            Field("x", "number", "First coordinate of its reference point, in the CRS's order."),
            Field("y", "number", "Second coordinate."),
            Field("z", "number", "Third coordinate, when the CRS has one."),
            Field("crs", description="The CRS of x, y and z, as an srsName."),
            Field(
                "extent",
                description="For a TrenchWall, the outline of the wall face, which DIGGS"
                " requires: POLYGON ((x y, x y, …)) or POLYGON Z ((x y z, …)), one closed"
                " ring, coordinates as reported.",
            ),
            Field("extent_crs", description="The CRS of extent, as an srsName."),
            Field("total_depth", "number", "Total measured depth, as reported."),
            Field("total_depth_uom", description="Unit of total_depth.", uom_of="total_depth"),
            Field(
                "depth_uom",
                description="Unit of every depth along this feature: m, ft, ft[US], cm or in.",
            ),
            Field(
                "start",
                description="Start time, ISO 8601, as reported (of a Station: when it was"
                " occupied).",
            ),
            Field("end", description="End time, ISO 8601, as reported."),
            Field(
                "parent",
                description="Key of the feature it sits in (a well in a borehole).",
                references="features",
            ),
        ),
        ("key",),
    ),
    Table(
        "sampling_activities",
        "One row per sampling activity: where, when and how samples were taken.",
        (
            _key("Your stable identifier of the activity."),
            Field("feature", description="Key of its feature.", references="features"),
            _NAME,
            _DESCRIPTION,
            *_depths("the sampled interval"),
            Field(
                "depth",
                "number",
                "The one depth sampled, for a sample taken at a point (in depth_uom).",
            ),
            *_TIMES,
            Field("method", description="The sampling method, as a code."),
            Field("method_code_space", description="The code list method comes from."),
            Field("recovery", "number", "Total sample recovery length, as reported."),
            Field("recovery_uom", description="Unit of recovery.", uom_of="recovery"),
        ),
        ("key",),
    ),
    Table(
        "samples",
        "One row per sample.",
        (
            _key("Your stable identifier of the sample. It becomes its gml:id."),
            Field(
                "sampling_activity",
                description="Key of the activity that produced it. When empty, one is made"
                " from feature, top, base, depth, start and end.",
                references="sampling_activities",
            ),
            Field("name", description="The sample's name (gml:name); its key when empty."),
            _DESCRIPTION,
            Field("type", description="The sample type, as a code (DIGGS `type`)."),
            Field("type_code_space", description="The code list type comes from."),
            Field(
                "classification",
                description="The DIGGS sample classification: disturbed, undisturbed …",
            ),
            Field(
                "feature",
                description="Key of its feature (read from its activity when that is given).",
                references="features",
            ),
            *_depths("the sample"),
            Field(
                "depth",
                "number",
                "The one depth of the sample, when it was taken at a point (in depth_uom).",
            ),
            *_TIMES,
        ),
        ("key",),
    ),
    Table(
        "observations",
        "One row per observation system, such as a lithology log of one feature.",
        (
            _key("Your stable identifier of the observation system."),
            Field("feature", description="Key of its feature.", references="features"),
            Field("element", description="The DIGGS element (LithologySystem when empty)."),
            _NAME,
            _DESCRIPTION,
        ),
        ("key",),
    ),
    Table(
        "layers",
        "One row per lithology layer.",
        (
            _key("Your stable identifier of the layer."),
            Field(
                "observation",
                description="Key of its observation system. When empty, one lithology"
                " system per feature is made.",
                references="observations",
            ),
            Field("feature", description="Key of its feature.", references="features"),
            *_depths("the layer"),
            Field("description", description="The layer's description (lithDescription)."),
            Field("classification", description="Its classification symbol, as a code."),
            Field("classification_code_space", description="The code list of classification."),
            Field("legend_code", description="Its legend code."),
            Field("geologic_unit", description="The name of its geologic unit."),
        ),
        ("key",),
    ),
    Table(
        "measurements",
        "One row per test, monitoring series or drilling log.",
        (
            _key("Your stable identifier of the test. It becomes its gml:id."),
            Field("feature", description="Key of its feature.", references="features"),
            Field(
                "element",
                description="The DIGGS element: Test, Monitor (a monitoring series) or"
                " MeasurementWhileDrilling (Test when empty).",
            ),
            Field(
                "procedure",
                description="The DIGGS procedure element: DrivenPenetrationTest,"
                " StaticConePenetrationTest, AtterbergLimitsTest …",
                required=True,
            ),
            _NAME,
            _DESCRIPTION,
            Field("sample", description="Key of the sample tested.", references="samples"),
            Field("result_time", description="When the result was obtained, ISO 8601."),
            Field("depth", "number", "The one depth all results were measured at."),
            *_depths("the one interval all results were measured over"),
        ),
        ("key",),
    ),
    Table(
        "result_columns",
        "One row per result column of a procedure type (or of one test, when it differs).",
        (
            Field("procedure", description="The procedure type the column belongs to."),
            Field(
                "measurement",
                description="Key of one test, when the row applies to that test only.",
                references="measurements",
            ),
            Field("column", description="The column's name in the result table.", required=True),
            Field("name", description="The property name as reported (propertyName)."),
            Field(
                "property",
                description="Key of the property in the DIGGS property dictionary"
                " (the column name when empty).",
            ),
            Field("definition", description="URL of the property's definition."),
            Field("uom", description="Unit of the column's values."),
            Field("type", description="number, string, boolean or datetime (number when empty)."),
            Field("null_value", description="The text that marks a missing value."),
        ),
        ("procedure", "measurement", "column"),
    ),
    Table(
        "components",
        "One row per record nested in a feature, layer or test procedure: a casing, a"
        " backfill, a water strike, an SPT drive set, a sieve reading.",
        (
            _key("Your stable identifier of the record."),
            Field(
                "holder",
                description="Key of the feature, layer or test that holds it.",
                required=True,
                references="*",
            ),
            Field(
                "parent",
                description="Key of the component it is nested in, when it is.",
                references="components",
            ),
            Field("role", description="The DIGGS property that holds it.", required=True),
            Field("element", description="Its DIGGS element.", required=True),
            _NAME,
            _DESCRIPTION,
            *_depths("the record"),
            Field("depth", "number", "The one depth the record applies at."),
            *_TIMES,
        ),
        ("key",),
    ),
    Table(
        "params",
        "One row per value with no column of its own, by DIGGS element name.",
        (
            Field(
                "node", description="Key of the row it belongs to.", required=True, references="*"
            ),
            Field(
                "scope",
                description="Empty, or `procedure` for a parameter of a test's procedure.",
            ),
            Field("name", description="The DIGGS element name.", required=True),
            Field("value", description="The value, as reported.", required=True),
            Field("uom", description="Its unit, for a quantity.", uom_of="value"),
            Field("code_space", description="Its code list, for a code."),
        ),
        ("node", "scope", "name"),
    ),
    Table(
        "remarks",
        "One row per remark, and per name after the first.",
        (
            Field(
                "node", description="Key of the row it belongs to.", required=True, references="*"
            ),
            Field("kind", description="`remark` or `name`.", required=True),
            Field("seq", "integer", "Its position among the node's remarks or names."),
            Field("text", description="The text.", required=True),
        ),
        ("node", "kind", "seq"),
    ),
    Table(
        "links",
        "One row per reference between rows that no column holds.",
        (
            Field("source", description="Key of the referring row.", required=True, references="*"),
            Field("relation", description="The DIGGS reference, without Ref.", required=True),
            Field("target", description="Key of the row it names, when it is in the data set."),
            Field("href", description="The reference as written, for one outside the data set."),
        ),
        (),
    ),
    Table(
        "extra",
        "One row per piece of DIGGS content the profile has no column for. Read only:"
        " pyDIGGS does not write these rows back to DIGGS, and says so.",
        (
            Field("node", description="Key of the row it belongs to, when it has one."),
            Field("seq", "integer", "Its position among the node's extra content."),
            Field("path", description="The DIGGS element."),
            Field("content", description="The content, as JSON."),
        ),
        (),
    ),
)

#: The fixed tables of the profile, by name. Result tables are not listed: there is one
#: per procedure type in the data, named ``results_<procedure>``.
TABLES: Final[dict[str, Table]] = {table.name: table for table in _TABLES}

#: The columns every result table starts with: its primary key.
RESULT_KEYS: Final = ("measurement", "result", "row")
#: Where a row is, when the test gives every row its own place.
RESULT_PLACES: Final = ("position", "top", "base")
#: When a row is, when the test gives every row its own time: the time as reported, or
#: the time elapsed since the start (its unit in ``result_columns``).
RESULT_TIMES: Final = ("time", "elapsed_time")


def results_table_name(procedure: str) -> str:
    """The name of the result table of procedure type ``procedure``.

    Examples:
        >>> results_table_name("StaticConePenetrationTest")
        'results_StaticConePenetrationTest'
    """
    return f"{RESULTS_PREFIX}{procedure}"


def _field_descriptor(field: Field) -> dict[str, Any]:
    out: dict[str, Any] = {"name": field.name, "type": field.type}
    if field.description:
        out["description"] = field.description
    if field.required:
        out["constraints"] = {"required": True}
    if field.uom_of is not None:
        out["uomOf"] = field.uom_of
    return out


def table_schema(table: Table) -> dict[str, Any]:
    """The Table Schema of one fixed table."""
    schema: dict[str, Any] = {"fields": [_field_descriptor(f) for f in table.fields]}
    if table.primary_key:
        schema["primaryKey"] = list(table.primary_key)
    keys = [
        {
            "fields": [field.name],
            "reference": {"resource": field.references, "fields": ["key"]},
        }
        for field in table.fields
        if field.references not in (None, "*")
    ]
    if keys:
        schema["foreignKeys"] = keys
    schema["missingValues"] = [""]
    return schema


def results_schema(columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The Table Schema of one result table with result columns ``columns``.

    ``columns`` maps a column name to its ``type`` and, when it has them, its ``unit``
    and ``property``.
    """
    fields: list[dict[str, Any]] = [
        {
            "name": "measurement",
            "type": "string",
            "description": "Key of the test.",
            "constraints": {"required": True},
        },
        {
            "name": "result",
            "type": "integer",
            "description": "Which result table of the test the row is in: 0, the test's"
            " outcome, when empty; 1 on, the tables its procedure holds, such as a"
            " dissipation time series.",
        },
        {"name": "row", "type": "integer", "description": "The row's number within the table."},
    ]
    fields.extend({"name": name, **facts} for name, facts in columns.items())
    return {
        "fields": fields,
        "primaryKey": list(RESULT_KEYS),
        "foreignKeys": [
            {
                "fields": ["measurement"],
                "reference": {"resource": "measurements", "fields": ["key"]},
            }
        ],
        "missingValues": [""],
    }


def profile() -> dict[str, Any]:
    """The profile as a Data Package descriptor: every fixed table and its Table Schema.

    Result tables depend on the data, so they are not listed; the descriptor a
    :class:`~pydiggs.tables.TableSet` writes beside its CSV files lists them too.

    Examples:
        >>> described = profile()
        >>> described["profile_version"], described["resources"][1]["name"]
        ('1.0-beta.1', 'projects')
        >>> described["resources"][2]["schema"]["primaryKey"]
        ['key']
    """
    return {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": "diggs-tables",
        "title": "DIGGS tables profile",
        "homepage": _PROFILE_URL,
        "profile_version": PROFILE_VERSION,
        "resources": [
            {
                "name": table.name,
                "path": f"{table.name}.csv",
                "type": "table",
                "format": "csv",
                "mediatype": "text/csv",
                "encoding": "utf-8",
                "description": table.description,
                "schema": table_schema(table),
            }
            for table in _TABLES
        ],
    }
