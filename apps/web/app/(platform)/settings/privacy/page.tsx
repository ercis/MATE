"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { rawFetch } from "@/lib/api";
import { useAnalytics } from "@/lib/stores/analytics";
import {
  useAnalyticsConfig,
  useAnalyticsSummary,
  useUpdateAnalyticsConfig,
  useWipeAnalytics,
  type AnalyticsConfig,
} from "@/lib/analytics-queries";
import { trackCustom } from "@/lib/analytics/client";
import { EV } from "@/lib/analytics/events";

export default function PrivacySettingsPage() {
  const router = useRouter();
  const cfgQuery = useAnalyticsConfig();
  const summaryQuery = useAnalyticsSummary();
  const updateMut = useUpdateAnalyticsConfig();
  const wipeMut = useWipeAnalytics();
  const setStoreEnabled = useAnalytics((s) => s.setEnabled);
  const setAnonId = useAnalytics((s) => s.setAnonUserId);
  const setCaptureFlags = useAnalytics((s) => s.setCaptureFlags);
  const [wipeOpen, setWipeOpen] = useState(false);

  const cfg = cfgQuery.data;

  // `force` mandates tracking – the tab is hidden, so a direct URL hit lands
  // here; bounce to General rather than show controls that can't be changed.
  const forced = cfg?.onboarding_mode === "force";
  useEffect(() => {
    if (forced) router.replace("/settings/general");
  }, [forced, router]);
  if (forced) return null;

  function patch(partial: Partial<AnalyticsConfig>) {
    if (!cfg) return;
    // Single global switch: when tracking is on, capture everything and keep
    // data forever. The granular flags + retention live in the backend
    // config for forward compatibility but are no longer user-configurable.
    const next: AnalyticsConfig = {
      ...cfg,
      ...partial,
      capture_clicks: true,
      capture_perf: true,
      capture_errors: true,
      retention_days: null,
    };
    updateMut.mutate(next, {
      onSuccess: (saved) => {
        setStoreEnabled(saved.enabled);
        setAnonId(saved.anon_user_id_seed);
        setCaptureFlags({
          captureClicks: saved.capture_clicks,
          capturePerf: saved.capture_perf,
          captureErrors: saved.capture_errors,
        });
        if (partial.enabled === true) trackCustom(EV.ANALYTICS_OPT_IN);
        if (partial.enabled === false) trackCustom(EV.ANALYTICS_OPT_OUT);
      },
      onError: (err) => toast.error(`Could not save: ${(err as Error).message}`),
    });
  }

  async function onExport() {
    // A plain `<a href={apiUrl(...)} download>` navigates the browser straight
    // to the API, which omits the bearer token (only `@/lib/api`'s fetch
    // wrappers attach it) and yields a 401. Fetch with auth, then save the blob.
    try {
      const res = await rawFetch("/api/v1/usage/export");
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "analytics-export.ndjson";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(`Export failed: ${(err as Error).message}`);
    }
  }

  async function onWipe() {
    try {
      const res = await wipeMut.mutateAsync();
      setAnonId(res.new_anon_user_id_seed);
      toast.success(
        `Deleted ${res.deleted_events} events and ${res.deleted_sessions} sessions.`,
      );
      setWipeOpen(false);
    } catch (err) {
      toast.error(`Wipe failed: ${(err as Error).message}`);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Behaviour tracking</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Captures which pages you visit, which buttons you click and where
            they take you, how features perform, and - on the server - the
            timing of operations like AI calls and imports plus how your jobs
            finish. Data never leaves your machine - it lives in the same SQLite
            file as your processes. Helps us improve the platform when you
            choose to share your usage during development.
          </p>

          <Label className="flex items-center justify-between gap-3">
            <span className="space-y-0.5">
              <span className="block text-sm font-medium">Enable tracking</span>
              <span className="block text-xs text-muted-foreground">
                Off by default. Toggle any time. When on, clicks, performance,
                and errors are all captured and kept forever (no auto-pruning).
              </span>
            </span>
            <Switch
              checked={!!cfg?.enabled}
              onCheckedChange={(v) => patch({ enabled: v })}
              disabled={!cfg}
              className="cursor-pointer"
            />
          </Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your data</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {summaryQuery.data ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Events" value={summaryQuery.data.total_events} />
              <Stat label="Sessions" value={summaryQuery.data.total_sessions} />
              <Stat
                label="Sessions (30d)"
                value={summaryQuery.data.sessions_last_30d}
              />
              <Stat
                label="Oldest"
                value={
                  summaryQuery.data.oldest_event
                    ? new Date(summaryQuery.data.oldest_event).toLocaleDateString()
                    : "–"
                }
              />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="space-y-1.5">
                  <Skeleton className="h-3 w-16" />
                  <Skeleton className="h-6 w-12" />
                </div>
              ))}
            </div>
          )}

          {summaryQuery.data && summaryQuery.data.by_type.length > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              {summaryQuery.data.by_type.map((t) => (
                <span
                  key={t.event_type}
                  className="rounded-full border border-border bg-muted px-2 py-0.5"
                >
                  {t.event_type}: {t.count}
                </span>
              ))}
            </div>
          )}

          <div className="flex flex-wrap gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!cfg?.enabled}
              onClick={() => void onExport()}
            >
              Export NDJSON
            </Button>
            <AlertDialog open={wipeOpen} onOpenChange={setWipeOpen}>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm">
                  Delete all analytics data
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete all analytics data?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Permanently removes every event and session row and rotates
                    your anonymous id. Cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={(e) => {
                      e.preventDefault();
                      void onWipe();
                    }}
                  >
                    Delete
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Anonymous identifier</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">
            Random uuid used to group events from your installation. Not tied
            to any account or personal data. Deleting all analytics data above
            rotates this id.
          </p>
          {cfg && (
            <code className="block rounded bg-muted px-2 py-1 font-mono text-xs">
              {cfg.anon_user_id_seed.slice(0, 8)}…
            </code>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">What is never captured</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">
          <ul className="list-disc space-y-1 pl-4">
            <li>Form field values</li>
            <li>AI chat message content</li>
            <li>File names or contents of imported processes</li>
            <li>URL query parameters</li>
            <li>Raw user-agent string</li>
            <li>Anything inside a UI element marked <code>data-no-track</code></li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}
