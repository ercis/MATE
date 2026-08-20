"use client";

import { useRef, useState } from "react";
import { HardDriveUpload, Loader2, Lock, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";
import {
  useDeleteModuleModel,
  useModuleModels,
  useUploadModuleModel,
  type ModelStoreManifest,
} from "@/lib/queries";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
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

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Generic "model files" card for modules that declare `model_store` in their
 * manifest. Models are uploaded to platform-shared storage (any user's upload
 * is visible to everyone); the *selection* – which model this account uses –
 * is per-user and persisted into the module config via `onSelect`.
 */
export function ModelStoreCard({
  moduleId,
  store,
  selected,
  onSelect,
  saving = false,
}: {
  moduleId: string;
  store: ModelStoreManifest;
  /** Currently selected model name from the module config draft. */
  selected: string | null;
  /** Persist the selection into the module config (folder name). */
  onSelect: (name: string) => Promise<void> | void;
  saving?: boolean;
}) {
  const modelsQ = useModuleModels(moduleId);
  const upload = useUploadModuleModel(moduleId);
  const remove = useDeleteModuleModel(moduleId);
  const fileRef = useRef<HTMLInputElement>(null);
  const [pendingName, setPendingName] = useState<string | null>(null);

  const title = store.title ?? "Model files";
  const accept = store.accept ?? ".tar.zst";
  const models = modelsQ.data?.models ?? [];
  // Admin pinned one shared model for everyone (Admin → Controls). Selection +
  // deletion go read-only; uploads stay allowed (still platform-additive).
  const locked = modelsQ.data?.locked ?? false;

  const onFilePicked = async (file: File | undefined) => {
    if (!file) return;
    try {
      const res = await upload.mutateAsync(file);
      toast.success(`Installed model "${res.name}"`);
      // First model ever? Select it for this account automatically.
      if (!selected) await onSelect(res.name);
    } catch (e) {
      toastError(`Upload failed: ${(e as Error).message}`);
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onChangeSelection = async (name: string) => {
    setPendingName(name);
    try {
      await onSelect(name);
      toast.success(`Using model "${name}"`);
    } catch (e) {
      toastError(`Failed to select model: ${(e as Error).message}`);
    } finally {
      setPendingName(null);
    }
  };

  const onDelete = async (name: string) => {
    try {
      await remove.mutateAsync(name);
      toast.success(`Deleted model "${name}"`);
    } catch (e) {
      toastError(`Delete failed: ${(e as Error).message}`);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {store.description && (
          <p className="text-sm text-muted-foreground">{store.description}</p>
        )}

        {locked && (
          <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
            <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
            <span>
              The detection model is set by your administrator and applies to
              everyone. You can't change the selection here.
            </span>
          </div>
        )}

        <div className="flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept={accept}
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
            {upload.isPending ? "Uploading…" : "Upload model"}
          </Button>
          <span className="text-xs text-muted-foreground">
            Accepts <code className="rounded bg-muted px-1 py-0.5">{accept}</code> · shared
            platform-wide
          </span>
        </div>

        {modelsQ.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : models.length === 0 ? (
          <p className="rounded-md border border-dashed py-6 text-center text-xs text-muted-foreground">
            No models installed yet. Upload a {accept} archive to get started.
          </p>
        ) : (
          <RadioGroup
            value={(locked ? modelsQ.data?.active : selected) ?? undefined}
            onValueChange={onChangeSelection}
            disabled={locked}
            className="gap-2"
          >
            {models.map((m) => {
              const busy = pendingName === m.name;
              return (
                <div
                  key={m.name}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                >
                  <div className="flex items-center gap-3">
                    <RadioGroupItem
                      id={`model-${m.name}`}
                      value={m.name}
                      disabled={saving || remove.isPending || locked}
                    />
                    <Label
                      htmlFor={`model-${m.name}`}
                      className="cursor-pointer font-mono text-xs"
                    >
                      {m.name}
                    </Label>
                    {m.active && (
                      <Badge variant="secondary" className="h-5 px-1.5 py-0 text-[10px]">
                        in use
                      </Badge>
                    )}
                    {busy && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="tabular-nums text-[11px] text-muted-foreground">
                      {formatBytes(m.size_bytes)}
                    </span>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 cursor-pointer text-muted-foreground hover:text-destructive"
                          disabled={remove.isPending || locked}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Delete model “{m.name}”?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This removes the model from disk for{" "}
                            <strong>every account on the platform</strong>, not just yours.
                            Anyone currently using it will need to pick another model.
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
              );
            })}
          </RadioGroup>
        )}
      </CardContent>
    </Card>
  );
}
