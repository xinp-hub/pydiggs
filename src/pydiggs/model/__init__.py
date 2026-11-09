"""diggs-model: the normalised, version-agnostic model of one DIGGS file (preview).

The read API (:func:`pydiggs.preview.read`) returns a :class:`Model`. This module holds
its node and value classes. It is pure: no I/O and no XML. Until pyDIGGS 1.7.0 freezes
diggs-model 1.0, it is a preview outside the stability promise; see
https://xinp-hub.github.io/pydiggs/model/.
"""

from pydiggs.model._model import MODEL_SCHEMA_URL, MODEL_SCHEMA_VERSION, Model, model_schema
from pydiggs.model._nodes import (
    Associate,
    DocumentInfo,
    Feature,
    LithologyLayer,
    Measurement,
    Node,
    Observation,
    Other,
    Project,
    Sample,
    SamplingActivity,
)
from pydiggs.model._values import (
    Code,
    Column,
    CrsComponent,
    CrsRef,
    Domain,
    ExtraNode,
    Geographic,
    Interval,
    Link,
    Position,
    Procedure,
    Provenance,
    Quantity,
    ResultTable,
    SiValue,
    SourceInfo,
)

__all__ = [
    "MODEL_SCHEMA_URL",
    "MODEL_SCHEMA_VERSION",
    "Associate",
    "Code",
    "Column",
    "CrsComponent",
    "CrsRef",
    "DocumentInfo",
    "Domain",
    "ExtraNode",
    "Feature",
    "Geographic",
    "Interval",
    "Link",
    "LithologyLayer",
    "Measurement",
    "Model",
    "Node",
    "Observation",
    "Other",
    "Position",
    "Procedure",
    "Project",
    "Provenance",
    "Quantity",
    "ResultTable",
    "Sample",
    "SamplingActivity",
    "SiValue",
    "SourceInfo",
    "model_schema",
]
