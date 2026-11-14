# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`pydiggs convert`: AGS4 to DIGGS on the command line.** `pydiggs convert site.ags -o
  site.xml [--report findings.json] [--format text|json] [--fail-on error|loss] [--force]`
  writes the DIGGS 3.0.0 document of the preview converter (mapping table 0.1) and
  prints every loss. Exit codes follow `pydiggs validate`: 0, 1 for findings at or above
  `--fail-on` (`error`, the default, includes an output that fails validation; `loss`
  adds any loss), 2 for a usage error, an input that cannot be read or is not AGS4, or an
  output that cannot be written. Outputs are written to a temporary file and moved into
  place; an existing output is replaced only with `--force`, and the input is never
  overwritten.
- **HTTP API: `POST /v1/convert` and `POST /v1/ags4/check`.** `/v1/convert` takes an
  AGS4 file (raw `application/octet-stream`, `text/plain` or `text/csv`, or multipart)
  and answers one JSON object, `ConversionResult`: the DIGGS XML as `output_base64` with
  `media_type`, `output_name` and `output_sha256`, the findings `report` about the
  source, the `validation` of the output, `complete`, `lossless`, `diggs_version` and
  `mapping_version`. `/v1/ags4/check` answers the diggs-findings 1.1 (or SARIF) report of
  the AGS4 rules, and takes an optional `dictionary` part, since pyDIGGS bundles no AGS
  data dictionary. Both have the limits of `/v1/read`; a file that is not AGS4 is 422
  `invalid-document` with `kind` `not-ags4` or `unsupported-version` for a conversion,
  and a finding for a check. The OpenAPI document describes both.
- **Stdio sidecar protocol 1.2: `convert` and `ags4.check`.** A host that negotiates 1.2
  gets both methods, advertised as `capabilities.convert` and
  `capabilities.ags4_check`; 1.0 and 1.1 sessions are unchanged and answer
  `method-not-found` for them. `convert` answers the same `ConversionResult` as the HTTP
  API; `ags4.check` takes `dictionary_base64` or `dictionary_path`. `openrpc.json`
  describes protocol 1.2.
- The conversion and the check run in the browser (Pyodide) and give CPython's bytes;
  the Pyodide smoke now converts an AGS4 file and compares.
- Documentation: [AGS4 files](https://xinp-hub.github.io/pydiggs/ags4/) and
  [AGS4 to DIGGS](https://xinp-hub.github.io/pydiggs/convert/).
- **`pydiggs.capabilities()`: what this pyDIGGS can do, machine-readable.** It returns a
  `Capabilities` with one `Operation` per thing pyDIGGS offers (`validate`, `read`,
  `detect`, `rules.list`, `rules.explain`, `ags4.read`, `ags4.write`, `ags4.check`,
  `convert.ags4-to-diggs`, `export.pandas`, `export.polars`, `gis.transform`,
  `export.geodataframe`, `export.gpkg`, `export.geoparquet`, `export.geojson`): its
  stability (`stable`, `preview` or `experimental`; the AGS4 and conversion operations
  are `preview`), inputs and outputs
  (`diggs-3.0.0`, `ags4-4.1.1`, `diggs-findings-1.1`, `diggs-model-1.0`, media types),
  its options as `OptionSpec`s (type, default, choices, a plain-language label), the
  extras it `needs`, and whether they are `available` here. Availability is found
  without importing anything optional, so the same wheel answers correctly in Pyodide,
  where only the core is installed. An app can build its menus from it instead of from
  hand edits per release:

  ```python
  import pydiggs

  for op in pydiggs.capabilities().operations:
      print(op.id, op.stability, op.available)
  ```

  `Capabilities.to_dict()` is a **diggs-capabilities 1.0** document, with its JSON
  Schema packaged (`pydiggs.capabilities_schema()`, `CAPABILITIES_SCHEMA_VERSION`,
  `CAPABILITIES_SCHEMA_URL`) and published beside the findings and model schemas. The
  same document is `pydiggs capabilities --json` on the command line (without `--json`,
  a short table), `GET /v1/capabilities` on the HTTP server (off, like `/v1/versions`,
  with `versions_enabled=False`), and the MCP server's new `pydiggs://capabilities`
  resource. Every name is new; nothing existing changes.
- **Plain-language rule guidance.** Every rule in `rule_catalogue()` and every id
  `explain_rule()` knows now has `RuleInfo.guidance`, a new `Guidance` object (`title`,
  `meaning`, `fix`, `locale`) written for geotechnical and geoenvironmental engineers who
  are not DIGGS or XML specialists: `explain_rule("dictionary.5").guidance.title` is
  `'Unknown code'`. The field defaults to `None` (an unknown id), so existing code that
  builds a `RuleInfo` is unaffected, and `to_dict()` gains a `guidance` key. The common
  `schema.cvc-*` rules have their own text; other family members get their family's. The
  HTTP `/v1/rules` routes, the sidecar's `rules.*` methods and the MCP `explain_rule` and
  `list_rules` tools return it too (`openapi.json` and `openrpc.json` add the optional
  `guidance` property). Findings documents are unchanged and stay `diggs-findings` 1.1:
  apps look guidance up by `rule_id`. Only English ships; a test fails if any rule lacks
  guidance.

### Changed
- Validation is about a fifth faster on documents with many Schematron hits (40,000
  boreholes: 3.4 s to 2.6 s), with the same findings byte for byte. Placing each
  Schematron failure on its element evaluated rule contexts such as
  `*[local-name()='X'][parent::*[local-name()='Y']]` as XPath over the whole document;
  lxml's tag filter now finds the same elements, in the same order.

## [1.9.0] - 2026-11-13

### Added
- **`pydiggs.ags4`: read and write AGS4 files** (the first part of AGS4 and DIGGS
  conversion). `ags4.read(path)` and `ags4.read_bytes(data)` return a `File` of `Group`
  tables (`name`, `headings`, `units`, `types`, `rows`, and for a group that was read
  the source `line` of its GROUP row and `row_lines` of its DATA rows, which take no part
  in equality), with every value kept as the text the file holds; `ags4.dumps(file)` and
  `ags4.write(file, path)` write one back the same way every time (every field quoted,
  CR LF, groups in the order given), so a file that follows the rules is written back
  byte for byte. `File` and `Group` have public constructors, and `ags4.AGS_VERSIONS`
  lists the versions read and written (4.0.3 to 4.2). The module is new and nothing at
  the top level changes:

  ```python
  from pydiggs import ags4

  f = ags4.read("site.ags")
  f.version  # '4.1.1', from TRAN_AGS
  f.groups["LOCA"].records()  # [{'LOCA_ID': 'BH01', ...}, ...]
  f.notes  # what reading had to work around, as findings
  ags4.write(f, "copy.ags")
  ```

  Reading is lenient, like `pydiggs.read`: a file that breaks the AGS4 rules still reads
  and each workaround is a note (`ags4.rule-4` for a short row, `ags4.rule-5` for an
  unquoted field, and so on; a missing UNIT or TYPE row is an empty `units` or `types`
  and an `ags4.rule-2b` note, never a row invented, and `dumps` writes no row for it);
  `DocumentError` (`kind` `not-ags4` or `unsupported-version`) is raised only when no
  table can be built. Text is read as UTF-8, and as Windows-1252 with an
  `ags4.encoding-assumed` warning when it is not.
  Every file is treated as untrusted: size, groups, headings, rows, cells, value length
  and time (`max_bytes`, `time_budget`) are limited, a limit gives a partial file with
  `complete=False`, nothing a file names is opened, and there is no network access.
  The reader is written for pyDIGGS from the published AGS4 rules, with no new
  dependency; no AGS data dictionary or AGS example file is bundled, and pyDIGGS is not
  an AGS-approved tool. Conversion follows.
- **`pydiggs.ags4.check` and `pydiggs ags4 check`: the AGS4 rules as findings.**
  `ags4.check(path_or_file, dictionary=None)` returns a `Report` (`format="ags4"`, step
  `ags4`) with one rule id per rule of section 4.1.1 of the AGS4 specification, Edition
  4.2 (`ags4.rule-1` to `ags4.rule-20`, including `2a`, `2b`, `10a` to `10c`, `11` to
  `11c`, `16a`, `18a`, `19a`, `19b`), each with its line and its place in the file
  (`Finding.path`, such as `LOCA/3/LOCA_TYPE`), plus `ags4.formula-text` (warning) for
  text a spreadsheet would read as a formula. Messages name groups and headings, never a
  value from the file. The rules that need the AGS4 data dictionary (7, 9, 10a, 10b,
  10c, 18, and parts of 11c, 13, 14 and 19b) run for what the file's own DICT group
  defines, and for standard groups only when you pass your own copy of the dictionary
  with `dictionary=` (`--dictionary`); none is bundled, and an `ags4.dictionary-not-checked`
  info finding says so. Rule 20's FILE folder is never opened. On the fixtures and the
  14 BGS files python-ags4 checks in full, the rules that need no dictionary agree with
  it, with two differences: pyDIGGS notes a UTF-8 byte order mark under Rule 1, and
  reports a missing part of a combined abbreviation as Rule 16a where python-ags4 says
  Rule 16.

  ```console
  $ pydiggs ags4 check site.ags --dictionary Standard_dictionary_v4_1_1.ags
  site.ags: not-valid: 1 error(s), 0 warning(s), 0 info
    site.ags:77: error ags4.rule-10a [XTST/2]: DATA row 2 of group XTST has the same KEY values as row 1.
  ```

  The command takes `--format text|json|sarif`, `--output`, `--fail-on`,
  `--max-findings`, `--max-bytes` and `--time-budget`, with the exit codes of
  `pydiggs validate`. In SARIF, a finding's place in the file is a logical location
  (`locations[].logicalLocations[]`, with `fullyQualifiedName` such as `XTST/2` and
  `kind` `element`); SARIF for DIGGS files is unchanged. `rule_catalogue()` and
  `explain_rule()` describe every new id.
- **diggs-findings 1.1**, which only adds to 1.0: the steps `ags4` and `convert`
  (`Step.AGS4`, `Step.CONVERT`; `validate()` never runs them), an optional
  `location.path` for a place in a source that is not XML (`Finding.path`, for AGS4
  `GROUP/row/HEADING`), and an optional `files[].format` (`Report.format`, `"ags4"` for an
  AGS4 file). The new schema is packaged and published as
  `diggs-findings-1.1.schema.json`; the 1.0 schema file is unchanged and still packaged
  and published, and every 1.0 document is a valid 1.1 document.
- **Preview: AGS4 to DIGGS conversion** (`pydiggs.preview.convert`, outside Semantic
  Versioning). `convert.ags4_to_diggs(source)` reads an AGS4 file (a path, bytes, a
  stream or an `ags4.File`), writes a DIGGS 3.0.0 document and returns a `Conversion`:
  `xml`, `model` (the document read back with `pydiggs.read_bytes`), `report` (the AGS4
  reader's notes and the conversion's findings, as a findings report with `format`
  `"ags4"`), `losses`, `lossless`, `validation` (the output validated), `trace` (the AGS4
  row each node came from, as `Origin(group, row, line)`), `mapping_version` and
  `complete`; `save(path)` writes the XML:

  ```python
  from pydiggs.preview import convert

  c = convert.ags4_to_diggs("site.ags")
  c.validation.ok  # True: DIGGS 3.0.0 with no error
  c.trace["BH01"]  # Origin(group='LOCA', row=1, line=60)
  for finding in c.losses:  # what did not reach the output, or was written by default
      print(finding.rule_id, finding.path, finding.message)
  ```

  It covers the Tier A groups (PROJ, TRAN, the ABBR/UNIT/TYPE/DICT lookups, LOCA, GEOL,
  SAMP, ISPT, LLPL, GRAG with GRAT, LNMC). Every non-null value of the source is either
  carried into DIGGS or named by a finding of the new `convert` step:
  `convert.unmapped-group`, `unmapped-heading`, `user-defined`, `unmapped-code`,
  `unmapped-unit`, `value-unparsed` and `assumed` (warnings), `orphan-row` and
  `incomplete` (errors), and `value-reformatted` (information, not a loss). One finding
  is given per group, heading or reason, with a count, and `location.path` names the
  first place as `GROUP/row/HEADING`. Values are carried as reported: units are renamed
  only through the unit table, a measure is written only with a unit DIGGS 3.0.0 allows
  for its element, grid coordinates only in the unit of their CRS's axes, and nothing is
  converted numerically; codes and grid references are mapped only where the bundled
  DIGGS code lists support them; nothing the file names is opened; and the same file
  always gives the same bytes. `max_bytes` limits the AGS4 input only: the DIGGS XML,
  often many times larger, is read back and validated whatever its size.
  `time_budget` is seconds for the whole call (reading, converting, validating and
  reading back each get what is left). When it runs out, `complete` is False and the
  output is partial (`convert.incomplete` names what was not converted, or `model`
  holds the members read back so far), except during validation, which gives
  `validation` a `structure.timeout` error instead. Writing the XML and a running
  libxml2 call are not interrupted, so the call can overrun the budget by that much.
- **The AGS4 to DIGGS mapping table** (version 0.1), data that can be reviewed and
  corrected: `src/pydiggs/convert/mappings/` (`ags4-diggs.toml`, `units.toml`,
  `codes.toml`), with its own JSON Schema (`convert-mapping-1.schema.json`) and a
  generated page, [AGS4 to DIGGS mapping](https://xinp-hub.github.io/pydiggs/convert-mapping/).
  Every group is a draft; mapping table 1.0 is signed off with pyDIGGS 1.10, when
  conversion becomes stable in `pydiggs.convert`. `convert.mapping_info()` describes
  the table. It holds only AGS4 group and heading names, keys and parents, and no text
  from the AGS4 data dictionary.

### Changed
- **Contributions are signed off under the Developer Certificate of Origin.** Every commit
  in a pull request needs a `Signed-off-by` line (`git commit -s`), checked by a new
  `DCO sign-off` job; there is still no contributor licence agreement. The terms are in
  the new `CONTRIBUTING.md`, and pull requests get a short checklist template.
- **Licensing is now documented file by file.** A top-level `NOTICE` file lists the
  attributions to keep when you redistribute pyDIGGS, including the bundled Energistics,
  OGC and EPSG material that the licence metadata did not name before. The wheel ships it
  with the third-party licence texts in `.dist-info/licenses/`. Every file in the
  repository has its copyright and licence recorded in `REUSE.toml` (REUSE 3.3), checked
  in CI. A new [Licence and use](https://xinp-hub.github.io/pydiggs/license/) page says
  what you may do, what you must do when you redistribute, and what a pyDIGGS check does
  and does not tell you about engineering data. The pyDIGGS licence is unchanged
  (Apache-2.0).
- **The package's licence metadata now names every licence it contains**:
  `Apache-2.0 AND MPL-2.0 AND MIT AND LicenseRef-Energistics-PLA AND LicenseRef-POSC-LA
  AND LicenseRef-OGC-2001 AND LicenseRef-EPSG-TOU AND LicenseRef-DIGGS-Unstated AND
  LicenseRef-OASIS` (the container image label likewise, without the test-only OASIS
  entry). If your SBOM or licence scanner allows only SPDX-listed licences, it will now
  flag pyDIGGS for review; the terms behind each `LicenseRef-` are in `LICENSES/` and on
  the [Licence and use](https://xinp-hub.github.io/pydiggs/license/) page, which now has
  a checklist for products that include pyDIGGS. The units dictionary is now recorded as
  containing Energistics definitions, as it says itself.
- The 26 unused Energistics schemas in DIGGS 2.6's `witsml/2.0/RemovedFromProfile/` are no
  longer bundled: DIGGS 2.6 does not import them, so validation is unchanged, and the
  Energistics agreement does not allow redistributing them unmodified.
- `pydiggs.preview` warns when one of its deprecated read-API names is used, rather
  than when it is imported, so that `pydiggs.preview.convert` can be imported without a
  warning. The deprecated names and their removal in 2.0 are unchanged.
- Findings documents now declare **diggs-findings 1.1**: `$schema` is the 1.1 URL and
  `schema_version` is `"1.1"` (`FINDINGS_SCHEMA_URL`, `FINDINGS_SCHEMA_VERSION`,
  `findings_schema()`, the HTTP API's OpenAPI document, the sidecar's OpenRPC document
  and the MCP `outputSchema` follow). Nothing else in a document about DIGGS files
  changes: the two new fields are written only for AGS4. If you check documents against
  the 1.0 schema, move to the 1.1 schema, because the 1.0 schema accepts only its own
  `$schema` URL:

  ```python
  jsonschema.validate(document, schema_1_0)  # 1.8.0
  jsonschema.validate(document, pydiggs.findings_schema())  # 1.9.0
  ```
- `GET /v1/rules?step=` and the rule catalogue accept and list the `ags4` step.
- Reading into JSON is about a fifth faster (`/v1/read`, `@pydiggs/client`): writing a
  model out as JSON (`Model.to_json`) takes half the time, and reading a tenth less,
  with the same bytes as before. The JSON encoder looked up each class's fields for
  every value it wrote, the decoder spelled out a location string for every field in
  case of an error, and the reader made a counter for every element; each now does that
  work once.

### Fixed
- The service layer behind `pydiggs serve --http`, the sidecar and `@pydiggs/client` no
  longer imports networking modules on Python 3.14. Hiding install folders in finding
  texts called `Path.as_uri()`, which from 3.14 imports `urllib.request` and with it
  `http.client`, `socket` and `ssl`; nothing was sent, but the core path promises never to
  import them, and in a browser they were dead weight. The `file:` spelling is now built
  directly.
- The source distribution holds only the Python package again. The 1.8.0 sdist also
  carried the npm package's sources (`js/`), `.github/scripts` and an example script,
  because its include patterns matched at any depth. The wheel was not affected.

## [1.8.0] - 2026-11-11

### Added
- **MCP read tools** (the `mcp` extra): `list_features` (a page of the document's
  features, with how many samples and measurements refer to each), `get_feature` (one
  feature as diggs-model 1.0, with its samples, measurements and lithology layers),
  `list_measurements` (a page of the measurements and other holders of result tables,
  including those that name no feature; filter by feature or procedure) and
  `get_results` (a page of rows of one result table, with its columns described). Each
  answers typed `structuredContent` whose `outputSchema` is built from the frozen
  diggs-model 1.0 definitions, and a text summary of counts only. Pages continue with a
  short opaque `next_cursor` tied to the document and the request. Every string from
  the file is cut to 512 characters (node keys at 4,096, so they can be sent back), a
  result is at most 1 MiB, and the tool descriptions say file text is untrusted. They
  share `validate_diggs`'s limits (10 MiB, one worker, 60 s); the worker keeps the
  models it read lately, so the calls after the first do not parse the file again.

### Changed
- `pydiggs.frames` `results` view: a result column named `position`, `top`, `base`,
  `time` or `elapsed_time` is now always `<name>_value` (those names say where a row
  is). In 1.7.0 a measured `elapsed_time`, as in a Lugeon or dissipation test, could be
  the frame column `elapsed_time`; it is now `elapsed_time_value`, whatever filters are
  used:

  ```python
  frame = frames.to_pandas(model, "results", procedure="LugeonTest")
  frame["elapsed_time"]  # 1.7.0
  frame["elapsed_time_value"]  # 1.8.0
  ```
- The read service keeps a model that was cut short by a work limit in its cache (the
  same bytes stop at the same place), so paging such a file parses it once. A model cut
  short by the time budget is still read again.
- The MCP `timeout` error text says the file was not checked or read.

### Fixed
- Stdio sidecar: a session that negotiated protocol 1.0 gets `method-not-found` for
  `read`, as documented, instead of an answer.
- `pydiggs.frames` `results` view: a result column named like an index column of
  another table in the same frame (`top`, `position` …) no longer shares that column
  with the places of the other table's rows (see the naming rule under Changed).

## [1.7.0] - 2026-11-11

### Added
- **The read API is stable.** `pydiggs.read()`, `pydiggs.read_bytes()`, `pydiggs.Model`,
  `pydiggs.model_schema()`, `pydiggs.MODEL_SCHEMA_VERSION`, `pydiggs.MODEL_SCHEMA_URL`
  and `pydiggs.DocumentError` are top-level, and `pydiggs.model` and `pydiggs.crs` are
  public modules covered by Semantic Versioning. The model is **diggs-model 1.0**
  (`schemas/diggs-model-1.0.schema.json` on the docs site), frozen: from here it changes
  only by minor versions that add, as the docs' Versioning section describes.
- `read(..., provenance="field")`: every node's `provenance.fields` says where each of
  its values was read (the element's xpath relative to the node, and its line).
- Depths along a linear referencing system that names a method in the DIGGS registry
  (`lrm.xml#md_ft`, `#md_m` …) get that method's unit. The registry's methods are
  packaged with pyDIGGS (from DIGGSml/def), so nothing is fetched.
- Read note `structure.duplicate-gml-id` (warning): two or more elements share one
  `gml:id`. Each copy is keyed by its xpath, and a link to the id resolves to the first.
- `pydiggs.crs.SUPPORTED_SRS_FORMS`.
- Reading has work limits, so a small hostile file cannot produce a huge model: at most
  100,000 objects, and as many elements with no typed field, plus one of each per 200
  bytes of the file, nested at most 64 deep (real files have a few thousand, over 1,000
  bytes apiece and about 10 deep). Reaching one returns what was read with `complete=False`,
  the limit in `limits_hit` and a `structure.read-limit` note. A namespace URI longer
  than 1,024 characters is refused (`DocumentError`, kind `namespace-too-long`), as
  `validate` already does.
- **HTTP API:** `POST /v1/read` returns the diggs-model document for an uploaded file,
  with the same limits, isolation and problem responses as `/v1/validate`. Bytes that
  are not a DIGGS document are 422 `invalid-document` (with `kind`). The OpenAPI
  document carries the model schema as `DiggsModel*` components.
- **Stdio sidecar:** a `read` method, in protocol **1.1** (1.0 hosts are answered as
  before). New errors `invalid-document` (-32010) and `result-too-large` (-32011);
  `initialize` reports `model_schema_version`, `model_schema_url`,
  `capabilities.read` and `limits.max_result_bytes`.
- On both, `select` (`{"results": "all" | "none" | [node keys]}`) chooses which result
  tables keep their values, so a host can list a large file first and fetch tables
  after. Every surface returns the bytes `pydiggs.read_bytes(...).to_json()` gives.
- **Data frames:** `pydiggs.frames.to_pandas()` (the new `frames` extra) and
  `to_polars()` (the `polars` extra) give a model's features, samples, lithology
  layers, measurements or result tables as a data frame, with filters by feature, kind,
  procedure and measurement, and result values in SI units on request.
- **GIS:** `pydiggs.gis` (the new `gis` extra: geopandas, pyproj, shapely, pyogrio,
  pyarrow). `transform()` gives every position in an EPSG or OGC CRS a longitude and
  latitude in the CRS you choose and records that it did; `to_geodataframe()` gives the
  features as points; `write()` saves them as GeoPackage, GeoParquet or GeoJSON.
  `import pydiggs` imports none of these libraries.
- **Browser (Pyodide):** `read_bytes` is tested in Pyodide, byte for byte against
  CPython, and the Web Worker example shows what a file contains after validating it.

### Changed
- diggs-model documents say `"schema_version": "1.0"` (1.6.0 wrote `1.0-beta.2`; the
  betas stay published under `schemas/preview/`). Every node's `provenance` has a
  `fields` member, `null` unless `provenance="field"` was asked for.

### Deprecated
- `pydiggs.preview`: importing it warns. Use the same names from `pydiggs`. It will be
  removed in 2.0.

### Fixed
- Reading from several threads at once could, on the first reads in a process, build a
  model with fields missing; the decoder's setup is now safe to share.
- `read()` rejects a `time_budget` that is not a finite number (NaN or infinity) with
  `ValueError`, instead of reading with no limit.

## [1.6.0] - 2026-11-10

### Added
- **Read API (preview):** `pydiggs.preview.read(path)` and `read_bytes(data)` read a
  DIGGS 2.5.a, 2.6 or 3.x file into a `Model` (diggs-model 1.0-beta.2): features,
  sampling activities, samples, lithology layers, measurements with every result table
  as typed columns, links between them, a CRS table, and notes on what reading had to
  work around. Files are parsed one top-level member at a time, nothing is fetched, and
  `Model.to_json()` is deterministic and round-trips through `Model.from_json()`. The
  node and value classes are in `pydiggs.model`, the schema in
  `pydiggs.model.model_schema()` and on the docs site. Preview: names may change before
  1.7.0 freezes diggs-model 1.0.
- Read API coverage (diggs-model **1.0-beta.2**):
  - Every node and procedure keeps its simple values in `params` (a `Quantity`, a `Code`
    or text), so every test has typed parameters: SPT hammer efficiency and penetration,
    CPT tip area and area ratio, and the rest.
  - Nested records are `Component` nodes with their own keys: borehole and well
    construction (method, casing, backfill, hole diameter, openings, events), water
    strikes and their readings, SPT drive sets, CPT cones, sieve analyses, lithologies.
  - A well's `parent` hole; a sample's depth, method and recovery from its sampling
    activity; a lithology layer's classification, colour and geologic unit; a test's
    `result_time`.
  - Result tables say where each row is: `domain.index` columns for CPT depths, stepped
    intervals, water-level times and elapsed times, and an `extent` for tests measured
    over one interval. Depths take the unit of the borehole's linear referencing
    system when the file states it.
- `pydiggs.crs` (preview): `parse()` an `srsName`, and `epsg()` facts (name, type, axis
  order, unit, datum) from a packaged subset of the EPSG Dataset (owned by IOGP, used
  under its terms of use). The model's `crs` table and `geographic` positions use them.
- `pydiggs.errors.DocumentError` (a `ValueError`): raised by the read API when the bytes
  are not a DIGGS document at all.
- Rule `structure.value-unparsed` (info), used by read notes.
- Documentation: a "Stay updated" page on how to follow new releases (GitHub release
  notifications, the releases feed, PyPI's feed, and an email list for feature releases).

### Security
- GitHub releases are immutable from 1.6.0: a published release's files cannot be
  changed, and its `v*` tag cannot be moved or reused. A release that needs a fix gets
  a new patch version.
- Release tags are signed by a maintainer's key, and the release workflow refuses to
  publish from a tag that is unsigned or signed by a key not listed in
  `.github/release-signers` on the default branch (never the copy in the tagged
  commit). Only a pushed tag publishes to PyPI; a manual run of the workflow stops at
  TestPyPI. Check a release with
  `git -c gpg.ssh.allowedSignersFile=.github/release-signers verify-tag vX.Y.Z`.

## [1.5.0] - 2026-11-09

### Added
- Experimental `3.1-dev` profile: the unreleased DIGGS 3.1 draft schema from
  DIGGSml/schema-dev (branch `3.1-dev`, pinned by commit). Use it with
  `diggs_version="3.1-dev"` or `--diggs-version 3.1-dev`; it is never auto-detected and
  never the default, so results for existing files do not change. When the schema
  step checks a file against it, the report carries a new `schema.unreleased-profile`
  warning.
- Four checks adapted from the DIGGS File Inspector, in `validate()` findings:
  `geometry.reported-depth-mismatch` (a `totalMeasuredDepth` more than 2 % off its
  `centerLine` length, when the file states its axis units),
  `geometry.crs-axis-order-suspect` (geographic coordinates that look like longitude,
  latitude), `geometry.crs-unresolvable` (an `srsName` that is not a recognised EPSG or
  DIGGS reference nor an element in the file) and `context.xlink-remote-unresolved`
  (info: a link into another document, which pyDIGGS does not fetch). The legacy
  `context_check` output does not change.
- Documentation: a draft of **diggs-model**, the data model the read API will return from
  1.6.0, with its JSON Schema and an example under `schemas/preview/` on the docs site
  ("Reading files (draft)"). Nothing in this release reads files into it yet; comments
  are welcome before it is frozen in 1.7.0.
- A conformance corpus in the repository (`conformance/`): the DIGGSml examples plus
  real-world files from the DIGGS File Inspector, pinned by commit and sha256, each with
  the outcome pyDIGGS gives (`expected.toml`) and what a reader must produce from it
  (`model.toml`). Other DIGGS tools can be checked against the same files with
  `scripts/conformance.py`.

### Changed
- `version_info().diggs_versions` and the `diggs_version` choices on every surface
  (Python, command line, HTTP, sidecar, MCP) include `3.1-dev`. `warm_up()` with no
  argument still warms only the released versions.
- Each report's `reference_data` also names the pinned DIGGSml/schema-dev commit.

### Fixed
- Deprecations introduced in DIGGS 3.1 now apply to the 3.1 profile; a version with a
  pre-release suffix such as `3.1-dev` was ordered as 3.0.

### Security
- The container image upgrades zlib1g to the Debian fix for CVE-2026-76844 and
  CVE-2026-85091; the image build fails if the fixed version is not installed.

## [1.4.1] - 2026-11-05

### Added
- Documentation site: a Quickstart (install, validate a file, read the findings, use
  them from Python and CI), an integration chooser that compares the Python API,
  command line, HTTP API, container, stdio sidecar, MCP server and browser build, and a
  Python API reference generated from the docstrings.

### Changed
- Documentation site navigation follows the reader's journey: Get started, Guides,
  Integrate, Reference and Project tabs, with breadcrumbs, a home page that routes each
  kind of user to their path, linked Python and command-line tabs, an automatic light
  or dark theme, and "Edit this page" links.
- Package metadata names the author and maintainer as "Xin Peng" (was "xin.peng").
  The README and the Authors page list Xin Peng as project lead and Asitha Senanayake
  as contributor.
- Container image: built on Python 3.14.10 (was 3.14.8), with uv 0.13.7 in the build
  stage; the build and runtime stages carry the same Python patch release. The image's
  locked FastAPI is 0.159.1 and Pydantic 2.14.1.

### Fixed
- Code blocks on the documentation site no longer show a second, unstyled "Copy"
  label under the theme's own copy button, and links inside a page work normally again.
- The documentation site no longer shows a cookie consent banner: it sets no cookies
  that need consent.
- Docstring examples render as highlighted code in the API reference (their headings
  are now `Examples:`).

## [1.4.0] - 2026-11-04

### Added
- `pydiggs --version` (or `-V`) prints the installed version.
- **MCP server** (`pip install 'pydiggs[mcp]'`, then `pydiggs serve --mcp`): AI
  assistants and other MCP clients can validate a DIGGS document and look up rules
  over stdio. Four read-only tools: `validate_diggs` (a base64 document in, a whole
  diggs-findings document out, equal to `pydiggs validate --format json` apart from
  the path and the limits below), `explain_rule`, `list_rules` and
  `supported_versions`. The text the model
  reads is a fixed-template summary that never contains text from the file. Limits:
  10 MiB, 200 findings, 512-character messages, one validation at a time with a hard
  60-second timeout. No path, network access or local dictionary is ever used. See
  the new "MCP server" page.
- **`pydiggs.mcp_server.create_server()`** returns that server (the official MCP
  Python SDK's `MCPServer`) for embedding and tests.
- **The `mcp` extra**: `mcp>=2.3.0` and `python-multipart>=0.0.31`.
- **Browser support, tested**: pydiggs runs in a web page with Pyodide 314.0.7, so a
  DIGGS file can be checked without leaving the user's machine. A new example,
  `examples/browser-worker`, validates in a module Web Worker (Pyodide loaded once
  with an integrity hash or self-hosted, pydiggs installed with `deps=False`, 25 MiB
  limit, findings rendered as text only, a strict Content-Security-Policy). CI now
  installs every build into that Pyodide and checks its reports are byte-identical to
  CPython's. See the new "Browser (Pyodide)" page.
- **Container image** `ghcr.io/xinp-hub/pydiggs:X.Y.Z` (also `:X.Y`; there is no
  `latest`) for `linux/amd64` and `linux/arm64`: the HTTP API on port 8000, built from
  the exact wheel published to PyPI and the locked `[server]` dependencies, on
  `python:3.14-slim-trixie`, running as uid 10001 with no package installer inside and
  a `/livez` health check. Set `PYDIGGS_ALLOWED_HOSTS` to the names clients use. Each
  image has a GitHub build provenance attestation (`gh attestation verify`), BuildKit
  provenance and an SBOM. A release image is pushed by digest and scanned for
  vulnerabilities before its tags point at it; the image built from every pull request
  is scanned too. See the new "Container image" page for the hardened run line, a compose
  example and memory sizing.
- **`constraints-X.Y.Z.txt`** on every GitHub release: the dependency versions the
  release was tested with, to pass to pip with `-c`.
- The OpenAPI document (`openapi.json`) and the sidecar's OpenRPC document
  (`openrpc.json`) are published on the documentation site under `schemas/`, and CI
  checks both against their official meta-schemas.

### Changed
- `pydiggs serve --mcp` is listed in `--help`. Without the `mcp` extra it exits 2 and
  names `pydiggs[mcp]`, as `--http` does for `pydiggs[server]`.
- `import pydiggs` and validation no longer load `urllib.request`, `http.client`,
  `socket` or `ssl`; they are imported only for an `allow_network=True` download.

### Fixed
- The documentation's release history now has its 1.2.0 entry.
- The documentation site's "Edit this page" links point at the `master` branch.

## [1.3.0] - 2026-11-03

Everything here is additive: 1.2.0 code, CLI output and exit codes are unchanged.

### Added
- **`validate_bytes()`** checks a DIGGS document held in memory: `bytes`,
  `bytearray`, `memoryview` or a binary stream. It applies the same checks and safety
  limits as `validate()`, never reads local dictionaries, and takes a display `name`.
- **`time_budget=`** on `validate()` and `validate_bytes()`: a soft time limit checked
  between steps. A report that runs out of time has one `structure.timeout` error and
  its unfinished steps are `incomplete`, the same shape as `pydiggs validate --timeout`.
- **`warm_up()`** compiles the bundled validators ahead of the first call.
- **Findings documents and schema:** `Report.to_document()`, `findings_document()`,
  `findings_json()`, `findings_schema()`, `FINDINGS_SCHEMA_VERSION` and
  `FINDINGS_SCHEMA_URL`.
- **`version_info()`** (`VersionInfo`): package version, findings schema, DIGGS
  versions and reference-data commits.
- **`rule_catalogue()`** and **`explain_rule()`** (`RuleInfo`): every rule id pyDIGGS
  can emit, with its step, severities and description.
- **HTTP API, `pydiggs[server]`.** `create_app()` / `create_router()` for FastAPI and
  `pydiggs serve --http`: `POST /v1/validate` (raw XML or multipart), `GET /v1/versions`,
  `GET /v1/rules[/{id}]`, health probes, RFC 9457 problem details and a committed
  OpenAPI 3.1 document. By default, validation runs in isolated worker processes with
  hard timeouts. Request size, admission, Host and cross-site checks are enforced
  inside the route. See [HTTP API](docs/http-api.md).
  - A request body must arrive within `body_timeout_s` (`PYDIGGS_BODY_TIMEOUT_S`,
    default 30 s) of admission; otherwise the answer is 408 `request-timeout` and the
    admission slot is freed.
  - `checks` must match the documented `step(,step)*` pattern of at most 512
    characters; blanks, empty items and longer values are 422 `invalid-parameter`.
  - On SIGTERM or SIGINT, `serve --http` waits at most `timeout_s` plus 5 s for
    requests in flight, then cancels them and exits.
  - Worker processes write no core file (POSIX), and `create_router()` logs the same
    thread-isolation warning as `create_app()`.
  - uvicorn does not time out slow request headers: put any non-loopback deployment
    behind a reverse proxy with client header and body timeouts.
- **Stdio sidecar for native apps, `pydiggs serve --stdio`.** A JSON-RPC 2.0 protocol
  over newline-delimited JSON on stdin and stdout, with an OpenRPC description. Any
  desktop or native host (Tauri, Electron, Qt, .NET, Java, Swift…) can drive pyDIGGS as
  a child process. Files are sent as content by default; reading by path works only
  inside folders the host grants with `--allow-path`. Uses the standard library only.
  See [Stdio sidecar](docs/sidecar.md) and [Host integration](docs/sidecar-hosts.md).
  - A path that, as spelled, is not under a root (given or canonical spelling) is
    refused before it is opened. Hard links into a root are not contained.
  - `--max-bytes`, when given, is also the by-path limit (100 MiB otherwise).
  - JSON nested deeper than 64 levels is a parse error on every Python version.
- `pydiggs serve --mcp` is reserved for the MCP server planned for 1.4.0: it is hidden
  from `--help` and exits 2. There is no `[mcp]` extra in this release.
- `python -m pydiggs` runs the CLI.

### Changed
- `import pydiggs` no longer imports rich; the CLI loads it when it prints. rich is
  still a dependency.
- `pydiggs validate --timeout` runs files on a supervised worker process, which is
  replaced after a timeout or crash. Output and exit codes are unchanged.
- The SARIF short description of `structure.timeout` is now "Validation did not
  finish within its time limit".

### Fixed
- SARIF `file:` URIs (`--absolute-paths`) no longer use `PurePath.as_uri()`, which is
  deprecated in Python 3.14; the URIs are unchanged.

## [1.2.0] - 2026-10-23

**Upgrade note:** some files that passed under 1.1.1 now fail, and the reverse.

- **Dictionary check 5.** The bundled DIGGS dictionaries are now exact copies of
  diggsml.org/def. pyDIGGS 1.1.1 and earlier had added eight codes to its copies of
  `properties.xml` and `roles.xml` (`areaRatio`, `cloudCover`, `coneDiameter`,
  `depthToWater`, `geotechnicalEngineer`, `obstructionType`, `pushRate`, `siteName`) that
  the official dictionaries do not define. Files using them now fail
  `dictionary_check()`, as they do in the official DIGGS validator.
- **DIGGS 2.6 schema.** Upstream corrected the required element `samplingFeatiureRef` to
  `samplingFeatureRef` in `Geophysics.xsd`. 2.6 files that spell it the old way now fail
  `schema_check()`, and files with the corrected spelling now pass.

### Changed
- Dictionary checks 8 to 12 name a property with several names by its first name in
  the dictionary; they used Python set order, so the text could change between runs.
  No example in the test corpus is affected.
- `pydiggs --help` now points to `pydiggs validate --help`. The legacy subcommands, their
  console output, exit codes and log files are unchanged (checked byte for byte against
  1.1.1 by a golden test suite).
- Re-synced the bundled reference data to DIGGSml/schemas `15ce278` (9 October 2026) and
  DIGGSml/def `2d76ba6` (8 October 2026):
  - 2.6 `Geophysics.xsd`: upstream fixed the element name `samplingFeatiureRef` to
    `samplingFeatureRef`.
  - Dictionaries: 57 more code lists (83 in all) are available offline, including the
    pile and rigid-inclusion lists; the existing lists now declare the DIGGS 3
    namespace, and `properties.xml` and `DiggsUomDictionary.xml` follow upstream.
  - `mwd_properties.xml` and `pil_properties.xml` are now the official copies rather
    than local supplements.
  - Upstream directory-listing pages (`index.html`) and `.gitignore` files no longer ship
    in the wheel.

### Added
- **Machine-readable findings.** `pydiggs.validate(path)` runs every native check on one
  file and returns a `Report` of `Finding`s, each with a stable rule id
  (`schema.cvc-complex-type.2.4`, `dictionary.5`, `schematron.Borehole.totalMeasuredDepth`,
  ...), a `Severity`, a `Step`, the line, the official DIGGS XPath and the nearest
  `gml:id`. It prints nothing and writes no files, and raises only `InstanceReadError`,
  `ResourceError` (both `PydiggsError`s) and `ValueError`; XML that is not well-formed is a
  finding. New public names: `Finding`, `Report`, `Severity`, `Step`, `validate`,
  `PydiggsError`, `InstanceReadError`, `ResourceError`.
- `pydiggs validate PATH...` with `--format text|json|sarif`, `--output`, `--checks`,
  `--fail-on error|warning`, `--snippets`, `--absolute-paths` and `--allow-network`.
  Directories are searched for `*.xml`; exit codes 0 (clean), 1 (findings), 2 (usage
  error or unreadable input).
- The diggs-findings 1.0 JSON format, with its JSON Schema shipped in the package and
  published at https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-1.0.schema.json,
  and SARIF 2.1.0 output for code-scanning tools. Output is deterministic, has no
  timestamps, and by default carries no source snippets and no absolute paths.
- Limits for `validate()`: at most 10,000 findings per file by default (`max_findings`,
  `--max-findings`; the overflow is counted in one `structure.findings-truncated`
  finding and the affected steps are `incomplete`), and instances up to 100 MiB by
  default (`max_bytes`; the legacy `validator` keeps 1 GiB).
  `--max-bytes` sets the size limit from the command line. Each finding's message and
  xpath are capped at 4,096 characters, and a report's findings at about 16 MiB.
- `validate(local_dictionaries="instance-dir" | "off")` and `--local-dictionaries`: by
  default a dictionary named by a relative or `file:` codeSpace is read only from
  inside the instance's folder (no working-directory fallback, no escape through `..`
  or symlinks). `dictionary_check()` is unchanged.
- In `validate()`, local dictionaries are read once per file however their path is
  spelled, under `max_bytes`, only if they are regular files, and at most 16 per
  instance, within one byte budget shared with the instance.
- Fail closed: a step that cannot finish (a libxml2/libxslt limit, a deprecation XPath
  that cannot be evaluated, or a file with more than 9,900,000 elements) is
  `incomplete` with a `<step>.incomplete` error, never a pass.
- `pydiggs validate --timeout SECONDS` (at most one day) validates each file in its own
  process and reports a file that does not finish as a `structure.timeout` error. With
  or without it, an unexpected error for one file becomes a `structure.internal-error`
  for that file (the exception type and a short text, with the file's path scrubbed)
  and the batch goes on.
- In `validate()`, the Schematron step (bundled or user rules) is skipped with a
  `schematron.incomplete` error when an element has more than 50,000 child elements,
  where libxslt can take minutes; the casing diameter rule still runs.
  `schematron_check()` is unchanged.
- In `validate()`, the Schematron step (bundled or user rules) is also skipped with a
  `schematron.incomplete` error when the rules would fire on more than 100,000
  elements, counted in linear time before they run: libxslt's time grows with the
  square of the number of elements the rules fire on, whatever their parents.
- In `validate()`, dictionary Checks 6 to 8 try each element only against the
  definition paths that can name it, a Definition may list at most 1,000 paths or
  names, and path matching may take at most 5,000,000 steps per instance; past either
  limit the step is `incomplete` with a `dictionary.incomplete` error. Check 6 and 8
  messages list at most 20 paths. Each Definition is read once per instance (it may
  have at most 10,000 nodes, and a path at most 64 steps), a property's `typeData`,
  `uom` and `nullValue` siblings are found once per parent, and sibling reads count
  towards the same budget. Check 13 has one budget of its own (250 million units):
  every character it reads and every element, `index`, group, reference, token, field
  and merged value it examines or keeps is charged, so no Check 13 loop is unbounded
  and what it keeps stays under 1 GB. The weights are calibrated so that a unit costs
  about the same time in every loop (`scripts/calibrate_check13.py`, which exits 1 if
  any loop costs more than 30 ns a unit on the machine it runs on; on an Apple M3 Max
  the worst loop is about 25 ns, the median of three runs, so the whole budget is about
  6 s there and 13 to 26 s on hardware two to four times slower; data-shaped 10 MB
  files use 21 to 23 % of it, the densest shape tried 59 %). A property `index` is parsed once, in the layout
  pass, and an index longer than 64 characters is not a column. A column's values over a group's
  ResultSets are merged once for all the group's properties, and each `index` belongs
  to its nearest PropertyParameters only, so nested PropertyParameters are not
  re-read.
- In `validate()`, a file whose internal-DTD entities make its parsed text more than
  twice its raw size gets one `structure.entity-expansion` error and no other check
  runs (human decision). Small text entities such as `&deg;` still work.
- The diggs-findings 1.0 format (not yet released, so changed in place): each file has
  a `verdict` (`valid`, `valid-with-warnings`, `not-valid`, `partly-checked`,
  `not-diggs`, `unreadable`); a file whose root is not `Diggs` has status `not-diggs`,
  `diggs_version` null and every step but `structure` skipped; and a step can be
  `partial` (the dictionary step when a code list could not be fetched offline), so an
  offline-unchecked step is never `passed`. Text output starts each file's line with
  its verdict, and a partly checked file gets a line naming its unfinished steps plus
  its `dictionary.offline` findings (shown although info findings are not); SARIF
  carries status and verdict in each artifact's properties. The JSON Schema ties
  status to verdict (a `not-diggs` file has verdict `not-diggs` and no
  `diggs_version`), and `Report` refuses an unknown status.
- In `validate()`, `geometry.srs-attributes` follows the official geometry module
  (human decision): it checks the module's nine objects (`LinearExtent`,
  `MultiPointLocation`, `MultiCurve`, `Solid`, `PointLocation`,
  `PlanarObservationRepresentation`, `PlanarSurface`, `MultiPlanarSurface`,
  `RectifiedGrid`) with or without coordinates, and no longer `gml:Point`,
  `LineString`, `Polygon`, `Curve`, `Surface`, `MultiPoint` or `MultiSurface`. No
  warning when the object or an ancestor declares both attributes, one best-practice
  warning when only the child `gml:pos`/`gml:posList` does, and one warning per object
  otherwise (two before; the official module makes that an error). The rule is one
  pass over the tree. `context_check()` is unchanged.
- In `validate()`, a namespace URI longer than 1,024 characters gives one
  `structure.namespace-too-long` error and no other check runs (human decision). The
  2x expansion check now runs whenever there is an internal DTD, counts UTF-8 bytes,
  and includes ATTLIST defaults and namespace URIs.
- In `validate()`, Check 13 keeps no copy of each ResultSet's rows, only the distinct
  values of the columns its properties ask for. Each ResultSet is read in one streaming
  pass for all the PropertyParameters it holds (those whose nearest ResultSet it is,
  at any depth, the same rule that decides where a property's values come from) or
  cites by `xlink:href`; the pass collects every column any of them asks for and splits
  each row no further than the last of those. Column sets are built once per
  PropertyParameters and shared between ResultSets;
  dataBlock arity is checked in constant time per (dataBlock, measurement); and a file
  whose path is not valid UTF-8 is passed to libxml2 as a `file:` URI.
- In `validate()`, any unexpected error inside a step is that step's `incomplete`
  error instead of an exception, and the remaining per-element work in the dictionary
  checks (candidate paths, Check 13 values and terms, ancestor names, memos) is cached
  or counted towards the work budget. In the context step, Properties for `dataBlock`
  arity are counted in one pass (same messages as `context_check()`).
- In `validate()`, a `/` inside a predicate of a definition path (for example
  `[text()="electrical resistivity (ERI/ERT)"]`) no longer splits the path.
- In `validate()`, Check 8 reads `//` in a definition's conditional paths as "any
  descendant". `dictionary_check()` keeps reading it as a direct child, which skips
  Check 8 under a measurement; the two AtterbergExample files in the test corpus
  therefore get two `dictionary.8` errors from `validate()` only.
- In `validate()`, a local dictionary with a markup entity in its internal DTD, or one
  libxml2 refuses for a resource limit, is a `dictionary.incomplete` error instead of a
  `dictionary.3` warning.
- In `validate()`, local dictionaries are limited to 20 MiB in all per instance (a
  parsed dictionary takes about 35 times its size in memory); a codeSpace not read
  because of that limit or the 16-dictionary cap makes the dictionary step
  `incomplete` with a `dictionary.incomplete` error.
- Schematron runs without lxml's location strings (`generate-paths=false`); pydiggs
  locates each failed assert from the rule contexts, which bounds memory per failure.
- `validate(dictionary_path=...)` uses that dictionary for every properties URL; in
  `dictionary_check()` the bundled `codes/properties.xml` still replaces it (1.1
  behaviour, unchanged).
- `validate(base_dir=...)` and `--base-dir` report paths relative to a chosen folder.
  Without it, paths are bare file names when the working directory is `/` or an
  ancestor of the home directory.
- In `validate()`, a whitelisted remote dictionary that is not bundled is reported as
  `dictionary.offline` (info) when network access is off, instead of a Check 3
  warning, so `--fail-on warning` does not fail a file just for being checked offline.
- Two checks in `validate()` only (not in `dictionary_check()`): dictionary **Check 13**,
  which requires result values of a property whose definition names a `valueCodeSpace`
  to be terms of that code list, and a **deprecation** check against the bundled DIGGS
  deprecation registry (deprecated items warn, removed items fail). Registry entries
  apply only from the release they name, so its 3.1.0 entries never fire on 3.0.0, 2.6
  or 2.5.a files.
- `pydiggs/PROVENANCE.toml` records the upstream commit and a sha256 for every vendored
  file. `scripts/sync_reference.py` re-syncs from local clones of the DIGGSml
  repositories, reading files from the pinned commit's git objects rather than the
  working tree, refuses unsafe or non-portable file names, and verifies the digests
  (`check`, run by the test suite).
- `validate()` escapes control, bidi-override and line-separator characters from the
  instance in every finding's message and in the reported path (as `\u202e`, `\x85`),
  so no output format can show reordered or hidden text, and replaces the home folder
  with `~` in messages. SARIF messages escape `[`, `]` and `\`, so text from a file can
  never become a link in a code-scanning viewer.
- `pydiggs` calls `multiprocessing.freeze_support()`, so `--timeout` works when pyDIGGS
  is frozen into an executable (PyInstaller), as in a desktop app.
- The DIGGS deprecation registry (`deprecation/DeprecationRegistry.xml`) is bundled for
  the deprecation check above. It lists 94 deprecated items (25 from DIGGS 3.0.0, 69
  from 3.1.0) and 56 items removed in 3.1.0.
- The DIGGS schemas' MPL-2.0 license ships at `schemas/LICENSE`, and
  `dictionaries/NOTICE` records the licence terms for the DIGGSml/def dictionaries. The
  package licence expression is now `Apache-2.0 AND MPL-2.0 AND MIT`, matching what the
  wheel contains.

### Performance
- The bundled Schematron rules match `*[local-name()='C'][parent::*[local-name()='P']]`
  instead of the equivalent `//*[local-name()='P']/*[local-name()='C']`, which libxslt
  evaluated in time growing with the square of the number of siblings (a 1 MB file
  with 18,000 sibling features took 31 s of CPU; now 0.1 s). Verdicts, failures and
  the `schematron_check()` output are unchanged, and the rule ids are the same.
  libxslt is still quadratic in the number of elements the rules fire on;
  `validate()` refuses files where that is over 100,000, or where one element has
  more than 50,000 children.
- Creating a `DictionarySemanticValidator` parses each bundled dictionary once instead
  of once per URL form, which more than offsets the larger dictionary bundle.

### Security
- `validate()` and `pydiggs validate` treat everything in an instance as data: the
  element paths in libxml2's schema messages (which can carry the instance's namespace
  URIs) are checked against a strict grammar and resolved by walking the tree, never
  evaluated as XPath, and Schematron failures are located from the trusted rule
  contexts, not from the report's location strings. Only regular files are read (a
  FIFO cannot block); messages about unreadable files carry no path; `--output` creates
  new files with mode 0600, keeps an existing file's mode, and never writes through a
  symlink; text output escapes control and bidi characters. A broken user Schematron
  file is a `ResourceError` (exit 2).
- In `validate()`, an instance whose internal DTD declares an entity with markup (`<`)
  in its value gets one `structure.markup-entity` error and no other check runs, so an
  entity cannot multiply the tree past the limits; this holds in every encoding the
  parser reads, including UTF-32 with a BOM. Entities with plain text still work.
  The element limit is counted on the parsed tree (an entity or a UTF-7 file cannot
  hide elements from it).
- `dictionaries/whiteList.xml`, which decides where `allow_network=True` may fetch
  dictionaries from, is maintained in pyDIGGS and digest-checked rather than synced
  from upstream, so a data update cannot widen it.

### Tests
- The official example corpus is synced to DIGGSml/diggs-examples `79da841` (4 October
  2026). Four examples that upstream fixed now pass every check and moved into the
  valid corpus (2.6 `FieldSurveyExample`, 2.6 `ODOT_Example_2_20180702-from25`,
  3.0 `Aeromag`, 3.0 `GeophysicalFieldSurveyExamples`). `PileDrivingExample` moved to
  `known_invalid`: upstream rewrote it with DIGGSml/schema-dev types while it still
  declares 3.0.0.

## [1.1.1] - 2026-10-14

First published release of the 1.1 line. 1.1.0 was tagged but never reached PyPI,
because the release workflow failed before upload. **1.1.1 contains everything listed
under [1.1.0 in the changelog](https://github.com/xinp-hub/pydiggs/blob/master/CHANGELOG.md#110---2026-10-14)**,
including the security hardening and the upgrade note: dictionary Check 12 (invalid unit
of measure) is an error again, so files that only warned under 1.0.0 now fail
`dictionary_check()`. There are no other changes to the package.

### Fixed
- Release workflow: the post-TestPyPI smoke install used a system Python that does not
  exist on the runner, so it failed before the PyPI upload.

## [1.1.0] - 2026-10-14

*Tagged but not published to PyPI; use 1.1.1.*

**Upgrade note:** dictionary Check 12 is an error again (see Changed). Files that use a
unit of measure outside its quantity class, and only warned under 1.0.0, now fail
`dictionary_check()`.

### Security
- Every XML input (instances, schemas, Schematron, dictionaries) is parsed with an
  explicit hardened parser. External entities, external DTDs and network access are
  refused, and entities declared inside a file still expand, so valid files validate
  exactly as before. pyDIGGS no longer relies on lxml's defaults. libxml2, the C library
  under lxml, was declared unmaintained in December 2025.
- Require `lxml>=6.1.3`, which stops loading external parameter entities by default
  (LP#2165901).
- Compressed (gzip) XML is refused. libxml2 silently decompressed files it opened by
  path, so an 11 kB file could expand to hundreds of megabytes. Files over 1 GB are
  rejected.
- A `codeSpace` that points at a UNC path (`file:////host/share/...`) is never opened.
  On Windows, even checking one sends your NTLM credentials to that host.
- The dictionary check runs in linear time. A crafted file with 20,000 sibling
  `codeSpace` elements took 73 s and now takes under 1 s.
- Log files are created readable by their owner only and are never written through a
  symlink in the working directory.
- Added `SECURITY.md`: report vulnerabilities privately through GitHub. It also states
  what pyDIGGS trusts: DIGGS files are untrusted, while custom schema and Schematron
  files are treated as code.

### Changed
- Dictionary check 12 (UOM not in its quantity class) is an **ERROR** again, now that
  the official example files no longer need the warning-only workaround.
- `DictionarySemanticValidator(allow_network=True)` now really downloads remote
  dictionaries; with libxml2 2.14 it silently fetched nothing. Downloads are limited to
  whitelisted http/https URLs (scheme, host, port and path must match, including every
  redirect), with a 15 s socket timeout, a 60 s overall limit and a 20 MB cap. A URL
  that fails is not retried. Network access stays off by default.
- A relative `codeSpace` path (`./`, `../`) is resolved next to the instance file first,
  then against the working directory as before.

### Fixed
- `schema_check()` returned a traceback instead of False when libxml2 hit an internal
  validation error; it now returns False.
- A `codeSpace` pointing at a local file that is not XML aborted the whole dictionary
  check; it is now reported as Check 3 (dictionary not accessible).
- `validator` accepts binary file objects as `instance_path` again.
- Dictionary check 12 treats the DIGGS 2.6 AllUnits spelling `cm2/m` as `cm2/min`
  (Kernel.xsd documents it as square centimeter per minute).

### Added
- `pydiggs.dictionaries.supplements`: an inventory of MWD and Pile codes required by the
  DIGGS 3.0 examples and tracked upstream
  ([#221](https://github.com/xinp-hub/pydiggs/issues/221);
  [DIGGSml/def#13](https://github.com/DIGGSml/def/issues/13) /
  [#14](https://github.com/DIGGSml/def/issues/14)).
- Releases are published to PyPI with PEP 740 attestations and attached to GitHub
  releases with Sigstore signatures and sha256 digests (see `SECURITY.md` to verify).

## [1.0.0] - 2026-08-07

Stable release of the DIGGS 3.0 + FORGE packaging train first shipped as 1.0.0a1.

### Added
- Everything from [1.0.0a1](#100a1---2026-08-07), now marked stable.
- Documentation aligned with the public 1.0.0 API (`diggs_version` override, Python 3.11+,
  four validation checks, known limits).

### Notes
- Dictionary check 12 (UOM not in quantity class) remains a **WARNING** so official
  diggs-examples with known unit/quantity mismatches still pass `dictionary_check()`.
- Unknown instance namespaces still default to DIGGS profile **3.0.0** (HG2).

## [1.0.0a1] - 2026-08-07

### Added
- DIGGS **3.0.0** bundled schemas (`schema-dev@3.0.0`) and namespace auto-detection
  (`detect_diggs_version`); unknown NS defaults to 3.0.0.
- Full official diggs-examples corpus under `tests/fixtures/official/` (2.5.a / 2.6 / 3.0
  goldens + known_invalid pins), covering schema/dictionary/Schematron/context.

### Changed
- Packaging migrated to FORGE standards: `src/` layout, **uv** + hatchling, PEP 621/639/735.
- `requires-python` raised to `>=3.11`.
- PyPI publish uses Trusted Publishing (OIDC) on `v*` tags; Actions SHA-pinned.
- DIGGS **2.5.a** auto-detect resolves bundled `Complete.xsd` (there is no `Diggs.xsd` in 2.5.a).
- Docs and contributing guide updated for uv / multi-version validation.
- Dictionary validation prefers modern `def/codes/.../properties.xml` definitions when
  instances still cite legacy `DIGGSTestPropertyDefinitions.xml` URLs; accepts integer-family
  `typeData` aliases (`int`/`integer`/`long`); maps common legacy fragments
  (`water_depth_calc`, `pore_water_pressure`); allows `measurand` where property dictionary
  Occurrences only list `propertyClass`.
- Schematron `weightRetained` assert allows zero (`>= 0`), matching `percentRetained`
  and valid empty-sieve fractions in official grading examples.

### Fixed
- `check-wheel-contents` W002 false failures from intentional byte-identical support XSDs
  across DIGGS profile trees.

### Notes
- Alpha release toward 1.0.0. Dictionary check 12 (UOM not in quantity class) is a WARNING
  so official diggs-examples with known unit/quantity mismatches still pass
  `dictionary_check()` while remaining visible in the log.

## [0.2.0] - 2026-08-07

### Added
- Semantic `dictionary_check()` aligned with DIGGSml/validation.
- Bundled lxml-adapted Schematron and `context_check()`.
- Official diggs-examples curated fixtures.

### Fixed
- DIGGS 2.5.a/2.6 schema sync; Geophysics element-name libxml2-safe fix.

## [0.1.5] - 2025-02-02

### Changed
- License Apache-2.0; optional log output CLI control; docs updates.

[Unreleased]: https://github.com/xinp-hub/pydiggs/compare/v1.9.0...HEAD
[1.9.0]: https://github.com/xinp-hub/pydiggs/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/xinp-hub/pydiggs/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/xinp-hub/pydiggs/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/xinp-hub/pydiggs/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/xinp-hub/pydiggs/compare/v1.4.1...v1.5.0
[1.4.1]: https://github.com/xinp-hub/pydiggs/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/xinp-hub/pydiggs/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/xinp-hub/pydiggs/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/xinp-hub/pydiggs/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/xinp-hub/pydiggs/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0a1...v1.0.0
[1.0.0a1]: https://github.com/xinp-hub/pydiggs/compare/v0.2.0...v1.0.0a1
[0.2.0]: https://github.com/xinp-hub/pydiggs/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/xinp-hub/pydiggs/releases/tag/v0.1.5
