# pyDIGGS

[![PyPI version](https://img.shields.io/pypi/v/pydiggs.svg)](https://pypi.python.org/pypi/pydiggs)
[![Python Versions](https://img.shields.io/pypi/pyversions/pydiggs)](https://pypi.org/project/pydiggs)
[![Downloads](https://static.pepy.tech/badge/pydiggs)](https://pepy.tech/project/pydiggs)
[![Downloads/Month](https://static.pepy.tech/badge/pydiggs/month)](https://pepy.tech/project/pydiggs)
[![Build Status](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml/badge.svg)](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml)
[![Documentation Status](https://github.com/xinp-hub/pydiggs/actions/workflows/docs.yml/badge.svg)](https://xinp-hub.github.io/pydiggs/)
[![Updates](https://pyup.io/repos/github/xinp-hub/pydiggs/shield.svg)](https://pyup.io/account/repos/github/xinp-hub/pydiggs/)
[![GitHub Issues](https://img.shields.io/github/issues/xinp-hub/pydiggs.svg)](https://github.com/xinp-hub/pydiggs/issues)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

* Licensed under the Apache License 2.0
* Documentation: [https://xinp-hub.github.io/pydiggs](https://xinp-hub.github.io/pydiggs)
* GitHub: [https://github.com/xinp-hub/pydiggs](https://github.com/xinp-hub/pydiggs)
* PyPI: [https://pypi.org/project/pydiggs](https://pypi.org/project/pydiggs)

## Project Lead

    * Xin Peng (<xin_peng@outlook.com>)
    * Asitha Senanayake (<asitha.senanayake@utexas.edu>)

## Features

* Validate DIGGS instance files against:
    * DIGGS XSD Schema (**2.5.a**, **2.6**, **3.0.0**) with namespace auto-detect
      (default profile **3.0.0** when the namespace is unknown)
    * DIGGS Standard dictionaries / codeSpace semantics (offline bundle from diggsml.org/def)
    * Schematron business rules (bundled DIGGS lxml-adapted rules by default)
    * Lightweight context checks (structure / SRS / xlink / dataBlock arity)
* Flexible validation output:
    * Write validation errors to log files
    * Print validation errors directly to console
* Command-line interface (CLI) for easy integration

## Web Application

Not a Python user? Looking for leveraging the power of pyDIGGS in a modern web application? Check out:

* [DXplorer](https://www.dx-plorer.com)

## Quick Start

Install pydiggs (requires Python 3.11+):
```bash
uv pip install pydiggs
# or: pip install pydiggs
```

Basic usage with Python:
```python
from pydiggs import detect_diggs_version, validator

# Profile auto-detected from the instance namespace (unknown → 3.0.0)
validation = validator("path/to/your/diggs_file.xml")

print(detect_diggs_version("path/to/your/diggs_file.xml"))  # "2.5.a", "2.6", or "3.0.0"

# Or pin a profile explicitly (Python API only)
validation = validator("path/to/your/diggs_file.xml", diggs_version="2.6")

# Schema validation
validation.schema_check()

# Dictionary validation (DIGGSml semantic checks; offline dictionaries)
validation.dictionary_check()

# Schematron validation (bundled DIGGS rules by default)
validation.schematron_check()

# Context checks
validation.context_check()

# Or supply a custom Schematron file
validation = validator("path/to/your/diggs_file.xml", schematron_path="path/to/schematron.sch")
validation.schematron_check()
```

Basic usage with CLI:
```bash
# Schema validation (auto-detects DIGGS 2.5.a / 2.6 / 3.0.0)
pydiggs schema_check "path/to/your/diggs_file.xml"

# Dictionary validation
pydiggs dictionary_check "path/to/your/diggs_file.xml"

# Schematron validation (bundled rules)
pydiggs schematron_check "path/to/your/diggs_file.xml"

# Context validation
pydiggs context_check "path/to/your/diggs_file.xml"

# Custom Schematron
pydiggs schematron_check "path/to/your/diggs_file.xml" --schematron_path "path/to/schematron.sch"
```

**1.0.0a1** is an alpha toward 1.0.0. See [usage](https://xinp-hub.github.io/pydiggs/usage/) for
`diggs_version` / `schema_path` overrides, log output options, and [known limits](https://xinp-hub.github.io/pydiggs/usage/#known-limits)
(dictionary check 12 as WARNING, bundled Schematron subset, and more).

For more detailed information and advanced usage, please see the [documentation](https://xinp-hub.github.io/pydiggs).
