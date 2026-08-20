"use client";

import { useState } from "react";
import { Loader2, Trash2, Users, User as UserIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError } from "@/lib/api";
import {
  useAddShare,
  useDashboardShares,
  useRemoveShare,
  useShareTargets,
} from "@/lib/sharing-queries";

export function ShareDialog({
  dashboardId,
  dashboardName,
  open,
  onOpenChange,
}: {
  dashboardId: string;
  dashboardName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const shares = useDashboardShares(open ? dashboardId : null);
  const targets = useShareTargets(open);
  const add = useAddShare(dashboardId);
  const remove = useRemoveShare(dashboardId);
  const [selected, setSelected] = useState("");

  // Don't offer a target that's already shared with.
  const sharedKeys = new Set((shares.data ?? []).map((s) => `${s.kind}:${s.target_id}`));
  const available = (targets.data ?? []).filter((t) => !sharedKeys.has(`${t.kind}:${t.id}`));

  const onAdd = async () => {
    if (!selected) return;
    const sep = selected.indexOf(":");
    const kind = selected.slice(0, sep);
    const id = selected.slice(sep + 1);
    try {
      await add.mutateAsync(kind === "team" ? { target_team_id: id } : { target_user_id: id });
      setSelected("");
    } catch (e) {
      toast.error(e instanceof ApiError && e.status === 409 ? "Already shared" : "Could not share");
    }
  };

  const onRemove = async (shareId: string) => {
    try {
      await remove.mutateAsync(shareId);
    } catch {
      toast.error("Could not remove share");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Share "{dashboardName}"</DialogTitle>
          <DialogDescription>
            Give other members read-only access to this dashboard. They can view it and its
            data but can't edit it.
          </DialogDescription>
        </DialogHeader>

        {/* Add a target */}
        {targets.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (targets.data ?? []).length === 0 ? (
          <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
            You're not in any team yet. Ask an admin to add you to a team – you can then share
            with your teammates.
          </p>
        ) : (
          <div className="flex items-center gap-2">
            <Select value={selected} onValueChange={setSelected}>
              <SelectTrigger className="min-w-0 flex-1">
                <SelectValue placeholder="Pick a member or team…" />
              </SelectTrigger>
              <SelectContent>
                {available.length === 0 ? (
                  <div className="px-2 py-1.5 text-sm text-muted-foreground">
                    Everyone available is already added.
                  </div>
                ) : (
                  available.map((t) => (
                    <SelectItem key={`${t.kind}:${t.id}`} value={`${t.kind}:${t.id}`}>
                      <span className="flex items-center gap-2">
                        {t.kind === "team" ? (
                          <Users className="h-3.5 w-3.5 text-muted-foreground" />
                        ) : (
                          <UserIcon className="h-3.5 w-3.5 text-muted-foreground" />
                        )}
                        {t.label}
                        {t.kind === "user" && t.sublabel ? (
                          <span className="text-xs text-muted-foreground">{t.sublabel}</span>
                        ) : null}
                      </span>
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
            <Button onClick={onAdd} disabled={!selected || add.isPending} className="shrink-0">
              {add.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              Share
            </Button>
          </div>
        )}

        {/* Current shares */}
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">Shared with</p>
          {shares.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : (shares.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Not shared with anyone yet.</p>
          ) : (
            <ul className="divide-y rounded-md border">
              {shares.data!.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-2 px-3 py-2">
                  <span className="flex items-center gap-2 text-sm">
                    {s.kind === "team" ? (
                      <Users className="h-3.5 w-3.5 text-muted-foreground" />
                    ) : (
                      <UserIcon className="h-3.5 w-3.5 text-muted-foreground" />
                    )}
                    {s.label}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${s.label}`}
                    onClick={() => onRemove(s.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
