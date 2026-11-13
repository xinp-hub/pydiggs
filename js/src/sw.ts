// Offline use for PydiggsBrowser, from your own service worker: Pyodide's files, lxml,
// micropip and the pydiggs wheel are kept in the Cache API the first time the worker
// downloads them, so later visits load with no network at all.
//
//     // sw.ts, bundled into your service worker
//     import { cachePydiggs } from "@pydiggs/client/sw";
//     cachePydiggs(self as unknown as ServiceWorkerGlobalScope);
//
// Every cached file is versioned (the pinned Pyodide and pydiggs are in its URL or its
// cache name), so it is served cache-first and never revalidated; a new release of this
// package uses a new cache and deletes the old one. The worker still checks the loader
// against `PYODIDE_SRI` and the wheel against its sha256, cached or not.
//
// This module adds event listeners and nothing else. It handles only the requests it
// recognises and leaves every other request to your own handlers.

import { PYDIGGS_VERSION, PYDIGGS_WHEEL, PYODIDE_VERSION } from "./generated/pins.ts";

/** The parts of a service worker's global scope this module uses. */
export interface ServiceWorkerScopeLike {
  addEventListener(type: "install" | "activate" | "fetch", listener: (event: any) => void): void;
  caches: CacheStorage;
  location: { href: string };
  fetch: typeof globalThis.fetch;
}

export interface CachePydiggsOptions {
  /**
   * Directory of the Pyodide distribution, as passed to `PydiggsBrowser` as
   * `pyodideBaseUrl`. Default: the jsDelivr CDN for the pinned release.
   */
  pyodideBaseUrl?: string | URL;
  /** The pydiggs wheel, as passed to `PydiggsBrowser` as `pydiggsWheelUrl`. Default: the pinned release. */
  pydiggsWheelUrl?: string | URL;
  /**
   * Download everything while the service worker installs, so even the first page
   * load can go offline. Default: false: files are kept as the page first uses them.
   */
  precache?: boolean;
  /** Pyodide packages to precache, with what they depend on. Default: lxml and micropip. */
  packages?: string[];
}

/** Prefix of every cache this module makes; anything else is left alone. */
export const CACHE_PREFIX = "pydiggs-";

/** The cache for this release of the package. */
export const CACHE_NAME: string = `${CACHE_PREFIX}pyodide-${PYODIDE_VERSION}-pydiggs-${PYDIGGS_VERSION}`;

// Pyodide's own files, which every load needs.
const CORE_FILES = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"];

interface FetchEventLike {
  request: Request;
  respondWith(response: Promise<Response>): void;
}

interface ExtendableEventLike {
  waitUntil(promise: Promise<unknown>): void;
}

function resolve(url: string | URL, base: string): string {
  return new URL(url, base).href;
}

async function precacheAll(scope: ServiceWorkerScopeLike, base: string, wheel: string, packages: string[]): Promise<void> {
  const cache = await scope.caches.open(CACHE_NAME);
  const lockResponse = await scope.fetch(new URL("pyodide-lock.json", base).href);
  if (!lockResponse.ok) throw new Error(`pyodide-lock.json: HTTP ${lockResponse.status}`);
  const lock = (await lockResponse.clone().json()) as {
    packages: Record<string, { file_name: string; depends?: string[] }>;
  };
  await cache.put(new URL("pyodide-lock.json", base).href, lockResponse);
  const files = new Set(CORE_FILES.filter((file) => file !== "pyodide-lock.json"));
  const wanted = [...packages];
  const seen = new Set<string>();
  while (wanted.length > 0) {
    const name = wanted.shift()!.toLowerCase();
    if (seen.has(name)) continue;
    seen.add(name);
    const entry = lock.packages[name];
    if (!entry) throw new Error(`Pyodide ${PYODIDE_VERSION} has no package ${JSON.stringify(name)}`);
    files.add(entry.file_name);
    wanted.push(...(entry.depends ?? []));
  }
  await cache.addAll([...[...files].map((file) => new URL(file, base).href), wheel]);
}

/**
 * Adds install, activate and fetch listeners to a service worker so that pydiggs's
 * files are served from the Cache API. Returns the URLs it answers for, to check.
 */
export function cachePydiggs(
  scope: ServiceWorkerScopeLike,
  options: CachePydiggsOptions = {},
): { pyodideBaseUrl: string; pydiggsWheelUrl: string | undefined } {
  const here = scope.location.href;
  const base = resolve(options.pyodideBaseUrl ?? `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`, here)
    .replace(/\/*$/, "/");
  const wheel = options.pydiggsWheelUrl !== undefined ? resolve(options.pydiggsWheelUrl, here) : PYDIGGS_WHEEL?.url;
  const ours = (url: string): boolean => url.startsWith(base) || url === wheel;

  scope.addEventListener("install", (event: ExtendableEventLike) => {
    if (options.precache && wheel) {
      event.waitUntil(precacheAll(scope, base, wheel, options.packages ?? ["lxml", "micropip"]));
    }
  });

  scope.addEventListener("activate", (event: ExtendableEventLike) => {
    event.waitUntil(
      scope.caches
        .keys()
        .then((names) =>
          Promise.all(
            names.filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME).map((name) => scope.caches.delete(name)),
          ),
        ),
    );
  });

  scope.addEventListener("fetch", (event: FetchEventLike) => {
    const { request } = event;
    if (request.method !== "GET" || !ours(request.url)) return;
    event.respondWith(
      (async (): Promise<Response> => {
        const cache = await scope.caches.open(CACHE_NAME);
        // The worker asks without credentials; match on the URL alone.
        const hit = await cache.match(request.url, { ignoreVary: true });
        if (hit) return hit;
        const response = await scope.fetch(request);
        // Only a complete answer is kept: never an error, a partial or an opaque one.
        if (response.ok && response.status === 200 && response.type !== "opaque") {
          await cache.put(request.url, response.clone());
        }
        return response;
      })(),
    );
  });

  return { pyodideBaseUrl: base, pydiggsWheelUrl: wheel };
}
