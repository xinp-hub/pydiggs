# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Conformance classes for measurement while drilling (preview).** `pydiggs conformance`
  has two new classes, `mwd-export` and `mwd-import`, for drilling logs
  (`MeasurementWhileDrilling`). A challenge holds two or three borings, each with one log
  recorded by the rig: elapsed time, depth, penetration rate, thrust, torque, rotation
  speed and flushing pressure, in metric or US units. The export challenge is a DIGGS tables
  profile package, like SPT, CPT and the laboratory tests. Values are looked up by their
  DIGGS MWD property at the depth of their reading. A flushing pressure the tables leave
  empty must stay empty (a `nullValue` counts as empty), the collar reading's zero
  penetration rate and rotation speed must be in the file, and no reading may be added
  where recording paused for a rod change, whichever cells it fills. Rotation speed may
  be given in rpm or any angular velocity unit. See
  [Measurement while drilling](https://xinp-hub.github.io/pydiggs/latest/conformance/#measurement-while-drilling).
- **Conformance classes for dissipation, vane and dilatometer tests (preview).** Four new
  classes: `dissipation-export` and `dissipation-import` (pore pressure dissipation tests
  in piezocone soundings: u0, u50 and t50 at the test depth, and the u1 and u2 series
  graded point by point at each elapsed time, in seconds or minutes), and
  `in-situ-strength-export` and `in-situ-strength-import` (field vane tests with their peak
  and remoulded torque and strength and the vane's size, and a flat plate dilatometer
  sounding with its A, B and C readings and membrane calibrations). Their export
  challenges are DIGGS tables profile packages, like SPT, CPT and the laboratory classes.
  Values are graded as reported: a t50 the source leaves empty, a reading the logger lost
  and a remoulded test that was not made must stay empty in the file (a `nullValue`
  counts as empty). DIGGS 3.0.0 has no
  property for a remoulded vane strength, so the suite looks for it as
  `shear_strength_undrained_residual`. Pressuremeter, penetrometer and in-situ CBR tests
  are in the catalogue but not yet graded. See
  [the conformance suite](https://xinp-hub.github.io/pydiggs/latest/conformance/).
- **Check a delivered DIGGS file (preview).** `pydiggs conformance accept FILE --class spt`
  (and `pydiggs.conformance.accept`) is the owner's check of one file received from a
  consultant or a laboratory: the file is valid DIGGS 3, and it holds what DIGGS defines
  for the kind of data it is delivered as (any family with a class: `spt`, `cpt`, `lab-index`,
  `deep-foundations`, `mwd`, `dissipation`, `in-situ-strength`). Each requirement is published with an id (`accept.present`,
  `accept.located`, `accept.result`, `accept.position` …) and the part of DIGGS 3.0.0 or
  of its dictionaries it comes from; `conformance.requirements(family)` lists them. The
  one-page HTML report (`--report`, `--format html`) names the file by its SHA-256 and says
  what the check does not show: it compares no value with the field or laboratory records
  and makes no engineering judgement. `--format json` gives the result as a
  `diggs-conformance-acceptance` 1.0-beta.1 document, with its JSON Schema published at
  `schemas/preview/diggs-conformance-acceptance-1.0-beta.1.schema.json`. Exit status 0 when every requirement is met, 1
  when one is not, 2 when the file cannot be read.
- **Round-trip matrix (preview).** `pydiggs conformance matrix DIR` (and
  `pydiggs.conformance.matrix`) lays out the evidence of several products class by class,
  and lists each pair of a product that passed a family's export and one that passed its
  import. Every `evidence.json` is graded again first; evidence that does not verify
  (edited, of another suite version, or without the file it graded beside it) is listed
  with the reason and never counted. Text, Markdown, HTML and JSON (a `diggs-conformance-matrix` 1.0-beta.1
  document, schema at `schemas/preview/diggs-conformance-matrix-1.0-beta.1.schema.json`). A pair says
  `not-tested-together` unless the folder holds the two documents of a pairwise test
  (next two entries).
- **Test two products together: pairwise import challenges (preview).**
  `pydiggs conformance new <family>-import --from-export EVIDENCE [--export FILE]` (in
  Python, `conformance.new(..., from_export=..., export=...)`) makes an import challenge
  whose DIGGS file is another product's graded export, byte for byte, instead of the
  suite's own file. `EVIDENCE` is the `evidence.json` of that product's passing
  `<family>-export` result; the export is found beside it, or named with `--export`. The
  challenge takes the seed of that export challenge, so its questions and expected
  answers are those of the suite's ordinary import challenge of that seed. Both files
  come from another party and are checked: the export is graded again first, and the
  challenge is refused (exit status 2, nothing written) when the evidence does not
  verify, is for another family or an import class, did not pass, was graded under
  another suite version, when the file is not the one the evidence graded (by SHA-256),
  or when either file is a link, unreadable or too large. The challenge and the evidence
  graded from it record the source as `source_export`: the exporting product's name and
  version, the SHA-256 of the exported file and of the exporter's evidence, and the suite
  version. Digests, never paths. Afterwards, every `grade`, `verify` and `matrix` reads
  the challenge's `challenge.xml` again: it must have the recorded SHA-256 and pass the
  export class for the challenge's seed, so a challenge cannot name a file that is not a
  correct export of its data. `verify` reports a `source_export` that was edited, removed
  or added in the evidence alone; the challenge's identity covers it, but that identity
  is a plain hash, so a challenge and evidence rewritten together are caught only by
  those file checks and by the matrix, which asks for the exporter's own evidence. The
  evidence document is `diggs-conformance` 1.0-beta.3, which adds only that optional
  member; evidence without it means what it did, and the 1.0-beta.1 and 1.0-beta.2
  schemas stay published and are still verified. `conformance.SourceExport` is the
  record in Python. See
  [Test two products together](https://xinp-hub.github.io/pydiggs/latest/conformance/#test-two-products-together).
- **The round-trip matrix says `read` and `not-read` (preview).** A pair's cell is
  `read` when the folder holds, each verified, the exporter's passing export evidence and
  evidence of a pairwise import challenge made from that very document and file (same
  family and seed, both digests), and the answers passed; `not-read` when none of the
  answers given passed; `not-tested-together` otherwise, which includes pairwise evidence
  whose exporter's evidence is not in the folder (its entry says so). Each `read` or
  `not-read` cell cites the SHA-256 of the export (`export_sha256`) and the import
  evidence that decides it. `read` means the export was correct, was the file in the
  importer's challenge, and the answers given are right for it. It cannot show which
  software opened the file, or that the answers came from that file and not from another
  copy of the same data: a signature from the importer's CI adds that
  ([Signing conformance evidence](https://xinp-hub.github.io/pydiggs/latest/conformance-signing/)).
  Pairwise evidence decides its cell only: it does not count as the importer's
  `<family>-import` class on the matrix or on a scorecard.
- `pydiggs.conformance.conformance_schema()` takes the document it describes:
  `"evidence"` (the default, as before), `"acceptance"` or `"matrix"`.
- `pydiggs.conformance.GRADED_SUITES` lists the suite versions a release grades, and
  `Challenge.suite_version` and `Result.suite_version` say which one made a challenge and
  graded a submission.
- **The conformance suite in your browser (preview).** A page in the documentation runs
  the same three steps as `pydiggs conformance new`, `grade` and `verify` in the visitor's
  own tab: pick a class, download the challenge as a ZIP, choose your DIGGS export or the
  filled `answers.csv`, and see the grading, with `evidence.json` and `report.html` to
  download; a third tab grades a file again and confirms its evidence. pyDIGGS runs on
  Pyodide in a Web Worker, so the page stays responsive, and the files you choose are
  never uploaded: once pyDIGGS has loaded, the page makes no request at all. It loads
  Pyodide from jsDelivr and the pyDIGGS 1.17.0 wheel from PyPI, each pinned and checked
  against its hash. A challenge made on the page is the folder `pydiggs conformance new`
  writes, and its evidence is the document `grade` writes, so the command line verifies
  a result from the page and the page verifies one from the command line. Results are
  self-tested, as everywhere. See
  [In your browser](https://xinp-hub.github.io/pydiggs/latest/conformance/#in-your-browser).
- **Agreement with the DIGGS validator.** A new docs page,
  [Agreement with the DIGGS validator](https://xinp-hub.github.io/pydiggs/latest/conformance-agreement/), compares pyDIGGS's
  verdict on every file of the public conformance corpus with the DIGGS project's own
  XSLT validator ([DIGGSml/validation](https://github.com/DIGGSml/validation), pinned by
  commit and run with SaxonC-HE 12.7). It lists every file where the two differ and why:
  today 36 of 44 files, all of them files that pyDIGGS passes and the validator fails, for
  six documented reasons (code values without a DIGGS code list, CRS forms the validator
  does not accept, CRS definitions that the offline run cannot fetch, and others). The
  validator runs offline, from pinned copies of the DIGGS schemas and dictionaries, and
  calls no outside service. A difference that nobody has explained fails the checks.
- **Signing conformance evidence.** A new page,
  [Signing conformance evidence](https://xinp-hub.github.io/pydiggs/latest/conformance-signing/), gives a GitHub Actions
  workflow that runs `pydiggs conformance new` and `grade` in your own CI and signs
  `evidence.json`, the challenge and your export with GitHub artifact attestations
  (Sigstore). It also shows how anyone checks the result with `gh attestation verify` and
  `pydiggs conformance verify`, what a signature proves and what it does not, and the same
  steps with `cosign` for other CI systems. pyDIGGS itself still writes the evidence
  unsigned and gains no signing dependency.

### Changed

- **Conformance suite 1.0-beta.3: a declared `nullValue` is an empty cell in every
  class.** DIGGS lets a property declare the text that marks a missing value in its data
  (`<nullValue>-999</nullValue>`). The `mwd`, `dissipation` and `in-situ-strength` classes
  already read a cell with that mark as empty. The `spt`, `cpt`, `lab-index` and
  `deep-foundations` classes now do too. What grades differently, in those four classes
  only: an export that writes the declared mark where the source has no value (a liquid
  limit of a non-plastic specimen, the stroke of a blow-less pile run, an unused second
  column of a result table) used to fail with `conformance.value-invented` or
  `conformance.value-differs`, and now passes. The same number with no `nullValue`
  declared is still a value, and still fails. One kind of file that passed now fails:
  a file that declares as `nullValue` a number it also reports as a real value (a
  `nullValue` of 0 on a column with a real zero). DIGGS says that cell is missing, so
  the value is reported as `conformance.fact-missing`.
  A new suite version draws new data, so a seed gives other borings and values than it
  did under 1.0-beta.2, and new challenges are of suite 1.0-beta.3.
- **Challenges and evidence of suite 1.0-beta.2 keep working.** This release grades both
  suites, each by its own rules: `grade` and `verify` on a 1.0-beta.2 challenge folder
  give what pyDIGGS 1.16 and 1.17 gave, and the evidence says `1.0-beta.2`. A scorecard
  and a matrix are of the current suite only, and refuse or list 1.0-beta.2 evidence
  without counting it; grade against a new challenge to be counted there. See
  [Versions](https://xinp-hub.github.io/pydiggs/latest/conformance/#versions).

## [1.17.0] - 2026-11-23

### Added
- **Time series and several result tables (preview).** The tables profile and
  `pydiggs.write` now carry monitoring series such as a piezometer's water levels (result
  rows placed by `time`, carried as reported; `measurements.element` = `Monitor`), drilling
  logs (`MeasurementWhileDrilling`, rows by `elapsed_time` with its unit), tests with
  several result tables, such as a dissipation test's summary and its series (numbered by
  the new `result` column), and tests reported with no result rows. Reading fills the
  same columns, so this content no longer lands in the `extra` table. The official MWD,
  pore pressure dissipation and triaxial examples now go from DIGGS to tables and back
  whole.
- **The tables profile from .NET, Java or any language (preview).** Software that is not
  written in Python can now export to DIGGS and import from it with no Python code of its
  own. Over HTTP, `POST /v1/tables/to-diggs` takes your CSV tables as a ZIP and answers
  with the DIGGS 3.0.0 file and every row or value not carried; `POST /v1/tables/check`
  and `POST /v1/tables/round-trip` check the tables and prove the round trip; and
  `POST /v1/tables/from-diggs` turns a DIGGS file into the tables, as a ZIP with
  `datapackage.json` and the load plan. The stdio sidecar offers the same as
  `tables.to_diggs`, `tables.check`, `tables.round_trip` and `tables.from_diggs`
  (protocol 1.4), taking the folder of CSV files by path when started with
  `--allow-path`. Every answer is the same, byte for byte, as the Python functions give
  for the same files. A ZIP you send is read in memory and never extracted: it may hold
  at most 256 files, unpack to at most four times the size limit, and hold only plain CSV
  and JSON files, or it is refused. See
  [From .NET, Java or any language](https://xinp-hub.github.io/pydiggs/latest/tables/#from-net-java-or-any-language).
  `pydiggs.capabilities()` lists the four new operations (`tables.to-diggs`,
  `tables.from-diggs`, `tables.check`, `tables.round-trip`). `@pydiggs/client` will
  offer them in a later release; until then call the HTTP API directly.
- **Tables from Arrow (preview).** A table given to `tables.to_model`, `tables.check` or
  `tables.round_trip` may be any object that exports an Arrow stream: a pyarrow table, a
  polars or pandas data frame, a DuckDB or ADBC result. It is read one record batch at a
  time through pyarrow, which the `gis` extra installs; without pyarrow you get a
  `TablesError` that says what to install.
- **A stable identity for conformance challenges.** Evidence now names its challenge by
  `challenge.id`, a digest of what defines the challenge (suite version, class, DIGGS
  version, seed, token and product), the same in every pyDIGGS release; `Challenge.id`
  gives it in Python. `challenge.sha256` keeps its meaning (the digest of
  `challenge.json` as written). The evidence document is diggs-conformance 1.0-beta.2;
  1.0-beta.1 stays published.

### Changed
- **A result series too long for one element is written in parts.** A series over 10 MB
  of text (such as a year of one-minute readings), which earlier was reported and not
  written, is now written as consecutive tests of the same feature, each under the limit
  every XML reader with safe settings accepts; `tables.from_model` joins the parts back
  into one test. Other software shows one test per part. Only a single row over 10 MB is
  refused.
- **Sidecar protocol 1.4.** A host that asks for 1.4 gets the `tables.*` methods,
  `tables_profile_version` and `capabilities.tables`; a host that asks for 1.3 or older
  sees no change.
- **`pydiggs conformance verify` works across pyDIGGS releases.** It matches the
  challenge by its `id`, so evidence written by one release verifies with a later one
  that writes the challenge's files differently; a different file digest is printed as a
  note. Evidence of 1.0-beta.1 (pyDIGGS 1.15 and 1.16), which has no `id`, is still
  verified by the digest. The docs state the rule the pile classes already apply: a
  driving record belongs to the pile its activity names.

### Fixed
- **The stdio sidecar no longer refuses an id it has just answered.** A host that reused
  a request id as soon as it had the reply could, now and then, get `invalid-request`
  ("Duplicate id"): the job was still counted as running for a moment after its reply.
  It is now finished before its reply is written. Slower machines (often Windows) saw it
  most.
- **Reading a result table with many declared properties and short rows is fast again.**
  When a `ResultSet` declared many `Property` columns but its rows held only a few
  values each, `pydiggs.read` and `read_bytes` (and so `convert`, `diff`, the
  conformance grader, and reading through the HTTP server and the sidecar; `validate`
  does not use the reader) did work for every declared column of every row, so a file of
  a few hundred kilobytes could take seconds: 1,000 properties over 40,000 one-value
  rows took about 11 seconds and now takes about 1, and with `results=False` about 0.2.
  The read model is unchanged, byte for byte, for every file, including short rows, rows
  longer than the declared columns and null values. Such a table is now also charged to
  the read's `time_budget` column by column, so a read given a budget stops with its
  usual `structure.timeout` note instead of running on.
- **A crafted result table can no longer make reading run out of memory.** The model
  gives every column of a result table one cell per row, so a row shorter than the
  declared properties is padded with nulls. A file of a few megabytes declaring 20,000
  `Property` columns over 1,000,000 one-value rows asked `pydiggs.read` and `read_bytes`
  (and `convert`, `diff`, the HTTP server and the sidecar) for 20 billion empty cells,
  hundreds of gigabytes, and the `time_budget` could not stop it in time (none is set by
  default). Reading now counts those empty cells before making them, across the whole
  file, and stops at 4,000,000 or one per byte of the file, whichever is more, like its
  other work limits: `complete` is false, `limits_hit` names `padded_cells`, and a
  `structure.read-limit` note says where it stopped. No real file comes close: the
  official DIGGS examples, the conformance corpus and the conformance challenges need at
  most 1,683 empty cells, about 2,000 times fewer than the limit. Reading with
  `results=False` makes no empty cells and is not limited by it.

## [1.16.0] - 2026-11-22

### Added
- **Your database to DIGGS and back (preview).** `pydiggs.tables` and `pydiggs tables`
  let software that keeps its data in a database write and read DIGGS without touching
  XML. You shape your own schema into a small set of plain tables, the DIGGS tables
  profile, with SQL views or a CSV export; `pydiggs tables to-diggs FOLDER -o site.xml`
  writes a DIGGS 3.0.0 file that passes every pyDIGGS check, and `pydiggs tables
  from-diggs site.xml -o load/` turns a DIGGS file into the same tables, with a load plan
  that lists them in the order to load and the columns that identify a row. In Python,
  rows come from dictionaries, CSV folders or any database cursor
  (`tables.rows_from_cursor`); pyDIGGS needs no database driver and never connects to
  your database. No value is converted to another unit, and every row or value that could
  not be carried is named with its table, row and column. `tables.round_trip` writes your
  tables, validates the file, reads it back and lists each value that came back changed.
  See [Your database to DIGGS and back](https://xinp-hub.github.io/pydiggs/latest/tables/).
  The first profile (`1.0-beta.1`) writes boreholes, soundings, trial pits and wells with
  their samples, lithology and tests; other kinds of record are read into tables and
  reported when they cannot yet be written. A total depth that is not positive is
  reported, not written, since DIGGS requires a positive one. Nothing is rounded: a value
  with more digits than a 64-bit number holds is reported. A result table too long to be
  read back (over 10 MB of text) is reported, not written; split it by hole or by period.
- **Mapping files instead of SQL views (preview).** `pydiggs.mapping` and `pydiggs map`
  read one TOML file, a `diggs-mapping` 1.0 document, that says where each of your own
  tables and columns goes in the tables profile: a column copied, a constant, a key built
  from columns, a unit renamed (never converted) or a code translated, each code with how
  well it matches (`exact`, `close`, `broader`, `narrower`). `pydiggs map init export/`
  starts a mapping from your CSV headers and marks every guess; `pydiggs map check` lints
  it against the profile and the DIGGS units, properties and bundled code lists, with no
  DIGGS written; `pydiggs map test` writes DIGGS, validates it, reads it back and exits
  with 1 when anything was left out or came back changed, so it can run in your CI; and
  `pydiggs map coverage` shows how much of your schema the mapping reads. Every source
  value the mapping does not use is named. See
  [Map your database with a mapping file](https://xinp-hub.github.io/pydiggs/latest/mapping/),
  with a worked example in `docs/examples/mapping/`. The AGS4 mapping table used by
  `pydiggs convert` does not change.
- **Check your tables' vocabulary.** `tables.check(tables)` reports result columns that
  are not DIGGS properties, a unit a property does not take or a missing one, and codes
  that are not in a code list pyDIGGS bundles, before anything is written.
- **A public DIGGS writer.** `pydiggs.write(model, path)` and `pydiggs.write_bytes(model)`
  write a model as a DIGGS 3.0.0 file, the same bytes every time. A model the writer
  cannot write is refused with `ModelWriteError`, which names the record.
- **diggs-findings 1.3.** The findings document gains the step `tables`, for what a
  tables conversion left out or assumed. Every 1.0, 1.1 and 1.2 document is a valid 1.3
  document.
- **Laboratory and pile conformance classes (preview).** `pydiggs conformance` gains
  `lab-index-export` and `lab-index-import` (natural water content, Atterberg limits with a
  non-plastic specimen, bulk and dry density, and a particle-size curve per boring) and
  `deep-foundations-export` and `deep-foundations-import` (steel pipe, steel H or concrete
  piles with their driving records: lengths, size, capacity, and blows, penetration and
  stroke at each tip depth, including a pile run and refusal). Pile files follow what DIGGS
  3.0.0 requires: each pile refers to a test boring, and each has its own driving activity.
- **Export challenges are DIGGS tables profile packages.** The SPT, CPT and laboratory
  challenges now hold the same tables `pydiggs tables` reads and writes, with a
  `datapackage.json` that describes every column and its unit and a `plan.json` with the
  load order, so `pydiggs tables to-diggs` on a challenge folder is a passing export. The
  pile challenges stay plain CSV tables with `columns.csv`: the profile has no pile tables
  yet.

### Changed
- **Conformance suite 1.0-beta.2.** Challenges of suite 1.0-beta.1 are not graded by this
  release: make a new one. Grading now also fails a file that writes a value where the
  source leaves the cell empty (`conformance.value-invented`, for example a density that
  was not measured, or limits for a non-plastic specimen, written as zero), reads a
  percentage written as a plain percent number with no unit (a liquid limit of 48), reads
  the points of a particle-size curve, and accepts a water content reported on a density
  test.

## [1.15.0] - 2026-11-21

### Added
- **A conformance suite for DIGGS software (preview).** `pydiggs conformance` and
  `pydiggs.conformance` test how a product reads and writes DIGGS 3, on your own machine,
  with no account and nothing uploaded. `pydiggs conformance new spt-export` makes a
  challenge: plain CSV tables generated from a seed, so every challenge has other
  borings, depths and values. `pydiggs conformance grade` checks the DIGGS file your
  software exported: it is DIGGS 3, it is valid, the project and location names are
  carried exactly, and every source value is in the file at its depth. Values are
  compared in SI units, so any declared unit is accepted, within the precision the source
  gives. Each missing or different value is named. Import classes (`spt-import`,
  `cpt-import`) give a DIGGS file and questions to answer from what your software shows,
  plus a file with one planted defect. This release has classes for standard penetration
  tests and cone penetration tests, in both directions.
- **Evidence anyone can re-run.** Grading writes `evidence.json` (a diggs-conformance
  `1.0-beta.1` document, with a JSON Schema) and `report.html`. The evidence holds no time
  and no machine detail, so `pydiggs conformance verify` gives it again on any machine. A
  result is self-tested: it shows that a file passed, not which software wrote it, and it
  is not a certification by the DIGGS committee or the ASCE Geo-Institute.
- **A catalogue of the whole standard, and a scorecard.** `pydiggs conformance classes`
  lists every part of DIGGS 3.0.0, including the parts the suite cannot test yet (piles,
  laboratory tests, grouting, monitoring, geophysics and more), and
  `pydiggs conformance scorecard` shows a product against all of them.
  See [Testing DIGGS support](https://xinp-hub.github.io/pydiggs/latest/conformance/).

### Fixed
- **Unversioned documentation addresses work again.** Since the documentation became
  versioned (1.12.0), an address such as `https://xinp-hub.github.io/pydiggs/http-api/`
  was a 404: only `latest/` and `dev/` were served. pyDIGGS emits such addresses itself
  (the `type` of every HTTP API problem, the SARIF `helpUri` of a finding), and older
  READMEs on PyPI link to them. Every page now has a redirect at its unversioned address
  that keeps the `?query` and `#fragment`, and any other unversioned path falls back to
  the same path under `latest/`. What pyDIGGS emits is unchanged.

## [1.14.0] - 2026-11-19

### Added
- **Video tutorials.** The documentation has a
  [Video tutorials](https://xinp-hub.github.io/pydiggs/latest/tutorials/) page, and the
  Quickstart, Reading files, AGS4 files, conversion, comparison and Browser pages each
  open with a short narrated video of that feature. Every video has English captions,
  chapters and a full transcript, plays from the docs site itself with no third-party
  player or cookies, and is rendered from a script in `docs/tutorials/` each time the
  site is published. The commands shown really run, and a video whose output no longer
  matches its script stops the build, so the videos cannot fall behind the software.
- **Follow the video tutorials step by step.** Under each video, *Try it yourself* offers
  the files the video starts from, exactly as it used them, one by one or as a zip with
  its SHA-256, and the commands it runs, ready to copy, after a three-line uv setup that
  pins the pyDIGGS version the video shows. The files are synthetic examples (Apache-2.0).
  Every docs build runs the commands from the unpacked zip and checks they print what the
  video shows, so the download is always enough to repeat it.

### Changed
- **The HTTP API refuses a query parameter an endpoint does not take.** `/v1/validate`,
  `/v1/read`, `/v1/convert`, `/v1/ags4/check`, `/v1/diff` and `/v1/rules` answer
  `422 invalid-parameter` with `errors: [{"parameter": "max_change", "problem": "unknown
  parameter"}]` instead of ignoring it, so a misspelt option is an error rather than a
  silent default. Values are never echoed; at most 20 names are listed. This is what
  `@pydiggs/client`'s handler already does. The Python library, the CLI and the sidecar
  already refused unknown options and do not change.

  **Upgrading:** remove the parameter the answer names. To keep the old behaviour for a
  while, set `ServerSettings(unknown_query="ignore")` or `PYDIGGS_UNKNOWN_QUERY=ignore`.
  See [Unknown query parameters](https://xinp-hub.github.io/pydiggs/latest/http-api/#unknown-query-parameters).

### Deprecated
- `ServerSettings(unknown_query="ignore")` and `PYDIGGS_UNKNOWN_QUERY=ignore`: they
  exist only to ease the upgrade to 1.14, warn when used, and will be removed in
  pyDIGGS 2.0.

## [1.13.1] - 2026-11-19

### Fixed
- **HTTP: a valid `/v1/convert` request was refused when a form field overrode a query
  value.** With `?to=ags4&ags_version=4.2` and the multipart field `ags_version=4.1.1`,
  the server answered 422 "applies only to to=ags4", because it checked the overridden
  query value without the query's `to`. Each overridden query value is now checked on
  its own in the request the fields complete, as the docs say ("a field wins, but both
  must be valid"); an invalid one, such as `?to=bogus` with the field `to=diggs`, is
  still 422. `/v1/diff` follows the same rule.
- **Sidecar: a `diff` file given by path that is refused now names its side.** A
  refusal (`path-not-permitted`, `input-too-large` or `compressed-input`) carries
  `errors: [{"param": "new_path", "problem": "is not permitted"}]` or the like, as a
  refusal of an inline file already named `old` or `new`. The path itself is never
  included.
- **The OpenAPI document of `POST /v1/diff`.** `max_changes` now has the `maximum` the
  server reads (18 digits); a longer number was valid by the document but refused. The
  200 response now says that running out of time is a 200 with `complete: false` under
  thread isolation, and 504 `timeout` under process isolation (`create_app`'s default).
- **Docs: what a hand-built `Model` needs for `diff`.** A `Model` built by hand must set
  `source.sha256` to a 64-digit lowercase hex digest for `diff.compare` to give a valid
  diggs-diff 1.0 report: the report copies it unchanged. Models from `pydiggs.read` and
  `pydiggs.convert` always do.

## [1.13.0] - 2026-11-19

### Added
- **Compare two files over HTTP, the sidecar and `@pydiggs/client`.** The comparison
  `pydiggs diff` makes is now offered on every surface, each answering the same
  diggs-diff 1.0 document:
  - **HTTP.** `POST /v1/diff` takes the two files as multipart parts `old` and `new`,
    with the options `match`, `rel_tol`, `abs_tol`, `max_changes`, `show_reformatted`,
    `show_renamed`, `old_name` and `new_name` in the query or the form. Each file is held
    to the server's `max_body_bytes`, the pair runs as one job under its time limit, and
    an answer larger than four times `max_body_bytes` is 422 `result-too-large`, as for
    `/v1/read`. See [HTTP API](https://xinp-hub.github.io/pydiggs/latest/http-api/).
  - **Sidecar protocol 1.3.** The method `diff` takes each file as base64 or as a path;
    `initialize` reports `capabilities.diff` and `diff_schema_url`. A session that
    negotiated protocol 1.0 to 1.2 is refused the method.
  - **`@pydiggs/client`.** `diff(old, new, options)` on the HTTP, browser, Node, pool and
    hybrid clients, and `/v1/diff` in `createPydiggsHandler`. Requires pyDIGGS 1.13.0 on
    the server or in the worker.

  A refused file is named by its side (`old` or `new`); errors never carry a path or the
  file's content.

### Changed
- **`pydiggs.diff` is stable, and its report format diggs-diff 1.0 is frozen.** A
  diggs-diff 1.0 document changes only in a major release of the format. `to_json()`
  writes `"schema_version": "1.0"` against
  [diggs-diff-1.0.schema.json](https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json);
  documents written by 1.12 (`1.0-beta.1`) still read, and the beta schema stays
  published under `schemas/preview/`. `capabilities()` lists `diff` as stable.
- **Two files are also compared on the root element and between records.** The root
  element's attributes (other than its id and `xsi:schemaLocation`) and text before,
  between or after the top-level members are compared and listed with the document's
  record, under paths that start with `xml.Diggs[1]`.
- **Each edit is listed once.** A changed declared type is listed at the column's type,
  unit labels a result index reads at the index's unit, and a changed link at its links;
  none is listed again as file content.
- **Fewer differences that are only re-export noise.** Other `cs`/`ts` separators, a
  default separator written out or left out, and `double` declared as `float` are
  *reformatted* (listed only with `--verbose`) when every value reads the same. A
  different decimal mark is still a change, since pyDIGGS reads numbers written with a
  point.
- `compare(match="gml_id")` and `--match gml_id` are accepted as another spelling of
  `gml-id`.

### Fixed
- Two result columns that moved and exchanged a declared type of the same kind compared
  identical. Content that the model does not keep is now paired by what its typed fields
  hold before file order.
- `pydiggs validate` and `pydiggs convert` no longer fail with a traceback when the
  terminal cannot encode the report (for example a non-UTF-8 Windows console); a stdout
  that cannot be written exits 2, as `pydiggs diff` does.
- `diff.compare` raised `OverflowError` for a tolerance or time budget given as an integer
  too large for a float (such as `abs_tol=10**400`); it now raises the documented
  `ValueError` naming the option.

## [1.12.0] - 2026-11-18

### Added
- **`pydiggs.diff` and `pydiggs diff`: what changed between two DIGGS files.** The
  comparison is of what the files describe, not of how they are written: it reads both
  with `pydiggs.read` and reports which boreholes, samples, tests and other records were
  added or removed, and which values changed in the records both files hold.
  **It is a preview in 1.12:** the report format and the comparison rules may change in
  a minor release; the stable `pydiggs.diff` and diggs-diff 1.0 are planned for 1.13.
  The new page [Comparing two files](https://xinp-hub.github.io/pydiggs/latest/diff/) describes
  what counts as a change.

  ```python
  from pydiggs import diff

  d = diff.compare("site-rev1.xml", "site-rev2.xml")
  d.identical  # False
  d.summary["features"]  # {'added': 0, 'removed': 0, 'changed': 1, 'unchanged': 0}
  print(d.to_text())  # the report `pydiggs diff` prints
  d.to_json(indent=2)  # a diggs-diff 1.0-beta.1 document
  ```

  - **Command line.** `pydiggs diff OLD NEW` prints the report as text or, with
    `--format json`, as a diggs-diff document; `-o FILE` writes it to a file, in UTF-8.
    Options: `--match auto|gml-id|natural`, `--rel-tol`, `--abs-tol`, `--max-changes`,
    `--time-budget`, `--max-bytes`, and `--verbose` to also list the values that are only
    written differently and the records whose `gml:id` changed. The exit codes are those
    of `diff(1)`: **0** when the files are identical and the comparison finished, **1**
    when they differ or the comparison is incomplete, **2** on a usage error, a file that
    cannot be read or is not DIGGS, or a report that cannot be written.
  - **Python.** `diff.compare(old, new)` takes paths, bytes or `Model`s, in any mix, and
    returns a `Diff` of the added, removed and changed nodes and their values.
  - **Compare the files for the full comparison.** Given two files (paths or bytes), the
    comparison covers what `pydiggs.read` puts in a model and, beyond that, what each
    record holds that a model does not keep: the unit labels, axis labels and dimension
    of a location, the geometry of a lithology layer, a grid's origin and offsets, the
    files of a data package, a column's property class, declared type and index, the
    reason of a null value, the decimal mark and separators of a result set, and values
    in a result row that no column holds. Such a value is listed under a path that
    starts with `xml.`; an element that is renamed, added or removed is listed as a count
    under a path that ends in `#elements`, and text after a comment or a child element
    under one that ends in `#tail`. Such content stays tied to the typed value of its
    element, so two names that exchange their `codeSpace` are a difference, and a
    separator after the last value of a result row is one more value. Given two
    `Model`s, or a file and a `Model`, the comparison is of the two models and can only
    see what a model holds.
  - **What does not count.** Element order, number formatting, provenance and every
    `gml:id`: a record's own, and that of a geometry, a time instant or any other element
    inside a record. Two exports of the same data with newly generated ids are identical.
  - **Matching.** Nodes are paired by `gml:id` and element, then by a natural key (a
    borehole's kind and name; a sample's borehole, name and top; ...), so they stay
    paired when their ids change. A node paired by natural key whose id differs is
    *renamed*, not changed: it is counted in `Diff.renamed` and listed, with its old and
    new id, only with `show_renamed=True` (`--verbose`). With `match="gml-id"` there is
    no natural key, so such a node is removed and added. A key that more than one node
    has is never matched and is reported as `diff.ambiguous-match`.
  - **References.** A local reference (`xlink:href="#x"`, `srsName="#x"`) is compared by
    what it points to, not by the text of the id; one that cannot be resolved for certain
    is compared as text, which errs on the side of reporting a difference. A link to an
    element inside a record is shown as the record's name and the link (`B-1 #p2`), and a
    link to one of two equal elements is compared by its text. Repeated elements that
    differ only in what their references point to are not taken as equal, also below the
    64 levels compared one by one; those that come in another order with new ids are the
    same data when each still names its target (unless one has a `gml:id` inside). A
    reference that kept its text and names another element is shown as
    `#a -> #a (another target)`.
  - **Compare files for the fullest independence from ids.** A reference to an element
    the model does not keep, such as a borehole's linear referencing system naming its
    centre line (`linearElement`), is resolved from the files as they are read (by the
    record holding the element and its path below it, when no element on the path has a
    same-named sibling), so it needs paths or bytes. Between two `Model`s that reference,
    and a link to such an element, are compared as text (the link by its record and its
    text), so the result may list references whose target could not be established. A
    site grid that no record keeps is the same grid under a new id only when the file
    defines no other system with its name and unit, which also takes the files.
  - **Numbers and units.** Numbers compare within `rel_tol` (default 1e-9, below 1) and
    `abs_tol`. The same number written differently is counted as reformatted and listed
    only with `show_reformatted=True` (`--verbose`). A change of unit is always a change,
    and nothing is converted.
  - **Result tables** align rows by the domain index (depth) when it is unique.
  - **Limits.** `max_changes` and `time_budget` bound the work and the list: the result
    then has `complete=False` and a `diff.limit` or `diff.timeout` finding. The summary
    counts stay exact unless the time ran out, and a long value such as a coordinate
    list is compared within the time budget. A result set the reader could not read,
    and a file with more content outside the model than a file of its size may have,
    also make the comparison incomplete, so it never ends as identical.
  - **Output.** `Diff.to_json()` writes a new contract, **diggs-diff 1.0-beta.1**, with
    its JSON Schema (`diff.diff_schema()`, published as
    [`diggs-diff-1.0-beta.1.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-diff-1.0-beta.1.schema.json)).
    Paths in it are at most 1000 characters. On a terminal whose encoding cannot hold a
    character, the command line writes it in escaped form.
    `Diff.to_text()` escapes every bracket that could open `rich` markup, so no tag can
    form across two values when the text is printed through a `rich` console.
  - **Where it runs.** Pure standard library, no new dependency, and `import pydiggs`
    does not import it. `pydiggs.capabilities()` lists the operation `diff` as `preview`. It also runs
    in the browser under Pyodide. The HTTP API, the sidecar, the MCP server and
    `@pydiggs/client` do not offer it yet.
- **diggs-findings 1.2** adds the step `diff` and the rule ids `diff.ambiguous-match`,
  `diff.limit` and `diff.timeout`. It only adds to 1.1, and the 1.0 and 1.1 schema files
  are packaged and published unchanged.
- **US survey foot in AGS4, as `ftUS`.** A value in US survey feet (DIGGS unit `ft[US]`,
  or a CRS such as EPSG:2263 or 6539 whose axes are in US survey feet) was reported as
  `convert.unmapped-unit` and left out when writing AGS4. It is now written as given, in
  the project-defined unit `ftUS`, and the UNIT group of the file defines it as "US survey
  foot (1200/3937 m)", as AGS4 requires of a unit it does not list. Plain `ft` stays the
  international foot. A file that uses `ftUS` reads back to `ft[US]`. Values are never
  converted.
- **AGS4 depths in feet are carried into DIGGS.** The unit of `LOCA_FDEP` (`m`, `ft` or
  `ftUS`) is now the unit of every depth along a feature: of each feature's linear
  referencing system, of the CPT depth column and of the sample and layer depths, as
  DIGGS `m`, `ft` or `ft[US]`. Before, a depth in feet was reported as
  `convert.unmapped-unit` and left out of the DIGGS file, so a US-unit file lost every
  depth-keyed value, and the DIGGS to AGS4 to DIGGS round trip of the official US
  examples lost all its depths. Depths are carried, never converted. A file with no unit
  on `LOCA_FDEP` is still read in metres (declared as `convert.assumed`). The DIGGS
  writer names the US survey foot as linear referencing method
  `Other: trueUSSurveyFootage`, as DIGGS 3.0.0 has no name for it.

### Changed
- **The documentation site is versioned.** `latest/` is the newest release and `dev/`
  is master; the site root redirects to `latest/`, and every page states the version it
  describes. The command-line facts page and the operations table in
  [Capabilities](https://xinp-hub.github.io/pydiggs/latest/capabilities/) are generated
  from the code, and the Python examples in the guides are run in CI. Every JSON Schema
  stays at its published URL under `schemas/` at the site root, which the `$schema` of
  every document pyDIGGS writes names.
- **Findings documents now declare diggs-findings 1.2** (`schema_version` and `$schema`),
  on every surface: the Python API, the command line, the HTTP API, the sidecar and the
  MCP server. The OpenAPI and OpenRPC documents and the generated `@pydiggs/client` types
  follow. Nothing was removed or renamed, and every 1.1 document is a valid 1.2 document.
  A consumer that checks documents against the 1.1 schema must move to the 1.2 schema,
  because each schema pins `$schema` to its own URL; one that compares `schema_version`
  for equality should accept any 1.x.
- **The AGS4 and DIGGS mapping table is 1.2** (`mapping_version`; 1.11.0 shipped 1.0), a
  minor version: it adds the unit `ftUS`, and depths in feet, reported as lost before,
  are now written. Output for files with depths in metres is unchanged.
- **A file has one depth unit, that of `LOCA_FDEP`.** A depth heading in another unit is
  reported as `convert.unmapped-unit`, naming both units, and not written. This changes
  one case: in a file whose `LOCA_FDEP` is in feet while other depth headings are in
  metres, 1.11 left out `LOCA_FDEP` and wrote the depths in metres; 1.12 writes
  `LOCA_FDEP` and the depths in feet, and reports the ones in metres.

### Fixed
- **The mapping table's JSON Schema is published.** `convert-mapping-1.schema.json`,
  shipped in the wheel since 1.11.0, names
  `https://xinp-hub.github.io/pydiggs/schemas/convert-mapping-1.schema.json` as its
  `$id`, but the documentation site did not publish it there. It does now.

## [1.11.0] - 2026-11-16

### Added
- **`pydiggs.convert` is stable, both ways.** `convert.ags4_to_diggs` (the 1.9 and 1.10
  preview) is joined by `convert.diggs_to_ags4(source)` and
  `convert.model_to_ags4(model)`, which write an AGS4 file (4.2 by default, or
  `ags_version="4.1.1"`) from a DIGGS document or a `Model` in memory, by the same mapping
  table read the other way. AGS lists 4.2 as the current edition and 4.1.1 as deprecated.
  As of November 2026 the BGS AGS validator (agsapi.bgs.ac.uk) and python-ags4 1.2.0
  accept AGS4 only up to 4.1.1, so pass `ags_version="4.1.1"` for files meant for them.
  `TRAN_AGS` is written as `4.2` (the value python-ags4 recognises), static cone tests go
  to CPTG/CPTT in 4.2 and SCPG/SCPT in 4.1.1, and 4.2 output also gives the CRS as an EPSG
  code (`LOCA_NSRI`, `LOCA_LLSI`). Every value of the model (names, remarks, `params`,
  typed fields, `extra` elements, links that are not structure, result-table cells) is
  either written into a cell or named by a finding: a value with no AGS4 target is the new
  `convert.unmapped-element` warning. A unit comes from the unit table; for a CRS outside
  the code table it is the EPSG axis unit (`metre` is written as `m`, `foot` as `ft`), and
  a value whose unit cannot be named (a US survey foot, which AGS4's unit table lacks) is
  reported as `convert.unmapped-unit`, never written with an empty unit. A location name
  that AGS4 cannot hold (a line break) is replaced by the record's id and reported. The
  output passes `ags4.check` (it carries the source's non-ASCII text unchanged, which Rule
  1 reports); without a bundled AGS4 dictionary, each heading's TYPE is the one its values
  have, and the ABBR, TYPE and UNIT groups list what the file uses. The module adds no
  top-level name:

  ```python
  from pydiggs import ags4, convert

  c = convert.diggs_to_ags4("site.xml")  # or convert.model_to_ags4(model)
  c.ags4, c.validation.ok, c.losses  # the File, ags4.check, what was lost
  ags4.write(c.ags4, "site.ags")  # or c.save("site.ags")

  back = convert.ags4_to_diggs("site.ags")
  back.residual  # an AGS4 File of what was not converted
  ```
- `Conversion.residual`: for AGS4 to DIGGS, an AGS4 `File` holding exactly the values a
  `convert` finding names, with the key fields of their rows, so the DIGGS output and the
  residual file together hold everything the source did. `Conversion.ags4` holds the AGS4
  output of the other direction, and `Conversion.save()` writes either.
- Formula-like text in AGS4 output (a value starting with `=`, `+`, `@`, or a `-` that is
  not a number) is written unchanged and reported as `ags4.formula-text`; the opt-in
  `formula_guard="prefix"` writes such text under a non-key text heading with an
  apostrophe in front, and reports it.
- **Mapping table 1.0** (`convert.MAPPING_VERSION`), with the Tier B groups: hole
  diameter (HDIA), casing (CDIA), backfill (BKFL), water strikes (WSTG with WSTD), density
  (LDEN) and static cone tests (SCPG with SCPT, and CPTG with CPTT, which replace them in
  AGS4 4.2; DIGGS to AGS4 writes the pair of the target version). Hole orientation (HORN)
  is listed and reported, not converted: AGS4 measures inclination positively down and
  DIGGS 3.0.0 plunge positively up, and values are never converted. The table format
  gains the `feature-component` pattern and the group keys `role`, `holder`,
  `holder_role`, `interval_top` and `to_ags4_versions` (`convert-mapping-1.schema.json`);
  `mapping_info()` reports the table. All 25 groups are **reviewed** (owner review against
  the AGS4 4.1.1 and 4.2 dictionaries and DIGGS 3.0.0); the generated page is now "AGS4
  and DIGGS mapping".
- **`pydiggs convert --to ags4`: DIGGS to AGS4 on the command line.**
  `pydiggs convert site.xml -o site.ags --to ags4 [--ags-version 4.2|4.1.1]` writes AGS4,
  and `pydiggs convert site.ags -o site.xml --residual left.ags` also writes the residual
  AGS4 file (not written when everything was converted). The direction is never guessed:
  AGS4 to DIGGS stays the default, and `--diggs-version`, `--ags-version` and
  `--residual` are refused for the other direction. With `--fail-on error` an output that
  fails the AGS4 check exits 1, as one that fails DIGGS validation does.
- **HTTP API and sidecar: conversion to AGS4.** `POST /v1/convert` and the sidecar
  `convert` method (protocol 1.2) take `to=ags4` with `ags_version` for a DIGGS document
  (a raw HTTP body is `application/xml` or `text/xml`) and answer AGS4 as
  `text/plain; charset=utf-8`, validated by `ags4.check`. `ConversionResult` gains
  `ags_version` and `residual_base64`, the residual AGS4 file of an AGS4 to DIGGS
  conversion (null when nothing was left over, and for `to=ags4`). The sidecar's
  `capabilities.convert` lists the targets, the AGS4 versions written and
  `residual: true`. OpenAPI, `openrpc.json` and the JS types are regenerated.
- `pydiggs.capabilities()` gains `convert.diggs-to-ags4`, and both conversions are now
  `stable` (the AGS4 reading, writing and checking operations stay `preview`).
- The Pyodide smoke converts an AGS4 file to DIGGS and that DIGGS back to AGS4 4.2 and
  4.1.1, and compares the outputs and the residual file with CPython's bytes.
- Documentation: the conversion page is now [AGS4 and DIGGS
  conversion](https://xinp-hub.github.io/pydiggs/latest/convert/), and the conversion API is in
  the Python API reference.

### Changed
- `Conversion.xml` is None for a conversion to AGS4 (it was always bytes in the preview).
- AGS4 to DIGGS: a GRAT row whose GRAT_TYPE is WS or DS is still written as a sieve
  grading, but the code itself is now reported as `convert.unmapped-code` (DIGGS does not
  say whether sieving was wet or dry); a test placed at its sample's top because it gives
  no depth of its own is reported as `convert.assumed`. Mapping table 0.1 becomes 1.0.
- **What moved since the 1.9.0 and 1.10.0 preview (mapping table 0.1 to 1.0).**
  Conversions of the same AGS4 file give different DIGGS than 1.9.0 and 1.10.0 did:
  - `GEOL_FORM` (the geological unit or stratum name) is now the DIGGS `unitName`;
    `GEOL_GEOL`, a project geology code whose meaning is in the file's ABBR group, is
    reported and no longer written as the unit name.
  - `LOCA_LOCA`, a grouping of locations by site sub-division, is reported and no longer
    written as the location's description.
  - The GRAG fractions (`GRAG_VCRE`, `GRAV`, `SAND`, `SILT`, `FINE`) are not converted
    either way and are reported: AGS4 uses ISO 14688-1 size limits (63 mm, 2 mm, 63 µm)
    and the DIGGS properties ASTM limits (75 mm, 4.75 mm, 0.075 mm). A GRAG row with no
    other result (`GRAG_UC`, `GRAG_CLAY`, the new `GRAG_CC`) cannot be written as a DIGGS
    test and is reported as `convert.incomplete` (an error: `pydiggs convert` exits 1),
    and its GRAT readings are then reported too.
  - `SAMP_TYPE`: `WS` and `SPT`, which are not AGS4 sample type codes, are written as
    text and reported; `L`, `SPTLS`, `M`, `TW` and `EW` are added. `TW` and `EW` share
    their DIGGS code with `UT` and `W`, so the AGS4 code is also reported.
  - Static cone tests whose `SCPG_TYPE` or `CPTG_TYPE` is `BALL` or `T-BAR` are reported,
    not written: their resistance is not a cone resistance.
  - `LNMC_MC` with `LNMC_ISNT` = N, and `LDEN_BDEN` and `LDEN_MC` of a `RECOMPACTED`
    specimen, are reported instead of being written as DIGGS properties for natural
    conditions. An SPT N (`ISPT_NVAL`, `ISPT_N60`) from a test drive that stopped short of
    300 mm (`ISPT_PEN3` to `ISPT_PEN6`) is reported, not written as blows per foot.
  - A missing or unreadable `TRAN_DATE` is written as
    `<creationDate indeterminatePosition="unknown"/>` (still `convert.assumed`), not as
    1970-01-01; `Model.document.creation_date` is then None. DIGGS to AGS4 leaves the
    REQUIRED `TRAN_DATE` empty for such a document and reports it.
  - AGS4 codes are matched as AGS4 says abbreviations are, not case sensitive, and joined
    codes match in either order with the file's `TRAN_RCON` (so `rc+cp` is DIGGS CP-RC).
  - New: `LOCA_TYPE` `SCP` (a Sounding), `SNC`, `VE`, `VC`, `AUGH`, `AUGS`, `TT`;
    `LOCA_GREF` `OSI`; AGS4 4.2 `LOCA_NSRI` and `LOCA_LLSI` read before `LOCA_GREF` and
    `LOCA_LLZ`; a ground level over British National Grid with `LOCA_NATD` naming
    Ordnance Datum Newlyn is written in EPSG:7405 (OSGB36 + ODN height), and any other
    ground level is reported as having no stated vertical datum; `ISPT_N60` (`n60`),
    `GRAG_CC` (`coef_curvature`), `WSTG_CAS` (`bottomCasing`), and the cone readings
    `SCPT_FRR`/`CPTT_RF` (`friction_ratio`), `CPTT_INCX`/`INCY`, `SCPT_TEMP`/`CPTT_TEMP`,
    `SCPT_SMP`/`CPTT_SMP` and `CPTT_DUR`; DIGGS to AGS4 writes NP in `LLPL_PL` only.
- The internal writer writes the `extra` elements of a component (such as a water
  strike's `bottomCasing`) as given, and writes a document with no creation date with
  an unknown one.
- The HTTP problem `invalid-document` is titled "Invalid document" (it was "Not a DIGGS
  document"), since `/v1/convert` answers it for AGS4 input too; the code, status and
  `kind` are unchanged. The sidecar's fixed messages for `invalid-document` and
  `result-too-large` (and so `openrpc.json`) no longer name DIGGS input or `select`
  only; the detail of a `read` that is too large still suggests `select`.
- The plain-language guidance of `convert.unmapped-code`, `convert.unmapped-unit` and
  `convert.assumed` fits both directions of conversion.

### Deprecated
- `pydiggs.preview.convert` is a deprecated alias of `pydiggs.convert`: using one of its
  names warns with a `DeprecationWarning`, and it will be removed in pyDIGGS 2.0.
  Importing it does not warn.

### Fixed
- `pydiggs.read` read a component's `bottomCasing` or `bottomHole` as its location when
  it had no other point, so a water strike that gave only its casing depth came back with
  that depth as its seal depth. Those two elements now stay in the component's `extra`.
  Only files with such components read differently.
- AGS4 to DIGGS no longer drops a unit without saying so. Grid coordinates written with
  no CRS whose axes the code table gives a unit for (an empty or unlisted `LOCA_GREF`),
  and values whose DIGGS property has no unit (the `%` of the LLPL limits), are written
  as before, and their UNIT is now reported as `convert.unmapped-unit`; the residual file
  keeps those values with their unit.

## [1.10.0] - 2026-11-14

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
- Documentation: [AGS4 files](https://xinp-hub.github.io/pydiggs/latest/ags4/) and
  [AGS4 to DIGGS](https://xinp-hub.github.io/pydiggs/latest/convert/).
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

### Fixed

- **A correct field plasticity term is no longer an error.** The bundled Schematron rule
  for `FieldProperties/plasticity`, copied from the official DIGGS validation rules,
  required a number of zero or more, but in DIGGS 2.6 and 3.0 plasticity is a term such
  as `low`, `medium`, `high` or `non-plastic`. Every correct value failed the file and a
  bare number passed. pyDIGGS drops that rule, so `schematron.FieldProperties.plasticity`
  is no longer reported or listed by `rule_catalogue()`. This is a known difference from
  the official validator, listed in the bundled Schematron file's header, and has been
  reported upstream ([DIGGSml/validation#2](https://github.com/DIGGSml/validation/issues/2)).
- **Plain-language guidance for the plunge rule says what the official rule gets
  wrong.** `schematron.AbstractLinearSamplingFeature.plunge` accepts only -45 to -10
  degrees, while DIGGS allows -90 to 90, so a vertical hole is outside it. (The rule names
  the abstract type, so it does not fire on a `Borehole`.) The guidance now tells users
  to keep a correct angle. Reported upstream
  ([DIGGSml/validation#3](https://github.com/DIGGSml/validation/issues/3)).

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
  generated page, [AGS4 to DIGGS mapping](https://xinp-hub.github.io/pydiggs/latest/convert-mapping/).
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
  in CI. A new [Licence and use](https://xinp-hub.github.io/pydiggs/latest/license/) page says
  what you may do, what you must do when you redistribute, and what a pyDIGGS check does
  and does not tell you about engineering data. The pyDIGGS licence is unchanged
  (Apache-2.0).
- **The package's licence metadata now names every licence it contains**:
  `Apache-2.0 AND MPL-2.0 AND MIT AND LicenseRef-Energistics-PLA AND LicenseRef-POSC-LA
  AND LicenseRef-OGC-2001 AND LicenseRef-EPSG-TOU AND LicenseRef-DIGGS-Unstated AND
  LicenseRef-OASIS` (the container image label likewise, without the test-only OASIS
  entry). If your SBOM or licence scanner allows only SPDX-listed licences, it will now
  flag pyDIGGS for review; the terms behind each `LicenseRef-` are in `LICENSES/` and on
  the [Licence and use](https://xinp-hub.github.io/pydiggs/latest/license/) page, which now has
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

Stable release of the DIGGS 3.0 and packaging work first shipped as 1.0.0a1.

### Added
- Everything from [1.0.0a1](#100a1---2026-08-07), now marked stable.
- Documentation aligned with the public 1.0.0 API (`diggs_version` override, Python 3.11+,
  four validation checks, known limits).

### Notes
- Dictionary check 12 (UOM not in quantity class) remains a **WARNING** so official
  diggs-examples with known unit/quantity mismatches still pass `dictionary_check()`.
- Unknown instance namespaces still default to DIGGS profile **3.0.0**.

## [1.0.0a1] - 2026-08-07

### Added
- DIGGS **3.0.0** bundled schemas (`schema-dev@3.0.0`) and namespace auto-detection
  (`detect_diggs_version`); unknown NS defaults to 3.0.0.
- Full official diggs-examples corpus under `tests/fixtures/official/` (2.5.a / 2.6 / 3.0
  goldens + known_invalid pins), covering schema/dictionary/Schematron/context.

### Changed
- Packaging modernised: `src/` layout, **uv** + hatchling, PEP 621/639/735.
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

[Unreleased]: https://github.com/xinp-hub/pydiggs/compare/v1.15.0...HEAD
[1.15.0]: https://github.com/xinp-hub/pydiggs/compare/v1.14.0...v1.15.0
[1.14.0]: https://github.com/xinp-hub/pydiggs/compare/v1.13.1...v1.14.0
[1.13.1]: https://github.com/xinp-hub/pydiggs/compare/v1.13.0...v1.13.1
[1.13.0]: https://github.com/xinp-hub/pydiggs/compare/v1.12.0...v1.13.0
[1.12.0]: https://github.com/xinp-hub/pydiggs/compare/v1.11.0...v1.12.0
[1.11.0]: https://github.com/xinp-hub/pydiggs/compare/v1.10.0...v1.11.0
[1.10.0]: https://github.com/xinp-hub/pydiggs/compare/v1.9.0...v1.10.0
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
