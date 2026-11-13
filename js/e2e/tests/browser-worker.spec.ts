import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

// The fixtures the Pyodide smokes use, and CPython's answers for them when CI provides
// them (tests/pyodide/cases.py). Without goldens, only the shape is checked.
const fixtures = new URL("../../../tests/pyodide/fixtures/", import.meta.url);
const FIXTURES = ["casing_units.xml", "markup_in_value.xml"];
const goldensPath = process.env.PYDIGGS_GOLDENS;
const goldens: Record<string, string> | undefined = goldensPath
  ? JSON.parse(readFileSync(goldensPath, "utf8"))
  : undefined;
const MARKUP = '<img src=x onerror="alert(document.domain)">';

test("the packed package runs pyDIGGS in a Web Worker, as CPython does", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (entry) => {
    if (entry.type() === "error") problems.push(entry.text());
  });
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("dialog", (dialog) => {
    problems.push(`a dialog opened: ${dialog.message()}`);
    void dialog.dismiss();
  });

  await page.goto("/");

  // Loading: the integrity-checked loader, the bundled worker, every stage in order.
  await expect(page.locator("#status")).toHaveText("ready");
  await expect(page.locator("#stages")).toHaveText("runtime,packages,pydiggs,warm-up");
  const versions = JSON.parse((await page.locator("#versions").textContent()) ?? "{}");
  expect(Object.keys(versions).sort()).toEqual(["lxml", "pydiggs", "pyodide"]);
  // Only a cross-origin isolated page can share the interrupt buffer with its worker.
  await expect(page.locator("#interruptible")).toHaveText(process.env.PYDIGGS_ISOLATED === "1" ? "true" : "false");

  for (const name of FIXTURES) {
    await page.locator("#file").setInputFiles(fileURLToPath(new URL(name, fixtures)));
    await expect(page.locator("#result-status")).toHaveText(`done:${name}`);
    const findings = JSON.parse((await page.locator("#findings").textContent()) ?? "null");
    const model = JSON.parse((await page.locator("#model").textContent()) ?? "null");
    if (goldens) {
      expect(findings).toEqual(JSON.parse(goldens[name]!));
      expect(model).toEqual(JSON.parse(goldens[`read:${name}`]!));
    } else {
      expect(findings.format).toBe("diggs-findings");
      expect(model.source.name).toBe(name);
    }
  }

  // Markup in an element value stays text: it is in a message, and no element was made.
  await expect(page.locator("#messages")).toContainText(MARKUP);
  await expect(page.locator("#messages img")).toHaveCount(0);

  // No CSP violation, no uncaught error, no dialog.
  expect(problems).toEqual([]);
});

test("the Vue recipe: a composable over one shared worker, as CPython does", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (entry) => {
    if (entry.type() === "error") problems.push(entry.text());
  });
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto("/vue.html");
  await expect(page.locator("#status")).toHaveText("ready");
  const name = FIXTURES[1]!;
  await page.locator("#file").setInputFiles(fileURLToPath(new URL(name, fixtures)));
  await expect(page.locator("#result-status")).toHaveText(`done:${name}`);
  const findings = JSON.parse((await page.locator("#findings").textContent()) ?? "null");
  if (goldens) expect(findings).toEqual(JSON.parse(goldens[name]!));
  await expect(page.locator("#messages")).toContainText(MARKUP);
  await expect(page.locator("#messages img")).toHaveCount(0);
  expect(problems).toEqual([]);
});
