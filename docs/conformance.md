# Testing DIGGS support (conformance suite)

`pydiggs conformance` tests how a piece of software reads and writes DIGGS 3. You run it on
your own machine: there is no account, nothing is uploaded, and the result can be re-run by
anyone you give the files to.

<!-- tutorial: conformance -->

!!! info "Preview (since pyDIGGS 1.15)"
    The classes, the checks and the evidence document may still change before they are
    frozen. The suite has its own version (`1.0-beta.3`), which changes whenever a file
    could be graded differently. A challenge is graded by the suite version that made it
    ([versions](#versions)).

!!! warning "What a result means"
    A result is **self-tested**. It shows that a file passed a published test. It does not
    show which software wrote the file, and it is **not a certification** by the DIGGS
    committee or the ASCE Geo-Institute. Every evidence document says so.

- **JSON Schema of the evidence:**
  [`schemas/preview/diggs-conformance-1.0-beta.3.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.3.schema.json)
  (also `pydiggs.conformance.conformance_schema()`).
- **API reference:** [Conformance suite](reference.md#conformance-suite).
- **In your browser:** [the same three steps on a web page](conformance/try/index.html),
  run by pyDIGGS inside the tab, with nothing uploaded ([below](#in-your-browser)).

## Test your export in three steps

**1. Make a challenge.** Name your product and its exact version; they are recorded in the
evidence as you declare them.

```console
$ pydiggs conformance new spt-export --product "MyLog" --product-version 4.2.1 -o challenge
Challenge spt-export written to challenge (seed 5f0c2a9d41b7e386, token 9D21F4).
Read challenge/README.md, then:
  pydiggs conformance grade challenge <your DIGGS file>
```

The folder is a [DIGGS tables profile](tables.md) package: one CSV file per table
(`projects.csv`, `features.csv`, `measurements.csv`, `results_DrivenPenetrationTest.csv`,
`params.csv` …), a `datapackage.json` that describes every table and column with its unit,
a `plan.json` with the load order, and a `README.md`. These are the same tables
`pydiggs tables` reads and writes, so a challenge is also a worked example of the profile.
The data is synthetic and is generated for this challenge from its seed, so your next
challenge has other borings, depths and values.

The pile classes are the exception: the profile has no pile tables yet, so their challenge
is plain CSV tables (`piles.csv`, `driving.csv`, `hammer.csv` …) with a `columns.csv` that
gives the unit and meaning of every column.

**2. Load the tables into your software and export one DIGGS 3.0.0 file**, the way a user
of your software would.

**3. Grade the file.**

```console
$ pydiggs conformance grade challenge my-export.xml
spt-export: PASS
  product     MyLog 4.2.1 (as declared)
  suite       1.0-beta.3, DIGGS 3.0.0, self-tested
  challenge   seed 5f0c2a9d41b7e386, token 9D21F4
  submission  my-export.xml, sha256 1c0e6f1d2a7b4c55…

  PASS               The file is a DIGGS 3 document (1/1)
  PASS               Valid DIGGS: schema, dictionaries and business rules (1/1)
  PASS               Project, locations and depths (7/7)
  PASS               Every source value, at its depth (54/54)

  Self-tested: this does not show which software produced the file.

  Evidence: challenge/evidence.json
  Report:   challenge/report.html
```

When something is wrong, each missing or different value is named, with what was expected
and what the file gives:

```text
  FAIL               Every source value, at its depth (53/54)
      conformance.value-differs: n_value 31 at 15.00 ft in 'BH 62': the file gives another value (expected 31, got 13)
```

`expected` and `got` are in SI units (metres, pascals), which is how values are compared.

Fix the export and grade again, as often as you like. The exit status is 0 for a pass, 1
for a fail, and 2 when a file cannot be read.

## What an export is graded on

| Stage | What must hold |
| --- | --- |
| The file is a DIGGS 3 document | It parses, and it is DIGGS 3. |
| Valid DIGGS | No error from [`pydiggs validate`](usage.md): schema, dictionaries, Schematron business rules and the cross-reference checks. Warnings do not fail it. |
| Project, locations and depths | The project's name and every location name (borings, soundings, piles) are in the file exactly as the tables write them, and each boring or sounding has its total depth. |
| Every source value, at its depth | Each value of the test tables is in the file: on the right location, under the right test procedure and property, at the right depth. |

The rules behind the last two stages:

- **Any declared unit.** Values are compared in SI units, so you may export feet as metres
  or kilopascals as megapascals, as long as the file states the unit.
- **Tolerance.** A value must agree with the source to the precision the source gives:
  within half a unit in the last place of the source value. `37.4 kPa` exported as
  `0.0374 MPa` passes; exported as `0.037 MPa` it does not. Depths follow the same rule.
- **Names are exact.** `BH 62` is not `BH-62` or `bh 62`. The project's name contains an
  ampersand and the challenge's token.
- **Extra content is allowed, contradictions are not.** The file may hold more than the
  tables give. Where it gives a value for a source fact more than once (the same
  property, on the same location and procedure, at the same depth), every one must agree
  with the source.
- **A unit must be one DIGGS can convert.** A value whose unit is not a unit of the right
  kind (a length for a depth, a pressure for a cone resistance) is not read as that value.
- **An empty cell stays empty.** Where the tables leave a value empty (a density that was
  not measured, the stroke of a blow-less pile run), the file must not hold one: an export
  that writes a zero there fails with `conformance.value-invented`. A cell that holds
  the `nullValue` its property declares is empty too, in every class: DIGGS lets a
  property name the text that marks a missing value in its data, such as
  `<nullValue>-999</nullValue>`. The same number with no `nullValue` declared is a
  value, and fails. (Suite 1.0-beta.2 read the mark as a value in the `spt`, `cpt`,
  `lab-index` and `deep-foundations` classes; see [versions](#versions).)
- **Percentages.** A percentage may be written in percent (`uom="%"`) or as the plain
  percent number with no unit, as DIGGS writes the liquid limit: `48`, not `0.48`. (Where
  the DIGGS property requires a unit, validation already fails a value without one.)
- **Where values are looked up.** Results are found by the DIGGS property (for example
  `n_value`, `tip_resistance`, `sleeve_friction`, `pore_pressure_u2`) on a test whose
  procedure is the class's (`DrivenPenetrationTest`, `StaticConePenetrationTest`); SPT
  drive parameters are `totalPenetration` and `hammerEfficiency` of the procedure, and
  apply to every depth their test has a result at. One test per drive, or one test with
  a row per drive, both read the same. Blow counts are exact.
- **Laboratory tests** are looked up at the specimen depth, under `WaterContentTest` (a
  water content on the `LabDensityTest` also counts), `AtterbergLimitsTest`,
  `LabDensityTest` and `ParticleSizeTest`. The non-plastic specimen must carry the
  `non_plastic` flag and no limits; every other specimen must not be flagged. A grading
  curve is looked up point by point: the percent passing at each sieve size, from the
  `Grading` records of the boring's particle-size test.
- **Piles** are features named as `piles.csv` names them (`SteelPipePile`, `SteelHPile`,
  `ConcretePile`), with their lengths, size and capacity. Each driving increment is looked
  up in the pile's `PileDrivingRecord` at the pile tip depth the record's location gives,
  along the pile's own linear reference from the ground surface. DIGGS 3.0.0 requires a
  pile to refer to a boring and allows one driving activity per pile; the challenge
  includes the test boring for that reason.
- **A driving record belongs to the pile its activity names.** The
  `PileDrivingActivity`'s `samplingFeatureRef` is what DIGGS says was driven; the linear
  reference a record's depths use is only a coordinate frame. A record is read for the
  activity's pile, and is not read when the activity names no single pile and its
  reference frame does not identify one either, or when the activity's pile and the
  frame's pile disagree: guessing would credit one pile's blows to another.
- **Pore pressure dissipation tests** are `Test`s whose procedure is a
  `PorePressureDissipationTest`. The test's own result (`pore_pressure_equil` for u0,
  `u50`, `t50`) is looked up at the test depth. The series is the procedure's
  `dissipationTimeSeries`: a `TemporalResult` whose rows are placed by elapsed time (a
  `TimeIntervalList` in any time unit DIGGS names), and each reading (`pore_pressure_u1`,
  `pore_pressure_u2`) is looked up point by point at its elapsed time. A series belongs
  to the test it is in; a sounding's tests are matched in depth order. Values are carried
  as reported: a t50 the source leaves empty (the test was stopped before half the excess
  pore pressure dissipated) must stay empty, not be worked out from the series.
- **Vane and dilatometer tests.** A field vane test is a `Test` at its depth with an
  `InsituVaneTest` procedure: the peak strength is `shear_strength_undrained`, the
  maximum torques are `maximumMeasuredTorque` of its `intactVaneTest` and
  `remoldedVaneTest` measurement data, and the vane's size is `vaneDiameter` and
  `vaneHeight`. DIGGS 3.0.0 has no property for a remoulded vane strength; the suite
  looks for it as `shear_strength_undrained_residual` (the dictionary's strength after
  large shear displacement), and the challenge's `results_InsituVaneTest` table carries
  it in that column, named `remoulded_strength`. A vane test with no remoulded reading
  has no `remoldedVaneTest` and no remoulded strength. A dilatometer sounding's readings
  are `a_pressure`, `b_pressure` and `c_pressure` at their depths under
  `FlatPlateDilatometerTest`, with the membrane calibrations `deltaA` and `deltaB` of the
  procedure.

The answer key is built from the tables alone. pyDIGGS's own converter and reader are run
against it on randomly drawn seeds in every run of pyDIGGS's tests, like any other
software. `Challenge.reference()` in Python gives that known-good export, to try the suite
or to compare with your own file.

The validation stage is pyDIGGS's validator of the release you run, and the evidence
records that release beside the suite version.

### Measurement while drilling

The `mwd` challenge is a [tables profile](tables.md) package like SPT and CPT: one
`MeasurementWhileDrilling` per boring in `measurements.csv`, and its readings in
`results_MWDProcedure.csv`, placed by `elapsed_time`, with the bit's `depth` and the
channels as columns coded in the DIGGS MWD dictionary (`mwd_properties.xml`):
`penetration_rate`, `crowd_downward_thrust` (thrust), `torque`, `rotation_shaft` (rotation
speed) and `fluid_injection_pressure` (flushing pressure).

- **Where values are looked up.** Each value is found by its MWD property on a
  `MeasurementWhileDrilling` (procedure `MWDProcedure`) of the boring, at the depth of its
  row. A row's depth is its `depth` column (`mwd_properties.xml#depth`); a log with no
  such column is placed by its result's location instead. The elapsed time of each
  reading is graded too: the `timeDomain` of the result, or a `time` column.
- **Rotation speed** may be in rpm, rev/s or any angular velocity unit (rad/s).
- **No reading is invented.** A flushing pressure the tables leave empty must be absent,
  or a value the column's `nullValue` marks as missing; a zero fails. Where recording
  paused (the depth jumps several steps), a reading at a depth of the pause is invented
  too, whether it gives a time, a penetration rate or any other channel: resampling a
  log to a regular depth step fails.
- **A zero is a value.** The first reading of each log, at the collar, has a penetration
  rate and a rotation speed of 0; they must be in the file.

### Monitoring

The `monitoring` challenge is a [tables profile](tables.md) package too: two or three
piezometers, each a borehole in `features.csv` with one `Monitor` in `measurements.csv`
and its readings in `results_WaterLevelMonitoring.csv`, placed by `time`. The columns are
the three properties the DIGGS property dictionary defines for a `WaterLevelMonitoring`
procedure: `water_depth`, `water_elev` and `temperature`, in metres and degrees Celsius
or in feet and degrees Fahrenheit. `components.csv` holds each series' sensor, the
detector at the sensor depth, and the reference point the levels are measured from;
`links.csv` says that the series' `monitorLocation` is that detector. Every value is
carried as the logger reports it: the suite works out no level, head or pressure.

- **Where values are looked up.** Each value is found by its property on a `Monitor` of
  the borehole whose procedure is a `WaterLevelMonitoring`, at the time of its row: the
  result's `timeDomain` must give one time per row (a `TimePositionList`).
- **A time is an instant.** The tables write each time as an ISO 8601 date and time with
  its offset from UTC: `Z` for about half the seeds, an offset such as `-05:00` for the
  others. Times are compared as instants, to the second, so `2024-03-05T09:30:00-05:00`
  and `2024-03-05T14:30:00Z` are the same reading and either form passes. A time with no
  offset is local time somewhere and names no instant: a row placed by one is not read,
  and its values are reported as `conformance.fact-missing`. Dropping the offset, or
  changing it without moving the clock time with it, fails.
- **Any unit DIGGS can convert.** Values are compared in SI: a length in metres, a
  temperature in kelvin, so a file may state `degC`, `degF` or `K` as long as its numbers
  are in the unit it states.
- **The sensor depth** is the place the series' `monitorLocation` gives, along the
  borehole: a one-dimensional `PointLocation` in the `monitorLocation` itself, or the
  location of the part of the procedure it refers to (the `detectorLocation` of a
  sensor's `Detector`, as in the suite's own file).
- **No reading is invented.** A temperature the tables leave empty must be absent, or a
  value the column's `nullValue` marks as missing; a zero fails. Each logger was stopped
  once: the readings of two to six intervals are not there, and a file that gives a
  value at one of those times (a series filled in or resampled to a regular interval)
  fails with `conformance.value-invented`.
- **A zero is a value.** One reading has the water at the reference point, a depth to
  water of 0; it must be in the file.

The import class asks for the reading at a time given in UTC, whatever offset the file
writes: software that ignores the offset finds another reading, or none.

## Test your import

An import class gives you a DIGGS file to open and questions to answer from what your
software shows.

```console
$ pydiggs conformance new cpt-import --product "MyLog" --product-version 4.2.1 -o cpt-import
$ pydiggs conformance grade cpt-import cpt-import/answers.csv
```

Open `challenge.xml` in your software and fill the `answer` column of `answers.csv`.
Counts must be exact. Names are compared ignoring letter case and surrounding spaces.
Numbers must be in the unit the question asks for, which is often not the unit the file
uses, and must agree within 0.5 % or the precision of the value in the file, whichever is
larger. Write numbers with a decimal point and no thousands separator: an answer with a
comma is not read. A unit typed after the number is ignored.

The folder also holds `defective.xml`, the same file with one planted defect (a reference
that resolves to nothing, a property that is not in the DIGGS dictionary, or an invalid
unit). The last question asks which defect your software reports. At the `core` level this
stage is **advisory**: it is reported and recorded, and it does not decide the verdict.

## Test two products together

!!! info "Preview (since pyDIGGS 1.18)"
    Pairwise import challenges, the `source_export` of their evidence and the `read` and
    `not-read` cells of the matrix may still change.

Passing `spt-export` shows that product A writes a correct file, and passing `spt-import`
shows that product B reads the suite's own file. Neither is a test of B on **the file A
wrote**. A *pairwise* import challenge is: it is an import challenge whose DIGGS file is
A's graded export instead of the suite's. What its result shows, and what no self-run
test can show, is [spelled out below](#what-read-means-and-what-it-does-not).

**1. The exporting vendor** passes the export class as usual, and gives the other party
two files: the DIGGS file their software exported, and the `evidence.json` that `grade`
wrote for it. (Nothing else of the challenge folder is needed.)

```console
$ pydiggs conformance new spt-export --product "LogA" --product-version 2.0 -o a-spt-export
$ pydiggs conformance grade a-spt-export a-spt-export/export.xml
spt-export: PASS
```

**2. The importing vendor** (or an owner who has both products) makes the import
challenge from those two files. The export is looked for beside the evidence, under the
name the evidence gives; `--export FILE` names it when it is somewhere else.

```console
$ pydiggs conformance new spt-import --product "Viewer" --product-version 7 \
    --from-export a-spt-export/evidence.json -o b-spt-import
Challenge spt-import written to b-spt-import (seed 5f0c2a9d41b7e386, token 9D21F4).
Its challenge.xml is the export of LogA 2.0 (as declared), verified here:
  sha256 519e60ede733289510fad9d2aa0ccc8aa7e26a11dc91164ca0bbb52888e4fbc8
Read b-spt-import/README.md, then:
  pydiggs conformance grade b-spt-import answers.csv
```

`challenge.xml` in that folder is A's file, byte for byte. The seed is the one A's export
challenge had, so the questions, and the answers the suite expects, are exactly those of
the suite's ordinary import challenge of that seed: they come from the seed's data, not
from the bytes of `challenge.xml`. They are right for A's file because A's file passed
the export class, which checks every source value at its depth.

**3. Open `challenge.xml` in the importing product, fill `answers.csv`, and grade**, as
for any import class.

```console
$ pydiggs conformance grade b-spt-import b-spt-import/answers.csv
spt-import: PASS
  product     Viewer 7 (as declared)
  suite       1.0-beta.3, DIGGS 3.0.0, self-tested
  challenge   seed 5f0c2a9d41b7e386, token 9D21F4
  file given  exported by LogA 2.0 (as declared), sha256 519e60ede7332895…
  submission  answers.csv, sha256 51f84aa598539cde…
```

The evidence of that run carries a `source_export` member, copied from the challenge:

```json
"source_export": {
  "product": {"name": "LogA", "version": "2.0"},
  "export_sha256": "519e60ede733289510fad9d2aa0ccc8aa7e26a11dc91164ca0bbb52888e4fbc8",
  "evidence_sha256": "d60e14b16f0881234c899be91f1a96db1b6b94c339762866b6d87c3a50b928e4",
  "suite_version": "1.0-beta.3"
}
```

It names the source by **digest, never by path**: the exporting product's name and
version as its evidence states them, the SHA-256 of the exported file, the SHA-256 of the
exporter's `evidence.json` as it was given, and the suite version that graded the export.
Evidence without `source_export` means what it always has: the file in the challenge
was the suite's own.

### What is checked before the challenge is made

The evidence and the export come from another party, so `new --from-export` checks
both. It exits with status 2 and a message, and writes nothing, when:

- the evidence is not a diggs-conformance document, or is for another family, or for an
  import class;
- the export did not pass: the suite's answers are right only for a correct export;
- the export file is not the one the evidence graded (its SHA-256 differs);
- **the evidence does not verify**: the export is graded again here, against the
  challenge the evidence names (class, product and seed are all a challenge is made of),
  and that must give the evidence back. Calling a failed export a pass, or renaming the
  product, is caught here;
- the export was graded under another suite version;
- either file is a link, is not a plain file, cannot be read, or is too large
  (`--max-bytes`, 100 MiB by default; `--time-budget` limits reading and validating the
  export). A link is never followed, and no path inside the evidence is used: only a
  plain file name, beside the evidence.

### What is checked afterwards, and what is not

`challenge.json` is a file in the importing party's own folder, and anyone can write
one by hand. So what it says of its source is checked again, by three different parties
to the result:

- **Every time the challenge is loaded** (`grade`, `verify`, `matrix`): `challenge.xml`
  must have the SHA-256 the challenge records, and it is graded again as an export of
  this challenge's seed and must pass. A challenge that names a file which is not a
  correct export of the data its questions are about is refused, whatever it claims:
  another product's export of other data, the wrong family, or a file that is not DIGGS.
  The size and time limits of the command apply to that file (`--max-bytes`,
  `--time-budget`); a challenge made with a larger `--max-bytes` is graded with it too.
- **`verify`** compares `source_export` in the evidence with the challenge, and the
  challenge's identity (`challenge.id`) covers it. Editing `source_export` in the
  evidence, removing it, or adding one to the evidence of an ordinary import challenge
  is reported (`source_export` differs). Editing it in `challenge.json` and in the
  evidence alike is reported too (`challenge` differs), unless the identity is worked
  out again as well, which anyone can do: the identity is a plain hash, not a signature.
- **`matrix`** is what checks the exporter's name and the `evidence_sha256`: it claims a
  cell only when the exporter's own passing export evidence is in the folder, verified,
  of the same family and seed, and is the very document, for the very file, that
  `source_export` names. Nothing short of that is detected by `verify` alone: a
  hand-written challenge whose file really is a correct export of its seed, and which
  names an exporter that never ran the suite, loads and verifies. It decides no cell.

To let someone verify the result, give them the whole challenge folder, with
`challenge.xml`.

### What `read` means, and what it does not

The [matrix](#round-trip-matrix) says `read` for the pair when the folder holds both
halves, each verified. It means three things, each graded again by whoever runs
`matrix`:

- **A's export was correct**: the file, named by its SHA-256, passed the export class;
- **that file was the one handed to B**: B's challenge holds it as `challenge.xml`, and
  names A's evidence by its digest;
- **the answers given for B are right for it**: counts, names, and values in the units
  asked for, which are often not the units of the file.

It does not show:

- **which software opened the file.** Like every result of this suite it is self-tested:
  it records answers, not how they were produced;
- **that the answers were taken from that file.** The expected answers depend on the
  seed, not on the bytes of `challenge.xml`: they are the same as for the suite's own
  import challenge of that seed. Answers worked out from any other copy of the same data
  (the suite's own file, the source tables) are graded the same. The suite cannot tell
  the difference; a signature from B's own CI, over a run that opens the file in B's
  product, is what adds that: see [Signing conformance evidence](conformance-signing.md);
- that B reads **every** file A can write. One export of one seed was used: one set of
  borings, in the units and layout A chose that time. Run several seeds for more;
- anything about values the questions do not ask for. A question set samples the file; it
  is not the point-by-point check an export gets;
- that B reports defects in A's files: `defective.xml` is the suite's own file.

`not-read` means such evidence exists and none of it passed. It does not say which side
is at fault: A's file passed the export class, so it is valid DIGGS that holds the data,
and B gave answers that differ. Read the failed items in B's report.

## Classes, and what cannot be tested yet

```console
$ pydiggs conformance classes
```

A class is one part of DIGGS in one direction, such as `spt-export` or `cpt-import`. These
parts of DIGGS have scenario challenges, each in both directions:

| Class family | What its challenge holds |
| --- | --- |
| `spt` | Standard penetration tests in three borings: refusals, weight-of-hammer tests, feet or metres |
| `cpt` | Piezocone soundings: qc in MPa beside fs and u2 in kPa, negative pore pressures, a gap where a rod was added |
| `lab-index` | Water content, Atterberg limits (one specimen non-plastic), bulk and dry density on undisturbed samples, a particle-size curve per boring |
| `deep-foundations` | Steel pipe, steel H or concrete piles with their driving records: a pile run of zero or one blow, refusal at the end |
| `mwd` | Measurement while drilling: one drilling log per boring, with penetration rate, thrust, torque, rotation speed and flushing pressure; a pause for a rod change, readings with no flushing pressure, units such as m/h, bar and rpm |
| `dissipation` | Dissipation tests in two piezocone soundings: u0, u50 and t50, and the u1 and u2 series by elapsed time in seconds or minutes; one test stopped before t50, one reading with no u2 |
| `in-situ-strength` | Field vane tests (peak and remoulded torque and strength, vane size; one test with no remoulded reading) and a flat plate dilatometer sounding (A, B and C readings, C at a few depths only), in SI or US units |
| `monitoring` | Water level series of two or three piezometers, each with a sensor at a depth: depth to water, water elevation and temperature at each time, in UTC or with an offset from UTC; a stretch where the logger was stopped, readings with no temperature, metres and degrees Celsius or feet and degrees Fahrenheit |

The catalogue behind the classes covers the whole of DIGGS 3.0.0: every test procedure of
the schema, piles and driving records, grouting, monitoring, geophysics and the rest. A part
with no challenge yet is listed as **not yet testable**, here and on every scorecard, so a
few passing classes are never mistaken for full DIGGS support. A family with a challenge
may still grade only part of what it names, and its note in `pydiggs conformance classes`
says which part: the `monitoring` family grades water level series, and not yet wells,
well openings or stations.

## Known limits of this version

- **One grading curve per boring.** Curve points are matched by boring and sieve size, not
  by specimen depth (the challenges have one particle-size test per boring), so a curve
  attached to the wrong sample of the right boring is not caught.
- **Pile depths from the ground surface.** Driving increments are matched at the depth
  along the pile's own linear reference, taken as measured from the ground surface, as the
  challenge's tables give it. A file whose pile reference starts at the pile head would
  not match.
- **MWD depths come from the `depth` column.** A drilling log is matched by the depth each
  reading gives, not by its time. A log with times and no depths (a `timeDomain` and no
  `depth` column) **fails** the fidelity stage: none of its readings can be placed, so
  every source value is reported as `conformance.fact-missing`. Only the five channels of the
  challenge are graded; other channels a rig logs are allowed and not checked.
- **MWD readings below the last one.** A reading invented in the rod-change pause is
  caught, whichever cells it fills. A reading added below a log's last reading is not:
  the tables say nothing of those depths. (A total depth that moved with it is caught.)
- **Monitoring series are piezometers in boreholes.** The `monitoring` challenge has
  water level series only (`WaterLevelMonitoring`: depth to water, water elevation,
  temperature), each on a `Borehole`. A `Well` with its openings, a `Station`, and a
  series of any other procedure are in the family and are not graded yet. The sensor
  depth is read only as a point: a file that gives the `monitorLocation` as an interval
  (a response zone) has its sensor depth reported as missing.
- **Monitoring readings outside the series.** A reading invented where the logger was
  stopped is caught only at a time the logger would have read at (its regular interval);
  one added at another time in the gap, before the first reading or after the last is
  not: the tables say nothing of those times.
- **A pairwise challenge keeps the suite's own defective file.** In an import challenge
  made from another product's export, `challenge.xml` is that product's file, and
  `defective.xml` is still the suite's own file with one planted defect: a defect is
  planted where the layout of the file is known. That question is advisory, as in every
  import challenge.
- **A pairwise challenge needs an export of the same suite version.** The questions are
  drawn from the suite's data for a seed, and each suite version draws its own. An export
  graded under suite 1.0-beta.2 cannot be the source of a 1.0-beta.3 import challenge:
  grade the exporting product against a new challenge first.
- **Very many parameters on a very long test.** A procedure parameter (and a vane torque)
  applies at every depth its test has a row at. The first of a test always does; past
  that, one file is read up to a million such placements, and a file with more has the
  rest reported as missing. A challenge needs a few thousand.
- **Dissipation series by rank, by elapsed time.** A series has no depth of its own in
  DIGGS, so it is matched to the source test of the same rank by depth in the sounding. A
  file that leaves out one test of a sounding, or gives a test's result and its series in
  two `Test`s, has the series of its deeper tests compared with the wrong ones. Only an
  elapsed-time series is read: a series placed by clock time (`TimePositionList`) is not.
- **Remoulded vane strength.** DIGGS 3.0.0 has no property for it, so the suite's choice,
  `shear_strength_undrained_residual`, is a convention, not the standard's. An export that
  writes it under another property fails that value.
- **Not graded in `in-situ-strength`.** Pressuremeter, pocket and in-situ penetrometer and
  in-situ CBR tests are in the family but not yet in its challenge; a pass covers vane and
  dilatometer tests only. Vane torque increments (the torque-rotation curve) and the
  dilatometer's derived indices are not graded.

## Evidence anyone can re-run

`grade` writes `evidence.json`, a
[diggs-conformance](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.3.schema.json)
document: the suite and pyDIGGS versions, the class, the challenge, the SHA-256 of the
submission, every stage, and the verdict. It holds no time and no machine detail.

The challenge has two names. `challenge.id` is its identity: a SHA-256 of what defines it
(suite version, class, DIGGS version, seed, token and product, as canonical JSON; and the
`source_export` of a [pairwise challenge](#test-two-products-together)), the
same in every pyDIGGS release. `challenge.sha256` is the digest of `challenge.json` as
written, which lists the digests of the challenge's files; some of those files are
written by pyDIGGS itself (the DIGGS file of an import challenge, the tables of an export
challenge), so another release may write them, and that digest, differently.

`verify` compares the verdict, the stages, the counts, what was expected and found, and
the challenge by its `id`. A different `challenge.sha256` is reported as a note, not a
failure. It does not compare the wording of messages or the pyDIGGS release. Evidence of
schema 1.0-beta.1 (pyDIGGS 1.15 and 1.16), which has no `id`, is still verified, by the
digest as before.

To let someone check your result, give them the challenge folder, the file you submitted
and the evidence. They run:

```console
$ pydiggs conformance verify challenge/evidence.json challenge my-export.xml
Reproduced: spt-export pass.
```

The schema of the evidence is
[`diggs-conformance-1.0-beta.3`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.3.schema.json).
The earlier ones stay published, and `verify` still reads evidence written to them:
[`1.0-beta.2`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.2.schema.json)
(pyDIGGS 1.17) and
[`1.0-beta.1`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.1.schema.json)
(pyDIGGS 1.15 and 1.16).

### Versions

Two things have a version, and they change for different reasons.

The **suite version** is the classes, the data a seed gives, the questions and the
grading rules. It changes whenever a file could be graded differently, and each suite
version draws its own data, so a seed gives other borings and values in another suite.

| Suite | pyDIGGS | What changed in how a file is graded |
| --- | --- | --- |
| `1.0-beta.1` | 1.15 | The first suite: `spt` and `cpt`. |
| `1.0-beta.2` | 1.16, 1.17 | A value where the source leaves the cell empty fails (`conformance.value-invented`); a percentage may be a plain percent number; `lab-index` and `deep-foundations` were added. The `mwd`, `dissipation` and `in-situ-strength` classes joined this suite later, reading a declared `nullValue` as empty from the start. |
| `1.0-beta.3` | 1.18 | A cell that holds the `nullValue` its property declares is empty in `spt`, `cpt`, `lab-index` and `deep-foundations` too: a correct export that marks an unmeasured value that way failed in those classes before, and passes now. A file that declares as `nullValue` a number it also reports as a real value (a `nullValue` of 0 on a column with a real zero) passed before and fails now: that cell is missing, as DIGGS defines it. The `monitoring` classes joined this suite in the release after 1.18; no file of another class is graded differently for it. |

**What an older suite version on evidence means.** Evidence states the suite that graded
it (`suite_version`), and it stays true of that suite: the file passed, or failed, by
the rules that suite had. This release grades challenges of suite `1.0-beta.2` and
`1.0-beta.3` (`conformance.GRADED_SUITES`), each by its own rules, so `grade` and
`verify` on a `1.0-beta.2` challenge folder give what pyDIGGS 1.16 and 1.17 gave, and its evidence
still verifies. A `1.0-beta.1` challenge is refused with a message that says so: install
the release that has that suite, or make a new challenge. New challenges are always of
the current suite. A [scorecard](#scorecard) and a [matrix](#round-trip-matrix) are each
of one suite version, the current one: they refuse or list evidence of another suite and
do not count it, because a pass under different rules is not the same statement. To be
counted there, grade the product against a new challenge.

The **evidence schema version** is the shape of `evidence.json`.

| Evidence schema | pyDIGGS | What changed in the document |
| --- | --- | --- |
| `1.0-beta.1` | 1.15, 1.16 | The first document. |
| `1.0-beta.2` | 1.17 | Adds `challenge.id`. |
| `1.0-beta.3` | 1.18 | Adds the optional `source_export`, which only evidence of a pairwise import challenge has. A document without it means what a `1.0-beta.2` document means. |

To show that a result came from your own build, sign the evidence in your CI: see
[Signing conformance evidence](conformance-signing.md). How pyDIGGS's own verdicts compare
with the DIGGS project's XSLT validator on the public corpus is on
[Agreement with the DIGGS validator](conformance-agreement.md).

## In your browser

[The conformance page](conformance/try/index.html) offers `new`, `grade` and `verify` on a
web page, for a reviewer or vendor who would rather not install Python. pyDIGGS runs in the
tab on [Pyodide](browser.md), in a Web Worker: the files you choose are read there and never
uploaded, and once pyDIGGS has loaded the page makes no request at all. It loads Pyodide
from jsDelivr and the pyDIGGS wheel from PyPI, each pinned by version and checked against
its hash, so the page runs a released pyDIGGS, which it names, and may lag a release
behind this documentation.

It is the same suite: a challenge made on the page is the folder `pydiggs conformance new`
writes (as a ZIP), its evidence is the document `grade` writes for the same files, and
each side verifies the other's. A result is self-tested there too.

The page names the pyDIGGS release it runs. That is pyDIGGS 1.18: it offers the 14
classes of that release (`spt`, `cpt`, `dissipation`, `in-situ-strength`, `mwd`,
`lab-index` and `deep-foundations`, export and import), its challenges and evidence are of
suite 1.0-beta.3, and it still grades and verifies a challenge of suite 1.0-beta.2
([versions](#versions)). The `monitoring` classes are newer than that release: until the
page runs a release that has them, make and grade their challenges on the command line.

A [pairwise challenge](#test-two-products-together) is command-line only. The page does
not make one (`new --from-export`), and it refuses to grade or verify one: such a
challenge is graded with the other product's export as `challenge.xml` in its folder,
which the page does not take.

## Scorecard

```console
$ pydiggs conformance scorecard spt/evidence.json cpt/evidence.json
$ pydiggs conformance scorecard */evidence.json --format markdown
```

A scorecard shows one product against every part of the catalogue: pass, fail, not run, or
not yet testable. The Markdown form is made for a README or a product page.

A scorecard is of the current suite version: evidence of another suite is refused
([versions](#versions)). Evidence of a [pairwise import challenge](#test-two-products-together)
is left out: it is a test of one product on another's file, which is a cell of the
[matrix](#round-trip-matrix), not a class.

## Check a delivered file (owner acceptance)

!!! info "Preview (since pyDIGGS 1.18)"
    The requirements, their ids and the JSON document may still change.

A conformance class tests software against a challenge whose content the suite knows. An
owner who receives a DIGGS file from a consultant or a laboratory has no such key.
`accept` checks what can be checked of any file: it is valid DIGGS 3, and it holds what
DIGGS defines for the kind of data it is delivered as.

```console
$ pydiggs conformance accept B-030.xml --class spt --report acceptance.html
spt: ACCEPTED
  file     B-030.xml, 26500 bytes
  sha256   5ffc8827f8457258b1e9de5343cea7db91e1e973bdc933da6ec20d9ae7772ba4
  checked  Standard penetration and other driven tests requirements, suite 1.0-beta.3, DIGGS 3.0.0

  PASS     accept.diggs-3   (1/1) The file is a DIGGS 3 document, read to its end within the reader's limits.
  PASS     accept.valid     (1/1) The file has no validation error: XML Schema, dictionaries, Schematron business rules and cross-references (warnings are allowed).
  PASS     accept.present   (1/1) The file holds at least one test of standard penetration and other driven tests: a measurement whose procedure is DrivenPenetrationTest.
  PASS     accept.located   (15/15) Every such test names the sampling feature (borehole, sounding) it was made in, directly or through the sample it was made on, and that feature is in the file.
  PASS     accept.result    (15/15) Every such test reports at least one value of a property that the DIGGS properties dictionary defines for its procedure. A cell that is empty, or is the nullValue its property declares, is not a value.
  PASS     accept.position  (15/15) Every such result value is placed along its feature: a depth or position in a length unit DIGGS can convert.
  ...
```

`--class` is one of `spt`, `cpt`, `dissipation`, `in-situ-strength`, `mwd`, `lab-index`,
`deep-foundations` and `monitoring`. `--format json`
prints the result as a
[`diggs-conformance-acceptance-1.0-beta.1`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-acceptance-1.0-beta.1.schema.json)
document (preview; also `conformance.conformance_schema("acceptance")`), `--format html` prints the one-page report, and `--report PATH` also writes
it. The report names the file by its SHA-256, so it cannot be passed off for another
file. The exit status is 0 when every requirement is met, 1 when one is not (a file that
is not DIGGS included), and 2 when the file cannot be read or the report cannot be
written. The file is untrusted, with the same limits as `grade` (`--max-bytes`,
`--time-budget`); a file cut short by a limit fails `accept.diggs-3`, and what the family
asks of its content (`accept.present` and the requirements after it) is then `skipped`:
nothing true can be said of a file that was read in part.

### The requirements

Every requirement comes from DIGGS 3.0.0 or its dictionaries; none is an engineering
judgement. `pydiggs.conformance.requirements(family)` lists them with their source.

| Id | What must hold | From |
| --- | --- | --- |
| `accept.diggs-3` | The file is a DIGGS 3 document, read to its end. | DIGGS 3.0.0 root element and namespace |
| `accept.valid` | No validation error (warnings allowed). | DIGGS 3.0.0 XML Schema, the DIGGS dictionaries, the DIGGS Schematron rules |
| `accept.present` | At least one test of the family: a measurement whose procedure is one of the family's elements (`DrivenPenetrationTest` for `spt`; `StaticConePenetrationTest` for `cpt`; `PorePressureDissipationTest` for `dissipation`; `InsituVaneTest`, `FlatPlateDilatometerTest` or another of the family's procedures for `in-situ-strength`; the ten laboratory index procedures). For piles: at least one pile and one `PileDrivingRecord`. For `mwd`: at least one `MeasurementWhileDrilling` log. For `monitoring`: at least one `Monitor` series. | The family's DIGGS 3.0.0 elements ([classes](#classes-and-what-cannot-be-tested-yet)) |
| `accept.located` | Every such test names the borehole or sounding it was made in, directly or through its sample, and that feature is in the file. For `mwd`: every drilling log names its borehole or sounding. For `monitoring`: every series names its well, borehole or station. For piles: every driving record's `PileDrivingActivity` names one pile of the file, and its tip locations are on no other pile. | `samplingFeatureRef` and `sampleRef` of a measurement; `samplingFeatureRef` of a construction activity (exactly one) |
| `accept.result` | Every such test reports at least one value of a property the DIGGS dictionary defines for its procedure (for SPT: `n_value`, `n60`, `n1_60` or `relative_density`). For piles: every driving record has a value of a pile-driving property at a pile tip depth. For `mwd`: every log has a value of a drilling channel, a property `mwd_properties.xml` defines other than `depth` and `time`. For `monitoring`: every series has a value, and where the dictionary defines properties for the series' procedure (for `WaterLevelMonitoring`: `water_depth`, `water_elev`, `temperature`) it is of one of them. A cell that is empty, or is the `nullValue` its property declares, is not a value. | `outcome` of a test (required); the `Occurrence` entries of `properties.xml`, `pil_properties.xml` and `mwd_properties.xml`; `nullValue` of a `Property` |
| `accept.position` | Every such result value is placed along its feature, in a length unit DIGGS can convert. (Not for piles: `accept.result` asks for the tip depth. Not for `mwd`: DIGGS lets a drilling log be placed by depth or by time. Not for `monitoring`: a series is placed by time, see `accept.time`.) | `outcome`: "the associated locations that the measurement results relate to" |
| `accept.time` | For `monitoring` only: every series that reports a value gives one time, or one elapsed time, for each result row. | `outcome` of a `Monitor`: a `TemporalResult`, whose `timeDomain` is required |

!!! warning "What an acceptance check does not show"
    It does not compare any value with the field or laboratory records, so a wrong blow
    count passes. It does not show that every test of the job was delivered, makes no
    engineering judgement, does not show which software wrote the file, and is not a
    certification. The report and the JSON say so.

## Round-trip matrix

!!! info "Preview (since pyDIGGS 1.18)"
    The matrix needs evidence from at least two vendors to mean anything. Its format is
    fixed now so that vendors can publish evidence in the layout it reads.

```console
$ pydiggs conformance matrix evidence/
$ pydiggs conformance matrix evidence/ --format html > matrix.html
```

`matrix` reads every `evidence.json` under the folder. Each must sit in its challenge
folder, where `grade` writes it, with the file it graded beside it under the name the
evidence gives (`answers.csv` for an import class). Every document is **verified first**:
grading the challenge and the file again must give it back. Evidence that does not verify
(edited, of another suite version, its file missing) is listed with the reason and
**never counted**. Links are not followed: an `evidence.json` that is a link is listed as
"a link, not read".

The matrix shows each product (as its vendor declared it) against every class: pass, fail
or not run. A class passes when any verified evidence of it passed. Formats: text, `json` (a
[`diggs-conformance-matrix-1.0-beta.1`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-matrix-1.0-beta.1.schema.json)
document, preview; also `conformance.conformance_schema("matrix")`), `markdown` and `html`; every value from a file is escaped in the
text, Markdown and HTML. The exit status is 0 when every evidence document verified, 1
when one did not (the matrix is still written, without it), and 2 when the folder holds
no evidence or cannot be read.

It then lists the round trips, one cell for each exporting and importing product of a
family:

| Cell | When |
| --- | --- |
| `read` | The folder holds A's passing export evidence and evidence of a [pairwise import challenge](#test-two-products-together) made from that very file, both verified, and B's answers passed. |
| `not-read` | The same, and none of the answers given for B passed. |
| `not-tested-together` | No such pair of documents in the folder. Both products passed their class separately, against the suite's own challenge: "A's export passed" and "B's import passed". |

```text
Round trips (export by A, import by B):
  spt: LogA 2.0 -> Viewer 7: read (export sha256 519e60ede733289510fad9d2aa0ccc8aa7e26a11dc91164ca0bbb52888e4fbc8)
```

A `read` or `not-read` cell **cites the export file by its SHA-256** (`export_sha256` in
the JSON) and the import evidence that decides it (`evidence`, its path under the
folder), so anyone can check which file it was.

A cell is claimed only when **the exporter's own evidence is in the folder** beside the
pairwise evidence. What pairwise evidence says of its source is the importing party's
statement; the exporter's evidence is what confirms it. That evidence must:

- have verified and passed, in the export class of the same family;
- be for the product name and version `source_export` states, and for the same seed;
- be the very `evidence.json` the challenge was made from (`evidence_sha256`), for the
  very file (`export_sha256`).

Otherwise the pair is not claimed, and the pairwise evidence is listed with the note
that it "decides no round-trip cell". That happens when the exporter's folder is not
there, when its evidence does not verify, and when the exporter has graded again since
with another file: keep the exporter's challenge folder as it was when the import
challenge was made, or make the import challenge again. See
[what `read` means](#what-read-means-and-what-it-does-not).

Pairwise evidence decides its cell only. It does not count as the importer's
`<family>-import` class, on the matrix or on a [scorecard](#scorecard): a class is passed
against the suite's own file. The pairwise challenge folder must still hold the export as
`challenge.xml`; without it the evidence is listed as not verified.

## In Python

```python
from pydiggs import conformance

challenge = conformance.new("spt-export", product="MyLog", version="4.2.1")
folder = challenge.write("challenge")  # an empty or new folder; nothing is overwritten
[c.id for c in conformance.classes()]
```

<!-- skip: next "needs the file your software exported" -->
```python
result = conformance.grade("challenge", "my-export.xml")
result.passed  # the verdict
result.stage("fidelity").items  # what failed, each with expected and got
evidence = result.to_dict()  # the diggs-conformance document
conformance.verify(evidence, "challenge", "my-export.xml").ok
```

The acceptance check and the matrix:

```python
from pydiggs import conformance

challenge = conformance.new("cpt-export", product="MyLog", version="4.2.1", seed="demo")
result = conformance.accept(challenge.reference(), "cpt")
assert result.accepted
print(result.file_sha256, [r.id for r in conformance.requirements("cpt")])
```

<!-- skip: next "needs a folder of challenge folders with their evidence" -->
```python
grid = conformance.matrix("evidence")
grid.unverified  # what did not verify, with the reason
grid.cell(("MyLog", "4.2.1"), "spt-export")  # pass, fail or not-run
[(r.exporter, r.importer, r.status, r.export_sha256) for r in grid.round_trips()]
```

A pairwise import challenge, from another product's evidence and export (paths, or the
bytes of each):

```python
from pydiggs import conformance

theirs = conformance.new("cpt-export", product="LogA", version="2.0", seed="demo")
exported = theirs.reference()  # stands for the file LogA exported
evidence = conformance.grade(theirs, exported).to_json().encode()

pair = conformance.new(
    "cpt-import", product="Viewer", version="7", from_export=evidence, export=exported
)
assert pair.files["challenge.xml"] == exported
assert pair.source_export.product_name == "LogA"
print(pair.source_export.export_sha256)  # what the evidence and the matrix will cite
```

`conformance.grade` on its answers gives a `Result` whose `source_export` is the same
record, and whose evidence carries it.

The submission is untrusted input: its size is limited (`max_bytes`, 100 MiB by default),
the time spent reading and validating it can be limited (`time_budget`), and the text,
Markdown and HTML reports escape what comes from it. A submission that hits a limit
fails with `conformance.incomplete`.

## Check ids

| Id | Meaning |
| --- | --- |
| `conformance.not-diggs` | The submission is not a DIGGS document. |
| `conformance.diggs-version` | The file is not DIGGS 3. |
| `conformance.incomplete` | The file could not be read to its end (a limit or the time budget). |
| `conformance.project-name` | The project's name is not carried exactly. |
| `conformance.fact-missing` | A location, a depth or a value of the source is not in the file. |
| `conformance.value-differs` | The file gives another value where the source value belongs. |
| `conformance.value-invented` | The file gives a value where the source leaves the cell empty. |
| `conformance.answer-missing` | A question of an import challenge has no answer. |
| `conformance.answer-differs` | An answer differs from what the file holds. |

A failed validation stage lists the [rule ids](findings.md#rule-ids) of `pydiggs validate`.
