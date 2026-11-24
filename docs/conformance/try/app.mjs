// The conformance page: tabs, forms and results. pyDIGGS runs in worker.mjs.
//
// Every string from a file or from the evidence is written with textContent or as a
// DOM text node, never as HTML. The page makes no network request of its own (its
// Content-Security-Policy says connect-src 'none'); files go to the worker by
// postMessage and results come back the same way.

const TIMEOUT_MS = 300_000; // a request that runs longer stops the worker
const STAGES = { runtime: "Loading Python (Pyodide)…", packages: "Loading lxml…", pydiggs: "Installing pyDIGGS…" };
const SHOWN_ITEMS = 50; // per stage; report.html and evidence.json have them all

const $ = (selector, root = document) => root.querySelector(selector);
const field = (root, name) => $(`[data-field="${name}"]`, root);

// --- The worker -------------------------------------------------------------------

let worker;
let ready;
let nextId = 1;
const pending = new Map();

function say(message, state) {
  const engine = $("#engine");
  engine.textContent = message;
  if (state) engine.dataset.state = state;
}

function problem(message) {
  const box = $("#problem");
  box.textContent = message;
  box.hidden = !message;
}

function startWorker() {
  worker = new Worker(new URL("./worker.mjs", import.meta.url), { type: "module" });
  ready = new Promise((resolve, reject) => {
    worker.addEventListener("message", (event) => {
      const message = event.data ?? {};
      if (message.type === "progress") {
        say(STAGES[message.stage] ?? "Loading…", "loading");
      } else if (message.type === "ready") {
        const v = message.versions;
        $("#versions").textContent = `Running in this tab: pyDIGGS ${v.pydiggs}, Pyodide ${v.pyodide}, lxml ${v.lxml}.`;
        resolve(v);
      } else if (message.type === "failed") {
        reject(new Error(message.message));
      } else if (typeof message.id === "number" && pending.has(message.id)) {
        const { resolve: done, fail, timer } = pending.get(message.id);
        pending.delete(message.id);
        clearTimeout(timer);
        if (message.type === "result") done(message.body);
        else fail(new Error(message.message));
      }
    });
    worker.addEventListener("error", (event) => reject(new Error(event.message || "the worker could not start")));
  });
}

function restartWorker(reason) {
  worker.terminate();
  for (const { fail, timer } of pending.values()) {
    clearTimeout(timer);
    fail(new Error(reason));
  }
  pending.clear();
  startWorker();
  ready.then(
    () => say("pyDIGGS was restarted and is ready.", "ready"),
    (error) => say(`pyDIGGS could not be loaded: ${error.message}`, "failed"),
  );
}

async function call(type, fields = {}, transfer = []) {
  await ready;
  const id = nextId++;
  return new Promise((resolve, fail) => {
    const timer = setTimeout(
      () => restartWorker(`pyDIGGS took longer than ${TIMEOUT_MS / 1000} s and was stopped`),
      TIMEOUT_MS,
    );
    pending.set(id, { resolve, fail, timer });
    worker.postMessage({ id, type, ...fields }, transfer);
  });
}

// --- Files in and out -------------------------------------------------------------

async function chosen(input) {
  const file = input.files?.[0];
  return file ? { name: file.name, data: await file.arrayBuffer() } : null;
}

const copy = (bytes) => bytes.slice().buffer; // a fresh ArrayBuffer to transfer

function fromBase64(text) {
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

function save(name, data, type) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// --- Tabs (WAI-ARIA tabs, automatic activation) -----------------------------------

function setUpTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab, focus) => {
    for (const other of tabs) {
      const on = other === tab;
      other.setAttribute("aria-selected", String(on));
      other.tabIndex = on ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
  };
  for (const tab of tabs) {
    tab.addEventListener("click", () => select(tab, false));
    tab.addEventListener("keydown", (event) => {
      const at = tabs.indexOf(tab);
      const to = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabs.length - 1 }[event.key];
      if (to === undefined) return;
      event.preventDefault();
      select(tabs[(to + tabs.length) % tabs.length], true);
    });
  }
}

// --- Busy state -------------------------------------------------------------------

let busy = false;
function setBusy(on) {
  busy = on;
  for (const button of document.querySelectorAll("form button[type=submit]")) button.disabled = on;
  document.body.setAttribute("aria-busy", String(on));
}

async function run(working, task) {
  if (busy) return;
  problem("");
  setBusy(true);
  say(working, "working");
  try {
    say(await task(), "ready");
  } catch (error) {
    say("pyDIGGS is ready.", "ready");
    problem(error.message);
  } finally {
    setBusy(false);
  }
}

function reply(body) {
  if (!body.ok) throw new Error(body.error);
  return body;
}

// --- 1. Make ----------------------------------------------------------------------

let made = null; // the challenge made on this page: { manifest, zip, folder, ... }

async function onMake(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const product = form.product.value.trim();
  const version = form.version.value.trim();
  const seed = form.seed.value.trim();
  if (!product || !version) {
    problem("Name the product and its exact version.");
    (product ? form.version : form.product).focus();
    return;
  }
  if (seed && !/^[A-Za-z0-9._-]{1,64}$/.test(seed)) {
    problem("A seed is 1 to 64 letters, digits, '.', '_' or '-'.");
    form.seed.focus();
    return;
  }
  await run("Making the challenge…", async () => {
    const body = reply(await call("make", { classId: form.class.value, product, version, seed }));
    made = {
      classId: body.class_id,
      direction: body.direction,
      seed: body.seed,
      folder: body.folder,
      manifest: new TextEncoder().encode(body.manifest),
      zip: fromBase64(body.zip),
    };
    const result = $("#make-result");
    field(result, "class").textContent = `${body.class_id}: ${body.title}`;
    field(result, "seed").textContent = body.seed;
    field(result, "token").textContent = body.token;
    field(result, "folder").textContent = `${body.folder}/ (${body.files.length} files)`;
    field(result, "next").textContent =
      body.direction === "export"
        ? "Unzip it, load the tables into your software, export one DIGGS 3.0.0 file, then grade it in step 2."
        : "Unzip it, open challenge.xml in your software, fill the answer column of answers.csv, then grade it in step 2.";
    $("#make-readme").textContent = body.readme;
    $("#make-reference").hidden = body.direction !== "export";
    for (const hint of document.querySelectorAll("[data-made-hint]")) {
      hint.textContent = `Leave empty to use the challenge made on this page (${body.class_id}, seed ${body.seed}).`;
    }
    result.hidden = false;
    $("#make-result-title").focus();
    return `Challenge ${body.class_id} made, seed ${body.seed}. Download it as a ZIP.`;
  });
}

async function onReference() {
  if (!made) return;
  await run("Writing pyDIGGS's own export…", async () => {
    const body = reply(await call("reference", { challenge: copy(made.manifest) }));
    save(body.name, fromBase64(body.data), "application/xml");
    return `Downloaded ${body.name}.`;
  });
}

// --- 2. Grade ---------------------------------------------------------------------

async function challengeFrom(input) {
  const file = await chosen(input);
  if (file) return file.data;
  if (made) return copy(made.manifest);
  throw new Error("Choose the challenge: its challenge.json or the challenge ZIP.");
}

let graded = null;

function showItems(container, stages) {
  container.replaceChildren();
  for (const stage of stages) {
    if (!stage.items.length) continue;
    const heading = document.createElement("h3");
    heading.textContent = `${stage.title}: ${stage.failed} problem(s)${stage.required ? "" : " (advisory)"}`;
    const list = document.createElement("ul");
    for (const item of stage.items.slice(0, SHOWN_ITEMS)) {
      const entry = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = item.check;
      entry.append(code, ` ${item.id}: ${item.message}`);
      if (item.expected !== undefined) entry.append(`; expected ${item.expected}`);
      if (item.got !== undefined) entry.append(`, got ${item.got}`);
      list.append(entry);
    }
    container.append(heading, list);
    const more = stage.failed - Math.min(stage.items.length, SHOWN_ITEMS);
    if (more > 0) {
      const note = document.createElement("p");
      note.textContent = `And ${more} more: see report.html or evidence.json.`;
      container.append(note);
    }
  }
}

const STATUS = { pass: "Passed", fail: "Failed", skipped: "Not run" };

async function onGrade(event) {
  event.preventDefault();
  const form = event.currentTarget;
  await run("Grading…", async () => {
    const submission = await chosen(form.submission);
    if (!submission) throw new Error("Choose your DIGGS export, or the filled answers.csv.");
    const challenge = await challengeFrom(form.challenge);
    const body = reply(
      await call("grade", { challenge, submission: submission.data, name: submission.name }, [
        challenge,
        submission.data,
      ]),
    );
    const evidence = body.evidence;
    graded = { evidence: body.evidence_json, report: body.report_html, classId: evidence.class.id };
    const result = $("#grade-result");
    const verdict = evidence.verdict === "pass" ? "PASS" : "FAIL";
    $("#grade-result-title").textContent = `${evidence.class.id}: ${verdict}`;
    result.dataset.verdict = evidence.verdict;
    field(result, "product").textContent = `${evidence.product.name} ${evidence.product.version} (as declared)`;
    field(result, "suite").textContent = `${evidence.suite_version}, DIGGS ${evidence.class.diggs_version}, ${evidence.tier}`;
    field(result, "challenge").textContent = `seed ${evidence.challenge.seed}, token ${evidence.challenge.token}`;
    field(result, "submission").textContent = `${evidence.submission.name}, sha256 ${evidence.submission.sha256}`;
    field(result, "statement").textContent = evidence.statement;
    const rows = $("tbody", result);
    rows.replaceChildren();
    for (const stage of evidence.stages) {
      const row = document.createElement("tr");
      const status = STATUS[stage.status] ?? stage.status;
      for (const text of [stage.title, stage.required ? status : `${status} (advisory)`, stage.total ? `${stage.passed} of ${stage.total}` : ""]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      row.dataset.status = stage.status;
      rows.append(row);
    }
    showItems($("#grade-items"), evidence.stages);
    result.hidden = false;
    $("#grade-result-title").focus();
    return `Graded: ${evidence.class.id} ${verdict}.`;
  });
}

// --- 3. Verify --------------------------------------------------------------------

async function onVerify(event) {
  event.preventDefault();
  const form = event.currentTarget;
  await run("Grading again…", async () => {
    const evidence = await chosen(form.evidence);
    const submission = await chosen(form.submission);
    if (!evidence) throw new Error("Choose the evidence.json to check.");
    if (!submission) throw new Error("Choose the graded file.");
    const challenge = await challengeFrom(form.challenge);
    const body = reply(
      await call(
        "verify",
        { evidence: evidence.data, challenge, submission: submission.data, name: submission.name },
        [evidence.data, challenge, submission.data],
      ),
    );
    const result = $("#verify-result");
    result.dataset.reproduced = String(body.reproduced);
    $("#verify-result-title").textContent = body.reproduced
      ? `Reproduced: ${body.class_id} ${body.verdict}`
      : "Not reproduced";
    field(result, "detail").textContent = body.reproduced
      ? "Grading the file again gives this evidence."
      : `Grading again gives ${body.verdict}, and these parts differ: ${body.differences.join(", ")}.`;
    const notes = field(result, "notes");
    notes.replaceChildren(
      ...body.notes.map((note) => {
        const entry = document.createElement("li");
        entry.textContent = `Note: ${note}.`;
        return entry;
      }),
    );
    result.hidden = false;
    $("#verify-result-title").focus();
    return body.reproduced ? `Reproduced: ${body.class_id} ${body.verdict}.` : "Not reproduced.";
  });
}

// --- Start ------------------------------------------------------------------------

function fillClasses(listing) {
  const select = $("#make-class");
  const groups = { export: "Export: your software writes DIGGS", import: "Import: your software reads DIGGS" };
  const children = [];
  for (const [direction, label] of Object.entries(groups)) {
    const group = document.createElement("optgroup");
    group.label = label;
    for (const item of listing.classes.filter((c) => c.direction === direction)) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${item.id}: ${item.title}`;
      group.append(option);
    }
    children.push(group);
  }
  select.replaceChildren(...children);
  select.disabled = false;
}

async function start() {
  setUpTabs();
  $("#make-form").addEventListener("submit", onMake);
  $("#grade-form").addEventListener("submit", onGrade);
  $("#verify-form").addEventListener("submit", onVerify);
  $("#make-download").addEventListener("click", () => {
    if (made) save(`${made.folder}.zip`, made.zip, "application/zip");
  });
  $("#make-reference").addEventListener("click", onReference);
  $("#grade-evidence").addEventListener("click", () => {
    if (graded) save("evidence.json", graded.evidence, "application/json");
  });
  $("#grade-report").addEventListener("click", () => {
    if (graded) save("report.html", graded.report, "text/html");
  });
  startWorker();
  try {
    await ready;
    const listing = reply(await call("catalogue"));
    fillClasses(listing);
    setBusy(false);
    say(`pyDIGGS ${listing.pydiggs} is ready in this tab. Suite ${listing.suite_version}.`, "ready");
  } catch (error) {
    say(`pyDIGGS could not be loaded: ${error.message}`, "failed");
  }
}

start();
