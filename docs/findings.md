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
could not finish, or lost findings to `max_findings`; its findings are partial).
Well-formedness is always checked, because nothing else can run without it: a file
that is not well-formed reports `structure` as `failed` even when `structure` was not
asked for.

### Fail closed

A check that cannot run to the end is never a pass. Whenever pydiggs stops a step for
safety, or libxml2/libxslt stop it, the step is `incomplete` and an **error** finding
says why, so `report.ok` is False and the CLI exits 1:

* `<step>.incomplete`: an XPath error from libxml2 (its node-set limit is 10,000,000
  nodes), an XSLT error while applying the bundled Schematron, a deprecation-registry
  XPath that cannot be evaluated, or libxml2 failing inside schema validation.
* A file with more than **9,900,000 elements** does not run the XPath-based steps
  (dictionary, Schematron, deprecation, geometry, context, structure root checks): each
  gets a `<step>.incomplete` error. Schema validation still runs. A 42 MB file with
  10.5 million elements is reported in about 3 s.
* `structure.findings-truncated` takes the severity of the worst finding it stands for.
* `structure.timeout`: `pydiggs validate --timeout SECONDS` validates each file in its
  own process and stops it after SECONDS (start-up included); that file's steps are all
  `incomplete` and the batch goes on.

### Limits

| Parameter | CLI | Default | What happens |
|---|---|---|---|
| `max_findings` | `--max-findings N` | 10,000 (`None` / `0`: no limit) | Findings past the limit are only counted. One extra `structure.findings-truncated` finding, as severe as the worst one left out (so `report.ok` and `--fail-on` stay right), says how many and from which steps; those steps are `incomplete`. |
| `max_bytes` | `--max-bytes N` | 100 MiB | A larger file raises `InstanceReadError` (an `unreadable` entry in the CLI). The legacy `validator` keeps its 1 GiB limit. |
| (fixed) | | 16 MiB per report | Findings count towards a budget of roughly their JSON size; reaching it truncates exactly like `max_findings`. |
| (fixed) | | 4,096 characters | A longer `message` is cut and ends in "…". A longer `xpath` or `gml_id` is reported as `null` (a cut path could name another element); the line is kept. |

The `structure.findings-truncated` marker describes the report, not the file: it is not
part of any step's status (the steps that lost findings are `incomplete`), it is left
out of conformance comparisons, it is shown by `--format text` even when it is info,
and `counts` include it.

Only regular files are read: a FIFO, socket or device fails at once with "not a
regular file" instead of blocking.

It raises only `InstanceReadError` (the file cannot be read; also an `OSError`),
`ResourceError` (a schema, dictionary or Schematron file you passed, or pydiggs's bundled
data, is missing or broken) and `ValueError` (an unknown `diggs_version` or check name).
XML that is not well-formed is a finding, `structure.well-formed`, not an exception.

## Command line

```text
pydiggs validate PATH... [--format text|json|sarif] [--output FILE]
                 [--checks STEP[,STEP...]] [--diggs-version 2.5.a|2.6|3.0.0]
                 [--schema-path P] [--dictionary-path P] [--schematron-path P]
                 [--fail-on error|warning] [--max-findings N] [--max-bytes N]
                 [--timeout SECONDS] [--snippets] [--absolute-paths] [--base-dir DIR]
                 [--local-dictionaries instance-dir|off] [--allow-network]
```

* A directory expands to every regular `*.xml` file under it, recursively, in sorted
  order; symbolic links and special files are skipped.
* A file that cannot be read becomes an entry with `"status": "unreadable"` and the rest
  of the batch still runs.
* `--checks` picks steps from `structure`, `schema`, `dictionary`, `schematron`,
  `geometry`, `deprecation`, `context` (default: all).
* `--output` is never written through a symbolic link. A new file is created with mode
  `0600`; an existing file is overwritten and keeps its mode.
* Text output escapes control and format characters (Unicode Cc and Cf, except tab) in
  paths and messages, so a file cannot send terminal escape sequences or bidi
  overrides to your terminal.

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
| `files[].path` | See [Paths](#paths); absolute only with `--absolute-paths` / `absolute_paths=True` |
| `files[].sha256` | Digest of the file's bytes (`null` when unreadable) |
| `files[].status` | `checked` or `unreadable`; an unreadable entry also has `error` |
| `findings[].layer` | `native` (pydiggs's own checks); `official` is reserved for the DIGGSml/validation layer |
| `findings[].step` | One of `structure`, `schema`, `codeType`, `dictionary`, `schematron`, `geometry`, `crs`, `coordinate`, `deprecation`, `context` |
| `location.line`, `location.column` | 1-based; the column is known only for XML syntax errors |
| `location.xpath` | The official `diggs:get-path` form: every element as `name()[n]` |
| `location.gml_id` | `gml:id` of the nearest element at or above the problem |
| `source_snippet` | A shallow copy of the element: its attributes and text plus at most eight child elements (their attributes and text only; deeper content is "..."), a comment counting any further children, and only the namespace declarations those use; at most 1 KiB of UTF-8. Only with `--snippets` / `include_snippets=True` |

There is no timestamp, keys come in a fixed order and findings are sorted (by step, line,
column, severity, rule id, xpath, message), so the same input gives the same bytes.
Compatibility: a new optional field is version 1.1; renaming or removing a field is 2.0.
**Messages are for people**: compare `rule_id`, `step`, `severity` and location, never
the message text. Unlike the legacy logs, a message has no `Check N:` prefix; the rule id
carries the number.

Strings in the JSON document are the file's data as found: JSON escapes C0 control
characters, but C1 controls (U+0080 to U+009F) and Unicode bidi or other format
characters (for example U+202E) are left as they are. A consumer that shows paths or
messages to people should escape those itself, as `--format text` does.

### Paths

| Situation | Reported path |
|---|---|
| `base_dir` / `--base-dir DIR` given, file under it | relative to `DIR` |
| `base_dir` given, file outside it | the bare file name |
| no `base_dir`, working directory is `/` or an ancestor of your home directory | the bare file name |
| no `base_dir`, file under the working directory | relative to the working directory |
| otherwise | the bare file name |

A file name that is not valid UTF-8 is shown with its stray bytes as `\xNN` escapes.

## Rule ids

Every rule id is `<step>.<name>` and matches `^<step>\.[A-Za-z0-9][A-Za-z0-9._-]*$`.
Rule ids are part of the stable contract.

| Rule id | Severity | What it means |
|---|---|---|
| `structure.well-formed` | error | Not well-formed XML (line and column from libxml2) |
| `structure.diggs-root` | error | Root is not `Diggs`, or `documentInformation` is missing or holds more than one `DocumentInformation` |
| `schema.cvc-<rule>` | error / warning | An XML Schema validation rule, e.g. `schema.cvc-complex-type.2.4`, `schema.cvc-datatype-valid.1.2.1`, `schema.cvc-minInclusive-valid`, named from libxml2's error type |
| `schema.libxml2.<TYPE>` | error / warning | A libxml2 error type that names no XSD rule, e.g. `schema.libxml2.SCHEMAV_DOCUMENT_ELEMENT_MISSING` |
| `<step>.incomplete` | error | The step could not finish: libxml2 could not finish schema validation, a libxml2/libxslt limit stopped an XPath or the bundled Schematron, or the file has more than 9,900,000 elements (see [Fail closed](#fail-closed)); the step is `incomplete` |
| `structure.timeout` | error | `pydiggs validate --timeout` stopped the file; every step is `incomplete` |
| `dictionary.1` ... `dictionary.12` | as the legacy check | The legacy dictionary checks, numbered as in their messages (1 uncontrolled codeSpace, 2 not whitelisted, 3 not accessible, 4 not a dictionary, 5 code not found, 6 wrong context, 7 name mismatch, 8 wrong measurement, 9 data type, 10 unitless with uom, 11 missing uom, 12 invalid uom) |
| `dictionary.13` | error / warning | Result values (in the property's `dataValues` column) are not terms of the code list its definition names in `valueCodeSpace` |
| `dictionary.offline` | info | A whitelisted remote (http/https) code list that pydiggs does not bundle was not fetched because network access is off. This replaces the `dictionary.3` (or `dictionary.13`) "could not be accessed" warning in `validate()` when `allow_network=False`; with network access on, a failed fetch is still `dictionary.3` at warning. |
| `structure.findings-truncated` | as the worst dropped finding | `max_findings` or the 16 MiB report budget was reached; the rest were counted, not reported |
| `schematron.<Context>.<property>` | error | A bundled Schematron rule, e.g. `schematron.Borehole.totalMeasuredDepth`, `schematron.Casing.outside-gt-inside` |
| `schematron.Casing.outside-gt-inside-converted`, `schematron.Casing.diameters-numeric-converted` | error | pydiggs's unit-converting casing diameter rule |
| `schematron.<id>` / `schematron.user-<hash>` | error | A rule from your own Schematron file: its `@id` (up to 4,096 characters), else a hash of its context and test |
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
  `nullValue` are allowed; `Property/@index` is read as an `xs:integer`. The message
  lists the first five invalid values and at most 20 valid terms (the official module
  lists all of them). `dictionary_check()` does not run it.
* **Local dictionaries.** `validate(local_dictionaries="instance-dir")` (the default;
  CLI `--local-dictionaries`) reads a dictionary named by a relative or `file:`
  codeSpace only if it is a file inside the instance's folder: there is no fallback to
  the working directory, and `..` or a symlink cannot leave the folder. Anything else,
  including a path the operating system rejects, is reported as `dictionary.3` (or
  `dictionary.13` for a Check 13 value code list; warning, "could not be accessed") with
  a note that says why; the check is made before the file is opened, so the finding is
  the same whether or not the file exists. Each file is read once per instance however
  its path is spelled, under `max_bytes` and only if it is a regular file, and at most
  16 different local dictionaries are read per instance (the rest are `dictionary.3`
  with a note). All local dictionaries of an instance share one byte budget: what
  `max_bytes` leaves after the instance itself. `"off"` reads no local dictionary. `dictionary_check()`
  keeps the 1.1 behaviour: next to the instance, then the working directory, and any
  `file:` URI.
* **`dictionary_path`.** In `validate()` the dictionary you pass serves both
  `properties.xml` URLs (https and http), the legacy `DIGGSTestPropertyDefinitions.xml`
  URLs (which are looked up in it first) and the fallback for any other
  `…/properties.xml`. `dictionary_check()` keeps 1.1 behaviour: the bundled
  `codes/properties.xml` is registered after it under the same URLs and replaces it.
* **Offline dictionaries.** `validate()` reports an unbundled remote dictionary as
  `dictionary.offline` (info) when network access is off; `dictionary_check()` still
  reports Check 3 as a warning.
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

## Security notes

* Schematron runs with `generate-paths=false`: lxml builds no `@location` strings (they
  copied the instance's namespace URIs, cost memory per failed assert and time per
  sibling). pydiggs locates each failed assert from the trusted schema instead: the
  n-th `svrl:fired-rule` of a pattern belongs to the n-th element, in document order,
  matched by one of that pattern's rule contexts. Nothing from the instance is
  evaluated as XPath. libxml2 error-log paths are checked against their `/name[n]`
  grammar and resolved by walking the tree.
* A broken user Schematron file (a test that does not compile, or an unknown function)
  is a `ResourceError` (CLI exit 2), not a finding.
* Messages about an unreadable file do not contain its path; the report shows the path
  as described in [Paths](#paths).

### Residual risks

pydiggs's own code is bounded: on the security review's test files (up to 10 MB, default
settings) no pydiggs-owned path took more than about 15 s or 400 MB beyond parsing. Three
costs belong to lxml and libxml2/libxslt and are not bounded by pydiggs. Measured on an
Apple M3 Max MacBook Pro (one process, default settings, lxml 6.1.3, libxml2 2.14.6):

| Cost | Input | Time |
|---|---|---|
| Schematron (libxslt runs lxml's ISO skeleton; roughly quadratic in sibling count) | 2 MB, 32,766 failing sibling Boreholes | 22 s (pydiggs's own Schematron work: under 1 s) |
| | 8 MB, about 125,000 failing siblings | over 200 s |
| | 11.5 MB, 20,000 sibling Property elements, no failures | 180 s |
| Schema validation (lxml builds an XPath for every libxml2 error by counting siblings) | 2 MB, 73,583 sibling schema errors | 40 s |
| | 8 MB of the same | over 400 s |
| Parsing (the lxml tree) | 42 MB, 10.5 million elements | 1.4 GiB |

Services that validate files from untrusted sources should therefore validate **each
upload in its own folder**, in a separate process with a **time and memory limit**:
`pydiggs validate --timeout SECONDS` does the first (a file that runs out of time is a
`structure.timeout` error), and `--max-bytes` keeps inputs small. Also:

* **`instance-dir`** reads any file in the instance's folder as a dictionary when a
  codeSpace names it: in a shared upload folder that includes other users' uploads,
  and a hard link to a file elsewhere cannot be told apart from a real file.
* **The legacy entry points** (`validator`, `pydiggs schema_check` and the other
  subcommands, `detect_diggs_version`) keep their 1.1.1 behaviour by design: local
  dictionaries next to the instance, then in the working directory, and any `file:`
  URI; a 1 GiB size limit; Schematron with lxml's location strings; element paths that
  cost time in proportion to depth times the number of findings; a local dictionary
  parsed once per spelling of its path; and a `dictionary_path` that the bundled
  `codes/properties.xml` replaces. Use `validate()` for untrusted input.

## Privacy

Findings can travel further than the file: into CI logs, issue trackers and code-scanning
dashboards. By default pydiggs keeps them free of the file's content and your file
system layout:

* **No snippets by default.** `source_snippet` copies the element's XML, which is client
  data; it appears only with `--snippets` / `include_snippets=True`, capped at 1 KiB.
* **No absolute paths by default.** Paths follow [Paths](#paths) in JSON, SARIF and
  text alike, so running from `/` or above your home directory never spells out your
  directory layout; messages never contain the absolute path of the instance.
  `--absolute-paths` opts in.
* **No timestamps, host names or user names** in either format.
* **No network** unless `--allow-network` / `allow_network=True`, and then only to
  whitelisted dictionary URLs.

Messages can still quote values from the file (a code that is not in a dictionary, for
example). Treat a findings file as you would treat the DIGGS file itself.
