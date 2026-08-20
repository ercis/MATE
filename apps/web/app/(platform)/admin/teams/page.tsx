"use client";

import { useState } from "react";
import { Check, Loader2, Pencil, Plus, Trash2, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import {
  useAddTeamMember,
  useAdminShares,
  useAdminTeams,
  useAdminUsers,
  useCreateTeam,
  useDeleteTeam,
  useRemoveTeamMember,
  useRevokeAdminShare,
  useTeamMembers,
  useUpdateTeam,
  type Team,
} from "@/lib/sharing-queries";

function userLabel(u: {
  name: string | null;
  preferred_username: string | null;
  email: string | null;
}): string {
  return u.name || u.preferred_username || u.email || "Unknown user";
}

export default function AdminTeamsPage() {
  const teams = useAdminTeams();
  const createTeam = useCreateTeam();
  const deleteTeam = useDeleteTeam();
  const updateTeam = useUpdateTeam();
  const [newTeam, setNewTeam] = useState("");
  const [selectedTeam, setSelectedTeam] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const startRename = (t: Team) => {
    setRenamingId(t.id);
    setRenameValue(t.name);
  };

  const commitRename = async () => {
    if (!renamingId) return;
    const name = renameValue.trim();
    const id = renamingId;
    setRenamingId(null);
    if (!name) return;
    try {
      await updateTeam.mutateAsync({ teamId: id, name });
    } catch {
      toast.error("Could not rename team");
    }
  };

  const onCreate = async () => {
    const name = newTeam.trim();
    if (!name) return;
    try {
      await createTeam.mutateAsync(name);
      setNewTeam("");
    } catch {
      toast.error("Could not create team");
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteTeam.mutateAsync(id);
      if (selectedTeam === id) setSelectedTeam(null);
    } catch {
      toast.error("Could not delete team");
    }
  };

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-medium">Teams</h2>
          <p className="text-sm text-muted-foreground">
            Teams group members. A user can share a dashboard with a whole team or with any
            teammate. Deleting a team revokes its shares.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Input
            value={newTeam}
            onChange={(e) => setNewTeam(e.target.value)}
            placeholder="New team name…"
            className="max-w-xs"
            onKeyDown={(e) => {
              if (e.key === "Enter") void onCreate();
            }}
          />
          <Button onClick={onCreate} disabled={!newTeam.trim() || createTeam.isPending}>
            {createTeam.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-1.5 h-4 w-4" />
            )}
            Create team
          </Button>
        </div>

        {teams.isLoading ? (
          <div className="grid gap-3 md:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-xl" />
            ))}
          </div>
        ) : (teams.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No teams yet.</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {teams.data!.map((t) => (
              <Card
                key={t.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedTeam(selectedTeam === t.id ? null : t.id)}
                className={cn(
                  "cursor-pointer transition-colors",
                  selectedTeam === t.id ? "border-primary" : "hover:border-primary/40",
                )}
              >
                <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 pb-2">
                  {renamingId === t.id ? (
                    <Input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        e.stopPropagation();
                        if (e.key === "Enter") void commitRename();
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      onBlur={() => void commitRename()}
                      className="h-7 text-sm"
                    />
                  ) : (
                    <CardTitle className="flex min-w-0 items-center gap-2 text-base">
                      <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="truncate">{t.name}</span>
                    </CardTitle>
                  )}
                  <div className="flex shrink-0 items-center gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={renamingId === t.id ? "Save name" : `Rename ${t.name}`}
                      className="h-7 w-7 text-muted-foreground hover:text-primary"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (renamingId === t.id) void commitRename();
                        else startRename(t);
                      }}
                    >
                      {renamingId === t.id ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : (
                        <Pencil className="h-3.5 w-3.5" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${t.name}`}
                      className="h-7 w-7 text-muted-foreground hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        void onDelete(t.id);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="text-xs text-muted-foreground">
                  {t.member_count} member{t.member_count === 1 ? "" : "s"} · click to manage
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {selectedTeam && <TeamMembersPanel teamId={selectedTeam} />}
      </section>

      <ShareOversight />
    </div>
  );
}

function TeamMembersPanel({ teamId }: { teamId: string }) {
  const members = useTeamMembers(teamId);
  const users = useAdminUsers();
  const add = useAddTeamMember(teamId);
  const remove = useRemoveTeamMember(teamId);
  const [pick, setPick] = useState("");

  const memberIds = new Set((members.data ?? []).map((m) => m.user_id));
  const candidates = (users.data ?? []).filter((u) => !memberIds.has(u.id));

  const onAdd = async () => {
    if (!pick) return;
    try {
      await add.mutateAsync(pick);
      setPick("");
    } catch {
      toast.error("Could not add member");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Members</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <Select value={pick} onValueChange={setPick}>
            <SelectTrigger className="max-w-xs">
              <SelectValue placeholder="Add a member…" />
            </SelectTrigger>
            <SelectContent>
              {candidates.length === 0 ? (
                <div className="px-2 py-1.5 text-sm text-muted-foreground">
                  No more users to add.
                </div>
              ) : (
                candidates.map((u) => (
                  <SelectItem key={u.id} value={u.id}>
                    {userLabel(u)}
                    {u.email ? ` (${u.email})` : ""}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
          <Button onClick={onAdd} disabled={!pick || add.isPending}>
            {add.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="mr-1.5 h-4 w-4" />
            )}
            Add
          </Button>
        </div>

        {members.isLoading ? (
          <div className="space-y-2 rounded-md border p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : (members.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No members yet.</p>
        ) : (
          <ul className="divide-y rounded-md border">
            {members.data!.map((m) => (
              <li key={m.user_id} className="flex items-center justify-between gap-2 px-3 py-2">
                <span className="text-sm">
                  {userLabel(m)}
                  {m.email ? <span className="ml-1 text-xs text-muted-foreground">{m.email}</span> : null}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-destructive"
                  aria-label="Remove member"
                  onClick={() =>
                    void remove.mutateAsync(m.user_id).catch(() => toast.error("Could not remove member"))
                  }
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ShareOversight() {
  const shares = useAdminShares();
  const revoke = useRevokeAdminShare();

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">Shared dashboards</h2>
        <p className="text-sm text-muted-foreground">
          Every dashboard share across all users. Revoke any of them here.
        </p>
      </div>

      {shares.isLoading ? (
        <div className="space-y-2 rounded-md border p-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : (shares.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">No dashboards are shared.</p>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Dashboard</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead>Shared with</TableHead>
                <TableHead>When</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {shares.data!.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="font-medium">{s.dashboard_name}</TableCell>
                  <TableCell className="text-muted-foreground">{s.owner_label}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5">
                      {s.target_kind === "team" ? (
                        <Users className="h-3.5 w-3.5 text-muted-foreground" />
                      ) : null}
                      {s.target_label}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatRelative(s.created_at)}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-destructive"
                      aria-label="Revoke share"
                      onClick={() =>
                        void revoke.mutateAsync(s.id).catch(() => toast.error("Could not revoke"))
                      }
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}
