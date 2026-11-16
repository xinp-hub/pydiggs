// The DIGGS XML a conversion produced, from its result document. A conversion result
// (`convert` on any engine, or POST /v1/convert) carries the output as base64 with its
// sha256, so the bytes can be checked wherever they arrive.

import { PydiggsError } from "./errors.ts";
import type { ConversionResult } from "./generated/http.ts";

type FromBase64 = { fromBase64?: (text: string) => Uint8Array };

function decode(text: string): Uint8Array {
  // Uint8Array.fromBase64 (ES2026) where the runtime has it; atob otherwise.
  const native = (Uint8Array as unknown as FromBase64).fromBase64;
  if (native) return native(text);
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const hex = (buffer: ArrayBuffer): string =>
  [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");

/**
 * The output of a conversion as bytes (the DIGGS XML), checked against the result's
 * `output_sha256`. Save it as `result.output_name`, or wrap it in a `Blob` with
 * `result.media_type` to offer it for download.
 *
 * Throws a `PydiggsError` if the bytes do not match their sha256.
 */
export async function conversionOutput(result: ConversionResult): Promise<Uint8Array<ArrayBuffer>> {
  const bytes = decode(result.output_base64);
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  if (hex(await crypto.subtle.digest("SHA-256", copy)) !== result.output_sha256) {
    throw new PydiggsError("the converted document does not match its sha256");
  }
  return copy;
}
