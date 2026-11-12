import { defineConfig, devices } from "@playwright/test";

// A production build, served by `vite preview` under the strict CSP, in three browser
// engines; and the Vite dev server, where most developers meet the package first. Never
// reuse a server already on a port: it may serve an older build.
export default defineConfig({
  testDir: "./tests",
  timeout: 240_000,
  expect: { timeout: 150_000 },
  retries: 0,
  fullyParallel: false,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
  webServer: [
    {
      command: "npx vite build && npx vite preview",
      url: "http://127.0.0.1:4173",
      timeout: 180_000,
      reuseExistingServer: false,
    },
    {
      command: "npx vite",
      url: "http://127.0.0.1:5173",
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "chromium-vite-dev", use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:5173" } },
  ],
});
