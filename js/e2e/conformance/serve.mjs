// Serves the in-browser conformance page (docs/conformance/try/) on
// http://127.0.0.1:4176/ for playwright.conformance.config.ts, as a static host would,
// with the worker's Content-Security-Policy. The page carries its own policy in a
// <meta> tag (what GitHub Pages serves); a worker takes its policy only from its own
// response headers, so this sends one for worker.mjs, the policy docs/browser.md gives.
// Any CSP violation is a console error, which the test fails on.
//
// Node standard library only; binds to localhost; serves the page's five files only.

import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

const ROOT = new URL("../../../docs/conformance/try/", import.meta.url);
const PORT = Number(process.env.PORT ?? 4176);
const TYPES = {
  "index.html": "text/html; charset=utf-8",
  "app.mjs": "text/javascript; charset=utf-8",
  "worker.mjs": "text/javascript; charset=utf-8",
  "style.css": "text/css; charset=utf-8",
  "conformance_page.py": "text/x-python; charset=utf-8",
};
const WORKER_CSP = [
  "default-src 'none'",
  "script-src 'self' blob: https://cdn.jsdelivr.net 'wasm-unsafe-eval'",
  "connect-src 'self' https://cdn.jsdelivr.net https://files.pythonhosted.org",
].join("; ");

createServer(async (request, response) => {
  const path = new URL(request.url ?? "/", "http://localhost").pathname;
  const name = path === "/" ? "index.html" : path.slice(1);
  const headers = { "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store" };
  if (request.method !== "GET" || !Object.hasOwn(TYPES, name)) {
    response.writeHead(path === "/favicon.ico" ? 204 : 404, headers).end();
    return;
  }
  const body = await readFile(fileURLToPath(new URL(name, ROOT)));
  if (name === "worker.mjs") headers["Content-Security-Policy"] = WORKER_CSP;
  response.writeHead(200, { ...headers, "Content-Type": TYPES[name] }).end(body);
}).listen(PORT, "127.0.0.1", () => console.log(`conformance page on http://127.0.0.1:${PORT}/`));
