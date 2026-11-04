# Container image

Every release from 1.4.0 publishes the [HTTP API](http-api.md) as an OCI image for
`linux/amd64` and `linux/arm64`:

```text
ghcr.io/xinp-hub/pydiggs:X.Y.Z    # one release
ghcr.io/xinp-hub/pydiggs:X.Y      # the newest patch of a minor release
```

There is **no `latest` tag**, on purpose: an upgrade should be a decision, not a side
effect of a restart. In production, pin the digest (`ghcr.io/xinp-hub/pydiggs@sha256:…`)
and let your own tooling propose updates. `docker buildx imagetools inspect
ghcr.io/xinp-hub/pydiggs:X.Y.Z` prints it, and the release workflow's run summary
records it.

The image is the one **tested deployment** of pydiggs: it is built from `uv.lock`, so it
runs exactly the dependency versions CI tested. (Installing with pip resolves your own
set; each GitHub release attaches `constraints-X.Y.Z.txt` if you want the tested one.)

!!! warning "Every string in a findings document is untrusted"
    Finding messages, paths and identifiers come from the uploaded file. Escape every
    string field before you render it in HTML or a terminal.

## Run it

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  --read-only \
  --tmpfs /tmp:rw,size=128m,noexec,nosuid,nodev \
  --cap-drop=ALL \
  --security-opt no-new-privileges \
  --memory 4608m \
  -e PYDIGGS_ALLOWED_HOSTS=diggs.example.org \
  -e PYDIGGS_WORKERS=2 \
  ghcr.io/xinp-hub/pydiggs:X.Y.Z
```

```bash
curl -s -H 'Host: diggs.example.org' -H 'Content-Type: application/xml' \
  --data-binary @BH-01.xml 'http://127.0.0.1:8000/v1/validate?name=BH-01.xml'
```

!!! warning "`PYDIGGS_ALLOWED_HOSTS` is required"
    The image serves on `0.0.0.0:8000`. Bound to a non-loopback address, pydiggs
    **refuses to start** until `PYDIGGS_ALLOWED_HOSTS` lists the host names clients use
    (comma-separated, no `*`). Requests with any other `Host` header get 400
    `invalid-host`, which protects against DNS rebinding. The health check sends the
    first name in the list.

Every option on that line is part of the supported configuration:

| option | why |
|---|---|
| `--read-only` | the root filesystem never changes; nothing in the image needs to write to it |
| `--tmpfs /tmp:…,noexec,nosuid,nodev` | the only writable place: multipart uploads spool here; nothing written there can be executed |
| `--cap-drop=ALL` | the server binds port 8000 and needs no Linux capability |
| `--security-opt no-new-privileges` | no process in the container can gain privileges through setuid binaries |
| `--memory` | caps the container; see [Sizing](#sizing) |
| `-e PYDIGGS_*` | the [settings](http-api.md#settings) of the HTTP API |

The image needs **no network egress**: it validates with the bundled schemas,
dictionaries and Schematron rules only. Docker's default seccomp profile is kept; no
custom profile is shipped.

### Behind a reverse proxy

Any deployment reachable from other machines must sit behind a reverse proxy (nginx,
Caddy, HAProxy, a cloud load balancer) that terminates TLS and has **client header and
body timeouts**. uvicorn has no timeout for reading request headers, so a client that
sends partial headers can hold connections until the connection limit is reached (see
[HTTP API](http-api.md)). pydiggs adds no authentication or per-client rate limiting
either; put those in the proxy or an API gateway. Publish the port on loopback (as
above) or on a private network only the proxy can reach.

### Compose

```yaml
services:
  pydiggs:
    image: ghcr.io/xinp-hub/pydiggs:X.Y.Z   # better: pin @sha256:…
    read_only: true
    tmpfs:
      - /tmp:rw,size=128m,noexec,nosuid,nodev
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 4608m
    pids_limit: 256
    environment:
      PYDIGGS_ALLOWED_HOSTS: diggs.example.org
      PYDIGGS_WORKERS: "2"
    ports:
      - "127.0.0.1:8000:8000"   # only the reverse proxy on this host reaches it
    restart: unless-stopped
```

### Kubernetes

The image runs as the numeric user `10001` (group `10001`) and has no home directory,
so `runAsNonRoot` can be verified:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: {drop: [ALL]}
  seccompProfile: {type: RuntimeDefault}
# with an emptyDir {medium: Memory, sizeLimit: 128Mi} mounted at /tmp,
# resources.limits.memory: 4608Mi for two workers,
# and probes on GET /livez (liveness) and GET /readyz (readiness), each sending a
# Host header that is in PYDIGGS_ALLOWED_HOSTS.
```

## Sizing

- **Memory:** `PYDIGGS_WORKERS × 2 GiB + 512 MiB`. Each worker process is capped at
  `PYDIGGS_WORKER_MEMORY_MB` (2048 by default, enforced on Linux, so in the container);
  the 512 MiB is the server process itself. One worker: `2560m`; two: `4608m`; four
  (the most the default picks): `8704m`.
- **`/tmp`:** at least `queue depth × max body`, where the queue depth is
  `PYDIGGS_QUEUE_DEPTH` (default `2 × workers`) and the body limit
  `PYDIGGS_MAX_BODY_BYTES` (default 25 MiB). Two workers: `4 × 25 MiB = 100 MiB`, so
  `128m`. A tmpfs counts against the container's memory only for what is written to it.
- **CPU:** one core per worker is enough; the default worker count is the CPUs minus
  one, from 1 to 4.

## What is in the image

- `python:3.14-slim-trixie` (Debian 13), pinned by digest, with pydiggs and the locked
  `[server]` dependencies in `/opt/venv`. The pydiggs wheel is the exact file published
  to PyPI: the release job downloads it and checks its sha256 against PyPI and against
  the release build before it enters the image.
- **No package installer:** pip, setuptools and `ensurepip` are removed from the base
  Python and the virtual environment.
- `USER 10001:10001`, `EXPOSE 8000`, and a `HEALTHCHECK` that requests `GET /livez`
  with Python's `urllib` (no curl in the image).
- The command is `pydiggs serve --http --host 0.0.0.0 --port 8000`, with process
  isolation and the hard timeout (`PYDIGGS_TIMEOUT_S`, 60 s by default). Override it to
  run any other `pydiggs` command, for example `docker run --rm IMAGE pydiggs --version`.
- OCI labels: `org.opencontainers.image.source`, `version`, `revision` (the release
  commit) and `licenses`.

## Verify the image

Each release image carries a **SLSA build provenance attestation** signed with the
release workflow's GitHub identity (Sigstore), stored in the registry next to the
image, plus BuildKit's own provenance (`mode=max`) and an SPDX **SBOM**.

```bash
# Built by this repository's release workflow, for tag vX.Y.Z, on GitHub's runners
# (DIGEST from `docker buildx imagetools inspect ghcr.io/xinp-hub/pydiggs:X.Y.Z`):
gh attestation verify oci://ghcr.io/xinp-hub/pydiggs@sha256:DIGEST \
  --repo xinp-hub/pydiggs \
  --signer-workflow xinp-hub/pydiggs/.github/workflows/publish.yml \
  --source-ref refs/tags/vX.Y.Z --deny-self-hosted-runners

# The SBOM and the BuildKit provenance, per platform:
docker buildx imagetools inspect ghcr.io/xinp-hub/pydiggs:X.Y.Z --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/xinp-hub/pydiggs:X.Y.Z --format '{{ json .Provenance }}'
```

Other Sigstore verifiers that read bundles stored as OCI referrers (for example cosign
with `--new-bundle-format`) can check the same attestation: the certificate identity is
`https://github.com/xinp-hub/pydiggs/.github/workflows/publish.yml@refs/tags/vX.Y.Z` and
the issuer `https://token.actions.githubusercontent.com`.

Every pull request builds the image, runs it with the options above, checks `/livez`,
`/v1/versions` and one validation, and scans it. A release builds its image from the
PyPI wheel, pushes it by digest, scans that digest on both platforms, and only then
points the `X.Y.Z` and `X.Y` tags at it. A HIGH or CRITICAL vulnerability with a fix
available fails either job, and a release image that fails gets no tags.
The base images are updated through Dependabot after a 14-day cooldown.
