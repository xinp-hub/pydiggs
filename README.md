# pyDIGGS

[![PyPI version](https://img.shields.io/pypi/v/pydiggs.svg)](https://pypi.python.org/pypi/pydiggs)
[![Python Versions](https://img.shields.io/pypi/pyversions/pydiggs)](https://pypi.org/project/pydiggs)
[![Downloads](https://static.pepy.tech/badge/pydiggs)](https://pepy.tech/project/pydiggs)
[![Downloads/Month](https://static.pepy.tech/badge/pydiggs/month)](https://pepy.tech/project/pydiggs)
[![Build Status](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml/badge.svg)](https://github.com/xinp-hub/pydiggs/actions/workflows/ci.yml)
[![Documentation Status](https://github.com/xinp-hub/pydiggs/actions/workflows/docs.yml/badge.svg)](https://xinp-hub.github.io/pydiggs/)
[![GitHub Issues](https://img.shields.io/github/issues/xinp-hub/pydiggs.svg)](https://github.com/xinp-hub/pydiggs/issues)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

* Licensed under the Apache License 2.0
* Documentation: [https://xinp-hub.github.io/pydiggs](https://xinp-hub.github.io/pydiggs)
* GitHub: [https://github.com/xinp-hub/pydiggs](https://github.com/xinp-hub/pydiggs)
* PyPI: [https://pypi.org/project/pydiggs](https://pypi.org/project/pydiggs)
* Release updates: **Watch → Custom → Releases** on this repository, the
  [releases feed](https://github.com/xinp-hub/pydiggs/releases.atom), or email
  (see [Stay updated](https://xinp-hub.github.io/pydiggs/stay-updated/))

## Authors

* **Project lead:** Xin Peng (<xin_peng@outlook.com>)
* **Contributor:** Asitha Senanayake (<asitha.senanayake@utexas.edu>)

## Features

* Validate DIGGS instance files against:
    * DIGGS XSD Schema (**2.5.a**, **2.6**, **3.0.0**) with namespace auto-detect
      (default profile **3.0.0** when the namespace is unknown)
    * DIGGS Standard dictionaries / codeSpace semantics (offline bundle from diggsml.org/def)
    * Schematron business rules (bundled DIGGS lxml-adapted rules by default)
    * Lightweight context checks (structure / SRS / xlink / dataBlock arity)
* Read what is in a file: `pydiggs.read()` returns boreholes, samples, tests and their
  result tables as Python objects and as JSON (diggs-model 1.0), the same shape for
  DIGGS 2.5.a, 2.6 and 3.x ([Reading files](https://xinp-hub.github.io/pydiggs/model/)).
  With the `frames`, `polars` and `gis` extras, `pydiggs.frames` gives pandas or
  polars data frames and `pydiggs.gis` gives GeoDataFrames, GeoPackage and
  GeoParquet files. The public modules are `pydiggs`, `pydiggs.model`,
  `pydiggs.crs`, `pydiggs.frames` and `pydiggs.gis`; every other module is internal.
* Flexible validation output:
    * Machine-readable findings with stable rule ids, lines, XPaths and `gml:id`s, as
      JSON (diggs-findings 1.0) or SARIF 2.1.0 (`pydiggs validate`, `validate()`)
    * Write validation errors to log files
    * Print validation errors directly to console
* Command-line interface (CLI) for easy integration
* Use it from any application:
    * Python: `validate()` for files, `validate_bytes()` for uploads and other
      in-memory data
    * Web apps: an HTTP API with OpenAPI 3.1 (the `server` extra, then
      `pydiggs serve --http`, or mount `pydiggs.server.create_router()` in FastAPI)
    * Containers: that HTTP API as a non-root, attested image for amd64 and arm64,
      `ghcr.io/xinp-hub/pydiggs:X.Y.Z` (no `latest` tag)
    * Desktop and native apps: a stdio JSON-RPC 2.0 sidecar (`pydiggs serve --stdio`)
      that any host language can drive
    * AI assistants: an MCP server with read-only validation and rule tools (the `mcp`
      extra, then `pydiggs serve --mcp`)
    * Browsers: in a web page with Pyodide, so the file never leaves the user's machine
      (a dependency-free Web Worker example is in `examples/browser-worker`)

## Security

pyDIGGS treats every DIGGS file as untrusted input. All XML is parsed with external
entities, DTD loading and network access refused, so a file cannot make pyDIGGS read
other local files or contact a server. There are two exceptions:

* A `codeSpace` that points at a local dictionary (`file:///`, `./`, `../`). The DIGGS
  whitelist permits these, and pyDIGGS parses them with the same hardened settings.
* With `DictionarySemanticValidator(allow_network=True)`, which is off by default,
  pyDIGGS downloads dictionaries, but only from whitelisted http/https URLs,
  re-checked on every redirect, with time and size limits.

Schemas and Schematron rules are different: they are code, not data. A custom
`schema_path` or `schematron_path` runs with your permissions (Schematron is compiled to
XSLT, which can read local files), so only pass files you trust. The bundled DIGGS
schemas and rules make no file or network access.

Log files are written to the working directory and are never written through a
symlink. Vulnerabilities: please report them privately (see [SECURITY.md](SECURITY.md)).

Install from the PyPI wheels, which bundle libxml2, rather than building lxml against
an older system libxml2.

## Web Application

Not a Python user? Looking for leveraging the power of pyDIGGS in a modern web application? Check out:

* [DXplorer](https://www.dx-plorer.com)

## Quick Start

Install pydiggs (requires Python 3.11+), then validate a file. The
[Quickstart](https://xinp-hub.github.io/pydiggs/quickstart/) walks through it:
```bash
uv tool install pydiggs   # or: python -m pip install pydiggs
pydiggs validate BH-01.xml
```

Basic usage with Python. `uv tool install` keeps pydiggs in its own environment, so to
import it, add it to your project with `uv add pydiggs` (or pip) first:
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

Every check at once, as machine-readable findings:
```bash
# JSON (diggs-findings 1.0); exit 0 = no errors, 1 = errors, 2 = usage/unreadable input
pydiggs validate --format json "path/to/your/diggs_file.xml"

# A whole folder as SARIF 2.1.0 for code-scanning tools
pydiggs validate --format sarif --output pydiggs.sarif "path/to/folder"
```
```python
from pydiggs import validate

report = validate("path/to/your/diggs_file.xml")  # prints nothing, writes no files
print(report.ok, report.counts)
for finding in report.findings:
    print(finding.rule_id, finding.severity, finding.line, finding.xpath)
```
See [findings](https://xinp-hub.github.io/pydiggs/findings/) for the format, rule ids,
exit codes and privacy defaults (no snippets, no absolute paths).

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

See [usage](https://xinp-hub.github.io/pydiggs/usage/) for `diggs_version` / `schema_path`
overrides, log output options, and [known limits](https://xinp-hub.github.io/pydiggs/usage/#known-limits)
(dictionary check 12 as WARNING, bundled Schematron subset, and more).

For more detailed information and advanced usage, please see the [documentation](https://xinp-hub.github.io/pydiggs).
