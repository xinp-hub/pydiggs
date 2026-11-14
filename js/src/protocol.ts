// Messages between the page side (worker-client.ts) and the worker (worker-core.ts).

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
  /** URL of a pure pydiggs wheel. Default: the pinned release, checked against its sha256. */
  pydiggsWheelUrl?: string;
  /** Largest document accepted, in bytes. */
  maxBytes: number;
  /**
   * One byte the page writes 2 (SIGINT) into to stop the Python that is running, as
   * Pyodide's setInterruptBuffer documents. Absent where SharedArrayBuffer is not
   * available (a page that is not cross-origin isolated).
   */
  interruptBuffer?: SharedArrayBuffer;
  /** Node only: import specifier or file URL of Pyodide's `pyodide.mjs`. Default: "pyodide". */
  pyodideModuleUrl?: string;
  /** Node only: directory where Pyodide keeps the packages it downloads. */
  packageCacheDir?: string;
  /** Node only: path of a pure pydiggs wheel on disk, installed with no download. */
  pydiggsWheelPath?: string;
  /** Node only, experimental: directory for a start-up snapshot of the Pyodide runtime. */
  startupSnapshotDir?: string;
}

/** What a request asks of pydiggs's service layer, as the HTTP API does. */
export type RequestKind = "validate" | "read" | "rules" | "explain" | "versions";

/** The request's parameters, in the HTTP API's own names. */
export interface RequestParameters {
  /** validate: the steps to run. */
  checks?: string[];
  /** validate, read: the DIGGS version to use instead of the detected one. */
  diggs_version?: string;
  /** validate: "findings" (default) or "sarif". */
  format?: "findings" | "sarif";
  /** read: which result tables keep their values. */
  select?: { results: "all" | "none" | string[] };
  /** rules: only this step's rules. */
  step?: string;
  /** explain: the rule id. */
  rule_id?: string;
}

/** page -> worker. `data` is transferred. */
export interface RequestMessage {
  id: number;
  type: RequestKind;
  /** validate, read: the file name to report. */
  name?: string;
  /** validate, read: the document. */
  data?: ArrayBuffer;
  parameters?: RequestParameters;
}

/** Why pydiggs produced no document, as its service layer and HTTP API name it. */
export interface ServiceProblem {
  code: string;
  detail?: string;
  errors?: { parameter: string; problem: string }[];
  kind?: string | null;
}

/** worker -> page. */
export type ReplyMessage =
  | { type: "progress"; stage: LoadStage }
  | { type: "ready"; versions: RuntimeVersions }
  | { type: "failed"; message: string }
  | { id: number; type: "result"; document: unknown }
  | { id: number; type: "error"; message: string; problem?: ServiceProblem };
