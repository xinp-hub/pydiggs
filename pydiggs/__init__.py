"""A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

This package provides tools for validating DIGGS instance files against:
1. XML Schema (XSD)
2. DIGGS Dictionary
3. Schematron Rules

For more information about DIGGS, visit: http://www.diggsml.org/
"""

from __future__ import annotations

from .detect import detect_diggs_version
from .pydiggs import validator

__author__ = """Xin Peng"""
__email__ = "xin_peng@outlook.com"
__version__ = "0.2.0"

__all__ = ["detect_diggs_version", "validator"]
