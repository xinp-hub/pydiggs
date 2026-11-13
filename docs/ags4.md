# AGS4 files

AGS4 is the quoted, comma-separated exchange format for ground-investigation data
published by the Association of Geotechnical and Geoenvironmental Specialists (AGS).
`pydiggs.ags4` reads AGS4 files into tables of strings, writes them back the same way
every time, and checks them against the AGS4 rules. To turn an AGS4 file into DIGGS, see
[AGS4 to DIGGS](convert.md).

!!! note "Not an AGS-approved tool; no AGS files bundled"
    pyDIGGS implements the published AGS4 rules (section 4.1.1 of the AGS4 specification,
    Edition 4.2, December 2025) independently. It is not an AGS-approved validator, and it
    bundles **no AGS data dictionary and no AGS example file**. Where a rule needs the
    dictionary, you supply your own copy (see [The data dictionary](#the-data-dictionary)).

Versions 4.0.3, 4.0.4, 4.1, 4.1.1 and 4.2 are read and written (`ags4.AGS_VERSIONS`); the
4.2 foreword says the rules are unchanged from 4.1.1, so one checker serves all five.
AGS 3 and older files are refused: convert them to AGS4 first with the tools AGS lists.

## Reading

```python
from pydiggs import ags4

f = ags4.read("site.ags")  # a path
f = ags4.read_bytes(upload, name="site.ags")  # bytes, or a binary stream
f.version  # '4.1.1', from TRAN_AGS (or None)
loca = f.groups["LOCA"]  # a Group: name, headings, units, types, rows
loca.headings[:3], loca.types[:3]  # ('LOCA_ID', 'LOCA_TYPE', 'LOCA_NATE'), ('ID', 'PA', '2DP')
loca.records()[0]["LOCA_ID"]  # 'BH01': every value is the text the file holds
f.notes  # what reading had to work around, as findings
```

Reading is **lenient**, like [`pydiggs.read`](model.md): a file that breaks the rules still
reads, and each workaround is a note in `f.notes` (`ags4.rule-4` for a short row padded
with empty values, `ags4.rule-5` for an unquoted field, and so on). The notes are not a
verdict; run the checker for one. Only a file with no AGS4 table at all raises
`DocumentError` (`kind` `not-ags4`, or `unsupported-version` for AGS 3).

Nothing is converted while reading: numbers, dates and units stay text, and are parsed
by their declared type only by the checker and the converter. Text is read as UTF-8 (with
or without a byte-order mark), and as Windows-1252 with an `ags4.encoding-assumed`
warning when the bytes are not UTF-8.

**Untrusted input.** Every file is treated as untrusted: at most 100 MiB by default
(`max_bytes`), gzip and zip are refused, and reading stops with `complete=False` and an
`ags4.limit` note past 10,000 groups, 2,000 headings in a group, 5,000,000 DATA rows or
1,000,000 characters in a value. `time_budget` stops it the same way with `ags4.timeout`.
Nothing the file names is opened (a `FILE` group's paths included) and nothing is fetched.

## Writing

```python
group = ags4.Group(
    name="PROJ",
    headings=("PROJ_ID", "PROJ_NAME"),
    units=("", ""),
    types=("ID", "X"),
    rows=[("P1", "Quay Street")],
)
ags4.write(ags4.File(groups=[group]), "out.ags")  # or ags4.dumps(file) -> bytes
```

Output is deterministic: every field in double quotes (a quote inside a value doubled),
each group as its GROUP, HEADING, UNIT and TYPE rows and then its DATA rows, one blank
line between groups, CR LF after every row. Reading a file that follows the rules and
writing it again gives the same bytes. Values are written exactly as held; a value a
spreadsheet would read as a formula is not changed (the checker reports it as
`ags4.formula-text`).

## Checking

```python
report = ags4.check("site.ags")  # a pydiggs.Report
report = ags4.check("site.ags", dictionary="Standard_dictionary.ags")
report.ok, [(f.rule_id, f.line, f.path) for f in report.findings]
```

```bash
pydiggs ags4 check site.ags                         # text; exit 0, 1 or 2 as pydiggs validate
pydiggs ags4 check *.ags --format sarif --output ags4.sarif
pydiggs ags4 check site.ags --dictionary Standard_dictionary_v4_1_1.ags
```

Each AGS4 rule has its own rule id: `ags4.rule-1`, `ags4.rule-2`, `ags4.rule-2a` …
`ags4.rule-20` (the full list, with severities, is in [Findings](findings.md#rule-ids)).
A finding's `path` is the place in the file (`GROUP`, `GROUP/row` or
`GROUP/row/HEADING`, DATA rows counted from 1) and its `line` the line in the file.
Messages are fixed templates: they name groups and headings, never a value from the file.
The report is an ordinary diggs-findings 1.1 file entry with `format: "ags4"`, so the same
JSON and SARIF tools read it.

Exit codes of `pydiggs ags4 check` follow `pydiggs validate`: 0 when no finding is at or
above `--fail-on` (default `error`), 1 when some are, 2 on a usage error, an unreadable
input or a dictionary that cannot be used. A file that is not AGS4 at all is a finding
(exit 1), not an error.

The same check is available to other programs: `POST /v1/ags4/check` in the
[HTTP API](http-api.md#post-v1ags4check) and `ags4.check` in the
[stdio sidecar](sidecar.md#ags4check) (protocol 1.2). Each gives the report
`ags4.check` gives for the same bytes, line numbers included.

## The data dictionary

Rules 7, 9, 10a, 10b, 10c and 18, and parts of 11c, 13, 14 and 19b, need the AGS4 data
dictionary: which groups and headings exist, their order, keys, units and types.

- They always run for the groups and headings the **file's own DICT group** defines.
- They run for the **standard groups** only when you pass a dictionary: an AGS4 file whose
  DICT group defines the groups (`DICT_TYPE` `GROUP`), such as the standard dictionary of
  the file's version, which you can download from the AGS website. Pass it as
  `dictionary=` (a path or an `ags4.File`), `--dictionary FILE`, the multipart part
  `dictionary` of `POST /v1/ags4/check`, or `dictionary_base64` / `dictionary_path` of the
  sidecar's `ags4.check`.
- Without a dictionary the report says so in one `ags4.dictionary-not-checked` info
  finding, so a clean report never hides what was not checked.

A dictionary that cannot be used (unreadable, read only in part, or without a DICT group
that defines groups) is an error of the call (`ResourceError` in Python, exit 2 in the
CLI, 422 `invalid-parameter` naming `dictionary` over HTTP, `invalid-params` in the
sidecar), never a silent fall-back.

pyDIGGS bundles no dictionary because nothing AGS publishes grants the right to
redistribute it; that will change only if AGS agrees in writing.

## Limits

| | Python / CLI | HTTP API | sidecar |
|---|---|---|---|
| Largest file | 100 MiB (`max_bytes`, `--max-bytes`) | `max_body_bytes` (25 MiB), dictionary included | `--max-bytes` (content), `--max-path-bytes` (path) |
| Findings | 10,000 (`max_findings`) | `max_findings` setting | `--max-findings` |
| Time | `time_budget` (none) | `timeout_s` | `time_budget_s` up to `--timeout` |

The HTTP API and the sidecar check a file by writing it to a private temporary folder
(mode 0700, removed when the check ends), because the checker finds each row's line in
the file; nothing else touches the filesystem.
