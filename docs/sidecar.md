# Stdio sidecar protocol

`pydiggs serve --stdio` runs pydiggs as a **sidecar**: a child process that any host
application can start and talk to over its standard input and output. It suits desktop
and native applications in any language (Rust, C#, Java, Swift, C++, TypeScript on Node or
Electron, and so on) that cannot embed CPython. This page is the normative specification
of protocol version **1.0**. [Host guidance](sidecar-hosts.md) covers supervision,
packaging and example clients.

The sidecar is part of the core package: it needs no extra and uses only the Python
standard library on top of pydiggs itself. A machine-readable description of the methods
ships with the package as `pydiggs/sidecar/openrpc.json` (OpenRPC 1.3) and is published as
[schemas/openrpc.json](https://xinp-hub.github.io/pydiggs/schemas/openrpc.json).

!!! warning "Findings are untrusted text"
    Every string field in a findings document (messages, paths, element names, values)
    can carry text taken from the validated file. Escape it before rendering it in HTML,
    a rich-text control, a terminal or a log.

## Launching

```text
pydiggs serve --stdio [--max-bytes N] [--max-findings N] [--timeout S]
                      [--allow-path DIR]... [--log-level L] [--log-format text|json]
```

| option | default | meaning |
|---|---|---|
| `--max-bytes` | 26,214,400 (25 MiB) | largest decoded document sent as content, and by path when given |
| `--max-findings` | 10,000 | findings kept per document (a truncation finding marks the cut) |
| `--timeout` | 60 | default and largest per-request **time budget**, in seconds |
| `--allow-path DIR` | none | enables by-path requests for files under `DIR` (repeatable) |
| `--log-level` | `warning` | `debug`, `info`, `warning`, `error`, `critical` |
| `--log-format` | `text` | `json` writes one object per line with `ts`, `level`, `msg`, `request_id` |

By-path documents may be up to 100 MiB, or `--max-bytes` when it is given.
`python -m pydiggs serve --stdio` is equivalent. The sidecar only ever uses pydiggs's
bundled schemas, dictionaries and Schematron rules; it never reaches the network and never
reads local dictionaries next to a document.

### Exit status

| status | when |
|---|---|
| 0 | `exit` after `shutdown`; stdin closed (EOF); stdout closed (broken pipe); SIGTERM, SIGINT or Ctrl+Break |
| 1 | `exit` without a prior `shutdown`, or an unexpected internal failure |
| 2 | bad launch arguments, including a refused `--allow-path` root or unusable stdio |
| 3 | the bundled reference data could not be loaded during `initialize` |

## Framing

- One JSON-RPC 2.0 message per line, UTF-8, terminated by `\n` (LF). Input lines may end
  in `\r\n`; blank lines are skipped.
- Split on byte `0x0A` only. Responses never contain a raw LF or CR, and U+0085, U+2028
  and U+2029 are always written as `\u` escapes, so line splitters that also break on those
  characters still see one message per line.
- Input is decoded as strict UTF-8 whatever the locale. A byte-order mark is an error.
- A line may be at most `max_line_bytes` long (reported by `initialize`): by default
  `ceil(4/3 × max_bytes) + 65,536`, about 34 MiB. A longer line is discarded up to the next
  LF and answered with `-32600` and `"id": null`.
- Responses can be several megabytes on one line (up to `max_findings` findings). Read
  them with a streaming line reader that has no fixed buffer size.
- Batches (JSON arrays) are not supported (`-32600`). Parameters are always an object
  (by name).
- stdout carries **only** frames. At start the sidecar keeps a private copy of descriptor
  1 for frames and points descriptor 1 at stderr, so stray output from Python, C libraries
  or child processes lands on stderr.

### stderr

Logs go to stderr. **The host must drain stderr or redirect it** to a file or the null
device: a full stderr pipe (often 64 KiB) blocks the sidecar. Logs never contain document
content or absolute paths, and client strings are escaped.

## Lifecycle

1. The host starts the process and sends `initialize`. Until its response arrives, the
   sidecar reads nothing more, so requests may be pipelined behind it. Requests sent
   before `initialize` (on a fresh process) are answered `not-initialized`; notifications
   are ignored.
2. `initialize` compiles the validators for the requested DIGGS versions (all by default;
   about a second cold), then answers. If this fails the answer is `resource-error` and the
   process exits with status 3.
3. The host sends requests. Each response carries the request's `id`; responses can arrive
   in a different order from the requests (see [Ordering](#ordering)).
4. To stop: send `shutdown`, wait for its `null` result, then send the `exit`
   notification. Closing stdin also stops the sidecar (status 0) and abandons any running
   validation.

### Version negotiation

`initialize.params.protocol_version` is `"MAJOR.MINOR"`. The major must equal the
sidecar's (`1`), otherwise the answer is `unsupported-protocol-version` with
`data.supported`, e.g. `["1.0"]`. The sidecar answers the highest minor it supports that
does not exceed the requested one. Minor versions only add: hosts must ignore unknown
result fields; new methods are advertised in `capabilities`. The sidecar refuses unknown
parameters with `-32602` (except unknown keys inside `capabilities`, which it ignores).

### Ordering

- `ping`, `versions`, `rules.list` and `rules.explain` are answered immediately, even
  while a validation runs.
- Validations run one at a time. At most **one** more may wait behind the running one;
  a further `validate` is answered `busy` at once. A host that needs throughput runs a
  pool of sidecar processes.
- Request ids must be unique among in-flight validations; reusing one is `-32600`.

### Cancellation and time limits

- `$/cancelRequest {id}`: a waiting validation is removed and answered `cancelled`
  at once. The running one ends `cancelled` at its next step boundary. Unknown or
  finished ids are ignored.
- `time_budget_s` (default and maximum: `--timeout`) is checked at the same step
  boundaries. Running out is **not** an error: the result is a normal findings document
  with a `structure.timeout` ERROR finding and the unfinished steps marked `incomplete`.
- Neither can interrupt a single schema or Schematron pass already running. For a hard
  limit, the host **must** kill the process (see [host guidance](sidecar-hosts.md)) and
  show the file as "not checked", never as passed.

### Shutdown and exit

`shutdown` lets the running validation finish, answers `shutting-down` to the waiting
one and to every later request, and then answers `null` (after the running validation's
own response). `exit` then ends the process with status 0; `exit` without `shutdown`
ends it with status 1. `exit` is honoured at any time and abandons any running work.

## Methods

### `initialize`

| param | type | notes |
|---|---|---|
| `protocol_version` | string, required | `"1.0"` |
| `client_info` | object | `{name ≤ 128 chars, version ≤ 64 chars}`, logged at `info` |
| `capabilities` | object | `{progress: bool}` asks for `$/progress` notifications |
| `diggs_versions` | string[] | DIGGS versions to warm up (default: every released bundled version, not the experimental `3.1-dev`) |

Result:

```json
{
  "protocol_version": "1.0",
  "pydiggs_version": "1.5.0",
  "findings_schema_version": "1.0",
  "findings_schema_url": "https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-1.0.schema.json",
  "diggs_versions": ["2.5.a", "2.6", "3.0.0", "3.1-dev"],
  "reference_data": {"def": "…", "schema-dev": "…", "schemas": "…"},
  "capabilities": {
    "validate": {"content": true, "path": false},
    "cancel": true, "progress": false, "rules": true, "ping": true
  },
  "limits": {
    "max_bytes": 26214400, "max_path_bytes": 104857600, "max_line_bytes": 35018070,
    "max_findings": 10000, "max_response_bytes_estimate": 20545536, "time_budget_s": 60.0
  }
}
```

`capabilities.validate.path` is true only when the sidecar was started with
`--allow-path`. `max_response_bytes_estimate` is a rough upper bound for sizing buffers,
not a guarantee.

### `validate`

| param | type | notes |
|---|---|---|
| `content_base64` | string | the document, standard base64 with padding and no whitespace |
| `path` | string | an absolute path under an `--allow-path` root (instead of content) |
| `name` | string ≤ 4096 | the name to report; reduced to a basename (default `input.xml`, or the path's basename) |
| `checks` | string[] | steps to run (default: all), as for `pydiggs validate --checks` |
| `diggs_version` | string | force a DIGGS version instead of detecting it |
| `time_budget_s` | number | `0 < t ≤ --timeout` |

Exactly one of `content_base64` and `path` is required. The encoded length is checked
against `max_bytes` before decoding.

The result is a complete one-file **diggs-findings 1.0** document, the same as
`pydiggs validate --format json` gives for that file apart from `files[].path` (always the
basename here) and with no `absolute_path`. Validate it against the document's own
`$schema` and tolerate unknown fields. A malformed or invalid document is a normal result
with findings, not an error.

### `ping`, `versions`

`ping` takes no parameters and returns `{}`. `versions` returns the same fields as
`pydiggs.version_info().to_dict()`.

### `rules.list`, `rules.explain`

`rules.list {step?}` returns every rule pydiggs can emit (`RuleInfo` objects: `id`,
`pattern`, `step`, `description`, `severities`, `help_uri`, `known`), optionally for one
step; open families appear as patterns. `rules.explain {rule_id}` (at most 256 characters)
describes one id; a well-formed unknown id returns `known: false`, a malformed one is
`-32602`.

### `shutdown`, `exit`

See [Shutdown and exit](#shutdown-and-exit). `shutdown` takes no parameters and returns
`null`; `exit` is a notification.

### Notifications

| method | direction | params |
|---|---|---|
| `exit` | host → sidecar | none |
| `$/cancelRequest` | host → sidecar | `{id}` |
| `$/progress` | sidecar → host, only if negotiated | `{request_id, step, status}` |

Other notifications, `$/`-prefixed or not, are ignored. In protocol 1.0, `$/progress`
reports `step: "queue", status: "waiting"` when a validation waits behind another,
`step: "validate", status: "started"` when it starts and `status: "running"` at each step
boundary. Treat it as a liveness signal; hosts must ignore step and status values they do
not know (later minors may name the individual checks).

## Errors

Every error has `error.code`, a fixed `error.message` and `error.data.code`, a stable
slug. **Switch on `data.code`.** The `-32000..-32099` band is pydiggs-defined: do not read
it with LSP or MCP meanings.

| code | `data.code` | when | extra `data` |
|---|---|---|---|
| -32700 | `parse-error` | invalid UTF-8, byte-order mark, invalid JSON (NaN/Infinity included), nesting deeper than 64 levels, integer too long | |
| -32600 | `invalid-request` | not a request object, batch, missing/null/invalid id, line too long, duplicate in-flight id, second `initialize` | `max_line_bytes` (line too long) |
| -32601 | `method-not-found` | unknown method | |
| -32602 | `invalid-params` | missing, unknown or malformed parameters | `errors: [{param, problem}]` |
| -32603 | `internal` | unexpected failure; details go to stderr only | `correlation_id` |
| -32000 | `busy` | one validation running and one waiting | |
| -32001 | `not-initialized` | request before `initialize` completed | |
| -32002 | `shutting-down` | request after `shutdown`, or dropped by it | |
| -32003 | `input-too-large` | document over `max_bytes` / `max_path_bytes` | `max_bytes` (content mode) |
| -32004 | `compressed-input` | gzip-compressed document | |
| -32005 | `path-not-permitted` | path mode off, refused path form, outside every root, missing, or not a regular file (all identical) | |
| -32006 | `input-unreadable` | the permitted file could not be read | |
| -32007 | `resource-error` | the bundled reference data could not be loaded | `correlation_id` |
| -32008 | `cancelled` | cancelled by `$/cancelRequest` | |
| -32009 | `unsupported-protocol-version` | major version mismatch | `supported` |

Errors for unparseable lines carry `"id": null`. A broken notification gets no answer.
Messages never echo request values; at most they name the reported basename.

## Path access

Sending content is the default and works everywhere. By-path requests avoid base64 for
large local files but are **off** unless the host starts the sidecar with one or more
`--allow-path DIR` roots.

- Roots are resolved once at launch. A filesystem root (`/`, a drive root, a UNC share
  root), the home folder itself or any of its ancestors is refused (exit status 2).
- A request `path` must be a plain absolute path. Refused: any URI (`file:` included),
  relative paths, `..` components, UNC paths, `\\?\` and `\\.\` prefixes, and Windows
  device names (`CON`, `NUL`, `COM1`, …) in any component, on every OS.
- A path that, **as spelled**, is not under a root is refused without being opened. A
  root may be spelled as given at launch or in its canonical form, so with
  `--allow-path /tmp` on macOS both `/tmp/a.xml` and `/private/tmp/a.xml` are accepted.
- The sidecar then **opens, then verifies**: it opens the file without following a final
  symlink (and without blocking on a FIFO), checks that the descriptor is a regular file,
  asks the operating system for the descriptor's real path (`/proc/self/fd` on Linux,
  `F_GETPATH` on macOS, `GetFinalPathNameByHandleW` on Windows) and accepts it only if that
  path lies inside a root. A symlink, or a file swapped between requests, cannot lead
  outside the roots.
- Every refusal is the same `-32005`, so a caller cannot probe which files exist.

!!! warning "Hard links are not contained"
    A hard link is not a pointer to another path: it *is* the file, under a second name.
    A hard link inside a root to a file elsewhere therefore has a real path inside the
    root, and the sidecar reads it. Anyone who can create files in a root can link any
    file they can reach on the same volume into it. Do not give untrusted parties write
    access to a root; point `--allow-path` at a folder only the host writes to.

## Memory and size

With the default 25 MiB content limit a request line is about 34 MiB and the sidecar peaks
at roughly 0.5 GiB. By-path documents of up to 100 MiB can peak at about 1.5 GiB on dense
files. Plan for about 2 GiB per sidecar process and cap it from the host (see
[host guidance](sidecar-hosts.md#memory-caps)).

## Example session

```text
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_version":"1.0","client_info":{"name":"example-host","version":"0.1"}}}
← {"jsonrpc":"2.0","id":1,"result":{"protocol_version":"1.0","pydiggs_version":"1.5.0",…}}
→ {"jsonrpc":"2.0","id":2,"method":"validate","params":{"content_base64":"PERpZ2dz…","name":"BH-01.xml"}}
→ {"jsonrpc":"2.0","id":3,"method":"ping"}
← {"jsonrpc":"2.0","id":3,"result":{}}
← {"jsonrpc":"2.0","id":2,"result":{"$schema":"https://xinp-hub.github.io/pydiggs/schemas/diggs-findings-1.0.schema.json","format":"diggs-findings",…}}
→ {"jsonrpc":"2.0","id":4,"method":"validate","params":{"path":"relative.xml"}}
← {"jsonrpc":"2.0","id":4,"error":{"code":-32005,"message":"The path is not permitted.","data":{"code":"path-not-permitted"}}}
→ {"jsonrpc":"2.0","id":5,"method":"shutdown"}
← {"jsonrpc":"2.0","id":5,"result":null}
→ {"jsonrpc":"2.0","method":"exit"}
```
