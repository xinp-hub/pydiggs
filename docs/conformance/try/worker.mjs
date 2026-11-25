// The conformance page's Web Worker: pyDIGGS in Pyodide, in the visitor's own tab.
//
// It loads Pyodide once (the loader checked against its SRI hash), installs the pinned
// pyDIGGS wheel (checked against its sha256) without its optional dependencies, imports
// conformance_page.py from this folder, then answers the page one request at a time:
//
//   page -> worker  { id, type: "catalogue" }
//                   { id, type: "make", classId, product, version, seed }
//                   { id, type: "reference", challenge: ArrayBuffer }
//                   { id, type: "grade", challenge, submission: ArrayBuffer, name }
//                   { id, type: "verify", evidence, challenge, submission, name }
//   worker -> page  { type: "progress", stage }     "runtime" | "packages" | "pydiggs"
//                   { type: "ready", versions }     once, after loading
//                   { type: "failed", message }     loading failed
//                   { id, type: "result", body }    the JSON reply of conformance_page.py
//                   { id, type: "error", message }
//
// The visitor's files stay in this worker. The only requests it makes are the GETs that
// load Pyodide, lxml, pyDIGGS and conformance_page.py, all before the first file.

// --- Pinned versions: the same as examples/browser-worker/worker.mjs and the npm
// package; tests/test_conformance_page.py holds them to tests/pyodide and PyPI. --------
const PYODIDE_VERSION = "314.0.7";
// sha384 of https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs.
const PYODIDE_SRI = "sha384-Lp79fMwxa4n2BLtugpUAoQMlbxIdg8iCUiP1r3h7nJLkQjwlXT1JzZTeHVL+f4Dg";
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PYDIGGS_VERSION = "1.18.0";
const PYDIGGS_WHEEL = {
  url: "https://files.pythonhosted.org/packages/06/b0/4b2ca62cfe7735e52bc6f25ff9f354b5206880f99332b210cd078d3822b5/pydiggs-1.18.0-py3-none-any.whl",
  sha256: "9a352a1180e38b2f7843b35e9819cce7ad335915db2daa73292ae7c42028b243",
};
const MAX_BYTES = 25 * 1024 * 1024; // conformance_page.MAX_BYTES

const post = (message) => self.postMessage(message);
const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");

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

async function download(url, sha256) {
  const response = await fetch(url, { credentials: "omit", referrerPolicy: "no-referrer" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  if (sha256 && hex(await crypto.subtle.digest("SHA-256", bytes)) !== sha256) {
    throw new Error("the pyDIGGS wheel does not match its pinned sha256; refusing to install it");
  }
  return new Uint8Array(bytes);
}

async function load() {
  // The wheel and the glue download while Pyodide loads.
  const wheel = download(PYDIGGS_WHEEL.url, PYDIGGS_WHEEL.sha256);
  const glue = download(new URL("./conformance_page.py", import.meta.url).href);
  wheel.catch(() => {});
  glue.catch(() => {});
  post({ type: "progress", stage: "runtime" });
  const { loadPyodide } = await importPyodide();
  const pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });
  post({ type: "progress", stage: "packages" });
  await pyodide.loadPackage(["lxml"], { messageCallback: () => {} });
  post({ type: "progress", stage: "pydiggs" });
  pyodide.unpackArchive(await wheel, "wheel");
  pyodide.FS.mkdirTree("/conformance-page");
  pyodide.FS.writeFile("/conformance-page/conformance_page.py", await glue);
  pyodide.runPython(`
import sys
sys.path.insert(0, "/conformance-page")
import pydiggs
pydiggs.warm_up()
import conformance_page
`);
  const page = pyodide.pyimport("conformance_page");
  const versions = {
    pyodide: pyodide.version,
    pydiggs: pyodide.runPython("pydiggs.__version__"),
    lxml: pyodide.runPython("__import__('lxml').__version__"),
  };
  if (versions.pydiggs !== PYDIGGS_VERSION) throw new Error(`installed pyDIGGS ${versions.pydiggs}, expected ${PYDIGGS_VERSION}`);
  post({ type: "ready", versions });
  return page;
}

const loading = load();
loading.catch((error) => post({ type: "failed", message: String(error?.message ?? error) }));

const bytes = (value, what) => {
  if (!(value instanceof ArrayBuffer)) throw new Error(`${what} is missing`);
  if (value.byteLength > MAX_BYTES) throw new Error(`${what} is larger than ${MAX_BYTES} bytes`);
  return new Uint8Array(value);
};
const text = (value) => (typeof value === "string" ? value : "");

function run(page, request) {
  switch (request.type) {
    case "catalogue":
      return page.catalogue();
    case "make":
      return page.make(text(request.classId), text(request.product), text(request.version), text(request.seed));
    case "reference":
      return page.reference(bytes(request.challenge, "the challenge"));
    case "grade":
      return page.grade(bytes(request.challenge, "the challenge"), bytes(request.submission, "the file"), text(request.name));
    case "verify":
      return page.verify(
        bytes(request.evidence, "the evidence"),
        bytes(request.challenge, "the challenge"),
        bytes(request.submission, "the file"),
        text(request.name),
      );
    default:
      throw new Error("unknown request");
  }
}

let queue = Promise.resolve();
self.addEventListener("message", (event) => {
  const request = event.data;
  if (typeof request !== "object" || request === null || typeof request.id !== "number") return;
  queue = queue.then(async () => {
    try {
      const page = await loading;
      post({ id: request.id, type: "result", body: JSON.parse(run(page, request)) });
    } catch (error) {
      post({ id: request.id, type: "error", message: String(error?.message ?? error) });
    }
  });
});
