// The worker thread behind "@pydiggs/client/node". It runs the same worker core as the
// browser, with Pyodide from the npm `pyodide` package in place of the CDN, so a Node
// server validates exactly as a browser tab does. Running in a worker thread keeps a
// long validation off the server's event loop.

import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parentPort, threadId } from "node:worker_threads";

import { PYODIDE_VERSION } from "./generated/pins.ts";
import type { InitMessage } from "./protocol.ts";
import { createWorkerCore, type PyodideLike } from "./worker-core.ts";

interface NodePyodide extends PyodideLike {
  makeMemorySnapshot(): Uint8Array;
  FS: {
    mkdirTree(path: string): void;
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
  };
}

interface PyodideModule {
  loadPyodide(options?: {
    indexURL?: string;
    packageCacheDir?: string;
    stdout?: (line: string) => void;
    stderr?: (line: string) => void;
    _makeSnapshot?: boolean;
    _loadSnapshot?: Uint8Array;
  }): Promise<NodePyodide>;
}

type LoadOptions = Parameters<PyodideModule["loadPyodide"]>[0];

// The start-up snapshot (experimental, opt-in): the memory of a freshly started Pyodide
// runtime, before lxml or pydiggs, which restores in a fraction of the time a start takes.
// Pyodide's snapshot options are private, so this is tied to the pinned Pyodide (the
// version is in the file name) and falls back to a normal start whenever it fails. A
// snapshot with lxml loaded is not possible: Pyodide refuses it.
const SNAPSHOT_FORMAT = 1;

async function withSnapshot(module: PyodideModule, options: LoadOptions, dir: string): Promise<NodePyodide> {
  const file = join(dir, `pyodide-${PYODIDE_VERSION}-runtime-v${SNAPSHOT_FORMAT}.snapshot`);
  let saved: Uint8Array | undefined;
  try {
    saved = await readFile(file);
  } catch {
    saved = undefined; // none yet
  }
  if (saved) {
    try {
      return await module.loadPyodide({ ...options, _loadSnapshot: saved });
    } catch (error) {
      console.warn(`[pydiggs] the start-up snapshot could not be used, starting without it: ${String(error)}`);
      await rm(file, { force: true });
    }
  }
  const pyodide = await module.loadPyodide({ ...options, _makeSnapshot: true });
  try {
    const snapshot = pyodide.makeMemorySnapshot();
    await mkdir(dir, { recursive: true });
    // Written whole under another name, then renamed: a reader never sees half a file,
    // and engines starting together each write their own.
    const partial = `${file}.${process.pid}-${threadId}.partial`;
    await writeFile(partial, snapshot);
    await rename(partial, file);
  } catch (error) {
    console.warn(`[pydiggs] could not save a start-up snapshot: ${String(error)}`);
  }
  return pyodide;
}

// Where a wheel from disk is put inside Pyodide's file system, for the worker to unpack.
const WHEEL_DIR = "/pydiggs-wheel";

const port = parentPort;
if (!port) throw new Error("@pydiggs/client/node: node-worker runs only as a worker thread");

let wheelPath: string | undefined;

const handle = createWorkerCore({
  post: (message) => port.postMessage(message),
  loadPyodide: async (init: InitMessage): Promise<NodePyodide> => {
    const specifier = init.pyodideModuleUrl ?? "pyodide";
    let moduleUrl: string;
    let module: PyodideModule;
    try {
      moduleUrl = import.meta.resolve(specifier);
      module = (await import(moduleUrl)) as PyodideModule;
    } catch {
      throw new Error(`cannot load Pyodide; install it next to @pydiggs/client: npm install pyodide@${PYODIDE_VERSION}`);
    }
    // Name Pyodide's folder. Left to itself, Pyodide works it out from a stack trace, and
    // with source maps on (`node --enable-source-maps`, as `next dev` runs) that trace
    // names its original sources, so it looks for its WebAssembly where there is none.
    const indexURL = fileURLToPath(new URL(".", moduleUrl));
    const options: LoadOptions = {
      indexURL,
      ...(init.packageCacheDir ? { packageCacheDir: init.packageCacheDir } : {}),
      // Pyodide's default writes to the process's file descriptors, which a worker thread
      // does not have ("The fd argument must be of type number"). Python prints nothing
      // on pydiggs's path; anything on stderr goes to the server's log, marked.
      stdout: () => {},
      stderr: (line: string) => console.warn(`[pydiggs] ${line}`),
    };
    const pyodide = init.startupSnapshotDir
      ? await withSnapshot(module, options, init.startupSnapshotDir)
      : await module.loadPyodide(options);
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
