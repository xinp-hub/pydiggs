import assert from "node:assert/strict";
import { test } from "node:test";

import { createPydiggsHandler, type PydiggsEngine } from "../src/handler.ts";
import { PydiggsHttpClient, PydiggsHttpError } from "../src/http.ts";

// An engine that records each call and answers from `behaviour`.
function engine(behaviour: { fail?: "document" | "engine"; status?: PydiggsEngine["status"]; pending?: number } = {}) {
  const calls: unknown[] = [];
  const state = { status: behaviour.status ?? "ready", pending: behaviour.pending ?? 0 };
  const answer = async (kind: string, input: unknown, options: Record<string, unknown> = {}) => {
    const size = (input as Uint8Array).byteLength;
    calls.push({ kind, size, name: options.name, results: options.results });
    if (behaviour.fail === "engine") {
      state.status = "failed";
      throw new Error("worker died");
    }
    if (behaviour.fail === "document") throw new Error("not a DIGGS document");
    return kind === "validate"
      ? { format: "diggs-findings", schema_version: "1.0", files: [{ path: options.name }] }
      : { schema_version: "1.0", features: [], source: { name: options.name } };
  };
  const value: PydiggsEngine = {
    get status() {
      return state.status;
    },
    get pending() {
      return state.pending;
    },
    validate: (input, options) => answer("validate", input, options as Record<string, unknown>) as never,
    read: (input, options) => answer("read", input, options as Record<string, unknown>) as never,
  };
  return { value, calls };
}

const xml = (path: string, init: RequestInit = {}) =>
  new Request(`http://app.test${path}`, {
    method: "POST",
    body: "<Diggs/>",
    ...init,
    headers: { "Content-Type": "application/xml", ...(init.headers as Record<string, string>) },
  });

test("validate answers with the engine's findings document", async () => {
  const { value, calls } = engine();
  const response = await createPydiggsHandler(value)(xml("/v1/validate?name=dir/BH-01.xml"));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Content-Type"), "application/json");
  assert.deepEqual((await response.json()).files, [{ path: "BH-01.xml" }]);
  assert.deepEqual(calls, [{ kind: "validate", size: 8, name: "BH-01.xml", results: undefined }]);
});

test("read honours select all and none, and refuses node-key lists", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  const none = encodeURIComponent('{"results":"none"}');
  assert.equal((await handle(xml(`/v1/read?select=${none}`))).status, 200);
  assert.equal((await handle(xml("/v1/read"))).status, 200);
  const keys = encodeURIComponent('{"results":["Test_1"]}');
  const refused = await handle(xml(`/v1/read?select=${keys}`));
  assert.equal(refused.status, 422);
  assert.equal((await refused.json()).code, "invalid-parameter");
  assert.deepEqual(
    calls.map((call) => (call as { results: boolean }).results),
    [false, true],
  );
});

test("the problems pydiggs sends: method, path, media type, encoding, size, parameters", async () => {
  const handle = createPydiggsHandler(engine().value, { maxBodyBytes: 10 });
  const code = async (request: Request) => {
    const response = await handle(request);
    assert.equal(response.headers.get("Content-Type"), "application/problem+json");
    const body = await response.json();
    return [response.status, body.code];
  };
  assert.deepEqual(await code(new Request("http://app.test/v1/validate")), [405, "method-not-allowed"]);
  assert.deepEqual(await code(xml("/v2/validate")), [404, "not-found"]);
  assert.deepEqual(await code(xml("/v1/validate", { headers: { "Content-Type": "text/plain" } })), [415, "unsupported-media-type"]);
  assert.deepEqual(await code(xml("/v1/validate", { headers: { "Content-Encoding": "gzip" } })), [415, "compressed-input"]);
  assert.deepEqual(await code(xml("/v1/validate", { body: "<Diggs>too long</Diggs>" })), [413, "input-too-large"]);
  assert.deepEqual(await code(xml("/v1/validate", { body: "" })), [400, "missing-input"]);
  assert.deepEqual(await code(xml("/v1/validate?checks=schema")), [422, "invalid-parameter"]);
  assert.deepEqual(await code(xml("/v1/validate", { headers: { Accept: "application/sarif+json" } })), [406, "not-acceptable"]);
  assert.deepEqual(await code(xml("/v1/validate", { headers: { "Sec-Fetch-Site": "cross-site" } })), [403, "cross-site"]);
});

test("a body with no length header is still cut off at the limit", async () => {
  const handle = createPydiggsHandler(engine().value, { maxBodyBytes: 4 });
  const stream = new ReadableStream({
    start(controller) {
      for (let i = 0; i < 4; i++) controller.enqueue(new Uint8Array(3));
      controller.close();
    },
  });
  const request = new Request("http://app.test/v1/validate", {
    method: "POST",
    body: stream,
    headers: { "Content-Type": "application/xml" },
    duplex: "half",
  } as RequestInit);
  assert.equal((await handle(request)).status, 413);
});

test("busy, not ready, a bad document and a dead engine", async () => {
  const busy = await createPydiggsHandler(engine({ pending: 8 }).value)(xml("/v1/validate"));
  assert.deepEqual([busy.status, busy.headers.get("Retry-After")], [503, "1"]);
  const loading = await createPydiggsHandler(engine({ status: "loading" }).value)(
    new Request("http://app.test/v1/readyz"),
  );
  assert.equal(loading.status, 503);
  const bad = await createPydiggsHandler(engine({ fail: "document" }).value)(xml("/v1/read"));
  assert.deepEqual([bad.status, (await bad.json()).code], [422, "invalid-document"]);
  const dead = await createPydiggsHandler(engine({ fail: "engine" }).value)(xml("/v1/validate"));
  assert.deepEqual([dead.status, (await dead.json()).code], [503, "not-ready"]);
});

test("texts are fixed: nothing the client sent comes back in a problem", async () => {
  const handle = createPydiggsHandler(engine().value);
  const response = await handle(xml("/v1/validate?<script>=1"));
  const text = await response.text();
  assert.equal(response.status, 422);
  assert.ok(text.includes("<script>"), "the parameter name is listed, as pydiggs lists it");
  assert.ok(!text.includes("<Diggs/>"));
});

test("basePath mounts the API under a prefix", async () => {
  const handle = createPydiggsHandler(engine().value, { basePath: "/api/pydiggs/" });
  assert.equal((await handle(xml("/api/pydiggs/v1/validate"))).status, 200);
  assert.equal((await handle(xml("/v1/validate"))).status, 404);
  assert.equal((await handle(new Request("http://app.test/api/pydiggs/v1/readyz"))).status, 200);
});

test("PydiggsHttpClient works against the handler unchanged, multipart included", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value, { basePath: "/api/pydiggs" });
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) =>
    handle(new Request(new URL(String(input), "http://app.test"), init))) as typeof globalThis.fetch;
  const client = new PydiggsHttpClient({ baseUrl: "/api/pydiggs", fetch });
  const findings = await client.validate(new TextEncoder().encode("<Diggs/>"), { name: "BH-01.xml" });
  assert.equal(findings.format, "diggs-findings");
  const model = await client.read("<Diggs/>", { results: "none", name: "x.xml" });
  assert.equal(model.schema_version, "1.0");
  assert.equal(await client.ready(), true);
  await assert.rejects(client.validate("<Diggs/>", { checks: ["schema"] }), (error: unknown) => {
    assert.ok(error instanceof PydiggsHttpError);
    assert.equal(error.code, "invalid-parameter");
    return true;
  });
  // A long node-key list goes as multipart; the handler parses it and refuses the list.
  const keys = Array.from({ length: 400 }, (_, i) => `Test_${i}`);
  await assert.rejects(client.read("<Diggs/>", { results: keys }), /Invalid parameter/);
  assert.deepEqual(
    calls.map((call) => (call as { kind: string }).kind),
    ["validate", "read"],
  );
});
