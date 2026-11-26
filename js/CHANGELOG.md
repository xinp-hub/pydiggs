# Changelog

All notable changes to `@pydiggs/client` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/). Its version is its own: what it works with
is stated by contract (diggs-findings 1.x, diggs-model 1.x, HTTP API `/v1`), not by a
matching pyDIGGS version number.

## [Unreleased]

## [0.13.0] - 2026-11-26

### Added

- **`pydiggsWheelSha256`: a wheel you host is checked before it is installed.** Until
  now only the default release from PyPI was checked against a sha256; a wheel named
  with `pydiggsWheelUrl` (`PydiggsBrowser`) or `pydiggsWheelPath` (`PydiggsNode`) was
  installed as downloaded. Pass the wheel's sha256 as 64 hex digits and the worker
  checks the bytes before it unpacks them. A wheel that does not match is never
  installed: loading fails, and `ready` and every request reject with a
  `PydiggsBrowserError` or `PydiggsNodeError` that says so. A value that is not 64 hex
  digits is refused before anything is downloaded. The check is on whichever wheel is
  installed: given with no wheel of your own, the default release from PyPI must match
  it too, and a build with no pinned wheel (it would install pyDIGGS by version through
  micropip, whose download the worker cannot check) fails to load. Without the option
  nothing changes.
- **`pyodideSri` (`PydiggsBrowser`): load a Pyodide release you host that is not the
  pinned one.** The worker checks Pyodide's loader against `PYODIDE_SRI`, the hash of
  release `PYODIDE_VERSION`, so a self-hosted copy of any other release (a newer patch
  release, say) could not start. With `pyodideBaseUrl`, `pyodideSri` now names the
  integrity value of your copy's `pyodide.mjs` (`sha384-...`; sha256 and sha512 are
  taken too). The loader is still checked, against that value; one that is malformed,
  or given without `pyodideBaseUrl`, is refused and nothing loads. The default is
  unchanged. Only the pinned release is tested by this package: CI proves pyDIGGS's
  answers on that Pyodide and its lxml alone, so with another release compatibility is
  yours to check. `PydiggsNode` and `pydiggs-client vendor` still take the pinned
  release only, and the `pyodide` peer dependency is unchanged.
- **`maxFindings` and `timeBudgetSeconds` on `validate` and `validateSarif`**, for
  `PydiggsBrowser`, `PydiggsNode`, a `PydiggsPool` of them and `PydiggsHybrid`.
  `maxFindings` is the most findings reported (pyDIGGS's default, 10 000, applied until
  now with no way to change it); when it is reached the list ends with a
  `structure.findings-truncated` finding. `timeBudgetSeconds` is how long pyDIGGS may
  spend, checked between steps; running out still gives a findings document, with a
  `structure.timeout` error (no budget by default, as before). A value pyDIGGS would
  not take (`0`, a fraction of a finding, `Infinity`, more than 86 400 seconds) is
  refused as `invalid-parameter` before the document is sent. They work with every
  pyDIGGS wheel the engine accepts (1.7.0 or later). `PydiggsHttpClient` has no such
  options: pyDIGGS's HTTP server takes these limits from its own settings
  (`PYDIGGS_MAX_FINDINGS`, `PYDIGGS_TIMEOUT_S`), never from a request, so in
  `PydiggsHybrid` they apply when the document runs in the tab.

### Changed

- Runs pyDIGGS 1.18.1 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. pyDIGGS 1.18.1 is 1.18.0 with a new version number: the release exists for
  its container image, and nothing the client calls differs.

## [0.12.0] - 2026-11-25

### Changed

- Runs pyDIGGS 1.18.0 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. Everything the client calls works as in 0.11.0, and the client gains no method.
  What pyDIGGS 1.18.0 adds is all in its conformance suite for DIGGS software, a preview
  offered as the `pydiggs conformance` command and the `pydiggs.conformance` Python
  module, with no HTTP route or sidecar method: six new classes (measurement while
  drilling, dissipation tests and in-situ strength tests, export and import), the check
  of a delivered file (`conformance accept`), the round-trip matrix
  (`conformance matrix`), pairwise import challenges made from another product's export,
  and suite 1.0-beta.3. The operations `capabilities()` reports, the rules `listRules`
  and `explainRule` know, and the findings, model and diff schemas are those of 1.17.0.

## [0.11.0] - 2026-11-23

### Added

- **The DIGGS tables profile (preview)**, pyDIGGS 1.17.0's `POST /v1/tables/*` routes,
  as four methods: `tablesToDiggs(zip)` writes a package of CSV tables (a ZIP, with
  `datapackage.json` if you have one) as DIGGS 3.0.0; `checkTables(zip)` checks the
  tables against the DIGGS dictionaries and resolves with a findings document;
  `tablesRoundTrip(zip)` writes, validates, reads back and compares; and
  `tablesFromDiggs(xml)` turns a DIGGS file into the tables as a ZIP, with its load plan.
  They are on every client: `PydiggsHttpClient`, `PydiggsBrowser`, `PydiggsNode`,
  `PydiggsPool` and `PydiggsHybrid`, and `createPydiggsHandler` serves the four routes.
  In the browser engine the export never leaves the device. The results are typed
  (`TablesToDiggsResult`, `TablesRoundTripResult`, `TablesFromDiggsResult`, generated
  from pyDIGGS's OpenAPI document), and `conversionOutput(result)` now also gives a
  tables result's file (the DIGGS file, or the package ZIP), checked against its
  sha256. They need pyDIGGS 1.17.0 or later on the server; an engine running an older
  pyDIGGS refuses them with `code: "internal"`. The `PoolEngine` methods are optional,
  as `diff` is, so an engine of your own written before them still is one.
  `PydiggsHybrid` sends a request to the server when its `browser` is such an engine;
  a `PydiggsPool` of such engines rejects it with a `PydiggsError`, as it does `diff`.
  A package is bytes (`Blob`, `ArrayBuffer` or a typed array), never a string. The
  engines' `maxResultBytes` must be a positive whole number; any other value is refused
  as `invalid-parameter`.

### Fixed

- `createPydiggsHandler`: a `name` with no base name (`?name=`, `?name=folder/`, `.`,
  `..`, or a multipart file part with an empty filename) on `/v1/convert` and
  `/v1/ags4/check` reached pyDIGGS as `input.xml`. It is now no name, as on pyDIGGS's
  own server, so pyDIGGS reports an AGS4 file under its default, `input.ags`.

### Changed

- Runs pyDIGGS 1.17.0 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. Everything the client called before works as in 0.10.0. The tables profile of
  1.17.0 carries time series and tests with several result tables, so the new methods
  do too. Its other news (tables from Arrow, a stable identity for conformance
  challenges, sidecar protocol 1.4) is for Python, the command line or the sidecar, so
  the client gains no other method with it.

## [0.10.0] - 2026-11-22

### Changed

- Runs pyDIGGS 1.16.0 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. Everything the client calls works as in 0.9.0. pyDIGGS 1.16.0 adds, as
  previews, the DIGGS tables profile (`pydiggs tables`, `pydiggs.tables`), mapping files
  (`pydiggs map`, `pydiggs.mapping`) and the laboratory and pile conformance classes, all
  from the command line or Python, with no HTTP route or sidecar method yet, so the
  client gains no method with them. Its new public writer (`pydiggs.write`) takes a
  Python model, which no HTTP route or sidecar method takes either.
- pyDIGGS 1.16.0 is the first release that reports the findings step `"tables"`, which
  the findings types already accept (diggs-findings 1.3, since 0.9.0). It reports that
  step only for a tables conversion, which the client does not run yet, so the findings
  of `validate`, `checkAgs4`, `convert` and `diff` do not carry it. The findings
  documents the engine returns now name the diggs-findings 1.3 schema (`FINDINGS_SCHEMA_URL`) rather than 1.2, and `listRules`
  and `explainRule` also know the `tables.*` rules.

## [0.9.0] - 2026-11-22

### Changed

- Runs pyDIGGS 1.15.0 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. pyDIGGS 1.15.0 adds the conformance suite for DIGGS software, a preview it
  offers as the `pydiggs conformance` command and the `pydiggs.conformance` Python
  module, with no HTTP route, so the client gains no method with it. Everything the
  client calls works as in 0.8.0.
- The findings types follow **diggs-findings 1.3**, which only adds to 1.2: the step
  `"tables"` (the findings of a tables conversion). `FINDINGS_SCHEMA_URL` is the 1.3 schema,
  shipped as `@pydiggs/client/schemas/diggs-findings-1.3.schema.json`; the 1.0, 1.1 and 1.2
  schema files are still shipped unchanged. pyDIGGS 1.15.0, which this release runs, does
  not report that step: it comes with the tables conversion in a later pyDIGGS release.

## [0.8.0] - 2026-11-20

### Changed

- Runs pyDIGGS 1.14.0 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. Its HTTP server now refuses a query parameter an endpoint does not take, with
  422 `invalid-parameter`, as `createPydiggsHandler` already does. `PydiggsHttpClient`
  sends only parameters each endpoint takes, so it works with it unchanged.

### Fixed

- `createPydiggsHandler` answers 503 `busy` only after a request's own checks, as
  pyDIGGS admits requests. A request that is wrong anyway (an unknown query parameter,
  the wrong media type, a compressed or too-large body) now gets that answer, 422, 415
  or 413, when the engine is full, not 503 `busy`.
- `/v1/versions`, `/v1/capabilities`, `/v1/rules` and `/v1/rules/{rule_id}` no longer
  wait behind documents or answer `busy` once they have answered. pyDIGGS answers them
  at once; the handler now keeps each first answer, which does not change while the
  engine runs, and gives it again without asking the engine. Refusals and unknown rules
  are never kept. Until a route has answered once, a full engine still makes it 503
  `busy`.
- `/v1/versions`, `/v1/capabilities`, `/v1/rules` and `/v1/rules/{rule_id}` always
  answer JSON, as pyDIGGS does, whatever `Accept` asks for. Before, an `Accept` header
  without JSON (say a browser's `text/html`) was answered 406 `not-acceptable`.

## [0.7.0] - 2026-11-19

### Added

- `diff(old, new, options)` compares two DIGGS files (pyDIGGS 1.13), on the HTTP, browser,
  Node, pool and hybrid clients, and `createPydiggsHandler` serves `/v1/diff`. The answer
  is a diggs-diff 1.0 document, typed as `DiggsDiffDocument` and shipped as
  `@pydiggs/client/schemas/diggs-diff-1.0.schema.json`. It needs pyDIGGS 1.13.0 or
  later on the server or in the worker.
- `PydiggsHybrid` has `validateSarif` and `checkAgs4Sarif`, as the other engines do. They
  route like `validate` and `checkAgs4`, and go to the server when the browser engine
  has no SARIF call.
- `createPydiggsHandler` takes `bodyTimeoutMs` (default 30000, as pyDIGGS): a request
  body that has not arrived by then is answered 408 `request-timeout`.
- `diff()` takes `maxResultBytes`: pyDIGGS refuses a larger answer as `result-too-large`
  before building it. The handler sets it to four times `maxBodyBytes`, as pyDIGGS's
  server does.

### Changed

- Runs pyDIGGS 1.13.1 (`PYDIGGS_VERSION`), its wheel pinned by sha256 from PyPI's
  record. AGS4 files with depths in feet convert to DIGGS (mapping table 1.2, pyDIGGS
  1.12).
- The findings types follow **diggs-findings 1.2** (pyDIGGS 1.12), which only adds to 1.1:
  the step `"diff"` (the findings of a model diff). `FINDINGS_SCHEMA_URL` is the 1.2 schema,
  shipped as `@pydiggs/client/schemas/diggs-findings-1.2.schema.json`; the 1.0 and 1.1
  schema files are still shipped unchanged.

### Fixed

`createPydiggsHandler` now answers as pyDIGGS's own server does, where it did not:

- A request beyond `maxPending` is refused with 503 `busy` before its body is read.
  `maxPending` now counts requests from arrival until they are answered, as pyDIGGS
  admits them. Before, uploads still being received did not count, so many slow uploads
  at once could each hold a body in memory. A body must arrive within `bodyTimeoutMs`,
  and a request whose client goes away gives up its place, so slow or abandoned uploads
  cannot hold every place.
- A field sent as a file part (say `name` as a file) is refused with 400
  `invalid-multipart` on `/v1/validate`, `/v1/read`, `/v1/convert` and `/v1/ags4/check`.
  Before, it was dropped silently.
- A query value that a form field overrides is still checked. An invalid one is refused
  with 422 `invalid-parameter`, with pyDIGGS's texts.
- The media type is checked before the encoding. A compressed body of the wrong type is
  now 415 `unsupported-media-type`, as in pyDIGGS, not `compressed-input`. An empty
  `Content-Encoding` header is accepted.
- A multipart body may be as large as pyDIGGS allows: the file part up to
  `maxBodyBytes`, plus room for the multipart framing.
- Multipart parts are checked in pyDIGGS's order and within its limits. No file part is
  400 `missing-input` (it was `invalid-multipart`), and on `/v1/diff` a missing file is
  reported before an unknown field. More than 8 fields, a field over 64 KiB, or more
  file parts than the route takes, is `invalid-multipart`. A too-large `/v1/diff` body
  closes the connection, and an upload whose client went away is `missing-input`.
- Names are measured in characters, as pyDIGGS counts them, not UTF-16 units. A name of
  emoji is no longer refused below the 4096-character limit.

## [0.6.0] - 2026-11-16

### Added

- AGS4, from pyDIGGS 1.10 and 1.11, on every engine: `convert(file)` turns an AGS4 file
  into DIGGS 3.0.0, and `convert(file, { to: "ags4", agsVersion })` a DIGGS document into
  an AGS4 file (4.2, or 4.1.1). The result carries the output as base64 with its sha256,
  the findings about the source and the output's validation. `checkAgs4(file,
  { dictionary })` and `checkAgs4Sarif` check an AGS4 file against the AGS4 rules. In
  `PydiggsBrowser` the file never leaves the tab; `PydiggsNode`, `PydiggsPool`,
  `PydiggsHybrid` and `PydiggsHttpClient` have the same calls, and `createPydiggsHandler`
  serves `/v1/convert` (both ways) and `/v1/ags4/check`. CI compares every answer with
  CPython's.
- `conversionOutput(result)`: the converted file as bytes, checked against its sha256
  (uses `Uint8Array.fromBase64` where the runtime has it), and `conversionResidual(result)`:
  for AGS4 to DIGGS, the AGS4 file of every value that was not converted.
- `capabilities()` on every engine and the HTTP client, and `/v1/capabilities` from the
  handler: what this pyDIGGS can do.
- Types for conversion results and capabilities documents (`ConversionResult`,
  `Capabilities`, `ConvertTarget`, `ConvertDiggsVersion`, `ConvertAgsVersion`),
  generated from pyDIGGS's OpenAPI document.

### Changed

- Runs pyDIGGS 1.11.0 (`PYDIGGS_VERSION`), its wheel pinned by sha256. Validation is
  about a fifth faster on documents with many Schematron hits (from 1.10).
- The `createPydiggsHandler` problem for `invalid-document` is titled "Invalid document",
  as pyDIGGS 1.11's HTTP API titles it now that `/v1/convert` also answers it for AGS4
  input. The code, status, `kind` and detail are unchanged.

## [0.5.0] - 2026-11-13

### Added

- `startupSnapshotDir` for `PydiggsNode` (experimental): the first start saves a snapshot
  of the freshly started Pyodide runtime, and later starts and restarts restore it, ready
  in about half the time (1.2 s against 2.5 s on a laptop). Tied to the pinned Pyodide,
  whose snapshot options are not public yet; a snapshot that cannot be used is replaced by
  a normal start.
- README recipes for standalone offline apps: installable web apps, Tauri 2 and Electron.

### Changed

- Faster start. The worker unpacks the pydiggs wheel itself instead of loading micropip
  to install it (installing took 0.8 s, now 0.3 s), and downloads the wheel while Pyodide
  starts rather than after. micropip is no longer downloaded, vendored or precached,
  except when no wheel is pinned.
- No more "The fd argument must be of type number" errors and "Loading lxml" lines in a
  Node server's log: Pyodide's output in the worker thread is routed, and stderr goes to
  `console.warn` marked `[pydiggs]`.
- The README no longer suggests COEP `credentialless`, which Safari does not support.
- Runs pyDIGGS 1.9.0 (`PYDIGGS_VERSION`), pinned by the sha256 PyPI published for its
  wheel. It adds AGS4 reading and checking, diggs-findings 1.1, and the AGS4 to DIGGS
  conversion preview, and no longer imports networking modules under Python 3.14, so
  the browser worker loads less.

## [0.4.0] - 2026-11-12

### Added

- The whole pyDIGGS API from JavaScript, without a server. The worker now runs every
  request through pyDIGGS's own service layer, the one `pydiggs serve --http` uses, so
  `PydiggsBrowser`, `PydiggsNode` and `PydiggsPool` take what the HTTP client takes:
  `validate(doc, { checks, diggsVersion })`, `validateSarif(doc)` (a SARIF 2.1.0 log),
  `read(doc, { diggsVersion, results })` where `results` may be `"all"`, `"none"` or a
  list of node keys (reading the same file again for other tables is fast: pyDIGGS
  keeps the last few models it read), `versions()`, `listRules({ step })` and
  `explainRule(id)`. CI checks each answer against CPython's service layer.
- `createPydiggsHandler` serves the full `/v1` API: `checks`, `diggs_version`, SARIF by
  `Accept` (negotiated as pyDIGGS does), `select` with node keys, `/v1/versions`,
  `/v1/rules` and `/v1/rules/{rule_id}`. Refusals are pyDIGGS's own problem documents,
  with its `errors` and `kind`.
- Errors carry `problem`: pyDIGGS's code (`invalid-document`, `invalid-parameter`,
  `input-too-large`, ...), its fixed text, `errors` and `kind`.
- Interrupting instead of restarting. Where memory can be shared with the worker (Node,
  and pages served cross-origin isolated), a request past `timeoutMs`, or one whose
  `signal` aborts, is stopped with Pyodide's interrupt buffer, and the worker is kept.
  Reading, which is Python, stops at once; validation finishes the lxml step it is in
  (schematron can take seconds), and a worker that has not stopped within
  `interruptGraceMs` (default 2 s) is replaced as before. New: `interruptible`,
  `restarts`.
- `idleTimeoutMs`: stop an idle worker to give its memory back; the next request starts
  it again. Status `"idle"`. Off by default.
- `@pydiggs/client/sw`: `cachePydiggs(self)` in your own service worker keeps Pyodide's
  files, lxml, micropip and the pydiggs wheel in the Cache API, so pyDIGGS loads and runs
  offline after one visit (tested in Chromium). `precache: true` downloads everything at
  install.

### Changed

- The worker installs the pinned pydiggs wheel itself and checks it against the sha256
  PyPI published for it (`PYDIGGS_WHEEL`), instead of asking micropip to find the
  release. A tampered or truncated wheel is refused, and the browser no longer contacts
  `pypi.org`: the worker CSP needs only `cdn.jsdelivr.net` and `files.pythonhosted.org`.
- `read`'s `results` option takes the HTTP client's values (`"all"`, `"none"`, a key
  list); `true` and `false` still work. The worker protocol (internal) changed with it.
- `PoolEngine` and the handler's `PydiggsEngine` include the new methods; an engine of
  your own needs them too.
- Still pins pyDIGGS 1.8.0. Its service layer imports `urllib.request` (and with it
  `http.client`, `socket` and `ssl`) when it first tidies a finding text under Python
  3.14, as Pyodide runs; nothing is sent. Fixed in the next pyDIGGS release, which the
  client follows.

## [0.3.0] - 2026-11-12

### Added

- `PydiggsPool`: several engines (`PydiggsBrowser` or `PydiggsNode`) behind one
  `validate` and `read`, each document going to the least busy engine; usable as
  `PydiggsHybrid`'s `browser` and with `createPydiggsHandler`.
- `pydiggs-client vendor <dir>`: assembles Pyodide, its packages and the pydiggs wheel
  into one folder, every file checked against Pyodide's lock file, PyPI and the worker's
  integrity hash, for self-hosting in a browser (`pyodideBaseUrl`) or as a complete
  `packageCacheDir` for Node and serverless functions (`--packages-only`). Also
  `vendor()` from `@pydiggs/client/node`.
- Tested: self-hosted with no CDN allowed by the CSP, in Chromium, Firefox and WebKit;
  the Vue recipe; the Node engine under Bun and Deno. The README has recipes for Vue,
  serverless functions and batches.

## [0.2.0] - 2026-11-12

### Added

- `@pydiggs/client/hybrid`: `PydiggsHybrid` runs each document in the browser when it
  can and on your server when it cannot: the document is over `maxBrowserBytes`, the
  worker failed to load or died on the document, or (with `whileLoading: "server"`) the
  worker is still loading. Every result says where it ran and why. Without a `server`,
  nothing is ever uploaded.
- `@pydiggs/client/node`: `PydiggsNode` runs pyDIGGS in a Node worker thread on the
  npm `pyodide` package (an optional peer dependency, pinned to `PYODIDE_VERSION`), with
  `packageCacheDir`, `pydiggsWheelPath` and limits above 25 MiB up to
  `ABSOLUTE_MAX_BYTES` (256 MiB, from measured memory use). A document that runs past
  `timeoutMs` (default 60 s, as in pyDIGGS's server) stops its worker thread and fails
  with `code: "timeout"` (504 from the handler); a fresh worker takes the documents
  still waiting, and a worker that dies on a document is replaced the same way. `createPydiggsHandler` serves pyDIGGS's HTTP API (`/v1/validate`,
  `/v1/read`, `/v1/readyz`) from it as a standard Fetch handler, for Next.js route
  handlers, Hono, Bun, Deno and others.
- `PydiggsBrowser`: a time limit, `timeoutMs` (default 120 s). A document that runs past
  it, or kills the worker, fails alone (`code: "timeout"` or `"crashed"`) and a fresh
  worker takes the documents still waiting, as in `PydiggsNode`. `PydiggsHybrid` sends
  such a document to the server (`reason: "browser-timeout"` or `"browser-unavailable"`).
  Documents now go to the worker one at a time; the worker already ran them one at a
  time.
- `PydiggsNode` tells Pyodide where it is installed, so it loads under
  `node --enable-source-maps` and `next dev`, where Pyodide's own guess from a stack
  trace pointed at its original sources.
- Tested set-ups, with the packed package: Vite production builds in Chromium, Firefox
  and WebKit; the Vite dev server; Next.js with Turbopack and with webpack, and `next
  dev`, with `PydiggsNode` behind a route handler. The README has the configuration
  each needs.
- `PydiggsBrowser`: `onProgress` reports the load stages (`runtime`, `packages`,
  `pydiggs`, `warm-up`), and `status` says whether the worker is loading, ready or failed.

### Changed

- The findings types follow **diggs-findings 1.1** (pyDIGGS 1.9.0), which only adds to
  1.0: `DiggsFindingsStep` gains `"ags4"` and `"convert"`, a finding's `location` may
  have a `path` (the place in a source that is not XML), and a file entry may have a
  `format` (`"ags4"`; absent for DIGGS). A 1.0 document still fits the types.
  `FINDINGS_SCHEMA_URL` is the 1.1 schema, shipped as
  `@pydiggs/client/schemas/diggs-findings-1.1.schema.json`; the 1.0 schema file is still
  shipped, unchanged. Code that switches over every `DiggsFindingsStep` needs the two new
  cases.

## [0.1.1] - 2026-11-11

### Changed

- Released by the repository's release workflow from a signed tag, staged on npm and
  approved by a maintainer, where 0.1.0 was published by hand. The code is the same as
  0.1.0.

## [0.1.0] - 2026-11-11

### Added

- TypeScript types for diggs-findings 1.0 and diggs-model 1.0 documents, generated from
  pyDIGGS's JSON Schemas, with the schemas themselves under `@pydiggs/client/schemas/`.
- `@pydiggs/client/http`: a `fetch` client for the pyDIGGS HTTP API (`validate`,
  `validateSarif`, `read`, `versions`, `listRules`, `explainRule`, `ready`).
- `@pydiggs/client/browser`: pydiggs in a Web Worker on Pyodide (`validate`, `read`),
  with Pyodide and the pydiggs wheel loadable from your own origin.
