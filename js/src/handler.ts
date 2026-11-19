// Serves pyDIGGS's HTTP API (`/v1/validate`, `/v1/read`, `/v1/convert`, `/v1/ags4/check`,
// `/v1/diff`, `/v1/versions`, `/v1/capabilities`, `/v1/rules`, `/v1/rules/{rule_id}`, `/v1/readyz`)
// from a JavaScript
// server, as a standard Fetch handler: `(request: Request) => Promise<Response>`. That is
// the signature of Next.js route handlers, Hono, Bun.serve, Deno.serve and Remix
// actions, and Express or Fastify adapt to it in a line. Behind it, PydiggsNode runs
// the real pydiggs, so `PydiggsHttpClient` and `PydiggsHybrid` work against it unchanged.
//
// Every request goes through the same service layer as `pydiggs serve --http`, so the
// parameters (`checks`, `diggs_version`, `select`, SARIF by `Accept`), the status codes
// and the problem documents are the same, and no text echoes a value sent. It adds no
// authentication, like pydiggs itself: put it behind your own.

import type { ConvertAgsVersion, ConvertDiggsVersion, ConvertTarget, DiggsVersion, Problem, RuleStep, ValidateCheck } from "./generated/http.ts";
import type { PoolEngine } from "./pool.ts";
import type { ServiceProblem } from "./protocol.ts";

/** What the handler needs from an engine; a `PydiggsNode` and a `PydiggsPool` are engines. */
export type PydiggsEngine = Pick<
  PoolEngine,
  | "status"
  | "pending"
  | "validate"
  | "validateSarif"
  | "read"
  | "versions"
  | "listRules"
  | "explainRule"
  | "convert"
  | "checkAgs4"
  | "checkAgs4Sarif"
  | "capabilities"
> &
  Partial<Pick<PoolEngine, "diff">>;

export interface PydiggsHandlerOptions {
  /** Path the API is mounted under, without `/v1`, e.g. "/api/pydiggs". Default: "". */
  basePath?: string;
  /**
   * Largest request body, in bytes. Default: 25 MiB. Keep it at or below the engine's
   * `maxBytes`. `/v1/diff` takes two files of up to this size each, as pyDIGGS does.
   */
  maxBodyBytes?: number;
  /**
   * Requests in progress at once, counted from before their body is read until they are
   * answered, as pyDIGGS admits them; beyond it the answer is 503 `busy`. Default: 8.
   */
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
const MAX_CHECKS = 32;
const MAX_NAME_CHARS = 4096;
const NAME_PROBLEM = `must be a string of at most ${MAX_NAME_CHARS} characters`;
const MAX_SELECT_CHARS = 64 * 1024;
const MAX_SELECT_KEYS = 1000;
const SELECT_PROBLEM = `expected {"results": "all" | "none" | [at most ${MAX_SELECT_KEYS} node keys]}`;

// Every value of a generated union type, at run time. The compiler refuses a list that
// misses one, so a value pyDIGGS's OpenAPI document adds cannot be refused here unseen.
function every<T extends string>() {
  return <const L extends readonly T[]>(...list: L & ([T] extends [L[number]] ? unknown : never)): L => list;
}
const DIGGS_VERSIONS = every<DiggsVersion>()("2.5.a", "2.6", "3.0.0", "3.1-dev");
const STEPS = every<ValidateCheck>()("context", "deprecation", "dictionary", "geometry", "schema", "schematron", "structure");
const CONVERT_DIGGS_VERSIONS = every<ConvertDiggsVersion>()("3.0.0");
const CONVERT_AGS_VERSIONS = every<ConvertAgsVersion>()("4.2", "4.1.1");

/** pyDIGGS's check of one parameter's values, with its fixed text; null when they are valid. */
type ParameterCheck = (values: string[]) => string | null;
const oneOf =
  (allowed: readonly string[], text: string): ParameterCheck =>
  (values) =>
    allowed.includes(values.at(-1)!) ? null : text;
const nameCheck: ParameterCheck = (values) => (values.at(-1)!.length > MAX_NAME_CHARS ? NAME_PROBLEM : null);
const diggsVersionCheck = oneOf(DIGGS_VERSIONS, `expected one of ${[...DIGGS_VERSIONS].sort().join(", ")}`);
const checksCheck: ParameterCheck = (values) => {
  const checks = checksOf(values) ?? [];
  const valid = checks.length > 0 && checks.length <= MAX_CHECKS && checks.every((c) => (STEPS as readonly string[]).includes(c));
  return valid ? null : `expected some of ${[...STEPS].sort().join(",")}`;
};

// The parameters of each route, in the order pyDIGGS checks them. A query value that a
// form field overrides never reaches pyDIGGS, so it is checked here, as pyDIGGS's own
// server checks it, and refused as `invalid-parameter`.
const VALIDATE_CHECKS: Record<string, ParameterCheck> = {
  name: nameCheck,
  checks: checksCheck,
  diggs_version: diggsVersionCheck,
};
const READ_CHECKS: Record<string, ParameterCheck> = {
  name: nameCheck,
  diggs_version: diggsVersionCheck,
  select: (values) => (resultsOf(values) === null ? SELECT_PROBLEM : null),
};
const VALIDATE_FIELDS = new Set(Object.keys(VALIDATE_CHECKS));
const READ_FIELDS = new Set(Object.keys(READ_CHECKS));

// What each kind of body may be sent as, raw: DIGGS as XML; AGS4, which has no registered
// media type, as generic text or bytes. Either may also come as multipart/form-data.
const RAW_TYPES = {
  diggs: new Set(["application/xml", "text/xml"]),
  ags4: new Set(["application/octet-stream", "text/csv", "text/plain"]),
};
const CONVERT_TARGETS = every<ConvertTarget>()("diggs", "ags4");
const CONVERT_CHECKS: Record<string, ParameterCheck> = {
  name: nameCheck,
  to: oneOf(CONVERT_TARGETS, `expected one of ${CONVERT_TARGETS.join(", ")}`),
  diggs_version: oneOf(CONVERT_DIGGS_VERSIONS, `expected one of ${CONVERT_DIGGS_VERSIONS.join(", ")}`),
  ags_version: oneOf(CONVERT_AGS_VERSIONS, `expected one of ${CONVERT_AGS_VERSIONS.join(", ")}`),
};
const AGS4_CHECK_CHECKS: Record<string, ParameterCheck> = { name: nameCheck };
const CONVERT_FIELDS = new Set(Object.keys(CONVERT_CHECKS));
const AGS4_CHECK_FIELDS = new Set(Object.keys(AGS4_CHECK_CHECKS));
const DIFF_FILES = ["old", "new"] as const;
const DIFF_FIELDS = new Set([
  "old_name",
  "new_name",
  "match",
  "rel_tol",
  "abs_tol",
  "max_changes",
  "show_reformatted",
  "show_renamed",
]);
// What multipart framing and the short fields may add to a body, as in pyDIGGS.
const MULTIPART_SLACK = 8 * 64 * 1024 + 64 * 1024;
// Numbers and integers as pyDIGGS's server reads them; other text is passed on as text,
// so pyDIGGS refuses it as `invalid-parameter` with its own fixed text.
const NUMBER = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/;
const INTEGER = /^[0-9]{1,18}$/;

function diffOption(key: string, text: string): unknown {
  if (key === "match") return text;
  if (key === "show_reformatted" || key === "show_renamed") return text === "true" ? true : text === "false" ? false : text;
  if (key === "max_changes") return INTEGER.test(text) ? Number(text) : text;
  return text.length <= 64 && NUMBER.test(text) ? Number(text) : text;
}

const DIFF_MATCH = ["auto", "gml-id", "natural", "gml_id"];
const finite = (value: unknown, belowOne = false): boolean =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 && (!belowOne || value < 1);

// pyDIGGS's own check of one parameter, with its fixed text, for a query value that a
// form field overrides: pyDIGGS never sees that value, yet both sources must be valid.
function diffParameterProblem(key: string, text: string): string | null {
  const value = diffOption(key, text);
  if (key === "old_name" || key === "new_name") {
    return text.length > 4096 ? "must be a string of at most 4096 characters" : null;
  }
  if (key === "match") return DIFF_MATCH.includes(text) ? null : "expected one of auto, gml-id, natural";
  if (key === "rel_tol") return finite(value, true) ? null : "must be a finite number, 0 or more and less than 1";
  if (key === "abs_tol") return finite(value) ? null : "must be a finite number, 0 or more";
  if (key === "max_changes") return typeof value === "number" ? null : "must be an integer, 0 or more";
  return typeof value === "boolean" ? null : "must be true or false";
}

interface Preflight {
  url: URL;
  multipart: boolean;
  /** The largest body accepted: a file, plus the framing when it is multipart. */
  limit: number;
}

function mediaType(request: Request): string {
  return (request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase();
}

// pyDIGGS accepts no encoding header, an empty one, or "identity".
function compressed(request: Request): boolean {
  const encoding = (request.headers.get("Content-Encoding") ?? "").trim().toLowerCase();
  return encoding !== "" && encoding !== "identity";
}

interface Parsed {
  data: Uint8Array;
  /** The reported name, reduced to its base name; `input.xml` when none was given. */
  name: string;
  /** The base name given, if any (AGS4 requests leave the default to pyDIGGS). */
  givenName: string | undefined;
  /** An AGS4 check's `dictionary` part. */
  dictionary?: Uint8Array;
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
 * Returns a Fetch handler serving `/v1/validate`, `/v1/read`, `/v1/convert`,
 * `/v1/ags4/check`, `/v1/diff`, `/v1/versions`, `/v1/capabilities`, `/v1/rules`,
 * `/v1/rules/{rule_id}` and `/v1/readyz` from `engine`.
 */
export function createPydiggsHandler(
  engine: PydiggsEngine,
  options: PydiggsHandlerOptions = {},
): (request: Request) => Promise<Response> {
  const base = (options.basePath ?? "").replace(/\/+$/, "");
  const maxBody = options.maxBodyBytes ?? 25 * MiB;
  const maxPending = options.maxPending ?? 8;
  const rejectCrossSite = options.rejectCrossSite ?? true;

  // What pyDIGGS checks before it admits a request and reads its body, in its order: the
  // media type, then the encoding, then the declared length.
  function preflight(request: Request, allowed: Set<string>, kind: "diggs" | "ags4" = "diggs"): Preflight | Response {
    const url = new URL(request.url);
    const unsupported = [...new Set(url.searchParams.keys())].filter((key) => !allowed.has(key));
    if (unsupported.length > 0) {
      return problem("invalid-parameter", {
        errors: unsupported.slice(0, 20).map((parameter) => ({ parameter, problem: "unknown parameter" })),
      });
    }
    const multipart = mediaType(request) === "multipart/form-data";
    if (!multipart && !RAW_TYPES[kind].has(mediaType(request))) return problem("unsupported-media-type");
    if (compressed(request)) return problem("compressed-input");
    const limit = maxBody + (multipart ? MULTIPART_SLACK : 0);
    const length = Number(request.headers.get("Content-Length") ?? "NaN");
    if (Number.isFinite(length) && length > limit) return problem("input-too-large");
    return { url, multipart, limit };
  }

  // Requests in progress, from before their body is read until they are answered. pyDIGGS
  // admits a request the same way, so many slow uploads cannot each hold a body in memory.
  let inFlight = 0;
  const busy = (): Response => problem("busy", { headers: { "Retry-After": "1", Connection: "close" } });

  async function admitted(run: () => Promise<Response>): Promise<Response> {
    if (inFlight >= maxPending) return busy();
    inFlight += 1;
    try {
      return await run();
    } finally {
      inFlight -= 1;
    }
  }

  async function parse(
    request: Request,
    pre: Preflight,
    checks: Record<string, ParameterCheck>,
    extraFiles: ReadonlySet<string> = new Set(),
  ): Promise<Parsed | Response> {
    let raw: Uint8Array;
    try {
      raw = await readLimited(request.body, pre.limit);
    } catch (error) {
      if (error instanceof TooLarge) return problem("input-too-large", { headers: { Connection: "close" } });
      return problem("missing-input");
    }

    const params = new Map<string, string[]>();
    for (const key of Object.keys(checks)) params.set(key, pre.url.searchParams.getAll(key));
    let data: Uint8Array;
    let fileName: string | null = null;
    let dictionary: Uint8Array | undefined;
    if (!pre.multipart) {
      data = raw;
    } else {
      let form: FormData;
      try {
        form = await new Response(raw as BodyInit, { headers: { "Content-Type": request.headers.get("Content-Type")! } }).formData();
      } catch {
        return problem("invalid-multipart");
      }
      const files = form.getAll("file");
      const fields = [...new Set(form.keys())].filter((key) => key !== "file" && !extraFiles.has(key));
      const extras = [...extraFiles].map((key) => form.getAll(key));
      if (
        files.length !== 1 ||
        !(files[0] instanceof Blob) ||
        // A field is short text, as in pyDIGGS: a file part in its place is refused.
        fields.some((key) => !(key in checks) || form.getAll(key).some((value) => typeof value !== "string")) ||
        extras.some((parts) => parts.length > 1 || (parts.length === 1 && !(parts[0] instanceof Blob)))
      ) {
        return problem("invalid-multipart");
      }
      const file = files[0];
      if (file.size > maxBody) return problem("input-too-large", { headers: { Connection: "close" } });
      const overridden: { parameter: string; problem: string }[] = [];
      for (const [key, check] of Object.entries(checks)) {
        const query = params.get(key) ?? [];
        if (!fields.includes(key) || query.length === 0) continue;
        const wrong = check(query);
        if (wrong !== null) overridden.push({ parameter: key, problem: wrong });
      }
      if (overridden.length > 0) return problem("invalid-parameter", { errors: overridden });
      for (const key of fields) params.set(key, form.getAll(key) as string[]);
      const extra = form.get("dictionary");
      if (extraFiles.has("dictionary") && extra instanceof Blob) dictionary = new Uint8Array(await extra.arrayBuffer());
      data = new Uint8Array(await file.arrayBuffer());
      if (file instanceof File) fileName = file.name;
    }
    if (data.byteLength === 0) return problem("missing-input");
    const name = params.get("name")?.at(-1) ?? fileName;
    if (name !== null && name.length > MAX_NAME_CHARS) {
      return problem("invalid-parameter", { errors: [{ parameter: "name", problem: NAME_PROBLEM }] });
    }
    const parsed: Parsed = { data, name: baseName(name), givenName: name === null ? undefined : baseName(name), params };
    if (dictionary !== undefined) parsed.dictionary = dictionary;
    return parsed;
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
    if (engine.pending >= maxPending) return busy();
    return null;
  }

  async function validate(request: Request): Promise<Response> {
    const refused = gate(request, false);
    if (refused) return refused;
    const format = negotiate(request.headers.get("Accept"));
    if (format === null) return problem("not-acceptable");
    const pre = preflight(request, VALIDATE_FIELDS);
    if (pre instanceof Response) return pre;
    return admitted(() => validateAdmitted(request, pre, format));
  }

  async function validateAdmitted(request: Request, pre: Preflight, format: "findings" | "sarif"): Promise<Response> {
    const parsed = await parse(request, pre, VALIDATE_CHECKS);
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
    const pre = preflight(request, READ_FIELDS);
    if (pre instanceof Response) return pre;
    return admitted(() => readAdmitted(request, pre));
  }

  async function readAdmitted(request: Request, pre: Preflight): Promise<Response> {
    const parsed = await parse(request, pre, READ_CHECKS);
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

  async function convert(request: Request): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    // A raw body is DIGGS XML for to=ags4 (given in the query) and AGS4 otherwise.
    const toAgs4 = new URL(request.url).searchParams.getAll("to").at(-1) === "ags4";
    const pre = preflight(request, CONVERT_FIELDS, toAgs4 ? "diggs" : "ags4");
    if (pre instanceof Response) return pre;
    return admitted(() => convertAdmitted(request, pre));
  }

  async function convertAdmitted(request: Request, pre: Preflight): Promise<Response> {
    const parsed = await parse(request, pre, CONVERT_CHECKS);
    if (parsed instanceof Response) return parsed;
    const to = parsed.params.get("to")?.at(-1);
    if (to !== undefined && !(CONVERT_TARGETS as readonly string[]).includes(to)) {
      return problem("invalid-parameter", {
        errors: [{ parameter: "to", problem: `expected one of ${CONVERT_TARGETS.join(", ")}` }],
      });
    }
    const diggsVersion = parsed.params.get("diggs_version")?.at(-1);
    const agsVersion = parsed.params.get("ags_version")?.at(-1);
    try {
      return json(
        await engine.convert(parsed.data, {
          signal: request.signal,
          ...(parsed.givenName !== undefined ? { name: parsed.givenName } : {}),
          ...(to !== undefined ? { to: to as ConvertTarget } : {}),
          ...(diggsVersion !== undefined ? { diggsVersion: diggsVersion as ConvertDiggsVersion } : {}),
          ...(agsVersion !== undefined ? { agsVersion: agsVersion as ConvertAgsVersion } : {}),
        }),
      );
    } catch (error) {
      return failure(error);
    }
  }

  async function checkAgs4(request: Request): Promise<Response> {
    const refused = gate(request, false);
    if (refused) return refused;
    const format = negotiate(request.headers.get("Accept"));
    if (format === null) return problem("not-acceptable");
    const pre = preflight(request, AGS4_CHECK_FIELDS, "ags4");
    if (pre instanceof Response) return pre;
    return admitted(() => checkAgs4Admitted(request, pre, format));
  }

  async function checkAgs4Admitted(request: Request, pre: Preflight, format: "findings" | "sarif"): Promise<Response> {
    const parsed = await parse(request, pre, AGS4_CHECK_CHECKS, new Set(["dictionary"]));
    if (parsed instanceof Response) return parsed;
    const call = {
      signal: request.signal,
      ...(parsed.givenName !== undefined ? { name: parsed.givenName } : {}),
      ...(parsed.dictionary !== undefined ? { dictionary: parsed.dictionary } : {}),
    };
    try {
      if (format === "sarif") return json(await engine.checkAgs4Sarif(parsed.data, call), 200, SARIF_TYPE);
      return json(await engine.checkAgs4(parsed.data, call));
    } catch (error) {
      return failure(error);
    }
  }

  async function diff(request: Request): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    if (engine.diff === undefined) return problem("not-found");
    const url = new URL(request.url);
    const unsupported = [...new Set(url.searchParams.keys())].filter((key) => !DIFF_FIELDS.has(key));
    if (unsupported.length > 0) {
      return problem("invalid-parameter", {
        errors: unsupported.slice(0, 20).map((parameter) => ({ parameter, problem: "unknown parameter" })),
      });
    }
    if (mediaType(request) !== "multipart/form-data") return problem("unsupported-media-type", { detail: "Send multipart/form-data." });
    if (compressed(request)) return problem("compressed-input");
    const limit = DIFF_FILES.length * maxBody + MULTIPART_SLACK;
    const length = Number(request.headers.get("Content-Length") ?? "NaN");
    if (Number.isFinite(length) && length > limit) return problem("input-too-large");
    return admitted(() => diffAdmitted(request, url, limit, engine.diff!));
  }

  async function diffAdmitted(
    request: Request,
    url: URL,
    limit: number,
    run: NonNullable<PydiggsEngine["diff"]>,
  ): Promise<Response> {
    let form: FormData;
    try {
      const raw = await readLimited(request.body, limit);
      form = await new Response(raw as BodyInit, { headers: { "Content-Type": request.headers.get("Content-Type")! } }).formData();
    } catch (error) {
      return problem(error instanceof TooLarge ? "input-too-large" : "invalid-multipart");
    }
    const fields = [...new Set(form.keys())].filter((key) => !(DIFF_FILES as readonly string[]).includes(key));
    if (fields.some((key) => !DIFF_FIELDS.has(key) || form.getAll(key).some((value) => typeof value !== "string"))) {
      return problem("invalid-multipart");
    }
    const files: Uint8Array[] = [];
    const names: (string | null)[] = [];
    for (const side of DIFF_FILES) {
      const parts = form.getAll(side);
      if (parts.length > 1) return problem("invalid-multipart");
      const part = parts[0];
      if (!(part instanceof Blob)) {
        return problem("missing-input", { detail: "Send the two documents in multipart parts named 'old' and 'new'." });
      }
      if (part.size > maxBody) return problem("input-too-large", { headers: { Connection: "close" } });
      files.push(new Uint8Array(await part.arrayBuffer()));
      names.push(part instanceof File ? part.name : null);
    }
    const params = new Map<string, string[]>();
    const overridden: { parameter: string; problem: string }[] = [];
    for (const key of DIFF_FIELDS) {
      const values = [...url.searchParams.getAll(key)];
      const formed = form.getAll(key).filter((value): value is string => typeof value === "string");
      if (formed.length > 0 && values.length > 0) {
        const wrong = diffParameterProblem(key, values.at(-1)!);
        if (wrong !== null) overridden.push({ parameter: key, problem: wrong });
      }
      params.set(key, formed.length > 0 ? formed : values);
    }
    if (overridden.length > 0) return problem("invalid-parameter", { errors: overridden });
    const name = (side: "old" | "new", index: number): string | Response => {
      const given = params.get(`${side}_name`)?.at(-1) ?? names[index] ?? null;
      if (given !== null && given.length > 4096) {
        return problem("invalid-parameter", {
          errors: [{ parameter: `${side}_name`, problem: "must be a string of at most 4096 characters" }],
        });
      }
      return baseName(given);
    };
    const oldName = name("old", 0);
    if (oldName instanceof Response) return oldName;
    const newName = name("new", 1);
    if (newName instanceof Response) return newName;
    const option = (key: string): unknown => {
      const text = params.get(key)?.at(-1);
      return text === undefined ? undefined : diffOption(key, text);
    };
    const compare = Object.fromEntries(
      (
        [
          ["match", "match"],
          ["relTol", "rel_tol"],
          ["absTol", "abs_tol"],
          ["maxChanges", "max_changes"],
          ["showReformatted", "show_reformatted"],
          ["showRenamed", "show_renamed"],
        ] as const
      )
        .map(([key, field]) => [key, option(field)] as const)
        .filter(([, value]) => value !== undefined),
    );
    try {
      const result = await run(files[0]!, files[1]!, { ...compare, oldName, newName, signal: request.signal });
      // The same bound as pyDIGGS's endpoint: four times the per-file body limit.
      const body = new TextEncoder().encode(JSON.stringify(result));
      if (body.byteLength > 4 * maxBody) return problem("result-too-large");
      return new Response(body as BodyInit, {
        status: 200,
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store", Vary: "Accept" },
      });
    } catch (error) {
      return failure(error);
    }
  }

  async function info(request: Request, route: string): Promise<Response> {
    const refused = gate(request);
    if (refused) return refused;
    try {
      if (route === "/v1/versions") return json(await engine.versions({ signal: request.signal }));
      if (route === "/v1/capabilities") return json(await engine.capabilities({ signal: request.signal }));
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
      if (route === "/v1/diff") {
        if (request.method !== "POST") return problem("method-not-allowed", { headers: { Allow: "POST" } });
        return await diff(request);
      }
      if (route === "/v1/convert" || route === "/v1/ags4/check") {
        if (request.method !== "POST") return problem("method-not-allowed", { headers: { Allow: "POST" } });
        return route === "/v1/convert" ? await convert(request) : await checkAgs4(request);
      }
      if (
        route === "/v1/versions" ||
        route === "/v1/capabilities" ||
        route === "/v1/rules" ||
        (route?.startsWith("/v1/rules/") && route.length > 10)
      ) {
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
