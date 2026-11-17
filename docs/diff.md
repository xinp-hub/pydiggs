# Comparing two files

`pydiggs diff` and `pydiggs.diff.compare()` say what changed between two DIGGS files:
which boreholes, samples, tests and other records were added or removed, and which values
changed in the ones both files hold. The comparison is of what the files **describe**, not
of how they are written, so two exports of the same data compare as identical even when
every `gml:id` and every line differs.

Typical uses: checking a revised deliverable against the previous one, checking that an
export from a new software version still holds the same data, and checking a file after a
round trip through another format.

`pydiggs.diff` is **stable** from pyDIGGS 1.12 and follows Semantic Versioning. It needs
nothing beyond the core install, and it reads any DIGGS version
[`pydiggs.read`](model.md) reads. The two files may be different DIGGS versions.

- **JSON Schema of the result:**
  [`schemas/diggs-diff-1.0.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json)
  (also `pydiggs.diff.diff_schema()`; `pydiggs.diff.DIFF_SCHEMA_URL` and
  `pydiggs.diff.DIFF_SCHEMA_VERSION`).
- **API reference:** [Comparing files](reference.md#comparing-files).

## Comparing

=== "Command line"

    ```console
    $ pydiggs diff site-rev1.xml site-rev2.xml
    Compared site-rev1.xml (DIGGS 3.0.0) with site-rev2.xml (DIGGS 3.0.0)
    The two files differ.
    document: 0 added, 0 removed, 1 changed, 0 unchanged
    projects: 0 added, 0 removed, 0 changed, 1 unchanged
    features: 0 added, 0 removed, 1 changed, 0 unchanged
    measurements: 0 added, 0 removed, 0 changed, 1 unchanged
    others: 0 added, 0 removed, 0 changed, 1 unchanged
    components: 0 added, 0 removed, 0 changed, 2 unchanged
    changed document Bdoc1, matched by gml:id
        creation_date: 2022-08-17 -> 2022-09-02
    changed features GP_ArealSurvey_bbg_yyp_r3c (Aeromag Survey Area), matched by gml:id
        params.totalSurveyArea: 1 km2 -> 1.2 km2
    $ echo $?
    1
    ```

    The first file is the earlier one (`OLD`), the second the later one (`NEW`):
    "added" means in `NEW` only. See [the command](#the-command) for the options and
    the exit codes.

=== "Python"

    ```python
    from pydiggs import diff

    d = diff.compare("site-rev1.xml", "site-rev2.xml")
    d.identical  # False
    d.complete  # True: no limit stopped the comparison
    d.summary["features"]  # {'added': 0, 'removed': 0, 'changed': 1, 'unchanged': 0}
    for change in d.changes:  # a record that was added, removed or changed
        print(change.op, change.collection, change.new_key or change.old_key)
        for value in change.values:  # each value that differs in it
            print("   ", value.path, value.old, "->", value.new)
    d.to_text()  # the same report as the command prints, with rich markup escaped
    d.to_json(indent=2)  # a diggs-diff 1.0 document
    ```

    `compare()` takes two paths, the bytes of two files, or two
    [`Model`](model.md) objects already read, in any mix. `from pydiggs import diff`
    loads the module; `import pydiggs` alone does not.

## What is compared

Both files are read with [`pydiggs.read`](model.md), and the two models are compared:

- every record of every collection: the document information, projects, associates,
  features (boreholes, soundings, wells …), sampling activities, samples, observations,
  lithology layers, measurements (tests), other objects, and the components nested in
  them (casing, backfill, drive sets, water strikes, test apparatus);
- each record's name, description and remarks, its parameters (`params`), its typed
  fields (depths, dates, locations, intervals) and its links to other records;
- everything the model keeps as `extra` (elements pyDIGGS has no typed field for),
  compared by structure;
- the result tables: their columns and every cell.

A link is compared by what it points to, not by the text of its `href`: if a sample
still belongs to the same borehole, renaming that borehole's `gml:id` is not a change of
the sample.

## What does not count as a change

- **`gml:id` values**, when the records can still be paired (see
  [Matching](#matching)). When two records are paired by their natural key and their ids
  differ, the id is listed as one changed value, `gml_id`.
- **The order** of elements in the file, and of rows in a table whose rows are matched by
  depth.
- **How a number is written**: `1.50` and `1.5` are the same value. Such values are
  counted as *reformatted* and listed only on request (`--verbose`,
  `show_reformatted=True`). Two files that differ only in this way are identical.
- **Where a record sits in the file**: line numbers and XPaths.
- **The file itself**: its name, size, encoding, indentation, comments and
  `schemaLocation`. The DIGGS version of each file is recorded in the result but is not a
  change.

A **unit change is always a change**. `5 ft` against `1.524 m` is reported as changed:
nothing is converted, because a comparison that converts would hide exactly the kind of
edit a reviewer needs to see.

Text is compared exactly, as [`pydiggs.read`](model.md) gives it, upper and lower case
included. A code is compared by its value and its code space.

Two things in `extra` are compared as written, because pyDIGGS does not interpret them:
the `gml:id` of an element nested inside a record (a geometry, a time interval), and the
namespace prefix of an attribute. A file that renames those shows them as changed values
of the record that holds them.

## Matching

Records are paired in two passes (`--match auto`, the default):

1. **By `gml:id`.** Two records with the same `gml:id` and the same element name are the
   same record.
2. **By natural key**, for the records still unpaired on both sides:

    | record | natural key |
    |---|---|
    | feature (borehole, sounding …) | its kind and name |
    | sampling activity, sample | its borehole, name and top depth |
    | observation | its borehole, classification system and name |
    | lithology layer | its borehole (through its observation), top and base |
    | measurement (test) | its borehole, test type, depth and samples |
    | component | the record that holds it, its role, and its index or position |
    | anything else | its name |

    The element name is part of every key, so a `Borehole` is never paired with a
    `Sounding` of the same name.

The report says how each changed record was paired (`matched by gml:id` or
`matched by natural key`).

A natural key that more than one record has, in either file, is **never matched**: two
boreholes both named `B-1` with different ids cannot be told apart, so pyDIGGS does not
guess. Those records are listed as removed and added, and a note
`diff.ambiguous-match` names the key. At most 50 such notes are written, then one that
counts the rest.

`--match gml-id` runs the first pass only: use it when the ids are known to be stable and
a renamed record should show as removed and added. `--match natural` runs the second
pass only: use it for two exports whose ids were generated afresh.

## Tolerances

Two numbers are equal when

```text
|a − b| ≤ max(abs_tol, rel_tol × max(|a|, |b|))
```

The defaults are `rel_tol = 1e-9` and `abs_tol = 0`: exact, apart from the noise of
binary floating point. To ignore differences below a reporting precision, raise one of
them:

```bash
pydiggs diff old.xml new.xml --abs-tol 0.005     # values within 0.005 of each other are equal
pydiggs diff old.xml new.xml --rel-tol 0.001     # values within 0.1 % are equal
```

`abs_tol` is in each value's own unit and applies to every number in the file, depths
and results alike, so choose it with the smallest quantity in mind. A tolerance never
makes two different units equal.

## Result tables

Columns are paired by their property key (the DIGGS dictionary key), or by name when a
column has no key. A column only one file has is an added or removed column.

Rows are paired by the table's **depth index** (a CPT's depth, an interval's top and
base) when every row of both tables has a different index value. Rows are then matched
by depth, within the tolerance, in whatever order they are written, and a reading
inserted in the middle of a sounding shows as one added row rather than as a change of
every row after it. When the index is missing or repeats, rows are paired by row number.

A changed cell is listed with its column and row, for example
`results[0].columns[tip_resistance].cells[@1.5]` (the cell at depth 1.5) or
`cells[12]` (row 12, when rows are paired by number). A row only one file has is listed
as an added or removed cell for each of its columns.

## Limits and incomplete comparisons

Both files are untrusted input, so the work is bounded:

| limit | default | when it is reached |
|---|---|---|
| `--max-bytes`, `max_bytes` | 100 MiB per file | the file is refused (exit 2, `InstanceReadError`) |
| `--max-changes`, `max_changes` | 10,000 | the list of changes is cut; the counts in the summary stay exact; note `diff.limit` |
| `--time-budget`, `time_budget` | none | the comparison stops, reading included; the counts are partial; note `diff.timeout` |

A value longer than 200 characters is shortened in the report and marked (`truncated`
in JSON, `(shortened)` in text).

When a limit cuts the list or stops the work, the result is **incomplete**:
`Diff.complete` is false, `Diff.limits_hit` names what stopped it (`max_changes`,
`time_budget`, or `old_read_incomplete` / `new_read_incomplete` when a file could only
be partly read), and `Diff.identical` is false even if no difference was found, because
an incomplete comparison cannot say the files are the same. The command exits 1.

The notes (`Diff.notes`) are [findings](findings.md) of step `diff`:
`diff.ambiguous-match`, `diff.limit` and `diff.timeout`.

## The command

```text
pydiggs diff OLD NEW [--format text|json] [-o FILE] [--match auto|gml-id|natural]
                     [--rel-tol R] [--abs-tol A] [--max-changes N]
                     [--time-budget SECONDS] [--max-bytes N] [--verbose]
```

| option | meaning |
|---|---|
| `OLD`, `NEW` | the earlier and the later DIGGS file |
| `--format text\|json` | the report: text (the default), or a [diggs-diff 1.0](#the-json-document) document |
| `-o FILE`, `--output FILE` | write the report to `FILE` instead of stdout. An existing file is replaced; a symlink, or one of the two inputs, is refused |
| `--match auto\|gml-id\|natural` | how records are paired (see [Matching](#matching)) |
| `--rel-tol R`, `--abs-tol A` | the number tolerance (see [Tolerances](#tolerances)) |
| `--max-changes N` | list at most `N` changes (default 10,000) |
| `--time-budget SECONDS` | stop after this long (default: no limit) |
| `--max-bytes N` | refuse a file larger than `N` bytes (default 100 MiB) |
| `--verbose` | also list the values that are only written differently |

**Exit codes**, the same as `diff(1)` and with the same three-way split as
`pydiggs validate`:

| code | meaning |
|---|---|
| **0** | the two files are identical, and the comparison finished |
| **1** | the files differ, **or** the comparison is incomplete |
| **2** | a usage error, a file that cannot be read or is not a DIGGS file, or a report that cannot be written |

So a script can rely on 0 meaning "the same data":

```bash
if pydiggs diff previous.xml current.xml -o changes.txt; then
    echo "no change in the data"
fi
```

Text from the files is data, not formatting: control characters and
direction-override characters in a name or value are printed spelled out (`\x1b`,
`‮`), so a file cannot rewrite what the terminal shows.

## The JSON document

`pydiggs diff --format json` and `Diff.to_json()` write a **diggs-diff 1.0** document.
Keys are sorted and the same comparison always gives the same bytes.

```json
{
  "$schema": "https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json",
  "format": "diggs-diff",
  "schema_version": "1.0",
  "tool": {"name": "pydiggs", "version": "1.12.0"},
  "identical": false,
  "complete": true,
  "limits_hit": [],
  "old": {"name": "site-rev1.xml", "diggs_version": "3.0.0", "size": 14278, "sha256": "9923e9ac…"},
  "new": {"name": "site-rev2.xml", "diggs_version": "3.0.0", "size": 14280, "sha256": "051980ed…"},
  "options": {"match": "auto", "rel_tol": 1e-09, "abs_tol": 0.0, "max_changes": 10000, "time_budget": null},
  "summary": {
    "features": {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}
  },
  "value_changes": 2,
  "reformatted": 0,
  "changes": [
    {
      "op": "changed",
      "collection": "features",
      "element": "GP_ArealSurvey",
      "name": "Aeromag Survey Area",
      "old_key": "GP_ArealSurvey_bbg_yyp_r3c",
      "new_key": "GP_ArealSurvey_bbg_yyp_r3c",
      "matched_by": "gml_id",
      "truncated": false,
      "values": [
        {
          "op": "changed",
          "path": "params.totalSurveyArea",
          "old": "1 km2",
          "new": "1.2 km2",
          "at": "/Diggs[1]/samplingFeature[1]/GP_ArealSurvey[1]",
          "truncated": false
        }
      ]
    }
  ],
  "notes": []
}
```

| field | meaning |
|---|---|
| `identical` | true only when nothing differs and the comparison finished |
| `complete`, `limits_hit` | whether a limit stopped the comparison, and which |
| `old`, `new` | each file's name, DIGGS version, size and sha256 |
| `options` | the matching mode, tolerances and limits that were used |
| `summary` | per collection, how many records were `added`, `removed`, `changed` and `unchanged` |
| `value_changes`, `reformatted` | how many values differ, and how many are only written differently |
| `changes[]` | one entry per record added, removed or changed, ordered by collection, then key. `op` is `added`, `removed`, `changed` or `reformatted`; `old_key` and `new_key` are the record's keys in the two files (null on the side that lacks it); `matched_by` is `gml_id` or `natural` |
| `changes[].values[]` | each value that differs: its `path` in the record, the `old` and `new` values (a quantity as text with its unit, null on the side that lacks it), and `at`, the XPath of the record in the new file (the old file for a removed record) |
| `notes[]` | findings of step `diff`, in the [diggs-findings](findings.md) form |

`Diff.from_dict()` reads a document back into a `Diff`. The format is versioned like
the other pyDIGGS documents: a consumer of 1.0 accepts any 1.x document, and a new
optional field is a minor version.

## In an application

[`pydiggs.capabilities()`](capabilities.md) lists the comparison as the operation
`diff`, with its options. It runs in the browser under [Pyodide](browser.md) as it does
in CPython. The HTTP API, the stdio sidecar, the MCP server and `@pydiggs/client` do not
offer it yet.
