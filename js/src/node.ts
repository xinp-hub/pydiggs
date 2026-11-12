// pydiggs in Node, with nothing Python to install: the real pydiggs on Pyodide, in a
// worker thread, from the npm `pyodide` package. Use it where a JavaScript server
// (Next.js route handlers on the Node runtime, Express, Fastify, Hono, NestJS, a
// serverless function) needs to validate or read DIGGS files, or behind
// `createPydiggsHandler` as the server side of `PydiggsHybrid`.
//
//     npm install @pydiggs/client pyodide@<PYODIDE_VERSION>
//
// Every string in a findings or model document comes from the validated file. Escape
// it before it reaches HTML, a terminal or a prompt.

import { isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { Worker } from "node:worker_threads";

import { DEFAULT_MAX_BYTES } from "./generated/pins.ts";
import type { InitMessage, LoadStage } from "./protocol.ts";
import { ABSOLUTE_MAX_BYTES } from "./worker-core.ts";
import {
  PydiggsWorkerError,
  SupervisedWorker,
  WorkerClient,
  type WorkerErrorCode,
  type WorkerLike,
} from "./worker-client.ts";

export type { DocumentBytes, WorkerReadOptions, WorkerRequestOptions, WorkerStatus } from "./worker-client.ts";
export type { LoadStage, RuntimeVersions } from "./protocol.ts";
export { PydiggsWorkerError } from "./worker-client.ts";
export { PydiggsError } from "./errors.ts";
export { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYODIDE_VERSION } from "./generated/pins.ts";
export { ABSOLUTE_MAX_BYTES } from "./worker-core.ts";
export { createPydiggsHandler, type PydiggsHandlerOptions } from "./handler.ts";

export interface PydiggsNodeOptions {
  /**
   * Largest document accepted, in bytes. Default: 25 MiB, as in the browser. A server
   * may go higher, up to `ABSOLUTE_MAX_BYTES` (256 MiB). Reading needs about 14 times
   * the document in memory (1.4 GiB at 100 MiB) and takes minutes at that size, so size
   * the server, and its timeouts, for the largest document you accept.
   */
  maxBytes?: number;
  /**
   * Directory where Pyodide keeps the packages it downloads on first start (lxml and
   * micropip). Set it to a persistent directory so a restart downloads nothing.
   */
  packageCacheDir?: string;
  /**
   * Path of a pure pydiggs wheel on disk (`pydiggs-X.Y.Z-py3-none-any.whl`), installed
   * with no download. Default: release `PYDIGGS_VERSION` from PyPI. With this and a
   * warm `packageCacheDir`, starting needs no network at all.
   */
  pydiggsWheelPath?: string;
  /**
   * Where `pyodide` is, when it cannot be found from this package (some monorepos): a
   * path or file URL of its `pyodide.mjs`. Default: the `pyodide` package.
   */
  pyodideModule?: string | URL;
  /** Called as loading moves on: "runtime", "packages", "pydiggs", "warm-up". */
  onProgress?: (stage: LoadStage) => void;
  /**
   * Longest a document may run, in milliseconds. Default: 60 000, as in pyDIGGS's own
   * server. Past it the worker thread is stopped and replaced, and the document fails
   * with code "timeout". `0` or `Infinity` turns the limit off.
   */
  timeoutMs?: number;
  /** Advanced: the worker file. Default: this package's `node-worker.js`. */
  workerUrl?: URL;
}

/** Why a request failed when the document itself was not the reason. */
export type PydiggsNodeErrorCode = WorkerErrorCode;

/**
 * The worker failed to load, refused the document, or was stopped. `code` is
 * "timeout" when the document ran past `timeoutMs`, "crashed" when the worker thread
 * died on it (out of memory, say); either way a fresh worker takes the next document.
 */
export class PydiggsNodeError extends PydiggsWorkerError {
  override name: string = "PydiggsNodeError";
}

function fileUrl(location: string | URL): string {
  if (location instanceof URL) return location.href;
  return isAbsolute(location) ? pathToFileURL(location).href : location;
}

// A worker thread speaks `on("message")`; the shared client expects a Web Worker's shape.
function adapt(thread: Worker): WorkerLike {
  const like: WorkerLike = {
    onmessage: null,
    onerror: null,
    postMessage: (message: unknown, transfer?: Transferable[]) =>
      thread.postMessage(message, transfer as unknown as Parameters<Worker["postMessage"]>[1]),
    terminate: () => thread.terminate(),
  };
  thread.on("message", (data: unknown) => like.onmessage?.({ data } as MessageEvent));
  thread.on("error", (error: Error) => like.onerror?.({ message: error.message } as ErrorEvent));
  thread.on("exit", (code: number) => {
    if (code !== 0) like.onerror?.({ message: `the worker thread exited with code ${code}` } as ErrorEvent);
  });
  return like;
}

/** pyDIGGS's own server stops a document after 60 s; so does this, by default. */
export const DEFAULT_TIMEOUT_MS: number = 60_000;

/**
 * pydiggs in a Node worker thread, with a supervisor around it. Loading starts when it
 * is constructed; documents made meanwhile wait. Documents run one at a time, in
 * arrival order, so for more throughput use a `PydiggsPool`.
 *
 * Pyodide cannot be interrupted, so a document that runs past `timeoutMs` stops its
 * worker thread: that document fails with code "timeout", and a fresh worker takes the
 * documents still waiting. A worker that dies on a document is replaced the same way.
 * A worker that fails to load is not retried: that is a setup problem, reported by
 * `ready` and by every request.
 *
 * Call `terminate()` when done, or the thread keeps the process alive.
 */
export class PydiggsNode extends SupervisedWorker {
  constructor(options: PydiggsNodeOptions = {}) {
    const init: InitMessage = {
      type: "init",
      maxBytes: Math.min(options.maxBytes ?? DEFAULT_MAX_BYTES, ABSOLUTE_MAX_BYTES),
    };
    if (options.pyodideModule !== undefined) init.pyodideModuleUrl = fileUrl(options.pyodideModule);
    if (options.packageCacheDir !== undefined) init.packageCacheDir = options.packageCacheDir;
    if (options.pydiggsWheelPath !== undefined) init.pydiggsWheelPath = options.pydiggsWheelPath;
    const timeout = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const workerUrl = options.workerUrl ?? new URL("./node-worker.js", import.meta.url);
    const makeError = (message: string, code?: WorkerErrorCode): PydiggsNodeError =>
      new PydiggsNodeError(message, code);
    super({
      label: "PydiggsNode",
      timeoutMs: Number.isFinite(timeout) && timeout > 0 ? timeout : Number.POSITIVE_INFINITY,
      makeError,
      start: () =>
        new WorkerClient({
          worker: adapt(new Worker(workerUrl)),
          init: { ...init },
          onProgress: options.onProgress,
          makeError: (message) => makeError(message),
        }),
    });
  }
}
