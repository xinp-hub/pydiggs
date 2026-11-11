// Everything the worker does except fetching Pyodide: configuration, installing
// pydiggs, the Python glue, the request queue and the replies. It is separate from
// browser-worker.ts so that tests/pyodide/worker-smoke.mjs can run it under Node
// against the real, pinned Pyodide, which a Web Worker file cannot be.

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION } from "./generated/pins.ts";
import type { InitMessage, ReplyMessage, RequestMessage, RuntimeVersions } from "./protocol.ts";

/** A Python function as Pyodide hands it to JavaScript. */
export interface PyCallable {
  (...args: unknown[]): unknown;
  callKwargs(...args: unknown[]): Promise<unknown>;
}

/** The few parts of Pyodide's API the worker uses. */
export interface PyodideLike {
  version: string;
  loadPackage(names: string[]): Promise<unknown>;
  pyimport(name: string): unknown;
  runPython(code: string): unknown;
  globals: { get(name: string): unknown };
}

export interface WorkerCoreOptions {
  /** Sends a reply to the page. */
  post: (message: ReplyMessage) => void;
  /** Loads Pyodide for this configuration. Called once, on the "init" message. */
  loadPyodide: (init: InitMessage) => Promise<PyodideLike>;
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

const text = (error: unknown): string => (error instanceof Error ? error.message : String(error));

function wheelSpec(init: InitMessage): string {
  const wheel = init.pydiggsWheelUrl;
  if (wheel === undefined) return `pydiggs==${PYDIGGS_VERSION}`;
  if (!/\/pydiggs-[^/]+-py3-none-any\.whl$/.test(new URL(wheel).pathname)) {
    throw new Error("pydiggsWheelUrl must name a pure pydiggs wheel (pydiggs-X.Y.Z-py3-none-any.whl)");
  }
  return wheel;
}

async function load(init: InitMessage, options: WorkerCoreOptions): Promise<Loaded> {
  const spec = wheelSpec(init); // refuse a bad wheel URL before downloading anything
  const pyodide = await options.loadPyodide(init);
  await pyodide.loadPackage(["lxml", "micropip"]);
  const micropip = pyodide.pyimport("micropip") as { install: PyCallable };
  // deps=False: rich and the extras are not needed in the browser.
  await micropip.install.callKwargs(spec, { deps: false });
  pyodide.runPython(PYTHON_SETUP);
  const versions = JSON.parse(pyodide.globals.get("versions") as string) as Omit<RuntimeVersions, "pyodide">;
  const limit = Number.isFinite(init.maxBytes) && init.maxBytes > 0 ? init.maxBytes : DEFAULT_MAX_BYTES;
  const loaded: Loaded = {
    maxBytes: Math.min(limit, DEFAULT_MAX_BYTES),
    validate: pyodide.globals.get("validate") as PyCallable,
    read: pyodide.globals.get("read") as PyCallable,
  };
  options.post({ type: "ready", versions: { pyodide: pyodide.version, ...versions } });
  return loaded;
}

function answer(loaded: Loaded, request: RequestMessage): ReplyMessage {
  const { id, type, data } = request;
  if (data.byteLength > loaded.maxBytes) {
    return { id, type: "error", message: `the document is larger than ${loaded.maxBytes} bytes` };
  }
  const name = typeof request.name === "string" && request.name !== "" ? request.name : "input.xml";
  const bytes = new Uint8Array(data);
  const result =
    type === "read"
      ? loaded.read(bytes, name, loaded.maxBytes, request.results !== false)
      : loaded.validate(bytes, name, loaded.maxBytes);
  const parsed = JSON.parse(result as string) as { error?: unknown };
  return typeof parsed.error === "string"
    ? { id, type: "error", message: parsed.error }
    : { id, type: "result", document: parsed };
}

/**
 * Returns the worker's message handler. The first "init" message starts loading; the
 * requests that follow are answered one at a time, in arrival order, each with exactly
 * one reply. Anything that is not a well-formed message is ignored.
 */
export function createWorkerCore(options: WorkerCoreOptions): (message: unknown) => void {
  let loading: Promise<Loaded> | undefined;
  let queue: Promise<void> = Promise.resolve();

  return (message: unknown): void => {
    if (typeof message !== "object" || message === null) return;
    const { type } = message as { type?: unknown };
    if (type === "init") {
      if (loading) return; // the first configuration stands
      // Promise.resolve().then: a synchronous throw from load becomes a rejection too.
      loading = Promise.resolve().then(() => load(message as InitMessage, options));
      loading.catch((error: unknown) => options.post({ type: "failed", message: text(error) }));
      return;
    }
    if (type !== "validate" && type !== "read") return;
    const request = message as RequestMessage;
    if (typeof request.id !== "number" || !(request.data instanceof ArrayBuffer)) return;
    const ready = loading;
    queue = queue.then(async () => {
      try {
        if (!ready) throw new Error("the worker was not initialised");
        options.post(answer(await ready, request));
      } catch (error) {
        options.post({ id: request.id, type: "error", message: text(error) });
      }
    });
  };
}
