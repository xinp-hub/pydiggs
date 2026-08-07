# History

## Unreleased

No user-facing changes yet.

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
