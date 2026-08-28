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

// The shared widget kit — build cards from these rather than a private
// `_kit.tsx`, so every module's cards read as one product (§7 Widgets).
export {
  CardShell,
  CardEmpty,
  CardError,
  CardSection,
  KpiTile,
  KpiGrid,
  InfoHint,
  WidgetHelpBody,
  hasHelp,
  SERIES_SLOTS,
  SERIES_COLORS,
  SEQUENTIAL_STEPS,
  CHART_CHROME,
  seriesColor,
  sequentialScale,
  divergingScale,
  statusColor,
  type StatusRole,
} from "@/components/dashboards/kit";

// Drill-down: navigating from a card to the page behind a number.
// `variantHref`/`activityHref` are the canonical entity views — link variants
// and activities there (with `next/link`) wherever a module renders them.
export {
  DRILL_PARAMS,
  resolveDrillHref,
  drillLabel,
  modulePath,
  variantHref,
  activityHref,
  useDrillParams,
} from "@/lib/dashboards/drill";
export type { DrillTarget, DrillHandler } from "@/lib/dashboards/drill";

// Which settings bucket a rendered visualization reads/writes. A widget that
// builds its own settings provider should resolve its scope as
// `props.scope ?? useCardScope() ?? panelScope(moduleId)` so the same component
// works both on a dashboard card and inside the module's panel.
export {
  useCardScope,
  cardScope,
  panelScope,
  isCardScope,
} from "@/lib/dashboards/card-scope";

// HTTP + WS client primitives.
export { api, rawFetch, ApiError, wsUrl } from "@/lib/api";
export { subscribeBus, subscribeJob } from "@/lib/ws";

// Formatting + class-name helpers.
export { cn } from "@/lib/cn";
export { formatDuration, formatNumber } from "@/lib/format";

export const SDK_VERSION = "0.2.0";
