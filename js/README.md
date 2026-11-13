# @pydiggs/client

TypeScript types and thin clients for [pyDIGGS](https://xinp-hub.github.io/pydiggs/),
which validates and reads [DIGGS](https://diggsml.org/) geotechnical XML files.

**This is not a JavaScript port of pyDIGGS.** Validation and reading are always done by
pyDIGGS itself: on a server you run, or in the user's browser on WebAssembly. This
package gives you the types of what comes back and the code to ask for it.

| Import | What it is | Runs |
|---|---|---|
| `@pydiggs/client` | Types for diggs-findings 1.1 and diggs-model 1.0 documents | anywhere (types only) |
| `@pydiggs/client/http` | Client for the pyDIGGS HTTP API | Node 22+, Deno, Bun, browsers |
| `@pydiggs/client/browser` | pyDIGGS in a Web Worker; the file never leaves the tab | browsers |
| `@pydiggs/client/hybrid` | The browser when it can, your server when it cannot | browsers |
| `@pydiggs/client/node` | pyDIGGS in a Node worker thread, and a handler serving its HTTP API | Node 22+, with `pyodide` installed |

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
import schema from "@pydiggs/client/schemas/diggs-findings-1.1.schema.json" with { type: "json" };
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
first visit; the browser caches them. The pydiggs wheel is checked against the sha256
PyPI published for it before it is installed. Requests run one at a time, and a document
may be at most 25 MiB. `signal` stops the wait for one document, and `terminate()` stops
the worker.

It takes what the HTTP client takes, and answers the same way, because both run
pyDIGGS's own service layer:

```ts
await pydiggs.validate(file, { checks: ["structure", "schema"], diggsVersion: "2.6" });
await pydiggs.validateSarif(file);                       // a SARIF 2.1.0 log
await pydiggs.read(file, { results: "none" });           // tables without their values
await pydiggs.read(file, { results: ["ATTERBERG_B-01_4.0"] }); // one test's values, fast
await pydiggs.listRules({ step: "schematron" });
await pydiggs.explainRule("dictionary.5");
await pydiggs.versions();
```

A refusal is a `PydiggsBrowserError` whose `problem` is pyDIGGS's own: `code`
(`invalid-document`, `invalid-parameter`, `input-too-large`, ...), its fixed `detail`,
and `errors` or `kind` where they apply.

**Time limit.** A document that runs past `timeoutMs` (default 120 s; the largest
document the tab accepts takes about 40 s on a fast laptop) fails with
`code: "timeout"`. On a page served cross-origin isolated
(`Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy:
require-corp` or `credentialless`), the worker is first interrupted, the way Ctrl-C
stops Python, and kept: reading stops at once, validation after the lxml step it is in.
If it has not stopped within `interruptGraceMs` (2 s), or the page is not isolated, the
worker is replaced by a fresh one, loaded from the browser's cache in seconds. An
aborted `signal` interrupts the same way. A worker that dies on a document (out of
memory) is replaced, with `code: "crashed"`. In `PydiggsHybrid`, a timeout or a crash
sends the document to your server if you configured one. `idleTimeoutMs` stops an idle
worker to give its memory back; the next document starts it again.

CI runs the packed package in Chromium, Firefox and WebKit, built by Vite and served
under the Content Security Policy below, and compares every answer with CPython's
(`js/e2e`).

### Making the first visit feel fast

- **Start early.** Create the `PydiggsBrowser` when the page opens, not when the user
  picks a file, so the download overlaps with the user finding their file.
- **Say what is happening.** `onProgress` reports each stage, and `status` says whether
  the worker is `loading`, `ready` or `failed`:

  ```ts
  const pydiggs = new PydiggsBrowser({
    onProgress: (stage) => {
      label.textContent = {
        runtime: "Downloading the validator (first visit only)…",
        packages: "Loading the XML engine…",
        pydiggs: "Installing pyDIGGS…",
        "warm-up": "Preparing the DIGGS schemas…",
      }[stage];
    },
  });
  ```

- **Answer from your server meanwhile,** if your users accept an upload: see
  [Hybrid](#hybrid).

## Hybrid

`PydiggsHybrid` puts the browser and the HTTP client behind one `validate` and one
`read`. A document runs in the tab when the tab can take it, and on your server when it
cannot:

| The document goes to the server when | `reason` |
|---|---|
| it is larger than `maxBrowserBytes` | `too-large` |
| the worker failed to load, or died while working on this document | `browser-unavailable` |
| the document ran past the browser's `timeoutMs` | `browser-timeout` |
| the worker is still loading and `whileLoading: "server"` is set | `browser-loading` |

```ts
import { PydiggsBrowser } from "@pydiggs/client/browser";
import { PydiggsHttpClient } from "@pydiggs/client/http";
import { PydiggsHybrid } from "@pydiggs/client/hybrid";

const lowMemory = ((navigator as { deviceMemory?: number }).deviceMemory ?? 8) <= 4;
const pydiggs = new PydiggsHybrid({
  browser: new PydiggsBrowser(),
  server: new PydiggsHttpClient({ baseUrl: "/api/pydiggs" }),
  maxBrowserBytes: lowMemory ? 5 * 1024 * 1024 : 25 * 1024 * 1024,
});

const { document, via, reason } = await pydiggs.validate(file);
note.textContent = via === "browser" ? "Checked on this device" : `Checked on the server (${reason})`;
```

- **Leave out `server` and nothing is ever uploaded.** The cases above then become
  errors that say why.
- **`whileLoading` is `"wait"` by default,** so a document that could stay in the tab is
  never uploaded just because the worker was still loading.
- **An error about the document is not retried** on the server: a file that is not
  DIGGS fails the same way in both places. Both routes run the same pyDIGGS, and CI
  checks that the browser's output is byte-identical to CPython's.
- **`route(size)`** tells you where a document of that size would go, without sending
  it, so the page can say so before the user commits.

### Frameworks and bundlers

The worker is found with `new URL("./browser-worker.js", import.meta.url)`, which Vite,
webpack and Turbopack all follow. CI runs each set-up below with the packed package
(`js/e2e`, `js/e2e-next`).

| Set-up | Tested | What it needs |
|---|---|---|
| Vite production build (React, Vue, Svelte, plain) | Chromium, Firefox, WebKit | `worker: { format: "es" }` |
| Vite dev server | Chromium | also `optimizeDeps: { exclude: ["@pydiggs/client"] }` |
| Next.js, Turbopack (`next build`, `next dev`) | Chromium | the recipe below |
| Next.js, webpack (`next build --webpack`) | Chromium | the recipe below |
| Vue (Vite) | Chromium, Firefox, WebKit | the Vue recipe below |
| Rspack, Parcel, esbuild | not tested | if the worker is not found, see `createWorker` below |

**Vite** (`vite.config.ts`):

```ts
export default defineConfig({
  worker: { format: "es" },                          // the worker imports shared chunks
  optimizeDeps: { exclude: ["@pydiggs/client"] },    // dev server: keep the worker file
});
```

**Never create the worker during server rendering.** Next.js, Nuxt, SvelteKit and Remix
render components on the server, where there is no `Worker`. Import the package inside
an effect, and share one worker across the app:

```js
"use client";
import { useEffect, useState } from "react";

let shared;
export function getPydiggs() {
  shared ??= import("@pydiggs/client/browser").then(({ PydiggsBrowser }) => new PydiggsBrowser());
  return shared;
}

export function usePydiggs() {
  const [pydiggs, setPydiggs] = useState(null);
  useEffect(() => {
    let alive = true;
    getPydiggs().then((instance) => alive && setPydiggs(instance));
    return () => {
      alive = false;
    };
  }, []);
  return pydiggs; // null until loaded on the client
}
```

The tested version, with load progress, is `js/e2e-next/lib/pydiggs.js`.

**Vue** (and Nuxt): the same shared worker, behind a composable that fills in once
mounted in the browser:

```ts
import { onMounted, ref, shallowRef } from "vue";
import type { PydiggsBrowser } from "@pydiggs/client/browser";

let shared: Promise<PydiggsBrowser> | undefined;
export function getPydiggs() {
  shared ??= import("@pydiggs/client/browser").then(({ PydiggsBrowser }) => new PydiggsBrowser());
  return shared;
}

export function usePydiggs() {
  const pydiggs = shallowRef<PydiggsBrowser | null>(null);
  const status = ref<"loading" | "ready" | "failed">("loading");
  onMounted(async () => {
    const instance = await getPydiggs();
    pydiggs.value = instance;
    instance.ready.then(() => (status.value = "ready"), () => (status.value = "failed"));
  });
  return { pydiggs, status };
}
```

The tested version is `js/e2e/vue.ts`. SvelteKit and Remix follow the same rule: create
the worker in `onMount` or an effect, once for the app.

**Next.js** (`next.config.mjs`) keeps the Node side out of the server bundle, so
`PydiggsNode` finds its worker-thread file and Pyodide finds its WebAssembly:

```js
export default {
  serverExternalPackages: ["@pydiggs/client", "pyodide"],
};
```

A route handler serving pyDIGGS's HTTP API is in [Serving pyDIGGS's HTTP API](#serving-pydiggss-http-api).
With a Content Security Policy, the worker's policy goes on `/_next/static/:path*`,
where Next serves the bundled worker (see `js/e2e-next/next.config.mjs`).

If a bundler does not find the worker, create it from your own code:

```ts
const pydiggs = new PydiggsBrowser({
  createWorker: () =>
    new Worker(new URL("@pydiggs/client/browser/worker", import.meta.url), { type: "module" }),
});
```

### Hosting everything yourself

By default the worker loads Pyodide from the jsDelivr CDN and pydiggs from PyPI. To
keep every request on your own origin, let the package's command assemble the files at
build time and serve the folder:

```bash
npm install -D pyodide@314.0.7
npx pydiggs-client vendor public/pydiggs
```

It copies Pyodide's own files from the npm `pyodide` package, downloads lxml, micropip
and what they need, and the pinned pydiggs wheel from PyPI (or `--pydiggs-wheel PATH`).
Each download must match the sha256 in Pyodide's lock file or on PyPI, and the loader
must match the integrity hash the worker checks, or nothing is written. The folder
lists what is in it, with hashes, in `pydiggs-vendor.json`. Then:

```ts
const pydiggs = new PydiggsBrowser({
  pyodideBaseUrl: "/pydiggs/",
  pydiggsWheelUrl: "/pydiggs/pydiggs-1.8.0-py3-none-any.whl",
});
```

and the worker's CSP needs no third-party origin (`connect-src 'self'`). CI runs this
set-up in Chromium, Firefox and WebKit with exactly that policy.

### Offline

`@pydiggs/client/sw` keeps Pyodide, lxml, micropip and the pydiggs wheel in the Cache
API from your own service worker, so after one visit the page validates with no network:

```ts
// sw.ts, bundled into your service worker
import { cachePydiggs } from "@pydiggs/client/sw";

cachePydiggs(self as unknown as ServiceWorkerGlobalScope, {
  // the same values you pass to PydiggsBrowser, if you self-host:
  // pyodideBaseUrl: "/pydiggs/", pydiggsWheelUrl: "/pydiggs/pydiggs-1.8.0-py3-none-any.whl",
  // precache: true,   // download everything at install, not on first use
});
```

Every file is pinned to a version, so it is served from the cache without asking the
network; a new release of this package uses a new cache and deletes the old one. The
worker still checks the loader's integrity hash and the wheel's sha256. Your page's own
files are yours to cache. Tested in Chromium (`js/e2e`, `offline.html`).

### Content Security Policy

A worker takes its policy from the HTTP headers of the worker script, not from the
page. It needs `'wasm-unsafe-eval'` and `blob:` in `script-src`, `https://cdn.jsdelivr.net`
in `script-src` and `connect-src`, and `https://files.pythonhosted.org` (the pydiggs
wheel) in `connect-src`; self-hosted, `'self'` alone. The page and worker policies are
in the [browser guide](https://xinp-hub.github.io/pydiggs/browser/#content-security-policy).

## Node: pyDIGGS in a JavaScript server

`PydiggsNode` runs the real pyDIGGS in a worker thread, on Pyodide from npm: nothing
Python to install, and the same answers as the browser and as CPython.

```bash
npm install @pydiggs/client pyodide@314.0.7
```

```ts
import { PydiggsNode } from "@pydiggs/client/node";

const pydiggs = new PydiggsNode({
  packageCacheDir: "/var/cache/pyodide", // keep lxml and micropip across restarts
  maxBytes: 100 * 1024 * 1024,           // a server may go above the browser's 25 MiB
});
const findings = await pydiggs.validate(await readFile("BH-01.xml"), { name: "BH-01.xml" });
```

- **The `pyodide` version must be the one exported as `PYODIDE_VERSION`;** the worker
  refuses any other, because CI proves pyDIGGS's answers for that release only.
- **First start downloads** lxml and micropip from Pyodide's CDN, and pyDIGGS from PyPI.
  With `packageCacheDir` and `pydiggsWheelPath` (a wheel on disk), later starts need no
  network.
- **One request at a time per instance,** off the event loop. Run a few instances for
  more throughput, and size the server's memory with the table below.
- **A time limit, as in pyDIGGS's own server.** A document that runs past `timeoutMs`
  (default 60 s) stops its worker thread and fails with `code: "timeout"`; a fresh
  worker takes the documents still waiting. A worker that dies on a document is
  replaced the same way (`code: "crashed"`). `timeoutMs: 0` turns the limit off.
- **Node runtime only.** Edge runtimes cannot hold Pyodide.
- **Bun and Deno** run the Node engine too: CI runs the same Node smoke (worker thread,
  HTTP handler, timeout) under each.

### Batches: several documents at once

One engine runs one document at a time. `PydiggsPool` runs several engines behind the
same `validate` and `read`, each document going to the least busy one; a timeout or
crash in one does not stop the others. It works in the browser and in Node, and goes
wherever an engine does (`PydiggsHybrid`'s `browser`, `createPydiggsHandler`):

```ts
import { PydiggsNode, PydiggsPool } from "@pydiggs/client/node";

const pool = new PydiggsPool(() => new PydiggsNode({ packageCacheDir }), { size: 3 });
const results = await Promise.all(files.map((file) => pool.validate(file.data, { name: file.name })));
```

The default size is one less than the processors reported, from 1 to 4. Each engine
holds its own Pyodide (about 90 MiB, plus some 14 times the document while it works),
so keep pools small in a browser tab, and size a server's memory for them.

### Serverless functions

pyDIGGS runs in Node functions (Vercel, AWS Lambda, Netlify, Google Cloud Run
functions), not in edge functions. Four things decide whether it runs well there:

- **Cold starts.** Bundle a complete package cache with the function so a cold start
  loads Pyodide from disk and downloads nothing (about 2 s), and a read-only file system
  is fine:

  ```bash
  npx pydiggs-client vendor pydiggs-runtime --packages-only
  ```

  ```ts
  const engine = new PydiggsNode({
    packageCacheDir: join(process.cwd(), "pydiggs-runtime"),
    pydiggsWheelPath: join(process.cwd(), "pydiggs-runtime", "pydiggs-1.8.0-py3-none-any.whl"),
  });
  ```

  In Next.js, include the folder in the function:
  `outputFileTracingIncludes: { "/api/pydiggs/[...path]": ["./pydiggs-runtime/**"] }`.
- **Reuse.** Create the engine at module scope or on `globalThis`, as in the route
  handler above, so warm invocations reuse the loaded worker.
- **Memory.** Give the function about 14 times the largest document you accept (Sizing,
  below), plus room for Node and the loaded runtime (an estimate of 250 MiB; measure
  your own); set `maxBytes` to match.
- **Time.** Set `timeoutMs` below the platform's maximum duration, so a slow document
  gets pyDIGGS's 504 and not the platform's own cut-off; a fresh worker then serves the
  next request.

### Serving pyDIGGS's HTTP API

`createPydiggsHandler` turns an engine into a standard Fetch handler for pyDIGGS's
`/v1` API: `/v1/validate` (with `checks`, `diggs_version`, and SARIF for
`Accept: application/sarif+json`), `/v1/read` (with `diggs_version` and `select`,
node keys included), `/v1/versions`, `/v1/rules`, `/v1/rules/{rule_id}` and
`/v1/readyz`, with pyDIGGS's own status codes and problem documents, including 504
`timeout` and 503 `busy` with `Retry-After`. `PydiggsHttpClient`
and `PydiggsHybrid` work against it unchanged. In a
Next.js route handler (`app/api/pydiggs/[...path]/route.ts`):

```ts
import { createPydiggsHandler, PydiggsNode } from "@pydiggs/client/node";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// One engine per server process, made on the first request (never during `next build`)
// and kept on globalThis, so the dev server's reloads do not start new worker threads.
const KEY = Symbol.for("pydiggs-handler");
const handler = () =>
  (globalThis[KEY] ??= createPydiggsHandler(new PydiggsNode(), { basePath: "/api/pydiggs" }));

export const POST = (request: Request) => handler()(request);
export const GET = (request: Request) => handler()(request);
```

With `serverExternalPackages` set as in [Frameworks and bundlers](#frameworks-and-bundlers).
The tested version is `js/e2e-next/app/api/pydiggs/[...path]/route.js`.

Every answer comes from the same service layer as `pydiggs serve --http`; CI compares
them with CPython's. It adds no authentication: put it behind your own.

## Sizing

Measured with `tests/pyodide/memory-probe.mjs` on 2026-11-12 (Apple silicon Mac,
Pyodide 314.0.7, pyDIGGS 1.8.0). The documents are synthetic: many small boreholes and
casings, so time grows with the number of features more than with bytes. Peak memory
includes the 90 MiB a loaded worker holds, and covers validating and then reading.

| Document | Peak memory | Validate | Read |
|---|---|---|---|
| 10 MiB | 224 MiB | 7 s | 15 s |
| 25 MiB | 387 MiB | 15 s | 39 s |
| 50 MiB | 716 MiB | 29 s | 74 s |
| 100 MiB | 1.4 GiB | 41 s | 130 s |

- **Browser:** 25 MiB stays the limit. Memory would allow more on a desktop, but a
  document above 25 MiB takes most of a minute or longer in the tab, which a server does
  better. On phones and tablets, set `maxBrowserBytes` to 5 or 10 MiB.
- **Node server:** up to `ABSOLUTE_MAX_BYTES` (256 MiB). Plan on about 14 times the
  largest document in memory per instance, and timeouts of minutes at 100 MiB.
- **Cloudflare Workers:** Python Workers run Pyodide too, but an isolate has 128 MB
  including WebAssembly, and a loaded pyDIGGS holds about 90 MiB before the document's
  14 times its size, so only files of a megabyte or two would fit. Use a Node function
  or the container for anything larger.

Where the time goes: reading spends about a third of its time writing the model out as
JSON (`tests/pyodide/profile-read.mjs`; 2 MiB synthetic document: 6.7 s, of which 2.4 s
in the model encoder). That is the next thing to speed up in pyDIGGS itself.

### Watched upstream

- **Start-up snapshots.** Pyodide can restore a memory snapshot of the bare runtime in
  about 0.1 s instead of 1.7 s, which would halve a cold start
  (`tests/pyodide/snapshot-probe.mjs`), but the API is private, and a snapshot with lxml
  loaded is refused. Adopted when Pyodide makes it public.
- **Memory.** 32-bit WebAssembly caps a worker at 4 GiB, and browsers often give a tab
  less. 64-bit WebAssembly (memory64) would lift this; Pyodide does not build for it
  yet. Capping one Pyodide instance's memory was taken up upstream
  (pyodide/pyodide#6482); it is adopted here once a pinned Pyodide release has it.

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
