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

## Features

* Validate DIGGS instance files against:
    * DIGGS XSD Schema (**2.5.a**, **2.6**, **3.0.0**) with namespace auto-detect
      (default profile **3.0.0** when the namespace is unknown)
    * DIGGS Standard dictionaries / codeSpace semantics (offline)
    * Schematron business rules (bundled DIGGS lxml-adapted rules by default)
    * Lightweight context checks
* Flexible validation output (log files or console)
* Command-line interface (CLI)

## Web Application

Not a Python user? Looking for leveraging the power of pyDIGGS in a modern web application? Check out:

* [DXplorer](https://www.dx-plorer.com)

## Quick Start

Install pydiggs:
```bash
uv pip install pydiggs
# or: pip install pydiggs
```

Basic usage with Python:
```python
from pydiggs import detect_diggs_version, validator

validation = validator("path/to/your/diggs_file.xml")
print(detect_diggs_version("path/to/your/diggs_file.xml"))

validation.schema_check()
validation.dictionary_check()
validation.schematron_check()
validation.context_check()
```

Basic usage with CLI:
```bash
pydiggs schema_check "path/to/your/diggs_file.xml"
pydiggs dictionary_check "path/to/your/diggs_file.xml"
pydiggs schematron_check "path/to/your/diggs_file.xml"
pydiggs context_check "path/to/your/diggs_file.xml"
```

For more detailed information and advanced usage, please see the [documentation](https://xinp-hub.github.io/pydiggs).
