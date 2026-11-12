# Changelog

All notable changes to `@pydiggs/client` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/). Its version is its own: what it works with
is stated by contract (diggs-findings 1.x, diggs-model 1.x, HTTP API `/v1`), not by a
matching pyDIGGS version number.

## [Unreleased]

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
