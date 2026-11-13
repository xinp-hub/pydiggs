// The page side of the pydiggs worker protocol, shared by PydiggsBrowser (a Web Worker)
// and PydiggsNode (a worker thread). It sends the configuration, tracks loading, queues
// requests by id, and turns replies into resolved or rejected promises.

import { PydiggsError } from "./errors.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type { DiggsModelDocument } from "./generated/model.ts";
import type { InitMessage, LoadStage, ReplyMessage, RequestMessage, RuntimeVersions } from "./protocol.ts";

/** The part of a worker this package uses: a Web `Worker`, or an adapted worker thread. */
export interface WorkerLike {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  terminate(): unknown;
  onmessage: ((event: MessageEvent) => unknown) | null;
  onerror: ((event: ErrorEvent) => unknown) | null;
}

/** Where the worker is: still loading, ready for documents, or out of service. */
export type WorkerStatus = "loading" | "ready" | "failed";

/** A DIGGS document held in memory. */
export type DocumentBytes = Blob | ArrayBuffer | ArrayBufferView;

export interface WorkerRequestOptions {
  /** File name to report. Default: the `File`'s own name, else `input.xml`. */
  name?: string;
  /**
   * Stops waiting for the answer. Pyodide cannot be interrupted, so the worker still
   * finishes the request; call `terminate()` to stop the work itself.
   */
  signal?: AbortSignal;
}

export interface WorkerReadOptions extends WorkerRequestOptions {
  /** Whether result tables keep their values. Default: true. */
  results?: boolean;
}

/** Why a request failed when the document itself was not the reason. */
export type WorkerErrorCode = "timeout" | "crashed";

/**
 * The worker failed to load, refused the document, or was stopped. `code` is
 * "timeout" when the document ran past the time limit, "crashed" when the worker died
 * on it (out of memory, say); either way a fresh worker takes the next document.
 */
export class PydiggsWorkerError extends PydiggsError {
  override name: string = "PydiggsWorkerError";
  readonly code: WorkerErrorCode | undefined;

  constructor(message: string, code?: WorkerErrorCode) {
    super(message);
    this.code = code;
  }
}

interface Pending {
  resolve: (document: unknown) => void;
  reject: (reason: unknown) => void;
}

// The worker receives its own copy, so the caller's buffer is never detached.
async function copyBytes(input: DocumentBytes): Promise<ArrayBuffer> {
  if (input instanceof Blob) return input.arrayBuffer();
  const copy = new ArrayBuffer(input.byteLength);
  new Uint8Array(copy).set(
    input instanceof ArrayBuffer
      ? new Uint8Array(input)
      : new Uint8Array(input.buffer, input.byteOffset, input.byteLength),
  );
  return copy;
}

export function documentName(input: DocumentBytes, name: string | undefined): string {
  if (name !== undefined) return name;
  return "name" in input && typeof input.name === "string" && input.name !== "" ? input.name : "input.xml";
}

export interface WorkerClientSetup {
  worker: WorkerLike;
  init: InitMessage;
  onProgress?: ((stage: LoadStage) => void) | undefined;
  makeError: (message: string) => PydiggsWorkerError;
}

export class WorkerClient {
  /** Settles once the worker has loaded Pyodide and pydiggs, or failed to. */
  readonly ready: Promise<RuntimeVersions>;

  #status: WorkerStatus = "loading";
  readonly #worker: WorkerLike;
  readonly #maxBytes: number;
  readonly #makeError: (message: string) => PydiggsWorkerError;
  readonly #pending: Map<number, Pending> = new Map();
  #nextId: number = 1;
  #failure: PydiggsWorkerError | undefined;

  constructor(setup: WorkerClientSetup) {
    this.#worker = setup.worker;
    this.#maxBytes = setup.init.maxBytes;
    this.#makeError = setup.makeError;

    let resolveReady!: (versions: RuntimeVersions) => void;
    let rejectReady!: (reason: unknown) => void;
    this.ready = new Promise<RuntimeVersions>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    // A caller who never awaits `ready` still learns of a failure from each request.
    this.ready.catch(() => {});

    const onProgress = setup.onProgress;
    this.#worker.onmessage = (event: MessageEvent): void => {
      const reply = event.data as ReplyMessage;
      switch (reply.type) {
        case "progress":
          if (this.#status === "loading") onProgress?.(reply.stage);
          break;
        case "ready":
          if (this.#status === "loading") this.#status = "ready";
          resolveReady(reply.versions);
          break;
        case "failed":
          rejectReady(this.#fail(`pydiggs could not be loaded: ${reply.message}`));
          break;
        case "result":
          this.#settle(reply.id)?.resolve(reply.document);
          break;
        case "error":
          this.#settle(reply.id)?.reject(this.#makeError(reply.message));
          break;
      }
    };
    this.#worker.onerror = (event: ErrorEvent): void => {
      rejectReady(this.#fail(`the pydiggs worker failed: ${event.message || "unknown error"}`));
    };
    this.#worker.postMessage(setup.init);
  }

  /**
   * "loading" until the worker has Pyodide and pydiggs, then "ready"; "failed" once
   * loading failed or the worker was terminated, after which every request is refused.
   */
  get status(): WorkerStatus {
    return this.#status;
  }

  /**
   * Validates a document. A malformed or invalid document is not an error: it comes
   * back as findings, with the verdict in `files[0].verdict`.
   */
  validate(input: DocumentBytes, options: WorkerRequestOptions = {}): Promise<DiggsFindingsDocument> {
    return this.#request("validate", input, options) as Promise<DiggsFindingsDocument>;
  }

  /**
   * Reads a document into a diggs-model document: boreholes, samples, tests and their
   * result tables. Reading is not validating; call `validate` for a verdict.
   */
  read(input: DocumentBytes, options: WorkerReadOptions = {}): Promise<DiggsModelDocument> {
    return this.#request("read", input, options, options.results ?? true) as Promise<DiggsModelDocument>;
  }

  /** Stops the worker at once. Requests in flight are rejected; make a new instance to go on. */
  terminate(): void {
    this.#fail("the pydiggs worker was terminated");
    void this.#worker.terminate();
  }

  /** How many requests are waiting for an answer. */
  get pending(): number {
    return this.#pending.size;
  }

  async #request(
    type: RequestMessage["type"],
    input: DocumentBytes,
    options: WorkerRequestOptions,
    results?: boolean,
  ): Promise<unknown> {
    const signal = options.signal;
    signal?.throwIfAborted();
    if (this.#failure) throw this.#failure;
    const size = input instanceof Blob ? input.size : input.byteLength;
    if (size > this.#maxBytes) {
      throw this.#makeError(`the document is larger than ${this.#maxBytes} bytes`);
    }
    const data = await copyBytes(input);
    signal?.throwIfAborted();
    if (this.#failure) throw this.#failure;

    const id = this.#nextId++;
    const message: RequestMessage = { id, type, name: documentName(input, options.name), data };
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
  #fail(message: string): PydiggsWorkerError {
    this.#status = "failed";
    this.#failure ??= this.#makeError(message);
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const request of pending) request.reject(this.#failure);
    return this.#failure;
  }
}

export interface SupervisedWorkerSetup {
  /** Starts a fresh worker: on construction, and again after a timeout or a crash. */
  start: () => WorkerClient;
  /** Longest a document may run once the worker is loaded; `Infinity` for no limit. */
  timeoutMs: number;
  makeError: (message: string, code?: WorkerErrorCode) => PydiggsWorkerError;
  /** Name used in the error a terminated instance gives, e.g. "PydiggsBrowser". */
  label: string;
}

/**
 * A worker with a supervisor around it, shared by PydiggsBrowser and PydiggsNode.
 * Documents run one at a time, in arrival order (Pyodide is single-threaded anyway).
 * Pyodide cannot be interrupted, so a document that runs past `timeoutMs` stops its
 * worker: that document fails with code "timeout", and a fresh worker takes the
 * documents still waiting. A worker that dies on a document is replaced the same way
 * (code "crashed"). A worker that fails to load is not retried: that is a setup problem,
 * reported by `ready` and by every request. Loading never counts against the limit.
 */
export class SupervisedWorker {
  readonly #setup: SupervisedWorkerSetup;
  readonly #loaded: WeakSet<WorkerClient> = new WeakSet();
  #client: WorkerClient;
  #queue: Promise<void> = Promise.resolve();
  #pending: number = 0;
  #closed: PydiggsWorkerError | undefined;

  constructor(setup: SupervisedWorkerSetup) {
    this.#setup = setup;
    this.#client = this.#start();
  }

  /** Settles once the current worker has loaded Pyodide and pydiggs, or failed to. */
  get ready(): Promise<RuntimeVersions> {
    return this.#client.ready;
  }

  /** "loading" (also while a replacement worker starts), "ready", or "failed". */
  get status(): WorkerStatus {
    return this.#closed ? "failed" : this.#client.status;
  }

  /** Documents waiting or running. */
  get pending(): number {
    return this.#pending;
  }

  /**
   * Validates a document. A malformed or invalid document is not an error: it comes
   * back as findings, with the verdict in `files[0].verdict`.
   */
  validate(input: DocumentBytes, options: WorkerRequestOptions = {}): Promise<DiggsFindingsDocument> {
    return this.#run(options.signal, (client) => client.validate(input, options));
  }

  /**
   * Reads a document into a diggs-model document: boreholes, samples, tests and their
   * result tables. Reading is not validating; call `validate` for a verdict.
   */
  read(input: DocumentBytes, options: WorkerReadOptions = {}): Promise<DiggsModelDocument> {
    return this.#run(options.signal, (client) => client.read(input, options));
  }

  /** Stops the worker. Documents waiting or running are rejected; nothing runs after it. */
  terminate(): void {
    this.#closed ??= this.#setup.makeError(`${this.#setup.label} was terminated`);
    this.#client.terminate();
  }

  #start(): WorkerClient {
    const client = this.#setup.start();
    client.ready.then(
      () => this.#loaded.add(client),
      () => {},
    );
    return client;
  }

  // Replaces a worker that timed out or died. One that never loaded stays failed.
  #restart(client: WorkerClient): void {
    if (this.#closed || client !== this.#client || !this.#loaded.has(client)) return;
    client.terminate();
    this.#client = this.#start();
  }

  #run<T>(signal: AbortSignal | undefined, work: (client: WorkerClient) => Promise<T>): Promise<T> {
    this.#pending++;
    const turn = this.#queue.then(() => this.#attempt(signal, work));
    this.#queue = turn.then(
      () => {},
      () => {},
    );
    return turn.finally(() => {
      this.#pending--;
    });
  }

  async #attempt<T>(signal: AbortSignal | undefined, work: (client: WorkerClient) => Promise<T>): Promise<T> {
    if (this.#closed) throw this.#closed;
    signal?.throwIfAborted();
    const client = this.#client;
    const limit = this.#setup.timeoutMs;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let settled = false;
    const timedOut = new Promise<never>((_, reject) => {
      if (!Number.isFinite(limit)) return;
      const arm = (): void => {
        if (settled) return;
        timer = setTimeout(() => {
          reject(this.#setup.makeError(`the document took longer than ${limit} ms`, "timeout"));
          this.#restart(client);
        }, limit);
      };
      // The clock starts when the worker is loaded: loading is not the document's time.
      if (client.status === "ready") arm();
      else client.ready.then(arm, () => {});
    });
    try {
      return await Promise.race([work(client), timedOut]);
    } catch (error) {
      if (error instanceof PydiggsWorkerError && error.code === "timeout") throw error;
      if (this.#closed) throw this.#closed;
      if (client.status === "failed" && client === this.#client && this.#loaded.has(client)) {
        this.#restart(client);
        throw this.#setup.makeError(error instanceof Error ? error.message : String(error), "crashed");
      }
      throw error;
    } finally {
      settled = true;
      clearTimeout(timer);
    }
  }
}
