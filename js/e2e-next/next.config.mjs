// The configuration a Next.js app needs for @pydiggs/client, and nothing more.

import { fileURLToPath } from "node:url";

// This app sits inside the pyDIGGS repository, next to another lockfile (js/), so Next
// would guess the workspace root. Name it. A standalone app does not need this.
const root = fileURLToPath(new URL(".", import.meta.url));

// The worker's policy (docs/browser.md). A worker takes its CSP from the response that
// served its script, and Next serves the bundled worker from /_next/static, so the policy
// goes there. A page policy for Next itself needs nonces for its inline scripts; that is
// Next's own set-up, not this package's.
const workerCsp = [
  "default-src 'none'",
  "script-src 'self' blob: https://cdn.jsdelivr.net 'wasm-unsafe-eval'",
  "connect-src 'self' https://cdn.jsdelivr.net https://pypi.org https://files.pythonhosted.org",
].join("; ");

/** @type {import("next").NextConfig} */
const config = {
  turbopack: { root },
  outputFileTracingRoot: root,
  // Keep the Node side as installed: PydiggsNode finds its worker-thread file next to
  // itself, and Pyodide loads its WebAssembly from its own folder. Bundled into the
  // server build, neither would be found.
  serverExternalPackages: ["@pydiggs/client", "pyodide"],
  async headers() {
    return [{ source: "/_next/static/:path*", headers: [{ key: "Content-Security-Policy", value: workerCsp }] }];
  },
};

export default config;
