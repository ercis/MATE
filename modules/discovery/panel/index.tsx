"use client";

import { useMemo, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/cn";
import { EmptyState } from "@/components/empty-state";
import { CanvasComputingState } from "@/components/visualizations/canvases/shared/canvas-skeleton";

import { BpmnCanvas, DEFAULT_BPMN_DECOR, type BpmnDecor } from "./canvases/BpmnCanvas";
import { buildFrequencyMaps, heatColor, HEAT_BUCKETS } from "./bpmn-decorate";
import { BpmnCanvasSettings } from "./bpmn-canvas-controls";
import { DfgCanvas } from "./canvases/DfgCanvas";
import { HeuristicsNetCanvas } from "./canvases/HeuristicsNetCanvas";
import { PetriNetCanvas } from "./canvases/PetriNetCanvas";
import { PetriCanvasSettings, type PetriAlgo } from "./petri-canvas-controls";
import { PrefixTreeCanvas } from "./canvases/PrefixTreeCanvas";
import { ProcessTreeCanvas } from "./canvases/ProcessTreeCanvas";
import { ProcessTreeCanvasSettings, type TreeAlgo } from "./process-tree-canvas-controls";
import { DfgDetailsPanel } from "./dfg-details-panel";
import { DiscoverySettingsProvider, useHeuristicsRenderSettings } from "./discovery-settings-context";

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

      </div>

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

/**
 * Every view here is *mined* from the event log - there is no model to upload
 * and nothing to fetch that a user could have forgotten to provide. A bare
 * skeleton over that read as "something is missing", so each view names what is
 * being computed instead. `CanvasComputingState` lives in `apps/web` because
 * Tailwind never scans `modules/**`.
 */
function CanvasSkeleton({ label, description }: { label: string; description?: string }) {
  return <CanvasComputingState title={label} description={description} />;
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

/**
 * Keeps the last non-undefined query result so a canvas stays mounted – and the
 * settings popover the user is interacting with stays open – while the next
 * model is mined. `busy` on the canvas reports the in-flight fetch.
 */
function useSticky<T>(value: T | undefined): T | undefined {
  const ref = useRef<T | undefined>(undefined);
  if (value !== undefined) ref.current = value;
  return ref.current;
}

// --------------------------------------------------------------------------
// Tabs
//
// A tab is just its canvas: every control lives in the canvas control
// cluster's settings popover (`canvas-toolbar.tsx`), so it is reachable in
// fullscreen and the graph runs full-bleed. Never add a filter bar above a
// canvas.
// --------------------------------------------------------------------------

function DfgTab({ logId }: { logId: string }) {
  const { data, isLoading, isError, error } = useDiscoveryDfg(logId);
  const [selected, setSelected] = useState<{ kind: "node" | "edge"; id: string } | null>(null);

  if (isLoading) return <CanvasSkeleton label="Mining the directly-follows graph" />;
  if (isError || !data) return <CanvasError message={(error as Error)?.message ?? "Unknown error"} />;

  return (
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
  );
}

function PetriTab({ logId }: { logId: string }) {
  const [algo, setAlgo] = useState<PetriAlgo>("inductive");
  const [noiseThreshold, setNoiseThreshold] = useState(0.2);

  const alpha = useDiscoveryPetriAlpha(logId);
  const alphaPlus = useDiscoveryPetriAlphaPlus(logId);
  const inductive = useDiscoveryPetriInductive(logId);
  const imf = useDiscoveryPetriImf(logId, noiseThreshold);
  // Opt-in: only fetch (and thus compute) ILP once the user selects it.
  const ilp = useDiscoveryPetriIlp(logId, algo === "ilp");

  const q = algo === "alpha" ? alpha
    : algo === "alpha-plus" ? alphaPlus
    : algo === "imf" ? imf
    : algo === "ilp" ? ilp
    : inductive;
  const shown = useSticky(q.data);

  const settings = (
    <PetriCanvasSettings
      algo={algo}
      onAlgoChange={setAlgo}
      noiseThreshold={noiseThreshold}
      onNoiseThresholdChange={setNoiseThreshold}
    />
  );

  if (q.isError) return <CanvasError message={(q.error as Error)?.message ?? "Unknown error"} />;
  if (!shown) return <CanvasSkeleton label="Mining the Petri net" />;

  return (
    <CanvasFrame>
      <PetriNetCanvas data={shown} settings={settings} busy={q.isFetching} />
    </CanvasFrame>
  );
}

function ProcessTreeTab({ logId }: { logId: string }) {
  const [algo, setAlgo] = useState<TreeAlgo>("inductive");
  const [noiseThreshold, setNoiseThreshold] = useState(0.2);

  const inductive = useDiscoveryProcessTree(logId);
  const imf = useDiscoveryProcessTreeImf(logId, noiseThreshold);

  const q = algo === "imf" ? imf : inductive;
  const shown = useSticky(q.data);

  const settings = (
    <ProcessTreeCanvasSettings
      algo={algo}
      onAlgoChange={setAlgo}
      noiseThreshold={noiseThreshold}
      onNoiseThresholdChange={setNoiseThreshold}
    />
  );

  if (q.isError) return <CanvasError message={(q.error as Error)?.message ?? "Unknown error"} />;
  if (!shown) return <CanvasSkeleton label="Mining the process tree" />;

  return (
    <CanvasFrame>
      <ProcessTreeCanvas data={shown} settings={settings} busy={q.isFetching} />
    </CanvasFrame>
  );
}

function PrefixTreeTab({ logId }: { logId: string }) {
  const { data, isLoading, isError, error } = useDiscoveryPrefixTree(logId);

  if (isLoading) return <CanvasSkeleton label="Building the prefix tree" />;
  if (isError || !data) return <CanvasError message={(error as Error)?.message ?? "Unknown error"} />;

  return (
    <CanvasFrame>
      <PrefixTreeCanvas data={data} />
    </CanvasFrame>
  );
}

function HeuristicsTab({ logId }: { logId: string }) {
  const [heur] = useHeuristicsRenderSettings();

  // Thresholds live entirely client-side (the canvas settings popover writes
  // them). Persisting them to the module /config on every slider drag used to
  // cascade refetches across every discovery query (`refetchType: "all"`),
  // which crashed the inactive ILP miner with OOM and overflowed FastAPI's
  // encoder on deep process trees.
  const queryThresholds: HeuristicsThresholds = {
    dependency_threshold: heur.dependencyThreshold,
    and_threshold: heur.andThreshold,
    loop_two_threshold: heur.loopTwoThreshold,
  };

  const q = useDiscoveryHeuristicsNet(logId, queryThresholds);
  const shown = useSticky(q.data);

  if (q.isError) return <CanvasError message={(q.error as Error)?.message ?? "Unknown error"} />;
  if (!shown) return <CanvasSkeleton label="Mining the heuristics net" />;

  return (
    <CanvasFrame>
      <HeuristicsNetCanvas data={shown} busy={q.isFetching} />
    </CanvasFrame>
  );
}

function BpmnTab({ logId }: { logId: string }) {
  // A single "frequency filter" drives the structural pruning: 0 keeps every
  // activity (plain Inductive Miner); raising it re-mines with the Infrequent
  // variant at that noise threshold, dropping the least-used behaviour.
  const [freqFilter, setFreqFilter] = useState(0);
  const algo: BpmnAlgo = freqFilter > 0 ? "imf" : "inductive";

  const q = useDiscoveryBpmn(logId, algo, freqFilter);
  const shown = useSticky(q.data);

  // Activity / connection frequencies for the visual overlay come from the DFG
  // endpoint and are matched to BPMN tasks by name.
  const dfgQuery = useDiscoveryDfg(logId);
  const freq = useMemo(() => buildFrequencyMaps(dfgQuery.data), [dfgQuery.data]);

  const [decor, setDecor] = useState<BpmnDecor>(DEFAULT_BPMN_DECOR);
  const [search, setSearch] = useState<{ q: string; nonce: number }>({ q: "", nonce: 0 });

  const settings = (
    <BpmnCanvasSettings
      freqFilter={freqFilter}
      onFreqFilterChange={setFreqFilter}
      decor={decor}
      onDecorChange={(patch) => setDecor((d) => ({ ...d, ...patch }))}
      onSearch={(query) => setSearch((s) => ({ q: query, nonce: s.nonce + 1 }))}
      onDownload={() => {
        void downloadBpmn(logId).catch((err) =>
          toast.error(`Download failed: ${(err as Error).message}`),
        );
      }}
      downloadDisabled={!shown}
    />
  );

  if (q.isError) return <CanvasError message={(q.error as Error)?.message ?? "Unknown error"} />;
  if (!shown)
    return (
      <CanvasSkeleton
        label="Mining the BPMN model"
        description="This diagram is derived from your events with the Inductive Miner — there is no model to upload here. The first run on a large log can take a minute; the result is cached afterwards."
      />
    );

  return (
    <>
      <CanvasFrame>
        {/* No `key` on the canvas: it re-imports new XML in place, so a re-mine
            keeps the viewer – and this settings popover – mounted. */}
        <BpmnCanvas
          xml={shown.xml}
          freq={freq}
          decor={decor}
          searchQuery={search.q}
          searchNonce={search.nonce}
          onSearchResult={(found) => {
            if (!found) toast.error(`No activity matching “${search.q}”`);
          }}
          settings={settings}
          // The BPMN model is derived, not dragged – so "reset" here is this
          // view's own state: the structural filter back to the unpruned
          // Inductive Miner and the overlay back to its defaults. The canvas
          // re-frames on top. (The search highlight expires on its own.)
          onReset={() => {
            setFreqFilter(0);
            setDecor(DEFAULT_BPMN_DECOR);
          }}
          busy={q.isFetching}
        />
        {decor.heatmap && freq.maxActivity > 0 && <HeatLegend maxActivity={freq.maxActivity} />}
      </CanvasFrame>
      {/* Plain text, not a link: `next/link` isn't a runtime external, and users
          kept reading this read-only mined diagram as a slot for their own
          model. Naming the module that *does* take an upload is enough. */}
      <p className="text-xs text-muted-foreground">
        Have your own reference model? Upload it in the Conformance module to compare it against
        what actually happened.
      </p>
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
