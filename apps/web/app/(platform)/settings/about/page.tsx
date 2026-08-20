"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toastError } from "@/lib/toast";

export default function AboutPage() {
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);

  const onCopyDiagnostics = async () => {
    setCopying(true);
    try {
      const blob = await api<Record<string, unknown>>("/api/v1/system/diagnostics");
      const text = JSON.stringify(blob, null, 2);
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Diagnostics copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      toastError(`Could not copy diagnostics: ${(err as Error).message}`);
    } finally {
      setCopying(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">About MATE Hub</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Stat label="Version" value="0.1.1" />
            <Stat label="License" value="MIT" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Diagnostics</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            Bundle platform version, system info, and installed-module
            metadata into one JSON blob to paste into a support thread.
          </p>
          <Button
            variant="outline"
            className="cursor-pointer gap-2"
            disabled={copying}
            onClick={onCopyDiagnostics}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Copied" : copying ? "Copying…" : "Copy diagnostics"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const id = label.toLowerCase();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        readOnly
        value={value}
        className="flex h-9 w-full rounded-md border border-input bg-muted px-3 py-1 text-sm shadow-sm cursor-default select-all text-muted-foreground focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
    </div>
  );
}
