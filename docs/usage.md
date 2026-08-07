# Usage

## DIGGS `validator`

pyDIGGS validates DIGGS instance files offline against:

1. **XSD schema** — bundled DIGGS **2.5.a**, **2.6**, or **3.0.0**, selected by
   namespace auto-detect (`detect_diggs_version`). Unknown namespaces default to
   **3.0.0**. Override with `diggs_version=` or `schema_path=`.
2. **Dictionary / codeSpace semantics** — progressive checks aligned with
   [DIGGSml/validation](https://github.com/DIGGSml/validation) (steps 1–13), using
   bundled DIGGS Standard dictionaries from [diggsml.org/def](https://diggsml.org/def/)
3. **Schematron** — bundled lxml-adapted DIGGS rules by default (from DIGGSml/validation,
   with online Geosetta unit API replaced by offline DiggsUomDictionary conversion for
   casing diameters)
4. **Context** — lightweight ports of DIGGSml/validation structure, geometry SRS,
   in-document `xlink:href`, and dataBlock arity checks

All four methods return `True` on success (dictionary: no `ERROR` severity findings).

```python
from pydiggs import detect_diggs_version, validator

# Write logs to the CWD (default), or print to the console:
validation = validator("DIGGS_Instance_File_Path", output_log=True)
validation = validator("DIGGS_Instance_File_Path", output_log=False)

print(detect_diggs_version("DIGGS_Instance_File_Path"))  # "2.5.a", "2.6", or "3.0.0"
```

### 1. Schema Validation

#### Using Python

```python
from pydiggs import validator

# Auto-detect profile from instance namespace (unknown → 3.0.0)
validation = validator("DIGGS_Instance_File_Path", output_log=False)
assert validation.schema_check() is True
print(validation.diggs_version)

# Pin a profile explicitly
validation = validator("DIGGS_Instance_File_Path", diggs_version="2.5.a", output_log=False)
validation.schema_check()

# Or pass a custom XSD
validation = validator(
    "DIGGS_Instance_File_Path",
    schema_path="path/to/Diggs.xsd",
    output_log=False,
)
validation.schema_check()

print(validation.syntax_error_log)  # XML syntax errors
print(validation.schema_validation_log)  # XSD validation errors
print(validation.schema_error_log)  # schema parse errors
```

#### Using Command Line Interface

The CLI auto-detects the DIGGS profile from the instance namespace (unknown → 3.0.0).
There is no `--diggs_version` flag; pin a profile in Python with `diggs_version=`, or pass
`--schema_path` to a specific XSD.

```bash
pydiggs schema_check "DIGGS_Instance_File_Path"
pydiggs schema_check "DIGGS_Instance_File_Path" --schema_path "DIGGS_Schema_File_Path"
pydiggs schema_check "DIGGS_Instance_File_Path" --no-output_log
```

### 2. Dictionary Validation

Dictionary validation implements DIGGS codeSpace semantics:

- Preferred (wiki guidance): `codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml#liquid_limit"`
- Legacy: `codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml"` with element text equal to a `gml:id` or `gml:name`
- Offline resolution of bundled dictionaries under `pydiggs/dictionaries/codes/`
- Offline UOM membership checks via `DiggsUomDictionary.xml`

#### Using Python

```python
from pydiggs import validator

validation = validator("DIGGS_Instance_File_Path", output_log=False)
ok = validation.dictionary_check()
print(validation.dictionary_validation_log)  # INFO / WARNING / ERROR messages
```

#### Using Command Line Interface

```bash
pydiggs dictionary_check "DIGGS_Instance_File_Path"
pydiggs dictionary_check "DIGGS_Instance_File_Path" --dictionary_path "path/to/properties.xml"
```

### 3. Schematron Validation

By default, `schematron_check()` uses the bundled
`pydiggs/schematron/diggs_schematron_rules_2.6.sch` (ISO Schematron / `xslt1` for lxml).
You may still supply a custom `.sch`. Cross-unit casing diameter comparison uses the
bundled Diggs UOM dictionary (not the online Geosetta API).

#### Using Python

```python
from pydiggs import validator

# Bundled DIGGS Schematron rules
validation = validator("DIGGS_Instance_File_Path", output_log=False)
assert validation.schematron_check() is True

# Custom Schematron
validation = validator(
    "DIGGS_Instance_File_Path",
    schematron_path="path/to/rules.sch",
    output_log=False,
)
validation.schematron_check()

print(validation.schematron_validation_log)
print(validation.schematron_error_log)
```

#### Using Command Line Interface

```bash
pydiggs schematron_check "DIGGS_Instance_File_Path"
pydiggs schematron_check "DIGGS_Instance_File_Path" --schematron_path "path/to/rules.sch"
```

### 4. Context Validation

```python
validation = validator("DIGGS_Instance_File_Path", output_log=False)
validation.context_check()
print(validation.context_validation_log)
```

```bash
pydiggs context_check "DIGGS_Instance_File_Path" --no-output_log
```

### Known limits

- **1.0.0a1** is an alpha toward 1.0.0; APIs and bundled assets may change before 1.0.0.
- Official [diggs-examples](https://github.com/DIGGSml/diggs-examples) files under
  `tests/fixtures/official/known_invalid/` fail schema (or XML syntax) against the bundled
  XSDs; they are pinned so we never silently accept them.
- Dictionary check 12 (UOM/quantity mismatch) is reported as WARNING so those examples still
  pass `dictionary_check()` with a visible advisory.
- Legacy `http://diggsml.org/dictionaries/DIGGSTestPropertyDefinitions.xml#…` codeSpaces are
  resolved against the modern `def/codes/DIGGS/0.1/properties.xml` definitions when present.
- Upstream Schematron `queryBinding="xslt3"` rules that call remote APIs are not executed by
  lxml; pyDIGGS ships an adapted subset plus offline casing UOM checks.
- Full DIGGSml/validation XSLT modules for CRS / xlink / dataBlock structure remain out of
  scope for this release train; see that repository for the Saxon pipeline.
