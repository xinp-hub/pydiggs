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
import { PydiggsWorkerError, WorkerClient, type WorkerLike } from "./worker-client.ts";

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
   * may go higher, up to `ABSOLUTE_MAX_BYTES`; pydiggs needs several times the
   * document in memory while it works, so size the server for it.
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
  /** Advanced: the worker file. Default: this package's `node-worker.js`. */
  workerUrl?: URL;
}

/** The worker failed to load, refused the document, or was terminated. */
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

/**
 * One worker thread running pydiggs. Loading starts when it is constructed; requests
 * made meanwhile wait for it. Requests run one at a time, so for more throughput run a
 * few instances. Call `terminate()` when done, or the thread keeps the process alive.
 */
export class PydiggsNode extends WorkerClient {
  constructor(options: PydiggsNodeOptions = {}) {
    const init: InitMessage = {
      type: "init",
      maxBytes: Math.min(options.maxBytes ?? DEFAULT_MAX_BYTES, ABSOLUTE_MAX_BYTES),
    };
    if (options.pyodideModule !== undefined) init.pyodideModuleUrl = fileUrl(options.pyodideModule);
    if (options.packageCacheDir !== undefined) init.packageCacheDir = options.packageCacheDir;
    if (options.pydiggsWheelPath !== undefined) init.pydiggsWheelPath = options.pydiggsWheelPath;
    const workerUrl = options.workerUrl ?? new URL("./node-worker.js", import.meta.url);
    super({
      worker: adapt(new Worker(workerUrl)),
      init,
      onProgress: options.onProgress,
      makeError: (message) => new PydiggsNodeError(message),
    });
  }
}
