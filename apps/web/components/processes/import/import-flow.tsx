"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion, type Transition } from "framer-motion";
import { AlertTriangle, Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { useAiConfig } from "@/lib/ai-queries";
import { useImportColumnMapping } from "@/lib/ai-guidance";
import { EV } from "@/lib/analytics/events";
import { useTrack } from "@/lib/analytics/hooks";
import type { LogProbeResponse } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { EtaTracker } from "@/lib/eta";
import { formatDuration } from "@/lib/format";
import { useEventLog, useImportEventLog, useStageUpload } from "@/lib/queries";
import { toastError } from "@/lib/toast";
import {
  displayNameFor,
  DropZone,
  formatBytes,
  SampleDataHint,
} from "@/components/processes/import/file-step";
import { ImportProgress } from "@/components/processes/import/import-progress";
import {
  hasLowConfidence,
  initialMapping,
  MappingStep,
  missingRequiredRoles,
  type MappingState,
} from "@/components/processes/import/mapping-step";
import { isLowConfidence, REQUIRED_ROLES } from "@/components/processes/import/role-card";
import { useImportStages } from "@/components/processes/import/use-import-stages";

/**
 * The import, as three deliberate steps:
 *
 *   choose → (upload once, server reads the columns) → confirm mapping → live import
 *
 * The file is uploaded exactly once, to the staging area, which is what buys
 * both a real byte-progress bar and a mapping step that works for formats the
 * browser can't read itself (XES, anything compressed). Confirming the mapping
 * hands the staging token back, which is when the log row and its job appear.
 */

type Phase = "idle" | "uploading" | "mapping" | "running";

const MOTION: Transition = { duration: 0.18, ease: [0.2, 0, 0, 1] };

interface ImportFlowProps {
  /** Set by the onboarding wizard: it owns the post-queue screen, so the flow
   *  hands off as soon as the import is queued instead of showing its own. */
  onSuccess?: (logId: string) => void;
}

export function ImportFlow({ onSuccess }: ImportFlowProps = {}) {
  const reduceMotion = useReducedMotion();
  const track = useTrack();
  const stage = useStageUpload();
  const importer = useImportEventLog();
  const { data: aiConfig } = useAiConfig();
  const aiMapping = useImportColumnMapping();
  const aiConfigured = Boolean(aiConfig?.selected_provider && aiConfig?.selected_model);

  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [probe, setProbe] = useState<LogProbeResponse | null>(null);
  const [mapping, setMapping] = useState<MappingState | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [uploadEta, setUploadEta] = useState<number | null>(null);
  const [logId, setLogId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aiSuggested, setAiSuggested] = useState<Set<string>>(new Set());

  const etaRef = useRef(new EtaTracker());

  // Poll the log row while it walks importing → processing → ready. The store
  // drives the live stages; this is what gives us the final counts.
  const { data: log } = useEventLog(phase === "running" ? logId : null);

  const { stages, finished, failed } = useImportStages({
    uploadPct: stage.progress,
    probing: stage.isPending && (stage.progress ?? 0) >= 100,
    staged: probe !== null,
    jobId,
  });

  // Upload ETA from the byte stream. Reset per attempt so a retry doesn't
  // inherit the previous file's rate.
  useEffect(() => {
    if (stage.progress === null) {
      etaRef.current.reset();
      setUploadEta(null);
      return;
    }
    etaRef.current.observe(stage.progress);
    setUploadEta(etaRef.current.estimateSeconds(100));
  }, [stage.progress]);

  const reset = useCallback(() => {
    setPhase("idle");
    setFile(null);
    setName("");
    setProbe(null);
    setMapping(null);
    setConfirmed(false);
    setLogId(null);
    setJobId(null);
    setError(null);
    etaRef.current.reset();
  }, []);

  // Asks the configured AI provider about the roles the resolver guessed, using
  // the probe's own column samples as evidence. Only fills low-confidence roles,
  // never overwrites an exact match or a choice the user already made.
  const suggestWithAi = useCallback(
    async (result: LogProbeResponse, base: MappingState) => {
      const headers = result.columns.map((c) => c.name);
      const depth = Math.max(...result.columns.map((c) => c.samples.length), 0);
      const sampleRows = Array.from({ length: depth }, (_, row) =>
        result.columns.map((c) => c.samples[row] ?? ""),
      );
      try {
        const res = await aiMapping.mutateAsync({ headers, sample_rows: sampleRows });
        const filled = new Set<string>();
        setMapping((current) => {
          if (!current) return current;
          const roles = { ...current.roles };
          const quality = { ...current.quality };
          for (const role of REQUIRED_ROLES) {
            const suggestion = res.suggestions[role as keyof typeof res.suggestions];
            if (!suggestion || !headers.includes(suggestion)) continue;
            if (!isLowConfidence(base.quality[role])) continue;
            if (roles[role] === suggestion) continue;
            roles[role] = suggestion;
            delete quality[role];
            filled.add(role);
          }
          return filled.size > 0 ? { ...current, roles, quality } : current;
        });
        if (filled.size > 0) setAiSuggested(filled);
      } catch {
        // Silent: the resolver's mapping stands and the user can fix it here.
      }
    },
    [aiMapping],
  );

  const onDrop = useCallback(
    async (picked: File) => {
      setError(null);
      setFile(picked);
      setName((current) => current || displayNameFor(picked.name));
      setPhase("uploading");
      track(EV.PROCESS_IMPORT_STARTED, { source: "file", size: picked.size });

      try {
        const result = await stage.mutateAsync(picked);
        setProbe(result);
        const base = initialMapping(result);
        setMapping(base);
        setAiSuggested(new Set());
        // The step always needs the explicit confirmation click, even when
        // every role matched exactly.
        setConfirmed(false);
        setPhase("mapping");

        // Best-effort AI second opinion, but only where the resolver was
        // unsure - an exact header match needs no help. Failures stay silent:
        // the mapping the user sees is already usable.
        if (aiConfigured && result.needs_mapping && hasLowConfidence(base)) {
          void suggestWithAi(result, base);
        }
      } catch (err: unknown) {
        const message = (err as Error).message || "Upload failed";
        setError(message);
        setPhase("idle");
        setFile(null);
        toastError(`Could not read that file: ${message}`);
      }
    },
    [aiConfigured, stage, suggestWithAi, track],
  );

  const submit = useCallback(async () => {
    if (!probe || !mapping) return;
    setError(null);
    try {
      const resp = await importer.mutateAsync({
        stagingToken: probe.staging_token,
        name: name || probe.filename || undefined,
        columnRoles: probe.needs_mapping ? mapping.roles : undefined,
        ...parserMappingFor(probe, mapping),
      });
      track(EV.PROCESS_IMPORT_FINISHED, { source: "file", format: probe.source_format, ok: true });
      setLogId(resp.log_id);
      setJobId(resp.job_id);
      if (onSuccess) {
        onSuccess(resp.log_id);
        return;
      }
      setPhase("running");
    } catch (err: unknown) {
      const message = (err as Error).message || "Import failed";
      track(EV.PROCESS_IMPORT_FINISHED, { source: "file", format: probe.source_format, ok: false });
      setError(message);
      toastError(`Import failed: ${message}`);
    }
  }, [importer, mapping, name, onSuccess, probe, track]);

  const confirm = useCallback(() => {
    if (!mapping) return;
    setConfirmed(true);
    track(EV.PROCESS_IMPORT_MAPPING_CONFIRMED, {
      changed_roles: Object.values(mapping.quality).filter((q) => q === "user").length,
      had_low_confidence: hasLowConfidence(mapping),
    });
  }, [mapping, track]);

  const canImport = Boolean(
    confirmed && mapping && (!probe?.needs_mapping || missingRequiredRoles(mapping).length === 0),
  );

  const enter = useMemo(
    () => ({
      initial: { opacity: 0, y: reduceMotion ? 0 : 8 },
      animate: { opacity: 1, y: 0 },
      exit: { opacity: 0, y: reduceMotion ? 0 : -8 },
      transition: reduceMotion ? { duration: 0 } : MOTION,
    }),
    [reduceMotion],
  );

  return (
    <div className="space-y-6">
      {phase !== "running" && (
        <DropZone
          file={file}
          onDrop={onDrop}
          onClear={reset}
          busy={phase === "uploading" || importer.isPending}
        />
      )}
      {phase === "idle" && !file && <SampleDataHint />}

      <AnimatePresence mode="wait" initial={false}>
        {phase === "uploading" && (
          <motion.div key="uploading" {...enter}>
            <Card>
              <CardContent>
                <UploadPanel
                  file={file}
                  pct={stage.progress}
                  eta={uploadEta}
                  reading={(stage.progress ?? 0) >= 100}
                />
              </CardContent>
            </Card>
          </motion.div>
        )}

        {phase === "mapping" && probe && mapping && (
          <motion.div key="mapping" {...enter}>
            <Card>
              <CardContent className="space-y-5">
                <div className="grid gap-2">
                  <Label htmlFor="display-name">Display name</Label>
                  <Input
                    id="display-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={probe.filename ?? "Event log"}
                  />
                </div>

                <MappingStep
                  probe={probe}
                  mapping={mapping}
                  onChange={(next) => {
                    setMapping(next);
                    // Any edit re-opens the gate - the user confirms what's on
                    // screen now, not what they saw a minute ago.
                    setConfirmed(false);
                  }}
                  confirmed={confirmed}
                  onConfirm={confirm}
                  aiPending={aiMapping.isPending}
                  aiSuggested={aiSuggested}
                />

                {error && (
                  <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                <div className="flex justify-end gap-2 border-t border-border pt-4">
                  <Button variant="outline" onClick={reset} className="cursor-pointer">
                    Cancel
                  </Button>
                  <Button
                    onClick={submit}
                    disabled={!canImport || importer.isPending}
                    className="cursor-pointer gap-2"
                  >
                    {importer.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    Start import
                  </Button>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        )}

        {phase === "running" && (
          <motion.div key="running" {...enter}>
            <Card>
              <CardContent>
                <ImportProgress
                  stages={stages}
                  fileName={probe?.filename ?? file?.name ?? "Event log"}
                  error={failed ? (log?.error ?? "The import job failed.") : error}
                  log={finished ? (log ?? null) : null}
                  logId={logId}
                  onRetry={reset}
                />
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** The live upload: the one phase whose progress the browser can measure. */
function UploadPanel({
  file,
  pct,
  eta,
  reading,
}: {
  file: File | null;
  pct: number | null;
  eta: number | null;
  reading: boolean;
}) {
  const sent = file && pct !== null ? (file.size * pct) / 100 : 0;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">
          {reading ? "Reading the file…" : "Uploading…"}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {reading
            ? "Detecting format and columns"
            : file
              ? `${formatBytes(sent)} / ${formatBytes(file.size)}${
                  eta !== null && Number.isFinite(eta) && eta > 1
                    ? ` · ~${formatDuration(eta)} left`
                    : ""
                }`
              : null}
        </span>
      </div>
      <Progress
        value={reading ? undefined : (pct ?? 0)}
        className={cn("h-1.5", reading && "animate-pulse")}
      />
      <p className="text-xs text-muted-foreground">
        {reading
          ? "We sample the first few hundred events to work out what each column means."
          : "Large logs take a moment - the column check comes next."}
      </p>
    </div>
  );
}

/**
 * Format-specific parser settings that a role mapping can't carry: the CSV
 * delimiter, the XML event element, the JSON array key, the timestamp format.
 * Roles themselves always travel as `column_roles` so the confirmed mapping is
 * recorded as the user's choice rather than a guess.
 */
function parserMappingFor(probe: LogProbeResponse, mapping: MappingState) {
  if (!probe.needs_mapping) return {};
  const roles = mapping.roles;
  const required = roles.case_id && roles.activity && roles.timestamp;
  if (!required) return {};

  const shared = {
    case_id: roles.case_id,
    activity: roles.activity,
    timestamp: roles.timestamp,
    end_timestamp: roles.end_timestamp || undefined,
    resource: roles.resource || undefined,
    cost: roles.cost || undefined,
    timestamp_format: mapping.timestampFormat || undefined,
  };

  if (probe.source_format === "csv") {
    return { csvMapping: { ...shared, delimiter: mapping.delimiter } };
  }
  if (probe.source_format === "xml" && mapping.eventElement) {
    return { xmlMapping: { ...shared, event_element: mapping.eventElement } };
  }
  if (probe.source_format === "json") {
    return { jsonMapping: { ...shared, event_path: mapping.eventPath || undefined } };
  }
  // XES (and XES-shaped XML) need no parser mapping at all - the parser knows
  // its own canonical schema and `column_roles` covers the rest.
  return {};
}
