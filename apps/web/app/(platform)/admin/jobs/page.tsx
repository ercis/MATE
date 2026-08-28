"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  ChevronDown,
  ChevronRight,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  XCircle,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { TableSkeleton } from "@/components/skeletons";
import { rawFetch } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDuration, formatNumber, formatRelative } from "@/lib/format";

interface AdminJobRow {
  id: string;
  type: string;
  title: string;
  subtitle: string | null;
  module_id: string | null;
  status: string;
  progress_current: number;
  progress_total: number | null;
  stage: string | null;
  message: string | null;
  error: string | null;
  rate: number | null;
  eta_seconds: number | null;
  priority: number;
  parent_job_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  owner_id: string;
  owner_email: string | null;
  owner_username: string | null;
  log_id: string | null;
  log_name: string | null;
  queue_paused: boolean;
}
interface LabelCount {
  label: string;
  count: number;
}
interface AdminJobList {
  total: number;
  items: AdminJobRow[];
  summary: {
    by_status: LabelCount[];
    active_total: number;
    paused_users: string[];
  };
}
interface AdminJobLogLine {
  ts: number;
  level: string;
  event: string;
  fields: Record<string, unknown>;
}
interface AdminJobLogs {
  job_id: string;
  lines: AdminJobLogLine[];
  truncated: boolean;
}

type GroupBy = "user" | "log" | "none";
// "all" / "active" are virtual; anything else is a concrete status filter.
type View = "all" | "active" | string;

const POLL_MS = 2500;
const LIMIT = 200;
const STATUS_ORDER = ["running", "queued", "paused", "failed", "completed", "cancelled"];
const ACTIVE = new Set(["queued", "running"]);

function ownerLabel(r: AdminJobRow): string {
  return r.owner_username || r.owner_email || `${r.owner_id.slice(0, 8)}…`;
}

function pct(r: AdminJobRow): number | null {
  if (!r.progress_total || r.progress_total <= 0) return null;
  return Math.min(100, Math.round((r.progress_current / r.progress_total) * 100));
}

function statusTone(s: string): string {
  switch (s) {
    case "running":
      return "bg-blue-500/15 text-blue-600 dark:text-blue-400";
    case "queued":
      return "bg-amber-500/15 text-amber-600 dark:text-amber-400";
    case "paused":
      return "bg-purple-500/15 text-purple-600 dark:text-purple-400";
    case "completed":
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
    case "failed":
      return "bg-destructive/15 text-destructive";
    default:
      return "bg-muted text-muted-foreground";
  }
}

export default function AdminJobsPage() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [view, setView] = useState<View>("active");
  const [typeFilter, setTypeFilter] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("user");

  const [data, setData] = useState<AdminJobList | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "forbidden" | "error">("loading");
  const inFlight = useRef(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (debouncedQ) p.set("q", debouncedQ);
    if (typeFilter) p.set("type", typeFilter);
    if (view === "active") p.set("active_only", "true");
    else if (view !== "all") p.set("status", view);
    p.set("limit", String(LIMIT));
    return p.toString();
  }, [debouncedQ, typeFilter, view]);

  const load = useCallback(
    async (silent: boolean) => {
      if (inFlight.current) return;
      inFlight.current = true;
      if (!silent) setState((s) => (s === "forbidden" ? s : "loading"));
      try {
        const res = await rawFetch(`/api/v1/admin/jobs?${query}`);
        if (res.status === 403) {
          setState("forbidden");
          return;
        }
        if (!res.ok) throw new Error(String(res.status));
        const json = (await res.json()) as AdminJobList;
        setData(json);
        setState("ready");
      } catch {
        if (!silent) setState("error");
      } finally {
        inFlight.current = false;
      }
    },
    [query],
  );

  // Refetch on filter change (with a loading state)…
  useEffect(() => {
    void load(false);
  }, [load]);
  // …and poll quietly so progress + new jobs stay live without a flicker.
  useEffect(() => {
    const t = setInterval(() => void load(true), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const runAction = useCallback(
    async (path: string, body: Record<string, unknown> | null, okMsg: string) => {
      try {
        const res = await rawFetch(path, { method: "POST", json: body ?? {} });
        if (!res.ok) {
          const detail = (await res.json().catch(() => null)) as { detail?: string } | null;
          toast.error(detail?.detail || `Request failed (${res.status})`);
          return;
        }
        const out = (await res.json().catch(() => null)) as { cancelled?: number } | null;
        toast.success(
          out && typeof out.cancelled === "number" ? `${okMsg} (${out.cancelled})` : okMsg,
        );
        void load(true);
      } catch (e) {
        toast.error((e as Error).message);
      }
    },
    [load],
  );

  const types = useMemo(() => {
    const set = new Set<string>(data?.items.map((i) => i.type) ?? []);
    if (typeFilter) set.add(typeFilter);
    return Array.from(set).sort();
  }, [data, typeFilter]);

  const groups = useMemo(() => buildGroups(data?.items ?? [], groupBy), [data, groupBy]);
  const byStatus = useMemo(
    () => Object.fromEntries((data?.summary.by_status ?? []).map((s) => [s.label, s.count])),
    [data],
  );
  const totalAll = useMemo(
    () => (data?.summary.by_status ?? []).reduce((a, s) => a + s.count, 0),
    [data],
  );

  return (
    <div className="space-y-4">
      {state === "forbidden" ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This view requires the <code>admin</code> role. Ask an administrator to grant it
            in Keycloak (Realm roles → admin).
          </span>
        </div>
      ) : (
        <>
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-48 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search job title, owner email or username…"
                className="pl-8"
              />
            </div>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="cursor-pointer rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            >
              <option value="">All types</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as GroupBy)}
              className="cursor-pointer rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
              aria-label="Group by"
            >
              <option value="user">Group by user</option>
              <option value="log">Group by event log</option>
              <option value="none">No grouping</option>
            </select>
            <Button
              variant="destructive"
              size="sm"
              disabled={!data || data.summary.active_total === 0}
              onClick={() => {
                if (!window.confirm("Cancel ALL active jobs across every user?")) return;
                void runAction("/api/v1/admin/jobs/cancel-all", null, "Cancelled all active jobs");
              }}
            >
              <Ban /> Cancel all
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load(false)}
              title="Refresh now"
            >
              <RefreshCw className={cn(state === "loading" && "animate-spin")} />
            </Button>
          </div>

          {/* Status chips */}
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <Chip active={view === "all"} onClick={() => setView("all")}>
              All <span className="tabular-nums opacity-70">{formatNumber(totalAll)}</span>
            </Chip>
            <Chip active={view === "active"} onClick={() => setView("active")}>
              Active{" "}
              <span className="tabular-nums opacity-70">
                {formatNumber(data?.summary.active_total ?? 0)}
              </span>
            </Chip>
            {STATUS_ORDER.filter((s) => byStatus[s]).map((s) => (
              <Chip key={s} active={view === s} onClick={() => setView(s)}>
                <span className={cn("h-1.5 w-1.5 rounded-full", statusTone(s).split(" ")[0])} />
                {s} <span className="tabular-nums opacity-70">{formatNumber(byStatus[s])}</span>
              </Chip>
            ))}
            {(data?.summary.paused_users.length ?? 0) > 0 ? (
              <span className="ml-auto inline-flex items-center gap-1 text-muted-foreground">
                <Pause className="h-3 w-3" />
                {data!.summary.paused_users.length} paused queue
                {data!.summary.paused_users.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>

          {/* Groups */}
          {state === "loading" && !data ? (
            <TableSkeleton />
          ) : state === "error" ? (
            <div className="rounded-md border border-border px-3 py-8 text-center text-xs text-destructive">
              Failed to load jobs.
            </div>
          ) : groups.length === 0 ? (
            <div className="rounded-md border border-border px-3 py-8 text-center text-xs text-muted-foreground">
              No jobs match.
            </div>
          ) : (
            <div className="space-y-4">
              {groups.map((g) => (
                <section key={g.key} className="space-y-1.5">
                  {groupBy !== "none" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="min-w-0">
                        <span className="font-medium">{g.label}</span>
                        {g.sub ? (
                          <span className="ml-2 text-xs text-muted-foreground">{g.sub}</span>
                        ) : null}
                      </div>
                      <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground tabular-nums">
                        {g.rows.length}
                      </span>
                      {g.activeCount > 0 ? (
                        <span className="rounded bg-blue-500/15 px-1.5 py-0.5 text-xs font-medium text-blue-600 tabular-nums dark:text-blue-400">
                          {g.activeCount} active
                        </span>
                      ) : null}
                      {groupBy === "user" && g.ownerId ? (
                        <div className="ml-auto flex items-center gap-1.5">
                          {g.paused ? (
                            <Button
                              variant="outline"
                              size="xs"
                              onClick={() =>
                                void runAction(
                                  "/api/v1/admin/jobs/queue/resume",
                                  { user_id: g.ownerId },
                                  `Resumed queue for ${g.label}`,
                                )
                              }
                            >
                              <Play /> Resume
                            </Button>
                          ) : (
                            <Button
                              variant="outline"
                              size="xs"
                              onClick={() =>
                                void runAction(
                                  "/api/v1/admin/jobs/queue/pause",
                                  { user_id: g.ownerId },
                                  `Paused queue for ${g.label}`,
                                )
                              }
                            >
                              <Pause /> Pause
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="xs"
                            disabled={g.activeCount === 0}
                            className="text-destructive hover:text-destructive"
                            onClick={() => {
                              if (!window.confirm(`Cancel all active jobs for ${g.label}?`)) return;
                              void runAction(
                                "/api/v1/admin/jobs/cancel-all",
                                { user_id: g.ownerId },
                                `Cancelled active jobs for ${g.label}`,
                              );
                            }}
                          >
                            <Ban /> Cancel active
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  <JobsTable
                    rows={g.rows}
                    hideOwner={groupBy === "user"}
                    hideLog={groupBy === "log"}
                    onCancel={(id) =>
                      void runAction(`/api/v1/admin/jobs/${id}/cancel`, null, "Job cancelled")
                    }
                    onKill={(id) =>
                      void runAction(`/api/v1/admin/jobs/${id}/kill`, null, "Job killed")
                    }
                    onRetry={(id) =>
                      void runAction(`/api/v1/admin/jobs/${id}/retry`, null, "Job re-queued")
                    }
                  />
                </section>
              ))}
            </div>
          )}

          {data && data.total > data.items.length ? (
            <p className="text-xs text-muted-foreground">
              Showing {formatNumber(data.items.length)} of {formatNumber(data.total)} – narrow the
              filters to see the rest.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

interface Group {
  key: string;
  label: string;
  sub?: string;
  ownerId?: string;
  paused?: boolean;
  activeCount: number;
  rows: AdminJobRow[];
}

function buildGroups(items: AdminJobRow[], groupBy: GroupBy): Group[] {
  if (groupBy === "none") {
    if (items.length === 0) return [];
    return [{ key: "all", label: "", activeCount: countActive(items), rows: items }];
  }

  const map = new Map<string, AdminJobRow[]>();
  for (const r of items) {
    const key = groupBy === "user" ? r.owner_id : (r.log_id ?? "__none__");
    (map.get(key) ?? map.set(key, []).get(key)!).push(r);
  }

  const groups: Group[] = [];
  for (const [key, rows] of map) {
    const first = rows[0];
    if (groupBy === "user") {
      const username = first.owner_username;
      const email = first.owner_email;
      groups.push({
        key,
        label: username || email || `${first.owner_id.slice(0, 8)}…`,
        sub: username && email ? email : undefined,
        ownerId: first.owner_id,
        paused: first.queue_paused,
        activeCount: countActive(rows),
        rows,
      });
    } else {
      groups.push({
        key,
        label: key === "__none__" ? "No event log" : first.log_name || `${key.slice(0, 8)}…`,
        activeCount: countActive(rows),
        rows,
      });
    }
  }

  // Groups with running/queued work float to the top; then most jobs first.
  groups.sort((a, b) => b.activeCount - a.activeCount || b.rows.length - a.rows.length);
  return groups;
}

function countActive(rows: AdminJobRow[]): number {
  return rows.reduce((n, r) => n + (ACTIVE.has(r.status) ? 1 : 0), 0);
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex cursor-pointer items-center gap-1 rounded-full border px-2 py-0.5 transition-colors",
        active
          ? "border-primary bg-primary/10 text-foreground"
          : "border-border text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function JobsTable({
  rows,
  hideOwner,
  hideLog,
  onCancel,
  onKill,
  onRetry,
}: {
  rows: AdminJobRow[];
  hideOwner: boolean;
  hideLog: boolean;
  onCancel: (id: string) => void;
  onKill: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  // Full-width logs row spans every visible column: Job + Status + Created +
  // Actions, plus Owner/Event-log when not grouped away.
  const colCount = 4 + (hideOwner ? 0 : 1) + (hideLog ? 0 : 1);
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">Job</th>
            {!hideOwner ? <th className="px-3 py-2 font-medium">Owner</th> : null}
            {!hideLog ? <th className="px-3 py-2 font-medium">Event log</th> : null}
            <th className="w-64 px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Created</th>
            <th className="px-3 py-2 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const active = ACTIVE.has(r.status);
            const p = pct(r);
            const isOpen = expanded.has(r.id);
            return (
              <Fragment key={r.id}>
              <tr className="border-b border-border last:border-0 hover:bg-muted/30">
                <td className="max-w-72 px-3 py-2">
                  <div className="truncate font-medium" title={r.title}>
                    {r.title}
                  </div>
                  <div className="truncate font-mono text-xs text-muted-foreground" title={r.type}>
                    {r.type}
                  </div>
                </td>
                {!hideOwner ? (
                  <td className="px-3 py-2">
                    <div className="truncate" title={r.owner_email ?? r.owner_id}>
                      {ownerLabel(r)}
                    </div>
                  </td>
                ) : null}
                {!hideLog ? (
                  <td className="max-w-48 truncate px-3 py-2 text-muted-foreground" title={r.log_name ?? ""}>
                    {r.log_name ?? "–"}
                  </td>
                ) : null}
                <td className="px-3 py-2">
                  {active && r.status === "running" ? (
                    <div className="space-y-1">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <StatusBadge status={r.status} />
                        {p != null ? <span className="tabular-nums">{p}%</span> : null}
                      </div>
                      {p != null ? <Progress value={p} /> : null}
                      <div className="truncate text-xs text-muted-foreground" title={r.message ?? r.stage ?? ""}>
                        {[r.stage, r.message].filter(Boolean).join(" · ") || "Working…"}
                        {r.eta_seconds ? ` · ETA ${formatDuration(r.eta_seconds)}` : ""}
                      </div>
                    </div>
                  ) : r.status === "failed" && r.error ? (
                    <div className="space-y-0.5">
                      <StatusBadge status={r.status} />
                      <div className="truncate text-xs text-destructive" title={r.error}>
                        {r.error}
                      </div>
                    </div>
                  ) : (
                    <StatusBadge status={r.status} />
                  )}
                </td>
                <td className="px-3 py-2 text-muted-foreground" title={r.created_at}>
                  {formatRelative(r.created_at)}
                </td>
                <td className="px-3 py-2">
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="xs"
                      className="text-muted-foreground"
                      onClick={() => toggle(r.id)}
                      aria-expanded={isOpen}
                      aria-controls={`job-logs-${r.id}`}
                      aria-label={isOpen ? "Hide module logs" : "Show module logs"}
                    >
                      {isOpen ? <ChevronDown /> : <ChevronRight />} Logs
                    </Button>
                    {active ? (
                      <Button
                        variant="ghost"
                        size="xs"
                        className="text-destructive hover:text-destructive"
                        onClick={() => onCancel(r.id)}
                      >
                        <XCircle /> Cancel
                      </Button>
                    ) : null}
                    {r.status === "running" ? (
                      <Button
                        variant="ghost"
                        size="xs"
                        className="text-destructive hover:text-destructive"
                        title="Force-kill: SIGKILL the job's whole process tree now, skipping the cooperative grace window."
                        onClick={() => {
                          if (
                            !window.confirm(
                              "Force-kill this job? It SIGKILLs the whole process tree immediately — use it for a job that won't respond to Cancel.",
                            )
                          )
                            return;
                          onKill(r.id);
                        }}
                      >
                        <Zap /> Kill
                      </Button>
                    ) : null}
                    {r.status === "failed" ? (
                      <Button variant="outline" size="xs" onClick={() => onRetry(r.id)}>
                        <RotateCcw /> Retry
                      </Button>
                    ) : null}
                  </div>
                </td>
              </tr>
              {isOpen ? (
                <tr className="border-b border-border bg-muted/20 last:border-0">
                  <td id={`job-logs-${r.id}`} colSpan={colCount} className="px-3 py-2">
                    <JobLogsPanel jobId={r.id} />
                  </td>
                </tr>
              ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn("inline-block rounded px-1.5 py-0.5 text-xs font-medium", statusTone(status))}>
      {status}
    </span>
  );
}

/**
 * Live tail of a single job's module-log lines (admin-only). Polls the
 * admin endpoint every 2s while the row is expanded - matches the page's poll
 * cadence and never opens an SSE (the admin surface stays request/response, per
 * the cross-user fan-out constraint). Shows the buffered backlog on open, so a
 * job that's been wedged for minutes is inspectable, not just future lines.
 */
function JobLogsPanel({ jobId }: { jobId: string }) {
  const [data, setData] = useState<AdminJobLogs | null>(null);
  const [loaded, setLoaded] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  // Sticky-bottom tail: follow new lines only while the user is parked at the
  // bottom. Scrolling up to read history flips this off so the 2s poll doesn't
  // yank them back down; scrolling back down re-arms it. Starts armed so the
  // first snapshot lands at the newest line.
  const stick = useRef(true);
  const onScroll = () => {
    const el = boxRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await rawFetch(`/api/v1/admin/jobs/${jobId}/logs?limit=500`);
        if (!alive || !res.ok) return;
        const json = (await res.json()) as AdminJobLogs;
        if (!alive) return;
        setData(json);
        setLoaded(true);
      } catch {
        /* transient network blip - keep the last snapshot */
      }
    };
    void load();
    const t = setInterval(() => void load(), 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [jobId]);

  // Pin to the newest line as logs stream in (only while armed; see `stick`).
  useEffect(() => {
    const el = boxRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [data]);

  const lines = data?.lines ?? [];
  return (
    <div className="rounded-md border border-border bg-background">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
        <span className="font-medium">Module logs</span>
        <span className="tabular-nums">
          {lines.length} {lines.length === 1 ? "line" : "lines"}
          {data?.truncated ? " · earlier dropped" : ""}
        </span>
      </div>
      <div
        ref={boxRef}
        onScroll={onScroll}
        className="max-h-72 overflow-auto px-3 py-2 font-mono text-xs leading-relaxed"
      >
        {!loaded ? (
          <div className="text-muted-foreground">Loading…</div>
        ) : lines.length === 0 ? (
          <div className="text-muted-foreground">
            No module logs captured for this job. It may not log during compute, or it ran before
            this build was deployed.
          </div>
        ) : (
          lines.map((ln, i) => <LogLine key={`${ln.ts}-${i}`} line={ln} />)
        )}
      </div>
    </div>
  );
}

function LogLine({ line }: { line: AdminJobLogLine }) {
  const time = new Date(line.ts * 1000).toLocaleTimeString();
  const fieldStr = Object.entries(line.fields)
    .filter(([k]) => k !== "exc_info")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
  return (
    <div className="flex gap-2 whitespace-pre-wrap break-words">
      <span className="shrink-0 tabular-nums text-muted-foreground">{time}</span>
      <span className={cn("shrink-0 font-medium uppercase", logLevelTone(line.level))}>
        {line.level}
      </span>
      <span className="text-foreground">
        {line.event}
        {fieldStr ? <span className="text-muted-foreground"> {fieldStr}</span> : null}
      </span>
    </div>
  );
}

function logLevelTone(level: string): string {
  switch (level) {
    case "error":
      return "text-destructive";
    case "warning":
      return "text-amber-600 dark:text-amber-500";
    case "debug":
      return "text-muted-foreground/70";
    default:
      return "text-muted-foreground";
  }
}
