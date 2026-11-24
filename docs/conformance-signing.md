# Signing conformance evidence

`pydiggs conformance grade` writes `evidence.json` unsigned, and pyDIGGS has no signing
code and no signing dependency. If you want others to know that a result came from *your*
build, sign it in your own CI. This page gives a GitHub Actions workflow that does so with
[artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations)
(Sigstore, keyless), how anyone checks the signature, and the same with
[cosign](https://docs.sigstore.dev/cosign/signing/signing_with_blobs/) for other CI
systems.

!!! warning "What a signature adds, and what it does not"
    A signature binds the evidence to **the repository, the workflow file, the commit and
    the run** that produced it, and records when, in a public transparency log. It does
    **not** make the result more than the suite checked: the file passed the published
    checks of one challenge, graded by one pyDIGGS release. It is still a
    [self-tested](conformance.md#evidence-anyone-can-re-run) result, not a certification
    by the DIGGS committee or the ASCE Geo-Institute. The signature also cannot show that
    the workflow really ran your product: that is a claim about what the workflow file
    says, which anyone can read at the signed commit.

## A GitHub Actions workflow

Save it in your product's repository as `.github/workflows/diggs-conformance.yml`. It
makes a fresh challenge, has your software export it, grades the export, and attests the
three files a reviewer needs. Replace the export step with your product's own commands.

```yaml
# DIGGS conformance, self-tested, with the evidence attested to this repository.
name: DIGGS conformance

on:
  workflow_dispatch:
  release:
    types: [published]

permissions: {}

jobs:
  conformance:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    permissions:
      contents: read       # check out the repository
      id-token: write      # the OIDC token Sigstore issues the signing certificate for
      attestations: write  # store the attestation with this repository
    env:
      PYDIGGS: "1.18.0"    # the pyDIGGS release that grades; evidence verifies with it
      PRODUCT: "MyLog"
      PRODUCT_VERSION: "4.2.1"
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2  # v10.3.3
      - name: Make a challenge
        run: >-
          uvx --from "pydiggs==$PYDIGGS" pydiggs conformance new spt-export
          --product "$PRODUCT" --product-version "$PRODUCT_VERSION" -o challenge
      - name: Load the challenge into the product and export DIGGS
        # Your product's commands go here. They must read the tables in challenge/ and
        # write export.xml. To try the workflow first, pyDIGGS's own converter does it:
        #   uvx --from "pydiggs==$PYDIGGS" pydiggs tables to-diggs challenge -o export.xml
        run: ./build/mylog import challenge && ./build/mylog export --diggs -o export.xml
      - name: Grade the export (writes challenge/evidence.json and challenge/report.html)
        run: uvx --from "pydiggs==$PYDIGGS" pydiggs conformance grade challenge export.xml
      - name: Attest the evidence, the submission and the challenge
        uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6  # v4.2.2
        with:
          subject-path: |
            challenge/evidence.json
            challenge/challenge.json
            export.xml
      - name: Keep the files a reviewer needs
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9  # v7.0.2
        with:
          name: diggs-conformance
          path: |
            challenge/
            export.xml
          if-no-files-found: error
```

Notes:

- **Permissions.** The job gets only what it needs: `contents: read`, `id-token: write`
  and `attestations: write`, and the workflow gets none by default. `actions/attest`
  also asks for `artifact-metadata: write`, but only to make storage records for
  container images it pushes to a registry, which this workflow does not do.
- **A failing grade stops the job**, so only a passing result is attested. To attest a
  failing one as well, give the grade step `continue-on-error: true`: the evidence says
  `"verdict": "fail"` either way.
- **Public repositories** are signed by the public Sigstore instance, and the
  attestation's certificate (repository, workflow, commit) goes into its public
  transparency log. Attestations in private repositories need GitHub Enterprise Cloud.
- **Actions are pinned by commit**, with the release in a comment, as pyDIGGS's own
  workflows pin them. Dependabot or Renovate can keep the pins current.

### The verdict as a signed statement (optional)

The attestation above is SLSA build provenance: it says which run produced each file.
To also sign *what the evidence says about the export*, attest the export with the
evidence itself as the predicate. Add this step after the grade step:

```yaml
      - name: Attest the verdict about the export
        uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6  # v4.2.2
        with:
          subject-path: export.xml
          predicate-type: https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-conformance-1.0-beta.3.schema.json
          predicate-path: challenge/evidence.json
```

The predicate type is the evidence's own `$schema`; change it together with the pyDIGGS
release if a later one writes another schema version.

## Checking a result

A reviewer downloads the `diggs-conformance` artifact of the run (or receives the files)
and checks two things: who produced the files, and that the grading reproduces.

**1. Who produced them**, with the [GitHub CLI](https://cli.github.com/manual/gh_attestation_verify)
(version 2.105 or later is current):

```console
$ gh attestation verify challenge/evidence.json --repo vendor/mylog \
    --signer-workflow vendor/mylog/.github/workflows/diggs-conformance.yml \
    --deny-self-hosted-runners
$ gh attestation verify export.xml --repo vendor/mylog
$ gh attestation verify challenge/challenge.json --repo vendor/mylog
```

`--owner vendor` instead of `--repo` accepts any repository of that organisation.
`--source-ref refs/tags/v4.2.1` or `--source-digest <commit>` pins the commit. With the
optional statement, `--predicate-type` (the URI above) and `--format json` print the
signed evidence, whose `verdict` field is what was attested.

A successful check proves that each file's SHA-256 was attested by a GitHub Actions run
of that workflow file in that repository, at the commit and time the output shows, on a
GitHub-hosted runner. Read the workflow at that commit to see what the run did.

**2. That the grading reproduces**, with pyDIGGS (the release the evidence names):

```console
$ pydiggs conformance verify challenge/evidence.json challenge export.xml
Reproduced: spt-export pass.
```

Neither step says anything about files other than these three, or about another version
of the product.

## Other CI systems: cosign

Any CI that can give a job an OpenID Connect token Sigstore accepts (GitLab CI,
Buildkite, CircleCI and others) can sign keylessly with
[cosign](https://github.com/sigstore/cosign) (3.1 is current). In GitLab CI, for
example, request the token in the job:

```yaml
diggs-conformance:
  id_tokens:
    SIGSTORE_ID_TOKEN:
      aud: sigstore
  script:
    - pydiggs conformance new spt-export --product MyLog --product-version 4.2.1 -o challenge
    - ./build/mylog import challenge && ./build/mylog export --diggs -o export.xml
    - pydiggs conformance grade challenge export.xml
    - cosign sign-blob --yes --bundle evidence.sigstore.json challenge/evidence.json
    - cosign sign-blob --yes --bundle export.sigstore.json export.xml
```

cosign reads the token from `SIGSTORE_ID_TOKEN`. Keep the `.sigstore.json` bundles with
the files. A reviewer checks each file against the identity of the pipeline that signed
it: for GitLab, the project path, `//`, the CI file and the ref.

```console
$ cosign verify-blob challenge/evidence.json --bundle evidence.sigstore.json \
    --certificate-identity "https://gitlab.com/vendor/mylog//.gitlab-ci.yml@refs/heads/main" \
    --certificate-oidc-issuer "https://gitlab.com"
Verified OK
```

The same command checks a file signed by cosign in GitHub Actions, with
`--certificate-identity "https://github.com/vendor/mylog/.github/workflows/diggs-conformance.yml@refs/heads/main"`
and `--certificate-oidc-issuer "https://token.actions.githubusercontent.com"`. What it
proves is what the GitHub check proves: the pipeline identity in the certificate signed
these bytes. Then run `pydiggs conformance verify` as above.
