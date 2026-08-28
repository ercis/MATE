"use client";

import { useCallback, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileDown, Upload, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageContainer } from "@/components/page";
import { ApiError, rawFetch } from "@/lib/api";
import { cn } from "@/lib/cn";
import { downloadBlob } from "@/lib/download";
import { toastError } from "@/lib/toast";
import { useProgressRouter } from "@/lib/use-progress-router";

const ACCEPT_SUFFIXES = [".zip", ".tar", ".tar.gz", ".tgz"];

function hasAcceptedSuffix(name: string) {
  const lowered = name.toLowerCase();
  return ACCEPT_SUFFIXES.some((s) => lowered.endsWith(s));
}

export default function ImportModulePage() {
  const router = useProgressRouter();
  const qc = useQueryClient();

  const onInstalled = useCallback(
    (jobId: string) => {
      toast.success("Module install queued", {
        description: "Track progress in the jobs dock (bottom-left).",
      });
      // Refresh the listings when the user gets back to /modules.
      qc.invalidateQueries({ queryKey: ["modules"] });
      router.push(`/modules?install=${jobId}`);
    },
    [qc, router],
  );

  return (
    <PageContainer className="space-y-6">
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-card/40 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <FileDown className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
          <div className="space-y-0.5">
            <div className="text-sm font-medium">Module authoring guide</div>
            <p className="text-xs text-muted-foreground">The complete guide to building a module</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0 cursor-pointer"
          onClick={() => downloadBlob("/api/v1/modules/readme", "README.md")}
        >
          <FileDown className="mr-1.5 h-3.5 w-3.5" /> Download README
        </Button>
      </div>

      <UploadTab onInstalled={onInstalled} />
    </PageContainer>
  );
}

function UploadTab({ onInstalled }: { onInstalled: (jobId: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const mutation = useMutation({
    mutationFn: async (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      const res = await rawFetch("/api/v1/modules/install", { method: "POST", body: fd });
      if (!res.ok) {
        const text = await res.text();
        let detail: unknown = text;
        try {
          detail = JSON.parse(text);
        } catch {
          /* keep as text */
        }
        throw new ApiError(res.status, detail);
      }
      return (await res.json()) as { job_id: string };
    },
    onSuccess: (r) => onInstalled(r.job_id),
    onError: (err: Error) => toastError(`Upload failed: ${err.message}`),
  });

  const onPick = (f: File | null) => {
    if (!f) return;
    if (!hasAcceptedSuffix(f.name)) {
      toastError(`Unsupported file: ${f.name}. Use ${ACCEPT_SUFFIXES.join(", ")}.`);
      return;
    }
    setFile(f);
  };

  return (
    <Card>
      <CardContent className="space-y-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            onPick(e.dataTransfer.files?.[0] ?? null);
          }}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-border bg-card/40 px-6 py-12 text-center transition-colors",
            drag && "border-primary bg-primary/5",
          )}
        >
          <Upload className="h-6 w-6 text-muted-foreground" />
          <div className="text-sm font-medium">
            {file ? file.name : "Drop a module archive here or click to browse"}
          </div>
          <div className="text-xs text-muted-foreground">
            {ACCEPT_SUFFIXES.join(" · ")}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT_SUFFIXES.join(",")}
            className="hidden"
            onChange={(e) => onPick(e.target.files?.[0] ?? null)}
          />
        </div>

        {file && (
          <div className="flex items-center justify-between gap-3">
            <Button
              variant="ghost"
              size="sm"
              className="cursor-pointer text-muted-foreground"
              onClick={() => setFile(null)}
              disabled={mutation.isPending}
            >
              <X className="mr-1 h-3.5 w-3.5" /> Clear
            </Button>
            <Button
              className="cursor-pointer"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(file)}
            >
              {mutation.isPending ? "Uploading…" : "Install"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
