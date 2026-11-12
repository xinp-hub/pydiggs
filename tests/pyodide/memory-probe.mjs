// Measures how much WebAssembly memory pydiggs needs to validate and read a document of
// a given size in Pyodide, to choose the size limits of @pydiggs/client: the browser
// worker's (25 MiB today) and a Node server's.
//
//   cd tests/pyodide && npm ci --ignore-scripts
//   uv build --wheel
//   node tests/pyodide/memory-probe.mjs --wheel dist/pydiggs-X.Y.Z-py3-none-any.whl \
//     [--sizes 5,10,25,50,100] [--file .cache/conformance/inspector/InspectorTestFile.xml]
//
// Each size gets a fresh Pyodide, because WebAssembly memory grows and never shrinks:
// its size after a run is that run's peak. The synthetic documents repeat a borehole
// with a casing until they reach the size; real documents (--file) are measured as they
// are. Browsers run the same WebAssembly, so the numbers carry over, except that a tab
// may be given less than the 4 GiB a 32-bit module can address (often 2 GiB on 64-bit
// desktops, much less on phones).

import { readFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { parseArgs } from "node:util";

import { loadPyodide } from "pyodide";

const { values: args } = parseArgs({
  options: {
    wheel: { type: "string" },
    sizes: { type: "string", default: "5,10,25,50,100" },
    file: { type: "string", multiple: true, default: [] },
  },
});
if (!args.wheel) throw new Error("usage: memory-probe.mjs --wheel PATH [--sizes MiB,...] [--file PATH ...]");

const MiB = 1024 * 1024;
const wheel = resolve(args.wheel);

function synthetic(bytes) {
  const head = '<?xml version="1.0" encoding="UTF-8"?>\n<Diggs xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2">\n';
  const tail = "</Diggs>\n";
  const parts = [head];
  let size = head.length + tail.length;
  for (let i = 1; size < bytes; i++) {
    const part =
      `  <samplingFeature><Borehole gml:id="BH-${i}"><gml:name>BH-${i}</gml:name>` +
      `<totalMeasuredDepth uom="ft">${(i % 200) + 10}.5</totalMeasuredDepth></Borehole></samplingFeature>\n` +
      `  <samplingFeature><Casing gml:id="C-${i}"><casingOutsideDiameter uom="mm">50</casingOutsideDiameter>` +
      `<casingInsideDiameter uom="in">3</casingInsideDiameter></Casing></samplingFeature>\n`;
    parts.push(part);
    size += part.length;
  }
  parts.push(tail);
  return Buffer.from(parts.join(""), "utf8");
}

async function measure(label, data) {
  const pyodide = await loadPyodide();
  await pyodide.loadPackage(["lxml", "micropip"], { messageCallback: () => {} });
  pyodide.FS.mkdirTree("/probe");
  pyodide.FS.writeFile(`/probe/${basename(wheel)}`, readFileSync(wheel));
  await pyodide.pyimport("micropip").install.callKwargs(`emfs:/probe/${basename(wheel)}`, { deps: false });
  pyodide.runPython("import pydiggs; pydiggs.warm_up()");
  const heap = () => pyodide._module.HEAPU8.byteLength;
  const before = heap();
  pyodide.FS.writeFile("/probe/doc.xml", data);
  data = null;
  const limit = pyodide.FS.stat("/probe/doc.xml").size + MiB;
  const run = (code) => {
    const t = performance.now();
    const out = pyodide.runPython(code);
    return [out, (performance.now() - t) / 1000];
  };
  const [verdict, validateSeconds] = run(`
import pathlib
_data = pathlib.Path("/probe/doc.xml").read_bytes()
pathlib.Path("/probe/doc.xml").unlink()
_report = pydiggs.validate_bytes(_data, name="doc.xml", max_bytes=${limit})
str(_report.verdict)`);
  const afterValidate = heap();
  const [, readSeconds] = run(`
del _report
_model = pydiggs.read_bytes(_data, name="doc.xml", results=False, max_bytes=${limit})
del _model`);
  const afterRead = heap();
  const size = limit - MiB;
  return {
    document: label,
    "size MiB": (size / MiB).toFixed(1),
    "heap after load MiB": (before / MiB).toFixed(0),
    "peak validate MiB": (afterValidate / MiB).toFixed(0),
    "peak read MiB": (afterRead / MiB).toFixed(0),
    "peak / size": (afterRead / size).toFixed(1),
    "validate s": validateSeconds.toFixed(1),
    "read s": readSeconds.toFixed(1),
    verdict,
  };
}

const rows = [];
for (const mib of args.sizes.split(",").map(Number).filter((n) => n > 0)) {
  try {
    rows.push(await measure(`synthetic ${mib} MiB`, synthetic(mib * MiB)));
  } catch (error) {
    rows.push({ document: `synthetic ${mib} MiB`, verdict: `FAILED: ${String(error?.message ?? error).split("\n").at(-2) ?? error}` });
  }
  console.error(`measured ${mib} MiB`);
}
for (const file of args.file) {
  rows.push(await measure(basename(file), readFileSync(file)));
  console.error(`measured ${file}`);
}

const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
console.log(`| ${columns.join(" | ")} |`);
console.log(`|${columns.map(() => "---").join("|")}|`);
for (const row of rows) console.log(`| ${columns.map((c) => row[c] ?? "").join(" | ")} |`);
