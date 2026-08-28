"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { useCardScope } from "@/lib/dashboards/card-scope";
import {
  panelScope,
  selectDfg,
  selectHeuristics,
  selectNodePositions,
  selectPetri,
  selectProcessTree,
  useVizSettings,
  type DfgRenderSettings,
  type GeneralSettings,
  type HeuristicsRenderSettings,
  type NodePositions,
  type PetriRenderSettings,
  type ProcessTreeRenderSettings,
  type VizKey,
} from "@/lib/stores/visualization-settings";

interface DiscoverySettingsContextValue {
  logId: string;
  moduleId: string;
  /** Which settings bucket these views read and write — see `useCardScope`. */
  scope: string;
}

const DiscoverySettingsContext = createContext<DiscoverySettingsContextValue | null>(null);

export function DiscoverySettingsProvider({
  logId,
  moduleId,
  scope,
  children,
}: {
  logId: string;
  moduleId: string;
  /** Override the settings bucket. Rarely needed: on a dashboard the card
   * supplies it through context automatically. */
  scope?: string;
  children: ReactNode;
}) {
  // Resolution order: an explicit prop, then the dashboard card this is
  // rendered inside (if any), then the module's panel scope.
  //
  // This is what lets a card differ from the panel. `ProcessMap` mounts this
  // provider exactly as the panel does, and used to therefore share the
  // panel's settings with no way to change them from the card; now the card
  // context silently redirects it to its own bucket. The panel passes no
  // `scope` and there is no card context there, so it behaves exactly as
  // before.
  const cardScope = useCardScope();
  const value = useMemo(
    () => ({ logId, moduleId, scope: scope ?? cardScope ?? panelScope(moduleId) }),
    [logId, moduleId, scope, cardScope],
  );
  return <DiscoverySettingsContext.Provider value={value}>{children}</DiscoverySettingsContext.Provider>;
}

function useScope(): DiscoverySettingsContextValue {
  const v = useContext(DiscoverySettingsContext);
  if (!v) throw new Error("Missing <DiscoverySettingsProvider>.");
  return v;
}

// -- General settings --------------------------------------------------------

export function useGeneralSettings(): GeneralSettings {
  return useVizSettings((s) => s.general);
}

export function useGeneralSettingsSetter() {
  return useVizSettings((s) => s.setGeneral);
}

export function useResetGeneralSettings() {
  return useVizSettings((s) => s.resetGeneral);
}

// -- Per-viz settings --------------------------------------------------------

export function useDfgSettings(): [DfgRenderSettings, (patch: Partial<DfgRenderSettings>) => void] {
  const { logId, scope } = useScope();
  const settings = useVizSettings((s) => selectDfg(s, logId, scope));
  const set = useVizSettings((s) => s.setDfg);
  return [settings, (patch) => set(logId, scope, patch)];
}

export function usePetriSettings(): [PetriRenderSettings, (patch: Partial<PetriRenderSettings>) => void] {
  const { logId, scope } = useScope();
  const settings = useVizSettings((s) => selectPetri(s, logId, scope));
  const set = useVizSettings((s) => s.setPetri);
  return [settings, (patch) => set(logId, scope, patch)];
}

export function useProcessTreeSettings(): [
  ProcessTreeRenderSettings,
  (patch: Partial<ProcessTreeRenderSettings>) => void,
] {
  const { logId, scope } = useScope();
  const settings = useVizSettings((s) => selectProcessTree(s, logId, scope));
  const set = useVizSettings((s) => s.setProcessTree);
  return [settings, (patch) => set(logId, scope, patch)];
}

export function useHeuristicsRenderSettings(): [
  HeuristicsRenderSettings,
  (patch: Partial<HeuristicsRenderSettings>) => void,
] {
  const { logId, scope } = useScope();
  const settings = useVizSettings((s) => selectHeuristics(s, logId, scope));
  const set = useVizSettings((s) => s.setHeuristics);
  return [settings, (patch) => set(logId, scope, patch)];
}

// -- Node positions (for draggable canvas state) ----------------------------

export function useNodePositions(viz: VizKey): NodePositions {
  const { logId, scope } = useScope();
  return useVizSettings((s) => selectNodePositions(s, logId, scope, viz));
}

export function usePersistNodePositions(viz: VizKey) {
  const { logId, scope } = useScope();
  const setNodePositions = useVizSettings((s) => s.setNodePositions);
  return (patch: NodePositions) => setNodePositions(logId, scope, viz, patch);
}

export function useResetPositions() {
  const { logId, scope } = useScope();
  const reset = useVizSettings((s) => s.resetPositions);
  return (viz?: VizKey) => reset(logId, scope, viz);
}

// -- Module config (server-side) --------------------------------------------

export interface ModuleConfigPayload {
  config: Record<string, unknown>;
  enabled: boolean;
}

export function useModuleConfig() {
  const { moduleId } = useScope();
  return useQuery<ModuleConfigPayload>({
    queryKey: ["modules", moduleId, "config"],
    queryFn: () => api<ModuleConfigPayload>(`/api/v1/modules/${moduleId}/config`),
  });
}

export function useModuleConfigSchema() {
  const { moduleId } = useScope();
  return useQuery<Record<string, unknown>>({
    queryKey: ["modules", moduleId, "config-schema"],
    queryFn: () => api<Record<string, unknown>>(`/api/v1/modules/${moduleId}/config-schema`),
  });
}

export function useUpdateModuleConfig() {
  const { moduleId } = useScope();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModuleConfigPayload) =>
      api<ModuleConfigPayload>(`/api/v1/modules/${moduleId}/config`, {
        method: "PUT",
        json: payload,
      }),
    onSuccess: () => {
      // Invalidate every namespaced query for this module (`config`,
      // `config-schema`, viz queries). Default `refetchType: "active"` only
      // refetches what's currently mounted – inactive tabs refresh lazily
      // when the user opens them. The previous "all" setting cascaded
      // expensive recomputes (ILP, process-tree) on every save and brought
      // the API down via OOM / recursion errors.
      qc.invalidateQueries({ queryKey: ["modules", moduleId] });
      toast.success("Settings saved");
    },
  });
}

export function useDiscoveryScope(): DiscoverySettingsContextValue {
  return useScope();
}
