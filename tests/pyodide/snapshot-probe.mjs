// Experiment, not a test: can a Pyodide memory snapshot taken with lxml and pydiggs
// loaded make start-up faster, and does a restored interpreter still give CPython's
// answers? Pyodide's snapshot API is private (`_makeSnapshot`, `_loadSnapshot`,
// `makeMemorySnapshot`), so this records what works in the pinned release:
//
//   A. a cold start: runtime, lxml and micropip, the pydiggs wheel, warm-up;
//   B. a snapshot taken after all of A, then restored, then checked against CPython;
//   C. if B is refused, a snapshot of the bare runtime, restored, then the rest of A.
//
//   cd tests/pyodide && npm ci --ignore-scripts
//   node tests/pyodide/snapshot-probe.mjs --wheel dist/pydiggs-X.Y.Z-py3-none-any.whl --goldens goldens.json
//
// Prints a Markdown table (for the job summary) and exits 0 whatever it finds.

import { readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { loadPyodide } from "pyodide";

const here = dirname(fileURLToPath(import.meta.url));
const { values: args } = parseArgs({ options: { wheel: { type: "string" }, goldens: { type: "string" } } });
if (!args.wheel || !args.goldens) throw new Error("usage: snapshot-probe.mjs --wheel PATH --goldens PATH");
const goldens = JSON.parse(readFileSync(args.goldens, "utf8"));
const FIXTURE = "markup_in_value.xml";
const rows = [];
const ms = (start) => Math.round(performance.now() - start);

async function packagesAndPydiggs(pyodide) {
  await pyodide.loadPackage(["lxml", "micropip"], { messageCallback: () => {} });
  pyodide.FS.mkdirTree("/w");
  pyodide.FS.writeFile(`/w/${basename(args.wheel)}`, readFileSync(args.wheel));
  // Through Python, so no JavaScript proxy of micropip is left alive: a snapshot
  // refuses any reference JavaScript still holds into Python.
  await pyodide.runPythonAsync(
    `import micropip\nawait micropip.install(${JSON.stringify(`emfs:/w/${basename(args.wheel)}`)}, deps=False)`,
  );
  pyodide.runPython("import pydiggs; pydiggs.warm_up()");
}

function equalsCPython(pyodide) {
  pyodide.FS.writeFile("/w/doc.xml", readFileSync(join(here, "fixtures", FIXTURE)));
  const text = pyodide.runPython(
    `import pydiggs; pydiggs.validate_bytes(open("/w/doc.xml","rb").read(), name=${JSON.stringify(FIXTURE)}, max_bytes=25*1024*1024).to_json()`,
  );
  return text === goldens[FIXTURE];
}

// A. Cold start.
let start = performance.now();
const cold = await loadPyodide();
const runtimeMs = ms(start);
await packagesAndPydiggs(cold);
rows.push(["A. cold start (runtime + lxml + pydiggs + warm-up)", `${ms(start)} ms (runtime ${runtimeMs} ms)`, equalsCPython(cold) ? "yes" : "NO"]);

// B. Snapshot with everything loaded.
let full;
try {
  start = performance.now();
  const maker = await loadPyodide({ _makeSnapshot: true });
  await packagesAndPydiggs(maker);
  full = maker.makeMemorySnapshot();
  rows.push(["B1. snapshot after pydiggs is loaded", `${ms(start)} ms, ${(full.byteLength / 1048576).toFixed(1)} MiB`, ""]);
} catch (error) {
  rows.push(["B1. snapshot after pydiggs is loaded", `refused: ${String(error?.message ?? error).split("\n")[0]}`, ""]);
}
if (full) {
  try {
    start = performance.now();
    const restored = await loadPyodide({ _loadSnapshot: full });
    restored.runPython("import pydiggs");
    rows.push(["B2. restored from it", `${ms(start)} ms`, equalsCPython(restored) ? "yes" : "NO"]);
  } catch (error) {
    rows.push(["B2. restored from it", `failed: ${String(error?.message ?? error).split("\n")[0]}`, ""]);
  }
}

// C. Snapshot of the bare runtime, then the rest.
try {
  start = performance.now();
  const maker = await loadPyodide({ _makeSnapshot: true });
  const bare = maker.makeMemorySnapshot();
  rows.push(["C1. snapshot of the bare runtime", `${ms(start)} ms, ${(bare.byteLength / 1048576).toFixed(1)} MiB`, ""]);
  start = performance.now();
  const restored = await loadPyodide({ _loadSnapshot: bare });
  const restoreMs = ms(start);
  await packagesAndPydiggs(restored);
  rows.push(["C2. restored, then lxml + pydiggs + warm-up", `${ms(start)} ms (restore ${restoreMs} ms)`, equalsCPython(restored) ? "yes" : "NO"]);
} catch (error) {
  rows.push(["C. bare-runtime snapshot", `failed: ${String(error?.message ?? error).split("\n")[0]}`, ""]);
}

console.log(`### Pyodide ${cold.version} memory snapshots\n`);
console.log("| step | time | equals CPython |\n|---|---|---|");
for (const row of rows) console.log(`| ${row.join(" | ")} |`);
