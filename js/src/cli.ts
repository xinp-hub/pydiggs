#!/usr/bin/env node
// pydiggs-client: the command-line side of @pydiggs/client.
//
//   npx pydiggs-client vendor <dir>                  Pyodide, its packages and the pydiggs
//                                                    wheel, for self-hosting in a browser
//   npx pydiggs-client vendor <dir> --packages-only  only what Node needs beyond the npm
//                                                    pyodide package: a ready packageCacheDir
//
// Options for vendor:
//   --pydiggs-version X.Y.Z   the pydiggs release to bring (default: the pinned one)
//   --pydiggs-wheel PATH      a pydiggs wheel on disk instead of a release from PyPI
//   --no-pydiggs              no pydiggs wheel
//   --pyodide-dir PATH        where the npm pyodide package is, if not found from here

import { relative } from "node:path";
import { parseArgs } from "node:util";

import { vendor, type VendorOptions } from "./vendor.ts";

const USAGE = `usage: pydiggs-client vendor <dir> [--packages-only] [--pydiggs-version X.Y.Z | --pydiggs-wheel PATH | --no-pydiggs] [--pyodide-dir PATH]`;

async function main(argv: string[]): Promise<number> {
  const { positionals, values } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      "packages-only": { type: "boolean", default: false },
      "pydiggs-version": { type: "string" },
      "pydiggs-wheel": { type: "string" },
      "no-pydiggs": { type: "boolean", default: false },
      "pyodide-dir": { type: "string" },
      help: { type: "boolean", short: "h", default: false },
    },
  });
  const [command, dir] = positionals;
  if (values.help || command !== "vendor" || !dir || positionals.length !== 2) {
    console.error(USAGE);
    return values.help ? 0 : 2;
  }
  const options: VendorOptions = {
    outDir: dir,
    packagesOnly: values["packages-only"],
    log: (line) => console.log(line),
  };
  if (values["pyodide-dir"]) options.pyodideDir = values["pyodide-dir"];
  if (values["no-pydiggs"]) options.pydiggs = false;
  else if (values["pydiggs-wheel"]) options.pydiggs = { wheelPath: values["pydiggs-wheel"] };
  else if (values["pydiggs-version"]) options.pydiggs = { version: values["pydiggs-version"] };

  const result = await vendor(options);
  const shown = relative(process.cwd(), result.dir) || ".";
  console.log("");
  if (!values["packages-only"]) {
    console.log("In a browser, serve the folder and point the worker at it:");
    console.log(`  new PydiggsBrowser({ pyodideBaseUrl: "/${shown}/"${result.pydiggsWheel ? `, pydiggsWheelUrl: "/${shown}/${result.pydiggsWheel}"` : ""} })`);
  }
  console.log("In Node, a cold start downloads nothing with:");
  console.log(`  new PydiggsNode({ packageCacheDir: "${shown}"${result.pydiggsWheel ? `, pydiggsWheelPath: "${shown}/${result.pydiggsWheel}"` : ""} })`);
  return 0;
}

main(process.argv.slice(2)).then(
  (code) => {
    process.exitCode = code;
  },
  (error: unknown) => {
    console.error(`pydiggs-client: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  },
);
