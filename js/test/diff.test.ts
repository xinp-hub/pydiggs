// `diff` on every surface of the client: the HTTP client (`POST /v1/diff`), the
// worker engines (PydiggsBrowser, and the worker core that runs pyDIGGS), the pool,
// the hybrid and the Fetch handler. tests/pyodide/worker-smoke.mjs runs the real glue.

import assert from "node:assert/strict";
import { test } from "node:test";

import { PydiggsBrowser, type WorkerLike } from "../src/browser.ts";
import type { DiggsDiffDocument } from "../src/generated/diff.ts";
import { createPydiggsHandler, type PydiggsEngine } from "../src/handler.ts";
import { PydiggsHttpClient, PydiggsHttpError } from "../src/http.ts";
import { PydiggsHybrid } from "../src/hybrid.ts";
import { PydiggsPool, type PoolEngine } from "../src/pool.ts";
import type { ReplyMessage } from "../src/protocol.ts";
import { createWorkerCore, type PyCallable, type PyodideLike } from "../src/worker-core.ts";

const DIFF = {
  $schema: "https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json",
  format: "diggs-diff",
  schema_version: "1.0",
  identical: true,
  complete: true,
  changes: [],
} as unknown as DiggsDiffDocument;

const tick = () => new Promise((resolve) => setImmediate(resolve));

// --- HTTP client ------------------------------------------------------------------------

test("diff posts both files as multipart parts old and new, with the options as fields", async () => {
  const seen: { url: URL; init: RequestInit }[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    seen.push({ url: new URL(String(input)), init: init ?? {} });
    return new Response(JSON.stringify(DIFF), { headers: { "Content-Type": "application/json" } });
  }) as typeof globalThis.fetch;
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  const document = await client.diff("<Diggs/>", new TextEncoder().encode("<Diggs />"), {
    oldName: "v1.xml",
    match: "gml_id",
    relTol: 1e-6,
    absTol: 0.001,
    maxChanges: 50,
    showReformatted: true,
    showRenamed: false,
  });
  assert.equal(document.format, "diggs-diff");
  const [request] = seen;
  assert.equal(request.url.href, "http://h/v1/diff");
  assert.equal(request.init.method, "POST");
  assert.equal(new Headers(request.init.headers).get("Content-Type"), null, "FormData sets its own");
  const form = request.init.body as FormData;
  assert.equal((form.get("old") as File).name, "v1.xml");
  assert.equal((form.get("new") as File).name, "input.xml");
  assert.equal(await (form.get("new") as File).text(), "<Diggs />");
  assert.deepEqual(
    Object.fromEntries([...form.entries()].filter(([, value]) => typeof value === "string")),
    {
      match: "gml_id",
      rel_tol: "0.000001",
      abs_tol: "0.001",
      max_changes: "50",
      show_reformatted: "true",
      show_renamed: "false",
    },
  );
});

test("diff sends no option it was not given, and refuses an answer that is not a diff", async () => {
  const forms: FormData[] = [];
  const fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    forms.push(init?.body as FormData);
    return new Response(JSON.stringify({ format: "diggs-findings", files: [] }), {
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof globalThis.fetch;
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  await assert.rejects(client.diff("<a/>", "<b/>"), PydiggsHttpError);
  assert.deepEqual([...forms[0]!.keys()], ["old", "new"]);
});

// --- the Fetch handler, and the HTTP client against it ------------------------------------

function engine(fail?: "document") {
  const calls: Record<string, unknown>[] = [];
  const value = {
    status: "ready",
    pending: 0,
    async diff(oldInput: Uint8Array, newInput: Uint8Array, options: Record<string, unknown> = {}) {
      const { signal: _signal, ...rest } = options;
      calls.push({ sizes: [oldInput.byteLength, newInput.byteLength], ...rest });
      if (fail === "document") {
        throw Object.assign(new Error("x"), {
          problem: {
            code: "invalid-document",
            detail: "The new file: The root element is not a DIGGS Diggs element.",
            errors: [{ parameter: "new", problem: "is not a DIGGS document" }],
            kind: "not-diggs",
          },
        });
      }
      return DIFF;
    },
  } as unknown as PydiggsEngine;
  return { value, calls };
}

const multipart = (parts: [string, string | Blob, string?][], query = ""): Request => {
  const form = new FormData();
  for (const [key, value, name] of parts) {
    if (typeof value === "string") form.append(key, value);
    else form.append(key, value, name);
  }
  return new Request(`http://app.test/v1/diff${query}`, { method: "POST", body: form });
};

test("the handler serves /v1/diff from the engine, parsing options as pyDIGGS does", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  const response = await handle(
    multipart(
      [
        ["old", new Blob(["<Diggs/>"]), "dir/a.xml"],
        ["new", new Blob(["<Diggs />"]), "b.xml"],
        ["max_changes", "5"],
        ["rel_tol", "1e-3"],
        ["show_renamed", "true"],
        ["match", "natural"],
      ],
      "?new_name=renamed.xml",
    ),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), DIFF);
  assert.deepEqual(calls, [
    {
      sizes: [8, 9],
      match: "natural",
      relTol: 0.001,
      maxChanges: 5,
      showRenamed: true,
      oldName: "a.xml",
      newName: "renamed.xml",
      maxResultBytes: 4 * 25 * 1024 * 1024,
    },
  ]);
  // Text that is not a value goes on as text, for pyDIGGS to refuse with its fixed text.
  await handle(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])], ["rel_tol", "nan"], ["max_changes", " 5"]]));
  assert.deepEqual([calls[1]!.relTol, calls[1]!.maxChanges], ["nan", " 5"]);
});

test("the handler refuses what pyDIGGS refuses on /v1/diff", async () => {
  const handle = createPydiggsHandler(engine().value, { maxBodyBytes: 100 });
  const status = async (request: Request) => {
    const response = await handle(request);
    return [response.status, ((await response.json()) as { code: string }).code];
  };
  assert.deepEqual(await status(multipart([["old", new Blob(["a"])]])), [400, "missing-input"]);
  assert.deepEqual(
    await status(multipart([["old", new Blob(["a"])], ["old", new Blob(["b"])], ["new", new Blob(["c"])]])),
    [400, "invalid-multipart"],
  );
  assert.deepEqual(
    await status(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])], ["checks", "schema"]])),
    [400, "invalid-multipart"],
  );
  assert.deepEqual(
    await status(new Request("http://app.test/v1/diff", { method: "POST", body: "<Diggs/>", headers: { "Content-Type": "application/xml" } })),
    [415, "unsupported-media-type"],
  );
  // Each file is held to maxBodyBytes on its own: two of 90 bytes pass, one of 101 does not.
  const ok = await handle(multipart([["old", new Blob(["x".repeat(90)])], ["new", new Blob(["y".repeat(90)])]]));
  assert.equal(ok.status, 200);
  assert.deepEqual(
    await status(multipart([["old", new Blob(["x".repeat(101)])], ["new", new Blob(["y"])]])),
    [413, "input-too-large"],
  );
  assert.deepEqual(
    await status(new Request("http://app.test/v1/diff", { method: "GET" })),
    [405, "method-not-allowed"],
  );
  // An option sent as a file part is refused, not dropped in favour of the default.
  assert.deepEqual(
    await status(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])], ["max_changes", new Blob(["5"]), "m.txt"]])),
    [400, "invalid-multipart"],
  );
  // The form wins, but a query value it overrides must still be valid, as in pyDIGGS.
  const overridden = await handle(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])], ["match", "auto"]], "?match=fuzzy"));
  assert.equal(overridden.status, 422);
  assert.deepEqual(((await overridden.json()) as { errors: unknown }).errors, [
    { parameter: "match", problem: "expected one of auto, gml-id, natural" },
  ]);
  const valid = await handle(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])], ["rel_tol", "0.1"]], "?rel_tol=0.2"));
  assert.equal(valid.status, 200);
});

test("the handler holds a diff answer to four times maxBodyBytes, as pyDIGGS's endpoint does", async () => {
  const change = { kind: "changed", path: "x".repeat(40) };
  const big = { ...DIFF, identical: false, changes: Array.from({ length: 20 }, () => change) };
  const handle = createPydiggsHandler({ diff: async () => big } as unknown as PydiggsEngine, { maxBodyBytes: 100 });
  const response = await handle(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])]]));
  assert.equal(response.status, 422);
  assert.equal(((await response.json()) as { code: string }).code, "result-too-large");
  const roomy = createPydiggsHandler({ diff: async () => big } as unknown as PydiggsEngine, { maxBodyBytes: 1000 });
  assert.equal((await roomy(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])]]))).status, 200);
});

test("PydiggsHttpClient.diff works against the handler, and a refusal names the file", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value, { basePath: "/api" });
  const fetchFor = (handler: (request: Request) => Promise<Response>) =>
    (async (input: RequestInfo | URL, init?: RequestInit) =>
      handler(new Request(new URL(String(input), "http://app.test"), init))) as typeof globalThis.fetch;
  const client = new PydiggsHttpClient({ baseUrl: "/api", fetch: fetchFor(handle) });
  assert.equal((await client.diff("<a/>", "<b/>", { maxChanges: 3, newName: "n.xml" })).identical, true);
  assert.deepEqual(calls[0], {
    sizes: [4, 4],
    maxChanges: 3,
    oldName: "input.xml",
    newName: "n.xml",
    maxResultBytes: 4 * 25 * 1024 * 1024,
  });

  const refusing = new PydiggsHttpClient({
    baseUrl: "/api",
    fetch: fetchFor(createPydiggsHandler(engine("document").value, { basePath: "/api" })),
  });
  const error = await refusing.diff("<Diggs/>", "<html/>").catch((caught: unknown) => caught);
  assert.ok(error instanceof PydiggsHttpError);
  assert.equal(error.code, "invalid-document");
  assert.deepEqual(error.problem?.errors, [{ parameter: "new", problem: "is not a DIGGS document" }]);
  assert.equal(error.problem?.kind, "not-diggs");
});

test("an engine without diff answers 404 on /v1/diff", async () => {
  const value = { status: "ready", pending: 0 } as unknown as PydiggsEngine;
  const response = await createPydiggsHandler(value)(multipart([["old", new Blob(["a"])], ["new", new Blob(["b"])]]));
  assert.equal(response.status, 404);
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

test("PydiggsBrowser.diff transfers copies of both files with the options in pyDIGGS's names", async () => {
  const worker = new FakeWorker();
  const client = new PydiggsBrowser({ createWorker: () => worker, maxBytes: 100 });
  const oldBytes = new TextEncoder().encode("<Diggs/>");
  const promise = client.diff(new File([oldBytes], "v1.xml"), new Blob(["<Diggs />"]), {
    maxChanges: 7,
    match: "gml-id",
    showReformatted: true,
  });
  await tick();
  const { message, transfer } = worker.posted[1]!;
  assert.equal(message.type, "diff");
  assert.equal(message.name, "v1.xml");
  assert.equal(message.newName, "input.xml");
  assert.equal(new TextDecoder().decode(message.data), "<Diggs/>");
  assert.equal(new TextDecoder().decode(message.newData), "<Diggs />");
  assert.deepEqual(message.parameters, { match: "gml-id", max_changes: 7, show_reformatted: true });
  assert.deepEqual(transfer, [message.data, message.newData]);
  worker.reply({ id: message.id, type: "result", document: DIFF });
  assert.deepEqual(await promise, DIFF);
});

test("PydiggsBrowser.diff holds each file to maxBytes on its own", async () => {
  const worker = new FakeWorker();
  const client = new PydiggsBrowser({ createWorker: () => worker, maxBytes: 10 });
  const error = await client.diff(new Uint8Array(4), new Uint8Array(11)).catch((caught: unknown) => caught);
  assert.equal((error as { problem?: { code: string } }).problem?.code, "input-too-large");
  await tick();
  assert.equal(worker.posted.length, 1, "nothing was sent");
  void client.diff(new Uint8Array(10), new Uint8Array(10));
  await tick();
  assert.equal(worker.posted[1]!.message.type, "diff");
});

// --- the worker: pyDIGGS side -------------------------------------------------------------

test("the worker core passes both files to the glue, each held to the limit", async () => {
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
          ? JSON.stringify({ pydiggs: "1.13.0", lxml: "6.1.3" })
          : callable(
              (kind: string, data: Uint8Array, name: string, max: number, parameters: string, _d: unknown, newData?: Uint8Array, newName?: string) => {
                log.push(`${kind} ${name} ${data.byteLength} ${newName} ${newData?.byteLength} ${max} ${parameters}`);
                return `D${JSON.stringify(DIFF)}`;
              },
            ),
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
  handle({ type: "init", maxBytes: 100, pydiggsWheelUrl: "emfs:/pydiggs-1.13.0-py3-none-any.whl" });
  handle({ id: 1, type: "diff", name: "a.xml", data: new ArrayBuffer(4), newData: new ArrayBuffer(5), parameters: { max_changes: 3 } });
  handle({ id: 2, type: "diff", data: new ArrayBuffer(4), newData: new ArrayBuffer(101) });
  handle({ id: 3, type: "diff", data: new ArrayBuffer(4) }); // no new file: dropped
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.deepEqual(log, ['diff a.xml 4 input.xml 5 100 {"max_changes":3}']);
  assert.deepEqual(
    replies.slice(1).map((reply) => [reply.type, "id" in reply ? reply.id : 0]),
    [
      ["result", 1],
      ["error", 2],
    ],
  );
  assert.equal((replies[2] as { problem?: { code: string } }).problem?.code, "input-too-large");
});

// --- the pool and the hybrid ------------------------------------------------------------

function poolEngine(log: string[], withDiff = true): PoolEngine {
  const engine = {
    status: "ready",
    pending: 0,
    ready: Promise.resolve({ pyodide: "p", pydiggs: "d", lxml: "l" }),
    terminate() {},
  } as unknown as PoolEngine;
  if (withDiff) {
    engine.diff = async (_old, _new, options) => {
      log.push(`diff ${options?.oldName} ${options?.maxChanges}`);
      return DIFF;
    };
  }
  return engine;
}

test("a pool compares on an engine, and says so when its engines cannot", async () => {
  const log: string[] = [];
  const pool = new PydiggsPool(() => poolEngine(log), { size: 2 });
  assert.equal((await pool.diff(new Uint8Array(1), new Uint8Array(1), { oldName: "a.xml", maxChanges: 2 })).format, "diggs-diff");
  assert.deepEqual(log, ["diff a.xml 2"]);
  const old = new PydiggsPool(() => poolEngine(log, false), { size: 1 });
  await assert.rejects(old.diff(new Uint8Array(1), new Uint8Array(1)), /cannot compare/);
});

test("the hybrid routes the pair by the larger file, and both go the same way", async () => {
  const calls: string[] = [];
  const browser = {
    status: "ready",
    ready: Promise.resolve({}),
    validate: async () => ({}),
    read: async () => ({}),
    diff: async (_o: unknown, _n: unknown, options: { oldName?: string; newName?: string }) => {
      calls.push(`browser ${options.oldName} ${options.newName}`);
      return DIFF;
    },
  };
  const server = {
    diff: async (_o: unknown, _n: unknown, options: { oldName?: string; newName?: string; maxChanges?: number }) => {
      calls.push(`server ${options.oldName} ${options.newName} ${options.maxChanges}`);
      return DIFF;
    },
  };
  const hybrid = new PydiggsHybrid({
    browser: browser as never,
    server: server as unknown as PydiggsHttpClient,
    maxBrowserBytes: 10,
  });
  const small = await hybrid.diff(new File([new Uint8Array(5)], "a.xml"), new Uint8Array(10));
  assert.deepEqual([small.via, small.reason], ["browser", "browser"]);
  const large = await hybrid.diff(new Uint8Array(3), new File([new Uint8Array(11)], "b.xml"), { maxChanges: 4 });
  assert.deepEqual([large.via, large.reason], ["server", "too-large"]);
  assert.deepEqual(calls, ["browser a.xml input.xml", "server input.xml b.xml 4"]);
  const noDiff = new PydiggsHybrid({
    browser: { ...browser, diff: undefined } as never,
    server: server as unknown as PydiggsHttpClient,
  });
  const result = await noDiff.diff(new Uint8Array(1), new Uint8Array(1));
  assert.deepEqual([result.via, result.reason], ["server", "no-browser"]);
});
