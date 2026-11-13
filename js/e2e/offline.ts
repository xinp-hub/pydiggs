// The offline recipe: a page whose service worker uses @pydiggs/client/sw. Playwright
// (tests/offline.spec.ts) loads it once online, then reloads it offline and validates.
/// <reference types="vite/client" />

import { PydiggsBrowser } from "@pydiggs/client/browser";
import { CACHE_NAME } from "@pydiggs/client/sw";

const $ = (id: string): HTMLElement => document.getElementById(id)!;
const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));
const wheel = import.meta.env.VITE_PYDIGGS_WHEEL as string | undefined;
const pyodideBase = import.meta.env.VITE_PYODIDE_BASE as string | undefined;

async function start(): Promise<void> {
  // Wait for the service worker to control the page, so the worker's downloads go
  // through it from the first visit.
  await navigator.serviceWorker.register("/sw.js", { type: "module", scope: "/" });
  if (!navigator.serviceWorker.controller) {
    await new Promise((resolve) => navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }));
  }
  $("sw").textContent = "controlled";

  const pydiggs = new PydiggsBrowser({
    ...(wheel ? { pydiggsWheelUrl: new URL(wheel, location.href) } : {}),
    ...(pyodideBase ? { pyodideBaseUrl: new URL(pyodideBase, location.href) } : {}),
  });
  pydiggs.ready.then(
    () => ($("status").textContent = "ready"),
    (error: unknown) => {
      $("status").textContent = "failed";
      $("error").textContent = message(error);
    },
  );
  const input = $("file") as HTMLInputElement;
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      $("findings").textContent = JSON.stringify(await pydiggs.validate(file));
      const cache = await caches.open(CACHE_NAME);
      $("cached").textContent = JSON.stringify((await cache.keys()).map((request) => request.url));
      $("result-status").textContent = `done:${file.name}`;
    } catch (error) {
      $("result-status").textContent = `error:${file.name}`;
      $("error").textContent = message(error);
    }
  });
}

start().catch((error: unknown) => {
  $("sw").textContent = "failed";
  $("error").textContent = message(error);
});
