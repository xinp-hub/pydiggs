# Validation checks

pyDIGGS validates DIGGS instance files offline against:

1. **XSD schema**: the bundled DIGGS **2.5.a**, **2.6** or **3.0.0** schema, chosen from
   the file's namespace (`detect_diggs_version`). Unknown namespaces use **3.0.0**.
   The unreleased DIGGS 3.1 draft is available as the
   [experimental `3.1-dev` profile](#experimental-diggs-31-dev), only when asked for.
2. **Dictionary and codeSpace semantics**: progressive checks aligned with
   [DIGGSml/validation](https://github.com/DIGGSml/validation) (steps 1–13), against
   the bundled DIGGS Standard dictionaries from [diggsml.org/def](https://diggsml.org/def/).
3. **Schematron**: the bundled DIGGS business rules, adapted for lxml. The online
   Geosetta unit API is replaced by an offline conversion for casing diameters.
4. **Context**: document structure, geometry SRS attributes, in-document `xlink:href`
   targets and dataBlock arity.

## Experimental: DIGGS 3.1-dev

pyDIGGS also bundles the **unreleased** DIGGS 3.1 draft schema, from
[DIGGSml/schema-dev](https://github.com/DIGGSml/schema-dev) branch `3.1-dev`, pinned to
one commit (see `PROVENANCE.toml`). Use it to check files written against the draft,
such as the DIGGS File Inspector's test files:

=== "Python"

    ```python
    import pydiggs

    report = pydiggs.validate("SF_Project_Lith_Test.xml", diggs_version="3.1-dev")
    ```

=== "Command line"

    ```bash
    pydiggs validate SF_Project_Lith_Test.xml --diggs-version 3.1-dev
    ```

The profile is experimental:

* It is used **only when you name it**. The 3.1 draft uses the same namespace as 3.0, so
  a file is never detected as 3.1-dev, and the default stays 3.0.0.
* When the schema step checks a file against it, the report carries a
  `schema.unreleased-profile` warning, so a pass reads `valid-with-warnings`: passing a
  draft schema is not conformance to a released DIGGS version.
* The draft changes upstream. pyDIGGS updates its pinned copy in minor releases, so the
  same file can get different findings after an upgrade. When DIGGS 3.1 is released,
  it becomes a normal, detected profile and `3.1-dev` is deprecated.
* Dictionary, Schematron and context checks are the same as for 3.0.0; only the schema
  (and which deprecations apply) differ.

## Two ways to run them

!!! tip "Recommended: every check at once, as findings"
    `pydiggs validate` (command line) and `pydiggs.validate()` (Python) run every check
    and return [findings](findings.md) with stable rule ids, lines and XPaths. The
    command prints the verdict and findings to standard output (or to `--output`);
    the Python function prints nothing. Neither writes log files. Start with the
    [Quickstart](quickstart.md).

The `validator` class and the `schema_check`, `dictionary_check`, `schematron_check`
and `context_check` commands run **one check at a time** and print their results or
write log files. They are kept for existing scripts and are shown on the rest of this
page.

=== "Python"

    ```python
    import pydiggs

    report = pydiggs.validate("BH-01.xml", checks=["schema", "dictionary"])
    print(report.ok, report.counts)
    ```

=== "Command line"

    ```bash
    pydiggs validate --checks schema,dictionary BH-01.xml
    ```

## Pin a DIGGS version

Detection follows the file's namespace. To validate against a specific version instead:

=== "Python"

    ```python
    import pydiggs

    print(pydiggs.detect_diggs_version("BH-01.xml"))  # "2.5.a", "2.6" or "3.0.0"
    report = pydiggs.validate("BH-01.xml", diggs_version="2.6")
    ```

=== "Command line"

    ```bash
    pydiggs validate --diggs-version 2.6 BH-01.xml
    ```

## One check at a time

All four `validator` methods return `True` on success (for the dictionary check: no
`ERROR` findings). Logs go to the working directory by default; pass
`output_log=False` (Python) or `--no-output_log` (command line) to print to the
console instead.

### 1. Schema

=== "Python"

    ```python
    from pydiggs import validator

    # Profile detected from the namespace (unknown → 3.0.0)
    validation = validator("BH-01.xml", output_log=False)
    assert validation.schema_check() is True
    print(validation.diggs_version)

    # Pin a profile, or pass your own XSD
    validator("BH-01.xml", diggs_version="2.5.a", output_log=False).schema_check()
    validator("BH-01.xml", schema_path="path/to/Diggs.xsd", output_log=False).schema_check()

    print(validation.syntax_error_log)  # XML syntax errors
    print(validation.schema_validation_log)  # XSD validation errors
    print(validation.schema_error_log)  # schema parse errors
    ```

=== "Command line"

    ```bash
    pydiggs schema_check BH-01.xml
    pydiggs schema_check BH-01.xml --schema_path path/to/Diggs.xsd
    pydiggs schema_check BH-01.xml --no-output_log
    ```

    The legacy commands have no version flag: they always detect the version. Use
    `pydiggs validate --diggs-version` or `--schema_path` to choose.

### 2. Dictionary

The dictionary check implements DIGGS codeSpace semantics:

- Preferred: `codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#liquid_limit"`
- Legacy: `codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"` with the
  element text equal to a `gml:id` or `gml:name`
- Bundled dictionaries under `pydiggs/dictionaries/codes/` are resolved offline
- Units are checked offline against `DiggsUomDictionary.xml`

=== "Python"

    ```python
    from pydiggs import validator

    validation = validator("BH-01.xml", output_log=False)
    ok = validation.dictionary_check()
    print(validation.dictionary_validation_log)  # INFO / WARNING / ERROR messages
    ```

=== "Command line"

    ```bash
    pydiggs dictionary_check BH-01.xml
    pydiggs dictionary_check BH-01.xml --dictionary_path path/to/properties.xml
    ```

### 3. Schematron

By default the check uses the bundled
`pydiggs/schematron/diggs_schematron_rules_2.6.sch` (ISO Schematron, `xslt1` for lxml).
Cross-unit casing diameter comparison uses the bundled DIGGS units dictionary.

!!! warning "Only use Schematron and XSD files you trust"
    A custom schema or Schematron file is code, not data: Schematron compiles to XSLT,
    which can read local files. The bundled rules make no file or network access.

=== "Python"

    ```python
    from pydiggs import validator

    validation = validator("BH-01.xml", output_log=False)
    assert validation.schematron_check() is True

    # Your own rules
    validator("BH-01.xml", schematron_path="path/to/rules.sch", output_log=False).schematron_check()

    print(validation.schematron_validation_log)
    print(validation.schematron_error_log)
    ```

=== "Command line"

    ```bash
    pydiggs schematron_check BH-01.xml
    pydiggs schematron_check BH-01.xml --schematron_path path/to/rules.sch
    ```

### 4. Context

=== "Python"

    ```python
    from pydiggs import validator

    validation = validator("BH-01.xml", output_log=False)
    validation.context_check()
    print(validation.context_validation_log)
    ```

=== "Command line"

    ```bash
    pydiggs context_check BH-01.xml --no-output_log
    ```

## Known limits

- The bundled schemas, dictionaries and deprecation registry are copies of
  [DIGGSml/schemas](https://github.com/DIGGSml/schemas) and
  [DIGGSml/def](https://github.com/DIGGSml/def) at the commits recorded in
  `PROVENANCE.toml` inside the installed `pydiggs` package, with a sha256 for every
  file. A file whose codes appear in a newer upstream dictionary is validated against
  the bundled copy until pyDIGGS re-syncs. To use a newer property dictionary, download
  it and pass `dictionary_path=` (command line `--dictionary-path`).
  `allow_network=True` (`--allow-network`) only fetches whitelisted dictionaries that
  are not bundled; it never replaces a bundled one.
- Official [diggs-examples](https://github.com/DIGGSml/diggs-examples) files under
  `tests/fixtures/official/known_invalid/` fail schema validation against the bundled
  XSDs; they are pinned so we never silently accept them. `PileDrivingExample.xml` is
  among them: upstream rewrote it with DIGGSml/schema-dev types while it still declares
  3.0.0. `3.0/PorePressureDissipation_Example.xml` uses eight codes the official
  dictionaries do not define, and fails dictionary check 5.
- Dictionary check 12 (UOM/quantity mismatch) is an **ERROR**. Three 2.6 pore-pressure
  fixtures correct `t50` to `s`; `coef_consolidation_horiz` keeps the 2.6 AllUnits
  spelling `cm2/m`, which check 12 aliases to `cm2/min`. Upstream:
  [DIGGSml/diggs-examples#4](https://github.com/DIGGSml/diggs-examples/issues/4);
  see `tests/fixtures/official/PROVENANCE.txt`.
- DIGGS 3.x MWD and pile-driving property codes live in the specialised dictionaries
  `mwd_properties.xml` and `pil_properties.xml`, now published on diggsml.org/def and
  bundled from there. The golden code inventory is `pydiggs.dictionaries.supplements`.
- Legacy `http://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml#…`
  codeSpaces are resolved against the modern `def/codes/DIGGS/0.1/properties.xml`
  definitions when present.
- Upstream Schematron `queryBinding="xslt3"` rules that call remote APIs are not
  executed by lxml; pyDIGGS ships an adapted subset plus offline casing unit checks.
- The full DIGGSml/validation XSLT modules for CRS, xlink and dataBlock structure
  remain out of scope; see that repository for the Saxon pipeline.
