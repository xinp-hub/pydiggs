"""Where the bundled Schematron deliberately differs from the official DIGGS rules.

Each difference is listed in the header of
``src/pydiggs/schematron/diggs_schematron_rules_2.6.sch`` and reported upstream.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pydiggs
from pydiggs.findings import rules

FIXTURE = Path(__file__).parent / "fixtures" / "official" / "2.6" / "DocumentationExample.xml"
FIELD_PROPERTIES_END = "<moistureCondition>DAMP</moistureCondition>\n                                </FieldProperties>"


def _with_plasticity(term: str) -> bytes:
    text = FIXTURE.read_text(encoding="utf-8")
    assert FIELD_PROPERTIES_END in text
    patched = text.replace(
        FIELD_PROPERTIES_END,
        "<moistureCondition>DAMP</moistureCondition>\n"
        f"<plasticity>{term}</plasticity>\n"
        "                                </FieldProperties>",
        1,
    )
    return patched.encode("utf-8")


@pytest.mark.parametrize("term", ["LOW", "non-plastic", "high"])
def test_a_plasticity_term_is_not_a_schematron_error(term):
    # DIGGS 2.6 and 3.0 define FieldProperties/plasticity as a gml:CodeType term; the
    # official rule's number(.) >= 0 failed every such term (and passed numbers).
    report = pydiggs.validate_bytes(_with_plasticity(term), name="plasticity.xml")
    assert not [f for f in report.findings if "plasticity" in f.rule_id]
    assert not [
        f for f in report.findings if f.step.value == "schema" and "plasticity" in f.message
    ]


def test_the_plasticity_rule_is_gone_from_the_catalogue():
    rule_id = "schematron.FieldProperties.plasticity"
    assert rule_id not in rules.SCHEMATRON_BUNDLED.values()
    assert rule_id not in {info.id for info in pydiggs.rule_catalogue()}
    # Still explained, but only as a member of the open family for your own rule files.
    assert pydiggs.explain_rule(rule_id).pattern is not None


def test_the_bundled_schematron_records_the_difference():
    sch = (
        Path(pydiggs.__file__).parent / "schematron" / "diggs_schematron_rules_2.6.sch"
    ).read_text(encoding="utf-8")
    header, _, body = sch.partition("-->")
    assert "removed the FieldProperties/plasticity rule" in header
    assert "'plasticity'" not in body
