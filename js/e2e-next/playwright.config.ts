import { defineConfig, devices } from "@playwright/test";

// NEXT_MODE picks how the app is served: "build" (next build, then next start) or "dev"
// (next dev). NEXT_BUILD_FLAGS adds flags to `next build`, e.g. "--webpack" to bundle
// with webpack in place of Turbopack. Never reuse a server already on the port.
const mode = process.env.NEXT_MODE ?? "build";
const command =
  mode === "dev"
    ? "npx next dev -p 3000 -H 127.0.0.1"
    : `npx next build ${process.env.NEXT_BUILD_FLAGS ?? ""} && npx next start -p 3000 -H 127.0.0.1`;

export default defineConfig({
  testDir: "./tests",
  timeout: 300_000,
  expect: { timeout: 180_000 },
  retries: 0,
  fullyParallel: false,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://127.0.0.1:3000", trace: "retain-on-failure" },
  webServer: { command, url: "http://127.0.0.1:3000", timeout: 300_000, reuseExistingServer: false },
  projects: [{ name: `chromium-next-${mode}`, use: { ...devices["Desktop Chrome"] } }],
});
