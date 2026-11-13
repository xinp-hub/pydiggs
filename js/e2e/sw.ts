// The test app's service worker: the package's offline cache for pydiggs's files, plus
// the simplest app-shell cache for the page itself (network first, the cache when
// offline). Built to /sw.js and registered by offline.ts.
/// <reference lib="webworker" />
/// <reference types="vite/client" />

import { cachePydiggs } from "@pydiggs/client/sw";

const scope = self as unknown as ServiceWorkerGlobalScope;
const wheel = import.meta.env.VITE_PYDIGGS_WHEEL as string | undefined;
const pyodideBase = import.meta.env.VITE_PYODIDE_BASE as string | undefined;

cachePydiggs(scope, {
  ...(wheel ? { pydiggsWheelUrl: wheel } : {}),
  ...(pyodideBase ? { pyodideBaseUrl: pyodideBase } : {}),
});

const SHELL = "app-shell";
scope.addEventListener("install", () => void scope.skipWaiting());
scope.addEventListener("activate", (event) => event.waitUntil(scope.clients.claim()));
scope.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Same-origin page files only; the package's handler above answers for its own.
  if (event.request.method !== "GET" || url.origin !== scope.location.origin || url.pathname.endsWith(".whl")) return;
  if (pyodideBase && url.href.startsWith(new URL(pyodideBase, scope.location.href).href)) return;
  event.respondWith(
    fetch(event.request)
      .then(async (response) => {
        if (response.ok) await (await caches.open(SHELL)).put(event.request, response.clone());
        return response;
      })
      .catch(async () => (await caches.match(event.request)) ?? Response.error()),
  );
});
