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
| `@pydiggs/client` | TypeScript types for [diggs-findings 1.0](findings.md) and [diggs-model 1.0](model.md) documents | nothing: types only |
| `@pydiggs/client/http` | A `fetch` client for the [HTTP API](http-api.md) | a pyDIGGS server or [container](container.md) |
| `@pydiggs/client/browser` | pyDIGGS in a Web Worker on [Pyodide](browser.md); the file never leaves the tab | a browser |

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
pinned Pyodide and integrity check, the same 25 MiB limit, one request at a time. To keep
every download on your own origin, pass `pyodideBaseUrl` and `pydiggsWheelUrl`. The
[Content Security Policy](browser.md#content-security-policy) on that page applies
unchanged.

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
