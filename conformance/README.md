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
  `raw.githubusercontent.com` at the pinned commit, and refuses a file whose size or
  sha256 differs from `corpus.toml`.
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
