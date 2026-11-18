"""The diggs-diff document (1.0-beta.1), its JSON Schema and the text renderer."""

from __future__ import annotations

import dataclasses
import json
from importlib import resources

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pydiggs
from pydiggs import diff
from pydiggs.model import Code

from ._helpers import (
    ANNOTATED,
    close_schema,
    column,
    corpus_paths,
    make_feature,
    make_model,
    make_test,
    read_corpus,
    renamed,
    schema_errors,
    small_models,
    table,
)

URL = "https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-diff-1.0-beta.1.schema.json"


def _sample_diff() -> diff.Diff:
    old = make_model(
        features=(make_feature("f1", "B-1", params={"a": "1"}), make_feature("f2", "B-2", at=2)),
        measurements=(
            make_test("t1", "T", "f1", results=(table(column("n", 1.0, 2.0, key="n")),)),
        ),
    )
    new = make_model(
        features=(
            make_feature("f1", "B-1", params={"a": "2", "b": Code(value="x", code_space="c")}),
        ),
        measurements=(
            make_test("t1", "T", "f1", results=(table(column("n", 1.0, 3.0, key="n")),)),
        ),
    )
    return diff.compare(old, new)


# --- the schema ----------------------------------------------------------------------


def test_the_schema_is_a_valid_published_contract():
    schema = diff.diff_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"] == URL == diff.DIFF_SCHEMA_URL
    assert schema["title"] == "diggs-diff 1.0 (beta 1)"
    assert schema["properties"]["schema_version"]["pattern"] == r"^1\.[0-9]+(-beta\.[0-9]+)?$"
    again = diff.diff_schema()
    again["title"] = "mutated"
    assert diff.diff_schema()["title"] == "diggs-diff 1.0 (beta 1)"  # a fresh dict each call
    packaged = resources.files("pydiggs.diff").joinpath("diggs-diff-1.0-beta.1.schema.json")
    assert json.loads(packaged.read_text("utf-8")) == schema


def test_the_open_schema_accepts_unknown_fields_and_the_closed_variant_refuses_them():
    document = _sample_diff().to_dict()
    document["x-extra"] = 1
    open_v = jsonschema.Draft202012Validator(diff.diff_schema())
    assert open_v.is_valid(document)
    assert schema_errors(document)
    nested = _sample_diff().to_dict()
    nested["changes"][0]["x-extra"] = 1
    assert schema_errors(nested)
    assert close_schema(diff.diff_schema())["unevaluatedProperties"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(schema_version="2.0"),
        lambda d: d.pop("changes"),
        lambda d: d.update(format="diggs-findings"),
        lambda d: d["changes"][0].update(op="mutated"),
        lambda d: d["changes"][0]["values"][0].update(op="mutated"),
        lambda d: d["summary"]["features"].pop("added"),
        lambda d: d["summary"]["features"].update(added=-1),
        lambda d: d["changes"][0].update(matched_by="guess"),
        lambda d: d["options"].update(match="fuzzy"),
        lambda d: d.pop("renamed"),
        lambda d: d.update(renamed=-1),
    ],
    ids=[
        "major-version",
        "missing-changes",
        "wrong-format",
        "bad-op",
        "bad-value-op",
        "summary-short",
        "negative-count",
        "bad-matched-by",
        "bad-match",
        "missing-renamed",
        "negative-renamed",
    ],
)
def test_the_schema_refuses_what_it_does_not_define(change):
    document = _sample_diff().to_dict()
    assert schema_errors(document) == []
    change(document)
    assert schema_errors(document)


# --- every output validates ----------------------------------------------------------


def test_a_diff_document_has_the_documented_shape_and_validates():
    result = _sample_diff()
    document = result.to_dict()
    assert schema_errors(document) == []
    assert document["$schema"] == URL
    assert (document["format"], document["schema_version"]) == ("diggs-diff", "1.0-beta.1")
    assert document["tool"] == {"name": "pydiggs", "version": pydiggs.__version__}
    assert document["identical"] is False and document["complete"] is True
    assert document["changes"][0]["collection"] == "features"
    assert list(document) == [
        "$schema",
        "format",
        "schema_version",
        "tool",
        "identical",
        "complete",
        "limits_hit",
        "old",
        "new",
        "options",
        "summary",
        "value_changes",
        "reformatted",
        "renamed",
        "changes",
        "notes",
    ]
    first = document["changes"][0]
    assert list(first) == [
        "op",
        "collection",
        "element",
        "name",
        "old_key",
        "new_key",
        "matched_by",
        "truncated",
        "values",
    ]
    assert list(first["values"][0]) == ["op", "path", "old", "new", "at", "truncated"]


@pytest.mark.parametrize("path", [p for p in corpus_paths() if read_corpus(p) is not None][::4])
def test_the_diff_of_corpus_files_validates_against_the_closed_schema(path):
    model = read_corpus(path)
    assert model is not None
    assert schema_errors(diff.compare(model, model).to_dict()) == []
    other = renamed(model)
    for result in (
        diff.compare(model, other),
        diff.compare(model, other, show_renamed=True),
        diff.compare(model, other, match="gml-id"),
    ):
        assert schema_errors(result.to_dict()) == []
        assert diff.Diff.from_dict(json.loads(result.to_json())) == result


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.data())
def test_documents_of_edited_models_validate_and_round_trip(data):
    from .test_properties import _edit

    model = data.draw(st.sampled_from(small_models()))
    edited, _ = _edit(data, model)
    result = diff.compare(model, edited, show_reformatted=True, show_renamed=True)
    text = result.to_json()
    assert schema_errors(json.loads(text)) == []
    assert diff.Diff.from_dict(json.loads(text)) == result
    assert result.to_json() == text  # the same diff always gives the same bytes


def test_the_json_is_sorted_compact_by_default_and_never_holds_nan():
    result = _sample_diff()
    text = result.to_json()
    assert "\n" not in text
    assert result.to_json(indent=2).count("\n") > 10
    document = json.loads(text)
    assert list(document["summary"]) == sorted(document["summary"])
    nan = float("nan")
    q = pydiggs.model.Quantity(text="x", value=nan, uom="m")
    a = make_model(features=(make_feature("f1", "B", total_depth=q),))
    b = make_model(
        features=(make_feature("f1", "B", total_depth=dataclasses.replace(q, text="y", value=2.0)),)
    )
    json.loads(diff.compare(a, b).to_json())  # does not raise


def test_to_dict_is_json_serialisable_without_a_custom_encoder():
    json.dumps(_sample_diff().to_dict(), allow_nan=False)


def test_from_dict_refuses_another_major_version_and_non_objects():
    with pytest.raises(ValueError, match="schema_version"):
        diff.Diff.from_dict({**_sample_diff().to_dict(), "schema_version": "2.0"})
    with pytest.raises(ValueError, match="JSON object"):
        diff.Diff.from_dict([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="changes"):
        diff.Diff.from_dict({"schema_version": "1.0"})


def _renamed_pair():
    old = make_model(features=(make_feature("f1", "B-1"), make_feature("f2", "B-2", at=2)))
    new = make_model(features=(make_feature("g1", "B-1"), make_feature("g2", "B-2", at=2)))
    return old, new


@pytest.mark.parametrize("listed", [False, True])
def test_a_diff_with_renamed_ids_validates_and_round_trips(listed):
    result = diff.compare(*_renamed_pair(), show_renamed=listed)
    document = json.loads(result.to_json())
    assert schema_errors(document) == []
    assert (document["identical"], document["renamed"]) == (True, 2)
    assert [c["op"] for c in document["changes"]] == (["renamed"] * 2 if listed else [])
    for change in document["changes"]:
        assert (change["matched_by"], change["old_key"] != change["new_key"]) == ("natural", True)
        (value,) = change["values"]
        assert (value["op"], value["path"]) == ("renamed", "gml_id")
        assert (value["old"], value["new"]) == (change["old_key"], change["new_key"])
    again = diff.Diff.from_dict(document)
    assert again == result and again.renamed == 2


def test_from_dict_refuses_a_document_without_the_renamed_count():
    # diggs-diff 1.0 requires the count, and no document was ever written without it.
    document = _sample_diff().to_dict()
    del document["renamed"]
    with pytest.raises(ValueError, match="missing renamed"):
        diff.Diff.from_dict(document)
    with pytest.raises(ValueError, match="renamed"):
        diff.Diff.from_dict({**_sample_diff().to_dict(), "renamed": "2"})


# --- truncation and escaping (the files are untrusted) -------------------------------


def test_long_values_are_truncated_to_200_characters_and_flagged():
    big = "x" * 5000
    a = make_model(features=(make_feature("f1", "B", params={"p": big}),))
    b = make_model(features=(make_feature("f1", "B", params={"p": big + "y"}),))
    result = diff.compare(a, b)
    (value,) = [v for c in result.changes for v in c.values]
    assert len(value.old) == len(value.new) == 200
    assert value.truncated is True
    assert schema_errors(result.to_dict()) == []
    short = diff.compare(
        make_model(features=(make_feature("f1", "B", params={"p": "a"}),)),
        make_model(features=(make_feature("f1", "B", params={"p": "b"}),)),
    )
    assert [v.truncated for c in short.changes for v in c.values] == [False]


def test_a_huge_name_or_key_cannot_make_a_huge_document():
    name = "n" * 100_000
    a = make_model(features=(make_feature("f1", name),))
    b = make_model(features=(make_feature("f1", name + "z"),))
    result = diff.compare(a, b)
    assert len(result.to_json()) < 5_000
    (change,) = result.changes
    assert len(change.name or "") <= 200 and change.truncated is True
    assert schema_errors(result.to_dict()) == []


def test_text_escapes_control_characters_and_rich_markup():
    from rich.console import Console
    from rich.text import Text

    hostile = "[bold red]x[/bold red]\x1b[2J\x07\u202e\r\nend [link=http://evil]c[/link]"
    a = make_model(features=(make_feature("f1", "B", params={"p": "a"}),))
    b = make_model(features=(make_feature("f1", "B", params={"p": hostile}),))
    text = diff.compare(a, b).to_text()
    assert "\x1b" not in text and "\x07" not in text and "\r" not in text
    assert "\u202e" not in text
    assert "\\x1b" in text
    console = Console(record=True, width=200, force_terminal=False, color_system=None)
    console.print(text)  # markup on: nothing in the file text may be interpreted
    printed = console.export_text()
    assert "[bold red]x[/bold red]" in printed
    assert "[link=http://evil]" in printed
    assert isinstance(Text.from_markup(text), Text)


def test_markup_split_over_two_values_cannot_form_a_tag_in_to_text():
    from rich.console import Console
    from rich.text import Text

    # "[black on black" in one value and "]" in a later one: escaped one value at a time
    # by rich.markup.escape, neither is a tag, and the joined report would hold one that
    # hides everything between them.
    a = make_model(features=(make_feature("f1", "B", params={"p": "a", "q": "1"}),))
    b = make_model(
        features=(make_feature("f1", "B", params={"p": "[black on black", "q": "k]m2"}),)
    )
    text = diff.compare(a, b).to_text()
    assert "    params.p: a -> \\[black on black\n" in text
    assert "    params.q: 1 -> k]m2\n" in text
    assert Text.from_markup(text).spans == []  # no style: nothing was read as a tag
    console = Console(record=True, width=200, force_terminal=False, color_system=None)
    console.print(text)
    printed = console.export_text()
    assert "params.p: a -> [black on black" in printed
    assert "params.q: 1 -> k]m2" in printed
    # A bracket that opens no tag is left alone, and so is text with none.
    plain = make_model(features=(make_feature("f1", "B", params={"p": "[1] [A] 5 [", "q": "1"}),))
    assert "    params.p: a -> [1] [A] 5 [\n" in diff.compare(a, plain).to_text()


def test_text_says_what_changed_and_is_plain_when_nothing_did():
    same = diff.compare(ANNOTATED, ANNOTATED).to_text()
    assert "identical" in same.lower()
    text = _sample_diff().to_text()
    assert "features" in text and "f2" in text and "params.a" in text
    assert "removed" in text and "added" in text
    assert "1" in text and "2" in text
    assert text.endswith("\n")


def test_text_lists_reformatted_values_only_when_asked():
    a = make_model(
        features=(
            make_feature(
                "f1", "B", total_depth=pydiggs.model.Quantity(text="1.50", value=1.5, uom="ft")
            ),
        )
    )
    b = make_model(
        features=(
            make_feature(
                "f1", "B", total_depth=pydiggs.model.Quantity(text="1.5", value=1.5, uom="ft")
            ),
        )
    )
    quiet = diff.compare(a, b)
    assert "1.50" not in quiet.to_text()
    assert "reformatted" in quiet.to_text().lower()
    shown = diff.compare(a, b, show_reformatted=True)
    assert "1.50" in shown.to_text()


def test_text_says_the_files_are_identical_and_counts_the_renamed_ids():
    quiet = diff.compare(*_renamed_pair()).to_text()
    assert quiet.splitlines()[1:] == [
        "The two files are identical.",
        "features: 0 added, 0 removed, 0 changed, 2 unchanged",
        "2 record(s) have a different gml:id (matched by natural key; not a change); not listed.",
    ]
    shown = diff.compare(*_renamed_pair(), show_renamed=True).to_text()
    assert shown.splitlines()[1:] == [
        "The two files are identical.",
        "features: 0 added, 0 removed, 0 changed, 2 unchanged",
        "2 record(s) have a different gml:id (matched by natural key; not a change).",
        "renamed features g1 (B-1), matched by natural key (f1 -> g1)",
        "    gml_id: renamed: f1 -> g1",
        "renamed features g2 (B-2), matched by natural key (f2 -> g2)",
        "    gml_id: renamed: f2 -> g2",
    ]
