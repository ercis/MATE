/**
 * Shared runtime exposed to dynamically-loaded module bundles (§5.4).
 *
 * Module bundles are esbuild-compiled per-module into `modules/<folder>/.dist/`
 * with every entry below marked as **external**. At runtime, each external
 * import resolves to a tiny shim that reads from `window.__FF_RUNTIME__[path]`.
 *
 * **Lazy by design.** All ~30 packages below are pulled in via dynamic imports
 * inside `installModuleRuntime()`, not at module-eval time. Otherwise the
 * Providers chain on every page (landing, settings, …) would eagerly load
 * xyflow, recharts, radix-ui, etc., causing both bundle bloat *and* SSR ↔
 * client hydration mismatches when any of those packages run side effects
 * (style injection, portal mounting, …) at import time.
 *
 * Add a new external: list it in `runtime-externals.json`, add the dynamic
 * import + assignment below, and the bundler picks it up on next install.
 */

import runtimeExternals from "@/lib/runtime-externals.json";

declare global {
  interface Window {
    __FF_RUNTIME__?: Record<string, unknown>;
  }
}

let _installPromise: Promise<void> | null = null;

/**
 * Idempotent. Returns a Promise that resolves after `window.__FF_RUNTIME__`
 * is populated with every external the bundler expects. Safe to call from
 * SSR – it resolves immediately on the server (where no module bundle
 * runs).
 */
export function installModuleRuntime(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (_installPromise) return _installPromise;

  _installPromise = (async () => {
    const [
      React,
      ReactJsxRuntime,
      ReactDOM,
      TanstackQuery,
      XyflowReact,
      LucideReact,
      Recharts,
      Sonner,
      RadixUi,
      D3Hierarchy,
      ElkBundle,
      UiAlertDialog,
      UiBadge,
      UiButton,
      UiCard,
      UiLabel,
      UiRadioGroup,
      UiScrollArea,
      UiSelect,
      UiSeparator,
      UiSkeleton,
      UiSlider,
      UiSwitch,
      UiTable,
      UiTabs,
      UiTooltip,
      EmptyState,
      AiGuidanceCard,
      CanvasShell,
      CanvasSkeleton,
      LibApi,
      LibCn,
      LibFormat,
      LibAiGuidance,
      LibAiQueries,
      LibModuleLayout,
      LibModuleWidgets,
      LibVizSettings,
      LibWs,
    ] = await Promise.all([
      import("react"),
      import("react/jsx-runtime"),
      import("react-dom"),
      import("@tanstack/react-query"),
      import("@xyflow/react"),
      import("lucide-react"),
      import("recharts"),
      import("sonner"),
      import("radix-ui"),
      import("d3-hierarchy"),
      import("elkjs/lib/elk.bundled.js"),
      import("@/components/ui/alert-dialog"),
      import("@/components/ui/badge"),
      import("@/components/ui/button"),
      import("@/components/ui/card"),
      import("@/components/ui/label"),
      import("@/components/ui/radio-group"),
      import("@/components/ui/scroll-area"),
      import("@/components/ui/select"),
      import("@/components/ui/separator"),
      import("@/components/ui/skeleton"),
      import("@/components/ui/slider"),
      import("@/components/ui/switch"),
      import("@/components/ui/table"),
      import("@/components/ui/tabs"),
      import("@/components/ui/tooltip"),
      import("@/components/empty-state"),
      import("@/components/ai/ai-guidance-card"),
      import("@/components/visualizations/canvases/shared/canvas-shell"),
      import("@/components/visualizations/canvases/shared/canvas-skeleton"),
      import("@/lib/api"),
      import("@/lib/cn"),
      import("@/lib/format"),
      import("@/lib/ai-guidance"),
      import("@/lib/ai-queries"),
      import("@/lib/module-layout"),
      import("@/lib/module-widgets"),
      import("@/lib/stores/visualization-settings"),
      import("@/lib/ws"),
    ]);

    const MODULES: Record<string, unknown> = {
      "react": React,
      "react/jsx-runtime": ReactJsxRuntime,
      "react-dom": ReactDOM,
      "@tanstack/react-query": TanstackQuery,
      "@xyflow/react": XyflowReact,
      "lucide-react": LucideReact,
      "recharts": Recharts,
      "sonner": Sonner,
      "radix-ui": RadixUi,
      "d3-hierarchy": D3Hierarchy,
      "elkjs/lib/elk.bundled.js": ElkBundle,
      "@/components/ui/alert-dialog": UiAlertDialog,
      "@/components/ui/badge": UiBadge,
      "@/components/ui/button": UiButton,
      "@/components/ui/card": UiCard,
      "@/components/ui/label": UiLabel,
      "@/components/ui/radio-group": UiRadioGroup,
      "@/components/ui/scroll-area": UiScrollArea,
      "@/components/ui/select": UiSelect,
      "@/components/ui/separator": UiSeparator,
      "@/components/ui/skeleton": UiSkeleton,
      "@/components/ui/slider": UiSlider,
      "@/components/ui/switch": UiSwitch,
      "@/components/ui/table": UiTable,
      "@/components/ui/tabs": UiTabs,
      "@/components/ui/tooltip": UiTooltip,
      "@/components/empty-state": EmptyState,
      "@/components/ai/ai-guidance-card": AiGuidanceCard,
      "@/components/visualizations/canvases/shared/canvas-shell": CanvasShell,
      "@/components/visualizations/canvases/shared/canvas-skeleton": CanvasSkeleton,
      "@/lib/api": LibApi,
      "@/lib/cn": LibCn,
      "@/lib/format": LibFormat,
      "@/lib/ai-guidance": LibAiGuidance,
      "@/lib/ai-queries": LibAiQueries,
      "@/lib/module-layout": LibModuleLayout,
      "@/lib/module-widgets": LibModuleWidgets,
      "@/lib/stores/visualization-settings": LibVizSettings,
      "@/lib/ws": LibWs,
    };

    // Drift check: bundler reads `runtime-externals.json` at build time and
    // marks each entry external; if MODULES is missing one of those keys a
    // module bundle will throw `require("X") is undefined` instead of a
    // clear error. Surface that loudly in the console.
    const expected = new Set(runtimeExternals as readonly string[]);
    const provided = new Set(Object.keys(MODULES));
    const missing = [...expected].filter((k) => !provided.has(k));
    const extra = [...provided].filter((k) => !expected.has(k));
    if (missing.length || extra.length) {
      // eslint-disable-next-line no-console
      console.error(
        "[module-runtime] drift between MODULES and runtime-externals.json",
        { missing, extra },
      );
    }

    window.__FF_RUNTIME__ = MODULES;
  })();

  return _installPromise;
}

/** Stable list of external specifiers (re-exported from JSON for TS callers). */
export const MODULE_RUNTIME_EXTERNALS: readonly string[] = runtimeExternals as readonly string[];
