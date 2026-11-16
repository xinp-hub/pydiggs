"""What this pydiggs can do, machine-readable: :func:`capabilities` and diggs-capabilities 1.0.

An app that bundles pyDIGGS builds its menus from :func:`capabilities` instead of from
hand edits per release: which operations the installed pyDIGGS offers, what each reads
and writes, its options, how stable it is, which extras it needs and whether they are
installed here. ``capabilities().to_dict()`` is a diggs-capabilities 1.0 document,
described by :func:`capabilities_schema` and published at :data:`CAPABILITIES_SCHEMA_URL`.

The catalogue below is written from the code: each operation names the public
functions it describes, and ``tests/test_capabilities.py`` fails when a public function,
an HTTP route, an MCP tool, a sidecar method or a CLI command has no entry, or when an
option's name or default differs from the function's signature.

``available`` is computed when :func:`capabilities` is called, by looking for each
module an operation needs (``importlib.util.find_spec``), never by importing it: the
answer is the same under CPython with extras and under Pyodide without them, and asking
never loads pandas, geopandas or pyproj.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from types import MappingProxyType
from typing import Any, Final

__all__ = [
    "CAPABILITIES_SCHEMA_URL",
    "CAPABILITIES_SCHEMA_VERSION",
    "OPTION_TYPES",
    "STABILITIES",
    "Capabilities",
    "Operation",
    "OptionSpec",
    "capabilities",
    "capabilities_schema",
]

#: Version of the diggs-capabilities schema :meth:`Capabilities.to_dict` follows.
CAPABILITIES_SCHEMA_VERSION: Final = "1.0"
#: ``$id`` of that schema, published on the documentation site.
CAPABILITIES_SCHEMA_URL: Final = (
    "https://xinp-hub.github.io/pydiggs/schemas/diggs-capabilities-1.0.schema.json"
)
_SCHEMA_RESOURCE: Final = "diggs-capabilities-1.0.schema.json"

#: How far an operation may change: ``stable`` follows Semantic Versioning, ``preview``
#: may still change in a minor release, ``experimental`` may change or go at any time.
STABILITIES: Final = ("stable", "preview", "experimental")
#: The value types an option takes. ``file`` is a second file, by path in Python.
OPTION_TYPES: Final = ("boolean", "integer", "number", "string", "string[]", "file")

OptionDefault = str | int | float | bool | tuple[str, ...] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OptionSpec:
    """One option of an :class:`Operation`: its type, default, choices and a label.

    The option's name is its key in ``Operation.options``; it is the keyword argument
    of the Python function and the parameter of the HTTP, MCP and sidecar requests that
    take it. ``default`` None means "not given" (the function then decides, as its
    documentation says); ``choices`` None means any value of ``type``.

    Examples:
        >>> from pydiggs import OptionSpec
        >>> spec = OptionSpec(type="boolean", label="Keep result values", default=True)
        >>> spec.to_dict()["default"], spec.to_dict()["choices"]
        (True, None)
        >>> OptionSpec(type="date", label="x")  # doctest: +ELLIPSIS
        Traceback (most recent call last):
        ...
        ValueError: OptionSpec.type must be one of boolean, ...; got 'date'
    """

    type: str
    label: str
    default: OptionDefault = None
    choices: tuple[str, ...] | None = None
    required: bool = False

    def __post_init__(self) -> None:
        if self.type not in OPTION_TYPES:
            raise ValueError(
                f"OptionSpec.type must be one of {', '.join(OPTION_TYPES)}; got {self.type!r}"
            )
        if self.choices is not None:
            object.__setattr__(self, "choices", tuple(self.choices))

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable dict, in field order."""
        default = list(self.default) if isinstance(self.default, tuple) else self.default
        return {
            "type": self.type,
            "label": self.label,
            "default": default,
            "choices": None if self.choices is None else list(self.choices),
            "required": self.required,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Operation:
    """One thing this pydiggs can do, such as ``validate``, ``read`` or ``export.gpkg``.

    ``inputs`` and ``outputs`` name what the operation reads and gives: a DIGGS version
    (``diggs-2.6``), an AGS4 version (``ags4-4.1.1``), a pyDIGGS document contract
    (``diggs-findings-1.1``, ``diggs-model-1.0``), a media type (``application/geo+json``)
    or, for an in-memory result, the Python type (``pandas.DataFrame``). ``needs`` lists
    the pyDIGGS extras the operation needs (empty for the core), and ``available`` says
    whether everything it needs is installed in this interpreter.

    Examples:
        >>> from pydiggs import Operation
        >>> op = Operation(
        ...     id="read",
        ...     label="Read a DIGGS file",
        ...     stability="stable",
        ...     inputs=("diggs-3.0.0",),
        ...     outputs=("diggs-model-1.0",),
        ... )
        >>> op.to_dict()["outputs"], op.available
        (['diggs-model-1.0'], True)
    """

    id: str
    label: str
    stability: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    options: Mapping[str, OptionSpec] = field(default_factory=dict, hash=False)
    needs: tuple[str, ...] = ()
    available: bool = True

    def __post_init__(self) -> None:
        if self.stability not in STABILITIES:
            raise ValueError(
                f"Operation.stability must be one of {', '.join(STABILITIES)};"
                f" got {self.stability!r}"
            )
        for name in ("inputs", "outputs", "needs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickle and deep-copy through a plain dict; a mappingproxy cannot be pickled."""
        return (_rebuild_operation, (self.to_dict(),))

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable dict, in field order."""
        return {
            "id": self.id,
            "label": self.label,
            "stability": self.stability,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "options": {name: spec.to_dict() for name, spec in self.options.items()},
            "needs": list(self.needs),
            "available": self.available,
        }


def _rebuild_operation(fields: dict[str, Any]) -> Operation:
    """Inverse of :meth:`Operation.to_dict`, for pickling."""
    options = {
        name: OptionSpec(
            type=spec["type"],
            label=spec["label"],
            default=tuple(spec["default"])
            if isinstance(spec["default"], list)
            else spec["default"],
            choices=spec["choices"],
            required=spec["required"],
        )
        for name, spec in fields["options"].items()
    }
    return Operation(**{**fields, "options": options})


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """What a pydiggs install offers: its version and its :class:`Operation` entries.

    Examples:
        >>> import pydiggs
        >>> caps = pydiggs.capabilities()
        >>> caps.schema_version, caps.pydiggs_version == pydiggs.__version__
        ('1.0', True)
        >>> caps.to_dict()["$schema"] == pydiggs.CAPABILITIES_SCHEMA_URL
        True
    """

    pydiggs_version: str
    schema_version: str
    operations: tuple[Operation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", tuple(self.operations))

    def to_dict(self) -> dict[str, Any]:
        """A diggs-capabilities document: JSON-serialisable, ``$schema`` first."""
        return {
            "$schema": CAPABILITIES_SCHEMA_URL,
            "schema_version": self.schema_version,
            "pydiggs_version": self.pydiggs_version,
            "operations": [op.to_dict() for op in self.operations],
        }


def capabilities_schema() -> dict[str, Any]:
    """The packaged diggs-capabilities JSON Schema, as a fresh dict.

    The same file is published at :data:`CAPABILITIES_SCHEMA_URL`.

    Raises:
        ResourceError: The packaged schema is missing or not valid JSON.

    Examples:
        >>> from pydiggs import CAPABILITIES_SCHEMA_URL, capabilities_schema
        >>> schema = capabilities_schema()
        >>> schema["$id"] == CAPABILITIES_SCHEMA_URL, schema["title"]
        (True, 'diggs-capabilities 1.0')
    """
    from pydiggs.errors import ResourceError  # noqa: PLC0415

    try:
        text = resources.files(__name__).joinpath(_SCHEMA_RESOURCE).read_text("utf-8")
        loaded: dict[str, Any] = json.loads(text)
    except (OSError, ValueError) as exc:
        raise ResourceError(f"packaged {_SCHEMA_RESOURCE} could not be read: {exc}") from exc
    return loaded


# --- the catalogue ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entry:
    """An operation without ``available``, plus the modules that decide it."""

    operation: Operation
    modules: tuple[str, ...] = ()


def _installed(module: str) -> bool:
    """Whether ``module`` can be imported here, found without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


#: The ``gis.write`` driver each file export uses (a value of ``pydiggs.gis.DRIVERS``).
_DRIVER_OF: Final = {
    "export.gpkg": "GPKG",
    "export.geoparquet": "Parquet",
    "export.geojson": "GeoJSON",
}


def _catalogue() -> tuple[_Entry, ...]:
    """Every operation, in the order an app would list them."""
    from pydiggs import ags4, frames, gis  # noqa: PLC0415
    from pydiggs.ags4._io import DEFAULT_MAX_BYTES as AGS4_MAX_BYTES  # noqa: PLC0415
    from pydiggs.convert._api import AGS_VERSIONS as CONVERT_AGS  # noqa: PLC0415
    from pydiggs.detect import DEFAULT_PROFILE, SCHEMA_PATHS  # noqa: PLC0415
    from pydiggs.findings.export import FINDINGS_SCHEMA_VERSION  # noqa: PLC0415
    from pydiggs.findings.model import Step  # noqa: PLC0415
    from pydiggs.findings.run import (  # noqa: PLC0415
        DEFAULT_MAX_BYTES,
        DEFAULT_MAX_FINDINGS,
        NATIVE_STEPS,
    )
    from pydiggs.model import MODEL_SCHEMA_VERSION  # noqa: PLC0415

    diggs = tuple(f"diggs-{v}" for v in SCHEMA_PATHS)  # oldest first
    ags = tuple(f"ags4-{v}" for v in ags4.AGS_VERSIONS)
    findings = f"diggs-findings-{FINDINGS_SCHEMA_VERSION}"
    model = f"diggs-model-{MODEL_SCHEMA_VERSION}"
    ags4_file = "pydiggs.ags4.File"

    diggs_version = OptionSpec(
        type="string",
        label=(
            "DIGGS version to use instead of the one detected from the file"
            " (3.1-dev, the unreleased draft, is experimental and used only when named)"
        ),
        choices=tuple(SCHEMA_PATHS),
    )
    time_budget = OptionSpec(
        type="number",
        label="Seconds to spend at most; what was done by then is returned, marked incomplete",
    )
    max_findings = OptionSpec(
        type="integer", label="Most findings to report", default=DEFAULT_MAX_FINDINGS
    )
    encoding = OptionSpec(
        type="string",
        label="Text encoding of the file (UTF-8, or Windows-1252 when it is not, if not given)",
        choices=("ascii", "utf-8", "utf-8-sig", "cp1252", "iso-8859-1"),
    )
    gis_to = OptionSpec(
        type="string",
        label="Geographic coordinate system for longitude and latitude",
        default="EPSG:4326",
    )
    gis_kind = OptionSpec(type="string", label="Only features of this kind, such as borehole")
    frame_options = {
        "view": OptionSpec(
            type="string",
            label="Which table: features, samples, layers, measurements or results",
            default="results",
            choices=frames.VIEWS,
        ),
        "feature": OptionSpec(type="string", label="Only rows of the feature with this key"),
        "kind": OptionSpec(type="string", label="Only features or measurements of this kind"),
        "procedure": OptionSpec(
            type="string", label="Only measurements or results of this test type"
        ),
        "measurement": OptionSpec(
            type="string", label="Only results of the measurement with this key"
        ),
        "si": OptionSpec(
            type="boolean", label="Results in SI units where the unit converts", default=False
        ),
    }
    gis_modules = ("geopandas", "shapely", "pyproj", "pandas")
    by_suffix = {driver: suffix for suffix, driver in gis.DRIVERS.items()}

    def gis_export(op_id: str, label: str, media_type: str, module: str) -> _Entry:
        return _Entry(
            Operation(
                id=op_id,
                label=f"{label} ({by_suffix[_DRIVER_OF[op_id]]})",
                stability="stable",
                inputs=(model,),
                outputs=(media_type,),
                options={"to": gis_to, "kind": gis_kind},
                needs=("gis",),
            ),
            (*gis_modules, module),
        )

    return (
        _Entry(
            Operation(
                id="validate",
                label="Check a DIGGS file against the schema, dictionaries and business rules",
                stability="stable",
                inputs=diggs,
                outputs=(findings,),
                options={
                    "diggs_version": diggs_version,
                    "checks": OptionSpec(
                        type="string[]",
                        label="Which checks to run (all of them if not given)",
                        choices=tuple(s.value for s in NATIVE_STEPS),
                    ),
                    "max_findings": max_findings,
                    "max_bytes": OptionSpec(
                        type="integer",
                        label="Largest file accepted, in bytes",
                        default=DEFAULT_MAX_BYTES,
                    ),
                    "time_budget": time_budget,
                },
            )
        ),
        _Entry(
            Operation(
                id="read",
                label="Read what a DIGGS file holds: boreholes, samples, tests and results",
                stability="stable",
                inputs=diggs,
                outputs=(model,),
                options={
                    "diggs_version": diggs_version,
                    "results": OptionSpec(
                        type="boolean",
                        label="Keep the values of result tables (off: only their columns and size)",
                        default=True,
                    ),
                    "provenance": OptionSpec(
                        type="string",
                        label="Where each node came from (object), or each value too (field)",
                        default="object",
                        choices=("object", "field"),
                    ),
                    "max_bytes": OptionSpec(
                        type="integer",
                        label="Largest file accepted, in bytes",
                        default=DEFAULT_MAX_BYTES,
                    ),
                    "time_budget": time_budget,
                },
            )
        ),
        _Entry(
            Operation(
                id="detect",
                label="Tell which DIGGS version a file is written in",
                stability="stable",
                inputs=diggs,
                outputs=("diggs-version",),
                options={
                    "default": OptionSpec(
                        type="string",
                        label="DIGGS version to answer when the file does not say",
                        default=DEFAULT_PROFILE,
                        choices=tuple(SCHEMA_PATHS),
                    )
                },
            )
        ),
        _Entry(
            Operation(
                id="rules.list",
                label="List every rule pyDIGGS can report",
                stability="stable",
                outputs=("rule-info",),
                options={
                    "step": OptionSpec(
                        type="string",
                        label="Only the rules of this check",
                        choices=tuple(s.value for s in Step),
                    )
                },
            )
        ),
        _Entry(
            Operation(
                id="rules.explain",
                label="Explain one rule a finding names",
                stability="stable",
                outputs=("rule-info",),
                options={
                    "rule_id": OptionSpec(
                        type="string",
                        label="The rule id, such as schema.cvc-complex-type.2.4",
                        required=True,
                    )
                },
            )
        ),
        _Entry(
            Operation(
                id="ags4.read",
                label="Read an AGS4 file as tables",
                stability="preview",
                inputs=ags,
                outputs=(ags4_file,),
                options={
                    "encoding": encoding,
                    "max_bytes": OptionSpec(
                        type="integer",
                        label="Largest file accepted, in bytes",
                        default=AGS4_MAX_BYTES,
                    ),
                    "time_budget": time_budget,
                },
            )
        ),
        _Entry(
            Operation(
                id="ags4.write",
                label="Write tables as an AGS4 file",
                stability="preview",
                inputs=(ags4_file,),
                outputs=ags,
            )
        ),
        _Entry(
            Operation(
                id="ags4.check",
                label="Check an AGS4 file against the AGS4 rules",
                stability="preview",
                inputs=ags,
                outputs=(findings,),
                options={
                    "dictionary": OptionSpec(
                        type="file",
                        label=(
                            "Your copy of the AGS4 data dictionary, to check the standard"
                            " groups too (none is bundled)"
                        ),
                    ),
                    "encoding": encoding,
                    "max_bytes": OptionSpec(
                        type="integer",
                        label="Largest file accepted, in bytes",
                        default=AGS4_MAX_BYTES,
                    ),
                    "max_findings": max_findings,
                    "time_budget": time_budget,
                },
            )
        ),
        _Entry(
            Operation(
                id="convert.ags4-to-diggs",
                label=(
                    "Convert an AGS4 file to DIGGS 3.0.0, listing everything not carried over"
                    " and keeping it in a residual AGS4 file"
                ),
                stability="stable",
                inputs=(*ags, ags4_file),
                outputs=("diggs-3.0.0", findings, model, ags4_file),
                options={
                    "diggs_version": OptionSpec(
                        type="string",
                        label="DIGGS version to write",
                        default="3.0.0",
                        choices=("3.0.0",),
                    ),
                    "validate": OptionSpec(
                        type="boolean",
                        label="Check the converted file against DIGGS before returning it",
                        default=True,
                    ),
                    "name": OptionSpec(type="string", label="File name to report the source as"),
                    "encoding": encoding,
                    "max_bytes": OptionSpec(
                        type="integer",
                        label="Largest file accepted, in bytes",
                        default=AGS4_MAX_BYTES,
                    ),
                    "time_budget": time_budget,
                },
            )
        ),
        _Entry(
            Operation(
                id="convert.diggs-to-ags4",
                label=(
                    "Convert a DIGGS file (or a read one) to an AGS4 file, listing everything"
                    " not carried over"
                ),
                stability="stable",
                inputs=(*diggs, model),
                outputs=(*(f"ags4-{v}" for v in CONVERT_AGS), ags4_file, findings),  # newest last
                options={
                    "ags_version": OptionSpec(
                        type="string",
                        label=(
                            "AGS4 version to write (the BGS AGS validator and python-ags4"
                            " 1.2.0 accept AGS4 only up to 4.1.1, as of November 2026)"
                        ),
                        default=CONVERT_AGS[-1],
                        choices=tuple(reversed(CONVERT_AGS)),
                    ),
                    "check": OptionSpec(
                        type="boolean",
                        label="Check the converted file against the AGS4 rules before returning it",
                        default=True,
                    ),
                    "formula_guard": OptionSpec(
                        type="string",
                        label=(
                            "Put an apostrophe before text a spreadsheet would read as a"
                            " formula (changes the value; only for files meant for a"
                            " spreadsheet)"
                        ),
                        choices=("prefix",),
                    ),
                    "name": OptionSpec(type="string", label="File name to report the source as"),
                    "max_bytes": OptionSpec(
                        type="integer",
                        label="Largest file accepted, in bytes",
                        default=DEFAULT_MAX_BYTES,
                    ),
                    "time_budget": time_budget,
                },
            )
        ),
        _Entry(
            Operation(
                id="export.pandas",
                label="Tables of a read file as a pandas data frame",
                stability="stable",
                inputs=(model,),
                outputs=("pandas.DataFrame",),
                options=frame_options,
                needs=("frames",),
            ),
            ("pandas",),
        ),
        _Entry(
            Operation(
                id="export.polars",
                label="Tables of a read file as a polars data frame",
                stability="stable",
                inputs=(model,),
                outputs=("polars.DataFrame",),
                options=frame_options,
                needs=("polars",),
            ),
            ("polars",),
        ),
        _Entry(
            Operation(
                id="gis.transform",
                label="Add longitude and latitude to every location that has a known CRS",
                stability="stable",
                inputs=(model,),
                outputs=(model,),
                options={"to": gis_to},
                needs=("gis",),
            ),
            ("pyproj",),
        ),
        _Entry(
            Operation(
                id="export.geodataframe",
                label="Boreholes and other features as map points (a GeoDataFrame)",
                stability="stable",
                inputs=(model,),
                outputs=("geopandas.GeoDataFrame",),
                options={"to": gis_to, "kind": gis_kind},
                needs=("gis",),
            ),
            gis_modules,
        ),
        gis_export(
            "export.gpkg",
            "Save features as a GeoPackage",
            "application/geopackage+sqlite3",
            "pyogrio",
        ),
        gis_export(
            "export.geoparquet",
            "Save features as GeoParquet",
            "application/vnd.apache.parquet",
            "pyarrow",
        ),
        gis_export("export.geojson", "Save features as GeoJSON", "application/geo+json", "pyogrio"),
    )


def capabilities() -> Capabilities:
    """Describe what this pydiggs can do: every operation, its options and stability.

    ``available`` is worked out now, in this interpreter: an operation whose extra is
    not installed (``pip install 'pydiggs[gis]'``, say) is listed with
    ``available=False``, so a host such as Pyodide, where only the core is installed,
    reports what it can really do. Nothing optional is imported to find out.

    Returns:
        A :class:`Capabilities`; ``capabilities().to_dict()`` is a diggs-capabilities
        1.0 document (see :func:`capabilities_schema`).

    Examples:
        >>> import pydiggs
        >>> caps = pydiggs.capabilities()
        >>> [op.id for op in caps.operations][:5]
        ['validate', 'read', 'detect', 'rules.list', 'rules.explain']
        >>> validate = caps.operations[0]
        >>> validate.stability, validate.outputs, validate.needs, validate.available
        ('stable', ('diggs-findings-1.1',), (), True)
        >>> validate.options["diggs_version"].choices
        ('2.5.a', '2.6', '3.0.0', '3.1-dev')
    """
    import pydiggs  # noqa: PLC0415  # the package imports this module

    operations = tuple(
        Operation(
            id=entry.operation.id,
            label=entry.operation.label,
            stability=entry.operation.stability,
            inputs=entry.operation.inputs,
            outputs=entry.operation.outputs,
            options=entry.operation.options,
            needs=entry.operation.needs,
            available=all(_installed(m) for m in entry.modules),
        )
        for entry in _catalogue()
    )
    return Capabilities(
        pydiggs_version=pydiggs.__version__,
        schema_version=CAPABILITIES_SCHEMA_VERSION,
        operations=operations,
    )
