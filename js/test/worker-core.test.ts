import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION } from "../src/generated/pins.ts";
import type { InitMessage, ReplyMessage } from "../src/protocol.ts";
import { createWorkerCore, type PyCallable, type PyodideLike } from "../src/worker-core.ts";

// A Pyodide that records what the worker asks of it. The Python functions are stand-ins:
// tests/pyodide/worker-smoke.mjs runs the real glue in the real Pyodide.
function fakePyodide(log: string[]): PyodideLike {
  const callable = (fn: (...args: any[]) => unknown): PyCallable =>
    Object.assign(fn, { callKwargs: async (...args: unknown[]) => fn(...args) });
  const globals: Record<string, unknown> = {
    versions: JSON.stringify({ pydiggs: "1.7.0", lxml: "6.1.3" }),
    validate: callable((data: Uint8Array, name: string, max: number) => {
      log.push(`validate ${name} ${data.byteLength} ${max}`);
      return JSON.stringify(name === "bad.xml" ? { error: "cannot read" } : { format: "diggs-findings", name });
    }),
    read: callable((data: Uint8Array, name: string, max: number, results: boolean) => {
      log.push(`read ${name} ${data.byteLength} ${max} ${results}`);
      return JSON.stringify({ schema_version: "1.0", name });
    }),
  };
  return {
    version: "314.0.7",
    loadPackage: async (names) => void log.push(`loadPackage ${names.join(",")}`),
    pyimport: (name) => ({
      install: callable((spec: string, kwargs: object) => void log.push(`${name}.install ${spec} ${JSON.stringify(kwargs)}`)),
    }),
    runPython: () => void log.push("runPython"),
    globals: { get: (name) => globals[name] },
  };
}

function start(loadPyodide?: (init: InitMessage) => Promise<PyodideLike>) {
  const log: string[] = [];
  const replies: ReplyMessage[] = [];
  const handle = createWorkerCore({
    post: (message) => replies.push(message),
    loadPyodide: loadPyodide ?? (async () => fakePyodide(log)),
  });
  return { log, replies, handle };
}

const settle = async () => {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
};
const bytes = (n: number) => new ArrayBuffer(n);

test("init installs the pinned pydiggs without dependencies and reports ready", async () => {
  const { log, replies, handle } = start();
  handle({ type: "init", maxBytes: 1000 });
  await settle();
  assert.deepEqual(log, [
    "loadPackage lxml,micropip",
    `micropip.install pydiggs==${PYDIGGS_VERSION} {"deps":false}`,
    "runPython",
  ]);
  assert.deepEqual(replies, [
    { type: "ready", versions: { pyodide: "314.0.7", pydiggs: "1.7.0", lxml: "6.1.3" } },
  ]);
});

test("a wheel URL replaces the PyPI release, and only a pure pydiggs wheel is accepted", async () => {
  const good = start();
  good.handle({ type: "init", maxBytes: 1, pydiggsWheelUrl: "https://static.test/w/pydiggs-1.8.0-py3-none-any.whl" });
  await settle();
  assert.match(good.log[1] ?? "", /^micropip\.install https:\/\/static\.test\/w\/pydiggs-1\.8\.0-py3-none-any\.whl /);

  let loaded = false;
  const bad = start(async () => {
    loaded = true;
    return fakePyodide([]);
  });
  bad.handle({ type: "init", maxBytes: 1, pydiggsWheelUrl: "https://static.test/w/other-1.0-py3-none-any.whl" });
  await settle();
  assert.equal(bad.replies[0]?.type, "failed");
  assert.equal(loaded, false, "nothing is downloaded for a refused wheel URL");
});

test("requests are answered in arrival order, one reply each", async () => {
  const { log, replies, handle } = start();
  handle({ type: "init", maxBytes: 1000 });
  handle({ id: 1, type: "validate", name: "a.xml", data: bytes(3) });
  handle({ id: 2, type: "read", name: "a.xml", data: bytes(3) });
  handle({ id: 3, type: "read", name: "", data: bytes(3), results: false });
  handle({ id: 4, type: "validate", name: "bad.xml", data: bytes(3) });
  await settle();
  assert.deepEqual(log.slice(3), [
    "validate a.xml 3 1000",
    "read a.xml 3 1000 true",
    "read input.xml 3 1000 false",
    "validate bad.xml 3 1000",
  ]);
  assert.deepEqual(replies.slice(1), [
    { id: 1, type: "result", document: { format: "diggs-findings", name: "a.xml" } },
    { id: 2, type: "result", document: { schema_version: "1.0", name: "a.xml" } },
    { id: 3, type: "result", document: { schema_version: "1.0", name: "input.xml" } },
    { id: 4, type: "error", message: "cannot read" },
  ]);
});

test("the size limit is enforced in the worker and capped at the default", async () => {
  const small = start();
  small.handle({ type: "init", maxBytes: 4 });
  small.handle({ id: 1, type: "validate", name: "a.xml", data: bytes(5) });
  await settle();
  assert.deepEqual(small.replies[1], { id: 1, type: "error", message: "the document is larger than 4 bytes" });
  assert.equal(small.log.some((line) => line.startsWith("validate")), false);

  const huge = start();
  huge.handle({ type: "init", maxBytes: DEFAULT_MAX_BYTES * 4 });
  huge.handle({ id: 1, type: "validate", name: "a.xml", data: bytes(1) });
  await settle();
  assert.equal(huge.log.at(-1), `validate a.xml 1 ${DEFAULT_MAX_BYTES}`);
});

test("a load failure is reported once, and each request then gets an error", async () => {
  const { replies, handle } = start(async () => {
    throw new Error("pyodide.mjs: HTTP 404");
  });
  handle({ type: "init", maxBytes: 1 });
  handle({ id: 7, type: "validate", name: "a.xml", data: bytes(1) });
  await settle();
  assert.deepEqual(replies, [
    { type: "failed", message: "pyodide.mjs: HTTP 404" },
    { id: 7, type: "error", message: "pyodide.mjs: HTTP 404" },
  ]);
});

test("a request before init is refused, a second init is ignored, and junk is dropped", async () => {
  let loads = 0;
  const { replies, handle } = start(async () => {
    loads++;
    return fakePyodide([]);
  });
  handle({ id: 1, type: "validate", name: "a.xml", data: bytes(1) });
  handle({ type: "init", maxBytes: 1 });
  handle({ type: "init", maxBytes: 99 });
  handle(null);
  handle("validate");
  handle({ type: "validate", id: "1", data: bytes(1) });
  handle({ type: "validate", id: 2, data: "not bytes" });
  handle({ type: "other", id: 3 });
  await settle();
  assert.equal(loads, 1);
  assert.deepEqual(
    replies.filter((reply) => reply.type !== "ready"),
    [{ id: 1, type: "error", message: "the worker was not initialised" }],
  );
});
