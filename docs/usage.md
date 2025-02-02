# Usage

## DIGGS `validator`

The DIGGS validator provides three types of validation:
1. Schema validation against XSD files
2. Dictionary validation against standard property definitions
3. Schematron validation against business rules

For all validation types, you can control the error output:
```python
# By default, validation errors will be written to log files
validation = validator("DIGGS_Instance_File_Path", output_log=True)

# To print validation errors directly instead of writing to log files
validation = validator("DIGGS_Instance_File_Path", output_log=False)
```

### 1. Schema Validation

#### Using Python

```python
from pydiggs import validator

# Using default DIGGS Schema (version 2.6)
validation = validator("DIGGS_Instance_File_Path")
validation.schema_check()

# Using a specific DIGGS Schema version
validation = validator("DIGGS_Instance_File_Path", schema_path="DIGGS_Schema_File_Path")
validation.schema_check()

# Access schema validation results
print(validation.syntax_error_log)      # Contains any XML syntax errors
print(validation.schema_validation_log) # Contains schema validation errors
print(validation.schema_error_log)      # Contains schema parse errors
```

#### Using Command Line Interface
```bash
# Using default schema
pydiggs schema_check "DIGGS_Instance_File_Path"

# Using specific schema
pydiggs schema_check "DIGGS_Instance_File_Path" --schema_path "DIGGS_Schema_File_Path"
```

### 2. Dictionary Validation

#### Using Python

```python
from pydiggs import validator

# Using default DIGGS Dictionary
validation = validator("DIGGS_Instance_File_Path")
validation.dictionary_check()

# Using a specific dictionary file
validation = validator("DIGGS_Instance_File_Path", dictionary_path="DIGGS_Dictionary_File_Path")
validation.dictionary_check()

# Access dictionary validation results
print(validation.dictionary_validation_log) # Contains dictionary validation errors
```

#### Using Command Line Interface
```bash
# Using default dictionary
pydiggs dictionary_check "DIGGS_Instance_File_Path"

# Using specific dictionary
pydiggs dictionary_check "DIGGS_Instance_File_Path" --dictionary_path "DIGGS_Dictionary_File_Path"
```

### 3. Schematron Validation

#### Using Python

```python
from pydiggs import validator

# Schematron validation requires a schematron schema file
validation = validator("DIGGS_Instance_File_Path", schematron_path="DIGGS_Schematron_File_Path")
validation.schematron_check()

# Access schematron validation results
print(validation.schematron_validation_log) # Contains schematron validation errors
print(validation.schematron_error_log)      # Contains schematron parse errors
```

#### Using Command Line Interface
```bash
pydiggs schematron_check "DIGGS_Instance_File_Path" --schematron_path "DIGGS_Schematron_File_Path"
```