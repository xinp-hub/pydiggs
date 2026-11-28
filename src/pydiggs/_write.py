"""The public DIGGS writer: a :class:`~pydiggs.model.Model` to a DIGGS 3.0.0 document."""

from __future__ import annotations

import os
from pathlib import Path

from pydiggs.errors import ModelWriteError
from pydiggs.model import Model

__all__ = ["write", "write_bytes"]


def write_bytes(model: Model) -> bytes:
    """Write ``model`` as one DIGGS 3.0.0 document: UTF-8 bytes with an XML declaration.

    The writer is deterministic: the same model always gives the same bytes, it reads no
    clock and it fetches nothing. Reading the result back with :func:`pydiggs.read_bytes`
    returns the model's nodes, values and links.

    It writes what :func:`pydiggs.tables.to_model` and the AGS4 converter build: typed
    fields, ``params``, components, links, and measurements (a ``Test``, a ``Monitor`` or a
    ``MeasurementWhileDrilling``) with their result tables: the first in the measurement's
    outcome, placed by depth or by time (a ``time`` or ``elapsed_time`` index), and each
    other one in the next result object of its procedure. A result table too long for one
    element (over 10 MB of text) is written in parts: part ``n`` (from 2) is a
    measurement of its own, with the gml:id of the first and ``.part<n>``, which
    :func:`pydiggs.tables.from_model` joins back. A feature with no centerline (a station,
    a trench wall) is written without one; of a feature's ``extra`` it writes a
    ``featureExtent`` and a ``whenOccupied``, as given. It does not write other ``extra``
    content or top-level business associates, so a model read from an arbitrary DIGGS file is
    usually refused; send such a model through :func:`pydiggs.tables.from_model` and
    :func:`pydiggs.tables.to_model`, which report what they leave out.

    Args:
        model: The model to write.

    Returns:
        The document. It has no DTD and no entity declarations, and every value of the
        model is escaped text or an attribute value, never markup.

    Raises:
        ModelWriteError: The model holds something that cannot be written so that
            reading it back returns it; the message names the node. It is also a
            :class:`ValueError`.
        ResourceError: The bundled layout of DIGGS 3.0.0 is missing or broken.

    Examples:
        >>> import pydiggs
        >>> from pydiggs import tables
        >>> result = tables.to_model(
        ...     {"features": [{"key": "BH-1", "name": "BH-1", "depth_uom": "m"}]}
        ... )
        >>> xml = pydiggs.write_bytes(result.model)
        >>> xml == pydiggs.write_bytes(result.model)
        True
        >>> pydiggs.validate_bytes(xml).verdict
        'valid'
    """
    # Imported here so that ``import pydiggs`` does not load the writer.
    from pydiggs._writer import write_model  # noqa: PLC0415

    try:
        return write_model(model, "3.0.0")
    except ModelWriteError:
        raise
    except ValueError as exc:
        raise ModelWriteError(str(exc)) from exc


def write(model: Model, path: str | os.PathLike[str]) -> None:
    """Write ``model`` to ``path`` as a DIGGS 3.0.0 document (see :func:`write_bytes`).

    The file is replaced when it exists. Nothing is written when the model is refused.

    Raises:
        ModelWriteError: See :func:`write_bytes`.
        OSError: The file could not be written.

    Examples:
        >>> import pathlib, tempfile
        >>> import pydiggs
        >>> from pydiggs import tables
        >>> result = tables.to_model(
        ...     {"features": [{"key": "BH-1", "name": "BH-1", "depth_uom": "m"}]}
        ... )
        >>> target = pathlib.Path(tempfile.mkdtemp()) / "out.xml"
        >>> pydiggs.write(result.model, target)
        >>> pydiggs.read(target).features[0].name
        'BH-1'
    """
    data = write_bytes(model)
    Path(path).write_bytes(data)
