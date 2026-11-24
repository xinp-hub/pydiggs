# Agreement with the DIGGS validator

pyDIGGS is one implementation of DIGGS validation. The DIGGS project has its own: the
[DIGGSml XSLT validator](https://github.com/DIGGSml/validation), a set of XSLT 3.0
stylesheets that check structure, schema location, code lists, dictionaries, Schematron
rules, geometry, coordinate reference systems, coordinates and deprecations. This page
runs both on every file of the public
[conformance corpus](https://github.com/xinp-hub/pydiggs/blob/master/conformance/README.md)
and lists every file where their verdicts differ, with the reason.

A difference is not hidden and not waved through. Each one must be explained in
[`conformance/intended.toml`](https://github.com/xinp-hub/pydiggs/blob/master/conformance/intended.toml),
and the explanation must cover every error that makes the difference. The check fails on
a difference nobody has explained, on an explanation that no longer applies, and on a
file that agrees again but is still listed.

## How the comparison is run

- **The same files.** Every file of the corpus: the DIGGS project's own examples (DIGGS
  2.5.a, 2.6 and 3.0) and the external files pinned by commit and SHA-256 in
  `conformance/corpus.toml`. Files that pyDIGGS also checks under another profile
  (`3.1-dev`) are compared once, under their own: the validator reads the schema the file
  names and cannot be told otherwise.
- **The verdict.** A file is *valid* for pyDIGGS when its report has no error
  (`report.ok`). It is *valid* for the validator when it ran to the end and wrote no
  `ERROR` message. Warnings and information messages are counted but do not decide.
- **The validator, pinned.** Its files are pinned by commit, size and SHA-256 in
  `conformance/xslt-validator.toml`, run with SaxonC-HE 12.7, the Saxon release the
  validator's repository ships, and never committed here (that repository states no
  licence).
- **Offline, and independent of any service.** Every URL the validator asks for is
  answered from a pinned local copy: the DIGGS schemas and dictionaries pyDIGGS bundles,
  at the addresses diggsml.org publishes them, and the validator's own files. Any other
  URL is unreachable during the run. That includes the units API the validator's
  dictionary step and Schematron rules call, so where they would need it they report
  that it could not be reached (a warning), and the job sends nothing anywhere. It also
  means the validator cannot download coordinate reference system definitions; the
  reasons below say which differences come from that.
- **Recorded, then checked.** The validator's outcome for every file is recorded in
  `conformance/xslt-expected.toml`, as pyDIGGS's is in `conformance/expected.toml`. A
  CI job runs the validator again whenever either changes, fails when its outcome differs
  from the record, and fails on any difference that is not explained. The test suite
  checks the explanations against the records on every change, without the validator.
  This page is built from the same records.

The verdict is a coarse measure. Two implementations can both call a file invalid for
different reasons, and that counts as agreement here; the error kinds at the end of the
page show what the validator reports.

<!-- pydiggs:agreement -->

## Run it yourself

From a checkout of pyDIGGS, with [uv](https://docs.astral.sh/uv/):

```console
$ python scripts/conformance.py fetch
$ python scripts/agreement.py fetch
$ uv venv --python 3.12 .cache/agreement/venv
$ uv pip sync --require-hashes --python .cache/agreement/venv conformance/xslt-requirements.txt
$ .cache/agreement/venv/bin/python scripts/agreement.py check --run
0 problem(s)
```

The first two commands download the external corpus files and the validator's files
(each checked against its recorded size and SHA-256). The next two install SaxonC-HE in
a throwaway environment, from wheels whose hashes are pinned, and the last runs the
comparison. SaxonC-HE is never a dependency of pyDIGGS. `agreement.py record` rewrites
`conformance/xslt-expected.toml` after a deliberate change, such as moving to a newer
validator commit; review its diff like any golden file.
