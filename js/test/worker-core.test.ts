import assert from "node:assert/strict";
import { test } from "node:test";

import { createHash } from "node:crypto";

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYDIGGS_WHEEL } from "../src/generated/pins.ts";
import type { InitMessage, ReplyMessage } from "../src/protocol.ts";
import {
  ABSOLUTE_MAX_BYTES,
  createWorkerCore,
  type PyCallable,
  type PyodideLike,
  wheelSource,
} from "../src/worker-core.ts";

// The wheel every fake download serves; this file pins its hash, so the worker's
// sha256 check passes for it (the pinned-wheel test also serves one that fails).
const WHEEL = new TextEncoder().encode("not really a wheel");
const realSha256 = PYDIGGS_WHEEL!.sha256;
(PYDIGGS_WHEEL as { sha256: string }).sha256 = createHash("sha256").update(WHEEL).digest("hex");
const fetched: string[] = [];
const fakeFetch = (async (url: string) => {
  fetched.push(String(url));
  return new Response(WHEEL as BodyInit);
}) as unknown as typeof globalThis.fetch;

// A Pyodide that records what the worker asks of it. The Python functions are stand-ins:
// tests/pyodide/worker-smoke.mjs runs the real glue in the real Pyodide.
function fakePyodide(log: string[], extra: Partial<PyodideLike> = {}): PyodideLike {
  const callable = (fn: (...args: any[]) => unknown): PyCallable =>
    Object.assign(fn, { callKwargs: async (...args: unknown[]) => fn(...args) });
  const globals: Record<string, unknown> = {
    versions: JSON.stringify({ pydiggs: "1.7.0", lxml: "6.1.3" }),
    // The glue's one entry point: "D" and a document, or "E" and a problem.
    run: callable((kind: string, data: Uint8Array | undefined, name: string, max: number, parameters: string) => {
      log.push(`${kind} ${name} ${data?.byteLength ?? "-"} ${max} ${parameters}`.trimEnd());
      if (name === "bad.xml") return `E${JSON.stringify({ code: "invalid-document", detail: "not DIGGS", errors: [], kind: "not-diggs" })}`;
      if (kind === "validate") return `D${JSON.stringify({ format: "diggs-findings", name })}`;
      if (kind === "read") return `D${JSON.stringify({ schema_version: "1.0", name })}`;
      return `D${JSON.stringify({ kind })}`;
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
    unpackArchive: (buffer, format) => void log.push(`unpackArchive ${buffer.byteLength} ${format}`),
    FS: { mkdirTree: () => {}, writeFile: () => {}, readFile: () => WHEEL },
    ...extra,
  };
}

function start(loadPyodide?: (init: InitMessage) => Promise<PyodideLike>, fetch?: typeof globalThis.fetch) {
  const log: string[] = [];
  const replies: ReplyMessage[] = [];
  const stages: string[] = [];
  const handle = createWorkerCore({
    post: (message) => (message.type === "progress" ? stages.push(message.stage) : replies.push(message)),
    loadPyodide: loadPyodide ?? (async () => fakePyodide(log)),
    fetch: fetch ?? fakeFetch,
  });
  return { log, replies, stages, handle };
}

// Long enough for the fake download and its sha256 (which runs off the main thread).
const settle = () => new Promise((resolve) => setTimeout(resolve, 60));
const bytes = (n: number) => new ArrayBuffer(n);

test("init unpacks the pinned wheel, without micropip, and reports ready", async () => {
  const { log, replies, handle } = start();
  fetched.length = 0;
  handle({ type: "init", maxBytes: 1000 });
  await settle();
  assert.deepEqual(fetched, [PYDIGGS_WHEEL!.url]);
  assert.deepEqual(log, ["loadPackage lxml", `unpackArchive ${WHEEL.byteLength} wheel`, "runPython"]);
  assert.deepEqual(replies, [
    { type: "ready", versions: { pyodide: "314.0.7", pydiggs: "1.7.0", lxml: "6.1.3" } },
  ]);
});

test("the wheel download starts before Pyodide has loaded", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  fetched.length = 0;
  const log: string[] = [];
  const worker = start(async () => {
    await gate;
    return fakePyodide(log);
  });
  worker.handle({ type: "init", maxBytes: 1 });
  await settle();
  assert.deepEqual(fetched, [PYDIGGS_WHEEL!.url], "fetched while the runtime was still loading");
  release();
  await settle();
  assert.equal(worker.replies[0]?.type, "ready");
});

test("a wheel URL replaces the pinned release, and only a pure pydiggs wheel is accepted", async () => {
  fetched.length = 0;
  const good = start();
  good.handle({ type: "init", maxBytes: 1, pydiggsWheelUrl: "https://static.test/w/pydiggs-1.8.0-py3-none-any.whl" });
  await settle();
  assert.deepEqual(fetched, ["https://static.test/w/pydiggs-1.8.0-py3-none-any.whl"]);
  assert.ok(good.log.includes(`unpackArchive ${WHEEL.byteLength} wheel`));

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

test("a wheel in Pyodide's file system is read there; with no pin, micropip installs by version", async () => {
  const emfs = start();
  emfs.handle({ type: "init", maxBytes: 1, pydiggsWheelUrl: "emfs:/w/pydiggs-1.8.0-py3-none-any.whl" });
  await settle();
  assert.deepEqual(emfs.log.slice(0, 2), ["loadPackage lxml", `unpackArchive ${WHEEL.byteLength} wheel`]);
  assert.deepEqual(wheelSource({ type: "init", maxBytes: 1 }, fakeFetch, null), { spec: `pydiggs==${PYDIGGS_VERSION}` });
});

test("requests are answered in arrival order, one reply each, with their parameters", async () => {
  const { log, replies, handle } = start();
  handle({ type: "init", maxBytes: 1000 });
  handle({ id: 1, type: "validate", name: "a.xml", data: bytes(3), parameters: { checks: ["schema"], diggs_version: "2.6" } });
  handle({ id: 2, type: "read", name: "a.xml", data: bytes(3) });
  handle({ id: 3, type: "read", name: "", data: bytes(3), parameters: { select: { results: ["k1"] } } });
  handle({ id: 4, type: "validate", name: "bad.xml", data: bytes(3) });
  handle({ id: 5, type: "rules", parameters: { step: "schema" } });
  handle({ id: 6, type: "versions" });
  await settle();
  assert.deepEqual(log.slice(3), [
    'validate a.xml 3 1000 {"checks":["schema"],"diggs_version":"2.6"}',
    "read a.xml 3 1000",
    'read input.xml 3 1000 {"select":{"results":["k1"]}}',
    "validate bad.xml 3 1000",
    'rules input.xml - 1000 {"step":"schema"}',
    "versions input.xml - 1000",
  ]);
  assert.deepEqual(replies.slice(1), [
    { id: 1, type: "result", document: { format: "diggs-findings", name: "a.xml" } },
    { id: 2, type: "result", document: { schema_version: "1.0", name: "a.xml" } },
    { id: 3, type: "result", document: { schema_version: "1.0", name: "input.xml" } },
    {
      id: 4,
      type: "error",
      message: "not DIGGS",
      problem: { code: "invalid-document", detail: "not DIGGS", errors: [], kind: "not-diggs" },
    },
    { id: 5, type: "result", document: { kind: "rules" } },
    { id: 6, type: "result", document: { kind: "versions" } },
  ]);
});

test("the size limit is enforced in the worker, up to the absolute ceiling", async () => {
  const small = start();
  small.handle({ type: "init", maxBytes: 4 });
  small.handle({ id: 1, type: "validate", name: "a.xml", data: bytes(5) });
  await settle();
  assert.deepEqual(small.replies[1], {
    id: 1,
    type: "error",
    message: "the document is larger than 4 bytes",
    problem: { code: "input-too-large", detail: "larger than 4 bytes" },
  });
  assert.equal(small.log.some((line) => line.startsWith("validate")), false);

  const huge = start();
  huge.handle({ type: "init", maxBytes: ABSOLUTE_MAX_BYTES * 4 });
  huge.handle({ id: 1, type: "validate", name: "a.xml", data: bytes(1) });
  await settle();
  assert.equal(huge.log.at(-1), `validate a.xml 1 ${ABSOLUTE_MAX_BYTES}`);

  const server = start();
  server.handle({ type: "init", maxBytes: DEFAULT_MAX_BYTES * 4 });
  server.handle({ id: 1, type: "validate", name: "a.xml", data: bytes(1) });
  await settle();
  assert.equal(server.log.at(-1), `validate a.xml 1 ${DEFAULT_MAX_BYTES * 4}`, "a server may go above 25 MiB");
});

test("a pinned wheel that fails its sha256 is never installed", async () => {
  const tampered = (async () => new Response(new TextEncoder().encode("tampered") as BodyInit)) as unknown as typeof globalThis.fetch;
  const log: string[] = [];
  const worker = start(async () => fakePyodide(log), tampered);
  worker.handle({ type: "init", maxBytes: 1 });
  await settle();
  assert.equal(worker.replies[0]?.type, "failed");
  assert.match((worker.replies[0] as { message: string }).message, /does not match its pinned sha256/);
  assert.equal(log.some((line) => line.startsWith("unpackArchive")), false);
  assert.notEqual(realSha256, PYDIGGS_WHEEL!.sha256, "this file pins the fake wheel, not the real one");
});

test("an interrupt buffer is handed to Pyodide, and cleared before each request", async () => {
  const buffers: Uint8Array[] = [];
  const log: string[] = [];
  const { replies, handle } = start(async () =>
    fakePyodide(log, { setInterruptBuffer: (buffer) => void buffers.push(buffer as Uint8Array) }),
  );
  const shared = new SharedArrayBuffer(1);
  handle({ type: "init", maxBytes: 10, interruptBuffer: shared });
  await settle();
  assert.equal(buffers.length, 1);
  new Uint8Array(shared)[0] = 2; // a stop meant for an earlier request
  handle({ id: 1, type: "validate", name: "a.xml", data: bytes(1) });
  await settle();
  assert.equal(new Uint8Array(shared)[0], 0);
  assert.equal(replies[1]?.type, "result");
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

test("loading reports its stages in order, before ready", async () => {
  const { stages, replies, handle } = start();
  handle({ type: "init", maxBytes: 1 });
  await settle();
  assert.deepEqual(stages, ["runtime", "packages", "pydiggs", "warm-up"]);
  assert.equal(replies[0]?.type, "ready");
});
