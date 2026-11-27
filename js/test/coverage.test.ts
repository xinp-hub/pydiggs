// js/capabilities-coverage.json is true to the code: every method it says the client
// offers exists on every engine. (tests/test_js_client_coverage.py holds the file to
// pydiggs.capabilities(), so between them a new pyDIGGS operation cannot go unnoticed.)

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { PydiggsHttpClient } from "../src/http.ts";
import { PydiggsHybrid } from "../src/hybrid.ts";
import { PydiggsPool } from "../src/pool.ts";
import { SupervisedWorker } from "../src/worker-client.ts";

interface Offered {
  methods: string[];
  hybrid: boolean;
  /** "pyodide": on the engines that run pyDIGGS itself only (pyDIGGS has no HTTP route). */
  engines?: "pyodide";
}

interface Planned {
  /** The methods the client will add for this operation. */
  methods: string[];
  /** Why it is not offered yet, and where it is planned. */
  why: string;
  /** "pyodide": planned for the engines that run pyDIGGS itself only. */
  engines?: "pyodide";
}

const coverage = JSON.parse(readFileSync(new URL("../capabilities-coverage.json", import.meta.url), "utf8")) as {
  offered: Record<string, Offered>;
  planned: Record<string, Planned>;
  not_offered: Record<string, string>;
};

// PydiggsBrowser and PydiggsNode are SupervisedWorkers; PydiggsPool spreads over them.
const engines: Record<string, object> = {
  PydiggsHttpClient: PydiggsHttpClient.prototype,
  SupervisedWorker: SupervisedWorker.prototype,
  PydiggsPool: PydiggsPool.prototype,
};

// The engines an entry is about: all of them, or those that run pyDIGGS itself.
function enginesOf(op: string, entry: { engines?: string }): Record<string, object> {
  if (entry.engines === undefined) return engines;
  assert.equal(entry.engines, "pyodide", `${op}: engines is "pyodide" or is left out`);
  const { PydiggsHttpClient: _http, ...pyodide } = engines;
  return pyodide;
}

test("every offered operation is a method on every engine", () => {
  for (const [op, entry] of Object.entries(coverage.offered)) {
    assert.ok(!(entry.engines && entry.hybrid), `${op}: an operation of the Pyodide engines only is not hybrid`);
    const among = enginesOf(op, entry);
    const where = entry.hybrid ? { ...among, PydiggsHybrid: PydiggsHybrid.prototype } : among;
    for (const method of entry.methods) {
      for (const [engine, prototype] of Object.entries(where)) {
        assert.equal(
          typeof (prototype as Record<string, unknown>)[method],
          "function",
          `${op}: ${engine} has no ${method}()`,
        );
      }
    }
  }
});

// A plan that has been carried out is no longer a plan: when every method a planned
// entry names exists on every engine, the entry belongs in "offered".
test("a planned operation is not already offered", () => {
  const has = (prototype: object, method: string) =>
    typeof (prototype as Record<string, unknown>)[method] === "function";
  for (const [op, entry] of Object.entries(coverage.planned)) {
    assert.ok(entry.methods.length > 0 && entry.why.trim() !== "", `planned.${op} needs methods and why`);
    const among = Object.values(enginesOf(op, entry));
    const built = entry.methods.every((method) => among.every((prototype) => has(prototype, method)));
    assert.ok(!built, `planned.${op}: ${entry.methods.join(", ")} exist on every engine; move it to "offered"`);
  }
});

// pyDIGGS has no HTTP route for an operation of the Pyodide engines only, so the HTTP
// client must not grow a method for one by accident.
test("an operation of the Pyodide engines only has no method on the HTTP client", () => {
  for (const [op, entry] of Object.entries({ ...coverage.offered, ...coverage.planned })) {
    if (entry.engines !== "pyodide") continue;
    for (const method of entry.methods) {
      assert.notEqual(
        typeof (PydiggsHttpClient.prototype as unknown as Record<string, unknown>)[method],
        "function",
        `${op}: PydiggsHttpClient has ${method}(), and pyDIGGS has no HTTP route for it`,
      );
    }
  }
});

test("an operation is in one section only", () => {
  const seen = new Map<string, string>();
  for (const section of ["offered", "planned", "not_offered"] as const) {
    for (const op of Object.keys(coverage[section])) {
      assert.ok(!seen.has(op), `${op} is in ${seen.get(op)} and ${section}`);
      seen.set(op, section);
    }
  }
});
