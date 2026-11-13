// The page side of the pydiggs worker protocol, shared by PydiggsBrowser (a Web Worker)
// and PydiggsNode (a worker thread). It sends the configuration, tracks loading, queues
// requests by id, and turns replies into resolved or rejected promises.

import { PydiggsError } from "./errors.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type { DiggsVersion, RuleInfo, RuleStep, SarifLog, ValidateCheck, Versions } from "./generated/http.ts";
import type { DiggsModelDocument } from "./generated/model.ts";
import type {
  InitMessage,
  LoadStage,
  ReplyMessage,
  RequestKind,
  RequestMessage,
  RequestParameters,
  RuntimeVersions,
  ServiceProblem,
} from "./protocol.ts";

export type { DiggsVersion, RuleInfo, RuleStep, SarifLog, ValidateCheck, Versions } from "./generated/http.ts";

/** The part of a worker this package uses: a Web `Worker`, or an adapted worker thread. */
export interface WorkerLike {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  terminate(): unknown;
  onmessage: ((event: MessageEvent) => unknown) | null;
  onerror: ((event: ErrorEvent) => unknown) | null;
}

/**
 * Where the worker is: still loading, ready for documents, stopped after `idleTimeoutMs`
 * with nothing to do (the next request starts it again), or out of service.
 */
export type WorkerStatus = "loading" | "ready" | "idle" | "failed";

/** A DIGGS document held in memory. */
export type DocumentBytes = Blob | ArrayBuffer | ArrayBufferView;

export interface WorkerCallOptions {
  /**
   * Stops waiting for the answer. Where the worker can be interrupted (Node, and pages
   * that are cross-origin isolated) the work itself stops too; elsewhere the worker
   * finishes it before taking the next request.
   */
  signal?: AbortSignal;
}

export interface WorkerRequestOptions extends WorkerCallOptions {
  /** File name to report. Default: the `File`'s own name, else `input.xml`. */
  name?: string;
}

export interface WorkerValidateOptions extends WorkerRequestOptions {
  /** Steps to run. Default: all. */
  checks?: readonly ValidateCheck[];
  /** DIGGS version to check against. Default: detected. */
  diggsVersion?: DiggsVersion;
}

export interface WorkerReadOptions extends WorkerRequestOptions {
  /** DIGGS version to read as. Default: detected. */
  diggsVersion?: DiggsVersion;
  /**
   * Which result tables keep their values: `"all"` (the default), `"none"`, or the
   * keys of the nodes whose tables you want (at most 1,000). A table that is left out
   * keeps its columns and `row_count`. `true` and `false` mean "all" and "none".
   * Reading the same file again for other tables is fast: pydiggs keeps the last few
   * files it read.
   */
  results?: boolean | "all" | "none" | readonly string[];
}

export interface WorkerRulesOptions extends WorkerCallOptions {
  /** Only the rules of this step. */
  step?: RuleStep;
}

/**
 * Why a request failed when the document itself was not the reason: "timeout" (it ran
 * past the time limit), "crashed" (the worker died on it, out of memory say). Either
 * way the next request runs.
 */
export type WorkerErrorCode = "timeout" | "crashed";

/**
 * The worker failed to load, refused the request, or was stopped. `code` is "timeout"
 * or "crashed" when the document was not the reason. `problem` is pydiggs's own answer
 * when it refused the request, with the codes and fixed texts of its HTTP API:
 * `invalid-document` with a `kind` for bytes that are not DIGGS, `invalid-parameter`
 * with `errors`, `input-too-large`, and so on.
 */
export class PydiggsWorkerError extends PydiggsError {
  override name: string = "PydiggsWorkerError";
  readonly code: WorkerErrorCode | undefined;
  readonly problem: ServiceProblem | undefined;

  constructor(message: string, code?: WorkerErrorCode, problem?: ServiceProblem) {
    super(message);
    this.code = code;
    this.problem = problem;
  }
}

export type { ServiceProblem } from "./protocol.ts";

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

/** The `select` pydiggs's service layer takes, from a `results` option. */
export function selectResults(results: WorkerReadOptions["results"]): RequestParameters["select"] | undefined {
  if (results === undefined || results === true || results === "all") return undefined;
  if (results === false || results === "none") return { results: "none" };
  return { results: [...results] };
}

/**
 * Whether this platform can share memory with a worker, which interrupting Python
 * needs: always in Node, and in a page only when it is cross-origin isolated (served
 * with `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy:
 * require-corp` or `credentialless`).
 */
export function canInterrupt(): boolean {
  if (typeof SharedArrayBuffer === "undefined") return false;
  const isolated = (globalThis as { crossOriginIsolated?: boolean }).crossOriginIsolated;
  return isolated !== false;
}

export interface WorkerClientSetup {
  worker: WorkerLike;
  init: InitMessage;
  onProgress?: ((stage: LoadStage) => void) | undefined;
  makeError: (message: string, problem?: ServiceProblem) => PydiggsWorkerError;
  /** Give the worker an interrupt buffer when the platform allows it. Default: true. */
  interruptible?: boolean;
}

export class WorkerClient {
  /** Settles once the worker has loaded Pyodide and pydiggs, or failed to. */
  readonly ready: Promise<RuntimeVersions>;
  /** True when `interrupt()` can stop the Python that is running. */
  readonly interruptible: boolean;

  #status: "loading" | "ready" | "failed" = "loading";
  readonly #worker: WorkerLike;
  readonly #maxBytes: number;
  readonly #makeError: (message: string, problem?: ServiceProblem) => PydiggsWorkerError;
  readonly #pending: Map<number, Pending> = new Map();
  readonly #interrupt: Uint8Array | undefined;
  #nextId: number = 1;
  #failure: PydiggsWorkerError | undefined;

  constructor(setup: WorkerClientSetup) {
    this.#worker = setup.worker;
    this.#maxBytes = setup.init.maxBytes;
    this.#makeError = setup.makeError;
    const init: InitMessage = { ...setup.init };
    if ((setup.interruptible ?? true) && canInterrupt()) {
      init.interruptBuffer = new SharedArrayBuffer(1);
      this.#interrupt = new Uint8Array(init.interruptBuffer);
    }
    this.interruptible = this.#interrupt !== undefined;

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
          this.#settle(reply.id)?.reject(this.#makeError(reply.message, reply.problem));
          break;
      }
    };
    this.#worker.onerror = (event: ErrorEvent): void => {
      rejectReady(this.#fail(`the pydiggs worker failed: ${event.message || "unknown error"}`));
    };
    this.#worker.postMessage(init);
  }

  /**
   * "loading" until the worker has Pyodide and pydiggs, then "ready"; "failed" once
   * loading failed or the worker was terminated, after which every request is refused.
   */
  get status(): "loading" | "ready" | "failed" {
    return this.#status;
  }

  /** How many requests are waiting for an answer. */
  get pending(): number {
    return this.#pending.size;
  }

  /**
   * Validates a document. A malformed or invalid document is not an error: it comes
   * back as findings, with the verdict in `files[0].verdict`.
   */
  validate(input: DocumentBytes, options: WorkerValidateOptions = {}): Promise<DiggsFindingsDocument> {
    return this.#document("validate", input, options, validateParameters(options)) as Promise<DiggsFindingsDocument>;
  }

  /** Validates a document and returns the findings as a SARIF 2.1.0 log. */
  validateSarif(input: DocumentBytes, options: WorkerValidateOptions = {}): Promise<SarifLog> {
    const parameters: RequestParameters = { ...validateParameters(options), format: "sarif" };
    return this.#document("validate", input, options, parameters) as Promise<SarifLog>;
  }

  /**
   * Reads a document into a diggs-model document: boreholes, samples, tests and their
   * result tables. Reading is not validating; call `validate` for a verdict.
   */
  read(input: DocumentBytes, options: WorkerReadOptions = {}): Promise<DiggsModelDocument> {
    const parameters: RequestParameters = {};
    if (options.diggsVersion !== undefined) parameters.diggs_version = options.diggsVersion;
    const select = selectResults(options.results);
    if (select) parameters.select = select;
    return this.#document("read", input, options, parameters) as Promise<DiggsModelDocument>;
  }

  /** pydiggs's version, its findings schema and reference-data commits, as `/v1/versions` gives them. */
  versions(options: WorkerCallOptions = {}): Promise<Versions> {
    return this.#send("versions", undefined, undefined, {}, options.signal) as Promise<Versions>;
  }

  /** Every rule id pydiggs can emit; open families come as patterns. */
  listRules(options: WorkerRulesOptions = {}): Promise<RuleInfo[]> {
    const parameters: RequestParameters = options.step === undefined ? {} : { step: options.step };
    return this.#send("rules", undefined, undefined, parameters, options.signal) as Promise<RuleInfo[]>;
  }

  /** One rule. A well-formed id pydiggs does not emit comes back with `known: false`. */
  explainRule(ruleId: string, options: WorkerCallOptions = {}): Promise<RuleInfo> {
    return this.#send("explain", undefined, undefined, { rule_id: ruleId }, options.signal) as Promise<RuleInfo>;
  }

  /**
   * Asks the worker to stop the Python that is running: it answers that request with
   * code "cancelled" as soon as Python next checks (lxml's own C code does not check,
   * so a long schema validation finishes its step first). Does nothing where the
   * worker cannot be interrupted.
   */
  interrupt(): void {
    if (this.#interrupt) Atomics.store(this.#interrupt, 0, 2); // 2 is SIGINT
  }

  /** Stops the worker at once. Requests in flight are rejected; make a new instance to go on. */
  terminate(): void {
    this.#fail("the pydiggs worker was terminated");
    void this.#worker.terminate();
  }

  async #document(
    type: RequestKind,
    input: DocumentBytes,
    options: WorkerRequestOptions,
    parameters: RequestParameters,
  ): Promise<unknown> {
    options.signal?.throwIfAborted();
    if (this.#failure) throw this.#failure;
    const size = input instanceof Blob ? input.size : input.byteLength;
    if (size > this.#maxBytes) {
      throw this.#makeError(`the document is larger than ${this.#maxBytes} bytes`, {
        code: "input-too-large",
        detail: `larger than ${this.#maxBytes} bytes`,
      });
    }
    const data = await copyBytes(input);
    return this.#send(type, documentName(input, options.name), data, parameters, options.signal);
  }

  #send(
    type: RequestKind,
    name: string | undefined,
    data: ArrayBuffer | undefined,
    parameters: RequestParameters,
    signal: AbortSignal | undefined,
  ): Promise<unknown> {
    try {
      signal?.throwIfAborted();
    } catch (error) {
      return Promise.reject(error);
    }
    if (this.#failure) return Promise.reject(this.#failure);
    const id = this.#nextId++;
    const message: RequestMessage = { id, type };
    if (name !== undefined) message.name = name;
    if (data !== undefined) message.data = data;
    if (Object.keys(parameters).length > 0) message.parameters = parameters;
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
      this.#worker.postMessage(message, data ? [data] : []);
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

function validateParameters(options: WorkerValidateOptions): RequestParameters {
  const parameters: RequestParameters = {};
  if (options.checks !== undefined) parameters.checks = [...options.checks];
  if (options.diggsVersion !== undefined) parameters.diggs_version = options.diggsVersion;
  return parameters;
}

export interface SupervisedWorkerSetup {
  /** Starts a fresh worker: on construction, after a timeout or a crash, and after idling. */
  start: () => WorkerClient;
  /** Longest a request may run once the worker is loaded; `Infinity` for no limit. */
  timeoutMs: number;
  /**
   * After a timeout, how long to wait for an interrupted worker to answer before
   * replacing it. Default: 2 000.
   */
  interruptGraceMs?: number;
  /**
   * Stop the worker after this long with nothing to do, to give its memory back; the
   * next request starts it again (from the cache: a few seconds). `Infinity` (the
   * default) keeps it running.
   */
  idleTimeoutMs?: number;
  makeError: (message: string, code?: WorkerErrorCode) => PydiggsWorkerError;
  /** Name used in the error a terminated instance gives, e.g. "PydiggsBrowser". */
  label: string;
}

/** How long an interrupted worker has to answer before it is replaced. */
export const DEFAULT_INTERRUPT_GRACE_MS: number = 2_000;

const positive = (value: number | undefined, fallback: number): number =>
  value !== undefined && Number.isFinite(value) && value > 0 ? value : fallback;

/**
 * A worker with a supervisor around it, shared by PydiggsBrowser and PydiggsNode.
 * Requests run one at a time, in arrival order (Pyodide is single-threaded anyway).
 *
 * A request that runs past `timeoutMs` fails with code "timeout". Where the worker can
 * be interrupted, the supervisor first asks it to stop and keeps the worker if it does
 * within `interruptGraceMs`; otherwise, or where it cannot be interrupted, the worker
 * is replaced and a fresh one takes the requests still waiting. A worker that dies on a
 * request is replaced the same way (code "crashed"). A worker that fails to load is not
 * retried: that is a setup problem, reported by `ready` and by every request. Loading
 * never counts against the limit.
 */
export class SupervisedWorker {
  readonly #setup: SupervisedWorkerSetup;
  readonly #loaded: WeakSet<WorkerClient> = new WeakSet();
  #client: WorkerClient;
  #queue: Promise<void> = Promise.resolve();
  #pending: number = 0;
  #restarts: number = 0;
  #idle: boolean = false;
  #idleTimer: ReturnType<typeof setTimeout> | undefined;
  #closed: PydiggsWorkerError | undefined;

  constructor(setup: SupervisedWorkerSetup) {
    this.#setup = setup;
    this.#client = this.#start();
  }

  /** Settles once the current worker has loaded Pyodide and pydiggs, or failed to. */
  get ready(): Promise<RuntimeVersions> {
    return this.#client.ready;
  }

  /** "loading" (also while a replacement worker starts), "ready", "idle", or "failed". */
  get status(): WorkerStatus {
    if (this.#closed) return "failed";
    return this.#idle ? "idle" : this.#client.status;
  }

  /** Requests waiting or running. */
  get pending(): number {
    return this.#pending;
  }

  /** How many times a worker was replaced after a timeout or a crash. */
  get restarts(): number {
    return this.#restarts;
  }

  /** True when a request that runs too long can be stopped without replacing the worker. */
  get interruptible(): boolean {
    return this.#client.interruptible;
  }

  /**
   * Validates a document. A malformed or invalid document is not an error: it comes
   * back as findings, with the verdict in `files[0].verdict`.
   */
  validate(input: DocumentBytes, options: WorkerValidateOptions = {}): Promise<DiggsFindingsDocument> {
    return this.#run(options.signal, (client) => client.validate(input, options));
  }

  /** Validates a document and returns the findings as a SARIF 2.1.0 log. */
  validateSarif(input: DocumentBytes, options: WorkerValidateOptions = {}): Promise<SarifLog> {
    return this.#run(options.signal, (client) => client.validateSarif(input, options));
  }

  /**
   * Reads a document into a diggs-model document: boreholes, samples, tests and their
   * result tables. Reading is not validating; call `validate` for a verdict. Bytes that
   * are not a DIGGS document are an error with `problem.code` "invalid-document".
   */
  read(input: DocumentBytes, options: WorkerReadOptions = {}): Promise<DiggsModelDocument> {
    return this.#run(options.signal, (client) => client.read(input, options));
  }

  /** pydiggs's version, its findings schema and reference-data commits. */
  versions(options: WorkerCallOptions = {}): Promise<Versions> {
    return this.#run(options.signal, (client) => client.versions(options));
  }

  /** Every rule id pydiggs can emit; open families come as patterns. */
  listRules(options: WorkerRulesOptions = {}): Promise<RuleInfo[]> {
    return this.#run(options.signal, (client) => client.listRules(options));
  }

  /** One rule. A well-formed id pydiggs does not emit comes back with `known: false`. */
  explainRule(ruleId: string, options: WorkerCallOptions = {}): Promise<RuleInfo> {
    return this.#run(options.signal, (client) => client.explainRule(ruleId, options));
  }

  /** Stops the worker. Requests waiting or running are rejected; nothing runs after it. */
  terminate(): void {
    this.#closed ??= this.#setup.makeError(`${this.#setup.label} was terminated`);
    clearTimeout(this.#idleTimer);
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
    this.#restarts++;
    this.#client = this.#start();
  }

  #run<T>(signal: AbortSignal | undefined, work: (client: WorkerClient) => Promise<T>): Promise<T> {
    this.#pending++;
    clearTimeout(this.#idleTimer);
    const turn = this.#queue.then(() => this.#attempt(signal, work));
    this.#queue = turn.then(
      () => {},
      () => {},
    );
    return turn.finally(() => {
      this.#pending--;
      if (this.#pending === 0) this.#armIdle();
    });
  }

  #armIdle(): void {
    const limit = this.#setup.idleTimeoutMs;
    if (limit === undefined || !Number.isFinite(limit) || limit <= 0 || this.#closed) return;
    clearTimeout(this.#idleTimer);
    this.#idleTimer = setTimeout(() => {
      if (this.#pending > 0 || this.#closed || this.#client.status !== "ready") return;
      this.#idle = true;
      this.#client.terminate();
    }, limit);
    // An idle timer alone must not keep a Node process alive.
    (this.#idleTimer as { unref?: () => void }).unref?.();
  }

  async #attempt<T>(signal: AbortSignal | undefined, work: (client: WorkerClient) => Promise<T>): Promise<T> {
    if (this.#closed) throw this.#closed;
    signal?.throwIfAborted();
    if (this.#idle) {
      this.#idle = false;
      this.#client = this.#start();
    }
    const client = this.#client;
    const limit = this.#setup.timeoutMs;
    const grace = positive(this.#setup.interruptGraceMs, DEFAULT_INTERRUPT_GRACE_MS);
    const running = work(client);
    // Stopping waiting is not enough when the work can be stopped too.
    const onAbort = (): void => client.interrupt();
    signal?.addEventListener("abort", onAbort, { once: true });
    let timer: ReturnType<typeof setTimeout> | undefined;
    let settled = false;
    let late = false;
    const timeoutError = (): PydiggsWorkerError =>
      this.#setup.makeError(`the request took longer than ${limit} ms`, "timeout");
    const timedOut = new Promise<never>((_, reject) => {
      if (!Number.isFinite(limit)) return;
      const arm = (): void => {
        if (settled) return;
        timer = setTimeout(() => {
          late = true;
          if (!client.interruptible) {
            this.#restart(client);
            reject(timeoutError());
            return;
          }
          // Ask Python to stop, and keep the worker if it answers within the grace
          // period. The next request waits for this, so it never lands on a worker
          // that is about to be replaced.
          client.interrupt();
          const replace = setTimeout(() => {
            this.#restart(client);
            reject(timeoutError());
          }, grace);
          running.then(
            () => clearTimeout(replace),
            () => clearTimeout(replace),
          );
        }, limit);
      };
      // The clock starts when the worker is loaded: loading is not the request's time.
      if (client.status === "ready") arm();
      else client.ready.then(arm, () => {});
    });
    try {
      return await Promise.race([running, timedOut]);
    } catch (error) {
      if (error instanceof PydiggsWorkerError && error.code === "timeout") throw error;
      // The worker stopped the request because the supervisor asked it to.
      if (late && error instanceof PydiggsWorkerError && error.problem?.code === "cancelled") throw timeoutError();
      if (this.#closed) throw this.#closed;
      if (client.status === "failed" && client === this.#client && this.#loaded.has(client)) {
        this.#restart(client);
        throw this.#setup.makeError(error instanceof Error ? error.message : String(error), "crashed");
      }
      throw error;
    } finally {
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
}
