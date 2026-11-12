import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, type Page, test } from "@playwright/test";

const fixtures = new URL("../../../tests/pyodide/fixtures/", import.meta.url);
const FIXTURES = ["casing_units.xml", "markup_in_value.xml"];
const goldensPath = process.env.PYDIGGS_GOLDENS;
const goldens: Record<string, string> | undefined = goldensPath
  ? JSON.parse(readFileSync(goldensPath, "utf8"))
  : undefined;
const fixture = (name: string): string => fileURLToPath(new URL(name, fixtures));

function watchProblems(page: Page): string[] {
  const problems: string[] = [];
  page.on("console", (entry) => {
    if (entry.type() === "error") problems.push(entry.text());
  });
  page.on("pageerror", (error) => problems.push(error.message));
  return problems;
}

test("a client component runs pyDIGGS in a Web Worker bundled by Next, as CPython does", async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto("/");
  await expect(page.locator("#status")).toHaveText("ready");
  await expect(page.locator("#stages")).toHaveText("runtime,packages,pydiggs,warm-up");
  for (const name of FIXTURES) {
    await page.locator("#file").setInputFiles(fixture(name));
    await expect(page.locator("#result-status")).toHaveText(`done:${name}`);
    const findings = JSON.parse((await page.locator("#findings").textContent()) ?? "null");
    const model = JSON.parse((await page.locator("#model").textContent()) ?? "null");
    if (goldens) {
      expect(findings).toEqual(JSON.parse(goldens[name]!));
      expect(model).toEqual(JSON.parse(goldens[`read:${name}`]!));
    } else {
      expect(findings.format).toBe("diggs-findings");
    }
  }
  await expect(page.locator("#messages")).toContainText('<img src=x onerror="alert(document.domain)">');
  await expect(page.locator("#messages img")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("the route handler serves pyDIGGS's HTTP API from a worker thread, as CPython does", async ({ request }) => {
  await expect
    .poll(async () => (await request.get("/api/pydiggs/v1/readyz")).status(), { timeout: 120_000 })
    .toBe(200);
  for (const name of FIXTURES) {
    const body = readFileSync(fixture(name));
    const validated = await request.post(`/api/pydiggs/v1/validate?name=${name}`, {
      data: body,
      headers: { "Content-Type": "application/xml" },
    });
    expect(validated.status()).toBe(200);
    const read = await request.post(`/api/pydiggs/v1/read?name=${name}`, {
      data: body,
      headers: { "Content-Type": "application/xml" },
    });
    expect(read.status()).toBe(200);
    if (goldens) {
      expect(await validated.json()).toEqual(JSON.parse(goldens[name]!));
      expect(await read.json()).toEqual(JSON.parse(goldens[`read:${name}`]!));
    }
  }
  const refused = await request.post("/api/pydiggs/v1/validate?checks=schema", {
    data: "<Diggs/>",
    headers: { "Content-Type": "application/xml" },
  });
  expect(refused.status()).toBe(422);
  expect((await refused.json()).code).toBe("invalid-parameter");
});

test("PydiggsHybrid in a page falls back to the app's own route handler", async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto("/hybrid");
  const name = FIXTURES[1]!;
  await page.locator("#file").setInputFiles(fixture(name));
  await expect(page.locator("#result-status")).toHaveText(`done:${name}`);
  await expect(page.locator("#via")).toHaveText("server");
  await expect(page.locator("#reason")).toHaveText("no-browser");
  if (goldens) {
    const findings = JSON.parse((await page.locator("#findings").textContent()) ?? "null");
    expect(findings).toEqual(JSON.parse(goldens[name]!));
  }
  expect(problems).toEqual([]);
});
