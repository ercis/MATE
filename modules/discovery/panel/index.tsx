"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  Download,
  RotateCcw,
  Search,
  Settings2,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { EmptyState } from "@/components/empty-state";

import { BpmnCanvas, type BpmnDecor } from "./canvases/BpmnCanvas";
import { buildFrequencyMaps, heatColor, HEAT_BUCKETS } from "./bpmn-decorate";
import { DfgCanvas } from "./canvases/DfgCanvas";
import { HeuristicsNetCanvas } from "./canvases/HeuristicsNetCanvas";
import { PetriNetCanvas } from "./canvases/PetriNetCanvas";
import { PrefixTreeCanvas } from "./canvases/PrefixTreeCanvas";
import { ProcessTreeCanvas } from "./canvases/ProcessTreeCanvas";
import { DfgDetailsPanel } from "./dfg-details-panel";
import { computeDfgVisibility } from "./dfg-filter";
import {
  DiscoverySettingsProvider,
  useDfgSettings,
  useHeuristicsRenderSettings,
  usePetriSettings,
  useProcessTreeSettings,
  useResetPositions,
} from "./discovery-settings-context";
import { SettingsSheet } from "./settings-sheet";

import {
  downloadBpmn,
  useDiscoveryBpmn,
  useDiscoveryDfg,
  useDiscoveryHeuristicsNet,
  useDiscoveryPetriAlpha,
  useDiscoveryPetriAlphaPlus,
  useDiscoveryPetriIlp,
  useDiscoveryPetriImf,
  useDiscoveryPetriInductive,
  useDiscoveryPrefixTree,
  useDiscoveryProcessTree,
  useDiscoveryProcessTreeImf,
  type BpmnAlgo,
  type HeuristicsThresholds,
} from "./queries";

type View = "dfg" | "petri" | "tree" | "prefix-tree" | "heuristics" | "bpmn";
const VIEWS: { value: View; label: string }[] = [
  { value: "dfg", label: "DFG" },
  { value: "petri", label: "Petri Net" },
  { value: "tree", label: "Process Tree" },
  { value: "prefix-tree", label: "Prefix Tree" },
  { value: "heuristics", label: "Heuristics Net" },
  { value: "bpmn", label: "BPMN" },
];

export function DiscoveryPanel({ logId, moduleId }: { logId: string; moduleId: string }) {
  return (
    <DiscoverySettingsProvider logId={logId} moduleId={moduleId}>
      <DiscoveryPanelContent logId={logId} />
    </DiscoverySettingsProvider>
  );
}

function DiscoveryPanelContent({ logId }: { logId: string }) {
  const [view, setView] = useState<View>("dfg");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const resetPositions = useResetPositions();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Discovery visualisations"
          className="inline-flex flex-1 max-w-3xl items-center gap-1 rounded-lg bg-muted p-[3px]"
        >
          {VIEWS.map((v) => {
            const isActive = view === v.value;
            return (
              <button
                key={v.value}
                type="button"
                role="tab"
                aria-selected={isActive}
                data-state={isActive ? "active" : "inactive"}
                onClick={() => setView(v.value)}
                className={cn(
                  "flex-1 cursor-pointer rounded-md px-3 py-1.5 text-sm font-medium transition-all",
                  isActive
                    ? "bg-background text-foreground shadow-sm"
                    : "text-foreground/60 hover:text-foreground",
                )}
              >
                {v.label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" className="cursor-pointer gap-1.5">
                <RotateCcw className="h-3.5 w-3.5" />
                Reset layout
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Reset layout?</AlertDialogTitle>
                <AlertDialogDescription>
                  All dragged node positions for this module on this log will be discarded and
                  the auto-layout will be reapplied. This cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
                <AlertDialogAction
                  className="cursor-pointer"
                  onClick={() => resetPositions()}
                >
                  Reset
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <Button
            variant="outline"
            size="sm"
            className="cursor-pointer gap-1.5"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 className="h-3.5 w-3.5" />
            Configure
          </Button>
        </div>
      </div>

      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />

      <div className="space-y-3">
        {view === "dfg" && <DfgTab logId={logId} />}
        {view === "petri" && <PetriTab logId={logId} />}
        {view === "tree" && <ProcessTreeTab logId={logId} />}
        {view === "prefix-tree" && <PrefixTreeTab logId={logId} />}
        {view === "heuristics" && <HeuristicsTab logId={logId} />}
        {view === "bpmn" && <BpmnTab logId={logId} />}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Frame helpers
// --------------------------------------------------------------------------

function CanvasFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative h-[640px] w-full overflow-hidden rounded-xl border bg-card">
      {children}
    </div>
  );
}

function CanvasSkeleton() {
  return (
    <div className="h-[640px] w-full overflow-hidden rounded-xl border bg-card p-4">
      <Skeleton className="h-full w-full" />
    </div>
  );
}

function CanvasError({ message }: { message: string }) {
  return (
    <CanvasFrame>
      <EmptyState
        icon={AlertTriangle}
        title="Could not compute discovery"
        description={message}
      />
    </CanvasFrame>
  );
}

function FilterBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border bg-muted/40 px-4 py-3 text-xs">
      {children}
    </div>
  );
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2.5">
      <Label className="text-xs text-muted-foreground font-normal shrink-0">{label}</Label>
      {children}
    </div>
  );
}

// --------------------------------------------------------------------------
// Tabs
// --------------------------------------------------------------------------

function DfgTab({ logId }: { logId: string }) {
  const [dfg, setDfg] = useDfgSettings();
  const [variantPct, setVariantPct] = useState<number>(1);
  const { data, isLoading, isError, error } = useDiscoveryDfg(logId, variantPct < 1 ? variantPct : undefined);

  const [selected, setSelected] = useState<{ kind: "node" | "edge"; id: string } | null>(null);

  // Live counts: ask the same helper the canvas uses, so the slider labels
  // and the rendered graph never disagree (e.g. spanning-floor edges still
  // show up in the count even when the user drags connections to 0).
  const counts = (() => {
    if (!data) {
      return { totalActivities: 0, shownActivities: 0, candidateEdges: 0, shownEdges: 0 };
    }
    const filtered = computeDfgVisibility(data, dfg);
    return {
      totalActivities: data.activities.length,
      shownActivities: filtered.visibleActivities.length,
      candidateEdges: filtered.candidateEdges.length,
      shownEdges: filtered.visibleEdges.length,
    };
  })();

  return (
    <>
      <div className="space-y-3 rounded-lg border bg-muted/40 px-4 py-3 text-xs">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-3">
          <RankSlider
            label="Activities"
            fraction={dfg.activitiesShown}
            shown={counts.shownActivities}
            total={counts.totalActivities}
            onChange={(v) => setDfg({ activitiesShown: v })}
          />
          <RankSlider
            label="Connections"
            fraction={dfg.connectionsShown}
            shown={counts.shownEdges}
            total={counts.candidateEdges}
            onChange={(v) => setDfg({ connectionsShown: v })}
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t pt-3">
          <FilterField label="Algorithm">
            <span className="text-xs font-medium text-foreground">Direct-Follows Graph</span>
          </FilterField>
          <FilterField label="Hide self-loops">
            <Switch
              checked={dfg.hideSelfLoops}
              onCheckedChange={(v) => setDfg({ hideSelfLoops: v })}
            />
          </FilterField>
          <FilterField label="Show top edges">
            <Select
              value={String(dfg.edgeTopPercent)}
              onValueChange={(v) => setDfg({ edgeTopPercent: Number(v) as typeof dfg.edgeTopPercent })}
            >
              <SelectTrigger className="h-7 w-20 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="100">100%</SelectItem>
                <SelectItem value="95">95%</SelectItem>
                <SelectItem value="90">90%</SelectItem>
                <SelectItem value="85">85%</SelectItem>
                <SelectItem value="80">80%</SelectItem>
                <SelectItem value="70">70%</SelectItem>
              </SelectContent>
            </Select>
          </FilterField>
          <FilterField label="Edge label">
            <Select value={dfg.edgeLabel} onValueChange={(v) => setDfg({ edgeLabel: v as typeof dfg.edgeLabel })}>
              <SelectTrigger className="h-7 w-32 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="count">Count</SelectItem>
                <SelectItem value="duration">Duration</SelectItem>
                <SelectItem value="off">Off</SelectItem>
              </SelectContent>
            </Select>
          </FilterField>
          <FilterField label="Thickness">
            <Select
              value={dfg.edgeThicknessEncoding}
              onValueChange={(v) => setDfg({ edgeThicknessEncoding: v as typeof dfg.edgeThicknessEncoding })}
            >
              <SelectTrigger className="h-7 w-28 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="log">Log</SelectItem>
                <SelectItem value="linear">Linear</SelectItem>
                <SelectItem value="off">Off</SelectItem>
              </SelectContent>
            </Select>
          </FilterField>
          <FilterField label="Layout">
            <Select
              value={dfg.layoutMode}
              onValueChange={(v) => setDfg({ layoutMode: v as typeof dfg.layoutMode })}
            >
              <SelectTrigger className="h-7 w-36 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="temporal">Temporal</SelectItem>
                <SelectItem value="temporal-phases-2">Phases #2</SelectItem>
                <SelectItem value="temporal-phases-3">Phases #3</SelectItem>
                <SelectItem value="temporal-swimlane">Swimlane</SelectItem>
                <SelectItem value="happy-path-tower">Happy Path Tower</SelectItem>
              </SelectContent>
            </Select>
          </FilterField>
          <FilterField label="Variant coverage">
            <Select
              value={String(variantPct)}
              onValueChange={(v) => setVariantPct(Number(v))}
            >
              <SelectTrigger className="h-7 w-20 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1">100%</SelectItem>
                <SelectItem value="0.95">95%</SelectItem>
                <SelectItem value="0.9">90%</SelectItem>
                <SelectItem value="0.8">80%</SelectItem>
                <SelectItem value="0.7">70%</SelectItem>
                <SelectItem value="0.5">50%</SelectItem>
              </SelectContent>
            </Select>
          </FilterField>
        </div>
      </div>

      {isLoading ? (
        <CanvasSkeleton />
      ) : isError || !data ? (
        <CanvasError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <CanvasFrame>
          <DfgCanvas
            data={data}
            selectedNodeId={selected?.kind === "node" ? selected.id : null}
            selectedEdgeId={selected?.kind === "edge" ? selected.id : null}
            onSelect={setSelected}
            overlay={
              selected ? (
                <DfgDetailsPanel
                  data={data}
                  selectedNodeId={selected.kind === "node" ? selected.id : null}
                  selectedEdgeId={selected.kind === "edge" ? selected.id : null}
                  onClose={() => setSelected(null)}
                />
              ) : null
            }
          />
        </CanvasFrame>
      )}
    </>
  );
}

function RankSlider({
  label,
  fraction,
  shown,
  total,
  step = 0.005,
  onChange,
}: {
  label: string;
  fraction: number;
  shown: number;
  total: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <Label className="w-24 shrink-0 text-xs text-muted-foreground font-normal">{label}</Label>
      <Slider
        value={[fraction]}
        min={0}
        max={1}
        step={step}
        onValueChange={(v) => onChange(v[0] ?? 0)}
        className="flex-1"
      />
      <span className="tabular-nums w-20 text-right text-muted-foreground shrink-0">
        {shown} / {total}
      </span>
    </div>
  );
}

type PetriAlgo = "alpha" | "alpha-plus" | "inductive" | "imf" | "ilp";

function PetriTab({ logId }: { logId: string }) {
  const [algo, setAlgo] = useState<PetriAlgo>("inductive");
  const [noiseThreshold, setNoiseThreshold] = useState(0.2);

  const alpha = useDiscoveryPetriAlpha(logId);
  const alphaPlus = useDiscoveryPetriAlphaPlus(logId);
  const inductive = useDiscoveryPetriInductive(logId);
  const imf = useDiscoveryPetriImf(logId, noiseThreshold);
  const ilp = useDiscoveryPetriIlp(logId);

  const q = algo === "alpha" ? alpha
    : algo === "alpha-plus" ? alphaPlus
    : algo === "imf" ? imf
    : algo === "ilp" ? ilp
    : inductive;

  return (
    <>
      <PetriFilterBar
        algo={algo}
        onAlgoChange={setAlgo}
        noiseThreshold={noiseThreshold}
        onNoiseThresholdChange={setNoiseThreshold}
      />
      {q.isLoading ? (
        <CanvasSkeleton />
      ) : q.isError || !q.data ? (
        <CanvasError message={(q.error as Error)?.message ?? "Unknown error"} />
      ) : (
        <CanvasFrame>
          <PetriNetCanvas data={q.data} />
        </CanvasFrame>
      )}
    </>
  );
}

function PetriFilterBar({
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
  return (
    <FilterBar>
      <FilterField label="Algorithm">
        <Select value={algo} onValueChange={(v) => onAlgoChange(v as PetriAlgo)}>
          <SelectTrigger className="h-7 w-40 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="inductive">Inductive Miner</SelectItem>
            <SelectItem value="imf">IM Infrequent</SelectItem>
            <SelectItem value="ilp">ILP Miner</SelectItem>
            <SelectItem value="alpha">Alpha Miner</SelectItem>
            <SelectItem value="alpha-plus">Alpha+ Miner</SelectItem>
          </SelectContent>
        </Select>
      </FilterField>
      {algo === "imf" && (
        <FilterField label="Noise threshold">
          <CommitSlider value={noiseThreshold} onCommit={onNoiseThresholdChange} />
        </FilterField>
      )}
      <FilterField label="Show invisible (τ)">
        <Switch
          checked={petri.showInvisibleTransitions}
          onCheckedChange={(v) => setPetri({ showInvisibleTransitions: v })}
        />
      </FilterField>
      <FilterField label="Highlight markings">
        <Switch
          checked={petri.highlightMarkings}
          onCheckedChange={(v) => setPetri({ highlightMarkings: v })}
        />
      </FilterField>
      <FilterField label="Arc weights">
        <Switch
          checked={petri.showArcWeights}
          onCheckedChange={(v) => setPetri({ showArcWeights: v })}
        />
      </FilterField>
      <FilterField label="Transition label">
        <Select
          value={petri.transitionLabelMode}
          onValueChange={(v) => setPetri({ transitionLabelMode: v as typeof petri.transitionLabelMode })}
        >
          <SelectTrigger className="h-7 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="activity">Activity</SelectItem>
            <SelectItem value="id">ID</SelectItem>
            <SelectItem value="both">Both</SelectItem>
          </SelectContent>
        </Select>
      </FilterField>
      <FilterField label="Place mode">
        <Select
          value={petri.placeMode}
          onValueChange={(v) => setPetri({ placeMode: v as typeof petri.placeMode })}
        >
          <SelectTrigger className="h-7 w-24 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="rings">Rings</SelectItem>
            <SelectItem value="count">Count</SelectItem>
          </SelectContent>
        </Select>
      </FilterField>
    </FilterBar>
  );
}

type TreeAlgo = "inductive" | "imf";

function ProcessTreeTab({ logId }: { logId: string }) {
  const [pt, setPt] = useProcessTreeSettings();
  const [algo, setAlgo] = useState<TreeAlgo>("inductive");
  const [noiseThreshold, setNoiseThreshold] = useState(0.2);

  const inductive = useDiscoveryProcessTree(logId);
  const imf = useDiscoveryProcessTreeImf(logId, noiseThreshold);

  const q = algo === "imf" ? imf : inductive;

  return (
    <>
      <FilterBar>
        <FilterField label="Algorithm">
          <Select value={algo} onValueChange={(v) => setAlgo(v as TreeAlgo)}>
            <SelectTrigger className="h-7 w-44 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="inductive">Inductive Miner</SelectItem>
              <SelectItem value="imf">IM Infrequent</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        {algo === "imf" && (
          <FilterField label="Noise threshold">
            <CommitSlider value={noiseThreshold} onCommit={setNoiseThreshold} />
          </FilterField>
        )}
        <FilterField label="Orientation">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-7 w-7 cursor-pointer p-0"
                onClick={() =>
                  setPt({
                    orientation: pt.orientation === "vertical" ? "horizontal" : "vertical",
                  })
                }
              >
                <ArrowUp
                  className="h-3.5 w-3.5 transition-transform"
                  style={{
                    transform: pt.orientation === "horizontal" ? "rotate(90deg)" : "rotate(0deg)",
                  }}
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {pt.orientation === "vertical" ? "Vertical" : "Horizontal"}
            </TooltipContent>
          </Tooltip>
        </FilterField>
        <FilterField label="Fold τ leaves">
          <Switch
            checked={pt.foldTauLeaves}
            onCheckedChange={(v) => setPt({ foldTauLeaves: v })}
          />
        </FilterField>
        <FilterField label="Max depth">
          <Select
            value={pt.maxDepth === null ? "all" : String(pt.maxDepth)}
            onValueChange={(v) => setPt({ maxDepth: v === "all" ? null : Number(v) })}
          >
            <SelectTrigger className="h-7 w-24 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="2">2</SelectItem>
              <SelectItem value="3">3</SelectItem>
              <SelectItem value="4">4</SelectItem>
              <SelectItem value="6">6</SelectItem>
              <SelectItem value="8">8</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
      </FilterBar>

      {q.isLoading ? (
        <CanvasSkeleton />
      ) : q.isError || !q.data ? (
        <CanvasError message={(q.error as Error)?.message ?? "Unknown error"} />
      ) : (
        <CanvasFrame>
          <ProcessTreeCanvas data={q.data} />
        </CanvasFrame>
      )}
    </>
  );
}

function PrefixTreeTab({ logId }: { logId: string }) {
  const { data, isLoading, isError, error } = useDiscoveryPrefixTree(logId);
  return (
    <>
      {isLoading ? (
        <CanvasSkeleton />
      ) : isError || !data ? (
        <CanvasError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <CanvasFrame>
          <PrefixTreeCanvas data={data} />
        </CanvasFrame>
      )}
    </>
  );
}

function HeuristicsTab({ logId }: { logId: string }) {
  const [heur, setHeur] = useHeuristicsRenderSettings();

  // Thresholds live entirely client-side. Persisting them to the module
  // /config on every slider drag used to cascade refetches across every
  // discovery query (`refetchType: "all"`), which crashed the inactive ILP
  // miner with OOM and overflowed FastAPI's encoder on deep process trees.
  const queryThresholds: HeuristicsThresholds = {
    dependency_threshold: heur.dependencyThreshold,
    and_threshold: heur.andThreshold,
    loop_two_threshold: heur.loopTwoThreshold,
  };

  const { data, isLoading, isError, error } = useDiscoveryHeuristicsNet(logId, queryThresholds);

  return (
    <>
      <FilterBar>
        <ThresholdSlider
          label="Dependency"
          value={heur.dependencyThreshold}
          onChange={(v) => setHeur({ dependencyThreshold: v })}
        />
        <ThresholdSlider
          label="AND"
          value={heur.andThreshold}
          onChange={(v) => setHeur({ andThreshold: v })}
        />
        <ThresholdSlider
          label="Loop-2"
          value={heur.loopTwoThreshold}
          onChange={(v) => setHeur({ loopTwoThreshold: v })}
        />
        <FilterField label="Edge label">
          <Select
            value={heur.edgeLabel}
            onValueChange={(v) => setHeur({ edgeLabel: v as typeof heur.edgeLabel })}
          >
            <SelectTrigger className="h-7 w-32 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="dependency">Dependency</SelectItem>
              <SelectItem value="count">Count</SelectItem>
              <SelectItem value="both">Both</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Hide rare arcs">
          <Switch
            checked={heur.hideRareArcs}
            onCheckedChange={(v) => setHeur({ hideRareArcs: v })}
          />
        </FilterField>
      </FilterBar>

      {isLoading ? (
        <CanvasSkeleton />
      ) : isError || !data ? (
        <CanvasError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <CanvasFrame>
          <HeuristicsNetCanvas data={data} />
        </CanvasFrame>
      )}
    </>
  );
}

function ThresholdSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <FilterField label={label}>
      <CommitSlider value={value} onCommit={onChange} />
    </FilterField>
  );
}

/**
 * Slider that updates its visual position on every drag step but only
 * commits the value (triggering parent re-renders / refetches) on
 * pointer release. Used for discovery thresholds where each step
 * change would otherwise queue an expensive miner / heuristics fetch.
 */
function CommitSlider({
  value,
  onCommit,
  min = 0,
  max = 1,
  step = 0.05,
  width = "w-72",
  digits = 2,
}: {
  value: number;
  onCommit: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: string;
  digits?: number;
}) {
  const safeValue = Number.isFinite(value) ? value : min;
  const [local, setLocal] = useState(safeValue);
  useEffect(() => {
    setLocal(safeValue);
  }, [safeValue]);
  return (
    <div className={cn("flex items-center gap-3", width)}>
      <Slider
        value={[local]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => setLocal(v[0] ?? min)}
        onValueCommit={(v) => onCommit(v[0] ?? min)}
        className="flex-1"
      />
      <span className="tabular-nums w-10 text-right text-muted-foreground shrink-0">
        {local.toFixed(digits)}
      </span>
    </div>
  );
}

function BpmnTab({ logId }: { logId: string }) {
  // A single "frequency filter" drives the structural pruning: 0 keeps every
  // activity (plain Inductive Miner); raising it re-mines with the Infrequent
  // variant at that noise threshold, dropping the least-used behaviour.
  const [freqFilter, setFreqFilter] = useState(0);
  const algo: BpmnAlgo = freqFilter > 0 ? "imf" : "inductive";

  const { data, isLoading, isError, error, dataUpdatedAt } = useDiscoveryBpmn(
    logId,
    algo,
    freqFilter,
  );
  // Activity / connection frequencies for the visual overlay come from the DFG
  // endpoint and are matched to BPMN tasks by name.
  const dfgQuery = useDiscoveryDfg(logId);
  const freq = useMemo(() => buildFrequencyMaps(dfgQuery.data), [dfgQuery.data]);

  const [decor, setDecor] = useState<BpmnDecor>({
    heatmap: true,
    freqLabels: true,
  });
  const patchDecor = (p: Partial<BpmnDecor>) => setDecor((d) => ({ ...d, ...p }));

  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState<{ q: string; nonce: number }>({ q: "", nonce: 0 });
  const runSearch = () => {
    const q = searchText.trim();
    if (q) setSearch({ q, nonce: Date.now() });
  };

  return (
    <>
      <FilterBar>
        <FilterField label="Frequency filter">
          <CommitSlider value={freqFilter} onCommit={setFreqFilter} max={0.5} width="w-48" />
        </FilterField>
        <FilterField label="Heatmap">
          <Switch checked={decor.heatmap} onCheckedChange={(v) => patchDecor({ heatmap: v })} />
        </FilterField>
        <FilterField label="Labels">
          <Switch checked={decor.freqLabels} onCheckedChange={(v) => patchDecor({ freqLabels: v })} />
        </FilterField>
        <FilterField label="Find">
          <div className="flex items-center gap-1">
            <input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") runSearch();
              }}
              placeholder="Activity name…"
              className="h-7 w-40 rounded-md border bg-background px-2 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            <Button variant="outline" size="icon" className="h-7 w-7 shrink-0" onClick={runSearch}>
              <Search className="h-3.5 w-3.5" />
            </Button>
          </div>
        </FilterField>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="cursor-pointer gap-1.5"
            disabled={!data}
            onClick={() => {
              void downloadBpmn(logId).catch((err) =>
                toast.error(`Download failed: ${(err as Error).message}`),
              );
            }}
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </Button>
        </div>
      </FilterBar>

      {isLoading ? (
        <CanvasSkeleton />
      ) : isError || !data ? (
        <CanvasError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <CanvasFrame>
          {/* Re-mount when the backend payload identity changes so we
              import fresh XML (e.g. frequency-filter re-mine). */}
          <BpmnCanvas
            key={dataUpdatedAt}
            xml={data.xml}
            freq={freq}
            decor={decor}
            searchQuery={search.q}
            searchNonce={search.nonce}
            onSearchResult={(found) => {
              if (!found) toast.error(`No activity matching “${search.q}”`);
            }}
          />
          {decor.heatmap && freq.maxActivity > 0 && (
            <HeatLegend maxActivity={freq.maxActivity} />
          )}
        </CanvasFrame>
      )}
    </>
  );
}

function HeatLegend({ maxActivity }: { maxActivity: number }) {
  const gradient = Array.from({ length: HEAT_BUCKETS }, (_, i) =>
    heatColor((i + 1) / HEAT_BUCKETS).fill,
  ).join(", ");
  return (
    <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-md border bg-card/90 px-2.5 py-1.5 text-[11px] shadow-sm backdrop-blur">
      <span className="text-muted-foreground">Less</span>
      <div
        className="h-2.5 w-24 rounded-sm border"
        style={{ background: `linear-gradient(to right, ${gradient})` }}
      />
      <span className="text-muted-foreground">More used</span>
      <span className="ml-1 tabular-nums text-muted-foreground">· max {maxActivity.toLocaleString()}</span>
    </div>
  );
}
