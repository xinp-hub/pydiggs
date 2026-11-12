// Serves pyDIGGS's HTTP API (`/v1/validate`, `/v1/read`, `/v1/readyz`) from a JavaScript
// server, as a standard Fetch handler: `(request: Request) => Promise<Response>`. That is
// the signature of Next.js route handlers, Hono, Bun.serve, Deno.serve and Remix
// actions, and Express or Fastify adapt to it in a line. Behind it, PydiggsNode runs
// the real pydiggs, so `PydiggsHttpClient` and `PydiggsHybrid` work against it unchanged.
//
// It speaks the subset of the API the worker supports, with the same status codes and
// problem documents as `pydiggs serve --http`, whose texts never echo a value sent. It
// adds no authentication, like pydiggs itself: put it behind your own.

import type { DocumentBytes, WorkerReadOptions, WorkerRequestOptions, WorkerStatus } from "./worker-client.ts";
import type { DiggsFindingsDocument } from "./generated/findings.ts";
import type { DiggsModelDocument } from "./generated/model.ts";
import type { Problem } from "./generated/http.ts";

/** What the handler needs from an engine; a `PydiggsNode` is one. */
export interface PydiggsEngine {
  readonly status: WorkerStatus;
  readonly pending: number;
  validate(input: DocumentBytes, options?: WorkerRequestOptions): Promise<DiggsFindingsDocument>;
  read(input: DocumentBytes, options?: WorkerReadOptions): Promise<DiggsModelDocument>;
}

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
  "invalid-document": [422, "Not a DIGGS document", "The bytes are not a DIGGS document."],
  "invalid-host": [400, "Invalid host", "The Host header is not allowed."],
  "invalid-multipart": [400, "Invalid multipart body", "The multipart body is not one file part with known fields."],
  "invalid-parameter": [422, "Invalid parameter", "A parameter is invalid or not supported by this server."],
  "method-not-allowed": [405, "Method not allowed", "This path does not answer that method."],
  "missing-input": [400, "Missing input", "No document was sent."],
  "not-acceptable": [406, "Not acceptable", "This server answers with application/json only."],
  "not-found": [404, "Not found", "No such path."],
  "not-ready": [503, "Not ready", "pydiggs is not ready on this server."],
  "request-timeout": [408, "Request timeout", "The body did not arrive in time."],
  "resource-error": [500, "Resource error", "Bundled reference data could not be loaded."],
  "result-too-large": [422, "Result too large", "The answer would be too large."],
  timeout: [504, "Timeout", "The document took too long."],
  "unsupported-media-type": [415, "Unsupported media type", "Send application/xml, text/xml or multipart/form-data."],
};

function problem(
  code: Problem["code"],
  extra: { errors?: { parameter: string; problem: string }[]; headers?: Record<string, string> } = {},
): Response {
  const [status, title, detail] = PROBLEMS[code];
  const body: Problem = { type: `${PROBLEM_BASE}${code}`, title, status, detail, code };
  if (extra.errors) body.errors = extra.errors;
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store", ...extra.headers },
  });
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
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

function acceptsJson(accept: string | null): boolean {
  if (accept === null || accept.trim() === "") return true;
  return accept
    .split(",")
    .map((part) => part.split(";")[0]!.trim().toLowerCase())
    .some((type) => type === "*/*" || type === "application/*" || type === "application/json");
}

// pydiggs reduces a reported name to its base name, `input.xml` when empty.
function baseName(name: string | null): string {
  const base = (name ?? "").split(/[\\/]/).pop() ?? "";
  return base === "" ? "input.xml" : base;
}

interface Parsed {
  data: Uint8Array;
  name: string;
  select: string | null;
}

/** Returns a Fetch handler serving `/v1/validate`, `/v1/read` and `/v1/readyz` from `engine`. */
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
    const unsupported = [...url.searchParams.keys()].filter((key) => !allowed.has(key));
    if (unsupported.length > 0) {
      return problem("invalid-parameter", {
        errors: unsupported.slice(0, 20).map((parameter) => ({ parameter, problem: "not supported by this server" })),
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

    let name = url.searchParams.get("name");
    let select = url.searchParams.get("select");
    let data: Uint8Array;
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
      const fields = [...form.keys()].filter((key) => key !== "file");
      if (files.length !== 1 || !(files[0] instanceof Blob) || fields.some((key) => !allowed.has(key))) {
        return problem("invalid-multipart");
      }
      const file = files[0];
      data = new Uint8Array(await file.arrayBuffer());
      const fieldName = form.get("name");
      if (typeof fieldName === "string") name = fieldName;
      else if (name === null && file instanceof File) name = file.name;
      const fieldSelect = form.get("select");
      if (typeof fieldSelect === "string") select = fieldSelect;
    } else {
      return problem("unsupported-media-type");
    }
    if (data.byteLength === 0) return problem("missing-input");
    if (name !== null && name.length > 4096) {
      return problem("invalid-parameter", { errors: [{ parameter: "name", problem: "too long" }] });
    }
    return { data, name: baseName(name), select };
  }

  function gate(request: Request): Response | null {
    if (rejectCrossSite && request.headers.get("Sec-Fetch-Site") === "cross-site") return problem("cross-site");
    if (!acceptsJson(request.headers.get("Accept"))) return problem("not-acceptable");
    if (engine.status === "failed") return problem("not-ready");
    if (engine.pending >= maxPending) return problem("busy", { headers: { "Retry-After": "1" } });
    return null;
  }

  async function validate(request: Request): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    const parsed = await parse(request, new Set(["name"]));
    if (parsed instanceof Response) return parsed;
    try {
      return json(await engine.validate(parsed.data, { name: parsed.name, signal: request.signal }));
    } catch {
      return engine.status === "failed" ? problem("not-ready") : problem("input-unreadable");
    }
  }

  async function read(request: Request): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    const parsed = await parse(request, new Set(["name", "select"]));
    if (parsed instanceof Response) return parsed;
    let results = true;
    if (parsed.select !== null) {
      let choice: unknown;
      try {
        choice = (JSON.parse(parsed.select) as { results?: unknown }).results;
      } catch {
        choice = undefined;
      }
      if (choice !== "all" && choice !== "none") {
        // Lists of node keys need table selection inside pydiggs, which the worker lacks.
        return problem("invalid-parameter", {
          errors: [{ parameter: "select", problem: 'only {"results": "all"} or {"results": "none"} is supported' }],
        });
      }
      results = choice === "all";
    }
    try {
      return json(await engine.read(parsed.data, { name: parsed.name, results, signal: request.signal }));
    } catch {
      return engine.status === "failed" ? problem("not-ready") : problem("invalid-document");
    }
  }

  return async (request: Request): Promise<Response> => {
    try {
      const path = new URL(request.url).pathname;
      const route = path.startsWith(`${base}/`) || path === base ? path.slice(base.length) : null;
      switch (route) {
        case "/v1/validate":
        case "/v1/read":
          if (request.method !== "POST") return problem("method-not-allowed", { headers: { Allow: "POST" } });
          return route === "/v1/validate" ? await validate(request) : await read(request);
        case "/v1/readyz":
          if (request.method !== "GET") return problem("method-not-allowed", { headers: { Allow: "GET" } });
          return engine.status === "ready" ? json({ status: "ready" }) : problem("not-ready");
        default:
          return problem("not-found");
      }
    } catch {
      return problem("internal");
    }
  };
}
