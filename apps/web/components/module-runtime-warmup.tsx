"use client";

import { useEffect } from "react";

import { CORE_EXTERNALS, installModuleRuntime } from "@/lib/module-runtime";

/**
 * Warms the module runtime behind `window.__FF_RUNTIME__` during browser idle
 * time after the platform shell mounts, so the first module panel a user opens
 * doesn't pay that install on click. `installModuleRuntime()` dedupes per
 * specifier – a panel opened before the idle callback fires simply awaits the
 * same in-flight imports.
 *
 * Only the CORE set: warming every external would spend idle bandwidth on
 * recharts/xyflow/radix for a user who never opens a panel that needs them,
 * and the panel loader tops up from the bundle's own require() calls anyway.
 */
export function ModuleRuntimeWarmup() {
  useEffect(() => {
    const warm = () => void installModuleRuntime(CORE_EXTERNALS);
    // Safari still lacks requestIdleCallback – fall back to a short timeout.
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(warm, { timeout: 5000 });
      return () => window.cancelIdleCallback(id);
    }
    const id = window.setTimeout(warm, 2500);
    return () => window.clearTimeout(id);
  }, []);
  return null;
}
