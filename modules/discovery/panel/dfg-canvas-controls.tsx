"use client";

import {
  CanvasSettings,
  CanvasSettingsBadgeToggle,
  CanvasSettingsSelect,
  CanvasSettingsSlider,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

import type { DfgData } from "./types";
import { computeDfgVisibility } from "./dfg-filter";
import { useDfgSettings } from "./discovery-settings-context";

/**
 * DFG settings – the popover body of the canvas control cluster
 * (`CanvasShell settings=…`), so they're reachable in fullscreen too. This is
 * the reference every other canvas follows: NO filter bar above the canvas,
 * every control built from the shared `CanvasSetting*` primitives.
 *
 * Reset (dragged node positions) is the cluster's own button – see `DfgCanvas`.
 */
export function DfgCanvasSettings({ data }: { data: DfgData }) {
  const [dfg, setDfg] = useDfgSettings();

  const filtered = computeDfgVisibility(data, dfg);

  return (
    <CanvasSettings>
      <RankSlider
        label="Activities"
        fraction={filtered.resolvedActivitiesShown}
        shown={filtered.visibleActivities.length}
        total={data.activities.length}
        auto={filtered.autoActivities}
        onChange={(v) => setDfg({ activitiesShown: v })}
        onAuto={() => setDfg({ activitiesShown: "auto" })}
      />
      <RankSlider
        label="Connections"
        fraction={filtered.resolvedConnectionsShown}
        shown={filtered.visibleEdges.length}
        total={filtered.candidateEdges.length}
        auto={filtered.autoConnections}
        onChange={(v) => setDfg({ connectionsShown: v })}
        onAuto={() => setDfg({ connectionsShown: "auto" })}
      />
      <CanvasSettingsSelect
        label="Layout"
        value={dfg.layoutMode}
        onChange={(v) =>
          setDfg({
            layoutMode: v as "celonis-classic" | "backbone" | "backbone-v2" | "sugiyama",
          })
        }
        options={[
          { value: "celonis-classic", label: "Process flow (Celonis)" },
          { value: "backbone", label: "Backbone (optimized)" },
          { value: "backbone-v2", label: "Backbone (smooth routing)" },
          { value: "sugiyama", label: "Layered (Sugiyama)" },
        ]}
      />
      <CanvasSettingsSelect
        label="Edge label"
        value={dfg.edgeLabel}
        onChange={(v) => setDfg({ edgeLabel: v })}
        options={[
          { value: "count", label: "Count" },
          { value: "duration", label: "Duration" },
          { value: "off", label: "Off" },
        ]}
      />
    </CanvasSettings>
  );
}

/** Frequency-rank slider with a shown/total readout and the "Auto" (knee) pill. */
function RankSlider({
  label,
  fraction,
  shown,
  total,
  auto,
  onChange,
  onAuto,
}: {
  label: string;
  fraction: number;
  shown: number;
  total: number;
  auto: boolean;
  onChange: (v: number) => void;
  onAuto: () => void;
}) {
  return (
    <CanvasSettingsSlider
      label={label}
      value={fraction}
      min={0}
      max={1}
      step={0.005}
      onChange={onChange}
      format={() => `${shown} / ${total}`}
      badge={
        <CanvasSettingsBadgeToggle
          label="Auto"
          active={auto}
          onActivate={onAuto}
          title={
            auto
              ? `Auto: knee of the frequency curve (showing ${shown})`
              : "Reset to the computed threshold (knee of the frequency curve)"
          }
        />
      }
    />
  );
}
