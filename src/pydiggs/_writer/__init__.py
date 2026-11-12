"""The minimal DIGGS writer: a diggs-model :class:`~pydiggs.model.Model` to DIGGS XML.

**Internal.** Nothing here is public API or covered by SemVer; the converter
(``pydiggs.convert``) is the only caller. The public, general writer is a later design.

The writer is the inverse of :func:`pydiggs.read` for the fields a converter builds:
typed fields, ``params``, components, links and result tables. It writes DIGGS 3.0.0 only,
never writes ``extra``, and is deterministic: the same model always gives the same bytes
(ids it makes up come from the model's own ids, and it reads no clock). The order of an
element's children comes from ``layout-3.0.0.toml``, generated from the bundled XSD by
``scripts/writer_layout.py``.
"""

from __future__ import annotations

from lxml import etree

from pydiggs._writer._emit import build_tree
from pydiggs._writer._layout import DIGGS_VERSIONS, load_layout
from pydiggs.model import Model

__all__ = ["DIGGS_VERSIONS", "write_model"]


def write_model(model: Model, diggs_version: str = "3.0.0") -> bytes:
    """Write ``model`` as one DIGGS document: UTF-8 bytes with an XML declaration.

    The model must be shaped as :func:`pydiggs.read` would return it, without ``extra``.
    What reading derives is not written and need not be filled: keys, ``kind``,
    ``provenance``, SI values, ``Position.geographic``, the units of depths along a
    linear referencing system, ``source``, ``tool`` and ``notes``. References are written
    from ``model.links``; ``model.crs`` is read only for the unit and name of a feature's
    linear referencing system.

    Args:
        model: The model to write.
        diggs_version: The DIGGS version to write; only ``"3.0.0"``; anything else is refused.

    Returns:
        The document. It has no DTD and no entity declarations, and every value of the
        model is escaped text or an attribute value, never markup.

    Raises:
        ValueError: ``diggs_version`` is not supported, or the model holds something
            that cannot be written so that reading it back returns it: a non-empty
            ``extra``, top-level ``associates`` or ``others``, an element or child
            element DIGGS 3.0.0 does not have, a measurement that is not a ``Test`` with
            one result table, a typed reference with no link, text that is empty or has
            surrounding white space, a character XML forbids, a non-finite number.
        ResourceError: The bundled layout file is missing or broken.

    Examples:
        >>> from pydiggs import read_bytes
        >>> from pydiggs.model import DocumentInfo, Model, Provenance, SourceInfo
        >>> document = DocumentInfo(
        ...     key="doc",
        ...     gml_id="doc",
        ...     element="DocumentInformation",
        ...     kind="document_info",
        ...     name=None,
        ...     names=(),
        ...     description=None,
        ...     remarks=(),
        ...     provenance=Provenance(xpath=""),
        ...     extra=(),
        ...     creation_date="2026-01-31",
        ... )
        >>> source = SourceInfo(
        ...     name=None, sha256="", size=0, diggs_version="3.0.0", namespace=None, detected=False
        ... )
        >>> model = Model(
        ...     schema_version="1.0", tool={}, source=source, complete=True, document=document
        ... )
        >>> xml = write_model(model)
        >>> xml == write_model(model)
        True
        >>> back = read_bytes(xml)
        >>> back.source.diggs_version, back.document.creation_date
        ('3.0.0', '2026-01-31')
    """
    layout = load_layout(diggs_version)
    root = build_tree(model, layout)
    return bytes(etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True))
