"use client";

import { LogOut, Mail, ShieldCheck, UserRound } from "lucide-react";
import { useSession } from "next-auth/react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/page";
import { initials } from "@/components/user-menu";

function providerLabel(provider?: string): string {
  if (!provider) return "Unknown";
  // Auth.js provider ids are lowercase (e.g. "keycloak").
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

export default function ProfilePage() {
  const { data: session, status } = useSession();
  const user = session?.user;
  const isLoading = status === "loading";

  return (
    <PageContainer className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6 text-sm">
          <div className="flex items-center gap-4">
            <span className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/15 text-base font-semibold text-primary">
              {user ? initials(user.name, user.email) : <UserRound className="h-6 w-6" />}
            </span>
            <div className="min-w-0 space-y-1">
              {isLoading ? (
                <Skeleton className="h-5 w-32" />
              ) : (
                <p className="truncate text-base font-medium">
                  {user?.name || "Signed in"}
                </p>
              )}
              {user?.email && (
                <p className="truncate text-muted-foreground">{user.email}</p>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field
              icon={<Mail className="h-4 w-4" />}
              label="Email"
              value={user?.email || "–"}
            />
            <Field
              icon={<ShieldCheck className="h-4 w-4" />}
              label="Signed in with"
              value={providerLabel(session?.provider)}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Session</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            Sign out to end your session here and on the identity provider.
          </p>
          {/* Plain navigation through GET /logout (not a fetch-based signOut())
              so signing out still works when an extension / content blocker /
              stale service worker intercepts fetches – the failure mode behind
              the Safari login loop. */}
          <Button variant="destructive" className="cursor-pointer gap-2" asChild>
            <a href="/logout">
              <LogOut className="h-3.5 w-3.5" />
              Sign out
            </a>
          </Button>
        </CardContent>
      </Card>
    </PageContainer>
  );
}

function Field({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </div>
      <p className="truncate text-sm">{value}</p>
    </div>
  );
}
