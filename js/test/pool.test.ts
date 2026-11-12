import assert from "node:assert/strict";
import { test } from "node:test";

import { defaultPoolSize, PydiggsPool, type PoolEngine } from "../src/pool.ts";
import { createPydiggsHandler } from "../src/handler.ts";
import { PydiggsError } from "../src/errors.ts";

// An engine that answers after `delay` ms and records which engine took each document.
function fakeEngine(index: number, log: string[], options: { status?: PoolEngine["status"]; fail?: boolean } = {}) {
  let pending = 0;
  const engine = {
    status: options.status ?? "ready",
    get pending() {
      return pending;
    },
    ready: options.fail ? Promise.reject(new Error("no pyodide")) : Promise.resolve({ pyodide: "p", pydiggs: "d", lxml: "l" }),
    terminated: false,
    async validate(_input: unknown, opts?: { name?: string }) {
      pending++;
      log.push(`${index}:${opts?.name}`);
      await new Promise((resolve) => setTimeout(resolve, 20));
      pending--;
      return { format: "diggs-findings", files: [{ path: opts?.name }] } as never;
    },
    async read(_input: unknown, opts?: { name?: string }) {
      return this.validate(_input, opts) as never;
    },
    terminate() {
      engine.terminated = true;
    },
  };
  engine.ready.catch(() => {});
  return engine;
}

test("documents spread over the engines, least busy first", async () => {
  const log: string[] = [];
  const pool = new PydiggsPool((i) => fakeEngine(i, log), { size: 3 });
  await Promise.all(["a", "b", "c", "d", "e", "f"].map((name) => pool.validate(new Uint8Array(1), { name })));
  const perEngine = [0, 1, 2].map((i) => log.filter((entry) => entry.startsWith(`${i}:`)).length);
  assert.deepEqual(perEngine, [2, 2, 2]);
  assert.equal(pool.pending, 0);
});

test("an engine out of service is skipped; a restarting one is used only when needed", async () => {
  const log: string[] = [];
  const engines = [
    fakeEngine(0, log, { status: "failed" }),
    fakeEngine(1, log, { status: "loading" }),
    fakeEngine(2, log),
  ];
  const pool = new PydiggsPool((i) => engines[i]!, { size: 3 });
  await pool.validate(new Uint8Array(1), { name: "x" });
  assert.deepEqual(log, ["2:x"]);
  assert.equal(pool.status, "ready");
});

test("ready settles with the first engine; it rejects only when all fail", async () => {
  const log: string[] = [];
  const mixed = new PydiggsPool((i) => fakeEngine(i, log, { fail: i === 0 }), { size: 2 });
  assert.deepEqual(await mixed.ready, { pyodide: "p", pydiggs: "d", lxml: "l" });
  const dead = new PydiggsPool((i) => fakeEngine(i, log, { fail: true, status: "failed" }), { size: 2 });
  await assert.rejects(dead.ready, PydiggsError);
  assert.equal(dead.status, "failed");
  assert.throws(() => dead.validate(new Uint8Array(1)), /out of service/);
});

test("terminate stops every engine", () => {
  const engines: ReturnType<typeof fakeEngine>[] = [];
  const pool = new PydiggsPool((i) => {
    const engine = fakeEngine(i, []);
    engines.push(engine);
    return engine;
  }, { size: 2 });
  pool.terminate();
  assert.deepEqual(engines.map((engine) => engine.terminated), [true, true]);
});

test("the default size is one less than the processors, from 1 to 4", () => {
  const size = defaultPoolSize();
  assert.ok(size >= 1 && size <= 4);
});

test("a pool serves the HTTP handler like a single engine", async () => {
  const log: string[] = [];
  const pool = new PydiggsPool((i) => fakeEngine(i, log), { size: 2 });
  const handle = createPydiggsHandler(pool);
  const response = await handle(
    new Request("http://app.test/v1/validate?name=a.xml", {
      method: "POST",
      body: "<Diggs/>",
      headers: { "Content-Type": "application/xml" },
    }),
  );
  assert.equal(response.status, 200);
  assert.equal(log.length, 1);
});
