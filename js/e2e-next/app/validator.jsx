"use client";

import { useState } from "react";

import { usePydiggs } from "../lib/pydiggs.js";

export default function Validator() {
  const { pydiggs, status, stages, error } = usePydiggs();
  const [result, setResult] = useState({ status: "", findings: null, model: null, messages: [] });

  async function onChange(event) {
    const file = event.target.files?.[0];
    if (!file || !pydiggs) return;
    setResult({ status: `working:${file.name}`, findings: null, model: null, messages: [] });
    try {
      const findings = await pydiggs.validate(file);
      const model = await pydiggs.read(file);
      setResult({
        status: `done:${file.name}`,
        findings,
        model,
        messages: (findings.files[0]?.findings ?? []).map((f) => f.message),
      });
    } catch (e) {
      setResult({ status: `error:${file.name}`, findings: null, model: null, messages: [String(e?.message ?? e)] });
    }
  }

  return (
    <main>
      <h1>@pydiggs/client in Next.js</h1>
      <p>
        Worker: <output id="status">{status}</output> <output id="stages">{stages.join(",")}</output>
      </p>
      <label>
        DIGGS file <input id="file" type="file" accept=".xml" onChange={onChange} disabled={!pydiggs} />
      </label>
      <p>
        Result: <output id="result-status">{result.status}</output>
      </p>
      {/* React writes text, never HTML: untrusted messages stay text. */}
      <ul id="messages">
        {result.messages.map((message, i) => (
          <li key={i}>{message}</li>
        ))}
      </ul>
      <pre id="findings" hidden>
        {result.findings ? JSON.stringify(result.findings) : ""}
      </pre>
      <pre id="model" hidden>
        {result.model ? JSON.stringify(result.model) : ""}
      </pre>
      <p>
        <output id="error">{error ?? ""}</output>
      </p>
    </main>
  );
}
