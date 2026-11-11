// The page side: hands a chosen file to the worker and renders the findings.
//
// Every string in a findings document is untrusted: messages quote element values and
// the path is the file's name. They are written with textContent / DOM nodes only,
// never innerHTML, so markup in a DIGGS file shows up as text.

const statusLine = document.getElementById("status");
const fileInput = document.getElementById("file");
const reportSection = document.getElementById("report");

// Pass ?wheel=... through to the worker (development only; the worker checks it).
const workerUrl = new URL("./worker.mjs", import.meta.url);
const wheel = new URL(location.href).searchParams.get("wheel");
if (wheel) workerUrl.searchParams.set("wheel", wheel);
const worker = new Worker(workerUrl, { type: "module" });

const pending = new Map();
let nextId = 1;
// The worker's limit too (it checks again): refused here before the file is read, so
// choosing a huge file cannot exhaust the tab's memory first.
const MAX_BYTES = 25 * 1024 * 1024;

// Ask the worker to "validate" (a diggs-findings document) or "read" (a diggs-model
// document without table values: an inventory of what is in the file).
function ask(type, file) {
  if (file.size > MAX_BYTES) {
    return Promise.reject(new Error(`the document is larger than ${MAX_BYTES} bytes`));
  }
  return file.arrayBuffer().then(
    (data) =>
      new Promise((resolve, reject) => {
        const id = nextId++;
        pending.set(id, { resolve, reject });
        worker.postMessage({ id, type, name: file.name, data }, [data]);
      }),
  );
}

// One line from the diggs-model document: what the file holds, by kind.
function contents(model) {
  const count = (items, noun) => `${items.length} ${noun}${items.length === 1 ? "" : "s"}`;
  const tables = model.measurements.reduce((sum, m) => sum + m.results.length, 0);
  return [
    `DIGGS ${model.source.diggs_version}`,
    count(model.features, "feature"),
    count(model.samples, "sample"),
    count(model.measurements, "measurement"),
    `${tables} result table${tables === 1 ? "" : "s"}`,
  ].join(", ");
}

worker.addEventListener("message", (event) => {
  const message = event.data;
  if (message.type === "ready") {
    const v = message.versions;
    statusLine.textContent = `Ready: pydiggs ${v.pydiggs}, Pyodide ${v.pyodide}, lxml ${v.lxml}.`;
    fileInput.disabled = false;
    return;
  }
  if (message.type === "failed") {
    statusLine.textContent = `Could not load pydiggs: ${message.message}`;
    statusLine.className = "error";
    return;
  }
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.type === "result") request.resolve(message.document);
  else request.reject(new Error(message.message));
});

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function render(doc) {
  reportSection.replaceChildren();
  for (const file of doc.files) {
    const c = file.counts;
    reportSection.append(
      el("h2", file.path),
      el("p", `${file.verdict} (DIGGS ${file.diggs_version ?? "?"}): ` +
        `${c.error} errors, ${c.warning} warnings, ${c.info} notes`, "verdict"),
    );
    if (file.findings.length === 0) continue;
    const table = el("table");
    const head = el("tr");
    for (const title of ["Severity", "Rule", "Line", "Message"]) head.append(el("th", title));
    table.append(head);
    for (const f of file.findings) {
      const row = el("tr");
      row.append(
        el("td", f.severity),
        el("td", f.rule_id),
        el("td", f.location?.line ?? ""),
        el("td", f.message, "message"),
      );
      table.append(row);
    }
    reportSection.append(table);
  }
}

fileInput.addEventListener("change", async () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  // One file at a time: a later choice could otherwise finish first and then be
  // overwritten by the earlier file's report.
  fileInput.disabled = true;
  statusLine.className = "";
  statusLine.textContent = "Validating…";
  try {
    const started = performance.now();
    render(await ask("validate", file));
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    statusLine.textContent = `Validated in ${seconds} s.`;
    try {
      // Reading is separate from validating: an invalid file usually still reads.
      const model = await ask("read", file);
      statusLine.textContent = `Validated in ${seconds} s. Contains: ${contents(model)}.`;
    } catch (error) {
      statusLine.textContent = `Validated in ${seconds} s. Not read: ${error.message}`;
    }
  } catch (error) {
    reportSection.replaceChildren();
    statusLine.className = "error";
    statusLine.textContent = `Not validated: ${error.message}`;
  } finally {
    fileInput.disabled = false;
  }
});
