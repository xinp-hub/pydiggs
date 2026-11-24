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
export type RequestKind =
  | "validate"
  | "read"
  | "rules"
  | "explain"
  | "versions"
  | "convert"
  | "ags4-check"
  | "diff"
  | "capabilities"
  | "tables"
  | "tables-from-diggs";

/** The request's parameters, in the HTTP API's own names. */
export interface RequestParameters {
  /** validate: the steps to run. */
  checks?: string[];
  /** validate, read: the DIGGS version to use instead of the detected one. */
  diggs_version?: string;
  /** validate, ags4-check: "findings" (default) or "sarif". */
  format?: "findings" | "sarif";
  /** convert: the target format, "diggs" (the default) or "ags4". */
  to?: "diggs" | "ags4";
  /** convert to "ags4": the AGS4 version of the output. */
  ags_version?: string;
  /** read: which result tables keep their values. */
  select?: { results: "all" | "none" | string[] };
  /** diff: how records are paired. */
  match?: string;
  /** diff: the relative tolerance for numbers. */
  rel_tol?: number;
  /** diff: the absolute tolerance for numbers. */
  abs_tol?: number;
  /** diff: most changes listed. */
  max_changes?: number;
  /** diff: also list values only written differently. */
  show_reformatted?: boolean;
  /** diff: also list records whose gml:id changed. */
  show_renamed?: boolean;
  /** diff, tables, tables-from-diggs: the largest answer pyDIGGS may build, in bytes. */
  max_result_bytes?: number;
  /** tables: what to do with the package, as `POST /v1/tables/<operation>`. */
  operation?: "to-diggs" | "check" | "round-trip";
  /** rules: only this step's rules. */
  step?: string;
  /** explain: the rule id. */
  rule_id?: string;
}

/** page -> worker. `data` is transferred. */
export interface RequestMessage {
  id: number;
  type: RequestKind;
  /** validate, read: the file name to report; diff: the old file's; tables: the package's. */
  name?: string;
  /**
   * validate, read, convert, ags4-check, tables-from-diggs: the document; diff: the old
   * file; tables: the package, a ZIP.
   */
  data?: ArrayBuffer;
  /** diff: the new file's name to report. */
  newName?: string;
  /** diff: the new file. Transferred like `data`. */
  newData?: ArrayBuffer;
  /** ags4-check: an AGS4 data dictionary. Transferred like `data`. */
  dictionary?: ArrayBuffer;
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
