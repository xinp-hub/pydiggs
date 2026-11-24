# Testing DIGGS support (conformance suite)

`pydiggs conformance` tests how a piece of software reads and writes DIGGS 3. You run it on
your own machine: there is no account, nothing is uploaded, and the result can be re-run by
anyone you give the files to.

<!-- tutorial: conformance -->

!!! info "Preview (since pyDIGGS 1.15)"
    The classes, the checks and the evidence document may still change before they are
    frozen. The suite has its own version (`1.0-beta.2`), which changes whenever a file
    could be graded differently. A challenge is graded by the suite version that made it.

!!! warning "What a result means"
    A result is **self-tested**. It shows that a file passed a published test. It does not
    show which software wrote the file, and it is **not a certification** by the DIGGS
    committee or the ASCE Geo-Institute. Every evidence document says so.

- **JSON Schema of the evidence:**
  [`schemas/preview/diggs-conformance-1.0-beta.2.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.2.schema.json)
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
  suite       1.0-beta.2, DIGGS 3.0.0, self-tested
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
  that writes a zero there fails with `conformance.value-invented`. In the `mwd`,
  `dissipation` and `in-situ-strength` classes a cell that holds the `nullValue` its
  property declares is empty too ([not yet in the others](#known-limits-of-this-version)).
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

The catalogue behind the classes covers the whole of DIGGS 3.0.0: every test procedure of
the schema, piles and driving records, grouting, monitoring, geophysics and the rest. A part
with no challenge yet is listed as **not yet testable**, here and on every scorecard, so a
few passing classes are never mistaken for full DIGGS support.

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
- **`nullValue` in the classes of suite 1.0-beta.2.** DIGGS lets a property declare a
  `nullValue`, the text that marks a missing value in its data. The `mwd`, `dissipation`
  and `in-situ-strength` classes read a cell with that mark as empty, and so does
  `accept`. The `spt`, `cpt`, `lab-index` and `deep-foundations` classes do not yet: an
  empty source cell written as, say, `-999` with `<nullValue>-999</nullValue>` fails with
  `conformance.value-invented`, as it has since pyDIGGS 1.17. Leave the cell empty in
  those classes. Reading the mark there changes how a file is graded, so it comes with
  the next suite version.
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
[diggs-conformance](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.1.schema.json)
document: the suite and pyDIGGS versions, the class, the challenge, the SHA-256 of the
submission, every stage, and the verdict. It holds no time and no machine detail.

The challenge has two names. `challenge.id` is its identity: a SHA-256 of what defines it
(suite version, class, DIGGS version, seed, token and product, as canonical JSON), the
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
[`diggs-conformance-1.0-beta.2`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.2.schema.json);
[`1.0-beta.1`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.1.schema.json),
which pyDIGGS 1.15 and 1.16 wrote, stays published.

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

## Scorecard

```console
$ pydiggs conformance scorecard spt/evidence.json cpt/evidence.json
$ pydiggs conformance scorecard */evidence.json --format markdown
```

A scorecard shows one product against every part of the catalogue: pass, fail, not run, or
not yet testable. The Markdown form is made for a README or a product page.

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
  checked  Standard penetration and other driven tests requirements, suite 1.0-beta.2, DIGGS 3.0.0

  PASS     accept.diggs-3   (1/1) The file is a DIGGS 3 document, read to its end within the reader's limits.
  PASS     accept.valid     (1/1) The file has no validation error: XML Schema, dictionaries, Schematron business rules and cross-references (warnings are allowed).
  PASS     accept.present   (1/1) The file holds at least one test of standard penetration and other driven tests: a measurement whose procedure is DrivenPenetrationTest.
  PASS     accept.located   (15/15) Every such test names the sampling feature (borehole, sounding) it was made in, directly or through the sample it was made on, and that feature is in the file.
  PASS     accept.result    (15/15) Every such test reports at least one value of a property that the DIGGS properties dictionary defines for its procedure. A cell that is empty, or is the nullValue its property declares, is not a value.
  PASS     accept.position  (15/15) Every such result value is placed along its feature: a depth or position in a length unit DIGGS can convert.
  ...
```

`--class` is one of `spt`, `cpt`, `dissipation`, `in-situ-strength`, `mwd`, `lab-index`
and `deep-foundations`. `--format json`
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
| `accept.present` | At least one test of the family: a measurement whose procedure is one of the family's elements (`DrivenPenetrationTest` for `spt`; `StaticConePenetrationTest` for `cpt`; `PorePressureDissipationTest` for `dissipation`; `InsituVaneTest`, `FlatPlateDilatometerTest` or another of the family's procedures for `in-situ-strength`; the ten laboratory index procedures). For piles: at least one pile and one `PileDrivingRecord`. For `mwd`: at least one `MeasurementWhileDrilling` log. | The family's DIGGS 3.0.0 elements ([classes](#classes-and-what-cannot-be-tested-yet)) |
| `accept.located` | Every such test names the borehole or sounding it was made in, directly or through its sample, and that feature is in the file. For `mwd`: every drilling log names its borehole or sounding. For piles: every driving record's `PileDrivingActivity` names one pile of the file, and its tip locations are on no other pile. | `samplingFeatureRef` and `sampleRef` of a measurement; `samplingFeatureRef` of a construction activity (exactly one) |
| `accept.result` | Every such test reports at least one value of a property the DIGGS dictionary defines for its procedure (for SPT: `n_value`, `n60`, `n1_60` or `relative_density`). For piles: every driving record has a value of a pile-driving property at a pile tip depth. For `mwd`: every log has a value of a drilling channel, a property `mwd_properties.xml` defines other than `depth` and `time`. A cell that is empty, or is the `nullValue` its property declares, is not a value. | `outcome` of a test (required); the `Occurrence` entries of `properties.xml`, `pil_properties.xml` and `mwd_properties.xml`; `nullValue` of a `Property` |
| `accept.position` | Every such result value is placed along its feature, in a length unit DIGGS can convert. (Not for piles: `accept.result` asks for the tip depth. Not for `mwd`: DIGGS lets a drilling log be placed by depth or by time.) | `outcome`: "the associated locations that the measurement results relate to" |

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
or not run. A class passes when any verified evidence of it passed. It then lists the
round trips: every pair of a product that passed a family's export class and one that
passed its import class. Formats: text, `json` (a
[`diggs-conformance-matrix-1.0-beta.1`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-matrix-1.0-beta.1.schema.json)
document, preview; also `conformance.conformance_schema("matrix")`), `markdown` and `html`; every value from a file is escaped in the
text, Markdown and HTML. The exit status is 0 when every evidence document verified, 1
when one did not (the matrix is still written, without it), and 2 when the folder holds
no evidence or cannot be read.

!!! warning "Every round trip says `not-tested-together`"
    Today each product passes its class against the suite's own challenge, so the matrix
    can say "A's export passed" and "B's import passed", never "B read A's export". That
    needs an import challenge made from A's file, which no challenge or evidence records
    yet. The cell's values `read` and `not-read` are reserved for that evidence.

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
```

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
