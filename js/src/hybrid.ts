// One `validate` and one `read` over both of this package's ways to run pydiggs: in the
// user's tab (PydiggsBrowser) and on your server (PydiggsHttpClient). Each document
// goes to the browser when the browser can take it, and to the server when it cannot:
// the document is too large for the device, the worker failed to load, or, if you
// allow it, the worker is still loading on a first visit.
//
// Pass no `server` and nothing ever leaves the tab. Every result says where it ran.

import type { BrowserDocumentInput } from "./browser.ts";
import type { PoolEngine } from "./pool.ts";
import { PydiggsError } from "./errors.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type { DiggsModelDocument } from "./generated/model.ts";
import { DEFAULT_MAX_BYTES } from "./generated/pins.ts";
import type { PydiggsHttpClient } from "./http.ts";

export { PydiggsError } from "./errors.ts";

/** Where a document was validated or read. */
export type Route = "browser" | "server";

/**
 * Why it went there: the browser was usable ("browser"), or there is no browser
 * ("no-browser"), the document is over `maxBrowserBytes` ("too-large"), the worker
 * failed to load or died on the document ("browser-unavailable"), the document ran past
 * the browser's time limit ("browser-timeout"), or the worker was still loading and
 * `whileLoading: "server"` was set ("browser-loading").
 */
export type RouteReason =
  | "browser"
  | "no-browser"
  | "too-large"
  | "browser-unavailable"
  | "browser-loading"
  | "browser-timeout";

export interface HybridResult<T> {
  document: T;
  via: Route;
  reason: RouteReason;
}

export interface PydiggsHybridOptions {
  /** Runs pydiggs in the tab: a `PydiggsBrowser`, or a `PydiggsPool` of them. Leave it out to use the server only. */
  browser?: Pick<PoolEngine, "status" | "ready" | "validate" | "read">;
  /** Runs pydiggs on your server. Leave it out and no document is ever uploaded. */
  server?: PydiggsHttpClient;
  /**
   * Documents larger than this go to the server. Default: 25 MiB, the browser's own
   * limit. Lower it for phones and tablets, which run short of memory sooner.
   */
  maxBrowserBytes?: number;
  /**
   * What to do with a document that arrives while the worker is still loading:
   * "wait" for it (the default: nothing is uploaded that could stay in the tab), or
   * send it to the server so a first visit gets its answer at once.
   */
  whileLoading?: "wait" | "server";
}

export interface HybridRequestOptions {
  /** File name to report. Default: the `File`'s own name, else `input.xml`. */
  name?: string;
  /** Stops waiting for the answer. */
  signal?: AbortSignal;
}

export interface HybridReadOptions extends HybridRequestOptions {
  /** Whether result tables keep their values. Default: true. */
  results?: boolean;
}

interface Decision {
  via: Route;
  reason: RouteReason;
}

function sizeOf(input: BrowserDocumentInput): number {
  return input instanceof Blob ? input.size : input.byteLength;
}

function nameOf(input: BrowserDocumentInput, options: HybridRequestOptions): string {
  if (options.name !== undefined) return options.name;
  return "name" in input && typeof input.name === "string" && input.name !== "" ? input.name : "input.xml";
}

// Waits for `promise`, unless `signal` aborts first.
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => reject(signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

export class PydiggsHybrid {
  readonly #browser: Pick<PoolEngine, "status" | "ready" | "validate" | "read"> | undefined;
  readonly #server: PydiggsHttpClient | undefined;
  readonly #maxBrowserBytes: number;
  readonly #whileLoading: "wait" | "server";

  constructor(options: PydiggsHybridOptions) {
    if (!options.browser && !options.server) {
      throw new PydiggsError("PydiggsHybrid needs a browser, a server, or both");
    }
    this.#browser = options.browser;
    this.#server = options.server;
    this.#maxBrowserBytes = Math.min(options.maxBrowserBytes ?? DEFAULT_MAX_BYTES, DEFAULT_MAX_BYTES);
    this.#whileLoading = options.whileLoading ?? "wait";
  }

  /**
   * Validates a document where it can run. A malformed or invalid document is not an
   * error: it comes back as findings, with the verdict in `files[0].verdict`.
   */
  validate(input: BrowserDocumentInput, options: HybridRequestOptions = {}): Promise<HybridResult<DiggsFindingsDocument>> {
    const request = { name: nameOf(input, options), ...(options.signal ? { signal: options.signal } : {}) };
    return this.#run(
      input,
      options.signal,
      () => this.#browser!.validate(input, request),
      () => this.#server!.validate(input, request),
    );
  }

  /**
   * Reads a document where it can run. Bytes that are not a DIGGS document are an
   * error on either route: a `PydiggsBrowserError` or a `PydiggsHttpError`, both
   * `PydiggsError`s.
   */
  read(input: BrowserDocumentInput, options: HybridReadOptions = {}): Promise<HybridResult<DiggsModelDocument>> {
    const name = nameOf(input, options);
    const signal = options.signal ? { signal: options.signal } : {};
    const results = options.results ?? true;
    return this.#run(
      input,
      options.signal,
      () => this.#browser!.read(input, { name, results, ...signal }),
      () => this.#server!.read(input, { name, results: results ? "all" : "none", ...signal }),
    );
  }

  // Sends the document where #decide says. If the worker dies on it (out of memory, say)
  // or it runs past the browser's time limit, the document goes to the server instead.
  // An error about the document is not retried: the server would say the same.
  async #run<T>(
    input: BrowserDocumentInput,
    signal: AbortSignal | undefined,
    inBrowser: () => Promise<T>,
    onServer: () => Promise<T>,
  ): Promise<HybridResult<T>> {
    const decision = await this.#decide(input, signal);
    if (decision.via === "server") return { document: await onServer(), ...decision };
    try {
      return { document: await inBrowser(), ...decision };
    } catch (error) {
      if (!this.#server || signal?.aborted) throw error;
      const code = (error as { code?: unknown } | null)?.code;
      if (code === "timeout") return { document: await onServer(), via: "server", reason: "browser-timeout" };
      if (code === "crashed" || this.#browser!.status === "failed") {
        return { document: await onServer(), via: "server", reason: "browser-unavailable" };
      }
      throw error;
    }
  }

  /** Where a document of this size would go now, without sending it anywhere. */
  route(sizeInBytes: number): Promise<Decision> {
    return this.#decideBySize(sizeInBytes, undefined);
  }

  #decide(input: BrowserDocumentInput, signal: AbortSignal | undefined): Promise<Decision> {
    return this.#decideBySize(sizeOf(input), signal);
  }

  async #decideBySize(size: number, signal: AbortSignal | undefined): Promise<Decision> {
    const browser = this.#browser;
    if (!browser) return this.#toServer("no-browser");
    if (size > this.#maxBrowserBytes) return this.#toServer("too-large");
    if (browser.status === "failed") return this.#toServer("browser-unavailable");
    if (browser.status === "loading") {
      if (this.#whileLoading === "server" && this.#server) return this.#toServer("browser-loading");
      try {
        await untilAborted(browser.ready, signal);
      } catch (error) {
        if (signal?.aborted) throw error;
        return this.#toServer("browser-unavailable");
      }
    }
    return { via: "browser", reason: "browser" };
  }

  #toServer(reason: RouteReason): Decision {
    if (!this.#server) {
      const why = {
        "no-browser": "there is no browser worker",
        "too-large": `the document is larger than ${this.#maxBrowserBytes} bytes`,
        "browser-unavailable": "the browser worker is not available",
        "browser-loading": "the browser worker is still loading",
        "browser-timeout": "the document ran past the browser's time limit",
        browser: "",
      }[reason];
      throw new PydiggsError(`cannot run pydiggs in the browser (${why}), and no server is configured`);
    }
    return { via: "server", reason };
  }
}
