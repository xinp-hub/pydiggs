// Assembles everything pydiggs needs at run time into one folder, so nothing is fetched
// from a CDN or PyPI when it runs:
//
// - for a browser, a folder to serve and pass as `pyodideBaseUrl` (with
//   `pydiggsWheelUrl` pointing at the wheel in it): Pyodide's own files from the npm
//   `pyodide` package, plus lxml, micropip and what they need;
// - for Node and serverless functions, a `packageCacheDir` that is already complete
//   (with `pydiggsWheelPath`), so a cold start downloads nothing and a read-only file
//   system is fine.
//
// Every downloaded file is checked against the sha256 that Pyodide's lock file or PyPI
// publishes for it, and Pyodide's loader against the integrity hash the browser worker
// checks. Nothing that fails a check is written.

import { createHash } from "node:crypto";
import { copyFile, mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { basename, dirname, join } from "node:path";

import { PYDIGGS_VERSION, PYODIDE_SRI, PYODIDE_VERSION } from "./generated/pins.ts";

export interface VendorOptions {
  /** The folder to fill. Created if needed; files already there with the right hash are kept. */
  outDir: string;
  /**
   * Leave out Pyodide's own files (the npm `pyodide` package has them for Node): only
   * the packages and the pydiggs wheel. Default: false.
   */
  packagesOnly?: boolean;
  /** Where the npm `pyodide` package is. Default: resolved from this package. */
  pyodideDir?: string;
  /** Pyodide packages to bring, with everything they depend on. Default: lxml and micropip. */
  packages?: string[];
  /**
   * The pydiggs wheel: a release from PyPI (default `PYDIGGS_VERSION`), a wheel on disk,
   * or `false` for none.
   */
  pydiggs?: { version: string } | { wheelPath: string } | false;
  /** Where Pyodide's packages are downloaded from. Default: Pyodide's CDN for this release. */
  packageBaseUrl?: string;
  /** The fetch to use. Default: the global one. */
  fetch?: typeof globalThis.fetch;
  /** Called with a line for each step. */
  log?: (line: string) => void;
  /** Advanced, for tests: the versions and hash to hold the folder to. */
  expect?: { pyodideVersion: string; loaderSri: string };
}

export interface VendoredFile {
  name: string;
  sha256: string;
  /** Where it came from: "pyodide" (the npm package), a URL, or a path. */
  source: string;
}

export interface VendorResult {
  dir: string;
  pyodideVersion: string;
  /** File name of the pydiggs wheel in the folder, if one was brought. */
  pydiggsWheel: string | undefined;
  files: VendoredFile[];
}

interface LockPackage {
  file_name: string;
  sha256: string;
  depends?: string[];
}

interface PypiRelease {
  urls: { filename: string; packagetype: string; url: string; digests: { sha256: string } }[];
}

const SAFE_NAME = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;
const MANIFEST = "pydiggs-vendor.json";

const sha256 = (data: Uint8Array): string => createHash("sha256").update(data).digest("hex");

function safeName(name: string, what: string): string {
  if (!SAFE_NAME.test(name)) throw new Error(`refusing ${what} with an unsafe file name: ${JSON.stringify(name)}`);
  return name;
}

async function existingHash(path: string): Promise<string | undefined> {
  try {
    return sha256(await readFile(path));
  } catch {
    return undefined;
  }
}

function findPyodideDir(): string {
  const require = createRequire(import.meta.url);
  try {
    return dirname(require.resolve("pyodide"));
  } catch {
    throw new Error(`cannot find the npm pyodide package: npm install pyodide@${PYODIDE_VERSION}, or pass pyodideDir`);
  }
}

/** Fills `outDir` and returns what is in it. Throws, writing nothing more, at the first failed check. */
export async function vendor(options: VendorOptions): Promise<VendorResult> {
  const fetch = options.fetch ?? globalThis.fetch;
  const log = options.log ?? ((): void => {});
  const expected = options.expect ?? { pyodideVersion: PYODIDE_VERSION, loaderSri: PYODIDE_SRI };
  const out = options.outDir;
  const files: VendoredFile[] = [];
  await mkdir(out, { recursive: true });

  // 1. The npm pyodide package must be the release pydiggs is tested with.
  const pyodideDir = options.pyodideDir ?? findPyodideDir();
  const pkg = JSON.parse(await readFile(join(pyodideDir, "package.json"), "utf8")) as { version?: string };
  if (pkg.version !== expected.pyodideVersion) {
    throw new Error(`pyodide ${pkg.version} is installed; @pydiggs/client needs ${expected.pyodideVersion}`);
  }
  const lockBytes = await readFile(join(pyodideDir, "pyodide-lock.json"));
  const lock = JSON.parse(lockBytes.toString("utf8")) as { packages: Record<string, LockPackage> };

  // 2. Pyodide's own files, for a browser. The loader must match the worker's integrity hash.
  if (!options.packagesOnly) {
    const loader = await readFile(join(pyodideDir, "pyodide.mjs"));
    const sri = `sha384-${createHash("sha384").update(loader).digest("base64")}`;
    if (sri !== expected.loaderSri) {
      throw new Error(`pyodide.mjs does not match the integrity hash the browser worker checks (${expected.loaderSri})`);
    }
    for (const entry of await readdir(pyodideDir, { withFileTypes: true })) {
      if (!entry.isFile() || entry.name === "package.json" || entry.name.endsWith(".d.ts")) continue;
      const data = await readFile(join(pyodideDir, entry.name));
      await writeFile(join(out, entry.name), data);
      files.push({ name: entry.name, sha256: sha256(data), source: "pyodide" });
    }
    log(`copied Pyodide ${pkg.version}'s own files (${files.length})`);
  } else {
    await writeFile(join(out, "pyodide-lock.json"), lockBytes);
    files.push({ name: "pyodide-lock.json", sha256: sha256(lockBytes), source: "pyodide" });
  }

  // 3. The packages and everything they depend on, checked against the lock file.
  const base = options.packageBaseUrl ?? `https://cdn.jsdelivr.net/pyodide/v${expected.pyodideVersion}/full/`;
  const wanted = [...(options.packages ?? ["lxml", "micropip"])];
  const seen = new Set<string>();
  while (wanted.length > 0) {
    const name = wanted.shift()!.toLowerCase();
    if (seen.has(name)) continue;
    seen.add(name);
    const entry = lock.packages[name];
    if (!entry) throw new Error(`Pyodide ${expected.pyodideVersion} has no package ${JSON.stringify(name)}`);
    wanted.push(...(entry.depends ?? []));
    const file = safeName(entry.file_name, `package ${name}`);
    const target = join(out, file);
    if ((await existingHash(target)) !== entry.sha256) {
      const url = new URL(file, base).href;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
      const data = new Uint8Array(await response.arrayBuffer());
      if (sha256(data) !== entry.sha256) throw new Error(`${file}: sha256 does not match Pyodide's lock file`);
      await writeFile(target, data);
      log(`downloaded ${file}`);
    } else {
      log(`kept ${file}`);
    }
    files.push({ name: file, sha256: entry.sha256, source: new URL(file, base).href });
  }

  // 4. The pydiggs wheel.
  let pydiggsWheel: string | undefined;
  const wheel = options.pydiggs ?? { version: PYDIGGS_VERSION };
  if (wheel && "wheelPath" in wheel) {
    pydiggsWheel = safeName(basename(wheel.wheelPath), "the pydiggs wheel");
    if (!/^pydiggs-[^-]+-py3-none-any\.whl$/.test(pydiggsWheel)) throw new Error(`not a pure pydiggs wheel: ${pydiggsWheel}`);
    await copyFile(wheel.wheelPath, join(out, pydiggsWheel));
    files.push({ name: pydiggsWheel, sha256: sha256(await readFile(join(out, pydiggsWheel))), source: wheel.wheelPath });
    log(`copied ${pydiggsWheel}`);
  } else if (wheel) {
    const index = `https://pypi.org/pypi/pydiggs/${encodeURIComponent(wheel.version)}/json`;
    const response = await fetch(index);
    if (!response.ok) throw new Error(`${index}: HTTP ${response.status}`);
    const release = (await response.json()) as PypiRelease;
    const found = release.urls.find((u) => u.packagetype === "bdist_wheel" && u.filename.endsWith("-py3-none-any.whl"));
    if (!found) throw new Error(`pydiggs ${wheel.version} has no pure wheel on PyPI`);
    pydiggsWheel = safeName(found.filename, "the pydiggs wheel");
    const target = join(out, pydiggsWheel);
    if ((await existingHash(target)) !== found.digests.sha256) {
      const download = await fetch(found.url);
      if (!download.ok) throw new Error(`${found.url}: HTTP ${download.status}`);
      const data = new Uint8Array(await download.arrayBuffer());
      if (sha256(data) !== found.digests.sha256) throw new Error(`${pydiggsWheel}: sha256 does not match PyPI's`);
      await writeFile(target, data);
      log(`downloaded ${pydiggsWheel}`);
    }
    files.push({ name: pydiggsWheel, sha256: found.digests.sha256, source: found.url });
  }

  const result: VendorResult = { dir: out, pyodideVersion: expected.pyodideVersion, pydiggsWheel, files };
  await writeFile(join(out, MANIFEST), `${JSON.stringify({ ...result, dir: undefined }, null, 2)}\n`);
  const size = (await Promise.all(files.map((f) => stat(join(out, f.name))))).reduce((sum, s) => sum + s.size, 0);
  log(`${files.length} files, ${(size / 1024 / 1024).toFixed(1)} MiB, in ${out} (listed in ${MANIFEST})`);
  return result;
}
