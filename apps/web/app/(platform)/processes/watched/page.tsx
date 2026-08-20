"use client";

import Link from "next/link";
import { useState } from "react";
import {
  ArrowLeft,
  FolderClock,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { EmptyState } from "@/components/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PageContainer,
  PageHeader,
  PageTitle,
  PageDescription,
} from "@/components/page";
import { toastError } from "@/lib/toast";
import {
  useDeleteWatchedFolder,
  useScanWatchedFolder,
  useUpdateWatchedFolder,
  useWatchedFolders,
} from "@/lib/watched-queries";
import type { WatchedFolderSummary } from "@/lib/api-types";

function modeLabel(w: WatchedFolderSummary): string {
  if (w.mode === "interval") {
    const mins = w.interval_seconds ? Math.round(w.interval_seconds / 60) : 0;
    return `Every ${mins} min`;
  }
  if (w.mode === "continuous") return "Automatic";
  return "Manual";
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "active") return "default";
  if (status === "error") return "destructive";
  return "secondary";
}

function fmtTime(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString();
}

export default function WatchedFoldersPage() {
  const { data, isLoading, isError, error } = useWatchedFolders();

  return (
    <PageContainer>
      <PageHeader>
        <div className="space-y-1">
          <Link
            href="/processes"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3 w-3" /> Processes
          </Link>
          <PageTitle>Watched folders</PageTitle>
          <PageDescription>
            Storage locations scanned for new event-log files. New files are imported
            automatically.
          </PageDescription>
        </div>
        <Button asChild className="gap-2 cursor-pointer">
          <Link href="/processes/import">
            <RefreshCw className="h-4 w-4" />
            New watched folder
          </Link>
        </Button>
      </PageHeader>

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <EmptyState
          icon={FolderClock}
          title="Couldn't load watched folders"
          description={(error as Error)?.message ?? "Unknown error"}
        />
      )}

      {!isLoading && !isError && (!data || data.length === 0) && (
        <EmptyState
          icon={FolderClock}
          title="No watched folders yet"
          description="Create one to auto-import event logs that land in a storage location."
          primaryAction={
            <Button asChild className="cursor-pointer">
              <Link href="/processes/import" className="gap-2">
                <RefreshCw className="h-4 w-4" />
                New watched folder
              </Link>
            </Button>
          }
        />
      )}

      {!isLoading && !isError && data && data.length > 0 && (
        <div className="space-y-3">
          {data.map((w) => (
            <WatchCard key={w.id} watch={w} />
          ))}
        </div>
      )}
    </PageContainer>
  );
}

function WatchCard({ watch }: { watch: WatchedFolderSummary }) {
  const scan = useScanWatchedFolder();
  const update = useUpdateWatchedFolder();
  const remove = useDeleteWatchedFolder();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const paused = watch.status === "paused";

  const onScan = async () => {
    try {
      const res = await scan.mutateAsync(watch.id);
      if (res.imported > 0) {
        toast.success(`Imported ${res.imported} new file${res.imported === 1 ? "" : "s"}`);
      } else if (res.failed > 0) {
        toast.warning(`${res.failed} file${res.failed === 1 ? "" : "s"} failed to import`);
      } else {
        toast.success(`Scanned – nothing new (${res.found} file${res.found === 1 ? "" : "s"})`);
      }
    } catch (err: unknown) {
      toastError(`Scan failed: ${(err as Error).message}`);
    }
  };

  const onTogglePause = async () => {
    try {
      await update.mutateAsync({
        id: watch.id,
        patch: { status: paused ? "active" : "paused" },
      });
      toast.success(paused ? "Resumed" : "Paused");
    } catch (err: unknown) {
      toastError((err as Error).message);
    }
  };

  const onDelete = async () => {
    try {
      await remove.mutateAsync(watch.id);
      toast.success("Watched folder deleted");
    } catch (err: unknown) {
      toastError((err as Error).message);
    } finally {
      setConfirmOpen(false);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{watch.name}</span>
            <Badge variant={statusVariant(watch.status)} className="text-[10px] capitalize">
              {watch.status}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              {modeLabel(watch)}
            </Badge>
          </div>
          <div className="truncate font-mono text-xs text-muted-foreground">
            {watch.source_path}
          </div>
          <div className="text-xs text-muted-foreground">
            {watch.imported_count} imported
            {watch.failed_count > 0 && (
              <span className="text-destructive"> · {watch.failed_count} failed</span>
            )}{" "}
            · last scan {fmtTime(watch.last_scanned_at)}
          </div>
          {watch.last_error && (
            <div className="text-xs text-destructive">Error: {watch.last_error}</div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={onScan}
            disabled={scan.isPending}
            className="gap-1.5 cursor-pointer"
          >
            {scan.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Scan now
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onTogglePause}
            disabled={update.isPending}
            className="gap-1.5 cursor-pointer"
            title={paused ? "Resume" : "Pause"}
          >
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setConfirmOpen(true)}
            className="cursor-pointer text-muted-foreground hover:text-destructive"
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{watch.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This stops scanning. Source files and already-imported logs are left untouched.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="cursor-pointer bg-destructive text-white hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
