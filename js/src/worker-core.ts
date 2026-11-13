// Everything the worker does except fetching Pyodide: configuration, installing
// pydiggs, the Python glue, the request queue and the replies. It is separate from
// browser-worker.ts and node-worker.ts so that tests/pyodide/worker-smoke.mjs can run
// it under Node against the real, pinned Pyodide.
//
// Every request goes through pydiggs's own service layer (pydiggs._service), the one
// `pydiggs serve --http` and the stdio sidecar use: the same parameter checks with the
// same fixed texts, the same documents byte for byte, the same error codes, and its
// cache of recently read models, so asking again for some result tables of the same
// file does not read it again.

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYDIGGS_WHEEL } from "./generated/pins.ts";
import type {
  InitMessage,
  LoadStage,
  ReplyMessage,
  RequestMessage,
  RuntimeVersions,
  ServiceProblem,
} from "./protocol.ts";

/** A Python function as Pyodide hands it to JavaScript. */
export interface PyCallable {
  (...args: unknown[]): unknown;
  callKwargs(...args: unknown[]): Promise<unknown>;
}

/** The few parts of Pyodide's API the worker uses. */
export interface PyodideLike {
  version: string;
  loadPackage(names: string[], options?: { messageCallback?: (message: string) => void }): Promise<unknown>;
  pyimport(name: string): unknown;
  runPython(code: string): unknown;
  globals: { get(name: string): unknown };
  FS?: { mkdirTree(path: string): void; writeFile(path: string, data: Uint8Array): void; readFile(path: string): Uint8Array };
  unpackArchive(buffer: Uint8Array, format: string): void;
  setInterruptBuffer?(buffer: Uint8Array | Int32Array): void;
}

export interface WorkerCoreOptions {
  /** Sends a reply to the page. */
  post: (message: ReplyMessage) => void;
  /** Loads Pyodide for this configuration. Called once, on the "init" message. */
  loadPyodide: (init: InitMessage) => Promise<PyodideLike>;
  /** Advanced, for tests: the fetch used for the pinned wheel. */
  fetch?: typeof globalThis.fetch;
}

// One string goes back to JavaScript, never a proxy of the Python object graph: "D" and
// the document's JSON, as the service layer wrote it, or "E" and a problem as JSON.
// KeyboardInterrupt is how setInterruptBuffer stops Python; it is a BaseException, so
// the service layer's own `except Exception` lets it through to here.
const PYTHON_SETUP = `
import json
import pydiggs

try:
    from pydiggs import _service as _svc
except ImportError:  # pydiggs before 1.7
    _svc = None

pydiggs.warm_up()

def _problem(code, detail="", errors=(), kind=None):
    return "E" + json.dumps({"code": code, "detail": detail,
                             "errors": [{"parameter": p, "problem": q} for p, q in errors],
                             "kind": kind})

def _outcome(out):
    if out.ok:
        return "D" + out.document.decode("utf-8")
    return _problem(out.code.value, out.detail, out.errors, out.kind)

def run(kind, data, name, max_bytes, parameters):
    if _svc is None:
        return _problem("internal", "this pydiggs is too old for @pydiggs/client (needs 1.7.0 or later)")
    p = json.loads(parameters) if parameters else {}
    try:
        if kind == "validate":
            return _outcome(_svc.validate(_svc.Request(
                content=data.to_bytes(), name=name, checks=p.get("checks"),
                diggs_version=p.get("diggs_version"), format=p.get("format", "findings"),
                max_bytes=max_bytes)))
        if kind == "read":
            return _outcome(_svc.read(_svc.ReadRequest(
                content=data.to_bytes(), name=name, diggs_version=p.get("diggs_version"),
                select=p.get("select"), max_bytes=max_bytes)))
        if kind == "rules":
            answer = _svc.rules(p.get("step"))
        elif kind == "explain":
            answer = _svc.explain(p.get("rule_id"))
        elif kind == "versions":
            answer = {"api_version": "1", **_svc.versions()}
        else:
            return _problem("invalid-parameter", "unknown request")
        return _outcome(answer) if isinstance(answer, _svc.Outcome) else "D" + json.dumps(answer)
    except KeyboardInterrupt:
        return _problem("cancelled", "The request was cancelled.")

versions = json.dumps({"pydiggs": pydiggs.__version__, "lxml": __import__("lxml").__version__})
`;

interface Loaded {
  maxBytes: number;
  run: PyCallable;
  interrupt: Uint8Array | undefined;
}

/**
 * The largest document any engine accepts. Measured with tests/pyodide/memory-probe.mjs
 * (2026-11-12, Pyodide 314.0.7): reading needs about 14 times the document in
 * WebAssembly memory (1.4 GiB at 100 MiB), and 32-bit WebAssembly stops at 4 GiB, so
 * about 280 MiB is the most that could fit. This keeps a margin below it.
 */
export const ABSOLUTE_MAX_BYTES: number = 256 * 1024 * 1024;

const text = (error: unknown): string => (error instanceof Error ? error.message : String(error));
const hex = (buffer: ArrayBuffer): string =>
  [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");

function checkWheelUrl(wheel: string): void {
  if (!/\/pydiggs-[^/]+-py3-none-any\.whl$/.test(new URL(wheel).pathname)) {
    throw new Error("pydiggsWheelUrl must name a pure pydiggs wheel (pydiggs-X.Y.Z-py3-none-any.whl)");
  }
}

// Where the wheel comes from, decided before Pyodide loads so that a download overlaps
// with the runtime's own (on a first visit both are network-bound):
// - bytes: a wheel URL the page chose, or the pinned release checked against its sha256;
//   unpacked straight into site-packages, so micropip is never downloaded or imported;
// - a file in Pyodide's file system (emfs:, Node's pydiggsWheelPath), read once loaded;
// - otherwise the release by version, which needs micropip and PyPI's index.
export type WheelSource = { bytes: Promise<Uint8Array> } | { file: string } | { spec: string };

async function download(url: string, fetch: typeof globalThis.fetch, sha256?: string): Promise<Uint8Array> {
  const response = await fetch(url, sha256 ? { credentials: "omit", referrerPolicy: "no-referrer" } : {});
  if (!response.ok) throw new Error(`the pydiggs wheel: HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  if (sha256 && hex(await crypto.subtle.digest("SHA-256", bytes)) !== sha256) {
    throw new Error("the pydiggs wheel does not match its pinned sha256; refusing to install it");
  }
  return new Uint8Array(bytes);
}

/** Exported for tests: where the wheel comes from for this configuration and pin. */
export function wheelSource(
  init: InitMessage,
  fetch: typeof globalThis.fetch,
  pin: { url: string; sha256: string } | null = PYDIGGS_WHEEL,
): WheelSource {
  const url = init.pydiggsWheelUrl;
  if (url !== undefined) {
    return url.startsWith("emfs:") ? { file: url.slice("emfs:".length) } : { bytes: download(url, fetch) };
  }
  if (pin) return { bytes: download(pin.url, fetch, pin.sha256) };
  return { spec: `pydiggs==${PYDIGGS_VERSION}` };
}

async function load(init: InitMessage, options: WorkerCoreOptions): Promise<Loaded> {
  if (init.pydiggsWheelUrl !== undefined) checkWheelUrl(init.pydiggsWheelUrl); // before any download
  const stage = (name: LoadStage): void => options.post({ type: "progress", stage: name });
  const source = wheelSource(init, options.fetch ?? globalThis.fetch);
  // Awaited below; until then a failed download must not count as unhandled.
  if ("bytes" in source) source.bytes.catch(() => {});
  stage("runtime");
  const pyodide = await options.loadPyodide(init);
  stage("packages");
  // The page hears about loading through "progress"; Pyodide's own "Loading lxml" lines
  // would only clutter the console or a server's log.
  await pyodide.loadPackage("spec" in source ? ["lxml", "micropip"] : ["lxml"], { messageCallback: () => {} });
  stage("pydiggs");
  if ("spec" in source) {
    const micropip = pyodide.pyimport("micropip") as { install: PyCallable };
    // deps=False: rich and the extras are not needed in a worker.
    await micropip.install.callKwargs(source.spec, { deps: false });
  } else {
    const bytes = "bytes" in source ? await source.bytes : pyodide.FS!.readFile(source.file);
    pyodide.unpackArchive(bytes, "wheel");
  }
  stage("warm-up");
  pyodide.runPython(PYTHON_SETUP);
  let interrupt: Uint8Array | undefined;
  if (init.interruptBuffer && pyodide.setInterruptBuffer) {
    interrupt = new Uint8Array(init.interruptBuffer);
    pyodide.setInterruptBuffer(interrupt);
  }
  const versions = JSON.parse(pyodide.globals.get("versions") as string) as Omit<RuntimeVersions, "pyodide">;
  // The page side sets the limit for its platform (25 MiB at most in a browser). This
  // ceiling only keeps a document inside what 32-bit WebAssembly can address at all.
  const limit = Number.isFinite(init.maxBytes) && init.maxBytes > 0 ? init.maxBytes : DEFAULT_MAX_BYTES;
  const loaded: Loaded = {
    maxBytes: Math.min(limit, ABSOLUTE_MAX_BYTES),
    run: pyodide.globals.get("run") as PyCallable,
    interrupt,
  };
  options.post({ type: "ready", versions: { pyodide: pyodide.version, ...versions } });
  return loaded;
}

const NEEDS_DOCUMENT = new Set(["validate", "read"]);

function answer(loaded: Loaded, request: RequestMessage): ReplyMessage {
  const { id, type } = request;
  if (NEEDS_DOCUMENT.has(type) && request.data!.byteLength > loaded.maxBytes) {
    const problem: ServiceProblem = { code: "input-too-large", detail: `larger than ${loaded.maxBytes} bytes` };
    return { id, type: "error", message: `the document is larger than ${loaded.maxBytes} bytes`, problem };
  }
  const name = typeof request.name === "string" && request.name !== "" ? request.name : "input.xml";
  const bytes = request.data ? new Uint8Array(request.data) : undefined;
  const parameters = request.parameters ? JSON.stringify(request.parameters) : "";
  // A stop meant for an earlier request that finished first must not stop this one.
  if (loaded.interrupt) Atomics.store(loaded.interrupt, 0, 0);
  const result = loaded.run(type, bytes, name, loaded.maxBytes, parameters) as string;
  if (result.startsWith("D")) return { id, type: "result", document: JSON.parse(result.slice(1)) };
  const problem = JSON.parse(result.slice(1)) as ServiceProblem;
  return { id, type: "error", message: problem.detail || problem.code, problem };
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
    if (type !== "validate" && type !== "read" && type !== "rules" && type !== "explain" && type !== "versions") {
      return;
    }
    const request = message as RequestMessage;
    if (typeof request.id !== "number") return;
    if (NEEDS_DOCUMENT.has(type) && !(request.data instanceof ArrayBuffer)) return;
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
