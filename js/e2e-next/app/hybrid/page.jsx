"use client";
// PydiggsHybrid with only a server: every document goes to this app's own route handler
// (reason "no-browser"), so the test exercises the handler from a page, through the
// package's HTTP client.

import { useState } from "react";

export default function HybridPage() {
  const [result, setResult] = useState({ status: "", via: "", reason: "", findings: null });

  async function onChange(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setResult({ status: `working:${file.name}`, via: "", reason: "", findings: null });
    const [{ PydiggsHybrid }, { PydiggsHttpClient }] = await Promise.all([
      import("@pydiggs/client/hybrid"),
      import("@pydiggs/client/http"),
    ]);
    const hybrid = new PydiggsHybrid({
      server: new PydiggsHttpClient({ baseUrl: "/api/pydiggs" }),
    });
    try {
      const { document, via, reason } = await hybrid.validate(file);
      setResult({ status: `done:${file.name}`, via, reason, findings: document });
    } catch (e) {
      setResult({ status: `error:${file.name}`, via: "", reason: String(e?.message ?? e), findings: null });
    }
  }

  return (
    <main>
      <h1>Hybrid, server route</h1>
      <input id="file" type="file" accept=".xml" onChange={onChange} />
      <p>
        <output id="result-status">{result.status}</output> <output id="via">{result.via}</output>{" "}
        <output id="reason">{result.reason}</output>
      </p>
      <pre id="findings" hidden>
        {result.findings ? JSON.stringify(result.findings) : ""}
      </pre>
    </main>
  );
}
