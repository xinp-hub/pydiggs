import { defineConfig } from "tsdown";

// One file per entry in package.json "exports". The worker is its own entry so that
// browser.js can find it next to itself with new URL("./browser-worker.js", import.meta.url).
export default defineConfig({
  entry: {
    index: "src/index.ts",
    http: "src/http.ts",
    browser: "src/browser.ts",
    hybrid: "src/hybrid.ts",
    node: "src/node.ts",
    cli: "src/cli.ts",
    "browser-worker": "src/browser-worker.ts",
    "node-worker": "src/node-worker.ts",
  },
  // Node's built-ins and Pyodide stay imports: the Node entries run where both exist,
  // and the browser entries never import them.
  deps: { neverBundle: [/^node:/, "pyodide"] },
  format: "esm",
  platform: "neutral",
  target: "es2022",
  fixedExtension: false,
  dts: true,
  sourcemap: true,
  clean: true,
});
