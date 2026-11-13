// Several engines behind one `validate` and one `read`, for batches: a page checking a
// folder of files, or a server answering several uploads at once. Each engine is one
// Pyodide worker running one document at a time, so N engines run N at once.
//
// Each engine holds its own Pyodide (about 90 MiB loaded, and some 14 times the document
// while it works: js/README.md, Sizing), so keep N small, above all in a browser tab.

import { PydiggsError } from "./errors.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type { DiggsModelDocument } from "./generated/model.ts";
import type { RuntimeVersions } from "./protocol.ts";
import type {
  DocumentBytes,
  RuleInfo,
  SarifLog,
  Versions,
  WorkerCallOptions,
  WorkerReadOptions,
  WorkerRulesOptions,
  WorkerStatus,
  WorkerValidateOptions,
} from "./worker-client.ts";

/** What a pool needs from an engine; `PydiggsBrowser` and `PydiggsNode` are engines. */
export interface PoolEngine {
  readonly status: WorkerStatus;
  readonly pending: number;
  readonly ready: Promise<RuntimeVersions>;
  validate(input: DocumentBytes, options?: WorkerValidateOptions): Promise<DiggsFindingsDocument>;
  validateSarif(input: DocumentBytes, options?: WorkerValidateOptions): Promise<SarifLog>;
  read(input: DocumentBytes, options?: WorkerReadOptions): Promise<DiggsModelDocument>;
  versions(options?: WorkerCallOptions): Promise<Versions>;
  listRules(options?: WorkerRulesOptions): Promise<RuleInfo[]>;
  explainRule(ruleId: string, options?: WorkerCallOptions): Promise<RuleInfo>;
  terminate(): void;
}

export interface PydiggsPoolOptions {
  /**
   * How many engines to run. Default: one less than the processors the platform
   * reports, at least 1 and at most 4.
   */
  size?: number;
}

/** One less than the processors reported (navigator.hardwareConcurrency), from 1 to 4. */
export function defaultPoolSize(): number {
  const cores = (globalThis as { navigator?: { hardwareConcurrency?: number } }).navigator?.hardwareConcurrency;
  return Math.min(4, Math.max(1, (typeof cores === "number" && cores > 0 ? cores : 2) - 1));
}

/**
 * Engines made by `create`, each document sent to the least busy engine that is not
 * out of service. One engine's timeout, crash or restart does not stop the others.
 * The pool has the same shape as an engine, so it goes wherever one does:
 * `PydiggsHybrid`'s `browser`, `createPydiggsHandler`.
 */
export class PydiggsPool<E extends PoolEngine = PoolEngine> {
  /** The engines, in creation order. */
  readonly engines: readonly E[];
  /** Settles when the first engine is ready, or rejects when every engine failed to load. */
  readonly ready: Promise<RuntimeVersions>;

  constructor(create: (index: number) => E, options: PydiggsPoolOptions = {}) {
    const size = Math.max(1, Math.floor(options.size ?? defaultPoolSize()));
    this.engines = Array.from({ length: size }, (_, index) => create(index));
    this.ready = Promise.any(this.engines.map((engine) => engine.ready)).catch(() => {
      throw new PydiggsError("no engine in the pool could load pydiggs");
    });
    this.ready.catch(() => {});
  }

  /** "ready" if any engine is, else "loading" if any is, else "idle" if any is, else "failed". */
  get status(): WorkerStatus {
    const statuses = this.engines.map((engine) => engine.status);
    for (const status of ["ready", "loading", "idle"] as const) if (statuses.includes(status)) return status;
    return "failed";
  }

  /** Documents waiting or running, over all engines. */
  get pending(): number {
    return this.engines.reduce((sum, engine) => sum + engine.pending, 0);
  }

  /** Validates a document on the least busy engine. */
  validate(input: DocumentBytes, options?: WorkerValidateOptions): Promise<DiggsFindingsDocument> {
    return this.#pick().validate(input, options);
  }

  /** Validates a document on the least busy engine, as a SARIF 2.1.0 log. */
  validateSarif(input: DocumentBytes, options?: WorkerValidateOptions): Promise<SarifLog> {
    return this.#pick().validateSarif(input, options);
  }

  /** Reads a document on the least busy engine. */
  read(input: DocumentBytes, options?: WorkerReadOptions): Promise<DiggsModelDocument> {
    return this.#pick().read(input, options);
  }

  /** pydiggs's versions, from the least busy engine. */
  versions(options?: WorkerCallOptions): Promise<Versions> {
    return this.#pick().versions(options);
  }

  /** The rule catalogue, from the least busy engine. */
  listRules(options?: WorkerRulesOptions): Promise<RuleInfo[]> {
    return this.#pick().listRules(options);
  }

  /** One rule, from the least busy engine. */
  explainRule(ruleId: string, options?: WorkerCallOptions): Promise<RuleInfo> {
    return this.#pick().explainRule(ruleId, options);
  }

  /** Stops every engine. */
  terminate(): void {
    for (const engine of this.engines) engine.terminate();
  }

  // Ready engines before loading or idle ones (a loading engine may be restarting after
  // a timeout; an idle one starts again on its next request), then the fewest
  // documents waiting, then creation order.
  #pick(): E {
    const rank = (engine: E): number => (engine.status === "ready" ? 0 : 1);
    const usable = this.engines.filter((engine) => engine.status !== "failed");
    const best = usable.sort((a, b) => rank(a) - rank(b) || a.pending - b.pending)[0];
    if (!best) throw new PydiggsError("every engine in the pool is out of service");
    return best;
  }
}
