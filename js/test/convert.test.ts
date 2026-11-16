import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

import { conversionOutput } from "../src/convert.ts";
import { PydiggsError } from "../src/errors.ts";
import type { ConversionResult } from "../src/generated/http.ts";

const xml = new TextEncoder().encode('<?xml version="1.0"?><Diggs/>');
const result = (overrides: Partial<ConversionResult> = {}): ConversionResult =>
  ({
    output_base64: Buffer.from(xml).toString("base64"),
    output_sha256: createHash("sha256").update(xml).digest("hex"),
    ...overrides,
  }) as ConversionResult;

test("conversionOutput decodes the DIGGS XML and checks it against its sha256", async () => {
  assert.deepEqual(await conversionOutput(result()), xml);
});

test("an output that does not match its sha256 is refused", async () => {
  await assert.rejects(conversionOutput(result({ output_sha256: "0".repeat(64) })), (error: unknown) => {
    assert.ok(error instanceof PydiggsError);
    return true;
  });
});

test("the atob fallback gives the same bytes as Uint8Array.fromBase64", async () => {
  const native = (Uint8Array as unknown as { fromBase64?: unknown }).fromBase64;
  (Uint8Array as unknown as { fromBase64?: unknown }).fromBase64 = undefined;
  try {
    assert.deepEqual(await conversionOutput(result()), xml);
  } finally {
    (Uint8Array as unknown as { fromBase64?: unknown }).fromBase64 = native;
  }
});
