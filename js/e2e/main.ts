// The test app: what a vendor's page does with @pydiggs/client/browser, written the way
// the README says. Playwright drives it in tests/browser-worker.spec.ts.
/// <reference types="vite/client" />

import { type LoadStage, PydiggsBrowser } from "@pydiggs/client/browser";

const $ = (id: string): HTMLElement => document.getElementById(id)!;
const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

// CI installs the wheel built from this checkout, so the answers can be compared with
// CPython's from the same checkout. Without it, the pinned release comes from PyPI.
// Self-hosted, VITE_PYODIDE_BASE names the folder `pydiggs-client vendor` filled.
const wheel = import.meta.env.VITE_PYDIGGS_WHEEL as string | undefined;
const pyodideBase = import.meta.env.VITE_PYODIDE_BASE as string | undefined;

const stages: LoadStage[] = [];
const pydiggs = new PydiggsBrowser({
  ...(wheel ? { pydiggsWheelUrl: new URL(wheel, location.href) } : {}),
  ...(pyodideBase ? { pyodideBaseUrl: new URL(pyodideBase, location.href) } : {}),
  onProgress: (stage) => {
    stages.push(stage);
    $("stages").textContent = stages.join(",");
  },
});

pydiggs.ready.then(
  (versions) => {
    $("status").textContent = "ready";
    $("versions").textContent = JSON.stringify(versions);
  },
  (error: unknown) => {
    $("status").textContent = "failed";
    $("error").textContent = message(error);
  },
);

const input = $("file") as HTMLInputElement;
input.addEventListener("change", async () => {
  const file = input.files?.[0];
  if (!file) return;
  $("result-status").textContent = `working:${file.name}`;
  try {
    const findings = await pydiggs.validate(file);
    const model = await pydiggs.read(file);
    $("findings").textContent = JSON.stringify(findings);
    $("model").textContent = JSON.stringify(model);
    $("verdict").textContent = findings.files[0]?.verdict ?? "";
    // Messages are untrusted: written as text, never as HTML.
    $("messages").replaceChildren(
      ...(findings.files[0]?.findings ?? []).map((finding) => {
        const item = document.createElement("li");
        item.textContent = finding.message;
        return item;
      }),
    );
    $("result-status").textContent = `done:${file.name}`;
  } catch (error) {
    $("result-status").textContent = `error:${file.name}`;
    $("error").textContent = message(error);
  }
});
