// Stands in for node-worker.js in the PydiggsNode tests: answers the protocol at once,
// and echoes what it was sent, so the tests see what crossed the thread boundary.
import { parentPort } from "node:worker_threads";

let interrupt;

parentPort.on("message", (message) => {
  if (message.type === "init") {
    if (message.maxBytes === 13) process.exit(3); // a crash during loading
    if (message.interruptBuffer) interrupt = new Uint8Array(message.interruptBuffer);
    parentPort.postMessage({ type: "progress", stage: "runtime" });
    const { interruptBuffer, ...init } = message;
    parentPort.postMessage({
      type: "ready",
      versions: { pyodide: "p", pydiggs: "d", lxml: "l", init, interruptible: interruptBuffer instanceof SharedArrayBuffer },
    });
    return;
  }
  if (message.name === "crash.xml") process.exit(4); // a crash on a document
  if (message.name === "slow.xml") return; // a document that never finishes
  if (message.name === "stoppable.xml") {
    // Python under setInterruptBuffer: checks the buffer now and then, and stops on 2.
    const timer = setInterval(() => {
      if (interrupt && Atomics.load(interrupt, 0) === 2) {
        clearInterval(timer);
        Atomics.store(interrupt, 0, 0);
        parentPort.postMessage({ id: message.id, type: "error", message: "The request was cancelled.", problem: { code: "cancelled" } });
      }
    }, 5);
    return;
  }
  parentPort.postMessage({
    id: message.id,
    type: "result",
    document: { type: message.type, name: message.name, size: message.data?.byteLength, parameters: message.parameters },
  });
});
