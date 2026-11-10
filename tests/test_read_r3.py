"""Read API R3: the stable top-level names, field provenance and registry LRS units."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import pydiggs
from pydiggs import crs, model

ROOT = Path(__file__).resolve().parent.parent
PIEZOMETER = ROOT / "tests" / "fixtures" / "official" / "3.0" / "Piezometer_Example.xml"


def test_the_read_api_is_top_level():
    for name in (
        "read",
        "read_bytes",
        "Model",
        "model_schema",
        "MODEL_SCHEMA_VERSION",
        "MODEL_SCHEMA_URL",
        "DocumentError",
    ):
        assert name in pydiggs.__all__
    assert pydiggs.MODEL_SCHEMA_VERSION == "1.0"
    assert pydiggs.MODEL_SCHEMA_URL.endswith("/schemas/diggs-model-1.0.schema.json")
    assert pydiggs.Model is model.Model


def test_preview_is_a_deprecated_alias():
    sys.modules.pop("pydiggs.preview", None)
    with pytest.warns(DeprecationWarning, match="pydiggs.preview is deprecated"):
        preview = importlib.import_module("pydiggs.preview")
    assert preview.read is pydiggs.read
    assert preview.Model is pydiggs.Model


def test_field_provenance_says_where_each_value_was_read():
    m = pydiggs.read(PIEZOMETER, provenance="field")
    borehole = m.get("Boring_1")
    assert borehole is not None
    fields = borehole.provenance.fields
    assert fields is not None
    assert fields["totalMeasuredDepth"].xpath == "totalMeasuredDepth[1]"
    assert fields["totalMeasuredDepth"].line == 45
    assert "totalMeasuredDepth" in borehole.params or borehole.total_depth is not None
    assert pydiggs.Model.from_json(m.to_json()) == m


def test_object_provenance_is_the_default():
    m = pydiggs.read(PIEZOMETER)
    assert all(node.provenance.fields is None for node in m.nodes())


def test_provenance_must_be_object_or_field():
    with pytest.raises(ValueError, match="provenance"):
        pydiggs.read(PIEZOMETER, provenance="cell")  # type: ignore[arg-type]


def test_registry_linear_referencing_methods_have_units():
    href = "https://diggsml.org/def/crs/DIGGS/0.1/lrm.xml#{}"
    assert crs._lrm_units(href.format("md_ft")) == "ft"
    assert crs._lrm_units(href.format("core_top_down_cm")) == "cm"
    assert crs._lrm_units(href.format("no_such_method")) is None
    assert crs._lrm_units("https://example.org/def/crs/DIGGS/0.1/lrm.xml#md_ft") is None


def test_depths_along_a_registry_lrs_take_its_unit():
    m = pydiggs.read(PIEZOMETER)
    assert m.crs["#Boring1_lsrs"].unit == "ft"
