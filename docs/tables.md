# Your database to DIGGS and back

<!-- tutorial: tables -->

!!! warning "Preview"
    `pydiggs.tables` and the tables profile (`1.0-beta.1`) are a preview. Table and column
    names may change in a minor release until the profile reaches 1.0. `pydiggs.write` and
    `pydiggs.write_bytes` are stable.

If your software keeps geotechnical data in a database, you do not need to learn DIGGS XML
to export or import it. You shape your data into a small set of plain tables, the **DIGGS
tables profile**, and pyDIGGS does the rest:

- **Export:** your tables → a DIGGS 3.0.0 file that passes every pyDIGGS check.
- **Import:** a DIGGS file → the same tables, in load order, ready for your own inserts.

pyDIGGS needs no database driver and never connects to your database. It never converts a
value from one unit to another, and every row or value it could not carry is named in a
finding, so nothing is lost silently.

## Export in three steps

**1. Shape your schema into profile tables.** SQL views are usually enough. Here a
vendor's `hole` and `blow` tables become four profile tables:

```sql
CREATE VIEW features AS
SELECT id AS key, label AS name, east AS x, north AS y,
       depth_ft AS total_depth, 'ft' AS depth_uom
FROM hole;

CREATE VIEW measurements AS
SELECT hole || '-SPT-' || rowid AS key, hole AS feature,
       'DrivenPenetrationTest' AS procedure, at_ft AS depth
FROM blow;

CREATE VIEW "results_DrivenPenetrationTest" AS
SELECT hole || '-SPT-' || rowid AS measurement, n AS n_value
FROM blow;

CREATE VIEW params AS
SELECT hole || '-SPT-' || rowid AS node, 'procedure' AS scope,
       'penetrationTestType' AS name, 'SPT' AS value
FROM blow;
```

The last view states something DIGGS requires of a driven penetration test: which kind it
is. Anything DIGGS names that has no column of its own goes in `params` this way.

**2. Hand the rows to pyDIGGS.** Rows are dictionaries; they can come from a cursor of any
DB-API driver (`sqlite3`, `pyodbc`, `psycopg`, `oracledb`), from CSV files, or from your
own code.

```python
import sqlite3
import pydiggs
from pydiggs import tables

db = sqlite3.connect(":memory:")  # your own database connection goes here
db.executescript("""
    CREATE TABLE hole (id TEXT, label TEXT, east REAL, north REAL, depth_ft REAL);
    INSERT INTO hole VALUES ('B-7', 'Boring 7', 1200.5, 880.25, 40);
    CREATE TABLE blow (hole TEXT, n INTEGER, at_ft REAL);
    INSERT INTO blow VALUES ('B-7', 14, 5), ('B-7', 22, 10);
""")
queries = {
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
    {name: tables.rows_from_cursor(db.execute(sql)) for name, sql in queries.items()}
)
pydiggs.write(result.model, "site.xml")
```

Here the queries are written out; with the views above in your database, each one is
`SELECT * FROM <view>`.

A table can also be anything that exports an Arrow stream: a pyarrow table, a polars or
pandas data frame, a DuckDB or ADBC result. pyDIGGS reads it one record batch at a time
through pyarrow, which the `gis` extra installs (`pip install 'pydiggs[gis]'`); without
pyarrow such a table is a `TablesError` that says so. Dates and times become ISO 8601
text.

**3. Read what was left out.** `result.findings` lists every row that could not be
written and every default pyDIGGS had to assume, each with the table, row and column:

```python
for finding in result.findings:
    print(finding.severity, finding.rule_id, finding.path, finding.message)
```

`result.lossless` is true when there is nothing above the level of a note.

## Import

```python
loaded = {}


def upsert(table, rows, key):  # your own code: insert or update by key
    loaded[table] = len(rows)


received = tables.from_model(pydiggs.read("site.xml"))
for step in received.plan.steps:  # parents before children
    upsert(step.table, received.rows(step.table), key=step.identity)
```

The plan gives the order to load the tables in and, for each one, the columns that
identify a row, so a second import of a corrected file can update rows instead of adding
them. pyDIGGS writes no SQL. To see what changed between two versions of a file before
you merge, compare them with [`pydiggs diff`](diff.md).

`tables.write_package(received, "load/")` writes the same tables as CSV files with a
`datapackage.json` ([Data Package](https://datapackage.org/) v2, one Table Schema per
table, with primary and foreign keys) and a `plan.json`. `tables.read_package("load/")`
reads such a folder back.

!!! note "CSV cells are data"
    Cells hold the values of the source file as they are. Load them as data; a
    spreadsheet that runs formulas may treat a cell that starts with `=` as one.

## Check your export

`tables.round_trip` runs the whole loop for you: it writes your tables as DIGGS, validates
the file, reads it back into tables and compares what came back with what you sent.

```python
queries_again = {name: tables.rows_from_cursor(db.execute(sql)) for name, sql in queries.items()}
checked = tables.round_trip(queries_again)
print(checked.ok)  # False: something was left out, invalid or changed
for finding in checked.findings:  # what writing left out or assumed
    print(finding.rule_id, finding.path)
for difference in checked.differences:  # a value that came back changed
    print(difference.table, difference.row, difference.column, difference.sent, difference.got)
```

For this small export the answer is no, and the findings say why: the tables give no
project, no creation date and no coordinate reference system. Fill those in and it passes.

## From .NET, Java or any language

You do not need to write Python to use the profile. Write the tables as CSV files in one
folder (one `<table>.csv` per table, as above), and let pyDIGGS run beside your program,
either as the [stdio sidecar](sidecar.md#tables) or as the
[HTTP API](http-api.md#post-v1tables). Both are a preview, like the profile.

**The sidecar** (`pydiggs serve --stdio --allow-path C:\Exports`) takes the folder by
path, one JSON request per line, and answers with the DIGGS file in base64:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_version":"1.4"}}
{"jsonrpc":"2.0","id":2,"method":"tables.to_diggs","params":{"path":"C:\\Exports\\site"}}
```

```csharp
// The answer to id 2: write the file, then show the findings to your user.
var result = JsonDocument.Parse(line).RootElement.GetProperty("result");
File.WriteAllBytes(@"C:\Exports\site.xml",
    Convert.FromBase64String(result.GetProperty("output_base64").GetString()!));
var findings = result.GetProperty("report").GetProperty("files")[0].GetProperty("findings");
```

`tables.check` and `tables.round_trip` take the same folder; `tables.from_diggs` takes a
DIGGS file (`content_base64` or `path`) and answers with the tables as a ZIP in
`output_base64`, ready for `ZipFile.ExtractToDirectory`, with the load plan beside it.
Without `--allow-path`, send the folder as a ZIP in `package_base64` instead.

**The HTTP API** (`pydiggs serve --http`) takes the folder as a ZIP:

```bash
curl -s -H 'Content-Type: application/zip' --data-binary @site.zip \
  'http://127.0.0.1:8000/v1/tables/to-diggs?name=site.zip' > result.json
```

```csharp
using var http = new HttpClient { BaseAddress = new Uri("http://127.0.0.1:8000") };
var body = new ByteArrayContent(File.ReadAllBytes("site.zip"));
body.Headers.ContentType = new MediaTypeHeaderValue("application/zip");
var answer = await http.PostAsync("/v1/tables/to-diggs?name=site.zip", body);
var result = JsonDocument.Parse(await answer.Content.ReadAsStringAsync()).RootElement;
```

The answer is the same as the sidecar's, byte for byte, and the DIGGS file is the one
`pydiggs.write_bytes(tables.to_model(...).model)` writes from the same CSV files. A ZIP
pyDIGGS receives is read in memory, never extracted, and refused if it holds anything
but plain CSV and JSON files (see [the limits](http-api.md#post-v1tables)).

**From JavaScript or TypeScript**, [`@pydiggs/client`](javascript.md) 0.11 or later has
the four operations as typed methods (`tablesToDiggs`, `checkTables`, `tablesRoundTrip`,
`tablesFromDiggs`), against this HTTP API or with pyDIGGS running in the browser.

## The tables

`tables.profile()` returns the full description of every table and column as a Data
Package descriptor. In short:

| Table | One row per | Key columns |
|---|---|---|
| `document` | data set (at most one row) | |
| `projects` | project | `key` |
| `features` | borehole, sounding, trial pit, well, station, trench wall | `key`; `project`, `parent` |
| `sampling_activities` | sampling activity | `key`; `feature` |
| `samples` | sample | `key`; `sampling_activity` or `feature` |
| `observations` | lithology log of one feature | `key`; `feature` |
| `layers` | lithology layer | `key`; `observation` or `feature` |
| `measurements` | test, monitoring series or drilling log | `key`; `feature`, `sample` |
| `results_<procedure>` | result row of a test | `measurement`, `result`, `row` |
| `result_columns` | result column (unit, property, type) | `procedure`, `measurement`, `column` |
| `components` | nested record: casing, well screen, water strike, SPT drive set, specimen | `key`; `holder`, `parent` |
| `params` | value with no column of its own | `node`, `scope`, `name` |
| `remarks` | remark, or name after the first | `node`, `kind`, `seq` |
| `links` | reference no column holds | `source`, `relation` |
| `extra` | DIGGS content the profile has no column for (read only) | `node`, `seq` |

A few rules cover most questions:

- **Keys are yours.** `key` is your own stable identifier, as text. It becomes the
  `gml:id` in the file. A key with characters an XML id cannot hold is adjusted, and
  `result.keys` lists each key with the id it got. Keys are unique across all tables.
- **Only `features` is required.** A table you do not have is simply absent. A sample
  with a `feature` and its `top` and `base` (or its one `depth`) gets its sampling
  activity made for it; a layer with a `feature` gets its lithology log.
- **Units travel beside values.** A quantity column `x` has a unit column `x_uom`. Every
  depth along a feature is in that feature's `depth_uom` (`m`, `ft`, `ft[US]`, `cm` or
  `in`); a depth given in another unit is reported, not converted.
- **Results are wide.** There is one result table per test type, named
  `results_<procedure>`, with one column per measured property, named by its key in the
  [DIGGS property dictionary](https://diggsml.org/def/codes/DIGGS/0.1/properties.xml)
  (`n_value`, `tip_resistance`). A test's results are placed by `depth` (or `top` and
  `base`) on its `measurements` row, or by a `position` column on each result row, as a
  CPT sounding is.
- **Units of result columns** go in `result_columns`, once per test type, or once per
  test when tests differ.
- **A test with no result rows** (a triaxial test reported without values, say) is
  written with an empty result table when `result_columns` names its columns for that
  test: rows with its key in `measurement`.
- **Anything else** that DIGGS names goes in `params`, by its DIGGS element name, with
  its unit or code list when it has one.

## Time series

A piezometer or logger series, a drilling log and a dissipation test are tests whose rows
are placed by time rather than by depth. Give each row one of two columns:

- `time`: the time of the reading, ISO 8601, written and read back exactly as you give it
  (`2024-11-05T13:00:01`, `2019-08-16T09:17:00-08:00`). A time holds no space.
- `elapsed_time`: the time since the start, a number, with its unit in `result_columns`
  like any other column: `s`, `min`, `h`, `d`, `month` or `year`.

`measurements.element` says what the row is: `Test` (when empty), `Monitor` for a
monitoring series, or `MeasurementWhileDrilling` for a drilling log. A `Monitor` names
where it measures with a `links` row whose `relation` is `monitorLocation`, as DIGGS
requires; a `WaterLevelMonitoring` procedure also needs its reference point, a
`components` row whose `role` is `referencePoints`.

```python
series = {
    "document": [{"creation_date": "2026-11-22"}],
    "projects": [{"key": "P1", "name": "Riverside"}],
    "features": [
        {
            "key": "PZ-1",
            "project": "P1",
            "x": 1,
            "y": 2,
            "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
            "depth_uom": "m",
        }
    ],
    "measurements": [
        {
            "key": "WL-1",
            "feature": "PZ-1",
            "element": "Monitor",
            "procedure": "WaterLevelMonitoring",
        }
    ],
    "components": [
        {
            "key": "RP-1",
            "holder": "WL-1",
            "role": "referencePoints",
            "element": "ReferencePoint",
            "start": "2024-11-01",
        }
    ],
    "links": [
        {"source": "WL-1", "relation": "monitorLocation", "target": "PZ-1"},
        {"source": "RP-1", "relation": "rpLocation", "target": "PZ-1"},
    ],
    "result_columns": [{"procedure": "WaterLevelMonitoring", "column": "water_depth", "uom": "m"}],
    "results_WaterLevelMonitoring": [
        {"measurement": "WL-1", "row": 0, "time": "2024-11-05T12:30:01", "water_depth": 3.12},
        {"measurement": "WL-1", "row": 1, "time": "2024-11-05T13:00:01", "water_depth": 3.10},
    ],
}
checked = tables.round_trip(series)
assert checked.ok, checked.findings
```

**Several result tables.** A test can have more than one result table: a pore pressure
dissipation test has its summary (`u50`, `t50`) at one depth and the dissipation series
itself, by `elapsed_time`. The `result` column says which table a row is in: `0` (or
empty) for the test's own result, `1`, `2` … for the tables its procedure holds. DIGGS
3.0.0 gives a test one result of its own, so each further table needs its holder in the
procedure: a `components` row with the test as `holder`, such as
`role` `dissipationTimeSeries` and `element` `TemporalResult`. The `measurements` row's
`depth`, `top` and `base` place result `0` only.

## Wells, samples and specimens

A monitoring well, the sample taken in its boring and the specimen a laboratory tested
are rows of the same tables:

- **A well** is a `features` row whose `element` is `Well`; `parent` names the boring it
  sits in. A well has its own depths, so it has its own `depth_uom`.
- **Its screen** is a `components` row held by the well: `role` `opening`, `element`
  `WellOpening`, with the screened interval in `top` and `base`, along the well. DIGGS
  requires the interval, so an opening without one is reported and the well is left out.
- **A sample taken at a point** (a grab sample, a water sample at one depth) has that
  depth in `depth`, on its `sampling_activities` row or, when you give no activity, on
  its `samples` row. A sample taken over an interval has `top` and `base` as before. A row
  gives one or the other: with both, the interval is written and the depth is reported.
  Reading fills `depth` on both rows for a point and leaves `top` and `base` empty.
- **A specimen** is a `components` row held by the test: `role` `specimen`, `element`
  `SoilSpecimen` (or another DIGGS specimen element). It names the sample it was cut from
  with a `links` row whose `relation` is `sample`. DIGGS requires that reference, so a
  specimen without it is reported and its test is left out.

```python
well = {
    "document": [{"creation_date": "2026-11-26"}],
    "projects": [{"key": "P1", "name": "Riverside"}],
    "features": [
        {
            "key": "BH-1",
            "project": "P1",
            "x": 1,
            "y": 2,
            "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
            "depth_uom": "m",
        },
        {
            "key": "PZ-1",
            "project": "P1",
            "element": "Well",
            "parent": "BH-1",
            "x": 1,
            "y": 2,
            "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
            "depth_uom": "m",
        },
    ],
    "sampling_activities": [{"key": "SA-1", "feature": "BH-1", "depth": 4.5}],
    "samples": [{"key": "S-1", "sampling_activity": "SA-1"}],
    "measurements": [
        {"key": "WC-1", "feature": "BH-1", "procedure": "WaterContentTest", "depth": 4.5}
    ],
    "result_columns": [
        {"procedure": "WaterContentTest", "column": "water_content_natural", "uom": "%"}
    ],
    "results_WaterContentTest": [{"measurement": "WC-1", "water_content_natural": 21.9}],
    "components": [
        {
            "key": "SCREEN-1",
            "holder": "PZ-1",
            "role": "opening",
            "element": "WellOpening",
            "top": 12,
            "base": 15,
        },
        {"key": "SPEC-1", "holder": "WC-1", "role": "specimen", "element": "SoilSpecimen"},
    ],
    "links": [{"source": "SPEC-1", "relation": "sample", "target": "S-1"}],
}
checked = tables.round_trip(well)
assert checked.ok, checked.findings
```

A water level series measured in the well is a `Monitor` whose `feature` is the well and
whose `monitorLocation` link names the screen (see [Time series](#time-series)). The
official DIGGS piezometer example, which has all of these, goes from DIGGS to tables and
back whole.

**Depths along a system the file does not define.** A DIGGS file states which depth
system each depth is measured along. When a file you read names one that is nowhere in
it (the official piezometer example does, twice), the depths can only be along the
feature the row belongs to; they are read into `top`, `base` and `depth` as such, and
`tables.assumed` says so. Depths along another feature's system, one the file does
define, are not reinterpreted: they stay in `extra`.

## Stations and trench walls

DIGGS gives a station and a trench wall no centerline, so nothing is measured by a depth
along them. Each is a `features` row; the profile writes it with its reference point and
no depth axis.

- **A station** is a row whose `element` is `Station`. It needs `x` and `y` (with `z`
  when you have it) and their `crs`. `start` and `end` are when it was occupied (DIGGS
  `whenOccupied`). What kind of station it is goes in `params`, name `type`.
- **A trench wall** is a row whose `element` is `TrenchWall`. Beside its reference point
  it needs `extent`, the outline of the wall face, which DIGGS requires: a polygon as
  well-known text, `POLYGON ((x y, x y, …))` or `POLYGON Z ((x y z, …))`, with one closed
  ring (the last position repeats the first), in the reference system `extent_crs` names.
  The coordinates are written as you give them, digit for digit; an outline that is not
  closed, has a hole or is not a polygon is reported and the wall is left out, never
  repaired. `start` and `end` are when it was constructed. Its depth, length and width go
  in `params` (`trenchDepth`, `trenchLength`, `trenchWidth`, each with its unit), and how
  it was dug in a `components` row (`role` `constructionMethod`, `element`
  `TrenchConstructionMethod`).

```python
site = {
    "document": [{"creation_date": "2026-11-27"}],
    "projects": [{"key": "P1", "name": "Riverside"}],
    "features": [
        {
            "key": "ST-1",
            "project": "P1",
            "element": "Station",
            "x": 451200,
            "y": 206300,
            "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
            "start": "2026-05-01",
        },
        {
            "key": "TW-1",
            "project": "P1",
            "element": "TrenchWall",
            "x": 451210,
            "y": 206310,
            "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
            "extent": "POLYGON ((451210 206310, 451228 206310, 451228 206312, 451210 206310))",
            "extent_crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
        },
    ],
    "params": [{"node": "TW-1", "name": "trenchDepth", "value": "5.5", "uom": "m"}],
}
checked = tables.round_trip(site)
assert checked.ok, checked.findings
```

**What is not written on them.** Such a feature has no depth axis, and the profile places
a layer, a sample, a test and a monitoring series by a depth along its feature only. So a
row of `layers`, `sampling_activities`, `samples` or `measurements` that names a station
or a trench wall is reported (`tables.incomplete`) and left out; nothing is placed for
it. A `depth_uom` or a `total_depth` on the feature's own row, and a depth on one of its
`components` rows, is reported (`tables.not-carried`) and the row is written without it.
The layers and faults drawn on a wall face, which DIGGS places by coordinates on the
face, have no columns: reading the official DIGGS trench wall example gives the wall, its
outline and how it was dug, and keeps what is drawn on the face in `extra`.

**An outline in the wall's own coordinates.** A DIGGS file can define a reference system
of its own for a wall face (along the wall, and down), and give the outline in it; the
official example does. The tables do not carry such a definition. Reading gives the
outline and the system's name (`#…`) as they are; writing them back writes that name,
and `tables.no-crs` says the file no longer defines it. Give the outline in a published
reference system when you write from your own data.

## What the first version does not do

- It writes boreholes, soundings, trial pits and wells with their screens, samples,
  specimens, lithology, tests, monitoring series and drilling logs, and stations and
  trench walls by themselves (see [Stations and trench walls](#stations-and-trench-walls)).
  Geophysical surveys are read into tables but not yet written; a row of such a kind is
  reported as `tables.incomplete`. A sample or a test located by coordinates, rather than
  by a depth along its feature, has no column for them, and they are not carried.
- Content in the `extra` table is not written back. Keep the original file when you need
  to pass the same content on.
- It reads all rows into memory (an Arrow stream is read batch by batch, but the rows
  are then held): a project of a million result rows (200 CPT soundings
  of 5,000 rows) takes about 20 seconds and under 1 GB. For larger data sets, write one
  file per project or per hole.
- One element of a DIGGS file holds up to 10 MB of text: XML readers with safe settings,
  pyDIGGS among them, refuse a longer text. A longer result table (a year of one-minute
  logger readings, about half a million rows) is written in parts, each under the limit:
  DIGGS gives a test one result, so part 2 on are measurements of their own, with the
  test's key and `.part2`, `.part3` …, the same element and references, and the next
  rows. `tables.from_model` joins them back into the one test, rows in order, so nothing
  changes for your tables. Only a single row longer than 10 MB is refused. A result table
  held by a procedure (a dissipation series) is not split.
- A result value is a 64-bit number. A value with more digits than one holds exactly
  (about 17) is reported and left out, never rounded. `12.50` and `12.5` are the same
  number.
- It checks structure, not vocabulary: run [`pydiggs validate`](quickstart.md) on the file
  you write to check codes, units and properties against the DIGGS dictionaries.

## Rule ids

| Rule | Level | Meaning |
|---|---|---|
| `tables.unmapped-column` | warning | A column is not a column of its profile table. |
| `tables.unknown-unit` | warning | A depth is not in its feature's depth unit, a unit is not one DIGGS names, or a result lacks the unit its property needs. |
| `tables.orphan-row` | error | A row names a row that was not converted. |
| `tables.duplicate-key` | error | A row repeats a key. |
| `tables.key-rewritten` | info | A key is not a valid XML id; the id written differs. |
| `tables.value-unparsed` | warning | A value is not the number or text DIGGS needs there. |
| `tables.assumed` | warning | DIGGS needs a value the tables lack and a documented default was written; or a file you read names a depth system it does not define, and its depths were read along the row's feature. |
| `tables.value-reformatted` | info | The same number in another form (12.50 and 12.5). |
| `tables.incomplete` | error | A row lacks something DIGGS requires, or is of a kind not yet written. |
| `tables.no-crs` | warning | Coordinates are given without their coordinate reference system, or with one only a DIGGS file defines. |
| `tables.extra-not-written` | warning | Rows of `extra` are not written back to DIGGS. |
| `tables.not-carried` | warning | DIGGS content with no column in the profile; or a value DIGGS has no place for on that kind of row, left out when writing. |

## Sensitive data

Use generated or public data when you test your export. If a data set may hold personal
data or information about critical infrastructure, follow your organisation's rules
before you copy it anywhere.
