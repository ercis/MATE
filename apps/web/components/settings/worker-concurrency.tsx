"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Slider } from "@/components/ui/slider";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";

interface JobsConfig {
  worker_concurrency: number;
  min: number;
  max: number;
  is_admin: boolean;
}

/**
 * Settings → General → Jobs worker-concurrency control.
 *
 * Reads the live value from `GET /api/v1/system/jobs` (any user) and, for
 * admins, writes it back on commit via `PUT` – the backend resizes the worker
 * pool immediately (running jobs are never interrupted) and persists the value.
 * Non-admins see a read-only slider.
 */
export function WorkerConcurrency() {
  const [config, setConfig] = useState<JobsConfig | null>(null);
  const [value, setValue] = useState(2);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await api<JobsConfig>("/api/v1/system/jobs");
        if (cancelled) return;
        setConfig(data);
        setValue(data.worker_concurrency);
      } catch {
        // Non-fatal: the control just stays in its loading placeholder.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function commit(next: number) {
    if (!config?.is_admin || next === config.worker_concurrency) return;
    setSaving(true);
    try {
      const data = await api<JobsConfig>("/api/v1/system/jobs", {
        method: "PUT",
        json: { worker_concurrency: next },
      });
      setConfig(data);
      setValue(data.worker_concurrency);
      toast.success(`Worker concurrency set to ${data.worker_concurrency}.`);
    } catch (err) {
      // Roll the slider back to the last known-good value.
      setValue(config.worker_concurrency);
      const msg =
        err instanceof ApiError && typeof err.detail === "string"
          ? err.detail
          : (err as Error).message;
      toast.error(`Couldn't update worker concurrency: ${msg}`);
    } finally {
      setSaving(false);
    }
  }

  if (config === null) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-10" />
        </div>
        <Skeleton className="h-2 w-full rounded-full" />
      </div>
    );
  }

  const { min, max, is_admin: isAdmin } = config;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm">
          Parallel workers: <span className="font-medium tabular-nums">{value}</span>
        </span>
        <span className="text-xs text-muted-foreground">
          {min}–{max}
        </span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={1}
        disabled={!isAdmin || saving}
        onValueChange={(v) => setValue(v[0] ?? value)}
        onValueCommit={(v) => void commit(v[0] ?? value)}
        className={isAdmin ? "cursor-pointer" : "cursor-not-allowed"}
      />
      <p className="text-xs text-muted-foreground">
        {isAdmin
          ? "Number of jobs (imports, module computations) the platform runs at once. Changes apply immediately and never interrupt running jobs."
          : "How many jobs the platform runs at once. Only an administrator can change this."}
      </p>
    </div>
  );
}
