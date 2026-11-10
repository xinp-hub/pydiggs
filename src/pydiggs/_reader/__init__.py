"""The diggs-model reader: ``read`` and ``read_bytes`` (public as :func:`pydiggs.read`).

``aliases.toml`` holds the per-version element renames the reader applies.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import BinaryIO, Literal

from pydiggs import _xml
from pydiggs._reader._read import read_document
from pydiggs.errors import InstanceReadError
from pydiggs.findings.run import (
    DEFAULT_MAX_BYTES,
    _check_version,
    _input_name,
    _limits,
    _payload,
    _reason,
)
from pydiggs.model import Model

__all__ = ["read", "read_bytes"]


def _check(
    diggs_version: str | None, max_bytes: int, time_budget: float | None, provenance: str
) -> int:
    _check_version(diggs_version)
    limit = _limits(None, max_bytes)
    if time_budget is not None and (
        isinstance(time_budget, bool)
        or not isinstance(time_budget, int | float)
        or not math.isfinite(time_budget)
        or time_budget <= 0
    ):
        raise ValueError(f"time_budget must be a positive number or None, got {time_budget!r}")
    if provenance not in ("object", "field"):
        raise ValueError(f'provenance must be "object" or "field", got {provenance!r}')
    return limit


def read(
    path: str | os.PathLike[str],
    *,
    diggs_version: str | None = None,
    results: bool = True,
    provenance: Literal["object", "field"] = "object",
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Model:
    """Read a DIGGS file into a :class:`~pydiggs.model.Model`.

    Reading is lenient: a schema-invalid file still reads, and nothing about the model
    says whether the file is valid (use :func:`pydiggs.validate` for that). What reading
    had to work around is in ``model.notes``. Nothing is fetched from the network.

    Args:
        path: The file. It must be a regular file of at most ``max_bytes`` bytes and
            not gzip-compressed.
        diggs_version: Read as this DIGGS version instead of the detected one (the
            experimental ``3.1-dev`` is used only when named here).
        results: False keeps every result table's columns and ``row_count`` but no
            values: an inventory of a large file.
        provenance: ``"object"`` (the default) gives every node its ``xpath`` and
            line; ``"field"`` also records, in ``provenance.fields``, where each value
            the node read came from (larger JSON).
        max_bytes: Largest file accepted (default 100 MiB, as for ``validate``).
        time_budget: Seconds to spend; when they run out the members read so far are
            returned with ``complete=False`` and a ``structure.timeout`` note.

    Raises:
        InstanceReadError: The file is missing, unreadable, too large, compressed or
            not a regular file.
        DocumentError: The file is not a readable DIGGS document (``kind``:
            ``malformed``, ``not-diggs`` or ``unsafe-entity``).
        ValueError: An argument is out of range.

    Examples:
        >>> from pydiggs import read
        >>> m = read("tests/fixtures/official/3.0/Borehole-CPT_Example_Annotated.xml")
        >>> m.source.diggs_version, len(m.features) > 0
        ('3.0.0', True)
    """
    limit = _check(diggs_version, max_bytes, time_budget, provenance)
    shown = os.fspath(path)
    try:
        data, _ = _xml.read_limited(Path(path), limit, regular_only=True)
    except OSError as exc:
        raise InstanceReadError(exc.errno or 0, _reason(exc), shown) from exc
    return read_document(
        data,
        name=Path(shown).name,
        diggs_version=diggs_version,
        results=results,
        field_provenance=provenance == "field",
        time_budget=time_budget,
    )


def read_bytes(
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    name: str | None = None,
    diggs_version: str | None = None,
    results: bool = True,
    provenance: Literal["object", "field"] = "object",
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> Model:
    """Read a DIGGS document held in memory, such as an upload.

    The same as :func:`read`, with the input rules of :func:`pydiggs.validate_bytes`:
    a ``bytearray`` or ``memoryview`` is copied first, a binary stream is read to its
    end and left open, and ``name`` is reduced to its last path component.

    Raises:
        InstanceReadError: The input is too large, compressed or an unreadable stream.
        DocumentError: The bytes are not a readable DIGGS document.
        TypeError: ``data`` is text, a text stream or a non-contiguous memoryview.
        ValueError: An argument is out of range.
    """
    limit = _check(diggs_version, max_bytes, time_budget, provenance)
    shown = _input_name(name)
    payload = _payload(data, limit, shown)
    return read_document(
        payload,
        name=shown,
        diggs_version=diggs_version,
        results=results,
        field_provenance=provenance == "field",
        time_budget=time_budget,
    )
