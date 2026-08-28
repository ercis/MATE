/**
 * Shared runtime exposed to dynamically-loaded module bundles (§5.4).
 *
 * Module bundles are esbuild-compiled per-module into `modules/<folder>/.dist/`
 * with every entry below marked as **external**. At runtime, each external
 * import resolves to a tiny shim that reads from `window.__FF_RUNTIME__[path]`.
 *
 * **Lazy by design, twice over.**
 *
 *  1. Every package below is pulled in via a dynamic import inside a loader
 *     thunk, never at module-eval time. Otherwise the Providers chain on every
 *     page (landing, settings, …) would eagerly load xyflow, recharts,
 *     radix-ui, etc., causing both bundle bloat *and* SSR ↔ client hydration
 *     mismatches when any of those packages run side effects (style injection,
 *     portal mounting, …) at import time.
 *  2. `installModuleRuntime(specifiers)` loads **only what the caller asks
 *     for**. The panel/widget loaders scan the fetched bundle for its
 *     `require("…")` calls and pass exactly that set, so opening one panel no
 *     longer downloads every other panel's dependencies. Measured on the
 *     discovery panel: the full set is ~878 KB gzip, of which recharts
 *     (150 KB), radix-ui (74 KB) and elkjs (429 KB, see below) are dead weight.
 *
 * Add a new external: list it in `runtime-externals.json`, add a loader entry
 * below, and the bundler picks it up on next install.
 */

import runtimeExternals from "@/lib/runtime-externals.json";

declare global {
  interface Window {
    __FF_RUNTIME__?: Record<string, unknown>;
  }
}

/**
 * Deferred elkjs.
 *
 * `elk.bundled.js` is 429 KB gzip - by far the largest single external - and
 * only three canvases ever touch it (discovery Petri + Heuristics, and
 * ocel_discovery). Those all construct it at module scope (`const elk = new
 * ELK()` in `layout/layered.ts`) and then only ever call `.layout()`, so the
 * bundle itself can be deferred behind a stand-in whose construction is free
 * and whose first real call pays the import.
 *
 * If a module ever needs an elkjs API beyond the four below it will fail
 * loudly here rather than silently mislayout - add the method and forward it.
 */
type ElkLike = {
  layout: (...args: unknown[]) => Promise<unknown>;
  knownLayoutAlgorithms: () => Promise<unknown>;
  knownLayoutOptions: () => Promise<unknown>;
  knownLayoutCategories: () => Promise<unknown>;
};

function makeLazyElk(): unknown {
  class LazyElk {
    #real: Promise<ElkLike>;

    constructor(options?: unknown) {
      this.#real = import("elkjs/lib/elk.bundled.js").then((m) => {
        const Ctor = (m as unknown as { default: new (o?: unknown) => ElkLike }).default;
        return new Ctor(options);
      });
    }

    layout(...args: unknown[]): Promise<unknown> {
      return this.#real.then((e) => e.layout(...args));
    }
    knownLayoutAlgorithms(): Promise<unknown> {
      return this.#real.then((e) => e.knownLayoutAlgorithms());
    }
    knownLayoutOptions(): Promise<unknown> {
      return this.#real.then((e) => e.knownLayoutOptions());
    }
    knownLayoutCategories(): Promise<unknown> {
      return this.#real.then((e) => e.knownLayoutCategories());
    }
  }
  // Shaped like the ESM namespace esbuild's CJS output reads (`x.default`).
  return { __esModule: true, default: LazyElk };
}

/** One dynamic import per external. Key set must match `runtime-externals.json`. */
const LOADERS: Record<string, () => Promise<unknown>> = {
  "react": () => import("react"),
  "react/jsx-runtime": () => import("react/jsx-runtime"),
  "react-dom": () => import("react-dom"),
  "@tanstack/react-query": () => import("@tanstack/react-query"),
  "@xyflow/react": () => import("@xyflow/react"),
  "lucide-react": () => import("lucide-react"),
  "recharts": () => import("recharts"),
  "sonner": () => import("sonner"),
  "radix-ui": () => import("radix-ui"),
  "d3-hierarchy": () => import("d3-hierarchy"),
  "elkjs/lib/elk.bundled.js": () => Promise.resolve(makeLazyElk()),
  "next/navigation": () => import("next/navigation"),
  // Real anchors for module-internal entity links (variantHref/activityHref):
  // router.push suppresses route loading.tsx fallbacks, <Link> doesn't.
  "next/link": () => import("next/link"),
  "@/components/ui/alert-dialog": () => import("@/components/ui/alert-dialog"),
  "@/components/ui/badge": () => import("@/components/ui/badge"),
  "@/components/ui/button": () => import("@/components/ui/button"),
  "@/components/ui/card": () => import("@/components/ui/card"),
  "@/components/ui/label": () => import("@/components/ui/label"),
  "@/components/ui/radio-group": () => import("@/components/ui/radio-group"),
  "@/components/ui/scroll-area": () => import("@/components/ui/scroll-area"),
  "@/components/ui/select": () => import("@/components/ui/select"),
  "@/components/ui/separator": () => import("@/components/ui/separator"),
  "@/components/ui/skeleton": () => import("@/components/ui/skeleton"),
  "@/components/ui/slider": () => import("@/components/ui/slider"),
  "@/components/ui/switch": () => import("@/components/ui/switch"),
  "@/components/ui/table": () => import("@/components/ui/table"),
  "@/components/ui/tabs": () => import("@/components/ui/tabs"),
  "@/components/ui/tooltip": () => import("@/components/ui/tooltip"),
  "@/components/empty-state": () => import("@/components/empty-state"),
  "@/components/ai/ai-guidance-card": () => import("@/components/ai/ai-guidance-card"),
  "@/components/dashboards/kit": () => import("@/components/dashboards/kit"),
  // The platform's own column-filter bar + time-range slider. Exposed so a panel
  // that needs MORE than the one log-scoped filter the shell provides (process
  // comparison: an independent filter per compared side) reuses the exact same
  // controls instead of re-implementing the filter vocabulary.
  "@/components/dashboards/dashboard-filter-bar": () =>
    import("@/components/dashboards/dashboard-filter-bar"),
  "@/components/dashboards/dashboard-time-range": () =>
    import("@/components/dashboards/dashboard-time-range"),
  "@/components/visualizations/canvases/shared/canvas-shell": () =>
    import("@/components/visualizations/canvases/shared/canvas-shell"),
  "@/components/visualizations/canvases/shared/canvas-controls": () =>
    import("@/components/visualizations/canvases/shared/canvas-controls"),
  "@/components/visualizations/canvases/shared/canvas-toolbar": () =>
    import("@/components/visualizations/canvases/shared/canvas-toolbar"),
  "@/components/visualizations/canvases/shared/canvas-skeleton": () =>
    import("@/components/visualizations/canvases/shared/canvas-skeleton"),
  "@/lib/api": () => import("@/lib/api"),
  "@/lib/cn": () => import("@/lib/cn"),
  // Column specs + time bounds for the filter controls above.
  "@/lib/dashboard-queries": () => import("@/lib/dashboard-queries"),
  "@/lib/dashboards/card-scope": () => import("@/lib/dashboards/card-scope"),
  "@/lib/dashboards/drill": () => import("@/lib/dashboards/drill"),
  "@/lib/format": () => import("@/lib/format"),
  "@/lib/ai-guidance": () => import("@/lib/ai-guidance"),
  "@/lib/ai-queries": () => import("@/lib/ai-queries"),
  "@/lib/module-widgets": () => import("@/lib/module-widgets"),
  "@/lib/stores/visualization-settings": () => import("@/lib/stores/visualization-settings"),
  "@/lib/ws": () => import("@/lib/ws"),
};

/**
 * Externals every panel and widget bundle requires (verified against the built
 * `.dist/*.js` of all bundled modules). Warmed on idle and kicked off in
 * parallel with the bundle fetch, so the scanned top-up is small.
 */
export const CORE_EXTERNALS: readonly string[] = [
  "react",
  "react/jsx-runtime",
  "react-dom",
  "@tanstack/react-query",
  "lucide-react",
  "@/lib/api",
  "@/lib/cn",
  "@/components/ui/skeleton",
];

const _loading = new Map<string, Promise<void>>();
let _driftChecked = false;

function checkDrift(): void {
  if (_driftChecked) return;
  _driftChecked = true;
  // Bundler reads `runtime-externals.json` at build time and marks each entry
  // external; if LOADERS is missing one of those keys a module bundle throws
  // `require("X") is undefined` instead of a clear error. Surface that loudly.
  const expected = new Set(runtimeExternals as readonly string[]);
  const provided = new Set(Object.keys(LOADERS));
  const missing = [...expected].filter((k) => !provided.has(k));
  const extra = [...provided].filter((k) => !expected.has(k));
  if (missing.length || extra.length) {
    // eslint-disable-next-line no-console
    console.error("[module-runtime] drift between LOADERS and runtime-externals.json", {
      missing,
      extra,
    });
  }
}

function loadOne(specifier: string): Promise<void> {
  const existing = _loading.get(specifier);
  if (existing) return existing;

  const loader = LOADERS[specifier];
  if (!loader) {
    return Promise.reject(
      new Error(
        `Module bundle required "${specifier}" which is not in the runtime. ` +
          `Add it to apps/web/lib/runtime-externals.json and lib/module-runtime.ts.`,
      ),
    );
  }

  const promise = loader().then((mod) => {
    window.__FF_RUNTIME__ = { ...(window.__FF_RUNTIME__ ?? {}), [specifier]: mod };
  });
  // A failed import must not poison the cache - a retry should re-attempt.
  promise.catch(() => _loading.delete(specifier));
  _loading.set(specifier, promise);
  return promise;
}

/**
 * Populate `window.__FF_RUNTIME__` with the given externals.
 *
 * Idempotent per specifier: concurrent callers share one in-flight import, and
 * an already-installed external resolves immediately. Safe to call from SSR -
 * it resolves immediately on the server (where no module bundle runs).
 *
 * Omitting `specifiers` installs **everything**, which is what a caller that
 * cannot know its bundle's imports up front has to do. Prefer passing the
 * scanned set.
 */
export function installModuleRuntime(specifiers?: readonly string[]): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  checkDrift();
  const wanted = specifiers ?? Object.keys(LOADERS);
  return Promise.all(wanted.map(loadOne)).then(() => undefined);
}

/**
 * Extract the external specifiers a built module bundle resolves through the
 * `require()` shim. esbuild emits every external as a literal
 * `require("<specifier>")`, including the deferred form it generates for
 * `await import(...)`, so a scan of the source is exhaustive.
 */
export function scanBundleExternals(source: string): string[] {
  const found = new Set<string>();
  const re = /\brequire\(\s*["']([^"']+)["']\s*\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    if (m[1] in LOADERS) found.add(m[1]);
  }
  return [...found];
}

/** Stable list of external specifiers (re-exported from JSON for TS callers). */
export const MODULE_RUNTIME_EXTERNALS: readonly string[] = runtimeExternals as readonly string[];
