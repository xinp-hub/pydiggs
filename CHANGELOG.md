# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Correct `t50` UOM on three vendored DIGGS 2.6 pore-pressure fixtures (`kPa` → `s`)
  so they match the 3.0 example ([#220](https://github.com/xinp-hub/pydiggs/issues/220);
  upstream [DIGGSml/diggs-examples#4](https://github.com/DIGGSml/diggs-examples/issues/4)).
- Dictionary check 12 treats DIGGS 2.6 AllUnits spelling `cm2/m` as `cm2/min` (Kernel.xsd
  documents it as square centimeter per minute).
- Release workflow skips republish when the tag version is already on PyPI (avoids
  red `pypi` Environment deployments after intentional tag retargets).

### Added
- `pydiggs.dictionaries.supplements` inventory of MWD/Pile codes required by 3.0 goldens
  and tracked upstream ([#221](https://github.com/xinp-hub/pydiggs/issues/221);
  [DIGGSml/def#13](https://github.com/DIGGSml/def/issues/13) /
  [#14](https://github.com/DIGGSml/def/issues/14)).
- FORGE-aligned **Release** workflow (`publish.yml`): tag/CHANGELOG guard, artifact handoff,
  TestPyPI + smoke install, PyPI attestations, Sigstore signing, GitHub Release creation.
- `security.yml` (pip-audit, dependency-review, CodeQL, gitleaks) and Dependabot for `uv` +
  GitHub Actions (weekly, 7-day cooldown).

### Changed
- Dictionary check 12 (UOM not in quantity class) is an **ERROR** again now that the
  official corpus goldens no longer require the advisory workaround. Instances that used
  invalid UOMs and previously only warned will now fail `dictionary_check()`.
- CI/docs/security/Release Actions refreshed to current majors with commit SHA pins
  (`checkout` v7, `setup-uv` v9, artifact upload/download v7/v8, CodeQL v4,
  dependency-review v5, `actions-gh-pages` v4, Sigstore action v3.5).
- CI jobs gain `timeout-minutes`; the `all green` gate reads `needs` via `env` (safer than
  interpolating into `run:`).
- Docs known limits link dictionary follow-ups to issues
  [#220](https://github.com/xinp-hub/pydiggs/issues/220) and
  [#221](https://github.com/xinp-hub/pydiggs/issues/221).

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

[Unreleased]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0a1...v1.0.0
[1.0.0a1]: https://github.com/xinp-hub/pydiggs/compare/v0.2.0...v1.0.0a1
[0.2.0]: https://github.com/xinp-hub/pydiggs/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/xinp-hub/pydiggs/releases/tag/v0.1.5
