=====
Usage
=====

DIGGS `validator`
--------------

To use `validator` in a Python project::

    from pydiggs import validator

Create a `validator` object for the target DIGGS instance file::

    validation = validator("DIGGS_Instance_File_Path")

Validate the DIGGS instance file against the default DIGGS XSD Schema::

    validation.schema_check()

Validate the DIGGS instance file against a specific version of the DIGGS XSD Schema::

    validation = validator("DIGGS_Instance_File_Path", schema_path = "DIGGS_Schema_File_Path")
    validation.schema_check()

Print validation log::

    print(validation.schema_validation_log)

Validate against a Schematron Schema::

    validation = validator("DIGGS_Instance_File_Path", schematron_path = "DIGGS_Schematron_File_Path")
    validation.schematron_check()

Validate a DIGGS instance File against the default DIGGS XSD Schema using `Command Line Interface`::

    pydiggs schema_check "DIGGS_Instance_File_Path"

Validate a DIGGS instance File against a specific version of DIGGS XSD Schema using `Command Line Interface`::

    pydiggs schema_check "DIGGS_Instance_File_Path" --schema_path "DIGGS_Schema_File_Path"

Validate a DIGGS instance File against a Schematron Schema using `Command Line Interface`::

    pydiggs schematron_check "DIGGS_Instance_File_Path" --schematron_path "DIGGS_Schematron_File_Path"