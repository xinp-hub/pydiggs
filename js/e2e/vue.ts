// The Vue recipe from the README, as a test app: one shared worker, created in the
// browser on first use (never during server rendering, as in Nuxt), and a composable
// that components use. Render functions, so no Vue compiler is needed in the test.
/// <reference types="vite/client" />

import type { PydiggsBrowser } from "@pydiggs/client/browser";
import { createApp, defineComponent, h, onMounted, ref, shallowRef } from "vue";

let shared: Promise<PydiggsBrowser> | undefined;

/** The app's one PydiggsBrowser, created on first call. Browser only. */
function getPydiggs(): Promise<PydiggsBrowser> {
  shared ??= import("@pydiggs/client/browser").then(({ PydiggsBrowser }) => {
    const wheel = import.meta.env.VITE_PYDIGGS_WHEEL as string | undefined;
    const base = import.meta.env.VITE_PYODIDE_BASE as string | undefined;
    return new PydiggsBrowser({
      ...(wheel ? { pydiggsWheelUrl: new URL(wheel, location.href) } : {}),
      ...(base ? { pyodideBaseUrl: new URL(base, location.href) } : {}),
    });
  });
  return shared;
}

/** `{ pydiggs, status }`, filled in once mounted in the browser. */
function usePydiggs() {
  const pydiggs = shallowRef<PydiggsBrowser | null>(null);
  const status = ref<"loading" | "ready" | "failed">("loading");
  onMounted(async () => {
    const instance = await getPydiggs();
    pydiggs.value = instance;
    instance.ready.then(
      () => (status.value = "ready"),
      () => (status.value = "failed"),
    );
  });
  return { pydiggs, status };
}

const App = defineComponent({
  setup() {
    const { pydiggs, status } = usePydiggs();
    const result = ref({ status: "", verdict: "", findings: "", messages: [] as string[] });
    async function onChange(event: Event) {
      const file = (event.target as HTMLInputElement).files?.[0];
      if (!file || !pydiggs.value) return;
      result.value = { status: `working:${file.name}`, verdict: "", findings: "", messages: [] };
      const findings = await pydiggs.value.validate(file);
      result.value = {
        status: `done:${file.name}`,
        verdict: findings.files[0]?.verdict ?? "",
        findings: JSON.stringify(findings),
        messages: (findings.files[0]?.findings ?? []).map((f) => f.message),
      };
    }
    // Vue writes text, never HTML: untrusted messages stay text.
    return () =>
      h("main", [
        h("h1", "@pydiggs/client in Vue"),
        h("output", { id: "status" }, status.value),
        h("input", { id: "file", type: "file", accept: ".xml", disabled: !pydiggs.value, onChange }),
        h("output", { id: "result-status" }, result.value.status),
        h("output", { id: "verdict" }, result.value.verdict),
        h("ul", { id: "messages" }, result.value.messages.map((m) => h("li", m))),
        h("pre", { id: "findings", hidden: true }, result.value.findings),
      ]);
  },
});

createApp(App).mount("#app");
