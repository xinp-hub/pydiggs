# History

## 1.4.1 (2026-11-05)

A documentation and maintenance release with no API changes:

- A rebuilt documentation site with a [Quickstart](quickstart.md), an
  [integration chooser](integrations.md) and a [Python API reference](reference.md).
- The container image moves to Python 3.14.10 and current FastAPI and Pydantic.

See the CHANGELOG for the full list.

## 1.4.0 (2026-11-04)

More ways to run pyDIGGS, all additive:

- An MCP server for AI assistants (the `mcp` extra, `pydiggs serve --mcp`); see
  [MCP server](mcp.md).
- Tested browser support with Pyodide, and an example that validates in a Web Worker
  without the file leaving the user's machine; see [Browser (Pyodide)](browser.md).
- A container image of the HTTP API on GHCR, with provenance, an SBOM and a build
  attestation; see [Container image](container.md).
- `pydiggs --version`, and a `constraints-X.Y.Z.txt` file on every GitHub release.

See the CHANGELOG for the full list.

## 1.3.0 (2026-11-03)

New ways to use pyDIGGS from other software, all additive:

- `validate_bytes()` for in-memory documents, a soft `time_budget`, `warm_up()`, and
  public helpers for findings documents, the findings schema, version information and
  the rule catalogue.
- An HTTP API (the `server` extra, `pydiggs serve --http`); see [HTTP API](http-api.md).
- A stdio JSON-RPC sidecar for desktop and native apps (`pydiggs serve --stdio`); see
  [Stdio sidecar](sidecar.md) and [Host integration](sidecar-hosts.md).

See the CHANGELOG for the full list.

## 1.2.0 (2026-10-23)

Machine-readable findings. Full details are in the
[changelog](https://github.com/xinp-hub/pydiggs/blob/master/CHANGELOG.md).

- **Upgrade note:** the bundled dictionaries are now exact copies of diggsml.org/def,
  so files using eight codes that only pyDIGGS's copies defined now fail dictionary
  check 5. 2.6 files that spell `samplingFeatiureRef` the old way now fail
  `schema_check()`.
- `pydiggs.validate(path)` runs every check on one file and returns a `Report` of
  findings with stable rule ids, lines, XPaths and `gml:id`s; see
  [Findings](findings.md).
- `pydiggs validate PATH...` writes text, diggs-findings 1.0 JSON or SARIF 2.1.0, with
  deterministic output and no snippets or absolute paths by default.
- Size and findings limits, and local dictionaries read only from the instance's
  folder.
- Reference data re-synced to DIGGSml/schemas `15ce278` and DIGGSml/def `2d76ba6`.

## 1.1.1 (2026-10-14)

First published release of 1.1: it contains everything listed under 1.1.0, which
was tagged but never reached PyPI.

## 1.1.0 (2026-10-14, not published)

Security release. Full details are in the
[changelog](https://github.com/xinp-hub/pydiggs/blob/master/CHANGELOG.md).

* **Upgrade note:** dictionary check 12 (invalid UOM) is an error again, so files that
  only warned under 1.0.0 now fail `dictionary_check()`.
* Every XML input is parsed with a hardened parser: external entities, external DTDs,
  network access and compressed files are refused. Requires `lxml>=6.1.3`.
* `allow_network=True` really downloads remote dictionaries, restricted to the
  whitelist, including redirects, with time and size limits.
* Relative `codeSpace` paths resolve next to the instance file first.
* New `SECURITY.md` with private vulnerability reporting.

## 1.0.0 (2026-08-07)

Stable release of the DIGGS 3.0 + FORGE packaging train first shipped as 1.0.0a1.

* Everything from 1.0.0a1, now marked stable.
* Documentation aligned with the public 1.0.0 API.

## 1.0.0a1 (2026-08-07)

Alpha release toward 1.0.0.

* Added DIGGS **3.0.0** bundled schemas (`schema-dev@3.0.0`) and namespace
  auto-detection (`detect_diggs_version`); unknown namespaces default to **3.0.0**.
* Added full official [diggs-examples](https://github.com/DIGGSml/diggs-examples) corpus
  under `tests/fixtures/official/` (2.5.a / 2.6 / 3.0 goldens + known_invalid pins).
* Migrated packaging to FORGE standards: `src/` layout, **uv** + hatchling (not Poetry),
  PEP 621/639/735; `requires-python` raised to **>=3.11**.
* PyPI publish uses Trusted Publishing (OIDC) on `v*` tags.
* DIGGS **2.5.a** auto-detect resolves bundled `Complete.xsd` (no `Diggs.xsd` in 2.5.a).
* Dictionary validation prefers modern `def/codes/.../properties.xml` when instances cite
  legacy URLs; accepts integer-family `typeData` aliases; maps common legacy fragments.
* Schematron `weightRetained` assert allows zero, matching official grading examples.
* **Note:** dictionary check 12 (UOM not in quantity class) is a WARNING so official
  diggs-examples with known unit/quantity mismatches still pass `dictionary_check()`.

## 0.2.0 (2026-08-07)

* DIGGS 2.x correctness release (pre–DIGGS 3.0).
* Synced DIGGS **2.5.a** XSD tree with schema-dev@2.5.a; retained 2.6 Geophysics element-name fix.
* Semantic `dictionary_check()` aligned with DIGGSml/validation (codeSpace steps, full codes/0.1 offline bundle, UOM checks).
* Bundled lxml-adapted Schematron + offline cross-uom casing diameter check.
* New `context_check()` for Diggs structure, geometry SRS attributes, in-document xlink:href, and dataBlock arity.
* CLI: `context_check`, `--dictionary_path`, default Schematron, `--output_log` / `--no-output_log`, nonzero exit on failure.
* Official diggs-examples curated fixtures under `tests/fixtures/official/`.
* Docs updated for current validation behavior (Python floor was 3.10 until 1.0.0a1).
* CI: mypy ignores missing `lxml` stubs so typecheck matches the validation codebase.

## 0.1.5 (2025-02-02)

* Added a new argument to allow users to specify whether to output the log files.
* Changed license from GNU General Public License v3 to Apache License 2.0.
* Updated the documentation.

## 0.1.4 (2025-01-17)

* Updated the default schema version to 2.6.
* Added new test files for schema V2.6 schema validation.
* Updated the default dictionary validation xml file to be consistent with the "DIGGS Measurement Properties" dictionary in "DIGGS Code Lists and Measurement Properties Dictionaries V0.1".
* Updated the pytest file for testing the updates above.
* Updated the minimum python version to 3.10 to support the latest versions of dependencies.
* Updated the dependencies with the latest versions.
* Updated Documentation.

## 0.1.3 (2023-03-19)

* Officially added the dictionary validation method.
* Switched CI/CD workflows from Travis CI to GitHub Actions.
* Updated the package publishing method to adopt Poetry.
* Updated Documentation.

## 0.1.2 (2021-06-30)

* Created a "validator" Class is  to incorporate all the validation-related methods.
* Added an argument to allow users specifying a specific version of the DIGGS XSD Schema for validation.
* Added a Schematron validation method.
* Updated Documentation.

## 0.1.0 (2021-06-14)

* First release on PyPI.
* Added DIGGS Schema validation features.
