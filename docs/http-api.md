# HTTP API

`pydiggs[server]` serves pydiggs over HTTP: upload a DIGGS document, get a
[diggs-findings 1.1](findings.md) document (or a SARIF 2.1.0 log) back. It is a FastAPI
application you can run as is, or a router you mount in your own FastAPI app.

```bash
pip install 'pydiggs[server]'
pydiggs serve --http                       # http://127.0.0.1:8000
curl -s -H 'Content-Type: application/xml' --data-binary @BH-01.xml \
  'http://127.0.0.1:8000/v1/validate?name=BH-01.xml'
```

!!! warning "Every string in a findings document is untrusted"
    Finding messages, paths and identifiers come from the uploaded file. Escape every
    string field before you render it in HTML or a terminal.

pydiggs adds no authentication, rate limiting per client or TLS. Put it behind your own
app, a reverse proxy or an API gateway for that.

!!! warning "Behind a reverse proxy when not on loopback"
    Any deployment reachable from other machines must sit behind a reverse proxy (nginx,
    Caddy, HAProxy, a cloud load balancer) with **client header and body timeouts**.
    uvicorn has no timeout for reading request headers: a client that opens connections
    and sends partial headers holds them, and enough of them exhaust uvicorn's
    `limit_concurrency` (the admission slots plus 4), so the server stops accepting
    work. pydiggs bounds only what happens after the headers: the body must arrive
    within `body_timeout_s` (408), and validation within `timeout_s` (504).

## Running it

=== "Command line"

    ```bash
    pydiggs serve --http --host 127.0.0.1 --port 8000 --workers 2 --timeout 60
    ```

    `--max-bytes`, `--max-findings`, `--timeout` and `--workers` override the
    `PYDIGGS_*` variables below. Bound to a non-loopback address, the server refuses to
    start until `PYDIGGS_ALLOWED_HOSTS` lists the host names clients use.

=== "Your own ASGI server"

    ```python
    from pydiggs.server import ServerSettings, create_app

    app = create_app(ServerSettings.from_env())
    ```

=== "Mounted in your app"

    ```python
    from fastapi import FastAPI
    from pydiggs.server import ServerSettings, create_router

    app = FastAPI()
    app.include_router(create_router(ServerSettings(isolation="process")))
    ```

    The router enforces its own limits (body size, admission, multipart, cross-site
    refusal), so they hold without `create_app`. It adds no `Host` check and no CORS
    headers: those are your app's job. Its default isolation is `"thread"`; pass
    `isolation="process"` for untrusted uploads.

## Endpoints

| method and path | answer |
|---|---|
| `POST /v1/validate` | 200 with a diggs-findings 1.1 document, or SARIF 2.1.0 for `Accept: application/sarif+json` |
| `POST /v1/read` | 200 with a diggs-model 1.0 document: what is in the file ([Reading files](model.md)) |
| `GET /v1/versions` | pydiggs, findings schema and DIGGS versions, reference-data commits, `api_version: "1"` (off with `versions_enabled=False`) |
| `GET /v1/rules?step=` | every rule id pydiggs can emit (open families as patterns) |
| `GET /v1/rules/{rule_id}` | one rule; a valid id pydiggs does not emit is 200 with `known: false` |
| `GET /v1/readyz` | 200 once the workers are warm, else 503 `not-ready` |
| `GET /livez` | 200 while the server runs (app only; use it for container health checks) |
| `GET /readyz` | as `/v1/readyz` (app only) |

### `POST /v1/validate`

Send the document either way:

- **Raw:** `Content-Type: application/xml` or `text/xml`, the document as the body, and
  the query parameters `name`, `checks`, `diggs_version`.
- **Multipart:** `Content-Type: multipart/form-data` with one part named `file` and the
  optional fields `name`, `checks`, `diggs_version`. A field wins over the same query
  parameter, but both must be valid. `name` wins over the part's file name. Other fields
  are refused.

| parameter | meaning |
|---|---|
| `name` | File name to report; reduced to a basename, `input.xml` when empty |
| `checks` | Comma-separated steps (repeatable): `structure`, `schema`, `dictionary`, `schematron`, `geometry`, `deprecation`, `context`; default all |
| `diggs_version` | `2.5.a`, `2.6` or `3.0.0`; default detected. `3.1-dev` selects the unreleased DIGGS 3.1 draft ([experimental](usage.md#experimental-diggs-31-dev)) |

```bash
curl -s -F file=@BH-01.xml -F checks=structure,schema \
  -H 'Accept: application/sarif+json' http://127.0.0.1:8000/v1/validate
```

Malformed XML, findings past `max_findings` (the `structure.findings-truncated` finding)
and an exhausted time budget (`structure.timeout`) are all **200 with findings**, as with
`pydiggs validate --format json`. Only a request that produced no document is an error.

`Accept` decides the format: `application/json` (the default, also for `*/*` and no
header) or `application/sarif+json`. When both are listed with the same quality, JSON
wins. An `Accept` that allows neither is 406.

### `POST /v1/read`

Reads the document into a **diggs-model 1.0** document: boreholes, samples, tests and
their result tables, as [`pydiggs.read()`](model.md) returns them. The body is exactly
`pydiggs.read_bytes(data, name=name).to_json()`, so every surface gives the same bytes
for the same file. Send the document as for `/v1/validate` (raw XML or one multipart
`file` part); the body limit, admission, isolation and time limit are the same.

| parameter | meaning |
|---|---|
| `name` | File name to report; reduced to a basename, `input.xml` when empty |
| `diggs_version` | As for `/v1/validate`; default detected |
| `select` | JSON text: `{"results": "all"}` (the default), `{"results": "none"}` or `{"results": ["key", …]}` (at most 1,000 node keys) |

```bash
curl -s -F file=@BH-01.xml -F 'select={"results": "none"}' http://127.0.0.1:8000/v1/read
```

- **`select`** says which result tables keep their values. A table that is left out keeps
  its columns and `row_count` with empty `values`. List a file first with
  `{"results": "none"}`, then ask for the measurements you need by `key`. Each worker
  keeps the last few models it read (by content hash), so the second call does not parse
  the file again. That is a cache only: nothing is stored between requests that a caller
  could address.
- **Reading is not validating.** A schema-invalid file still reads with 200; run
  `/v1/validate` for a verdict. What reading had to work around is in the document's
  `notes`.
- **Not a DIGGS document** is 422 `invalid-document`, with `kind` `malformed`,
  `not-diggs`, `unsafe-entity` or `namespace-too-long`. (`/v1/validate` reports the same
  cases as findings in a 200.)
- **Size.** The reader's own work limits ([Reading files](model.md#reading-a-file))
  apply, and a document larger than four times `max_body_bytes` is 422
  `result-too-large`; ask for fewer tables with `select`.
- **Time limit:** with thread isolation, running out of time is a 200 with
  `complete: false` and the members read so far; with process isolation it is 504
  `timeout`.
- The answer is always `application/json`. On the conformance corpus a whole model is
  0.6 to 2 times the size of its XML (1.1 times overall); compressing responses is the
  host's job.

## Limits and admission

Checks run in this order, before the body is read where possible:

1. Cross-site refusal (403).
2. `Accept` (406), media type (415), `Content-Encoding` other than `identity` (415).
3. `Content-Length` above the limit: 413 at once, body not read.
4. Admission: a slot among `workers + queue_depth` is taken **without waiting**; when
   none is free the answer is 503 `busy` with `Retry-After: 1`, body not read.
5. The body is streamed with a running byte count and abandoned at the limit (413), so
   chunked bodies are bounded too. Multipart allows one file and at most 8 fields of
   64 KiB; the file counts against `max_body_bytes`; spooled parts are deleted.
6. The whole body must arrive within `body_timeout_s` (30 s) of admission; otherwise the
   answer is 408 `request-timeout` with `Connection: close`, and the slot is freed.

Gzip-compressed documents are refused (415 `compressed-input`).

These limits live in the route, so they also hold when only the router is mounted.

## Isolation

| `isolation` | how a request runs | time limit |
|---|---|---|
| `"process"` (default for `create_app` and `serve --http`) | in one of `workers` spawned processes | **hard**: past `timeout_s` the worker is killed and replaced, 504 `timeout` |
| `"thread"` (default for `create_router`) | in one of `workers` threads | cooperative only: a long libxml2 call holds its thread; **unsafe for untrusted input** |

Process workers warm every bundled DIGGS version before they report ready. On Linux each
worker runs under an address-space cap (`worker_memory_mb`, 2 GiB) and a CPU-time
backstop; macOS enforces only the CPU backstop and Windows neither, so use a container or
host limits there. Workers send results back as JSON bytes, never pickles. `create_app`
logs a warning when it is given `isolation="thread"`.

Size the host for *ASGI server processes × pydiggs workers × about 2 GiB*.

## Host, cross-site and CORS

- `create_app` refuses any `Host` not in `allowed_hosts` with 400 `invalid-host`
  (DNS-rebinding protection). Unset, it allows `127.0.0.1`, `localhost` and `[::1]`.
- Every route refuses a request with `Sec-Fetch-Site: cross-site`, or with an `Origin`
  whose host differs from `Host` (`null` included), unless the origin is in
  `cors_origins` (403 `cross-site`; turn off with `reject_cross_site=False`).
- `create_app` sends CORS headers only to the origins in `cors_origins`; `"*"` is
  refused.
- `/docs`, `/redoc` and `/openapi.json` are off unless `docs_enabled` (Swagger UI loads
  JavaScript from a CDN).
- uvicorn runs with HTTP/1.1 only, no access log (query strings carry file names), no
  `Server` header, a 16 KiB header limit, a 5 s keep-alive and a connection limit of the
  admission slots plus 4. On SIGTERM or SIGINT it stops accepting connections and waits
  at most `timeout_s` plus 5 s for requests in flight, then cancels them and exits.
- `/v1/versions` and the probes reveal versions to unauthenticated callers.

## Settings

`ServerSettings.from_env()` reads these variables; unset ones keep their defaults.

| field | variable | default |
|---|---|---|
| `max_body_bytes` | `PYDIGGS_MAX_BODY_BYTES` | 26214400 (25 MiB) |
| `max_findings` | `PYDIGGS_MAX_FINDINGS` | 10000 |
| `timeout_s` | `PYDIGGS_TIMEOUT_S` | 60 |
| `body_timeout_s` | `PYDIGGS_BODY_TIMEOUT_S` | 30 |
| `workers` | `PYDIGGS_WORKERS` | CPUs − 1, from 1 to 4 |
| `queue_depth` | `PYDIGGS_QUEUE_DEPTH` | 2 × `workers` |
| `isolation` | `PYDIGGS_ISOLATION` | per factory (see above) |
| `worker_memory_mb` | `PYDIGGS_WORKER_MEMORY_MB` | 2048 (Linux only) |
| `cors_origins` | `PYDIGGS_CORS_ORIGINS` (comma-separated) | none |
| `allowed_hosts` | `PYDIGGS_ALLOWED_HOSTS` (comma-separated) | loopback names |
| `reject_cross_site` | `PYDIGGS_REJECT_CROSS_SITE` | true |
| `docs_enabled` | `PYDIGGS_DOCS_ENABLED` | false |
| `versions_enabled` | `PYDIGGS_VERSIONS_ENABLED` | true |

## Errors

Every error is RFC 9457 `application/problem+json` with `type`, `title`, `status`,
`detail` and `code`. Switch on `code`. Texts are fixed: they never echo a value you sent
or a server path. `invalid-parameter` adds `errors` (at most 20 `{parameter, problem}`
pairs); 500 answers add a `correlation_id` that matches the server's log line.

```json
{"type": "https://xinp-hub.github.io/pydiggs/http-api/#problem-busy", "title": "Server busy",
 "status": 503, "detail": "Too many requests are in progress; try again shortly.", "code": "busy"}
```

| status | code | when |
|---|---|---|
| 400 | <span id="problem-missing-input"></span>`missing-input` | no `file` part, or the body stopped arriving |
| 400 | <span id="problem-invalid-multipart"></span>`invalid-multipart` | unparseable multipart, a second file, too many, unknown or oversized fields |
| 400 | <span id="problem-invalid-host"></span>`invalid-host` | `Host` not in `allowed_hosts` (app only) |
| 403 | <span id="problem-cross-site"></span>`cross-site` | cross-site request |
| 404 | <span id="problem-not-found"></span>`not-found` | unknown path (app only) |
| 405 | <span id="problem-method-not-allowed"></span>`method-not-allowed` | wrong method; see `Allow` (app only) |
| 406 | <span id="problem-not-acceptable"></span>`not-acceptable` | `Accept` allows neither JSON nor SARIF (`/v1/read`: not JSON) |
| 408 | <span id="problem-request-timeout"></span>`request-timeout` | the body did not arrive within `body_timeout_s` |
| 413 | <span id="problem-input-too-large"></span>`input-too-large` | body or file above `max_body_bytes` |
| 415 | <span id="problem-compressed-input"></span>`compressed-input` | gzip document, or `Content-Encoding` other than `identity` |
| 415 | <span id="problem-unsupported-media-type"></span>`unsupported-media-type` | neither XML nor multipart |
| 422 | <span id="problem-invalid-parameter"></span>`invalid-parameter` | bad `checks`, `diggs_version`, `name`, `select`, `step` or rule id |
| 422 | <span id="problem-invalid-document"></span>`invalid-document` | `/v1/read` only: the bytes are not a DIGGS document; `kind` says why |
| 422 | <span id="problem-result-too-large"></span>`result-too-large` | `/v1/read` only: the document is larger than four times `max_body_bytes`; ask for fewer tables with `select` |
| 422 | <span id="problem-input-unreadable"></span>`input-unreadable` | the document could not be read |
| 500 | <span id="problem-resource-error"></span>`resource-error` | bundled reference data could not be loaded |
| 500 | <span id="problem-internal"></span>`internal` | anything else |
| 503 | <span id="problem-busy"></span>`busy` | admission full; see `Retry-After` |
| 503 | <span id="problem-not-ready"></span>`not-ready` | workers still warming (probes) |
| 504 | <span id="problem-timeout"></span>`timeout` | hard timeout (process isolation only) |

## OpenAPI and versioning

The OpenAPI 3.1 document is committed as `pydiggs/server/openapi.json`, tested
against the code and published as
[schemas/openapi.json](https://xinp-hub.github.io/pydiggs/schemas/openapi.json). Its
`DiggsFindings*` components are derived from the packaged findings JSON Schema. A
breaking wire change would be served as `/v2` beside `/v1`; within `/v1`, validate
documents against their own `$schema` and ignore unknown fields.
