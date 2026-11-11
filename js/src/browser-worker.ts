// The Web Worker behind "@pydiggs/client/browser". It loads Pyodide once, installs
// pydiggs without its optional dependencies, warms it up, then answers requests one at
// a time (Pyodide is single-threaded). The document stays in this worker: nothing here
// sends it anywhere.
//
// It follows examples/browser-worker/worker.mjs in the pydiggs repository, which CI
// runs against the pinned Pyodide. The differences: the page sends the configuration
// in an "init" message, and `read` takes `results`.

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYODIDE_SRI, PYODIDE_VERSION } from "./generated/pins.ts";
import type { InitMessage, ReplyMessage, RequestMessage, RuntimeVersions } from "./protocol.ts";

// The few parts of Pyodide's API used here.
interface PyCallable {
  (...args: unknown[]): unknown;
  callKwargs(...args: unknown[]): Promise<unknown>;
}
interface Pyodide {
  version: string;
  loadPackage(names: string[]): Promise<unknown>;
  pyimport(name: string): { install: PyCallable };
  runPython(code: string): unknown;
  globals: { get(name: string): unknown };
}
interface PyodideModule {
  loadPyodide(options: { indexURL: string }): Promise<Pyodide>;
}

const post = (message: ReplyMessage): void => self.postMessage(message);

// import() takes no integrity option and module workers have no import maps, so the
// loader is fetched with Subresource Integrity and imported from a blob: URL.
async function importPyodide(base: string): Promise<PyodideModule> {
  const response = await fetch(`${base}pyodide.mjs`, {
    integrity: PYODIDE_SRI,
    credentials: "omit",
    referrerPolicy: "no-referrer",
  });
  if (!response.ok) throw new Error(`pyodide.mjs: HTTP ${response.status}`);
  const blob = new Blob([await response.arrayBuffer()], { type: "text/javascript" });
  const url = URL.createObjectURL(blob);
  try {
    return (await import(/* @vite-ignore */ /* webpackIgnore: true */ url)) as PyodideModule;
  } finally {
    URL.revokeObjectURL(url);
  }
}

// One JSON string goes back to JavaScript, never a proxy of the Python object graph.
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

def read(data, name, max_bytes, results):
    read_bytes = getattr(pydiggs, "read_bytes", None)
    if read_bytes is None:
        return json.dumps({"error": "this pydiggs has no read API (needs 1.7.0 or later)"})
    try:
        model = read_bytes(data.to_bytes(), name=name, results=bool(results), max_bytes=max_bytes)
    except (pydiggs.InstanceReadError, ValueError) as exc:  # DocumentError is a ValueError
        return json.dumps({"error": str(exc)})
    return model.to_json()

versions = json.dumps({"pydiggs": pydiggs.__version__, "lxml": __import__("lxml").__version__})
`;

interface Loaded {
  maxBytes: number;
  validate: PyCallable;
  read: PyCallable;
}

async function load(init: InitMessage): Promise<Loaded> {
  const base = init.pyodideBaseUrl ?? `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
  const wheel = init.pydiggsWheelUrl;
  if (wheel !== undefined && !/\/pydiggs-[^/]+-py3-none-any\.whl$/.test(new URL(wheel).pathname)) {
    throw new Error("pydiggsWheelUrl must name a pure pydiggs wheel (pydiggs-X.Y.Z-py3-none-any.whl)");
  }
  const { loadPyodide } = await importPyodide(base);
  const pyodide = await loadPyodide({ indexURL: base });
  await pyodide.loadPackage(["lxml", "micropip"]);
  // deps=False: rich and the extras are not needed in the browser.
  await pyodide.pyimport("micropip").install.callKwargs(wheel ?? `pydiggs==${PYDIGGS_VERSION}`, { deps: false });
  pyodide.runPython(PYTHON_SETUP);
  const versions = JSON.parse(pyodide.globals.get("versions") as string) as Omit<RuntimeVersions, "pyodide">;
  post({ type: "ready", versions: { pyodide: pyodide.version, ...versions } });
  const limit = Number.isFinite(init.maxBytes) && init.maxBytes > 0 ? init.maxBytes : DEFAULT_MAX_BYTES;
  return {
    maxBytes: Math.min(limit, DEFAULT_MAX_BYTES),
    validate: pyodide.globals.get("validate") as PyCallable,
    read: pyodide.globals.get("read") as PyCallable,
  };
}

let loading: Promise<Loaded> | undefined;
let queue: Promise<void> = Promise.resolve();

function answer(loaded: Loaded, request: RequestMessage): ReplyMessage {
  const { id, type, data } = request;
  if (data.byteLength > loaded.maxBytes) {
    return { id, type: "error", message: `the document is larger than ${loaded.maxBytes} bytes` };
  }
  const name = String(request.name ?? "input.xml");
  const bytes = new Uint8Array(data);
  const text =
    type === "read"
      ? loaded.read(bytes, name, loaded.maxBytes, request.results !== false)
      : loaded.validate(bytes, name, loaded.maxBytes);
  const parsed = JSON.parse(text as string) as { error?: string };
  return typeof parsed.error === "string"
    ? { id, type: "error", message: parsed.error }
    : { id, type: "result", document: parsed };
}

self.addEventListener("message", (event: MessageEvent): void => {
  const message = (event.data ?? {}) as Partial<InitMessage> | Partial<RequestMessage>;
  if (message.type === "init") {
    if (loading) return; // the first configuration stands
    loading = load(message as InitMessage);
    loading.catch((error: unknown) => {
      post({ type: "failed", message: error instanceof Error ? error.message : String(error) });
    });
    return;
  }
  if (message.type !== "validate" && message.type !== "read") return;
  const request = message as RequestMessage;
  if (typeof request.id !== "number" || !(request.data instanceof ArrayBuffer)) return;
  const ready = loading;
  queue = queue.then(async () => {
    try {
      if (!ready) throw new Error("the worker was not initialised");
      post(answer(await ready, request));
    } catch (error) {
      post({ id: request.id, type: "error", message: error instanceof Error ? error.message : String(error) });
    }
  });
});
