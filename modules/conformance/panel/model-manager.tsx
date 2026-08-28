"use client";

import { useRef } from "react";
import { HardDriveUpload, Loader2, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
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
import { Skeleton } from "@/components/ui/skeleton";

import {
  useActivateModel,
  useConformanceModels,
  useDeleteModel,
  useUploadModel,
} from "./queries";

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Upload / pick / delete the per-log reference BPMN. A fresh upload becomes the
 * active model and (via `onModelReady`) kicks off a run automatically.
 */
export function ModelManager({
  logId,
  running,
  onRun,
  onModelReady,
}: {
  logId: string;
  running: boolean;
  onRun: () => void;
  /** Called after a new model is uploaded + activated (auto-run trigger). */
  onModelReady: () => void;
}) {
  const modelsQ = useConformanceModels(logId);
  const upload = useUploadModel(logId);
  const remove = useDeleteModel(logId);
  const activate = useActivateModel(logId);
  const fileRef = useRef<HTMLInputElement>(null);

  const models = modelsQ.data?.models ?? [];
  const active = modelsQ.data?.active ?? undefined;

  const onFilePicked = async (file: File | undefined) => {
    if (!file) return;
    try {
      const res = await upload.mutateAsync(file);
      toast.success(`Reference model "${res.name}" ready (${res.tasks} tasks)`);
      onModelReady();
    } catch (e) {
      toast.error(`Upload failed: ${(e as Error).message}`);
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onActivate = async (name: string) => {
    if (name === active) return;
    try {
      await activate.mutateAsync(name);
    } catch (e) {
      toast.error(`Failed to switch model: ${(e as Error).message}`);
    }
  };

  const onDelete = async (name: string) => {
    try {
      await remove.mutateAsync(name);
      toast.success(`Deleted "${name}"`);
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={fileRef}
          type="file"
          accept=".bpmn,.xml"
          className="hidden"
          onChange={(e) => onFilePicked(e.target.files?.[0])}
        />
        <Button
          size="sm"
          variant="outline"
          className="cursor-pointer gap-2"
          disabled={upload.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {upload.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <HardDriveUpload className="h-3.5 w-3.5" />
          )}
          {upload.isPending ? "Uploading…" : "Upload BPMN"}
        </Button>
        {models.length > 0 ? (
          <Button size="sm" className="cursor-pointer gap-2" disabled={running} onClick={onRun}>
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {running ? "Running…" : "Run conformance"}
          </Button>
        ) : null}
        <span className="text-xs text-muted-foreground">Accepts a .bpmn / .xml reference model</span>
      </div>

      {modelsQ.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : models.length === 0 ? null : (
        <RadioGroup
          value={active}
          onValueChange={onActivate}
          className="gap-2"
          disabled={activate.isPending || running}
        >
          {models.map((m) => (
            <div
              key={m.name}
              className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
            >
              <div className="flex min-w-0 items-center gap-3">
                <RadioGroupItem id={`conf-model-${m.name}`} value={m.name} />
                <Label
                  htmlFor={`conf-model-${m.name}`}
                  className="cursor-pointer truncate font-mono text-xs"
                >
                  {m.name}
                </Label>
                {m.active ? (
                  <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                    active
                  </span>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="tabular-nums text-[11px] text-muted-foreground">
                  {formatBytes(m.size_bytes)}
                </span>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 cursor-pointer text-muted-foreground hover:text-destructive"
                      disabled={remove.isPending}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete reference model “{m.name}”?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This removes the reference model for this event log. Any cached conformance
                        results for it are dropped.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={() => onDelete(m.name)}
                        className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        Delete
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </div>
          ))}
        </RadioGroup>
      )}
    </div>
  );
}
