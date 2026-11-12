// The worker thread behind "@pydiggs/client/node". It runs the same worker core as the
// browser, with Pyodide from the npm `pyodide` package in place of the CDN, so a Node
// server validates exactly as a browser tab does. Running in a worker thread keeps a
// long validation off the server's event loop.

import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import { parentPort } from "node:worker_threads";

import { PYODIDE_VERSION } from "./generated/pins.ts";
import type { InitMessage } from "./protocol.ts";
import { createWorkerCore, type PyodideLike } from "./worker-core.ts";

interface NodePyodide extends PyodideLike {
  FS: {
    mkdirTree(path: string): void;
    writeFile(path: string, data: Uint8Array): void;
  };
}

interface PyodideModule {
  loadPyodide(options?: { packageCacheDir?: string }): Promise<NodePyodide>;
}

// Where a wheel from disk is put inside Pyodide's file system for micropip to install.
const WHEEL_DIR = "/pydiggs-wheel";

const port = parentPort;
if (!port) throw new Error("@pydiggs/client/node: node-worker runs only as a worker thread");

let wheelPath: string | undefined;

const handle = createWorkerCore({
  post: (message) => port.postMessage(message),
  loadPyodide: async (init: InitMessage): Promise<NodePyodide> => {
    const specifier = init.pyodideModuleUrl ?? "pyodide";
    let module: PyodideModule;
    try {
      module = (await import(specifier)) as PyodideModule;
    } catch {
      throw new Error(`cannot load Pyodide; install it next to @pydiggs/client: npm install pyodide@${PYODIDE_VERSION}`);
    }
    const pyodide = await module.loadPyodide(init.packageCacheDir ? { packageCacheDir: init.packageCacheDir } : {});
    // lxml comes from this Pyodide release, and CI checks byte-equality with CPython
    // for exactly this release. Another version may give other answers.
    if (pyodide.version !== PYODIDE_VERSION) {
      throw new Error(
        `@pydiggs/client needs pyodide ${PYODIDE_VERSION}, found ${pyodide.version}: npm install pyodide@${PYODIDE_VERSION}`,
      );
    }
    if (wheelPath !== undefined) {
      pyodide.FS.mkdirTree(WHEEL_DIR);
      pyodide.FS.writeFile(`${WHEEL_DIR}/${basename(wheelPath)}`, await readFile(wheelPath));
    }
    return pyodide;
  },
});

port.on("message", (message: unknown): void => {
  const init = message as Partial<InitMessage> | null;
  if (init?.type === "init" && typeof init.pydiggsWheelPath === "string" && wheelPath === undefined) {
    wheelPath = init.pydiggsWheelPath;
    handle({ ...init, pydiggsWheelUrl: `emfs:${WHEEL_DIR}/${basename(wheelPath)}` });
    return;
  }
  handle(message);
});
