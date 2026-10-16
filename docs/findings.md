# Findings: JSON and SARIF output

`pydiggs validate` and the `validate()` function report every problem in a DIGGS file as a
**finding**, with a stable rule id, a severity and a location (line, XPath, `gml:id`).
They print nothing and write no log files. The legacy subcommands (`schema_check`,
`dictionary_check`, ...) and `validator` are unchanged.

```bash
pydiggs validate site/BH-01.xml --format json
pydiggs validate site/ --format sarif --output pydiggs.sarif
```

```python
from pydiggs import validate

report = validate("site/BH-01.xml")
report.ok  # False if any finding is an error
report.diggs_version  # "2.5.a", "2.6" or "3.0.0"
report.counts  # {"error": 2, "warning": 31, "info": 20}
report.steps  # {Step.SCHEMA: "failed", Step.DICTIONARY: "passed", ...}
for f in report.findings:
    print(f.step, f.rule_id, f.severity, f.line, f.xpath, f.gml_id, f.message)
report.to_dict()  # this file's entry of the JSON document
report.to_json(indent=2)  # a whole diggs-findings document
report.to_sarif()  # a SARIF 2.1.0 log, as a dict
```

`validate()` runs every native step by default, even after an earlier one fails, and
records what happened to each in `report.steps`: `passed`, `failed` (at least one error),
`skipped` (not asked for, or the file is not well-formed XML) or `incomplete` (the step
could not finish; its findings are partial).

It raises only `InstanceReadError` (the file cannot be read; also an `OSError`),
`ResourceError` (a schema, dictionary or Schematron file you passed, or pydiggs's bundled
data, is missing or broken) and `ValueError` (an unknown `diggs_version` or check name).
XML that is not well-formed is a finding, `structure.well-formed`, not an exception.

## Command line

```text
pydiggs validate PATH... [--format text|json|sarif] [--output FILE]
                 [--checks STEP[,STEP...]] [--diggs-version 2.5.a|2.6|3.0.0]
                 [--schema-path P] [--dictionary-path P] [--schematron-path P]
                 [--fail-on error|warning] [--snippets] [--absolute-paths]
                 [--allow-network]
```

* A directory expands to every `*.xml` under it, recursively, in sorted order; symbolic
  links are not followed.
* A file that cannot be read becomes an entry with `"status": "unreadable"` and the rest
  of the batch still runs.
* `--checks` picks steps from `structure`, `schema`, `dictionary`, `schematron`,
  `geometry`, `deprecation`, `context` (default: all).
* `--output` never writes through a symbolic link.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No finding at or above `--fail-on` (default `error`) |
| 1 | At least one finding at or above `--fail-on`. A file that is not well-formed XML counts here. |
| 2 | Usage error, no input files found, a broken schema/dictionary/Schematron input, or any input that could not be read. 2 outranks 1. |

## The JSON document

The format is **diggs-findings 1.0**, described by a JSON Schema (draft 2020-12) that
ships inside the package (`pydiggs/findings/diggs-findings-1.0.schema.json`) and is
published with this site at
[`schemas/diggs-findings-1.0.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-1.0.schema.json).

```json
{
  "$schema": "https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-1.0.schema.json",
  "format": "diggs-findings",
  "schema_version": "1.0",
  "tool": {
    "name": "pydiggs",
    "version": "1.2.0",
    "reference_data": {"def": "2d76ba6c...", "schemas": "15ce2784..."}
  },
  "files": [
    {
      "path": "site/BH-01.xml",
      "sha256": "9f2c...",
      "diggs_version": "2.6",
      "status": "checked",
      "steps": [{"step": "structure", "status": "passed"}, {"step": "schema", "status": "failed"}],
      "counts": {"error": 1, "warning": 0, "info": 0},
      "findings": [
        {
          "layer": "native",
          "step": "schema",
          "rule_id": "schema.cvc-complex-type.2.4",
          "severity": "error",
          "message": "Element '{http://diggsml.org/schemas/2.6}businessAssociate': This element is not expected. ...",
          "location": {
            "line": 12,
            "column": null,
            "xpath": "/Diggs[1]/documentInformation[1]/DocumentInformation[1]/author[1]/businessAssociate[1]",
            "gml_id": "BA_LADOTD"
          }
        }
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `tool.reference_data` | Upstream commit of each vendored DIGGS source (`schemas`, `def`), from the bundled `PROVENANCE.toml` |
| `files[].path` | Relative to the working directory when the file is under it, else the bare file name; absolute only with `--absolute-paths` / `absolute_paths=True` |
| `files[].sha256` | Digest of the file's bytes (`null` when unreadable) |
| `files[].status` | `checked` or `unreadable`; an unreadable entry also has `error` |
| `findings[].layer` | `native` (pydiggs's own checks); `official` is reserved for the DIGGSml/validation layer |
| `findings[].step` | One of `structure`, `schema`, `codeType`, `dictionary`, `schematron`, `geometry`, `crs`, `coordinate`, `deprecation`, `context` |
| `location.line`, `location.column` | 1-based; the column is known only for XML syntax errors |
| `location.xpath` | The official `diggs:get-path` form: every element as `name()[n]` |
| `location.gml_id` | `gml:id` of the nearest element at or above the problem |
| `source_snippet` | The element's XML, at most 1 KiB; only with `--snippets` / `include_snippets=True` |

There is no timestamp, keys come in a fixed order and findings are sorted (by step, line,
column, severity, rule id, xpath, message), so the same input gives the same bytes.
Compatibility: a new optional field is version 1.1; renaming or removing a field is 2.0.
**Messages are for people**: compare `rule_id`, `step`, `severity` and location, never
the message text. Unlike the legacy logs, a message has no `Check N:` prefix; the rule id
carries the number.

## Rule ids

Every rule id is `<step>.<name>` and matches `^<step>\.[A-Za-z0-9][A-Za-z0-9._-]*$`.
Rule ids are part of the stable contract.

| Rule id | Severity | What it means |
|---|---|---|
| `structure.well-formed` | error | Not well-formed XML (line and column from libxml2) |
| `structure.diggs-root` | error | Root is not `Diggs`, or `documentInformation` is missing or holds more than one `DocumentInformation` |
| `schema.cvc-<rule>` | error / warning | An XML Schema validation rule, e.g. `schema.cvc-complex-type.2.4`, `schema.cvc-datatype-valid.1.2.1`, `schema.cvc-minInclusive-valid`, named from libxml2's error type |
| `schema.libxml2.<TYPE>` | error / warning | A libxml2 error type that names no XSD rule, e.g. `schema.libxml2.SCHEMAV_DOCUMENT_ELEMENT_MISSING` |
| `schema.incomplete` | error | libxml2 could not finish validating the file |
| `dictionary.1` ... `dictionary.12` | as the legacy check | The legacy dictionary checks, numbered as in their messages (1 uncontrolled codeSpace, 2 not whitelisted, 3 not accessible, 4 not a dictionary, 5 code not found, 6 wrong context, 7 name mismatch, 8 wrong measurement, 9 data type, 10 unitless with uom, 11 missing uom, 12 invalid uom) |
| `dictionary.13` | error / warning | Result values (in the property's `dataValues` column) are not terms of the code list its definition names in `valueCodeSpace` |
| `dictionary.offline` | info | Remote code lists were not fetched because network access is off |
| `schematron.<Context>.<property>` | error | A bundled Schematron rule, e.g. `schematron.Borehole.totalMeasuredDepth`, `schematron.Casing.outside-gt-inside` |
| `schematron.Casing.outside-gt-inside-converted`, `schematron.Casing.diameters-numeric-converted` | error | pydiggs's unit-converting casing diameter rule |
| `schematron.<id>` / `schematron.user-<hash>` | error | A rule from your own Schematron file: its `@id`, else a hash of its context and test |
| `geometry.srs-attributes` | warning | A geometry with coordinates lacks `srsName` or `srsDimension` |
| `deprecation.deprecated` | warning | Uses something the DIGGS schema deprecates |
| `deprecation.removed` | error | Uses something the DIGGS schema has removed |
| `context.xlink-href-resolves` | warning | A local `xlink:href="#id"` matches no `gml:id` |
| `context.datablock-arity` | error | A `dataBlock` tuple has a different number of values than `Property` declarations |

### Differences from the legacy checks

`validate()` runs two checks that the legacy `validator` does not, so a file can pass
`dictionary_check()` and still have errors in `validate()`:

* **Dictionary Check 13** (`dictionary.13`), a port of the official
  `dictionary-validation.xsl` check: for a property whose dictionary definition names a
  `valueCodeSpace`, every value in that property's column of the result set's
  `dataValues` (and of every result set citing the same parameters by `xlink:href`)
  must be a term (`gml:id`) of that code list. Empty values and the property's
  `nullValue` are allowed. `dictionary_check()` does not run it.
* **Deprecation** (`deprecation.*`), from the bundled DIGGS deprecation registry
  (`deprecation/DeprecationRegistry.xml`): deprecated items are warnings and removed
  items errors, with the official message text. An entry applies only from the release
  it names (`since`, or `removedIn` for removed items), so the registry's 3.1.0 entries
  never fire on 3.0.0, 2.6 or 2.5.a files; the official module does not make this
  distinction.

## SARIF

`--format sarif` / `report.to_sarif()` writes SARIF 2.1.0 (validated in the test suite
against the official OASIS errata01 schema):

| SARIF | From |
|---|---|
| one `run` per layer | `layer` (`run.properties.layer`) |
| `tool.driver.rules[]` | every rule id in the run, with `shortDescription`, `helpUri` and `defaultConfiguration.level` (the most severe level seen) |
| `artifacts[]` | each file, `uriBaseId: SRCROOT`, with its `sha-256`; a `file:` URI only with absolute paths |
| `results[].level` | `error` → `error`, `warning` → `warning`, `info` → `note` |
| `results[].locations[].physicalLocation.region` | only when the line is known (`startLine`, and `startColumn` for syntax errors) |
| `results[].locations[].logicalLocations[]` | `{fullyQualifiedName: xpath, kind: "element"}` (`"attribute"` for an attribute path) |
| `results[].partialFingerprints["diggsConformanceKey/v1"]` | the conformance key, `step|severity|location|rule_id` |
| `invocations[].toolExecutionNotifications` | one error per unreadable input |

## Conformance keys

`Finding.conformance_key()` returns `(step, severity, location, rule_id)`, where location
is `"xpath:"` plus the xpath with namespace prefixes dropped and every position made
explicit, else `"line:<n>"`, else `"file"`. Two layers agree when their keys match as
multisets over the steps both ran. The message is never compared.

## Privacy

Findings can travel further than the file: into CI logs, issue trackers and code-scanning
dashboards. By default pydiggs keeps them free of the file's content and your file
system layout:

* **No snippets by default.** `source_snippet` copies the element's XML, which is client
  data; it appears only with `--snippets` / `include_snippets=True`, capped at 1 KiB.
* **No absolute paths by default.** Paths are relative to the working directory, or the
  bare file name for files outside it, in JSON and SARIF alike; messages never contain
  the absolute path of the instance. `--absolute-paths` opts in.
* **No timestamps, host names or user names** in either format.
* **No network** unless `--allow-network` / `allow_network=True`, and then only to
  whitelisted dictionary URLs.

Messages can still quote values from the file (a code that is not in a dictionary, for
example). Treat a findings file as you would treat the DIGGS file itself.
