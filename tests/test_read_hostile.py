"""The read API on unusual or hostile (but well-formed) input: it reads, or it says why.

Each case here crashed with an undocumented exception, or read the wrong thing, before
the 1.6.0 pre-release review.
"""

from __future__ import annotations

import json

import pytest

import pydiggs
from pydiggs import crs
from pydiggs._reader import _read
from pydiggs.errors import DocumentError
from pydiggs.model import Model

_NS = (
    'xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink"'
)


def _read_body(body: str) -> Model:
    return pydiggs.read_bytes(f"<Diggs {_NS}>{body}</Diggs>".encode())


_RESULT_SET = (
    '<measurement><Test gml:id="t"><outcome><TestResult><results><ResultSet>'
    '<parameters><PropertyParameters><properties><Property index="1">'
    "<propertyName>a</propertyName><typeData>double</typeData></Property></properties>"
    "</PropertyParameters></parameters>"
    '<dataValues ts="" cs=",">1</dataValues></ResultSet></results></TestResult>'
    "</outcome></Test></measurement>"
)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_RESULT_SET, id="empty-tuple-separator"),
        pytest.param(
            '<samplingFeature><Borehole gml:id="b"><referencePoint><PointLocation>'
            '<gml:pos srsDimension="²" srsName="EPSG:4326">1 2</gml:pos>'
            "</PointLocation></referencePoint></Borehole></samplingFeature>",
            id="superscript-srs-dimension",
        ),
        pytest.param(
            '<samplingFeature><Borehole gml:id="b"><referencePoint>'
            '<PointLocation srsName="#²"><gml:pos>1 2</gml:pos></PointLocation>'
            "</referencePoint></Borehole></samplingFeature>",
            id="superscript-srs-name",
        ),
        pytest.param(
            '<project><Project gml:id="@/Diggs[1]/samplingFeature[1]/Borehole[1]"/></project>'
            "<samplingFeature><Borehole/></samplingFeature>",
            id="gml-id-that-looks-like-a-key",
        ),
        pytest.param(
            '<samplingFeature><Borehole gml:id="b"><top>1</top><base>2</base>'
            '<lrs codeSpace="x">y</lrs></Borehole></samplingFeature>',
            id="params-named-like-an-interval",
        ),
    ],
)
def test_unusual_values_read_without_crashing(body):
    model = _read_body(body)
    assert Model.from_json(model.to_json()) == model


def test_superscript_digits_are_not_epsg_codes():
    assert crs.parse("#²").form == "local"


def test_layers_of_one_observation_have_their_own_keys():
    model = _read_body(
        '<observation><LithologySystem gml:id="ls"><lithologyObservation>'
        '<LithologyObservation gml:id="L1"/><LithologyObservation gml:id="L2"/>'
        "</lithologyObservation></LithologySystem></observation>"
        '<samplingFeature><Borehole gml:id="b"><description xlink:href="#L1"/>'
        "</Borehole></samplingFeature>"
    )
    assert [layer.key for layer in model.observations[0].layers] == ["L1", "L2"]
    assert [(link.href, link.target) for link in model.links] == [("#L1", "L1")]


def test_layers_without_ids_have_distinct_xpath_keys():
    model = _read_body(
        '<observation><LithologySystem gml:id="ls"><lithologyObservation>'
        "<LithologyObservation/><LithologyObservation/>"
        "</lithologyObservation></LithologySystem></observation>"
    )
    keys = [layer.key for layer in model.observations[0].layers]
    assert keys[0].endswith("/LithologyObservation[1]")
    assert keys[1].endswith("/LithologyObservation[2]")


def test_document_properties_keep_their_text():
    model = _read_body(
        "<gml:description>About this file</gml:description><gml:name>Site X</gml:name>"
    )
    found = {(o.kind, o.element): dict(o.params) for o in model.others}
    assert found[("property", "description")] == {"description": "About this file"}
    assert found[("property", "name")] == {"name": "Site X"}


def test_member_references_are_still_references():
    model = _read_body('<project xlink:href="#p"/>')
    assert [(o.kind, o.element) for o in model.others] == [("reference", "project")]


@pytest.mark.parametrize(
    ("note", "message"),
    [
        ({"rule_id": "a", "severity": "info", "message": "m"}, "needs step"),
        (
            {
                "step": "structure",
                "rule_id": "a",
                "severity": "info",
                "message": "m",
                "location": [1],
            },
            "location: expected an object",
        ),
    ],
)
def test_from_dict_rejects_bad_notes_with_value_error(note, message):
    document = json.loads(_read_body("").to_json())
    with pytest.raises(ValueError, match=message):
        Model.from_dict({**document, "notes": [note]})


@pytest.mark.parametrize(
    "prolog",
    [
        pytest.param('<!DOCTYPE Diggs [<!ENTITY x SYSTEM "file:///etc/passwd">]>', id="external"),
        pytest.param(
            '<!DOCTYPE Diggs [<!ENTITY % p SYSTEM "http://example.invalid/x.dtd"> %p;]>',
            id="parameter",
        ),
        pytest.param('<!DOCTYPE Diggs SYSTEM "http://example.invalid/diggs.dtd">', id="dtd"),
    ],
)
def test_external_entities_and_dtds_are_never_loaded(prolog):
    data = f"{prolog}<Diggs {_NS}><gml:name>&x;</gml:name></Diggs>".encode()
    if 'SYSTEM "file' not in prolog:
        data = data.replace(b"&x;", b"n")
    try:
        model = pydiggs.read_bytes(data)
    except DocumentError as exc:
        assert exc.kind == "malformed"
        return
    assert "root:" not in model.to_json()


def test_tag_and_crs_caches_are_bounded():
    assert _read._local.cache_info().maxsize is not None
    assert _read._crs_of.cache_info().maxsize is not None


def test_a_finite_time_budget_is_required():
    for budget in (float("nan"), float("inf"), 0, -1):
        with pytest.raises(ValueError, match="time_budget"):
            pydiggs.read_bytes(b"<Diggs/>", time_budget=budget)


def test_duplicate_gml_ids_are_noted_and_keyed_by_xpath():
    model = _read_body(
        '<samplingFeature><Borehole gml:id="B"/></samplingFeature>'
        '<samplingFeature><Borehole gml:id="B"/></samplingFeature>'
    )
    keys = [f.key for f in model.features]
    assert all(key.startswith("@/") for key in keys)
    assert len(set(keys)) == 2
    notes = [n for n in model.notes if n.rule_id == "structure.duplicate-gml-id"]
    assert len(notes) == 1
    assert notes[0].severity == pydiggs.Severity.WARNING
    assert '"B" is used by 2 elements' in notes[0].message
    assert pydiggs.explain_rule("structure.duplicate-gml-id").known


def test_duplicate_id_notes_are_bounded():
    body = "".join(
        f'<samplingFeature><Borehole gml:id="B{i}"/></samplingFeature>' * 2 for i in range(150)
    )
    notes = [n for n in _read_body(body).notes if n.rule_id == "structure.duplicate-gml-id"]
    assert len(notes) == 101
    assert notes[-1].message.startswith("50 more gml:ids")


# --- work limits (1.7.0 pre-release review) ---------------------------------------------------


def _unknown(count: int) -> str:
    return "<samplingFeature><Borehole>" + "<a/>" * count + "</Borehole></samplingFeature>"


def test_a_long_namespace_is_refused():
    ok = f'<Diggs {_NS}><x xmlns="urn:{"n" * 1000}"/></Diggs>'.encode()
    assert pydiggs.read_bytes(ok).complete
    long = f'<Diggs {_NS}><samplingFeature><Borehole xmlns="urn:{"n" * 2000}"><a/></Borehole>'
    with pytest.raises(DocumentError) as info:
        pydiggs.read_bytes((long + "</samplingFeature></Diggs>").encode())
    assert info.value.kind == "namespace-too-long"
    assert info.value.finding.rule_id == "structure.namespace-too-long"


def test_too_many_untyped_elements_stop_the_read(monkeypatch):
    monkeypatch.setattr(_read, "LIMIT_BASE", 50)
    model = _read_body(
        '<samplingFeature><Borehole gml:id="kept"><a/></Borehole></samplingFeature>'
        + _unknown(60)
        + '<samplingFeature><Borehole gml:id="after"/></samplingFeature>'
    )
    assert model.complete is False and model.limits_hit == ("extra_elements",)
    assert [f.key for f in model.features] == ["kept"]  # the member cut short is not half-read
    notes = [n for n in model.notes if n.rule_id == "structure.read-limit"]
    assert len(notes) == 1 and notes[0].severity == pydiggs.Severity.ERROR
    assert pydiggs.explain_rule("structure.read-limit").known
    assert Model.from_json(model.to_json()) == model


def test_too_many_objects_stop_the_read(monkeypatch):
    monkeypatch.setattr(_read, "LIMIT_BASE", 20)
    model = _read_body('<samplingFeature><Borehole gml:id="kept"/></samplingFeature>' + "<a/>" * 40)
    assert model.complete is False and model.limits_hit == ("nodes",)
    assert [f.key for f in model.features] == ["kept"]
    assert len(model.others) <= 20
    assert [n.rule_id for n in model.notes] == ["structure.read-limit"]


def test_the_limits_grow_with_the_file(monkeypatch):
    monkeypatch.setattr(_read, "LIMIT_BASE", 20)
    padded = "<a/>" * 40 + "<!--" + " " * (40 * _read.LIMIT_BYTES_PER_ITEM) + "-->"
    assert _read_body(padded).complete  # the same 40 objects in a file large enough for them


def test_untyped_elements_nested_too_deep_stop_the_read():
    deep = "<a>" * 250 + "</a>" * 250  # libxml2 itself allows 256
    model = _read_body(f"<samplingFeature><Borehole>{deep}</Borehole></samplingFeature>")
    assert model.complete is False and model.limits_hit == ("extra_depth",)
    assert model.features == ()
    model.to_json()  # and what was read always serialises (it used to recurse too deep)
    shallow = "<a>" * 60 + "</a>" * 60
    assert _read_body(f"<samplingFeature><Borehole>{shallow}</Borehole></samplingFeature>").complete


def test_real_files_are_far_below_the_limits():
    assert _read.LIMIT_BASE >= 20 * 5_000  # the largest corpus file has about 5,000 of each
    assert _read.LIMIT_BYTES_PER_ITEM <= 1_000 / 5  # and over 1,000 bytes for each
    assert _read.MAX_EXTRA_DEPTH >= 5 * 11  # and nests them about 11 deep


def test_the_document_grows_with_the_input_not_faster():
    """N and 2N untyped elements: twice the document, and a bounded size per element."""
    small = len(_read_body(_unknown(2_000)).to_json())
    large = len(_read_body(_unknown(4_000)).to_json())
    assert large < 2.2 * small
    assert small / 2_000 < 400  # bytes of JSON per untyped element


def test_a_decoding_error_names_where_it_is():
    # The decoder spells the location out only when it fails; it must still be exact.
    document = json.loads(_read_body("").to_json())
    document["features"] = [{**f, "name": 5} for f in document["features"]] or [{"name": 5}]
    with pytest.raises(ValueError, match=r"^\$\.features(\.name: expected a string|: missing )"):
        Model.from_dict(document)
    nested = json.loads(_read_body("").to_json())
    nested["source"] = {**nested["source"], "name": 5}
    with pytest.raises(ValueError, match=r"^\$\.source\.name: expected a string"):
        Model.from_dict(nested)
