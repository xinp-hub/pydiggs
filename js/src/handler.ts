// Serves pyDIGGS's HTTP API (`/v1/validate`, `/v1/read`, `/v1/versions`, `/v1/rules`,
// `/v1/rules/{rule_id}`, `/v1/readyz`) from a JavaScript
// server, as a standard Fetch handler: `(request: Request) => Promise<Response>`. That is
// the signature of Next.js route handlers, Hono, Bun.serve, Deno.serve and Remix
// actions, and Express or Fastify adapt to it in a line. Behind it, PydiggsNode runs
// the real pydiggs, so `PydiggsHttpClient` and `PydiggsHybrid` work against it unchanged.
//
// Every request goes through the same service layer as `pydiggs serve --http`, so the
// parameters (`checks`, `diggs_version`, `select`, SARIF by `Accept`), the status codes
// and the problem documents are the same, and no text echoes a value sent. It adds no
// authentication, like pydiggs itself: put it behind your own.

import type { DiggsVersion, Problem, RuleStep, ValidateCheck } from "./generated/http.ts";
import type { PoolEngine } from "./pool.ts";
import type { ServiceProblem } from "./protocol.ts";

/** What the handler needs from an engine; a `PydiggsNode` and a `PydiggsPool` are engines. */
export type PydiggsEngine = Pick<
  PoolEngine,
  "status" | "pending" | "validate" | "validateSarif" | "read" | "versions" | "listRules" | "explainRule"
>;

export interface PydiggsHandlerOptions {
  /** Path the API is mounted under, without `/v1`, e.g. "/api/pydiggs". Default: "". */
  basePath?: string;
  /** Largest request body, in bytes. Default: 25 MiB. Keep it at or below the engine's `maxBytes`. */
  maxBodyBytes?: number;
  /** Requests allowed to wait at once; beyond it the answer is 503 `busy`. Default: 8. */
  maxPending?: number;
  /**
   * Refuse requests a browser marks as cross-site (`Sec-Fetch-Site: cross-site`), as
   * pydiggs does by default. Default: true.
   */
  rejectCrossSite?: boolean;
}

const MiB = 1024 * 1024;
const PROBLEM_BASE = "https://xinp-hub.github.io/pydiggs/http-api/#problem-";

// Fixed texts, as in pydiggs: a problem never repeats what the client sent.
const PROBLEMS: Record<Problem["code"], [number, string, string]> = {
  busy: [503, "Server busy", "Too many requests are in progress; try again shortly."],
  "compressed-input": [415, "Compressed input", "Send the document uncompressed."],
  "cross-site": [403, "Cross-site request", "Cross-site requests are refused."],
  "input-too-large": [413, "Input too large", "The document is larger than this server accepts."],
  "input-unreadable": [422, "Input unreadable", "The document could not be read."],
  internal: [500, "Internal error", "The server failed to answer."],
  "invalid-document": [422, "Invalid document", "The bytes are not a DIGGS document."],
  "invalid-host": [400, "Invalid host", "The Host header is not allowed."],
  "invalid-multipart": [400, "Invalid multipart body", "The multipart body is not one file part with known fields."],
  "invalid-parameter": [422, "Invalid parameter", "invalid parameters"],
  "method-not-allowed": [405, "Method not allowed", "This path does not answer that method."],
  "missing-input": [400, "Missing input", "No document was sent."],
  "not-acceptable": [406, "Not acceptable", "Accept application/json (diggs-findings) or application/sarif+json."],
  "not-found": [404, "Not found", "No such path."],
  "not-ready": [503, "Not ready", "pydiggs is not ready on this server."],
  "request-timeout": [408, "Request timeout", "The body did not arrive in time."],
  "resource-error": [500, "Resource error", "Bundled reference data could not be loaded."],
  "result-too-large": [422, "Result too large", "The answer would be too large."],
  timeout: [504, "Timeout", "The document took too long."],
  "unsupported-media-type": [415, "Unsupported media type", "Send application/xml, text/xml or multipart/form-data."],
};

interface ProblemExtra {
  /** pydiggs's own text, which names at most the reported base name. Default: the fixed text. */
  detail?: string | undefined;
  errors?: { parameter: string; problem: string }[] | undefined;
  kind?: string | null | undefined;
  headers?: Record<string, string>;
}

function problem(code: Problem["code"], extra: ProblemExtra = {}): Response {
  const [status, title, fixed] = PROBLEMS[code];
  const detail = extra.detail ? extra.detail : fixed;
  const body: Problem = { type: `${PROBLEM_BASE}${code}`, title, status, detail, code };
  if (extra.errors && extra.errors.length > 0) body.errors = extra.errors;
  if (typeof extra.kind === "string") body.kind = extra.kind;
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store", ...extra.headers },
  });
}

function json(value: unknown, status = 200, type = "application/json"): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": type, "Cache-Control": "no-store", Vary: "Accept" },
  });
}

class TooLarge extends Error {}

// Reads the body without ever holding more than `limit` bytes of it.
async function readLimited(body: ReadableStream<Uint8Array> | null, limit: number): Promise<Uint8Array> {
  if (!body) return new Uint8Array(0);
  const chunks: Uint8Array[] = [];
  let size = 0;
  const reader = body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      throw new TooLarge();
    }
    chunks.push(value);
  }
  const all = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    all.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return all;
}

// Content negotiation as pydiggs does it: the most specific matching range sets each
// candidate's quality; the higher quality wins, then the more specific match, then JSON.
const MAX_ACCEPT_ITEMS = 32;
const FINDINGS_TYPE = "application/json";
const SARIF_TYPE = "application/sarif+json";

function quality(params: string[]): number {
  for (const param of params) {
    const [key, value] = param.split("=", 2) as [string, string | undefined];
    if (key.trim().toLowerCase() === "q") {
      const q = Number((value ?? "").trim());
      return Number.isFinite(q) && (value ?? "").trim() !== "" ? Math.min(1, Math.max(0, q)) : 0;
    }
  }
  return 1;
}

function best(accept: string): { findings: [number, number]; sarif: [number, number] } {
  const result: { findings: [number, number]; sarif: [number, number] } = { findings: [-1, 0], sarif: [-1, 0] };
  for (const item of accept.split(",").slice(0, MAX_ACCEPT_ITEMS)) {
    const [media = "", ...params] = item.split(";");
    const type = media.trim().toLowerCase();
    const q = quality(params);
    for (const [format, exact] of [["findings", FINDINGS_TYPE], ["sarif", SARIF_TYPE]] as const) {
      const specificity = type === exact ? 2 : type === "application/*" ? 1 : type === "*/*" ? 0 : -1;
      if (specificity > result[format][0]) result[format] = [specificity, q];
    }
  }
  return result;
}

/** "findings", "sarif", or null when neither is acceptable (406). */
export function negotiate(accept: string | null): "findings" | "sarif" | null {
  if (accept === null || accept.trim() === "") return "findings";
  const { findings, sarif } = best(accept);
  if (findings[1] <= 0 && sarif[1] <= 0) return null;
  if (sarif[1] > findings[1] || (sarif[1] === findings[1] && sarif[0] > findings[0])) return "sarif";
  return "findings";
}

function acceptsJson(accept: string | null): boolean {
  if (accept === null || accept.trim() === "") return true;
  return best(accept).findings[1] > 0;
}

// pydiggs reduces a reported name to its base name, `input.xml` when empty.
function baseName(name: string | null): string {
  const base = (name ?? "").split(/[\\/]/).pop() ?? "";
  return base === "" ? "input.xml" : base;
}


const MAX_CHECKS_CHARS = 512;
const MAX_SELECT_CHARS = 64 * 1024;
const MAX_SELECT_KEYS = 1000;
const SELECT_PROBLEM = `expected {"results": "all" | "none" | [at most ${MAX_SELECT_KEYS} node keys]}`;
const VALIDATE_FIELDS = new Set(["name", "checks", "diggs_version"]);
const READ_FIELDS = new Set(["name", "diggs_version", "select"]);

interface Parsed {
  data: Uint8Array;
  name: string;
  /** Every value of each parameter, query first, then the form's (which win). */
  params: Map<string, string[]>;
}

// `checks` as repeated and/or comma-separated values, each step once, as pydiggs reads
// it. An unknown or empty item is kept, so pydiggs refuses it as `invalid-parameter`.
function checksOf(values: string[]): string[] | undefined {
  if (values.length === 0) return undefined;
  if (values.some((value) => value.length > MAX_CHECKS_CHARS)) return [];
  return [...new Set(values.flatMap((value) => value.split(",")))];
}

// `select` as JSON text; the last value counts.
function resultsOf(values: string[]): "all" | "none" | string[] | null {
  const text = values.at(-1);
  if (text === undefined) return "all";
  let parsed: unknown;
  try {
    parsed = text.length > MAX_SELECT_CHARS ? undefined : JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const keys = Object.keys(parsed);
  if (keys.some((key) => key !== "results")) return null;
  const results = (parsed as { results?: unknown }).results ?? "all";
  if (results === "all" || results === "none") return results;
  if (
    !Array.isArray(results) ||
    results.length > MAX_SELECT_KEYS ||
    !results.every((key) => typeof key === "string" && key.length > 0 && key.length <= 256)
  ) {
    return null;
  }
  return results as string[];
}

/**
 * Returns a Fetch handler serving `/v1/validate`, `/v1/read`, `/v1/versions`,
 * `/v1/rules`, `/v1/rules/{rule_id}` and `/v1/readyz` from `engine`.
 */
export function createPydiggsHandler(
  engine: PydiggsEngine,
  options: PydiggsHandlerOptions = {},
): (request: Request) => Promise<Response> {
  const base = (options.basePath ?? "").replace(/\/+$/, "");
  const maxBody = options.maxBodyBytes ?? 25 * MiB;
  const maxPending = options.maxPending ?? 8;
  const rejectCrossSite = options.rejectCrossSite ?? true;

  async function parse(request: Request, allowed: Set<string>): Promise<Parsed | Response> {
    const url = new URL(request.url);
    const unsupported = [...new Set(url.searchParams.keys())].filter((key) => !allowed.has(key));
    if (unsupported.length > 0) {
      return problem("invalid-parameter", {
        errors: unsupported.slice(0, 20).map((parameter) => ({ parameter, problem: "unknown parameter" })),
      });
    }
    const encoding = (request.headers.get("Content-Encoding") ?? "identity").toLowerCase();
    if (encoding !== "identity") return problem("compressed-input");
    const length = Number(request.headers.get("Content-Length") ?? "NaN");
    if (Number.isFinite(length) && length > maxBody) return problem("input-too-large");

    const type = (request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase();
    let raw: Uint8Array;
    try {
      raw = await readLimited(request.body, maxBody);
    } catch (error) {
      if (error instanceof TooLarge) return problem("input-too-large");
      return problem("missing-input");
    }

    const params = new Map<string, string[]>();
    for (const key of allowed) params.set(key, url.searchParams.getAll(key));
    let data: Uint8Array;
    let fileName: string | null = null;
    if (type === "application/xml" || type === "text/xml") {
      data = raw;
    } else if (type === "multipart/form-data") {
      let form: FormData;
      try {
        form = await new Response(raw as BodyInit, { headers: { "Content-Type": request.headers.get("Content-Type")! } }).formData();
      } catch {
        return problem("invalid-multipart");
      }
      const files = form.getAll("file");
      const fields = [...new Set(form.keys())].filter((key) => key !== "file");
      if (files.length !== 1 || !(files[0] instanceof Blob) || fields.some((key) => !allowed.has(key))) {
        return problem("invalid-multipart");
      }
      const file = files[0];
      data = new Uint8Array(await file.arrayBuffer());
      if (file instanceof File) fileName = file.name;
      for (const key of fields) {
        const values = form.getAll(key).filter((value): value is string => typeof value === "string");
        if (values.length > 0) params.set(key, values);
      }
    } else {
      return problem("unsupported-media-type");
    }
    if (data.byteLength === 0) return problem("missing-input");
    const name = params.get("name")?.at(-1) ?? fileName;
    if (name !== null && name.length > 4096) {
      return problem("invalid-parameter", { errors: [{ parameter: "name", problem: "must be a string of at most 4096 characters" }] });
    }
    return { data, name: baseName(name), params };
  }

  // pydiggs's own answer when it refused the request; 504 for a request that ran too
  // long, as in pydiggs; 500 for a worker that died on it.
  function failure(error: unknown): Response {
    const { code, problem: refused } = (error ?? {}) as { code?: unknown; problem?: ServiceProblem };
    if (code === "timeout") return problem("timeout");
    if (code === "crashed") return problem("internal");
    if (refused && refused.code in PROBLEMS && refused.code !== "cancelled") {
      const status = PROBLEMS[refused.code as Problem["code"]][0];
      return problem(refused.code as Problem["code"], {
        detail: status === 500 ? undefined : refused.detail,
        errors: refused.errors,
        kind: refused.kind,
        ...(status === 413 ? { headers: { Connection: "close" } } : {}),
      });
    }
    return engine.status === "failed" ? problem("not-ready") : problem("internal");
  }

  function gate(request: Request, json = true): Response | null {
    if (rejectCrossSite && request.headers.get("Sec-Fetch-Site") === "cross-site") return problem("cross-site");
    if (json && !acceptsJson(request.headers.get("Accept"))) {
      return problem("not-acceptable", { detail: "Accept application/json." });
    }
    if (engine.status === "failed") return problem("not-ready");
    if (engine.pending >= maxPending) return problem("busy", { headers: { "Retry-After": "1" } });
    return null;
  }

  async function validate(request: Request): Promise<Response> {
    const refused = gate(request, false);
    if (refused) return refused;
    const format = negotiate(request.headers.get("Accept"));
    if (format === null) return problem("not-acceptable");
    const parsed = await parse(request, VALIDATE_FIELDS);
    if (parsed instanceof Response) return parsed;
    const checks = checksOf(parsed.params.get("checks") ?? []);
    const diggsVersion = parsed.params.get("diggs_version")?.at(-1);
    const call = {
      name: parsed.name,
      signal: request.signal,
      ...(checks ? { checks: checks as ValidateCheck[] } : {}),
      ...(diggsVersion !== undefined ? { diggsVersion: diggsVersion as DiggsVersion } : {}),
    };
    try {
      if (format === "sarif") return json(await engine.validateSarif(parsed.data, call), 200, SARIF_TYPE);
      return json(await engine.validate(parsed.data, call));
    } catch (error) {
      return failure(error);
    }
  }

  async function read(request: Request): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    const parsed = await parse(request, READ_FIELDS);
    if (parsed instanceof Response) return parsed;
    const results = resultsOf(parsed.params.get("select") ?? []);
    if (results === null) {
      return problem("invalid-parameter", { errors: [{ parameter: "select", problem: SELECT_PROBLEM }] });
    }
    const diggsVersion = parsed.params.get("diggs_version")?.at(-1);
    try {
      return json(
        await engine.read(parsed.data, {
          name: parsed.name,
          results,
          signal: request.signal,
          ...(diggsVersion !== undefined ? { diggsVersion: diggsVersion as DiggsVersion } : {}),
        }),
      );
    } catch (error) {
      return failure(error);
    }
  }

  async function info(request: Request, route: string): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    try {
      if (route === "/v1/versions") return json(await engine.versions({ signal: request.signal }));
      if (route === "/v1/rules") {
        const url = new URL(request.url);
        const unknown = [...new Set(url.searchParams.keys())].filter((key) => key !== "step");
        if (unknown.length > 0) {
          return problem("invalid-parameter", {
            errors: unknown.slice(0, 20).map((parameter) => ({ parameter, problem: "unknown parameter" })),
          });
        }
        const step = url.searchParams.get("step");
        return json(await engine.listRules({ signal: request.signal, ...(step !== null ? { step: step as RuleStep } : {}) }));
      }
      const id = decodeURIComponent(route.slice("/v1/rules/".length));
      return json(await engine.explainRule(id, { signal: request.signal }));
    } catch (error) {
      return failure(error);
    }
  }

  return async (request: Request): Promise<Response> => {
    try {
      const path = new URL(request.url).pathname;
      const route = path.startsWith(`${base}/`) || path === base ? path.slice(base.length) : null;
      if (route === "/v1/validate" || route === "/v1/read") {
        if (request.method !== "POST") return problem("method-not-allowed", { headers: { Allow: "POST" } });
        return route === "/v1/validate" ? await validate(request) : await read(request);
      }
      if (route === "/v1/versions" || route === "/v1/rules" || (route?.startsWith("/v1/rules/") && route.length > 10)) {
        if (request.method !== "GET") return problem("method-not-allowed", { headers: { Allow: "GET" } });
        return await info(request, route);
      }
      if (route === "/v1/readyz") {
        if (request.method !== "GET") return problem("method-not-allowed", { headers: { Allow: "GET" } });
        return engine.status === "ready" || engine.status === "idle" ? json({ status: "ready" }) : problem("not-ready");
      }
      return problem("not-found");
    } catch {
      return problem("internal");
    }
  };
}
