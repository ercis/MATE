"use client";

import { useMemo } from "react";

import { formatNumber } from "@/lib/format";
import { jobProgress } from "@/lib/job-progress";
import { useModuleNames } from "@/lib/queries";
import {
  parseJobTitle,
  selectJobGroups,
  useJobsStore,
  type JobGroup,
  type LiveJob,
  type StepState,
} from "@/lib/stores/jobs";

export type StageState = "pending" | "active" | "done" | "failed" | "skipped";

export interface ImportStage {
  key: string;
  label: string;
  state: StageState;
  /** Right-aligned trailer: byte counts, event counts, "3 / 5 steps", … */
  detail?: string;
  /** 0–100 for a determinate bar; `null` renders the indeterminate pulse. */
  pct?: number | null;
  /** Per-module breakdown, on the "Preparing modules" stage only. */
  modules?: ImportModuleStep[];
}

/** One module of the precompute closure, as its own checklist row. */
export interface ImportModuleStep {
  moduleId: string;
  /** Display name; falls back to the raw id, verbatim. */
  name: string;
  state: StageState;
  /** Right-aligned trailer: "62%", "Queued", "Waiting on Discovery", "Done", … */
  detail: string;
  /** 0–100 for a determinate bar; `null` renders the indeterminate pulse. */
  pct: number | null;
}

export interface ImportStagesInput {
  /** 0–100 while the file is going up, `null` once the request settled. */
  uploadPct: number | null;
  /** True between the last upload byte and the probe's response. */
  probing: boolean;
  /** True once the staged file has been described. */
  staged: boolean;
  /** The import job, once `POST /event-logs` has answered. */
  jobId: string | null;
}

// The backend's ingest stages, in the order dispatch.py emits them. Anything
// unknown (a future stage key) simply doesn't advance the checklist rather than
// breaking it.
const JOB_STAGES = [
  { key: "parsing", label: "Reading data" },
  { key: "normalizing", label: "Processing events" },
  { key: "writing", label: "Saving" },
] as const;

const STAGE_ORDER: Record<string, number> = {
  parsing: 0,
  normalizing: 1,
  writing: 2,
  done: 3,
};

// A precompute step's state mapped onto the checklist's visual vocabulary. A
// cancelled step reads as `skipped` (struck through) rather than as a failure -
// nothing went wrong with the log, that module just won't run.
const STEP_STATE: Record<StepState, StageState> = {
  waiting: "pending",
  queued: "pending",
  running: "active",
  paused: "active",
  completed: "done",
  failed: "failed",
  cancelled: "skipped",
  skipped: "skipped",
};

const STEP_DETAIL: Record<StepState, string> = {
  waiting: "Waiting",
  queued: "Queued",
  running: "",
  paused: "Paused",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  skipped: "Skipped",
};

function toModuleStep(
  moduleId: string,
  name: string,
  state: StepState,
  job: LiveJob | null,
  waitingOnNames: string[],
): ImportModuleStep {
  const progress = job && state === "running" ? jobProgress(job) : null;
  const detail =
    state === "running"
      ? (progress?.label ?? "Working…")
      : state === "waiting" && waitingOnNames.length > 0
        ? `Waiting on ${waitingOnNames.join(", ")}`
        : STEP_DETAIL[state];
  return {
    moduleId,
    name,
    state: STEP_STATE[state] ?? "pending",
    detail,
    pct: progress?.pct ?? null,
  };
}

/**
 * The precompute closure as one row per module, in plan (topological) order.
 * Prefers the parent's `precompute_plan` - that's the only source that knows
 * about steps whose job doesn't exist yet - and falls back to the live children
 * for an older, plan-less import job.
 */
function buildModuleSteps(
  group: JobGroup | null,
  moduleName: (id: string) => string,
): ImportModuleStep[] {
  if (!group) return [];
  if (group.steps) {
    return group.steps.map((step) =>
      toModuleStep(
        step.moduleId,
        moduleName(step.moduleId),
        step.state,
        step.job,
        step.waitingOn.map(moduleName),
      ),
    );
  }
  return group.children.map((child) =>
    toModuleStep(
      child.module_id ?? child.id,
      child.module_id ? moduleName(child.module_id) : parseJobTitle(child).name,
      child.status as StepState,
      child,
      [],
    ),
  );
}

/**
 * The stage bar for "Preparing modules". Settled steps count 1, a running one
 * counts its own fraction - so with 17 modules the bar creeps instead of sitting
 * still for a minute and then jumping 6%.
 */
function modulesPct(steps: ImportModuleStep[], done: number, total: number): number | null {
  if (total <= 0) return null;
  const partial = steps.reduce(
    (sum, s) => (s.state === "active" && s.pct !== null ? sum + s.pct / 100 : sum),
    0,
  );
  return Math.min(100, Math.floor(((done + partial) / total) * 100));
}

/**
 * The import as a linear checklist: upload → read → process → save → modules.
 *
 * Everything after the upload comes from the shared jobs store (hydrated and
 * kept live by `JobsProvider`), so this opens no second stream. Returns the
 * stage list plus the parent job, so the caller can render errors and the
 * finished state without re-deriving them.
 */
export function useImportStages(input: ImportStagesInput): {
  stages: ImportStage[];
  job: LiveJob | null;
  /** Every stage settled and the log's modules are done (or there were none). */
  finished: boolean;
  failed: boolean;
} {
  const byId = useJobsStore((s) => s.byId);
  // Resolved once for the whole checklist, not per row. Shares the
  // `useModules(null)` cache the topbar already primes, so it's a cache hit.
  const moduleName = useModuleNames();
  const { uploadPct, probing, staged, jobId } = input;

  return useMemo(() => {
    const job = jobId ? (byId.get(jobId) ?? null) : null;
    const group = jobId
      ? (selectJobGroups(byId).groups.find((g) => g.parent.id === jobId) ?? null)
      : null;

    const stages: ImportStage[] = [];

    // 1) The upload itself - the only phase the client measures directly.
    stages.push({
      key: "upload",
      label: "Uploading file",
      state: staged || job ? "done" : uploadPct !== null ? "active" : "pending",
      pct: uploadPct,
    });

    // 2) The server describing the staged file (sniff + sample).
    stages.push({
      key: "probe",
      label: "Reading columns",
      state: staged || job ? "done" : probing ? "active" : "pending",
      pct: null,
    });

    if (!jobId) {
      // Before the import is confirmed the remaining work is only a preview.
      for (const stage of JOB_STAGES) {
        stages.push({ key: stage.key, label: stage.label, state: "pending" });
      }
      stages.push({ key: "modules", label: "Preparing modules", state: "pending" });
      return { stages, job, finished: false, failed: false };
    }

    const jobFailed = job?.status === "failed" || job?.status === "cancelled";
    const jobDone = job?.status === "completed";
    const currentIndex = jobDone ? STAGE_ORDER.done : (STAGE_ORDER[job?.stage ?? ""] ?? -1);
    const queued = !job || job.status === "queued";
    const progress = job ? jobProgress(job) : null;

    for (const [index, stage] of JOB_STAGES.entries()) {
      let state: StageState = "pending";
      if (jobFailed && index === Math.max(currentIndex, 0)) state = "failed";
      else if (currentIndex > index || jobDone) state = "done";
      else if (currentIndex === index && !queued) state = "active";

      stages.push({
        key: stage.key,
        label: stage.label,
        state,
        detail:
          state === "active" && progress
            ? stage.key === "parsing" && job?.progress_current
              ? `${formatNumber(job.progress_current)} events`
              : progress.label
            : undefined,
        pct: state === "active" ? (progress?.pct ?? null) : undefined,
      });
    }

    // 3) The precompute closure: the log stays "processing" until it's settled.
    //    One nested row per module, so a long precompute shows *which* module is
    //    working and how far along it is, not just "4 / 17".
    const steps = group?.steps ?? null;
    const hasModules = Boolean(steps?.length || group?.children.length);
    const modules = hasModules ? buildModuleSteps(group, moduleName) : [];
    const modulesDone = hasModules ? group!.done >= group!.total : jobDone;
    stages.push({
      key: "modules",
      label: "Preparing modules",
      state: !hasModules
        ? jobDone
          ? "skipped"
          : "pending"
        : modulesDone
          ? "done"
          : jobDone
            ? "active"
            : "pending",
      detail: hasModules ? `${group!.done} / ${group!.total} steps` : undefined,
      pct: hasModules ? modulesPct(modules, group!.done, group!.total) : undefined,
      modules,
    });

    return {
      stages,
      job,
      finished: jobDone && modulesDone,
      failed: jobFailed,
    };
    // `selectJobGroups` allocates fresh wrappers on every call, so this must be
    // memoised on the stable `byId` map - never used as a shallow selector.
  }, [byId, uploadPct, probing, staged, jobId, moduleName]);
}
