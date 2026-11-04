// Pyodide smoke for pydiggs (brief D10): install the locally built wheel into the pinned
// Pyodide under Node, with deps=False as a browser page would, and check that
//   - the wheel is pure (py3-none-any) and installs from the Emscripten filesystem;
//   - the running Pyodide matches pyodide-facts.json (Pyodide, CPython and lxml versions);
//   - every report is byte-identical to CPython's (goldens from cases.py, same checkout);
//   - HTML in an element value comes back verbatim, as data;
//   - the core path (import, warm_up, validation) executed no import of rich,
//     multiprocessing, fastapi, mcp or subprocess, and started no thread;
//   - the bundled UoM dictionary is on the Emscripten filesystem and in use;
//   - the 25 MiB limit refuses a larger document.
//
//   cd tests/pyodide && npm ci --ignore-scripts
//   uv build --wheel && uv run --no-sync python tests/pyodide/cases.py goldens.json
//   node tests/pyodide/smoke.mjs --wheel dist/pydiggs-X.Y.Z-py3-none-any.whl --goldens goldens.json
//
// Pyodide fetches lxml and micropip from its CDN and checks each against the sha256 in
// its pyodide-lock.json, so this needs network access.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { loadPyodide } from "pyodide";

const here = dirname(fileURLToPath(import.meta.url));
const { values: args } = parseArgs({
  options: { wheel: { type: "string" }, goldens: { type: "string" } },
});
assert.ok(args.wheel && args.goldens, "usage: smoke.mjs --wheel PATH --goldens PATH");

const facts = JSON.parse(readFileSync(join(here, "pyodide-facts.json"), "utf8"));
const goldens = JSON.parse(readFileSync(args.goldens, "utf8"));
const wheelName = basename(args.wheel);
const wheelMatch = /^pydiggs-([^-]+)-py3-none-any\.whl$/.exec(wheelName);
assert.ok(wheelMatch, `not a pure pydiggs wheel: ${wheelName}`);
const MARKUP = '<img src=x onerror="alert(document.domain)">';

const started = performance.now();
const elapsed = () => `${((performance.now() - started) / 1000).toFixed(1)} s`;

const pyodide = await loadPyodide();
assert.equal(pyodide.version, facts.pyodide, "pyodide-facts.json names another Pyodide");
await pyodide.loadPackage(["lxml", "micropip"], { messageCallback: () => {} });
console.log(`pyodide ${pyodide.version} ready (${elapsed()})`);

// Everything pydiggs reads comes from the Emscripten filesystem: the wheel, the fixtures
// and the shared cases module. Nothing is mounted from the host.
const FS = pyodide.FS;
FS.mkdirTree("/smoke/fixtures");
FS.writeFile(`/smoke/${wheelName}`, readFileSync(args.wheel));
FS.writeFile("/smoke/cases.py", readFileSync(join(here, "cases.py")));
for (const name of readdirSync(join(here, "fixtures"))) {
  FS.writeFile(`/smoke/fixtures/${name}`, readFileSync(join(here, "fixtures", name)));
}

const micropip = pyodide.pyimport("micropip");
await micropip.install.callKwargs(`emfs:/smoke/${wheelName}`, { deps: false });
// The import watcher goes in before pydiggs is first imported.
pyodide.runPython(`
import sys
sys.path.insert(0, "/smoke")
import cases
cases.watch_imports()
import pydiggs
pydiggs.warm_up()
`);
console.log(`pydiggs ${wheelMatch[1]} installed with deps=False and warmed up (${elapsed()})`);

const out = JSON.parse(
  pyodide.runPython(`
import json
results = cases.run("/smoke/fixtures")
refused = None
try:
    import pydiggs
    pydiggs.validate_bytes(b" " * (cases.MAX_BYTES + 1), max_bytes=cases.MAX_BYTES)
except pydiggs.InstanceReadError as exc:
    refused = type(exc).__name__
json.dumps({"results": results, "probe": cases.probe(), "refused": refused})
`),
);

// 1. Byte equality with CPython, per document.
assert.deepEqual(Object.keys(out.results).sort(), Object.keys(goldens).sort());
for (const [name, text] of Object.entries(out.results)) {
  const got = Buffer.from(text, "utf8");
  const want = Buffer.from(goldens[name], "utf8");
  assert.ok(got.equals(want), `${name}: Pyodide output differs from CPython's`);
  console.log(`ok  ${name}: ${got.length} bytes identical to CPython`);
}

// 2. Markup in an element value is data: it reaches a message unchanged.
const markupDoc = JSON.parse(out.results["markup_in_value.xml"]);
const hits = markupDoc.files[0].findings.filter((f) => f.message.includes(MARKUP));
assert.equal(hits.length, 1, "the markup must reach exactly one finding message verbatim");
console.log(`ok  markup round-trips verbatim in ${hits[0].rule_id}: ${JSON.stringify(MARKUP)}`);

// 3. The interpreter after validation.
const p = out.probe;
assert.equal(p.python, facts.python, "pyodide-facts.json names another CPython");
assert.equal(p.lxml, facts.lxml, "pyodide-facts.json names another lxml");
assert.equal(p.pydiggs, wheelMatch[1], "the installed pydiggs is not the wheel's version");
assert.deepEqual(p.forbidden_imported, {}, `forbidden imports: ${JSON.stringify(p.forbidden_imported)}`);
assert.deepEqual(p.forbidden_loaded, [], `forbidden modules loaded: ${p.forbidden_loaded}`);
assert.equal(p.threads, 1, "the core path started a thread");
assert.ok(p.uom_present, `UoM dictionary missing at ${p.uom_path}`);
assert.ok(p.uom_units > 100, `UoM dictionary loaded only ${p.uom_units} units`);
assert.ok(Math.abs(p.inch_in_mm - 25.4) < 1e-9, `1 in = ${p.inch_in_mm} mm`);
assert.equal(out.refused, "InstanceReadError", "a document over 25 MiB was not refused");
console.log(`ok  CPython ${p.python}, lxml ${p.lxml}, pydiggs ${p.pydiggs}`);
console.log("ok  no import of rich, multiprocessing, fastapi, mcp or subprocess; 1 thread");
// Known and tolerated (see cases.REPORTED): printed so a change is visible in the log.
console.log(`note imported, no connection made: ${JSON.stringify(p.reported_imported)}`);
console.log(`ok  UoM dictionary: ${p.uom_units} units from ${p.uom_path}`);
console.log("ok  a document over 25 MiB is refused (InstanceReadError)");
console.log(`pyodide smoke passed (${elapsed()})`);
