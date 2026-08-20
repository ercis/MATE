"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Download, Search, ShieldAlert } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { rawFetch } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { cn } from "@/lib/cn";
import { formatNumber, formatRelative } from "@/lib/format";

interface AdminLogRow {
  id: string;
  name: string;
  owner_id: string;
  owner_email: string | null;
  owner_username: string | null;
  status: string;
  source_format: string | null;
  log_model: string;
  events_count: number | null;
  cases_count: number | null;
  objects_count: number | null;
  date_min: string | null;
  date_max: string | null;
  created_at: string;
  imported_at: string | null;
  folder_id: string | null;
}
interface AdminLogList {
  total: number;
  items: AdminLogRow[];
}

type SortKey = "created_at" | "imported_at" | "name" | "events_count";

const PAGE_SIZE = 50;
const STATUSES = ["", "ready", "importing", "error"];

export default function AdminLogsPage() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<SortKey>("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);

  const [data, setData] = useState<AdminLogList | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "forbidden" | "error">("loading");

  // Debounce the search box; reset to page 1 when the query settles.
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedQ(q);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (debouncedQ) p.set("q", debouncedQ);
    if (status) p.set("status", status);
    p.set("sort", sort);
    p.set("order", order);
    p.set("limit", String(PAGE_SIZE));
    p.set("offset", String(offset));
    return p.toString();
  }, [debouncedQ, status, sort, order, offset]);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    void (async () => {
      try {
        const res = await rawFetch(`/api/v1/admin/insights/event-logs?${query}`);
        if (res.status === 403) {
          if (!cancelled) setState("forbidden");
          return;
        }
        if (!res.ok) throw new Error(String(res.status));
        const json = (await res.json()) as AdminLogList;
        if (!cancelled) {
          setData(json);
          setState("ready");
        }
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [query]);

  function toggleSort(key: SortKey) {
    if (sort === key) {
      setOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSort(key);
      setOrder("desc");
    }
    setOffset(0);
  }

  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      {state === "forbidden" ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This view requires the <code>admin</code> role. Ask an administrator
            to grant it in Keycloak (Realm roles → admin).
          </span>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-48">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search log name, owner email or username…"
                className="pl-8"
              />
            </div>
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
              className="cursor-pointer rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            >
              {STATUSES.map((s) => (
                <option key={s || "all"} value={s}>
                  {s ? s[0].toUpperCase() + s.slice(1) : "All statuses"}
                </option>
              ))}
            </select>
          </div>

          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
                  <SortableTh label="Name" col="name" sort={sort} order={order} onClick={toggleSort} />
                  <th className="px-3 py-2 font-medium">Owner</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Format</th>
                  <th className="px-3 py-2 font-medium">Model</th>
                  <SortableTh
                    label="Events"
                    col="events_count"
                    sort={sort}
                    order={order}
                    onClick={toggleSort}
                    numeric
                  />
                  <th className="px-3 py-2 text-right font-medium">Cases</th>
                  <SortableTh
                    label="Created"
                    col="created_at"
                    sort={sort}
                    order={order}
                    onClick={toggleSort}
                  />
                  <SortableTh
                    label="Imported"
                    col="imported_at"
                    sort={sort}
                    order={order}
                    onClick={toggleSort}
                  />
                  <th className="px-3 py-2 text-right font-medium">
                    <span className="sr-only">Download</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {state === "loading" ? (
                  Array.from({ length: 6 }).map((_, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td colSpan={10} className="px-3 py-2.5">
                        <Skeleton className="h-5 w-full" />
                      </td>
                    </tr>
                  ))
                ) : state === "error" ? (
                  <tr>
                    <td colSpan={10} className="px-3 py-8 text-center text-xs text-destructive">
                      Failed to load event logs.
                    </td>
                  </tr>
                ) : data && data.items.length > 0 ? (
                  data.items.map((row) => (
                    <tr key={row.id} className="border-b border-border last:border-0 hover:bg-muted/30">
                      <td className="max-w-64 truncate px-3 py-2 font-medium" title={row.name}>
                        {row.name}
                      </td>
                      <td className="px-3 py-2">
                        <div className="truncate" title={row.owner_email ?? row.owner_id}>
                          {row.owner_username || row.owner_email || row.owner_id.slice(0, 8)}
                        </div>
                        {row.owner_username && row.owner_email ? (
                          <div className="truncate text-xs text-muted-foreground">
                            {row.owner_email}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-3 py-2">
                        <StatusBadge status={row.status} />
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {row.source_format ?? "–"}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {row.log_model === "object_centric" ? "OCEL" : "Case"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {row.log_model === "object_centric"
                          ? formatNumber(row.objects_count)
                          : formatNumber(row.events_count)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {formatNumber(row.cases_count)}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground" title={row.created_at}>
                        {formatRelative(row.created_at)}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground" title={row.imported_at ?? ""}>
                        {formatRelative(row.imported_at)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() =>
                            downloadBlob(
                              `/api/v1/admin/insights/event-logs/${row.id}/download`,
                              row.source_format ? `${row.name}.${row.source_format}` : row.name,
                            )
                          }
                          title="Download original upload"
                          className="inline-flex cursor-pointer items-center rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                        >
                          <Download className="h-4 w-4" />
                          <span className="sr-only">Download {row.name}</span>
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={10} className="px-3 py-8 text-center text-xs text-muted-foreground">
                      No event logs match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {total === 0 ? "No logs" : `${formatNumber(total)} log${total === 1 ? "" : "s"}`}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                className="cursor-pointer rounded-md border border-border px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Previous
              </button>
              <span>
                Page {page} / {pageCount}
              </span>
              <button
                type="button"
                disabled={page >= pageCount}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
                className="cursor-pointer rounded-md border border-border px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SortableTh({
  label,
  col,
  sort,
  order,
  onClick,
  numeric,
}: {
  label: string;
  col: SortKey;
  sort: SortKey;
  order: "asc" | "desc";
  onClick: (col: SortKey) => void;
  numeric?: boolean;
}) {
  const active = sort === col;
  return (
    <th className={cn("px-3 py-2 font-medium", numeric && "text-right")}>
      <button
        type="button"
        onClick={() => onClick(col)}
        className={cn(
          "inline-flex cursor-pointer items-center gap-1 hover:text-foreground",
          active && "text-foreground",
          numeric && "flex-row-reverse",
        )}
      >
        {label}
        {active ? (
          order === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )
        ) : null}
      </button>
    </th>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "ready"
      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
      : status === "error"
        ? "bg-destructive/15 text-destructive"
        : "bg-muted text-muted-foreground";
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", tone)}>{status}</span>
  );
}
