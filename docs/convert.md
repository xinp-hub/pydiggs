# AGS4 and DIGGS conversion

pyDIGGS converts AGS4 files to **DIGGS 3.0.0** documents, and DIGGS documents to **AGS4**
files, and tells you exactly what did not make it across. The conversion is the same in
Python, on the command line, over HTTP, in the stdio sidecar and in the browser: the same
input gives the same bytes everywhere.

`pydiggs.convert` is **stable** from pyDIGGS 1.10 and follows Semantic Versioning. Both
directions use one bundled mapping table, **1.0**, whose 25 groups have all been
reviewed against the AGS4 4.1.1 and 4.2 dictionaries and DIGGS 3.0.0; the generated
[AGS4 and DIGGS mapping](convert-mapping.md) page lists every heading with its DIGGS
target, or the reason it is not converted. Changing where a heading goes is a new major
version of the table; adding groups, headings or codes is a minor one. Corrections from
DIGGS and AGS practitioners are welcome.

New DIGGS output is DIGGS 3.x only: the converter writes DIGGS 3.0.0, the bundled stable
3.x schema. DIGGS to AGS4 reads any DIGGS version [`pydiggs.read`](model.md) reads. pyDIGGS
bundles no AGS data dictionary or example file and is not an AGS-approved tool (see
[AGS4 files](ags4.md)).

## AGS4 versions written

DIGGS to AGS4 writes **AGS4 4.2** by default, or **4.1.1** with `ags_version="4.1.1"`
(`--ags-version 4.1.1`, `ags_version=4.1.1` over HTTP and in the sidecar). AGS lists 4.2 as
the current edition and 4.1.1 as deprecated.

!!! warning "Validators that stop at 4.1.1"
    As of November 2026 the BGS AGS validator (agsapi.bgs.ac.uk) and python-ags4 1.2.0
    accept AGS4 only up to 4.1.1. Write `ags_version="4.1.1"` for files meant for them.

The two versions differ in what is written: `TRAN_AGS` is `4.2` (the value python-ags4
recognises) or `4.1.1`; static cone tests go to CPTG/CPTT in 4.2 and SCPG/SCPT in 4.1.1;
and 4.2 output also gives the CRS as an EPSG code (`LOCA_NSRI`, `LOCA_LLSI`). AGS4 to
DIGGS reads 4.0.3, 4.0.4, 4.1, 4.1.1 and 4.2.

## Converting

=== "Python"

    ```python
    from pydiggs import ags4, convert

    # AGS4 to DIGGS
    c = convert.ags4_to_diggs("site.ags")
    c.xml  # bytes: the DIGGS 3.0.0 document
    c.model  # pydiggs.Model: that document read back
    c.residual  # an ags4.File of what was not converted
    c.report  # findings about the source: AGS4 notes and convert.* findings
    c.losses  # the convert findings that say something was lost or assumed
    c.lossless  # True when there are none
    c.validation  # pydiggs.validate_bytes on c.xml
    c.trace["BH01"]  # Origin(group='LOCA', row=1, line=60)
    c.complete  # False when a size or time limit stopped the conversion
    c.save("site.xml")
    ags4.write(c.residual, "site.residual.ags")

    # DIGGS to AGS4
    back = convert.diggs_to_ags4("site.xml")  # or ags_version="4.1.1"
    back = convert.model_to_ags4(c.model)  # a Model already in memory
    back.ags4  # the ags4.File; ags4.dumps(back.ags4) gives its bytes
    back.validation  # pydiggs.ags4.check on the output
    back.losses, back.lossless
    back.save("site.ags")
    ```

    `Conversion.xml` is the DIGGS document for AGS4 to DIGGS and None for DIGGS to
    AGS4; `Conversion.ags4` is the other way round. `convert.mapping_info()` describes
    the bundled table.

=== "Command line"

    ```bash
    pydiggs convert site.ags -o site.xml --report site.findings.json --residual left.ags
    pydiggs validate site.xml
    pydiggs convert site.xml -o site.ags --to ags4                   # AGS4 4.2
    pydiggs convert site.xml -o site.ags --to ags4 --ags-version 4.1.1
    pydiggs ags4 check site.ags
    ```

    | option | meaning |
    |---|---|
    | `-o OUT` | where to write the output (required) |
    | `--to diggs\|ags4` | the format to convert to: `diggs` (the default; `IN` is AGS4) or `ags4` (`IN` is DIGGS XML). The direction is never guessed from the input |
    | `--diggs-version 3.0.0` | `--to diggs` only: the DIGGS version to write |
    | `--ags-version 4.2\|4.1.1` | `--to ags4` only: the AGS4 version to write (default 4.2) |
    | `--residual FILE` | `--to diggs` only: also write the residual AGS4 file; not written when everything was converted (the summary then says `residual: none`) |
    | `--report FILE` | also write the findings report (diggs-findings 1.1 JSON) |
    | `--format text\|json` | the summary on stdout; JSON is the result without the output and residual bytes, with `output_path` and `residual_path` |
    | `--fail-on error\|loss` | exit 1 on an error (default), or on any loss as well |
    | `--force` | replace `OUT`, `--report` and `--residual` if they exist |
    | `--max-bytes N`, `--time-budget SECONDS` | limits (default 100 MiB, no time limit) |

    Exit codes follow `pydiggs validate`: **0** no finding at or above `--fail-on`;
    **1** some (with `error`: an error in the report, or the output fails validation or
    the AGS4 check; with `loss`: also any loss or an incomplete conversion); **2** a
    usage error, an input that cannot be read or is not of the expected format (AGS4,
    or DIGGS for `--to ags4`), or an output that cannot be written.

    Outputs are written to a temporary file beside the target and moved into place, so a
    reader never sees half a file. An existing output is replaced only with `--force`,
    an output that is the input itself is always refused, and a symlinked output is
    refused.

=== "HTTP"

    ```bash
    curl -s -F file=@site.ags http://127.0.0.1:8000/v1/convert > result.json
    jq -r .output_base64 result.json | base64 --decode > site.xml
    jq -r '.residual_base64 // empty' result.json | base64 --decode > left.ags

    curl -s -F file=@site.xml -F to=ags4 -F ags_version=4.1.1 \
      http://127.0.0.1:8000/v1/convert | jq -r .output_base64 | base64 --decode > site.ags
    ```

    See [`POST /v1/convert`](http-api.md#post-v1convert).

=== "Sidecar"

    ```json
    {"jsonrpc": "2.0", "id": 7, "method": "convert", "params": {"content_base64": "…", "name": "site.ags"}}
    {"jsonrpc": "2.0", "id": 8, "method": "convert", "params": {"content_base64": "…", "name": "site.xml", "to": "ags4", "ags_version": "4.2"}}
    ```

    Protocol 1.2; see [`convert`](sidecar.md#convert).

The HTTP API and the sidecar answer with the same **conversion result**, a JSON object:

| field | meaning |
|---|---|
| `to` | the output format: `"diggs"` or `"ags4"` |
| `media_type` | of the output: `application/xml` (DIGGS) or `text/plain; charset=utf-8` (AGS4, which has no registered media type) |
| `output_name` | a file name for the output: the input's name with `.xml` or `.ags` |
| `diggs_version` | of the DIGGS output, or of the DIGGS input for `to=ags4` |
| `ags_version` | of the AGS4 output (`"4.2"` or `"4.1.1"`); null for `to=diggs` |
| `mapping_version` | the mapping table used (`"1.0"`) |
| `complete` | false when a size or time limit stopped the conversion; the output is then partial |
| `lossless` | true when there is no loss |
| `report` | diggs-findings 1.1 document about the source: the reader's notes and every `convert` finding |
| `validation` | diggs-findings 1.1 document about the output: `pydiggs.validate` (DIGGS) or `pydiggs.ags4.check` (AGS4) |
| `output_sha256` | sha256 of the output bytes |
| `output_base64` | the output bytes, standard base64 |
| `residual_base64` | AGS4 to DIGGS: the residual AGS4 file, standard base64; null when everything was converted, and always null for `to=ags4` |

`pydiggs convert --format json` prints the same object without `output_base64` and
`residual_base64`.

## The residual file

AGS4 to DIGGS also gives a **residual** AGS4 file (`Conversion.residual`): exactly the
values a `convert` finding names, with the key fields of their rows, so that each row can
be placed again. The DIGGS output and the residual file together hold everything the
source did. A group appears in the residual file when at least one of its cells was not
converted, with its key headings and the headings of those cells, in the source's order;
units and types are the source's. When nothing was left over the residual file has no
group, the surfaces send `residual_base64: null` and the CLI writes no `--residual`
file. DIGGS to AGS4 has no residual file: what it does not write is named by
`convert.unmapped-element` findings.

## What is converted

Mapping table 1.0 covers PROJ, TRAN, the ABBR, UNIT, TYPE and DICT lookups, LOCA, GEOL,
SAMP, ISPT, LLPL, GRAG with GRAT, LNMC (Tier A) and HDIA, CDIA, BKFL, WSTG with WSTD,
LDEN, and static cone tests, SCPG with SCPT and CPTG with CPTT (Tier B). Hole orientation
(HORN) is listed and reported, not converted: AGS4 measures inclination positively down
and DIGGS 3.0.0 plunge positively up, and values are never converted. Any other group is
reported, and its rows are in the residual file.

Values are carried **as reported**: units are renamed only through the unit table, and
nothing is converted numerically. DIGGS to AGS4 writes a quantity's text as it was
written and a result-table number in its shortest form; pyDIGGS bundles no AGS4
dictionary, so each heading's TYPE is the one its values have, and the ABBR, TYPE and
UNIT groups list what the file uses. The DIGGS output is validated and the AGS4 output
checked: on the test files and the official examples both have no error. (AGS4 output
carries the source's non-ASCII text unchanged, which the check reports under Rule 1.)

Text a spreadsheet would read as a formula (starting with `=`, `+`, `@`, or a `-` that is
not a number) is written unchanged in AGS4 output and reported as `ags4.formula-text`;
`formula_guard="prefix"` writes such text under a non-key text heading with an apostrophe
in front instead, and reports it. That changes the value, so use it only for files meant
for a spreadsheet.

## Losses

Nothing is dropped silently: every non-null value of the source is either carried into
the output or named by a `convert` finding.

| rule id | severity | meaning |
|---|---|---|
| `convert.unmapped-group` | warning | AGS4 to DIGGS: a whole group has no mapping; its rows are not converted |
| `convert.unmapped-heading` | warning | AGS4 to DIGGS: a heading of a mapped group has no target; its values are not converted |
| `convert.user-defined` | warning | AGS4 to DIGGS: a group or heading the file's own DICT group defines: reported, never interpreted |
| `convert.unmapped-element` | warning | DIGGS to AGS4: a value of the model (a name or remark, a `params` entry, a typed field, an `extra` element, a link, a result-table cell) has no AGS4 target, or cannot be written there as it is |
| `convert.unmapped-code`, `convert.unmapped-unit` | warning | a code or unit with no entry in the tables |
| `convert.value-unparsed` | warning | a value that does not parse as its declared type |
| `convert.assumed` | warning | the output needs a value the source lacks; a documented default was written (or, for AGS4's `TRAN_DATE`, the field was left empty) |
| `convert.orphan-row` | error | a row whose parent record is missing; it is not converted |
| `convert.incomplete` | error | a size or time limit stopped the conversion, or DIGGS requires something of a record that the source does not give |
| `convert.value-reformatted` | info | the same value in another form (a date in ISO 8601); not a loss |

Some losses worth knowing before you convert:

- **GRAG fractions are not converted, either way.** `GRAG_VCRE`, `GRAG_GRAV`,
  `GRAG_SAND`, `GRAG_SILT` and `GRAG_FINE` are reported: AGS4 uses the ISO 14688-1 size
  limits (63 mm, 2 mm, 63 µm) and the DIGGS properties the ASTM ones (75 mm, 4.75 mm,
  0.075 mm), and values are never converted.
- **A GRAG row with no other result is incomplete.** A row that gives none of
  `GRAG_UC`, `GRAG_CLAY` or `GRAG_CC` cannot be written as a DIGGS test: it is reported
  as `convert.incomplete` (an error, so `pydiggs convert` exits 1), and its GRAT readings
  are reported too. Its values are in the residual file.
- `GEOL_GEOL` (a project geology code) and `LOCA_LOCA` (a grouping of locations) are
  reported; `GEOL_FORM` is the DIGGS `unitName`.
- Static cone tests whose `SCPG_TYPE` or `CPTG_TYPE` is `BALL` or `T-BAR` are reported,
  not written: their resistance is not a cone resistance.
- `LNMC_MC` with `LNMC_ISNT` = N, the density and moisture content of a `RECOMPACTED`
  `LDEN` specimen, and an SPT N from a test drive that stopped short of 300 mm are
  reported instead of being written as DIGGS properties for natural conditions.
- A missing or unreadable `TRAN_DATE` is written as an unknown DIGGS creation date
  (`convert.assumed`); DIGGS to AGS4 leaves the REQUIRED `TRAN_DATE` empty for such a
  document and reports it.

A group-level finding stands for every cell in the group; findings are not repeated per
cell. A finding about AGS4 has the source `line` and, in `location.path`, the place in
the file (`GROUP/row/HEADING`); one about DIGGS has `location.xpath`. The full list is in
[Findings](findings.md#rule-ids).

## Limits

| | Python / CLI | HTTP API | sidecar |
|---|---|---|---|
| Largest input | 100 MiB (`max_bytes`, `--max-bytes`) | `max_body_bytes` (25 MiB) | `--max-bytes`, `--max-path-bytes` |
| Largest result | none | 4 × `max_body_bytes` (422 `result-too-large`) | 256 MiB (`result-too-large`) |
| Time | `time_budget` (none) | `timeout_s`: a partial result with thread isolation, 504 with process isolation | `time_budget_s` up to `--timeout` |

The DIGGS output is usually several times the size of the AGS4 input (about 4 to 5
times on the test files, and the base64 in a result adds a third). A time budget that
runs out gives a result with `complete: false`, never an exception: AGS4 to DIGGS
reports `convert.incomplete`, and DIGGS to AGS4 converts the members read so far. In
the sidecar, `$/cancelRequest` stops a queued conversion; a running one is checked only
when it ends. Every input is untrusted: the [AGS4 reader's limits](ags4.md#reading) and
the [DIGGS reader's](model.md#reading-a-file) apply, nothing a file names is opened, and
nothing is fetched.

## From the 1.9 preview

pyDIGGS 1.9.0 shipped AGS4 to DIGGS as a preview in `pydiggs.preview.convert`, with
mapping table 0.1. From 1.10 `pydiggs.preview.convert` is a deprecated alias of
`pydiggs.convert` (using one of its names warns), and it will be removed in pyDIGGS 2.0.
Replace `from pydiggs.preview import convert` with `from pydiggs import convert`. The
same AGS4 file converts to different DIGGS than in 1.9.0; the CHANGELOG lists what moved
between mapping tables 0.1 and 1.0.
