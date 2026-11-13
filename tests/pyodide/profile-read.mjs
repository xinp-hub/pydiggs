// Experiment, not a test: where does reading spend its time in Pyodide? Reading (the
// diggs-model document) is about three times slower than validating at the same size
// (js/README.md, Sizing), and it is mostly Python, so a profile shows what to speed up
// in pydiggs itself. Runs cProfile around `pydiggs.read_bytes` inside the pinned
// Pyodide, on a synthetic document or a real one, and prints the top functions by
// their own time and by cumulative time, as Markdown (for the job summary).
//
//   cd tests/pyodide && npm ci --ignore-scripts
//   node tests/pyodide/profile-read.mjs --wheel dist/pydiggs-X.Y.Z-py3-none-any.whl [--mib 5] [--file PATH] [--top 25]

import { readFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { parseArgs } from "node:util";

import { loadPyodide } from "pyodide";

const { values: args } = parseArgs({
  options: {
    wheel: { type: "string" },
    mib: { type: "string", default: "5" },
    file: { type: "string" },
    top: { type: "string", default: "25" },
  },
});
if (!args.wheel) throw new Error("usage: profile-read.mjs --wheel PATH [--mib N] [--file PATH] [--top N]");

// Boreholes with casings, and lab tests with result tables, so the profile covers the
// feature, measurement and table paths.
function synthetic(bytes) {
  const head =
    '<?xml version="1.0" encoding="UTF-8"?>\n<Diggs xmlns="http://diggsml.org/schemas/2.6" ' +
    'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:xlink="http://www.w3.org/1999/xlink">\n';
  const tail = "</Diggs>\n";
  const parts = [head];
  let size = head.length + tail.length;
  for (let i = 1; size < bytes; i++) {
    const values = Array.from({ length: 20 }, (_, k) => `${k * 0.5},${(i * k) % 97}`).join(" ");
    const part =
      `  <samplingFeature><Borehole gml:id="BH-${i}"><gml:name>BH-${i}</gml:name>` +
      `<totalMeasuredDepth uom="ft">${(i % 200) + 10}.5</totalMeasuredDepth></Borehole></samplingFeature>\n` +
      `  <measurement><Test gml:id="T-${i}"><gml:name>T-${i}</gml:name>` +
      `<samplingFeatureRef xlink:href="#BH-${i}"/><outcome><TestResult gml:id="R-${i}"><results>` +
      `<ResultSet><parameters><PropertyParameters gml:id="P-${i}"><properties>` +
      `<Property index="1"><propertyName>depth</propertyName><typeData>double</typeData><propertyClass>depth</propertyClass></Property>` +
      `<Property index="2"><propertyName>value</propertyName><typeData>double</typeData><propertyClass>other</propertyClass></Property>` +
      `</properties></PropertyParameters></parameters><dataValues cs="," ts=" ">${values}</dataValues></ResultSet>` +
      `</results></TestResult></outcome></Test></measurement>\n`;
    parts.push(part);
    size += part.length;
  }
  parts.push(tail);
  return Buffer.from(parts.join(""), "utf8");
}

const wheel = resolve(args.wheel);
const data = args.file ? readFileSync(args.file) : synthetic(Number(args.mib) * 1024 * 1024);
const label = args.file ? basename(args.file) : `synthetic ${args.mib} MiB`;

const pyodide = await loadPyodide();
await pyodide.loadPackage(["lxml", "micropip"], { messageCallback: () => {} });
pyodide.FS.mkdirTree("/probe");
pyodide.FS.writeFile(`/probe/${basename(wheel)}`, readFileSync(wheel));
await pyodide.runPythonAsync(`import micropip\nawait micropip.install("emfs:/probe/${basename(wheel)}", deps=False)`);
pyodide.runPython("import pydiggs; pydiggs.warm_up()");
pyodide.FS.writeFile("/probe/doc.xml", data);

const report = pyodide.runPython(`
import cProfile, io, pstats, time
import pydiggs

data = open("/probe/doc.xml", "rb").read()
profile = cProfile.Profile()
started = time.perf_counter()
profile.enable()
model = pydiggs.read_bytes(data, name="doc.xml", max_bytes=len(data) + 1)
text = model.to_json()
profile.disable()
seconds = time.perf_counter() - started

def table(sort):
    stats = pstats.Stats(profile)
    stats.sort_stats(sort)
    rows = []
    for func in stats.fcn_list[:${Number(args.top)}]:
        cc, nc, tt, ct, _ = stats.stats[func]
        where = f"{func[0].split('site-packages/')[-1]}:{func[1]} {func[2]}"
        rows.append(f"| {where.replace('|', '/')} | {nc} | {tt:.2f} | {ct:.2f} |")
    return "\\n".join(["| function | calls | own s | cumulative s |", "|---|---|---|---|", *rows])

f"read + to_json: {seconds:.1f} s for {len(data) / 1048576:.1f} MiB, model {len(text) / 1048576:.1f} MiB\\n\\n" \\
    + "#### By own time\\n\\n" + table("tottime") + "\\n\\n#### By cumulative time\\n\\n" + table("cumulative")
`);
console.log(`### Read profile, Pyodide ${pyodide.version}, ${label}\n`);
console.log(report);
