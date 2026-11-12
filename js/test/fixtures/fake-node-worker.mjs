// Stands in for node-worker.js in the PydiggsNode tests: answers the protocol at once,
// and echoes what it was sent, so the tests see what crossed the thread boundary.
import { parentPort } from "node:worker_threads";

parentPort.on("message", (message) => {
  if (message.type === "init") {
    if (message.maxBytes === 13) process.exit(3); // a crash during loading
    parentPort.postMessage({ type: "progress", stage: "runtime" });
    parentPort.postMessage({ type: "ready", versions: { pyodide: "p", pydiggs: "d", lxml: "l", init: message } });
    return;
  }
  if (message.name === "crash.xml") process.exit(4); // a crash on a document
  if (message.name === "slow.xml") return; // a document that never finishes
  parentPort.postMessage({
    id: message.id,
    type: "result",
    document: { type: message.type, name: message.name, size: message.data.byteLength, results: message.results },
  });
});
