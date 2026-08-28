"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronRight, ShieldAlert } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api";
import { type AdminUser, useAdminUsers } from "@/lib/sharing-queries";

function userLabel(u: AdminUser): string {
  return u.name || u.preferred_username || u.email || u.id.slice(0, 8);
}

export default function AdminUsersPage() {
  const users = useAdminUsers();
  const [q, setQ] = useState("");

  const all = users.data ?? [];
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((u) =>
      [u.name, u.preferred_username, u.email, u.id]
        .filter((v): v is string => Boolean(v))
        .some((v) => v.toLowerCase().includes(needle)),
    );
  }, [all, q]);

  if (users.error instanceof ApiError && users.error.status === 403) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          User administration requires the <code>admin</code> role. Ask an
          administrator to grant it in Keycloak (Realm roles → admin).
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="space-y-1">
        <h2 className="text-sm font-semibold">Users</h2>
        <p className="text-xs text-muted-foreground">
          Every account that has signed in. Open a user to see everything they
          own and to permanently delete them.
        </p>
      </section>

      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by name, username, email, or id…"
        className="max-w-sm"
      />

      {users.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-xs text-muted-foreground">No users match.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Username</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((u) => (
              <TableRow key={u.id} className="cursor-pointer">
                <TableCell className="font-medium">
                  <Link
                    href={`/admin/users/${u.id}`}
                    className="block hover:underline"
                  >
                    {userLabel(u)}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {u.email ?? "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {u.preferred_username ?? "—"}
                </TableCell>
                <TableCell>
                  <Link
                    href={`/admin/users/${u.id}`}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label={`Open ${userLabel(u)}`}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
