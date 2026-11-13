import assert from "node:assert/strict";
import { test } from "node:test";

import { ABSOLUTE_MAX_BYTES, DEFAULT_MAX_BYTES, PydiggsNode, PydiggsNodeError } from "../src/node.ts";

const fakeWorker = new URL("./fixtures/fake-node-worker.mjs", import.meta.url);

test("a worker thread loads, reports progress, and answers in order", async () => {
  const stages: string[] = [];
  const node = new PydiggsNode({ workerUrl: fakeWorker, onProgress: (stage) => stages.push(stage) });
  try {
    const versions = (await node.ready) as unknown as { init: Record<string, unknown> };
    assert.deepEqual(stages, ["runtime"]);
    assert.equal(node.status, "ready");
    assert.equal(versions.init.maxBytes, DEFAULT_MAX_BYTES);
    const [validated, read] = await Promise.all([
      node.validate(Buffer.from("<Diggs/>"), { name: "a.xml" }),
      node.read(new Uint8Array(3), { results: false }),
    ]);
    assert.deepEqual(validated, { type: "validate", name: "a.xml", size: 8, parameters: undefined });
    assert.deepEqual(read, { type: "read", name: "input.xml", size: 3, parameters: { select: { results: "none" } } });
  } finally {
    node.terminate();
  }
});

test("Node options reach the worker: module path as a file URL, cache, wheel, a larger limit", async () => {
  const node = new PydiggsNode({
    workerUrl: fakeWorker,
    maxBytes: 200 * 1024 * 1024,
    pyodideModule: "/opt/app/node_modules/pyodide/pyodide.mjs",
    packageCacheDir: "/var/cache/pyodide",
    pydiggsWheelPath: "/opt/app/pydiggs-1.8.0-py3-none-any.whl",
  });
  try {
    const { init } = (await node.ready) as unknown as { init: Record<string, unknown> };
    assert.equal(init.maxBytes, 200 * 1024 * 1024);
    assert.equal(init.pyodideModuleUrl, "file:///opt/app/node_modules/pyodide/pyodide.mjs");
    assert.equal(init.packageCacheDir, "/var/cache/pyodide");
    assert.equal(init.pydiggsWheelPath, "/opt/app/pydiggs-1.8.0-py3-none-any.whl");
  } finally {
    node.terminate();
  }
});

test("maxBytes is held to the absolute ceiling, and enforced before sending", async () => {
  const big = new PydiggsNode({ workerUrl: fakeWorker, maxBytes: ABSOLUTE_MAX_BYTES * 2 });
  const small = new PydiggsNode({ workerUrl: fakeWorker, maxBytes: 4 });
  try {
    const { init } = (await big.ready) as unknown as { init: Record<string, unknown> };
    assert.equal(init.maxBytes, ABSOLUTE_MAX_BYTES);
    await assert.rejects(small.validate(new Uint8Array(5)), PydiggsNodeError);
  } finally {
    big.terminate();
    small.terminate();
  }
});

test("a thread that dies while loading fails the client", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker, maxBytes: 13 });
  await assert.rejects(node.ready, /exited with code 3/);
  assert.equal(node.status, "failed");
  await assert.rejects(node.validate(new Uint8Array(1)), PydiggsNodeError);
});

test("a thread that dies on a document fails that document, and a fresh worker takes the next", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker });
  try {
    await node.ready;
    await assert.rejects(node.validate(new Uint8Array(1), { name: "crash.xml" }), (error: unknown) => {
      assert.ok(error instanceof PydiggsNodeError);
      assert.equal(error.code, "crashed");
      assert.match(error.message, /exited with code 4/);
      return true;
    });
    assert.notEqual(node.status, "failed");
    assert.deepEqual(await node.validate(new Uint8Array(2), { name: "b.xml" }), {
      type: "validate",
      name: "b.xml",
      size: 2,
      parameters: undefined,
    });
  } finally {
    node.terminate();
  }
});

test("a document past timeoutMs fails with code timeout; the documents behind it still run", async () => {
  // slow.xml ignores the interrupt, so after the grace period its thread is replaced.
  const node = new PydiggsNode({ workerUrl: fakeWorker, timeoutMs: 300, interruptGraceMs: 100 });
  try {
    await node.ready;
    const slow = node.validate(new Uint8Array(1), { name: "slow.xml" });
    const next = node.validate(new Uint8Array(3), { name: "next.xml" });
    assert.equal(node.pending, 2);
    await assert.rejects(slow, (error: unknown) => {
      assert.ok(error instanceof PydiggsNodeError);
      assert.equal(error.code, "timeout");
      return true;
    });
    assert.equal(((await next) as unknown as { name: string }).name, "next.xml", "the queued document ran on the replacement worker");
    assert.equal(node.pending, 0);
    assert.equal(node.status, "ready");
  } finally {
    node.terminate();
  }
});

test("loading does not count against the timeout, and timeoutMs 0 turns it off", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker, timeoutMs: 0 });
  try {
    const slow = node.validate(new Uint8Array(1), { name: "slow.xml" });
    const outcome = await Promise.race([
      slow.then(() => "answered", () => "rejected"),
      new Promise((resolve) => setTimeout(() => resolve("still running"), 500)),
    ]);
    assert.equal(outcome, "still running");
  } finally {
    node.terminate();
  }
});

test("terminate rejects waiting documents, and nothing runs after it", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker, timeoutMs: 0 });
  const slow = node.validate(new Uint8Array(1), { name: "slow.xml" });
  const waiting = node.validate(new Uint8Array(1), { name: "w.xml" });
  await node.ready;
  node.terminate();
  await assert.rejects(slow, PydiggsNodeError);
  await assert.rejects(waiting, /terminated/);
  await assert.rejects(node.read(new Uint8Array(1)), /terminated/);
  assert.equal(node.status, "failed");
});

test("a worker thread shares an interrupt buffer, and a document past timeoutMs stops without a restart", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker, timeoutMs: 50, interruptGraceMs: 2000 });
  try {
    const versions = (await node.ready) as unknown as { interruptible: boolean };
    assert.equal(versions.interruptible, true);
    assert.equal(node.interruptible, true);
    await assert.rejects(node.validate(new Uint8Array(1), { name: "stoppable.xml" }), (error: unknown) => {
      assert.ok(error instanceof PydiggsNodeError);
      assert.equal(error.code, "timeout");
      return true;
    });
    assert.equal(node.restarts, 0, "the worker stopped when asked, so it was kept");
    const after = (await node.versions()) as unknown as { type: string };
    assert.equal(after.type, "versions");
  } finally {
    node.terminate();
  }
});

test("rules and explain go to the worker with their parameters", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker });
  try {
    const rules = (await node.listRules({ step: "crs" })) as unknown as { type: string; parameters: unknown };
    assert.deepEqual([rules.type, rules.parameters], ["rules", { step: "crs" }]);
    const rule = (await node.explainRule("crs.1")) as unknown as { type: string; parameters: unknown };
    assert.deepEqual([rule.type, rule.parameters], ["explain", { rule_id: "crs.1" }]);
  } finally {
    node.terminate();
  }
});
