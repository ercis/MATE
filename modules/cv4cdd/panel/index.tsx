"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Waves,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { rawFetch } from "@/lib/api";

import { useCv4cddResults, type Cv4cddDrift } from "./queries";

// ── Drift-type metadata ───────────────────────────────────────────────────────

const DRIFT_META: Record<
  string,
  { icon: LucideIcon; colour: string; label: string; explainer: string }
> = {
  sudden: {
    icon: Zap,
    colour: "rgb(120,120,120)",
    label: "Sudden",
    explainer: "A single point where the process abruptly changes.",
  },
  gradual: {
    icon: TrendingUp,
    colour: "rgb(30,144,255)",
    label: "Gradual",
    explainer: "Old and new variants co-exist for a while before the switch completes.",
  },
  incremental: {
    icon: TrendingDown,
    colour: "rgb(217,70,239)",
    label: "Incremental",
    explainer: "The process drifts in a series of small adjustments over time.",
  },
  recurring: {
    icon: Waves,
    colour: "rgb(34,211,238)",
    label: "Recurring",
    explainer: "An earlier process version returns and replaces the current one.",
  },
};

// ── Panel ─────────────────────────────────────────────────────────────────────

export function Cv4cddPanel({ logId }: { logId: string; moduleId: string }) {
  const resultsQ = useCv4cddResults(logId);

  // Cache-buster for the <img> src so the browser refetches after each run.
  const [imageNonce] = useState<number>(() => Date.now());

  const data = resultsQ.data;
  const drifts: Cv4cddDrift[] = data?.drifts ?? [];
  const hasResults = Boolean(data?.ran);

  return (
    <div className="space-y-6">
      {resultsQ.isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : !hasResults ? (
        <EmptyState
          icon={Sparkles}
          title="No drifts detected yet"
          description="A fine-tuned computer-vision model scans a similarity-matrix encoding of the log for sudden, gradual, incremental, and recurring concept drifts. Detection runs automatically as a background job – the module tile greys out on the process page while it's in flight."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1.2fr]">
          <DriftsCard
            drifts={drifts}
            threshold={data?.confidence_threshold}
            nWindows={data?.n_windows}
          />
          <ImageCard logId={logId} nonce={imageNonce} />
        </div>
      )}
    </div>
  );
}

// ── Overlay image ─────────────────────────────────────────────────────────────

function ImageCard({ logId, nonce }: { logId: string; nonce: number }) {
  // The /image route is auth-gated (it serves per-user cached PNG bytes), so a
  // plain <img src> can't reach it – the browser won't attach the bearer token
  // and the request 401s. Fetch it through rawFetch (which adds the token) and
  // render the bytes via a blob URL instead.
  const [src, setSrc] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setStatus("loading");
    setSrc(null);

    (async () => {
      try {
        const res = await rawFetch(
          `/api/v1/modules/cv4cdd/image?log_id=${encodeURIComponent(logId)}&t=${nonce}`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
        setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [logId, nonce]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Drift map</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-[11px] text-muted-foreground">
          Pairwise window-similarity matrix (viridis). Detected drifts are
          shown as coloured bounding boxes – the x-axis is time.
        </p>
        <div
          className="relative w-[70%] overflow-hidden rounded-md border bg-muted/30"
          style={{ aspectRatio: "1 / 1" }}
        >
          {status === "error" ? (
            <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">
              <AlertTriangle className="mr-2 h-4 w-4" />
              Image not yet available
            </div>
          ) : status === "loading" || !src ? (
            <Skeleton className="h-full w-full" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={src}
              alt="CV4CDD drift map with bounding boxes"
              className="h-full w-full object-contain"
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Drifts table ──────────────────────────────────────────────────────────────

function DriftsCard({
  drifts,
  threshold,
  nWindows,
}: {
  drifts: Cv4cddDrift[];
  threshold?: number;
  nWindows?: number;
}) {
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const d of drifts) out[d.type] = (out[d.type] ?? 0) + 1;
    return out;
  }, [drifts]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Detected drifts</CardTitle>
        {(threshold !== undefined || nWindows) && (
          <CardAction>
            <span className="text-[10px] tabular-nums text-muted-foreground">
              {threshold !== undefined && `confidence ≥ ${(threshold * 100).toFixed(0)}%`}
              {nWindows ? ` · ${nWindows} windows` : null}
            </span>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {Object.keys(counts).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(counts).map(([type, n]) => {
              const meta = DRIFT_META[type];
              const Icon = meta?.icon ?? Sparkles;
              return (
                <Badge
                  key={type}
                  variant="secondary"
                  className="gap-1 border-0 px-2 py-0.5 text-[10px]"
                  style={{
                    background: `${meta?.colour ?? "var(--muted)"}22`,
                    color: meta?.colour ?? "var(--foreground)",
                  }}
                >
                  <Icon className="h-3 w-3" />
                  {meta?.label ?? type} · {n}
                </Badge>
              );
            })}
          </div>
        )}

        {drifts.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">
            The model didn&apos;t find any drifts above the confidence threshold.
          </p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Type</TableHead>
                  <TableHead className="text-xs">Start</TableHead>
                  <TableHead className="text-xs">End</TableHead>
                  <TableHead className="text-right text-xs">Confidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {drifts.map((d, i) => {
                  const meta = DRIFT_META[d.type];
                  const Icon = meta?.icon ?? Sparkles;
                  return (
                    <TableRow key={`${d.type}-${d.start_window}-${i}`}>
                      <TableCell className="text-xs">
                        <span
                          className="inline-flex items-center gap-1.5 font-medium"
                          style={{ color: meta?.colour }}
                          title={meta?.explainer}
                        >
                          <Icon className="h-3 w-3" />
                          {meta?.label ?? d.type}
                        </span>
                      </TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">
                        {fmtTs(d.start_timestamp)}
                      </TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">
                        {fmtTs(d.end_timestamp)}
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums">
                        {(d.confidence * 100).toFixed(1)}%
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function fmtTs(iso: string): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
