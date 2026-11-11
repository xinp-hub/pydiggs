// Pyodide smoke for the npm package's worker (js/src/worker-core.ts): drive it with the
// messages a page sends, against the pinned Pyodide and the locally built wheel, and
// check that
//   - "init" with a wheel URL installs that wheel and reports the running versions;
//   - every `validate` and `read` answer equals CPython's document (goldens from
//     cases.py, same checkout), so the worker's Python glue adds and loses nothing;
//   - `read` with results: false keeps the tables and drops their values;
//   - HTML in an element value comes back verbatim, as data;
//   - a malformed document is findings for `validate` and an error for `read`;
//   - a document over the limit is refused without reaching Python;
//   - replies arrive in request order, one per request.
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

import { loadPyodide } from "pyodide";

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
let wake = () => {};
const handle = createWorkerCore({
  post: (message) => {
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
handle({ type: "init", maxBytes: MAX_BYTES, pydiggsWheelUrl: `emfs:/smoke/${wheelName}` });
const ready = await reply(0);
assert.equal(ready.type, "ready", `the worker did not load: ${ready.message}`);
assert.deepEqual(ready.versions, { pyodide: facts.pyodide, pydiggs: wheelMatch[1], lxml: facts.lxml });
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
  send({ type: "read", name, data: fixture(name), results: false });
}
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
  } else if (request.results === undefined) {
    assert.deepEqual(answer.document, JSON.parse(goldens[`read:${request.name}`]));
    withValues += tables(answer.document).flatMap((table) => table.columns).filter((c) => c.values.length > 0).length;
    console.log(`ok  read ${request.name}: equal to CPython's model`);
  } else {
    const full = JSON.parse(goldens[`read:${request.name}`]);
    const bare = tables(answer.document);
    assert.equal(bare.length, tables(full).length, "results: false must keep every table");
    for (const [i, table] of bare.entries()) {
      assert.equal(table.row_count, tables(full)[i].row_count);
      assert.ok(table.columns.every((column) => column.values.length === 0), "results: false must drop the values");
    }
    console.log(`ok  read ${request.name}, results: false: ${bare.length} table(s), no values`);
  }
}
if (withValues === 0) console.log("note: no fixture has result values, so results: false was compared on empty tables only");

// 4. Markup in an element value is data: it reaches a message unchanged.
const markupId = requests.find((r) => r.type === "validate" && r.name === "markup_in_value.xml").id;
const hits = answers.get(markupId).document.files[0].findings.filter((f) => f.message.includes(MARKUP));
assert.equal(hits.length, 1, "the markup must reach exactly one finding message verbatim");
console.log(`ok  markup round-trips verbatim in ${hits[0].rule_id}`);

// 5. A malformed document: findings from validate, an error from read.
const validated = answers.get(badValidate);
assert.equal(validated.type, "result", "validate must answer a malformed document with findings");
assert.notEqual(validated.document.files[0].verdict, "valid");
assert.equal(answers.get(badRead).type, "error", "read must refuse a malformed document");
console.log(`ok  malformed: validate -> ${validated.document.files[0].verdict}; read -> error`);

// 6. The size limit.
assert.deepEqual(answers.get(tooLarge), {
  id: tooLarge,
  type: "error",
  message: `the document is larger than ${MAX_BYTES} bytes`,
});
console.log("ok  a document over 25 MiB is refused");
console.log(`worker smoke passed (${elapsed()})`);
