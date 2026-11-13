// pyDIGGS's HTTP API, served by this Next.js app on the Node runtime: the real pyDIGGS in
// a worker thread, no Python. The README's Next.js recipe.

import { tmpdir } from "node:os";
import { join } from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// One engine per server process, created on the first request (never during `next
// build`), and kept on globalThis so the dev server's reloads do not start new threads.
const KEY = Symbol.for("@pydiggs/client/next-handler");

async function handler() {
  globalThis[KEY] ??= import("@pydiggs/client/node").then(({ createPydiggsHandler, PydiggsNode }) => {
    const wheel = process.env.PYDIGGS_WHEEL_PATH;
    const engine = new PydiggsNode({
      packageCacheDir: join(tmpdir(), "pydiggs-pyodide-packages"),
      ...(wheel ? { pydiggsWheelPath: wheel } : {}),
      onProgress: (stage) => console.log(`pydiggs: loading (${stage})`),
    });
    // The handler answers 503 while pydiggs is not ready and never says why to a
    // client; the server's own log is where the reason belongs.
    engine.ready.then(
      (versions) => console.log(`pydiggs: ready ${JSON.stringify(versions)}`),
      (error) => console.error(`pydiggs: failed to load: ${error?.stack ?? error}`),
    );
    return createPydiggsHandler(engine, { basePath: "/api/pydiggs" });
  });
  return globalThis[KEY];
}

export async function GET(request) {
  return (await handler())(request);
}

export async function POST(request) {
  return (await handler())(request);
}
