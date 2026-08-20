"use client";

import { CheckCircle2 } from "lucide-react";

import { ImportForm } from "@/components/processes/import-form";

interface UploadStepProps {
  uploadedLogId: string | null;
  onUploaded: (logId: string) => void;
}

export function UploadStep({ uploadedLogId, onUploaded }: UploadStepProps) {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="space-y-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Upload your first event log</h1>
        <p className="text-sm text-muted-foreground">
          Drop a XES, XES.gz, or CSV file. You can skip this step and import later.
        </p>
      </div>

      {uploadedLogId ? (
        <div className="flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
          <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <div className="space-y-0.5 text-sm">
            <div className="font-medium">Import queued</div>
            <div className="text-xs text-muted-foreground">
              Processing in the background - you can continue setup.
            </div>
          </div>
        </div>
      ) : (
        <ImportForm onSuccess={onUploaded} />
      )}
    </div>
  );
}
