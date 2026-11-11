import assert from "node:assert/strict";
import { test } from "node:test";

import { PydiggsHttpClient, PydiggsHttpError } from "../src/http.ts";

interface Seen {
  url: URL;
  init: RequestInit;
}

// A fetch that records each request and answers from `respond`.
function fakeFetch(respond: (seen: Seen) => Response): { fetch: typeof fetch; seen: Seen[] } {
  const seen: Seen[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = { url: new URL(String(input)), init: init ?? {} };
    seen.push(request);
    return respond(request);
  }) as typeof globalThis.fetch;
  return { fetch, seen };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

const problem = (status: number, code: string, headers: Record<string, string> = {}): Response =>
  new Response(
    JSON.stringify({ type: "https://example.test/p", title: "Title", status, detail: "Detail.", code }),
    { status, headers: { "Content-Type": "application/problem+json", ...headers } },
  );

const FINDINGS = { format: "diggs-findings", schema_version: "1.0", files: [] };
const MODEL = { schema_version: "1.0", features: [] };
const headerOf = (seen: Seen, name: string) => new Headers(seen.init.headers).get(name);

test("validate posts the document as XML with the options as query parameters", async () => {
  const { fetch, seen } = fakeFetch(() => json(FINDINGS));
  const client = new PydiggsHttpClient({ baseUrl: "http://127.0.0.1:8000/", fetch });
  const document = await client.validate("<Diggs/>", {
    name: "BH 01.xml",
    checks: ["structure", "schema"],
    diggsVersion: "2.6",
  });
  assert.deepEqual(document, FINDINGS);
  const [request] = seen;
  assert.equal(request.init.method, "POST");
  assert.equal(request.url.origin + request.url.pathname, "http://127.0.0.1:8000/v1/validate");
  assert.equal(request.url.searchParams.get("name"), "BH 01.xml");
  assert.equal(request.url.searchParams.get("checks"), "structure,schema");
  assert.equal(request.url.searchParams.get("diggs_version"), "2.6");
  assert.equal(headerOf(request, "Content-Type"), "application/xml");
  assert.equal(headerOf(request, "Accept"), "application/json");
  assert.equal(request.init.body, "<Diggs/>");
});

test("options left out add no query parameters, and a path prefix is kept", async () => {
  const { fetch, seen } = fakeFetch(() => json(FINDINGS));
  const client = new PydiggsHttpClient({ baseUrl: new URL("https://gateway.test/diggs/"), fetch });
  await client.validate(new Uint8Array([60, 68, 47, 62]));
  assert.equal(seen[0].url.href, "https://gateway.test/diggs/v1/validate");
});

test("validateSarif asks for SARIF", async () => {
  const { fetch, seen } = fakeFetch(() => json({ version: "2.1.0", runs: [] }));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  assert.deepEqual(await client.validateSarif("<Diggs/>"), { version: "2.1.0", runs: [] });
  assert.equal(headerOf(seen[0], "Accept"), "application/sarif+json");
});

test("read sends a short select in the query", async () => {
  const { fetch, seen } = fakeFetch(() => json(MODEL));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  await client.read("<Diggs/>", { results: "none" });
  await client.read("<Diggs/>", { results: ["Test_1"] });
  await client.read("<Diggs/>", { results: "all" });
  await client.read("<Diggs/>");
  assert.equal(seen[0].url.searchParams.get("select"), '{"results":"none"}');
  assert.equal(seen[1].url.searchParams.get("select"), '{"results":["Test_1"]}');
  assert.equal(seen[2].url.searchParams.has("select"), false);
  assert.equal(seen[3].url.searchParams.has("select"), false);
});

test("read sends a long select as a multipart field, away from the header limit", async () => {
  const { fetch, seen } = fakeFetch(() => json(MODEL));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  const keys = Array.from({ length: 400 }, (_, i) => `Test_${i}`);
  await client.read("<Diggs/>", { results: keys, name: "site.xml" });
  const [request] = seen;
  assert.equal(request.url.searchParams.has("select"), false);
  assert.equal(headerOf(request, "Content-Type"), null, "fetch must set the multipart boundary itself");
  const form = request.init.body as FormData;
  assert.ok(form instanceof FormData);
  assert.deepEqual(JSON.parse(form.get("select") as string), { results: keys });
  const file = form.get("file") as File;
  assert.equal(file.name, "site.xml");
  assert.equal(await file.text(), "<Diggs/>");
});

test("a problem answer becomes a PydiggsHttpError with its code and Retry-After", async () => {
  const { fetch } = fakeFetch(() => problem(503, "busy", { "Retry-After": "1" }));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  await assert.rejects(client.validate("<Diggs/>"), (error: unknown) => {
    assert.ok(error instanceof PydiggsHttpError);
    assert.equal(error.status, 503);
    assert.equal(error.code, "busy");
    assert.equal(error.retryAfterSeconds, 1);
    assert.equal(error.problem?.detail, "Detail.");
    assert.match(error.message, /busy/);
    return true;
  });
});

test("an error that is not a problem document reports the status alone", async () => {
  const { fetch } = fakeFetch(() => new Response("<html>Bad gateway</html>", { status: 502 }));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  await assert.rejects(client.versions(), (error: unknown) => {
    assert.ok(error instanceof PydiggsHttpError);
    assert.equal(error.status, 502);
    assert.equal(error.code, undefined);
    assert.equal(error.problem, undefined);
    return true;
  });
});

test("a 200 that is not the promised document is refused", async () => {
  const { fetch } = fakeFetch(() => json({ hello: "world" }));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  await assert.rejects(client.validate("<Diggs/>"), PydiggsHttpError);
  await assert.rejects(client.read("<Diggs/>"), PydiggsHttpError);
  await assert.rejects(client.listRules(), PydiggsHttpError);
  const html = fakeFetch(() => new Response("<html>", { status: 200 }));
  await assert.rejects(new PydiggsHttpClient({ baseUrl: "http://h", fetch: html.fetch }).versions(), PydiggsHttpError);
});

test("ready is true on 200, false on not-ready, and throws otherwise", async () => {
  const up = fakeFetch(() => json({ status: "ready" }));
  assert.equal(await new PydiggsHttpClient({ baseUrl: "http://h", fetch: up.fetch }).ready(), true);
  assert.equal(up.seen[0].url.pathname, "/v1/readyz");
  const warming = fakeFetch(() => problem(503, "not-ready"));
  assert.equal(await new PydiggsHttpClient({ baseUrl: "http://h", fetch: warming.fetch }).ready(), false);
  const refused = fakeFetch(() => problem(403, "cross-site"));
  await assert.rejects(new PydiggsHttpClient({ baseUrl: "http://h", fetch: refused.fetch }).ready(), PydiggsHttpError);
});

test("rules: the step filter and an escaped rule id", async () => {
  const rule = { id: "schema.x", pattern: null, step: "schema", description: "", severities: [], help_uri: "", known: true };
  const { fetch, seen } = fakeFetch((request) => json(request.url.pathname === "/v1/rules" ? [rule] : rule));
  const client = new PydiggsHttpClient({ baseUrl: "http://h", fetch });
  assert.deepEqual(await client.listRules({ step: "schema" }), [rule]);
  assert.equal(seen[0].url.searchParams.get("step"), "schema");
  assert.deepEqual(await client.explainRule("schema.a/b"), rule);
  assert.equal(seen[1].url.pathname, "/v1/rules/schema.a%2Fb");
});

test("headers come from the option, static or computed per request, and the signal is passed on", async () => {
  const { fetch, seen } = fakeFetch(() => json({ pydiggs_version: "1.7.0" }));
  let calls = 0;
  const client = new PydiggsHttpClient({
    baseUrl: "http://h",
    fetch,
    headers: async () => ({ Authorization: `Bearer t${++calls}` }),
  });
  const controller = new AbortController();
  await client.versions({ signal: controller.signal });
  await client.versions();
  assert.equal(headerOf(seen[0], "Authorization"), "Bearer t1");
  assert.equal(headerOf(seen[1], "Authorization"), "Bearer t2");
  assert.equal(seen[0].init.signal, controller.signal);
  const fixed = new PydiggsHttpClient({ baseUrl: "http://h", fetch, headers: { "X-Key": "k" } });
  await fixed.versions();
  assert.equal(headerOf(seen[2], "X-Key"), "k");
});

test("a relative base URL is used as written", async () => {
  const urls: string[] = [];
  const fetch = (async (input: RequestInfo | URL) => {
    urls.push(String(input));
    return json({ pydiggs_version: "1.7.0" });
  }) as typeof globalThis.fetch;
  await new PydiggsHttpClient({ baseUrl: "/api/pydiggs/", fetch }).versions();
  assert.deepEqual(urls, ["/api/pydiggs/v1/versions"]);
});
