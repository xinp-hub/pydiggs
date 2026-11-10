# Reading DIGGS files (preview)

!!! warning "Preview: may change before 1.7.0"
    The read API is in **pyDIGGS 1.6** as `pydiggs.preview`, outside the stability
    promise. It returns **diggs-model 1.0-beta.1**; field names may still change.

    - **1.7.0** freezes **diggs-model 1.0** and adds `pydiggs.read()`, HTTP and sidecar
      `read`, and the `[frames]`, `[polars]` and `[gis]` extras.
    - **1.8.0** adds MCP read tools.

    Comments are welcome as [GitHub issues](https://github.com/xinp-hub/pydiggs/issues).

`pydiggs.validate()` tells you whether a file is correct. The read API gives you what is
*in* the file: boreholes, samples, tests and their result tables, in the same shape for
DIGGS 2.5.a, 2.6 and 3.x, as Python objects and as JSON.

- **JSON Schema:**
  [`schemas/preview/diggs-model-1.0-beta.1.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-model-1.0-beta.1.schema.json)
  (also `pydiggs.model.model_schema()`)
- **Example document** (from the draft):
  [`schemas/preview/diggs-model-1.0-draft.example.json`](schemas/preview/diggs-model-1.0-draft.example.json)

## Reading a file

```python
from pydiggs import preview

m = preview.read("site.xml")  # or preview.read_bytes(data, name="site.xml")
for f in m.features:
    print(f.key, f.kind, f.name, f.total_depth)  # B-01 borehole B-01 Quantity(text='160.00', …)

bh = m.get("Location_B-01")
for test in m.related(bh, relation="samplingFeature", direction="in"):
    for table in getattr(test, "results", ()):
        n = table.column("n_value")  # find columns by dictionary key
        if n is not None:
            print(test.procedure.type, n.values)

text = m.to_json()  # a diggs-model document; the same model always gives the same bytes
again = preview.Model.from_json(text)  # == m
```

`read(path, *, diggs_version=None, results=True, max_bytes=100 MiB, time_budget=None)`:

- **`results=False`** keeps every table's columns and `row_count` but no values: a quick
  inventory of a large file.
- **`time_budget`** (seconds) returns what was read so far, with `complete=False`, when
  time runs out.
- **Errors:** `InstanceReadError` when the file cannot be read (as for `validate`);
  `DocumentError` (a `ValueError`) when the bytes are not a DIGGS document at all, with
  `kind` `malformed`, `not-diggs` or `unsafe-entity` and the matching `structure.*`
  finding. Everything else becomes a note.
- Nothing is fetched from the network, and the file is parsed one top-level member at a
  time, so memory stays close to the file's size.

## Principles

- **Reading is not validating.** `read` works on invalid files and never says "valid".
  Run `validate()` for a verdict. What reading had to work around (a link that goes
  nowhere, a number that is not a number) is recorded in `notes`, as
  [findings](findings.md).
- **Nothing is dropped.** Every element the reader has no typed field for is kept, in
  document order, in the node's `extra` list. Coverage grows in minor releases by moving
  content from `extra` into typed fields.
- **Values are kept as written.** A quantity keeps its `text` exactly as reported, its
  parsed `value` and `uom`, and an `si` value only when the unit converts. Coordinates
  stay in the file's CRS and axis order. Nothing is converted or transformed silently.
- **Nothing is fetched.** Links to other files stay unresolved, with a note.
- **One key per node.** `key` is the `gml:id` when it is present and unique, otherwise
  `"@"` plus the node's XPath. `provenance.xpath` uses the same form as a finding's
  location, so a finding from `validate()` can be traced to the node that contains it.

## The document

A diggs-model document is one JSON object:

| Field | What it holds |
| --- | --- |
| `schema_version` | `1.0-beta.1` in 1.6.0, `1.0` from 1.7.0 (the R0 draft was `1.0-draft`) |
| `tool`, `source` | pyDIGGS version; file name, sha256, size, DIGGS version and namespace |
| `complete`, `limits_hit` | `false` and the limit's name when a limit stopped reading |
| `document`, `projects`, `associates` | Document information, projects, business associates |
| `features` | Sampling features: boreholes, soundings, wells and every other kind |
| `sampling_activities`, `samples` | Sampling activities and samples |
| `observations` | Observation systems, with lithology layers typed |
| `measurements` | Tests, monitors and surveys, each with a `procedure` and result tables |
| `others` | Top-level members with no typed mapper yet |
| `crs` | Every coordinate reference system used, keyed by its `srsName` |
| `links` | Every link between nodes: `source`, `relation`, `target`, `href`, `resolved` |
| `notes` | Findings about reading, in the diggs-findings shape |

Every node starts with the same header: `key`, `gml_id`, `element` (the DIGGS element
name), `kind` (for example `borehole`), `name`, `names`, `description`, `remarks`,
`provenance` and `extra`.

### Result tables

Every DIGGS result set has the same structure in 2.5.a, 2.6 and 3.x: property
definitions and a block of values. The model turns each into a table with a `domain`
(point, interval, temporal, grid), `columns` and a `row_count`. A column carries its
`name`, its dictionary `key` (for example `liquid_limit`), `uom`, `type` and `values`, and
the factor to SI where the unit has one. Tables are generic for every procedure, so a test
with no typed mapper still gives you its numbers.

### Notes

Notes use rule ids that `validate()` already reports, so one explanation covers both
(`pydiggs explain <rule>`):

| Situation | Rule id |
| --- | --- |
| A link to an id that is not in the file | `context.xlink-href-resolves` |
| An `srsName` that is not a known CRS or an id in the file | `geometry.crs-unresolvable` |
| Coordinates that look like longitude first | `geometry.crs-axis-order-suspect` |
| A link into another file | `context.xlink-remote-unresolved` |
| A value that is not a number where one is expected | `structure.value-unparsed` |
| A result row longer or shorter than its columns | `context.datablock-arity` |
| A time limit stopped reading | `structure.timeout` |

## Versioning

`schema_version` is separate from the pyDIGGS version.

- **Minor (1.x):** new optional fields, new definitions, new values of the open
  vocabularies (`kind`, `procedure.type`, `relation`, `domain.shape`, `column.type`), and
  content moving from `extra` into typed fields.
- **Major:** anything else. Both majors are then produced side by side for at least one
  pyDIGGS minor release plus 12 months.
- **The published schema is open.** Objects accept unknown fields and vocabularies are
  listed as `examples`, not `enum`, so a consumer written for 1.0 accepts any 1.x
  document. pyDIGGS's own tests check its output against a closed variant.
- Pre-release schemas live under `schemas/preview/` and may change between betas. The
  frozen 1.0 schema will never be edited; fixes become 1.1.

## Coverage in 1.0

Every top-level member is read. **Typed** means named fields with tests; **generic**
means header, links, location and result tables, with the rest in `extra`.

| Area | Level in 1.0 |
| --- | --- |
| Document information, projects, business associates | Typed |
| Boreholes, soundings, wells (total depth, location, centreline, construction) | Typed |
| Other sampling features (trial pits, stations, piles, geophysical features) | Generic |
| Lithology observations and layers | Typed |
| Other observation systems | Generic |
| Sampling activities and samples | Typed |
| SPT, CPT, Atterberg limits, particle size, water level | Typed parameters and tables |
| Every other test | Generic table, parameters in `extra` |

### What 1.0-beta.1 (pyDIGGS 1.6.0) reads today

- Every top-level member, with its header, links and `extra`; nothing is dropped.
- Features: `location`, `centerline`, `total_depth` (with SI value) and `lrs`.
- Sampling activities and samples: their depth `interval`.
- Lithology systems: one layer per `LithologyObservation`, with `interval`,
  description and classification codes.
- Measurements: the procedure's type and its simple parameters (the rest in the
  procedure's `extra`), and **every result table**, for every procedure, with typed
  columns, dictionary keys and SI factors. Construction activities keep their result
  tables too (in `others`).
- Links, resolved to the node that holds the referenced element, and the `feature`,
  `samples`, `sampling_activity` and `specimen` shortcuts built from them.
- A CRS table (`crs`): each `srsName`, its form and EPSG codes, and `geographic`
  latitude and longitude for the common geographic CRSs.

Coming in the 1.6.x betas: typed construction and water strikes, a well's parent hole,
typed parameters for SPT, CPT, Atterberg and particle-size tests, CRS names, units and
datums from a bundled EPSG subset, and table index columns.

## Checking a reader

The [conformance corpus](https://github.com/xinp-hub/pydiggs/tree/master/conformance)
records, for each of its DIGGS files, what a reader must produce: members, procedures,
links by relation, result tables, rows and cells. The counts come from a plain parse of
each file, not from the pyDIGGS reader, so any reader can be checked against
`conformance/model.toml`:

```bash
python scripts/conformance.py check --model
```
