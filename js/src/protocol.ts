// Messages between the page (browser.ts) and the worker (browser-worker.ts).

/** Versions the worker reports once it has loaded. */
export interface RuntimeVersions {
  pyodide: string;
  pydiggs: string;
  lxml: string;
}

/** page -> worker, once, before anything else. */
export interface InitMessage {
  type: "init";
  /** Directory of a Pyodide "full" distribution, ending in "/". Default: the jsDelivr CDN. */
  pyodideBaseUrl?: string;
  /** URL of a pure pydiggs wheel. Default: the pinned release from PyPI. */
  pydiggsWheelUrl?: string;
  /** Largest document accepted, in bytes. */
  maxBytes: number;
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
  | { type: "ready"; versions: RuntimeVersions }
  | { type: "failed"; message: string }
  | { id: number; type: "result"; document: unknown }
  | { id: number; type: "error"; message: string };
