=====
Usage
=====

DIGGS `Validator`
------------------

1. Using Jupyter Notebooks or py files
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To use `Validator` in a Python project::

    from pydiggs import Validator

Create a `Validator` object for the target DIGGS instance file::

    validation = Validator("DIGGS_Instance_File_Path")

Validate the DIGGS instance file against the default DIGGS XSD Schema::

    validation.schema_check()

Validate the DIGGS instance file against a specific version of the DIGGS XSD Schema::

    validation = Validator("DIGGS_Instance_File_Path", schema_path = "DIGGS_Schema_File_Path")
    validation.schema_check()

Print validation log::

    print(validation.schema_validation_log)

Validate against a Schematron Schema::

    validation = Validator("DIGGS_Instance_File_Path", schematron_path = "DIGGS_Schematron_File_Path")
    validation.schematron_check()

Validate against the standard XML Dictionary file::

    validation = Validator("DIGGS_Instance_File_Path")
    validation.dictionary_check()


1. Using Command Line Interface (CLI)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Validate a DIGGS instance File against the default DIGGS XSD Schema using `Command Line Interface(CLI)`::

    pydiggs schema_check "DIGGS_Instance_File_Path"

Validate a DIGGS instance File against a specific version of DIGGS XSD Schema using `Command Line Interface`::

    pydiggs schema_check "DIGGS_Instance_File_Path" --schema_path "DIGGS_Schema_File_Path"

Validate a DIGGS instance File against a Schematron Schema using `Command Line Interface`::

    pydiggs schematron_check "DIGGS_Instance_File_Path" --schematron_path "DIGGS_Schematron_File_Path"

Validate a DIGGS instance File against the standard Dictionary XML file using `Command Line Interface`::

    pydiggs dictionary_check "DIGGS_Instance_File_Path"
