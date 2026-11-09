# Conformance corpus

The DIGGS files pyDIGGS is checked against, and the outcome pyDIGGS is expected to give
for each one. Any other implementation (a web or desktop reader, the official DIGGSml
pipeline) can be compared against the same files and the same expected outcomes.

| File | What it is | Edited by |
| --- | --- | --- |
| `corpus.toml` | External files: source repository, pinned commit, licence, path, sha256, size | Hand, in review |
| `expected.toml` | For every corpus file: verdict, error and warning counts, distinct error and warning rule ids | `scripts/conformance.py record` only |

The corpus also includes the vendored DIGGSml examples in `tests/fixtures/official/`
(provenance in its `PROVENANCE.toml`, kept current by `scripts/sync_reference.py`).

## Commands

Run from the repository root:

```bash
python scripts/conformance.py fetch
python scripts/conformance.py check
python scripts/conformance.py record
```

- `fetch` downloads external files into `.cache/conformance/` (git-ignored), only from
  `raw.githubusercontent.com` at the pinned commit, and refuses a file whose size or
  sha256 differs from `corpus.toml`.
- `check` validates every file and compares with `expected.toml`. It needs no network
  once the files are fetched. Add `--allow-missing` to skip external files you have not
  fetched.
- `record` rewrites `expected.toml`. Review its diff like a golden file: every change is a
  change in what pyDIGGS reports for real files.

Exit codes: 0 success, 1 a difference or a missing or corrupt file, 2 a malformed manifest.

## Adding a file

1. Check its licence allows redistribution of test results (the file itself is never
   committed).
2. Add or reuse a `[sources.<name>]` entry with the full 40-character commit.
3. Add `[files."<name>/<file>"]` with `path`, `sha256` and `size` (`shasum -a 256`;
   hash after CRLF -> LF normalisation if the file has Windows line endings).
4. `fetch`, `record`, review the diff, commit both files.
