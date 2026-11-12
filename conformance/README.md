# Conformance corpus

The DIGGS files pyDIGGS is checked against, and the outcome pyDIGGS is expected to give
for each one. Any other implementation (a web or desktop reader, the official DIGGSml
pipeline) can be compared against the same files and the same expected outcomes.

| File | What it is | Edited by |
| --- | --- | --- |
| `corpus.toml` | External files: source repository, pinned commit, licence, path, sha256, size; `[profiles]`: files also checked under another DIGGS profile | Hand, in review |
| `expected.toml` | For every corpus file: verdict, error and warning counts, distinct error and warning rule ids | `scripts/conformance.py record` only |
| `model.toml` | For every corpus file: what a reader must produce (top-level members, procedures, links by relation, result tables, rows and cells), or the error it must raise | `scripts/conformance.py record --model` only |

The corpus also includes the vendored DIGGSml examples in `tests/fixtures/official/`
(provenance in its `PROVENANCE.toml`, kept current by `scripts/sync_reference.py`).

## Commands

Run from the repository root:

```bash
python scripts/conformance.py fetch
python scripts/conformance.py check
python scripts/conformance.py record
python scripts/conformance.py check --model
python scripts/conformance.py record --model
```

- `fetch` downloads external files into `.cache/conformance/` (git-ignored), only from
  `raw.githubusercontent.com` or `gitlab.com` at the pinned commit, and refuses a file
  whose size or sha256 differs from `corpus.toml`.
- `check` validates every file and compares with `expected.toml`. It needs no network
  once the files are fetched. Add `--allow-missing` to skip external files you have not
  fetched.
- `record` rewrites `expected.toml`. Review its diff like a golden file: every change is a
  change in what pyDIGGS reports for real files.

- `--model` makes `check` and `record` use `model.toml`, the reader census, instead.

Exit codes: 0 success, 1 a difference or a missing or corrupt file, 2 a malformed manifest.

## Other profiles

A file can also be checked under a DIGGS profile other than the detected one, such as
the experimental `3.1-dev` (the unreleased DIGGS 3.1 draft, which is never detected).
List it in `corpus.toml`:

```toml
[profiles]
"inspector/SF_Project_Lith_Test.xml" = ["3.1-dev"]
```

`expected.toml` then has a second entry, `"inspector/SF_Project_Lith_Test.xml@3.1-dev"`,
next to the outcome under the detected profile.

## The reader census (`model.toml`)

`model.toml` is the conformance contract for *reading* DIGGS files, as `expected.toml`
is for validating them. It is counted from a whole-tree parse by `census()` in
`scripts/conformance.py`, independently of any reader, by DIGGS local name, so it reads
the same for 2.5.a, 2.6 and 3.x:

| Field | Meaning |
| --- | --- |
| `readable` | `false` when a reader must refuse the file; `document_error` then names why (`malformed`, `not-diggs`) |
| `diggs_version` | The detected profile |
| `elements`, `gml_ids`, `duplicate_gml_ids` | Element nodes (not comments), distinct `gml:id`s, ids used more than once |
| `members` | Each child of the root as `<property>/<element>`; `-` for a by-reference member |
| `procedures` | The element inside every `procedure` |
| `links` | Every element with `xlink:href`, by relation (the name without `Ref`) |
| `links_unresolved`, `links_remote` | `#id` targets not in the file; targets in another file |
| `result_tables`, `result_rows`, `result_cells` | Every `*ResultSet`, with the tuples and values of its `dataValues` |

The pyDIGGS read API (`diggs-model`, from 1.6.0) is tested against these numbers, and
any other reader can be. It has no `@profile` rows: what a file contains does not depend
on the profile it is validated with.

## Adding a file

1. Check its licence allows redistribution of test results (the file itself is never
   committed).
2. Add or reuse a `[sources.<name>]` entry with the full 40-character commit.
3. Add `[files."<name>/<file>"]` with `path`, `sha256` and `size` (`shasum -a 256`;
   hash after CRLF -> LF normalisation if the file has Windows line endings).
4. `fetch`, `record`, `record --model`, review the diffs, commit the manifest and both
   outcome files.

## The AGS4 set

The AGS4 files pyDIGGS's AGS4 reader, rule check and converter are checked against, with
what python-ags4, the AGS Data Format Working Group's reference library, makes of each.

| File | What it is | Edited by |
| --- | --- | --- |
| `ags4-corpus.toml` | External AGS4 files, in the same manifest pattern as `corpus.toml`, plus each source's licence statement, where it was read, and the attribution line | Hand, in review |
| `ags4-expected.toml` | For every external file and every `tests/fixtures/ags4/*.ags`: python-ags4's tables (groups, headings, units, types, row counts, a digest of the rows; the rows themselves for fixtures only) and its rule check (rule ids, counts, first lines) | `scripts/ags4_oracle.py record` only |

```bash
python scripts/conformance.py fetch --set ags4
python scripts/conformance.py check --set ags4
```

`fetch --set ags4` downloads into `.cache/conformance/ags4/` with the same pinning, size
and sha256 refusal as the DIGGS corpus. Sources may be on GitHub or GitLab
(`https://gitlab.com/<group>/<project>`, fetched from `gitlab.com/.../-/raw/<commit>/`).
`check --set ags4` needs neither network nor python-ags4: it confirms that
`ags4-expected.toml` has one record per file, recorded from the bytes on disk (the raw
sha256, since line endings matter to AGS4), and none for a file that has left the set.
Comparing pyDIGGS's reading with the record is the job of the `pydiggs.ags4` tests:
`test_reader_parity_with_the_recorded_oracle` in `tests/test_ags4_read.py` reads every
recorded file that is on disk and compares its groups, headings, units, types and rows
(the rows themselves for fixtures, `row_count` and `rows_sha256` for external files). A
file python-ags4 could not read must read with notes or raise `DocumentError`; a file in
which it found no group (`ags3.ags`) must raise `DocumentError`. An external file that
is not fetched is skipped, so the test suite needs no network; CI's `conformance` job
fetches the set, runs `check --set ags4`, and runs that test with
`PYDIGGS_AGS4_CORPUS=required`, which turns a missing file into a failure.

A group with no UNIT or TYPE row has `units = []` (or `types = []`) in the record, and
`pydiggs.ags4` reads it the same way: an empty tuple, with an `ags4.rule-2b` note, and
`dumps` writes no row for it.

### Licences

| Source | Licence | Used as |
| --- | --- | --- |
| Synthetic fixtures, `tests/fixtures/ags4/` | Apache-2.0 (written for pyDIGGS; invented data) | In the repository |
| BGS [pyagsapi](https://github.com/BritishGeologicalSurvey/pyagsapi) `test/files/real`, 15 files | [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/), by the repository README's "Example Files" statement at the pinned commit (the code is LGPL-3.0) | Fetched only, never committed |

Attribution for the BGS files: "Contains public sector information licensed under the
Open Government Licence v3.0. Contains British Geological Survey materials © UKRI."
OGL v3.0 does not cover personal data, and real investigation files can name people, so
none is vendored and `ags4-expected.toml` records only a digest of their cell values.

Not used, and why:

- **AGS data dictionaries and AGS example files** (the specification's example,
  ags.org.uk downloads, the `Standard_dictionary_*.ags` files inside python-ags4): "All
  rights reserved", no redistribution grant. Not used until AGS agrees in writing.
- **python-ags4's `tests/test_files`** (about 80 rule files on GitLab): published by
  the AGS Data Format Working Group, with no notice beyond the repository's own licence
  (LGPL-3.0 by its PyPI classifier, GPL-3.0 text in `LICENSE`), and their origin is not
  stated (one is `example1.ags`). Held back with the AGS files above; the
  synthetic `rule_*.ags` fixtures cover the rules meanwhile. GitLab fetch support is
  ready for them.
- **pyagsapi `test/files/*.ags` outside `real/`** (`example_ags.ags` and others): not
  covered by the OGL statement, origin not stated.
- **pyagsapi `test/files/real/AGS3/`**: AGS 3 is out of scope (refused by the reader).
- **The other `test/files/real` files**: same licence; left out for size (eight are 2 to
  31 MB) or because a comma in the name fails the corpus path rule. Add them like any
  other file when a test needs them.

### The synthetic fixtures

Invented data, written for pyDIGGS. Most use CRLF line endings, which `.gitattributes`
keeps byte-exact. Besides the reader fixtures (`clean_*.ags` and the encoding and quoting
files), there are `site_tier_a.ags` (the Tier A groups, 4.1.1), `site_tier_b_4_1_1.ags`
and `site_tier_b_4_2.ags` (the Tier B groups, with static cone data as SCPG/SCPT and as
CPTG/CPTT), each with no python-ags4 errors; and `rule_<id>.ags`, one small file per rule
python-ags4 reports, each making it report `ags4.rule-<id>` (some report others too: the
record lists them). Rules 6 and 12 have no file because python-ags4 never reports them;
Rule 2a is the reader fixture `lf_only.ags`, Rule 3 is also `ags3.ags`, and Rule 9 is
reported by `rule_18.ags` and the `rule_19*.ags` files.

### Refreshing the oracle

python-ags4 is never a dependency of pyDIGGS (its caps conflict with the lock, and it is
copyleft). Record in a throwaway environment, after fetching:

```bash
python scripts/conformance.py fetch --set ags4
uv run --isolated --with python-ags4==1.2.0 python scripts/ags4_oracle.py record
```

(`uv run --isolated --no-project --with python-ags4==1.2.0 ...` does the same without
installing pyDIGGS beside it.) Re-record when a fixture is added or changed, when `ags4-corpus.toml` changes, or when
moving to a new python-ags4 release (change `ORACLE_VERSION` in the script and the pin in
the command together). Review the diff of `ags4-expected.toml` like a golden file, and
commit it with the change that caused it. The record is deterministic: the same files and
the same python-ags4 and pandas give the same bytes.
