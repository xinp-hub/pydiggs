# Browser (Pyodide)

pydiggs runs in a web page through [Pyodide](https://pyodide.org/), CPython compiled to
WebAssembly. The page reads the file the user picks and validates it in the same tab:
**the document never leaves the browser**, and no server of yours is involved beyond
serving static files.

The [browser-worker example](https://github.com/xinp-hub/pydiggs/tree/master/examples/browser-worker)
is a complete page in four small files, with no build step and no dependency besides
Pyodide. It runs pydiggs in a module Web Worker, so validation never blocks the page.

!!! warning "Every string field in a findings document is untrusted"
    `message`, `location.xpath`, `location.gml_id` and `files[].path` are copied from the
    document or its file name, so a crafted file can put any text there, including HTML
    such as `<img src=x onerror=...>`. Every renderer must escape them. In a page, write
    them with `textContent` or DOM nodes, never `innerHTML`, `outerHTML`,
    `insertAdjacentHTML` or `document.write`, and never into a template that is later
    parsed as HTML. The example does this, and its test suite checks that no script in it
    uses those sinks.

## Run the example

```bash
cd examples/browser-worker
python3 serve.py                                      # http://127.0.0.1:8000/
```

Open the address, wait for "Ready", and pick a DIGGS file. `serve.py` uses only the
standard library, binds to localhost and serves the example's files with their
Content-Security-Policy headers (see [CSP](#content-security-policy)). Any static host
works the same way.

To try an unreleased pydiggs, build a wheel and hand it to the server; it serves the
wheel at `/wheels/` and prints a URL with `?wheel=` that makes the worker install it
instead of the PyPI release. The worker honours `?wheel=` only on `localhost`,
`127.0.0.1` or `[::1]`, so a link cannot swap the wheel on a deployed site:

```bash
uv build --wheel
python3 examples/browser-worker/serve.py --wheel dist/pydiggs-1.4.1-py3-none-any.whl
```

The worker accepts only a same-origin URL to a pure pydiggs wheel there.

## How it works

The worker ([`worker.mjs`](https://github.com/xinp-hub/pydiggs/blob/master/examples/browser-worker/worker.mjs))
does the slow part once, when the page opens:

1. imports Pyodide 314.0.7's loader, checked against a Subresource Integrity hash;
2. `loadPackage(["lxml", "micropip"])`: Pyodide's own lxml 6.1.3 build;
3. `micropip.install("pydiggs==…", deps=False)`: the pure pydiggs wheel, without `rich`
   or any extra, which the browser does not need;
4. `pydiggs.warm_up()`, so the first file does not pay for compiling the schemas.

Then it answers messages. Each request carries an `id` that its reply repeats:

| Direction | Message |
|---|---|
| page → worker | `{id, type: "validate", name, data}` with `data` an `ArrayBuffer` (transfer it) |
| worker → page | `{type: "ready", versions}` once loaded, or `{type: "failed", message}` |
| worker → page | `{id, type: "result", document}`: a [diggs-findings 1.0](findings.md) document |
| worker → page | `{id, type: "error", message}`: refused (too large, gzip-compressed, …) |

`document` is exactly what `validate_bytes(data, name=name, max_bytes=25 MiB).to_json()`
gives in CPython, byte for byte (CI checks this). From the page
([`app.mjs`](https://github.com/xinp-hub/pydiggs/blob/master/examples/browser-worker/app.mjs)):

```js
const worker = new Worker(new URL("./worker.mjs", import.meta.url), { type: "module" });
const data = await file.arrayBuffer();
worker.postMessage({ id: 1, type: "validate", name: file.name, data }, [data]);
```

`validate_bytes` reads no file and no URL the document names: local dictionaries are off
and `allow_network` is false, so validation itself makes no request at all.

## Loading Pyodide: CDN or self-hosted

The first visit downloads the Pyodide runtime (a 9.6 MB WebAssembly binary and a 2.5 MB
standard library), lxml and pydiggs (about 1.5 MB); the browser caches them afterwards.
Choose where they come from with `PYODIDE_BASE` in `worker.mjs`.

**From the jsDelivr CDN (the default).** The loader, `pyodide.mjs`, is fetched with
`integrity: "sha384-…"` and imported from a `blob:` URL, because `import()` takes no
integrity option and module workers have no import maps. That hash pins the loader only:

- `pyodide.asm.mjs`, `pyodide.asm.wasm`, `python_stdlib.zip` and `pyodide-lock.json`
  are then fetched by the loader without an integrity check;
- lxml and micropip are checked against the sha256 in that `pyodide-lock.json`;
- pydiggs is checked by micropip against the sha256 PyPI publishes for it.

So the CDN (and PyPI) stay in your trust base. Compute the hash from the file you
reference:

```bash
curl -sSf https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs \
  | openssl dgst -sha384 -binary | openssl base64 -A
```

**Self-hosted.** Download `pyodide-314.0.7.tar.bz2` (or the smaller
`pyodide-core-314.0.7.tar.bz2` plus the `lxml` and `micropip` wheels named in its
`pyodide-lock.json`) from the [Pyodide release](https://github.com/pyodide/pyodide/releases/tag/314.0.7),
serve it from your own origin, and set
`PYODIDE_BASE = new URL("./pyodide/", import.meta.url).href`. The integrity hash stays
valid (the file is the same) and every file now comes from servers you control. Serve
the pydiggs wheel yourself too (change the worker's install line to its URL; `?wheel=`
works only on localhost), and the CSP can drop every third-party origin.

## Content Security Policy

The page's `<meta>` policy allows only the page's own files and its worker:

```text
default-src 'none'; script-src 'self'; worker-src 'self'; style-src 'self';
base-uri 'none'; form-action 'none'
```

A worker does **not** inherit that policy: it takes the one sent in the HTTP headers of
the worker script. Send this with `worker.mjs`, as `serve.py` does:

```text
default-src 'none';
script-src 'self' blob: https://cdn.jsdelivr.net 'wasm-unsafe-eval';
connect-src 'self' https://cdn.jsdelivr.net https://pypi.org https://files.pythonhosted.org
```

`'wasm-unsafe-eval'` lets the worker compile WebAssembly; `blob:` is the integrity-checked
loader; `connect-src` covers Pyodide's files and packages and the pydiggs release. No
`'unsafe-eval'` and no `'unsafe-inline'` are needed. Self-hosted, replace both
third-party lists with `'self'`. If you can set headers, also send the page policy as a
header with `frame-ancestors 'none'` added (a `<meta>` policy cannot carry it).

## Limits

- **Size:** 25 MiB per document, refused before Python sees it and again by
  `validate_bytes(max_bytes=…)`.
- **One at a time:** Pyodide runs one thing at a time, so requests queue in arrival
  order. For parallel validation, start more workers (each loads its own Pyodide).
- **No hard timeout inside:** a long validation cannot be interrupted from Python. To
  stop one, `worker.terminate()` and start a new worker.
- **Memory:** a 32-bit WebAssembly module addresses at most 4 GiB, and browsers may
  allow less; a document needs several times its size in memory while it is checked.
- **Startup:** loading takes a few seconds on a fast connection, more on the first visit.

## Versions and what CI checks

The tested Pyodide is pinned exactly: npm `pyodide@314.0.7` in `tests/pyodide`
(CPython 3.14.2, lxml 6.1.3). On every change, CI builds the wheel, installs it into
that Pyodide under Node with `deps=False`, and checks that:

- every report and the combined findings document are byte-identical to CPython's;
- HTML in an element value comes back verbatim, as data;
- validation imports no `rich`, `multiprocessing`, `fastapi`, `mcp` or `subprocess` and
  starts no thread;
- the bundled UoM dictionary is read from the Emscripten filesystem;
- a document over 25 MiB is refused.

pydiggs's `lxml` floor is never above the lxml the tested Pyodide ships, and a test fails
if it ever is. A newer Pyodide is adopted only after the 14-day cooldown, together with
the example's version and integrity hash.
