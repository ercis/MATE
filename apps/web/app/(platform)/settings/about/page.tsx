"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronRight, Compass, Copy } from "lucide-react";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { toastError } from "@/lib/toast";
import { useTour } from "@/lib/stores/tour";

// Local, tolerant mirror of the API's `DiagnosticsOut` (routes/system.py).
// Kept optional so the page compiles before `make codegen` regenerates
// `lib/api-types.ts` from the new response schema (follow-up).
type DiagnosticsSystem = {
  platform?: string;
  machine?: string;
  processor?: string;
  python_version?: string;
  python_implementation?: string;
  cpu_count_logical?: number | null;
  cpu_count_physical?: number | null;
  memory_total_bytes?: number | null;
  memory_available_bytes?: number | null;
  process_uptime_seconds?: number | null;
  process_started_at?: string | null;
  process_rss_bytes?: number | null;
};

type DiagnosticsVersions = {
  duckdb?: string | null;
  pyarrow?: string | null;
  pandas?: string | null;
  pm4py?: string | null;
  sqlalchemy?: string | null;
  fastapi?: string | null;
  sqlite?: string | null;
};

type DiagnosticsLogs = {
  available?: boolean;
  note?: string | null;
  source?: string;
  capacity?: number;
  line_count?: number;
  byte_count?: number;
  truncated?: boolean;
  lines?: string[];
};

type Diagnostics = {
  platform_version?: string;
  python?: string;
  is_admin?: boolean;
  system?: DiagnosticsSystem;
  versions?: DiagnosticsVersions;
  settings?: Record<string, unknown>;
  modules?: Array<Record<string, unknown>>;
  module_count?: number;
  logs?: DiagnosticsLogs;
};

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  parts.push(`${s % 60}s`);
  return parts.join(" ");
}

export default function AboutPage() {
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const startTour = useTour((s) => s.start);

  const { data, refetch } = useQuery<Diagnostics>({
    queryKey: ["system-diagnostics"],
    queryFn: () => api<Diagnostics>("/api/v1/system/diagnostics"),
    staleTime: 30_000,
  });

  const onCopyDiagnostics = async () => {
    setCopying(true);
    try {
      // Refetch so the copied blob (incl. the live log tail) is current.
      const fresh = await refetch();
      const blob = fresh.data ?? data;
      if (!blob) throw new Error("diagnostics unavailable");
      await navigator.clipboard.writeText(JSON.stringify(blob, null, 2));
      setCopied(true);
      toast.success("Diagnostics copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      toastError(`Could not copy diagnostics: ${(err as Error).message}`);
    } finally {
      setCopying(false);
    }
  };

  const sys = data?.system;
  const versions = data?.versions;
  const logs = data?.logs;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">About PM-MATE</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Stat label="Version" value={data?.platform_version ?? "0.1.1"} />
            <Stat label="License" value="MIT" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Product tour</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <p className="text-xs text-muted-foreground">
            Replay the guided walkthrough of the platform — where your data
            lives, how a log becomes an analysis, and where each module runs.
            About two minutes.
          </p>
          <Button
            variant="outline"
            className="shrink-0 cursor-pointer gap-2"
            onClick={() => startTour()}
          >
            <Compass className="h-4 w-4" />
            Restart product tour
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Diagnostics</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-muted-foreground">
          <p>
            Bundle platform version, system info, installed-module metadata, and
            a recent log tail into one JSON blob to paste into a support thread.
          </p>
          <Button
            variant="outline"
            className="cursor-pointer gap-2"
            disabled={copying}
            onClick={onCopyDiagnostics}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Copied" : copying ? "Copying…" : "Copy diagnostics"}
          </Button>

          {sys && (
            <div className="space-y-2 pt-1">
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                System info
              </p>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
                <Info label="OS" value={sys.platform} />
                <Info label="Machine" value={sys.machine} />
                <Info
                  label="Python"
                  value={
                    sys.python_version
                      ? `${sys.python_version} (${sys.python_implementation ?? "?"})`
                      : undefined
                  }
                />
                <Info
                  label="CPU cores"
                  value={
                    sys.cpu_count_logical != null
                      ? `${sys.cpu_count_logical} logical / ${
                          sys.cpu_count_physical ?? "?"
                        } physical`
                      : undefined
                  }
                />
                <Info label="Memory total" value={formatBytes(sys.memory_total_bytes)} />
                <Info
                  label="Memory available"
                  value={formatBytes(sys.memory_available_bytes)}
                />
                <Info label="Process RSS" value={formatBytes(sys.process_rss_bytes)} />
                <Info label="Uptime" value={formatUptime(sys.process_uptime_seconds)} />
                <Info label="Modules installed" value={data?.module_count?.toString()} />
                <Info label="DuckDB" value={versions?.duckdb} />
                <Info label="pandas" value={versions?.pandas} />
                <Info label="pm4py" value={versions?.pm4py} />
                <Info label="SQLite" value={versions?.sqlite} />
                <Info label="FastAPI" value={versions?.fastapi} />
              </dl>
            </div>
          )}

          {logs && (
            <div className="space-y-2 pt-1">
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Recent logs
              </p>
              {logs.available ? (
                <Collapsible open={logsOpen} onOpenChange={setLogsOpen}>
                  <CollapsibleTrigger asChild>
                    <Button
                      variant="ghost"
                      className="h-7 cursor-pointer gap-1.5 px-2 text-xs"
                    >
                      <ChevronRight
                        className={cn(
                          "h-3.5 w-3.5 transition-transform",
                          logsOpen && "rotate-90",
                        )}
                      />
                      {logsOpen ? "Hide" : "Show"} log tail
                      <span className="text-muted-foreground">
                        ({logs.line_count ?? 0}
                        {logs.truncated ? ` of ~${logs.capacity ?? 500}` : ""} lines)
                      </span>
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    {logs.lines && logs.lines.length > 0 ? (
                      <ScrollArea className="mt-2 h-64 rounded-md border border-border bg-muted/40">
                        <pre className="whitespace-pre-wrap break-words p-3 font-mono text-[11px] leading-relaxed text-foreground/80">
                          {logs.lines.join("\n")}
                        </pre>
                      </ScrollArea>
                    ) : (
                      <p className="mt-2 text-xs">No log lines captured yet.</p>
                    )}
                  </CollapsibleContent>
                </Collapsible>
              ) : (
                <p className="text-xs">
                  {logs.note ?? "The log tail is restricted to admin accounts."}
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Info({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/40 py-0.5">
      <dt className="shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-right text-xs font-medium text-foreground/90">
        {value == null || value === "" ? "—" : value}
      </dd>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const id = label.toLowerCase();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        readOnly
        value={value}
        className="flex h-9 w-full rounded-md border border-input bg-muted px-3 py-1 text-sm shadow-sm cursor-default select-all text-muted-foreground focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
    </div>
  );
}
