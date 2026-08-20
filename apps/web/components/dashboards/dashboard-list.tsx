"use client";

import { useRef, useState } from "react";
import { useProgressRouter } from "@/lib/use-progress-router";
import { useQueryClient } from "@tanstack/react-query";
import { prefetchDashboard } from "@/lib/client-prefetch";
import Link from "next/link";
import {
  Boxes,
  LayoutDashboard,
  Loader2,
  Plus,
  Share2,
  Trash2,
  Upload,
  Users,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { EmptyState } from "@/components/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PageContainer,
  PageHeader,
  PageTitle,
  PageDescription,
  PageActions,
} from "@/components/page";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import {
  canvasSettings,
  useCreateDashboard,
  useDashboards,
  useDeleteDashboard,
  useImportDashboard,
  type CanvasSettings,
  type DashboardItem,
  type LogModel,
} from "@/lib/dashboard-queries";
import { useSharedWithMe } from "@/lib/sharing-queries";
import { ShareDialog } from "@/components/dashboards/share-dialog";

const MODEL_OPTIONS: {
  value: LogModel;
  label: string;
  hint: string;
  icon: typeof Workflow;
}[] = [
  {
    value: "case_centric",
    label: "Case-centric",
    hint: "One case per process instance (XES / CSV logs).",
    icon: Workflow,
  },
  {
    value: "object_centric",
    label: "Object-centric",
    hint: "Multiple object types per event (OCEL logs).",
    icon: Boxes,
  },
];

export function DashboardList() {
  const router = useProgressRouter();
  const qc = useQueryClient();
  const { data: dashboards, isLoading } = useDashboards();
  const { data: sharedWithMe } = useSharedWithMe();
  const create = useCreateDashboard();
  const del = useDeleteDashboard();
  const importDash = useImportDashboard();
  const fileRef = useRef<HTMLInputElement>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [model, setModel] = useState<LogModel>("case_centric");
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [shareTarget, setShareTarget] = useState<{ id: string; name: string } | null>(null);

  const onCreateOpenChange = (open: boolean) => {
    setCreateOpen(open);
    if (!open) {
      setNewName("");
      setModel("case_centric");
    }
  };

  const onCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const dash = await create.mutateAsync({ name, log_model: model });
      onCreateOpenChange(false);
      router.push(`/dashboards/${dash.id}`);
    } catch {
      toast.error("Could not create dashboard");
    }
  };

  const onImportFile = async (file: File) => {
    try {
      const text = await file.text();
      const doc = JSON.parse(text) as {
        name?: string;
        description?: string | null;
        log_model?: LogModel;
        items?: DashboardItem[];
        settings?: CanvasSettings;
      };
      const dash = await importDash.mutateAsync({
        name: doc.name,
        description: doc.description ?? null,
        log_model: doc.log_model,
        items: Array.isArray(doc.items) ? doc.items : [],
        settings: doc.settings ? canvasSettings(doc.settings) : undefined,
      });
      toast.success("Dashboard imported");
      router.push(`/dashboards/${dash.id}`);
    } catch {
      toast.error("Invalid dashboard file");
    }
  };

  const onDelete = async () => {
    if (!deleteId) return;
    try {
      await del.mutateAsync(deleteId);
      toast.success("Dashboard deleted");
    } catch {
      toast.error("Could not delete dashboard");
    } finally {
      setDeleteId(null);
    }
  };

  return (
    <PageContainer>
      <PageHeader>
        <div className="space-y-1">
          <PageTitle>Dashboards</PageTitle>
          <PageDescription>
            Compose cards from any module into a saved, reopenable board.
          </PageDescription>
        </div>
        <PageActions>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onImportFile(f);
              e.target.value = "";
            }}
          />
          <Button variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload className="mr-1.5 h-4 w-4" />
            Import
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-4 w-4" />
            New dashboard
          </Button>
        </PageActions>
      </PageHeader>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : !dashboards || dashboards.length === 0 ? (
        <EmptyState
          icon={LayoutDashboard}
          title="No dashboards yet"
          description="Create a dashboard and drag in cards from your installed modules."
          primaryAction={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="mr-1.5 h-4 w-4" />
              New dashboard
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {dashboards.map((d) => (
            <Card
              key={d.id}
              className="group relative transition-colors hover:border-primary/40"
              onMouseEnter={() => prefetchDashboard(qc, d.id)}
            >
              <Link href={`/dashboards/${d.id}`} className="absolute inset-0" aria-label={d.name}>
                <span className="sr-only">{d.name}</span>
              </Link>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="truncate text-base">{d.name}</CardTitle>
                  <div className="relative z-10 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Share ${d.name}`}
                      className="h-7 w-7 text-muted-foreground hover:text-primary"
                      onClick={(e) => {
                        e.preventDefault();
                        setShareTarget({ id: d.id, name: d.name });
                      }}
                    >
                      <Share2 className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${d.name}`}
                      className="h-7 w-7 text-muted-foreground hover:text-destructive"
                      onClick={(e) => {
                        e.preventDefault();
                        setDeleteId(d.id);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center gap-1">
                    <LayoutDashboard className="h-3.5 w-3.5" />
                    {d.card_count} card{d.card_count === 1 ? "" : "s"}
                  </span>
                  <span>Updated {formatRelative(d.updated_at)}</span>
                </div>
                {d.description && (
                  <p className="mt-2 line-clamp-2">{d.description}</p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Shared with me */}
      {sharedWithMe && sharedWithMe.length > 0 && (
        <div className="mt-8 space-y-3">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-medium">Shared with me</h2>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {sharedWithMe.map((d) => (
              <Card
              key={d.id}
              className="group relative transition-colors hover:border-primary/40"
              onMouseEnter={() => prefetchDashboard(qc, d.id)}
            >
                <Link
                  href={`/dashboards/${d.id}`}
                  className="absolute inset-0"
                  aria-label={d.name}
                >
                  <span className="sr-only">{d.name}</span>
                </Link>
                <CardHeader className="pb-2">
                  <CardTitle className="truncate text-base">{d.name}</CardTitle>
                </CardHeader>
                <CardContent className="text-xs text-muted-foreground">
                  <div className="flex items-center gap-3">
                    <span className="inline-flex items-center gap-1">
                      <LayoutDashboard className="h-3.5 w-3.5" />
                      {d.card_count} card{d.card_count === 1 ? "" : "s"}
                    </span>
                    <span>by {d.owner_label}</span>
                  </div>
                  {d.description && <p className="mt-2 line-clamp-2">{d.description}</p>}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Share dialog */}
      {shareTarget && (
        <ShareDialog
          dashboardId={shareTarget.id}
          dashboardName={shareTarget.name}
          open={shareTarget !== null}
          onOpenChange={(o) => !o && setShareTarget(null)}
        />
      )}

      {/* Create dialog */}
      <Dialog open={createOpen} onOpenChange={onCreateOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New dashboard</DialogTitle>
            <DialogDescription>
              Name it and pick its process type. The type is fixed once created – it
              decides which cards and event logs the board can use.
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. Throughput overview"
            onKeyDown={(e) => {
              if (e.key === "Enter") void onCreate();
            }}
          />
          <div className="grid grid-cols-2 gap-2">
            {MODEL_OPTIONS.map((opt) => {
              const Icon = opt.icon;
              const selected = model === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setModel(opt.value)}
                  aria-pressed={selected}
                  className={cn(
                    "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors",
                    selected
                      ? "border-primary bg-primary/5 ring-1 ring-primary"
                      : "border-border hover:border-primary/40 hover:bg-muted/40",
                  )}
                >
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                    {opt.label}
                  </span>
                  <span className="text-[11px] leading-snug text-muted-foreground">
                    {opt.hint}
                  </span>
                </button>
              );
            })}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => onCreateOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={onCreate} disabled={!newName.trim() || create.isPending}>
              {create.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <AlertDialog open={deleteId !== null} onOpenChange={(o) => !o && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete dashboard?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the dashboard. The underlying event log and module
              data are not affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageContainer>
  );
}
