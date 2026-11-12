# Changelog

All notable changes to `@pydiggs/client` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/). Its version is its own: what it works with
is stated by contract (diggs-findings 1.x, diggs-model 1.x, HTTP API `/v1`), not by a
matching pyDIGGS version number.

## [Unreleased]

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
