"use client";

import { useState } from "react";
import { ExternalLink, FileText, FileUp, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

const ACCEPT =
  ".xes,.xes.gz,.csv,.xml,.json,.jsonocel,.xmlocel,.sqlite,.gz,.gzip,.bz2,.xz,.lzma,.zip," +
  "application/xml,text/xml,text/csv,application/json,application/zip,application/gzip";

/** The drop target. Collapses into a one-line file chip once a file is picked. */
export function DropZone({
  file,
  onDrop,
  onClear,
  busy = false,
}: {
  file: File | null;
  onDrop: (file: File) => void;
  onClear: () => void;
  /** While the upload runs the file can't be swapped out. */
  busy?: boolean;
}) {
  const [dragOver, setDragOver] = useState(false);

  if (file) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3">
        <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{file.name}</div>
          <div className="text-xs text-muted-foreground">{formatBytes(file.size)}</div>
        </div>
        {!busy && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onClear}
            className="cursor-pointer"
            aria-label="Remove file"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>
    );
  }

  return (
    <label
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onDrop(f);
      }}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed bg-surface p-12 text-center transition-colors",
        dragOver
          ? "border-primary/60 bg-accent"
          : "border-border hover:border-primary/40 hover:bg-accent/40",
      )}
    >
      <FileUp className="h-8 w-8 text-muted-foreground" />
      <div className="text-sm font-medium">Drop an event log here or click to choose a file</div>
      <div className="text-xs text-muted-foreground">
        Supports XES, CSV, XML, JSON, and OCEL - plain or compressed (.gz, .bz2, .xz, .zip)
      </div>
      <input
        type="file"
        className="sr-only"
        accept={ACCEPT}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onDrop(f);
        }}
      />
    </label>
  );
}

/** Shown under an empty dropzone – a way in for people with no log at hand. */
export function SampleDataHint() {
  return (
    <div className="flex items-center justify-center">
      <a
        href="https://www.processmining.org/event-data.html"
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ExternalLink className="h-3.5 w-3.5" />
        Just exploring? Get public event logs from processmining.org
      </a>
    </div>
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** "log.csv.gz" → "log" – the default display name for an imported log. */
export function displayNameFor(filename: string): string {
  return filename
    .replace(/\.(gz|gzip|bz2|xz|lzma|zip)$/i, "")
    .replace(/\.(xes|csv|xml|json|jsonocel|xmlocel|sqlite)$/i, "");
}
