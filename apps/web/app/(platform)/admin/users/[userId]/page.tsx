"use client";

import { type ReactNode, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, ShieldAlert, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { toastError } from "@/lib/toast";
import { useProgressRouter } from "@/lib/use-progress-router";
import {
  type AdminUserDetail,
  useAdminUserDetail,
  useDeleteUser,
} from "@/lib/users-queries";

function fmtBytes(n: number | null): string {
  if (n == null) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function AdminUserDetailPage() {
  const { userId } = useParams<{ userId: string }>();
  const detail = useAdminUserDetail(userId, true);

  if (detail.error instanceof ApiError && detail.error.status === 403) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          User administration requires the <code>admin</code> role.
        </span>
      </div>
    );
  }
  if (detail.error instanceof ApiError && detail.error.status === 404) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="text-sm text-muted-foreground">This user no longer exists.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />
      {detail.isLoading || !detail.data ? (
        <div className="space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        <UserDetail d={detail.data} />
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/admin/users"
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      All users
    </Link>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
}

function UserDetail({ d }: { d: AdminUserDetail }) {
  const activeLogs = d.event_logs.filter((l) => !l.deleted_at).length;
  const lastOwned = d.modules.filter((m) => m.last_owner).length;

  return (
    <div className="space-y-6">
      {/* Identity */}
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            {d.label}
            {d.name && d.name !== d.label && (
              <span className="text-xs font-normal text-muted-foreground">{d.name}</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <Row k="Email" v={d.email ?? "—"} />
          <Row k="Username" v={d.preferred_username ?? "—"} />
          <Row k="User id" v={<code className="text-xs">{d.id}</code>} />
          <Row k="Joined" v={fmtDate(d.created_at)} />
          <Row k="Last seen" v={fmtDate(d.last_seen_at)} />
        </CardContent>
      </Card>

      {/* Footprint */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        <Stat label="Event logs" value={`${activeLogs}/${d.event_logs.length}`} />
        <Stat label="Dashboards" value={d.dashboards.length} />
        <Stat label="Folders" value={d.folders_count} />
        <Stat label="Watched folders" value={d.watched_folders.length} />
        <Stat label="Active jobs" value={d.jobs.active} />
        <Stat label="Modules" value={d.modules.length} />
        <Stat label="API tokens" value={d.api_tokens.length} />
        <Stat label="Disk" value={fmtBytes(d.storage_bytes)} />
        <Stat label="Teams" value={d.teams.length} />
        <Stat label="Shares out" value={d.shares_created} />
        <Stat label="Shares in" value={d.shares_received} />
        <Stat label="Analytics events" value={d.analytics_events} />
      </div>

      {/* Resource lists */}
      <div className="grid gap-4 lg:grid-cols-2">
        <ListCard
          title="Event logs"
          empty="No event logs."
          items={d.event_logs.map((l) => ({
            key: l.id,
            main: l.name,
            side: (
              <span className="flex items-center gap-1.5">
                <Badge variant="outline" className="capitalize">
                  {l.status}
                </Badge>
                {l.deleted_at && (
                  <Badge variant="secondary" className="text-[10px]">
                    deleted
                  </Badge>
                )}
              </span>
            ),
          }))}
        />
        <ListCard
          title="Dashboards"
          empty="No dashboards."
          items={d.dashboards.map((x) => ({ key: x.id, main: x.name }))}
        />
        <ListCard
          title="Modules"
          empty="No modules installed."
          items={d.modules.map((m) => ({
            key: m.module_id,
            main: <code className="text-xs">{m.module_id}</code>,
            side: m.last_owner ? (
              <Badge variant="outline" className="text-[10px]">
                sole owner
              </Badge>
            ) : null,
          }))}
        />
        <ListCard
          title="Teams"
          empty="Not in any team."
          items={d.teams.map((t) => ({
            key: t.team_id,
            main: t.name,
            side: <span className="text-xs text-muted-foreground capitalize">{t.role}</span>,
          }))}
        />
        <ListCard
          title="Watched folders"
          empty="None."
          items={d.watched_folders.map((w) => ({
            key: w.id,
            main: w.name,
            side: (
              <Badge variant="outline" className="capitalize">
                {w.status}
              </Badge>
            ),
          }))}
        />
        <ListCard
          title="API tokens"
          empty="None."
          items={d.api_tokens.map((t) => ({
            key: t.id,
            main: t.name,
            side: t.revoked ? (
              <Badge variant="secondary" className="text-[10px]">
                revoked
              </Badge>
            ) : (
              <code className="text-xs text-muted-foreground">{t.token_prefix}…</code>
            ),
          }))}
        />
      </div>

      <DangerZone d={d} lastOwned={lastOwned} />
    </div>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}

interface ListItem {
  key: string;
  main: ReactNode;
  side?: ReactNode;
}

function ListCard({ title, items, empty }: { title: string; items: ListItem[]; empty: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          {title} <span className="text-muted-foreground">({items.length})</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground">{empty}</p>
        ) : (
          <ul className="divide-y divide-border text-sm">
            {items.map((it) => (
              <li key={it.key} className="flex items-center justify-between gap-3 py-1.5">
                <span className="min-w-0 truncate">{it.main}</span>
                {it.side && <span className="shrink-0">{it.side}</span>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function DangerZone({ d, lastOwned }: { d: AdminUserDetail; lastOwned: number }) {
  const router = useProgressRouter();
  const del = useDeleteUser();
  const [confirm, setConfirm] = useState("");

  // Type-to-confirm gate: the least-ambiguous stable identifier we can show.
  const expected = d.preferred_username || d.email || d.id;

  const onDelete = async () => {
    try {
      const report = await del.mutateAsync(d.id);
      const bits = [
        `${report.jobs_cancelled} jobs cancelled`,
        `${report.modules_torn_down} modules removed`,
      ];
      if (report.keycloak_deleted) bits.push("Keycloak account removed");
      toast.success(`Deleted ${d.label}`, { description: bits.join(" · ") });
      for (const w of report.warnings) toast.warning(w);
      router.push("/admin/users");
    } catch (e) {
      toastError(
        e instanceof ApiError && e.status === 400
          ? "You cannot delete your own account."
          : `Delete failed: ${(e as Error).message}`,
      );
    }
  };

  return (
    <Card className="border-destructive/30">
      <CardHeader>
        <CardTitle className="text-base">Danger zone</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Permanently delete this user and <strong>everything they own</strong> —
          {" "}
          {d.event_logs.length} event log(s), {d.dashboards.length} dashboard(s),
          all jobs, module installs, {d.api_tokens.length} API token(s), and their
          on-disk + remote storage. This also revokes{" "}
          {d.shares_created + d.shares_received} dashboard share(s) involving them
          {lastOwned > 0 && (
            <>
              {" "}
              and tears down {lastOwned} module(s) they solely own
            </>
          )}
          . The Keycloak account is removed too (a later re-login would create a
          fresh, empty account). <strong>This cannot be undone.</strong>
        </p>
        <AlertDialog
          onOpenChange={(open) => {
            if (!open) setConfirm("");
          }}
        >
          <AlertDialogTrigger asChild>
            <Button
              variant="outline"
              className="cursor-pointer gap-2 text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete user
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {d.label}?</AlertDialogTitle>
              <AlertDialogDescription>
                This purges all of their data and the Keycloak account. To
                confirm, type <code className="font-semibold">{expected}</code>{" "}
                below.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="confirm-delete" className="text-xs">
                Confirm
              </Label>
              <Input
                id="confirm-delete"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={expected}
                autoComplete="off"
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
              <Button
                variant="destructive"
                className="cursor-pointer gap-2"
                disabled={confirm !== expected || del.isPending}
                onClick={onDelete}
              >
                {del.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Delete permanently
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </CardContent>
    </Card>
  );
}
