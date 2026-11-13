#!/usr/bin/env node
// Keeps @pydiggs/client on the latest pyDIGGS release. A pyDIGGS release PR bumps
// PYDIGGS_VERSION in examples/browser-worker/worker.mjs (a test holds it to
// pydiggs.__version__); once that release is on PyPI, this script pins its wheel:
//
//   node scripts/follow-pydiggs.mjs           write pydiggs-wheel.json from PyPI's record
//                                             of PYDIGGS_VERSION, and regenerate the pins
//   node scripts/follow-pydiggs.mjs --check   report whether the client lags PyPI; exit 0
//                                             when current, 3 when it lags (for CI)
//
// The sha256 comes from PyPI's JSON API for that exact release. The wheel URL must be on
// files.pythonhosted.org and name a pure wheel, or nothing is written. Releasing the
// client afterwards (a version bump, a changelog entry, the signed tag) stays a human
// step, as for every release.

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const JS_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WORKER = join(JS_ROOT, "..", "examples", "browser-worker", "worker.mjs");
const PIN = join(JS_ROOT, "pydiggs-wheel.json");

const { values: args } = parseArgs({ options: { check: { type: "boolean", default: false } } });

const workerVersion = /^const PYDIGGS_VERSION = "([0-9A-Za-z.+-]+)";/m.exec(readFileSync(WORKER, "utf8"))?.[1];
if (!workerVersion) throw new Error(`${WORKER}: cannot find const PYDIGGS_VERSION`);

async function json(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

// PyPI's record of one release: its pure wheel and that wheel's sha256.
async function wheelOf(version) {
  const release = await json(`https://pypi.org/pypi/pydiggs/${encodeURIComponent(version)}/json`);
  const filename = `pydiggs-${version}-py3-none-any.whl`;
  const file = release.urls.find((u) => u.packagetype === "bdist_wheel" && u.filename === filename);
  if (!file) throw new Error(`pydiggs ${version} has no ${filename} on PyPI`);
  const url = new URL(file.url);
  if (url.protocol !== "https:" || url.hostname !== "files.pythonhosted.org" || !url.pathname.endsWith(`/${filename}`)) {
    throw new Error(`unexpected wheel URL for pydiggs ${version}: ${file.url}`);
  }
  if (!/^[0-9a-f]{64}$/.test(file.digests.sha256)) throw new Error(`malformed sha256 for ${filename}`);
  if (file.yanked) throw new Error(`pydiggs ${version} is yanked on PyPI`);
  return { version, filename, url: file.url, sha256: file.digests.sha256 };
}

let pinned;
try {
  pinned = JSON.parse(readFileSync(PIN, "utf8"));
} catch {
  pinned = undefined;
}

if (args.check) {
  const latest = (await json("https://pypi.org/pypi/pydiggs/json")).info.version;
  const status = {
    pypi_latest: latest,
    client_targets: workerVersion,
    wheel_pinned: pinned?.version ?? null,
    lags: latest !== workerVersion || pinned?.version !== workerVersion,
  };
  console.log(JSON.stringify(status));
  process.exit(status.lags ? 3 : 0);
}

const wheel = await wheelOf(workerVersion);
if (pinned && pinned.version === wheel.version && pinned.sha256 === wheel.sha256 && pinned.url === wheel.url) {
  console.log(`pydiggs-wheel.json already pins pydiggs ${wheel.version}`);
} else {
  writeFileSync(PIN, `${JSON.stringify(wheel, null, 2)}\n`);
  console.log(`pinned pydiggs ${wheel.version}: ${wheel.sha256}`);
}
execFileSync(process.execPath, [join(JS_ROOT, "scripts", "generate.mjs")], { stdio: "inherit" });
