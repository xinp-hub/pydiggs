import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, existsSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { vendor } from "../src/vendor.ts";

const sha256 = (data: string | Uint8Array) => createHash("sha256").update(data).digest("hex");
const sri = (data: string) => `sha384-${createHash("sha384").update(data).digest("base64")}`;

// A stand-in for the npm pyodide package, and a fake network serving what it is told to.
function fixture(options: { version?: string; lockName?: string } = {}) {
  const pyodideDir = mkdtempSync(join(tmpdir(), "fake-pyodide-"));
  const outDir = join(mkdtempSync(join(tmpdir(), "vendored-")), "out");
  const loader = "export const loadPyodide = 1;";
  const wheels: Record<string, string> = { "lxml-6.wasm.whl": "lxml bytes", "dep-1.whl": "dep bytes", "micropip-0.whl": "micropip bytes" };
  writeFileSync(join(pyodideDir, "package.json"), JSON.stringify({ version: options.version ?? "9.9.9" }));
  writeFileSync(join(pyodideDir, "pyodide.mjs"), loader);
  writeFileSync(join(pyodideDir, "pyodide.asm.wasm"), "wasm");
  writeFileSync(join(pyodideDir, "pyodide.d.ts"), "types");
  writeFileSync(
    join(pyodideDir, "pyodide-lock.json"),
    JSON.stringify({
      packages: {
        lxml: { file_name: options.lockName ?? "lxml-6.wasm.whl", sha256: sha256(wheels["lxml-6.wasm.whl"]!), depends: ["dep"] },
        dep: { file_name: "dep-1.whl", sha256: sha256(wheels["dep-1.whl"]!) },
        micropip: { file_name: "micropip-0.whl", sha256: sha256(wheels["micropip-0.whl"]!) },
      },
    }),
  );
  const wheel = "the pydiggs wheel";
  const served: Record<string, string> = {
    ...Object.fromEntries(Object.entries(wheels).map(([name, body]) => [`https://cdn.test/${name}`, body])),
    "https://pypi.org/pypi/pydiggs/1.8.0/json": JSON.stringify({
      urls: [
        { filename: "pydiggs-1.8.0.tar.gz", packagetype: "sdist", url: "https://files.test/sdist", digests: { sha256: "x" } },
        {
          filename: "pydiggs-1.8.0-py3-none-any.whl",
          packagetype: "bdist_wheel",
          url: "https://files.test/pydiggs-1.8.0-py3-none-any.whl",
          digests: { sha256: sha256(wheel) },
        },
      ],
    }),
    "https://files.test/pydiggs-1.8.0-py3-none-any.whl": wheel,
  };
  const fetched: string[] = [];
  const fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    fetched.push(url);
    const body = served[url];
    return body === undefined ? new Response("missing", { status: 404 }) : new Response(body);
  }) as typeof globalThis.fetch;
  const base = {
    outDir,
    pyodideDir,
    fetch,
    packageBaseUrl: "https://cdn.test/",
    expect: { pyodideVersion: "9.9.9", loaderSri: sri(loader) },
  };
  return { base, outDir, fetched, served };
}

test("vendor brings Pyodide's files, the packages with their dependencies, and the pydiggs wheel", async () => {
  const { base, outDir, fetched } = fixture();
  const result = await vendor({ ...base, pydiggs: { version: "1.8.0" } });
  for (const name of ["pyodide.mjs", "pyodide.asm.wasm", "pyodide-lock.json", "lxml-6.wasm.whl", "dep-1.whl", "micropip-0.whl", "pydiggs-1.8.0-py3-none-any.whl", "pydiggs-vendor.json"]) {
    assert.ok(existsSync(join(outDir, name)), `${name} is in the folder`);
  }
  assert.equal(existsSync(join(outDir, "package.json")), false);
  assert.equal(existsSync(join(outDir, "pyodide.d.ts")), false);
  assert.equal(result.pydiggsWheel, "pydiggs-1.8.0-py3-none-any.whl");
  const manifest = JSON.parse(readFileSync(join(outDir, "pydiggs-vendor.json"), "utf8"));
  assert.equal(manifest.pyodideVersion, "9.9.9");
  assert.ok(manifest.files.every((f: { sha256: string }) => /^[0-9a-f]{64}$/.test(f.sha256)));
  assert.equal(fetched.filter((url) => url.startsWith("https://cdn.test/")).length, 3);
});

test("a second run keeps files whose hash already matches", async () => {
  const { base, fetched } = fixture();
  await vendor({ ...base, pydiggs: false });
  const before = fetched.length;
  await vendor({ ...base, pydiggs: false });
  assert.equal(fetched.length, before, "nothing downloaded again");
});

test("packagesOnly leaves Pyodide's own files out but keeps its lock file", async () => {
  const { base, outDir } = fixture();
  await vendor({ ...base, packagesOnly: true, pydiggs: false });
  assert.equal(existsSync(join(outDir, "pyodide.mjs")), false);
  assert.ok(existsSync(join(outDir, "pyodide-lock.json")));
  assert.ok(existsSync(join(outDir, "lxml-6.wasm.whl")));
});

test("a download whose hash differs from the lock file is refused and not written", async () => {
  const { base, outDir, served } = fixture();
  served["https://cdn.test/dep-1.whl"] = "tampered";
  await assert.rejects(vendor({ ...base, pydiggs: false }), /dep-1\.whl: sha256 does not match/);
  assert.equal(existsSync(join(outDir, "dep-1.whl")), false);
});

test("a tampered pydiggs wheel from PyPI is refused", async () => {
  const { base, served } = fixture();
  served["https://files.test/pydiggs-1.8.0-py3-none-any.whl"] = "tampered";
  await assert.rejects(vendor({ ...base, pydiggs: { version: "1.8.0" } }), /does not match PyPI/);
});

test("the wrong Pyodide, a loader the worker would reject, or an unsafe name stop it", async () => {
  const wrong = fixture({ version: "1.0.0" });
  await assert.rejects(vendor(wrong.base), /pyodide 1\.0\.0 is installed/);
  const loader = fixture();
  await assert.rejects(vendor({ ...loader.base, expect: { pyodideVersion: "9.9.9", loaderSri: "sha384-other" } }), /integrity hash/);
  const unsafe = fixture({ lockName: "../escape.whl" });
  await assert.rejects(vendor({ ...unsafe.base, pydiggs: false }), /unsafe file name/);
});

test("a local wheel is copied, and only a pure pydiggs wheel", async () => {
  const { base, outDir } = fixture();
  const dir = mkdtempSync(join(tmpdir(), "wheels-"));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "pydiggs-2.0.0-py3-none-any.whl"), "local wheel");
  const result = await vendor({ ...base, pydiggs: { wheelPath: join(dir, "pydiggs-2.0.0-py3-none-any.whl") } });
  assert.equal(result.pydiggsWheel, "pydiggs-2.0.0-py3-none-any.whl");
  assert.equal(readFileSync(join(outDir, "pydiggs-2.0.0-py3-none-any.whl"), "utf8"), "local wheel");
  writeFileSync(join(dir, "other-1.0-py3-none-any.whl"), "x");
  await assert.rejects(vendor({ ...base, pydiggs: { wheelPath: join(dir, "other-1.0-py3-none-any.whl") } }), /not a pure pydiggs wheel/);
});
