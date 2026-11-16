// Pyodide smoke for the npm package's worker (js/src/worker-core.ts): drive it with the
// messages a page sends, against the pinned Pyodide and the locally built wheel, and
// check that
//   - "init" with a wheel URL installs that wheel, reports each load stage in order, and
//     then the running versions;
//   - every `validate` and `read` answer equals CPython's document (goldens from
//     cases.py, same checkout), so the worker's Python glue adds and loses nothing;
//   - `read` with select "none" keeps the tables and drops their values;
//   - the service-layer requests equal CPython's service layer: SARIF, a subset of
//     checks, one node's tables, versions, the rule list and one rule;
//   - HTML in an element value comes back verbatim, as data;
//   - a malformed document is findings for `validate` and an invalid-document problem
//     for `read`; a bad parameter is pydiggs's invalid-parameter problem;
//   - a document over the limit is refused without reaching Python;
//   - replies arrive in request order, one per request;
//   - AGS4: convert, back to AGS4 in 4.2 and 4.1.1, and a check with and without a data
//     dictionary (also as SARIF), equal CPython's service layer; each output and the
//     residual decode to the converter's bytes; capabilities lists the same core operations;
//   - writing SIGINT to the interrupt buffer stops a long validation with "cancelled",
//     and the same worker answers the next request.
// Only the Web Worker shell (js/src/browser-worker.ts: the integrity-checked fetch of
// pyodide.mjs and the message listener) is not run here; it needs a browser.
//
//   cd tests/pyodide && npm ci --ignore-scripts
//   uv build --wheel && uv run --no-sync python tests/pyodide/cases.py goldens.json
//   node tests/pyodide/worker-smoke.mjs --wheel dist/pydiggs-X.Y.Z-py3-none-any.whl --goldens goldens.json
//
// Node runs the TypeScript source directly (type stripping, Node 24 or later). Pyodide
// fetches lxml and micropip from its CDN, so this needs network access.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { Worker } from "node:worker_threads";

import { loadPyodide } from "pyodide";

import { conversionOutput, conversionResidual } from "../../js/src/convert.ts";
import { createWorkerCore } from "../../js/src/worker-core.ts";

const here = dirname(fileURLToPath(import.meta.url));
const { values: args } = parseArgs({
  options: { wheel: { type: "string" }, goldens: { type: "string" } },
});
assert.ok(args.wheel && args.goldens, "usage: worker-smoke.mjs --wheel PATH --goldens PATH");

const facts = JSON.parse(readFileSync(join(here, "pyodide-facts.json"), "utf8"));
const goldens = JSON.parse(readFileSync(args.goldens, "utf8"));
const wheelName = basename(args.wheel);
const wheelMatch = /^pydiggs-([^-]+)-py3-none-any\.whl$/.exec(wheelName);
assert.ok(wheelMatch, `not a pure pydiggs wheel: ${wheelName}`);
const FIXTURES = ["casing_units.xml", "markup_in_value.xml"];
const MARKUP = '<img src=x onerror="alert(document.domain)">';
const MAX_BYTES = 25 * 1024 * 1024;

const started = performance.now();
const elapsed = () => `${((performance.now() - started) / 1000).toFixed(1)} s`;

// The page's side: every reply in arrival order, and a promise for the next one.
const replies = [];
const stages = [];
let wake = () => {};
const handle = createWorkerCore({
  post: (message) => {
    if (message.type === "progress") {
      stages.push(message.stage);
      return;
    }
    replies.push(message);
    wake();
  },
  // In a browser this is the integrity-checked loader; here it is the same Pyodide
  // release from npm, with the wheel put on its filesystem for micropip to install.
  loadPyodide: async () => {
    const pyodide = await loadPyodide();
    pyodide.FS.mkdirTree("/smoke");
    pyodide.FS.writeFile(`/smoke/${wheelName}`, readFileSync(args.wheel));
    return pyodide;
  },
});
async function reply(index) {
  while (replies.length <= index) await new Promise((resolve) => (wake = resolve));
  return replies[index];
}

// A fresh ArrayBuffer, as a page transfers one (a Buffer may share a pooled one).
const arrayBuffer = (buffer) => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
const fixture = (name) => arrayBuffer(readFileSync(join(here, "fixtures", name)));

// 1. Configure, load, report.
const interrupt = new Uint8Array(new SharedArrayBuffer(1));
handle({ type: "init", maxBytes: MAX_BYTES, pydiggsWheelUrl: `emfs:/smoke/${wheelName}`, interruptBuffer: interrupt.buffer });
const ready = await reply(0);
assert.equal(ready.type, "ready", `the worker did not load: ${ready.message}`);
assert.deepEqual(ready.versions, { pyodide: facts.pyodide, pydiggs: wheelMatch[1], lxml: facts.lxml });
assert.deepEqual(stages, ["runtime", "packages", "pydiggs", "warm-up"], "load stages out of order");
console.log(`ok  ready: pyodide ${facts.pyodide}, pydiggs ${wheelMatch[1]}, lxml ${facts.lxml} (${elapsed()})`);

// 2. Send everything at once, as a page may; the worker queues.
const requests = [];
const send = (message) => {
  const id = requests.length + 1;
  requests.push({ id, ...message });
  handle({ id, ...message });
  return id;
};
for (const name of FIXTURES) {
  send({ type: "validate", name, data: fixture(name) });
  send({ type: "read", name, data: fixture(name) });
  send({ type: "read", name, data: fixture(name), parameters: { select: { results: "none" } } });
}
const service = [];
for (const name of FIXTURES) {
  service.push([`sarif:${name}`, send({ type: "validate", name, data: fixture(name), parameters: { format: "sarif" } })]);
  service.push([`checks:${name}`, send({ type: "validate", name, data: fixture(name), parameters: { checks: ["structure", "schema"] } })]);
  service.push([`select:${name}`, send({ type: "read", name, data: fixture(name), parameters: { select: { results: ["ATTERBERG_B-01_4.0"] } } })]);
}
service.push(["versions", send({ type: "versions" })]);
service.push(["rules:schema", send({ type: "rules", parameters: { step: "schema" } })]);
const explainId = JSON.parse(goldens["rules:schema"])[0].id;
service.push(["explain", send({ type: "explain", parameters: { rule_id: explainId } })]);
const AGS4 = "site_tier_a.ags";
const [CHECKED, DICTIONARY] = ["c2_user_group.ags", "c2_dictionary.ags"];
service.push([`service-convert:${AGS4}`, send({ type: "convert", name: AGS4, data: fixture(AGS4), parameters: { to: "diggs" } })]);
service.push([`service-ags4-check:${AGS4}`, send({ type: "ags4-check", name: AGS4, data: fixture(AGS4) })]);
// pyDIGGS 1.11: that DIGGS back to AGS4, in each AGS4 version it writes.
const AGS4_XML = AGS4.replace(/\.ags$/, ".xml");
const AGS_VERSIONS = ["4.2", "4.1.1"];
for (const version of AGS_VERSIONS) {
  const data = arrayBuffer(Buffer.from(goldens[`convert:${AGS4}`], "utf8"));
  service.push([
    `service-to-ags4:${version}:${AGS4}`,
    send({ type: "convert", name: AGS4_XML, data, parameters: { to: "ags4", ags_version: version } }),
  ]);
}
service.push([`ags4:${CHECKED}`, send({ type: "ags4-check", name: CHECKED, data: fixture(CHECKED), dictionary: fixture(DICTIONARY) })]);
service.push([`ags4-no-dictionary:${CHECKED}`, send({ type: "ags4-check", name: CHECKED, data: fixture(CHECKED) })]);
service.push([
  `ags4-sarif:${CHECKED}`,
  send({ type: "ags4-check", name: CHECKED, data: fixture(CHECKED), dictionary: fixture(DICTIONARY), parameters: { format: "sarif" } }),
]);
const capabilitiesId = send({ type: "capabilities" });
const notAgs4 = send({ type: "convert", name: "x.ags", data: fixture(FIXTURES[0]), parameters: { to: "diggs" } });
const badChecks = send({ type: "validate", name: "a.xml", data: fixture(FIXTURES[0]), parameters: { checks: ["nope"] } });
const malformed = arrayBuffer(Buffer.from("<Diggs><unclosed></Diggs>"));
const badValidate = send({ type: "validate", name: "bad.xml", data: malformed.slice(0) });
const badRead = send({ type: "read", name: "bad.xml", data: malformed.slice(0) });
const tooLarge = send({ type: "validate", name: "big.xml", data: new ArrayBuffer(MAX_BYTES + 1) });

const answers = new Map();
for (let i = 0; i < requests.length; i++) {
  const answer = await reply(i + 1);
  assert.equal(answer.id, requests[i].id, "replies must come in request order");
  answers.set(answer.id, answer);
}
await new Promise((resolve) => setTimeout(resolve, 50));
assert.equal(replies.length, requests.length + 1, "exactly one reply per request");

// 3. Equality with CPython, per document.
const tables = (model) => [...model.measurements, ...model.others].flatMap((node) => node.results ?? []);
let withValues = 0;
for (const request of requests.slice(0, FIXTURES.length * 3)) {
  const answer = answers.get(request.id);
  assert.equal(answer.type, "result", `${request.type} ${request.name}: ${answer.message}`);
  if (request.type === "validate") {
    assert.deepEqual(answer.document, JSON.parse(goldens[request.name]));
    console.log(`ok  validate ${request.name}: equal to CPython's report`);
  } else if (request.parameters === undefined) {
    assert.deepEqual(answer.document, JSON.parse(goldens[`read:${request.name}`]));
    withValues += tables(answer.document).flatMap((table) => table.columns).filter((c) => c.values.length > 0).length;
    console.log(`ok  read ${request.name}: equal to CPython's model`);
  } else {
    const full = JSON.parse(goldens[`read:${request.name}`]);
    const bare = tables(answer.document);
    assert.equal(bare.length, tables(full).length, "select none must keep every table");
    for (const [i, table] of bare.entries()) {
      assert.equal(table.row_count, tables(full)[i].row_count);
      assert.ok(table.columns.every((column) => column.values.length === 0), "select none must drop the values");
    }
    console.log(`ok  read ${request.name}, select none: ${bare.length} table(s), no values`);
  }
}
if (withValues === 0) console.log("note: no fixture has result values, so select none was compared on empty tables only");

// 3b. The service layer's other answers, against CPython's service layer.
for (const [golden, id] of service) {
  const answer = answers.get(id);
  assert.equal(answer.type, "result", `${golden}: ${answer.message}`);
  assert.deepEqual(answer.document, JSON.parse(goldens[golden]), `${golden}: differs from CPython's`);
  console.log(`ok  ${golden}: equal to CPython's service layer`);
}
// The converted DIGGS, decoded and checked against its sha256, is the converter's XML.
const conversion = answers.get(service.find(([golden]) => golden.startsWith("service-convert:"))[1]).document;
const xml = await conversionOutput(conversion);
assert.equal(new TextDecoder().decode(xml), goldens[`convert:${AGS4}`]);
console.log(`ok  convert ${AGS4}: ${conversion.output_name}, ${xml.byteLength} bytes, identical to the converter's XML`);
const residual = conversionResidual(conversion);
if (residual === null) {
  console.log(`note: ${AGS4} converted with no residual`);
} else {
  assert.equal(new TextDecoder().decode(residual), goldens[`residual:${AGS4}`]);
  console.log(`ok  residual of ${AGS4}: ${residual.byteLength} bytes, identical to the converter's`);
}
for (const version of AGS_VERSIONS) {
  const golden = `service-to-ags4:${version}:${AGS4}`;
  const back = answers.get(service.find(([name]) => name === golden)[1]).document;
  const text = new TextDecoder().decode(await conversionOutput(back));
  assert.equal(text, goldens[`to-ags4:${version}:${AGS4}`]);
  console.log(`ok  ${AGS4_XML} to AGS4 ${version}: ${back.output_name}, identical to the converter's AGS4 file`);
}
// Capabilities: the core operations (extras differ between CPython and Pyodide).
const capabilities = answers.get(capabilitiesId);
assert.equal(capabilities.type, "result", capabilities.message);
const core = { ...capabilities.document, operations: capabilities.document.operations.filter((op) => op.needs.length === 0) };
const sortKeys = (value) =>
  Array.isArray(value)
    ? value.map(sortKeys)
    : value && typeof value === "object"
      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortKeys(value[key])]))
      : value;
assert.deepEqual(sortKeys(core), sortKeys(JSON.parse(goldens.capabilities)));
console.log(`ok  capabilities: ${core.operations.length} core operations, as CPython lists them`);
const notAgs4Answer = answers.get(notAgs4);
assert.deepEqual([notAgs4Answer.type, notAgs4Answer.problem?.code], ["error", "invalid-document"]);
console.log(`ok  DIGGS XML sent to convert is invalid-document (${notAgs4Answer.problem.kind})`);
const checksProblem = answers.get(badChecks);
assert.equal(checksProblem.type, "error");
assert.equal(checksProblem.problem.code, "invalid-parameter");
assert.equal(checksProblem.problem.errors[0].parameter, "checks");
console.log("ok  an unknown check is pydiggs's invalid-parameter problem");

// 4. Markup in an element value is data: it reaches a message unchanged.
const markupId = requests.find((r) => r.type === "validate" && r.name === "markup_in_value.xml").id;
const hits = answers.get(markupId).document.files[0].findings.filter((f) => f.message.includes(MARKUP));
assert.equal(hits.length, 1, "the markup must reach exactly one finding message verbatim");
console.log(`ok  markup round-trips verbatim in ${hits[0].rule_id}`);

// 5. A malformed document: findings from validate, an error from read.
const validated = answers.get(badValidate);
assert.equal(validated.type, "result", "validate must answer a malformed document with findings");
assert.notEqual(validated.document.files[0].verdict, "valid");
const refusedRead = answers.get(badRead);
assert.equal(refusedRead.type, "error", "read must refuse a malformed document");
assert.deepEqual([refusedRead.problem.code, refusedRead.problem.kind], ["invalid-document", "malformed"]);
console.log(`ok  malformed: validate -> ${validated.document.files[0].verdict}; read -> invalid-document (malformed)`);

// 6. The size limit.
assert.deepEqual(answers.get(tooLarge), {
  id: tooLarge,
  type: "error",
  message: `the document is larger than ${MAX_BYTES} bytes`,
  problem: { code: "input-too-large", detail: `larger than ${MAX_BYTES} bytes` },
});
console.log("ok  a document over 25 MiB is refused");

// 7. Interrupting: SIGINT in the buffer stops Python with KeyboardInterrupt, the glue
// answers "cancelled", and the same worker takes the next request. Python checks the
// buffer between bytecodes, not inside lxml's C code: reading (mostly Python) stops at
// once, validation finishes the step it is in (schematron, lxml's XSLT, is the long one).
const slowDoc = (() => {
  const parts = ['<?xml version="1.0"?>\n<Diggs xmlns="http://diggsml.org/schemas/2.6" xmlns:gml="http://www.opengis.net/gml/3.2">\n'];
  for (let i = 1; i <= 40000; i++) {
    parts.push(`<samplingFeature><Borehole gml:id="BH-${i}"><totalMeasuredDepth uom="ft">-1</totalMeasuredDepth></Borehole></samplingFeature>\n`);
  }
  parts.push("</Diggs>\n");
  return Buffer.from(parts.join(""));
})();
// Python runs on this thread here, so a timer could not fire until it finished; a page
// writes from its own thread, and so does this helper.
const stopAfter = (ms) =>
  new Worker(
    "const { workerData } = require('node:worker_threads');" +
      `Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ${ms});` +
      "Atomics.store(new Uint8Array(workerData), 0, 2);",
    { eval: true, workerData: interrupt.buffer },
  );
for (const [type, limit] of [["read", 5000], ["validate", 60000]]) {
  const before = replies.length;
  const began = performance.now();
  const id = send({ type, name: "slow.xml", data: arrayBuffer(slowDoc) });
  const stopper = stopAfter(1000);
  const stopped = await reply(before);
  const took = performance.now() - began;
  await stopper.terminate();
  assert.equal(stopped.id, id);
  assert.equal(stopped.type, "error", `the interrupted ${type} must not produce a document`);
  assert.equal(stopped.problem.code, "cancelled");
  assert.ok(took < limit, `${type} took ${took} ms to stop`);
  console.log(`ok  SIGINT at 1 s stopped ${type} of a ${(slowDoc.byteLength / 1024 / 1024).toFixed(1)} MiB document at ${(took / 1000).toFixed(1)} s with "cancelled"`);
  const nextId = send({ type: "validate", name: FIXTURES[0], data: fixture(FIXTURES[0]) });
  const next = await reply(before + 1);
  assert.equal(next.id, nextId);
  assert.deepEqual(next.document, JSON.parse(goldens[FIXTURES[0]]));
  console.log("ok  the same worker answers the next request, equal to CPython's");
}
console.log(`worker smoke passed (${elapsed()})`);
