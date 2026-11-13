import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { PYDIGGS_VERSION } from "../src/generated/pins.ts";

const script = fileURLToPath(new URL("../scripts/follow-pydiggs.mjs", import.meta.url));
const fakePypi = new URL("./fixtures/fake-pypi.mjs", import.meta.url).href;
const pin = JSON.parse(readFileSync(new URL("../pydiggs-wheel.json", import.meta.url), "utf8"));
// A pyDIGGS release PR bumps PYDIGGS_VERSION before that release is on PyPI; until this
// script pins it, the pin names the previous release and the steady-state tests do not apply.
const BETWEEN = pin.version === PYDIGGS_VERSION ? false : `pydiggs-wheel.json pins ${pin.version}, the worker targets ${PYDIGGS_VERSION}`;

function run(args: string[], answers: Record<string, unknown>) {
  const result = spawnSync(process.execPath, ["--import", fakePypi, script, ...args], {
    env: { ...process.env, FAKE_PYPI: JSON.stringify(answers) },
    encoding: "utf8",
  });
  return { status: result.status, out: result.stdout, err: result.stderr };
}

const release = (version: string, overrides: Record<string, unknown> = {}) => ({
  urls: [
    { packagetype: "sdist", filename: `pydiggs-${version}.tar.gz`, url: "https://files.pythonhosted.org/x.tar.gz", digests: { sha256: "0".repeat(64) } },
    {
      packagetype: "bdist_wheel",
      filename: `pydiggs-${version}-py3-none-any.whl`,
      // The pinned wheel for the pinned release; a well-formed PyPI URL for any other.
      url: version === pin.version ? pin.url : `https://files.pythonhosted.org/packages/00/00/0/pydiggs-${version}-py3-none-any.whl`,
      digests: { sha256: version === pin.version ? pin.sha256 : "1".repeat(64) },
      yanked: false,
      ...overrides,
    },
  ],
});

test("--check exits 0 when the pinned wheel is the latest release, 3 when PyPI is ahead", { skip: BETWEEN }, () => {
  const current = run(["--check"], { "https://pypi.org/pypi/pydiggs/json": { info: { version: pin.version } } });
  assert.equal(current.status, 0, current.err);
  assert.deepEqual(JSON.parse(current.out), {
    pypi_latest: pin.version,
    client_targets: pin.version,
    wheel_pinned: pin.version,
    lags: false,
  });
  const behind = run(["--check"], { "https://pypi.org/pypi/pydiggs/json": { info: { version: "99.0.0" } } });
  assert.equal(behind.status, 3);
  assert.equal(JSON.parse(behind.out).lags, true);
});

test("pinning the release already pinned changes nothing", { skip: BETWEEN }, () => {
  const result = run([], { [`https://pypi.org/pypi/pydiggs/${pin.version}/json`]: release(pin.version) });
  assert.equal(result.status, 0, result.err);
  assert.match(result.out, /already pins pydiggs/);
});

test("a wheel that is not on files.pythonhosted.org, or is yanked, is refused", () => {
  const elsewhere = run([], {
    [`https://pypi.org/pypi/pydiggs/${PYDIGGS_VERSION}/json`]: release(PYDIGGS_VERSION, {
      url: `https://evil.test/pydiggs-${PYDIGGS_VERSION}-py3-none-any.whl`,
    }),
  });
  assert.notEqual(elsewhere.status, 0);
  assert.match(elsewhere.err, /unexpected wheel URL/);
  const yanked = run([], { [`https://pypi.org/pypi/pydiggs/${PYDIGGS_VERSION}/json`]: release(PYDIGGS_VERSION, { yanked: true }) });
  assert.notEqual(yanked.status, 0);
  assert.match(yanked.err, /yanked/);
  assert.deepEqual(JSON.parse(readFileSync(new URL("../pydiggs-wheel.json", import.meta.url), "utf8")), pin, "nothing was written");
});
