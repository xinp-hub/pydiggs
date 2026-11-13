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
  assert.deepEqual(worker.posted[0].message, {
    type: "init",
    maxBytes: 1000,
    pyodideBaseUrl: "https://static.test/pyodide/",
    pydiggsWheelUrl: "https://static.test/wheels/pydiggs-1.7.0-py3-none-any.whl",
  });
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

test("read passes results, true unless asked otherwise, and names a File by its name", async () => {
  const { worker, client } = start();
  const file = new File(["<Diggs/>"], "site.xml");
  const all = Promise.all([
    client.read(file),
    client.read(file, { results: false, name: "other.xml" }),
    client.read(new Blob(["<Diggs/>"])),
  ]);
  await answerAll(worker, 3);
  await all;
  const [first, second, third] = worker.requests;
  assert.deepEqual([first.type, first.name, first.results], ["read", "site.xml", true]);
  assert.deepEqual([second.name, second.results], ["other.xml", false]);
  assert.equal(third.name, "input.xml");
  assert.notEqual(first.id, second.id);
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

test("a document past timeoutMs fails with code timeout; a fresh worker takes the next", async () => {
  const { workers, client } = startRestartable({ timeoutMs: 100 });
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
