import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { PYODIDE_VERSION } from "../src/generated/pins.ts";
import { FINDINGS_SCHEMA_URL, MODEL_SCHEMA_URL, type DiggsFindingsFinding, type DiggsModelQuantity } from "../src/index.ts";

const repo = new URL("../../", import.meta.url);

test("the generated files match pydiggs's contracts", () => {
  const script = fileURLToPath(new URL("../scripts/generate.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [script, "--check"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("the Pyodide pin is the one CI tests pydiggs against", () => {
  const facts = JSON.parse(readFileSync(new URL("tests/pyodide/pyodide-facts.json", repo), "utf8"));
  assert.equal(PYODIDE_VERSION, facts.pyodide);
});

test("the shipped schemas carry the exported URLs", () => {
  const read = (name: string) => JSON.parse(readFileSync(new URL(`../schemas/${name}`, import.meta.url), "utf8"));
  assert.equal(read("diggs-findings-1.0.schema.json").$id, FINDINGS_SCHEMA_URL);
  assert.equal(read("diggs-model-1.0.schema.json").$id, MODEL_SCHEMA_URL);
});

test("a document literal fits the generated types", () => {
  // Checked by `npm run typecheck`; at run time this only shows the shapes.
  const finding: DiggsFindingsFinding = {
    layer: "native",
    step: "schema",
    rule_id: "schema.invalid",
    severity: "error",
    message: "Element is not expected.",
    location: { line: 12, column: 3, xpath: "/diggs:Diggs[1]", gml_id: null },
  };
  const depth: DiggsModelQuantity = { text: "160.00", value: 160, uom: "ft", si: { value: 48.768, uom: "m" } };
  assert.equal(finding.severity, "error");
  assert.equal(depth.si?.uom, "m");
});
