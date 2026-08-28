"use client";

import {
  CanvasSettings,
  CanvasSettingsSection,
  CanvasSettingsSelect,
  CanvasSettingsSlider,
  CanvasSettingsSwitch,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

import { useHeuristicsRenderSettings } from "./discovery-settings-context";

/**
 * Heuristics-net settings – popover body of the canvas control cluster.
 *
 * The three thresholds are client-side store state that the panel's query reads;
 * committing one refetches the net (the canvas stays mounted on the previous
 * result, see `HeuristicsTab`).
 */
export function HeuristicsCanvasSettings() {
  const [heur, setHeur] = useHeuristicsRenderSettings();

  return (
    <CanvasSettings>
      <CanvasSettingsSection title="Thresholds" first>
        <CanvasSettingsSlider
          label="Dependency"
          value={heur.dependencyThreshold}
          step={0.05}
          onCommit={(v) => setHeur({ dependencyThreshold: v })}
        />
        <CanvasSettingsSlider
          label="AND"
          value={heur.andThreshold}
          step={0.05}
          onCommit={(v) => setHeur({ andThreshold: v })}
        />
        <CanvasSettingsSlider
          label="Loop-2"
          value={heur.loopTwoThreshold}
          step={0.05}
          onCommit={(v) => setHeur({ loopTwoThreshold: v })}
        />
      </CanvasSettingsSection>

      <CanvasSettingsSection title="Render">
        <CanvasSettingsSelect
          label="Edge label"
          value={heur.edgeLabel}
          onChange={(v) => setHeur({ edgeLabel: v })}
          options={[
            { value: "dependency", label: "Dependency" },
            { value: "count", label: "Count" },
            { value: "both", label: "Both" },
          ]}
        />
        <CanvasSettingsSwitch
          label="Hide rare arcs"
          checked={heur.hideRareArcs}
          onChange={(v) => setHeur({ hideRareArcs: v })}
        />
      </CanvasSettingsSection>
    </CanvasSettings>
  );
}
