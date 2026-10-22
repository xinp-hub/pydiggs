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
records what happened to each in `report.steps`: `passed`, `partial` (no error, but part
of the step could not be checked: the dictionary step when a code list was not fetched
because network access is off, `dictionary.offline`), `failed` (at least one error),
`skipped` (not asked for, the file is not well-formed XML, or it is not a DIGGS file) or
`incomplete` (the step could not finish, or lost findings to `max_findings`; its
findings are partial). A step with an error is `failed` or `incomplete`, never
`partial`.

A file whose root element is not `Diggs` (any namespace) is **not a DIGGS file**: it
gets one `structure.diggs-root` error naming the root it has, `report.status` is
`not-diggs`, `diggs_version` is `None`, and every other step is `skipped`, since no
DIGGS check means anything for it. In the test corpus,
`tests/fixtures/official/known_invalid/2.6_Temporal_Spatial_CRS_Example.xml` (a
`gml:Dictionary`, not a DIGGS instance) is reported this way.

`report.verdict` sums a file up in one word, derived only from its status, steps and
findings (in this order): `not-diggs`; `not-valid` (any error); `partly-checked` (no
error, but a step is `partial` or `incomplete`: a step stopped by a safety limit always
has an error, so it is `not-valid`, but one whose warnings were cut by `max_findings`
may have none); `valid-with-warnings`; `valid`. An unreadable input's verdict is
`unreadable`. The text output starts each file's line with it. Text output lists
errors and warnings, not info findings; for a `partly-checked` file it adds a
`not fully checked:` line naming the `partial` or `incomplete` steps and lists its
`dictionary.offline` findings, so the reason is always shown.

Well-formedness is always checked, because nothing else can run without it: a file
that is not well-formed reports `structure` as `failed` even when `structure` was not
asked for.

### Fail closed

A check that cannot run to the end is never a pass. Whenever pydiggs stops a step for
safety, or libxml2/libxslt stop it, the step is `incomplete` and an **error** finding
says why, so `report.ok` is False and the CLI exits 1:

* `<step>.incomplete`: an XPath error from libxml2 (its node-set limit is 10,000,000
  nodes), an XSLT error while applying the bundled Schematron, a deprecation-registry
  XPath that cannot be evaluated, or libxml2 failing inside schema validation. Any
  other unexpected error inside a step (a bug) is reported the same way, with the
  exception's type and a short text, so it can neither escape `validate()` nor leave
  the step `passed`.
* `structure.markup-entity`: the file's internal DTD declares an entity whose value
  contains markup (`<`, also when written `&#60;`), such as
  `<!ENTITY e "<b/><b/>">`. One such entity can multiply the number of elements
  without the file growing, so `validate()` reports this one error and runs no other
  check. The prolog is read first, with entities left unexpanded; if that parser
  cannot read the file (lxml's push parser rejects UTF-32 with a BOM, for example),
  the whole file is parsed without expanding entities, and the DTD the main parser
  saw is checked again after the full parse. Entities whose value is plain text
  (`<!ENTITY org "Acme Ltd">`) still work.
* `structure.namespace-too-long`: a namespace URI longer than **1,024 characters** is
  declared, in the file or as an `xmlns` default from the internal DTD's ATTLIST. Every
  element's tag carries its namespace, so one long URI multiplies the cost of every
  step; real DIGGS URIs are under 100 characters. One C-level pass over the namespace
  declarations decides this; `validate()` reports this one error and runs no other
  check.
* `structure.entity-expansion`: the file has an internal DTD and, once parsed, its
  text (element text, tails, attribute values including ATTLIST defaults, and
  namespace URIs) is more than **twice its raw size**, both counted in UTF-8 bytes.
  Plain-text entities that stay under that still work (an `&deg;` here and there
  costs nothing). The text is measured in one pass, only when there is an internal
  DTD (with one, the file is parsed a second time with the internal subset's ATTLIST
  defaults filled in, to measure them); `validate()` then reports this one error and
  runs no other check. Only the internal DTD subset is ever used: an external subset
  (a `SYSTEM` id) is never opened by `validate()`, so a local file, a FIFO or a network
  share named there contributes nothing and is not even touched.
  Every size limit (`max_bytes`, the 10 MB the security review measures against)
  refers to the raw file, so this keeps the parsed size within a small factor of it.
* A file with more than **9,900,000 elements** does not run the XPath-based steps
  (dictionary, Schematron, deprecation, geometry, context, structure root checks): each
  gets a `<step>.incomplete` error. Schema validation still runs. The elements are
  counted on the parsed tree (about 0.1 s per million), so no encoding or entity can
  hide them.
* `schematron.incomplete` when an element has more than **50,000 child elements**
  (`MAX_SCHEMATRON_SIBLINGS`): the Schematron rules (bundled or your own) are not run,
  because libxslt can take minutes on such a file. pydiggs's own casing diameter rule
  still runs, so its findings can appear next to the error. Schema validation and the
  other steps are not affected; `schematron_check()` is unchanged.
* `schematron.incomplete` when the rules would fire on more than **100,000 elements**
  (`MAX_SCHEMATRON_FIRED`), counted before they run: libxslt's time grows with the
  square of that number (about 3 s at the limit, minutes at 1 million). Bundled and
  user rules alike; a context written `*[local-name()='X']...` is counted with lxml's
  C-level tag filter, any other with XPath `count()`, and a context XPath cannot
  evaluate on its own (a `$variable`) is not counted. The casing rule still runs.
* `dictionary.incomplete` when a local dictionary was not read because of a safety
  limit: more than 16 different local dictionaries, more than their byte budget (see
  [Limits](#limits)), an internal DTD that declares a markup entity, or a libxml2
  resource limit (entity amplification, nesting depth, a huge text node, an overlong
  name, a comment, processing instruction or CDATA section over libxml2's 10 MB node
  limit). The codeSpace also gets its `dictionary.3` warning. Limits are recognised by
  libxml2's error code or its own limit wording; on a libxml2 build that reports a
  limit with neither, it is indistinguishable from a syntax error and gives only the
  warning.
* `dictionary.incomplete` when the dictionary checks would cost too much: a Definition
  that lists more than 1,000 source or conditional paths or names, has more than
  10,000 nodes, or uses a path of more than 64 steps; more than 200,000,000 Check 13
  work units (one per `dataValues` character read, separators included, and a weight
  per element, `index`, group, reference, token, field and merged value, calibrated by
  `scripts/calibrate_check13.py` so that a unit costs at most about 60 ns whatever the
  loop: the whole budget is about 12 s of CPU; typical 10 MB files need 10 to 15 % of
  it, and the densest one tried, 4.8 million cells, about 83 million or 42 %); more
  than 10,000,000 values kept in Check 13's per-key caches (merged values, column
  unions, sorted columns, invalid values: a few hundred MB at most); or more than
  5,000,000 dictionary work steps in one instance (path matching, sibling reads).
  Real DIGGS definitions list at most 9 paths, and the largest corpus file needs
  39,000 steps; files shaped like real ones reach the budget at about 30 to
  40 MB (an AtterbergExample measurement repeated 3,000 times, 26.5 MB, needs
  3.7 million), so a very large file can be `incomplete` for this reason alone.
* `context.incomplete` when `dataBlock`s inside nested `measurement`s would need more
  than 5,000,000 comparisons.
* `structure.findings-truncated` takes the severity of the worst finding it stands for.
* `structure.timeout`: `pydiggs validate --timeout SECONDS` validates each file in its
  own process and stops it after SECONDS (start-up included); that file's requested
  steps are `incomplete` and the batch goes on. A worker that dies (killed, out of
  memory) is reported the same way.
* `structure.internal-error`: `pydiggs validate` caught an unexpected exception for one
  file (a bug, or `MemoryError`); that file's requested steps are `incomplete` and the
  batch goes on. Only a broken resource you passed (`ResourceError`) stops the batch.

### Limits

| Parameter | CLI | Default | What happens |
|---|---|---|---|
| `max_findings` | `--max-findings N` | 10,000 (`None` / `0`: no limit) | Findings past the limit are only counted. One extra `structure.findings-truncated` finding, as severe as the worst one left out (so `report.ok` and `--fail-on` stay right), says how many and from which steps; those steps are `incomplete`. |
| `max_bytes` | `--max-bytes N` | 100 MiB | A larger file raises `InstanceReadError` (an `unreadable` entry in the CLI). The limit is on the raw bytes; entities may make the parsed text at most twice that (`structure.entity-expansion`). The legacy `validator` keeps its 1 GiB limit. |
| (fixed) | | 20 MiB of local dictionaries per instance | All local dictionaries of one instance share this budget (or what `max_bytes` leaves after the instance, if less). A parsed dictionary takes up to about 35 times its file size in memory (about 800 MB peak for 20 MiB of `<b/>` elements); dictionaries with markup entities are refused, so entities cannot multiply that. A dictionary past the budget is not read: `dictionary.3` plus a `dictionary.incomplete` error. Real DIGGS dictionaries are under 1 MB. |
| (fixed) | | 50,000 child elements under one element | Above it the Schematron step is skipped with a `schematron.incomplete` error (the casing rule still runs). |
| (fixed) | | 100,000 elements the Schematron rules fire on | Counted before the rules run; above it, as above. |
| (fixed) | | 1,000 paths or names and 10,000 nodes per dictionary Definition; 64 steps per path; 5,000,000 dictionary work steps per instance | Above either, the dictionary step is `incomplete` with a `dictionary.incomplete` error. Check 6 and Check 8 messages list at most 20 paths, then "... and K more". |
| | `--timeout SECONDS` | none | Positive, at most 86,400 (one day). |
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
      "verdict": "not-valid",
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
| `files[].status` | `checked`, `not-diggs` (the root is not `Diggs`; `diggs_version` is `null` and only `structure` ran) or `unreadable` (the entry also has `error`) |
| `files[].verdict` | `valid`, `valid-with-warnings`, `not-valid`, `partly-checked`, `not-diggs` or `unreadable`, derived as described under [validate()](#findings-json-and-sarif-output); SARIF carries `status` and `verdict` in each artifact's `properties` |
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
| `structure.diggs-root` | error | Root is not `Diggs` (then the file is `not-diggs` and nothing else runs), or `documentInformation` is missing or holds more than one `DocumentInformation` |
| `schema.cvc-<rule>` | error / warning | An XML Schema validation rule, e.g. `schema.cvc-complex-type.2.4`, `schema.cvc-datatype-valid.1.2.1`, `schema.cvc-minInclusive-valid`, named from libxml2's error type |
| `schema.libxml2.<TYPE>` | error / warning | A libxml2 error type that names no XSD rule, e.g. `schema.libxml2.SCHEMAV_DOCUMENT_ELEMENT_MISSING` |
| `<step>.incomplete` | error | The step could not finish: libxml2 could not finish schema validation, a libxml2/libxslt limit stopped an XPath or the bundled Schematron, the file has more than 9,900,000 elements, an element has more than 50,000 children or the rules would fire on more than 100,000 elements (Schematron), a local dictionary was refused by a safety limit, or dictionary path matching passed its limits (see [Fail closed](#fail-closed)); the step is `incomplete` |
| `structure.markup-entity` | error | The internal DTD declares an entity whose value contains markup; no other check ran (see [Fail closed](#fail-closed)) |
| `structure.entity-expansion` | error | The internal DTD's entities or ATTLIST defaults make the parsed text more than twice the file's raw size; no other check ran |
| `structure.namespace-too-long` | error | A namespace URI is longer than 1,024 characters; no other check ran |
| `structure.timeout` | error | `pydiggs validate --timeout` stopped the file, or its worker died; the requested steps are `incomplete` |
| `structure.internal-error` | error | `pydiggs validate` caught an unexpected error for this file; the requested steps are `incomplete` |
| `dictionary.1` ... `dictionary.12` | as the legacy check | The legacy dictionary checks, numbered as in their messages (1 uncontrolled codeSpace, 2 not whitelisted, 3 not accessible, 4 not a dictionary, 5 code not found, 6 wrong context, 7 name mismatch, 8 wrong measurement, 9 data type, 10 unitless with uom, 11 missing uom, 12 invalid uom) |
| `dictionary.13` | error / warning | Result values (in the property's `dataValues` column) are not terms of the code list its definition names in `valueCodeSpace` |
| `dictionary.offline` | info | A whitelisted remote (http/https) code list that pydiggs does not bundle was not fetched because network access is off. This replaces the `dictionary.3` (or `dictionary.13`) "could not be accessed" warning in `validate()` when `allow_network=False`; with network access on, a failed fetch is still `dictionary.3` at warning. |
| `structure.findings-truncated` | as the worst dropped finding | `max_findings` or the 16 MiB report budget was reached; the rest were counted, not reported |
| `schematron.<Context>.<property>` | error | A bundled Schematron rule, e.g. `schematron.Borehole.totalMeasuredDepth`, `schematron.Casing.outside-gt-inside` |
| `schematron.Casing.outside-gt-inside-converted`, `schematron.Casing.diameters-numeric-converted` | error | pydiggs's unit-converting casing diameter rule |
| `schematron.<id>` / `schematron.user-<hash>` | error | A rule from your own Schematron file: its `@id` (up to 4,096 characters), else a hash of its context and test |
| `geometry.srs-attributes` | warning | A geometry object (one of the nine the official geometry module checks, with or without coordinates) has no `srsName` and `srsDimension` on itself or any ancestor: one warning per object, saying either what is missing or that they sit on a child (`gml:pos`/`gml:posList`, allowed by GML 3.2) when DIGGS best practice is the object itself |
| `deprecation.deprecated` | warning | Uses something the DIGGS schema deprecates |
| `deprecation.removed` | error | Uses something the DIGGS schema has removed |
| `context.xlink-href-resolves` | warning | A local `xlink:href="#id"` matches no `gml:id` |
| `context.datablock-arity` | error | A `dataBlock` tuple has a different number of values than `Property` declarations |

### Differences from the legacy checks

`validate()` differs from the legacy `validator` in these ways. Some of them (Check 13,
Check 8, deprecation) mean a file can pass `dictionary_check()` and still have errors
in `validate()`:

* **Dictionary Check 13** (`dictionary.13`), a port of the official
  `dictionary-validation.xsl` check: for a property whose dictionary definition names a
  `valueCodeSpace`, every value in that property's column of the result set's
  `dataValues` (and of every result set citing the same parameters by `xlink:href`)
  must be a term (`gml:id`) of that code list. Empty values and the property's
  `nullValue` are allowed; `Property/@index` is read as an `xs:integer`. The message
  lists the first five invalid values and at most 20 valid terms (the official module
  lists all of them). `dictionary_check()` does not run it.
* **Dictionary Check 8** reads `//` in a definition's conditional paths as "any
  descendant", as XPath does. `dictionary_check()` reads
  `ancestor::diggs:measurement//diggs:procedure` as "`procedure` is a direct child of
  `measurement`", which is never true in real files (the path is
  `measurement/Test/procedure`), so it skips Check 8 for properties under a
  measurement. In the test corpus this adds two `dictionary.8` errors in `validate()`
  for each of the two AtterbergExample files ("uscs group symbol" and "natural water
  content" are not allowed in that type of measurement); `dictionary_check()` still
  passes them (1.1 behaviour, kept byte for byte). The same reading applies to Check 6
  source paths of the form `ancestor::…//…` (none of the bundled dictionaries has
  one). Predicates in definition paths (`[text()="…"]`) are ignored, as before, so
  matching is lenient; in `validate()` a `/` inside a predicate no longer splits the
  path into steps (`dictionary_check()` keeps splitting it).
* **Geometry SRS.** `validate()` follows the official geometry module
  (`modules/geometry-validation.xsl`, DIGGSml/validation commit `5f7a365`). It checks
  the same nine objects: `LinearExtent`, `MultiPointLocation`, `MultiCurve`, `Solid`,
  `PointLocation`, `PlanarObservationRepresentation`, `PlanarSurface`,
  `MultiPlanarSurface` and `RectifiedGrid`, with or without coordinates (so an empty
  `<LinearExtent gml:id="l"/>` is checked; `gml:Point`, `gml:LineString` and other GML
  primitives are not). An object passes when it or an ancestor has both `srsName` and
  `srsDimension` (present, even if empty); when only a child (`gml:pos`,
  `gml:posList`) has them, as GML 3.2 allows, it gets one best-practice warning;
  otherwise one warning names what is missing. The official module reports that last
  case, an object with no SRS on itself, any ancestor or any child, as an error;
  pydiggs keeps it a warning, for coordinate-less objects too. The legacy
  `context_check()` keeps its own list (`Point`, `LineString`, `LinearExtent`,
  `Polygon`, `Curve`, `Surface`, `MultiPoint`, `MultiCurve`, `MultiSurface`,
  `PointLocation`, `MultiPointLocation`), skips objects with no coordinates and no
  `srsName`, and gives one warning per missing attribute on the object itself, so
  `DocumentationExample.xml` gets 62 there and 31 in `validate()`. Across the 73
  corpus files (the pydiggs test files and the desktop app's test data) the new list
  changes one verdict: the 3.x `Aeromag.xml` goes from valid to valid-with-warnings
  (its `RectifiedGrid` has SRS only on a child).
* **Local dictionaries.** `validate(local_dictionaries="instance-dir")` (the default;
  CLI `--local-dictionaries`) reads a dictionary named by a relative or `file:`
  codeSpace only if it is a file inside the instance's folder: there is no fallback to
  the working directory, and `..` or a symlink cannot leave the folder. Anything else,
  including a path the operating system rejects, is reported as `dictionary.3` (or
  `dictionary.13` for a Check 13 value code list; warning, "could not be accessed") with
  a note that says why; the check is made before the file is opened, so the finding is
  the same whether or not the file exists. Each file is read once per instance however
  its path is spelled, under `max_bytes` and only if it is a regular file, and at most
  16 different local dictionaries are read per instance. All local dictionaries of an
  instance share one byte budget: 20 MiB, or what `max_bytes` leaves after the instance
  if that is less. A dictionary left unread by the cap or the budget is `dictionary.3`
  with a note and makes the step `incomplete` with a `dictionary.incomplete` error. A
  local dictionary that is simply not well-formed XML (no safety limit involved) is a
  `dictionary.3` warning, "could not be accessed", by design: that is what the
  official validator reports, and whoever controls the dictionary could as well
  write a permissive one.
  `"off"` reads no local dictionary. `dictionary_check()` keeps the 1.1 behaviour: next
  to the instance, then the working directory, and any `file:` URI.
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
  sibling). pydiggs locates each failed assert from the trusted schema instead, read
  from the same included and expanded schema lxml compiles, with the active phase
  applied: the n-th `svrl:fired-rule` of a pattern belongs to the n-th element, in
  document order, matched by one of that pattern's rule contexts (the first matching
  rule wins). The pairing is checked: the `active-pattern` id, the number of
  `fired-rule` elements and each one's context must agree with the elements found,
  or that pattern's failures have no location (never a wrong one). Contexts that match
  attributes, text or the root are never located. Nothing from the instance is
  evaluated as XPath. libxml2 error-log paths are checked against their `/name[n]`
  grammar and resolved by walking the tree.
* A broken user Schematron file (a test that does not compile, or an unknown function)
  is a `ResourceError` (CLI exit 2), not a finding. That includes an XSLT error that only
  some instance triggers while the rules run: your rules are trusted input, so such an
  error is reported as a broken resource and stops the batch rather than being blamed
  on the file. Test your rules on representative files before using them on uploads.
* Messages about an unreadable file do not contain its path; the report shows the path
  as described in [Paths](#paths).

### Residual risks

pydiggs's own code is bounded. On the security reviews' test files (up to 10 MB raw,
default settings) no pydiggs-owned path took more than about 11 s of CPU (1,000
conditional paths tried against 180,000 elements, stopped by the work budget) or about
350 MB beyond parsing (ancestor-name sets under a 250-deep chain with 260,000 parents).
The geometry rule is one pass over the tree (250 nested `MultiCurve` around 170,000
`PointLocation`, 9 MB: 1.4 s, where walking each object's ancestors and subtree took
99 s). Check 13 reads each ResultSet once for all the Properties of the
PropertyParameters it holds (its nearest ResultSet, at any depth) or cites by
`xlink:href` (400 of them over 2,000 rows of one 2 KB field: 1.4 s, where it took
44 s), and a column's values over a group's ResultSets are merged once for all the
group's properties. The security review's 10 MB shapes are linear or stop at the
budget: 55,000 ResultSets citing a 30,000-Property group, 1.4 s; 500 ResultSets citing
one group up to 500 times each, 0.6 s; 20,000 PropertyParameters outside `parameters`
over 7 million separators, 1.0 s (the first and last of these shapes ran for more than
460 s before, the last growing to 3 GB); 50,000 coded Properties read from 50,000
citing ResultSets, 2.0 s; 200 ResultSets each citing the same 400 groups of 400
columns, `incomplete` after about 5 s. Each `index` belongs to its nearest
PropertyParameters only, so 250 nested PropertyParameters around 480,000 index holders
(9.4 MB) take 1.5 s and about 240 MB, where re-reading every holder for each enclosing
group took 102 s and 3.7 GB. Check 13 keeps no rows, only the distinct values of
the columns its properties ask for, and one column set per group of
PropertyParameters (2,200 ResultSets of 2,200 cells citing one group, 9.9 MB: about
205 MB beyond parsing, where keying the sets by ResultSet used 718 MB; one 9 MB row
of 9 million empty fields takes 0.2 s and 113 MB, where reading every column took
2.1 GB). Groups read by the same ResultSets share one merge (300 groups each cited by
the same 300 ResultSets, 2,700 distinct values each: 1.3 s and 171 MB); when every
group is read by a different set of ResultSets, the merges are counted against the
kept-values cap instead and the step stops `incomplete` (1.5 s, about 250 MB).

The costs below belong to lxml and libxml2/libxslt and are not bounded by
pydiggs. Measured on an Apple M3 Max MacBook Pro in CPU seconds (one process, default
settings, lxml 6.1.3, libxml2 2.14.6, libxslt 1.1.43; wall time on a busy machine is
higher):

| Cost | Input | CPU |
|---|---|---|
| Schematron (libxslt; grows with the square of the number of elements the rules fire on, whatever their parents) | 1.8 MB, 100,000 fired elements, all failing (the most `validate()` runs) | 3.4 to 5.5 s |
| | 1.7 MB, 100,000 fired elements, all passing | 3.6 s |
| | 9 MB, about 1 million fired elements (21 parents × 50,000) | 174 s and 1.7 GB before the cap; now refused (`schematron.incomplete`) in 1.7 s |
| | 9.9 MB, 100,000 fired elements among 1.1 million same-named elements the rules do not fire on (under both caps) | 35 to 43 s and about 500 MB; 22 to 29 s with `Casing` |
| | 9 to 10 MB with no element a rule fires on | 5 to 6 s |
| | 1 MB, 18,000 sibling features (no failures) | 0.1 s (31 s with the pre-1.2 rule contexts) |
| Schema validation (lxml builds an XPath for every libxml2 error by counting siblings) | 2 MB of sibling schema errors (37,000 siblings) | 14 s |
| | 4 MB of the same | 107 s |
| | 8 MB of the same | over 400 s |
| Parsing (the lxml tree) | 42 MB, 10.5 million elements | 1.4 GiB, 1.4 s |
| Local dictionaries (parsed trees) | 20 MiB of `<b/>` elements, the most the budget allows | about 800 MB |

The bundled rules' contexts were rewritten for 1.2 (`*[local-name()='C'][parent::*[local-name()='P']]`
instead of `//*[local-name()='P']/*[local-name()='C']`; same elements, same verdicts).
The old form made libxslt quadratic in the number of siblings of any element (over
400 s on several 8 to 11 MB files). No form tried is linear in both that and the
number of elements a rule fires on, so `validate()` caps both: more than 50,000
children under one element, or more than 100,000 fired elements, skips Schematron
with a `schematron.incomplete` error.

Entities are bounded relative to the raw file. An internal DTD entity with markup is
refused (`structure.markup-entity`) before any check runs, in every encoding the parser
reads: usually decided from the prolog alone, and for UTF-32 with a BOM, which lxml's
push parser cannot read, after one parse that expands no entity. A file whose
plain-text entities make its text more than twice its raw size is refused after the
parse (`structure.entity-expansion`); the parse itself is libxml2's, which limits
entity amplification to a factor of about 5 and any single text, comment, PI, CDATA or
attribute node to 10 MB (without `huge_tree`, which pydiggs never sets), so the
largest parse pydiggs can be asked to do is bounded by the raw size before its own 2x
check runs. A 9.6 MB file that would expand to 12 million
elements is reported in 0.1 s and 54 MB, where 1.2 development builds used 1.7 GB;
dataBlocks of entity-expanded text that took 9 s are refused in under 0.1 s. Local
dictionaries with markup entities are refused the same way (`dictionary.incomplete`);
one 20 MiB dictionary that expanded to 26 million elements used 3.4 GB before.

Services that validate files from untrusted sources should therefore validate **each
upload in its own folder**, in a separate process with a **time and memory limit**.
`pydiggs validate --timeout SECONDS` gives a time limit (a file that runs out of time,
or whose worker dies, is a `structure.timeout` error) and `--max-bytes` keeps inputs
small. Also:

* **`--timeout` has a floor.** Each file gets a fresh interpreter (the `spawn` start
  method, on every OS), which imports pydiggs and lxml and compiles the bundled
  schemas before validating: about 0.3 s of CPU for a small file, and several seconds
  of wall time on a loaded machine. The limit includes that start-up, so values under
  about 10 s can stop files that would pass; use 30 s or more on shared machines.
* **`spawn` re-imports your main module.** A script that calls
  `pydiggs.cli.main(["validate", "--timeout", ...])` must do it under
  `if __name__ == "__main__":`, or each worker runs the script again. The `pydiggs`
  command needs nothing.
* **No memory limit.** `--timeout` limits time only. Workers have no memory or CPU
  rlimit, run with your privileges and inherit your environment; set memory limits
  with the operating system or the container (for example `ulimit -v`, a cgroup, or a
  Kubernetes memory limit).
* **`instance-dir`** reads any file in the instance's folder as a dictionary when a
  codeSpace names it: in a shared upload folder that includes other users' uploads,
  and a hard link to a file elsewhere cannot be told apart from a real file.
* **The legacy entry points** (`validator`, `pydiggs schema_check` and the other
  subcommands, `detect_diggs_version`) keep their 1.1.1 behaviour by design: local
  dictionaries next to the instance, then in the working directory, and any `file:`
  URI; a 1 GiB size limit; internal DTD entities expanded; Schematron with lxml's
  location strings; element paths that cost time in proportion to depth times the
  number of findings; a local dictionary parsed once per spelling of its path; and a
  `dictionary_path` that the bundled `codes/properties.xml` replaces. They use the same
  rewritten bundled Schematron rules, so their verdicts and output are unchanged. Use
  `validate()` for untrusted input.

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
