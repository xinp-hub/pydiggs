// The tables profile on every surface of the client: the HTTP client (`POST
// /v1/tables/*`), the worker engines (PydiggsBrowser, and the worker core that runs
// pyDIGGS), the pool, the hybrid and the Fetch handler, each against a fake of the layer
// below it. The real glue runs in tests/pyodide/worker-smoke.mjs: a from-diggs request
// and a to-diggs package on the pinned Pyodide, compared with CPython's answers.

import assert from "node:assert/strict";
import { test } from "node:test";

import { PydiggsBrowser, type WorkerLike } from "../src/browser.ts";
import { conversionOutput } from "../src/convert.ts";
import type { DiggsFindingsDocument } from "../src/generated/findings.ts";
import type { TablesFromDiggsResult, TablesRoundTripResult, TablesToDiggsResult } from "../src/generated/http.ts";
import { createPydiggsHandler, type PydiggsEngine } from "../src/handler.ts";
import { PydiggsHttpClient, PydiggsHttpError } from "../src/http.ts";
import { PydiggsHybrid } from "../src/hybrid.ts";
import { PydiggsPool, type PoolEngine } from "../src/pool.ts";
import type { ReplyMessage } from "../src/protocol.ts";
import { createWorkerCore, type PyCallable, type PyodideLike } from "../src/worker-core.ts";

const FINDINGS = { format: "diggs-findings", files: [] } as unknown as DiggsFindingsDocument;
const XML = "<Diggs/>";
// Not the digest of the output: conversionOutput must refuse these as they are.
const XML_SHA256 = "0".repeat(64);

const TO_DIGGS = {
  profile_version: "1.0-beta.1",
  diggs_version: "3.0.0",
  media_type: "application/xml",
  output_name: "site.xml",
  lossless: true,
  counts: { features: 1, samples: 0, layers: 0, measurements: 0 },
  report: FINDINGS,
  output_sha256: XML_SHA256,
  output_base64: btoa(XML),
} as TablesToDiggsResult;

const ROUND_TRIP = {
  profile_version: "1.0-beta.1",
  ok: true,
  report: FINDINGS,
  validation: FINDINGS,
  differences: [],
  output_sha256: XML_SHA256,
  output_base64: btoa(XML),
} as TablesRoundTripResult;

const FROM_DIGGS = {
  profile_version: "1.0-beta.1",
  diggs_version: "3.0.0",
  complete: true,
  lossless: true,
  plan: { profile_version: "1.0-beta.1", source_sha256: null, steps: [] },
  report: FINDINGS,
  media_type: "application/zip",
  output_name: "input.zip",
  output_sha256: XML_SHA256,
  output_base64: btoa("PK"),
} as TablesFromDiggsResult;

const sha256 = async (text: string): Promise<string> =>
  [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");

const tick = () => new Promise((resolve) => setImmediate(resolve));
const ZIP = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);

// --- HTTP client ------------------------------------------------------------------------

function recordingFetch(answer: (url: URL) => unknown) {
  const seen: { url: URL; init: RequestInit }[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    seen.push({ url, init: init ?? {} });
    return new Response(JSON.stringify(answer(url)), { headers: { "Content-Type": "application/json" } });
  }) as typeof globalThis.fetch;
  return { seen, fetch };
}

test("the tables methods post the package as application/zip to /v1/tables/<operation>", async () => {
  const { seen, fetch } = recordingFetch((url) =>
    url.pathname.endsWith("/check") ? FINDINGS : url.pathname.endsWith("/round-trip") ? ROUND_TRIP : TO_DIGGS,
  );
  const client = new PydiggsHttpClient({ baseUrl: "http://h/api/", fetch });
  assert.equal((await client.tablesToDiggs(ZIP, { name: "site.zip" })).output_name, "site.xml");
  assert.equal((await client.checkTables(ZIP)).format, "diggs-findings");
  assert.equal((await client.tablesRoundTrip(new Blob([ZIP]))).ok, true);
  assert.deepEqual(
    seen.map(({ url }) => url.href),
    [
      "http://h/api/v1/tables/to-diggs?name=site.zip",
      "http://h/api/v1/tables/check",
      "http://h/api/v1/tables/round-trip",
    ],
  );
  for (const { init } of seen) {
    assert.equal(init.method, "POST");
    assert.equal(new Headers(init.headers).get("Content-Type"), "application/zip");
    assert.equal(new Headers(init.headers).get("Accept"), "application/json");
  }
});

test("tablesFromDiggs posts the DIGGS file as XML and returns the package result", async () => {
  const { seen, fetch } = recordingFetch(() => FROM_DIGGS);
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  const result = await client.tablesFromDiggs(XML, { name: "BH-01.xml" });
  assert.equal(result.media_type, "application/zip");
  assert.equal(seen[0]!.url.href, "http://h/v1/tables/from-diggs?name=BH-01.xml");
  assert.equal(new Headers(seen[0]!.init.headers).get("Content-Type"), "application/xml");
  assert.equal(seen[0]!.init.body, XML);
});

test("each tables method refuses an answer that is not its document", async () => {
  const { fetch } = recordingFetch(() => ({ format: "diggs-diff", changes: [] }));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  await assert.rejects(client.tablesToDiggs(ZIP), PydiggsHttpError);
  await assert.rejects(client.checkTables(ZIP), PydiggsHttpError);
  await assert.rejects(client.tablesRoundTrip(ZIP), PydiggsHttpError);
  await assert.rejects(client.tablesFromDiggs(XML), PydiggsHttpError);
});

test("conversionOutput reads a tables result, checked against its sha256", async () => {
  const good = { ...TO_DIGGS, output_sha256: await sha256(XML) };
  assert.equal(new TextDecoder().decode(await conversionOutput(good)), XML);
  await assert.rejects(conversionOutput(TO_DIGGS), /sha256/);
  const zip = { ...FROM_DIGGS, output_sha256: await sha256("PK") };
  assert.equal(new TextDecoder().decode(await conversionOutput(zip)), "PK");
});

// --- the Fetch handler, and the HTTP client against it ------------------------------------

function engine(fail?: "document") {
  const calls: Record<string, unknown>[] = [];
  const record =
    (method: string, answer: unknown) =>
    async (input: Uint8Array, options: Record<string, unknown> = {}) => {
      const { signal: _signal, ...rest } = options;
      calls.push({ method, size: input.byteLength, ...rest });
      if (fail === "document") {
        throw Object.assign(new Error("x"), {
          problem: { code: "invalid-document", detail: "site.zip is not a ZIP file.", kind: "not-zip" },
        });
      }
      return answer;
    };
  const value = {
    status: "ready",
    pending: 0,
    tablesToDiggs: record("tablesToDiggs", TO_DIGGS),
    checkTables: record("checkTables", FINDINGS),
    tablesRoundTrip: record("tablesRoundTrip", ROUND_TRIP),
    tablesFromDiggs: record("tablesFromDiggs", FROM_DIGGS),
  } as unknown as PydiggsEngine;
  return { value, calls };
}

const post = (path: string, body: BodyInit, type?: string, headers: Record<string, string> = {}): Request =>
  new Request(`http://app.test${path}`, {
    method: "POST",
    body,
    headers: { ...(type ? { "Content-Type": type } : {}), ...headers },
  });

test("the handler serves the four tables routes from the engine", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  const answers = [
    await handle(post("/v1/tables/to-diggs?name=dir/site.zip", ZIP, "application/zip")),
    await handle(post("/v1/tables/check", ZIP, "application/x-zip-compressed")),
    await handle(post("/v1/tables/round-trip", ZIP, "application/zip")),
    await handle(post("/v1/tables/from-diggs", XML, "application/xml")),
  ];
  assert.deepEqual(
    answers.map((response) => [response.status, response.headers.get("Content-Type")]),
    Array.from({ length: 4 }, () => [200, "application/json"]),
  );
  assert.deepEqual(await answers[0]!.json(), TO_DIGGS);
  assert.deepEqual(await answers[1]!.json(), FINDINGS);
  const limit = 4 * 25 * 1024 * 1024;
  assert.deepEqual(calls, [
    { method: "tablesToDiggs", size: 4, name: "site.zip", maxResultBytes: limit },
    { method: "checkTables", size: 4 }, // findings are not held to the limit
    { method: "tablesRoundTrip", size: 4, maxResultBytes: limit },
    { method: "tablesFromDiggs", size: 8, maxResultBytes: limit },
  ]);
  // A multipart upload names the package by its file name, as in pyDIGGS.
  const form = new FormData();
  form.append("file", new Blob([ZIP]), "export.zip");
  assert.equal((await handle(new Request("http://app.test/v1/tables/to-diggs", { method: "POST", body: form }))).status, 200);
  assert.equal(calls.at(-1)!.name, "export.zip");
});

test("the handler refuses what pyDIGGS refuses on /v1/tables/*", async () => {
  const handle = createPydiggsHandler(engine().value, { maxBodyBytes: 100 });
  const answer = async (request: Request) => {
    const response = await handle(request);
    const body = (await response.json()) as { code: string; detail: string };
    return [response.status, body.code, body.detail];
  };
  assert.deepEqual(await answer(post("/v1/tables/to-diggs", XML, "application/xml")), [
    415,
    "unsupported-media-type",
    "Send application/zip, application/x-zip-compressed or multipart/form-data.",
  ]);
  assert.deepEqual((await answer(post("/v1/tables/from-diggs", ZIP, "application/zip"))).slice(0, 2), [415, "unsupported-media-type"]);
  assert.deepEqual((await answer(post("/v1/tables/check?checks=schema", ZIP, "application/zip"))).slice(0, 2), [422, "invalid-parameter"]);
  assert.deepEqual((await answer(post("/v1/tables/check", new Uint8Array(101), "application/zip"))).slice(0, 2), [413, "input-too-large"]);
  assert.deepEqual((await answer(post("/v1/tables/check", new Uint8Array(0), "application/zip"))).slice(0, 2), [400, "missing-input"]);
  assert.deepEqual(
    await answer(post("/v1/tables/to-diggs", ZIP, "application/zip", { Accept: "application/sarif+json" })),
    [406, "not-acceptable", "Accept application/json."],
  );
  assert.deepEqual(
    await answer(post("/v1/tables/from-diggs", XML, "application/xml", { Accept: "text/html" })),
    [406, "not-acceptable", "Accept application/json (the tables as a package ZIP, in base64)."],
  );
  const get = await handle(new Request("http://app.test/v1/tables/check"));
  assert.equal(get.status, 405);
  assert.equal(get.headers.get("Allow"), "POST");
  assert.equal((await handle(post("/v1/tables/unknown", ZIP, "application/zip"))).status, 404);
});

test("a name with no base is no name on the tables routes, so pyDIGGS names the package", async () => {
  // pyDIGGS reads "", "folder/", "." and ".." as no name: tables.zip, or input.xml for from-diggs.
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  for (const name of ["", "folder/", "dir%5C", ".", "a/.."]) {
    assert.equal((await handle(post(`/v1/tables/to-diggs?name=${name}`, ZIP, "application/zip"))).status, 200);
    assert.equal((await handle(post(`/v1/tables/from-diggs?name=${name}`, XML, "application/xml"))).status, 200);
  }
  // A multipart file part with an empty filename (written out: FormData drops an empty
  // one), and an empty name field over a filename.
  const unnamed =
    '--b\r\nContent-Disposition: form-data; name="file"; filename=""\r\nContent-Type: application/zip\r\n\r\nPK\r\n--b--\r\n';
  assert.equal((await handle(post("/v1/tables/check", unnamed, "multipart/form-data; boundary=b"))).status, 200);
  const emptied = new FormData();
  emptied.append("file", new File([ZIP], "export.zip"));
  emptied.append("name", "");
  assert.equal((await handle(new Request("http://app.test/v1/tables/check", { method: "POST", body: emptied }))).status, 200);
  assert.equal(calls.length, 12);
  for (const call of calls) assert.ok(!("name" in call), JSON.stringify(call));
});

test("the handler holds a tables answer to four times maxBodyBytes, but not the findings of a check", async () => {
  const big = { ...TO_DIGGS, output_base64: "A".repeat(500) };
  const findings = { ...FINDINGS, files: Array.from({ length: 50 }, () => ({ path: "x".repeat(20) })) };
  const seen: unknown[] = [];
  const value = {
    status: "ready",
    pending: 0,
    tablesToDiggs: async () => big,
    checkTables: async (_input: Uint8Array, options: { maxResultBytes?: number }) => (seen.push(options.maxResultBytes), findings),
  } as unknown as PydiggsEngine;
  const checked = await createPydiggsHandler(value, { maxBodyBytes: 100 })(post("/v1/tables/check", ZIP, "application/zip"));
  assert.equal(checked.status, 200, "pyDIGGS does not hold a check's findings to the limit");
  assert.ok((await checked.text()).length > 400);
  assert.deepEqual(seen, [undefined]);
  const response = await createPydiggsHandler(value, { maxBodyBytes: 100 })(post("/v1/tables/to-diggs", ZIP, "application/zip"));
  assert.equal(response.status, 422);
  assert.equal(((await response.json()) as { code: string }).code, "result-too-large");
  const roomy = createPydiggsHandler(value, { maxBodyBytes: 1000 });
  assert.equal((await roomy(post("/v1/tables/to-diggs", ZIP, "application/zip"))).status, 200);
});

test("an engine without the tables methods answers 404 on /v1/tables/*", async () => {
  const value = { status: "ready", pending: 0 } as unknown as PydiggsEngine;
  const handle = createPydiggsHandler(value);
  for (const path of ["to-diggs", "check", "round-trip"]) {
    assert.equal((await handle(post(`/v1/tables/${path}`, ZIP, "application/zip"))).status, 404);
  }
  assert.equal((await handle(post("/v1/tables/from-diggs", XML, "application/xml"))).status, 404);
});

test("PydiggsHttpClient's tables methods work against the handler, and a refusal keeps its kind", async () => {
  const { value, calls } = engine();
  const fetchFor = (handler: (request: Request) => Promise<Response>) =>
    (async (input: RequestInfo | URL, init?: RequestInit) =>
      handler(new Request(new URL(String(input), "http://app.test"), init))) as typeof globalThis.fetch;
  const client = new PydiggsHttpClient({ baseUrl: "/api", fetch: fetchFor(createPydiggsHandler(value, { basePath: "/api" })) });
  assert.deepEqual(await client.tablesToDiggs(ZIP, { name: "site.zip" }), TO_DIGGS);
  assert.deepEqual(await client.checkTables(ZIP), FINDINGS);
  assert.deepEqual(await client.tablesRoundTrip(ZIP), ROUND_TRIP);
  assert.deepEqual(await client.tablesFromDiggs(XML), FROM_DIGGS);
  assert.deepEqual(
    calls.map((call) => [call.method, call.name]),
    [
      ["tablesToDiggs", "site.zip"],
      ["checkTables", undefined],
      ["tablesRoundTrip", undefined],
      ["tablesFromDiggs", undefined],
    ],
  );

  const refusing = new PydiggsHttpClient({
    baseUrl: "/api",
    fetch: fetchFor(createPydiggsHandler(engine("document").value, { basePath: "/api" })),
  });
  const error = await refusing.tablesToDiggs(ZIP, { name: "site.zip" }).catch((caught: unknown) => caught);
  assert.ok(error instanceof PydiggsHttpError);
  assert.equal(error.status, 422);
  assert.equal(error.code, "invalid-document");
  assert.equal(error.problem?.kind, "not-zip");
});

// --- the worker: page side --------------------------------------------------------------

class FakeWorker implements WorkerLike {
  onmessage: ((event: MessageEvent) => unknown) | null = null;
  onerror: ((event: ErrorEvent) => unknown) | null = null;
  posted: { message: any; transfer: Transferable[] | undefined }[] = [];
  postMessage(message: unknown, transfer?: Transferable[]): void {
    this.posted.push({ message, transfer });
  }
  terminate(): void {}
  reply(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }
}

test("PydiggsBrowser sends each tables request with its operation, and leaves an unnamed package to pyDIGGS", async () => {
  const worker = new FakeWorker();
  const client = new PydiggsBrowser({ createWorker: () => worker, maxBytes: 100 });
  const answers = [
    client.tablesToDiggs(new File([ZIP], "site.zip"), { maxResultBytes: 1000 }),
    client.checkTables(ZIP),
    client.tablesRoundTrip(ZIP, { name: "named.zip" }),
    client.tablesFromDiggs(new TextEncoder().encode(XML)),
  ];
  // One request at a time: answer each as it arrives.
  const documents = [TO_DIGGS, FINDINGS, ROUND_TRIP, FROM_DIGGS];
  const seen: any[] = [];
  for (let i = 0; i < 4; i++) {
    await tick();
    await tick();
    const { message, transfer } = worker.posted.at(-1)!;
    seen.push(message);
    assert.deepEqual(transfer, [message.data]);
    worker.reply({ id: message.id, type: "result", document: documents[i] });
    await answers[i];
  }
  assert.deepEqual(
    seen.map((message) => [message.type, message.name, message.parameters, message.data.byteLength]),
    [
      ["tables", "site.zip", { operation: "to-diggs", max_result_bytes: 1000 }, 4],
      ["tables", undefined, { operation: "check" }, 4],
      ["tables", "named.zip", { operation: "round-trip" }, 4],
      ["tables-from-diggs", undefined, undefined, 8],
    ],
  );
  assert.deepEqual(await Promise.all(answers), documents);
});

test("maxResultBytes must be a positive whole number, checked before anything is sent", async () => {
  const worker = new FakeWorker();
  const client = new PydiggsBrowser({ createWorker: () => worker, maxBytes: 100 });
  // Infinity would reach pyDIGGS as JSON null, which there means no limit at all.
  for (const maxResultBytes of [Number.POSITIVE_INFINITY, Number.NaN, 0, -1, 1.5, 2 ** 53]) {
    for (const call of [
      client.tablesToDiggs(ZIP, { maxResultBytes }),
      client.checkTables(ZIP, { maxResultBytes }),
      client.tablesRoundTrip(ZIP, { maxResultBytes }),
      client.tablesFromDiggs(ZIP, { maxResultBytes }),
    ]) {
      const error = (await call.catch((caught: unknown) => caught)) as { problem?: { code: string; errors?: unknown } };
      assert.equal(error.problem?.code, "invalid-parameter", String(maxResultBytes));
      assert.deepEqual(error.problem?.errors, [{ parameter: "max_result_bytes", problem: "must be a positive integer" }]);
    }
  }
  await tick();
  assert.equal(worker.posted.length, 1, "nothing was sent");
  // The worker is still in service, and a valid limit goes on.
  void client.tablesToDiggs(ZIP, { maxResultBytes: 1 });
  await tick();
  await tick();
  assert.deepEqual(worker.posted[1]!.message.parameters, { operation: "to-diggs", max_result_bytes: 1 });
});

test("the handler answers an engine's invalid-parameter refusal as 422", async () => {
  const worker = new FakeWorker();
  const browser = new PydiggsBrowser({ createWorker: () => worker });
  const value = {
    status: "ready",
    pending: 0,
    tablesToDiggs: (input: Uint8Array) => browser.tablesToDiggs(input, { maxResultBytes: Number.POSITIVE_INFINITY }),
  } as unknown as PydiggsEngine;
  const response = await createPydiggsHandler(value)(post("/v1/tables/to-diggs", ZIP, "application/zip"));
  assert.equal(response.status, 422);
  assert.deepEqual(((await response.json()) as { errors: unknown }).errors, [
    { parameter: "max_result_bytes", problem: "must be a positive integer" },
  ]);
});

test("PydiggsBrowser holds a package to maxBytes before sending it", async () => {
  const worker = new FakeWorker();
  const client = new PydiggsBrowser({ createWorker: () => worker, maxBytes: 10 });
  const error = await client.tablesToDiggs(new Uint8Array(11)).catch((caught: unknown) => caught);
  assert.equal((error as { problem?: { code: string } }).problem?.code, "input-too-large");
  await tick();
  assert.equal(worker.posted.length, 1, "nothing was sent");
});

// --- the worker: pyDIGGS side -------------------------------------------------------------

test("the worker core passes tables requests to the glue, held to the limit", async () => {
  const log: string[] = [];
  const replies: ReplyMessage[] = [];
  const callable = (fn: (...args: any[]) => unknown): PyCallable =>
    Object.assign(fn, { callKwargs: async (...args: unknown[]) => fn(...args) });
  const pyodide: PyodideLike = {
    version: "314.0.7",
    loadPackage: async () => {},
    pyimport: () => ({}),
    runPython: () => {},
    globals: {
      get: (name: string) =>
        name === "versions"
          ? JSON.stringify({ pydiggs: "1.17.0", lxml: "6.1.3" })
          : callable((kind: string, data: Uint8Array, name: string, max: number, parameters: string) => {
              log.push(`${kind} [${name}] ${data.byteLength} ${max} ${parameters}`);
              return `D${JSON.stringify(TO_DIGGS)}`;
            }),
    },
    unpackArchive: () => {},
    FS: { mkdirTree: () => {}, writeFile: () => {}, readFile: () => new Uint8Array() },
  };
  const handle = createWorkerCore({
    post: (message) => void (message.type !== "progress" && replies.push(message)),
    loadPyodide: async () => pyodide,
    pin: null,
    fetch: (async () => new Response("")) as unknown as typeof globalThis.fetch,
  });
  handle({ type: "init", maxBytes: 100, pydiggsWheelUrl: "emfs:/pydiggs-1.17.0-py3-none-any.whl" });
  handle({ id: 1, type: "tables", name: "site.zip", data: new ArrayBuffer(4), parameters: { operation: "check" } });
  handle({ id: 2, type: "tables", data: new ArrayBuffer(4), parameters: { operation: "to-diggs" } });
  handle({ id: 3, type: "tables-from-diggs", data: new ArrayBuffer(8) });
  handle({ id: 4, type: "tables", data: new ArrayBuffer(101) });
  handle({ id: 5, type: "tables-from-diggs" }); // no file: dropped
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.deepEqual(log, [
    'tables [site.zip] 4 100 {"operation":"check"}',
    'tables [] 4 100 {"operation":"to-diggs"}',
    "tables-from-diggs [] 8 100 ",
  ]);
  assert.deepEqual(
    replies.slice(1).map((reply) => [reply.type, "id" in reply ? reply.id : 0]),
    [
      ["result", 1],
      ["result", 2],
      ["result", 3],
      ["error", 4],
    ],
  );
  assert.equal((replies[4] as { problem?: { code: string } }).problem?.code, "input-too-large");
});

// --- the pool and the hybrid ------------------------------------------------------------

function poolEngine(log: string[], withTables = true): PoolEngine {
  const engine = {
    status: "ready",
    pending: 0,
    ready: Promise.resolve({ pyodide: "p", pydiggs: "d", lxml: "l" }),
    terminate() {},
  } as unknown as PoolEngine;
  if (withTables) {
    engine.tablesToDiggs = async (_input, options) => (log.push(`tablesToDiggs ${options?.name}`), TO_DIGGS);
    engine.checkTables = async (_input, options) => (log.push(`checkTables ${options?.name}`), FINDINGS);
    engine.tablesRoundTrip = async (_input, options) => (log.push(`tablesRoundTrip ${options?.name}`), ROUND_TRIP);
    engine.tablesFromDiggs = async (_input, options) => (log.push(`tablesFromDiggs ${options?.name}`), FROM_DIGGS);
  }
  return engine;
}

test("a pool runs the tables methods on an engine, and says so when its engines cannot", async () => {
  const log: string[] = [];
  const pool = new PydiggsPool(() => poolEngine(log), { size: 2 });
  assert.equal((await pool.tablesToDiggs(ZIP, { name: "a.zip" })).output_name, "site.xml");
  assert.equal((await pool.checkTables(ZIP)).format, "diggs-findings");
  assert.equal((await pool.tablesRoundTrip(ZIP)).ok, true);
  assert.equal((await pool.tablesFromDiggs(ZIP, { name: "b.xml" })).media_type, "application/zip");
  assert.deepEqual(log, ["tablesToDiggs a.zip", "checkTables undefined", "tablesRoundTrip undefined", "tablesFromDiggs b.xml"]);
  const old = new PydiggsPool(() => poolEngine(log, false), { size: 1 });
  await assert.rejects(old.tablesToDiggs(ZIP), /no tablesToDiggs/);
  await assert.rejects(old.tablesFromDiggs(ZIP), /no tablesFromDiggs/);
});

test("the hybrid runs the tables methods in the browser, and on the server when it must", async () => {
  const log: string[] = [];
  const browser = poolEngine(log);
  const server = {
    tablesToDiggs: async (_input: unknown, options: { name?: string }) => (log.push(`server ${options.name}`), TO_DIGGS),
    tablesFromDiggs: async (_input: unknown, options: { name?: string }) => (log.push(`server ${options.name}`), FROM_DIGGS),
  } as unknown as PydiggsHttpClient;
  const hybrid = new PydiggsHybrid({ browser, server, maxBrowserBytes: 10 });

  const small = await hybrid.tablesToDiggs(new File([ZIP], "site.zip"));
  assert.deepEqual([small.via, small.reason, small.document.output_name], ["browser", "browser", "site.xml"]);
  assert.equal((await hybrid.checkTables(ZIP)).via, "browser");
  assert.equal((await hybrid.tablesRoundTrip(ZIP)).via, "browser");
  const large = await hybrid.tablesFromDiggs(new Uint8Array(11), { name: "big.xml" });
  assert.deepEqual([large.via, large.reason], ["server", "too-large"]);
  assert.deepEqual(log, ["tablesToDiggs site.zip", "checkTables undefined", "tablesRoundTrip undefined", "server big.xml"]);

  // A browser engine of your own without the tables methods sends them to the server.
  const plain = new PydiggsHybrid({ browser: poolEngine([], false), server });
  const routed = await plain.tablesToDiggs(ZIP, { name: "x.zip" });
  assert.deepEqual([routed.via, routed.reason], ["server", "no-browser"]);

  // A PydiggsPool always has the methods, so a pool of such engines is asked, and its
  // refusal is the answer: nothing goes to the server, as with diff.
  const before = log.length;
  const pooled = new PydiggsHybrid({ browser: new PydiggsPool(() => poolEngine([], false), { size: 1 }), server });
  await assert.rejects(pooled.tablesToDiggs(ZIP), /this pool's engines have no tablesToDiggs/);
  await assert.rejects(pooled.diff(ZIP, ZIP), /cannot compare/);
  assert.equal(log.length, before, "the server was not asked");
});

test("a package is bytes: the HTTP client's types refuse a string where a ZIP goes", () => {
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch: (async () => new Response("{}")) as typeof globalThis.fetch });
  const never = (): void => {
    // @ts-expect-error a ZIP is not text
    void client.tablesToDiggs("PK");
    // @ts-expect-error a ZIP is not text
    void client.checkTables("PK");
    // @ts-expect-error a ZIP is not text
    void client.tablesRoundTrip("PK");
    void client.tablesFromDiggs("<Diggs/>"); // a DIGGS file may be text
  };
  assert.equal(typeof never, "function"); // held by `npm run typecheck`
});
