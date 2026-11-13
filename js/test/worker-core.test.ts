import assert from "node:assert/strict";
import { test } from "node:test";

import { createHash } from "node:crypto";

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION, PYDIGGS_WHEEL } from "../src/generated/pins.ts";
import type { InitMessage, ReplyMessage } from "../src/protocol.ts";
import { ABSOLUTE_MAX_BYTES, createWorkerCore, type PyCallable, type PyodideLike } from "../src/worker-core.ts";

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
    ...(fetch ? { fetch } : {}),
  });
  return { log, replies, stages, handle };
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

test("the pinned wheel is fetched, checked against its sha256 and installed from Pyodide's file system", async () => {
  assert.ok(PYDIGGS_WHEEL, "the generator pins the wheel for this release");
  const pin = PYDIGGS_WHEEL!;
  const file = pin.url.split("/").at(-1)!;
  const run = async (body: Uint8Array) => {
    const written: string[] = [];
    const fs = { mkdirTree: () => {}, writeFile: (path: string) => void written.push(path) };
    const fetched: string[] = [];
    const fetch = (async (url: string) => {
      fetched.push(url);
      return new Response(body as BodyInit);
    }) as unknown as typeof globalThis.fetch;
    const log: string[] = [];
    const worker = start(async () => fakePyodide(log, { FS: fs }), fetch);
    worker.handle({ type: "init", maxBytes: 1 });
    // The digest runs off the main thread, so wait for the reply rather than a few ticks.
    for (let i = 0; i < 500 && worker.replies.length === 0; i++) await new Promise((resolve) => setTimeout(resolve, 10));
    return { log, written, fetched, replies: worker.replies };
  };

  // A wheel whose bytes match a pin: patch the expected hash by hashing what we serve.
  const body = new TextEncoder().encode("not really a wheel");
  const digest = createHash("sha256").update(body).digest("hex");
  const real = pin.sha256;
  (pin as { sha256: string }).sha256 = digest;
  try {
    const good = await run(body);
    assert.deepEqual(good.fetched, [pin.url]);
    assert.deepEqual(good.written, [`/pydiggs-wheel/${file}`]);
    assert.ok(good.log.includes(`micropip.install emfs:/pydiggs-wheel/${file} {"deps":false}`));
    assert.equal(good.replies[0]?.type, "ready");
  } finally {
    (pin as { sha256: string }).sha256 = real;
  }

  const bad = await run(body);
  assert.equal(bad.replies[0]?.type, "failed");
  assert.match((bad.replies[0] as { message: string }).message, /does not match its pinned sha256/);
  assert.deepEqual(bad.written, [], "a wheel that fails the check is never written");
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
