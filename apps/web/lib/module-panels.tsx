"use client";

import { useEffect, useState, type ComponentType } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { rawFetch } from "@/lib/api";
import { CORE_EXTERNALS, installModuleRuntime, scanBundleExternals } from "@/lib/module-runtime";

export interface ModulePanelProps {
  logId: string;
  moduleId: string;
}

/**
 * Dynamic per-module panel loader (§5.4).
 *
 * Each module ships a CJS bundle at `modules/<id>/.dist/panel.js`, produced
 * by `apps/web/scripts/bundle-modules.mjs` with every runtime dependency
 * marked external. The bundle is served by FastAPI at
 * `/api/v1/modules/{id}/assets/panel.js`. We fetch the text, execute it via
 * `new Function(module, exports, require, source)`, and intercept each
 * `require()` to read from `window.__FF_RUNTIME__` so React, shadcn, etc.
 * resolve to the host's single instances.
 *
 * Panel discovery: prefer `module.exports.Panel`, then `.default`, then the
 * first React-component-looking named export. Authors should add
 * `export default Panel` or `export const Panel = ...` going forward – the
 * fallback exists to keep current `DiscoveryPanel` / `PerformancePanel` /
 * `Cv4cddPanel` / `ComplexityPanel` named exports working.
 */

type AnyComponent = ComponentType<ModulePanelProps>;

declare global {
  interface Window {
    __FF_RUNTIME__?: Record<string, unknown>;
  }
}

const _panelCache = new Map<string, AnyComponent>();
const _inflight = new Map<string, Promise<AnyComponent>>();

function ffRequire(specifier: string): unknown {
  const runtime = (typeof window !== "undefined" ? window.__FF_RUNTIME__ : undefined) ?? {};
  const entry = runtime[specifier];
  if (entry === undefined) {
    throw new Error(
      `Module panel required "${specifier}" which is not in the runtime. ` +
        `Add it to apps/web/lib/runtime-externals.json and lib/module-runtime.ts.`,
    );
  }
  return entry;
}

function pickPanel(exports: Record<string, unknown>): AnyComponent | null {
  const named = exports.Panel;
  if (typeof named === "function") return named as AnyComponent;
  const def = exports.default;
  if (typeof def === "function") return def as AnyComponent;
  // Fallback: first export whose name ends in "Panel".
  for (const [key, val] of Object.entries(exports)) {
    if (key === "__esModule") continue;
    if (key.endsWith("Panel") && typeof val === "function") return val as AnyComponent;
  }
  // Last resort: first function export.
  for (const [key, val] of Object.entries(exports)) {
    if (key === "__esModule") continue;
    if (typeof val === "function") return val as AnyComponent;
  }
  return null;
}

async function loadPanel(moduleId: string): Promise<AnyComponent> {
  const cached = _panelCache.get(moduleId);
  if (cached) return cached;
  const pending = _inflight.get(moduleId);
  if (pending) return pending;

  const promise = (async () => {
    // The core externals are needed by every bundle, so start them now and let
    // them stream in parallel with the bundle fetch instead of gating it. The
    // rest is installed from the bundle's own require() calls below - the two
    // used to be one eager all-externals install ahead of the fetch, which
    // both serialised the two big transfers and pulled in ~650 KB gzip of
    // dependencies the panel never touches.
    const core = installModuleRuntime(CORE_EXTERNALS);
    const res = await rawFetch(
      `/api/v1/modules/${encodeURIComponent(moduleId)}/assets/panel.js`,
    );
    if (!res.ok) {
      throw new Error(`Failed to load module panel ${moduleId} (HTTP ${res.status}).`);
    }
    const source = await res.text();
    // Runtime must be complete before the bundle's require() shim fires.
    await Promise.all([core, installModuleRuntime(scanBundleExternals(source))]);
    const moduleObj: { exports: Record<string, unknown> } = { exports: {} };
    // Module bundles are CJS. We treat the source as the body of an IIFE so
    // top-level `var ...` doesn't leak into globals.
    const factory = new Function(
      "module",
      "exports",
      "require",
      `${source}\n;return module.exports;`,
    );
    const exportsOut = factory(moduleObj, moduleObj.exports, ffRequire) as Record<string, unknown>;
    const Panel = pickPanel(exportsOut);
    if (!Panel) {
      throw new Error(
        `Module ${moduleId} did not export a panel component (expected default, "Panel", or a *Panel named export).`,
      );
    }
    _panelCache.set(moduleId, Panel);
    return Panel;
  })();
  _inflight.set(moduleId, promise);
  try {
    return await promise;
  } finally {
    _inflight.delete(moduleId);
  }
}

/**
 * Fire-and-forget warm-up for a panel bundle (hover/focus intent on module
 * links). Dedup comes free from `_panelCache`/`_inflight`; failures are
 * swallowed – the real navigation retries and surfaces the error UI.
 */
export function prefetchModulePanel(moduleId: string): void {
  if (typeof window === "undefined") return;
  void loadPanel(moduleId).catch(() => {});
}

/** Async React component that resolves the dynamic bundle on mount. */
function DynamicModulePanel({ logId, moduleId }: ModulePanelProps) {
  const [Panel, setPanel] = useState<AnyComponent | null>(() => _panelCache.get(moduleId) ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (Panel) return;
    let cancelled = false;
    loadPanel(moduleId).then(
      (P) => {
        if (!cancelled) setPanel(() => P);
      },
      (err: Error) => {
        if (!cancelled) setError(err.message);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [moduleId, Panel]);

  if (error) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
        Failed to load module panel: {error}
      </div>
    );
  }
  if (!Panel) return <PanelSkeleton />;
  return <Panel logId={logId} moduleId={moduleId} />;
}

export function getModulePanel(
  _moduleId: string,
  options: { hasFrontend?: boolean } = {},
): AnyComponent | null {
  // Modules without a `frontend.panel` in their manifest have no bundle on
  // disk; returning null lets callers render their own placeholder rather
  // than triggering a 404 fetch on every paint. The dynamic loader is the
  // same wrapper for every module that does declare a frontend.
  if (options.hasFrontend === false) return null;
  return DynamicModulePanel;
}

// The most-seen skeleton in the app: shown while the runtime installs on the
// first panel visit AND while any panel bundle loads. Shaped like a typical
// module panel (title, toolbar/tab strip, canvas, stat row) to minimise the
// layout jump when the real panel lands.
function PanelSkeleton() {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Skeleton className="h-8 w-72 max-w-full" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-24 rounded-md" />
        ))}
      </div>
      <Skeleton className="h-[28rem] w-full rounded-xl" />
      <div className="grid gap-4 sm:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}
