import { readFileSync } from "node:fs";

import { type Download, expect, type Page, test } from "@playwright/test";

// The in-browser conformance page (docs/conformance/try/): make a challenge, grade
// pyDIGGS's own export of it, verify the evidence, and catch a tampered one, all in the
// tab. The page says nothing leaves the browser; this test holds it to that: once
// pyDIGGS is loaded, making, grading and verifying send no request at all, and no
// request of the whole visit carries a body or anything from the files.

const PYDIGGS_WHEEL = "pydiggs-1.18.0-py3-none-any.whl";
const MARKUP = '<img src=x onerror="alert(document.domain)">';

interface Seen {
  url: string;
  method: string;
  body: Buffer | null;
  phase: string;
}

async function saved(page: Page, click: () => Promise<void>, path: string): Promise<{ name: string; data: Buffer }> {
  const [download]: [Download, void] = await Promise.all([page.waitForEvent("download"), click()]);
  await download.saveAs(path);
  return { name: download.suggestedFilename(), data: readFileSync(path) };
}

test("make, grade and verify in the tab, with nothing sent anywhere", async ({ page, context, browserName }, testInfo) => {
  const problems: string[] = [];
  page.on("console", (entry) => {
    if (entry.type() === "error") problems.push(entry.text());
  });
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("dialog", (dialog) => {
    problems.push(`a dialog opened: ${dialog.message()}`);
    void dialog.dismiss();
  });

  // Every request of the visit, the worker's included (it fetches the wheel itself).
  let phase = "load";
  const seen: Seen[] = [];
  context.on("request", (request) => {
    // A blob: or data: URL is the page's own memory (the downloads), not the network.
    if (/^(blob|data):/.test(request.url())) return;
    seen.push({ url: request.url(), method: request.method(), body: request.postDataBuffer(), phase });
  });

  await page.goto("/");
  await expect(page.locator("#engine")).toHaveAttribute("data-state", "ready");
  await expect(page.locator("#engine")).toContainText("pyDIGGS 1.18.0 is ready");
  await expect(page.locator("#engine")).toContainText("Suite 1.0-beta.3");
  // The classes come from the pinned pyDIGGS: all 14 of this release.
  await expect(page.locator("#make-class option")).toHaveCount(14);
  for (const family of ["mwd", "dissipation", "in-situ-strength"]) {
    await expect(page.locator(`#make-class option[value="${family}-export"]`)).toHaveCount(1);
    await expect(page.locator(`#make-class option[value="${family}-import"]`)).toHaveCount(1);
  }
  // The recorder sees the worker's own requests, so an upload from the worker would be
  // seen too.
  expect(seen.some((r) => r.url.endsWith(`/${PYDIGGS_WHEEL}`))).toBe(true);
  expect(seen.some((r) => r.url.endsWith("/pyodide.mjs"))).toBe(true);

  // What the page promises, in words.
  await expect(page.locator(".notice")).toContainText("never uploaded");
  await expect(page.locator(".notice")).toContainText("self-tested");

  // Keyboard: the tabs follow the arrow keys, Home and End.
  await page.getByRole("tab", { name: "1. Make a challenge" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "2. Grade" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-grade")).toBeVisible();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "3. Verify evidence" })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(page.locator("#panel-make")).toBeVisible();
  await expect(page.locator("#engine")).toHaveAttribute("aria-live", "polite");

  phase = "files";
  const marker = `Marker${Date.now().toString(36)}`;
  if (browserName === "chromium") await context.setOffline(true); // and it still works

  // 1. Make an export challenge; every control is found by its label.
  await page.getByLabel("Class").selectOption("spt-export");
  await page.getByLabel("Product", { exact: true }).fill(marker);
  await page.getByLabel("Product version").fill("4.2.1");
  await page.getByLabel("Seed (optional)").fill("e2e");
  await page.getByRole("button", { name: "Make the challenge" }).click();
  await expect(page.locator("#make-result")).toBeVisible();
  await expect(page.locator("#make-result-title")).toBeFocused();
  await expect(page.locator("#engine")).toContainText("Challenge spt-export made, seed e2e");
  const zip = await saved(page, () => page.getByRole("button", { name: "Download the challenge (ZIP)" }).click(), testInfo.outputPath("challenge.zip"));
  expect(zip.name).toBe("spt-export-e2e.zip");
  expect(zip.data.subarray(0, 2).toString("latin1")).toBe("PK");
  const xml = await saved(page, () => page.getByRole("button", { name: /pyDIGGS's own export/ }).click(), testInfo.outputPath("export.xml"));
  expect(xml.data.toString("utf8")).toContain("<Diggs");

  // 2. Grade it against the challenge made on the page.
  await page.getByRole("tab", { name: "2. Grade" }).click();
  await page.getByLabel("Your DIGGS export, or the filled answers.csv").setInputFiles(testInfo.outputPath("export.xml"));
  await page.getByRole("button", { name: "Grade", exact: true }).click();
  await expect(page.locator("#grade-result-title")).toHaveText("spt-export: PASS");
  await expect(page.locator("#grade-result-title")).toBeFocused();
  await expect(page.locator("#grade-result")).toContainText(`${marker} 4.2.1 (as declared)`);
  const evidenceFile = await saved(page, () => page.getByRole("button", { name: "Download evidence.json" }).click(), testInfo.outputPath("evidence.json"));
  const evidence = JSON.parse(evidenceFile.data.toString("utf8"));
  expect(evidence.verdict).toBe("pass");
  expect(evidence.tier).toBe("self-tested");
  expect(evidence.submission.name).toBe("export.xml");
  const report = await saved(page, () => page.getByRole("button", { name: "Download report.html" }).click(), testInfo.outputPath("report.html"));
  expect(report.data.toString("utf8")).toContain("spt-export");

  // 3. Verify it, with the challenge as the ZIP this time; then a tampered copy.
  await page.getByRole("tab", { name: "3. Verify evidence" }).click();
  await page.getByLabel("Evidence: evidence.json").setInputFiles(testInfo.outputPath("evidence.json"));
  await page.locator("#verify-challenge").setInputFiles(testInfo.outputPath("challenge.zip"));
  await page.getByLabel("The graded file").setInputFiles(testInfo.outputPath("export.xml"));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.locator("#verify-result-title")).toHaveText("Reproduced: spt-export pass");
  const tampered = { ...evidence, verdict: "fail" };
  await page.getByLabel("Evidence: evidence.json").setInputFiles({
    name: "evidence.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(tampered)),
  });
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.locator("#verify-result-title")).toHaveText("Not reproduced");
  await expect(page.locator("#verify-result")).toContainText("verdict");

  // A file that is not DIGGS, carrying markup, fails as text: no element is made.
  await page.getByRole("tab", { name: "2. Grade" }).click();
  await page.getByLabel("Your DIGGS export, or the filled answers.csv").setInputFiles({
    name: "not-diggs.xml",
    mimeType: "application/xml",
    buffer: Buffer.from(`<root>${MARKUP}</root>`),
  });
  await page.getByRole("button", { name: "Grade", exact: true }).click();
  await expect(page.locator("#grade-result-title")).toHaveText("spt-export: FAIL");
  await expect(page.locator("#grade-result img")).toHaveCount(0);

  // A class new in this release: its challenge and pyDIGGS's own export of it grade too.
  await page.getByRole("tab", { name: "1. Make a challenge" }).click();
  await page.getByLabel("Class").selectOption("mwd-export");
  await page.getByLabel("Seed (optional)").fill("e2e");
  await page.getByRole("button", { name: "Make the challenge" }).click();
  await expect(page.locator("#engine")).toContainText("Challenge mwd-export made, seed e2e");
  const mwdZip = await saved(page, () => page.getByRole("button", { name: "Download the challenge (ZIP)" }).click(), testInfo.outputPath("mwd.zip"));
  expect(mwdZip.name).toBe("mwd-export-e2e.zip");
  await saved(page, () => page.getByRole("button", { name: /pyDIGGS's own export/ }).click(), testInfo.outputPath("mwd.xml"));
  await page.getByRole("tab", { name: "2. Grade" }).click();
  await page.getByLabel("Your DIGGS export, or the filled answers.csv").setInputFiles(testInfo.outputPath("mwd.xml"));
  await page.getByRole("button", { name: "Grade", exact: true }).click();
  await expect(page.locator("#grade-result-title")).toHaveText("mwd-export: PASS");

  // An import challenge, graded with no answers: it fails, and says why.
  await page.getByRole("tab", { name: "1. Make a challenge" }).click();
  await page.getByLabel("Class").selectOption("spt-import");
  await page.getByLabel("Seed (optional)").fill("e2e");
  await page.getByRole("button", { name: "Make the challenge" }).click();
  await expect(page.locator("#engine")).toContainText("Challenge spt-import made");
  await page.getByRole("tab", { name: "2. Grade" }).click();
  await page.getByLabel("Your DIGGS export, or the filled answers.csv").setInputFiles({
    name: "answers.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("question_id,answer\r\n"),
  });
  await page.getByRole("button", { name: "Grade", exact: true }).click();
  await expect(page.locator("#grade-result-title")).toHaveText("spt-import: FAIL");
  await expect(page.locator("#grade-items")).toContainText("conformance.answer-missing");

  // Nothing left the tab: no request at all once files were in play, and none in the
  // whole visit that carried a body, the product name or the file's digest.
  expect(seen.filter((r) => r.phase === "files").map((r) => `${r.method} ${r.url}`)).toEqual([]);
  for (const request of seen) {
    expect(request.method, request.url).toBe("GET");
    expect(request.body, request.url).toBeNull();
    expect(request.url).not.toContain(marker);
    expect(request.url).not.toContain(evidence.submission.sha256);
  }
  expect(problems).toEqual([]);
});
