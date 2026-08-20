"use client";

import { useEffect } from "react";
import { useProgressRouter } from "@/lib/use-progress-router";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { subscribeBus } from "@/lib/ws";
import { useJobsStore } from "@/lib/stores/jobs";
import { useUi } from "@/lib/stores/ui";
import { queryKeys } from "@/lib/queries";
import type { JobDetail } from "@/lib/api-types";

/**
 * Mounts once in the platform layout. Hydrates the jobs store, subscribes to
 * `WS /events?topic=job.*` for the lifetime of the session, fans events into
 * the store, fires Sonner toasts (debounced), and invalidates TanStack
 * caches that depend on job state (event-logs list when an import completes).
 */
export function JobsProvider() {
  const setAll = useJobsStore((s) => s.setAll);
  const apply = useJobsStore((s) => s.applyEvent);
  const muted = useUi((s) => s.notificationsMuted);
  const qc = useQueryClient();
  const router = useProgressRouter();

  // Initial hydration.
  useEffect(() => {
    let cancelled = false;
    api<JobDetail[]>("/api/v1/jobs?limit=100")
      .then((rows) => {
        if (!cancelled) setAll(rows);
      })
      .catch(() => {
        /* the dock falls back to "no jobs yet" – non-fatal */
      });
    return () => {
      cancelled = true;
    };
  }, [setAll]);

  useEffect(() => {
    const sub = subscribeBus<Record<string, unknown>>(
      ["job.*", "log.imported", "log.ready"],
      (env) => {
        apply(env.topic, env.payload);

        const id = (env.payload.id as string | undefined) ?? "";
        const title = (env.payload.title as string | undefined) ?? id;

        if (env.topic === "log.imported") {
          const fixedCols = env.payload.fixed_columns as string[] | undefined;
          if (fixedCols && fixedCols.length > 0 && !muted) {
            toast.info(
              fixedCols.length === 1
                ? "1 column was automatically fixed during import"
                : `${fixedCols.length} columns were automatically fixed during import`,
              { description: `Mixed-type values coerced to null: ${fixedCols.join(", ")}` },
            );
          }
          return;
        }

        if (env.topic === "log.ready") {
          // The log is now openable – it either had no subscribing modules
          // (`processing` skipped) or every one finished precomputing. This is
          // the success signal, moved off the import job's `job.completed`
          // because that only marks the *parse* done, not module readiness.
          qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
          const logId = env.payload.log_id as string | undefined;
          if (!muted && logId) {
            const name =
              Array.from(useJobsStore.getState().byId.values()).find(
                (j) =>
                  j.type === "event_log.import" &&
                  (j.payload_json as { log_id?: string } | undefined)?.log_id === logId,
              )?.title ?? logId;
            toast.success(`Imported - ${name}`, {
              action: {
                label: "Open",
                onClick: () => router.push(`/processes/${logId}`),
              },
            });
          }
          return;
        }

        if (env.topic === "job.completed") {
          // Refresh anything keyed off the api state. Event-log imports flip a
          // log row from `importing` → `processing`/`ready`, so the /processes
          // table needs to refetch. The "Imported" success toast itself fires on
          // `log.ready` (above), once modules have finished.
          qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
          const type = (env.payload.type as string | undefined) ?? "";
          if (type !== "event_log.import" && !muted) {
            toast.success(`Completed - ${title}`);
          }
          return;
        }

        if (env.topic === "job.failed") {
          // `job.failed` payload has no `title`; look it up from the store
          // (populated when `job.queued` arrived) so we show the job name, not the UUID.
          const storedTitle = useJobsStore.getState().byId.get(id)?.title ?? title;
          const error = (env.payload.error as string | undefined) ?? "";
          toastError(`Failed - ${storedTitle}`, {
            description: error || undefined,
            duration: Number.POSITIVE_INFINITY,
            action: {
              label: "Details",
              onClick: () => useJobsStore.getState().setDrawerOpen(true),
            },
          });
          return;
        }

        if (env.topic === "job.cancelled" && !muted) {
          toast.warning(`Cancelled - ${title}`);
          return;
        }
      },
    );

    return () => sub.close();
  }, [apply, muted, qc, router]);

  return null;
}
