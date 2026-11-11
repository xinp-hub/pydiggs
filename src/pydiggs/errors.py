"""Exceptions raised by :func:`pydiggs.validate`.

Problems *in* an instance (not well-formed, schema-invalid, unknown codes) are never
exceptions: they are :class:`~pydiggs.Finding` objects in the returned report. An
exception means the check could not be run at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydiggs.findings.model import Finding

__all__ = ["DocumentError", "InstanceReadError", "PydiggsError", "ResourceError"]


class PydiggsError(Exception):
    """Base class for every exception pydiggs raises on purpose.

    Examples:
        >>> issubclass(InstanceReadError, PydiggsError)
        True
    """


class InstanceReadError(PydiggsError, OSError):
    """The instance file could not be read (missing, unreadable, too large, compressed).

    It is also an :class:`OSError`, so ``except OSError`` keeps working.

    Examples:
        >>> from pydiggs import validate
        >>> try:
        ...     validate("no-such-file.xml")
        ... except InstanceReadError as exc:
        ...     print(type(exc).__name__, isinstance(exc, OSError))
        InstanceReadError True
    """


class ResourceError(PydiggsError):
    """A trusted input (a schema, dictionary or Schematron file) is missing or broken.

    Raised for files given by path and for pydiggs's own bundled reference data.

    Examples:
        >>> from pydiggs import validate
        >>> try:
        ...     validate("tests/test_files/2.6/No_Error_2_6.xml", schema_path="missing.xsd")
        ... except ResourceError as exc:
        ...     print("schema" in str(exc))
        True
    """


class DocumentError(PydiggsError, ValueError):
    """The bytes are not a readable DIGGS document (raised by the read API only).

    ``kind`` says why: ``malformed`` (not well-formed XML), ``not-diggs`` (the root is
    not a DIGGS ``Diggs`` element), ``unsafe-entity`` (the internal DTD declares an
    entity whose value contains markup) or ``namespace-too-long`` (a namespace URI of
    more than 1,024 characters). The set may grow in a minor release. ``finding`` is the
    same ``structure.*`` finding :func:`pydiggs.validate` reports for that file. It is
    also a :class:`ValueError`.
    """

    def __init__(self, kind: str, message: str, finding: Finding | None = None) -> None:
        """Record ``kind`` and the matching ``finding``."""
        super().__init__(message)
        self.kind = kind
        self.finding = finding
