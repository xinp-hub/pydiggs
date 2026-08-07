# Usage

## DIGGS `validator`

pyDIGGS validates DIGGS instance files offline against:

1. **XSD schema** — bundled DIGGS **2.6** (`Diggs.xsd`) by default, or **2.5.a** via `schema_path`
2. **Dictionary / codeSpace semantics** — progressive checks aligned with [DIGGSml/validation](https://github.com/DIGGSml/validation) (steps 1–13), using bundled DIGGS Standard dictionaries from [diggsml.org/def](https://diggsml.org/def/)
3. **Schematron** — bundled lxml-adapted DIGGS rules by default (from DIGGSml/validation, with online Geosetta unit API replaced by offline DiggsUomDictionary conversion for casing diameters)

All three methods return `True` on success (dictionary: no `ERROR` severity findings).

```python
from pydiggs import validator

# Write logs to the CWD (default), or print to the console:
validation = validator("DIGGS_Instance_File_Path", output_log=True)
validation = validator("DIGGS_Instance_File_Path", output_log=False)
```

### 1. Schema Validation

#### Using Python

```python
from pydiggs import validator

# Default: embedded DIGGS Schema 2.6
validation = validator("DIGGS_Instance_File_Path", output_log=False)
assert validation.schema_check() is True

# Explicit schema (e.g. DIGGS 2.5.a Complete.xsd)
validation = validator(
    "DIGGS_Instance_File_Path",
    schema_path="path/to/diggs-schema-2.5.a/Complete.xsd",
    output_log=False,
)
validation.schema_check()

print(validation.syntax_error_log)       # XML syntax errors
print(validation.schema_validation_log)  # XSD validation errors
print(validation.schema_error_log)       # schema parse errors
```

#### Using Command Line Interface

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

Lightweight ports of DIGGSml/validation structure, geometry SRS, in-document
`xlink:href`, and dataBlock arity checks:

```python
validation = validator("DIGGS_Instance_File_Path", output_log=False)
validation.context_check()
print(validation.context_validation_log)
```

```bash
pydiggs context_check "DIGGS_Instance_File_Path" --no-output_log
```

### Known limits (DIGGS 2.x)

- Official [diggs-examples](https://github.com/DIGGSml/diggs-examples) files
  `FieldSurveyExample.xml` and `Temporal_Spatial_CRS_Example.xml` are known not to
  validate as Diggs XSD document roots (document with the DIGGS maintainers / omit from golden suites).
- Upstream Schematron `queryBinding="xslt3"` rules that call remote APIs are not executed by
  lxml; pyDIGGS ships an adapted subset plus offline casing UOM checks.
- Full DIGGSml/validation XSLT modules for CRS / xlink / dataBlock structure remain out of scope
  for this release train; see that repository for the Saxon pipeline.
