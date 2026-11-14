import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

// @pydiggs/client/sw: after one visit online, the page loads Pyodide and pydiggs and
// validates with the network off. Chromium only: Playwright's offline switch reaches a
// service worker's own requests there.
const fixture = fileURLToPath(new URL("../../../tests/pyodide/fixtures/casing_units.xml", import.meta.url));
const goldensPath = process.env.PYDIGGS_GOLDENS;
const goldens: Record<string, string> | undefined = goldensPath ? JSON.parse(readFileSync(goldensPath, "utf8")) : undefined;

test("after one visit, pydiggs loads and validates offline", async ({ page, context, browserName, baseURL }) => {
  test.skip(browserName !== "chromium" || !baseURL?.endsWith(":4173"), "the production build in Chromium");
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(error.message));

  const run = async (): Promise<unknown> => {
    await expect(page.locator("#sw")).toHaveText("controlled");
    await expect(page.locator("#status")).toHaveText("ready");
    await page.locator("#file").setInputFiles(fixture);
    await expect(page.locator("#result-status")).toHaveText("done:casing_units.xml");
    return JSON.parse((await page.locator("#findings").textContent()) ?? "null");
  };

  await page.goto("/offline.html");
  const online = await run();
  const cached: string[] = JSON.parse((await page.locator("#cached").textContent()) ?? "[]");
  for (const file of ["pyodide.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json", "lxml-", "pydiggs-"]) {
    expect(cached.some((url) => url.split("/").pop()!.startsWith(file)), `${file} is cached`).toBe(true);
  }

  // The first visit was not yet under the service worker, so its page files are not in
  // the app shell; one more visit online puts them there.
  await page.reload();
  await run();
  await context.setOffline(true);
  await page.reload();
  const offline = await run();
  expect(offline).toEqual(online);
  if (goldens) expect(offline).toEqual(JSON.parse(goldens["casing_units.xml"]!));
  expect(problems).toEqual([]);
});
