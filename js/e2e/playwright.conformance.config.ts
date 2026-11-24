import { defineConfig, devices } from "@playwright/test";

// The in-browser conformance page (docs/conformance/try/), as the docs site serves it,
// in three browser engines. It loads Pyodide from jsDelivr and the pinned pyDIGGS wheel
// from PyPI, so it needs the network.
//
//   cd js/e2e && npm ci --ignore-scripts && npx playwright install chromium firefox webkit
//   npx playwright test -c playwright.conformance.config.ts
export default defineConfig({
  testDir: "./conformance",
  timeout: 300_000,
  expect: { timeout: 180_000 },
  retries: 0,
  fullyParallel: false,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://127.0.0.1:4176", trace: "retain-on-failure", acceptDownloads: true },
  webServer: {
    command: "node conformance/serve.mjs",
    url: "http://127.0.0.1:4176",
    timeout: 30_000,
    reuseExistingServer: false,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
