/**
 * Public TS SDK for module frontends.
 *
 * Module panels and widgets are bundled with these specifiers marked
 * **external** by `apps/web/scripts/bundle-modules.mjs`. At runtime, the
 * host's `window.__FF_RUNTIME__` resolves them to the platform's single
 * instances (see `apps/web/lib/module-runtime.ts`), so a module's
 * `<Button>` is byte-identical to the host's and React hook context flows
 * across module boundaries.
 *
 * Authors should import from this package rather than reaching into the
 * host's `@/` aliases directly – the indirection lets the platform evolve
 * its internal layout without breaking installed modules.
 */

// Cross-module widget loader (§7.7).
export { useWidget } from "@/lib/module-widgets";
export type { WidgetProps } from "@/lib/module-widgets";

// Per-(user, log, module) layout persistence (§7.7).
export { useModuleLayout, useSaveModuleLayout } from "@/lib/module-layout";

// HTTP + WS client primitives.
export { api, rawFetch, ApiError, wsUrl } from "@/lib/api";
export { subscribeBus, subscribeJob } from "@/lib/ws";

// Formatting + class-name helpers.
export { cn } from "@/lib/cn";
export { formatDuration, formatNumber } from "@/lib/format";

export const SDK_VERSION = "0.2.0";
