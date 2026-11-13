"use client";
// The React recipe from the README, as this test app uses it.
//
// One worker for the whole app, created on first use in the browser and never during
// server rendering: the package is imported inside the effect, so the server never
// loads it, and the worker outlives any one component.

import { useEffect, useState } from "react";

let shared;

/** The app's one PydiggsBrowser, created on first call. Browser only. */
export function getPydiggs() {
  shared ??= import("@pydiggs/client/browser").then(({ PydiggsBrowser }) => {
    const wheel = process.env.NEXT_PUBLIC_PYDIGGS_WHEEL;
    return new PydiggsBrowser({
      ...(wheel ? { pydiggsWheelUrl: new URL(wheel, location.href) } : {}),
      onProgress: (stage) => listeners.forEach((listener) => listener({ stage })),
    });
  });
  return shared;
}

const listeners = new Set();

/** `{ pydiggs, status, stages }`: re-renders as the worker loads. */
export function usePydiggs() {
  const [state, setState] = useState({ pydiggs: null, status: "loading", stages: [] });
  useEffect(() => {
    let alive = true;
    const onProgress = ({ stage }) => alive && setState((s) => ({ ...s, stages: [...s.stages, stage] }));
    listeners.add(onProgress);
    getPydiggs().then((pydiggs) => {
      if (!alive) return;
      setState((s) => ({ ...s, pydiggs, status: pydiggs.status }));
      pydiggs.ready.then(
        () => alive && setState((s) => ({ ...s, status: "ready" })),
        (error) => alive && setState((s) => ({ ...s, status: "failed", error: String(error?.message ?? error) })),
      );
    });
    return () => {
      alive = false;
      listeners.delete(onProgress);
    };
  }, []);
  return state;
}
