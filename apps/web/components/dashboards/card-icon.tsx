import {
  Activity,
  AlertTriangle,
  BarChart3,
  Boxes,
  Gauge,
  GitBranch,
  Hourglass,
  Layers,
  LineChart,
  type LucideIcon,
  Network,
  Table2,
  Timer,
  TrendingUp,
  Waves,
  Workflow,
} from "lucide-react";

/**
 * Curated lucide lookup for card icons. Manifests reference icons by name
 * (`frontend.widgets[].icon`); we map a known set and fall back to a generic
 * chart glyph. A curated map (vs `import * as`) keeps tree-shaking intact.
 */
const ICONS: Record<string, LucideIcon> = {
  Activity,
  AlertTriangle,
  BarChart3,
  Boxes,
  Gauge,
  GitBranch,
  Hourglass,
  Layers,
  LineChart,
  Network,
  Table2,
  Timer,
  TrendingUp,
  Waves,
  Workflow,
};

export function cardIcon(name: string | null | undefined): LucideIcon {
  if (name && ICONS[name]) return ICONS[name];
  return BarChart3;
}
