# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  towards the same budget. Check 13 has one budget of its own (200 million units):
  every character it reads and every element, Property, reference, token, field and
  property-and-ResultSet pair it examines is charged, so no Check 13 loop is unbounded.
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
- The DIGGS deprecation registry (`deprecation/DeprecationRegistry.xml`) is bundled for
  a forthcoming deprecation check. It lists 94 deprecated items (25 from DIGGS 3.0.0, 69
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

[Unreleased]: https://github.com/xinp-hub/pydiggs/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/xinp-hub/pydiggs/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/xinp-hub/pydiggs/compare/v1.0.0a1...v1.0.0
[1.0.0a1]: https://github.com/xinp-hub/pydiggs/compare/v0.2.0...v1.0.0a1
[0.2.0]: https://github.com/xinp-hub/pydiggs/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/xinp-hub/pydiggs/releases/tag/v0.1.5
