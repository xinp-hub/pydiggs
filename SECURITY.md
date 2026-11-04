# Security Policy

## Supported versions

Security fixes land on the latest minor release (currently 1.0.x). Older versions are not
patched, so please upgrade before reporting an issue against one.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting:
<https://github.com/xinp-hub/pydiggs/security/advisories/new>

If that is unavailable, email xin_peng@outlook.com with `SECURITY` in the subject.

Please include the affected version, a reproduction, the impact you believe it has, and
any proposed fix. You will get an acknowledgement within 5 working days and an
assessment within 10. If the report is valid, we will agree a disclosure timeline with
you: normally publication within 90 days or on release of the fix, whichever comes
first. Credit is given unless you prefer otherwise.

## What pyDIGGS trusts

- **DIGGS instance files are untrusted.** They are parsed with external entities, DTD
  loading and network access refused, and compressed input is rejected. A report that a
  crafted instance file can read local files, reach the network (with the default
  `allow_network=False`), exhaust memory or crash a check is in scope.
- **Requests to `pydiggs serve` are untrusted.** The HTTP API and the stdio sidecar
  use only the bundled schemas, dictionaries and Schematron rules. Network access,
  snippets and local dictionaries are always off. Request bodies are bounded in size,
  validation in time, and admitted requests in number; the HTTP server also bounds how
  long a body may take to arrive. uvicorn has no timeout for reading request headers, so
  a client that sends partial headers can hold connections until `limit_concurrency` is
  reached: a non-loopback deployment must sit behind a reverse proxy with client header
  and body timeouts. A sidecar reads a file by path only inside folders the host granted
  with `--allow-path` (hard links into such a folder are not contained). A report that a
  request can escape these limits is in scope.
- **Schema and Schematron files are trusted code.** A custom `schema_path` or
  `schematron_path` runs with your permissions; reports that require a malicious one
  are out of scope.
- pyDIGGS parses XML with lxml, which bundles libxml2. libxml2 was declared unmaintained
  in December 2025 and still gets occasional security releases. pyDIGGS requires
  `lxml>=6.1.3` and does not rely on lxml's parser defaults.

## Supply-chain assurances

- Releases are published from GitHub Actions with **PyPI Trusted Publishing** (OIDC).
  The project has no long-lived PyPI API tokens. A token committed to this repository
  in 2021 has been revoked.
- Distributions carry **PEP 740 attestations**, so you can verify that a file on PyPI
  was built by this repository's release workflow.
- Release artifacts attached to GitHub releases are **Sigstore-signed**, and their
  sha256 digests are recorded in `dist-digests.txt` on each release.
- Workflows run with least-privilege `permissions:` and actions are pinned to commit SHAs.
- The **container image** (`ghcr.io/xinp-hub/pydiggs`, from 1.4.0) is built in the
  release workflow from the wheel published to PyPI, after checking its sha256 against
  PyPI and the release build, and from the locked dependency set. It runs as uid 10001
  with no package installer, is documented to run read-only with no capabilities and
  `no-new-privileges`, and carries a SLSA provenance attestation and an SBOM. Trivy
  scans it on every pull request and scans the pushed digest at release; a HIGH or
  CRITICAL vulnerability with a fix available fails the build. There is no `latest`
  tag; pin a digest.
- Dependencies are audited on every push and weekly (`pip-audit`), code is scanned with
  CodeQL, secrets with gitleaks and GitHub secret scanning with push protection. A
  failed weekly scan opens an issue.

### Verifying what you installed

```console
# Confirm the file you downloaded is the file that was published
python -m pip download --no-deps pydiggs==<version>
sha256sum pydiggs-*.whl   # compare against the release's dist-digests.txt

# Verify the PyPI attestation (provenance) for a release
uvx pypi-attestations verify pypi --repository https://github.com/xinp-hub/pydiggs pypi:pydiggs-<version>-py3-none-any.whl

# Verify the container image was built by the release workflow
gh attestation verify oci://ghcr.io/xinp-hub/pydiggs:<version> --owner xinp-hub
```
