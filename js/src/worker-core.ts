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

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYDIGGS_WHEEL, PYODIDE_SRI } from "./generated/pins.ts";
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
  /** Advanced, for tests: the pinned wheel. Default: `PYDIGGS_WHEEL`. */
  pin?: { url: string; sha256: string } | null;
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

def _too_old(what, version="1.10.0"):
    return _problem("internal", f"this pydiggs is too old for {what} (needs {version} or later)")

_DIFF_OPTIONS = ("match", "rel_tol", "abs_tol", "max_changes", "show_reformatted", "show_renamed", "max_result_bytes")

# The limits are the adapter's own, so pydiggs's Request raises on a bad one instead of
# answering. The page side checks them first; this gives a hand-made message the same
# answer, in the same fixed texts.
def _validate_limits(p):
    limits, errors = {}, []
    found = p.get("max_findings")
    if found is not None:
        if type(found) is int and found >= 1:
            limits["max_findings"] = found
        else:
            errors.append(("max_findings", "must be a positive integer"))
    budget = p.get("time_budget")
    if budget is not None:
        if type(budget) in (int, float) and 0 < budget <= _svc.MAX_SECONDS:
            limits["time_budget"] = budget
        else:
            errors.append(("time_budget", f"must be a positive number of seconds, at most {_svc.MAX_SECONDS}"))
    return limits, errors

def run(kind, data, name, max_bytes, parameters, dictionary=None, new_data=None, new_name=None):
    if _svc is None:
        return _problem("internal", "this pydiggs is too old for @pydiggs/client (needs 1.7.0 or later)")
    p = json.loads(parameters) if parameters else {}
    try:
        if kind == "validate":
            limits, errors = _validate_limits(p)
            if errors:
                return _problem("invalid-parameter", "invalid parameters", errors)
            return _outcome(_svc.validate(_svc.Request(
                content=data.to_bytes(), name=name, checks=p.get("checks"),
                diggs_version=p.get("diggs_version"), format=p.get("format", "findings"),
                max_bytes=max_bytes, **limits)))
        if kind == "read":
            return _outcome(_svc.read(_svc.ReadRequest(
                content=data.to_bytes(), name=name, diggs_version=p.get("diggs_version"),
                select=p.get("select"), max_bytes=max_bytes)))
        if kind == "convert":
            if not hasattr(_svc, "convert"):
                return _too_old("convert")
            extra = {}
            if p.get("ags_version") is not None:
                if not hasattr(_svc, "CONVERT_AGS_VERSIONS"):
                    return _too_old("convert to AGS4", "1.11.0")
                extra["ags_version"] = p["ags_version"]
            return _outcome(_svc.convert(_svc.ConvertRequest(
                content=data.to_bytes(), name=name or None, to=p.get("to", "diggs"),
                diggs_version=p.get("diggs_version"), max_bytes=max_bytes, **extra)))
        if kind == "ags4-check":
            if not hasattr(_svc, "ags4_check"):
                return _too_old("checkAgs4")
            return _outcome(_svc.ags4_check(_svc.Ags4CheckRequest(
                content=data.to_bytes(), name=name or None,
                dictionary=dictionary.to_bytes() if dictionary is not None else None,
                format=p.get("format", "findings"), max_bytes=max_bytes)))
        if kind == "diff":
            if not hasattr(_svc, "diff"):
                return _too_old("diff", "1.13.0")
            return _outcome(_svc.diff(_svc.DiffRequest(
                old=data.to_bytes(), new=new_data.to_bytes(), old_name=name or None,
                new_name=new_name or None, max_bytes=max_bytes,
                **{k: p[k] for k in _DIFF_OPTIONS if k in p})))
        if kind == "tables":
            if not hasattr(_svc, "tables"):
                return _too_old("the tables profile", "1.17.0")
            return _outcome(_svc.tables(_svc.TablesRequest(
                content=data.to_bytes(), name=name or None,
                operation=p.get("operation", "to-diggs"), max_bytes=max_bytes,
                **{k: p[k] for k in ("max_result_bytes",) if k in p})))
        if kind == "tables-from-diggs":
            if not hasattr(_svc, "tables_from_diggs"):
                return _too_old("the tables profile", "1.17.0")
            return _outcome(_svc.tables_from_diggs(_svc.TablesFromDiggsRequest(
                content=data.to_bytes(), name=name or None, max_bytes=max_bytes,
                **{k: p[k] for k in ("max_result_bytes",) if k in p})))
        if kind == "capabilities":
            if not hasattr(_svc, "capabilities"):
                return _too_old("capabilities")
            answer = _svc.capabilities()
        elif kind == "rules":
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

// Whether the bytes have this sha256 (64 lower-case hex digits). Every digit is compared,
// so the time taken does not say where two digests first differ.
async function hasSha256(bytes: Uint8Array, sha256: string): Promise<boolean> {
  const actual = hex(await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>));
  if (actual.length !== sha256.length) return false;
  let different = 0;
  for (let i = 0; i < actual.length; i++) different |= actual.charCodeAt(i) ^ sha256.charCodeAt(i);
  return different === 0;
}

/**
 * Exported for tests: the sha256 the caller requires of the pydiggs wheel, in lower
 * case, or undefined when none was given. Anything that is not 64 hex digits is refused,
 * so a mistyped digest never reads as "no check".
 */
export function wheelSha256(init: InitMessage): string | undefined {
  const given: unknown = init.pydiggsWheelSha256;
  if (given === undefined) return undefined;
  if (typeof given !== "string" || !/^[0-9a-fA-F]{64}$/.test(given)) {
    throw new Error("pydiggsWheelSha256 must be a sha256 as 64 hex digits");
  }
  return given.toLowerCase();
}

// One hash of Subresource Integrity: sha256, sha384 or sha512, in base64 of that length.
const SRI_HASH = /^(?:sha256-[A-Za-z0-9+/]{43}=|sha384-[A-Za-z0-9+/]{64}|sha512-[A-Za-z0-9+/]{86}==)$/;

/**
 * Exported for tests: the Subresource Integrity value Pyodide's loader must match. It
 * is the pinned release's (`PYODIDE_SRI`), unless the page hosts its own Pyodide
 * (`pyodideBaseUrl`) and names that copy's hash in `pyodideSri`. A browser skips a hash
 * it cannot parse and, with none left, checks nothing; so a value that is not one or
 * more well-formed sha256, sha384 or sha512 hashes is refused here.
 */
export function pyodideIntegrity(init: InitMessage): string {
  const given: unknown = init.pyodideSri;
  if (given === undefined) return PYODIDE_SRI;
  if (init.pyodideBaseUrl === undefined) {
    throw new Error("pyodideSri is for a Pyodide you host: pass pyodideBaseUrl with it");
  }
  const hashes = typeof given === "string" ? given.trim().split(/\s+/) : [];
  if (hashes.length === 0 || !hashes.every((hash) => SRI_HASH.test(hash))) {
    throw new Error(
      "pyodideSri must be a Subresource Integrity value: sha256-, sha384- or sha512- and the hash in base64",
    );
  }
  return hashes.join(" ");
}

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
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (sha256 && !(await hasSha256(bytes, sha256))) {
    throw new Error("the pydiggs wheel does not match its pinned sha256; refusing to install it");
  }
  return bytes;
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
  const required = wheelSha256(init); // a malformed one is refused before any download too
  const stage = (name: LoadStage): void => options.post({ type: "progress", stage: name });
  const source = wheelSource(init, options.fetch ?? globalThis.fetch, options.pin === undefined ? PYDIGGS_WHEEL : options.pin);
  // Awaited below; until then a failed download must not count as unhandled.
  if ("bytes" in source) source.bytes.catch(() => {});
  // micropip downloads a release by version itself: its bytes never pass through here.
  if (required !== undefined && "spec" in source) {
    throw new Error(
      "pydiggsWheelSha256 needs a wheel to check: pass pydiggsWheelUrl (in Node, pydiggsWheelPath) with it",
    );
  }
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
    if (required !== undefined && !(await hasSha256(bytes, required))) {
      throw new Error("the pydiggs wheel does not match pydiggsWheelSha256; refusing to install it");
    }
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

const NEEDS_DOCUMENT = new Set(["validate", "read", "convert", "ags4-check", "diff", "tables", "tables-from-diggs"]);
const KINDS = new Set([
  "validate",
  "read",
  "rules",
  "explain",
  "versions",
  "convert",
  "ags4-check",
  "diff",
  "capabilities",
  "tables",
  "tables-from-diggs",
]);
// Kinds whose unnamed input pyDIGGS names itself: input.ags for AGS4, tables.zip for a
// package, input.xml for the DIGGS file a package is made from.
const OWN_DEFAULT_NAME = new Set(["convert", "ags4-check", "tables", "tables-from-diggs"]);

function answer(loaded: Loaded, request: RequestMessage): ReplyMessage {
  const { id, type } = request;
  // Each file of a comparison is held to the limit on its own, as on pyDIGGS's server.
  const sizes = [request.data, request.newData].filter((data) => data !== undefined).map((data) => data.byteLength);
  if (NEEDS_DOCUMENT.has(type) && Math.max(...sizes) > loaded.maxBytes) {
    const problem: ServiceProblem = { code: "input-too-large", detail: `larger than ${loaded.maxBytes} bytes` };
    return { id, type: "error", message: `the document is larger than ${loaded.maxBytes} bytes`, problem };
  }
  // An AGS4 file or a package without a name gets pyDIGGS's own default; a DIGGS one, input.xml.
  const ownDefault = OWN_DEFAULT_NAME.has(type);
  const name = typeof request.name === "string" && request.name !== "" ? request.name : ownDefault ? "" : "input.xml";
  const bytes = request.data ? new Uint8Array(request.data) : undefined;
  const dictionary = request.dictionary instanceof ArrayBuffer ? new Uint8Array(request.dictionary) : undefined;
  const parameters = request.parameters ? JSON.stringify(request.parameters) : "";
  const newBytes = request.newData instanceof ArrayBuffer ? new Uint8Array(request.newData) : undefined;
  const newName = typeof request.newName === "string" && request.newName !== "" ? request.newName : "input.xml";
  // A stop meant for an earlier request that finished first must not stop this one.
  if (loaded.interrupt) Atomics.store(loaded.interrupt, 0, 0);
  const result = loaded.run(type, bytes, name, loaded.maxBytes, parameters, dictionary, newBytes, newName) as string;
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
    if (typeof type !== "string" || !KINDS.has(type)) return;
    const request = message as RequestMessage;
    if (typeof request.id !== "number") return;
    if (NEEDS_DOCUMENT.has(type) && !(request.data instanceof ArrayBuffer)) return;
    if (type === "diff" && !(request.newData instanceof ArrayBuffer)) return;
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
