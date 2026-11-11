# @pydiggs/client

TypeScript types and thin clients for [pyDIGGS](https://xinp-hub.github.io/pydiggs/),
which validates and reads [DIGGS](https://diggsml.org/) geotechnical XML files.

**This is not a JavaScript port of pyDIGGS.** Validation and reading are always done by
pyDIGGS itself: on a server you run, or in the user's browser on WebAssembly. This
package gives you the types of what comes back and the code to ask for it.

| Import | What it is | Runs |
|---|---|---|
| `@pydiggs/client` | Types for diggs-findings 1.0 and diggs-model 1.0 documents | anywhere (types only) |
| `@pydiggs/client/http` | Client for the pyDIGGS HTTP API | Node 22+, Deno, Bun, browsers |
| `@pydiggs/client/browser` | pyDIGGS in a Web Worker; the file never leaves the tab | browsers |

It has no runtime dependencies and ships as ES modules only.

```bash
npm install @pydiggs/client
```

> **Every string in a findings or model document is untrusted.** Messages, paths and
> identifiers are copied from the validated file, so a crafted file can put any text
> there, including HTML. Write them to a page with `textContent` or DOM nodes, never
> with `innerHTML`, and escape them before they reach a terminal or a prompt.

## Types

```ts
import type { DiggsFindingsDocument, DiggsModelDocument } from "@pydiggs/client";

function errors(document: DiggsFindingsDocument): number {
  return document.files.reduce((sum, file) => sum + file.counts.error, 0);
}
```

The names are those of the `DiggsFindings*` and `DiggsModel*` components in pyDIGGS's
OpenAPI document. The types are generated from the JSON Schemas, which are also in the
package for a validator such as Ajv:

```ts
import schema from "@pydiggs/client/schemas/diggs-findings-1.0.schema.json" with { type: "json" };
```

Both formats grow by adding optional fields within 1.x, so ignore fields you do not
know. The model's string vocabularies (`kind`, `relation`, …) are open.

## HTTP client

Start a server with `pydiggs serve --http`, or run the
[container image](https://xinp-hub.github.io/pydiggs/container/).

```ts
import { readFile } from "node:fs/promises";
import { PydiggsHttpClient, PydiggsHttpError } from "@pydiggs/client/http";

const pydiggs = new PydiggsHttpClient({ baseUrl: "http://127.0.0.1:8000" });

const findings = await pydiggs.validate(await readFile("BH-01.xml"), { name: "BH-01.xml" });
console.log(findings.files[0]?.verdict); // "valid", "valid-with-warnings", "not-valid", …

const model = await pydiggs.read(await readFile("BH-01.xml"), { results: "none" });
console.log(model.features.map((feature) => feature.name));
```

- **An invalid document is not an error.** `validate` resolves with findings; read the
  verdict. Only a request that produced no document throws.
- **Errors** are `PydiggsHttpError` with `status`, the problem's `code` (switch on it),
  the whole `problem` document and `retryAfterSeconds`. A busy server answers 503
  `busy` with a retry time.
- **Time limits and cancelling:** pass `signal`, for example `AbortSignal.timeout(30_000)`.
- **Credentials:** pyDIGGS has no authentication; put it behind your own gateway and
  pass `headers` (an object, or a function called for each request).
- **From a browser,** call it through your own server. pyDIGGS refuses cross-site
  requests unless the origin is listed in its `cors_origins`.

The full contract is in the [HTTP API guide](https://xinp-hub.github.io/pydiggs/http-api/).

## Browser

```ts
import { PydiggsBrowser } from "@pydiggs/client/browser";

const pydiggs = new PydiggsBrowser(); // starts loading at once
await pydiggs.ready;                  // a few seconds; requests made earlier wait

input.addEventListener("change", async () => {
  const file = input.files?.[0];
  if (!file) return;
  const findings = await pydiggs.validate(file);
  status.textContent = findings.files[0]?.verdict ?? "";
});
```

The worker downloads Pyodide (about 12 MB), lxml and pydiggs (about 1.5 MB) on the
first visit; the browser caches them. Requests run one at a time, and a document may
be at most 25 MiB. A running request cannot be interrupted: `signal` stops the wait,
`terminate()` stops the worker.

### Bundlers

The default worker is found with `new URL("./browser-worker.js", import.meta.url)`.
If your bundler does not follow that pattern inside dependencies (Vite's dependency
pre-bundling is one case), create the worker from your own code:

```ts
const pydiggs = new PydiggsBrowser({
  createWorker: () =>
    new Worker(new URL("@pydiggs/client/browser/worker", import.meta.url), { type: "module" }),
});
```

### Hosting everything yourself

By default the worker loads Pyodide from the jsDelivr CDN and pydiggs from PyPI. To
keep every request on your own origin, serve the Pyodide `full` distribution of
version `PYODIDE_VERSION` and a pydiggs wheel, and point the worker at them:

```ts
import { PydiggsBrowser, PYODIDE_VERSION } from "@pydiggs/client/browser";

const pydiggs = new PydiggsBrowser({
  pyodideBaseUrl: `/vendor/pyodide-${PYODIDE_VERSION}/`,
  pydiggsWheelUrl: "/vendor/pydiggs-1.7.0-py3-none-any.whl",
});
```

Pyodide's loader is checked against a pinned integrity hash wherever it comes from.
The wheel you name is not checked, so only name one you control.

### Content Security Policy

A worker takes its policy from the HTTP headers of the worker script, not from the
page. It needs `'wasm-unsafe-eval'` and `blob:` in `script-src`, and the hosts it
downloads from in `script-src` and `connect-src`. The exact policies, for the CDN and
for self-hosting, are in the
[browser guide](https://xinp-hub.github.io/pydiggs/browser/#content-security-policy).

## Versions

| This package | Works with |
|---|---|
| types, `/http` | any pyDIGGS server that speaks HTTP API `/v1` with diggs-findings 1.x; `read` needs pyDIGGS 1.7.0 or later |
| `/browser` | the pinned `PYODIDE_VERSION` and `PYDIGGS_VERSION`, both exported |

## Development

The types, the pins and the schemas are generated from the files in the pyDIGGS
repository; nothing under `src/generated/` or `schemas/` is edited by hand.

```bash
npm ci
npm run generate   # after a change to a schema, the OpenAPI document or the browser example
npm run check      # drift check, type check, tests, build, publint, attw
```

## License

Apache-2.0.
