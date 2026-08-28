"use client";

import { useEffect, useState, type ComponentType } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { rawFetch } from "@/lib/api";
import type { DrillHandler } from "@/lib/dashboards/drill";
import { CORE_EXTERNALS, installModuleRuntime, scanBundleExternals } from "@/lib/module-runtime";

/**
 * Cross-module widget loader (§7.7).
 *
 * Any module can expose reusable widgets via its manifest:
 *
 *   frontend:
 *     widgets:
 *       - id: throughput-chart
 *         entry: ./widgets/Throughput.tsx
 *
 * The bundler in `apps/web/scripts/bundle-modules.mjs` writes each widget
 * to `modules/<id>/.dist/widget-<widget_id>.js` as a CJS module; FastAPI
 * serves it at `/api/v1/modules/<id>/assets/widget-<widget_id>.js`.
 *
 * `useWidget("source-id", "widget-id")` resolves the bundle, picks its
 * default / `Widget` named export, and returns it. While loading or if the
 * source module isn't installed, a Skeleton placeholder is rendered.
 *
 * Usage from a consumer module's panel:
 *
 *   const Throughput = useWidget("performance", "throughput-chart");
 *   return <Throughput logId={logId} />;
 */

export interface WidgetProps {
  logId?: string;
  moduleId?: string;
  config?: Record<string, unknown>;
  /**
   * Navigate from a clicked mark to the page that explains it.
   *
   * Supplied by the dashboard; **undefined when the widget is embedded
   * elsewhere** (a panel using `useWidget`), so always call it optionally:
   *
   *   <KpiTile … onClick={() => onDrill?.({ params: { activity } })} />
   *
   * Calling it with no argument opens this card's own module for the bound
   * log. See `lib/dashboards/drill.ts` for the standard parameter names —
   * prefer those over inventing one, since cross-module links only work when
   * both sides agree (`discovery` → `performance` via `?activity=`).
   */
  onDrill?: DrillHandler;
  [key: string]: unknown;
}

type WidgetComponent = ComponentType<WidgetProps>;

const _cache = new Map<string, WidgetComponent>();
const _inflight = new Map<string, Promise<WidgetComponent>>();

function ffRequire(specifier: string): unknown {
  const runtime = (typeof window !== "undefined" ? window.__FF_RUNTIME__ : undefined) ?? {};
  const entry = runtime[specifier];
  if (entry === undefined) {
    throw new Error(`Widget required "${specifier}" which is not in the runtime.`);
  }
  return entry;
}

function pickWidget(exports: Record<string, unknown>): WidgetComponent | null {
  for (const key of ["Widget", "default"]) {
    const v = exports[key];
    if (typeof v === "function") return v as WidgetComponent;
  }
  for (const [key, val] of Object.entries(exports)) {
    if (key === "__esModule") continue;
    if (typeof val === "function") return val as WidgetComponent;
  }
  return null;
}

async function loadWidget(moduleId: string, widgetId: string): Promise<WidgetComponent> {
  const cacheKey = `${moduleId}::${widgetId}`;
  const cached = _cache.get(cacheKey);
  if (cached) return cached;
  const pending = _inflight.get(cacheKey);
  if (pending) return pending;

  const promise = (async () => {
    // Core externals stream in parallel with the bundle; the rest comes from
    // the bundle's own require() calls (see module-panels.tsx). A dashboard
    // mounts many widgets at once, so the per-specifier dedupe in
    // installModuleRuntime() is what keeps that to one import each.
    const core = installModuleRuntime(CORE_EXTERNALS);
    const url = `/api/v1/modules/${encodeURIComponent(moduleId)}/assets/widget-${encodeURIComponent(widgetId)}.js`;
    const res = await rawFetch(url);
    if (!res.ok) {
      throw new Error(
        `Failed to load widget ${moduleId}/${widgetId} (HTTP ${res.status}). ` +
          "Is the source module installed and is the widget declared in its manifest.frontend.widgets?",
      );
    }
    const source = await res.text();
    await Promise.all([core, installModuleRuntime(scanBundleExternals(source))]);
    const moduleObj: { exports: Record<string, unknown> } = { exports: {} };
    const factory = new Function(
      "module",
      "exports",
      "require",
      `${source}\n;return module.exports;`,
    );
    const exportsOut = factory(moduleObj, moduleObj.exports, ffRequire) as Record<string, unknown>;
    const Widget = pickWidget(exportsOut);
    if (!Widget) {
      throw new Error(`Widget ${moduleId}/${widgetId} did not export a component.`);
    }
    _cache.set(cacheKey, Widget);
    return Widget;
  })();
  _inflight.set(cacheKey, promise);
  try {
    return await promise;
  } finally {
    _inflight.delete(cacheKey);
  }
}

/**
 * Resolve a widget by `(sourceModuleId, widgetId)`. Returns a component
 * that's safe to render immediately – it shows a Skeleton while the bundle
 * streams in, then renders the resolved widget; if the source module is
 * missing or the fetch fails, renders an inline placeholder.
 */
export function useWidget(sourceModuleId: string, widgetId: string): WidgetComponent {
  const [resolved, setResolved] = useState<WidgetComponent | null>(
    () => _cache.get(`${sourceModuleId}::${widgetId}`) ?? null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (resolved) return;
    let cancelled = false;
    loadWidget(sourceModuleId, widgetId).then(
      (W) => {
        if (!cancelled) setResolved(() => W);
      },
      (err: Error) => {
        if (!cancelled) setError(err.message);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [sourceModuleId, widgetId, resolved]);

  return function ResolvedWidget(props: WidgetProps) {
    if (error) {
      return (
        <div className="rounded-md border border-dashed border-border bg-card/40 px-3 py-4 text-xs text-muted-foreground">
          Widget unavailable: {error}
        </div>
      );
    }
    if (!resolved) {
      return <Skeleton className="h-32 w-full" />;
    }
    const Resolved = resolved;
    return <Resolved {...props} />;
  };
}
