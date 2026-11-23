"""The DIGGS tables profile: tables to DIGGS, DIGGS to tables, and nothing silent."""

from __future__ import annotations

import doctest
import importlib
import itertools
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import ModelWriteError, TablesError, tables
from pydiggs.findings.model import Step
from pydiggs.findings.rules import explain_rule
from pydiggs.tables import _report

OFFICIAL = Path("tests/fixtures/official/3.0")
SAMPLE_TYPES = "https://diggsml.org/def/codes/DIGGS/0.1/sampleType.xml"


def site() -> dict[str, list[dict[str, Any]]]:
    """A small site in profile tables: a borehole, a sample, two layers, three tests."""
    cone = [
        {
            "measurement": "CPT-1",
            "row": row,
            "position": 0.02 * (row + 1),
            "tip_resistance": 2.5 + row,
            "sleeve_friction": 21.0 + row,
        }
        for row in range(5)
    ]
    return {
        "document": [{"creation_date": "2026-11-21"}],
        "projects": [{"key": "P1", "name": "Riverside"}],
        "features": [
            {
                "key": "BH-1",
                "project": "P1",
                "name": "BH-1",
                "x": 451200,
                "y": 206500,
                "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
                "total_depth": 12.5,
                "depth_uom": "m",
            }
        ],
        "samples": [
            {
                "key": "S1",
                "name": "S1",
                "feature": "BH-1",
                "top": 2,
                "base": 2.45,
                "type": "open_tube",
                "type_code_space": SAMPLE_TYPES,
            }
        ],
        "layers": [
            {"key": "L1", "feature": "BH-1", "top": 0, "base": 1.2, "description": "MADE GROUND"},
            {
                "key": "L2",
                "feature": "BH-1",
                "top": 1.2,
                "base": 5,
                "description": "Grey CLAY",
                "legend_code": "201",
            },
        ],
        "measurements": [
            {"key": "SPT-1", "feature": "BH-1", "procedure": "DrivenPenetrationTest", "depth": 7},
            {"key": "CPT-1", "feature": "BH-1", "procedure": "StaticConePenetrationTest"},
            {
                "key": "LL-1",
                "feature": "BH-1",
                "procedure": "AtterbergLimitsTest",
                "sample": "S1",
                "depth": 2.1,
            },
        ],
        "params": [
            {
                "node": "SPT-1",
                "scope": "procedure",
                "name": "penetrationTestType",
                "value": "SPT",
            },
            {
                "node": "SPT-1",
                "scope": "procedure",
                "name": "hammerEfficiency",
                "value": "72",
                "uom": "%",
            },
            {"node": "BH-1-lithology", "name": "lithologyClassificationType", "value": "USCS"},
        ],
        "observations": [{"key": "BH-1-lithology", "feature": "BH-1"}],
        "result_columns": [
            {"procedure": "StaticConePenetrationTest", "column": "tip_resistance", "uom": "MPa"},
            {"procedure": "StaticConePenetrationTest", "column": "sleeve_friction", "uom": "kPa"},
        ],
        "results_DrivenPenetrationTest": [{"measurement": "SPT-1", "n_value": 32}],
        "results_StaticConePenetrationTest": cone,
        "results_AtterbergLimitsTest": [
            {"measurement": "LL-1", "liquid_limit": 48, "plastic_limit": 22, "plasticity_index": 26}
        ],
    }


def with_observation(data: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    for layer in data["layers"]:
        layer["observation"] = "BH-1-lithology"
    return data


def assert_same_rows(data: dict[str, list[dict[str, Any]]], back: tables.TableSet) -> None:
    """Every row given is a row that came back, with every value it was given with."""
    for name, rows in data.items():
        if name == "result_columns":
            continue  # only what a column's name does not already say comes back
        found = back.rows(name)
        if name == "params":  # in the order of the nodes, with the defaults DIGGS requires
            for wanted in rows:
                assert any(
                    all(row.get(column) == value for column, value in wanted.items())
                    for row in found
                ), wanted
            continue
        assert len(found) == len(rows), name
        for wanted, got in zip(rows, found, strict=True):
            for column, value in wanted.items():
                assert got[column] == value, (name, column)


def rules(findings: Any) -> list[str]:
    return sorted({finding.rule_id for finding in findings})


# --- tables to DIGGS ------------------------------------------------------------------


def test_tables_give_a_valid_diggs_file_with_nothing_left_out() -> None:
    result = tables.to_model(with_observation(site()))
    assert result.findings == ()
    assert result.lossless
    report = pydiggs.validate_bytes(pydiggs.write_bytes(result.model))
    assert report.verdict == "valid", [f.message for f in report.findings]


def test_the_file_holds_every_value_of_the_tables() -> None:
    model = pydiggs.read_bytes(pydiggs.write_bytes(tables.to_model(with_observation(site())).model))
    feature = model.features[0]
    assert (feature.name, feature.element, feature.total_depth.value) == ("BH-1", "Borehole", 12.5)
    assert feature.location.coordinates == (451200.0, 206500.0)
    assert [layer.description for layer in model.observations[0].layers] == [
        "MADE GROUND",
        "Grey CLAY",
    ]
    cone = model.get("CPT-1")
    table = cone.results[0]
    assert table.row_count == 5
    assert table.column("tip_resistance").uom == "MPa"
    assert table.column("tip_resistance").values == (2.5, 3.5, 4.5, 5.5, 6.5)
    assert table.domain.index[0].values == (0.02, 0.04, 0.06, 0.08, 0.1)
    spt = model.get("SPT-1")
    assert spt.procedure.params["hammerEfficiency"].uom == "%"
    assert model.get("LL-1").samples == ("S1",)
    assert model.samples[0].interval.top.value == 2.0


def test_writing_is_deterministic() -> None:
    first = pydiggs.write_bytes(tables.to_model(site()).model)
    assert first == pydiggs.write_bytes(tables.to_model(site()).model)


def test_defaults_are_named_not_silent() -> None:
    result = tables.to_model({"features": [{"key": "BH-1"}]})
    assert rules(result.findings) == ["tables.assumed"]
    messages = " ".join(finding.message for finding in result.findings)
    for word in ("creation date", "project", "depth_uom", "location"):
        assert word in messages
    assert pydiggs.validate_bytes(pydiggs.write_bytes(result.model)).verdict == "valid"


@pytest.mark.parametrize(
    ("change", "rule", "path"),
    [
        (("features", 0, "colour", "red"), "tables.unmapped-column", "features"),
        (("layers", 0, "feature", "BH-9"), "tables.orphan-row", "layers/1/feature"),
        (("layers", 1, "key", "L1"), "tables.duplicate-key", "layers/2/key"),
        (("layers", 0, "depth_uom", "ft"), "tables.unknown-unit", "layers/1/depth_uom"),
        (("layers", 0, "top", "shallow"), "tables.value-unparsed", "layers/1/top"),
        (("layers", 0, "description", ""), "tables.incomplete", "layers/1"),
        (("measurements", 0, "depth", None), "tables.incomplete", "measurements/1"),
        (("features", 0, "depth_uom", "km"), "tables.unknown-unit", "features/1/depth_uom"),
    ],
)
def test_a_row_that_cannot_be_written_is_named(
    change: tuple[str, int, str, Any], rule: str, path: str
) -> None:
    data = site()
    table, row, column, value = change
    data[table][row][column] = value
    result = tables.to_model(data)
    found = [f for f in result.findings if f.rule_id == rule]
    assert found, rules(result.findings)
    assert found[0].path == path
    assert found[0].step is Step.TABLES
    # What is left is still a valid file: a bad row never spoils the rest.
    assert pydiggs.validate_bytes(pydiggs.write_bytes(result.model)).verdict == "valid"


def test_coordinates_without_a_reference_system_are_named() -> None:
    data = site()
    del data["features"][0]["crs"]
    found = [f for f in tables.to_model(data).findings if f.rule_id == "tables.no-crs"]
    assert [f.path for f in found] == ["features/1/crs"]


@pytest.mark.parametrize("depth", [0, "0.00", -3])
def test_a_total_depth_that_is_not_positive_is_named_not_written(depth: Any) -> None:
    data = site()
    data["features"][0]["total_depth"] = depth
    result = tables.to_model(data)
    found = [f for f in result.findings if f.rule_id == "tables.value-unparsed"]
    assert [f.path for f in found] == ["features/1/total_depth"]
    assert result.model.features[0].total_depth is None
    assert pydiggs.validate_bytes(pydiggs.write_bytes(result.model)).verdict == "valid"


def test_depths_are_never_converted() -> None:
    data = site()
    data["layers"][0]["depth_uom"] = "ft"
    result = tables.to_model(data)
    layers = result.model.observations[0].layers
    assert [layer.key for layer in layers] == ["L2"]
    assert all(layer.interval.top.uom == "m" for layer in layers)


def test_a_key_that_is_not_an_xml_id_is_rewritten_and_listed() -> None:
    data = site()
    data["features"][0]["key"] = "BH 1/A"
    for name in ("samples", "layers", "measurements"):
        for row in data[name]:
            row["feature"] = "BH 1/A"
    data["observations"][0]["feature"] = "BH 1/A"
    result = tables.to_model(data)
    assert result.keys["BH 1/A"] == "BH_1_A"
    assert "tables.key-rewritten" in rules(result.findings)
    assert result.model.get("BH_1_A").name == "BH-1"


def test_extra_rows_are_reported_not_written() -> None:
    data = site()
    data["extra"] = [{"node": "BH-1", "seq": 0, "path": "diggs:role", "content": "{}"}]
    assert "tables.extra-not-written" in rules(tables.to_model(data).findings)


def test_numbers_keep_the_text_they_were_given_with() -> None:
    data = site()
    data["features"][0]["total_depth"] = "12.50"
    xml = pydiggs.write_bytes(tables.to_model(data).model)
    assert b">12.50</" in xml


@pytest.mark.parametrize(
    "bad",
    [{"boreholes": []}, {"features": [{"name": "BH-1"}]}, {"results_X": [{"value": 1}]}],
)
def test_input_that_is_not_the_profile_is_an_error(bad: dict[str, Any]) -> None:
    with pytest.raises(TablesError) as caught:
        tables.to_model(bad)
    assert isinstance(caught.value, ValueError)


# --- DIGGS to tables ------------------------------------------------------------------


def test_round_trip_reaches_a_fixed_point() -> None:
    xml = pydiggs.write_bytes(tables.to_model(with_observation(site())).model)
    once = tables.from_model(pydiggs.read_bytes(xml))
    again = tables.to_model(once.tables)
    assert again.findings == ()
    assert pydiggs.write_bytes(again.model) == xml
    assert tables.from_model(pydiggs.read_bytes(xml)).tables == once.tables


def test_tables_come_back_as_they_went_in() -> None:
    data = with_observation(site())
    back = tables.from_model(pydiggs.read_bytes(pydiggs.write_bytes(tables.to_model(data).model)))
    assert_same_rows(data, back)
    assert back.rows("results_StaticConePenetrationTest")[0]["row"] == 0


def test_the_load_plan_puts_parents_first() -> None:
    back = tables.from_model(tables.to_model(with_observation(site())).model)
    order = [step.table for step in back.plan.steps]
    assert order.index("projects") < order.index("features") < order.index("samples")
    assert order.index("measurements") < order.index("results_DrivenPenetrationTest")
    assert order.index("measurements") < order.index("params")
    by_table = {step.table: step for step in back.plan.steps}
    assert by_table["features"].identity == ("key",)
    assert by_table["features"].depends_on == ("projects",)
    assert by_table["results_DrivenPenetrationTest"].identity == ("measurement", "result", "row")
    assert by_table["results_DrivenPenetrationTest"].rows == 1
    assert json.dumps(back.plan.to_dict())  # plain JSON


def test_a_form_such_as_trailing_zeros_is_noted() -> None:
    data = site()
    data["features"][0]["total_depth"] = "12.50"
    back = tables.from_model(pydiggs.read_bytes(pydiggs.write_bytes(tables.to_model(data).model)))
    assert back.rows("features")[0]["total_depth"] == 12.5
    assert "tables.value-reformatted" in rules(back.findings)


@pytest.mark.parametrize("path", sorted(OFFICIAL.glob("*.xml")), ids=lambda p: p.name)
def test_official_files_go_to_tables_and_back_without_new_errors(path: Path) -> None:
    source = pydiggs.read(path)
    flat = tables.from_model(source)
    assert all(f.rule_id in _report.RULES for f in flat.findings)
    result = tables.to_model(flat.tables)
    xml = pydiggs.write_bytes(result.model)  # whatever is kept can be written
    before = {f.rule_id for f in pydiggs.validate(path).findings if f.severity.value == "error"}
    after = {f.rule_id for f in pydiggs.validate_bytes(xml).findings if f.severity.value == "error"}
    assert after <= before
    # Every row that did not make it is named.
    kept = set(result.keys)
    lost = [row["key"] for row in flat.rows("features") if row["key"] not in kept]
    if lost:
        assert {"tables.incomplete", "tables.orphan-row"} & set(rules(result.findings))


def test_the_official_borehole_example_keeps_every_record_that_has_data() -> None:
    source = pydiggs.read(OFFICIAL / "Borehole-CPT_Example_Annotated.xml")
    result = tables.to_model(tables.from_model(source).tables)
    back = pydiggs.read_bytes(pydiggs.write_bytes(result.model))
    assert len(back.features) == len(source.features)
    assert len(back.samples) == len(source.samples)
    layers = sum(len(o.layers) for o in source.observations)
    assert sum(len(o.layers) for o in back.observations) == layers
    # Every test comes back, the triaxial tests with no result rows too.
    assert len(back.measurements) == len(source.measurements)


def test_content_outside_the_profile_is_kept_as_json() -> None:
    source = pydiggs.read(OFFICIAL / "Borehole-CPT_Example_Annotated.xml")
    flat = tables.from_model(source)
    assert flat.rows("extra")
    assert "tables.not-carried" in rules(flat.findings)
    for row in flat.rows("extra"):
        json.loads(row["content"])


# --- the writer -----------------------------------------------------------------------


def test_a_model_the_writer_cannot_write_is_refused_by_name() -> None:
    source = pydiggs.read(OFFICIAL / "Borehole-CPT_Example_Annotated.xml")
    with pytest.raises(ModelWriteError, match="extra") as caught:
        pydiggs.write_bytes(source)
    assert isinstance(caught.value, ValueError)
    assert isinstance(caught.value, pydiggs.PydiggsError)


def test_write_puts_the_file_on_disk(tmp_path: Path) -> None:
    target = tmp_path / "out.xml"
    pydiggs.write(tables.to_model(site()).model, target)
    assert pydiggs.validate(target).verdict == "valid"


# --- files, cursors and the descriptor ------------------------------------------------


def test_csv_package_round_trip(tmp_path: Path) -> None:
    result = tables.to_model(with_observation(site()))
    xml = pydiggs.write_bytes(result.model)
    written = tables.write_package(tables.from_model(pydiggs.read_bytes(xml)), tmp_path)
    assert [path.name for path in written][-2:] == ["datapackage.json", "plan.json"]
    again = tables.to_model(tables.read_package(tmp_path))
    assert again.findings == ()
    assert pydiggs.write_bytes(again.model) == xml
    described = json.loads((tmp_path / "datapackage.json").read_text(encoding="utf-8"))
    names = [resource["name"] for resource in described["resources"]]
    assert names == [path.stem for path in written[:-2]]
    cone = next(r for r in described["resources"] if r["name"].endswith("ConePenetrationTest"))
    fields = {field["name"]: field for field in cone["schema"]["fields"]}
    assert fields["tip_resistance"]["unit"] == "MPa"
    assert cone["schema"]["primaryKey"] == ["measurement", "result", "row"]


def test_csv_files_without_a_descriptor_are_read_by_name(tmp_path: Path) -> None:
    (tmp_path / "features.csv").write_text(
        "key,name,x,y,depth_uom\nBH-1,BH-1,1,2,ft\n", encoding="utf-8"
    )
    result = tables.to_model(tables.read_package(tmp_path))
    assert result.model.features[0].location.coordinates == (1.0, 2.0)


def test_a_descriptor_cannot_reach_outside_its_folder(tmp_path: Path) -> None:
    folder = tmp_path / "package"
    folder.mkdir()
    (tmp_path / "features.csv").write_text("key,name\nBH-1,BH-1\n", encoding="utf-8")
    (folder / "datapackage.json").write_text(
        json.dumps({"resources": [{"name": "features", "path": "../features.csv"}]}),
        encoding="utf-8",
    )
    with pytest.raises(TablesError, match="leaves the folder"):
        tables.read_package(folder)


@pytest.mark.parametrize("name", ["boreholes.csv", "results_.csv"])
def test_a_file_that_is_not_a_profile_table_is_refused(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("key\n1\n", encoding="utf-8")
    with pytest.raises(TablesError, match="not a table of the profile"):
        tables.read_package(tmp_path)


def test_rows_come_straight_from_a_database_cursor() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE hole (id TEXT, label TEXT, east REAL, north REAL, depth_ft REAL);
        INSERT INTO hole VALUES ('B-7', 'Boring 7', 1200.5, 880.25, 40);
        CREATE TABLE blow (hole TEXT, n INTEGER, at_ft REAL);
        INSERT INTO blow VALUES ('B-7', 14, 5), ('B-7', 22, 10);
        """
    )
    views = {
        "features": "SELECT id AS key, label AS name, east AS x, north AS y,"
        " depth_ft AS total_depth, 'ft' AS depth_uom FROM hole",
        "measurements": "SELECT hole || '-SPT-' || rowid AS key, hole AS feature,"
        " 'DrivenPenetrationTest' AS procedure, at_ft AS depth FROM blow",
        "results_DrivenPenetrationTest": "SELECT hole || '-SPT-' || rowid AS measurement,"
        " n AS n_value FROM blow",
        "params": "SELECT hole || '-SPT-' || rowid AS node, 'procedure' AS scope,"
        " 'penetrationTestType' AS name, 'SPT' AS value FROM blow",
    }
    result = tables.to_model(
        {name: tables.rows_from_cursor(db.execute(sql)) for name, sql in views.items()}
    )
    model = pydiggs.read_bytes(pydiggs.write_bytes(result.model))
    assert [m.results[0].columns[0].values for m in model.measurements] == [(14.0,), (22.0,)]
    assert model.features[0].total_depth.uom == "ft"
    with pytest.raises(TablesError, match="no result set"):
        list(tables.rows_from_cursor(db.execute("CREATE TABLE x (y)")))


def test_the_profile_describes_every_table() -> None:
    described = tables.profile()
    assert described["profile_version"] == tables.PROFILE_VERSION
    by_name = {resource["name"]: resource["schema"] for resource in described["resources"]}
    assert by_name["features"]["primaryKey"] == ["key"]
    assert {
        "fields": ["project"],
        "reference": {"resource": "projects", "fields": ["key"]},
    } in by_name["features"]["foreignKeys"]
    uom = next(f for f in by_name["features"]["fields"] if f["name"] == "total_depth_uom")
    assert uom["uomOf"] == "total_depth"
    for schema in by_name.values():
        assert all(field["type"] in {"string", "number", "integer"} for field in schema["fields"])


def test_every_rule_has_a_catalogue_entry_and_guidance() -> None:
    for rule in _report.RULES:
        info = explain_rule(rule)
        assert info.severities == (_report.RULES[rule],)
        assert info.step is Step.TABLES
        assert info.guidance is not None, rule


# --- properties -----------------------------------------------------------------------

_depth = st.integers(min_value=0, max_value=2000).map(lambda n: n / 10)
_word = st.text("ABCDEFGHJKLMNPQRSTUVWXYZ", min_size=1, max_size=6)


@st.composite
def sites(draw: st.DrawFn) -> dict[str, list[dict[str, Any]]]:
    unit = draw(st.sampled_from(["m", "ft"]))
    holes = draw(st.lists(_word, min_size=1, max_size=3, unique=True))
    data: dict[str, list[dict[str, Any]]] = {
        "document": [{"creation_date": "2026-01-31"}],
        "projects": [{"key": "P", "name": "P"}],
        "features": [],
        "observations": [],
        "layers": [],
        "measurements": [],
        "params": [],
        "results_DrivenPenetrationTest": [],
    }
    for hole in holes:
        key = f"BH-{hole}"
        data["features"].append(
            {
                "key": key,
                "project": "P",
                "name": hole,
                "x": draw(st.integers(0, 10**6)) / 4,
                "y": draw(st.integers(0, 10**6)) / 4,
                "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
                "depth_uom": unit,
            }
        )
        cuts = sorted(draw(st.lists(_depth, min_size=2, max_size=5, unique=True)))
        if len(cuts) > 1:
            data["observations"].append({"key": f"{key}-log", "feature": key})
            data["params"].append(
                {"node": f"{key}-log", "name": "lithologyClassificationType", "value": "USCS"}
            )
        for number, (top, base) in enumerate(itertools.pairwise(cuts)):
            data["layers"].append(
                {
                    "key": f"{key}-L{number}",
                    "observation": f"{key}-log",
                    "feature": key,
                    "top": top,
                    "base": base,
                    "depth_uom": unit,
                    "description": draw(_word),
                }
            )
        for number, depth in enumerate(draw(st.lists(_depth, max_size=4, unique=True))):
            test = f"{key}-SPT{number}"
            data["measurements"].append(
                {
                    "key": test,
                    "feature": key,
                    "procedure": "DrivenPenetrationTest",
                    "depth": depth,
                    "depth_uom": unit,
                }
            )
            data["results_DrivenPenetrationTest"].append(
                {"measurement": test, "row": 0, "n_value": float(draw(st.integers(0, 100)))}
            )
            data["params"].append(
                {"node": test, "scope": "procedure", "name": "penetrationTestType", "value": "SPT"}
            )
    return {name: rows for name, rows in data.items() if rows}


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sites())
def test_any_site_goes_to_diggs_and_comes_back_row_for_row(
    data: dict[str, list[dict[str, Any]]],
) -> None:
    result = tables.to_model(data)
    assert result.findings == ()
    xml = pydiggs.write_bytes(result.model)
    assert pydiggs.validate_bytes(xml).verdict == "valid"
    back = tables.from_model(pydiggs.read_bytes(xml))
    assert not [f for f in back.findings if f.severity.rank]
    assert_same_rows(data, back)


@pytest.mark.parametrize("unit", ["m", "ft", "ft[US]", "cm", "in"])
def test_a_feature_with_no_depths_keeps_its_depth_unit(unit: str) -> None:
    made = tables.to_model({"features": [{"key": "BH-1", "depth_uom": unit}]})
    back = tables.from_model(pydiggs.read_bytes(pydiggs.write_bytes(made.model)))
    assert back.rows("features")[0]["depth_uom"] == unit


@pytest.mark.parametrize(
    "name",
    [
        "pydiggs.tables",
        "pydiggs.tables._to_model",
        "pydiggs.tables._from_model",
        "pydiggs.tables._io",
        "pydiggs.tables._profile",
        "pydiggs._write",
    ],
)
def test_the_examples_in_the_docstrings_run(name: str) -> None:
    module = importlib.import_module(name)
    failed, attempted = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert attempted
    assert failed == 0


# --- the round-trip check -------------------------------------------------------------


def test_a_complete_site_passes_the_round_trip_check() -> None:
    checked = tables.round_trip(with_observation(site()))
    assert checked.differences == ()
    assert checked.findings == ()
    assert checked.validation.verdict == "valid"
    assert checked.ok


def test_the_round_trip_check_fails_when_a_row_is_left_out() -> None:
    data = with_observation(site())
    data["layers"][0]["feature"] = "BH-9"
    data["layers"][0]["observation"] = None
    checked = tables.round_trip(data)
    assert not checked.ok
    assert "tables.orphan-row" in rules(checked.findings)
    assert checked.differences == ()  # what was written came back as sent


def test_the_round_trip_check_compares_numbers_as_numbers() -> None:
    data = with_observation(site())
    data["features"][0]["total_depth"] = "12.50"
    data["results_DrivenPenetrationTest"][0]["n_value"] = "32"
    assert tables.round_trip(data).ok


def test_a_value_that_comes_back_changed_is_a_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydiggs.tables import _round_trip

    real = _round_trip.from_model

    def altered(model: Any) -> tables.TableSet:
        found = real(model)
        found.rows("features")[0]["total_depth"] = 99.0
        found.rows("results_DrivenPenetrationTest").clear()
        return found

    monkeypatch.setattr(_round_trip, "from_model", altered)
    checked = tables.round_trip(with_observation(site()))
    assert not checked.ok
    assert tables.Difference("features", "BH-1", "total_depth", 12.5, 99.0) in checked.differences
    missing = [d for d in checked.differences if d.table == "results_DrivenPenetrationTest"]
    assert [(d.row, d.column) for d in missing] == [("SPT-1 row 0", "(row)")]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sites())
def test_any_site_passes_the_round_trip_check(data: dict[str, list[dict[str, Any]]]) -> None:
    assert tables.round_trip(data).ok
