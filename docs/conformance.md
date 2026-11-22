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
  that writes a zero there fails with `conformance.value-invented`.
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

The answer key is built from the tables alone. pyDIGGS's own converter and reader are run
against it on randomly drawn seeds in every run of pyDIGGS's tests, like any other
software. `Challenge.reference()` in Python gives that known-good export, to try the suite
or to compare with your own file.

The validation stage is pyDIGGS's validator of the release you run, and the evidence
records that release beside the suite version.

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

## Scorecard

```console
$ pydiggs conformance scorecard spt/evidence.json cpt/evidence.json
$ pydiggs conformance scorecard */evidence.json --format markdown
```

A scorecard shows one product against every part of the catalogue: pass, fail, not run, or
not yet testable. The Markdown form is made for a README or a product page.

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
