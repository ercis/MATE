"use client";

import {
  CanvasSettings,
  CanvasSettingsSection,
  CanvasSettingsSegmented,
  CanvasSettingsSelect,
  CanvasSettingsSlider,
  CanvasSettingsSwitch,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

import { useProcessTreeSettings } from "./discovery-settings-context";

export type TreeAlgo = "inductive" | "imf";

/** Process-tree settings – popover body of the canvas control cluster. */
export function ProcessTreeCanvasSettings({
  algo,
  onAlgoChange,
  noiseThreshold,
  onNoiseThresholdChange,
}: {
  algo: TreeAlgo;
  onAlgoChange: (v: TreeAlgo) => void;
  noiseThreshold: number;
  onNoiseThresholdChange: (v: number) => void;
}) {
  const [pt, setPt] = useProcessTreeSettings();

  return (
    <CanvasSettings>
      <CanvasSettingsSection title="Model" first>
        <CanvasSettingsSelect
          label="Algorithm"
          value={algo}
          onChange={onAlgoChange}
          options={[
            { value: "inductive", label: "Inductive Miner" },
            { value: "imf", label: "IM Infrequent" },
          ]}
        />
        {algo === "imf" && (
          <CanvasSettingsSlider
            label="Noise threshold"
            value={noiseThreshold}
            step={0.05}
            onCommit={onNoiseThresholdChange}
          />
        )}
      </CanvasSettingsSection>

      <CanvasSettingsSection title="Render">
        <CanvasSettingsSegmented
          label="Orientation"
          value={pt.orientation}
          onChange={(v) => setPt({ orientation: v })}
          options={[
            { value: "vertical", label: "Vertical" },
            { value: "horizontal", label: "Horizontal" },
          ]}
        />
        <CanvasSettingsSwitch
          label="Fold τ leaves"
          checked={pt.foldTauLeaves}
          onChange={(v) => setPt({ foldTauLeaves: v })}
        />
        <CanvasSettingsSelect
          label="Max depth"
          value={pt.maxDepth === null ? "all" : String(pt.maxDepth)}
          onChange={(v) => setPt({ maxDepth: v === "all" ? null : Number(v) })}
          className="w-24"
          options={[
            { value: "all", label: "All" },
            { value: "2", label: "2" },
            { value: "3", label: "3" },
            { value: "4", label: "4" },
            { value: "6", label: "6" },
            { value: "8", label: "8" },
          ]}
        />
      </CanvasSettingsSection>
    </CanvasSettings>
  );
}
