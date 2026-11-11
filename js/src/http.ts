// A typed client for the pydiggs HTTP API (`pydiggs serve --http`, or the container
// image). It uses `fetch` and nothing else, so it runs in Node 22+, Deno, Bun, edge
// runtimes and browsers.
//
// Every string in a findings or model document comes from the validated file. Escape
// it before it reaches HTML, a terminal or a prompt.

import { PydiggsError } from "./errors.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type {
  DiggsVersion,
  Problem,
  RuleInfo,
  RuleStep,
  SarifLog,
  ValidateCheck,
  Versions,
} from "./generated/http.ts";
import type { DiggsModelDocument } from "./generated/model.ts";

export type { DiggsVersion, Problem, Ready, RuleInfo, RuleStep, SarifLog, ValidateCheck, Versions } from "./generated/http.ts";
export { PydiggsError } from "./errors.ts";

/** The `code` of a problem answer; switch on it. */
export type ProblemCode = Problem["code"];

/**
 * A DIGGS document to send. A string is sent as UTF-8, so pass bytes for a document
 * that declares another encoding.
 */
export type DocumentInput = Blob | ArrayBuffer | ArrayBufferView | string;

/** Extra request headers, or a function that returns them for each request. */
export type HeadersSource = HeadersInit | (() => HeadersInit | Promise<HeadersInit>);

export interface PydiggsHttpClientOptions {
  /**
   * Where the API is served, without the `/v1` part: `"http://127.0.0.1:8000"`, or a
   * path such as `"/api/pydiggs"` when your own server proxies it.
   */
  baseUrl: string | URL;
  /** The `fetch` to use. Default: the global one. */
  fetch?: typeof globalThis.fetch;
  /**
   * Headers for every request, for example the credentials your gateway asks for.
   * pydiggs itself has no authentication.
   */
  headers?: HeadersSource;
}

export interface RequestOptions {
  /** Cancels the request. Use `AbortSignal.timeout(ms)` for a time limit. */
  signal?: AbortSignal;
}

export interface ValidateOptions extends RequestOptions {
  /** File name to report. The server reduces it to a base name. */
  name?: string;
  /** Steps to run. Default: all. */
  checks?: readonly ValidateCheck[];
  /** DIGGS version to check against. Default: detected. */
  diggsVersion?: DiggsVersion;
}

export interface ReadOptions extends RequestOptions {
  /** File name to report. The server reduces it to a base name. */
  name?: string;
  /** DIGGS version to read as. Default: detected. */
  diggsVersion?: DiggsVersion;
  /**
   * Which result tables keep their values: `"all"` (the default), `"none"`, or the
   * keys of the nodes whose tables you want (at most 1,000). A table that is left out
   * keeps its columns and `row_count`.
   */
  results?: "all" | "none" | readonly string[];
}

export interface ListRulesOptions extends RequestOptions {
  /** Only the rules of this step. */
  step?: RuleStep;
}

/**
 * The server answered with an error, or with something that is not the document the
 * endpoint promises.
 */
export class PydiggsHttpError extends PydiggsError {
  override name: string = "PydiggsHttpError";
  /** HTTP status of the answer. */
  readonly status: number;
  /** The problem's `code`, when the answer was a problem document. */
  readonly code: ProblemCode | undefined;
  /** The RFC 9457 problem document, when the answer was one. */
  readonly problem: Problem | undefined;
  /** Seconds from a `Retry-After` header, as sent with 503 `busy`. */
  readonly retryAfterSeconds: number | undefined;

  constructor(
    message: string,
    status: number,
    problem?: Problem,
    retryAfterSeconds?: number,
  ) {
    super(message);
    this.status = status;
    this.problem = problem;
    this.code = problem?.code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

// The server's header limit is 16 KiB, so a long `select` goes in a multipart field.
const MAX_QUERY_SELECT_LENGTH = 1024;

type Query = Record<string, string | undefined>;

interface Call {
  method: "GET" | "POST";
  path: string;
  query?: Query;
  accept?: string;
  body?: BodyInit;
  contentType?: string;
  signal?: AbortSignal | undefined;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function toBlob(document: DocumentInput): Blob {
  if (document instanceof Blob) return document;
  return new Blob([document as BlobPart]);
}

export class PydiggsHttpClient {
  readonly #baseUrl: string;
  readonly #fetch: typeof globalThis.fetch | undefined;
  readonly #headers: HeadersSource | undefined;

  constructor(options: PydiggsHttpClientOptions) {
    this.#baseUrl = String(options.baseUrl).replace(/\/+$/, "");
    this.#fetch = options.fetch;
    this.#headers = options.headers;
  }

  /**
   * Validates a document. A malformed or invalid document is not an error: it comes
   * back as findings, with the verdict in `files[0].verdict`.
   */
  async validate(document: DocumentInput, options: ValidateOptions = {}): Promise<DiggsFindingsDocument> {
    const body = await this.#json(this.#validateCall(document, options, "application/json"));
    if (!isRecord(body) || body["format"] !== "diggs-findings" || !Array.isArray(body["files"])) {
      throw new PydiggsHttpError("The answer is not a diggs-findings document.", 200);
    }
    return body as unknown as DiggsFindingsDocument;
  }

  /** Validates a document and returns the findings as a SARIF 2.1.0 log. */
  async validateSarif(document: DocumentInput, options: ValidateOptions = {}): Promise<SarifLog> {
    const body = await this.#json(this.#validateCall(document, options, "application/sarif+json"));
    if (!isRecord(body) || body["version"] !== "2.1.0" || !Array.isArray(body["runs"])) {
      throw new PydiggsHttpError("The answer is not a SARIF 2.1.0 log.", 200);
    }
    return body as unknown as SarifLog;
  }

  /**
   * Reads a document into a diggs-model document: boreholes, samples, tests and their
   * result tables. Reading is not validating; call `validate` for a verdict. Bytes
   * that are not a DIGGS document are a `PydiggsHttpError` with code
   * `invalid-document`.
   */
  async read(document: DocumentInput, options: ReadOptions = {}): Promise<DiggsModelDocument> {
    const results = options.results;
    const select =
      results === undefined || results === "all" ? undefined : JSON.stringify({ results });
    const query: Query = { name: options.name, diggs_version: options.diggsVersion };
    let call: Call;
    if (select !== undefined && select.length > MAX_QUERY_SELECT_LENGTH) {
      const form = new FormData();
      form.append("file", toBlob(document), options.name ?? "input.xml");
      form.append("select", select);
      call = { method: "POST", path: "/v1/read", query, body: form };
    } else {
      call = {
        method: "POST",
        path: "/v1/read",
        query: { ...query, select },
        body: document as BodyInit,
        contentType: "application/xml",
      };
    }
    const body = await this.#json({ ...call, accept: "application/json", signal: options.signal });
    if (!isRecord(body) || typeof body["schema_version"] !== "string" || !Array.isArray(body["features"])) {
      throw new PydiggsHttpError("The answer is not a diggs-model document.", 200);
    }
    return body as unknown as DiggsModelDocument;
  }

  /** The server's pydiggs version, findings schema and reference-data commits. */
  async versions(options: RequestOptions = {}): Promise<Versions> {
    const body = await this.#json({ method: "GET", path: "/v1/versions", signal: options.signal });
    if (!isRecord(body) || typeof body["pydiggs_version"] !== "string") {
      throw new PydiggsHttpError("The answer is not a versions document.", 200);
    }
    return body as unknown as Versions;
  }

  /** Every rule id the server can emit; open families come as patterns. */
  async listRules(options: ListRulesOptions = {}): Promise<RuleInfo[]> {
    const body = await this.#json({
      method: "GET",
      path: "/v1/rules",
      query: { step: options.step },
      signal: options.signal,
    });
    if (!Array.isArray(body)) throw new PydiggsHttpError("The answer is not a list of rules.", 200);
    return body as RuleInfo[];
  }

  /** One rule. A well-formed id the server does not emit comes back with `known: false`. */
  async explainRule(ruleId: string, options: RequestOptions = {}): Promise<RuleInfo> {
    const body = await this.#json({
      method: "GET",
      path: `/v1/rules/${encodeURIComponent(ruleId)}`,
      signal: options.signal,
    });
    if (!isRecord(body) || typeof body["id"] !== "string") {
      throw new PydiggsHttpError("The answer is not a rule.", 200);
    }
    return body as unknown as RuleInfo;
  }

  /** Whether the server's workers are warm. `false` while they are still loading. */
  async ready(options: RequestOptions = {}): Promise<boolean> {
    try {
      await this.#json({ method: "GET", path: "/v1/readyz", signal: options.signal });
      return true;
    } catch (error) {
      if (error instanceof PydiggsHttpError && error.code === "not-ready") return false;
      throw error;
    }
  }

  #validateCall(document: DocumentInput, options: ValidateOptions, accept: string): Call {
    return {
      method: "POST",
      path: "/v1/validate",
      query: {
        name: options.name,
        checks: options.checks && options.checks.length > 0 ? options.checks.join(",") : undefined,
        diggs_version: options.diggsVersion,
      },
      accept,
      body: document as BodyInit,
      contentType: "application/xml",
      signal: options.signal,
    };
  }

  async #json(call: Call): Promise<unknown> {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(call.query ?? {})) {
      if (value !== undefined) search.set(key, value);
    }
    const query = search.toString();
    const url = `${this.#baseUrl}${call.path}${query ? `?${query}` : ""}`;

    const source = typeof this.#headers === "function" ? await this.#headers() : this.#headers;
    const headers = new Headers(source);
    headers.set("Accept", call.accept ?? "application/json");
    // A FormData body sets its own multipart Content-Type, boundary included.
    if (call.contentType !== undefined) headers.set("Content-Type", call.contentType);

    const init: RequestInit = { method: call.method, headers };
    if (call.body !== undefined) init.body = call.body;
    if (call.signal !== undefined) init.signal = call.signal;

    const doFetch = this.#fetch ?? globalThis.fetch;
    const response = await doFetch(url, init);
    if (!response.ok) throw await problemError(response);
    try {
      return await response.json();
    } catch {
      throw new PydiggsHttpError("The answer is not JSON.", response.status);
    }
  }
}

async function problemError(response: Response): Promise<PydiggsHttpError> {
  let problem: Problem | undefined;
  if ((response.headers.get("Content-Type") ?? "").includes("application/problem+json")) {
    try {
      const body: unknown = await response.json();
      if (isRecord(body) && typeof body["code"] === "string") problem = body as unknown as Problem;
    } catch {
      // Not a problem document after all; report the status alone.
    }
  }
  const header = response.headers.get("Retry-After");
  const seconds = header !== null && /^\d+$/.test(header.trim()) ? Number(header) : undefined;
  const message = problem
    ? `${problem.title}: ${problem.detail} (${problem.code})`
    : `HTTP ${response.status} from the pydiggs server`;
  return new PydiggsHttpError(message, response.status, problem, seconds);
}
