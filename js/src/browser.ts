// pydiggs in the browser: the document is validated or read in a Web Worker in the
// user's own tab, by the real pydiggs running on Pyodide (CPython on WebAssembly). The
// document never leaves the browser.
//
// Every string in a findings or model document comes from the validated file. Write
// it to the page with `textContent` or DOM nodes, never with `innerHTML`.

import { DEFAULT_MAX_BYTES } from "./generated/pins.ts";
import type { InitMessage, LoadStage } from "./protocol.ts";
import {
  type DocumentBytes,
  PydiggsWorkerError,
  SupervisedWorker,
  WorkerClient,
  type WorkerErrorCode,
  type WorkerLike,
  type WorkerReadOptions,
  type WorkerRequestOptions,
  type WorkerStatus,
} from "./worker-client.ts";

export type { LoadStage, RuntimeVersions } from "./protocol.ts";
export type { WorkerErrorCode, WorkerLike } from "./worker-client.ts";
export { PydiggsWorkerError } from "./worker-client.ts";
export { PydiggsError } from "./errors.ts";
export { defaultPoolSize, type PoolEngine, PydiggsPool, type PydiggsPoolOptions } from "./pool.ts";
export { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYODIDE_SRI, PYODIDE_VERSION } from "./generated/pins.ts";

/** Where the worker is: still loading, ready for documents, or out of service. */
export type BrowserStatus = WorkerStatus;
/** A DIGGS document held in the page. */
export type BrowserDocumentInput = DocumentBytes;
export type BrowserRequestOptions = WorkerRequestOptions;
export type BrowserReadOptions = WorkerReadOptions;

export interface PydiggsBrowserOptions {
  /**
   * Creates the worker. Default: a module worker from this package's own worker file,
   * found next to it with `new URL("./browser-worker.js", import.meta.url)`. Pass your
   * own when your bundler does not follow that pattern inside dependencies:
   *
   *     createWorker: () =>
   *       new Worker(new URL("@pydiggs/client/browser/worker", import.meta.url), { type: "module" })
   */
  createWorker?: () => Worker | WorkerLike;
  /**
   * Directory of a self-hosted Pyodide "full" distribution of the pinned version
   * (`PYODIDE_VERSION`). Default: the jsDelivr CDN. The loader is checked against
   * `PYODIDE_SRI` either way.
   */
  pyodideBaseUrl?: string | URL;
  /**
   * URL of a pure pydiggs wheel you host (`pydiggs-X.Y.Z-py3-none-any.whl`). Default:
   * release `PYDIGGS_VERSION` from PyPI. Only pass a URL you control: the wheel's
   * code runs next to the user's document.
   */
  pydiggsWheelUrl?: string | URL;
  /** Largest document accepted, in bytes. Default and upper bound: 25 MiB. */
  maxBytes?: number;
  /**
   * Called as loading moves on, so a page can say what it is waiting for. The first
   * visit spends most of its time in "runtime" (downloading Pyodide); a later visit is
   * served from the browser's cache.
   */
  onProgress?: (stage: LoadStage) => void;
  /**
   * Longest a document may run, in milliseconds, once the worker is loaded. Default:
   * 120 000. Past it the worker is stopped and replaced (the browser keeps Pyodide in
   * its cache, so the replacement loads in seconds), and the document fails with code
   * "timeout". `0` or `Infinity` turns the limit off.
   */
  timeoutMs?: number;
}

/**
 * The longest a document may run in the tab by default. The largest document the tab
 * accepts (25 MiB) took 15 s to validate and 39 s to read on a fast laptop
 * (js/README.md, Sizing); this leaves room for slower machines, and stops a tab that
 * would otherwise never answer.
 */
export const DEFAULT_BROWSER_TIMEOUT_MS: number = 120_000;

/**
 * The worker failed to load, refused the document, or was stopped. `code` is
 * "timeout" or "crashed" when the document was not the reason.
 */
export class PydiggsBrowserError extends PydiggsWorkerError {
  override name: string = "PydiggsBrowserError";
}

function absoluteUrl(url: string | URL): string {
  if (url instanceof URL) return url.href;
  return typeof location === "undefined" ? url : new URL(url, location.href).href;
}

/**
 * One Web Worker running pydiggs, with a supervisor around it. Loading starts when it
 * is constructed and takes a few seconds; documents sent meanwhile wait for it.
 * Documents run one at a time. A document that runs past `timeoutMs`, or kills its
 * worker, fails alone: a fresh worker takes the documents still waiting.
 */
export class PydiggsBrowser extends SupervisedWorker {
  constructor(options: PydiggsBrowserOptions = {}) {
    const create =
      options.createWorker ??
      ((): Worker => new Worker(new URL("./browser-worker.js", import.meta.url), { type: "module" }));
    const init: InitMessage = {
      type: "init",
      maxBytes: Math.min(options.maxBytes ?? DEFAULT_MAX_BYTES, DEFAULT_MAX_BYTES),
    };
    if (options.pyodideBaseUrl !== undefined) {
      init.pyodideBaseUrl = absoluteUrl(options.pyodideBaseUrl).replace(/\/*$/, "/");
    }
    if (options.pydiggsWheelUrl !== undefined) {
      init.pydiggsWheelUrl = absoluteUrl(options.pydiggsWheelUrl);
    }
    const timeout = options.timeoutMs ?? DEFAULT_BROWSER_TIMEOUT_MS;
    const makeError = (message: string, code?: WorkerErrorCode): PydiggsBrowserError =>
      new PydiggsBrowserError(message, code);
    super({
      label: "PydiggsBrowser",
      timeoutMs: Number.isFinite(timeout) && timeout > 0 ? timeout : Number.POSITIVE_INFINITY,
      makeError,
      start: () =>
        new WorkerClient({
          worker: create() as WorkerLike,
          init: { ...init },
          onProgress: options.onProgress,
          makeError: (message) => makeError(message),
        }),
    });
  }
}
