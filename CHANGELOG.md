# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-10-14

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

[Unreleased]: https://github.com/xinp-hub/pydiggs/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0a1...v1.0.0
[1.0.0a1]: https://github.com/xinp-hub/pydiggs/compare/v0.2.0...v1.0.0a1
[0.2.0]: https://github.com/xinp-hub/pydiggs/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/xinp-hub/pydiggs/releases/tag/v0.1.5
