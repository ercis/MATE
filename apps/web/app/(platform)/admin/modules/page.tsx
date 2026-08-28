"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Package,
  ShieldAlert,
  Trash2,
  UserPlus,
} from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api";
import type { AdminModuleOwner, AdminModuleRow } from "@/lib/api-types";
import {
  useAdminModules,
  useForceInstallModule,
  useForceUninstallModule,
  useSetModuleDefault,
  useSetModuleWithheld,
} from "@/lib/module-admin-queries";
import { type AdminUser, useAdminUsers } from "@/lib/sharing-queries";
import { toastError } from "@/lib/toast";

function ownerLabel(o: AdminModuleOwner): string {
  return o.username || o.email || o.user_id.slice(0, 8);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString();
}

export default function AdminModulesPage() {
  const modules = useAdminModules();
  const users = useAdminUsers();

  if (modules.error instanceof ApiError && modules.error.status === 403) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          Module administration requires the <code>admin</code> role. Ask an
          administrator to grant it in Keycloak (Realm roles → admin).
        </span>
      </div>
    );
  }

  const rows = modules.data ?? [];

  return (
    <div className="space-y-6">
      <section className="space-y-1">
        <h2 className="text-sm font-semibold">Modules</h2>
        <p className="text-xs text-muted-foreground">
          Every module loaded on the platform and who owns it. Flag a module as a
          default to give it to every user (existing and new). Force-install or
          remove a module for an individual user by expanding its row.{" "}
          <Link href="/admin/logs" className="underline hover:text-foreground">
            Who uploaded which event logs →
          </Link>
        </p>
      </section>

      {modules.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : modules.isError ? (
        <p className="text-xs text-destructive">Failed to load modules.</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">No modules loaded.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Module</TableHead>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Owners</TableHead>
                <TableHead>Uploaded by</TableHead>
                <TableHead className="text-right">Default</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((m) => (
                <ModuleRow key={m.id} m={m} users={users.data ?? []} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function ModuleRow({ m, users }: { m: AdminModuleRow; users: AdminUser[] }) {
  const [open, setOpen] = useState(false);
  const setDefault = useSetModuleDefault();
  const label = m.name || m.id;

  return (
    <>
      <TableRow className="cursor-pointer" onClick={() => setOpen((v) => !v)}>
        <TableCell className="align-middle text-muted-foreground">
          {open ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap items-center gap-2">
            <Package className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <div className="text-sm font-medium">{label}</div>
              <div className="text-xs text-muted-foreground">
                {m.id}
                {m.version ? ` · v${m.version}` : ""}
              </div>
            </div>
            {m.is_bundled && <Badge variant="secondary">Bundled</Badge>}
            {m.is_default && !m.is_bundled && <Badge variant="secondary">Default</Badge>}
            {m.withheld_from_new_users && (
              <Badge
                variant="outline"
                className="border-amber-500/40 text-amber-600 dark:text-amber-400"
              >
                Withheld
              </Badge>
            )}
            {!m.has_frontend && (
              <Badge variant="outline" className="text-muted-foreground">
                No panel
              </Badge>
            )}
          </div>
        </TableCell>
        <TableCell className="text-xs capitalize">{m.category}</TableCell>
        <TableCell className="text-right tabular-nums">{m.owner_count}</TableCell>
        <TableCell className="text-xs">
          {m.uploaded_by ? (
            ownerLabel(m.uploaded_by)
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
          <span
            title={
              m.default_locked
                ? "Bundled modules are always default and can't be changed."
                : undefined
            }
          >
            <Switch
              checked={m.is_default}
              disabled={m.default_locked || setDefault.isPending}
              onCheckedChange={(v) =>
                setDefault.mutate(
                  { moduleId: m.id, isDefault: v },
                  {
                    onSuccess: () =>
                      toast.success(
                        v
                          ? `"${label}" is now a default for all users.`
                          : `"${label}" is no longer a default.`,
                      ),
                    onError: (e) => toastError(`Could not change default: ${(e as Error).message}`),
                  },
                )
              }
            />
          </span>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="bg-muted/30 hover:bg-muted/30">
          <TableCell colSpan={6} className="p-0">
            <ModuleDetail m={m} users={users} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function ModuleDetail({ m, users }: { m: AdminModuleRow; users: AdminUser[] }) {
  const forceInstall = useForceInstallModule();
  const setWithheld = useSetModuleWithheld();
  const [pick, setPick] = useState("");

  const ownerIds = new Set(m.owners.map((o) => o.user_id));
  const candidates = users.filter((u) => !ownerIds.has(u.id));

  return (
    <div className="space-y-4 px-4 py-3">
      {m.is_default && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-border bg-background px-3 py-2">
          <div className="min-w-0">
            <div className="text-xs font-medium">Withhold from new users</div>
            <p className="text-[11px] text-muted-foreground">
              Keep this default for current owners, but stop auto-seeding it to users who
              don&apos;t have it yet
              {m.default_locked
                ? " — the only way to stop a bundled default reaching new users."
                : "."}
            </p>
          </div>
          <Switch
            checked={m.withheld_from_new_users}
            disabled={setWithheld.isPending}
            onCheckedChange={(v) =>
              setWithheld.mutate(
                { moduleId: m.id, withheld: v },
                {
                  onSuccess: () =>
                    toast.success(
                      v
                        ? `"${m.name || m.id}" will no longer be seeded to new users.`
                        : `"${m.name || m.id}" will be seeded to new users again.`,
                    ),
                  onError: (e) => toastError(`Could not update: ${(e as Error).message}`),
                },
              )
            }
          />
        </div>
      )}

      <div className="space-y-2">
        <h3 className="text-xs font-semibold text-muted-foreground">
          Owners ({m.owners.length})
        </h3>
        {m.owners.length === 0 ? (
          <p className="text-xs text-muted-foreground">No users own this module.</p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border bg-background">
            {m.owners.map((o) => (
              <OwnerRow key={o.user_id} m={m} o={o} />
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={pick} onValueChange={setPick}>
          <SelectTrigger className="h-8 w-64 text-xs">
            <SelectValue placeholder="Select a user…" />
          </SelectTrigger>
          <SelectContent>
            {candidates.length === 0 ? (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                All users already have this module.
              </div>
            ) : (
              candidates.map((u) => (
                <SelectItem key={u.id} value={u.id} className="text-xs">
                  {u.preferred_username || u.email || u.id}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant="outline"
          disabled={!pick || forceInstall.isPending}
          onClick={() =>
            forceInstall.mutate(
              { moduleId: m.id, userId: pick },
              {
                onSuccess: () => {
                  setPick("");
                  toast.success("Module installed for user.");
                },
                onError: (e) => toastError(`Install failed: ${(e as Error).message}`),
              },
            )
          }
        >
          {forceInstall.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <UserPlus className="h-3.5 w-3.5" />
          )}
          Install for user
        </Button>
      </div>
    </div>
  );
}

function OwnerRow({ m, o }: { m: AdminModuleRow; o: AdminModuleOwner }) {
  const forceUninstall = useForceUninstallModule();
  const label = ownerLabel(o);
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
      <div className="min-w-0 truncate">
        <span className="font-medium">{label}</span>
        {o.email && o.email !== label && (
          <span className="text-muted-foreground"> · {o.email}</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge variant="outline" className="capitalize">
          {o.source ?? "—"}
        </Badge>
        <span className="text-muted-foreground">{formatDate(o.installed_at)}</span>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              size="icon-sm"
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              aria-label={`Remove ${m.name || m.id} from ${label}`}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Remove module from user?</AlertDialogTitle>
              <AlertDialogDescription>
                This removes <strong>{m.name || m.id}</strong> from{" "}
                <strong>{label}</strong>. If they are the last owner and the module
                isn&apos;t a default, its files are also deleted from the server.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={() =>
                  forceUninstall.mutate(
                    { moduleId: m.id, userId: o.user_id },
                    {
                      onSuccess: () => toast.success("Module removed from user."),
                      onError: (e) => toastError(`Remove failed: ${(e as Error).message}`),
                    },
                  )
                }
              >
                Remove
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </li>
  );
}
