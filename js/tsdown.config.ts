import { defineConfig } from "tsdown";

// One file per entry in package.json "exports". The worker is its own entry so that
// browser.js can find it next to itself with new URL("./browser-worker.js", import.meta.url).
export default defineConfig({
  entry: {
    index: "src/index.ts",
    http: "src/http.ts",
    browser: "src/browser.ts",
    "browser-worker": "src/browser-worker.ts",
  },
  format: "esm",
  platform: "neutral",
  target: "es2022",
  fixedExtension: false,
  dts: true,
  sourcemap: true,
  clean: true,
});
