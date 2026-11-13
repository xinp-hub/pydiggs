import assert from "node:assert/strict";
import { test } from "node:test";

import { createPydiggsHandler, negotiate, type PydiggsEngine } from "../src/handler.ts";
import { PydiggsHttpClient, PydiggsHttpError } from "../src/http.ts";

// An engine that records each call and answers from `behaviour`.
function engine(
  behaviour: {
    fail?: "document" | "parameter" | "engine" | "timeout" | "crashed";
    status?: PydiggsEngine["status"];
    pending?: number;
  } = {},
) {
  const calls: Record<string, unknown>[] = [];
  const state = { status: behaviour.status ?? "ready", pending: behaviour.pending ?? 0 };
  const answer = async (kind: string, input: unknown, options: Record<string, unknown> = {}) => {
    const { signal: _signal, ...rest } = options;
    calls.push({ kind, ...(input ? { size: (input as Uint8Array).byteLength } : {}), ...rest });
    if (behaviour.fail === "engine") {
      state.status = "failed";
      throw new Error("worker died");
    }
    if (behaviour.fail === "document") {
      throw Object.assign(new Error("x"), {
        problem: { code: "invalid-document", detail: "a.xml: The root element is not a DIGGS Diggs element.", kind: "not-diggs" },
      });
    }
    if (behaviour.fail === "parameter") {
      throw Object.assign(new Error("x"), {
        problem: { code: "invalid-parameter", detail: "invalid parameters", errors: [{ parameter: "checks", problem: "expected some of …" }] },
      });
    }
    if (behaviour.fail === "timeout" || behaviour.fail === "crashed") {
      throw Object.assign(new Error("stopped"), { code: behaviour.fail });
    }
    switch (kind) {
      case "validate":
        return { format: "diggs-findings", schema_version: "1.0", files: [{ path: options.name }] };
      case "sarif":
        return { version: "2.1.0", runs: [] };
      case "versions":
        return { api_version: "1", pydiggs_version: "1.8.0" };
      case "rules":
        return [{ id: "schema.1" }];
      case "explain":
        return { id: options.ruleId, known: true };
      default:
        return { schema_version: "1.0", features: [], source: { name: options.name } };
    }
  };
  const value: PydiggsEngine = {
    get status() {
      return state.status;
    },
    get pending() {
      return state.pending;
    },
    validate: (input, options) => answer("validate", input, options as Record<string, unknown>) as never,
    validateSarif: (input, options) => answer("sarif", input, options as Record<string, unknown>) as never,
    read: (input, options) => answer("read", input, options as Record<string, unknown>) as never,
    versions: (options) => answer("versions", undefined, options as Record<string, unknown>) as never,
    listRules: (options) => answer("rules", undefined, options as Record<string, unknown>) as never,
    explainRule: (ruleId, options) => answer("explain", undefined, { ...options, ruleId }) as never,
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
  assert.deepEqual(calls, [{ kind: "validate", size: 8, name: "BH-01.xml" }]);
});

test("validate passes checks (repeated or comma-separated) and the DIGGS version", async () => {
  const { value, calls } = engine();
  const response = await createPydiggsHandler(value)(
    xml("/v1/validate?checks=structure,schema&checks=schema&checks=dictionary&diggs_version=2.6"),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [
    { kind: "validate", size: 8, name: "input.xml", checks: ["structure", "schema", "dictionary"], diggsVersion: "2.6" },
  ]);
});

test("validate answers SARIF when Accept prefers it, as pydiggs negotiates", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  const sarif = await handle(xml("/v1/validate", { headers: { Accept: "application/sarif+json" } }));
  assert.equal(sarif.status, 200);
  assert.equal(sarif.headers.get("Content-Type"), "application/sarif+json");
  assert.equal((await sarif.json()).version, "2.1.0");
  const json = await handle(xml("/v1/validate", { headers: { Accept: "application/sarif+json;q=0.5, application/json" } }));
  assert.equal(json.headers.get("Content-Type"), "application/json");
  const neither = await handle(xml("/v1/validate", { headers: { Accept: "text/html" } }));
  assert.equal(neither.status, 406);
  assert.deepEqual(
    calls.map((call) => call.kind),
    ["sarif", "validate"],
  );
});

test("negotiate matches pydiggs's choices", () => {
  assert.equal(negotiate(null), "findings");
  assert.equal(negotiate("*/*"), "findings");
  assert.equal(negotiate("application/sarif+json"), "sarif");
  assert.equal(negotiate("application/*, application/sarif+json"), "sarif");
  assert.equal(negotiate("application/json;q=0, */*"), "sarif");
  assert.equal(negotiate("application/xml"), null);
});

test("read passes select all, none and node-key lists, and refuses a malformed select", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  const none = encodeURIComponent('{"results":"none"}');
  assert.equal((await handle(xml(`/v1/read?select=${none}`))).status, 200);
  assert.equal((await handle(xml("/v1/read?diggs_version=3.0.0"))).status, 200);
  const keys = encodeURIComponent('{"results":["Test_1"]}');
  assert.equal((await handle(xml(`/v1/read?select=${keys}`))).status, 200);
  for (const bad of ["nope", '{"results":"some"}', '{"other":1}', "[1]"]) {
    const refused = await handle(xml(`/v1/read?select=${encodeURIComponent(bad)}`));
    assert.equal(refused.status, 422, bad);
    const body = await refused.json();
    assert.equal(body.code, "invalid-parameter");
    assert.equal(body.errors[0].parameter, "select");
  }
  assert.deepEqual(
    calls.map((call) => [call.results, call.diggsVersion]),
    [
      ["none", undefined],
      ["all", "3.0.0"],
      [["Test_1"], undefined],
    ],
  );
});

test("versions, rules and one rule are served", async () => {
  const { value, calls } = engine();
  const handle = createPydiggsHandler(value);
  const get = (path: string) => handle(new Request(`http://app.test${path}`));
  assert.equal((await (await get("/v1/versions")).json()).api_version, "1");
  assert.deepEqual(await (await get("/v1/rules?step=schema")).json(), [{ id: "schema.1" }]);
  assert.deepEqual(await (await get("/v1/rules/schema.no%2Dsuch")).json(), { id: "schema.no-such", known: true });
  assert.equal((await get("/v1/rules?other=1")).status, 422);
  assert.equal((await handle(new Request("http://app.test/v1/rules", { method: "POST" }))).status, 405);
  assert.deepEqual(calls, [
    { kind: "versions" },
    { kind: "rules", step: "schema" },
    { kind: "explain", ruleId: "schema.no-such" },
  ]);
});

test("pydiggs's own refusals come back as its problems, with its errors and kind", async () => {
  const bad = await createPydiggsHandler(engine({ fail: "document" }).value)(xml("/v1/read"));
  const body = await bad.json();
  assert.deepEqual([bad.status, body.code, body.kind], [422, "invalid-document", "not-diggs"]);
  assert.match(body.detail, /not a DIGGS Diggs element/);
  const parameter = await createPydiggsHandler(engine({ fail: "parameter" }).value)(xml("/v1/validate?checks=nope"));
  const refused = await parameter.json();
  assert.deepEqual([parameter.status, refused.code, refused.errors[0].parameter], [422, "invalid-parameter", "checks"]);
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
  assert.deepEqual(await code(xml("/v1/validate?select=x")), [422, "invalid-parameter"]);
  assert.deepEqual(await code(xml("/v1/read", { headers: { Accept: "application/sarif+json" } })), [406, "not-acceptable"]);
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
  const dead = await createPydiggsHandler(engine({ fail: "engine" }).value)(xml("/v1/validate"));
  assert.deepEqual([dead.status, (await dead.json()).code], [503, "not-ready"]);
});

test("a timeout is 504 timeout and a crashed worker is 500 internal, as in pydiggs", async () => {
  const timeout = await createPydiggsHandler(engine({ fail: "timeout" }).value)(xml("/v1/read"));
  assert.deepEqual([timeout.status, (await timeout.json()).code], [504, "timeout"]);
  const crashed = await createPydiggsHandler(engine({ fail: "crashed" }).value)(xml("/v1/validate"));
  assert.deepEqual([crashed.status, (await crashed.json()).code], [500, "internal"]);
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
  const sarif = await client.validateSarif("<Diggs/>", { checks: ["schema"], diggsVersion: "2.6" });
  assert.equal(sarif.version, "2.1.0");
  // A long node-key list goes as multipart; the handler reads it from the form.
  const keys = Array.from({ length: 400 }, (_, i) => `Test_${i}`);
  await client.read("<Diggs/>", { results: keys });
  assert.equal((await client.versions()).pydiggs_version, "1.8.0");
  assert.deepEqual(await client.listRules({ step: "schema" }), [{ id: "schema.1" }]);
  assert.equal((await client.explainRule("schema.1")).known, true);
  assert.deepEqual(
    calls.map((call) => call.kind),
    ["validate", "read", "sarif", "read", "versions", "rules", "explain"],
  );
  assert.deepEqual(calls[2], { kind: "sarif", size: 8, name: "input.xml", checks: ["schema"], diggsVersion: "2.6" });
  assert.deepEqual((calls[3] as { results: string[] }).results, keys);
  const refused = await (async () => {
    try {
      await new PydiggsHttpClient({ baseUrl: "/api/pydiggs", fetch: (async (input: RequestInfo | URL, init?: RequestInit) =>
        createPydiggsHandler(engine({ fail: "document" }).value, { basePath: "/api/pydiggs" })(
          new Request(new URL(String(input), "http://app.test"), init),
        )) as typeof globalThis.fetch }).read("<x/>");
    } catch (error) {
      return error;
    }
  })();
  assert.ok(refused instanceof PydiggsHttpError);
  assert.equal(refused.code, "invalid-document");
});
