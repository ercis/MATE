"use client";

import { Progress } from "@/components/ui/progress";

/**
 * Upload progress for the model-store uploads (`useUploadModuleModel`).
 *
 * Two phases: a determinate bar while bytes are sent (`progress` 0–99), then an
 * indeterminate pulsing bar labelled "Installing…" once the upload hits 100 and
 * the server is still extracting the ~0.5 GB archive. Renders nothing when no
 * upload is in flight.
 */
export function UploadProgress({
  isPending,
  progress,
}: {
  isPending: boolean;
  progress: number | null;
}) {
  if (!isPending) return null;
  const installing = progress === null || progress >= 100;
  return (
    <div className="space-y-1.5">
      <Progress
        value={installing ? 100 : progress}
        className={installing ? "animate-pulse" : undefined}
      />
      <p className="text-xs text-muted-foreground">
        {installing
          ? "Installing… extracting the archive on the server"
          : `Uploading… ${progress}%`}
      </p>
    </div>
  );
}
