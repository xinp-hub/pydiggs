// Pyodide smoke for the npm package's Node side (js/src/node.ts, node-worker.ts and
// handler.ts): the real pydiggs in a Node worker thread, from the npm `pyodide` package,
// and served over pyDIGGS's HTTP API by the Fetch handler. It checks that
//   - PydiggsNode loads the pinned Pyodide in a worker thread, installs the wheel from
//     a path on disk, keeps the downloaded packages in packageCacheDir, and reports its
//     load stages in order;
//   - every validate and read equals CPython's document (goldens from cases.py);
//   - PydiggsHttpClient, talking to createPydiggsHandler(PydiggsNode), gets the same
//     documents, so a JavaScript server can stand in for `pydiggs serve --http`;
//   - a limit above the browser's 25 MiB is honoured on a server;
//   - a document past timeoutMs fails with code "timeout", and the next document runs
//     on a fresh worker thread with the same answers.
//
//   cd tests/pyodide && npm ci --ignore-scripts
//   uv build --wheel && uv run --no-sync python tests/pyodide/cases.py goldens.json
//   node tests/pyodide/node-smoke.mjs --wheel dist/pydiggs-X.Y.Z-py3-none-any.whl --goldens goldens.json
//
// Node runs the TypeScript sources directly (type stripping, Node 24 or later), in the
// worker thread too. Pyodide fetches lxml and micropip from its CDN on first start.

import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { PydiggsHttpClient } from "../../js/src/http.ts";
import { createPydiggsHandler, PydiggsNode } from "../../js/src/node.ts";

const here = dirname(fileURLToPath(import.meta.url));
const { values: args } = parseArgs({
  options: { wheel: { type: "string" }, goldens: { type: "string" } },
});
assert.ok(args.wheel && args.goldens, "usage: node-smoke.mjs --wheel PATH --goldens PATH");

const facts = JSON.parse(readFileSync(join(here, "pyodide-facts.json"), "utf8"));
const goldens = JSON.parse(readFileSync(args.goldens, "utf8"));
const FIXTURES = ["casing_units.xml", "markup_in_value.xml"];
const MiB = 1024 * 1024;

const started = performance.now();
const elapsed = () => `${((performance.now() - started) / 1000).toFixed(1)} s`;

const cache = mkdtempSync(join(tmpdir(), "pyodide-packages-"));
const stages = [];
const node = new PydiggsNode({
  workerUrl: new URL("../../js/src/node-worker.ts", import.meta.url),
  pyodideModule: join(here, "node_modules", "pyodide", "pyodide.mjs"),
  pydiggsWheelPath: resolve(args.wheel),
  packageCacheDir: cache,
  maxBytes: 64 * MiB,
  onProgress: (stage) => stages.push(stage),
});

try {
  // 1. Loading.
  const versions = await node.ready;
  const wheelVersion = /pydiggs-([^-]+)-py3-none-any\.whl$/.exec(args.wheel)[1];
  assert.deepEqual(versions, { pyodide: facts.pyodide, pydiggs: wheelVersion, lxml: facts.lxml });
  assert.deepEqual(stages, ["runtime", "packages", "pydiggs", "warm-up"]);
  assert.ok(readdirSync(cache).length > 0, `packageCacheDir ${cache} stayed empty`);
  console.log(`ok  PydiggsNode ready in a worker thread: pyodide ${versions.pyodide}, pydiggs ${versions.pydiggs} (${elapsed()})`);
  console.log(`ok  packages cached in packageCacheDir: ${readdirSync(cache).join(", ")}`);

  // 2. The engine directly, against CPython.
  for (const name of FIXTURES) {
    const data = readFileSync(join(here, "fixtures", name));
    assert.deepEqual(await node.validate(data, { name }), JSON.parse(goldens[name]));
    assert.deepEqual(await node.read(data, { name }), JSON.parse(goldens[`read:${name}`]));
    console.log(`ok  PydiggsNode ${name}: validate and read equal CPython's`);
  }

  // 3. Over HTTP: the Fetch handler, called by the package's own HTTP client.
  const handle = createPydiggsHandler(node, { basePath: "/api/pydiggs", maxBodyBytes: 64 * MiB });
  const fetch = async (input, init) => handle(new Request(new URL(String(input), "http://app.test"), init));
  const client = new PydiggsHttpClient({ baseUrl: "/api/pydiggs", fetch });
  assert.equal(await client.ready(), true);
  for (const name of FIXTURES) {
    const data = readFileSync(join(here, "fixtures", name));
    assert.deepEqual(await client.validate(data, { name }), JSON.parse(goldens[name]));
    assert.deepEqual(await client.read(data, { name }), JSON.parse(goldens[`read:${name}`]));
    console.log(`ok  handler + PydiggsHttpClient ${name}: equal CPython's`);
  }

  // 4. A server may take more than the browser's 25 MiB.
  const big = Buffer.concat([
    Buffer.from('<?xml version="1.0"?>\n<Diggs xmlns="http://diggsml.org/schemas/2.6"><!--'),
    Buffer.alloc(30 * MiB, 0x20),
    Buffer.from("--></Diggs>"),
  ]);
  const findings = await node.validate(big, { name: "big.xml" });
  assert.equal(findings.files[0].path, "big.xml");
  console.log(`ok  a ${(big.byteLength / MiB).toFixed(0)} MiB document is validated on a server (${elapsed()})`);
} finally {
  node.terminate();
}

// 5. The timeout: a document with many features takes seconds, so a 1 s limit stops it.
const slowDoc = (() => {
  const parts = ['<?xml version="1.0"?>\n<Diggs xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2">\n'];
  for (let i = 1; i <= 40000; i++) {
    parts.push(`<samplingFeature><Borehole gml:id="BH-${i}"><totalMeasuredDepth uom="ft">-1</totalMeasuredDepth></Borehole></samplingFeature>\n`);
  }
  parts.push("</Diggs>\n");
  return Buffer.from(parts.join(""));
})();
const timed = new PydiggsNode({
  workerUrl: new URL("../../js/src/node-worker.ts", import.meta.url),
  pyodideModule: join(here, "node_modules", "pyodide", "pyodide.mjs"),
  pydiggsWheelPath: resolve(args.wheel),
  packageCacheDir: cache,
  timeoutMs: 1000,
});
try {
  await timed.ready;
  const slow = timed.validate(slowDoc, { name: "slow.xml" });
  const after = timed.validate(readFileSync(join(here, "fixtures", FIXTURES[0])), { name: FIXTURES[0] });
  await assert.rejects(slow, (error) => error.code === "timeout");
  console.log(`ok  a ${(slowDoc.byteLength / MiB).toFixed(1)} MiB document past timeoutMs fails with code "timeout" (${elapsed()})`);
  assert.deepEqual(await after, JSON.parse(goldens[FIXTURES[0]]));
  assert.equal(timed.status, "ready");
  console.log(`ok  the next document ran on a fresh worker thread and equals CPython's (${elapsed()})`);
} finally {
  timed.terminate();
}
console.log(`node smoke passed (${elapsed()})`);
