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
    assert.deepEqual(validated, { type: "validate", name: "a.xml", size: 8, results: undefined });
    assert.deepEqual(read, { type: "read", name: "input.xml", size: 3, results: false });
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

test("a thread that dies on a document rejects it and fails the client", async () => {
  const node = new PydiggsNode({ workerUrl: fakeWorker });
  await node.ready;
  await assert.rejects(node.validate(new Uint8Array(1), { name: "crash.xml" }), /exited with code 4/);
  assert.equal(node.status, "failed");
});
