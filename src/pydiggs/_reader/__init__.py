"""The diggs-model reader: ``read`` and ``read_bytes`` (public as :func:`pydiggs.read`).

``aliases.toml`` holds the per-version element renames the reader applies.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

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
from pydiggs.model import ExtraNode, Model

if TYPE_CHECKING:
    from collections.abc import Callable

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
            returned with ``complete=False`` and a ``structure.timeout`` note. Reading
            also stops, the same way with a ``structure.read-limit`` note, at a work
            limit: more objects, or more elements the model has no typed field for,
            than 100,000 plus one per 200 bytes of the file, such elements nested
            more than 64 deep (real files have a few thousand, about 10 deep), or,
            with ``results``, short result rows needing more null cells than
            4,000,000 or one per byte of the file, whichever is more.

    Raises:
        InstanceReadError: The file is missing, unreadable, too large, compressed or
            not a regular file.
        DocumentError: The file is not a readable DIGGS document (``kind``:
            ``malformed``, ``not-diggs``, ``unsafe-entity`` or ``namespace-too-long``).
        ValueError: An argument is out of range.

    Examples:
        >>> from pydiggs import read
        >>> m = read("tests/fixtures/official/3.0/Borehole-CPT_Example_Annotated.xml")
        >>> m.source.diggs_version, len(m.features) > 0
        ('3.0.0', True)
    """
    limit = _check(diggs_version, max_bytes, time_budget, provenance)
    data, name = _file_bytes(path, limit)
    return read_document(
        data,
        name=name,
        diggs_version=diggs_version,
        results=results,
        field_provenance=provenance == "field",
        time_budget=time_budget,
    )


def _file_bytes(path: str | os.PathLike[str], limit: int) -> tuple[bytes, str]:
    """The bytes of the file at ``path``, at most ``limit``, and the name it is shown by."""
    shown = os.fspath(path)
    try:
        data, _ = _xml.read_limited(Path(path), limit, regular_only=True)
    except OSError as exc:
        raise InstanceReadError(exc.errno or 0, _reason(exc), shown) from exc
    return data, Path(shown).name


def read_with_places(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
) -> tuple[
    Model,
    dict[str, tuple[str, str]],
    Counter[tuple[str | None, str | None]],
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, tuple[ExtraNode, ...]],
    dict[str, dict[str, int]],
]:
    """:func:`read` or :func:`read_bytes`, and what the reader saw that the model does not keep.

    For ``pydiggs.diff`` only. The second value maps the ``gml:id`` of each element a
    typed field read (so the model keeps neither the id nor the element) to the key of
    the record that holds it and the element's path below that record. The third
    counts the coordinate systems the file defines, by name and unit, referenced or not.
    The fourth has the ids of such elements that have no path of their own (a same-named
    sibling on the way, too deep, an id used twice): for each, the record, and a digest
    of the position and the content, of every element with that id. The fifth has,
    by the key of each record, what the file holds of it that the model does not: the
    attributes and text that no typed field holds, as ``extra`` elements, and under the
    key ``_read.ROOT`` what the file holds outside every record (the key ``""`` is there
    when a work limit cut that short). The sixth counts, per record, the
    elements whose whole content typed fields hold, by the names of the elements from
    the record down to them. All are built in the same pass as the
    model, which is the model :func:`read` gives.
    """
    limit = _check(None, max_bytes, time_budget, "object")
    if isinstance(source, str | os.PathLike):
        data, name = _file_bytes(source, limit)
    else:
        name = _input_name(None)
        data = _payload(source, limit, name)
    places: dict[str, tuple[str, str]] = {}
    systems: Counter[tuple[str | None, str | None]] = Counter()
    marks: dict[str, tuple[tuple[str, str], ...]] = {}
    unread: dict[str, tuple[ExtraNode, ...]] = {}
    names: dict[str, dict[str, int]] = {}
    model = read_document(
        data,
        name=name,
        diggs_version=None,
        results=True,
        time_budget=time_budget,
        id_places=places,
        crs_defined=systems,
        id_marks=marks,
        unread=unread,
        names=names,
    )
    return model, places, systems, marks, unread, names


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
    return read_stoppable(
        data,
        name=name,
        diggs_version=diggs_version,
        results=results,
        provenance=provenance,
        max_bytes=max_bytes,
        time_budget=time_budget,
        should_stop=None,
    )


def read_stoppable(
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    name: str | None,
    diggs_version: str | None,
    results: bool,
    provenance: Literal["object", "field"] = "object",
    max_bytes: int,
    time_budget: float | None,
    should_stop: Callable[[], bool] | None,
) -> Model:
    """:func:`read_bytes` with a cancellation hook, for ``pydiggs._service`` only.

    ``should_stop`` is checked between top-level members and every so often inside
    one; when it returns True the read ends with ``pydiggs.findings.run._Cancelled``.
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
        should_stop=should_stop,
    )
