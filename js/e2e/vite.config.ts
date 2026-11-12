import { defineConfig } from "vite";

// The policy docs/browser.md documents for the CDN set-up, page and worker together:
// the worker may compile WebAssembly and import the integrity-checked loader from a
// blob: URL, and fetch only from Pyodide's CDN, PyPI and this origin. A violation shows
// as a console error, which the test fails on.
const csp = [
  "default-src 'none'",
  "script-src 'self' blob: https://cdn.jsdelivr.net 'wasm-unsafe-eval'",
  "worker-src 'self'",
  "connect-src 'self' https://cdn.jsdelivr.net https://pypi.org https://files.pythonhosted.org",
  "style-src 'self'",
  "img-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

export default defineConfig({
  // The package's worker imports shared chunks, so it must be an ES module worker.
  worker: { format: "es" },
  build: { target: "es2022" },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    headers: { "Content-Security-Policy": csp },
  },
});
