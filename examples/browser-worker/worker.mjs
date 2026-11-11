// pydiggs in a module Web Worker. Loads Pyodide once, installs pydiggs with deps=False,
// warms it up, then answers validation requests:
//
//   page -> worker  { id, type: "validate" | "read", name, data: ArrayBuffer }   (transfer `data`)
//   worker -> page  { type: "ready", versions }                         once, after loading
//                   { type: "failed", message }                         loading failed
//                   { id, type: "result", document }                    a diggs-findings 1.0 document
//                   { id, type: "error", message }                      refused (too large, gzip, ...)
//
// Requests run one at a time in arrival order (Pyodide is single-threaded). The
// document stays in this worker: nothing here sends it anywhere.

// --- Pinned versions. A pydiggs release bumps PYDIGGS_VERSION (a test holds it to
// pydiggs.__version__); a Pyodide bump changes PYODIDE_VERSION and PYODIDE_SRI together
// with tests/pyodide. ----------------------------------------------------------------
const PYODIDE_VERSION = "314.0.7";
// sha384 of https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs, byte-identical to
// pyodide.mjs in npm pyodide@314.0.7.
const PYODIDE_SRI = "sha384-Lp79fMwxa4n2BLtugpUAoQMlbxIdg8iCUiP1r3h7nJLkQjwlXT1JzZTeHVL+f4Dg";
// The CDN, or a self-hosted copy of the Pyodide "full" distribution, for example
// new URL("./pyodide/", import.meta.url).href. The SRI check above applies either way.
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PYDIGGS_VERSION = "1.8.0";
const MAX_BYTES = 25 * 1024 * 1024; // brief D8: the browser example's limit

// While developing, ?wheel=/wheels/pydiggs-X.Y.Z-py3-none-any.whl on the page installs a
// same-origin wheel instead of the PyPI release (serve.py --wheel serves one there).
// Honoured on localhost only: on a deployed site that also serves uploads, a link could
// otherwise install any same-origin wheel and run its code next to the user's file.
const LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"];
function wheelOverride() {
  const value = new URL(self.location.href).searchParams.get("wheel");
  if (!value || !LOCAL_HOSTS.includes(self.location.hostname)) return null;
  const url = new URL(value, self.location.href);
  if (url.origin !== self.location.origin || !/\/pydiggs-[^/]+-py3-none-any\.whl$/.test(url.pathname)) {
    throw new Error("wheel= must be a same-origin pure pydiggs wheel");
  }
  return url.href;
}

// import() takes no integrity option and module workers have no import maps, so the
// loader is fetched with Subresource Integrity and imported from a blob: URL.
async function importPyodide() {
  const response = await fetch(`${PYODIDE_BASE}pyodide.mjs`, {
    integrity: PYODIDE_SRI,
    credentials: "omit",
    referrerPolicy: "no-referrer",
  });
  if (!response.ok) throw new Error(`pyodide.mjs: HTTP ${response.status}`);
  const blob = new Blob([await response.arrayBuffer()], { type: "text/javascript" });
  const url = URL.createObjectURL(blob);
  try {
    return await import(url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

const PYTHON_SETUP = `
import json
import pydiggs

pydiggs.warm_up()

def validate(data, name, max_bytes):
    try:
        report = pydiggs.validate_bytes(data.to_bytes(), name=name, max_bytes=max_bytes)
    except pydiggs.InstanceReadError as exc:
        return json.dumps({"error": str(exc)})
    return report.to_json(indent=None)

def read(data, name, max_bytes):
    # pydiggs.read_bytes is top-level from pydiggs 1.7.0. One JSON string goes back to
    # JavaScript (JSON.parse), never a proxy of the Python object graph.
    read_bytes = getattr(pydiggs, "read_bytes", None)
    if read_bytes is None:
        return json.dumps({"error": "this pydiggs has no read API (needs 1.7.0 or later)"})
    try:
        model = read_bytes(data.to_bytes(), name=name, results=False, max_bytes=max_bytes)
    except (pydiggs.InstanceReadError, ValueError) as exc:  # DocumentError is a ValueError
        return json.dumps({"error": str(exc)})
    return model.to_json()

versions = json.dumps({"pydiggs": pydiggs.__version__, "lxml": __import__("lxml").__version__})
`;

async function load() {
  const { loadPyodide } = await importPyodide();
  const pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });
  await pyodide.loadPackage(["lxml", "micropip"]);
  const micropip = pyodide.pyimport("micropip");
  await micropip.install.callKwargs(wheelOverride() ?? `pydiggs==${PYDIGGS_VERSION}`, {
    deps: false, // rich and the extras are not needed in the browser
  });
  pyodide.runPython(PYTHON_SETUP);
  return {
    pyodide,
    validate: pyodide.globals.get("validate"),
    read: pyodide.globals.get("read"),
  };
}

const loading = load();
loading.then(
  ({ pyodide }) => {
    const versions = JSON.parse(pyodide.globals.get("versions"));
    self.postMessage({ type: "ready", versions: { pyodide: pyodide.version, ...versions } });
  },
  (error) => self.postMessage({ type: "failed", message: String(error?.message ?? error) }),
);

let queue = Promise.resolve();
self.addEventListener("message", (event) => {
  const { id, type, name, data } = event.data ?? {};
  if ((type !== "validate" && type !== "read") || !(data instanceof ArrayBuffer)) return;
  queue = queue.then(async () => {
    try {
      if (data.byteLength > MAX_BYTES) {
        throw new Error(`the document is larger than ${MAX_BYTES} bytes`);
      }
      const loaded = await loading;
      const run = type === "read" ? loaded.read : loaded.validate;
      const text = run(new Uint8Array(data), String(name ?? "input.xml"), MAX_BYTES);
      const parsed = JSON.parse(text);
      if (parsed.error) {
        self.postMessage({ id, type: "error", message: parsed.error });
      } else {
        self.postMessage({ id, type: "result", document: parsed });
      }
    } catch (error) {
      self.postMessage({ id, type: "error", message: String(error?.message ?? error) });
    }
  });
});
