"use client";

import { useEffect, useMemo, useState } from "react";
import { Database, Download, Filter, Network, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { rawFetch } from "@/lib/api";
import {
  exportQueryString,
  useExportFacets,
  useExportPreview,
} from "@/lib/admin-export-queries";
import type {
  EventSource,
  ExportCaseNotion,
  ExportFilters,
  ExportFormat,
} from "@/lib/api-types";
import { downloadBlob } from "@/lib/download";
import { formatNumber } from "@/lib/format";

interface ExportInfo {
  is_admin: boolean;
  user_count: number | null;
  event_count: number | null;
  db_size_bytes: number | null;
}

function formatBytes(n: number | null): string {
  if (n == null) return "–";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

function tsName(base: string, ext: string): string {
  const d = new Date();
  const p = (x: number) => String(x).padStart(2, "0");
  return `${base}-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(
    d.getHours(),
  )}${p(d.getMinutes())}${p(d.getSeconds())}.${ext}`;
}

/** A `datetime-local` value (no zone) → ISO-8601 UTC string for the API. */
function localToIso(v: string): string | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export default function AdminExportPage() {
  const [info, setInfo] = useState<ExportInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await rawFetch("/api/v1/admin/export-info");
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as ExportInfo;
        if (!cancelled) setInfo(data);
      } catch {
        // Treat any failure as "not permitted" rather than leaking detail.
        if (!cancelled)
          setInfo({ is_admin: false, user_count: null, event_count: null, db_size_bytes: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loading = info === null;
  const isAdmin = info?.is_admin === true;

  return (
    <div className="space-y-4">
      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={i}
              className="space-y-3 rounded-xl border border-border bg-card p-5"
            >
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-72" />
              <Skeleton className="h-9 w-full max-w-md rounded-md" />
            </div>
          ))}
        </div>
      ) : !isAdmin ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            These exports require the <code>admin</code> role. Ask an
            administrator to grant it in Keycloak (Realm roles → admin).
          </span>
        </div>
      ) : (
        <>
          <MetadataDbCard info={info} />
          <BehaviorExportCard />
        </>
      )}
    </div>
  );
}

function MetadataDbCard({ info }: { info: ExportInfo | null }) {
  const [busy, setBusy] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Database className="h-4 w-4" />
          Full metadata database
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          A SQLite snapshot containing <strong>every user&apos;s data</strong> -
          accounts, usage analytics, process metadata, and settings. Taken live
          and transactionally consistent.
        </p>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <Stat label="Users" value={info?.user_count ?? "–"} />
          <Stat label="Database size" value={formatBytes(info?.db_size_bytes ?? null)} />
        </div>

        <Button
          onClick={async () => {
            setBusy(true);
            try {
              await downloadBlob("/api/v1/admin/export/metadata-db", tsName("metadata", "db"));
            } catch (err) {
              toast.error(`Export failed: ${(err as Error).message}`);
            } finally {
              setBusy(false);
            }
          }}
          disabled={busy}
          className="cursor-pointer gap-1.5"
        >
          <Download className="h-4 w-4" />
          {busy ? "Preparing…" : "Download metadata database"}
        </Button>
      </CardContent>
    </Card>
  );
}

const SELECT_CLASS =
  "block w-full cursor-pointer rounded-md border border-border bg-surface px-2 py-1.5 text-sm";

function BehaviorExportCard() {
  const facets = useExportFacets();

  const [userIds, setUserIds] = useState<string[]>([]);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [source, setSource] = useState<EventSource | "">("");
  const [eventName, setEventName] = useState("");
  const [pathPrefix, setPathPrefix] = useState("");
  const [startLocal, setStartLocal] = useState("");
  const [endLocal, setEndLocal] = useState("");
  const [format, setFormat] = useState<ExportFormat>("xes");
  const [caseNotion, setCaseNotion] = useState<ExportCaseNotion>("session");
  const [busy, setBusy] = useState(false);

  // The backend filter set takes a single user_id / event_type; when the
  // multiselect has exactly one value we pass it through, otherwise we leave it
  // unfiltered (the preview/export then spans the selection's superset). This
  // keeps the wire contract simple while the UI still offers multiselect.
  const filters: ExportFilters = useMemo(
    () => ({
      user_id: userIds.length === 1 ? userIds[0] : null,
      event_type: eventTypes.length === 1 ? eventTypes[0] : null,
      source: source || null,
      event_name: eventName.trim() || null,
      path_prefix: pathPrefix.trim() || null,
      start: localToIso(startLocal),
      end: localToIso(endLocal),
    }),
    [userIds, eventTypes, source, eventName, pathPrefix, startLocal, endLocal],
  );

  // Debounce the filter set before it drives the live preview query so typing
  // in the text inputs doesn't fire a request per keystroke.
  const [debounced, setDebounced] = useState<ExportFilters>(filters);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(filters), 300);
    return () => clearTimeout(t);
  }, [filters]);

  const preview = useExportPreview(debounced);

  async function runDownload() {
    setBusy(true);
    try {
      const ocelFmt = format.startsWith("ocel-") ? format.slice(5) : null;
      const ext = ocelFmt
        ? `ocel.${ocelFmt}`
        : format === "xes"
          ? "xes"
          : format === "csv"
            ? "csv"
            : "ndjson";
      const route = ocelFmt
        ? "events-ocel2"
        : format === "xes"
          ? "event-log.xes"
          : format === "csv"
            ? "events.csv"
            : "events.ndjson";
      const params: Record<string, string | null | undefined> = { ...filters };
      if (format === "xes") params.case = caseNotion;
      if (ocelFmt) params.fmt = ocelFmt;
      const qs = exportQueryString(params as ExportFilters & { case?: string });
      await downloadBlob(
        `/api/v1/admin/export/${route}${qs ? `?${qs}` : ""}`,
        tsName("events", ext),
      );
    } catch (err) {
      toast.error(`Export failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Network className="h-4 w-4" />
          Behavior export
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Behaviour-tracking events across <strong>all users</strong> - clicks,
          inputs, keyboard, pointer traces, navigation, server-side operations,
          and job outcomes. Filter the set below, preview the match, then
          download as OCEL 2.0 (object-centric UI log), XES, NDJSON, or CSV.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="exp-users">Users</Label>
            <select
              id="exp-users"
              multiple
              value={userIds}
              onChange={(e) =>
                setUserIds(Array.from(e.target.selectedOptions, (o) => o.value))
              }
              className={`${SELECT_CLASS} h-28`}
            >
              {facets.data?.users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.preferred_username || u.email || u.id}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground">
              Select one to filter; leave empty for all.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exp-types">Event types</Label>
            <select
              id="exp-types"
              multiple
              value={eventTypes}
              onChange={(e) =>
                setEventTypes(Array.from(e.target.selectedOptions, (o) => o.value))
              }
              className={`${SELECT_CLASS} h-28`}
            >
              {facets.data?.event_types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground">
              Select one to filter; leave empty for all.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exp-start">From</Label>
            <Input
              id="exp-start"
              type="datetime-local"
              value={startLocal}
              onChange={(e) => setStartLocal(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="exp-end">To</Label>
            <Input
              id="exp-end"
              type="datetime-local"
              value={endLocal}
              onChange={(e) => setEndLocal(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exp-name">Event name</Label>
            <Input
              id="exp-name"
              placeholder="exact match, e.g. ui.click"
              value={eventName}
              onChange={(e) => setEventName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="exp-path">Path prefix</Label>
            <Input
              id="exp-path"
              placeholder="e.g. /processes"
              value={pathPrefix}
              onChange={(e) => setPathPrefix(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exp-source">Source</Label>
            <select
              id="exp-source"
              value={source}
              onChange={(e) => setSource(e.target.value as EventSource | "")}
              className={SELECT_CLASS}
            >
              <option value="">All</option>
              <option value="client">Client (browser)</option>
              <option value="server">Server (backend)</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exp-format">Format</Label>
            <select
              id="exp-format"
              value={format}
              onChange={(e) => setFormat(e.target.value as ExportFormat)}
              className={SELECT_CLASS}
            >
              <option value="ocel-json">OCEL 2.0 JSON (object-centric)</option>
              <option value="ocel-sqlite">OCEL 2.0 SQLite</option>
              <option value="ocel-xml">OCEL 2.0 XML</option>
              <option value="xes">XES (process mining)</option>
              <option value="ndjson">NDJSON</option>
              <option value="csv">CSV</option>
            </select>
          </div>

          {format === "xes" && (
            <div className="space-y-1.5">
              <Label htmlFor="exp-case">Case (trace)</Label>
              <select
                id="exp-case"
                value={caseNotion}
                onChange={(e) => setCaseNotion(e.target.value as ExportCaseNotion)}
                className={SELECT_CLASS}
              >
                <option value="session">Session - one trace per visit</option>
                <option value="user">User - one trace per person</option>
              </select>
            </div>
          )}
        </div>

        <PreviewPanel
          loading={preview.isLoading}
          fetching={preview.isFetching}
          data={preview.data}
        />

        <Button
          onClick={runDownload}
          disabled={busy || (preview.data?.matched_events ?? 0) === 0}
          className="cursor-pointer gap-1.5"
        >
          <Download className="h-4 w-4" />
          {busy ? "Preparing…" : `Download ${format.toUpperCase()}`}
        </Button>
      </CardContent>
    </Card>
  );
}

function PreviewPanel({
  loading,
  fetching,
  data,
}: {
  loading: boolean;
  fetching: boolean;
  data:
    | {
        matched_events: number;
        matched_sessions: number;
        distinct_users: number;
        date_min: string | null;
        date_max: string | null;
        event_types: { label: string; count: number }[];
      }
    | undefined;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Filter className="h-3.5 w-3.5" />
        Live preview {fetching && !loading ? "(updating…)" : ""}
      </div>
      {loading ? (
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="space-y-1.5">
              <Skeleton className="h-3 w-12" />
              <Skeleton className="h-6 w-16" />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <Stat label="Events" value={formatNumber(data?.matched_events ?? 0)} />
            <Stat label="Sessions" value={formatNumber(data?.matched_sessions ?? 0)} />
            <Stat label="Users" value={formatNumber(data?.distinct_users ?? 0)} />
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {data?.date_min && data?.date_max
              ? `Span: ${new Date(data.date_min).toLocaleString()} → ${new Date(
                  data.date_max,
                ).toLocaleString()}`
              : "No events match the current filters."}
          </p>
          {data && data.event_types.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {data.event_types.map((t) => (
                <span
                  key={t.label}
                  className="rounded border border-border bg-card px-1.5 py-0.5 text-[11px]"
                >
                  {t.label}: {formatNumber(t.count)}
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}
