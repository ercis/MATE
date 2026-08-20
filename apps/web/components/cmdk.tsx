"use client";

import { useProgressRouter } from "@/lib/use-progress-router";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Cog, FileBox, FolderKanban, Inbox, Plus, Upload } from "lucide-react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { api } from "@/lib/api";
import { useJobsStore } from "@/lib/stores/jobs";
import type { EventLogSummary } from "@/lib/api-types";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const STATIC_NAV = [
  { id: "nav-processes", label: "Go to Processes", icon: FolderKanban, href: "/processes" },
  { id: "nav-import", label: "Import event log", icon: Upload, href: "/processes/import" },
  { id: "nav-settings", label: "Open Settings", icon: Cog, href: "/settings" },
  { id: "nav-modules", label: "Manage modules", icon: FileBox, href: "/modules" },
  { id: "nav-import-module", label: "Import module", icon: Plus, href: "/modules/import" },
];

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useProgressRouter();
  const setDrawerOpen = useJobsStore((s) => s.setDrawerOpen);
  const [query, setQuery] = useState("");

  const { data: logs } = useQuery({
    queryKey: ["event-logs", "for-cmdk"],
    queryFn: () => api<EventLogSummary[]>("/api/v1/event-logs"),
    enabled: open,
    staleTime: 10_000,
  });

  // Reset the query when the palette closes so the next open starts fresh.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const matchingLogs = useMemo(() => (logs ?? []).slice(0, 8), [logs]);

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Command palette">
      <CommandInput
        placeholder="Jump to a process, run a command…"
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        <CommandEmpty>No matches.</CommandEmpty>

        <CommandGroup heading="Navigation">
          {STATIC_NAV.map((item) => {
            const Icon = item.icon;
            return (
              <CommandItem
                key={item.id}
                value={item.label}
                onSelect={() => go(item.href)}
                className="cursor-pointer"
              >
                <Icon className="mr-2 h-4 w-4" />
                {item.label}
              </CommandItem>
            );
          })}
          <CommandItem
            value="Show jobs"
            onSelect={() => {
              onOpenChange(false);
              setDrawerOpen(true);
            }}
            className="cursor-pointer"
          >
            <Activity className="mr-2 h-4 w-4" />
            Show jobs
          </CommandItem>
        </CommandGroup>

        {matchingLogs.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Processes">
              {matchingLogs.map((log) => (
                <CommandItem
                  key={log.id}
                  value={`${log.name} ${log.id}`}
                  onSelect={() => go(`/processes/${log.id}`)}
                  className="cursor-pointer"
                >
                  <Inbox className="mr-2 h-4 w-4" />
                  <span className="truncate">{log.name}</span>
                  <span className="ml-auto truncate text-xs text-muted-foreground">
                    {log.status}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
  );
}
