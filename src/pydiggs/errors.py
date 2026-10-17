"""Exceptions raised by :func:`pydiggs.validate`.

Problems *in* an instance (not well-formed, schema-invalid, unknown codes) are never
exceptions: they are :class:`~pydiggs.Finding` objects in the returned report. An
exception means the check could not be run at all.
"""

from __future__ import annotations

__all__ = ["InstanceReadError", "PydiggsError", "ResourceError"]


class PydiggsError(Exception):
    """Base class for every exception pydiggs raises on purpose.

    Example:
        >>> issubclass(InstanceReadError, PydiggsError)
        True
    """


class InstanceReadError(PydiggsError, OSError):
    """The instance file could not be read (missing, unreadable, too large, compressed).

    It is also an :class:`OSError`, so ``except OSError`` keeps working.

    Example:
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

    Example:
        >>> from pydiggs import validate
        >>> try:
        ...     validate("tests/test_files/2.6/No_Error_2_6.xml", schema_path="missing.xsd")
        ... except ResourceError as exc:
        ...     print("schema" in str(exc))
        True
    """
