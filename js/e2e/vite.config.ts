import { defineConfig } from "vite";

// The policies docs/browser.md documents, page and worker together: the worker may
// compile WebAssembly and import the integrity-checked loader from a blob: URL. With
// Pyodide from its CDN it may also fetch from the CDN and PyPI; self-hosted
// (PYDIGGS_SELF_HOSTED=1, files from `pydiggs-client vendor`) it may fetch from this
// origin only, so any file the folder lacks is a violation. A violation shows as a
// console error, which the test fails on.
const selfHosted = process.env.PYDIGGS_SELF_HOSTED === "1";
// PYDIGGS_ISOLATED=1 serves the app cross-origin isolated, so the page can share memory
// with the worker and a long request is interrupted rather than the worker replaced.
const isolated = process.env.PYDIGGS_ISOLATED === "1";
const thirdParty = selfHosted ? "" : " https://cdn.jsdelivr.net";
const csp = [
  "default-src 'none'",
  `script-src 'self' blob:${thirdParty} 'wasm-unsafe-eval'`,
  "worker-src 'self'",
  `connect-src 'self'${selfHosted ? "" : " https://cdn.jsdelivr.net https://files.pythonhosted.org"}`,
  "style-src 'self'",
  "img-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

export default defineConfig({
  // The package's worker imports shared chunks, so it must be an ES module worker.
  worker: { format: "es" },
  // The dev server pre-bundles dependencies into one file, which loses the worker file
  // the package finds next to itself. Leaving the package out of pre-bundling keeps it
  // as published; the production build is not affected.
  optimizeDeps: { exclude: ["@pydiggs/client"] },
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
  build: {
    target: "es2022",
    // Three pages: the plain one (main.ts), the Vue recipe (vue.ts) and the offline
    // recipe (offline.ts), plus the offline recipe's service worker at a fixed /sw.js.
    rollupOptions: {
      input: { main: "index.html", vue: "vue.html", offline: "offline.html", sw: "sw.ts" },
      output: { entryFileNames: (chunk) => (chunk.name === "sw" ? "sw.js" : "assets/[name]-[hash].js") },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    headers: {
      "Content-Security-Policy": csp,
      ...(isolated ? { "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp" } : {}),
    },
  },
});
