"""A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

This package provides tools for validating DIGGS instance files against:
1. XML Schema (XSD)
2. DIGGS Dictionary
3. Schematron Rules

For more information about DIGGS, visit: http://www.diggsml.org/
"""

from __future__ import annotations

from ._versions import VersionInfo, version_info
from .detect import detect_diggs_version
from .errors import InstanceReadError, PydiggsError, ResourceError
from .findings.export import (
    FINDINGS_SCHEMA_URL,
    FINDINGS_SCHEMA_VERSION,
    findings_document,
    findings_json,
    findings_schema,
)
from .findings.model import Finding, Report, Severity, Step
from .findings.rules import RuleInfo, explain_rule, rule_catalogue
from .findings.run import validate, validate_bytes, warm_up
from .pydiggs import validator

__author__ = """Xin Peng"""
__email__ = "xin_peng@outlook.com"
__version__ = "1.4.1"

# The adapters (pydiggs.server, pydiggs.sidecar, pydiggs.mcp_server) are never imported
# here: ``import pydiggs`` stays free of their dependencies (and of rich).
__all__ = [
    "FINDINGS_SCHEMA_URL",
    "FINDINGS_SCHEMA_VERSION",
    "Finding",
    "InstanceReadError",
    "PydiggsError",
    "Report",
    "ResourceError",
    "RuleInfo",
    "Severity",
    "Step",
    "VersionInfo",
    "detect_diggs_version",
    "explain_rule",
    "findings_document",
    "findings_json",
    "findings_schema",
    "rule_catalogue",
    "validate",
    "validate_bytes",
    "validator",
    "version_info",
    "warm_up",
]
