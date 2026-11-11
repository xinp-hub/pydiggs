// The Web Worker behind "@pydiggs/client/browser". It loads Pyodide once, installs
// pydiggs without its optional dependencies, warms it up, then answers requests one at
// a time (Pyodide is single-threaded). The document stays in this worker: nothing here
// sends it anywhere.
//
// This file only fetches Pyodide and connects the worker's messages; the rest is
// worker-core.ts. It follows examples/browser-worker/worker.mjs in the pydiggs
// repository. The differences: the page sends the configuration in an "init" message,
// and `read` takes `results`.

import { PYODIDE_SRI, PYODIDE_VERSION } from "./generated/pins.ts";
import type { InitMessage, ReplyMessage } from "./protocol.ts";
import { createWorkerCore, type PyodideLike } from "./worker-core.ts";

interface PyodideModule {
  loadPyodide(options: { indexURL: string }): Promise<PyodideLike>;
}

// import() takes no integrity option and module workers have no import maps, so the
// loader is fetched with Subresource Integrity and imported from a blob: URL.
async function importPyodide(base: string): Promise<PyodideModule> {
  const response = await fetch(`${base}pyodide.mjs`, {
    integrity: PYODIDE_SRI,
    credentials: "omit",
    referrerPolicy: "no-referrer",
  });
  if (!response.ok) throw new Error(`pyodide.mjs: HTTP ${response.status}`);
  const blob = new Blob([await response.arrayBuffer()], { type: "text/javascript" });
  const url = URL.createObjectURL(blob);
  try {
    return (await import(/* @vite-ignore */ /* webpackIgnore: true */ url)) as PyodideModule;
  } finally {
    URL.revokeObjectURL(url);
  }
}

const handle = createWorkerCore({
  post: (message: ReplyMessage): void => self.postMessage(message),
  loadPyodide: async (init: InitMessage): Promise<PyodideLike> => {
    const base = init.pyodideBaseUrl ?? `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
    const { loadPyodide } = await importPyodide(base);
    return loadPyodide({ indexURL: base });
  },
});

self.addEventListener("message", (event: MessageEvent): void => handle(event.data));
