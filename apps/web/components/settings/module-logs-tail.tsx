"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/cn";
import { subscribeBus } from "@/lib/ws";

interface LogEntry {
  ts: number;
  level: "debug" | "info" | "warning" | "error";
  event: string;
  fields: Record<string, unknown>;
}

const MAX_LINES = 100;
const LEVEL_COLORS: Record<string, string> = {
  debug: "text-muted-foreground",
  info: "text-foreground",
  warning: "text-amber-600 dark:text-amber-400",
  error: "text-destructive",
};

function formatEntryForCopy(e: LogEntry): string {
  const ts = new Date(e.ts * 1000).toISOString();
  const fields =
    Object.keys(e.fields).length > 0 ? ` ${JSON.stringify(e.fields)}` : "";
  return `${ts} [${e.level.toUpperCase()}] ${e.event}${fields}`;
}

/**
 * Tail the last `MAX_LINES` log lines emitted by `moduleId` (§7.6.2).
 *
 * Subscribes to `module.log.*` bus topics filtered client-side by
 * `payload.module_id`. The per-module logger in `loader.py` publishes
 * every `ctx.logger.<level>` call to those topics.
 */
export function ModuleLogsTail({ moduleId }: { moduleId: string }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const sub = subscribeBus<{ module_id: string; event: string; fields: Record<string, unknown> }>(
      ["module.log.*"],
      (env) => {
        if (env.payload.module_id !== moduleId) return;
        const level = env.topic.split(".").pop() as LogEntry["level"];
        setEntries((prev) => {
          const next = [
            ...prev,
            { ts: env.ts, level, event: env.payload.event, fields: env.payload.fields ?? {} },
          ];
          return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next;
        });
      },
    );
    return () => sub.close();
  }, [moduleId]);

  const onCopy = async () => {
    if (entries.length === 0) return;
    try {
      const text = entries.map(formatEntryForCopy).join("\n");
      await navigator.clipboard.writeText(text);
      setCopied(true);
      // Brief visual confirmation, then revert to the copy icon.
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Failed to copy logs to clipboard");
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs text-muted-foreground">
          Last {entries.length} of {MAX_LINES} lines · live stream
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="cursor-pointer gap-1.5 text-xs"
          onClick={onCopy}
          disabled={entries.length === 0}
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5" />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              Copy
            </>
          )}
        </Button>
      </div>
      <ScrollArea className="h-48 rounded-md border bg-muted/30 px-3 py-2 font-mono text-[11px]">
        {entries.length === 0 ? (
          <div className="text-muted-foreground">No log lines yet. Trigger a module route to see output.</div>
        ) : (
          <ul className="space-y-0.5">
            {entries.map((e, i) => (
              <li key={i} className="flex items-baseline gap-2">
                <span className="shrink-0 text-muted-foreground">
                  {new Date(e.ts * 1000).toLocaleTimeString()}
                </span>
                <Badge
                  variant="outline"
                  className="h-4 shrink-0 border-0 bg-muted px-1 text-[9px] uppercase"
                >
                  {e.level}
                </Badge>
                <span className={cn("truncate", LEVEL_COLORS[e.level])}>{e.event}</span>
                {Object.keys(e.fields).length > 0 && (
                  <span className="truncate text-muted-foreground">
                    {JSON.stringify(e.fields)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </ScrollArea>
    </div>
  );
}
