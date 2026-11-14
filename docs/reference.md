# Python API reference

Everything below is importable from `pydiggs` and follows
[Semantic Versioning](https://semver.org/): names are only removed or changed in a
major release, after a deprecation warning.

!!! tip "Where to start"
    Most programs need only `validate()` for files or `validate_bytes()` for data
    already in memory. Both return a `Report`; see [Findings](findings.md) for its
    JSON form.

## Validate

::: pydiggs.validate

::: pydiggs.validate_bytes

::: pydiggs.detect_diggs_version

::: pydiggs.warm_up

## Read

Stable from 1.7.0; see [Reading files](model.md). The node and value classes are in
`pydiggs.model`.

::: pydiggs.read

::: pydiggs.read_bytes

::: pydiggs.Model

::: pydiggs.model_schema

::: pydiggs.model.Component

::: pydiggs.model.Procedure

::: pydiggs.errors.DocumentError

### Coordinate reference systems

::: pydiggs.crs.parse

::: pydiggs.crs.epsg

::: pydiggs.crs.epsg_source

::: pydiggs.crs.SUPPORTED_SRS_FORMS

## Data frames and GIS

The `frames`, `polars` and `gis` extras; see
[Reading files](model.md#data-frames-and-gis).

::: pydiggs.frames.to_pandas

::: pydiggs.frames.to_polars

::: pydiggs.gis.transform

::: pydiggs.gis.to_geodataframe

::: pydiggs.gis.write

## Results

::: pydiggs.Report

::: pydiggs.Finding

::: pydiggs.Severity

::: pydiggs.Step

## Findings documents

::: pydiggs.findings_document

::: pydiggs.findings_json

::: pydiggs.findings_schema

::: pydiggs.FINDINGS_SCHEMA_VERSION

::: pydiggs.FINDINGS_SCHEMA_URL

## Rules and versions

::: pydiggs.explain_rule

::: pydiggs.rule_catalogue

::: pydiggs.RuleInfo

::: pydiggs.Guidance

::: pydiggs.version_info

::: pydiggs.VersionInfo

## Capabilities

What this pyDIGGS can do; see [Capabilities](capabilities.md).

::: pydiggs.capabilities

::: pydiggs.Capabilities

::: pydiggs.Operation

::: pydiggs.OptionSpec

::: pydiggs.capabilities_schema

::: pydiggs.CAPABILITIES_SCHEMA_VERSION

::: pydiggs.CAPABILITIES_SCHEMA_URL

## AGS4 and DIGGS conversion

Stable from pyDIGGS 1.10; see [AGS4 and DIGGS conversion](convert.md).

::: pydiggs.convert.ags4_to_diggs

::: pydiggs.convert.diggs_to_ags4

::: pydiggs.convert.model_to_ags4

::: pydiggs.convert.Conversion

::: pydiggs.convert.Origin

::: pydiggs.convert.mapping_info

::: pydiggs.convert.MAPPING_VERSION

## Errors

::: pydiggs.PydiggsError

::: pydiggs.InstanceReadError

::: pydiggs.ResourceError

## Legacy checks

The `validator` class runs one check at a time and prints or logs its results. New code
should prefer `validate()`; `validator` stays for existing scripts. See
[Validation checks](usage.md) for examples.

::: pydiggs.validator
    options:
      members:
        - schema_check
        - dictionary_check
        - schematron_check
        - context_check
