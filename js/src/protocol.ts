// Messages between the page (browser.ts) and the worker (browser-worker.ts).

/** Versions the worker reports once it has loaded. */
export interface RuntimeVersions {
  pyodide: string;
  pydiggs: string;
  lxml: string;
}

/**
 * What the worker is doing while it loads, in order: downloading and starting the
 * Python runtime, loading lxml, installing pydiggs, compiling its schemas.
 */
export type LoadStage = "runtime" | "packages" | "pydiggs" | "warm-up";

/** page -> worker, once, before anything else. */
export interface InitMessage {
  type: "init";
  /** Directory of a Pyodide "full" distribution, ending in "/". Default: the jsDelivr CDN. */
  pyodideBaseUrl?: string;
  /** URL of a pure pydiggs wheel. Default: the pinned release from PyPI. */
  pydiggsWheelUrl?: string;
  /** Largest document accepted, in bytes. */
  maxBytes: number;
  /** Node only: import specifier or file URL of Pyodide's `pyodide.mjs`. Default: "pyodide". */
  pyodideModuleUrl?: string;
  /** Node only: directory where Pyodide keeps the packages it downloads. */
  packageCacheDir?: string;
  /** Node only: path of a pure pydiggs wheel on disk, installed with no download. */
  pydiggsWheelPath?: string;
}

/** page -> worker. `data` is transferred. */
export interface RequestMessage {
  id: number;
  type: "validate" | "read";
  name: string;
  data: ArrayBuffer;
  /** `read` only: whether result tables keep their values. */
  results?: boolean;
}

/** worker -> page. */
export type ReplyMessage =
  | { type: "progress"; stage: LoadStage }
  | { type: "ready"; versions: RuntimeVersions }
  | { type: "failed"; message: string }
  | { id: number; type: "result"; document: unknown }
  | { id: number; type: "error"; message: string };
