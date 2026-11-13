# JavaScript and TypeScript

[`@pydiggs/client`](https://www.npmjs.com/package/@pydiggs/client) is the npm package for
web and Node applications. It is **not a JavaScript port**: validation and reading are
always done by pyDIGGS itself, on a server you run or in the user's browser. The package
gives you the types of what comes back and the code to ask for it.

```bash
npm install @pydiggs/client
```

| Import | What it is | Needs |
|---|---|---|
| `@pydiggs/client` | TypeScript types for [diggs-findings 1.1](findings.md) and [diggs-model 1.0](model.md) documents | nothing: types only |
| `@pydiggs/client/http` | A `fetch` client for the [HTTP API](http-api.md) | a pyDIGGS server or [container](container.md) |
| `@pydiggs/client/browser` | pyDIGGS in a Web Worker on [Pyodide](browser.md); the file never leaves the tab | a browser |
| `@pydiggs/client/hybrid` | The browser when it can, your server when it cannot | a browser, and a server if you want the fallback |
| `@pydiggs/client/node` | pyDIGGS in a Node worker thread, and a handler serving the [HTTP API](http-api.md) | Node 22+ and the npm `pyodide` package |

It has no runtime dependencies and ships as ES modules only.

!!! warning "Every string in a findings or model document is untrusted"
    Messages, paths and identifiers are copied from the validated file. Write them to a
    page with `textContent` or DOM nodes, never `innerHTML`, and escape them before they
    reach a terminal or a prompt.

## Against a server

```ts
import { readFile } from "node:fs/promises";
import { PydiggsHttpClient, PydiggsHttpError } from "@pydiggs/client/http";

const pydiggs = new PydiggsHttpClient({ baseUrl: "http://127.0.0.1:8000" });
const data = await readFile("BH-01.xml");

try {
  const findings = await pydiggs.validate(data, { name: "BH-01.xml" });
  console.log(findings.files[0]?.verdict);

  const model = await pydiggs.read(data, { results: "none" });
  console.log(model.features.map((feature) => feature.name));
} catch (error) {
  if (error instanceof PydiggsHttpError && error.code === "busy") {
    console.log(`retry in ${error.retryAfterSeconds} s`);
  } else {
    throw error;
  }
}
```

An invalid document is not an error: `validate` resolves with findings, and the verdict
says what was found. Errors carry the [problem `code`](http-api.md#errors). From a browser,
call the API through your own server; pyDIGGS refuses cross-site requests by default.

## In the browser

```ts
import { PydiggsBrowser } from "@pydiggs/client/browser";

const pydiggs = new PydiggsBrowser();
const findings = await pydiggs.validate(file); // a File from an <input type="file">
```

The worker is the one described in [Browser (Pyodide)](browser.md), packaged: the same
pinned Pyodide and integrity check, the same 25 MiB limit, one request at a time, and
the pydiggs wheel checked against the sha256 PyPI published for it. To keep every
download on your own origin, pass `pyodideBaseUrl` and `pydiggsWheelUrl`. The
[Content Security Policy](browser.md#content-security-policy) on that page applies,
except that the package's worker never contacts `pypi.org`.

It takes what the [HTTP client](#against-a-server) takes, because the worker runs
pyDIGGS's own service layer, as `pydiggs serve --http` does: `checks` and `diggsVersion`
for `validate`, `validateSarif`, `results` of `"all"`, `"none"` or a list of node keys
for `read`, `versions()`, `listRules()` and `explainRule()`. Refusals carry pyDIGGS's
problem (`error.problem.code`, `detail`, `errors`, `kind`).

Create the worker when the page opens, so its first-visit download (about 14 MB, cached
afterwards) overlaps with the user finding a file, and show its `onProgress` stages.
A document that runs past `timeoutMs` (default 120 s) fails alone. On a page served
cross-origin isolated, the worker is interrupted and kept (reading stops at once,
validation after its current lxml step); otherwise, or if it does not stop within
2 s, a fresh worker takes the next document. `idleTimeoutMs` gives an idle worker's
memory back.

For offline use, `cachePydiggs(self)` from `@pydiggs/client/sw`, called in your own
service worker, keeps Pyodide and the wheel in the Cache API after one visit. Desktop and installable apps (a PWA, Tauri 2, Electron) vendor everything into the
app's assets; the package's README has the
[recipes](https://github.com/xinp-hub/pydiggs/tree/master/js#standalone-offline-apps).

### Frameworks

CI runs the packed package in a Vite app (production build in Chromium, Firefox and
WebKit, and the dev server) and in a Next.js app (Turbopack and webpack builds, and
`next dev`, with a route handler serving the HTTP API). Two rules cover most apps:

- **Vite:** set `worker: { format: "es" }`, and for the dev server
  `optimizeDeps: { exclude: ["@pydiggs/client"] }`.
- **Server rendering** (Next.js, Nuxt, SvelteKit, Remix): import
  `@pydiggs/client/browser` inside an effect, never during rendering, and share one
  worker across the app. In Next.js, also set
  `serverExternalPackages: ["@pydiggs/client", "pyodide"]` for the Node side.

The package's [README](https://github.com/xinp-hub/pydiggs/tree/master/js#frameworks-and-bundlers)
has the recipes (React, Vue, Next.js), and `js/e2e` and `js/e2e-next` the tested apps.

### Self-hosting, serverless and batches

- **No CDN:** `npx pydiggs-client vendor public/pydiggs` assembles Pyodide, lxml and the
  pydiggs wheel into one folder, each file checked against its published hash; point
  `pyodideBaseUrl` at it and the worker's CSP needs no third-party origin.
- **Serverless functions** (Node runtime): bundle `pydiggs-client vendor <dir>
  --packages-only` with the function and pass it as `packageCacheDir`, so a cold start
  downloads nothing; keep the engine at module scope, and set `timeoutMs` below the
  platform's limit.
- **Batches:** `PydiggsPool` runs several engines, in a browser or in Node.

## Browser first, server as fallback

```ts
import { PydiggsBrowser } from "@pydiggs/client/browser";
import { PydiggsHttpClient } from "@pydiggs/client/http";
import { PydiggsHybrid } from "@pydiggs/client/hybrid";

const pydiggs = new PydiggsHybrid({
  browser: new PydiggsBrowser(),
  server: new PydiggsHttpClient({ baseUrl: "/api/pydiggs" }),
  maxBrowserBytes: 10 * 1024 * 1024,
});
const { document, via, reason } = await pydiggs.validate(file);
```

A document runs in the tab when it can, and on the server when it is larger than
`maxBrowserBytes`, when the worker failed to load or died on it, or, with
`whileLoading: "server"`, while the worker is still loading. Every result says where it
ran and why. Without a `server`, nothing is ever uploaded.

## A JavaScript server, no Python

For apps whose server is JavaScript (Next.js, Express, Fastify, Hono, a serverless
function on the Node runtime), `PydiggsNode` runs pyDIGGS in a worker thread on Pyodide
from npm, and `createPydiggsHandler` serves the same `/v1` API as `pydiggs serve --http`
(validate with `checks`, `diggs_version` and SARIF, read with `select`, versions and
rules), answered by the same service layer:

```ts
import { createPydiggsHandler, PydiggsNode } from "@pydiggs/client/node";

const handle = createPydiggsHandler(new PydiggsNode(), { basePath: "/api/pydiggs" });
// a Fetch handler: (request: Request) => Promise<Response>
```

`startupSnapshotDir` (experimental) halves later starts by restoring a saved snapshot of
the Pyodide runtime. Point `PydiggsHybrid`'s `server` at that path, and large documents, weak devices and a
worker that fails to load all fall back to your own server, with no Python anywhere.
Use the [Python server](http-api.md) or the [container](container.md) where you want
pyDIGGS's process pool, its own limits and logging, or documents above 256 MiB.

## Types only

```ts
import type { DiggsFindingsDocument, DiggsModelDocument } from "@pydiggs/client";
```

The types are generated from the JSON Schemas and carry the names of the `DiggsFindings*`
and `DiggsModel*` components in the [OpenAPI document](http-api.md#openapi-and-versioning).
The schemas ship in the package too, as `@pydiggs/client/schemas/*.json`.

## Versions

The package has its own version number. What it works with is stated by contract: any
pyDIGGS server that speaks HTTP API `/v1`, and diggs-findings 1.x and diggs-model 1.x
documents; `read` needs pyDIGGS 1.7.0 or later. The browser entry pins one Pyodide and
one pyDIGGS release, exported as `PYODIDE_VERSION` and `PYDIGGS_VERSION`.

The package's own [README](https://github.com/xinp-hub/pydiggs/tree/master/js#readme) has
the bundler notes and every option.
