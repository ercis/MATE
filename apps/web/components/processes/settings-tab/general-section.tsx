"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";

import { ActionButton } from "@/components/ui/action-button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { EventLogDetail } from "@/lib/api-types";
import { useUpdateEventLog } from "@/lib/queries";

export function GeneralSection({ logId, log }: { logId: string; log: EventLogDetail }) {
  const update = useUpdateEventLog(logId);
  const [name, setName] = useState(log.name);
  const [description, setDescription] = useState(log.description ?? "");

  useEffect(() => {
    setName(log.name);
    setDescription(log.description ?? "");
  }, [log.name, log.description]);

  const dirty =
    name.trim() !== log.name.trim() ||
    description.trim() !== (log.description ?? "").trim();

  const onSave = async () => {
    try {
      await update.mutateAsync({
        name: name.trim() !== log.name.trim() ? name.trim() : undefined,
        description: description.trim() !== (log.description ?? "").trim() ? description : undefined,
      });
      toast.success("Saved");
    } catch (err) {
      toastError(`Save failed: ${(err as Error).message}`);
    }
  };

  return (
    <SectionShell title="General" description="Basic information about this event log.">
      <div className="space-y-2.5 max-w-2xl">
        <div className="space-y-1">
          <Label htmlFor="general-name" className="text-xs font-medium text-muted-foreground">
            Name
          </Label>
          <Input
            id="general-name"
            maxLength={255}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-8"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="general-description" className="text-xs font-medium text-muted-foreground">
            Description
          </Label>
          <textarea
            id="general-description"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Notes about this log - owner, source system, caveats…"
            className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20"
          />
        </div>
        <div className="flex justify-end pt-1">
          <ActionButton
            size="sm"
            disabled={!dirty}
            isPending={update.isPending}
            isSuccess={update.isSuccess}
            onClick={onSave}
            className="cursor-pointer"
          >
            {update.isPending ? "Saving…" : "Save"}
          </ActionButton>
        </div>
      </div>
    </SectionShell>
  );
}

export function SectionShell({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="mb-3.5">
        <h2 className="text-sm font-semibold leading-tight">{title}</h2>
        {description && (
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {children}
    </section>
  );
}
