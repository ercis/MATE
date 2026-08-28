"use client";

import {
  CanvasSettings,
  CanvasSettingsSection,
  CanvasSettingsSegmented,
  CanvasSettingsSelect,
  CanvasSettingsSlider,
  CanvasSettingsSwitch,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

import { usePetriSettings, useResetPositions } from "./discovery-settings-context";

export type PetriAlgo = "alpha" | "alpha-plus" | "inductive" | "imf" | "ilp";

/**
 * Petri-net settings – popover body of the canvas control cluster. Model choice
 * (which drives the fetch) sits above the pure render settings; both live in
 * the canvas, never in a bar above it. See `dfg-canvas-controls.tsx` for the
 * reference this follows.
 */
export function PetriCanvasSettings({
  algo,
  onAlgoChange,
  noiseThreshold,
  onNoiseThresholdChange,
}: {
  algo: PetriAlgo;
  onAlgoChange: (v: PetriAlgo) => void;
  noiseThreshold: number;
  onNoiseThresholdChange: (v: number) => void;
}) {
  const [petri, setPetri] = usePetriSettings();
  const resetPositions = useResetPositions();

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
            { value: "ilp", label: "ILP Miner (heavy)" },
            { value: "alpha", label: "Alpha Miner" },
            { value: "alpha-plus", label: "Alpha+ Miner" },
          ]}
        />
        {algo === "imf" && (
          // Commit-only: a re-mine per drag step would queue one miner run per pixel.
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
          label="Layout"
          value={petri.layoutDirection === "TB" ? "TB" : "LR"}
          onChange={(v) => {
            // Dragged positions were recorded in the old direction; merged over
            // a rotated layout they land on nothing, and their drift also drops
            // every incident arc back to a plain bezier. Clear them on flip.
            resetPositions("petri");
            setPetri({ layoutDirection: v });
          }}
          options={[
            { value: "LR", label: "LR" },
            { value: "TB", label: "TB" },
          ]}
          hint="Reading direction of the net. Switching clears manually moved nodes."
        />
        <CanvasSettingsSwitch
          label="Merge arcs"
          checked={petri.mergeArcs}
          onChange={(v) => setPetri({ mergeArcs: v })}
          hint="Arcs out of a place share a trunk and branch late, instead of one exit each"
        />
        <CanvasSettingsSwitch
          label="Show invisible (τ)"
          checked={petri.showInvisibleTransitions}
          onChange={(v) => setPetri({ showInvisibleTransitions: v })}
        />
        <CanvasSettingsSwitch
          label="Highlight markings"
          checked={petri.highlightMarkings}
          onChange={(v) => setPetri({ highlightMarkings: v })}
        />
        <CanvasSettingsSwitch
          label="Arc weights"
          checked={petri.showArcWeights}
          onChange={(v) => setPetri({ showArcWeights: v })}
        />
        <CanvasSettingsSelect
          label="Transition label"
          value={petri.transitionLabelMode}
          onChange={(v) => setPetri({ transitionLabelMode: v })}
          options={[
            { value: "activity", label: "Activity" },
            { value: "id", label: "ID" },
            { value: "both", label: "Both" },
          ]}
        />
        <CanvasSettingsSelect
          label="Place mode"
          value={petri.placeMode}
          onChange={(v) => setPetri({ placeMode: v })}
          options={[
            { value: "rings", label: "Rings" },
            { value: "count", label: "Count" },
          ]}
        />
      </CanvasSettingsSection>
    </CanvasSettings>
  );
}
