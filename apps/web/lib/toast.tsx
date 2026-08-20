"use client";

import { Copy } from "lucide-react";
import { toast } from "sonner";

function ErrorContent({ message, description }: { message: string; description?: string }) {
  const copyText = description ? `${message}\n${description}` : message;
  return (
    <div className="flex min-w-0 items-start gap-2">
      <div className="min-w-0 flex-1 space-y-1">
        <span className="block break-words">{message}</span>
        {description && (
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] opacity-80">
            {description}
          </pre>
        )}
      </div>
      <button
        type="button"
        aria-label="Copy error message"
        className="mt-0.5 shrink-0 opacity-60 hover:opacity-100 transition-opacity cursor-pointer"
        onClick={(e) => {
          e.stopPropagation();
          navigator.clipboard.writeText(copyText).catch(() => null);
        }}
      >
        <Copy className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function toastError(
  message: string,
  options?: Parameters<typeof toast.error>[1],
) {
  const { description, ...rest } = options ?? {};
  const descStr = typeof description === "string" ? description : undefined;
  toast.error(<ErrorContent message={message} description={descStr} />, rest);
}
