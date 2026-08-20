"use client";

import { useQuery } from "@tanstack/react-query";

import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";

interface Storage {
  fs_total: number;
  fs_used: number;
  fs_free: number;
  by_dir: Record<string, number>;
  data_dir: string;
  modules_dir: string;
}

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[u]}`;
}

export function StorageGauge() {
  const { data, isLoading, isError } = useQuery<Storage>({
    queryKey: ["system", "storage"],
    queryFn: () => api<Storage>("/api/v1/system/storage"),
    staleTime: 30_000,
  });

  if (isLoading) {
    return <div className="text-xs text-muted-foreground">Reading filesystem…</div>;
  }
  if (isError || !data) {
    return <div className="text-xs text-destructive">Could not read storage stats.</div>;
  }
  const pct = data.fs_total ? Math.min(100, Math.round((data.fs_used / data.fs_total) * 100)) : 0;

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs">
          <span className="text-muted-foreground">Filesystem</span>
          <span>
            {bytes(data.fs_used)} / {bytes(data.fs_total)} ({pct}%)
          </span>
        </div>
        <Progress value={pct} />
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-md bg-muted/40 p-2">
          <div className="text-muted-foreground">data/</div>
          <div className="font-mono">{bytes(data.by_dir.data ?? 0)}</div>
          <div className="truncate text-[10px] text-muted-foreground">{data.data_dir}</div>
        </div>
        <div className="rounded-md bg-muted/40 p-2">
          <div className="text-muted-foreground">modules/</div>
          <div className="font-mono">{bytes(data.by_dir.modules ?? 0)}</div>
          <div className="truncate text-[10px] text-muted-foreground">{data.modules_dir}</div>
        </div>
      </div>
    </div>
  );
}
