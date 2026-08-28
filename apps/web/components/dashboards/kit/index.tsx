/**
 * The dashboard widget kit — the shared building blocks every module's cards
 * are made of.
 *
 * Before this, each module carried a private `widgets/_kit.tsx`. Eight copies
 * had drifted: discovery's KPI tile had no explanation affordance while
 * performance's did, agentsimulator hardcoded its own two-colour scheme, and
 * each was inlined into every widget bundle. A board showing cards from three
 * modules looked like three different products.
 *
 * Import from here instead:
 *
 *   import { CardShell, KpiTile, KpiGrid, seriesColor } from "@/components/dashboards/kit";
 *
 * This path is a runtime external (see `lib/runtime-externals.json`), so it is
 * loaded once and shared rather than duplicated into each widget bundle.
 */

export { CardShell, CardEmpty, CardError, CardSection } from "@/components/dashboards/kit/card-shell";
export { KpiTile, KpiGrid } from "@/components/dashboards/kit/kpi";
export { ChartFrame, LegendDot } from "@/components/dashboards/kit/chart-frame";
export { InfoHint } from "@/components/dashboards/kit/info-hint";
export { WidgetHelpBody, hasHelp } from "@/components/dashboards/kit/help";
export {
  SERIES_SLOTS,
  SERIES_COLORS,
  SEQUENTIAL_STEPS,
  CHART_CHROME,
  seriesColor,
  sequentialScale,
  divergingScale,
  statusColor,
  type StatusRole,
} from "@/components/dashboards/kit/palette";
