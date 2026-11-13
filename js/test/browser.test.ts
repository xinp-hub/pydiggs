import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_MAX_BYTES, PydiggsBrowser, PydiggsBrowserError, type WorkerLike } from "../src/browser.ts";

// Stands in for the worker: records what the page posts and lets a test reply.
class FakeWorker implements WorkerLike {
  onmessage: ((event: MessageEvent) => unknown) | null = null;
  onerror: ((event: ErrorEvent) => unknown) | null = null;
  posted: { message: any; transfer: Transferable[] | undefined }[] = [];
  terminated = false;

  postMessage(message: unknown, transfer?: Transferable[]): void {
    this.posted.push({ message, transfer });
  }
  terminate(): void {
    this.terminated = true;
  }
  reply(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }
  get requests(): any[] {
    return this.posted.map((entry) => entry.message).filter((message) => message.type !== "init");
  }
}

function start(options: object = {}): { worker: FakeWorker; client: PydiggsBrowser } {
  const worker = new FakeWorker();
  const client = new PydiggsBrowser({ createWorker: () => worker, ...options });
  return { worker, client };
}

const VERSIONS = { pyodide: "314.0.7", pydiggs: "1.7.0", lxml: "6.1.3" };
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("the worker is configured first, and ready resolves with its versions", async () => {
  const { worker, client } = start({
    pyodideBaseUrl: "https://static.test/pyodide",
    pydiggsWheelUrl: new URL("https://static.test/wheels/pydiggs-1.7.0-py3-none-any.whl"),
    maxBytes: 1000,
  });
  const { interruptBuffer, ...init } = worker.posted[0].message;
  assert.deepEqual(init, {
    type: "init",
    maxBytes: 1000,
    pyodideBaseUrl: "https://static.test/pyodide/",
    pydiggsWheelUrl: "https://static.test/wheels/pydiggs-1.7.0-py3-none-any.whl",
  });
  assert.ok(interruptBuffer instanceof SharedArrayBuffer, "Node shares memory, so the worker can be interrupted");
  assert.equal(client.interruptible, true);
  worker.reply({ type: "ready", versions: VERSIONS });
  assert.deepEqual(await client.ready, VERSIONS);
});

test("maxBytes cannot be raised above the default", () => {
  const { worker } = start({ maxBytes: DEFAULT_MAX_BYTES * 2 });
  assert.equal(worker.posted[0].message.maxBytes, DEFAULT_MAX_BYTES);
});

test("validate transfers a copy and resolves with the worker's document", async () => {
  const { worker, client } = start();
  const bytes = new TextEncoder().encode("<Diggs/>");
  const promise = client.validate(bytes, { name: "a.xml" });
  await tick();
  const [request] = worker.requests;
  assert.equal(request.type, "validate");
  assert.equal(request.name, "a.xml");
  assert.equal(new TextDecoder().decode(request.data), "<Diggs/>");
  assert.deepEqual(worker.posted[1].transfer, [request.data]);
  assert.notEqual(request.data, bytes.buffer);
  assert.equal(bytes.byteLength, 8, "the caller's buffer must not be detached");
  worker.reply({ id: request.id, type: "result", document: { format: "diggs-findings" } });
  assert.deepEqual(await promise, { format: "diggs-findings" });
});

test("a view sends only its own bytes", async () => {
  const { worker, client } = start();
  const whole = new TextEncoder().encode("xx<Diggs/>yy");
  void client.validate(whole.subarray(2, 10));
  await tick();
  assert.equal(new TextDecoder().decode(worker.requests[0].data), "<Diggs/>");
});

// Answers each request as it reaches the worker; documents go one at a time.
async function answerAll(worker: FakeWorker, count: number): Promise<void> {
  for (let i = 0; i < count; i++) {
    await tick();
    const request = worker.requests[i];
    assert.ok(request, `request ${i + 1} reached the worker`);
    worker.reply({ id: request.id, type: "result", document: { n: i } });
  }
}

test("read sends the table selection pydiggs's service layer takes, and names a File by its name", async () => {
  const { worker, client } = start();
  const file = new File(["<Diggs/>"], "site.xml");
  const all = Promise.all([
    client.read(file),
    client.read(file, { results: false, name: "other.xml" }),
    client.read(new Blob(["<Diggs/>"]), { results: ["hole-1"], diggsVersion: "2.6" }),
    client.read(file, { results: "none" }),
  ]);
  await answerAll(worker, 4);
  await all;
  const [first, second, third, fourth] = worker.requests;
  assert.deepEqual([first.type, first.name, first.parameters], ["read", "site.xml", undefined]);
  assert.deepEqual([second.name, second.parameters], ["other.xml", { select: { results: "none" } }]);
  assert.equal(third.name, "input.xml");
  assert.deepEqual(third.parameters, { diggs_version: "2.6", select: { results: ["hole-1"] } });
  assert.deepEqual(fourth.parameters, { select: { results: "none" } });
  assert.notEqual(first.id, second.id);
});

test("validate passes checks and the DIGGS version; validateSarif asks for SARIF", async () => {
  const { worker, client } = start();
  const all = Promise.all([
    client.validate(new ArrayBuffer(1), { checks: ["structure", "schema"], diggsVersion: "3.0.0" }),
    client.validateSarif(new ArrayBuffer(1)),
  ]);
  await answerAll(worker, 2);
  await all;
  const [first, second] = worker.requests;
  assert.deepEqual(first.parameters, { checks: ["structure", "schema"], diggs_version: "3.0.0" });
  assert.deepEqual([second.type, second.parameters], ["validate", { format: "sarif" }]);
});

test("versions, listRules and explainRule ask the worker, with no document", async () => {
  const { worker, client } = start();
  const all = Promise.all([client.versions(), client.listRules({ step: "schema" }), client.explainRule("schema.1")]);
  await answerAll(worker, 3);
  assert.deepEqual(await all, [{ n: 0 }, { n: 1 }, { n: 2 }]);
  assert.deepEqual(
    worker.requests.map((r) => [r.type, r.data, r.parameters]),
    [
      ["versions", undefined, undefined],
      ["rules", undefined, { step: "schema" }],
      ["explain", undefined, { rule_id: "schema.1" }],
    ],
  );
});

test("a refusal carries pydiggs's problem", async () => {
  const { worker, client } = start();
  const promise = client.read(new ArrayBuffer(1));
  await tick();
  const problem = { code: "invalid-document", detail: "a.xml is not DIGGS", errors: [], kind: "not-diggs" };
  worker.reply({ id: worker.requests[0].id, type: "error", message: problem.detail, problem });
  await assert.rejects(promise, (error: unknown) => {
    assert.ok(error instanceof PydiggsBrowserError);
    assert.equal(error.code, undefined);
    assert.deepEqual(error.problem, problem);
    return true;
  });
});

test("documents go one at a time, and each reply settles its own document", async () => {
  const { worker, client } = start();
  const first = client.validate(new ArrayBuffer(1));
  const second = client.validate(new ArrayBuffer(1));
  await tick();
  assert.equal(worker.requests.length, 1, "the second waits for the first");
  assert.equal(client.pending, 2);
  worker.reply({ id: worker.requests[0].id, type: "result", document: { n: 1 } });
  assert.deepEqual(await first, { n: 1 });
  await tick();
  worker.reply({ id: worker.requests[1].id, type: "error", message: "not a DIGGS document" });
  await assert.rejects(second, (error: unknown) => {
    assert.ok(error instanceof PydiggsBrowserError);
    assert.equal(error.message, "not a DIGGS document");
    assert.equal(error.code, undefined, "a refused document is not a timeout or a crash");
    return true;
  });
  assert.equal(client.pending, 0);
});

// A worker factory that records every worker made, so a test can see a restart.
function startRestartable(options: object = {}): { workers: FakeWorker[]; client: PydiggsBrowser } {
  const workers: FakeWorker[] = [];
  const client = new PydiggsBrowser({
    createWorker: () => {
      const worker = new FakeWorker();
      workers.push(worker);
      return worker;
    },
    ...options,
  });
  return { workers, client };
}

// A page that is not cross-origin isolated has no SharedArrayBuffer to give the worker.
async function notIsolated<T>(body: () => Promise<T>): Promise<T> {
  const global = globalThis as { crossOriginIsolated?: boolean };
  global.crossOriginIsolated = false;
  try {
    return await body();
  } finally {
    delete global.crossOriginIsolated;
  }
}

test("where the worker cannot be interrupted, a document past timeoutMs replaces it", () => notIsolated(async () => {
  const { workers, client } = startRestartable({ timeoutMs: 100 });
  assert.equal(workers[0]!.posted[0]?.message.interruptBuffer, undefined);
  assert.equal(client.interruptible, false);
  workers[0]!.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  const slow = client.validate(new ArrayBuffer(1));
  const next = client.validate(new ArrayBuffer(2));
  await assert.rejects(slow, (error: unknown) => {
    assert.ok(error instanceof PydiggsBrowserError);
    assert.equal(error.code, "timeout");
    return true;
  });
  assert.equal(workers.length, 2, "the stuck worker was replaced");
  assert.equal(workers[0]!.terminated, true);
  assert.equal(workers[1]!.posted[0]?.message.type, "init", "the new worker is configured like the first");
  assert.equal(client.status, "loading");
  workers[1]!.reply({ type: "ready", versions: VERSIONS });
  await tick();
  const request = workers[1]!.requests[0];
  assert.equal(request.data.byteLength, 2, "the waiting document went to the new worker");
  workers[1]!.reply({ id: request.id, type: "result", document: { ok: true } });
  assert.deepEqual(await next, { ok: true });
  assert.equal(client.restarts, 1);
}));

test("a document past timeoutMs is interrupted, and the worker kept when it stops in time", async () => {
  const { workers, client } = startRestartable({ timeoutMs: 50, interruptGraceMs: 1000 });
  workers[0]!.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  const buffer = new Uint8Array(workers[0]!.posted[0]!.message.interruptBuffer);
  const slow = client.validate(new ArrayBuffer(1));
  const next = client.validate(new ArrayBuffer(2));
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(buffer[0], 2, "SIGINT was written for the worker");
  const [first] = workers[0]!.requests;
  workers[0]!.reply({ id: first.id, type: "error", message: "cancelled", problem: { code: "cancelled" } });
  await assert.rejects(slow, (error: unknown) => {
    assert.ok(error instanceof PydiggsBrowserError);
    assert.equal(error.code, "timeout");
    return true;
  });
  await tick();
  const second = workers[0]!.requests[1];
  assert.equal(second.data.byteLength, 2, "the same worker takes the next document");
  workers[0]!.reply({ id: second.id, type: "result", document: { ok: true } });
  assert.deepEqual(await next, { ok: true });
  assert.equal(workers.length, 1);
  assert.equal(client.restarts, 0);
});

test("an interrupted worker that does not stop within the grace period is replaced", async () => {
  const { workers, client } = startRestartable({ timeoutMs: 30, interruptGraceMs: 30 });
  workers[0]!.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  await assert.rejects(client.validate(new ArrayBuffer(1)), (error: unknown) => {
    assert.equal((error as PydiggsBrowserError).code, "timeout");
    return true;
  });
  assert.equal(workers.length, 2);
  assert.equal(workers[0]!.terminated, true);
  assert.equal(client.restarts, 1);
});

test("aborting a running document interrupts the worker", async () => {
  const { worker, client } = start();
  worker.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  const buffer = new Uint8Array(worker.posted[0]!.message.interruptBuffer);
  const controller = new AbortController();
  const promise = client.validate(new ArrayBuffer(1), { signal: controller.signal });
  await tick();
  controller.abort(new Error("stop"));
  await assert.rejects(promise, /stop/);
  assert.equal(buffer[0], 2);
});

test("an idle worker is stopped after idleTimeoutMs and started again by the next request", async () => {
  const { workers, client } = startRestartable({ idleTimeoutMs: 30 });
  workers[0]!.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  const first = client.validate(new ArrayBuffer(1));
  await tick();
  workers[0]!.reply({ id: workers[0]!.requests[0].id, type: "result", document: { n: 1 } });
  await first;
  assert.equal(client.status, "ready");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(client.status, "idle");
  assert.equal(workers[0]!.terminated, true);
  const again = client.validate(new ArrayBuffer(1));
  await tick();
  assert.equal(workers.length, 2, "a fresh worker was started");
  workers[1]!.reply({ type: "ready", versions: VERSIONS });
  await tick();
  workers[1]!.reply({ id: workers[1]!.requests[0].id, type: "result", document: { n: 2 } });
  assert.deepEqual(await again, { n: 2 });
  assert.equal(client.restarts, 0, "idling is not a restart");
  client.terminate();
});

test("loading does not count against timeoutMs", async () => {
  const { workers, client } = startRestartable({ timeoutMs: 50 });
  const document = client.validate(new ArrayBuffer(1));
  await new Promise((resolve) => setTimeout(resolve, 120));
  workers[0]!.reply({ type: "ready", versions: VERSIONS });
  await tick();
  const request = workers[0]!.requests[0];
  workers[0]!.reply({ id: request.id, type: "result", document: { fine: true } });
  assert.deepEqual(await document, { fine: true });
  assert.equal(workers.length, 1);
});

test("a worker that dies on a document fails it with code crashed, and is replaced", async () => {
  const { workers, client } = startRestartable();
  workers[0]!.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  const document = client.validate(new ArrayBuffer(1));
  await tick();
  workers[0]!.onerror?.({ message: "out of memory" } as ErrorEvent);
  await assert.rejects(document, (error: unknown) => {
    assert.ok(error instanceof PydiggsBrowserError);
    assert.equal(error.code, "crashed");
    assert.match(error.message, /out of memory/);
    return true;
  });
  assert.equal(workers.length, 2);
  assert.notEqual(client.status, "failed");
});

test("a document over the limit is refused before it reaches the worker", async () => {
  const { worker, client } = start({ maxBytes: 4 });
  await assert.rejects(client.validate(new ArrayBuffer(5)), /larger than 4 bytes/);
  assert.equal(worker.requests.length, 0);
});

test("a load failure rejects ready, requests in flight and later requests", async () => {
  const { worker, client } = start();
  const inFlight = client.validate(new ArrayBuffer(1));
  await tick();
  worker.reply({ type: "failed", message: "pyodide.mjs: HTTP 404" });
  await assert.rejects(client.ready, /could not be loaded: pyodide\.mjs: HTTP 404/);
  await assert.rejects(inFlight, PydiggsBrowserError);
  await assert.rejects(client.read(new ArrayBuffer(1)), PydiggsBrowserError);
});

test("a worker error event fails the client", async () => {
  const { worker, client } = start();
  worker.onerror?.({ message: "SyntaxError" } as ErrorEvent);
  await assert.rejects(client.ready, /the pydiggs worker failed: SyntaxError/);
});

test("terminate stops the worker and rejects what was waiting", async () => {
  const { worker, client } = start();
  const inFlight = client.validate(new ArrayBuffer(1));
  await tick();
  client.terminate();
  assert.equal(worker.terminated, true);
  await assert.rejects(inFlight, /terminated/);
  await assert.rejects(client.validate(new ArrayBuffer(1)), /terminated/);
});

test("an abort signal stops the wait, before or after the request is sent", async () => {
  const { worker, client } = start();
  const already = AbortSignal.abort(new Error("stop"));
  await assert.rejects(client.validate(new ArrayBuffer(1), { signal: already }), /stop/);
  assert.equal(worker.requests.length, 0);

  const controller = new AbortController();
  const promise = client.validate(new ArrayBuffer(1), { signal: controller.signal });
  await tick();
  controller.abort(new Error("changed my mind"));
  await assert.rejects(promise, /changed my mind/);
  // A late answer for the abandoned request is ignored.
  worker.reply({ id: worker.requests[0].id, type: "result", document: {} });
});

test("progress stages reach onProgress, and status follows loading", async () => {
  const stages: string[] = [];
  const { worker, client } = start({ onProgress: (stage: string) => stages.push(stage) });
  assert.equal(client.status, "loading");
  for (const stage of ["runtime", "packages", "pydiggs", "warm-up"]) worker.reply({ type: "progress", stage });
  worker.reply({ type: "ready", versions: VERSIONS });
  await client.ready;
  assert.deepEqual(stages, ["runtime", "packages", "pydiggs", "warm-up"]);
  assert.equal(client.status, "ready");
  worker.reply({ type: "progress", stage: "runtime" });
  assert.equal(stages.length, 4, "progress after ready is ignored");
});

test("status is failed after a load failure or terminate", async () => {
  const failed = start();
  failed.worker.reply({ type: "failed", message: "x" });
  await assert.rejects(failed.client.ready);
  assert.equal(failed.client.status, "failed");
  const stopped = start();
  stopped.client.terminate();
  assert.equal(stopped.client.status, "failed");
});
