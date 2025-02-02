# History

## 0.1.0 (2021-06-14)

* First release on PyPI.
* Added DIGGS Schema validation features.

## 0.1.2 (2021-06-30)

* Created a "validator" Class is  to incorporate all the validation-related methods.
* Added an argument to allow users specifying a specific version of the DIGGS XSD Schema for validation.
* Added a Schematron validation method.
* Updated Documentation.

## 0.1.3 (2023-03-19)

* Officially added the dictionary validation method.
* Switched CI/CD workflows from Travis CI to GitHub Actions.
* Updated the package publishing method to adopt Poetry.
* Updated Documentation.

## 0.1.4 (2025-01-17)

* Updated the default schema version to 2.6.
* Added new test files for schema V2.6 schema validation.
* Updated the default dictionary validation xml file to be consistent with the "DIGGS Measurement Properties" dictionary in "DIGGS Code Lists and Measurement Properties Dictionaries V0.1". (Note: if you want to validate against other dictionaries in "DIGGS Code Lists and Measurement Properties Dictionaries V0.1", you can download the dictionary file from the DIGGS website and specify the dictionary path in the validator class.)
* Updated the pytest file for testing the updates above.
* Updated the minimum python version to 3.10 to support the latest versions of dependencies.
* Updated the dependencies with the latest versions.
* Updated Documentation.

## 0.1.5 (2025-02-02)

* Added a new argument to allow users to specify whether to output the log files.
* Updated the documentation.
