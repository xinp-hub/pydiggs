# pydiggs in the browser (Pyodide, module Web Worker)

A page that validates a DIGGS file in the browser tab. The file is read locally and
handed to a Web Worker running pydiggs in [Pyodide](https://pyodide.org/); it is never
uploaded. No build step and no dependencies beyond Pyodide itself.

```bash
python3 serve.py                  # http://127.0.0.1:8000/, pydiggs from PyPI
uv build --wheel && python3 serve.py --wheel ../../dist/pydiggs-<version>-py3-none-any.whl
```

| File | Role |
|---|---|
| `index.html` | the page, with a strict `<meta>` Content-Security-Policy |
| `app.mjs` | starts the worker, sends files, renders findings with `textContent` only |
| `worker.mjs` | loads Pyodide once (SRI-checked), installs pydiggs with `deps=False`, answers requests |
| `style.css` | minimal styles (a separate file, so the CSP needs no `'unsafe-inline'`) |
| `serve.py` | a localhost server that also sends the worker its own CSP header |

The worker protocol, the CDN-or-self-hosted choice, the CSP and the limits are described
in the [browser guide](https://xinp-hub.github.io/pydiggs/browser/). Every string in a
findings document is untrusted and must be escaped by whatever renders it.
