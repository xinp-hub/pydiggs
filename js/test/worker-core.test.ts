import assert from "node:assert/strict";
import { test } from "node:test";

import { createHash } from "node:crypto";

import { DEFAULT_MAX_BYTES, PYDIGGS_VERSION } from "../src/generated/pins.ts";
import type { InitMessage, ReplyMessage } from "../src/protocol.ts";
import {
  ABSOLUTE_MAX_BYTES,
  createWorkerCore,
  type PyCallable,
  type PyodideLike,
  pyodideIntegrity,
  wheelSha256,
  wheelSource,
} from "../src/worker-core.ts";
import { PYODIDE_SRI } from "../src/generated/pins.ts";

// The wheel every fake download serves, and a pin that matches it, passed to the worker
// as its pinned wheel. The real pin is null between a pyDIGGS release PR and the
// release reaching PyPI, so these tests never depend on it.
const WHEEL = new TextEncoder().encode("not really a wheel");
const PIN = {
  url: "https://files.pythonhosted.org/packages/xx/pydiggs-9.9.9-py3-none-any.whl",
  sha256: createHash("sha256").update(WHEEL).digest("hex"),
};
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
    run: callable((kind: string, data: Uint8Array | undefined, name: string, max: number, parameters: string, dictionary?: Uint8Array) => {
      log.push(`${kind} ${name} ${data?.byteLength ?? "-"} ${max} ${parameters}`.trimEnd() + (dictionary ? ` dict=${dictionary.byteLength}` : ""));
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
    pin: PIN,
  });
  return { log, replies, stages, handle };
}

// Long enough for the fake download and its sha256 (which runs off the main thread).
const settle = () => new Promise((resolve) => setTimeout(resolve, 60));
const bytes = (n: number) => new ArrayBuffer(n);

// Waits for what a test is about instead of for a fixed time, which a loaded machine
// overruns: until `done()`, for five seconds at most.
async function until(done: () => boolean): Promise<void> {
  const deadline = Date.now() + 5_000;
  while (!done() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
}

test("init unpacks the pinned wheel, without micropip, and reports ready", async () => {
  const { log, replies, handle } = start();
  fetched.length = 0;
  handle({ type: "init", maxBytes: 1000 });
  await until(() => replies.length > 0);
  assert.deepEqual(fetched, [PIN.url]);
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
  assert.deepEqual(fetched, [PIN.url], "fetched while the runtime was still loading");
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
});

const OWN_WHEEL = "https://static.test/w/pydiggs-1.8.0-py3-none-any.whl";
const failure = (worker: { replies: ReplyMessage[] }): string => {
  const [reply] = worker.replies;
  assert.equal(reply?.type, "failed");
  return (reply as { message: string }).message;
};
const unpacked = (log: string[]): boolean => log.some((line) => line.startsWith("unpackArchive"));

test("a wheel URL with its sha256 is checked before it is unpacked, in either letter case", async () => {
  for (const sha256 of [PIN.sha256, PIN.sha256.toUpperCase()]) {
    const worker = start();
    worker.handle({ type: "init", maxBytes: 1, pydiggsWheelUrl: OWN_WHEEL, pydiggsWheelSha256: sha256 });
    await settle();
    assert.equal(worker.replies[0]?.type, "ready");
    assert.ok(unpacked(worker.log));
  }
});

test("a wheel that does not match pydiggsWheelSha256 is never installed", async () => {
  const other = "0".repeat(64);
  // Downloaded from the caller's URL, read from Pyodide's file system (Node's
  // pydiggsWheelPath), and the pinned release, which must then match both digests.
  for (const init of [{ pydiggsWheelUrl: OWN_WHEEL }, { pydiggsWheelUrl: "emfs:/w/pydiggs-1.8.0-py3-none-any.whl" }, {}]) {
    const worker = start();
    worker.handle({ type: "init", maxBytes: 1, ...init, pydiggsWheelSha256: other });
    await settle();
    assert.match(failure(worker), /does not match pydiggsWheelSha256; refusing to install it/);
    assert.equal(unpacked(worker.log), false);
    assert.equal(worker.log.includes("runPython"), false, "pydiggs is never imported");
  }
});

test("a malformed pydiggsWheelSha256 is refused before anything is downloaded", async () => {
  for (const sha256 of ["", "abc", PIN.sha256.slice(1), `${PIN.sha256}0`, `sha256-${PIN.sha256}`, "z".repeat(64), 5]) {
    fetched.length = 0;
    let loaded = false;
    const worker = start(async () => {
      loaded = true;
      return fakePyodide([]);
    });
    worker.handle({ type: "init", maxBytes: 1, pydiggsWheelUrl: OWN_WHEEL, pydiggsWheelSha256: sha256 as string });
    await settle();
    assert.match(failure(worker), /pydiggsWheelSha256 must be a sha256 as 64 hex digits/);
    assert.deepEqual([fetched, loaded], [[], false]);
  }
  assert.equal(wheelSha256({ type: "init", maxBytes: 1 }), undefined);
});

test("pydiggsWheelSha256 with no wheel bytes to check (a release installed by version) fails closed", async () => {
  const log: string[] = [];
  const replies: ReplyMessage[] = [];
  const handle = createWorkerCore({
    post: (message) => void (message.type === "progress" || replies.push(message)),
    loadPyodide: async () => fakePyodide(log),
    fetch: fakeFetch,
    pin: null,
  });
  handle({ type: "init", maxBytes: 1, pydiggsWheelSha256: PIN.sha256 });
  await settle();
  assert.match(failure({ replies }), /pydiggsWheelSha256 needs a wheel to check/);
  assert.deepEqual(log, [], "micropip is never asked to install");
});

test("the Pyodide loader's integrity is the pin, or the caller's for a Pyodide they host", () => {
  const init = { type: "init", maxBytes: 1 } as const;
  const hosted = { ...init, pyodideBaseUrl: "https://static.test/pyodide/" };
  const sha384 = `sha384-${"A".repeat(64)}`;
  const sha256 = `sha256-${"b".repeat(43)}=`;
  const sha512 = `sha512-${"c".repeat(86)}==`;
  assert.equal(pyodideIntegrity(init), PYODIDE_SRI);
  assert.equal(pyodideIntegrity(hosted), PYODIDE_SRI, "self-hosting alone keeps the pin");
  assert.equal(pyodideIntegrity({ ...hosted, pyodideSri: sha384 }), sha384);
  assert.equal(pyodideIntegrity({ ...hosted, pyodideSri: ` ${sha256}  ${sha512}\n` }), `${sha256} ${sha512}`);
  assert.equal(pyodideIntegrity({ ...hosted, pyodideSri: PYODIDE_SRI }), PYODIDE_SRI, "the pin itself is well-formed");
  assert.throws(() => pyodideIntegrity({ ...init, pyodideSri: sha384 }), /pass pyodideBaseUrl with it/);
  // A browser ignores what it cannot parse, and then checks nothing: each is refused.
  for (const bad of ["", " ", "md5-abc", "sha384-short", `sha1-${"A".repeat(27)}=`, `${sha384} junk`, `sha384-${"A".repeat(63)}!`, `${sha384}?opt`, 7]) {
    assert.throws(() => pyodideIntegrity({ ...hosted, pyodideSri: bad as string }), /must be a Subresource Integrity value/, String(bad));
  }
});

test("validate's limits reach the glue with the other parameters", async () => {
  const { log, handle } = start();
  handle({ type: "init", maxBytes: 1000 });
  handle({ id: 1, type: "validate", name: "a.xml", data: bytes(3), parameters: { checks: ["schema"], max_findings: 1000, time_budget: 20 } });
  await settle();
  assert.equal(log.at(-1), 'validate a.xml 3 1000 {"checks":["schema"],"max_findings":1000,"time_budget":20}');
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

test("convert, ags4-check and capabilities go to the glue; AGS4 names default to pyDIGGS's", async () => {
  const { log, replies, handle } = start();
  handle({ type: "init", maxBytes: 1000 });
  handle({ id: 1, type: "convert", name: "site.ags", data: bytes(4), parameters: { to: "diggs" } });
  handle({ id: 2, type: "ags4-check", data: bytes(4), dictionary: bytes(2) });
  handle({ id: 3, type: "ags4-check", name: "", data: bytes(4), parameters: { format: "sarif" } });
  handle({ id: 4, type: "capabilities" });
  handle({ id: 5, type: "convert", data: bytes(2000) });
  handle({ id: 6, type: "convert", data: bytes(3), parameters: { to: "ags4", ags_version: "4.1.1" } });
  await settle();
  assert.deepEqual(log.slice(3), [
    'convert site.ags 4 1000 {"to":"diggs"}',
    "ags4-check  4 1000 dict=2",
    'ags4-check  4 1000 {"format":"sarif"}',
    "capabilities input.xml - 1000",
    'convert  3 1000 {"to":"ags4","ags_version":"4.1.1"}',
  ]);
  assert.deepEqual(
    replies.slice(1).map((reply) => [reply.type, "id" in reply ? reply.id : 0]),
    [
      ["result", 1],
      ["result", 2],
      ["result", 3],
      ["result", 4],
      ["error", 5],
      ["result", 6],
    ],
  );
  assert.equal((replies[5] as { problem?: { code: string } }).problem?.code, "input-too-large");
});
