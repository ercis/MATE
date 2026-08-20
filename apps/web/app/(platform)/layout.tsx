import { Suspense } from "react";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { JobsProvider } from "@/components/jobs/jobs-provider";
import { JobsDock } from "@/components/jobs/jobs-dock";
import { JobsDrawer } from "@/components/jobs/jobs-drawer";
import { OnboardingOverlay } from "@/components/onboarding";
import { MateAiSidebar } from "@/components/mate-ai/mate-ai-sidebar";
import { RouteProgress } from "@/components/route-progress";
import { SessionGuard } from "@/components/session-guard";

export default async function PlatformLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  // A refresh-failed session is still non-null (valid cookie, flagged error) but
  // has no usable token – treat it as logged-out and redirect server-side, before
  // any dashboard HTML renders. Otherwise the shell paints, then the client api
  // wrapper (lib/api.ts) catches the same error and signs out → a dashboard flash.
  // Mirrors lib/api-server.ts and (auth)/login/page.tsx.
  if (!session || session.error === "RefreshAccessTokenError") {
    redirect("/login");
  }
  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <SessionGuard />
      <Suspense fallback={null}>
        <RouteProgress />
      </Suspense>
      <Sidebar isAdmin={session.user.isAdmin === true} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Suspense>
          <Topbar />
        </Suspense>
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
      <MateAiSidebar />
      <JobsProvider />
      <JobsDock />
      <JobsDrawer />
      <OnboardingOverlay />
    </div>
  );
}
