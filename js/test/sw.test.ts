import assert from "node:assert/strict";
import { test } from "node:test";

import { PYDIGGS_WHEEL, PYODIDE_VERSION } from "../src/generated/pins.ts";
import { CACHE_NAME, cachePydiggs, type ServiceWorkerScopeLike } from "../src/sw.ts";

// A pyDIGGS release PR bumps PYDIGGS_VERSION before that release is on PyPI, so no
// wheel is pinned (PYDIGGS_WHEEL is null) until scripts/follow-pydiggs.mjs pins it.
const UNPINNED = "no pyDIGGS wheel is pinned yet (scripts/follow-pydiggs.mjs pins it once the release is on PyPI)";

// A service worker scope with an in-memory Cache API and a network that counts requests.
function scope(network: Record<string, () => Response>) {
  const stores = new Map<string, Map<string, Response>>([["pydiggs-pyodide-0-pydiggs-0", new Map()], ["app", new Map()]]);
  const listeners: Record<string, (event: any) => void> = {};
  const fetched: string[] = [];
  const open = async (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const store = stores.get(name)!;
    return {
      match: async (url: string) => store.get(url)?.clone(),
      put: async (url: string, response: Response) => void store.set(url, response),
      addAll: async (urls: string[]) => {
        for (const url of urls) {
          const response = await fake.fetch(url);
          if (!response.ok) throw new Error(`${url}: ${response.status}`);
          store.set(url, response);
        }
      },
    } as unknown as Cache;
  };
  const fake: ServiceWorkerScopeLike = {
    addEventListener: (type, listener) => void (listeners[type] = listener),
    location: { href: "https://app.test/sw.js" },
    caches: {
      open,
      keys: async () => [...stores.keys()],
      delete: async (name: string) => stores.delete(name),
    } as unknown as CacheStorage,
    fetch: (async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      fetched.push(url);
      return network[url]?.() ?? new Response("missing", { status: 404 });
    }) as typeof globalThis.fetch,
  };
  // Fires an event; returns what the handler answered, if it answered.
  const fire = async (type: string, request?: Request): Promise<Response | undefined> => {
    let answer: Promise<Response> | undefined;
    const waits: Promise<unknown>[] = [];
    listeners[type]?.({
      request,
      respondWith: (response: Promise<Response>) => void (answer = response),
      waitUntil: (promise: Promise<unknown>) => void waits.push(promise),
    });
    await Promise.all(waits);
    return answer ? await answer : undefined;
  };
  return { fake, fire, fetched, stores };
}

const CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

test("Pyodide's files and the wheel are fetched once, then served from the cache", { skip: PYDIGGS_WHEEL ? false : UNPINNED }, async () => {
  const wasm = `${CDN}pyodide.asm.wasm`;
  const { fake, fire, fetched } = scope({ [wasm]: () => new Response("wasm"), [PYDIGGS_WHEEL!.url]: () => new Response("whl") });
  const routes = cachePydiggs(fake);
  assert.deepEqual(routes, { pyodideBaseUrl: CDN, pydiggsWheelUrl: PYDIGGS_WHEEL!.url });
  for (let i = 0; i < 2; i++) {
    assert.equal(await (await fire("fetch", new Request(wasm)))!.text(), "wasm");
    assert.equal(await (await fire("fetch", new Request(PYDIGGS_WHEEL!.url)))!.text(), "whl");
  }
  assert.deepEqual(fetched, [wasm, PYDIGGS_WHEEL!.url], "the second round came from the cache");
});

test("other requests, errors and non-GET requests are left alone", async () => {
  const missing = `${CDN}nope.whl`;
  const { fake, fire, fetched, stores } = scope({});
  cachePydiggs(fake);
  assert.equal(await fire("fetch", new Request("https://app.test/index.html")), undefined);
  assert.equal(await fire("fetch", new Request(`${CDN}pyodide.mjs`, { method: "POST", body: "x" })), undefined);
  assert.equal((await fire("fetch", new Request(missing)))!.status, 404);
  assert.equal((await fire("fetch", new Request(missing)))!.status, 404);
  assert.deepEqual(fetched, [missing, missing], "a 404 is never cached");
  assert.equal(stores.get(CACHE_NAME)?.size ?? 0, 0);
});

test("a self-hosted folder and wheel are matched instead of the CDN", async () => {
  const { fake, fire, fetched } = scope({
    "https://app.test/pydiggs/pyodide.mjs": () => new Response("loader"),
    "https://app.test/pydiggs/pydiggs-1.8.0-py3-none-any.whl": () => new Response("whl"),
  });
  const routes = cachePydiggs(fake, { pyodideBaseUrl: "/pydiggs", pydiggsWheelUrl: "/pydiggs/pydiggs-1.8.0-py3-none-any.whl" });
  assert.equal(routes.pyodideBaseUrl, "https://app.test/pydiggs/");
  await fire("fetch", new Request("https://app.test/pydiggs/pyodide.mjs"));
  await fire("fetch", new Request("https://app.test/pydiggs/pyodide.mjs"));
  assert.equal(await fire("fetch", new Request(`${CDN}pyodide.mjs`)), undefined, "the CDN is not ours any more");
  assert.deepEqual(fetched, ["https://app.test/pydiggs/pyodide.mjs"]);
});

test("activate deletes older pydiggs caches and nothing else", async () => {
  const { fake, fire, stores } = scope({});
  cachePydiggs(fake);
  await fire("activate");
  assert.deepEqual([...stores.keys()].sort(), ["app"]);
});

test("precache downloads the core files, the packages with their dependencies, and the wheel", { skip: PYDIGGS_WHEEL ? false : UNPINNED }, async () => {
  const lock = {
    packages: {
      lxml: { file_name: "lxml.whl", depends: ["six"] },
      six: { file_name: "six.whl" },
      micropip: { file_name: "micropip.whl", depends: [] },
    },
  };
  const network: Record<string, () => Response> = { [`${CDN}pyodide-lock.json`]: () => Response.json(lock) };
  for (const file of ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "lxml.whl", "six.whl", "micropip.whl"]) {
    network[`${CDN}${file}`] = () => new Response(file);
  }
  network[PYDIGGS_WHEEL!.url] = () => new Response("whl");
  const { fake, fire, stores } = scope(network);
  cachePydiggs(fake, { precache: true });
  await fire("install");
  const cached = [...stores.get(CACHE_NAME)!.keys()].map((url) => url.replace(CDN, "")).sort();
  assert.deepEqual(cached, [
    PYDIGGS_WHEEL!.url,
    "lxml.whl",
    "pyodide-lock.json",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "pyodide.mjs",
    "python_stdlib.zip",
    "six.whl",
  ].sort());
});
