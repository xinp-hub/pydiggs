import assert from "node:assert/strict";
import { test } from "node:test";

import type { BrowserStatus, PydiggsBrowser } from "../src/browser.ts";
import type { PydiggsHttpClient } from "../src/http.ts";
import { PydiggsError, PydiggsHybrid } from "../src/hybrid.ts";

// Stand-ins that record calls. The real clients are tested on their own; this tests
// only where the hybrid sends each document.
class FakeBrowser {
  status: BrowserStatus = "ready";
  calls: string[] = [];
  ready: Promise<unknown>;
  #settle: { resolve: (v: unknown) => void; reject: (e: unknown) => void } | undefined;
  fail: Error | undefined;

  constructor(status: BrowserStatus = "ready") {
    this.status = status;
    this.ready =
      status === "loading"
        ? new Promise((resolve, reject) => (this.#settle = { resolve, reject }))
        : status === "ready"
          ? Promise.resolve({})
          : Promise.reject(new Error("failed"));
    this.ready.catch(() => {});
  }
  finishLoading(ok: boolean): void {
    this.status = ok ? "ready" : "failed";
    if (ok) this.#settle?.resolve({});
    else this.#settle?.reject(new Error("load failed"));
  }
  async validate(_input: unknown, options: { name: string }) {
    this.calls.push(`validate ${options.name}`);
    if (this.fail) throw this.fail;
    return { from: "browser" };
  }
  async read(_input: unknown, options: { name: string; results: unknown }) {
    this.calls.push(`read ${options.name} ${options.results}`);
    if (this.fail) throw this.fail;
    return { from: "browser" };
  }
}

class FakeServer {
  calls: string[] = [];
  async validate(_input: unknown, options: { name: string }) {
    this.calls.push(`validate ${options.name}`);
    return { from: "server" };
  }
  async read(_input: unknown, options: { name: string; results: string }) {
    this.calls.push(`read ${options.name} ${options.results}`);
    return { from: "server" };
  }
}

function hybrid(options: { browser?: FakeBrowser; server?: FakeServer; maxBrowserBytes?: number; whileLoading?: "wait" | "server" }) {
  return new PydiggsHybrid({
    ...(options.browser ? { browser: options.browser as unknown as PydiggsBrowser } : {}),
    ...(options.server ? { server: options.server as unknown as PydiggsHttpClient } : {}),
    ...(options.maxBrowserBytes !== undefined ? { maxBrowserBytes: options.maxBrowserBytes } : {}),
    ...(options.whileLoading ? { whileLoading: options.whileLoading } : {}),
  });
}

const doc = (bytes: number) => new File([new Uint8Array(bytes)], "BH-01.xml");

test("a ready browser takes the document, and the server is not called", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  const result = await hybrid({ browser, server }).validate(doc(10));
  assert.deepEqual(result, { document: { from: "browser" }, via: "browser", reason: "browser" });
  assert.deepEqual(browser.calls, ["validate BH-01.xml"]);
  assert.deepEqual(server.calls, []);
});

test("a document over maxBrowserBytes goes to the server", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  const result = await hybrid({ browser, server, maxBrowserBytes: 5 }).validate(doc(6));
  assert.equal(result.via, "server");
  assert.equal(result.reason, "too-large");
  assert.deepEqual(browser.calls, []);
});

test("a failed worker sends documents to the server", async () => {
  const server = new FakeServer();
  const result = await hybrid({ browser: new FakeBrowser("failed"), server }).validate(doc(1));
  assert.deepEqual([result.via, result.reason], ["server", "browser-unavailable"]);
});

test("while loading, the default waits for the browser", async () => {
  const browser = new FakeBrowser("loading");
  const server = new FakeServer();
  const pending = hybrid({ browser, server }).validate(doc(1));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(browser.calls, []);
  browser.finishLoading(true);
  const result = await pending;
  assert.equal(result.via, "browser");
  assert.deepEqual(server.calls, []);
});

test("while loading, whileLoading: server answers from the server at once", async () => {
  const browser = new FakeBrowser("loading");
  const server = new FakeServer();
  const result = await hybrid({ browser, server, whileLoading: "server" }).validate(doc(1));
  assert.deepEqual([result.via, result.reason], ["server", "browser-loading"]);
});

test("a load failure while waiting sends the document to the server", async () => {
  const browser = new FakeBrowser("loading");
  const server = new FakeServer();
  const pending = hybrid({ browser, server }).validate(doc(1));
  browser.finishLoading(false);
  const result = await pending;
  assert.deepEqual([result.via, result.reason], ["server", "browser-unavailable"]);
});

test("a worker that dies on the document is retried on the server", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  browser.fail = new Error("the pydiggs worker failed: out of memory");
  const original = browser.validate.bind(browser);
  browser.validate = async (input, options) => {
    browser.status = "failed";
    return original(input, options);
  };
  const result = await hybrid({ browser, server }).validate(doc(1));
  assert.deepEqual([result.via, result.reason], ["server", "browser-unavailable"]);
  assert.deepEqual(server.calls, ["validate BH-01.xml"]);
});

test("a document past the browser's time limit is retried on the server", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  browser.fail = Object.assign(new Error("the document took longer than 120000 ms"), { code: "timeout" });
  const result = await hybrid({ browser, server }).validate(doc(1));
  assert.deepEqual([result.via, result.reason], ["server", "browser-timeout"]);
  assert.deepEqual(server.calls, ["validate BH-01.xml"]);
});

test("a timed-out document without a server is an error, not an upload", async () => {
  const browser = new FakeBrowser();
  browser.fail = Object.assign(new Error("the document took longer than 120000 ms"), { code: "timeout" });
  await assert.rejects(hybrid({ browser }).validate(doc(1)), /took longer/);
});

test("an error about the document is not retried on the server", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  browser.fail = new Error("not a DIGGS document");
  await assert.rejects(hybrid({ browser, server }).read(doc(1)), /not a DIGGS document/);
  assert.deepEqual(server.calls, []);
});

test("with no server, nothing is uploaded: the browser-only limits become errors", async () => {
  await assert.rejects(
    hybrid({ browser: new FakeBrowser(), maxBrowserBytes: 5 }).validate(doc(6)),
    (error: unknown) => error instanceof PydiggsError && /larger than 5 bytes.*no server/.test(error.message),
  );
  await assert.rejects(hybrid({ browser: new FakeBrowser("failed") }).validate(doc(1)), /not available.*no server/);
  // whileLoading: "server" without a server waits instead.
  const browser = new FakeBrowser("loading");
  const pending = hybrid({ browser, whileLoading: "server" }).validate(doc(1));
  browser.finishLoading(true);
  assert.equal((await pending).via, "browser");
});

test("with no browser, everything goes to the server", async () => {
  const server = new FakeServer();
  const result = await hybrid({ server }).read(doc(1), { results: false, name: "x.xml" });
  assert.deepEqual([result.via, result.reason], ["server", "no-browser"]);
  assert.deepEqual(server.calls, ["read x.xml none"]);
});

test("read passes the same results choice to either route", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  await hybrid({ browser, server }).read(doc(1));
  await hybrid({ browser, server, maxBrowserBytes: 0 }).read(doc(1), { results: false });
  await hybrid({ browser, server, maxBrowserBytes: 0 }).read(doc(1));
  await hybrid({ browser, server }).read(doc(1), { results: ["k"] });
  assert.deepEqual(browser.calls, ["read BH-01.xml all", "read BH-01.xml k"]);
  assert.deepEqual(server.calls, ["read BH-01.xml none", "read BH-01.xml all"]);
});

test("an abort while waiting for the worker rejects with the abort reason", async () => {
  const browser = new FakeBrowser("loading");
  const controller = new AbortController();
  const pending = hybrid({ browser, server: new FakeServer() }).validate(doc(1), { signal: controller.signal });
  controller.abort(new Error("user left"));
  await assert.rejects(pending, /user left/);
});

test("route() reports the decision without sending anything", async () => {
  const browser = new FakeBrowser();
  const server = new FakeServer();
  const h = hybrid({ browser, server, maxBrowserBytes: 100 });
  assert.deepEqual(await h.route(10), { via: "browser", reason: "browser" });
  assert.deepEqual(await h.route(101), { via: "server", reason: "too-large" });
  assert.deepEqual([browser.calls, server.calls], [[], []]);
});

test("a hybrid needs at least one way to run pydiggs", () => {
  assert.throws(() => new PydiggsHybrid({}), PydiggsError);
});
