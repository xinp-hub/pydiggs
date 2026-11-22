"""``pydiggs.mapping``: the diggs-mapping 1.0 format, strict loading, and nothing silent."""

from __future__ import annotations

import doctest
import importlib
import sqlite3
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import PydiggsError, mapping, tables
from pydiggs.findings.model import Finding, Severity, Step
from pydiggs.findings.rules import explain_rule
from pydiggs.mapping import MappingError
from pydiggs.mapping import _format as fmt
from pydiggs.tables import _report

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "mapping"
CODES = "https://diggsml.org/def/codes/DIGGS/0.1"
CRS = "http://www.opengis.net/def/crs/EPSG/0/27700"
HEADER = """
mapping_format = "1.0"
name = "test"
version = "1"
method = "manual"
profile_version = "1.0-beta.1"
"""
HOLES = """
[[tables]]
source = "hole"
target = "features"
[tables.columns]
key = "id"
"""
MAPPING_RULES = (
    "tables.mapping-unmapped-source",
    "tables.mapping-unmapped-code",
    "tables.mapping-code-not-exact",
    "tables.mapping-unknown-unit",
    "tables.mapping-unknown-target",
)


def load(body: str = HOLES) -> mapping.Mapping:
    return mapping.load(HEADER + body)


def found(findings: tuple[Finding, ...]) -> list[tuple[str, str | None]]:
    return [(finding.rule_id, finding.path) for finding in findings]


# --- loading ------------------------------------------------------------------------


def test_load_reads_the_header_and_the_tables() -> None:
    loaded = mapping.load(
        HEADER
        + 'creator = "A. Vendor"\nlicence = "Apache-2.0"\ndate = 2026-11-21\n'
        + '[source]\nschema = "LogDB"\nversion = "7"\n'
        + '[units]\nmetres = "m"\n'
        + HOLES
    )
    assert loaded.mapping_format == mapping.MAPPING_FORMAT_VERSION == "1.0"
    assert (loaded.name, loaded.version, loaded.method) == ("test", "1", "manual")
    assert (loaded.creator, loaded.licence, loaded.date) == (
        "A. Vendor",
        "Apache-2.0",
        "2026-11-21",
    )
    assert (loaded.source_schema, loaded.source_version) == ("LogDB", "7")
    assert loaded.profile_version == tables.PROFILE_VERSION
    assert dict(loaded.units) == {"metres": "m"}
    assert loaded.tables[0].columns["key"].source == "id"
    assert loaded.reads() == {"hole": ("id",)}


def test_load_takes_a_path_or_the_text(tmp_path: Path) -> None:
    path = tmp_path / "my.toml"
    path.write_text(HEADER + HOLES, encoding="utf-8")
    assert mapping.load(path) == mapping.load(str(path)) == load()


def test_a_mapping_is_immutable() -> None:
    loaded = load()
    with pytest.raises(AttributeError):
        loaded.name = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        loaded.tables[0].columns["x"] = None  # type: ignore[index]


@pytest.mark.parametrize(
    ("text", "place"),
    [
        (HEADER.replace('"1.0"', '"2.0"'), "mapping_format: this release reads '1.0'"),
        (HEADER.replace('name = "test"\n', ""), "name: must be a non-empty string"),
        (HEADER.replace('"manual"', '"by magic"'), "method: must be one of manual, assisted"),
        (HEADER.replace("1.0-beta.1", "0.9"), "profile_version: this release has"),
        (HEADER + "colour = 1\n", "unknown key 'colour'"),
        (HEADER + 'date = "last week"\n', "date: must be a date"),
        (HEADER + "[source]\nurl = 'x'\n", "source: unknown key 'url'"),
        (HEADER + "[units]\nmetres = 1\n", "units.metres: must be a non-empty string"),
        (HEADER + "tables = 3\n", "tables: must be an array of tables"),
        (HEADER + "[[tables]]\nsource = 'a'\n", "tables[1].target: must be a non-empty string"),
        (
            HEADER + "[[tables]]\nsource = 'a'\ntarget = 'features'\n",
            "tables[1].columns: must be a table with at least one column",
        ),
        (
            HEADER + "[[tables]]\nsource = 'a'\ntarget = 'my table'\ncolumns = {key = 'id'}\n",
            "tables[1].target: 'my table' is not a table name",
        ),
        (HEADER + HOLES + "name = 7\n", "tables[1].columns.name: must be a table"),
        (HEADER + HOLES + "'bad name' = 'x'\n", "tables[1].columns.bad name: 'bad name' is not"),
        (
            HEADER + HOLES + "name = { from = 'a', value = 'b' }\n",
            "tables[1].columns.name: give exactly one of from, value, template",
        ),
        (
            HEADER + HOLES + "name = { units = true }\n",
            "tables[1].columns.name: give exactly one of from, value, template",
        ),
        (HEADER + HOLES + "name = { from = 'a', eval = 'x' }\n", "unknown key 'eval'"),
        (HEADER + HOLES + "name = { from = 'a', units = 'yes' }\n", "units: must be true or false"),
        (
            HEADER + HOLES + "name = { from = 'a', codes = 'none' }\n",
            "tables[1].columns.name.codes: there is no code map [codes.none]",
        ),
        (
            HEADER + HOLES + "name = { from = 'a', uom = 'm' }\n",
            "tables[1].columns.name.uom: only a column of a results_<procedure> table",
        ),
        (HEADER + HOLES + "name = { value = [1] }\n", "name.value: a constant is a string"),
        (HEADER + HOLES + "name = { value = '' }\n", "name.value: must be a non-empty string"),
        (HEADER + HOLES + f"name = {{ value = '{'x' * 501}' }}\n", "is longer than 500"),
        (HEADER + HOLES + 'name = { value = "a\\u0007" }\n', "holds a control character"),
        (
            HEADER + HOLES + "[[tables.params]]\nvalue = 'SPT'\n",
            "tables[1].params[1].name: must be a non-empty string",
        ),
        (
            HEADER
            + HOLES
            + "[[tables.params]]\nname = 'a'\nfrom = 'b'\nuom = 'm'\nuom_from = 'u'\n",
            "tables[1].params[1]: give uom (a DIGGS unit) or uom_from (a column), not both",
        ),
        (HEADER + "[codes.c]\ncode_space = 'x'\n", "codes.c.map: must be a table"),
        (
            HEADER + "[codes.c.map]\nA = { code = 'a', match = 'similar' }\n",
            "codes.c.map.A.match: 'similar' is not one of exact, close, broader, narrower",
        ),
        (HEADER + "[codes.c.map]\nA = { match = 'exact' }\n", "codes.c.map.A.code: must be"),
        ("name = [\n", "the mapping is not TOML"),
    ],
)
def test_loading_is_strict_and_names_the_place(text: str, place: str) -> None:
    with pytest.raises(MappingError) as caught:
        mapping.load(text)
    assert place in str(caught.value)


def test_a_mapping_error_is_a_value_error_and_is_not_a_top_level_name() -> None:
    assert issubclass(MappingError, PydiggsError)
    assert issubclass(MappingError, ValueError)
    assert "MappingError" not in pydiggs.__all__


def test_a_mapping_file_is_bounded_in_size(tmp_path: Path) -> None:
    big = HEADER + HOLES + "# " + "x" * fmt.MAX_MAPPING_BYTES + "\n"
    with pytest.raises(MappingError, match="larger than 1048576 bytes"):
        mapping.load(big)
    path = tmp_path / "big.toml"
    path.write_text(big, encoding="utf-8")
    with pytest.raises(MappingError, match="larger than 1048576 bytes"):
        mapping.load(path)


def test_a_file_that_cannot_be_read_is_a_mapping_error(tmp_path: Path) -> None:
    with pytest.raises(MappingError, match="is not a file"):
        mapping.load(tmp_path / "missing.toml")
    with pytest.raises(MappingError, match="is not a file"):
        mapping.load(tmp_path)
    binary = tmp_path / "binary.toml"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(MappingError, match="not UTF-8"):
        mapping.load(binary)


# --- templates ----------------------------------------------------------------------


def test_a_template_substitutes_plain_columns_only() -> None:
    template = fmt.parse_template("{hole}-SPT-{seq}", "here")
    assert template.columns == ("hole", "seq")
    assert template.render({"hole": "BH1", "seq": "2"}) == "BH1-SPT-2"
    assert template.render({"hole": "BH1", "seq": None}) is None
    assert fmt.parse_template("{{{a b}}}", "here").render({"a b": "x"}) == "{x}"


@pytest.mark.parametrize(
    "text",
    [
        "{a.b}",
        "{a[0]}",
        "{a!r}",
        "{a:>10}",
        "{a.__class__.__mro__}",
        "{0.__class__}",
        "{}",
        "{ }",
        "{a",
        "a}",
        "{a{b}}",
        "no column",
    ],
)
def test_a_template_that_is_more_than_substitution_is_refused(text: str) -> None:
    with pytest.raises(MappingError, match=r"^tables\[1\]\.columns\.key\.template: "):
        load(HOLES.replace('key = "id"', f"key = {{ template = '{text}' }}"))


def test_a_template_never_formats_a_value() -> None:
    class Loud:
        def __format__(self, spec: str) -> str:
            raise AssertionError("formatted")

        def __str__(self) -> str:
            return "{other}"

    loaded = load(HOLES.replace('key = "id"', 'key = { template = "{id}-{id}" }'))
    applied = loaded.apply({"hole": [{"id": Loud(), "other": "secret"}]})
    # The value's text is used as it is: a brace in it is not a second substitution.
    assert applied.tables["features"] == [{"key": "{other}-{other}"}]


# --- applying -----------------------------------------------------------------------

FULL = """
[units]
metres = "m"
feet = "ft"

[codes.sampler]
code_space = "https://diggsml.org/def/codes/DIGGS/0.1/sampleType.xml"
[codes.sampler.map]
U = "open_tube"
P = { code = "piston", match = "close" }
B = { code = "bulk", match = "broader" }
G = { code = "grab", match = "narrower" }

[codes.rig]
[codes.rig.map]
CP = "cable percussion"

[[tables]]
source = "hole"
target = "features"
[tables.columns]
key = "id"
name = "label"
total_depth = "depth"
total_depth_uom = { from = "unit", units = true }
depth_uom = { from = "unit", units = true }
[[tables.params]]
name = "boreholeType"
from = "rig"
codes = "rig"
[[tables.params]]
name = "holeDiameter"
from = "dia"
uom_from = "dia_unit"

[[tables]]
source = "smp"
target = "samples"
[tables.columns]
key = { template = "{hole}-S{no}" }
feature = "hole"
top = "from"
base = "to"
depth_uom = { from = "unit", units = true }
type = { from = "kind", codes = "sampler" }

[[tables]]
source = "cpt"
target = "measurements"
[tables.columns]
key = "sounding"
feature = "hole"
procedure = { value = "StaticConePenetrationTest" }

[[tables]]
source = "cpt_data"
target = "results_StaticConePenetrationTest"
[tables.columns]
measurement = "sounding"
position = "z"
qc = { from = "qc_mpa", property = "tip_resistance", uom = "MPa" }
sleeve_friction = { from = "fs", uom = "kPa" }
"""


def source() -> dict[str, list[dict[str, Any]]]:
    return {
        "hole": [{"id": "B1", "label": "Boring 1", "depth": 12.5, "unit": "metres"}],
        "smp": [{"hole": "B1", "no": 1, "from": 2, "to": 2.5, "unit": "metres", "kind": "U"}],
        "cpt": [{"sounding": "C1", "hole": "B1"}],
        "cpt_data": [
            {"sounding": "C1", "z": 0.02, "qc_mpa": 2.5, "fs": 21},
            {"sounding": "C1", "z": 0.04, "qc_mpa": 2.7, "fs": 23},
        ],
    }


def test_apply_copies_renames_translates_and_builds_keys() -> None:
    applied = load(FULL).apply(source())
    assert applied.findings == ()
    assert applied.lossless
    assert applied.tables == {
        "features": [
            {
                "key": "B1",
                "name": "Boring 1",
                "total_depth": 12.5,
                "total_depth_uom": "m",
                "depth_uom": "m",
            }
        ],
        "samples": [
            {
                "key": "B1-S1",
                "feature": "B1",
                "top": 2,
                "base": 2.5,
                "depth_uom": "m",
                "type": "open_tube",
                "type_code_space": f"{CODES}/sampleType.xml",
            }
        ],
        "measurements": [{"key": "C1", "feature": "B1", "procedure": "StaticConePenetrationTest"}],
        "results_StaticConePenetrationTest": [
            {"measurement": "C1", "position": 0.02, "qc": 2.5, "sleeve_friction": 21},
            {"measurement": "C1", "position": 0.04, "qc": 2.7, "sleeve_friction": 23},
        ],
        "result_columns": [
            {
                "procedure": "StaticConePenetrationTest",
                "column": "qc",
                "property": "tip_resistance",
                "uom": "MPa",
            },
            {
                "procedure": "StaticConePenetrationTest",
                "column": "sleeve_friction",
                "uom": "kPa",
            },
        ],
    }
    assert tables.check(applied.tables)[-1:] == tables.to_model(applied.tables).findings[-1:]
    assert not {f.rule_id for f in tables.check(applied.tables)} & {
        "tables.unknown-property",
        "tables.unknown-unit",
        "tables.unknown-code",
    }


def test_apply_takes_rows_from_iterators_and_missing_tables_are_empty() -> None:
    applied = load(FULL).apply({"hole": iter(source()["hole"])})
    assert list(applied.tables) == ["features"]
    assert applied.findings == ()


def test_a_blank_source_cell_makes_no_target_cell_and_a_blank_row_no_row() -> None:
    rows = [
        {"id": "B1", "label": "", "depth": None, "unit": "  "},
        {"id": "", "label": None, "depth": float("nan"), "unit": None},
    ]
    applied = load(FULL).apply({"hole": rows})
    assert applied.tables == {"features": [{"key": "B1"}]}
    assert applied.findings == ()


def test_a_source_column_the_mapping_does_not_read_is_named() -> None:
    data = source()
    data["hole"][0] |= {"driller": "J. Smith", "rig_no": None}
    data["hole"].append({"id": "B2", "unit": "metres", "driller": "K. Jones"})
    data["weather"] = [{"day": "Monday", "sky": "grey"}]
    data["empty"] = [{"a": ""}]
    findings = load(FULL).apply(data).findings
    assert found(findings) == [
        ("tables.mapping-unmapped-source", "hole/1/driller"),
        ("tables.mapping-unmapped-source", "weather"),
    ]
    assert all(f.step is Step.TABLES and f.severity is Severity.WARNING for f in findings)
    assert "'driller'" in findings[0].message
    assert "(2 rows)" in findings[0].message
    assert "'weather'" in findings[1].message


def test_a_code_with_no_entry_is_carried_as_text_and_named() -> None:
    data = source()
    data["smp"][0]["kind"] = "W"
    data["hole"][0]["rig"] = "RC"
    applied = load(FULL).apply(data)
    assert found(applied.findings) == [
        ("tables.mapping-unmapped-code", "hole/1/rig"),
        ("tables.mapping-unmapped-code", "smp/1/kind"),
    ]
    assert applied.findings[0].severity is Severity.WARNING
    assert "'W'" in applied.findings[1].message
    sample = applied.tables["samples"][0]
    assert sample["type"] == "W"
    assert "type_code_space" not in sample
    assert applied.tables["params"] == [{"node": "B1", "name": "boreholeType", "value": "RC"}]
    assert not applied.lossless


@pytest.mark.parametrize(
    ("code", "written", "match"),
    [("P", "piston", "close"), ("B", "bulk", "broader"), ("G", "grab", "narrower")],
)
def test_a_code_mapped_by_a_match_that_is_not_exact_is_a_note(
    code: str, written: str, match: str
) -> None:
    data = source()
    data["smp"][0]["kind"] = code
    applied = load(FULL).apply(data)
    (finding,) = applied.findings
    assert (finding.rule_id, finding.path) == ("tables.mapping-code-not-exact", "smp/1/kind")
    assert finding.severity is Severity.INFO
    assert f"a {match} match" in finding.message
    assert applied.tables["samples"][0]["type"] == written
    assert applied.tables["samples"][0]["type_code_space"] == f"{CODES}/sampleType.xml"
    assert applied.lossless


def test_a_code_map_with_no_code_space_only_renames() -> None:
    data = source()
    data["hole"][0]["rig"] = "CP"
    applied = load(FULL).apply(data)
    assert applied.findings == ()
    assert applied.tables["params"] == [
        {"node": "B1", "name": "boreholeType", "value": "cable percussion"}
    ]


def test_a_unit_with_no_entry_is_left_out_with_its_values_and_never_converted() -> None:
    data = source()
    data["hole"][0]["unit"] = "fathoms"
    data["smp"][0]["unit"] = "yards"
    applied = load(FULL).apply(data)
    assert found(applied.findings) == [
        ("tables.mapping-unknown-unit", "hole/1/unit"),
        ("tables.mapping-unknown-unit", "smp/1/unit"),
    ]
    assert applied.findings[0].severity is Severity.WARNING
    assert "'fathoms'" in applied.findings[0].message
    # A feature's depth unit is passed on as given, so the tables conversion refuses the
    # feature instead of reading its depths in metres.
    assert applied.tables["features"] == [{"key": "B1", "name": "Boring 1", "depth_uom": "fathoms"}]
    made = tables.to_model(applied.tables)
    assert [f.path for f in made.findings if f.rule_id == "tables.unknown-unit"] == [
        "features/1/depth_uom"
    ]
    assert made.model.features == ()
    # A depth in an unknown unit is left out too: it is not read in the feature's unit.
    assert applied.tables["samples"][0] == {
        "key": "B1-S1",
        "feature": "B1",
        "type": "open_tube",
        "type_code_space": f"{CODES}/sampleType.xml",
    }


def test_a_unit_is_renamed_and_the_number_is_carried_as_given() -> None:
    data = source()
    data["hole"][0] |= {"depth": "41.00", "unit": "feet"}
    applied = load(FULL).apply(data)
    assert applied.tables["features"][0]["total_depth"] == "41.00"
    assert applied.tables["features"][0]["total_depth_uom"] == "ft"


def test_parameters_take_a_unit_from_a_column_through_the_unit_table() -> None:
    data = source()
    data["hole"][0] |= {"dia": 150, "dia_unit": "feet"}
    data["hole"].append({"id": "B2", "unit": "metres", "dia": 6, "dia_unit": "inches"})
    data["hole"].append({"id": "B3", "unit": "metres", "dia": 100})
    applied = load(FULL).apply(data)
    assert applied.tables["params"] == [
        {"node": "B1", "name": "holeDiameter", "value": 150, "uom": "ft"},
        {"node": "B3", "name": "holeDiameter", "value": 100},
    ]
    assert found(applied.findings) == [("tables.mapping-unknown-unit", "hole/2/dia_unit")]


def test_a_parameters_table_row_with_an_unknown_unit_is_left_out() -> None:
    loaded = load(
        HOLES
        + """
[[tables]]
source = "facts"
target = "params"
[tables.columns]
node = "hole"
name = "what"
value = "amount"
uom = { from = "unit", units = true }
"""
    )
    rows = [{"hole": "B1", "what": "holeDiameter", "amount": 6, "unit": "inches"}]
    applied = loaded.apply({"hole": [{"id": "B1"}], "facts": rows})
    assert "params" not in applied.tables
    assert found(applied.findings) == [("tables.mapping-unknown-unit", "facts/1/unit")]


def test_messages_show_source_text_cut_short_and_without_control_characters() -> None:
    data = source()
    data["smp"][0]["kind"] = "W\u202e" + "x" * 300
    data["hole"][0]["extra\u202ecolumn"] = 1
    for finding in load(FULL).apply(data).findings:
        assert "\u202e" not in finding.message
        assert "\u202e" not in (finding.path or "")
        assert len(finding.message) < 400


# --- the static check ---------------------------------------------------------------


def test_a_complete_mapping_has_no_finding() -> None:
    assert mapping.check(load(FULL)) == ()


@pytest.mark.parametrize(
    ("body", "path", "says"),
    [
        ("", "tables", "No entry fills the profile table features"),
        (
            HOLES.replace('"features"', '"boreholes"'),
            "tables[1].target",
            "'boreholes' is not a table",
        ),
        (HOLES + "depth_top = 'd'\n", "tables[1].columns.depth_top", "not a column of the profile"),
        (HOLES.replace('key = "id"', 'name = "id"'), "tables[1].columns.key", "requires `key`"),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'results_SptTest'\ncolumns = {measurement = 'm'}\n",
            "tables[2].target",
            "'SptTest' is not a DIGGS 3.0.0 procedure element",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'results_'\ncolumns = {measurement = 'm'}\n",
            "tables[2].target",
            "'results_' is not a table of the profile",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'results_DrivenPenetrationTest'\ncolumns = {n_value = 'n'}\n",
            "tables[2].columns.measurement",
            "requires `measurement`",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'measurements'\n"
            + "columns = {key = 'k', feature = 'h', procedure = {value = 'SPT'}}\n",
            "tables[2].columns.procedure",
            "'SPT' is not a DIGGS 3.0.0 element",
        ),
        (
            HOLES + "element = { value = 'Hole' }\n",
            "tables[1].columns.element",
            "'Hole' is not a DIGGS 3.0.0 element",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'links'\n"
            + "columns = {source = 'a', relation = 'r'}\n[[tables.params]]\nname = 'n'\nvalue = 1\n",
            "tables[2].params",
            "get no `key`",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'results_DrivenPenetrationTest'\n"
            + "columns = {measurement = 'm', n_value = 'n'}\n[[tables.params]]\nname = 'n'\nvalue = 1\n",
            "tables[2].params",
            "put it on the entry that fills `measurements`",
        ),
    ],
)
def test_a_target_the_profile_lacks_is_an_error(body: str, path: str, says: str) -> None:
    findings = mapping.check(load(body))
    matching = [f for f in findings if f.path == path]
    assert [f.rule_id for f in matching] == ["tables.mapping-unknown-target"]
    assert matching[0].severity is Severity.ERROR
    assert matching[0].step is Step.TABLES
    assert says in matching[0].message


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ("[units]\nmetres = 'metre'\n" + HOLES, "units.metres"),
        (HOLES + "depth_uom = { value = 'km' }\n", "tables[1].columns.depth_uom"),
        (HOLES + "total_depth_uom = { value = 'kg' }\n", "tables[1].columns.total_depth_uom"),
        (
            HOLES + "[[tables.params]]\nname = 'holeDiameter'\nfrom = 'd'\nuom = 'inches'\n",
            "tables[1].params[1].uom",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'results_StaticConePenetrationTest'\n"
            + "columns = {measurement = 'm', tip_resistance = {from = 'q', uom = 'm'}}\n",
            "tables[2].columns.tip_resistance",
        ),
        (
            HOLES
            + "[[tables]]\nsource = 's'\ntarget = 'results_StaticConePenetrationTest'\n"
            + "columns = {measurement = 'm', depth_uom = {value = 'km'}}\n",
            "tables[2].columns.depth_uom",
        ),
    ],
)
def test_a_diggs_unit_that_is_not_one_is_named(body: str, path: str) -> None:
    assert found(mapping.check(load(body))) == [("tables.unknown-unit", path)]


@pytest.mark.parametrize(
    ("column", "says", "rule"),
    [
        ("blows = 'n'", "'blows' is not a property", "tables.unknown-property"),
        ("tip_resistance = 'q'", "needs a unit", "tables.unknown-unit"),
        ("n_value = { from = 'n', uom = 'm' }", "has no unit", "tables.unknown-unit"),
        (
            "n = { from = 'n', property = 'spt_n' }",
            "'spt_n' is not a property",
            "tables.unknown-property",
        ),
    ],
)
def test_a_result_column_that_disagrees_with_the_property_dictionary_is_named(
    column: str, says: str, rule: str
) -> None:
    body = (
        HOLES
        + "[[tables]]\nsource = 's'\ntarget = 'results_DrivenPenetrationTest'\n"
        + f"[tables.columns]\nmeasurement = 'm'\nrow = 'i'\n{column}\n"
    )
    (finding,) = mapping.check(load(body))
    name = column.split(" ", 1)[0]
    assert (finding.rule_id, finding.path) == (
        rule,
        f"tables[2].columns.{name}",
    )
    assert says in finding.message


def test_a_diggs_code_that_is_not_in_its_code_list_is_named() -> None:
    body = FULL.replace('U = "open_tube"', 'U = "tube"')
    (finding,) = mapping.check(load(body))
    assert (finding.rule_id, finding.path) == ("tables.unknown-code", "codes.sampler.map.U")
    assert "'tube' is not a code of the code list" in finding.message


def test_a_code_map_used_for_a_value_its_code_list_is_not_for_is_named() -> None:
    body = (
        f"[codes.uscs]\ncode_space = '{CODES}/astmD2487.xml'\n[codes.uscs.map]\nCL = 'CL'\n"
        + HOLES
        + "[[tables]]\nsource = 's'\ntarget = 'layers'\n[tables.columns]\nkey = 'k'\n"
        + "classification = { from = 'c', codes = 'uscs' }\n"
    )
    (finding,) = mapping.check(load(body))
    assert (finding.rule_id, finding.path) == ("tables.unknown-code", "codes.uscs.map.CL")
    assert "is a code for classificationCode, not for classificationSymbol" in finding.message
    assert mapping.check(load(body.replace("astmD2487", "grp-astmD2487"))) == ()


def test_a_constant_code_beside_a_constant_code_space_is_checked() -> None:
    body = (
        HOLES
        + "[[tables]]\nsource = 's'\ntarget = 'samples'\n[tables.columns]\nkey = 'k'\n"
        + f"type = {{ value = 'tube' }}\ntype_code_space = {{ value = '{CODES}/sampleType.xml' }}\n"
    )
    assert found(mapping.check(load(body))) == [("tables.unknown-code", "tables[2].columns.type")]


def test_a_code_list_that_is_not_bundled_is_not_checked() -> None:
    body = FULL.replace(f"{CODES}/sampleType.xml", "https://example.com/ours.xml")
    assert mapping.check(load(body.replace('U = "open_tube"', 'U = "anything"'))) == ()


@pytest.mark.parametrize("rule", MAPPING_RULES)
def test_the_mapping_rules_are_catalogued_with_guidance(rule: str) -> None:
    info = explain_rule(rule)
    assert info.known
    assert info.step == "tables"
    assert info.severities == (_report.RULES[rule],)
    assert info.guidance is not None


# --- the schema ---------------------------------------------------------------------


def as_json(text: str) -> dict[str, Any]:
    document = tomllib.loads(text)
    if "date" in document:
        document["date"] = str(document["date"])
    return document


def test_the_schema_is_a_valid_schema_and_ships_with_the_package() -> None:
    schema = mapping.mapping_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"].endswith("/diggs-mapping-1.schema.json")
    assert resources.files("pydiggs.mapping").joinpath("diggs-mapping-1.schema.json").is_file()
    assert set(schema["properties"]) == set(fmt._TOP_KEYS)
    assert tuple(schema["properties"]["method"]["enum"]) == fmt.METHODS


@pytest.mark.parametrize(
    "text",
    [HEADER + HOLES, HEADER + FULL, (EXAMPLE / "demo.toml").read_text(encoding="utf-8")],
    ids=["small", "full", "example"],
)
def test_what_loads_is_valid_by_the_schema(text: str) -> None:
    mapping.load(text)
    jsonschema.Draft202012Validator(mapping.mapping_schema()).validate(as_json(text))


@pytest.mark.parametrize(
    "text",
    [
        HEADER.replace('"1.0"', '"2.0"'),
        HEADER.replace('"manual"', '"by magic"'),
        HEADER + "colour = 1\n",
        HEADER + "[[tables]]\nsource = 'a'\ntarget = 'features'\n",
        HEADER + HOLES + "name = { from = 'a', value = 'b' }\n",
        HEADER + HOLES + "name = { units = true }\n",
        HEADER + HOLES + "name = { from = 'a', eval = 'x' }\n",
        HEADER + HOLES + "[[tables.params]]\nvalue = 'SPT'\n",
        HEADER + HOLES + "[[tables.params]]\nname = 'a'\nfrom = 'b'\nuom = 'm'\nuom_from = 'u'\n",
        HEADER + "[codes.c.map]\nA = { code = 'a', match = 'similar' }\n",
    ],
)
def test_what_the_schema_refuses_does_not_load(text: str) -> None:
    validator = jsonschema.Draft202012Validator(mapping.mapping_schema())
    assert list(validator.iter_errors(as_json(text)))
    with pytest.raises(MappingError):
        mapping.load(text)


# --- scaffold and coverage ----------------------------------------------------------

SCHEMA = {
    "holes": ["hole_id", "hole_name", "easting", "northing", "final_depth", "rig"],
    "strata": ["hole_id", "depth_top", "depth_base", "description", "uscs"],
    "spt": ["hole_id", "depth", "n"],
}


def test_a_scaffold_loads_and_every_guess_is_marked() -> None:
    text = mapping.scaffold(SCHEMA, name="logdb")
    loaded = mapping.load(text)
    assert (loaded.name, loaded.method) == ("logdb", "generated")
    assert [(t.source, t.target) for t in loaded.tables] == [
        ("holes", "features"),
        ("strata", "layers"),
    ]
    assert {k: v.source for k, v in loaded.tables[1].columns.items()} == {
        "feature": "hole_id",
        "top": "depth_top",
        "base": "depth_base",
        "description": "description",
    }
    live = [
        line
        for line in text.splitlines()
        if line.strip() and not line.startswith(("#", "[")) and " = " in line
    ]
    body = live[live.index('profile_version = "1.0-beta.1"') + 1 :]
    # ``source =`` names a table of the schema itself; every other filled line is a guess.
    guesses = [line for line in body if not line.startswith("source = ")]
    assert guesses
    assert all(line.endswith("# guess") for line in guesses)
    assert '# not mapped yet: "rig"' in text
    assert '# source = "spt"' in text


def test_a_scaffold_with_nothing_to_guess_still_loads() -> None:
    loaded = mapping.load(mapping.scaffold({"t1": ["a", "b"], "empty": []}))
    assert loaded.tables == ()
    assert found(mapping.check(loaded)) == [("tables.mapping-unknown-target", "tables")]


def test_a_scaffold_cannot_be_broken_out_of_by_a_name() -> None:
    hostile = {
        'holes"\n[[tables]]\nsource = "x': ["hole_id", 'id"\nmethod = "manual', "a\nb = 1"],
        "spt\nname = 'evil'": ["x\ny"],
    }
    text = mapping.scaffold(hostile, name='n"\nmethod = "manual')
    loaded = mapping.load(text)
    assert loaded.method == "generated"
    assert loaded.name == 'n"\nmethod = "manual' or loaded.name.startswith("n")
    assert all(line.startswith("#") or "\n" not in line for line in text.splitlines())
    assert len(loaded.tables) <= 1


def test_a_scaffold_refuses_a_schema_that_is_not_one() -> None:
    with pytest.raises(MappingError, match="list of names, not text"):
        mapping.scaffold({"holes": "hole_id"})
    with pytest.raises(MappingError, match="mapping of table name"):
        mapping.scaffold(["holes"])  # type: ignore[arg-type]


def test_coverage_lists_what_is_read_and_what_is_filled() -> None:
    loaded = mapping.load(mapping.scaffold(SCHEMA))
    covered = mapping.coverage(loaded, SCHEMA | {"holes": [*SCHEMA["holes"][1:]]})
    assert covered.source_mapped["strata"] == ("hole_id", "depth_top", "depth_base", "description")
    assert covered.source_unmapped == {
        "holes": ("rig",),
        "strata": ("uscs",),
        "spt": ("hole_id", "depth", "n"),
    }
    assert covered.source_missing == {"holes": ("hole_id",)}
    assert covered.profile_filled["layers"] == ("feature", "top", "base", "description")
    assert "key" in covered.profile_empty["layers"]
    assert covered.source_percent == round(100 * 8 / 13, 1)
    assert 0 < covered.profile_percent < 100
    assert covered.to_dict()["source_unmapped"]["holes"] == ["rig"]


def test_coverage_of_nothing_is_complete() -> None:
    covered = mapping.coverage(load(""), {})
    assert (covered.source_percent, covered.profile_percent) == (100.0, 100.0)


def test_a_source_schema_from_csv_headers_and_from_a_cursor(tmp_path: Path) -> None:
    (tmp_path / "holes.csv").write_text('hole_id,"hole, name"\nBH1,x\n', encoding="utf-8")
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a table", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    assert mapping.schema_from_csv(tmp_path) == {"empty": [], "holes": ["hole_id", "hole, name"]}
    rows = mapping.read_source(tmp_path)
    assert list(rows["holes"]) == list(rows["holes"]) == [{"hole_id": "BH1", "hole, name": "x"}]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE holes (hole_id TEXT, easting REAL)")
    cursor = db.execute("SELECT * FROM holes")
    assert mapping.columns_from_cursor(cursor) == ["hole_id", "easting"]
    with pytest.raises(MappingError, match="no result set"):
        mapping.columns_from_cursor(db.cursor())
    with pytest.raises(MappingError, match="is not a folder"):
        mapping.schema_from_csv(tmp_path / "missing")
    with pytest.raises(MappingError, match="is not a folder"):
        mapping.read_source(tmp_path / "holes.csv")


def test_a_csv_link_out_of_the_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n1\n", encoding="utf-8")
    folder = tmp_path / "export"
    folder.mkdir()
    try:
        (folder / "holes.csv").symlink_to(outside)
    except OSError:  # pragma: no cover - a platform without symlinks
        pytest.skip("symlinks are not available")
    with pytest.raises(MappingError, match="leaves the folder"):
        mapping.read_source(folder)
    with pytest.raises(MappingError, match="leaves the folder"):
        mapping.schema_from_csv(folder)


def test_cells_beyond_a_csv_header_are_reported_not_lost(tmp_path: Path) -> None:
    (tmp_path / "hole.csv").write_text("id\nB1,stray\n", encoding="utf-8")
    applied = load().apply(mapping.read_source(tmp_path))
    assert applied.tables == {"features": [{"key": "B1"}]}
    assert [f.rule_id for f in applied.findings] == ["tables.mapping-unmapped-source"]


# --- the worked example -------------------------------------------------------------


def test_the_worked_example_checks_and_round_trips() -> None:
    loaded = mapping.load(EXAMPLE / "demo.toml")
    assert (loaded.method, loaded.licence, loaded.date) == ("manual", "Apache-2.0", "2026-11-21")
    assert mapping.check(loaded) == ()
    covered = mapping.coverage(loaded, mapping.schema_from_csv(EXAMPLE))
    assert covered.source_percent == 100.0
    assert covered.source_unmapped == covered.source_missing == {}
    applied = loaded.apply(mapping.read_source(EXAMPLE))
    assert found(applied.findings) == [("tables.mapping-code-not-exact", "strata/4/uscs")]
    assert tables.check(applied.tables) == ()
    checked = tables.round_trip(applied.tables)
    assert checked.ok
    assert checked.findings == ()
    assert checked.differences == ()
    model = pydiggs.read_bytes(checked.diggs)
    assert [feature.name for feature in model.features] == ["Borehole 1", "Borehole 2"]
    assert len(model.samples) == 3
    assert len(model.measurements) == 5
    assert sum(len(observation.layers) for observation in model.observations) == 5


def test_the_worked_example_is_synthetic_and_small() -> None:
    files = sorted(path.name for path in EXAMPLE.iterdir())
    assert files == [
        "demo.toml",
        "holes.csv",
        "project.csv",
        "samples.csv",
        "spt.csv",
        "strata.csv",
    ]
    assert "synthetic" in (EXAMPLE / "project.csv").read_text(encoding="utf-8")
    assert all(path.stat().st_size < 8192 for path in EXAMPLE.iterdir())


# --- the property -------------------------------------------------------------------

NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8)
WORDS = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=10,
)
NUMBERS = st.one_of(
    st.integers(0, 5000),
    st.integers(0, 500_000).map(lambda n: n / 100),
    st.integers(0, 500_000).map(lambda n: f"{n / 100:.2f}"),
)


@st.composite
def exports(draw: st.DrawFn) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """A source table with made-up column names, and a mapping that sends each one on."""
    table = draw(NAMES)
    key, name, depth, east, north, note = draw(st.lists(NAMES, min_size=6, max_size=6, unique=True))
    keys = draw(st.lists(WORDS, min_size=1, max_size=5, unique_by=str.lower))
    rows = [
        {
            key: f"H{given}",
            name: draw(WORDS),
            depth: draw(NUMBERS.filter(lambda n: float(n) > 0)),  # DIGGS: a positive depth
            east: draw(st.integers(100_000, 600_000)),
            north: draw(st.integers(100_000, 900_000).map(lambda n: n + 0.5)),
            note: draw(WORDS),
        }
        for given in keys
    ]
    text = (
        HEADER
        + f"""
[[tables]]
source = "{table}"
target = "features"
[tables.columns]
key = "{key}"
name = "{name}"
total_depth = "{depth}"
x = "{east}"
y = "{north}"
description = "{note}"
project = {{ value = "PROJECT" }}
crs = {{ value = "{CRS}" }}
depth_uom = {{ value = "m" }}

[[tables]]
source = "{table}"
target = "projects"
columns = {{ key = {{ value = "PROJECT" }}, name = "{name}" }}

[[tables]]
source = "{table}"
target = "document"
columns = {{ creation_date = {{ value = "2026-11-21" }}, description = "{note}" }}
"""
    )
    return text, {table: rows}


def same(sent: Any, got: Any) -> bool:
    if str(sent) == str(got):
        return True
    try:
        return float(sent) == float(got)
    except (TypeError, ValueError):
        return False


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(exports())
def test_a_one_to_one_mapping_loses_nothing(
    export: tuple[str, dict[str, list[dict[str, Any]]]],
) -> None:
    text, data = export
    loaded = mapping.load(text)
    assert mapping.check(loaded) == ()
    applied = loaded.apply(data)
    assert applied.findings == ()
    # One project and one document row are enough: keep the first of each.
    shaped = dict(applied.tables)
    shaped["projects"] = shaped["projects"][:1]
    shaped["document"] = shaped["document"][:1]
    checked = tables.round_trip(shaped)
    assert checked.ok, (checked.findings, checked.differences)
    back = tables.from_model(pydiggs.read_bytes(checked.diggs))
    returned = {row["key"]: row for row in back.rows("features")}
    (rows,) = data.values()
    for row in rows:
        key = next(value for value in row.values() if str(value).startswith("H"))
        got = list(returned[checked_key(key, returned)].values())
        for value in row.values():
            assert any(same(value, other) for other in got), (value, got)


def checked_key(key: str, returned: dict[str, Any]) -> str:
    assert key in returned
    return key


# --- docstrings ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "pydiggs.mapping",
        "pydiggs.mapping._format",
        "pydiggs.mapping._apply",
        "pydiggs.mapping._lint",
        "pydiggs.mapping._scaffold",
    ],
)
def test_docstring_examples_run(module: str) -> None:
    result = doctest.testmod(importlib.import_module(module))
    assert result.attempted > 0
    assert result.failed == 0


def test_every_public_name_has_a_docstring_with_an_example() -> None:
    for name in mapping.__all__:
        if name == "MAPPING_FORMAT_VERSION":
            continue
        documented = getattr(mapping, name).__doc__ or ""
        assert ">>>" in documented, name
