// pydiggs in the browser: the document is validated or read in a Web Worker in the
// user's own tab, by the real pydiggs running on Pyodide (CPython on WebAssembly). The
// document never leaves the browser.
//
// Every string in a findings or model document comes from the validated file. Write
// it to the page with `textContent` or DOM nodes, never with `innerHTML`.

import { PydiggsError } from "./errors.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type { DiggsModelDocument } from "./generated/model.ts";
import { DEFAULT_MAX_BYTES } from "./generated/pins.ts";
import type { InitMessage, ReplyMessage, RequestMessage, RuntimeVersions } from "./protocol.ts";

export type { RuntimeVersions } from "./protocol.ts";
export { PydiggsError } from "./errors.ts";
export { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYODIDE_SRI, PYODIDE_VERSION } from "./generated/pins.ts";

/** The part of `Worker` this package uses; a `Worker` is one. */
export interface WorkerLike {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  terminate(): void;
  onmessage: ((event: MessageEvent) => unknown) | null;
  onerror: ((event: ErrorEvent) => unknown) | null;
}

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
}

export interface BrowserRequestOptions {
  /** File name to report. Default: the `File`'s own name, else `input.xml`. */
  name?: string;
  /**
   * Stops waiting for the answer. Pyodide cannot be interrupted, so the worker still
   * finishes the request; call `terminate()` to stop the work itself.
   */
  signal?: AbortSignal;
}

export interface BrowserReadOptions extends BrowserRequestOptions {
  /** Whether result tables keep their values. Default: true. */
  results?: boolean;
}

/** The worker failed to load, refused the document, or was terminated. */
export class PydiggsBrowserError extends PydiggsError {
  override name: string = "PydiggsBrowserError";
}

/** A DIGGS document held in the page. */
export type BrowserDocumentInput = Blob | ArrayBuffer | ArrayBufferView;

interface Pending {
  resolve: (document: unknown) => void;
  reject: (reason: unknown) => void;
}

function absoluteUrl(url: string | URL): string {
  if (url instanceof URL) return url.href;
  return typeof location === "undefined" ? url : new URL(url, location.href).href;
}

// The worker receives its own copy, so the caller's buffer is never detached.
async function copyBytes(input: BrowserDocumentInput): Promise<ArrayBuffer> {
  if (input instanceof Blob) return input.arrayBuffer();
  const copy = new ArrayBuffer(input.byteLength);
  new Uint8Array(copy).set(
    input instanceof ArrayBuffer
      ? new Uint8Array(input)
      : new Uint8Array(input.buffer, input.byteOffset, input.byteLength),
  );
  return copy;
}

function defaultName(input: BrowserDocumentInput): string {
  return "name" in input && typeof input.name === "string" && input.name !== "" ? input.name : "input.xml";
}

/**
 * One worker running pydiggs. Loading starts when it is constructed and takes a few
 * seconds; requests made meanwhile wait for it. Requests run one at a time.
 */
export class PydiggsBrowser {
  /** Settles once the worker has loaded Pyodide and pydiggs, or failed to. */
  readonly ready: Promise<RuntimeVersions>;

  readonly #worker: WorkerLike;
  readonly #maxBytes: number;
  readonly #pending: Map<number, Pending> = new Map();
  #nextId: number = 1;
  #failure: PydiggsBrowserError | undefined;

  constructor(options: PydiggsBrowserOptions = {}) {
    this.#maxBytes = Math.min(options.maxBytes ?? DEFAULT_MAX_BYTES, DEFAULT_MAX_BYTES);
    const create =
      options.createWorker ??
      ((): Worker => new Worker(new URL("./browser-worker.js", import.meta.url), { type: "module" }));
    this.#worker = create() as WorkerLike;

    let resolveReady!: (versions: RuntimeVersions) => void;
    let rejectReady!: (reason: unknown) => void;
    this.ready = new Promise<RuntimeVersions>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    // A caller who never awaits `ready` still learns of a failure from each request.
    this.ready.catch(() => {});

    this.#worker.onmessage = (event: MessageEvent): void => {
      const reply = event.data as ReplyMessage;
      switch (reply.type) {
        case "ready":
          resolveReady(reply.versions);
          break;
        case "failed":
          rejectReady(this.#fail(`pydiggs could not be loaded: ${reply.message}`));
          break;
        case "result":
          this.#settle(reply.id)?.resolve(reply.document);
          break;
        case "error":
          this.#settle(reply.id)?.reject(new PydiggsBrowserError(reply.message));
          break;
      }
    };
    this.#worker.onerror = (event: ErrorEvent): void => {
      rejectReady(this.#fail(`the pydiggs worker failed: ${event.message || "unknown error"}`));
    };

    const init: InitMessage = { type: "init", maxBytes: this.#maxBytes };
    if (options.pyodideBaseUrl !== undefined) {
      init.pyodideBaseUrl = absoluteUrl(options.pyodideBaseUrl).replace(/\/*$/, "/");
    }
    if (options.pydiggsWheelUrl !== undefined) {
      init.pydiggsWheelUrl = absoluteUrl(options.pydiggsWheelUrl);
    }
    this.#worker.postMessage(init);
  }

  /**
   * Validates a document. A malformed or invalid document is not an error: it comes
   * back as findings, with the verdict in `files[0].verdict`.
   */
  validate(input: BrowserDocumentInput, options: BrowserRequestOptions = {}): Promise<DiggsFindingsDocument> {
    return this.#request("validate", input, options) as Promise<DiggsFindingsDocument>;
  }

  /**
   * Reads a document into a diggs-model document: boreholes, samples, tests and their
   * result tables. Reading is not validating; call `validate` for a verdict.
   */
  read(input: BrowserDocumentInput, options: BrowserReadOptions = {}): Promise<DiggsModelDocument> {
    return this.#request("read", input, options, options.results ?? true) as Promise<DiggsModelDocument>;
  }

  /** Stops the worker at once. Requests in flight are rejected; make a new instance to go on. */
  terminate(): void {
    this.#fail("the pydiggs worker was terminated");
    this.#worker.terminate();
  }

  async #request(
    type: RequestMessage["type"],
    input: BrowserDocumentInput,
    options: BrowserRequestOptions,
    results?: boolean,
  ): Promise<unknown> {
    const signal = options.signal;
    signal?.throwIfAborted();
    if (this.#failure) throw this.#failure;
    const size = input instanceof Blob ? input.size : input.byteLength;
    if (size > this.#maxBytes) {
      throw new PydiggsBrowserError(`the document is larger than ${this.#maxBytes} bytes`);
    }
    const data = await copyBytes(input);
    signal?.throwIfAborted();
    if (this.#failure) throw this.#failure;

    const id = this.#nextId++;
    const message: RequestMessage = { id, type, name: options.name ?? defaultName(input), data };
    if (results !== undefined) message.results = results;
    return new Promise<unknown>((resolve, reject) => {
      const onAbort = (): void => {
        this.#pending.delete(id);
        reject(signal?.reason);
      };
      this.#pending.set(id, {
        resolve: (document) => {
          signal?.removeEventListener("abort", onAbort);
          resolve(document);
        },
        reject: (reason) => {
          signal?.removeEventListener("abort", onAbort);
          reject(reason);
        },
      });
      signal?.addEventListener("abort", onAbort, { once: true });
      this.#worker.postMessage(message, [data]);
    });
  }

  #settle(id: number): Pending | undefined {
    const pending = this.#pending.get(id);
    this.#pending.delete(id);
    return pending;
  }

  // Records the first failure, rejects everything in flight, and returns the failure.
  #fail(message: string): PydiggsBrowserError {
    this.#failure ??= new PydiggsBrowserError(message);
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const request of pending) request.reject(this.#failure);
    return this.#failure;
  }
}
