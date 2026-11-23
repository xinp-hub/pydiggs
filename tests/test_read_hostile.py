"""The read API on unusual or hostile (but well-formed) input: it reads, or it says why.

Each case here crashed with an undocumented exception, or read the wrong thing, before
the 1.6.0 pre-release review.
"""

from __future__ import annotations

import json
import time
import tracemalloc
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import crs
from pydiggs._reader import _read, read_with_places
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
    # The most empty cells a real file needs is 1,683 (6.5 MB), and at most 0.00053 per
    # byte of the file (the official, conformance and challenge files).
    assert _read.PADDED_CELLS_BASE >= 1_000 * 1_683
    assert _read.PADDED_CELLS_PER_BYTE >= 1_000 * 0.00053


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


_NS30 = 'xmlns="http://diggsml.org/schemas/3.0" xmlns:gml="http://www.opengis.net/gml/3.2"'
_TYPES = ("double", "integer", "boolean", "string", "dateTime", None)
_CELLS = ("", " ", "1", "-2.5e1", "x", "true", "0", "NaN", "2020-01-01", "nil")


def _table_doc(types: list[str | None], rows: list[list[str]], ts: str) -> bytes:
    props = "".join(
        f"<Property><propertyName>p{i}</propertyName>"
        + ("" if t is None else f"<typeData>{t}</typeData>")
        + "</Property>"
        for i, t in enumerate(types)
    )
    body = ts.join(",".join(row) for row in rows)
    return (
        f'<Diggs {_NS30}><measurement><Test gml:id="t"><outcome><TestResult><results>'
        f"<ResultSet><parameters><PropertyParameters><properties>{props}</properties>"
        f'</PropertyParameters></parameters><dataValues cs="," ts="{ts}">{body}</dataValues>'
        "</ResultSet></results></TestResult></outcome></Test></measurement></Diggs>"
    ).encode()


def _old_columns(
    self: Any, props: list[Any], rows: list[list[str]]
) -> tuple[list[dict[str, Any]], int, list[set[int]]]:
    """The columns as the reader built them up to 1.16.0: a cell per row per column."""
    columns, unparsed = [], 0
    for i, prop in enumerate(props):
        column, bad = self._column(prop, [r[i].strip() if i < len(r) else "" for r in rows])
        unparsed += bad
        columns.append(column)
    unread = [
        {n for n, r in enumerate(rows) if i < len(r) and r[i].strip() and c["values"][n] is None}
        for i, c in enumerate(columns)
    ]
    if not self.results:
        for column in columns:
            column["values"] = []
    return columns, unparsed, unread


def _old_fill_units(self: Any, value: Any, table: dict[str, Any]) -> None:
    """``_fill_units`` up to 1.16.0: it walked every cell of every column too."""
    if isinstance(value, list):
        for item in value:
            _old_fill_units(self, item, table)
        return
    if not isinstance(value, dict):
        return
    if value.keys() >= _read._INTERVAL_KEYS:
        self._interval_units(value, _read._local_unit(table.get(value["lrs"] or "")))
        return
    if value.keys() >= _read._TABLE_KEYS:
        _read._index_units(value["index"], _read._local_unit(table.get(value["crs"] or "")))
        return
    for name, item in value.items():
        if name not in ("params", "extra") and isinstance(item, dict | list):
            _old_fill_units(self, item, table)


def _everything(data: bytes) -> tuple[str, str, Any]:
    """What a read gives: with values, without them, and with the content left out."""
    model, *rest = read_with_places(data)
    return (
        pydiggs.read_bytes(data).to_json(),
        pydiggs.read_bytes(data, results=False).to_json(),
        (model.to_json(), *rest),
    )


@st.composite
def _tables_and_rows(draw: st.DrawFn) -> tuple[list[str | None], list[list[str]], bytes]:
    types = draw(st.lists(st.sampled_from(_TYPES), max_size=6))
    ts = draw(st.sampled_from([" ", ";"]))
    cells = st.sampled_from([c for c in _CELLS if ts != " " or " " not in c])
    width = st.integers(1, len(types) + 3)  # short, full and long rows
    rows = draw(st.lists(width.flatmap(lambda n: st.lists(cells, min_size=n, max_size=n))))
    body = ts.join(",".join(row) for row in rows).strip()
    tuples = body.split() if ts == " " else [t.strip() for t in body.split(ts) if t.strip()]
    return types, [t.split(",") for t in tuples], _table_doc(types, rows, ts)  # rows as read


def _tables() -> st.SearchStrategy[bytes]:
    return _tables_and_rows().map(lambda table: table[2])


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_tables())
def test_short_long_and_null_rows_read_as_before(data):
    """The model, and what the reader keeps beside it, are those the old reader gave."""
    new = _everything(data)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_read._Reader, "_columns", _old_columns)
        patch.setattr(_read._Reader, "_fill_units", _old_fill_units)
        old = _everything(data)
    assert new == old


def test_a_wide_table_is_charged_to_the_time_budget(monkeypatch):
    """The budget runs out between two columns of one table: the read stops there."""
    calls: list[int] = []
    real = _read._Reader._column

    def column(self, prop, cells):
        calls.append(len(cells))
        if len(calls) == 3:
            self.deadline = 0.0  # out of time from here on
        return real(self, prop, cells)

    monkeypatch.setattr(_read._Reader, "_column", column)
    model = pydiggs.read_bytes(_table_doc(["double"] * 50, [["1"]] * 10, " "), time_budget=60)
    assert len(calls) == 3  # not the 47 columns after
    assert model.complete is False and model.limits_hit == ("time_budget",)
    assert model.measurements == ()  # the member cut short is not half-read
    assert model.notes[-1].rule_id == "structure.timeout"


def test_result_cells_decode_as_before():
    """Cells are passed through in one pass only when each would be: ints still widen."""
    document = json.loads(pydiggs.read_bytes(_table_doc(["double"], [["1"]], " ")).to_json())
    values = document["measurements"][0]["results"][0]["columns"][0]
    values["values"] = [1, None, "a", True, 2.5]
    read = Model.from_dict(document).measurements[0].results[0].columns[0].values
    assert read == (1.0, None, "a", True, 2.5) and type(read[0]) is float
    values["values"] = [1.0, {"a": 1}]
    with pytest.raises(ValueError, match="is not one of"):
        Model.from_dict(document)


def _padded(types: list[str | None], rows: list[list[str]]) -> int:
    """The null cells a table of these rows needs under ``types``."""
    return len(types) * len(rows) - sum(min(len(r), len(types)) for r in rows)


def test_short_rows_past_the_padded_cell_bound_stop_the_read():
    """20,000 properties over 100,000 one-value rows: 2x10^9 null cells from 1.8 MB."""
    data = _table_doc(["double"] * 20_000, [["1"]] * 100_000, " ")
    assert _padded(["double"] * 20_000, [["1"]] * 100_000) > 1_000 * len(data)
    tracemalloc.start()
    try:
        started = time.perf_counter()
        model = pydiggs.read_bytes(data)
        took = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert took < 5  # about 0.3 s here; allocating the cells would take hundreds of GB
    assert peak < 200 * 2**20  # about 20 MB
    assert model.complete is False and model.limits_hit == ("padded_cells",)
    assert model.measurements == ()  # the member cut short is not half-read
    notes = [n for n in model.notes if n.rule_id == "structure.read-limit"]
    assert len(notes) == 1 and notes[0].severity == pydiggs.Severity.ERROR
    assert "empty" in notes[0].message
    assert Model.from_json(model.to_json()) == model
    # Without the values no cell is made, and the read is whole.
    assert pydiggs.read_bytes(data, results=False).complete


def test_validate_reports_no_read_limit_for_padded_cells():
    """Like the other read limits, it is the reader's: validate does not read a model."""
    data = _table_doc(["double"] * 20_000, [["1"]] * 100_000, " ")
    report = pydiggs.validate_bytes(data)
    assert "structure.read-limit" not in {f.rule_id for f in report.findings}


def test_the_padded_cell_bound_is_inclusive_and_counts_every_table(monkeypatch):
    rows = [["1"]] * 5  # 9 nulls a row under 10 properties: 45 per table
    monkeypatch.setattr(_read, "PADDED_CELLS_PER_BYTE", 0)
    monkeypatch.setattr(_read, "PADDED_CELLS_BASE", 45)
    assert pydiggs.read_bytes(_table_doc(["double"] * 10, rows, " ")).complete  # just under
    monkeypatch.setattr(_read, "PADDED_CELLS_BASE", 44)
    model = pydiggs.read_bytes(_table_doc(["double"] * 10, rows, " "))
    assert model.complete is False and model.limits_hit == ("padded_cells",)
    # Two tables of 45 in two members: the first is read, the second stops the read.
    monkeypatch.setattr(_read, "PADDED_CELLS_BASE", 60)
    one = _table_doc(["double"] * 10, rows, " ").decode()
    test = one[one.index("<measurement>") : one.index("</Diggs>")]
    two = one.replace("</Diggs>", test.replace('gml:id="t"', 'gml:id="u"') + "</Diggs>")
    model = pydiggs.read_bytes(two.encode())
    assert model.limits_hit == ("padded_cells",)
    assert [m.key for m in model.measurements] == ["t"]


def test_the_padded_cell_bound_grows_with_the_file(monkeypatch):
    monkeypatch.setattr(_read, "PADDED_CELLS_BASE", 0)
    data = _table_doc(["double"] * 200, [["1"]] * 200, " ")  # 39,800 nulls in 16 kB
    assert not pydiggs.read_bytes(data).complete
    padded = data.replace(b"</Diggs>", b"<!--" + b" " * 40_000 + b"--></Diggs>")
    assert pydiggs.read_bytes(padded).complete  # the same table in a file large enough


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_tables_and_rows(), st.integers(0, 40))
def test_tables_under_the_padded_cell_bound_read_as_before(table, bound):
    """Under the bound the output is the old reader's; over it the read stops there."""
    types, rows, data = table
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_read, "PADDED_CELLS_BASE", bound)
        patch.setattr(_read, "PADDED_CELLS_PER_BYTE", 0)
        new = _everything(data)
        patch.setattr(_read._Reader, "_columns", _old_columns)
        patch.setattr(_read._Reader, "_fill_units", _old_fill_units)
        old = _everything(data)
    if _padded(types, rows) <= bound:
        assert new == old
    else:
        assert Model.from_json(new[0]).limits_hit == ("padded_cells",)
        assert new[1] == old[1]  # results=False makes no null cell, and counts none
