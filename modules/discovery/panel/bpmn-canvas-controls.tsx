"use client";

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  CanvasSettings,
  CanvasSettingsActions,
  CanvasSettingsSearch,
  CanvasSettingsSection,
  CanvasSettingsSlider,
  CanvasSettingsSwitch,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

import type { BpmnDecor } from "./canvases/BpmnCanvas";

/** BPMN settings – popover body of the canvas control cluster. */
export function BpmnCanvasSettings({
  freqFilter,
  onFreqFilterChange,
  decor,
  onDecorChange,
  onSearch,
  onDownload,
  downloadDisabled,
}: {
  freqFilter: number;
  onFreqFilterChange: (v: number) => void;
  decor: BpmnDecor;
  onDecorChange: (patch: Partial<BpmnDecor>) => void;
  onSearch: (query: string) => void;
  onDownload: () => void;
  downloadDisabled?: boolean;
}) {
  return (
    <CanvasSettings>
      <CanvasSettingsSection title="Model" first>
        {/* Commit-only: every step would re-mine the model server-side. */}
        <CanvasSettingsSlider
          label="Frequency filter"
          value={freqFilter}
          max={0.5}
          step={0.05}
          onCommit={onFreqFilterChange}
          hint="0 keeps every activity (Inductive Miner); higher re-mines with IM Infrequent"
        />
      </CanvasSettingsSection>

      <CanvasSettingsSection title="Overlay">
        <CanvasSettingsSwitch
          label="Heatmap"
          checked={decor.heatmap}
          onChange={(v) => onDecorChange({ heatmap: v })}
        />
        <CanvasSettingsSwitch
          label="Frequency labels"
          checked={decor.freqLabels}
          onChange={(v) => onDecorChange({ freqLabels: v })}
        />
        <CanvasSettingsSearch placeholder="Activity name…" onSearch={onSearch} />
      </CanvasSettingsSection>

      <CanvasSettingsActions>
        <Button
          variant="outline"
          size="sm"
          className="cursor-pointer gap-1.5"
          disabled={downloadDisabled}
          onClick={onDownload}
        >
          <Download className="h-3.5 w-3.5" />
          Download BPMN
        </Button>
      </CanvasSettingsActions>
    </CanvasSettings>
  );
}
