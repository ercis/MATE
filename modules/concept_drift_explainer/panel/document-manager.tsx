"use client";

import { useRef } from "react";
import { FileText, Trash2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";

import {
  useCdeDocuments,
  useDeleteDocument,
  useRunIngest,
  useUploadDocument,
} from "./queries";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentManager({ logId }: { logId: string }) {
  const docsQ = useCdeDocuments(logId);
  const upload = useUploadDocument(logId);
  const remove = useDeleteDocument(logId);
  const ingest = useRunIngest(logId);
  const inputRef = useRef<HTMLInputElement>(null);

  const documents = docsQ.data?.documents ?? [];
  const hasIndexable = documents.some((d) => d.indexable);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.txt,.pptx,.png,.jpg,.jpeg"
          multiple
          className="hidden"
          onChange={async (e) => {
            const files = Array.from(e.target.files ?? []);
            for (const f of files) {
              try {
                await upload.mutateAsync(f);
              } catch (err) {
                console.error("upload failed", f.name, err);
              }
            }
            if (inputRef.current) inputRef.current.value = "";
          }}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
        >
          <Upload className="mr-1.5 h-3.5 w-3.5" />
          Upload
        </Button>
        <Button
          size="sm"
          onClick={() => ingest.mutate()}
          disabled={!hasIndexable || ingest.isPending}
        >
          {ingest.isPending ? "Indexing…" : "Re-index"}
        </Button>
        <span className="text-[11px] text-muted-foreground">
          Filenames must start with <code>YYYY-MM-DD_</code>
        </span>
      </div>

      {documents.length === 0 ? (
        <p className="rounded border border-dashed py-6 text-center text-xs text-muted-foreground">
          No documents uploaded yet. PDFs, DOCX, PPTX, TXT, PNG and JPG are
          supported.
        </p>
      ) : (
        <ul className="divide-y rounded-md border">
          {documents.map((doc) => (
            <li
              key={doc.name}
              className="flex items-center justify-between px-3 py-2 text-xs"
            >
              <div className="flex items-center gap-2 min-w-0">
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="truncate font-mono">{doc.name}</span>
                {!doc.indexable && (
                  <span className="text-[10px] text-amber-600">
                    no date prefix
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                <span className="tabular-nums text-muted-foreground">
                  {fmtBytes(doc.size_bytes)}
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-6 w-6"
                  onClick={() => remove.mutate(doc.name)}
                  disabled={remove.isPending}
                  title="Remove document"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
