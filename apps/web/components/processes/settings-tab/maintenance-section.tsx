"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { RefreshCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";

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
import type { EventLogDetail } from "@/lib/api-types";
import { useDeleteEventLog, useReimportEventLog } from "@/lib/queries";

import { SectionShell } from "./general-section";

export function MaintenanceSection({ log }: { log: EventLogDetail }) {
  const router = useRouter();
  const reimport = useReimportEventLog();
  const del = useDeleteEventLog();
  const [reimportOpen, setReimportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const importing = log.status === "importing";

  const onReimport = async () => {
    try {
      await reimport.mutateAsync(log.id);
      toast.success("Re-import started");
      setReimportOpen(false);
    } catch (err) {
      toastError(`Re-import failed: ${(err as Error).message}`);
    }
  };

  const onDelete = async () => {
    try {
      await del.mutateAsync(log.id);
      toast.success(`Deleted "${log.name}"`);
      router.push("/processes");
    } catch (err) {
      toastError(`Delete failed: ${(err as Error).message}`);
    }
  };

  return (
    <SectionShell title="Maintenance" description="Operations that affect the underlying parquet files.">
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4 rounded-md border border-border/60 p-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">Re-run import</p>
            <p className="text-xs text-muted-foreground">
              Re-parses the original upload from disk; manual cell edits are preserved on the
              SQLite side but the parquet is rebuilt from scratch. Useful after editing the CSV
              column mapping or fixing the source file.
            </p>
          </div>
          <Button
            variant="outline"
            disabled={importing || !log.source_format}
            onClick={() => setReimportOpen(true)}
            className="cursor-pointer"
          >
            <RefreshCcw className="mr-2 h-3.5 w-3.5" />
            Re-run import
          </Button>
        </div>

        <div className="flex items-start justify-between gap-4 rounded-md border border-destructive/30 p-3">
          <div className="space-y-1">
            <p className="text-sm font-medium text-destructive">Delete event log</p>
            <p className="text-xs text-muted-foreground">
              Removes the parquet files, the original upload, and all cached module results.
              This cannot be undone.
            </p>
          </div>
          <Button
            variant="outline"
            className="cursor-pointer border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 className="mr-2 h-3.5 w-3.5" />
            Delete…
          </Button>
        </div>
      </div>

      <AlertDialog open={reimportOpen} onOpenChange={setReimportOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-run import for &ldquo;{log.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              The original upload on disk is re-parsed from scratch. The log will be marked
              <em> importing </em>and unavailable to open until the new import finishes.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onReimport}
              disabled={reimport.isPending}
              className="cursor-pointer"
            >
              Re-run import
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{log.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              The parquet files and the original upload will be removed from disk. This cannot
              be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SectionShell>
  );
}
