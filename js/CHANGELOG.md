# Changelog

All notable changes to `@pydiggs/client` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/). Its version is its own: what it works with
is stated by contract (diggs-findings 1.x, diggs-model 1.x, HTTP API `/v1`), not by a
matching pyDIGGS version number.

## [Unreleased]

## [0.1.0] - 2026-11-11

### Added

- TypeScript types for diggs-findings 1.0 and diggs-model 1.0 documents, generated from
  pyDIGGS's JSON Schemas, with the schemas themselves under `@pydiggs/client/schemas/`.
- `@pydiggs/client/http`: a `fetch` client for the pyDIGGS HTTP API (`validate`,
  `validateSarif`, `read`, `versions`, `listRules`, `explainRule`, `ready`).
- `@pydiggs/client/browser`: pydiggs in a Web Worker on Pyodide (`validate`, `read`),
  with Pyodide and the pydiggs wheel loadable from your own origin.
