# pyDIGGS

[![PyPI version](https://img.shields.io/pypi/v/pydiggs.svg)](https://pypi.python.org/pypi/pydiggs)
[![Python Versions](https://img.shields.io/pypi/pyversions/pydiggs)](https://pypi.org/project/pydiggs)
[![Downloads](https://static.pepy.tech/badge/pydiggs)](https://pepy.tech/project/pydiggs)
[![Downloads/Month](https://static.pepy.tech/badge/pydiggs/month)](https://pepy.tech/project/pydiggs)
[![Build Status](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml/badge.svg)](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml)
[![Documentation Status](https://github.com/xinp-hub/pydiggs/actions/workflows/docs.yml/badge.svg)](https://xinp-hub.github.io/pydiggs/)
[![Updates](https://pyup.io/repos/github/xinp-hub/pydiggs/shield.svg)](https://pyup.io/account/repos/github/xinp-hub/pydiggs/)

A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

* Licensed under the Apache License 2.0
* Documentation: [https://xinp-hub.github.io/pydiggs](https://xinp-hub.github.io/pydiggs)
* GitHub: [https://github.com/xinp-hub/pydiggs](https://github.com/xinp-hub/pydiggs)

## Features

* Validate DIGGS instance files against:
    * DIGGS XSD Schema (version 2.6 by default)
    * Standard XML Dictionary (v0.1/properties.xml by default)
    * Schematron rules for business logic validation
* Flexible validation output:
    * Write validation errors to log files
    * Print validation errors directly to console
* Command-line interface (CLI) for easy integration

## Quick Start

Install pydiggs:
```bash
pip install pydiggs
```

Basic usage with Python:
```python
from pydiggs import validator

# Create a validator instance
validation = validator("path/to/your/diggs_file.xml")

# Schema validation (using default DIGGS Schema v2.6)
validation.schema_check()

# Dictionary validation (using default DIGGS Dictionary)
validation.dictionary_check()

# Schematron validation (requires schematron file)
validation = validator("path/to/your/diggs_file.xml", schematron_path="path/to/schematron.sch")
validation.schematron_check()
```

Basic usage with CLI:
```bash
# Schema validation
pydiggs schema_check "path/to/your/diggs_file.xml"

# Dictionary validation
pydiggs dictionary_check "path/to/your/diggs_file.xml"

# Schematron validation
pydiggs schematron_check "path/to/your/diggs_file.xml" --schematron_path "path/to/schematron.sch"
```

For more detailed information and advanced usage, please see the [documentation](https://xinp-hub.github.io/pydiggs). 