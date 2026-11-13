"""What this pydiggs is: package version, findings schema, DIGGS versions, reference data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

__all__ = ["VersionInfo", "version_info"]


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionInfo:
    """The versions a pydiggs install reports, for an ``/versions`` endpoint or a log line.

    ``diggs_versions`` are the DIGGS versions with a bundled schema, oldest first.
    ``reference_data`` maps each vendored reference-data source (schemas, dictionaries,
    Schematron) to the upstream commit it was taken from.

    Examples:
        >>> from pydiggs import VersionInfo
        >>> info = VersionInfo(
        ...     pydiggs_version="1.3.0",
        ...     findings_schema_version="1.0",
        ...     findings_schema_url="https://example.org/s.json",
        ...     diggs_versions=("2.6",),
        ...     reference_data={"schemas": "abc1234"},
        ... )
        >>> info.to_dict()["diggs_versions"]
        ['2.6']
    """

    pydiggs_version: str
    findings_schema_version: str
    findings_schema_url: str
    diggs_versions: tuple[str, ...]
    reference_data: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diggs_versions", tuple(self.diggs_versions))
        frozen = MappingProxyType(dict(sorted(self.reference_data.items())))
        object.__setattr__(self, "reference_data", frozen)

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickle and deep-copy through a plain dict; a mappingproxy cannot be pickled."""
        return (_rebuild, (self.to_dict(),))

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable dict, in field order."""
        return {
            "pydiggs_version": self.pydiggs_version,
            "findings_schema_version": self.findings_schema_version,
            "findings_schema_url": self.findings_schema_url,
            "diggs_versions": list(self.diggs_versions),
            "reference_data": dict(self.reference_data),
        }


def _rebuild(fields: dict[str, Any]) -> VersionInfo:
    """Inverse of :meth:`VersionInfo.to_dict`, for pickling."""
    return VersionInfo(**fields)


def version_info() -> VersionInfo:
    """Describe the running pydiggs: its version, findings schema and bundled data.

    Returns:
        A :class:`VersionInfo`; ``version_info().to_dict()`` is JSON-ready.

    Raises:
        ResourceError: The bundled ``PROVENANCE.toml`` is missing or broken.

    Examples:
        >>> import pydiggs
        >>> info = pydiggs.version_info()
        >>> info.pydiggs_version == pydiggs.__version__, info.diggs_versions
        (True, ('2.5.a', '2.6', '3.0.0', '3.1-dev'))
        >>> info.findings_schema_version
        '1.1'
    """
    import pydiggs  # noqa: PLC0415  # the package imports this module
    from pydiggs.detect import SCHEMA_PATHS  # noqa: PLC0415
    from pydiggs.findings.export import (  # noqa: PLC0415
        FINDINGS_SCHEMA_URL,
        FINDINGS_SCHEMA_VERSION,
    )
    from pydiggs.findings.run import reference_data  # noqa: PLC0415

    return VersionInfo(
        pydiggs_version=pydiggs.__version__,
        findings_schema_version=FINDINGS_SCHEMA_VERSION,
        findings_schema_url=FINDINGS_SCHEMA_URL,
        diggs_versions=tuple(SCHEMA_PATHS),  # defined oldest first
        reference_data=reference_data(),
    )
