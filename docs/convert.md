# AGS4 to DIGGS

pyDIGGS converts AGS4 files to **DIGGS 3.0.0** documents and tells you exactly what did
not make it across. The conversion is the same in Python, on the command line, over HTTP,
in the stdio sidecar and in the browser: the same input gives the same bytes everywhere.

!!! warning "Preview"
    The conversion uses mapping table **0.1**, and every group mapping in it is a draft
    (see the [AGS4 to DIGGS mapping](convert-mapping.md)). In Python it lives in
    `pydiggs.preview.convert`, outside Semantic Versioning; the stable
    `pydiggs.convert` API and mapping table 1.0 are planned for pyDIGGS 1.10. Output may
    change between releases while the table is reviewed. Corrections from DIGGS and AGS
    practitioners are welcome.

New output is DIGGS 3.x only: the converter writes DIGGS 3.0.0, the bundled stable 3.x
schema. Reading and validating DIGGS 2.x files is unchanged. pyDIGGS bundles no AGS data
dictionary or example file and is not an AGS-approved tool (see [AGS4 files](ags4.md)).

## Converting

=== "Python"

    ```python
    from pydiggs.preview import convert

    c = convert.ags4_to_diggs("site.ags")
    c.xml  # bytes: the DIGGS 3.0.0 document
    c.model  # pydiggs.Model: that document read back
    c.report  # findings about the source: AGS4 notes and convert.* findings
    c.losses  # the convert findings that say something was lost or assumed
    c.lossless  # True when there are none
    c.validation  # pydiggs.validate_bytes on c.xml
    c.trace["BH01"]  # Origin(group='LOCA', row=1, line=60)
    c.complete  # False when a size or time limit stopped the conversion
    c.save("site.xml")
    ```

=== "Command line"

    ```bash
    pydiggs convert site.ags -o site.xml --report site.findings.json
    pydiggs validate site.xml
    ```

    | option | meaning |
    |---|---|
    | `-o OUT` | where to write the DIGGS XML (required) |
    | `--report FILE` | also write the findings report (diggs-findings 1.1 JSON) |
    | `--format text\|json` | the summary on stdout; JSON is the result without the output bytes |
    | `--fail-on error\|loss` | exit 1 on an error (default), or on any loss as well |
    | `--force` | replace `OUT` and `--report` if they exist |
    | `--to diggs` | the target format; `diggs` is the only one so far |
    | `--diggs-version 3.0.0` | the DIGGS version to write |
    | `--max-bytes N`, `--time-budget SECONDS` | limits (default 100 MiB, no time limit) |

    Exit codes follow `pydiggs validate`: **0** no finding at or above `--fail-on`;
    **1** some (with `error`: an error in the report, or the output fails validation;
    with `loss`: also any loss or an incomplete conversion); **2** a usage error, an
    input that cannot be read or is not AGS4, or an output that cannot be written.

    Outputs are written to a temporary file beside the target and moved into place, so a
    reader never sees half a file. An existing output is replaced only with `--force`,
    an output that is the input itself is always refused, and a symlinked output is
    refused.

=== "HTTP"

    ```bash
    curl -s -F file=@site.ags http://127.0.0.1:8000/v1/convert \
      | jq -r .output_base64 | base64 --decode > site.xml
    ```

    See [`POST /v1/convert`](http-api.md#post-v1convert).

=== "Sidecar"

    ```json
    {"jsonrpc": "2.0", "id": 7, "method": "convert", "params": {"content_base64": "…", "name": "site.ags"}}
    ```

    Protocol 1.2; see [`convert`](sidecar.md#convert).

The HTTP API and the sidecar answer with the same **conversion result**, a JSON object:

| field | meaning |
|---|---|
| `to` | the output format, `"diggs"` |
| `media_type` | `application/xml` |
| `output_name` | a file name for the output: the input's name with `.xml` |
| `diggs_version` | `"3.0.0"` |
| `mapping_version` | the mapping table used (`"0.1"`) |
| `complete` | false when a size or time limit stopped the conversion; the output is then partial |
| `lossless` | true when there is no loss |
| `report` | diggs-findings 1.1 document about the source: the AGS4 reader's notes and every `convert` finding |
| `validation` | diggs-findings 1.1 document from validating the output |
| `output_sha256` | sha256 of the output bytes |
| `output_base64` | the output bytes, standard base64 |

`pydiggs convert --format json` prints the same object without `output_base64`.

## What is converted

The draft table covers the common groups (Tier A): PROJ, TRAN, the ABBR, UNIT, TYPE and
DICT lookups, LOCA, GEOL, SAMP, ISPT, LLPL, GRAG with GRAT, and LNMC. The
[mapping page](convert-mapping.md), generated from the table itself, lists every heading
with its DIGGS target, or the reason it is not converted.

Values are carried **as reported**: units are renamed only through the unit table, and
nothing is converted numerically. The output is validated, and on the test files it is
valid DIGGS 3.0.0 with no errors.

## Losses

Nothing is dropped silently: every non-null value of the source is either carried into
DIGGS or named by a `convert` finding.

| rule id | severity | meaning |
|---|---|---|
| `convert.unmapped-group` | warning | a whole group has no mapping; its rows are not converted |
| `convert.unmapped-heading` | warning | a heading of a mapped group has no target; its values are not converted |
| `convert.user-defined` | warning | a group or heading the file's own DICT group defines: reported, never interpreted |
| `convert.unmapped-code`, `convert.unmapped-unit` | warning | a code or unit with no entry in the tables |
| `convert.value-unparsed` | warning | a value that does not parse as its declared type |
| `convert.assumed` | warning | DIGGS needs a value the source lacks; a documented default was written |
| `convert.orphan-row` | error | a row whose parent record is missing; it is not converted |
| `convert.incomplete` | error | a size or time limit stopped the conversion |
| `convert.value-reformatted` | info | the same value in another form (a date in ISO 8601); not a loss |

A group-level finding stands for every cell in the group; findings are not repeated per
cell. Each finding has the source `line` and, in `location.path`, the place in the file
(`GROUP/row/HEADING`). The full list is in [Findings](findings.md#rule-ids).

**Coming in pyDIGGS 1.10:** a *residual* AGS4 file holding exactly the groups and
headings that were not converted (so DIGGS output plus residual is everything the source
held), DIGGS to AGS4, and more groups (Tier B). This branch does not have them yet.

## Limits

| | Python / CLI | HTTP API | sidecar |
|---|---|---|---|
| Largest input | 100 MiB | `max_body_bytes` (25 MiB) | `--max-bytes`, `--max-path-bytes` |
| Largest result | none | 4 × `max_body_bytes` (422 `result-too-large`) | 256 MiB (`result-too-large`) |
| Time | `time_budget` (none) | `timeout_s`: a partial result with thread isolation, 504 with process isolation | `time_budget_s` up to `--timeout` |

The DIGGS output is usually several times the size of the AGS4 input (about 4 to 5
times on the test files, and the base64 in a result adds a third). A time budget that
runs out gives a result with `complete: false` and `convert.incomplete`, never an
exception. In the sidecar, `$/cancelRequest` stops a queued conversion; a running one is
checked only when it ends. Every AGS4 file is untrusted: the [AGS4 reader's
limits](ags4.md#reading) apply, nothing the file names is opened, and nothing is
fetched.
