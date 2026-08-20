"use client";

import { create } from "zustand";
import { EtaTracker } from "@/lib/eta";
import type { JobDetail } from "@/lib/api-types";

/**
 * Live job-state store. Hydrated once by the `JobsProvider` from
 * `GET /api/v1/jobs` on mount, then patched in real time from
 * `WS /api/v1/events?topic=job.*` and from the focused `WS /jobs/{id}/stream`.
 *
 * The Sonner toasts, the bottom-left dock, and the drawer all subscribe to
 * narrow slices of this store via tiny selectors so re-renders stay cheap
 * during high-frequency progress events.
 */

interface JobLive extends JobDetail {
  // Frontend-only adornments
  rate_local?: number | null;
  eta_local?: number | null;
  // Epoch ms of the last `job.started`/`job.progress` tick. Drives the stall
  // hint - a running job that hasn't ticked in a while is likely wedged. Purely
  // client-side (the server stores no last-progress timestamp).
  last_progress_at?: number;
}

interface State {
  byId: Map<string, JobLive>;
  drawerOpen: boolean;
  paused: boolean;
  finishedHidden: boolean;

  setAll: (rows: JobDetail[]) => void;
  upsert: (job: Partial<JobLive> & { id: string }) => void;
  applyEvent: (topic: string, payload: Record<string, unknown>) => void;
  setDrawerOpen: (open: boolean) => void;
  setPaused: (paused: boolean) => void;
  setFinishedHidden: (hidden: boolean) => void;
  remove: (id: string) => void;
  clearFinished: () => void;
}

const trackers = new Map<string, EtaTracker>();

function tracker(id: string): EtaTracker {
  let t = trackers.get(id);
  if (!t) {
    t = new EtaTracker();
    trackers.set(id, t);
  }
  return t;
}

export const useJobsStore = create<State>((set, get) => {
  const finishedHidden = typeof localStorage !== "undefined"
    ? localStorage.getItem("ff.jobs.finishedHidden") === "true"
    : false;

  return {
    byId: new Map(),
    drawerOpen: false,
    paused: false,
    finishedHidden,

  setAll: (rows) => {
    const byId = new Map<string, JobLive>();
    for (const r of rows) byId.set(r.id, { ...r });
    set({ byId });
  },

  upsert: (job) => {
    const byId = new Map(get().byId);
    const prev = byId.get(job.id);
    byId.set(job.id, { ...(prev ?? {}), ...job } as JobLive);
    set({ byId });
  },

  applyEvent: (topic, payload) => {
    const id = (payload.id as string | undefined) ?? "";
    if (!id && topic.startsWith("job.")) return;

    if (topic === "job.queue.paused") {
      set({ paused: true });
      return;
    }
    if (topic === "job.queue.resumed") {
      set({ paused: false });
      return;
    }

    const byId = new Map(get().byId);
    const prev = byId.get(id);

    if (topic === "job.queued") {
      const base: LiveJob = {
        id,
        type: (payload.type as string) ?? "unknown",
        title: (payload.title as string) ?? id,
        subtitle: (payload.subtitle as string | null) ?? null,
        module_id: (payload.module_id as string | null) ?? null,
        payload_json: {},
        status: "queued",
        progress_current: 0,
        progress_total: null,
        stage: null,
        message: null,
        error: null,
        rate: null,
        eta_seconds: null,
        priority: (payload.priority as number) ?? 0,
        parent_job_id: (payload.parent_job_id as string | null) ?? null,
        created_at: new Date().toISOString(),
        started_at: null,
        finished_at: null,
      };
      byId.set(id, { ...base, ...(prev ?? {}), status: "queued" });
    } else if (topic === "job.started") {
      byId.set(id, {
        ...(prev as JobLive),
        id,
        status: "running",
        started_at: prev?.started_at ?? new Date().toISOString(),
        last_progress_at: Date.now(),
      });
      tracker(id).reset();
    } else if (topic === "job.progress") {
      const t = tracker(id);
      const cur = (payload.current as number | undefined) ?? prev?.progress_current ?? 0;
      const total = (payload.total as number | null | undefined) ?? prev?.progress_total ?? null;
      t.observe(cur);
      const localRate = t.ratePerSecond();
      const localEta = t.estimateSeconds(total ?? null);
      byId.set(id, {
        ...(prev as JobLive),
        id,
        status: "running",
        progress_current: cur,
        progress_total: total ?? null,
        stage: (payload.stage as string | null) ?? prev?.stage ?? null,
        message: (payload.message as string | null) ?? prev?.message ?? null,
        rate: (payload.rate as number | null) ?? prev?.rate ?? null,
        eta_seconds: (payload.eta_seconds as number | null) ?? prev?.eta_seconds ?? null,
        rate_local: localRate,
        eta_local: localEta,
        last_progress_at: Date.now(),
      });
    } else if (topic === "job.completed") {
      byId.set(id, {
        ...(prev as JobLive),
        id,
        status: "completed",
        finished_at: new Date().toISOString(),
        progress_current: prev?.progress_total ?? prev?.progress_current ?? 0,
      });
      trackers.delete(id);
    } else if (topic === "job.failed") {
      byId.set(id, {
        ...(prev as JobLive),
        id,
        status: "failed",
        error: (payload.error as string | null) ?? null,
        finished_at: new Date().toISOString(),
      });
      trackers.delete(id);
    } else if (topic === "job.cancelled") {
      byId.delete(id);
      trackers.delete(id);
    } else if (topic === "job.snapshot") {
      // Per-job WS sends an initial snapshot – overwrite cleanly.
      byId.set(id, payload as unknown as JobLive);
    } else if (topic === "job.plan") {
      // The import handler publishes the precompute DAG plan once it knows which
      // modules will run, so the group card can show waiting/skipped steps for a
      // *live* import before its children exist (the parent's payload in the
      // store is otherwise empty until a full `GET /jobs` rehydrate).
      if (!prev) return;
      byId.set(id, {
        ...prev,
        payload_json: {
          ...(prev.payload_json ?? {}),
          precompute_plan: payload.precompute_plan,
        },
      });
    } else {
      return;
    }

    set({ byId });
  },

  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  setPaused: (paused) => set({ paused }),
  setFinishedHidden: (finishedHidden) => {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("ff.jobs.finishedHidden", finishedHidden.toString());
    }
    set({ finishedHidden });
  },

  remove: (id) => {
    const byId = new Map(get().byId);
    byId.delete(id);
    trackers.delete(id);
    set({ byId });
  },

  clearFinished: () => {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("ff.jobs.finishedHidden", "true");
    }
    set({ finishedHidden: true });
  },
  };
});

export type LiveJob = JobLive;

export type JobTypeCategory = "Import" | "Precompute" | "Module" | "Other";

export function categorizeJobType(job: LiveJob): JobTypeCategory {
  const type = job.type.toLowerCase();
  if (type.includes("import")) return "Import";
  if (type.includes("module.")) return "Precompute";
  return "Other";
}

// Job titles use " - " (em-dash) as a separator between a "type" part and a
// "name" part. Which side is which depends on the job kind:
//   "Import - env_permit"      → badge: Import,      name: env_permit
//   "Discovery - precompute"   → badge: precompute,   name: Discovery
// Known type-prefixes (first part) are treated as the badge; everything else
// is assumed to be name-first, type-last.
const TITLE_TYPE_PREFIXES = new Set(["import", "export"]);

export function parseJobTitle(job: LiveJob): { name: string; badge: string } {
  const SEP = " - "; // " - "
  const { title } = job;

  if (!title.includes(SEP)) {
    return { name: title, badge: categorizeJobType(job) };
  }

  const idx = title.indexOf(SEP);
  const first = title.slice(0, idx);
  const rest = title.slice(idx + SEP.length);

  if (TITLE_TYPE_PREFIXES.has(first.toLowerCase())) {
    return { name: rest, badge: first };
  }

  return { name: first, badge: rest };
}

/* -------- selectors -------- */

const ACTIVE = new Set(["queued", "running", "paused"]);
const FINISHED = new Set(["completed", "failed", "cancelled"]);

export const selectActiveJobs = (s: State): LiveJob[] => {
  const out: LiveJob[] = [];
  for (const j of s.byId.values()) if (ACTIVE.has(j.status)) out.push(j);
  return out.sort((a, b) => (b.created_at < a.created_at ? -1 : 1));
};

export const selectFinishedJobs = (s: State): LiveJob[] => {
  if (s.finishedHidden) return [];
  const out: LiveJob[] = [];
  for (const j of s.byId.values()) if (FINISHED.has(j.status)) out.push(j);
  return out.sort((a, b) => (a.finished_at ?? a.created_at) > (b.finished_at ?? b.created_at) ? -1 : 1);
};

/**
 * True if there is a queued/running/paused job for the given (logId, moduleId).
 * Used by the module grid and module panels to disable interactions while a
 * module's job is in flight. Reads `payload_json.log_id` since the JobDetail
 * shape doesn't surface log_id at the top level.
 */
export const hasActiveModuleJob = (
  s: State,
  logId: string,
  moduleId: string,
): boolean => {
  for (const j of s.byId.values()) {
    if (j.module_id !== moduleId) continue;
    if ((j.payload_json as { log_id?: string } | null)?.log_id !== logId) continue;
    if (ACTIVE.has(j.status)) return true;
  }
  return false;
};

export const selectCounts = (s: State) => {
  let running = 0;
  let queued = 0;
  let finished = 0;
  for (const j of s.byId.values()) {
    if (j.status === "running") running++;
    else if (j.status === "queued" || j.status === "paused") queued++;
    else if (FINISHED.has(j.status)) finished++;
  }
  return { running, queued, finished };
};

/**
 * A parent job (e.g. a log import) and the children it spawned (one module job
 * per installed module, linked via `parent_job_id`). Children can be completed
 * while the group is still active, so this is computed over the *full* map, not
 * the active-only selector.
 */
export interface JobGroup {
  parent: LiveJob;
  children: LiveJob[];
  active: boolean;
  /** Steps in a terminal state (terminal job or skipped). */
  done: number;
  /** Total checklist steps. */
  total: number;
  /**
   * The precompute DAG as an ordered checklist, when the parent import job
   * carries a `precompute_plan`. Includes steps whose job doesn't exist yet
   * (`waiting`) or never will (`skipped`, because an upstream failed). `null`
   * when there's no plan – the card falls back to rendering `children`.
   */
  steps: PrecomputeStep[] | null;
}

/** One row in an import group's precompute checklist. */
export interface PrecomputeStep {
  moduleId: string;
  /** The live job row, or `null` for a not-yet-/never-submitted step. */
  job: LiveJob | null;
  state: StepState;
  /** Upstream module-ids this step is still blocked on (for `waiting`). */
  waitingOn: string[];
}

export type StepState =
  | "waiting"
  | "skipped"
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

interface PrecomputePlanNode {
  id: string;
  after?: string[];
}

// A step is "done" (for the N-of-M bar) once it can make no further progress:
// a terminal job, or a `skipped` step whose upstream failed.
const STEP_DONE = new Set<StepState>(["completed", "failed", "cancelled", "skipped"]);

/**
 * Build the precompute checklist from the parent's `precompute_plan` and its
 * live child jobs. Live rows fix a node's state; the rest are resolved to a
 * fixpoint: a chained step is `skipped` once *every* upstream is settled without
 * success (so its `<upstream>.completed` trigger will never fire), otherwise it
 * is `waiting`. Returns `null` when the parent carries no plan.
 */
function buildPrecomputeSteps(parent: LiveJob, children: LiveJob[]): PrecomputeStep[] | null {
  const raw = (parent.payload_json as { precompute_plan?: PrecomputePlanNode[] } | null)
    ?.precompute_plan;
  if (!Array.isArray(raw) || raw.length === 0) return null;

  const childByModule = new Map<string, LiveJob>();
  for (const c of children) if (c.module_id) childByModule.set(c.module_id, c);

  const stateOf = new Map<string, StepState>();
  for (const node of raw) {
    const job = childByModule.get(node.id);
    if (job) stateOf.set(node.id, job.status as StepState);
  }

  const succeeded = (id: string) => stateOf.get(id) === "completed";
  const deadEnd = (id: string) => {
    const st = stateOf.get(id);
    return st === "failed" || st === "cancelled" || st === "skipped";
  };
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of raw) {
      if (stateOf.has(node.id)) continue;
      const after = node.after ?? [];
      if (after.length === 0) continue; // a root awaiting its own job → waiting
      if (after.some(succeeded)) continue; // an upstream succeeded → will be triggered
      if (after.every(deadEnd)) {
        stateOf.set(node.id, "skipped");
        changed = true;
      }
    }
  }

  const steps: PrecomputeStep[] = raw.map((node) => {
    const job = childByModule.get(node.id) ?? null;
    const fixed = stateOf.get(node.id);
    if (fixed === "skipped" && !job) {
      return { moduleId: node.id, job: null, state: "skipped", waitingOn: [] };
    }
    if (job && fixed) {
      return { moduleId: node.id, job, state: fixed, waitingOn: [] };
    }
    const waitingOn = (node.after ?? []).filter((u) => stateOf.get(u) !== "completed");
    return { moduleId: node.id, job: null, state: "waiting", waitingOn };
  });

  // Defensive: surface any real child the plan didn't predict so a running job
  // is never hidden (e.g. a module installed after import).
  const planIds = new Set(raw.map((n) => n.id));
  for (const c of children) {
    if (c.module_id && !planIds.has(c.module_id)) {
      steps.push({ moduleId: c.module_id, job: c, state: c.status as StepState, waitingOn: [] });
    }
  }
  return steps;
}

/** Default stall threshold: a running job silent this long is flagged. */
export const STALL_THRESHOLD_MS = 180_000; // 3 min

/**
 * Seconds a running job has been silent past the threshold, or `null` when it's
 * healthy / not running. Frontend-only: keyed off `last_progress_at`, which only
 * exists once we've seen a live `job.started`/`job.progress` for this job.
 */
export function jobStallSeconds(
  job: LiveJob,
  nowMs: number,
  thresholdMs = STALL_THRESHOLD_MS,
): number | null {
  if (job.status !== "running" || !job.last_progress_at) return null;
  const silentMs = nowMs - job.last_progress_at;
  return silentMs >= thresholdMs ? Math.floor(silentMs / 1000) : null;
}

const byCreatedAsc = (a: LiveJob, b: LiveJob) =>
  a.created_at < b.created_at ? -1 : 1;

/**
 * Partition jobs into parent/child groups + standalone jobs.
 *
 * A job is a group parent iff ≥1 other job points at it via `parent_job_id`.
 * An import job with no children yet (still importing) has no group and renders
 * standalone – it becomes a header the moment its first child is queued.
 *
 * This allocates fresh `JobGroup` wrappers every call, so it must NOT be used
 * as a `useShallow` selector (the new references defeat the comparison and spin
 * `useSyncExternalStore` into an infinite render loop). Subscribe to the stable
 * `byId` map and run this inside a `useMemo` keyed on it instead.
 */
export const selectJobGroups = (
  byId: Map<string, LiveJob>,
): { groups: JobGroup[]; standalone: LiveJob[] } => {
  const childrenByParent = new Map<string, LiveJob[]>();
  for (const j of byId.values()) {
    if (!j.parent_job_id) continue;
    const list = childrenByParent.get(j.parent_job_id);
    if (list) list.push(j);
    else childrenByParent.set(j.parent_job_id, [j]);
  }

  const groups: JobGroup[] = [];
  const standalone: LiveJob[] = [];
  for (const j of byId.values()) {
    if (j.parent_job_id && byId.has(j.parent_job_id)) continue; // a child
    const children = childrenByParent.get(j.id);
    if (!children) {
      standalone.push(j);
      continue;
    }
    children.sort(byCreatedAsc);
    const steps = buildPrecomputeSteps(j, children);
    let done: number;
    let total: number;
    if (steps) {
      total = steps.length;
      done = steps.filter((s) => STEP_DONE.has(s.state)).length;
    } else {
      total = children.length;
      done = children.filter((c) => FINISHED.has(c.status)).length;
    }
    const active = ACTIVE.has(j.status) || done < total;
    groups.push({ parent: j, children, steps, active, done, total });
  }

  groups.sort((a, b) =>
    b.parent.created_at < a.parent.created_at ? -1 : 1,
  );
  standalone.sort((a, b) => (b.created_at < a.created_at ? -1 : 1));
  return { groups, standalone };
};
