# pyDIGGS

[![PyPI version](https://img.shields.io/pypi/v/pydiggs.svg)](https://pypi.python.org/pypi/pydiggs)
[![Python Versions](https://img.shields.io/pypi/pyversions/pydiggs)](https://pypi.org/project/pydiggs)
[![Downloads](https://static.pepy.tech/badge/pydiggs)](https://pepy.tech/project/pydiggs)
[![Downloads/Month](https://static.pepy.tech/badge/pydiggs/month)](https://pepy.tech/project/pydiggs)
[![Build Status](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml/badge.svg)](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml)
[![Documentation Status](https://github.com/xinp-hub/pydiggs/actions/workflows/docs.yml/badge.svg)](https://xinp-hub.github.io/pydiggs/)
[![Updates](https://pyup.io/repos/github/xinp-hub/pydiggs/shield.svg)](https://pyup.io/account/repos/github/xinp-hub/pydiggs/)

A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

* Free software: GNU General Public License v3
* Documentation: [https://xinp-hub.github.io/pydiggs](https://xinp-hub.github.io/pydiggs)
* GitHub: [https://github.com/xinp-hub/pydiggs](https://github.com/xinp-hub/pydiggs)

## Features

* Validate DIGGS instance files against DIGGS XSD, Schematron schemas, and XML Dictionaries.

## Quick Start

Install pydiggs:
```bash
pip install pydiggs
```

Basic usage:
```python
from pydiggs import validator

# Create a validator instance
v = validator.Validator()

# Validate a DIGGS XML file
v.validate_schema("path/to/your/diggs_file.xml")
```

For more information, please see the [documentation](https://xinp-hub.github.io/pydiggs). 