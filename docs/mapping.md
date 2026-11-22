# Map your database with a mapping file

<!-- tutorial: mapping -->

!!! warning "Preview"
    `pydiggs.mapping`, `pydiggs map` and the `diggs-mapping` 1.0 format are a preview.
    They may change in a minor release.

[Your database to DIGGS and back](tables.md) shows how to shape your data into the DIGGS
tables profile with SQL views. If you would rather not write views, write one mapping
file instead. It says, for each of your tables and columns, where the value goes in the
profile. pyDIGGS reads your tables as they are, applies the mapping, and writes DIGGS.

A mapping file is data, not code. Nothing in it is run, and no number in your data is
changed: a unit is renamed, never converted, and a value pyDIGGS cannot place is named in
a finding.

## Start one from your own tables

Export your tables as CSV files into one folder, then let pyDIGGS start a mapping for you:

```bash
pydiggs map init export/ -o my-mapping.toml
```

pyDIGGS looks at each table's columns. Where a name suggests a place in the profile (a
table `holes` suggests `features`; a column `depth_top` suggests `top`) it fills the line
in and marks it `# guess`. A guess comes from the name alone, so check each one. Columns
it could not place are listed in comments for you to complete.

## What a mapping file says

A small part of the worked example in
[`docs/examples/mapping/demo.toml`](https://github.com/xinp-hub/pydiggs/blob/master/docs/examples/mapping/demo.toml):

```toml
mapping_format = "1.0"
name = "demo-borehole-log"
version = "1.0"
method = "manual"            # manual, assisted or generated
licence = "Apache-2.0"
profile_version = "1.0-beta.1"

[units]                      # your unit text = the DIGGS unit (a rename only)
metres = "m"

[codes.uscs]                 # your codes = DIGGS codes, and how well each matches
code_space = "https://diggsml.org/def/codes/DIGGS/0.1/grp-astmD2487.xml"
[codes.uscs.map]
CL = "CL"
TS = { code = "OL", match = "broader" }

[[tables]]
source = "holes"             # your table
target = "features"          # the profile's table
[tables.columns]
key = "hole_id"              # copy a column
element = { value = "Borehole" }                    # a constant
depth_uom = { from = "depth_unit", units = true }   # a column, through [units]

[[tables]]
source = "spt"
target = "measurements"
[tables.columns]
key = { template = "{hole_id}-SPT-{seq}" }          # a key built from columns
feature = "hole_id"
procedure = { value = "DrivenPenetrationTest" }
depth = "depth_m"
[[tables.params]]            # a value DIGGS names that has no column of its own
name = "penetrationTestType"
scope = "procedure"
value = "SPT"
```

- **A column** is copied as it is: `key = "hole_id"`.
- **A constant** is `{ value = "..." }`.
- **A key** can be built from columns: `{ template = "{hole_id}-SPT-{seq}" }`. Only plain
  `{column}` names are allowed in a template.
- **A unit** column goes through `[units]` with `units = true`. A unit missing from the
  table is reported, and its values are left out.
- **A code** column goes through a code map with `codes = "uscs"`. Each entry says how
  well it matches: `exact`, `close`, `broader` or `narrower` (the SKOS words). Anything
  but `exact` is noted each time it is used, so a reader of the file can see it.
- **Results** go to a table named `results_<procedure>`, one column per measured property,
  named by its key in the DIGGS property dictionary (`n_value`, `tip_resistance`).

## Check it, then test it

The worked example's CSV files are in `export/` here, as yours would be:

```python
>>> from pydiggs import mapping, tables
>>> loaded = mapping.load(open("demo.toml", encoding="utf-8").read())
>>> mapping.check(loaded)
()
>>> applied = loaded.apply(mapping.read_source("export"))
>>> [(finding.rule_id, finding.path) for finding in applied.findings]
[('tables.mapping-code-not-exact', 'strata/4/uscs')]

```

`mapping.check` needs no data: it checks that every target is a table and column of the
profile, and that the units and codes on the right of each line are DIGGS ones.
`apply` names every source value it did not use and every code it could not translate.

Then the whole loop: write DIGGS, validate it, read it back, compare.

```python
>>> checked = tables.round_trip(applied.tables)
>>> checked.ok, checked.differences
(True, ())
>>> tables.check(applied.tables)
()

```

`tables.check(applied.tables)` checks the shaped tables against the DIGGS vocabularies
without writing DIGGS: property keys, the unit each property needs, and codes from the
code lists pyDIGGS bundles.

From the command line, the same three steps:

```bash
pydiggs map check export/ --mapping my-mapping.toml
pydiggs map test export/ --mapping my-mapping.toml -o site.xml
pydiggs map coverage export/ --mapping my-mapping.toml
```

`map test` exits with 1 when anything was left out, the file is not valid, or a value
came back changed; put it in your CI. `map coverage` shows how much of your schema the
mapping reads and how much of the profile it fills.

## Rule ids

| Rule | Level | Meaning |
|---|---|---|
| `tables.mapping-unknown-target` | error | A target is not a table or column of the profile. |
| `tables.mapping-unmapped-source` | warning | A source column with values that the mapping does not read. |
| `tables.mapping-unmapped-code` | warning | A source code with no entry in its code map; carried as text. |
| `tables.mapping-unknown-unit` | warning | A source unit with no entry in `[units]`; the value is left out. |
| `tables.mapping-code-not-exact` | info | A code was translated with a `close`, `broader` or `narrower` match. |
| `tables.unknown-property` | warning | A result column is not a property of the DIGGS property dictionary. |
| `tables.unknown-code` | warning | A code is not in the bundled code list it names. |

The rules of the tables themselves are listed on
[Your database to DIGGS and back](tables.md#rule-ids).

## A mapping you share

The header records who wrote the mapping (`creator`), how (`method`: `manual`, `assisted`
when a person checked a draft, `generated` when nobody has yet), under what `licence`, and
for which `version` of your schema (`[source]`). A mapping file holds names and codes,
never data, so it can be shared and reviewed like code.

If a tool or an AI assistant drafts the mapping for you, give it your column names, not
your data, and keep `method = "assisted"` until a person has checked every line and
`pydiggs map test` passes on a real export. If the data may hold personal information or
information about critical infrastructure, follow your organisation's rules before you
copy it anywhere.
