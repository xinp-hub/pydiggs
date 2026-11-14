# Changelog

All notable changes to `@pydiggs/client` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/). Its version is its own: what it works with
is stated by contract (diggs-findings 1.x, diggs-model 1.x, HTTP API `/v1`), not by a
matching pyDIGGS version number.

## [Unreleased]

### Changed

- The `createPydiggsHandler` problem for `invalid-document` is titled "Invalid document",
  as pyDIGGS 1.10's HTTP API titles it now that `/v1/convert` also answers it for AGS4
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
